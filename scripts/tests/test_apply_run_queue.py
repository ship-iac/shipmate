"""`apply.yml` checks the dispatch, then calls `apply-run.yml`, which queues per pull request.

Threat model: accidental regression in the call boundary -- an input or secret not forwarded,
the group keyed on another value, the queue turned into a cancel, a `concurrency:` moved onto a
job of `apply.yml`, which would hold the dispatch check or the reply inside the queue. Each is
compared whole against a hand-written constant.
"""

import re

from _loader import ENGINE, action_yaml, workflow_jobs, workflow_yaml

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
    "needs": ["guard", "admit"],
    "if": (
        "${{ !cancelled() && needs.guard.result == 'success' && "
        "(needs.admit.outputs.admitted == 'true' || github.event.inputs.comment_id == '') }}"
    ),
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


#: The whole `workflow_call.outputs` of `apply-run.yml`. Each reads empty when the call was
#: cancelled while it waited in the queue, and a job's values read empty when it never ran.
APPLY_RUN_OUTPUTS = {
    "review_result": {"value": "${{ jobs.review.result }}"},
    "unbound": {"value": "${{ jobs.review.outputs.unbound }}"},
    "posted": {"value": "${{ jobs.summary.outputs.posted }}"},
    "summary_result": {"value": "${{ jobs.summary.result }}"},
    "run_cancelled": {
        "value": (
            "${{ jobs.review.result == 'cancelled' || jobs.detect.result == 'cancelled' || "
            "jobs.lock.result == 'cancelled' || jobs.envlevel0.result == 'cancelled' || "
            "jobs.envlevel1.result == 'cancelled' || jobs.envlevel2.result == 'cancelled' || "
            "jobs.envlevel3.result == 'cancelled' }}"
        )
    },
}

#: Hand-written: the `apply-run.yml` actionlint ignore list, whole.
APPLY_RUN_IGNORES = [
    r'property "result" is not defined in object type \{outputs: ',
    'unexpected key "cache-mode" for "job" section',
]
#: actionlint 1.7.12's message for `jobs.review.result` in a `workflow_call` output, and for a
#: misspelt property in the same place, copied from runs of the release binary.
ACTIONLINT_RESULT_MESSAGE = (
    'property "result" is not defined in object type {outputs: {decision: string; '
    "unbound: string}} [expression]"
)
ACTIONLINT_TYPO_MESSAGE = (
    'property "reslt" is not defined in object type {outputs: {decision: string; '
    "unbound: string}} [expression]"
)


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


def test_apply_run_exports_exactly_these_outputs():
    """Mutations: drop `jobs.lock.result == 'cancelled'` from `run_cancelled` -- red; read
    `posted` from `jobs.review` -- red."""
    assert workflow_yaml("apply-run.yml")["on"]["workflow_call"]["outputs"] == APPLY_RUN_OUTPUTS


def test_actionlint_ignores_only_result_and_cache_mode_in_apply_run():
    """Python `re.search` stands in for actionlint's Go regexp; the entries use no syntax where
    they differ. Mutations: widen the first entry to `'property "[a-z]+" is not defined'` -- red;
    drop the `cache-mode` entry -- red."""
    config = workflow_yaml(ENGINE / ".github" / "actionlint.yaml")
    ignores = config["paths"][".github/workflows/apply-run.yml"]["ignore"]
    assert ignores == APPLY_RUN_IGNORES
    assert re.search(ignores[0], ACTIONLINT_RESULT_MESSAGE)
    assert not re.search(ignores[0], ACTIONLINT_TYPO_MESSAGE)


def test_the_run_job_is_this_whole_job():
    """Mutations: drop `auto` from `with` -- red; drop `environment` from `with` -- red; drop
    `SHIPMATE_SECRETS` from `secrets` -- red; set `if:` to `${{ always() }}` -- red; drop
    the `admitted` clause, and a `taken` run of a comment applies a second time -- red; key it on
    `needs.admit.result == 'success'`, and a failed queue step stops the apply -- red; drop
    `|| github.event.inputs.comment_id == ''`, and an `auto` run depends on `admit`'s outputs,
    which a lost `admit` runner leaves empty -- red; drop `admit` from `needs`, and its outputs
    read empty -- red."""
    assert workflow_jobs("apply.yml")["run"] == RUN_JOB


