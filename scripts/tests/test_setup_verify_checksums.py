"""actions/setup verifies each tool's release checksum file against a signer key in this repo.

Each verify step's real `run:` body executes here with real `gpg`, `openssl` and `base64`; its
signature logic is `verify_terramate` and `verify_tofu` in `actions/setup/release.sh`. Only
`curl` is a stub: it appends its argv to a log and serves the raw upstream files under
`fixtures/release-sums/` by URL basename. `gpg`, `gpgconf` and `openssl` wrappers log their argv
(`gpg` and `gpgconf` also their `GNUPGHOME`) and run the real tools. A cache hit is simulated by
files placed in `$RUNNER_TEMP/<tool>-release` before the step runs, with `SHIPMATE_CACHE_KEY` set
to the entry's key.

Threat model: an accidental regression in a SHA-pinned, reviewed engine file -- a dropped
signature check, an exit-code-only check, a fetched key, a lost guard. A hostile edit to the
step is out of scope. Cases that need a correctly signed but edited checksum file sign it with
a throwaway key made here, and run the step against a copy of `release.sh` with the key path
(and, for OpenTofu, the fingerprint constant) substituted.
"""

import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from _loader import (
    ENGINE,
    bash_only,
    bash_symlink,
    run_step,
    setup_curl_argv,
    step_by,
    write_stub,
)

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

_REPO = "o/r"
_TOFU_CACHE_KEY = f"shipmate-tools-Linux-X64-tofu-{_TOFU}"
_TM_CACHE_KEY = f"shipmate-tools-Linux-X64-terramate-{_TERRAMATE}"


def _warning(sid, reason):
    """The one warning step `sid` prints before it downloads a failed cache entry's files again."""
    tool, key, names = {
        "tofu-sums": (
            f"OpenTofu {_TOFU}",
            _TOFU_CACHE_KEY,
            f"{_TOFU_SUMS} and {_TOFU_SUMS}.gpgsig",
        ),
        "terramate-sums": (
            f"Terramate {_TERRAMATE}",
            _TM_CACHE_KEY,
            "checksums.txt and checksums.txt.sig",
        ),
    }[sid]
    return (
        f"::warning title=Tool cache entry failed verification::{tool}: {reason} Downloading "
        f"{names} again. Every job repeats this download until the entry is deleted: "
        f"gh cache delete {key} --repo {_REPO}\n"
    )


def _refusal(stdout):
    """The `refusal=` line a failed step writes: its one annotation without the
    `::error title=...::` prefix. Every caller pins `stdout` whole."""
    return "refusal=" + stdout.split("::", 2)[2]


