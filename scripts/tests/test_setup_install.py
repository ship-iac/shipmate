"""actions/setup downloads each pinned tool release with one curl and fails closed.

The `Install Terramate` and `Install OpenTofu` bodies run here against stub `curl`,
`sha256sum` and `uname` binaries, plus each tool's extract stubs (`tar` and `terramate`;
`python3`, `chmod` and `tofu`). The stubs record their argv, so curl's whole command line is
compared to a hand-written constant: a dropped `-L`, timeout or `--retry-all-errors`, or an added
`--fail`, reds it.

Shared tests take the tool from a literal pair, not from `_TOOLS`'s keys, so a tool dropped
from the record errors instead of going untested.
"""

import os

import pytest
from _loader import (
    ENGINE,
    argv_log,
    bash_only,
    bash_symlink,
    refusal_output,
    run_step,
    setup_curl_argv,
    step_by,
    write_stub,
)

_ACTION = "setup"
_DIGEST = "a" * 64
_EACH_TOOL = pytest.mark.parametrize("tool", ("terramate", "tofu"))


def _pinned(t):
    """The `<tool>_sha256` verify output: the signed `sha256sum` lines for this version."""
    return f"{_DIGEST}  {t['asset']}\n{'b' * 64}  {t['arm64_asset']}"


