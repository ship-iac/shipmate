"""Each pre-merge apply cell checks, read-only and on every attempt, that its pull request still
holds the cell's lock: a wave can wait hours on an environment approval, and "Re-run failed jobs"
re-runs a failed wave without its `lock` job. The check is the wave job's first step, before the
cloud credentials; the merge deploy passes no pull request and skips it. Its failure skips every
step but apply-cell, which runs only to refuse, so the cell reports a blocked reason. The read
uses the wave job's GITHUB_TOKEN at `contents: read`.

Whole parsed values against hand-written constants: the lock-verify step, the wave steps that
read its outcome, the wave jobs' permissions and apply-cell's refusing step.
"""

from _loader import step_by, workflow_jobs

WAVES = [f"wave{n}" for n in range(8)]

VERIFY_STEP = {
    "name": "Verify the pull request holds the cell lock",
    "shell": "bash",
    "env": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
        "SHIPMATE_ENV": "${{ inputs.env }}",
        "STACK": "${{ inputs.stack }}",
    },
    "run": 'exec python3 "$GITHUB_ACTION_PATH/../../scripts/cell-lock" verify',
}

WAVE_VERIFY = {
    "name": "Verify the pull request holds the cell lock",
    "id": "cell-lock",
    "if": "${{ inputs.pr_number != '' }}",
    "uses": "$/actions/lock-verify",
    "with": {
        "pr-number": "${{ inputs.pr_number }}",
        "env": "${{ matrix.environment }}",
        "stack": "${{ matrix.stack }}",
    },
}


def test_lock_verify_reads_the_lock_with_the_job_token():
    """Mutations: read the token from `secrets.GITHUB_TOKEN`; run `cell-lock marks`."""
    assert step_by("lock-verify", name="Verify the pull request holds the cell lock") == (
        VERIFY_STEP
    )


def test_every_wave_verifies_first_and_refuses_through_apply_cell():
    """Mutations: drop the `if:` (the deploy's cells refuse on an empty pull request); add
    `&& github.run_attempt > 1` (first attempts skip the check); invert to
    `inputs.pr_number == ''`; move the step after the credentials step; drop apply-cell's `if:`
    (a failed check skips apply-cell and the cell reports no blocked reason); drop its
    `!cancelled() &&`; pass `steps.cell-lock.conclusion`; drop `contents: read` from the wave
    permissions (the lock read 403s and every pre-merge cell refuses)."""
    jobs = workflow_jobs("apply-env-level.yml")
    found = {}
    for wave in WAVES:
        steps = jobs[wave]["steps"]
        (cell,) = [s for s in steps if str(s.get("uses", "")).endswith("apply-cell")]
        found[wave] = (
            jobs[wave]["permissions"],
            steps[0],
            cell.get("if"),
            cell["with"].get("cell-lock-outcome"),
        )
    expected = (
        {"contents": "read", "actions": "read", "id-token": "write"},
        WAVE_VERIFY,
        "${{ !cancelled() && (success() || steps.cell-lock.outcome == 'failure') }}",
        "${{ steps.cell-lock.outcome }}",
    )
    assert found == dict.fromkeys(WAVES, expected)


def test_apply_cell_refuses_unless_the_lock_check_passed_or_was_skipped():
    """Mutations: refuse only on `failure` (an empty, dropped input proceeds); drop
    `exit 1`; let `cancelled` through."""
    assert step_by("apply-cell", id="cell-lock") == {
        "name": "Refuse a cell whose lock check did not pass",
        "id": "cell-lock",
        "shell": "bash",
        "env": {
            "ENV": "${{ inputs.env }}",
            "STACK": "${{ inputs.stack }}",
            "OUTCOME": "${{ inputs.cell-lock-outcome }}",
        },
        "run": (
            'if [ "$OUTCOME" != success ] && [ "$OUTCOME" != skipped ]; then\n'
            '  echo "::error::apply aborted for $ENV/$STACK: the cell lock check did not pass '
            "(outcome: '$OUTCOME'); the job's lock check step names why\"\n"
            "  exit 1\n"
            "fi\n"
        ),
    }
