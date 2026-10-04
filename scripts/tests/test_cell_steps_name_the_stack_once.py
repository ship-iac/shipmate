"""Every cell action step that needs the stack reads it as `STACK`, the name the cell summary
writers read. A second name for the same `inputs.stack` is how cell.json once carried the stack
twice, under two keys a reader could tell apart.

Asserted on the parsed step `env:` mappings, so a comment naming a variable satisfies nothing.
"""

import re

import pytest
from _loader import action_steps

CELLS = ("apply-cell", "drift-cell", "plan-cell", "unlock-cell")
INPUTS_STACK = "${{inputs.stack}}"
READS_STACK = re.compile(r"\$\{?STACK\b")


def _is_inputs_stack(value):
    return "".join(str(value).split()) == INPUTS_STACK


@pytest.mark.parametrize("action", CELLS)
def test_inputs_stack_is_mapped_only_as_stack(action):
    """Mutation: map `STACK_PATH: ${{inputs.stack}}` beside `STACK` in a drift-cell step."""
    steps = action_steps(action)
    assert steps, f"{action} parsed to no steps"
    names = {
        key
        for step in steps
        for key, value in (step.get("env") or {}).items()
        if _is_inputs_stack(value)
    }
    assert names == {"STACK"}


@pytest.mark.parametrize("action", CELLS)
def test_every_step_reading_stack_maps_it(action):
    """A step's `run` sees only its own `env:`, and under `set -u` an unmapped `$STACK` aborts.

    Mutation: delete `STACK: ${{ inputs.stack }}` from unlock-cell's "Report that no lock was
    released" step.
    """
    readers = [s for s in action_steps(action) if READS_STACK.search(s.get("run") or "")]
    assert readers, f"{action} has no step reading $STACK"
    unmapped = [
        s.get("name") or s.get("id")
        for s in readers
        if not _is_inputs_stack((s.get("env") or {}).get("STACK"))
    ]
    assert unmapped == []