def _stubbed(tmp_path, curl_output, curl_rc, digest, cached_digest, machine, system):
    """Write the stubs both steps share; return (bin dir, logs dir).

    The curl stub records its argv, prints `curl_output` and exits `curl_rc`. The sha256sum
    stub prints `digest` once curl has run, else `cached_digest` (default `digest`), so a
    restored archive and a fresh one hash apart, and appends its name to the shared `order` log.
    `uname` prints `system` for `-s` and `machine` for `-m`.
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
        "sha256sum",
        f"echo sha256sum >> '{log}/order'\nprintf '%s\\n' \"$@\" >> '{log}/sha256sum'\n"
        f"if [ -f '{log}/downloaded' ]; then d={digest}; else d={restored}; fi\n"
        'if [ -n "$d" ]; then echo "$d  $1"; fi',
    )
    write_stub(
        bin_dir,
        "uname",
        f'if [ "$1" = -s ]; then echo {system}; elif [ "$1" = -m ]; then echo {machine}; fi',
    )
    return bin_dir, logs


def _run(tmp_path, step, bin_dir, logs, env, key):
    """Run `step`'s body with the stubs first on PATH, `env` added, and `key`, when given, as
    the `SHIPMATE_CACHE_KEY`. Return (result, runner_temp, the logs dir).
    """
    runner_temp = tmp_path / "runner-temp"
    github_path = tmp_path / "github_path"
    github_path.write_text("", encoding="utf-8")
    (tmp_path / "github_output").write_text("", encoding="utf-8")
    r = run_step(
        tmp_path,
        step_by(_ACTION, name=step)["run"],
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": runner_temp.as_posix(),
            "GITHUB_PATH": str(github_path),
            "GITHUB_OUTPUT": str(tmp_path / "github_output"),
            "GITHUB_ACTION_PATH": (ENGINE / "actions" / "setup").as_posix(),
            **env,
            "GITHUB_REPOSITORY": "o/r",
            **({} if key is None else {"SHIPMATE_CACHE_KEY": key}),
        },
    )
    return r, runner_temp.as_posix(), logs


def _install_terramate(
    tmp_path,
    curl_output,
    curl_rc,
    binary_version="0.17.1",
    tar_rc=0,
    terramate_rc=0,
    digest=_DIGEST,
    cached_digest=None,
    key=None,
    machine="x86_64",
    system="Linux",
):
    """Run the step's body against `_stubbed` and a tar stub. The tar stub appends its name to
    the shared `order` log and exits `tar_rc` when non-zero; otherwise it writes a `terramate`
    stub where the real extract would, and that stub records its argv, prints `binary_version`
    and exits `terramate_rc` when run.
    """
    bin_dir, logs = _stubbed(tmp_path, curl_output, curl_rc, digest, cached_digest, machine, system)
    log = logs.as_posix()
    write_stub(
        bin_dir,
        "tar",
        f"echo tar >> '{log}/order'\nprintf '%s\\n' \"$@\" > '{log}/tar'\n"
        f"[ {tar_rc} = 0 ] || exit {tar_rc}\n"
        f'printf \'#!/usr/bin/env bash\\nprintf "%%s\\\\n" "$@" > {log}/terramate\\n'
        f'echo "$STUB_TERRAMATE_VERSION"\\nexit {terramate_rc}\\n\''
        ' > "$4/terramate"\nchmod +x "$4/terramate"',
    )
    t = _TOOLS["terramate"]
    env = {
        "SHIPMATE_TERRAMATE_VERSION": t["version"],
        "SHIPMATE_TERRAMATE_SHA256": _pinned(t),
        "STUB_TERRAMATE_VERSION": binary_version,
    }
    return _run(tmp_path, t["step"], bin_dir, logs, env, key)


def _install_tofu(
    tmp_path,
    curl_output,
    curl_rc,
    binary_version="OpenTofu v1.13.2",
    python_rc=0,
    chmod_rc=0,
    tofu_rc=0,
    digest=_DIGEST,
    cached_digest=None,
    key=None,
    machine="x86_64",
    system="Linux",
):
    """Run the step's body against `_stubbed` and python3, chmod and tofu stubs.

    The python3 and chmod stubs append their names to the shared `order` log. The python3 stub
    exits `python_rc` when non-zero; otherwise it writes a `tofu` stub, without the exec bit,
    where `python3 -m zipfile -e` would. The chmod stub exits `chmod_rc` when non-zero;
    otherwise it runs the real chmod. The tofu stub records its argv and the `$GITHUB_PATH` text
    it sees, prints `binary_version` and a platform line, and exits `tofu_rc`.
    """
    bin_dir, logs = _stubbed(tmp_path, curl_output, curl_rc, digest, cached_digest, machine, system)
    log = logs.as_posix()
    write_stub(
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
    write_stub(
        bin_dir,
        "chmod",
        f"echo chmod >> '{log}/order'\nprintf '%s\\n' \"$@\" > '{log}/chmod'\n"
        f"[ {chmod_rc} = 0 ] || exit {chmod_rc}\n"
        'exec /usr/bin/chmod "$@"',
    )
    t = _TOOLS["tofu"]
    env = {
        "SHIPMATE_TOFU_VERSION": t["version"],
        "SHIPMATE_TOFU_SHA256": _pinned(t),
        "STUB_TOFU_VERSION": binary_version,
    }
    return _run(tmp_path, t["step"], bin_dir, logs, env, key)


_TOOLS = {
    "terramate": {
        "install": _install_terramate,
        "step": "Install Terramate",
        "label": "Terramate",
        "version": "0.17.1",
        "asset": "terramate_0.17.1_linux_x86_64.tar.gz",
        "arm64_asset": "terramate_0.17.1_linux_arm64.tar.gz",
        "url": (
            "https://github.com/terramate-io/terramate/releases/download/"
            "v0.17.1/terramate_0.17.1_linux_x86_64.tar.gz"
        ),
        "arm64_url": (
            "https://github.com/terramate-io/terramate/releases/download/"
            "v0.17.1/terramate_0.17.1_linux_arm64.tar.gz"
        ),
        "prefix": "::error title=Terramate install failed::Terramate 0.17.1 did not install: ",
        "sums": "checksums.txt",
        "release": "terramate-release",
        "bin": "terramate",
        "extract": "tar",
        "cache_key": "shipmate-tools-Linux-X64-terramate-0.17.1",
    },
    "tofu": {
        "install": _install_tofu,
        "step": "Install OpenTofu",
        "label": "OpenTofu",
        "version": "1.13.2",
        "asset": "tofu_1.13.2_linux_amd64.zip",
        "arm64_asset": "tofu_1.13.2_linux_arm64.zip",
        "url": (
            "https://github.com/opentofu/opentofu/releases/download/"
            "v1.13.2/tofu_1.13.2_linux_amd64.zip"
        ),
        "arm64_url": (
            "https://github.com/opentofu/opentofu/releases/download/"
            "v1.13.2/tofu_1.13.2_linux_arm64.zip"
        ),
        "prefix": "::error title=OpenTofu install failed::OpenTofu 1.13.2 did not install: ",
        "sums": "tofu_1.13.2_SHA256SUMS",
        "release": "tofu-release",
        "bin": "tofu",
        "extract": "python3",
        "cache_key": "shipmate-tools-Linux-X64-tofu-1.13.2",
    },
}


def _extract_argv(tool, archive, runner_temp):
    """The extract stub's whole argv for `archive`."""
    if tool == "terramate":
        return ["-xzf", archive, "-C", f"{runner_temp}/terramate", "terramate"]
    return ["-m", "zipfile", "-e", archive, f"{runner_temp}/tofu"]


