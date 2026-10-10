"""actions/setup resolves its tool versions from the release's own VERSIONS file.

No input selects a version: the action reads `$GITHUB_ACTION_PATH/../../VERSIONS` at the SHA the
consumer pinned. Its one input, `tofu`, only decides whether OpenTofu is installed at all.

The structural guards pin the shape that makes that file the source -- the resolve step runs
before both installers, and both installers read its outputs -- and the behavioural ones execute
the shipped `run:` body against a hand-written VERSIONS fixture. One case runs the repository's
own file, because that is the file every consumer of this release reads.

The fail-closed property is what the behavioural cases exist for: `opentofu/setup-opentofu`
resolves an empty `tofu_version` as "latest", so a missing file or a missing key must fail the
step and write no output at all. Mutations: moving the `$GITHUB_OUTPUT` write above the emptiness
checks reds the missing-key case, and deleting the file check reds the missing-file one.
"""

import os
import re
import shutil
import stat
from pathlib import Path

import pytest
from _loader import ENGINE, action_steps, action_yaml, bash_only, run_step, step_by

_ACTION = "setup"

#: Every step, in order. Hand-written: the resolve step must precede both installers, and one
#: whole-list comparison pins that ordering without a second selector to disagree with.
_EXPECTED_STEPS = [
    "Resolve versions",
    "Install OpenTofu",
    "Install Terramate",
    "Provider plugin cache",
]

#: The OpenTofu installer's whole `with:` block. `uses:` is deliberately absent -- it is a pin,
#: bumped on its own schedule, and pinning it here would red this guard on every pin bump.
_EXPECTED_TOFU_WITH = {
    "tofu_version": "${{ steps.versions.outputs.tofu }}",
    "tofu_wrapper": False,
    "checksums": "${{ steps.versions.outputs.tofu_sha256 }}",
}

#: The Terramate installer is a `run:` step; its whole `env:` block carries the version and the
#: pinned `sha256sum` lines.
_EXPECTED_TERRAMATE_ENV = {
    "SHIPMATE_TERRAMATE_VERSION": "${{ steps.versions.outputs.terramate }}",
    "SHIPMATE_TERRAMATE_SHA256": "${{ steps.versions.outputs.terramate_sha256 }}",
}

_A, _B, _C, _D, _E = (c * 64 for c in "abcde")

#: Two versions, the four pinned assets, and one line per tool naming another version.
_FIXTURE_VERSIONS = (
    "terramate=9.9.9\ntofu=8.8.8\n"
    f"{_A}  terramate_9.9.9_linux_x86_64.tar.gz\n"
    f"{_B}  terramate_9.9.9_linux_arm64.tar.gz\n"
    f"{_E}  terramate_9.9.8_linux_x86_64.tar.gz\n"
    f"{_C}  tofu_8.8.8_linux_amd64.zip\n"
    f"{_D}  tofu_8.8.8_linux_arm64.zip\n"
    f"{_E}  tofu_8.8.7_linux_amd64.zip\n"
)

#: The whole $GITHUB_OUTPUT the fixture resolves to, hand-written: the Terramate lines whole for
#: the install step's lookup, the tofu digests hash-only for setup-opentofu's `checksums`.
_FIXTURE_OUTPUT = (
    "terramate=9.9.9\ntofu=8.8.8\n"
    "terramate_sha256<<SHIPMATE_EOF\n"
    f"{_A}  terramate_9.9.9_linux_x86_64.tar.gz\n"
    f"{_B}  terramate_9.9.9_linux_arm64.tar.gz\n"
    "SHIPMATE_EOF\n"
    f"tofu_sha256<<SHIPMATE_EOF\n{_C}\n{_D}\nSHIPMATE_EOF\n"
)


