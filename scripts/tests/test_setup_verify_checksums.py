"""actions/setup verifies each tool's release checksum file against a signer key in this repo.

Each verify step's real `run:` body executes here with real `gpg`, `openssl` and `base64`. Only
`curl` is a stub: it appends its argv to a log and serves the raw upstream files under
`fixtures/release-sums/` by URL basename. A `gpg` wrapper logs its argv and runs the real gpg.

Threat model: an accidental regression in a SHA-pinned, reviewed engine file -- a dropped
signature check, an exit-code-only check, a fetched key, a lost guard. A hostile edit to the
step is out of scope. Cases that need a correctly signed but edited checksum file sign it with
a throwaway key made here, and run a copy of the body with the key path (and, for OpenTofu, the
fingerprint constant) substituted.
"""

import base64
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest
from _loader import ENGINE, bash_only, run_step, step_by

_ACTION = "setup"
_FIXTURES = ENGINE / "scripts" / "tests" / "fixtures" / "release-sums"

_TOFU = "1.13.2"
_TERRAMATE = "0.17.3"
_TOFU_SUMS = f"tofu_{_TOFU}_SHA256SUMS"
_TOFU_BASE = f"https://github.com/opentofu/opentofu/releases/download/v{_TOFU}"
_TM_BASE = f"https://github.com/terramate-io/terramate/releases/download/v{_TERRAMATE}"

_TOFU_KEY = "$GITHUB_ACTION_PATH/../../keys/opentofu.asc"
_TM_KEY = "$GITHUB_ACTION_PATH/../../keys/terramate-cosign.pub"
_SIGNER = "E3E6E43D84CB852EADB0051D0C0AF313E5FD9F80"

_TOFU_ERROR = f"::error title=OpenTofu checksum verification failed::OpenTofu {_TOFU}: "
_TM_ERROR = f"::error title=Terramate checksum verification failed::Terramate {_TERRAMATE}: "


def _refusal(stdout):
    """The `refusal=` line a failed step writes: its one annotation without the
    `::error title=...::` prefix. Every caller pins `stdout` whole."""
    return "refusal=" + stdout.split("::", 2)[2]


#: Each step's whole $GITHUB_OUTPUT on the genuine fixtures, digests copied by hand from them.
_TOFU_OUTPUT = (
    "tofu_sha256<<SHIPMATE_EOF\n"
    "723bebb0df8411daff1e5fc3862e28ff5581fe5634087f4c29a9aa41d78208b5\n"
    "6e815c58a09ee957de606ff48cefad082942e70afc43b737ff975654c54ec453\n"
    "SHIPMATE_EOF\n"
)
_TM_OUTPUT = (
    "terramate_sha256<<SHIPMATE_EOF\n"
    "303fd597a76af00c728b3eb626493dc2a71585dcd679c5e07a338da44d24a060  "
    f"terramate_{_TERRAMATE}_linux_x86_64.tar.gz\n"
    "b3956602a1eeeee9ea40add6682375d3f74c87d47b269103653b7b825b1dfd4a  "
    f"terramate_{_TERRAMATE}_linux_arm64.tar.gz\n"
    "SHIPMATE_EOF\n"
)

_HTML = b"<!DOCTYPE html><html><body>rate limit exceeded</body></html>\n"

#: The verify steps' whole definitions but `run:`. The OpenTofu one carries the same `if:` as
#: `Install OpenTofu`: a job that never runs tofu must not fail on an OpenTofu signer change.
_EXPECTED_STEPS = {
    "tofu-sums": {
        "name": "Verify OpenTofu checksums",
        "id": "tofu-sums",
        "if": "${{ inputs.tofu != 'false' }}",
        "shell": "bash",
        "env": {"SHIPMATE_TOFU_VERSION": "${{ steps.versions.outputs.tofu }}"},
    },
    "terramate-sums": {
        "name": "Verify Terramate checksums",
        "id": "terramate-sums",
        "shell": "bash",
        "env": {"SHIPMATE_TERRAMATE_VERSION": "${{ steps.versions.outputs.terramate }}"},
    },
}


@pytest.fixture
def _signature_tools():
    """Skip without gpg or openssl locally; fail under `CI` (GitHub sets it), so a runner
    without them reds the signature guards instead of skipping them."""
    if shutil.which("gpg") and shutil.which("openssl"):
        return
    reason = "gpg and openssl verify the signatures; ubuntu-latest ships both"
    if os.environ.get("CI") == "true":
        pytest.fail(reason)
    pytest.skip(reason)


