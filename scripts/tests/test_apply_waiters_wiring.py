"""Guards `actions/apply-waiters` and its one step in `apply-run.yml`, in the `waiters` job.

The step only tells waiting runs which run they wait behind. `waiters` runs beside the apply after
a bound `review`, no job needs it, and its step may not fail it. Threat model is accidental
regression -- `continue-on-error` or `timeout-minutes` dropped, the step moved back into
`review`, a second waiters step added, the job run after a refused review or given a token, a job
made to need it, the display mint widened to `actions: write`, or a job's own token widened to
carry the reply -- not a hostile edit to a SHA-pinned engine file. Each value is compared whole
against a hand-written constant.
"""

from _loader import ENGINE_ENVIRONMENT, action_yaml, workflow_jobs
from test_apply_run_check_wiring import REVIEW_STEP

_WITH = {
    "pr-number": "${{ inputs.pr_number }}",
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}

#: The one waiters step, whole.
WAITERS_STEP = {
    "name": "Tell the apply runs waiting behind this one",
    "uses": "$/actions/apply-waiters",
    "continue-on-error": True,
    "timeout-minutes": 1,
    "with": _WITH,
}

#: The `waiters` job minus its steps, whole: only after a bound review of the run that owns the
#: command, no token of its own.
WAITERS_JOB = {
    "needs": ["review"],
    "if": "${{ needs.review.result == 'success' && needs.review.outputs.admitted == 'true' }}",
    "runs-on": "ubuntu-slim",
    "timeout-minutes": 3,
    "continue-on-error": True,
    "environment": ENGINE_ENVIRONMENT,
    "permissions": {},
}

MINT_WITH = {
    "client-id": "${{ inputs.app-id }}",
    "private-key": "${{ inputs.private-key }}",
    "permission-actions": "read",
    "permission-pull-requests": "write",
}

RUN_ENV = {
    "GH_TOKEN": "${{ steps.token.outputs.token }}",
    "PR_NUMBER": "${{ inputs.pr-number }}",
    "SHIPMATE_APP_SLUG": "${{ steps.token.outputs.app-slug }}",
    "SHIPMATE_ACTOR": "${{ github.actor }}",
    "SHIPMATE_TRIGGERING_ACTOR": "${{ github.triggering_actor }}",
}


def _steps(job):
    return workflow_jobs("apply-run.yml")[job]["steps"]


def test_the_action_mints_actions_read_and_pull_requests_write():
    """Mutations: `permission-actions: write` -- red; add `repositories:` -- red."""
    steps = action_yaml("apply-waiters")["runs"]["steps"]
    assert steps[1]["uses"].startswith("actions/create-github-app-token@")
    assert steps[1]["with"] == MINT_WITH


def test_the_action_runs_the_waiters_mode_with_both_actors():
    """Mutations: drop `SHIPMATE_TRIGGERING_ACTOR` -- red; drop the `waiters` argument -- red."""
    step = action_yaml("apply-waiters")["runs"]["steps"][2]
    assert step["env"] == RUN_ENV
    assert step["run"] == 'python3 "$GITHUB_ACTION_PATH/../../scripts/apply-queue" waiters'


def test_the_waiters_job_steps_are_whole():
    """The action verifies the App key itself, inside this display-only step. Mutations: drop
    `continue-on-error` -- red; drop `timeout-minutes` -- red; add a job-level key check before
    it -- red."""
    assert _steps("waiters") == [WAITERS_STEP]


def test_the_waiters_job_is_this_whole_mapping():
    """Mutations: `if: ${{ always() }}` -- red; drop the `admitted` clause -- red;
    `permissions: { pull-requests: write }` -- red; drop `timeout-minutes` -- red; drop
    `continue-on-error` -- red."""
    job = workflow_jobs("apply-run.yml")["waiters"]
    assert {k: v for k, v in job.items() if k != "steps"} == WAITERS_JOB


def test_review_ends_with_the_run_check():
    """The run check step, whole (`test_apply_run_check_wiring.py`'s hand-written constant).
    Mutations: move the waiters step back to the end of `review` -- red; `timeout-minutes: 2`
    on the run check step -- red."""
    assert _steps("review")[-1] == REVIEW_STEP


def test_waiters_holds_the_only_waiters_step():
    """Mutations: move the waiters step back into `review` -- red; re-add it to `summary` --
    red."""
    found = [
        (name, step)
        for name, job in workflow_jobs("apply-run.yml").items()
        for step in job.get("steps", [])
        if "apply-waiters" in str(step.get("uses") or "")
    ]
    assert found == [("waiters", WAITERS_STEP)]


def test_no_job_needs_waiters():
    """Display only: a job needing it would wait on it, and `summary`'s `results` would read it.
    Mutation: add `waiters` to `summary`'s `needs` -- red."""
    needs = {
        name: job.get("needs") or []
        for name, job in workflow_jobs("apply-run.yml").items()
        if "waiters" in (job.get("needs") or [])
    }
    assert needs == {}


def test_review_and_summary_job_tokens_stay_as_they_were():
    """The reply rides the action's own mint. Mutation: add `pull-requests: write` to
    `summary` -- red."""
    jobs = workflow_jobs("apply-run.yml")
    assert jobs["review"]["permissions"] == {}
    assert jobs["summary"]["permissions"] == {"actions": "read"}