def _outputs(text):
    """Parse $GITHUB_OUTPUT text, `key=value` lines and `key<<DELIM` blocks alike."""
    got, lines = {}, iter(text.splitlines())
    for line in lines:
        if "<<" in line:
            key, delim = line.split("<<", 1)
            got[key] = "\n".join(iter(lines.__next__, delim))
        else:
            key, _, value = line.partition("=")
            got[key] = value
    return got


def test_the_steps_run_in_this_order():
    """Reds when the resolve step is moved after the installers."""
    assert [s.get("name") for s in action_steps(_ACTION)] == _EXPECTED_STEPS


def test_both_installers_read_the_resolve_steps_outputs():
    """Reds when an installer is pointed at anything but the resolve step's outputs."""
    got = (
        step_by(_ACTION, name="Install OpenTofu").get("with"),
        step_by(_ACTION, name="Install Terramate").get("env"),
    )
    assert got == (_EXPECTED_TOFU_WITH, _EXPECTED_TERRAMATE_ENV)
    assert step_by(_ACTION, name="Resolve versions").get("id") == "versions"


#: The action's whole `inputs:` mapping. A version override input would read as a second source
#: beside VERSIONS, so any input beyond `tofu` reds this.
_EXPECTED_INPUTS = {
    "tofu": {
        "description": 'Whether to install OpenTofu. "false" for jobs that never run it.',
        "required": False,
        "default": "true",
    },
}


def test_the_only_input_is_the_tofu_switch():
    """Mutations: `default: "true"` -> `"false"`; add a `tofu-version` input."""
    assert action_yaml(_ACTION).get("inputs") == _EXPECTED_INPUTS


def test_only_the_opentofu_install_is_gated_on_the_tofu_input():
    """Composite inputs are strings, so anything but "false" installs OpenTofu.

    Mutation: `!=` -> `==` in the `Install OpenTofu` step's `if:`.
    """
    got = {s.get("name"): s.get("if") for s in action_steps(_ACTION)}
    assert got == {
        "Resolve versions": None,
        "Install OpenTofu": "${{ inputs.tofu != 'false' }}",
        "Install Terramate": None,
        "Provider plugin cache": None,
    }


def _resolve(tmp_path, versions=_FIXTURE_VERSIONS):
    """Execute the real `Resolve versions` body against a fixture VERSIONS two directories up
    from a fake `$GITHUB_ACTION_PATH`, and return (result, the raw $GITHUB_OUTPUT text, the path
    the body is expected to name in its refusals).

    `versions=None` writes no file, which is the missing-release-file case.
    """
    action_path = tmp_path / "actions" / "setup"
    action_path.mkdir(parents=True)
    if versions is not None:
        (tmp_path / "VERSIONS").write_text(versions, encoding="utf-8", newline="\n")
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    # Two levels below the cwd's `../../` holds no VERSIONS, so a body that read `../../VERSIONS`
    # relative to the cwd instead of to the action finds nothing rather than the fixture.
    cwd = tmp_path / "elsewhere" / "a" / "b"
    cwd.mkdir(parents=True)
    # Git for Windows' gawk reads in text mode and drops a carriage return that Linux awk keeps;
    # BINMODE=1 makes it read bytes, so the CRLF case can fail on either platform.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    awk = bin_dir / "awk"
    real_awk = shutil.which("awk")
    assert real_awk, "awk is not on PATH"
    real_awk = Path(real_awk).as_posix()
    awk.write_text(
        f'#!/bin/sh\nexec "{real_awk}" -v BINMODE=1 "$@"\n', encoding="utf-8", newline="\n"
    )
    awk.chmod(awk.stat().st_mode | stat.S_IEXEC)
    r = run_step(
        tmp_path,
        step_by(_ACTION, name="Resolve versions")["run"],
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "GITHUB_ACTION_PATH": action_path.as_posix(),
            "GITHUB_OUTPUT": str(out),
        },
        cwd=cwd,
    )
    # Bytes, because read_text's newline translation would hide a carriage return.
    output = out.read_bytes().decode("utf-8")
    return r, output, f"{action_path.as_posix()}/../../VERSIONS"