def _archive(t, runner_temp):
    return f"{runner_temp}/{t['release']}/{t['asset']}"


def _verified(t):
    return f"Verified {t['asset']} sha256 {_DIGEST}.\n"


def _github_path(tmp_path):
    return (tmp_path / "github_path").read_text(encoding="utf-8")


def _annotation(t, code, rc):
    url = t["url"]
    return (
        f"{t['prefix']}{url} answered HTTP {code} from {url} (curl exit {rc}); "
        "re-run the failed job."
    )


def _mismatch(t, got):
    return (
        f"{t['url']} has sha256 {got}, not the {_DIGEST} the signed {t['sums']} names; the release "
        "asset differs from the signed one."
    )


def _warning(t, reason):
    return (
        f"::warning title=Tool cache entry failed verification::{t['label']} {t['version']}: "
        f"{reason} Downloading {t['asset']} again. Every job repeats this download until the entry "
        f"is deleted: gh cache delete {t['cache_key']} --repo o/r\n"
    )


def _cached(tmp_path, t, data=b"cached"):
    """Place a restored archive where the cache restore writes it."""
    release = tmp_path / "runner-temp" / t["release"]
    release.mkdir(parents=True)
    (release / t["asset"]).write_bytes(data)


def _install_cached(tmp_path, t, **kwargs):
    """`install` on a runner temp holding a restored archive, with the entry's key."""
    _cached(tmp_path, t)
    return t["install"](tmp_path, "200", 0, key=t["cache_key"], **kwargs)


def test_the_terramate_install_step_is_fail_closed():
    """No `continue-on-error`, no `if:`, no third-party `uses:`.

    Mutation: add `continue-on-error: true` to the step.
    """
    assert set(step_by(_ACTION, name="Install Terramate")) == {"name", "shell", "env", "run", "id"}


def test_the_tofu_install_step_is_fail_closed():
    """No `continue-on-error` and no third-party `uses:`.

    Mutation: add `continue-on-error: true` to the step.
    """
    assert set(step_by(_ACTION, name="Install OpenTofu")) == {
        "name",
        "if",
        "shell",
        "env",
        "run",
        "id",
    }


@bash_only
@pytest.mark.parametrize("machine", ("x86_64", "x64"))
def test_a_terramate_200_verifies_extracts_checks_the_binary_and_adds_the_directory_to_path(
    tmp_path, machine
):
    """Mutations: drop `--retry-all-errors`, `-L` or `--speed-time`, or add `--fail` (curl
    argv); drop the `terramate --version` line (terramate argv); move the sha256 comparison
    after `tar` (order); drop `|x64` from the step's `case` (the `x64` id).
    """
    t = _TOOLS["terramate"]
    r, runner_temp, logs = _install_terramate(tmp_path, "200", 0, machine=machine)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    tarball = f"{runner_temp}/terramate-release/terramate_0.17.1_linux_x86_64.tar.gz"
    assert argv_log(logs, "curl") == setup_curl_argv(t["url"], tarball)
    assert argv_log(logs, "sha256sum") == [tarball]
    assert argv_log(logs, "order") == ["sha256sum", "tar"]
    assert r.stdout == _verified(t)
    assert argv_log(logs, "tar") == ["-xzf", tarball, "-C", f"{runner_temp}/terramate", "terramate"]
    assert argv_log(logs, "terramate") == ["--version"]
    assert _github_path(tmp_path) == f"{runner_temp}/terramate\n"


