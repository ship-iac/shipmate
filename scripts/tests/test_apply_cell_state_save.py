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

from _loader import action_steps, action_yaml, step_by


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
    # always(), not !cancelled(), so a cancelled apply's partial state persists.
    assert (
        _guard(_save_state_step()) == "always() && steps.init.outputs.restore-outcome == 'success'"
    )


def test_save_state_not_gated_by_not_cancelled():
    # The whole defect: !cancelled() drops the cancelled apply's mutated state.
    assert "!cancelled()" not in _guard(_save_state_step())


def test_save_state_still_requires_successful_restore():
    # Never save a state that was never restored, the restore itself having failed.
    assert "steps.init.outputs.restore-outcome == 'success'" in _guard(_save_state_step())


_LOST_SAVE_WARNING = {
    "name": "Warn that the applied state was not saved",
    "id": "lost-save",
    "if": (
        "${{ always() && steps.apply.outcome != 'skipped' "
        "&& steps.init.outputs.restore-outcome == 'success' "
        "&& steps.save-state.outputs.saved != 'true' }}"
    ),
    "shell": "bash",
    "run": (
        'echo "::error::The apply step ran and its state was not saved or not verified. Never '
        "re-run this job or its workflow run, and do not re-comment 'shipmate apply' for this "
        "cell until the recovery has moved its state: either applies the reviewed plan again "
        "against the state from before the apply. Until then every plan of this stack, on any "
        "pull request, repeats the lost apply: apply none but the recovery's import and forget "
        "plans. Follow docs/troubleshooting.md section The apply step ran and its state was not "
        'saved."\n'
        "exit 1\n"
    ),
}


def test_an_unsaved_state_after_an_apply_fails_the_cell_and_warns_against_a_re_run():
    """The warning fires whenever an apply step ran over restored state and the save is not
    verified (failed, cancelled, not found, or skipped for an absent file), a cancelled run
    included, where failure() is false; it fails the job so the apply check stays pending; its
    text forbids a re-run and a re-comment and calls no re-plan unsafe.

    Mutations: `steps.save-state.outputs.saved != 'true'` to `steps.save-state.outcome ==
    'failure'`; drop the `exit 1`; restore the old "or re-plan against the cache" text.
    """
    steps = action_steps("apply-cell")
    names = [s.get("name") for s in steps]
    warning = steps[names.index("Warn that the applied state was not saved")]
    assert warning == _LOST_SAVE_WARNING
    assert steps[names.index("Save state")]["id"] == "save-state"


_STATE_KEY = "${{ steps.key.outputs.key }}"

_LOOKUP_STEP = {
    "name": "Look up the saved entry",
    "id": "verify",
    "if": "${{ inputs.mode == 'save' && steps.encrypt.outputs.skip != 'true' }}",
    "uses": "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
    "with": {"path": "${{ inputs.path }}", "key": _STATE_KEY, "lookup-only": "true"},
}


def test_the_state_save_is_verified_by_an_exact_key_lookup():
    """`actions/cache/save` warns and succeeds on a cache-service error, so only a lookup of the
    exact key proves the entry exists; a `restore-keys` would let an older entry answer for it.

    Mutations: add `restore-keys` to the lookup; change the lookup's key; give the lookup an
    `if:` other than the save's.
    """
    lookup = step_by("state", id="verify")
    assert lookup == _LOOKUP_STEP
    save = step_by("state", name="Save state")
    assert save["with"]["key"] == _STATE_KEY
    assert save["if"] == _LOOKUP_STEP["if"]


def test_saved_is_true_only_on_the_lookups_exact_hit():
    """Mutations: `value: 'true'`; `value: ${{ steps.encrypt.outputs.skip }}`."""
    assert action_yaml("state")["outputs"] == {
        "saved": {
            "description": (
                "`true` only when the save mode's lookup found the entry this run saved under its "
                "exact key. Anything else, empty included, means the state was not saved: the "
                "save failed, was cancelled or skipped, or the cache stored nothing."
            ),
            "value": "${{ steps.verify.outputs.cache-hit }}",
        }
    }


def test_the_cell_summary_reads_the_lost_save_warnings_outcome():
    """`state_lost` in cell.json comes from the outcome of the warning step itself, so the
    comment names exactly the cells whose job printed the lost-save error.

    Mutations: drop `LOST_SAVE_OUTCOME` from `Compose cell summary`'s env; map it to
    `steps.save-state.outcome`."""
    env = step_by("apply-cell", name="Compose cell summary")["env"]
    assert env.get("LOST_SAVE_OUTCOME") == "${{ steps.lost-save.outcome }}"