@bash_only
def test_both_versions_and_their_digests_come_from_the_file(tmp_path):
    """Mutations: emit the Terramate digests into `tofu_sha256`; drop the version from the
    lookup prefix (the other-version lines reach the outputs).
    """
    r, out, _ = _resolve(tmp_path)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == _FIXTURE_OUTPUT


@bash_only
def test_a_crlf_versions_file_yields_outputs_without_carriage_returns(tmp_path):
    """setup-opentofu compares each `checksums` line to a bare hex digest, and the install step
    compares asset names exactly, so a carriage return would fail every install.

    Mutation: drop the `tr -d '\\r'` from the digest lookup.
    """
    r, out, _ = _resolve(tmp_path, versions=_FIXTURE_VERSIONS.replace("\n", "\r\n"))
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == _FIXTURE_OUTPUT


@pytest.mark.parametrize(("tool", "version"), [("terramate", "9.9.9"), ("tofu", "8.8.8")])
@bash_only
def test_a_version_without_digest_lines_fails_the_step_and_writes_nothing(tmp_path, tool, version):
    """A version bump that left only the previous release's checksum lines.

    Mutation: drop the emptiness check for that tool's digests.
    """
    fixture = _FIXTURE_VERSIONS.replace(f"{tool}_{version}_", f"{tool}_0.0.1_")
    r, out, versions = _resolve(tmp_path, versions=fixture)
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == ""
    assert r.stdout.strip() == (
        f"::error::{versions} carries no sha256 line for {tool} {version}; "
        "paste the release's checksum lines."
    )


@bash_only
def test_the_repositorys_own_versions_file_resolves(tmp_path):
    """The cases above prove the logic; this one proves the file it runs against.

    `sed -n 's/^terramate=//p'` is stricter than the `line.partition("=")` reader this
    replaced: a spaced `tofu = 1.13.0`, or a UTF-8 BOM, refuses at runtime in every job of the
    release, and no other check reads this file. Read as bytes and decoded so a BOM or CRLF
    reaches the body untranslated. The shape is asserted, never the numbers, or a tool-version
    bump reds this. Both Linux architectures of both tools must be pinned: a missing one
    resolves here and refuses only on that architecture's runners.

    Mutations: write `tofu = 1.12.4` into VERSIONS; delete the two tofu lines from it.
    """
    real = (ENGINE / "VERSIONS").read_bytes().decode("utf-8")
    r, out, _ = _resolve(tmp_path, versions=real)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    got = _outputs(out)
    assert set(got) == {"terramate", "tofu", "terramate_sha256", "tofu_sha256"}, out
    v = got["terramate"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", v), out
    assert re.fullmatch(r"\d+\.\d+\.\d+", got["tofu"]), out
    terramate = {}
    for line in got["terramate_sha256"].split("\n"):
        digest, _, asset = line.partition("  ")
        terramate[asset] = digest
    assert set(terramate) == {
        f"terramate_{v}_linux_x86_64.tar.gz",
        f"terramate_{v}_linux_arm64.tar.gz",
    }, out
    tofu = got["tofu_sha256"].split("\n")
    digests = [*terramate.values(), *tofu]
    assert len(digests) == 4, out
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in digests), out
    assert len(set(digests)) == 4, out


@bash_only
def test_a_missing_versions_file_fails_the_step_and_writes_nothing(tmp_path):
    r, out, versions = _resolve(tmp_path, versions=None)
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == ""
    assert r.stdout.strip() == (
        f"::error::{versions} does not exist: it carries the terramate= and tofu= versions "
        "this release pins."
    )


@bash_only
def test_a_missing_key_fails_the_step_and_writes_nothing(tmp_path):
    """A present file missing one key is the case an "install latest" fallback would hide."""
    r, out, versions = _resolve(tmp_path, versions="terramate=9.9.9\n")
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == ""
    assert r.stdout.strip() == (f"::error::{versions} carries no tofu= line; restore it.")
