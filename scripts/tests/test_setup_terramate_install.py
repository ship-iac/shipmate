"""actions/setup downloads the pinned Terramate release with one curl and fails closed.

The `Install Terramate` body runs here against stub `curl`, `sha256sum`, `tar`, `terramate`
and `uname` binaries. The stubs record their argv, so curl's whole command line is compared to a
hand-written constant: a dropped `-L`, timeout or `--retry-all-errors`, or an added `--fail`,
reds it.
"""

import os

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
_STEP = "Install Terramate"
_VERSION = "0.17.1"
_URL = (
    "https://github.com/terramate-io/terramate/releases/download/"
    "v0.17.1/terramate_0.17.1_linux_x86_64.tar.gz"
)
_PREFIX = f"::error title=Terramate install failed::Terramate {_VERSION} did not install: "
_ASSET = "terramate_0.17.1_linux_x86_64.tar.gz"
_DIGEST = "a" * 64

#: The `terramate_sha256` verify output: the signed `sha256sum` lines for this version.
_PINNED = f"{_DIGEST}  {_ASSET}\n{'b' * 64}  terramate_0.17.1_linux_arm64.tar.gz"
_VERIFIED = f"Verified {_ASSET} sha256 {_DIGEST}.\n"


def _annotation(code, rc):
    return (
        f"{_PREFIX}{_URL} answered HTTP {code} from {_URL} (curl exit {rc}); re-run the failed job."
    )


def _install(
    tmp_path,
    curl_output,
    curl_rc,
    binary_version=_VERSION,
    tar_rc=0,
    terramate_rc=0,
    digest=_DIGEST,
    cached_digest=None,
    key=None,
    machine="x86_64",
):
    """Run the step's body with a curl that prints `curl_output` and exits `curl_rc`, and a
    `uname -m` that prints `machine`. `key`, when given, is the `SHIPMATE_CACHE_KEY`.

    The sha256sum stub prints `digest` once curl has run, else `cached_digest` (default
    `digest`), so a restored archive and a fresh one hash apart; it and the tar stub append their
    names to the shared `order` log. The tar stub exits `tar_rc` when non-zero; otherwise it
    writes a `terramate` stub where the real extract would, and that stub records its argv,
    prints `binary_version` and exits `terramate_rc` when run. Return (result, runner_temp, the logs
    dir holding each stub's argv).
    """
    bin_dir = tmp_path / "bin"
    logs = tmp_path / "logs"
    runner_temp = tmp_path / "runner-temp"
    for d in (bin_dir, logs, runner_temp):
        d.mkdir(exist_ok=True)
    log = logs.as_posix()
    restored = digest if cached_digest is None else cached_digest
    write_stub(
        bin_dir,
        "curl",
        f"printf '%s\\n' \"$@\" >> '{log}/curl'\ntouch '{log}/downloaded'\n"
        'prev=""; for a in "$@"; do if [ "$prev" = -o ]; then : > "$a"; fi; prev="$a"; done\n'
        f"printf '{curl_output} %s' \"${{!#}}\"\nexit {curl_rc}",
    )
    write_stub(
        bin_dir,
        "tar",
        f"echo tar >> '{log}/order'\nprintf '%s\\n' \"$@\" > '{log}/tar'\n"
        f"[ {tar_rc} = 0 ] || exit {tar_rc}\n"
        f'printf \'#!/usr/bin/env bash\\nprintf "%%s\\\\n" "$@" > {log}/terramate\\n'
        f'echo "$STUB_TERRAMATE_VERSION"\\nexit {terramate_rc}\\n\''
        ' > "$4/terramate"\nchmod +x "$4/terramate"',
    )
    write_stub(
        bin_dir,
        "sha256sum",
        f"echo sha256sum >> '{log}/order'\nprintf '%s\\n' \"$@\" >> '{log}/sha256sum'\n"
        f"if [ -f '{log}/downloaded' ]; then d={digest}; else d={restored}; fi\n"
        'if [ -n "$d" ]; then echo "$d  $1"; fi',
    )
    write_stub(
        bin_dir,
        "uname",
        f'if [ "$1" = -s ]; then echo Linux; elif [ "$1" = -m ]; then echo {machine}; fi',
    )
    github_path = tmp_path / "github_path"
    github_path.write_text("", encoding="utf-8")
    (tmp_path / "github_output").write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        step_by(_ACTION, name=_STEP)["run"],
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": runner_temp.as_posix(),
            "GITHUB_PATH": str(github_path),
            "GITHUB_OUTPUT": str(tmp_path / "github_output"),
            "GITHUB_ACTION_PATH": (ENGINE / "actions" / "setup").as_posix(),
            "SHIPMATE_TERRAMATE_VERSION": _VERSION,
            "SHIPMATE_TERRAMATE_SHA256": _PINNED,
            "STUB_TERRAMATE_VERSION": binary_version,
            "GITHUB_REPOSITORY": "o/r",
            **({} if key is None else {"SHIPMATE_CACHE_KEY": key}),
        },
    )
    return r, runner_temp.as_posix(), logs


