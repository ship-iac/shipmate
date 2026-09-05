"""plan-cell restores flavor state itself, on the same terms as drift-cell.

The engine's reusable plan workflow carries no slug step and no `actions/state` call: it hands
the cell one path and the cell does both. Two properties are pinned. The restore is CONDITIONAL
on a non-empty `state-path` -- a remote-backend consumer passes "" and an unconditional restore
would fail its every cell on a missing artifact. And the restore runs BEFORE the plan -- state
restored after `tofu plan` is state the plan never read, which is a silent wrong plan rather
than an error.

Assertions are on the parsed action.yml. A substring form is satisfied by a comment naming
`actions/state`, and by a restore step whose `if:` was inverted.
"""

from _loader import action_steps, action_yaml

_STATE = "ship-iac/shipmate/actions/state"


def _step_index(steps, predicate):
    return next((i for i, s in enumerate(steps) if predicate(s)), None)


def test_the_state_path_input_matches_drift_cells():
    """Both cells take the same input on the same terms; a `required: true` here would break
    every remote-backend consumer at run time with no way to opt out."""
    plan = (action_yaml("plan-cell")["inputs"] or {})["state-path"]
    drift = (action_yaml("drift-cell")["inputs"] or {})["state-path"]
    assert plan["required"] is False
    assert plan["default"] == ""
    assert plan["required"] == drift["required"] and plan["default"] == drift["default"]


def test_the_restore_is_conditional_on_a_non_empty_state_path():
    """Mutation: delete the `if:` from the restore step, or change it to `!= 'x'`."""
    steps = action_steps("plan-cell")
    i = _step_index(steps, lambda s: _STATE in str(s.get("uses", "")))
    assert i is not None, "plan-cell no longer calls actions/state"
    assert steps[i]["if"] == "${{ inputs.state-path != '' }}"


def test_state_is_restored_before_the_plan_runs():
    """Mutation: move the restore step below the `Plan` step."""
    steps = action_steps("plan-cell")
    restore = _step_index(steps, lambda s: _STATE in str(s.get("uses", "")))
    plan = _step_index(steps, lambda s: s.get("name") == "Plan")
    assert restore is not None and plan is not None
    assert restore < plan, f"restore at {restore}, Plan at {plan}"


def test_the_restore_call_passes_the_whole_expected_with_block():
    """Hand-written whole-value comparison: a derived expectation passes whatever the file says."""
    steps = action_steps("plan-cell")
    i = _step_index(steps, lambda s: _STATE in str(s.get("uses", "")))
    assert i is not None, "plan-cell no longer calls actions/state"
    assert steps[i]["with"] == {
        "stack-slug": "${{ steps.ids.outputs.slug }}",
        "env": "${{ inputs.env }}",
        "mode": "restore",
        "path": "${{ inputs.state-path }}",
    }