@bash_only
@pytest.mark.parametrize("machine", ("x86_64", "x64"))
def test_a_tofu_200_verifies_extracts_checks_the_binary_and_adds_the_directory_to_path(
    tmp_path, machine
):
    """Mutations: drop `--retry-max-time 120` (curl argv); move the sha256 comparison after the
    extract (order); drop the `chmod` (order); append to `$GITHUB_PATH` before `tofu version`
    (the path the binary saw); drop `|x64` from the step's `case` (the `x64` id).
    """
    t = _TOOLS["tofu"]
    r, runner_temp, logs = _install_tofu(tmp_path, "200", 0, machine=machine)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    zip_path = f"{runner_temp}/tofu-release/tofu_1.13.2_linux_amd64.zip"
    assert argv_log(logs, "curl") == setup_curl_argv(t["url"], zip_path)
    assert argv_log(logs, "sha256sum") == [zip_path]
    assert argv_log(logs, "order") == ["sha256sum", "python3", "chmod"]
    assert r.stdout == _verified(t)
    assert argv_log(logs, "python3") == ["-m", "zipfile", "-e", zip_path, f"{runner_temp}/tofu"]
    assert argv_log(logs, "chmod") == ["+x", f"{runner_temp}/tofu/tofu"]
    assert argv_log(logs, "tofu") == ["version"]
    assert argv_log(logs, "path_at_version") == []
    assert _github_path(tmp_path) == f"{runner_temp}/tofu\n"


@bash_only
@_EACH_TOOL
def test_an_aarch64_runner_installs_the_arm64_asset(tmp_path, tool):
    """Mutations: map `aarch64|arm64` to the x86 arch in either step's `case` (digest mismatch);
    drop `aarch64|` from either `case` (no sha256 for the unmapped asset, curl never runs).
    Dropping `|arm64` cannot fail: the `*)` arm yields `arm64` from `uname -m` as well.
    """
    t = _TOOLS[tool]
    r, runner_temp, logs = t["install"](tmp_path, "200", 0, digest="b" * 64, machine="aarch64")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = f"{runner_temp}/{t['release']}/{t['arm64_asset']}"
    assert argv_log(logs, "curl") == setup_curl_argv(t["arm64_url"], archive)
    assert argv_log(logs, "sha256sum") == [archive]
    assert r.stdout == f"Verified {t['arm64_asset']} sha256 {'b' * 64}.\n"


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
@_EACH_TOOL
def test_a_failed_download_fails_the_step_with_one_annotation(
    tmp_path, tool, curl_output, curl_rc, code
):
    t = _TOOLS[tool]
    r, _, logs = t["install"](tmp_path, curl_output, curl_rc)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == _annotation(t, code, curl_rc) + "\n"
    assert argv_log(logs, t["extract"]) is None
    assert _github_path(tmp_path) == ""
    refusal_output(tmp_path, r.stdout)


@bash_only
@_EACH_TOOL
def test_a_404_names_the_version_and_platform_as_the_remedy(tmp_path, tool):
    """The URL has no asset, so a re-run cannot help.

    Mutation: pass the other remedy on a 404 in `fetch`.
    """
    t = _TOOLS[tool]
    r, _, logs = t["install"](tmp_path, "404", 0)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{t['prefix']}{t['url']} answered HTTP 404 from {t['url']} (curl exit 0); "
        f"check the signed {t['sums']} and the runner's OS and architecture.\n"
    )
    assert argv_log(logs, t["extract"]) is None
    assert _github_path(tmp_path) == ""
    refusal_output(tmp_path, r.stdout)


