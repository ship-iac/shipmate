"""A "Re-run failed jobs" of an apply re-runs a failed wave without its `lock` job, so each apply
cell checks, read-only, that its pull request still holds the cell's lock. The read uses the wave
job's GITHUB_TOKEN at `contents: read`. The merge deploy passes no pull request and skips it.

Whole parsed values against hand-written constants: the step, the wave jobs' permissions and
the pull request each wave passes in.
"""

from _loader import step_by, workflow_jobs

WAVES = [f"wave{n}" for n in range(8)]


def test_the_apply_cell_verifies_its_lock_only_given_a_pull_request():
    """Mutations: drop the `if:` (the deploy's cells refuse on an empty pull request); invert
    it to `== ''`; read the token from `secrets.GITHUB_TOKEN`; run `cell-lock marks`."""
    assert step_by("apply-cell", name="Verify the pull request holds the cell lock") == {
        "name": "Verify the pull request holds the cell lock",
        "id": "cell-lock",
        "if": "${{ inputs.pr-number != '' }}",
        "shell": "bash",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
            "SHIPMATE_ENV": "${{ inputs.env }}",
            "STACK": "${{ inputs.stack }}",
        },
        "run": 'exec python3 "$GITHUB_ACTION_PATH/../../scripts/cell-lock" verify',
    }


def test_every_wave_passes_its_pull_request_and_can_read_the_lock():
    """Mutations: drop `pr-number` from the wave steps' apply-cell call; drop `contents: read`
    from the wave permissions (the lock read 403s and every pre-merge cell refuses)."""
    jobs = workflow_jobs("apply-env-level.yml")
    found = {}
    for wave in WAVES:
        (cell,) = [s for s in jobs[wave]["steps"] if str(s.get("uses", "")).endswith("apply-cell")]
        found[wave] = (jobs[wave]["permissions"], cell["with"].get("pr-number"))
    expected = (
        {"contents": "read", "actions": "read", "id-token": "write"},
        "${{ inputs.pr_number }}",
    )
    assert found == dict.fromkeys(WAVES, expected)
