"""deploy.yml's `summary` job writes the post-merge gate through `actions/gate-refresh`.

The verdict over the env-level results is `gate-status deploy`, whose cases live in
`test_gate_status.py`. This guard pins the wiring that feeds it: a dropped `deploy-results` writes
a verdict over no env-level result, and dropping both inputs turns the step into a refresh, which
greens the merged head without reading a single deploy result.
"""

from _loader import ENGINE_ENVIRONMENT, workflow_jobs

#: The whole job, hand-written: a value read back from the workflow passes whatever it says.
EXPECTED_SUMMARY = {
    "needs": ["detect", "envlevel0", "envlevel1", "envlevel2", "envlevel3"],
    "if": "always()",
    "runs-on": "ubuntu-slim",
    "concurrency": {
        "group": "shipmate-gate-${{ needs.detect.outputs.head_sha || github.run_id }}",
        "cancel-in-progress": False,
        "queue": "max",
    },
    "environment": ENGINE_ENVIRONMENT,
    "permissions": {},
    "steps": [
        {
            "name": "Complete gate on the merged PR head SHA",
            "uses": "$/actions/gate-refresh",
            "with": {
                "head-sha": "${{ needs.detect.outputs.head_sha }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
                "detect-result": "${{ needs.detect.result }}",
                "deploy-results": "${{ join(needs.*.result, ',') }}",
            },
        },
        {
            "name": "Release the merged pull request's cell locks",
            "if": "${{ !cancelled() && needs.detect.outputs.pr_number != '' }}",
            "uses": "$/actions/lock-release",
            "with": {
                "pr-number": "${{ needs.detect.outputs.pr_number }}",
                "head-sha": "${{ needs.detect.outputs.head_sha }}",
                "merged": "true",
                "release-pending": "${{ needs.detect.outputs.refused_locked == 'true' }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        },
    ],
}

#: The whole `replan` job, hand-written.
EXPECTED_REPLAN = {
    "needs": ["detect", "summary"],
    "if": "${{ !cancelled() }}",
    "runs-on": "${{ inputs.runs_on }}",
    "timeout-minutes": 20,
    "continue-on-error": True,
    "environment": ENGINE_ENVIRONMENT,
    "permissions": {},
    "steps": [
        {
            "name": "Re-plan the open pull requests this push may put behind",
            "uses": "$/actions/base-push-plan",
            "with": {
                "push": "${{ needs.detect.outputs.push }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        },
    ],
}


def test_the_deploy_gate_step_hands_gate_refresh_every_result():
    """Mutations: drop `deploy-results` or `detect-result`; add back a mint step;
    `head_sha` -> `head-sha`; drop `queue: max`."""
    assert workflow_jobs("deploy.yml")["summary"] == EXPECTED_SUMMARY


def test_the_re_plan_job_never_fails_the_deploy_and_grants_nothing():
    """Display-only: it runs after `summary` whatever `detect` and `summary` concluded, and
    `continue-on-error` keeps its failure off the run's conclusion. A permission the caller's
    `deploy` job does not grant would startup-fail every consumer, and the App mint carries what
    it needs. Mutations: drop `continue-on-error`; `if: always()`; drop `summary` from `needs`;
    add `actions: write` to `permissions`; `runs-on: ubuntu-slim`; `timeout-minutes: 5`."""
    assert workflow_jobs("deploy.yml")["replan"] == EXPECTED_REPLAN
