"""A closed pull request's cell locks are released by `release.yml` and by `deploy.yml`'s `summary`.

Threat model: accidental regression in the wiring. A dropped `environment` leaves the mint with no
App key; a checkout puts branch content beside the key; a `success()` `if:` keeps every lock of a
deploy whose level failed, the cells that applied included; a release ahead of the gate write
delays the gate behind it; a dropped `SHIPMATE_*` variable refuses every release. Each site is
compared whole against a hand-written constant.
"""

from _loader import ENGINE_ENVIRONMENT, action_steps, action_yaml, workflow_jobs, workflow_yaml

_APP = {
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}

RELEASE_WORKFLOW = {
    "name": "release",
    "on": {
        "workflow_call": {
            "inputs": {
                "runs_on": {"required": False, "default": "ubuntu-26.04-arm", "type": "string"}
            },
            "secrets": {"SHIPMATE_APP_PRIVATE_KEY": {"required": False}},
        }
    },
    "permissions": {},
    "jobs": {
        "release": {
            "runs-on": "${{ inputs.runs_on }}",
            "environment": ENGINE_ENVIRONMENT,
            "permissions": {},
            "steps": [
                {
                    "uses": "$/actions/lock-release",
                    "with": {
                        "pr-number": "${{ github.event.pull_request.number }}",
                        "head-sha": "${{ github.event.pull_request.head.sha }}",
                        "merged": "${{ github.event.pull_request.merged }}",
                        "closed-at": "${{ github.event.pull_request.closed_at }}",
                        **_APP,
                    },
                }
            ],
        }
    },
}


def test_release_workflow_releases_the_closed_pull_requests_locks():
    """Mutations: remove `environment`; add an `actions/checkout` step; pass `merged` as
    `'true'`; drop the `SHIPMATE_APP_PRIVATE_KEY` secret."""
    assert workflow_yaml("release.yml") == RELEASE_WORKFLOW


SUMMARY_STEP_NAMES = [
    "Complete gate on the merged PR head SHA",
    "Release the merged pull request's cell locks",
]

DEPLOY_RELEASE_STEP = {
    "name": "Release the merged pull request's cell locks",
    "if": "${{ !cancelled() && needs.detect.outputs.pr_number != '' }}",
    "uses": "$/actions/lock-release",
    "with": {
        "pr-number": "${{ needs.detect.outputs.pr_number }}",
        "head-sha": "${{ needs.detect.outputs.head_sha }}",
        "merged": "true",
        **_APP,
    },
}


def test_deploy_summary_releases_last_after_any_level_result():
    """Mutations: move the release step first; its `if:` to `success()`."""
    steps = workflow_jobs("deploy.yml")["summary"]["steps"]
    assert [s["name"] for s in steps] == SUMMARY_STEP_NAMES
    assert steps[-1] == DEPLOY_RELEASE_STEP


def test_deploy_detect_hands_the_pull_request_number_to_the_summary():
    """Mutations: drop `pr_number` from `detect`'s job outputs; from the action's outputs."""
    assert workflow_jobs("deploy.yml")["detect"]["outputs"]["pr_number"] == (
        "${{ steps.d.outputs.pr_number }}"
    )
    assert action_yaml("deploy-detect")["outputs"]["pr_number"]["value"] == (
        "${{ steps.detect.outputs.pr_number }}"
    )


LOCK_RELEASE_STEPS = [
    {
        "name": "Verify the App key arrived",
        "uses": "$/actions/verify-app-key",
        "with": {"app-id": "${{ inputs.app-id }}", "private-key": "${{ inputs.private-key }}"},
    },
    {
        "name": "Mint App installation token",
        "id": "token",
        "uses": "actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510",
        "with": {
            "client-id": "${{ inputs.app-id }}",
            "private-key": "${{ inputs.private-key }}",
            "owner": "${{ github.repository_owner }}",
            "repositories": "${{ github.event.repository.name }}",
            "permission-contents": "write",
            "permission-checks": "read",
            "permission-pull-requests": "read",
        },
    },
    {
        "name": "Release the cell locks",
        "shell": "bash",
        "env": {
            "GH_TOKEN": "${{ steps.token.outputs.token }}",
            "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
            "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
            "SHIPMATE_MERGED": "${{ inputs.merged }}",
            "SHIPMATE_CLOSED_AT": "${{ inputs.closed-at }}",
            "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        },
        "run": 'exec python3 "$GITHUB_ACTION_PATH/../../scripts/cell-lock" release',
    },
]


def test_lock_release_runs_the_release_command_with_its_inputs():
    """Mutations: drop `SHIPMATE_CLOSED_AT`; drop `permission-checks`; run `acquire`."""
    assert action_steps("lock-release") == LOCK_RELEASE_STEPS
