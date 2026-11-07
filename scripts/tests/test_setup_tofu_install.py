"""actions/setup downloads the pinned OpenTofu release with one curl and fails closed.

The `Install OpenTofu` body runs here against stub `curl`, `sha256sum`, `python3`, `chmod`,
`tofu` and `uname` binaries. The stubs record their argv, so curl's whole command line is
compared to a hand-written constant: a dropped `-L`, timeout or `--retry-all-errors`, or an
added `--fail`, reds it.
"""

import os
import stat

import pytest
from _loader import bash_only, run_step, step_by

_ACTION = "setup"
_STEP = "Install OpenTofu"
_VERSION = "1.13.2"
_ASSET = "tofu_1.13.2_linux_amd64.zip"
_URL = f"https://github.com/opentofu/opentofu/releases/download/v1.13.2/{_ASSET}"
_PREFIX = f"::error title=OpenTofu install failed::OpenTofu {_VERSION} did not install: "
_SUMS = "tofu_1.13.2_SHA256SUMS"
_DIGEST = "a" * 64

#: The `tofu_sha256` verify output: the signed `sha256sum` lines for this version.
_PINNED = f"{_DIGEST}  {_ASSET}\n{'b' * 64}  tofu_1.13.2_linux_arm64.zip"
_VERIFIED = f"Verified {_ASSET} sha256 {_DIGEST}.\n"


def _annotation(code, rc):
    return f"{_PREFIX}{_URL} answered HTTP {code} (curl exit {rc}); re-run the failed job."


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _install(
    tmp_path,
    curl_output,
    curl_rc,
    binary_version=f"OpenTofu v{_VERSION}",
    python_rc=0,
    chmod_rc=0,
    tofu_rc=0,
    digest=_DIGEST,
    machine="x86_64",
    system="Linux",
):
    """Run the step's body with a curl that prints `curl_output` and exits `curl_rc`, and a
    `uname` that prints `system` for `-s` and `machine` for `-m`.

    The sha256sum, python3 and chmod stubs append their names to the shared `order` log. The
    python3 stub exits `python_rc` when non-zero; otherwise it writes a `tofu` stub, without the
    exec bit, where `python3 -m zipfile -e` would. The chmod stub exits `chmod_rc` when
    non-zero; otherwise it runs the real chmod. The tofu
    stub records its argv and the `$GITHUB_PATH` text it sees, prints `binary_version` and a
    platform line, and exits `tofu_rc`. Return (result, runner_temp, the logs dir).
    """
    bin_dir = tmp_path / "bin"
    logs = tmp_path / "logs"
    runner_temp = tmp_path / "runner-temp"
    for d in (bin_dir, logs, runner_temp):
        d.mkdir()
    log = logs.as_posix()
    _stub(
        bin_dir,
        "curl",
        f"printf '%s\\n' \"$@\" > '{log}/curl'\nprintf '{curl_output}'\nexit {curl_rc}",
    )
    _stub(
        bin_dir,
        "python3",
        f"echo python3 >> '{log}/order'\nprintf '%s\\n' \"$@\" > '{log}/python3'\n"
        f"[ {python_rc} = 0 ] || exit {python_rc}\n"
        'mkdir -p "$5"\n'
        f'printf \'#!/usr/bin/env bash\\nprintf "%%s\\\\n" "$@" > {log}/tofu\\n'
        f'cat "$GITHUB_PATH" > {log}/path_at_version\\n'
        f'echo "$STUB_TOFU_VERSION"\\necho on linux_amd64\\nexit {tofu_rc}\\n\''
        ' > "$5/tofu"',
    )
    _stub(
        bin_dir,
        "chmod",
        f"echo chmod >> '{log}/order'\nprintf '%s\\n' \"$@\" > '{log}/chmod'\n"
        f"[ {chmod_rc} = 0 ] || exit {chmod_rc}\n"
        'exec /usr/bin/chmod "$@"',
    )
    _stub(
        bin_dir,
        "sha256sum",
        f"echo sha256sum >> '{log}/order'\nprintf '%s\\n' \"$@\" > '{log}/sha256sum'\n"
        f'echo "{digest}  $1"',
    )
    _stub(
        bin_dir,
        "uname",
        f'if [ "$1" = -s ]; then echo {system}; elif [ "$1" = -m ]; then echo {machine}; fi',
    )
    github_path = tmp_path / "github_path"
    github_path.write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        step_by(_ACTION, name=_STEP)["run"],
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": runner_temp.as_posix(),
            "GITHUB_PATH": str(github_path),
            "SHIPMATE_TOFU_VERSION": _VERSION,
            "SHIPMATE_TOFU_SHA256": _PINNED,
            "STUB_TOFU_VERSION": binary_version,
        },
    )
    return r, runner_temp.as_posix(), logs


def _argv(logs, name):
    path = logs / name
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else None


def _github_path(tmp_path):
    return (tmp_path / "github_path").read_text(encoding="utf-8")


def test_the_install_step_is_fail_closed():
    """No `continue-on-error` and no third-party `uses:`.

    Mutation: add `continue-on-error: true` to the step.
    """
    assert set(step_by(_ACTION, name=_STEP)) == {"name", "if", "shell", "env", "run"}


#: curl's whole argv, hand-written; `{zip}` stands for the download path.
_CURL_ARGV = ["--retry-all-errors", "-sSL", "--retry", "10", "--retry-max-time", "120"]
_CURL_ARGV += ["--connect-timeout", "10"]
_CURL_ARGV += ["--speed-limit", "1024", "--speed-time", "30", "-o", "{zip}"]
_CURL_ARGV += ["-w", "%{http_code}", _URL]


