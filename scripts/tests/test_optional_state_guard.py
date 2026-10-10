"""Guards how each cell finds, restores and saves local state.

The state path is what `scripts/state-path` read from the stack's `tofu init` record, never a
caller input. An empty path means a backend other than `local` owns state, so every
`actions/state` step is skipped: a restore that still ran would fail the cell, because no cache
entry exists. `actions/cell-init` locates and restores state for every cell; only apply-cell
saves, through `cell-init`'s outputs. The cell's call step is pinned whole in
test_provider_cache.py.

Whole parsed steps compared to hand-written constants, never substrings: an inverted operator, a
dropped `always()` or a condition moved into a comment must fail these guards.
"""

from _loader import local_action, step_by

_INIT_ACTION = "cell-init"

#: Inside `terramate run`, as init is, so the record is read with the TF_DATA_DIR and
#: TF_WORKSPACE OpenTofu saw. A bare `python3` would read `.terraform` from the repository root.
_LOCATE = {
    "name": "Locate state",
    "id": "locate-state",
    "shell": "bash",
    "env": {"STACK": "${{ inputs.stack }}"},
    "run": (
        "terramate run --disable-safeguards=git-out-of-sync --no-recursive -C "
        '"$STACK" -- python3 "$GITHUB_ACTION_PATH/../../scripts/state-path"'
    ),
}

#: No status function: the implied success() skips the restore after a failed init or locate,
#: which would otherwise restore state for a stack init could not set up.
_RESTORE = {
    "name": "Restore state",
    "id": "restore-state",
    "if": "${{ steps.locate-state.outputs.path != '' }}",
    "uses": local_action("state"),
    "with": {
        "stack-slug": "${{ inputs.slug }}",
        "env": "${{ inputs.env }}",
        "mode": "restore",
        "path": "${{ steps.locate-state.outputs.path }}",
    },
}

#: always(), so a failed or cancelled apply's partial state persists; gated on the restore's
#: success, so state never restored is never saved, and a remote backend's skipped restore skips
#: the save too.
_SAVE = {
    "name": "Save state",
    "if": "${{ always() && steps.init.outputs.restore-outcome == 'success' }}",
    "uses": local_action("state"),
    "with": {
        "stack-slug": "${{ steps.ids.outputs.slug }}",
        "env": "${{ inputs.env }}",
        "mode": "save",
        "path": "${{ steps.init.outputs.state-path }}",
    },
}


def test_locate_state_reads_the_init_record_inside_terramate_run():
    """A one-line `run:` fails the step on a non-zero init, so the default `success()` condition
    skips it; the absent `if:` is part of the whole step. Mutation: run
    `python3 .../scripts/state-path` bare, outside `terramate run`."""
    assert step_by(_INIT_ACTION, name="Locate state") == _LOCATE


def test_restore_state_uses_the_located_path():
    """Mutations: `if: always()` on the restore; `stack-slug: ${{ inputs.stack }}`."""
    assert step_by(_INIT_ACTION, name="Restore state") == _RESTORE


def test_apply_cell_saves_state_to_the_located_path():
    """Mutations: drop `always()` from the `if`; save with `mode: restore`."""
    assert step_by("apply-cell", name="Save state") == _SAVE