@pytest.mark.parametrize(
    ("tar_rc", "terramate_rc", "message"),
    [
        # Mutation: drop the `|| fail ...` after `tar`.
        (2, 0, "tar could not extract terramate from {url}."),
        # Mutation: drop the `|| fail ...` after `terramate --version`.
        (0, 1, "the binary from {url} failed to run terramate --version."),
    ],
)
@bash_only
def test_a_failed_terramate_extract_or_binary_fails_the_step_with_one_annotation(
    tmp_path, tar_rc, terramate_rc, message
):
    t = _TOOLS["terramate"]
    r, _, _ = _install_terramate(tmp_path, "200", 0, tar_rc=tar_rc, terramate_rc=terramate_rc)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_verified(t)}{t['prefix']}{message.format(url=t['url'])}\n"
    assert _github_path(tmp_path) == ""
    refusal_output(tmp_path, r.stdout)


@pytest.mark.parametrize(
    ("python_rc", "chmod_rc", "tofu_rc", "message"),
    [
        # Mutation: drop the `|| fail ...` after the extract.
        (1, 0, 0, "python3 could not extract OpenTofu from {url}."),
        # Mutation: drop the `|| fail ...` after `chmod`.
        (0, 1, 0, "could not make the OpenTofu binary executable."),
        # Mutation: drop the `|| fail ...` after `tofu version`.
        (0, 0, 1, "the OpenTofu binary from {url} failed its version check."),
    ],
)
@bash_only
def test_a_failed_tofu_extract_or_binary_fails_the_step_with_one_annotation(
    tmp_path, python_rc, chmod_rc, tofu_rc, message
):
    t = _TOOLS["tofu"]
    r, _, _ = _install_tofu(
        tmp_path, "200", 0, python_rc=python_rc, chmod_rc=chmod_rc, tofu_rc=tofu_rc
    )
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == f"{_verified(t)}{t['prefix']}{message.format(url=t['url'])}\n"
    assert _github_path(tmp_path) == ""
    refusal_output(tmp_path, r.stdout)


@bash_only
def test_a_terramate_binary_of_another_version_fails_the_step_with_one_annotation(tmp_path):
    """Mutation: replace the version comparison with a bare `"$dir/terramate" --version`."""
    t = _TOOLS["terramate"]
    r, _, _ = _install_terramate(tmp_path, "200", 0, binary_version="0.17.0")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_verified(t)}{t['prefix']}{t['url']} delivered version 0.17.0; "
        "check the signed checksums.txt and the release asset.\n"
    )
    assert _github_path(tmp_path) == ""
    refusal_output(tmp_path, r.stdout)


@bash_only
def test_a_tofu_binary_of_another_version_fails_the_step_with_one_annotation(tmp_path):
    """Mutation: replace the version comparison with a bare `"$bin/tofu" version`."""
    t = _TOOLS["tofu"]
    r, _, _ = _install_tofu(tmp_path, "200", 0, binary_version="OpenTofu v1.13.0")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_verified(t)}{t['prefix']}{t['url']} delivered OpenTofu v1.13.0; "
        "check the signed tofu_1.13.2_SHA256SUMS and the release asset.\n"
    )
    assert _github_path(tmp_path) == ""
    refusal_output(tmp_path, r.stdout)


@bash_only
@_EACH_TOOL
def test_a_digest_mismatch_fails_the_step_before_the_extract(tmp_path, tool):
    """The asset differs from the signed one, so the annotation names no re-run.

    Mutation: drop the sha256 comparison.
    """
    t = _TOOLS[tool]
    r, _, logs = t["install"](tmp_path, "200", 0, digest="c" * 64)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{t['prefix']}{t['url']} has sha256 {'c' * 64}, not the {_DIGEST} the signed "
        f"{t['sums']} names; the release asset differs from the signed one.\n"
    )
    assert argv_log(logs, t["extract"]) is None
    assert _github_path(tmp_path) == ""
    refusal_output(tmp_path, r.stdout)