#: The whole `admit` job of `apply.yml`. No job-token permission: its writes use the App token,
#: and only under the trust rule `scripts/apply-queue` applies. `admitted` is `true` or unset.
ADMIT_JOB = {
    "needs": ["guard"],
    "if": "${{ !cancelled() && needs.guard.result == 'success' }}",
    "runs-on": "ubuntu-slim",
    "environment": {"name": "shipmate-engine", "deployment": False},
    "permissions": {},
    "outputs": {"admitted": "${{ steps.admit.outputs.admitted }}"},
    "steps": [
        {
            "id": "claim",
            "if": (
                "${{ github.event_name == 'workflow_dispatch' && "
                "github.event.inputs.comment_id != '' }}"
            ),
            "uses": "$/actions/claim",
            "with": {
                "verb": "apply",
                "environment": "${{ inputs.environment }}",
                "pr-number": "${{ inputs.pr_number }}",
                "comment-id": "${{ github.event.inputs.comment_id }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        },
        {
            "id": "admit",
            "if": (
                "${{ steps.claim.outputs.state == 'owned' || "
                "github.event.inputs.comment_id == '' }}"
            ),
            "run": 'echo "admitted=true" >> "$GITHUB_OUTPUT"',
        },
        {
            "id": "queue",
            "if": "${{ steps.admit.outputs.admitted == 'true' }}",
            "uses": "$/actions/apply-queue",
            "with": {
                "pr-number": "${{ inputs.pr_number }}",
                "environment": "${{ inputs.environment }}",
                "auto": "${{ inputs.auto }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        },
    ],
}

#: The step of `actions/apply-queue` that runs the script: the trust inputs come from the
#: dispatch's own `github` context and the slug from the token mint.
QUEUE_STEP_ENV = {
    "GH_TOKEN": "${{ steps.token.outputs.token }}",
    "PR_NUMBER": "${{ inputs.pr-number }}",
    "SHIPMATE_ENV": "${{ inputs.environment }}",
    "SHIPMATE_AUTO": "${{ inputs.auto }}",
    "SHIPMATE_APP_SLUG": "${{ steps.token.outputs.app-slug }}",
    "SHIPMATE_ACTOR": "${{ github.actor }}",
    "SHIPMATE_TRIGGERING_ACTOR": "${{ github.triggering_actor }}",
}


def test_the_admit_job_is_this_whole_job():
    """A guard refusal skips `admit`; a `taken` or failed claim writes no `admitted`; an `auto`
    dispatch skips the claim step and is admitted by its empty `comment_id`. Mutations:
    `permissions: { actions: write }` -- red; drop `needs.guard.result == 'success' &&` from
    `admit.if`, and a guard refusal starts the claim, because `!cancelled()` drops the implicit
    success of `needs: guard` -- red; drop the admit step's `if:` (admitted unconditionally, so a
    `taken` claim applies) -- red; `!= 'owned'` in it -- red; drop its
    `|| github.event.inputs.comment_id == ''` (no `auto` run is admitted) -- red; drop the queue
    step's `if:` -- red; add `continue-on-error: true` to the queue step -- red; drop `auto`
    from its `with` -- red; drop the claim step's `comment_id` clause -- red; pass
    `github.event.inputs.pr_number` (unvalidated) as its `pr-number` -- red; drop `environment`
    from its `with` -- red."""
    assert workflow_jobs("apply.yml")["admit"] == ADMIT_JOB


def test_the_queue_action_mints_these_permissions_and_passes_both_actors():
    """Mutations: drop `permission-actions` -- red; read `SHIPMATE_TRIGGERING_ACTOR` from
    `github.actor` -- red."""
    steps = action_yaml("apply-queue")["runs"]["steps"]
    mint = steps[1]["with"]
    assert {k: v for k, v in mint.items() if k.startswith("permission-")} == {
        "permission-actions": "write",
        "permission-pull-requests": "write",
    }
    assert steps[2]["env"] == QUEUE_STEP_ENV
    assert steps[2]["run"] == 'python3 "$GITHUB_ACTION_PATH/../../scripts/apply-queue"'