@bash_only
def test_a_200_verifies_extracts_checks_the_binary_and_adds_the_directory_to_path(tmp_path):
    """Mutations: drop `--retry-max-time 120` (curl argv); move the sha256 comparison after the
    extract (order); drop the `chmod` (order); append to `$GITHUB_PATH` before `tofu version`
    (the path the binary saw).
    """
    r, runner_temp, logs = _install(tmp_path, "200", 0)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    zip_path = f"{runner_temp}/tofu-release/{_ASSET}"
    expected = [zip_path if a == "{zip}" else a for a in _CURL_ARGV]
    assert _argv(logs, "curl") == expected
    assert _argv(logs, "sha256sum") == [zip_path]
    assert _argv(logs, "order") == ["sha256sum", "python3", "chmod"]
    assert r.stdout == _VERIFIED
    assert _argv(logs, "python3") == ["-m", "zipfile", "-e", zip_path, f"{runner_temp}/tofu"]
    assert _argv(logs, "chmod") == ["+x", f"{runner_temp}/tofu/tofu"]
    assert _argv(logs, "tofu") == ["version"]
    assert _argv(logs, "path_at_version") == []
    assert _github_path(tmp_path) == f"{runner_temp}/tofu\n"


@pytest.mark.parametrize(
    ("curl_output", "curl_rc", "code"),
    [
        # Mutation: drop the `!= 200` status check.
        ("500", 0, "500"),
        # Mutation: drop the `${code:-000}` default.
        ("", 7, "000"),
        # A transfer cut mid-body. Mutation: replace `|| rc=$?` with `|| true`.
        ("200", 18, "200"),
    ],
)
@bash_only
def test_a_failed_download_fails_the_step_with_one_annotation(tmp_path, curl_output, curl_rc, code):
    r, _, logs = _install(tmp_path, curl_output, curl_rc)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == _annotation(code, curl_rc) + "\n"
    assert _argv(logs, "python3") is None
    assert _github_path(tmp_path) == ""


@bash_only
def test_a_404_names_the_version_and_platform_as_the_remedy(tmp_path):
    """The URL has no asset, so a re-run cannot help.

    Mutation: drop the 404 branch of the remedy.
    """
    r, _, logs = _install(tmp_path, "404", 0)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}{_URL} answered HTTP 404 (curl exit 0); "
        f"check the signed {_SUMS} and the runner's OS and architecture.\n"
    )
    assert _argv(logs, "python3") is None
    assert _github_path(tmp_path) == ""


@bash_only
def test_a_digest_mismatch_fails_the_step_before_the_extract(tmp_path):
    """The asset differs from the signed one, so the annotation names no re-run.

    Mutation: drop the sha256 comparison.
    """
    r, _, logs = _install(tmp_path, "200", 0, digest="c" * 64)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}{_URL} has sha256 {'c' * 64}, not the {_DIGEST} the signed {_SUMS} "
        "names; the release asset differs from the signed one.\n"
    )
    assert _argv(logs, "python3") is None
    assert _github_path(tmp_path) == ""


@pytest.mark.parametrize(
    ("python_rc", "chmod_rc", "tofu_rc", "message"),
    [
        # Mutation: drop the `|| fail ...` after the extract.
        (1, 0, 0, f"python3 could not extract OpenTofu from {_URL}."),
        # Mutation: drop the `|| fail ...` after `chmod`.
        (0, 1, 0, "could not make the OpenTofu binary executable."),
        # Mutation: drop the `|| fail ...` after `tofu version`.
        (0, 0, 1, f"the OpenTofu binary from {_URL} failed its version check."),
    ],
)
@bash_only
def test_a_failed_extract_or_binary_fails_the_step_with_one_annotation(
    tmp_path, python_rc, chmod_rc, tofu_rc, message
):
    r, _, _ = _install(tmp_path, "200", 0, python_rc=python_rc, chmod_rc=chmod_rc, tofu_rc=tofu_rc)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_VERIFIED}{_PREFIX}{message}\n"
    assert _github_path(tmp_path) == ""


@bash_only
def test_a_binary_of_another_version_fails_the_step_with_one_annotation(tmp_path):
    """Mutation: replace the version comparison with a bare `"$bin/tofu" version`."""
    r, _, _ = _install(tmp_path, "200", 0, binary_version="OpenTofu v1.13.0")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_VERIFIED}{_PREFIX}{_URL} delivered OpenTofu v1.13.0; "
        f"check the signed {_SUMS} and the release asset.\n"
    )
    assert _github_path(tmp_path) == ""


@pytest.mark.parametrize(
    ("system", "machine", "asset"),
    [
        # Mutation: drop the `|| fail` after the digest lookup.
        ("Linux", "riscv64", "tofu_1.13.2_linux_riscv64.zip"),
        # Mutation: hard-code `linux` in place of `uname -s`.
        ("Darwin", "x86_64", "tofu_1.13.2_darwin_amd64.zip"),
    ],
)
@bash_only
def test_an_unpinned_platform_fails_the_step_before_curl_runs(tmp_path, system, machine, asset):
    r, _, logs = _install(tmp_path, "200", 0, machine=machine, system=system)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}the signed {_SUMS} yields no sha256 for {asset}; "
        "shipmate installs on Linux x86_64 and arm64.\n"
    )
    assert _argv(logs, "curl") is None
    assert _github_path(tmp_path) == ""
