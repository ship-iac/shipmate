"""`cell.yml`, the called workflow that runs one wave cell, and the eight wave jobs that call it.

Threat model: an accidental edit that (a) lets the job that runs pull-request code write the
state entry, or the job that writes it run pull-request code, hold a secret or bind an
environment; (b) skips `save` after an `apply` that may have changed the state; (c) feeds
`save` another value than the `apply` job's state output; (d) lets one wave pass the cell
another row's environment or stack, which the concurrency group and the steps read. Whole parsed
values against hand-written constants.
"""

from _loader import local_action, workflow_jobs

WAVES = [f"wave{n}" for n in range(8)]

CELL_WITH = {
    "cell": "${{ toJSON(matrix) }}",
    "environment": "${{ matrix.environment }}",
    "stack": "${{ matrix.stack }}",
    "head_sha": "${{ inputs.head_sha }}",
    "runs_on": "${{ inputs.runs_on }}",
    "tools": "${{ inputs.tools }}",
    "pr_number": "${{ inputs.pr_number }}",
    "auto": "${{ inputs.auto }}",
    "provenance_run": "${{ inputs.provenance_run }}",
    "base_sha": "${{ inputs.base_sha }}",
    "deploy": "${{ inputs.deploy }}",
}
CELL_SECRETS = {
    "SHIPMATE_ENCRYPTION_KEY": "${{ secrets.SHIPMATE_ENCRYPTION_KEY }}",
    "SHIPMATE_SECRETS": "${{ secrets.SHIPMATE_SECRETS }}",
}
#: The keys of a calling wave job. GitHub refuses `runs-on` and `environment` on a job that calls
#: a workflow; a repeated `concurrency` group fails the call at once; a `cache-mode: read` there
#: fails `save` at startup.
WAVE_KEYS = {"needs", "if", "uses", "permissions", "strategy", "name", "with", "secrets"}

SAVE = {
    "needs": ["apply"],
    "if": "${{ always() }}",
    "runs-on": "${{ inputs.runs_on }}",
    "cache-mode": "write",
    "permissions": {"actions": "read"},
    "steps": [
        {
            "uses": local_action("state-save"),
            "with": {
                "job-id": "${{ job.check_run_id }}",
                "stack": "${{ inputs.stack }}",
                "environment": "${{ inputs.environment }}",
                "state": "${{ needs.apply.outputs.state }}",
            },
        }
    ],
}


def test_every_wave_calls_the_cell_with_one_block():
    """Mutation: `stack: ${{ matrix.environment }}` on `wave3`; add `cache-mode: read` to
    `wave0`."""
    jobs = workflow_jobs("apply-env-level.yml")
    found = {w: (set(jobs[w]), jobs[w]["uses"], jobs[w]["with"], jobs[w]["secrets"]) for w in WAVES}
    expected = (WAVE_KEYS, "./.github/workflows/cell.yml", CELL_WITH, CELL_SECRETS)
    assert found == dict.fromkeys(WAVES, expected)


def test_the_save_job_is_whole():
    """Mutations: `if: success()`; add `environment: ${{ fromJSON(inputs.cell).env_binding }}`;
    add an `actions/checkout` step; read `STATE` from `needs.apply.outputs.digest`."""
    assert workflow_jobs("cell.yml")["save"] == SAVE


def test_the_apply_job_outputs_its_cells_state():
    """Mutation: read `state` from `steps.handoff.outputs.state` (no such step in the job)."""
    apply = workflow_jobs("cell.yml")["apply"]
    (cell,) = [s for s in apply["steps"] if s.get("id") == "cell"]
    assert (apply["outputs"], cell["uses"]) == (
        {"state": "${{ steps.cell.outputs.state }}"},
        local_action("apply-cell"),
    )
