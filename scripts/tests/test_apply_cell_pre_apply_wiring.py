"""Each pre-merge apply cell runs the pre-apply check, read-only and on every attempt: its pull
request still holds the cell's lock and is open at the applied head, and, under the auto form,
the plan run detect bound is still the head's newest autoplan and the cell's newest plan record,
and the base tip is the one detect read. A wave can wait hours on an environment approval, and
"Re-run failed jobs" re-runs a failed wave without its `detect` and `lock` jobs. The check is the
wave job's first step, before the cloud credentials; the merge deploy passes no pull request and
skips it. Its failure skips every step but apply-cell, which runs only to refuse, so the cell
reports a blocked reason: the check's own refusal. The reads use the wave job's GITHUB_TOKEN.

Whole parsed values against hand-written constants: the check's step, the wave steps that read
its outcome, the wave jobs' permissions, apply-cell's refusing step and the callers'
permissions.
"""

from _loader import action_yaml, step_by, workflow_jobs

WAVES = [f"wave{n}" for n in range(8)]
LEVELS = [f"envlevel{n}" for n in range(4)]

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
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
    },
    "run": 'exec python3 "$GITHUB_ACTION_PATH/../../scripts/cell-lock" verify',
}

WAVE_VERIFY = {
    "name": "Pre-apply check",
    "id": "pre-apply",
    "if": "${{ inputs.pr_number != '' }}",
    "uses": "$/actions/pre-apply",
    "with": {
        "pr-number": "${{ inputs.pr_number }}",
        "env": "${{ matrix.environment }}",
        "stack": "${{ matrix.stack }}",
        "head-sha": "${{ inputs.head_sha }}",
        "auto": "${{ inputs.auto }}",
        "provenance-run": "${{ inputs.provenance_run }}",
        "base-sha": "${{ inputs.base_sha }}",
        "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    },
}


def test_pre_apply_runs_the_check_with_the_job_token():
    """Mutations: read the token from `secrets.GITHUB_TOKEN`; run `cell-lock marks`; drop
    `SHIPMATE_AUTO` (every auto cell refuses as a malformed mode); drop the step `id` (the
    action's `refusal` output reads empty)."""
    assert step_by("pre-apply", name="Pre-apply check") == VERIFY_STEP


def test_every_wave_checks_first_and_refuses_through_apply_cell():
    """Mutations: drop the `if:` (the deploy's cells refuse on an empty pull request); add
    `&& github.run_attempt > 1` (first attempts skip the check); invert to
    `inputs.pr_number == ''`; move the step after the credentials step; pass
    `inputs.provenance_run` as `base-sha`; drop apply-cell's `if:` (a failed check skips
    apply-cell and the cell reports no blocked reason); drop its `!cancelled() &&`; pass
    `steps.pre-apply.conclusion`; drop the refusal input (the blocked reason names no cause);
    drop `contents: read`, `pull-requests: read` or `checks: read` from the wave permissions
    (a read 403s and every pre-merge cell refuses)."""
    jobs = workflow_jobs("apply-env-level.yml")
    found = {}
    for wave in WAVES:
        steps = jobs[wave]["steps"]
        (cell,) = [s for s in steps if str(s.get("uses", "")).endswith("apply-cell")]
        found[wave] = (
            jobs[wave]["permissions"],
            steps[0],
            cell.get("if"),
            {k: v for k, v in cell["with"].items() if k.startswith("pre-apply")},
        )
    expected = (
        {
            "contents": "read",
            "actions": "read",
            "checks": "read",
            "pull-requests": "read",
            "id-token": "write",
        },
        WAVE_VERIFY,
        "${{ !cancelled() && (success() || steps.pre-apply.outcome == 'failure') }}",
        {
            "pre-apply-outcome": "${{ steps.pre-apply.outcome }}",
            "pre-apply-refusal": "${{ steps.pre-apply.outputs.refusal }}",
        },
    )
    assert found == dict.fromkeys(WAVES, expected)


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


def test_every_level_call_grants_what_the_wave_requests():
    """A reusable workflow cannot exceed its caller's permissions, so a level missing one the
    wave requests fails at load. The levels' `with:` blocks, the deploy's passing no pull
    request among them, are pinned whole by `test_no_env_level_call_passes_a_cap`. Mutation:
    drop `pull-requests: read` from `apply.yml`'s, then `deploy.yml`'s, level permissions."""
    found = {
        (workflow, lvl): workflow_jobs(workflow)[lvl]["permissions"]
        for workflow in ("apply.yml", "deploy.yml")
        for lvl in LEVELS
    }
    assert found == dict.fromkeys(
        [(w, lvl) for w in ("apply.yml", "deploy.yml") for lvl in LEVELS],
        {
            "contents": "read",
            "checks": "read",
            "actions": "read",
            "pull-requests": "read",
            "id-token": "write",
        },
    )
