"""Every cell action step that needs the stack reads it as `STACK`, the name the cell summary
writers read. A second name for the same `inputs.stack` is how cell.json once carried the stack
twice, under two keys a reader could tell apart.

Asserted on the parsed step `env:` mappings, so a comment naming a variable satisfies nothing.
"""

import pytest
from _loader import action_steps

CELLS = ("apply-cell", "drift-cell", "plan-cell", "unlock-cell")


@pytest.mark.parametrize("action", CELLS)
def test_inputs_stack_is_mapped_only_as_stack(action):
    """Mutation: map `STACK_PATH: ${{ inputs.stack }}` beside `STACK` in a drift-cell step."""
    steps = action_steps(action)
    assert steps, f"{action} parsed to no steps"
    names = {
        key
        for step in steps
        for key, value in (step.get("env") or {}).items()
        if value == "${{ inputs.stack }}"
    }
    assert names == {"STACK"}
