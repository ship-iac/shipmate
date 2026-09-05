"""A gate held by `gate-state` must not be greened by `gate-refresh`.

`gate-state`'s hold verdict lives only in the transient commit status, and `shipmate apply` never
consults the gate -- `scripts/authorize` reads `reviewDecision`. So a partial plan download holds
the gate, the parsed cells apply anyway, and `apply-gate` reports verdict=complete over those
cells' checks. Without this guard `gate-refresh` then PATCHes `shipmate / gate` to success, and
the pull request merges with planned stacks that were never applied and never would be:
`deploy-detect`'s work queue is the pending apply checks, and stacks whose summaries were never
read have no check at all.

Invariant: the only exit from a hold is a fresh plan run.

The tests execute the real, unmodified `Complete gate` step with `gh` and `python3` replaced by
bash functions. Bash resolves a function before searching PATH, so this needs no fake
executables. `python3` forwards to the real interpreter, so the greening runs also pin the whole
body `scripts/gate-status-body` produces.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest
from _loader import ACTIONS, action_steps, usable_bash

_BASH = usable_bash()

HEAD_SHA = "a" * 40

# Dispatches on the two `gh api` calls the step makes: the pre-write gate read
# (`/commits/<sha>/status --jq ...`), and the write (`/statuses/<sha>`). `python3` resolves to
# the real interpreter rather than a stub printing `{}`, so `scripts/gate-status-body` runs as
# shipped: a body builder that died or printed nothing would leave `gh api --input gate.json`
# reading an empty file, which these tests would otherwise never see.
GH_STUB = f"""
gh() {{
  case "$*" in
    *"/status --jq"*) printf '%s' "$FAKE_GATE_STATE" ;;
    *"/statuses/"*) cp gate.json "$WROTE" ;;
    *) printf 'unexpected gh call: %s\\n' "$*" >&2 ; return 1 ;;
  esac
}}
python3() {{ '{pathlib.Path(sys.executable).as_posix()}' "$@" ; }}
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
            "GITHUB_ACTION_PATH": str(ACTIONS / "gate-refresh"),
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
    posted = json.loads(wrote.read_text(encoding="utf-8")) if wrote.exists() else None
    return proc, posted


#: The whole body a greening run must POST, hand-written rather than read back from
#: `scripts/gate-status-body`: a derived expectation passes whatever that file says, and this is
#: the one place the context, the state and the run link are pinned together. Matches the
#: GITHUB_* values `_run_step` supplies.
GREEN_BODY = {
    "state": "success",
    "context": "shipmate / gate",
    "description": "all applies complete — nothing left to apply",
    "target_url": "https://example.invalid/acme/demo/actions/runs/999",
}


def test_the_gate_is_read_before_it_is_written():
    # Structural companion to the behavioural tests: an ordering inversion would read the status
    # that the write itself made.
    run = _complete_step()["run"]
    assert run.index("held=$(gh api") < run.index("/statuses/$HEAD_SHA")


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_refuses_to_green_a_held_gate(tmp_path):
    proc, posted = _run_step(tmp_path, "failure")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    assert "Re-plan" in proc.stdout
    assert posted is None, f"a held gate was overwritten with {posted!r}"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_pending_gate_still_greens(tmp_path):
    # The legitimate transition this refusal must not break: gate-state writes `pending` while
    # applies are outstanding, and completing them is what gate-refresh exists to record.
    proc, posted = _run_step(tmp_path, "pending")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert posted == GREEN_BODY


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_an_absent_gate_still_greens(tmp_path):
    # `.[0].state // empty` yields an empty string when no gate status exists for the head SHA
    # at all. That is not a hold.
    proc, posted = _run_step(tmp_path, "")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert posted == GREEN_BODY
