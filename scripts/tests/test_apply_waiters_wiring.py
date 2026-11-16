"""Guards `actions/apply-waiters` and its two steps in `apply-run.yml`, the last of `review` and
the last of `summary`.

Both steps only tell waiting runs which run they wait behind, so neither may fail its job: a red
`review` stops every apply, and `summary` has already written the gate and the apply comment.
Threat model is accidental regression -- `continue-on-error` or `timeout-minutes` dropped, a step
moved ahead of the work its job exists for, the display mint widened to `actions: write`, or a
job's own token widened to carry the reply -- not a hostile edit to a SHA-pinned engine file.
Each value is compared whole against a hand-written constant.
"""

from _loader import action_yaml, workflow_jobs

_WITH = {
    "pr-number": "${{ inputs.pr_number }}",
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}

#: `review`'s last step, whole: after `rd`, with the default `if:`, so a refused review posts
#: nothing.
REVIEW_STEP = {
    "name": "Tell the apply runs waiting behind this one",
    "uses": "$/actions/apply-waiters",
    "continue-on-error": True,
    "timeout-minutes": 1,
    "with": _WITH,
}

#: `summary`'s last step, whole: after the apply comment, and run after a failed gate refresh.
SUMMARY_STEP = {
    "name": "Tell the apply runs waiting behind this one",
    "if": "${{ !cancelled() }}",
    "uses": "$/actions/apply-waiters",
    "continue-on-error": True,
    "timeout-minutes": 1,
    "with": _WITH,
}

MINT_WITH = {
    "client-id": "${{ inputs.app-id }}",
    "private-key": "${{ inputs.private-key }}",
    "owner": "${{ github.repository_owner }}",
    "repositories": "${{ github.event.repository.name }}",
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
    """Mutations: `permission-actions: write` -- red; drop `repositories:` -- red."""
    steps = action_yaml("apply-waiters")["runs"]["steps"]
    assert steps[1]["uses"].startswith("actions/create-github-app-token@")
    assert steps[1]["with"] == MINT_WITH


def test_the_action_runs_the_waiters_mode_with_both_actors():
    """Mutations: drop `SHIPMATE_TRIGGERING_ACTOR` -- red; drop the `waiters` argument -- red."""
    step = action_yaml("apply-waiters")["runs"]["steps"][2]
    assert step["env"] == RUN_ENV
    assert step["run"] == 'python3 "$GITHUB_ACTION_PATH/../../scripts/apply-queue" waiters'


def test_review_ends_with_the_waiters_step_after_rd():
    """Mutations: drop `continue-on-error` -- red; drop `timeout-minutes` -- red; move the step
    before `rd` -- red; give it `if: always()` -- red."""
    steps = _steps("review")
    assert steps[-1] == REVIEW_STEP
    assert steps[-2]["id"] == "rd"


def test_summary_ends_with_the_waiters_step_after_the_apply_comment():
    """Mutations: drop `continue-on-error` -- red; drop `timeout-minutes` -- red; move the step
    before `Post apply result comment` -- red."""
    steps = _steps("summary")
    assert steps[-1] == SUMMARY_STEP
    assert steps[-2]["id"] == "result"


def test_review_and_summary_job_tokens_stay_as_they_were():
    """The reply rides the action's own mint. Mutation: add `pull-requests: write` to
    `summary` -- red."""
    jobs = workflow_jobs("apply-run.yml")
    assert jobs["review"]["permissions"] == {}
    assert jobs["summary"]["permissions"] == {"actions": "read"}
