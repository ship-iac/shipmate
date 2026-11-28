"""The plan run id reaches `apply-cell` per cell, never as a workflow input.

Each cell row carries its own `plan_run_id`, read from that cell's own apply
check, so no workflow needs to thread one id down for every cell in the run.
Two properties, because a rail can come back at either end: no workflow declares
the input, and `stage.yml`'s `cell` job hands `cell.yml` its whole matrix row, from which
`apply-cell` reads the value.
"""

from _loader import WORKFLOWS, workflow_yaml

#: Every `workflow_call`/`workflow_dispatch` input name in the engine, per file. Hand-written,
#: never read back from the files it guards: the property is that no entry says `plan_run_id`, and
#: a guard that derives the constant asserts whatever the files happen to say. A file with no
#: inputs is listed as `{}`, so a new workflow reaching for the retired rail cannot dodge the
#: comparison.
EXPECTED_INPUTS = {
    "apply.yml": {"workflow_call": ["auto", "environment", "pr_number", "ref", "runs_on"]},
    "cell.yml": {
        "workflow_call": [
            "auto",
            "base_sha",
            "by_wave",
            "cell",
            "deploy",
            "environment",
            "head_sha",
            "pr_number",
            "provenance_run",
            "runs_on",
            "stack",
            "tools",
        ]
    },
    "apply-run.yml": {"workflow_call": ["auto", "environment", "pr_number", "ref", "runs_on"]},
    "ci.yml": {},
    "comment-ops.yml": {"workflow_call": ["runs_on"]},
    "deploy.yml": {"workflow_call": ["runs_on"]},
    "drift.yml": {"workflow_call": ["runs_on", "tags"]},
    "manifest-load.yml": {},
    "plan.yml": {"workflow_call": ["runs_on"]},
    "release.yml": {"workflow_call": ["runs_on"]},
    "stage.yml": {
        "workflow_call": [
            "auto",
            "base_sha",
            "by_wave",
            "counts",
            "deploy",
            "first",
            "head_sha",
            "key",
            "keys",
            "level_waves",
            "n",
            "pr_number",
            "provenance_run",
            "runs_on",
            "tools",
        ]
    },
    "unlock.yml": {"workflow_call": ["environment", "ref", "runs_on"]},
    "waves.yml": {
        "workflow_call": [
            "auto",
            "base_sha",
            "by_wave",
            "counts",
            "deploy",
            "head_sha",
            "keys",
            "levels",
            "pr_number",
            "provenance_run",
            "runs_on",
            "stages",
            "tools",
        ]
    },
}

PER_CELL_PLAN_RUN = "${{ fromJSON(inputs.cell).plan_run_id }}"
WHOLE_ROW = "${{ toJSON(matrix) }}"
CELL = "./.github/workflows/cell.yml"


def _inputs(spec):
    """Input names per trigger, for the triggers that declare any."""
    on = spec.get("on") or {}
    found = {}
    for trigger in ("workflow_call", "workflow_dispatch"):
        block = on.get(trigger) if isinstance(on, dict) else None
        names = (block.get("inputs") if isinstance(block, dict) else None) or {}
        if names:
            found[trigger] = sorted(names)
    return found


def test_no_engine_workflow_declares_a_plan_run_id_input():
    found = {p.name: _inputs(workflow_yaml(p)) for p in sorted(WORKFLOWS.glob("*.y*ml"))}
    assert found == EXPECTED_INPUTS, f"engine workflow inputs changed: {found}"


def test_the_cell_job_takes_the_plan_run_id_from_its_matrix_row():
    """Mutations: pass `cell: ${{ toJSON(inputs) }}` on `stage.yml`'s `cell`; read `plan-run-id`
    from `inputs.provenance_run` in `cell.yml`."""
    jobs = workflow_yaml("stage.yml")["jobs"]
    cell_callers = sorted(j for j, job in jobs.items() if job.get("uses") == CELL)
    assert cell_callers == ["cell"], f"cell.yml callers changed: {cell_callers}"
    assert jobs["cell"]["with"]["cell"] == WHOLE_ROW
    steps = workflow_yaml("cell.yml")["jobs"]["apply"]["steps"]
    cell = [s for s in steps if "actions/apply-cell" in (s.get("uses") or "")]
    assert [s["with"]["plan-run-id"] for s in cell] == [PER_CELL_PLAN_RUN]
