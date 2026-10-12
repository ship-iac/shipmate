"""The gate-writing jobs are serialized per head, and an evicted plan summary leaves a hold.

Threat model: accidental interleaving of engine jobs on one head -- a pre-merge apply's gate
refresh, a same-head re-plan's summary, a summary re-run, a post-merge deploy. Each `summary` job
runs in the group `shipmate-gate-<head sha>` with `cancel-in-progress: false`: a job-level group
in a called workflow is shared across runs and across called workflows, and a third arrival
cancels the pending one rather than the running one. The `github.run_id` fallback keeps an empty
head SHA (a failed `detect` or `facts`) in a group of its own instead of one every such run
shares.

A cancelled plan `summary` never publishes its cells, so plan.yml's `gate-hold` writes a hold
naming the recovery. It runs outside the group: a hold only closes the gate.
"""

import pytest
from _loader import workflow_jobs

#: Hand-written per workflow: each names its head SHA differently, and a hyphen/underscore slip
#: keys an empty value, which the fallback turns into a group of one.
EXPECTED = {
    "plan.yml": {
        "group": "shipmate-gate-${{ needs.facts.outputs.head-sha || github.run_id }}",
        "cancel-in-progress": False,
    },
    "apply.yml": {
        "group": "shipmate-gate-${{ inputs.ref || github.run_id }}",
        "cancel-in-progress": False,
    },
    "deploy.yml": {
        "group": "shipmate-gate-${{ needs.detect.outputs.head_sha || github.run_id }}",
        "cancel-in-progress": False,
    },
}


@pytest.mark.parametrize("workflow", sorted(EXPECTED))
def test_every_gate_writing_summary_is_serialized_per_head(workflow):
    """Mutations: delete `concurrency` from any one summary; `cancel-in-progress: true`; drop
    the `|| github.run_id` fallback."""
    assert workflow_jobs(workflow)["summary"].get("concurrency") == EXPECTED[workflow]


HOLD_DESCRIPTION = (
    "re-plan this pull request (comment shipmate plan or push): its plan summary was cancelled "
    "before it published"
)
#: The whole job, hand-written.
EXPECTED_GATE_HOLD = {
    "needs": ["facts", "summary"],
    "if": "${{ !cancelled() && needs.summary.result == 'cancelled' }}",
    "runs-on": "ubuntu-slim",
    "environment": "shipmate-engine",
    "permissions": {},
    "steps": [
        {
            "uses": "$/actions/gate-refresh",
            "with": {
                "head-sha": "${{ needs.facts.outputs.head-sha }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
                "hold-description": HOLD_DESCRIPTION,
            },
        }
    ],
}


def test_an_evicted_plan_summary_leaves_a_hold():
    """Mutations: `if: ${{ always() && ... }}`; drop `!cancelled() &&`; drop `hold-description`
    (a refresh greens the head instead)."""
    assert workflow_jobs("plan.yml")["gate-hold"] == EXPECTED_GATE_HOLD


def test_the_hold_fits_the_statuses_api():
    """The statuses API keeps 140 characters. Mutation: lengthen `HOLD_DESCRIPTION` past 140."""
    assert len(HOLD_DESCRIPTION) <= 140
