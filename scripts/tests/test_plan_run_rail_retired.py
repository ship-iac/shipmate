"""The plan run id reaches `apply-cell` per cell, never as a workflow input.

Each wave cell carries its own `plan_run_id`, read from that cell's own apply
check, so no workflow needs to thread one id down for every cell in the run.
Two properties, because a rail can come back at either end: no workflow declares
the input, and every wave job reads the value off its matrix row.
"""

import yaml
from _loader import WORKFLOWS

# Every `workflow_call`/`workflow_dispatch` input name in the engine, per file.
# Hand-written, never read back from the files it guards: the property is that
# no entry says `plan_run_id`, and a guard that derives the constant asserts
# whatever the files happen to say. A file with no inputs is listed as `{}` so a
# new workflow reaching for the retired rail cannot dodge the comparison.
EXPECTED_INPUTS = {
    "apply-all.yml": {"workflow_call": ["pr_number", "ref", "state_suffix"]},
    "apply-env-level.yml": {"workflow_call": ["head_sha", "state_suffix", "waves_json"]},
    "apply.yml": {"workflow_call": ["environment", "pr_number", "ref", "state_suffix"]},
    "ci.yml": {},
    "deploy.yml": {"workflow_call": ["state_suffix"]},
    "internal-pins.yml": {},
    "manifest-load.yml": {},
    "summary.yml": {
        "workflow_call": [
            "detect-result",
            "head-repo",
            "head-sha",
            "is-draft",
            "on-demand",
            "plan-result",
            "planned-cells",
            "pr-number",
        ]
    },
    "unlock.yml": {"workflow_call": ["environment", "ref"]},
}

# The wave fan-out `apply-env-level.yml` pre-declares. Hand-written so a deleted
# wave job reddens instead of passing by absence -- a wave job that references a
# workflow input the file no longer declares fails the run at startup, with no
# job and no log, so "every job that exists is correct" is not enough.
EXPECTED_WAVE_JOBS = ["wave0", "wave1", "wave2", "wave3", "wave4", "wave5", "wave6", "wave7"]

PER_CELL_PLAN_RUN = "${{ matrix.plan_run_id }}"


def _workflow(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _inputs(spec):
    """Input names per trigger, for the triggers that declare any.

    `on:` is YAML 1.1's `on`/`yes`/`y` family, so `yaml.safe_load` hands the key
    back as `True`; reading only the string spelling would find nothing.
    """
    on = spec.get("on", spec.get(True)) or {}
    found = {}
    for trigger in ("workflow_call", "workflow_dispatch"):
        block = on.get(trigger) if isinstance(on, dict) else None
        names = (block.get("inputs") if isinstance(block, dict) else None) or {}
        if names:
            found[trigger] = sorted(names)
    return found


def test_no_engine_workflow_declares_a_plan_run_id_input():
    found = {p.name: _inputs(_workflow(p)) for p in sorted(WORKFLOWS.glob("*.y*ml"))}
    assert found == EXPECTED_INPUTS, f"engine workflow inputs changed: {found}"


def test_every_wave_job_takes_the_plan_run_id_from_its_matrix_cell():
    jobs = _workflow(WORKFLOWS / "apply-env-level.yml")["jobs"]
    wave_jobs = sorted(j for j in jobs if j.startswith("wave"))
    assert wave_jobs == EXPECTED_WAVE_JOBS, f"wave fan-out changed: {wave_jobs}"
    for job_id in wave_jobs:
        cell = [s for s in jobs[job_id]["steps"] if "actions/apply-cell" in (s.get("uses") or "")]
        assert len(cell) == 1, f"{job_id} calls apply-cell {len(cell)} times, expected once"
        assert cell[0]["with"]["plan-run-id"] == PER_CELL_PLAN_RUN, (
            f"{job_id} passes plan-run-id: {cell[0]['with']['plan-run-id']!r}"
        )
