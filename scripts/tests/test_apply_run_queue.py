"""`apply.yml` checks the dispatch, then calls `apply-run.yml`, which queues per pull request.

Threat model: accidental regression in the call boundary -- an input or secret not forwarded,
the group keyed on another value, the queue turned into a cancel, a `concurrency:` moved onto a
job of `apply.yml`, which would hold the dispatch check or the reply inside the queue. Each is
compared whole against a hand-written constant.
"""

from _loader import workflow_jobs, workflow_yaml

#: The whole top-level `concurrency:` of `apply-run.yml`. `guard` refuses a non-canonical
#: `pr_number` before the call, so the key needs no fallback.
APPLY_RUN_CONCURRENCY = {
    "group": "shipmate-apply-${{ inputs.pr_number }}",
    "cancel-in-progress": False,
    "queue": "max",
}

#: The whole `run` job of `apply.yml`. `permissions` is the union `apply-run.yml`'s jobs request;
#: `with` forwards every input; `secrets` names each one `apply-run.yml` declares.
RUN_JOB = {
    "needs": ["guard"],
    "if": "${{ !cancelled() && needs.guard.result == 'success' }}",
    "uses": "./.github/workflows/apply-run.yml",
    "permissions": {
        "contents": "read",
        "checks": "read",
        "actions": "read",
        "pull-requests": "read",
        "id-token": "write",
    },
    "with": {
        "environment": "${{ inputs.environment }}",
        "ref": "${{ inputs.ref }}",
        "pr_number": "${{ inputs.pr_number }}",
        "runs_on": "${{ inputs.runs_on }}",
        "auto": "${{ inputs.auto }}",
    },
    "secrets": {
        "SHIPMATE_APP_PRIVATE_KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
        "SHIPMATE_ENCRYPTION_KEY": "${{ secrets.SHIPMATE_ENCRYPTION_KEY }}",
        "SHIPMATE_SECRETS": "${{ secrets.SHIPMATE_SECRETS }}",
    },
}


def test_apply_run_queues_one_run_per_pull_request():
    """Mutations: drop `queue: max` -- red; key the group on `inputs.ref` -- red."""
    assert workflow_yaml("apply-run.yml").get("concurrency") == APPLY_RUN_CONCURRENCY


def test_no_apply_yml_job_sits_in_a_concurrency_group():
    """`guard` must refuse and `outcome` must answer while a run waits in the queue, so neither
    joins a group; a group on `run` would queue the call a second time.

    Mutation: give `run` the group `shipmate-apply-${{ inputs.pr_number }}` -- red."""
    grouped = {j for j, job in workflow_jobs("apply.yml").items() if "concurrency" in job}
    assert grouped == set()
    assert "concurrency" not in workflow_yaml("apply.yml")


def test_the_run_job_is_this_whole_job():
    """Mutations: drop `auto` from `with` -- red; drop `environment` from `with` -- red; drop
    `SHIPMATE_SECRETS` from `secrets` -- red; set `if:` to `${{ always() }}` -- red."""
    assert workflow_jobs("apply.yml")["run"] == RUN_JOB
