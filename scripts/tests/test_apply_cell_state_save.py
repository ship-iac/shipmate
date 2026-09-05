"""apply-cell must save state after a *cancelled* apply, not only a failed one.

A cancelled `tofu apply` (manual cancel, or `apply-<env>-<stack>` concurrency
displacement) mutates local state per-resource exactly like a failed apply. If
`Save state` is guarded by `!cancelled()`, that partial state is discarded, the
next run restores the pre-apply state, and retries orphan resources / duplicate
creates.

The state sink is `actions/cache`, commit-on-complete and keyed per `run_id-run_attempt`: a
torn-down upload never commits and cannot clobber the last-good entry, so saving on cancellation
is safe. The correct guard is therefore `always()`, still gated on a successful restore so that a
state which was never restored is never saved. Mutation: regress that guard to `!cancelled()`.
"""

import re

from _loader import action_steps


def _save_state_step():
    saves = [
        s
        for s in action_steps("apply-cell")
        if s.get("name") == "Save state" and "state" in str(s.get("uses", ""))
    ]
    assert len(saves) == 1, f"expected exactly one 'Save state' step, got {len(saves)}"
    return saves[0]


def _guard(step):
    """The step's `if:` with the ${{ }} wrapper stripped and whitespace collapsed."""
    raw = str(step.get("if", "")).strip()
    inner = re.sub(r"^\$\{\{\s*|\s*\}\}$", "", raw)
    return re.sub(r"\s+", " ", inner).strip()


def test_save_state_runs_on_cancellation():
    # always(), not !cancelled(), so a cancelled apply's partial state persists. The state-path
    # conjunct skips the save entirely when a remote backend owns state; it does not weaken
    # always() for the artifact-state path.
    assert _guard(_save_state_step()) == (
        "always() && inputs.state-path != '' && steps.restore-state.outcome == 'success'"
    )


def test_save_state_not_gated_by_not_cancelled():
    # The whole defect: !cancelled() drops the cancelled apply's mutated state.
    assert "!cancelled()" not in _guard(_save_state_step())


def test_save_state_still_requires_successful_restore():
    # Never save a state that was never restored, the restore itself having failed.
    assert "steps.restore-state.outcome == 'success'" in _guard(_save_state_step())
