"""actions/setup downloads the pinned Terramate release with one curl and fails closed.

The `Install Terramate` body runs here against stub `curl`, `sha256sum`, `tar`, `terramate`
and `uname` binaries. The stubs record their argv, so curl's whole command line is compared to a
hand-written constant: a dropped `-L`, timeout or `--retry-all-errors`, or an added `--fail`,
reds it.
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
_ASSET = "terramate_0.17.1_linux_x86_64.tar.gz"
_DIGEST = "a" * 64

#: The `terramate_sha256` resolve output: the `sha256sum` lines pinned for this version.
_PINNED = f"{_DIGEST}  {_ASSET}\n{'b' * 64}  terramate_0.17.1_linux_arm64.tar.gz"
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
    binary_version=_VERSION,
    tar_rc=0,
    terramate_rc=0,
    digest=_DIGEST,
    machine="x86_64",
):
    """Run the step's body with a curl that prints `curl_output` and exits `curl_rc`, and a
    `uname -m` that prints `machine`.

    The sha256sum stub prints `digest` for its file; it and the tar stub append their names to
    the shared `order` log. The tar stub exits `tar_rc` when non-zero; otherwise it writes a
    `terramate` stub where the real extract would, and that stub records its argv, prints
    `binary_version` and exits `terramate_rc` when run. Return (result, runner_temp, the logs
    dir holding each stub's argv).
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
        "tar",
        f"echo tar >> '{log}/order'\nprintf '%s\\n' \"$@\" > '{log}/tar'\n"
        f"[ {tar_rc} = 0 ] || exit {tar_rc}\n"
        f'printf \'#!/usr/bin/env bash\\nprintf "%%s\\\\n" "$@" > {log}/terramate\\n'
        f'echo "$STUB_TERRAMATE_VERSION"\\nexit {terramate_rc}\\n\''
        ' > "$4/terramate"\nchmod +x "$4/terramate"',
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
        f'if [ "$1" = -s ]; then echo Linux; elif [ "$1" = -m ]; then echo {machine}; fi',
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
            "SHIPMATE_TERRAMATE_SHA256": _PINNED,
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


#: curl's whole argv, hand-written; `{tarball}` stands for the download path.
_CURL_ARGV = ["--retry-all-errors", "-sSL", "--retry", "3", "--connect-timeout", "10"]
_CURL_ARGV += ["--speed-limit", "1024", "--speed-time", "30", "-o", "{tarball}"]
_CURL_ARGV += ["-w", "%{http_code}", _URL]


@bash_only
def test_a_200_verifies_extracts_checks_the_binary_and_adds_the_directory_to_path(tmp_path):
    """Mutations: drop `--retry-all-errors`, `-L` or `--speed-time`, or add `--fail` (curl
    argv); drop the `terramate --version` line (terramate argv); move the sha256 comparison
    after `tar` (order).
    """
    r, runner_temp, logs = _install(tmp_path, "200", 0)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    tarball = f"{runner_temp}/terramate/terramate.tar.gz"
    expected = [tarball if a == "{tarball}" else a for a in _CURL_ARGV]
    assert _argv(logs, "curl") == expected
    assert _argv(logs, "sha256sum") == [tarball]
    assert _argv(logs, "order") == ["sha256sum", "tar"]
    assert r.stdout == _VERIFIED
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
    r, _, logs = _install(tmp_path, curl_output, curl_rc)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == _annotation(code, curl_rc) + "\n"
    assert _argv(logs, "tar") is None
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""


@bash_only
def test_a_404_names_the_version_and_platform_as_the_remedy(tmp_path):
    """The URL has no asset, so a re-run cannot help.

    Mutation: drop the 404 branch of the remedy.
    """
    r, _, logs = _install(tmp_path, "404", 0)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}{_URL} answered HTTP 404 (curl exit 0); "
        "check VERSIONS and the runner's OS and architecture.\n"
    )
    assert _argv(logs, "tar") is None
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""


@bash_only
def test_a_curl_older_than_7_71_is_told_its_floor_not_to_re_run(tmp_path):
    """curl exits 2 on an option it does not know, which a re-run repeats.

    Mutation: delete the exit-2 branch (the generic re-run message appears).
    """
    r, _, logs = _install(tmp_path, "", 2)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}curl exited 2 on an option it does not know; shipmate needs curl 7.71 "
        "or later (CONTRACT.md §Runner prerequisites).\n"
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
    r, _, _ = _install(tmp_path, "200", 0, tar_rc=tar_rc, terramate_rc=terramate_rc)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_VERIFIED}{_PREFIX}{message}\n"
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""


@bash_only
def test_a_binary_of_another_version_fails_the_step_with_one_annotation(tmp_path):
    """Mutation: replace the version comparison with a bare `"$dir/terramate" --version`."""
    r, _, _ = _install(tmp_path, "200", 0, binary_version="0.17.0")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_VERIFIED}{_PREFIX}{_URL} delivered version 0.17.0; "
        "check VERSIONS and the release asset.\n"
    )
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""


@bash_only
def test_a_digest_mismatch_fails_the_step_before_tar_runs(tmp_path):
    """The asset differs from the pinned one, so the annotation names no re-run.

    Mutation: drop the sha256 comparison.
    """
    r, _, logs = _install(tmp_path, "200", 0, digest="c" * 64)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}{_URL} has sha256 {'c' * 64}, not the {_DIGEST} VERSIONS pins; "
        "the release asset differs from the pinned one.\n"
    )
    assert _argv(logs, "tar") is None
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""


@bash_only
def test_an_unpinned_platform_fails_the_step_before_curl_runs(tmp_path):
    """Mutation: drop the `|| fail` after the digest lookup."""
    r, _, logs = _install(tmp_path, "200", 0, machine="riscv64")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}VERSIONS pins no sha256 for terramate_0.17.1_linux_riscv64.tar.gz; "
        "shipmate installs on Linux x86_64 and arm64.\n"
    )
    assert _argv(logs, "curl") is None
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
            "SHIPMATE_TERRAMATE_SHA256": _PINNED,
        },
    )
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_PREFIX}curl is not installed on the runner.\n"
    assert github_path.read_text(encoding="utf-8") == ""
