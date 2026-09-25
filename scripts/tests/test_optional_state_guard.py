"""Guards how each cell finds, restores and saves local state.

The state path is what `scripts/state-path` read from the stack's `tofu init` record, never a
caller input. An empty path means a backend other than `local` owns state, so every
`actions/state` step is skipped: a restore that still ran would fail the cell, because no cache
entry exists. drift-cell and plan-cell only restore.

Whole parsed steps compared to hand-written constants, never substrings: an inverted operator, a
dropped `always()` or a condition moved into a comment must fail these guards.
"""

import pytest
import yaml
from _loader import ACTIONS, WORKFLOWS, action_steps, local_action

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


def _with_keys(where, node):
    for job_id, job in (node.get("jobs") or {}).items():
        yield f"{where} jobs.{job_id}", job.get("with") or {}
        for i, step in enumerate(job.get("steps") or []):
            yield f"{where} jobs.{job_id}.steps[{i}]", step.get("with") or {}
    for i, step in enumerate((node.get("runs") or {}).get("steps") or []):
        yield f"{where} runs.steps[{i}]", step.get("with") or {}


def test_no_workflow_or_action_carries_a_state_path_setting():
    """The state path comes from the init record alone, so no caller can name one. Parsed keys,
    not text: the three cells legitimately run the helper `scripts/state-path`.

    Mutations: a `state-path:` key back in `apply-env-level.yml` `wave3`'s cell `with:`, and
    `state_suffix` re-declared under `plan.yml`'s `workflow_call.inputs`.
    """
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        on = spec.get("on", spec.get(True)) or {}
        call = on.get("workflow_call") if isinstance(on, dict) else None
        if "state_suffix" in ((call or {}).get("inputs") or {}):
            found.append(f"{path.name} workflow_call.inputs.state_suffix")
        found += [
            f"{where}.with.{key}"
            for where, keys in _with_keys(path.name, spec)
            for key in ("state_suffix", "state-path")
            if key in keys
        ]
    for path in sorted(ACTIONS.glob("*/action.yml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        where = path.parent.name
        if "state-path" in (spec.get("inputs") or {}):
            found.append(f"{where} inputs.state-path")
        found += [
            f"{w}.with.{key}"
            for w, keys in _with_keys(where, spec)
            for key in ("state_suffix", "state-path")
            if key in keys
        ]
    assert found == []


@pytest.mark.parametrize("cell", _CELLS)
def test_locate_state_reads_the_init_record_inside_terramate_run(cell):
    """Mutation: run `python3 .../scripts/state-path` bare, outside `terramate run`."""
    assert _step(cell, "Locate state") == _LOCATE


@pytest.mark.parametrize("cell", _CELLS)
def test_restore_state_uses_the_located_path(cell):
    """Mutation: restore `path: ${{ inputs.state-path }}`, or `env: ${{ inputs.stack }}`."""
    assert _step(cell, "Restore state") == _RESTORE


def test_apply_cell_saves_state_to_the_located_path():
    """Mutation: drop `always()` from the `if`, or save with `mode: restore`."""
    assert _step("apply-cell", "Save state") == _SAVE


@pytest.mark.parametrize("cell", _CELLS)
def test_a_failed_init_skips_locate_and_restore(cell):
    """A one-line `run:` fails the step on a non-zero init, so the default `success()` condition
    skips what follows. An `always()` or `failure()` on either would locate or restore state for
    a stack init could not set up. Mutation: `if: always()` on `Locate state`."""
    init = _step(cell, "Initialize the stack")
    assert init["run"] == _INIT_RUN
    assert "if" not in init
    assert {name: _step(cell, name).get("if") for name in ("Locate state", "Restore state")} == {
        "Locate state": None,
        "Restore state": "${{ steps.locate-state.outputs.path != '' }}",
    }
