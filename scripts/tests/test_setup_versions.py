"""actions/setup resolves its tool versions from the release's own VERSIONS file.

Both inputs are overrides now: empty means "use the versions this release pins", read from
`$GITHUB_ACTION_PATH/../../VERSIONS` at the SHA the consumer pinned. The structural guards pin
the shape that makes the fallback reachable -- the resolve step runs before both installers, and
both installers read its outputs rather than the inputs -- and the behavioural ones execute the
shipped `run:` body against a hand-written VERSIONS fixture, never the repository's own file.

The fail-closed property is what the behavioural cases exist for: `opentofu/setup-opentofu`
resolves an empty `tofu_version` as "latest", so a missing file or a missing key must fail the
step and write no output at all. Mutations: moving the `$GITHUB_OUTPUT` write above the emptiness
checks reds the missing-key case, and deleting the file check reds the missing-file one.
"""

import os
import subprocess

import pytest
from _loader import action_steps, action_yaml, usable_bash

_BASH = usable_bash()
_ACTION = "setup"

#: Every step, in order. Hand-written: the resolve step must precede both installers, and one
#: whole-list comparison pins that ordering without a second selector to disagree with.
_EXPECTED_STEPS = [
    "Resolve versions",
    "Install OpenTofu",
    "Install Terramate",
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

#: Both inputs, whole: optional and empty-by-default is what makes the file the source.
_EXPECTED_INPUTS = {
    "terramate-version": (False, ""),
    "tofu-version": (False, ""),
}

_FIXTURE_VERSIONS = "terramate=9.9.9\ntofu=8.8.8\n"


def _step(name):
    matches = [s for s in action_steps(_ACTION) if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}, got {len(matches)}"
    return matches[0]


def test_the_steps_run_in_this_order():
    """Reds when the resolve step is moved after the installers."""
    assert [s.get("name") for s in action_steps(_ACTION)] == _EXPECTED_STEPS


def test_both_installers_read_the_resolve_steps_outputs():
    """Reds when an installer is pointed back at `inputs.tofu-version`."""
    got = {name: _step(name).get("with") for name in _EXPECTED_WITH}
    assert got == _EXPECTED_WITH
    assert _step("Resolve versions").get("id") == "versions"


def test_both_inputs_are_optional_and_default_to_empty():
    """Reds when either input goes back to `required: true` with no default."""
    inputs = action_yaml(_ACTION)["inputs"]
    got = {name: (spec.get("required"), spec.get("default")) for name, spec in inputs.items()}
    assert got == _EXPECTED_INPUTS


def _resolve(tmp_path, versions=_FIXTURE_VERSIONS, **env):
    """Execute the real `Resolve versions` body against a fixture VERSIONS two directories up
    from a fake `$GITHUB_ACTION_PATH`, and return (result, the raw $GITHUB_OUTPUT text, the path
    the body is expected to name in its refusals).

    `versions=None` writes no file, which is the missing-release-file case.
    """
    assert _BASH is not None  # callers are skipif-gated; also narrows the type
    action_path = tmp_path / "actions" / "setup"
    action_path.mkdir(parents=True)
    if versions is not None:
        (tmp_path / "VERSIONS").write_text(versions, encoding="utf-8", newline="\n")
    script = tmp_path / "resolve.sh"
    script.write_text(_step("Resolve versions")["run"], encoding="utf-8", newline="\n")
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    full = dict(os.environ)
    full.update(
        {
            "GITHUB_ACTION_PATH": action_path.as_posix(),
            "GITHUB_OUTPUT": str(out),
            "TERRAMATE_VERSION": "",
            "TOFU_VERSION": "",
        }
    )
    full.update(env)
    r = subprocess.run(
        [_BASH, str(script)], env=full, capture_output=True, encoding="utf-8", timeout=30
    )
    return r, out.read_text(encoding="utf-8"), f"{action_path.as_posix()}/../../VERSIONS"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_empty_inputs_take_both_versions_from_the_file(tmp_path):
    r, out, _ = _resolve(tmp_path)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == "terramate=9.9.9\ntofu=8.8.8\n"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_non_empty_input_wins_over_the_file(tmp_path):
    """Reds when the file read drops its `[ -n "$TOFU_VERSION" ] ||` guard."""
    r, out, _ = _resolve(tmp_path, TOFU_VERSION="1.2.3")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == "terramate=9.9.9\ntofu=1.2.3\n"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_missing_versions_file_fails_the_step_and_writes_nothing(tmp_path):
    r, out, versions = _resolve(tmp_path, versions=None)
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == ""
    assert r.stdout.strip() == (
        f"::error::{versions} does not exist: it carries the terramate= and tofu= versions "
        "this release pins."
    )


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_missing_key_fails_the_step_and_writes_nothing(tmp_path):
    """A present file missing one key is the case an "install latest" fallback would hide."""
    r, out, versions = _resolve(tmp_path, versions="terramate=9.9.9\n")
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert out == ""
    assert r.stdout.strip() == (
        f"::error::{versions} carries no tofu= line: pass tofu-version to actions/setup, "
        "or restore the line."
    )