def _refusal(tmp_path, stdout):
    """Assert the step wrote one `refusal=` line: its last annotation without the
    `::error title=...::` prefix. Every caller pins `stdout` whole."""
    last = stdout.splitlines()[-1]
    out = (tmp_path / "github_output").read_text(encoding="utf-8")
    assert out == "refusal=" + last.split("::", 2)[2] + "\n"


def _argv(logs, name):
    path = logs / name
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else None


def test_the_install_step_is_fail_closed():
    """No `continue-on-error`, no `if:`, no third-party `uses:`.

    Mutation: add `continue-on-error: true` to the step.
    """
    assert set(step_by(_ACTION, name=_STEP)) == {"name", "shell", "env", "run", "id"}


@bash_only
def test_a_200_verifies_extracts_checks_the_binary_and_adds_the_directory_to_path(tmp_path):
    """Mutations: drop `--retry-all-errors`, `-L` or `--speed-time`, or add `--fail` (curl
    argv); drop the `terramate --version` line (terramate argv); move the sha256 comparison
    after `tar` (order).
    """
    r, runner_temp, logs = _install(tmp_path, "200", 0)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    tarball = f"{runner_temp}/terramate-release/{_ASSET}"
    expected = setup_curl_argv(_URL, tarball)
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
        # Mutation: in `fetch`, return on curl exit 0 alone.
        ("500", 0, "500"),
        # No response arrived: curl prints status 000.
        ("000", 7, "000"),
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
    _refusal(tmp_path, r.stdout)


@bash_only
def test_a_404_names_the_version_and_platform_as_the_remedy(tmp_path):
    """The URL has no asset, so a re-run cannot help.

    Mutation: pass the other remedy on a 404 in `fetch`.
    """
    r, _, logs = _install(tmp_path, "404", 0)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}{_URL} answered HTTP 404 from {_URL} (curl exit 0); "
        "check the signed checksums.txt and the runner's OS and architecture.\n"
    )
    assert _argv(logs, "tar") is None
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""
    _refusal(tmp_path, r.stdout)


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
    _refusal(tmp_path, r.stdout)


@bash_only
def test_a_binary_of_another_version_fails_the_step_with_one_annotation(tmp_path):
    """Mutation: replace the version comparison with a bare `"$dir/terramate" --version`."""
    r, _, _ = _install(tmp_path, "200", 0, binary_version="0.17.0")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_VERIFIED}{_PREFIX}{_URL} delivered version 0.17.0; "
        "check the signed checksums.txt and the release asset.\n"
    )
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""
    _refusal(tmp_path, r.stdout)


@bash_only
def test_a_digest_mismatch_fails_the_step_before_tar_runs(tmp_path):
    """The asset differs from the signed one, so the annotation names no re-run.

    Mutation: drop the sha256 comparison.
    """
    r, _, logs = _install(tmp_path, "200", 0, digest="c" * 64)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}{_URL} has sha256 {'c' * 64}, not the {_DIGEST} the signed checksums.txt "
        "names; the release asset differs from the signed one.\n"
    )
    assert _argv(logs, "tar") is None
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""
    _refusal(tmp_path, r.stdout)