needs_tools = pytest.mark.usefixtures("_signature_tools")


def test_the_verify_steps_are_fail_closed():
    """No `continue-on-error`, and no `if:` beyond the OpenTofu step's own.

    Mutations: remove the `if:` on `Verify OpenTofu checksums`; add `continue-on-error: true`
    to `Verify Terramate checksums`.
    """
    got = {}
    for sid in _EXPECTED_STEPS:
        step = dict(step_by(_ACTION, id=sid))
        assert step.pop("run")
        got[sid] = step
    assert got == _EXPECTED_STEPS


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _genuine():
    return {p.name: p.read_bytes() for p in _FIXTURES.iterdir() if p.name != "README"}


def _run(tmp_path, sid, *, served=None, codes=None, body=None, curl_exit=0):
    """Run step `sid`'s body (or `body`) with curl serving `served` (basename -> bytes; the
    genuine fixtures by default) at HTTP 200, or `codes[basename]`, and exiting `curl_exit`.

    Return (result, $GITHUB_OUTPUT text, curl argv per call, gpg argv per call, RUNNER_TEMP).
    """
    tmp_path.mkdir(exist_ok=True)
    bin_dir, logs, serve, rt = (tmp_path / d for d in ("bin", "logs", "serve", "runner-temp"))
    for d in (bin_dir, logs, serve, rt):
        d.mkdir()
    for name, data in (_genuine() if served is None else served).items():
        (serve / name).write_bytes(data)
    for name, code in (codes or {}).items():
        (serve / f"{name}.code").write_text(code, encoding="utf-8")
    log = logs.as_posix()
    _stub(
        bin_dir,
        "curl",
        f"printf '%s\\x1f' \"$@\" >> '{log}/curl'\necho >> '{log}/curl'\n"
        'out=""; prev=""\n'
        'for a in "$@"; do if [ "$prev" = -o ]; then out="$a"; fi; prev="$a"; done\n'
        'name="${!#}"; name="${name##*/}"\n'
        f"serve='{serve.as_posix()}'\n"
        'if [ -f "$serve/$name" ]; then cp "$serve/$name" "$out"; fi\n'
        'if [ -f "$serve/$name.code" ]; then cat "$serve/$name.code"; else printf 200; fi\n'
        f"exit {curl_exit}",
    )
    gpg = shutil.which("gpg")
    _stub(
        bin_dir,
        "gpg",
        f"printf '%s\\x1f' \"$@\" >> '{log}/gpg'\necho >> '{log}/gpg'\n"
        f'exec "{gpg and gpg.replace(os.sep, "/")}" "$@"',
    )
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        body if body is not None else step_by(_ACTION, id=sid)["run"],
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": rt.as_posix(),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_ACTION_PATH": (ENGINE / "actions" / "setup").as_posix(),
            "SHIPMATE_TOFU_VERSION": _TOFU,
            "SHIPMATE_TERRAMATE_VERSION": _TERRAMATE,
        },
        timeout=120,
    )
    return r, out.read_text(encoding="utf-8"), _calls(logs / "curl"), _calls(logs / "gpg"), rt


def _calls(path):
    if not path.exists():
        return []
    return [line.split("\x1f")[:-1] for line in path.read_text(encoding="utf-8").splitlines()]


def _curl(url, out):
    """curl's whole argv for one download, hand-written."""
    return [
        "--retry-all-errors",
        "-sSL",
        "--retry",
        "3",
        "--connect-timeout",
        "10",
        "--speed-limit",
        "1024",
        "--speed-time",
        "30",
        "-o",
        out,
        "-w",
        "%{http_code}",
        url,
    ]


def _edit(body, old, new):
    """`body` with its one `old` replaced: a renamed target must red, not no-op."""
    assert body.count(old) == 1, f"{old!r} occurs {body.count(old)} times in the step body"
    return body.replace(old, new)


def _flip(data, needle):
    """`data` with the first byte of `needle` changed."""
    i = data.index(needle)
    return data[:i] + bytes([data[i] ^ 1]) + data[i + 1 :]


