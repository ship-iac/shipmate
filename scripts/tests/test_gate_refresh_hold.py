"""A gate held by `gate-state` must not be greened by `gate-refresh`.

`gate-state`'s hold verdict lives only in the transient commit status, and `shipmate apply` never
consults the gate -- `scripts/authorize` reads `reviewDecision`. So a partial plan download holds
the gate, the parsed cells apply anyway, and `apply-gate` reports verdict=complete over those
cells' checks. Without this guard `gate-refresh` then PATCHes `shipmate / gate` to success, and
the pull request merges with planned stacks that were never applied and never would be:
`deploy-detect`'s work queue is the pending apply checks, and stacks whose summaries were never
read have no check at all.

Invariant: the only exit from a hold is a fresh plan run.

The tests execute the real, unmodified `Complete gate` step with `gh` replaced by a bash
function. Bash resolves a function before searching PATH, so this needs no fake executable. The
greening runs also pin the write's whole argv: endpoint, method and every field.
"""

import os
import subprocess

import pytest
from _loader import ACTIONS, step_by, usable_bash

_BASH = usable_bash()

HEAD_SHA = "a" * 40

# Dispatches on the two `gh api` calls the step makes: the pre-write gate read
# (`/commits/<sha>/status --jq ...`), and the write (`/statuses/<sha>`), whose argv it records.
GH_STUB = """
gh() {
  case "$*" in
    *"/status --jq"*) printf '%s' "$FAKE_GATE_STATE" ;;
    *"/statuses/"*) printf '%s\\n' "$@" > "$WROTE" ;;
    *) printf 'unexpected gh call: %s\\n' "$*" >&2 ; return 1 ;;
  esac
}
"""


def _run_step(tmp_path, gate_state):
    assert _BASH is not None  # callers are skipif-gated on this; narrows the type too
    script = tmp_path / "step.sh"
    script.write_text(
        GH_STUB + step_by("gate-refresh", name="Complete gate")["run"],
        encoding="utf-8",
        newline="\n",
    )
    wrote = tmp_path / "wrote"
    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "x",
            "GITHUB_ACTION_PATH": str(ACTIONS / "gate-refresh"),
            "HEAD_SHA": HEAD_SHA,
            # A different SHA, so posting to the merge commit instead of the head reddens.
            "GITHUB_SHA": "b" * 40,
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
    posted = wrote.read_text(encoding="utf-8").splitlines() if wrote.exists() else None
    return proc, posted


#: The whole `gh` argv a greening run must send, hand-written rather than read back from the
#: `Complete gate` run block: a derived expectation passes whatever that block says. This is the one
#: place the endpoint, the method (POST, implied by the fields), the context, the state and the run
#: link are pinned together. Matches the values `_run_step` supplies.
GREEN_ARGV = [
    "api",
    f"repos/acme/demo/statuses/{HEAD_SHA}",
    "-f",
    "state=success",
    "-f",
    "context=shipmate / gate",
    "-f",
    "description=all applies complete — nothing left to apply",
    "-f",
    "target_url=https://example.invalid/acme/demo/actions/runs/999",
]


def test_the_gate_is_read_before_it_is_written():
    # Structural companion to the behavioural tests: an ordering inversion would read the status
    # that the write itself made.
    run = step_by("gate-refresh", name="Complete gate")["run"]
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
    assert posted == GREEN_ARGV


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_an_absent_gate_still_greens(tmp_path):
    # `.[0].state // empty` yields an empty string when no gate status exists for the head SHA
    # at all. That is not a hold.
    proc, posted = _run_step(tmp_path, "")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert posted == GREEN_ARGV
