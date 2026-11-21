"""Every listed engine workflow declares an empty workflow-level `permissions: {}` floor.

Without the floor, a job that loses its own `permissions:` block inherits everything the caller
granted, `id-token: write` included, instead of getting nothing.
"""

import pytest
from _loader import workflow_yaml

#: Every engine workflow whose empty workflow-level floor is pinned. Hand-written, never globbed:
#: a glob would pin whatever the directory holds.
_FLOORED = ["apply-env-level.yml", "comment-ops.yml", "drift.yml", "plan.yml", "unlock.yml"]


@pytest.mark.parametrize("name", _FLOORED)
def test_workflow_level_permissions_are_an_empty_floor(name):
    """Mutation: `permissions: { contents: read }` at workflow level in any listed file."""
    spec = workflow_yaml(name)
    assert spec.get("permissions") == {}, (
        f"{name} must declare a workflow-level `permissions: {{}}` floor "
        "-- without it a job that loses its own block inherits everything the "
        f"caller granted, id-token: write included; got {spec.get('permissions')!r}"
    )
