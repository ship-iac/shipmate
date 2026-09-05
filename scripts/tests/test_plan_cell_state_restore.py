"""plan-cell restores flavor state itself, on the same terms as drift-cell.

The engine's reusable plan workflow carries no slug step and no `actions/state` call: it hands
the cell one path and the cell does both. The restore runs BEFORE the plan -- state restored
after `tofu plan` is state the plan never read, which is a silent wrong plan rather than an
error -- and AFTER `Stack slug`, because a forward `steps.<id>` reference renders empty and an
empty slug builds a `restore-keys:` prefix matching no real key, so the cell plans against empty
state and reports it clean.

`test_optional_state_guard.py` owns the `state-path` input's terms and the skipped-when-empty
property, for all three cells against hand-written constants; what stays here is the wiring the
registry does not reach.

Assertions are on the parsed action.yml. A substring form is satisfied by a comment naming
`actions/state`, and by a restore step whose `if:` was inverted.
"""

from _loader import action_steps

_STATE = "ship-iac/shipmate/actions/state"


def _step_index(steps, predicate, what):
    hits = [i for i, s in enumerate(steps) if predicate(s)]
    assert len(hits) == 1, f"expected exactly one {what} step, got {len(hits)}"
    return hits[0]


def _restore_index(steps):
    return _step_index(steps, lambda s: _STATE in str(s.get("uses", "")), "actions/state")


def test_the_slug_is_computed_before_the_restore_and_the_restore_before_the_plan():
    """Mutations: move `Stack slug` below `Restore state`; move `Restore state` below `Plan`."""
    steps = action_steps("plan-cell")
    slug = _step_index(steps, lambda s: s.get("id") == "ids", "id: ids")
    restore = _restore_index(steps)
    plan = _step_index(steps, lambda s: s.get("name") == "Plan", "name: Plan")
    assert slug < restore < plan, f"slug at {slug}, restore at {restore}, Plan at {plan}"


def test_the_restore_call_passes_the_whole_expected_with_block():
    """Hand-written whole-value comparison: a derived expectation passes whatever the file says."""
    steps = action_steps("plan-cell")
    assert steps[_restore_index(steps)]["with"] == {
        "stack-slug": "${{ steps.ids.outputs.slug }}",
        "env": "${{ inputs.env }}",
        "mode": "restore",
        "path": "${{ inputs.state-path }}",
    }
