"""deploy.yml's `summary` job writes the post-merge gate through `actions/gate-refresh`.

The verdict over the env-level results is `gate-status deploy`, whose cases live in
`test_gate_status.py`. This guard pins the wiring that feeds it: a dropped `deploy-results` turns
the step into a refresh, which greens the merged head without reading a single deploy result.
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
                "deploy-results": "${{ join(needs.*.result, ',') }}",
            },
        }
    ],
}


def test_the_deploy_gate_step_hands_gate_refresh_every_result():
    """Mutations: drop `deploy-results`; add back a mint step; `head_sha` -> `head-sha`."""
    assert workflow_jobs("deploy.yml")["summary"] == EXPECTED_SUMMARY