@bash_only
@needs_tools
def test_genuine_opentofu_sums_verify_and_write_both_digests(tmp_path):
    """Mutations: swap the output names; add a third curl call fetching a key; add
    `--keyserver` to gpg."""
    r, out, curl, gpg, rt = _run(tmp_path, "tofu-sums")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == _TOFU_OUTPUT
    d = f"{rt.as_posix()}/tofu-sums"
    assert curl == [
        _curl(f"{_TOFU_BASE}/{_TOFU_SUMS}", f"{d}/{_TOFU_SUMS}"),
        _curl(f"{_TOFU_BASE}/{_TOFU_SUMS}.gpgsig", f"{d}/{_TOFU_SUMS}.gpgsig"),
    ]
    key = f"{(ENGINE / 'actions' / 'setup').as_posix()}/../../keys/opentofu.asc"
    assert gpg == [
        ["--batch", "--import", key],
        [
            "--batch",
            "--status-fd",
            "1",
            "--verify",
            f"{d}/{_TOFU_SUMS}.gpgsig",
            f"{d}/{_TOFU_SUMS}",
        ],
    ]


@bash_only
@needs_tools
def test_genuine_terramate_sums_verify_and_write_both_lines(tmp_path):
    """Mutations: swap the output names; add a third curl call fetching a key."""
    r, out, curl, gpg, rt = _run(tmp_path, "terramate-sums")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == _TM_OUTPUT
    d = f"{rt.as_posix()}/terramate-sums"
    assert curl == [
        _curl(f"{_TM_BASE}/checksums.txt", f"{d}/checksums.txt"),
        _curl(f"{_TM_BASE}/checksums.txt.sig", f"{d}/checksums.txt.sig"),
    ]
    assert gpg == []


@bash_only
@needs_tools
def test_a_flipped_byte_in_the_opentofu_sums_fails(tmp_path):
    """Mutation: append `|| true` to the gpg verify inside its `$(...)`."""
    served = _genuine()
    served[_TOFU_SUMS] = _flip(served[_TOFU_SUMS], b"723bebb0")
    r, out, *_ = _run(tmp_path, "tofu-sums", served=served)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_TOFU_ERROR}the signature on {_TOFU_SUMS} did not verify against the committed "
        "OpenTofu key (gpg exit 1).\n"
    )
    assert out == _refusal(r.stdout)


@bash_only
@needs_tools
def test_a_flipped_byte_in_the_terramate_sums_fails(tmp_path):
    """Mutation: replace `|| rc=$?` after the openssl verify with `|| true`."""
    served = _genuine()
    served["checksums.txt"] = _flip(served["checksums.txt"], b"303fd597")
    r, out, *_ = _run(tmp_path, "terramate-sums", served=served)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_TM_ERROR}the signature on checksums.txt did not verify against the committed "
        "Terramate key (openssl exit 1).\n"
    )
    assert out == _refusal(r.stdout)


@bash_only
@needs_tools
def test_a_signature_by_another_key_fails_the_whole_fingerprint_compare(tmp_path):
    """The genuine signature verifies, so only the VALIDSIG compare can refuse it.

    Mutation: check gpg's exit code alone (drop the fingerprint compare).
    """
    other = _SIGNER[:-1] + "1"
    body = _edit(step_by(_ACTION, id="tofu-sums")["run"], _SIGNER, other)
    r, out, *_ = _run(tmp_path, "tofu-sums", body=body)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_TOFU_ERROR}{_TOFU_SUMS} is signed by {_SIGNER}, not the OpenTofu signer {other}.\n"
    )
    assert out == _refusal(r.stdout)


@pytest.mark.parametrize(
    ("sid", "name", "message"),
    [
        (
            "tofu-sums",
            f"{_TOFU_SUMS}.gpgsig",
            (
                f"{_TOFU_ERROR}the signature on {_TOFU_SUMS} did not verify against the committed "
                "OpenTofu key (gpg exit 2).\n"
            ),
        ),
        (
            "terramate-sums",
            "checksums.txt.sig",
            f"{_TM_ERROR}{_TM_BASE}/checksums.txt.sig is not base64.\n",
        ),
    ],
)
@bash_only
@needs_tools
def test_an_html_signature_at_200_fails_with_one_annotation(tmp_path, sid, name, message):
    """A proxy or rate-limit page served as the signature.

    Mutations: append `|| true` to the gpg verify inside its `$(...)`; drop the `|| fail` after
    `base64 -d` (bash exits on the bare failure with no annotation).
    """
    served = _genuine()
    served[name] = _HTML
    r, out, *_ = _run(tmp_path, sid, served=served)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == message
    assert out == _refusal(r.stdout)