#: Each step's whole $GITHUB_OUTPUT on the genuine fixtures, digests copied by hand from them.
_TOFU_OUTPUT = (
    "tofu_sha256<<SHIPMATE_EOF\n"
    "723bebb0df8411daff1e5fc3862e28ff5581fe5634087f4c29a9aa41d78208b5  "
    f"tofu_{_TOFU}_linux_amd64.zip\n"
    "6e815c58a09ee957de606ff48cefad082942e70afc43b737ff975654c54ec453  "
    f"tofu_{_TOFU}_linux_arm64.zip\n"
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
        "env": {
            "SHIPMATE_TOFU_VERSION": "${{ steps.versions.outputs.tofu }}",
            "SHIPMATE_CACHE_KEY": "${{ steps.tofu-cache.outputs.cache-matched-key }}",
        },
    },
    "terramate-sums": {
        "name": "Verify Terramate checksums",
        "id": "terramate-sums",
        "shell": "bash",
        "env": {
            "SHIPMATE_TERRAMATE_VERSION": "${{ steps.versions.outputs.terramate }}",
            "SHIPMATE_CACHE_KEY": "${{ steps.terramate-cache.outputs.cache-matched-key }}",
        },
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
    to `Verify Terramate checksums`; map `Verify Terramate checksums`' `SHIPMATE_CACHE_KEY` to
    `steps.tofu-cache.outputs.cache-primary-key`.
    """
    got = {}
    for sid in _EXPECTED_STEPS:
        step = dict(step_by(_ACTION, id=sid))
        run = step.pop("run")
        assert run
        got[sid] = step
    assert got == _EXPECTED_STEPS


def _genuine():
    return {p.name: p.read_bytes() for p in _FIXTURES.iterdir() if p.name != "README"}


def _run(
    tmp_path,
    sid,
    *,
    served=None,
    codes=None,
    action=None,
    curl_exit=0,
    cached=None,
    key=None,
    env=None,
    gpgconf_rc=None,
):
    """Run step `sid`'s body with curl serving `served` (basename -> bytes; the genuine fixtures
    by default) at HTTP 200, or `codes[basename]`, and exiting `curl_exit`. `cached` (basename ->
    bytes, or None for a directory) is placed in the step's release directory first, and `key`,
    when given, is the `SHIPMATE_CACHE_KEY`. `action` (default the engine's) is
    `GITHUB_ACTION_PATH`; `env` is added last, over a host environment without `GNUPGHOME`. The
    `gpgconf` wrapper exits `gpgconf_rc` instead of running the real one when that is given.

    Return (result, $GITHUB_OUTPUT text, curl argv per call, gpg argv per call, RUNNER_TEMP);
    openssl's and gpgconf's argv per call are in `tmp_path/logs/`, and each gpg and gpgconf
    call's `GNUPGHOME` in `logs/gpg.home` and `logs/gpgconf.home`.
    """
    tmp_path.mkdir(exist_ok=True)
    bin_dir, logs, serve, rt = (tmp_path / d for d in ("bin", "logs", "serve", "runner-temp"))
    for d in (bin_dir, logs, serve, rt):
        d.mkdir(exist_ok=True)
    for name, data in (_genuine() if served is None else served).items():
        (serve / name).write_bytes(data)
    release = rt / ("tofu-release" if sid == "tofu-sums" else "terramate-release")
    for name, data in (cached or {}).items():
        release.mkdir(exist_ok=True)
        if data is None:
            (release / name).mkdir()
        else:
            (release / name).write_bytes(data)
    for name, code in (codes or {}).items():
        (serve / f"{name}.code").write_text(code, encoding="utf-8")
    log = logs.as_posix()
    write_stub(
        bin_dir,
        "curl",
        f"printf '%s\\x1f' \"$@\" >> '{log}/curl'\necho >> '{log}/curl'\n"
        'out=""; prev=""\n'
        'for a in "$@"; do if [ "$prev" = -o ]; then out="$a"; fi; prev="$a"; done\n'
        'name="${!#}"; name="${name##*/}"\n'
        f"serve='{serve.as_posix()}'\n"
        'if [ -f "$serve/$name" ]; then cp "$serve/$name" "$out"; fi\n'
        'code=200; if [ -f "$serve/$name.code" ]; then code="$(cat "$serve/$name.code")"; fi\n'
        'printf \'%s %s\' "$code" "${!#}"\n'
        f"exit {curl_exit}",
    )
    gpg = shutil.which("gpg")
    write_stub(
        bin_dir,
        "gpg",
        f"printf '%s\\x1f' \"$@\" >> '{log}/gpg'\necho >> '{log}/gpg'\n"
        f"echo \"${{GNUPGHOME:-}}\" >> '{log}/gpg.home'\n"
        f'exec "{gpg and gpg.replace(os.sep, "/")}" "$@"',
    )
    gpgconf = shutil.which("gpgconf")
    write_stub(
        bin_dir,
        "gpgconf",
        f"printf '%s\\x1f' \"$@\" >> '{log}/gpgconf'\necho >> '{log}/gpgconf'\n"
        f"echo \"${{GNUPGHOME:-}}\" >> '{log}/gpgconf.home'\n"
        + (
            f"exit {gpgconf_rc}"
            if gpgconf_rc is not None
            else f'exec "{gpgconf and gpgconf.replace(os.sep, "/")}" "$@"'
        ),
    )
    openssl = shutil.which("openssl")
    write_stub(
        bin_dir,
        "openssl",
        f"printf '%s\\x1f' \"$@\" >> '{log}/openssl'\necho >> '{log}/openssl'\n"
        f'exec "{openssl and openssl.replace(os.sep, "/")}" "$@"',
    )
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        step_by(_ACTION, id=sid)["run"],
        {
            **{k: v for k, v in os.environ.items() if k != "GNUPGHOME"},
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": rt.as_posix(),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_ACTION_PATH": action or (ENGINE / "actions" / "setup").as_posix(),
            "SHIPMATE_TOFU_VERSION": _TOFU,
            "SHIPMATE_TERRAMATE_VERSION": _TERRAMATE,
            "GITHUB_REPOSITORY": _REPO,
            **({} if key is None else {"SHIPMATE_CACHE_KEY": key}),
            **(env or {}),
        },
        timeout=120,
    )
    return r, out.read_text(encoding="utf-8"), _calls(logs / "curl"), _calls(logs / "gpg"), rt


def _calls(path):
    if not path.exists():
        return []
    return [line.split("\x1f")[:-1] for line in path.read_text(encoding="utf-8").splitlines()]


def _curl(url, out):
    return setup_curl_argv(url, out)


def _downloads(sid, rt):
    """curl's whole argv for each of step `sid`'s two downloads, in order."""
    if sid == "tofu-sums":
        base, names, d = _TOFU_BASE, [_TOFU_SUMS, f"{_TOFU_SUMS}.gpgsig"], "tofu-release"
    else:
        base, names, d = _TM_BASE, ["checksums.txt", "checksums.txt.sig"], "terramate-release"
    return [_curl(f"{base}/{n}", f"{rt.as_posix()}/{d}/{n}") for n in names]


def _verifier(sid, rt):
    """The signature tool's whole argv per call: gpg for OpenTofu, openssl for Terramate."""
    action = (ENGINE / "actions" / "setup").as_posix()
    if sid == "tofu-sums":
        d = f"{rt.as_posix()}/tofu-release"
        return [
            ["--batch", "--import", f"{action}/../../keys/opentofu.asc"],
            [
                "--batch",
                "--status-fd",
                "1",
                "--verify",
                f"{d}/{_TOFU_SUMS}.gpgsig",
                f"{d}/{_TOFU_SUMS}",
            ],
        ]
    return [
        [
            "dgst",
            "-sha256",
            "-verify",
            f"{action}/../../keys/terramate-cosign.pub",
            "-signature",
            f"{rt.as_posix()}/terramate-checksums.der",
            f"{rt.as_posix()}/terramate-release/checksums.txt",
        ]
    ]


def _lib(tmp_path, *edits):
    """A copy of `actions/setup` and `keys/` under `tmp_path/engine` with each (old, new) of
    `edits` applied once to `release.sh`; return the copy's `GITHUB_ACTION_PATH`. A renamed
    target reds the edit instead of leaving the copy unchanged."""
    engine = tmp_path / "engine"
    shutil.copytree(ENGINE / "keys", engine / "keys")
    action = engine / "actions" / "setup"
    shutil.copytree(ENGINE / "actions" / "setup", action)
    lib = (action / "release.sh").read_text(encoding="utf-8")
    for old, new in edits:
        assert lib.count(old) == 1, f"{old!r} occurs {lib.count(old)} times in release.sh"
        lib = lib.replace(old, new)
    (action / "release.sh").write_text(lib, encoding="utf-8", newline="\n")
    return action.as_posix()


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
    d = f"{rt.as_posix()}/tofu-release"
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
    """The DER signature is written outside the release directory, which the cache saves.

    Mutations: swap the output names; add a third curl call fetching a key; write the DER
    signature to `$dir/checksums.txt.der`.
    """
    r, out, curl, gpg, rt = _run(tmp_path, "terramate-sums")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == _TM_OUTPUT
    assert curl == _downloads("terramate-sums", rt)
    assert gpg == []
    assert _calls(tmp_path / "logs" / "openssl") == _verifier("terramate-sums", rt)


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
    action = _lib(tmp_path / "lib", (_SIGNER, other))
    r, out, *_ = _run(tmp_path, "tofu-sums", action=action)
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

    Mutations: append `|| true` to the gpg verify inside its `$(...)`; drop the `|| { ... }` after
    `base64 -d` (openssl then refuses the DER, and the annotation names openssl).
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
    """Mutations: in `fetch`, return on curl exit 0 alone; drop the `refusal=` write from
    `fail`."""
    name = url.rsplit("/", 1)[1]
    served = _genuine()
    served[name] = b"Not Found"
    r, out, *_ = _run(tmp_path, sid, served=served, codes={name: "404"})
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    prefix = _TOFU_ERROR if sid == "tofu-sums" else _TM_ERROR
    assert r.stdout == f"{prefix}{url} answered HTTP 404 from {url} (curl exit 0).\n"
    tool = f"OpenTofu {_TOFU}" if sid == "tofu-sums" else f"Terramate {_TERRAMATE}"
    assert out == f"refusal={tool}: {url} answered HTTP 404 from {url} (curl exit 0).\n"


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
        ("terramate-sums", "curl", ("openssl", "base64", "sha256sum")),
        ("terramate-sums", "openssl", ("curl", "base64", "sha256sum")),
        ("terramate-sums", "base64", ("curl", "openssl", "sha256sum")),
        ("terramate-sums", "sha256sum", ("curl", "openssl", "base64")),
        ("tofu-sums", "gpg", ("curl", "openssl", "base64", "sha256sum", "gpgconf")),
        ("tofu-sums", "gpgconf", ("curl", "openssl", "base64", "sha256sum", "gpg")),
    ],
)
@bash_only
def test_a_missing_prerequisite_is_named_not_reported_as_a_signature_failure(
    tmp_path, sid, missing, present
):
    """PATH holds only never-run stubs for `present`. Every step checks the four base tools;
    the OpenTofu step also checks gpg and gpgconf.

    Mutations: drop `curl`, `openssl`, `base64` or `sha256sum` from `require`'s base list;
    drop `gpg` or `gpgconf` from the OpenTofu step's `require`.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in present:
        write_stub(bin_dir, name, "exit 99")
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        step_by(_ACTION, id=sid)["run"],
        {
            "PATH": str(bin_dir),
            "RUNNER_TEMP": (tmp_path / "runner-temp").as_posix(),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_ACTION_PATH": (ENGINE / "actions" / "setup").as_posix(),
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
    """Mutation: drop the `[ -n "$line" ] || { ... }` guard (an empty digest line is written)."""
    pub, fpr, _, _ = _keygen(tmp_path, gnupg_home)
    sums = _drop(_genuine()[_TOFU_SUMS], *dropped)
    sig = _sign(
        tmp_path,
        'gpg --batch --pinentry-mode loopback --passphrase "" -o "$1.sig" --detach-sign "$1"',
        sums,
        {**os.environ, "GNUPGHOME": gnupg_home},
    )
    action = _lib(tmp_path / "lib", (_TOFU_KEY, pub), (_SIGNER, fpr))
    served = {_TOFU_SUMS: sums, f"{_TOFU_SUMS}.gpgsig": sig}
    r, out, *_ = _run(tmp_path / "run", "tofu-sums", served=served, action=action)
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
    """Mutation: drop the `[ -n "$line" ] || { ... }` guard (an empty line is written)."""
    _, _, pem, pub = _keygen(tmp_path, gnupg_home)
    sums = _drop(_genuine()["checksums.txt"], *dropped)
    der = _sign(
        tmp_path,
        f'openssl dgst -sha256 -sign "{pem}" -out "$1.sig" "$1"',
        sums,
        dict(os.environ),
    )
    action = _lib(tmp_path / "lib", (_TM_KEY, pub))
    served = {"checksums.txt": sums, "checksums.txt.sig": base64.b64encode(der)}
    r, out, *_ = _run(tmp_path / "run", "terramate-sums", served=served, action=action)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_TM_ERROR}checksums.txt has no line for {missing}.\n"
    assert out == _refusal(r.stdout)


_SUMS_NAMES = {
    "tofu-sums": (_TOFU_SUMS, f"{_TOFU_SUMS}.gpgsig"),
    "terramate-sums": ("checksums.txt", "checksums.txt.sig"),
}
_CACHE_KEYS = {"tofu-sums": _TOFU_CACHE_KEY, "terramate-sums": _TM_CACHE_KEY}
_TOOLS = {"tofu-sums": f"OpenTofu {_TOFU}", "terramate-sums": f"Terramate {_TERRAMATE}"}
_OUTPUTS = {"tofu-sums": _TOFU_OUTPUT, "terramate-sums": _TM_OUTPUT}


def _signature_log(sid, tmp_path, gpg):
    return gpg if sid == "tofu-sums" else _calls(tmp_path / "logs" / "openssl")


@pytest.mark.parametrize("sid", ["tofu-sums", "terramate-sums"])
@bash_only
@needs_tools
def test_a_genuine_cache_hit_downloads_nothing_and_verifies_the_same_way(tmp_path, sid):
    """Mutation: run `download` before the restored-entry `if` (curl is called twice)."""
    r, out, curl, gpg, rt = _run(
        tmp_path, sid, cached={n: _genuine()[n] for n in _SUMS_NAMES[sid]}, key=_CACHE_KEYS[sid]
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert (r.stdout, out, curl) == ("", _OUTPUTS[sid], [])
    assert _signature_log(sid, tmp_path, gpg) == _verifier(sid, rt)


#: A cached file that fails verification, per tool and file kind, and the reason `fail()` would
#: have given after its `<Tool> <version>: ` prefix.
_POISONED = [
    (
        "tofu-sums",
        _TOFU_SUMS,
        b"723bebb0",
        (
            f"the signature on {_TOFU_SUMS} did not verify against the committed OpenTofu key "
            "(gpg exit 1)."
        ),
    ),
    (
        "terramate-sums",
        "checksums.txt",
        b"303fd597",
        (
            "the signature on checksums.txt did not verify against the committed Terramate key "
            "(openssl exit 1)."
        ),
    ),
    (
        "tofu-sums",
        f"{_TOFU_SUMS}.gpgsig",
        None,
        (
            f"the signature on {_TOFU_SUMS} did not verify against the committed OpenTofu key "
            "(gpg exit 2)."
        ),
    ),
    (
        "terramate-sums",
        "checksums.txt.sig",
        None,
        f"{_TM_BASE}/checksums.txt.sig is not base64.",
    ),
]


def _poison(name, needle):
    """The genuine `name` with a flipped byte at `needle`, or an HTML page when `needle` is None."""
    return _HTML if needle is None else _flip(_genuine()[name], needle)


@pytest.mark.parametrize(("sid", "name", "needle", "reason"), _POISONED)
@bash_only
@needs_tools
def test_a_cached_file_that_fails_is_downloaded_again_with_one_warning(
    tmp_path, sid, name, needle, reason
):
    """A poisoned entry costs a download, never a wrong digest, and names its key for deletion.

    Mutations: fail instead of downloading again (trust the hit: exit 1); download again without
    the warning.
    """
    cached = {n: _genuine()[n] for n in _SUMS_NAMES[sid]}
    cached[name] = _poison(name, needle)
    r, out, curl, _, rt = _run(tmp_path, sid, cached=cached, key=_CACHE_KEYS[sid])
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == _warning(sid, reason)
    assert (out, curl) == (_OUTPUTS[sid], _downloads(sid, rt))


@pytest.mark.parametrize("sid", ["tofu-sums", "terramate-sums"])
@bash_only
@needs_tools
def test_a_restored_checksum_path_that_is_a_directory_is_downloaded_again(tmp_path, sid):
    """A restored path that is not a regular file costs a download, not the job.

    Mutation: `rm -f` in place of `rm -rf` in `drop` (the step exits 1 on the directory).
    """
    name, sig = _SUMS_NAMES[sid]
    cached = {name: None, sig: _genuine()[sig]}
    r, out, curl, _, rt = _run(tmp_path, sid, cached=cached, key=_CACHE_KEYS[sid])
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    d = rt / ("tofu-release" if sid == "tofu-sums" else "terramate-release")
    reason = f"{d.as_posix()}/{name} is not a regular file."
    assert r.stdout == _warning(sid, reason)
    assert (out, curl) == (_OUTPUTS[sid], _downloads(sid, rt))


@pytest.mark.parametrize(("sid", "name", "needle", "reason"), _POISONED[:2])
@bash_only
@needs_tools
def test_a_failed_entry_whose_fresh_copy_fails_too_fails_after_one_download(
    tmp_path, sid, name, needle, reason
):
    """Mutation: download again until verification succeeds (a bounded three-attempt loop: six
    curl calls, not two; an unbounded one never ends)."""
    cached = {n: _genuine()[n] for n in _SUMS_NAMES[sid]}
    cached[name] = _poison(name, needle)
    served = _genuine()
    served[name] = cached[name]
    r, out, curl, _, rt = _run(tmp_path, sid, served=served, cached=cached, key=_CACHE_KEYS[sid])
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    error = f"{_TOFU_ERROR if sid == 'tofu-sums' else _TM_ERROR}{reason}\n"
    assert r.stdout == _warning(sid, reason) + error
    assert (out, curl) == (f"refusal={_TOOLS[sid]}: {reason}\n", _downloads(sid, rt))


@pytest.mark.parametrize("sid", ["tofu-sums", "terramate-sums"])
@bash_only
@needs_tools
def test_files_present_without_a_restored_key_are_downloaded_again(tmp_path, sid):
    """Nothing explains a file present on a miss, so it is replaced, never verified.

    Mutation: ignore the key and keep any present file (curl is not called).
    """
    cached = {n: _genuine()[n] for n in _SUMS_NAMES[sid]}
    r, out, curl, _, rt = _run(tmp_path, sid, cached=cached, key="")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert (r.stdout, out, curl) == ("", _OUTPUTS[sid], _downloads(sid, rt))


#: Per verify step id, the install step that follows it: (its name, its digest variable, the
#: archive name, the extracting command to stub, that stub's body).
_INSTALLS = {
    "tofu-sums": (
        "Install OpenTofu",
        "SHIPMATE_TOFU_SHA256",
        f"tofu_{_TOFU}_linux_amd64.zip",
        "python3",
        (
            'mkdir -p "$5"\n'
            f'printf "#!/usr/bin/env bash\\necho \\"OpenTofu v{_TOFU}\\"\\n" > "$5/tofu"'
        ),
    ),
    "terramate-sums": (
        "Install Terramate",
        "SHIPMATE_TERRAMATE_SHA256",
        f"terramate_{_TERRAMATE}_linux_x86_64.tar.gz",
        "tar",
        (
            f'printf "#!/usr/bin/env bash\\necho {_TERRAMATE}\\n" > "$4/terramate"\n'
            'chmod +x "$4/terramate"'
        ),
    ),
}

#: Each release directory's whole listing after verify and install, hand-written.
_RELEASE_FILES = {
    "tofu-sums": sorted([_TOFU_SUMS, f"{_TOFU_SUMS}.gpgsig", f"tofu_{_TOFU}_linux_amd64.zip"]),
    "terramate-sums": sorted(
        ["checksums.txt", "checksums.txt.sig", f"terramate_{_TERRAMATE}_linux_x86_64.tar.gz"]
    ),
}


def _install_after(tmp_path, sid, rt, archive):
    """Run the install step that follows `sid` on `rt`, after `sid` passed, with curl serving
    `archive` and the extract stubbed; return its result."""
    step, digest_var, asset, extract, extract_body = _INSTALLS[sid]
    bin_dir = tmp_path / "bin"
    write_stub(bin_dir, "uname", 'if [ "$1" = -s ]; then echo Linux; else echo x86_64; fi')
    write_stub(bin_dir, extract, extract_body)
    path = tmp_path / "github_path"
    path.write_text("", encoding="utf-8")
    return run_step(
        tmp_path,
        step_by(_ACTION, name=step)["run"],
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": rt.as_posix(),
            "GITHUB_PATH": str(path),
            "GITHUB_OUTPUT": str(tmp_path / "github_output"),
            "GITHUB_ACTION_PATH": (ENGINE / "actions" / "setup").as_posix(),
            "SHIPMATE_TOFU_VERSION": _TOFU,
            "SHIPMATE_TERRAMATE_VERSION": _TERRAMATE,
            digest_var: f"{hashlib.sha256(archive).hexdigest()}  {asset}",
        },
    )


@pytest.mark.parametrize("sid", ["tofu-sums", "terramate-sums"])
@bash_only
@needs_tools
def test_the_release_dir_holds_exactly_the_release_files(tmp_path, sid):
    """The cache saves the whole directory, so a derived file there would be saved and restored.

    Mutation: write the DER signature into `terramate-release` (`$dir/checksums.txt.der`).
    """
    archive = b"release archive"
    served = {**_genuine(), _INSTALLS[sid][2]: archive}
    r, _, _, _, rt = _run(tmp_path, sid, served=served)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    r = _install_after(tmp_path, sid, rt, archive)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    release = rt / ("tofu-release" if sid == "tofu-sums" else "terramate-release")
    assert sorted(os.listdir(release)) == _RELEASE_FILES[sid]
    if sid == "terramate-sums":
        assert (rt / "terramate-checksums.der").is_file()


@pytest.mark.parametrize("sid", ["tofu-sums", "terramate-sums"])
@pytest.mark.parametrize("root", ["symlink", "file"])
@bash_only
@needs_tools
def test_a_restored_root_that_is_not_a_real_directory_is_replaced_and_installs(tmp_path, sid, root):
    """The release root is a symlink to a directory holding the genuine files, or a regular
    file. It is removed (a link's target stays as it was), the files download again, and the
    install step that follows succeeds.

    Mutations: `[ ! -d "$1" ]` alone in `release_dir` (the symlink is followed: no download);
    drop `release_dir`'s removal (mkdir fails on the file root).
    """
    rt = tmp_path / "runner-temp"
    rt.mkdir(parents=True)
    release = rt / ("tofu-release" if sid == "tofu-sums" else "terramate-release")
    target = tmp_path / "elsewhere"
    if root == "symlink":
        target.mkdir()
        for n in _SUMS_NAMES[sid]:
            (target / n).write_bytes(_genuine()[n])
        bash_symlink(tmp_path, target, release)
    else:
        release.write_bytes(b"not a directory")
    archive = b"release archive"
    served = {**_genuine(), _INSTALLS[sid][2]: archive}
    r, out, curl, _, rt = _run(tmp_path, sid, served=served, key=_CACHE_KEYS[sid])
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    reason = f"{release.as_posix()}/{_SUMS_NAMES[sid][0]} is not a regular file."
    assert (r.stdout, out, curl) == (_warning(sid, reason), _OUTPUTS[sid], _downloads(sid, rt))
    assert (release.is_symlink(), release.is_dir()) == (False, True)
    if root == "symlink":
        assert sorted(os.listdir(target)) == sorted(_SUMS_NAMES[sid])
    r = _install_after(tmp_path, sid, rt, archive)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@pytest.mark.parametrize("sid", ["tofu-sums", "terramate-sums"])
@bash_only
@needs_tools
def test_a_restored_checksum_file_that_is_a_symlink_is_downloaded_again(tmp_path, sid):
    """The link points at a genuine copy, so only the symlink test refuses it.

    Mutation: drop `[ -L "$dir/$f" ]` from `_release_files` (the link verifies: no download).
    """
    name, sig = _SUMS_NAMES[sid]
    target = tmp_path / "elsewhere"
    target.write_bytes(_genuine()[name])
    rt = tmp_path / "runner-temp"
    release = rt / ("tofu-release" if sid == "tofu-sums" else "terramate-release")
    release.mkdir(parents=True)
    (release / sig).write_bytes(_genuine()[sig])
    bash_symlink(tmp_path, target, release / name)
    r, out, curl, _, rt = _run(tmp_path, sid, key=_CACHE_KEYS[sid])
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    reason = f"{release.as_posix()}/{name} is not a regular file."
    assert (r.stdout, out, curl) == (_warning(sid, reason), _OUTPUTS[sid], _downloads(sid, rt))
    assert ((release / name).is_symlink(), target.read_bytes()) == (False, _genuine()[name])


@pytest.mark.parametrize(
    ("sid", "function", "needle", "reason"),
    [
        ("tofu-sums", "verify_tofu", b"723bebb0", _POISONED[0][3]),
        ("terramate-sums", "verify_terramate", b"303fd597", _POISONED[1][3]),
    ],
)
@bash_only
@needs_tools
def test_a_verify_function_returns_its_reason_and_writes_nothing(
    tmp_path, sid, function, needle, reason
):
    """The step and the pre-merge check decide what a failed verification costs, so the function
    returns 1 with `reason` set; it neither exits nor writes an output. A genuine pair writes the
    Linux digests whole (the genuine-sums tests above).

    Mutation: `fail` in place of the signature `reason` in `verify_tofu` or `verify_terramate`.
    """
    d = tmp_path / "release"
    d.mkdir()
    for n in _SUMS_NAMES[sid]:
        (d / n).write_bytes(_genuine()[n])
    name = _SUMS_NAMES[sid][0]
    (d / name).write_bytes(_flip(_genuine()[name], needle))
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    version = _TOFU if sid == "tofu-sums" else _TERRAMATE
    r = run_step(
        tmp_path,
        "set -euo pipefail\n"
        'source "$GITHUB_ACTION_PATH/release.sh"\n'
        f'{function} {version} "{d.as_posix()}" || echo "returned $?: $reason"\n',
        {
            **{k: v for k, v in os.environ.items() if k != "GNUPGHOME"},
            "RUNNER_TEMP": tmp_path.as_posix(),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_ACTION_PATH": (ENGINE / "actions" / "setup").as_posix(),
        },
        timeout=120,
    )
    assert (r.returncode, r.stdout, out.read_text(encoding="utf-8")) == (
        0,
        f"returned 1: {reason}\n",
        "",
    )


def _agent_running(tmp_path, home):
    """Whether a gpg-agent answers for keyring `home`, asked without starting one."""
    r = run_step(
        tmp_path,
        "gpg-connect-agent --no-autostart 'GETINFO pid' /bye\n",
        {**os.environ, "GNUPGHOME": home},
    )
    return any(line.startswith("D ") for line in r.stdout.splitlines())


def _homes(tmp_path, tool):
    path = tmp_path / "logs" / f"{tool}.home"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _kill_agents(tmp_path, homes):
    for home in set(homes):
        run_step(tmp_path, "gpgconf --kill gpg-agent\n", {**os.environ, "GNUPGHOME": home})


@pytest.mark.parametrize("passing", [True, False], ids=["passing", "failing"])
@bash_only
@needs_tools
def test_no_gpg_agent_outlives_the_opentofu_verify(tmp_path, passing):
    """The EXIT handler stops the agent of the step's own keyring, on success and on failure.

    Mutations: drop the kill from `_release_exit`; key it on `GNUPGHOME` (the step exports none).
    """
    served = _genuine()
    if not passing:
        served[_TOFU_SUMS] = _flip(served[_TOFU_SUMS], b"723bebb0")
    r, out, _, gpg, rt = _run(tmp_path, "tofu-sums", served=served)
    homes = _homes(tmp_path, "gpg")
    try:
        assert r.returncode == (0 if passing else 1), f"stdout={r.stdout!r} stderr={r.stderr!r}"
        assert out == (_TOFU_OUTPUT if passing else _refusal(r.stdout))
        assert gpg == _verifier("tofu-sums", rt)
        assert len(homes) == 2 and len(set(homes)) == 1 and homes[0], homes
        assert _calls(tmp_path / "logs" / "gpgconf") == [["--kill", "gpg-agent"]]
        assert _homes(tmp_path, "gpgconf") == homes[:1]
        assert not _agent_running(tmp_path, homes[0])
    finally:
        _kill_agents(tmp_path, homes)


@bash_only
@needs_tools
def test_a_restored_entry_verified_twice_uses_one_keyring(tmp_path):
    """A failed restored entry runs `verify_tofu` twice; the key is imported once, into the one
    keyring whose agent the EXIT handler stops.

    Mutation: drop the `[ -z "$_release_gnupghome" ]` guard in `verify_tofu` (a second keyring,
    whose agent nothing stops).
    """
    cached = {n: _genuine()[n] for n in _SUMS_NAMES["tofu-sums"]}
    cached[_TOFU_SUMS] = _poison(_TOFU_SUMS, b"723bebb0")
    r, out, _, gpg, rt = _run(tmp_path, "tofu-sums", cached=cached, key=_TOFU_CACHE_KEY)
    homes = _homes(tmp_path, "gpg")
    try:
        assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
        assert out == _TOFU_OUTPUT
        import_, verify = _verifier("tofu-sums", rt)
        assert gpg == [import_, verify, verify]
        assert len(homes) == 3 and len(set(homes)) == 1, homes
        assert not _agent_running(tmp_path, homes[0])
    finally:
        _kill_agents(tmp_path, homes)


@bash_only
@needs_tools
def test_a_failing_gpgconf_leaves_a_passing_step_green(tmp_path):
    """Mutation: drop the `|| true` after the kill in `_release_exit`."""
    r, out, *_ = _run(tmp_path, "tofu-sums", gpgconf_rc=1)
    try:
        assert (r.returncode, r.stdout, out) == (0, "", _TOFU_OUTPUT), r.stderr
        assert _calls(tmp_path / "logs" / "gpgconf") == [["--kill", "gpg-agent"]]
    finally:
        _kill_agents(tmp_path, _homes(tmp_path, "gpg"))


@bash_only
@needs_tools
def test_a_runner_gnupghome_is_untouched_by_a_terramate_only_step(tmp_path, gnupg_home):
    """The runner's own agent, under the `GNUPGHOME` it exported, still runs after the step.

    Mutation: key the kill on `GNUPGHOME` instead of `_release_gnupghome`.
    """
    r = run_step(tmp_path, "gpg-connect-agent /bye\n", {**os.environ, "GNUPGHOME": gnupg_home})
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _agent_running(tmp_path, gnupg_home)
    r, out, *_ = _run(tmp_path / "run", "terramate-sums", env={"GNUPGHOME": gnupg_home})
    assert (r.returncode, r.stdout, out) == (0, "", _TM_OUTPUT), r.stderr
    assert _calls(tmp_path / "run" / "logs" / "gpgconf") == []
    assert _agent_running(tmp_path, gnupg_home)


@bash_only
def test_a_failed_rm_in_drop_writes_the_generic_refusal(tmp_path):
    """`set -e` ends the step inside `drop()`, where no `fail` runs. The release root exists, so
    `release_dir` runs no `rm`; a curl that ran would refuse with its own reason.

    Mutation: drop `trap _release_exit EXIT` from release.sh (no refusal is written).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "rm", "exit 1")
    write_stub(bin_dir, "curl", "exit 99")
    (tmp_path / "runner-temp" / "terramate-release").mkdir(parents=True)
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        step_by(_ACTION, id="terramate-sums")["run"],
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": (tmp_path / "runner-temp").as_posix(),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_ACTION_PATH": (ENGINE / "actions" / "setup").as_posix(),
            "SHIPMATE_TERRAMATE_VERSION": _TERRAMATE,
        },
    )
    reason = (
        f'Terramate {_TERRAMATE}: a command exited 1 under "Terramate checksum verification '
        'failed"; the step log names it.'
    )
    assert (r.returncode, r.stdout, out.read_text(encoding="utf-8")) == (
        1,
        f"::error title=Terramate checksum verification failed::{reason}\n",
        f"refusal={reason}\n",
    )
