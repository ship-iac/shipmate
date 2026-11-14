"""The claim's wiring: which runs claim their comment, what a claim gates, and the reaction an
unlock writes. Every job, step and `if:` is compared whole against a hand-written constant; the
expressions are never evaluated here, so each scenario a docstring names is pinned by the clause
that decides it.

Threat model: accidental regression -- a clause dropped or inverted, a `needs` entry lost (its
outputs then read empty), a grant widened, the claim moved after work or onto an autoplan.
"""

from _loader import action_yaml, workflow_jobs

#: Admits a run whose claim answered 201, and one whose claim skipped (an autoplan, the replan,
#: an `auto` apply). A failed claim (`result == 'failure'`, `state` empty) and a `taken` one
#: gate closed.
CLAIMED = "(needs.claim.outputs.state == 'owned' || needs.claim.result == 'skipped')"
CLAIM_IF = "${{ github.event_name == 'workflow_dispatch' && github.event.inputs.comment_id != '' }}"
ENGINE_ENVIRONMENT = {"name": "shipmate-engine", "deployment": False}


def _claim_job(with_, needs=None):
    job = {
        "if": CLAIM_IF,
        "runs-on": "ubuntu-slim",
        "environment": ENGINE_ENVIRONMENT,
        "permissions": {},
        "outputs": {"state": "${{ steps.claim.outputs.state }}"},
        "steps": [{"id": "claim", "uses": "$/actions/claim", "with": with_}],
    }
    return {"needs": needs, **job} if needs else job


def test_the_plan_claim_job_is_this_whole_job():
    """An autoplan (`pull_request_target`) and the replan (a dispatch with no `comment_id`)
    skip it; a commented plan claims before `facts` reads anything. Mutations: drop the
    `comment_id` clause (the replan would fail its claim); `||` for `&&` (an autoplan would
    claim); drop the `outputs` line (every commented plan gates closed); `permissions:
    { pull-requests: write }`; drop `environment`; drop `owner-job` (a full re-run of the
    owning run would plan nothing while its plan artifacts are gone)."""
    assert workflow_jobs("plan.yml")["claim"] == _claim_job(
        {
            "verb": "plan",
            "owner-job": "facts",
            "pr-number": "${{ github.event.inputs.pr_number }}",
            "comment-id": "${{ github.event.inputs.comment_id }}",
            "app-id": "${{ vars.SHIPMATE_APP_ID }}",
            "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
        }
    )


def test_plan_facts_runs_only_for_an_owned_or_skipped_claim():
    """`facts` is the root of every plan job, so gating it gates the run: a `taken` claim skips
    `facts`, which skips `detect`, `plan` and `summary`. `!cancelled()`, so a skipped claim does
    not skip it. Mutations: drop `!cancelled() &&` (an autoplan's skipped claim skips the
    autoplan); drop the claim clause (a second run of one comment plans again); drop `claim`
    from `needs` (its outputs read empty)."""
    facts = workflow_jobs("plan.yml")["facts"]
    assert (facts["needs"], facts["if"]) == (["claim"], f"${{{{ !cancelled() && {CLAIMED} }}}}")


def test_the_apply_claim_job_is_this_whole_job():
    """After `guard`, whose refusal (a non-bot actor, an `apply` with no `comment_id`) skips it,
    so a refused dispatch writes no reaction. An `auto` dispatch carries no `comment_id` and
    skips it. Mutations: drop `needs: [guard]` (a refused dispatch claims); drop the
    `comment_id` clause; pass `github.event.inputs.pr_number` (unvalidated) as `pr-number`;
    drop `environment` from `with:`."""
    assert workflow_jobs("apply.yml")["claim"] == _claim_job(
        {
            "verb": "apply",
            "environment": "${{ inputs.environment }}",
            "pr-number": "${{ inputs.pr_number }}",
            "comment-id": "${{ github.event.inputs.comment_id }}",
            "app-id": "${{ vars.SHIPMATE_APP_ID }}",
            "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
        },
        needs=["guard"],
    )


def test_the_claim_action_mints_exactly_these_permissions_and_runs_the_script():
    """`actions: read` lists a re-run plan's earlier attempts (`owner-job`). Mutations: raise
    `permission-actions` to `write`; drop it; read the slug from an input; drop
    `SHIPMATE_OWNER_JOB`; drop `COMMENT_ID`; read `GH_TOKEN` from `github.token` (the reaction
    would not be the App's, and the actor check reads the App's slug)."""
    steps = action_yaml("claim")["runs"]["steps"]
    assert [s.get("name") for s in steps] == [
        "Verify the App key arrived",
        "Mint App installation token",
        "Claim the command's comment",
    ]
    mint = steps[1]["with"]
    assert {k: v for k, v in mint.items() if k.startswith("permission-")} == {
        "permission-pull-requests": "write",
        "permission-actions": "read",
    }
    assert steps[2] == {
        "name": "Claim the command's comment",
        "id": "claim",
        "shell": "bash",
        "env": {
            "GH_TOKEN": "${{ steps.token.outputs.token }}",
            "COMMENT_ID": "${{ inputs.comment-id }}",
            "PR_NUMBER": "${{ inputs.pr-number }}",
            "SHIPMATE_APP_SLUG": "${{ steps.token.outputs.app-slug }}",
            "SHIPMATE_OWNER_JOB": "${{ inputs.owner-job }}",
            "SHIPMATE_REPLY_VERB": "${{ inputs.verb }}",
            "SHIPMATE_REPLY_ENV": "${{ inputs.environment }}",
        },
        "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/claim"',
    }
    assert action_yaml("claim")["outputs"]["state"]["value"] == "${{ steps.claim.outputs.state }}"


def test_the_unlock_guard_marks_the_command_started_after_it_passes():
    """Display only: the rocket gates nothing and is written with the job token, from a job that
    checks nothing out. After the actor check, so a refused dispatch writes none; the script
    writes none for a malformed id (test_claim.py). Mutations: move the reaction step first;
    `permissions: { pull-requests: write, contents: read }`; pass `inputs.ref` as the id."""
    guard = workflow_jobs("unlock.yml")["guard"]
    assert guard["permissions"] == {"pull-requests": "write"}
    assert guard["steps"][1:] == [
        {
            "uses": "$/actions/unlock-rocket",
            "with": {"comment-id": "${{ github.event.inputs.comment_id }}"},
        }
    ]
    assert guard["steps"][0]["name"] == "Reject dispatches that did not come from a bot actor"
    assert action_yaml("unlock-rocket")["runs"]["steps"] == [
        {
            "name": "Mark the command started",
            "shell": "bash",
            "env": {"GH_TOKEN": "${{ github.token }}", "COMMENT_ID": "${{ inputs.comment-id }}"},
            "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/claim" --display',
        }
    ]