@pytest.mark.parametrize(
    ("sid", "url"),
    [
        ("tofu-sums", f"{_TOFU_BASE}/{_TOFU_SUMS}"),
        ("tofu-sums", f"{_TOFU_BASE}/{_TOFU_SUMS}.gpgsig"),
        ("terramate-sums", f"{_TM_BASE}/checksums.txt"),
        ("terramate-sums", f"{_TM_BASE}/checksums.txt.sig"),
    ],
)
@bash_only
@needs_tools
def test_a_404_fails_naming_the_url(tmp_path, sid, url):
    """Mutations: drop the `!= 200` status check; drop the `refusal=` write from either
    step's `fail()`."""
    name = url.rsplit("/", 1)[1]
    served = _genuine()
    served[name] = b"Not Found"
    r, out, *_ = _run(tmp_path, sid, served=served, codes={name: "404"})
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    prefix = _TOFU_ERROR if sid == "tofu-sums" else _TM_ERROR
    assert r.stdout == f"{prefix}{url} answered HTTP 404 (curl exit 0).\n"
    tool = f"OpenTofu {_TOFU}" if sid == "tofu-sums" else f"Terramate {_TERRAMATE}"
    assert out == f"refusal={tool}: {url} answered HTTP 404 (curl exit 0).\n"


@bash_only
def test_a_curl_older_than_7_71_is_told_its_floor_not_to_re_run(tmp_path):
    """curl exits 2 on an option it does not know, which a re-run repeats. The Terramate verify
    step makes the first download of the action, so the floor is reported there.

    Mutation: delete the exit-2 branch (the generic HTTP message appears).
    """
    r, out, curl, *_ = _run(tmp_path, "terramate-sums", codes={"checksums.txt": ""}, curl_exit=2)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_TM_ERROR}curl exited 2 on an option it does not know; shipmate needs curl 7.71 "
        "or later (CONTRACT.md §Runner prerequisites).\n"
    )
    assert (len(curl), out) == (1, _refusal(r.stdout))


