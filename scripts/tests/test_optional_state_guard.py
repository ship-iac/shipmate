"""Guards how each cell finds, restores and saves local state.

The state path is what `scripts/state-path` read from the stack's `tofu init` record, never a
caller input. An empty path means a backend other than `local` owns state, so every
`actions/state` step is skipped: a restore that still ran would fail the cell, because no cache
entry exists. drift-cell and plan-cell only restore.

Whole parsed steps compared to hand-written constants, never substrings: an inverted operator, a
dropped `always()` or a condition moved into a comment must fail these guards.
"""

import pytest
from _loader import action_steps, action_yaml, local_action

_CELLS = ["apply-cell", "drift-cell", "plan-cell"]

_STACK_ENV = {"STACK": "${{ inputs.stack }}"}

_INIT_RUN = (
    "terramate run --disable-safeguards=git-out-of-sync --no-recursive -C "
    '"$STACK" -- tofu init -input=false -reconfigure'
)

#: Inside `terramate run`, as init is, so the record is read with the TF_DATA_DIR and
#: TF_WORKSPACE OpenTofu saw. A bare `python3` would read `.terraform` from the repository root.
_LOCATE = {
    "name": "Locate state",
    "id": "locate-state",
    "shell": "bash",
    "env": _STACK_ENV,
    "run": (
        "terramate run --disable-safeguards=git-out-of-sync --no-recursive -C "
        '"$STACK" -- python3 "$GITHUB_ACTION_PATH/../../scripts/state-path"'
    ),
}

_STATE_WITH = {
    "stack-slug": "${{ steps.ids.outputs.slug }}",
    "env": "${{ inputs.env }}",
    "path": "${{ steps.locate-state.outputs.path }}",
}

_RESTORE = {
    "name": "Restore state",
    "id": "restore-state",
    "if": "${{ steps.locate-state.outputs.path != '' }}",
    "uses": local_action("state"),
    "with": {**_STATE_WITH, "mode": "restore"},
}

#: always(), so a failed or cancelled apply's partial state persists; gated on the restore's
#: success, so state never restored is never saved, and a remote backend's skipped restore skips
#: the save too.
_SAVE = {
    "name": "Save state",
    "if": "${{ always() && steps.restore-state.outcome == 'success' }}",
    "uses": local_action("state"),
    "with": {**_STATE_WITH, "mode": "save"},
}


def _step(action, name):
    matches = [s for s in action_steps(action) if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step in {action}, got {len(matches)}"
    return matches[0]


@pytest.mark.parametrize("cell", _CELLS)
def test_no_cell_takes_a_state_path_input(cell):
    assert "state-path" not in action_yaml(cell)["inputs"]


@pytest.mark.parametrize("cell", _CELLS)
def test_locate_state_reads_the_init_record_inside_terramate_run(cell):
    assert _step(cell, "Locate state") == _LOCATE


@pytest.mark.parametrize("cell", _CELLS)
def test_restore_state_uses_the_located_path(cell):
    assert _step(cell, "Restore state") == _RESTORE


def test_apply_cell_saves_state_to_the_located_path():
    assert _step("apply-cell", "Save state") == _SAVE


@pytest.mark.parametrize("cell", _CELLS)
def test_a_failed_init_skips_locate_and_restore(cell):
    """A one-line `run:` fails the step on a non-zero init, so the default `success()` condition
    skips what follows. An `always()` or `failure()` on either would locate or restore state for
    a stack init could not set up."""
    init = _step(cell, "Initialize the stack")
    assert init["run"] == _INIT_RUN
    assert "if" not in init
    assert {name: _step(cell, name).get("if") for name in ("Locate state", "Restore state")} == {
        "Locate state": None,
        "Restore state": "${{ steps.locate-state.outputs.path != '' }}",
    }