@pytest.mark.parametrize(
    ("tool", "platform", "asset"),
    [
        # Mutation: drop the `|| fail` after the digest lookup.
        ("terramate", {"machine": "riscv64"}, "terramate_0.17.1_linux_riscv64.tar.gz"),
        ("tofu", {"machine": "riscv64"}, "tofu_1.13.2_linux_riscv64.zip"),
        # Mutation: hard-code `linux` in place of `uname -s` in either step.
        ("terramate", {"system": "Darwin"}, "terramate_0.17.1_darwin_x86_64.tar.gz"),
        ("tofu", {"system": "Darwin"}, "tofu_1.13.2_darwin_amd64.zip"),
    ],
    ids=["terramate-riscv64", "tofu-riscv64", "terramate-darwin", "tofu-darwin"],
)
@bash_only
def test_an_unpinned_platform_fails_the_step_before_curl_runs(tmp_path, tool, platform, asset):
    t = _TOOLS[tool]
    r, _, logs = t["install"](tmp_path, "200", 0, **platform)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{t['prefix']}the signed {t['sums']} yields no sha256 for {asset}; "
        "shipmate installs on Linux x86_64 and arm64.\n"
    )
    assert argv_log(logs, "curl") is None
    assert _github_path(tmp_path) == ""
    refusal_output(tmp_path, r.stdout)


@bash_only
@_EACH_TOOL
def test_a_restored_archive_whose_digest_matches_is_not_downloaded(tmp_path, tool):
    """Mutation: run the `fetch` before the restored-entry `if` (curl is called)."""
    t = _TOOLS[tool]
    r, runner_temp, logs = _install_cached(tmp_path, t)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = _archive(t, runner_temp)
    assert (argv_log(logs, "curl"), argv_log(logs, "sha256sum")) == (None, [archive])
    assert r.stdout == _verified(t)
    assert argv_log(logs, t["extract"]) == _extract_argv(tool, archive, runner_temp)
    assert _github_path(tmp_path) == f"{runner_temp}/{t['bin']}\n"


@bash_only
@_EACH_TOOL
def test_a_restored_archive_whose_digest_differs_is_downloaded_again_with_one_warning(
    tmp_path, tool
):
    """Mutations: fail on the restored archive (trust the hit: exit 1); download again without
    the warning."""
    t = _TOOLS[tool]
    r, runner_temp, logs = _install_cached(tmp_path, t, cached_digest="c" * 64)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = _archive(t, runner_temp)
    assert (argv_log(logs, "curl"), argv_log(logs, "sha256sum")) == (
        setup_curl_argv(t["url"], archive),
        [archive, archive],
    )
    assert r.stdout == _warning(t, _mismatch(t, "c" * 64)) + _verified(t)
    assert argv_log(logs, t["extract"]) == _extract_argv(tool, archive, runner_temp)
    assert _github_path(tmp_path) == f"{runner_temp}/{t['bin']}\n"


@bash_only
@_EACH_TOOL
def test_a_restored_archive_whose_fresh_copy_differs_too_fails_after_one_download(tmp_path, tool):
    """Mutation: download again until the digest matches (a bounded three-attempt loop: three
    curl calls, not one)."""
    t = _TOOLS[tool]
    r, runner_temp, logs = _install_cached(tmp_path, t, cached_digest="c" * 64, digest="d" * 64)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert argv_log(logs, "curl") == setup_curl_argv(t["url"], _archive(t, runner_temp))
    assert r.stdout == (
        _warning(t, _mismatch(t, "c" * 64)) + t["prefix"] + _mismatch(t, "d" * 64) + "\n"
    )
    assert (argv_log(logs, t["extract"]), _github_path(tmp_path)) == (None, "")
    refusal_output(tmp_path, r.stdout)


@bash_only
@_EACH_TOOL
def test_an_archive_present_without_a_restored_key_is_downloaded_again(tmp_path, tool):
    """Nothing explains a file present on a miss, so it is replaced, never verified.

    Mutation: ignore the key and keep any present archive (curl is not called).
    """
    t = _TOOLS[tool]
    _cached(tmp_path, t)
    r, runner_temp, logs = t["install"](tmp_path, "200", 0, key="")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert argv_log(logs, "curl") == setup_curl_argv(t["url"], _archive(t, runner_temp))


