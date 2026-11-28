"""Each pre-merge apply cell runs the pre-apply check, read-only and on every attempt: its pull
request still holds the cell's lock and is open at the applied head, the base tip is the one
detect read, the cell's newest plan record still names the stage row's plan run and digest,
and, under the auto form, the plan run detect bound is still the head's newest autoplan. A stage
can wait hours on an environment approval, and "Re-run failed jobs" re-runs a failed stage
without its `detect` and `lock` jobs. The check follows only the App-key refusal in `cell.yml`'s
`apply`, before the cloud credentials. The merge deploy passes the merged pull request (empty on
a push) with `deploy` true and the job's check run id, and the check then refuses only a lock
another pull request holds or, on a re-run, a cell an earlier attempt may have applied without
saving its state. Its failure skips every step but apply-cell, which runs only to refuse, so the
cell reports a blocked reason: the check's own refusal. The reads use the `apply` job's
GITHUB_TOKEN.

Whole parsed values against hand-written constants: the check's step, the cell steps that read
its outcome, the cell's and `stage.yml`'s `cell` job's permissions, apply-cell's refusing step and
the callers' permissions.
"""

from _loader import action_yaml, checkouts, secrets_refs, step_by, workflow_jobs

VERIFY_STEP = {
    "name": "Pre-apply check",
    "id": "verify",
    "shell": "bash",
    "env": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
        "SHIPMATE_ENV": "${{ inputs.env }}",
        "STACK": "${{ inputs.stack }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_AUTO": "${{ inputs.auto }}",
        "SHIPMATE_PROVENANCE_RUN": "${{ inputs.provenance-run }}",
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_PLAN_RUN_ID": "${{ inputs.plan-run-id }}",
        "SHIPMATE_PLAN_SHA256": "${{ inputs.plan-sha256 }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_DEPLOY": "${{ inputs.deploy }}",
        "JOB_ID": "${{ inputs.job-id }}",
    },
    "run": 'exec python3 "$GITHUB_ACTION_PATH/../../scripts/cell-lock" verify',
}

WAVE_VERIFY = {
    "name": "Pre-apply check",
    "id": "pre-apply",
    "if": "${{ inputs.pr_number != '' || inputs.deploy }}",
    "uses": "$/actions/pre-apply",
    "with": {
        "pr-number": "${{ inputs.pr_number }}",
        "env": "${{ inputs.environment }}",
        "stack": "${{ inputs.stack }}",
        "head-sha": "${{ inputs.head_sha }}",
        "auto": "${{ inputs.auto }}",
        "provenance-run": "${{ inputs.provenance_run }}",
        "base-sha": "${{ inputs.base_sha }}",
        "plan-run-id": "${{ fromJSON(inputs.cell).plan_run_id }}",
        "plan-sha256": "${{ fromJSON(inputs.cell).plan_sha256 }}",
        "app-id": "${{ vars.SHIPMATE_APP_ID }}",
        "deploy": "${{ inputs.deploy }}",
        "job-id": "${{ job.check_run_id }}",
    },
}


def test_pre_apply_runs_the_check_with_the_job_token():
    """Mutations: read the token from `secrets.GITHUB_TOKEN`; run `cell-lock marks`; drop
    `SHIPMATE_AUTO` (every auto cell refuses as a malformed mode); drop the step `id` (the
    action's `refusal` output reads empty); drop `SHIPMATE_DEPLOY` (every deploy cell runs the
    pull request check, and the merged, closed pull request refuses); drop
    `SHIPMATE_PLAN_SHA256` (every pre-merge cell refuses as missing its digest); drop `JOB_ID`
    (every re-run deploy cell refuses as not listed)."""
    assert step_by("pre-apply", name="Pre-apply check") == VERIFY_STEP


PERMISSIONS = {
    "contents": "read",
    "actions": "read",
    "checks": "read",
    "pull-requests": "read",
    "id-token": "write",
}


def test_the_cell_checks_first_and_refuses_through_apply_cell():
    """Mutations: drop `|| inputs.deploy` from the `if:` (deploy waves run no check); add
    `&& github.run_attempt > 1` (first attempts skip the check); invert to
    `inputs.pr_number == ''`; drop `deploy:` from `with:` (deploy cells run the pull request
    check); move the step after the credentials step; pass
    `inputs.provenance_run` as `base-sha`; drop `plan-run-id` from `with:` (every pre-merge
    cell refuses as missing its plan run); drop `job-id` from `with:` (every re-run deploy cell
    refuses as not listed); drop apply-cell's `if:` (a failed check skips
    apply-cell and the cell reports no blocked reason); drop its `!cancelled() &&`; pass
    `steps.pre-apply.conclusion`; drop the refusal input (the blocked reason names no cause);
    drop `contents: read`, `pull-requests: read` or `checks: read` from `cell.yml`'s `apply`
    permissions, or from `stage.yml`'s `cell`, which caps it (a read 403s and every pre-merge
    cell refuses)."""
    apply = workflow_jobs("cell.yml")["apply"]
    steps = apply["steps"]
    (cell,) = [s for s in steps if str(s.get("uses", "")).endswith("apply-cell")]
    found = (
        apply["permissions"],
        steps[1],
        cell.get("if"),
        {k: v for k, v in cell["with"].items() if k.startswith("pre-apply")},
    )
    expected = (
        PERMISSIONS,
        WAVE_VERIFY,
        "${{ !cancelled() && (success() || steps.pre-apply.outcome == 'failure') }}",
        {
            "pre-apply-outcome": "${{ steps.pre-apply.outcome }}",
            "pre-apply-refusal": "${{ steps.pre-apply.outputs.refusal }}",
        },
    )
    assert found == expected
    assert workflow_jobs("stage.yml")["cell"]["permissions"] == PERMISSIONS