@bash_only
def test_an_unpinned_platform_fails_the_step_before_curl_runs(tmp_path):
    """Mutation: drop the `|| fail` after the digest lookup."""
    r, _, logs = _install(tmp_path, "200", 0, machine="riscv64")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}the signed checksums.txt yields no sha256 for "
        "terramate_0.17.1_linux_riscv64.tar.gz; shipmate installs on Linux x86_64 and arm64.\n"
    )
    assert _argv(logs, "curl") is None
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == ""
    _refusal(tmp_path, r.stdout)


_CACHE_KEY = f"shipmate-tools-Linux-X64-terramate-{_VERSION}"


def _cached(tmp_path, data=b"cached"):
    """Place a restored archive where the cache restore writes it."""
    release = tmp_path / "runner-temp" / "terramate-release"
    release.mkdir(parents=True)
    (release / _ASSET).write_bytes(data)


def _mismatch(got):
    return (
        f"{_URL} has sha256 {got}, not the {_DIGEST} the signed checksums.txt names; the release "
        "asset differs from the signed one."
    )


def _warning(reason):
    return (
        f"::warning title=Tool cache entry failed verification::Terramate {_VERSION}: {reason} "
        f"Downloading {_ASSET} again. Every job repeats this download until the entry is "
        f"deleted: gh cache delete {_CACHE_KEY} --repo o/r\n"
    )


def _curl(archive):
    """curl's whole argv for one download of the archive to `archive`."""
    return setup_curl_argv(_URL, archive)


def _install_cached(tmp_path, **kwargs):
    """`_install` on a runner temp holding a restored archive, with the entry's key."""
    _cached(tmp_path)
    return _install(tmp_path, "200", 0, key=_CACHE_KEY, **kwargs)


@bash_only
def test_a_restored_archive_whose_digest_matches_is_not_downloaded(tmp_path):
    """Mutation: run the `fetch` before the restored-entry `if` (curl is called)."""
    r, runner_temp, logs = _install_cached(tmp_path)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = f"{runner_temp}/terramate-release/{_ASSET}"
    assert (_argv(logs, "curl"), _argv(logs, "sha256sum")) == (None, [archive])
    assert r.stdout == _VERIFIED
    assert _argv(logs, "tar") == ["-xzf", archive, "-C", f"{runner_temp}/terramate", "terramate"]
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == f"{runner_temp}/terramate\n"


@bash_only
def test_a_restored_archive_whose_digest_differs_is_downloaded_again_with_one_warning(tmp_path):
    """Mutations: fail on the restored archive (trust the hit: exit 1); download again without
    the warning."""
    r, runner_temp, logs = _install_cached(tmp_path, cached_digest="c" * 64)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = f"{runner_temp}/terramate-release/{_ASSET}"
    assert (_argv(logs, "curl"), _argv(logs, "sha256sum")) == (_curl(archive), [archive, archive])
    assert r.stdout == _warning(_mismatch("c" * 64)) + _VERIFIED
    assert _argv(logs, "tar") == ["-xzf", archive, "-C", f"{runner_temp}/terramate", "terramate"]
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == f"{runner_temp}/terramate\n"


@bash_only
def test_a_restored_archive_whose_fresh_copy_differs_too_fails_after_one_download(tmp_path):
    """Mutation: download again until the digest matches (a bounded three-attempt loop: three
    curl calls, not one)."""
    r, runner_temp, logs = _install_cached(tmp_path, cached_digest="c" * 64, digest="d" * 64)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _argv(logs, "curl") == _curl(f"{runner_temp}/terramate-release/{_ASSET}")
    assert r.stdout == _warning(_mismatch("c" * 64)) + _PREFIX + _mismatch("d" * 64) + "\n"
    assert (_argv(logs, "tar"), (tmp_path / "github_path").read_text(encoding="utf-8")) == (
        None,
        "",
    )
    _refusal(tmp_path, r.stdout)