@bash_only
@_EACH_TOOL
def test_a_restored_archive_path_that_is_a_directory_is_downloaded_again(tmp_path, tool):
    """A restored path that is not a regular file costs a download, not the job.

    Mutation: `rm -f` in place of `rm -rf` before the download (the step exits 1 on the
    directory).
    """
    t = _TOOLS[tool]
    (tmp_path / "runner-temp" / t["release"] / t["asset"]).mkdir(parents=True)
    r, runner_temp, logs = t["install"](tmp_path, "200", 0, key=t["cache_key"])
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = _archive(t, runner_temp)
    assert (argv_log(logs, "curl"), argv_log(logs, "sha256sum")) == (
        setup_curl_argv(t["url"], archive),
        [archive],
    )
    assert r.stdout == _warning(t, f"{archive} is not a regular file.") + _verified(t)


@bash_only
@_EACH_TOOL
def test_an_empty_sha256sum_output_fails_naming_the_tool_not_the_asset(tmp_path, tool):
    """Mutation: drop the empty-digest guard (the annotation reports a tampered asset)."""
    t = _TOOLS[tool]
    r, runner_temp, logs = t["install"](tmp_path, "200", 0, digest="")
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = _archive(t, runner_temp)
    assert r.stdout == f"{t['prefix']}sha256sum printed no digest for {archive}.\n"
    assert (argv_log(logs, t["extract"]), _github_path(tmp_path)) == (None, "")
    refusal_output(tmp_path, r.stdout)


@bash_only
def test_a_curl_older_than_7_71_is_told_its_floor_by_the_install_step(tmp_path):
    """Mutation: drop the exit-2 branch of `fetch` (the re-run remedy appears)."""
    r, _, logs = _install_terramate(tmp_path, "000", 2)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        f"{_TOOLS['terramate']['prefix']}curl exited 2 on an option it does not know; shipmate "
        "needs curl 7.71 or later (CONTRACT.md §Runner prerequisites).\n"
    )
    assert argv_log(logs, "tar") is None
    refusal_output(tmp_path, r.stdout)


@bash_only
@_EACH_TOOL
def test_a_restored_archive_sha256sum_cannot_read_is_downloaded_again(tmp_path, tool):
    """An unreadable restored file: the sha256sum stub prints no digest for it, as sha256sum does
    on a read error. A fresh archive that yields none still fails (the test above).

    Mutation: `fail` in place of the empty-digest `reason` in `_release_check` (the step exits 1).
    """
    t = _TOOLS[tool]
    r, runner_temp, logs = _install_cached(tmp_path, t, cached_digest="")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = _archive(t, runner_temp)
    assert (argv_log(logs, "curl"), argv_log(logs, "sha256sum")) == (
        setup_curl_argv(t["url"], archive),
        [archive, archive],
    )
    assert r.stdout == _warning(t, f"sha256sum printed no digest for {archive}.") + _verified(t)
    assert _github_path(tmp_path) == f"{runner_temp}/{t['bin']}\n"


@bash_only
@_EACH_TOOL
def test_a_restored_archive_that_is_a_symlink_is_downloaded_again(tmp_path, tool):
    """The link is removed, not followed: its target stays as it was.

    Mutation: drop `[ -L "$archive" ]` from `_release_check` (the link's digest matches; curl is not
    called).
    """
    t = _TOOLS[tool]
    release = tmp_path / "runner-temp" / t["release"]
    release.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.write_bytes(b"cached")
    bash_symlink(tmp_path, target, release / t["asset"])
    r, runner_temp, logs = t["install"](tmp_path, "200", 0, key=t["cache_key"])
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    archive = _archive(t, runner_temp)
    assert (argv_log(logs, "curl"), argv_log(logs, "sha256sum")) == (
        setup_curl_argv(t["url"], archive),
        [archive],
    )
    assert r.stdout == _warning(t, f"{archive} is not a regular file.") + _verified(t)
    assert (target.read_bytes(), (release / t["asset"]).is_symlink()) == (b"cached", False)
