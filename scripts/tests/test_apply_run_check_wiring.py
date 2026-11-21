"""Guards `actions/apply-run-check` and its three call sites: `review` and `summary` in
`apply-run.yml`, `outcome` in `apply.yml`.

The check only shows a running apply on the pull request, and nothing reads it to decide, so no
site may fail its job. Threat model is accidental regression -- `continue-on-error` dropped, an
`if:` that skips the finish after a failed step or runs it before a result was posted, `start`
moved ahead of the review that binds `ref` to the head, the mint widened or narrowed -- not a
hostile edit to a SHA-pinned engine file. Each value is compared whole against a hand-written
constant.
"""

from _loader import action_yaml, workflow_jobs

USES = "$/actions/apply-run-check"
START = "Show the running apply on the pull request"
FINISH = "End the running apply check"


def _with(mode):
    return {
        "mode": mode,
        "head-sha": "${{ inputs.ref }}",
        "app-id": "${{ vars.SHIPMATE_APP_ID }}",
        "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
    }


INPUTS = {
    "mode": {"description": "`start` or `finish`.", "required": True},
    "head-sha": {
        "description": "The pull request head SHA the check is on.",
        "required": True,
    },
    "app-id": {
        "description": "shipmate App id (a repo/org variable, not a secret).",
        "required": True,
    },
    "private-key": {
        "description": "shipmate App private key (from the shipmate-engine environment secret).",
        "required": True,
    },
}

STEPS = [
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
            "permission-checks": "write",
            "permission-actions": "read",
        },
    },
    {
        "name": "Create or complete the apply run check",
        "shell": "bash",
        "env": {
            "GH_TOKEN": "${{ steps.token.outputs.token }}",
            "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
            "MODE": "${{ inputs.mode }}",
            "HEAD_SHA": "${{ inputs.head-sha }}",
        },
        "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/apply-run-check" "$MODE" "$HEAD_SHA"',
    },
]

#: `review`'s step, whole: after `rd`, with the default `if:`, so only a review that bound
#: `ref` to the head starts a check.
REVIEW_STEP = {
    "name": START,
    "uses": USES,
    "continue-on-error": True,
    "timeout-minutes": 1,
    "with": _with("start"),
}

#: `summary`'s step, whole: after the apply comment, and run after a failed gate refresh.
SUMMARY_STEP = {
    "name": FINISH,
    "if": "${{ always() && steps.result.outputs.posted == 'true' }}",
    "uses": USES,
    "continue-on-error": True,
    "timeout-minutes": 1,
    "with": _with("finish"),
}

#: `outcome`'s step, whole: after the no-result reply, only when it posted.
OUTCOME_STEP = {
    "name": FINISH,
    "if": "${{ always() && steps.reply.outputs.posted == 'true' }}",
    "uses": USES,
    "continue-on-error": True,
    "timeout-minutes": 1,
    "with": _with("finish"),
}


def _site(workflow, job):
    """(the step before, the one step that calls the action) in `workflow`'s `job`."""
    steps = workflow_jobs(workflow)[job]["steps"]
    found = [i for i, s in enumerate(steps) if s.get("uses") == USES]
    assert len(found) == 1, f"{workflow}:{job} calls {USES} {len(found)} times"
    assert found[0] > 0, f"{workflow}:{job} calls {USES} first"
    return steps[found[0] - 1], steps[found[0]]


def test_the_action_inputs_are_whole():
    """Mutation: drop `required: true` from `head-sha` -- red."""
    assert action_yaml("apply-run-check")["inputs"] == INPUTS


def test_the_action_steps_are_whole():
    """Mutations: drop `permission-checks: write`; drop `permission-actions: read`; add
    `permission-pull-requests: write`; drop `SHIPMATE_APP_ID` from the env -- each red."""
    assert action_yaml("apply-run-check")["runs"]["steps"] == STEPS


def test_review_starts_the_check_after_rd():
    """Mutations: drop `continue-on-error`; `mode: finish`; `head-sha: ${{ github.sha }}`;
    move the step before `rd` -- each red."""
    before, step = _site("apply-run.yml", "review")
    assert before["id"] == "rd"
    assert step == REVIEW_STEP


def test_summary_finishes_the_check_after_the_apply_comment():
    """Mutations: drop `continue-on-error`; drop `always() && ` from the `if:`; drop the
    `posted` clause; `head-sha: ${{ github.sha }}`; move the step before the apply comment --
    each red."""
    before, step = _site("apply-run.yml", "summary")
    assert before["id"] == "result"
    assert step == SUMMARY_STEP


def test_outcome_finishes_the_check_after_the_reply():
    """Mutations: drop `continue-on-error`; the `if:` as `${{ always() }}`; drop `always() && `;
    `head-sha: ${{ github.sha }}`; move the step before the reply -- each red."""
    before, step = _site("apply.yml", "outcome")
    assert before["id"] == "reply"
    assert before["uses"] == "$/actions/outcome-reply"
    assert step == OUTCOME_STEP