@bash_only
def test_an_archive_present_without_a_restored_key_is_downloaded_again(tmp_path):
    """Nothing explains a file present on a miss, so it is replaced, never verified.

    Mutation: ignore the key and keep any present archive (curl is not called).
    """
    _cached(tmp_path)
    r, runner_temp, logs = _install(tmp_path, "200", 0, key="")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _argv(logs, "curl") == _curl(f"{runner_temp}/terramate-release/{_ASSET}")


@bash_only
def test_a_restored_archive_path_that_is_a_directory_is_downloaded_again(tmp_path):
    """A restored path that is not a regular file costs a download, not the job.

    Mutation: `rm -f` in place of `rm -rf` before the download (the step exits 1 on the
    directory).
    """
    release = tmp_path / "runner-temp" / "terramate-release"
    (release / _ASSET).mkdir(parents=True)
    r, runner_temp, logs = _install(tmp_path, "200", 0, key=_CACHE_KEY)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = f"{runner_temp}/terramate-release/{_ASSET}"
    assert (_argv(logs, "curl"), _argv(logs, "sha256sum")) == (_curl(archive), [archive])
    assert r.stdout == _warning(f"{archive} is not a regular file.") + _VERIFIED


@bash_only
def test_an_empty_sha256sum_output_fails_naming_the_tool_not_the_asset(tmp_path):
    """Mutation: drop the empty-digest guard (the annotation reports a tampered asset)."""
    r, runner_temp, logs = _install(tmp_path, "200", 0, digest="")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = f"{runner_temp}/terramate-release/{_ASSET}"
    assert r.stdout == f"{_PREFIX}sha256sum printed no digest for {archive}.\n"
    assert (_argv(logs, "tar"), (tmp_path / "github_path").read_text(encoding="utf-8")) == (
        None,
        "",
    )
    _refusal(tmp_path, r.stdout)


@bash_only
def test_a_curl_older_than_7_71_is_told_its_floor_by_the_install_step(tmp_path):
    """Mutation: drop the exit-2 branch of `fetch` (the re-run remedy appears)."""
    r, _, logs = _install(tmp_path, "000", 2)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_PREFIX}curl exited 2 on an option it does not know; shipmate needs curl 7.71 or "
        "later (CONTRACT.md §Runner prerequisites).\n"
    )
    assert _argv(logs, "tar") is None
    _refusal(tmp_path, r.stdout)


@bash_only
def test_a_restored_archive_sha256sum_cannot_read_is_downloaded_again(tmp_path):
    """An unreadable restored file: the sha256sum stub prints no digest for it, as sha256sum does
    on a read error. A fresh archive that yields none still fails (the test above).

    Mutation: `fail` in place of the empty-digest `reason` in `_release_check` (the step exits 1).
    """
    r, runner_temp, logs = _install_cached(tmp_path, cached_digest="")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = f"{runner_temp}/terramate-release/{_ASSET}"
    assert (_argv(logs, "curl"), _argv(logs, "sha256sum")) == (_curl(archive), [archive, archive])
    assert r.stdout == _warning(f"sha256sum printed no digest for {archive}.") + _VERIFIED
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == f"{runner_temp}/terramate\n"


@bash_only
def test_a_restored_archive_that_is_a_symlink_is_downloaded_again(tmp_path):
    """The link is removed, not followed: its target stays as it was.

    Mutation: drop `[ -L "$archive" ]` from `_release_check` (the link's digest matches; curl is not
    called).
    """
    release = tmp_path / "runner-temp" / "terramate-release"
    release.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.write_bytes(b"cached")
    bash_symlink(tmp_path, target, release / _ASSET)
    r, runner_temp, logs = _install(tmp_path, "200", 0, key=_CACHE_KEY)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = f"{runner_temp}/terramate-release/{_ASSET}"
    assert (_argv(logs, "curl"), _argv(logs, "sha256sum")) == (_curl(archive), [archive])
    assert r.stdout == _warning(f"{archive} is not a regular file.") + _VERIFIED
    assert (target.read_bytes(), (release / _ASSET).is_symlink()) == (b"cached", False)