def test_the_cell_checks_out_the_head_without_persisting_the_token():
    """`save` checks nothing out (`test_cell_workflow.py`). Mutation: delete
    `persist-credentials` from `apply`'s checkout."""
    found = [s.get("with") for s in checkouts(workflow_jobs("cell.yml")["apply"]["steps"])]
    assert found == [{"ref": "${{ inputs.head_sha }}", "persist-credentials": False}]


def test_the_cell_references_exactly_these_secrets():
    """`apply` runs pull request code, so every secret it references reaches that code; `save`
    references only the App key. Which steps name the App key is
    `test_cells_hold_no_app_key.py`'s. Mutation: an `env: { K: ${{ secrets.SHIPMATE_VARS }} }`
    on the apply-cell step."""
    jobs = workflow_jobs("cell.yml")
    assert {j: secrets_refs(jobs[j]) for j in ("apply", "save")} == {
        "apply": [
            "secrets.SHIPMATE_APP_PRIVATE_KEY",
            "secrets.SHIPMATE_ENCRYPTION_KEY",
            "secrets.SHIPMATE_SECRETS",
        ],
        "save": ["secrets.SHIPMATE_APP_PRIVATE_KEY"],
    }


def test_pre_apply_exposes_the_checks_refusal():
    """Mutation: point `refusal` at `steps.pre-apply.outputs.refusal` (no such inner step)."""
    assert action_yaml("pre-apply")["outputs"]["refusal"]["value"] == (
        "${{ steps.verify.outputs.refusal }}"
    )


def test_apply_cell_refuses_unless_the_pre_apply_check_passed_or_was_skipped():
    """Mutations: refuse only on `failure` (an empty, dropped input proceeds); drop
    `exit 1`; let `cancelled` through."""
    assert step_by("apply-cell", id="pre-apply") == {
        "name": "Refuse a cell whose pre-apply check did not pass",
        "id": "pre-apply",
        "shell": "bash",
        "env": {
            "ENV": "${{ inputs.env }}",
            "STACK": "${{ inputs.stack }}",
            "OUTCOME": "${{ inputs.pre-apply-outcome }}",
        },
        "run": (
            'if [ "$OUTCOME" != success ] && [ "$OUTCOME" != skipped ]; then\n'
            '  echo "::error::apply aborted for $ENV/$STACK: the pre-apply check did not pass '
            "(outcome: '$OUTCOME'); the job's Pre-apply check step names why\"\n"
            "  exit 1\n"
            "fi\n"
        ),
    }


def test_apply_cell_hands_the_refusal_to_the_cell_summary():
    """Mutation: pass `inputs.pre-apply-outcome` as the refusal."""
    env = step_by("apply-cell", name="Compose cell summary")["env"]
    assert {k: env[k] for k in ("PRE_APPLY_OUTCOME", "PRE_APPLY_REFUSAL")} == {
        "PRE_APPLY_OUTCOME": "${{ steps.pre-apply.outcome }}",
        "PRE_APPLY_REFUSAL": "${{ inputs.pre-apply-refusal }}",
    }


def test_every_stage_call_grants_what_the_cell_requests():
    """A reusable workflow cannot exceed its caller's permissions, so a call missing one the
    cell requests fails at load. The `waves` calls' `with:` blocks, the deploy's passing the
    merged pull request with `deploy` true among them, are pinned whole by
    `test_no_stage_call_passes_a_cap`. Mutation: drop `pull-requests: read` from
    `apply-run.yml`'s, then `deploy.yml`'s, `waves` permissions; from `waves.yml`'s `stage`."""
    calls = [("apply-run.yml", "waves"), ("deploy.yml", "waves"), ("waves.yml", "stage")]
    found = {(w, j): workflow_jobs(w)[j]["permissions"] for w, j in calls}
    assert found == dict.fromkeys(
        calls,
        {
            "contents": "read",
            "checks": "read",
            "actions": "read",
            "pull-requests": "read",
            "id-token": "write",
        },
    )
