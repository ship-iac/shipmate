"""actions/setup resolves its tool versions from the release's own VERSIONS file.

The action takes no input: it reads `$GITHUB_ACTION_PATH/../../VERSIONS` at the SHA the consumer
pinned. The structural guards pin the shape that makes that file the source -- the resolve step
runs before both installers, and both installers read its outputs -- and the behavioural ones
execute the shipped `run:` body against a hand-written VERSIONS fixture. One case runs the
repository's own file, because that is the file every consumer of this release reads.

The fail-closed property is what the behavioural cases exist for: `opentofu/setup-opentofu`
resolves an empty `tofu_version` as "latest", so a missing file or a missing key must fail the
step and write no output at all. Mutations: moving the `$GITHUB_OUTPUT` write above the emptiness
checks reds the missing-key case, and deleting the file check reds the missing-file one.
"""

import os
import re

from _loader import ENGINE, action_steps, action_yaml, bash_only, run_step, step_by

_ACTION = "setup"

#: Every step, in order. Hand-written: the resolve step must precede both installers, and one
#: whole-list comparison pins that ordering without a second selector to disagree with.
_EXPECTED_STEPS = [
    "Resolve versions",
    "Install OpenTofu",
    "Retry transient download errors",
    "Install Terramate",
    "Report a failed Terramate install",
    "Provider plugin cache",
    "Restore plugin cache",
]

#: Each installer's whole `with:` block. `uses:` is deliberately absent -- it is a pin, bumped on
#: its own schedule, and pinning it here would red this guard on every pin bump.
_EXPECTED_WITH = {
    "Install OpenTofu": {
        "tofu_version": "${{ steps.versions.outputs.tofu }}",
        "tofu_wrapper": False,
    },
    "Install Terramate": {"version": "${{ steps.versions.outputs.terramate }}"},
}

_FIXTURE_VERSIONS = "terramate=9.9.9\ntofu=8.8.8\n"


def test_the_steps_run_in_this_order():
    """Reds when the resolve step is moved after the installers."""
    assert [s.get("name") for s in action_steps(_ACTION)] == _EXPECTED_STEPS


def test_both_installers_read_the_resolve_steps_outputs():
    """Reds when an installer is pointed at anything but the resolve step's outputs."""
    got = {name: step_by(_ACTION, name=name).get("with") for name in _EXPECTED_WITH}
    assert got == _EXPECTED_WITH
    assert step_by(_ACTION, name="Resolve versions").get("id") == "versions"


def test_the_action_takes_no_input():
    """Reds when a version override input comes back: the VERSIONS file is the only source."""
    assert "inputs" not in action_yaml(_ACTION)


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
    r = run_step(
        tmp_path,
        step_by(_ACTION, name="Resolve versions")["run"],
        {**os.environ, "GITHUB_ACTION_PATH": action_path.as_posix(), "GITHUB_OUTPUT": str(out)},
        cwd=cwd,
    )
    return r, out.read_text(encoding="utf-8"), f"{action_path.as_posix()}/../../VERSIONS"


@bash_only
def test_both_versions_come_from_the_file(tmp_path):
    r, out, _ = _resolve(tmp_path)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == "terramate=9.9.9\ntofu=8.8.8\n"


@bash_only
def test_the_repositorys_own_versions_file_resolves(tmp_path):
    """The cases above prove the logic; this one proves the file it runs against.

    `sed -n 's/^terramate=//p'` is stricter than the `line.partition("=")` reader this
    replaced: a spaced `tofu = 1.13.0`, or a UTF-8 BOM, refuses at runtime in every job of the
    release, and no other check reads this file. Read as bytes and decoded so a BOM or CRLF
    reaches the body untranslated. The shape is asserted, never the numbers, or a tool-version
    bump reds this.

    Mutation: write `tofu = 1.12.4` into VERSIONS.
    """
    real = (ENGINE / "VERSIONS").read_bytes().decode("utf-8")
    r, out, _ = _resolve(tmp_path, versions=real)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert re.fullmatch(r"terramate=\S+\ntofu=\S+\n", out), out


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
