"""plan-cell restores flavor state through its `cell-init` call, on the same terms as drift-cell.

The engine's reusable plan workflow carries no slug step and no `actions/state` call: the cell
computes the slug and its `cell-init` call restores state at the path it located after init. The
call runs BEFORE the plan -- state restored after `tofu plan` is state the plan never read, which
is a silent wrong plan rather than an error -- and AFTER `Stack slug`, because a forward
`steps.<id>` reference renders empty and an empty slug builds a `restore-keys:` prefix matching
no real key, so the cell plans against empty state and reports it clean.

`test_optional_state_guard.py` owns the whole restore step inside `cell-init`, and
`test_provider_cache.py` the whole call step with the slug it passes; what stays here is the
slug-before-call order.

Assertions are on the parsed action.yml. A substring form is satisfied by a comment naming
`actions/cell-init`.
"""

from _loader import action_steps, local_action

_CELL_INIT = local_action("cell-init")


def _step_index(steps, predicate, what):
    hits = [i for i, s in enumerate(steps) if predicate(s)]
    assert len(hits) == 1, f"expected exactly one {what} step, got {len(hits)}"
    return hits[0]


def test_the_slug_is_computed_before_the_restore_and_the_restore_before_the_plan():
    """Mutations: move `Stack slug` below the `cell-init` call; move the call below `Plan`."""
    steps = action_steps("plan-cell")
    slug = _step_index(steps, lambda s: s.get("id") == "ids", "id: ids")
    init = _step_index(steps, lambda s: s.get("uses") == _CELL_INIT, "cell-init call")
    plan = _step_index(steps, lambda s: s.get("name") == "Plan", "name: Plan")
    assert slug < init < plan, f"slug at {slug}, cell-init at {init}, Plan at {plan}"