@pytest.mark.parametrize(
    ("sid", "missing", "present"),
    [
        ("terramate-sums", "curl", ("openssl", "base64")),
        ("terramate-sums", "openssl", ("curl", "base64")),
        ("terramate-sums", "base64", ("curl", "openssl")),
        ("tofu-sums", "gpg", ()),
    ],
)
@bash_only
def test_a_missing_prerequisite_is_named_not_reported_as_a_signature_failure(
    tmp_path, sid, missing, present
):
    """PATH holds only never-run stubs for `present`. The Terramate step runs first and checks
    the tools it and the installers need; the OpenTofu step checks gpg alone.

    Mutations: drop `curl`, `openssl` or `base64` from the Terramate step's `command -v` loop;
    delete the OpenTofu step's `command -v gpg` line.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in present:
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        step_by(_ACTION, id=sid)["run"],
        {
            "PATH": str(bin_dir),
            "RUNNER_TEMP": (tmp_path / "runner-temp").as_posix(),
            "GITHUB_OUTPUT": str(out),
            "SHIPMATE_TOFU_VERSION": _TOFU,
            "SHIPMATE_TERRAMATE_VERSION": _TERRAMATE,
        },
    )
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    prefix = _TOFU_ERROR if sid == "tofu-sums" else _TM_ERROR
    assert r.stdout == f"{prefix}{missing} is not installed on the runner.\n"
    assert out.read_text(encoding="utf-8") == _refusal(r.stdout)


@pytest.fixture
def gnupg_home():
    """A GNUPGHOME under the system temp directory: gpg-agent's socket path lives in it, and a
    pytest `tmp_path` is too long for one on Windows. Git for Windows' gpg reads `C:/x` as a
    relative path, so it gets `/c/x`."""
    path = Path(tempfile.mkdtemp(prefix="gpg"))
    p = path.as_posix()
    home = f"/{p[0].lower()}{p[2:]}" if os.name == "nt" and p[1:2] == ":" else p
    yield home
    gpgconf = shutil.which("gpgconf")
    if gpgconf:
        subprocess.run([gpgconf, "--homedir", home, "--kill", "gpg-agent"], check=False)
    shutil.rmtree(path, ignore_errors=True)


def _keygen(tmp_path, home):
    """A throwaway OpenTofu-style gpg key in `home` and a Terramate-style EC key. Return
    (armored gpg public key path, gpg fingerprint, EC private key path, EC public key path)."""
    keys = tmp_path / "keys"
    keys.mkdir()
    k = keys.as_posix()
    r = run_step(
        tmp_path,
        "set -euo pipefail\n"
        "gpg --batch --pinentry-mode loopback --passphrase '' "
        "--quick-gen-key test@example.invalid ed25519 sign 0\n"
        f"gpg --batch --armor --export test@example.invalid > '{k}/gpg.asc'\n"
        "gpg --batch --with-colons --list-keys test@example.invalid "
        "| awk -F: '$1 == \"fpr\" && !n++ {print $10}'\n"
        f"openssl ecparam -genkey -name prime256v1 -noout -out '{k}/ec.pem'\n"
        f"openssl ec -in '{k}/ec.pem' -pubout -out '{k}/ec.pub'\n",
        {**os.environ, "GNUPGHOME": home},
        timeout=120,
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    return f"{k}/gpg.asc", r.stdout.strip(), f"{k}/ec.pem", f"{k}/ec.pub"


def _sign(tmp_path, command, data, env):
    """Run `command` with `$1` a file holding `data`; return the bytes it wrote to `$1.sig`."""
    path = tmp_path / "to-sign"
    path.write_bytes(data)
    r = run_step(tmp_path, f'set -euo pipefail\nset -- "{path.as_posix()}"\n{command}\n', env)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    return (tmp_path / "to-sign.sig").read_bytes()


def _drop(data, *suffixes):
    return b"".join(
        line for line in data.splitlines(keepends=True) if not line.rstrip().endswith(suffixes)
    )


@pytest.mark.parametrize(
    ("dropped", "missing"),
    [
        ((b"_linux_arm64.zip",), f"tofu_{_TOFU}_linux_arm64.zip"),
        ((b"_linux_arm64.zip", b"_linux_amd64.zip"), f"tofu_{_TOFU}_linux_amd64.zip"),
    ],
    ids=["arm64", "both"],
)
@bash_only
@needs_tools
def test_signed_opentofu_sums_without_an_asset_line_fail_naming_it(
    tmp_path, gnupg_home, dropped, missing
):
    """Mutation: drop the `[ -n "$d" ] || fail` guard (an empty digest line is written)."""
    pub, fpr, _, _ = _keygen(tmp_path, gnupg_home)
    sums = _drop(_genuine()[_TOFU_SUMS], *dropped)
    sig = _sign(
        tmp_path,
        'gpg --batch --pinentry-mode loopback --passphrase "" -o "$1.sig" --detach-sign "$1"',
        sums,
        {**os.environ, "GNUPGHOME": gnupg_home},
    )
    body = _edit(step_by(_ACTION, id="tofu-sums")["run"], _TOFU_KEY, pub)
    body = _edit(body, _SIGNER, fpr)
    served = {_TOFU_SUMS: sums, f"{_TOFU_SUMS}.gpgsig": sig}
    r, out, *_ = _run(tmp_path / "run", "tofu-sums", served=served, body=body)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_TOFU_ERROR}{_TOFU_SUMS} has no line for {missing}.\n"
    assert out == _refusal(r.stdout)


@pytest.mark.parametrize(
    ("dropped", "missing"),
    [
        ((b"_linux_arm64.tar.gz",), f"terramate_{_TERRAMATE}_linux_arm64.tar.gz"),
        (
            (b"_linux_arm64.tar.gz", b"_linux_x86_64.tar.gz"),
            f"terramate_{_TERRAMATE}_linux_x86_64.tar.gz",
        ),
    ],
    ids=["arm64", "both"],
)
@bash_only
@needs_tools
def test_signed_terramate_sums_without_an_asset_line_fail_naming_it(
    tmp_path, gnupg_home, dropped, missing
):
    """Mutation: drop the `[ -n "$line" ] || fail` guard (an empty line is written)."""
    _, _, pem, pub = _keygen(tmp_path, gnupg_home)
    sums = _drop(_genuine()["checksums.txt"], *dropped)
    der = _sign(
        tmp_path,
        f'openssl dgst -sha256 -sign "{pem}" -out "$1.sig" "$1"',
        sums,
        dict(os.environ),
    )
    body = _edit(step_by(_ACTION, id="terramate-sums")["run"], _TM_KEY, pub)
    served = {"checksums.txt": sums, "checksums.txt.sig": base64.b64encode(der)}
    r, out, *_ = _run(tmp_path / "run", "terramate-sums", served=served, body=body)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_TM_ERROR}checksums.txt has no line for {missing}.\n"
    assert out == _refusal(r.stdout)
