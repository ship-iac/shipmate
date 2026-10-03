"""actions/setup downloads the pinned Terramate release with one curl and fails closed.

The `Install Terramate` body runs here against stub `curl`, `tar`, `terramate` and `uname`
binaries. The stubs record their argv, so curl's whole command line is compared to a
hand-written constant per curl version: a dropped `-L` or timeout, `--retry-all-errors`
passed to a curl older than 7.71 or withheld from a newer one, or an added `--fail`, reds it.
"""

import os
import shutil
import stat
from pathlib import Path

import pytest
from _loader import bash_only, run_step, step_by

_ACTION = "setup"
_STEP = "Install Terramate"
_VERSION = "0.17.1"
_URL = (
    "https://github.com/terramate-io/terramate/releases/download/"
    "v0.17.1/terramate_0.17.1_linux_x86_64.tar.gz"
)
_PREFIX = f"::error title=Terramate install failed::Terramate {_VERSION} did not install: "


def _annotation(code, rc):
    return f"{_PREFIX}{_URL} answered HTTP {code} (curl exit {rc}); re-run the failed job."


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _install(
    tmp_path, curl_output, curl_rc, curl_version, binary_version=_VERSION, tar_rc=0, terramate_rc=0
):
    """Run the step's body with a curl that answers `-V` as `curl_version`, and otherwise prints
    `curl_output` and exits `curl_rc`.

    The tar stub exits `tar_rc` when non-zero; otherwise it writes a `terramate` stub where the
    real extract would, and that stub records its argv, prints `binary_version` and exits
    `terramate_rc` when run. Return (result, runner_temp, the logs dir holding each stub's argv).
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
        f'if [ "$1" = -V ]; then echo "curl {curl_version} (x86_64-pc-linux-gnu)"; exit 0; fi\n'
        f"printf '%s\\n' \"$@\" > '{log}/curl'\nprintf '{curl_output}'\nexit {curl_rc}",
    )
    _stub(
        bin_dir,
        "tar",
        f"printf '%s\\n' \"$@\" > '{log}/tar'\n"
        f"[ {tar_rc} = 0 ] || exit {tar_rc}\n"
        f'printf \'#!/usr/bin/env bash\\nprintf "%%s\\\\n" "$@" > {log}/terramate\\n'
        f'echo "$STUB_TERRAMATE_VERSION"\\nexit {terramate_rc}\\n\''
        ' > "$4/terramate"\nchmod +x "$4/terramate"',
    )
    _stub(
        bin_dir,
        "uname",
        'if [ "$1" = -s ]; then echo Linux; elif [ "$1" = -m ]; then echo x86_64; fi',
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
            "SHIPMATE_TERRAMATE_VERSION": _VERSION,
            "STUB_TERRAMATE_VERSION": binary_version,
        },
    )
    return r, runner_temp.as_posix(), logs


def _argv(logs, name):
    path = logs / name
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else None


def test_the_install_step_is_fail_closed():
    """No `continue-on-error`, no `if:`, no third-party `uses:`.

    Mutation: add `continue-on-error: true` to the step.
    """
    assert set(step_by(_ACTION, name=_STEP)) == {"name", "shell", "env", "run"}


_CURL = "8.19.0"
_TAIL = ["-sSL", "--retry", "3", "--connect-timeout", "10", "--speed-limit", "1024"]
_TAIL += ["--speed-time", "30", "-o", "{tarball}", "-w", "%{http_code}", _URL]

#: curl version -> its whole argv, hand-written: `--retry-all-errors` is curl 7.71+, and an
#: older curl exits 2 on it.
_CURL_ARGV = {
    "7.68.0": _TAIL,
    "7.71.0": ["--retry-all-errors", *_TAIL],
    "8.19.0": ["--retry-all-errors", *_TAIL],
}


@pytest.mark.parametrize("curl_version", sorted(_CURL_ARGV))
@bash_only
def test_a_200_extracts_checks_the_binary_and_adds_the_directory_to_path(tmp_path, curl_version):
    """Mutations: `-ge 71` -> `-gt 71` (7.71.0 row); pass `--retry-all-errors` unconditionally
    (7.68.0 row); drop `-L` or `--speed-time`, or add `--fail` (every row); drop the
    `terramate --version` line (terramate argv).
    """
    r, runner_temp, logs = _install(tmp_path, "200", 0, curl_version)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    tarball = f"{runner_temp}/terramate/terramate.tar.gz"
    expected = [tarball if a == "{tarball}" else a for a in _CURL_ARGV[curl_version]]
    assert _argv(logs, "curl") == expected
    assert _argv(logs, "tar") == ["-xzf", tarball, "-C", f"{runner_temp}/terramate", "terramate"]
    assert _argv(logs, "terramate") == ["--version"]
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == f"{runner_temp}/terramate\n"


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
    r, _, logs = _install(tmp_path, curl_output, curl_rc, _CURL)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == _annotation(code, curl_rc) + "\n"
    assert _argv(logs, "tar") is None
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""


@bash_only
def test_a_404_names_the_version_and_platform_as_the_remedy(tmp_path):
    """The URL has no asset, so a re-run cannot help.

    Mutation: drop the 404 branch of the remedy.
    """
    r, _, logs = _install(tmp_path, "404", 0, _CURL)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}{_URL} answered HTTP 404 (curl exit 0); "
        "check VERSIONS and the runner's OS and architecture.\n"
    )
    assert _argv(logs, "tar") is None
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("tar_rc", "terramate_rc", "message"),
    [
        # Mutation: drop the `|| fail ...` after `tar`.
        (2, 0, f"tar could not extract terramate from {_URL}."),
        # Mutation: drop the `|| fail ...` after `terramate --version`.
        (0, 1, f"the binary from {_URL} failed to run terramate --version."),
    ],
)
@bash_only
def test_a_failed_extract_or_binary_fails_the_step_with_one_annotation(
    tmp_path, tar_rc, terramate_rc, message
):
    r, _, _ = _install(tmp_path, "200", 0, _CURL, tar_rc=tar_rc, terramate_rc=terramate_rc)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_PREFIX}{message}\n"
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""


@bash_only
def test_a_binary_of_another_version_fails_the_step_with_one_annotation(tmp_path):
    """Mutation: replace the version comparison with a bare `"$dir/terramate" --version`."""
    r, _, _ = _install(tmp_path, "200", 0, _CURL, binary_version="0.17.0")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}{_URL} delivered version 0.17.0; check VERSIONS and the release asset.\n"
    )
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""


@bash_only
def test_a_runner_without_curl_fails_the_step_with_one_annotation(tmp_path):
    """PATH holds every tool the step runs but curl. The stubs use `#!/bin/sh`, because
    `#!/usr/bin/env bash` would look bash up on this PATH.

    Mutation: delete the `command -v curl` line (the step reports HTTP 000, curl exit 127).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {"uname": 'if [ "$1" = -s ]; then echo Linux; elif [ "$1" = -m ]; then echo x86_64; fi'}
    for tool in ("tr", "mkdir", "awk"):
        real = shutil.which(tool)
        assert real, f"{tool} is not on PATH"
        stubs[tool] = f'exec "{Path(real).as_posix()}" "$@"'
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    github_path = tmp_path / "github_path"
    github_path.write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        step_by(_ACTION, name=_STEP)["run"],
        {
            **os.environ,
            "PATH": str(bin_dir),
            "RUNNER_TEMP": (tmp_path / "runner-temp").as_posix(),
            "GITHUB_PATH": str(github_path),
            "SHIPMATE_TERRAMATE_VERSION": _VERSION,
        },
    )
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_PREFIX}curl is not installed on the runner.\n"
    assert github_path.read_text(encoding="utf-8") == ""
