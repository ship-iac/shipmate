"""Guards the optional-state contract: an empty state-path means a remote
backend owns state, so BOTH actions/state steps must be skipped -- a restore
that still runs would fail the cell (no artifact exists), and a save that
still runs would upload nothing meaningful and mask the skip.

Asserts whole parsed `if:` expressions, never substrings: an inverted
operator or a condition moved into a comment must fail these guards.
"""

import pytest
from _loader import action_steps, action_yaml

# drift-cell joins this list in the next commit (Task 2) — every commit must
# leave the suite green, so this file only asserts what is already shipped.
OPTIONAL_STATE_ACTIONS = ["apply-cell"]


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
