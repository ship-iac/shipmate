"""A gate held by `gate-state` must not be greened by `gate-refresh`.

`gate-state`'s hold verdict lives only in the transient commit status, and
`shipmate apply` never consults the gate (`scripts/authorize` reads
`reviewDecision`). So a partial plan download holds the gate, the parsed cells
apply anyway, `apply-gate` reports verdict=complete over those cells' checks --
and, before this guard, `gate-refresh` PATCHed `shipmate / gate` to success. The
pull request then merged with planned stacks that were never applied, and never
would be: `deploy-detect`'s work queue IS the pending apply checks, and stacks
whose summaries were never read have no check at all.

Invariant: the only exit from a hold is a fresh plan run.

The tests execute the real, unmodified `Complete gate` step with `gh` and
`python3` replaced by bash functions -- bash resolves a function before
searching PATH, so this needs no fake executables.
"""

import os
import subprocess

import pytest
from _loader import action_steps, usable_bash

_BASH = usable_bash()

HEAD_SHA = "a" * 40

# Dispatches on the two `gh api` calls the step makes: the pre-write gate read
# (`/commits/<sha>/status --jq ...`) and the write (`/statuses/<sha>`).
GH_STUB = """
gh() {
  case "$*" in
    *"/status --jq"*) printf '%s' "$FAKE_GATE_STATE" ;;
    *"/statuses/"*) printf wrote > "$WROTE" ;;
    *) printf 'unexpected gh call: %s\\n' "$*" >&2 ; return 1 ;;
  esac
}
python3() { cat > /dev/null ; printf '{}' ; }
"""


def _complete_step():
    matches = [s for s in action_steps("gate-refresh") if s.get("name") == "Complete gate"]
    assert len(matches) == 1, f"expected exactly one Complete gate step, got {len(matches)}"
    return matches[0]


def _run_step(tmp_path, gate_state):
    assert _BASH is not None  # callers are skipif-gated on this; narrows the type too
    script = tmp_path / "step.sh"
    script.write_text(GH_STUB + _complete_step()["run"], encoding="utf-8", newline="\n")
    wrote = tmp_path / "wrote"
    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "x",
            "HEAD_SHA": HEAD_SHA,
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_SERVER_URL": "https://example.invalid",
            "GITHUB_RUN_ID": "999",
            "FAKE_GATE_STATE": gate_state,
            "WROTE": str(wrote),
        }
    )
    proc = subprocess.run(
        [_BASH, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    return proc, wrote.exists()


def test_the_gate_is_read_before_it_is_written():
    # Structural companion to the behavioural tests: an ordering inversion
    # would read the status the write just made.
    run = _complete_step()["run"]
    assert run.index("held=$(gh api") < run.index("/statuses/$HEAD_SHA")


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_refuses_to_green_a_held_gate(tmp_path):
    proc, wrote = _run_step(tmp_path, "failure")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    assert "Re-plan" in proc.stdout
    assert not wrote, "a held gate was overwritten"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_pending_gate_still_greens(tmp_path):
    # The legitimate transition this refusal must not break: gate-state writes
    # `pending` while applies are outstanding, and completing them is exactly
    # what gate-refresh exists to record.
    proc, wrote = _run_step(tmp_path, "pending")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert wrote


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_an_absent_gate_still_greens(tmp_path):
    # `.[0].state // empty` yields an empty string when no gate status exists
    # for the head SHA at all; that is not a hold.
    proc, wrote = _run_step(tmp_path, "")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert wrote
