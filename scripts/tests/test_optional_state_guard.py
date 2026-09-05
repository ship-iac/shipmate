"""Guards the optional-state contract: an empty state-path means a remote backend owns state, so
every actions/state step must be skipped. A restore that still runs would fail the cell, because
no artifact exists; apply-cell's save that still runs would upload nothing meaningful and mask
the skip. drift-cell only restores.

Asserts whole parsed `if:` expressions, never substrings: an inverted operator or a condition
moved into a comment must fail these guards.
"""

import pytest
from _loader import action_steps, action_yaml

OPTIONAL_STATE_ACTIONS = ["apply-cell", "drift-cell"]


def _step(action, name):
    matches = [s for s in action_steps(action) if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step in {action}, got {len(matches)}"
    return matches[0]


@pytest.mark.parametrize("action", OPTIONAL_STATE_ACTIONS)
def test_state_path_input_is_optional_with_empty_default(action):
    spec = action_yaml(action)["inputs"]["state-path"]
    assert spec.get("required") is False, f"{action}: state-path must be required: false"
    assert spec.get("default") == "", f"{action}: state-path default must be ''"


def test_apply_cell_restore_is_skipped_when_state_path_empty():
    step = _step("apply-cell", "Restore state")
    assert step.get("if") == "${{ inputs.state-path != '' }}"


def test_apply_cell_save_is_skipped_when_state_path_empty():
    step = _step("apply-cell", "Save state")
    assert step.get("if") == (
        "${{ always() && inputs.state-path != '' && steps.restore-state.outcome == 'success' }}"
    )


def test_drift_cell_restore_is_skipped_when_state_path_empty():
    step = _step("drift-cell", "Restore state")
    assert step.get("if") == "${{ inputs.state-path != '' }}"
