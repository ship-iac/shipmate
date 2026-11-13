"""The gate-writing jobs queue per head, and a cancelled plan summary leaves a hold.

Threat model: accidental interleaving of engine jobs on one head -- a pre-merge apply's gate
refresh, a same-head re-plan's summary, a summary re-run, a post-merge deploy. Each `summary` job
runs in the group `shipmate-gate-<head sha>` with `cancel-in-progress: false` and `queue: max`: a
job-level group in a called workflow is shared across runs and across called workflows, up to 100
arrivals wait in it, and an arrival at a full queue is cancelled. GitHub rejects `queue: max`
beside `cancel-in-progress: true`. The `github.run_id` fallback keeps an empty head SHA (a failed
`detect` or `facts`) in a group of its own instead of one every such run shares.

A plan `summary` cancelled while its run was not never publishes its cells, so plan.yml's
`gate-hold` writes a hold naming the recovery. It runs outside the group: a hold only closes the
gate.

actionlint 1.7.12 rejects the `queue` key, so `.github/actionlint.yaml` ignores that one message.
The ignore must not hide another unknown `concurrency` key.
"""

import re

import pytest
from _loader import ENGINE, ENGINE_ENVIRONMENT, workflow_jobs, workflow_yaml

#: Hand-written per workflow: each names its head SHA differently, and a hyphen/underscore slip
#: keys an empty value, which the fallback turns into a group of one.
EXPECTED = {
    "plan.yml": {
        "group": "shipmate-gate-${{ needs.facts.outputs.head-sha || github.run_id }}",
        "cancel-in-progress": False,
        "queue": "max",
    },
    "apply.yml": {
        "group": "shipmate-gate-${{ inputs.ref || github.run_id }}",
        "cancel-in-progress": False,
        "queue": "max",
    },
    "deploy.yml": {
        "group": "shipmate-gate-${{ needs.detect.outputs.head_sha || github.run_id }}",
        "cancel-in-progress": False,
        "queue": "max",
    },
}


@pytest.mark.parametrize("workflow", sorted(EXPECTED))
def test_every_gate_writing_summary_is_serialized_per_head(workflow):
    """Mutations: delete `concurrency` from any one summary; drop `queue: max` from any one;
    `queue: single`; `cancel-in-progress: true` beside `queue: max`; drop the
    `|| github.run_id` fallback."""
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
    "environment": ENGINE_ENVIRONMENT,
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


def test_a_cancelled_plan_summary_leaves_a_hold():
    """Mutations: `if: ${{ always() && ... }}`; drop `!cancelled() &&`; drop `hold-description`
    (a refresh greens the head instead)."""
    assert workflow_jobs("plan.yml")["gate-hold"] == EXPECTED_GATE_HOLD


def test_the_hold_fits_the_statuses_api():
    """The statuses API keeps 140 characters. Mutation: lengthen `HOLD_DESCRIPTION` past 140."""
    assert len(HOLD_DESCRIPTION) <= 140


QUEUE_IGNORE = 'unexpected key "queue" for "concurrency" section'
#: Hand-written: the `.github/workflows/*.yml` ignore list, whole.
EXPECTED_WORKFLOW_IGNORES = [
    r'specifying action "\$/actions/[a-z-]+" in invalid format because ref is missing',
    'property "workflow_repository" is not defined in object type',
    'unexpected key "cache-mode" for "workflow" section',
    QUEUE_IGNORE,
]
#: actionlint 1.7.12's messages for `queue` and for a second unknown key in one `concurrency`
#: block, copied from a run of the release binary.
ACTIONLINT_QUEUE_MESSAGE = (
    'unexpected key "queue" for "concurrency" section. expected one of "cancel-in-progress", '
    '"group" [syntax-check]'
)
ACTIONLINT_BOGUS_MESSAGE = (
    'unexpected key "bogus" for "concurrency" section. expected one of "cancel-in-progress", '
    '"group" [syntax-check]'
)


def _workflow_ignores():
    config = workflow_yaml(ENGINE / ".github" / "actionlint.yaml")
    return config["paths"][".github/workflows/*.yml"]["ignore"]


def test_actionlint_ignores_exactly_the_known_messages():
    """Mutations: drop the `queue` entry; widen it to
    `'unexpected key "[a-z-]+" for "concurrency" section'`."""
    assert _workflow_ignores() == EXPECTED_WORKFLOW_IGNORES


def test_the_queue_ignore_hides_queue_and_no_other_key():
    """Python `re.search` stands in for actionlint's Go regexp; the entry uses no syntax where
    they differ. Mutations: drop the `queue` entry; widen it to
    `'unexpected key "[a-z-]+" for "concurrency" section'`."""
    entries = [e for e in _workflow_ignores() if re.search(e, ACTIONLINT_QUEUE_MESSAGE)]
    assert len(entries) == 1
    assert not re.search(entries[0], ACTIONLINT_BOGUS_MESSAGE)
