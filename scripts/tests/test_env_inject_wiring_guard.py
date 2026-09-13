"""The route a cell's identity variables take: job-level `SHIPMATE_LEGACY_*`, then `env-inject`.

`TF_VAR_env`, `TF_VAR_region` and `TF_WORKSPACE` used to be job-level `env:` bindings read
straight from `vars.*`. They now reach the process through `scripts/env-inject`, which reads the
renamed `SHIPMATE_LEGACY_*` bindings and writes `$GITHUB_ENV`. Two things can silently break
that route: one of eleven cell jobs missing a binding, and an `env-inject` step that runs after
the plan it was supposed to feed. Both leave a green run and a wrong fingerprint.

Every assertion is a whole hand-written value against `yaml.safe_load` output. A membership check
passes an entry whose expression was mistyped; a substring check is satisfied by a comment.
"""

import pytest
import yaml
from _loader import ACTIONS, WORKFLOWS, action_steps, run_lines

#: The jobs that run a cell, hand-written. `test_the_registry_names_every_job_that_runs_a_cell`
#: derives the same set from the files, so a twelfth cell job reds rather than going unguarded.
_CELL_JOBS = {
    "plan.yml": ("plan",),
    "drift.yml": ("drift",),
    "unlock.yml": ("unlock",),
    "apply-env-level.yml": (
        "wave0",
        "wave1",
        "wave2",
        "wave3",
        "wave4",
        "wave5",
        "wave6",
        "wave7",
    ),
}

#: The whole `env:` block every cell job writes, byte-identical across all eleven. The first
#: three are what `env-inject` reads; the last three carry no behaviour yet and exist so the
#: rename happens once. Uppercase here because GitHub uppercases variable names -- the names
#: `env-inject` writes are lowercase after the prefix, and that difference is the fingerprint.
_LEGACY_ENV = {
    "SHIPMATE_LEGACY_TF_VAR_ENV": "${{ vars.TF_VAR_env }}",
    "SHIPMATE_LEGACY_TF_VAR_REGION": "${{ vars.TF_VAR_region }}",
    "SHIPMATE_LEGACY_TF_WORKSPACE": "${{ vars.TF_WORKSPACE }}",
    "SHIPMATE_LEGACY_AWS_ROLE_ARN": "${{ vars.AWS_ROLE_ARN }}",
    "SHIPMATE_LEGACY_AWS_REGION": "${{ vars.AWS_REGION }}",
    "SHIPMATE_LEGACY_AWS_ROLE_ARN_WORKLOAD": (
        "${{ matrix.workload_var != '' && "
        "vars[format('AWS_ROLE_ARN_{0}', matrix.workload_var)] || '' }}"
    ),
}

#: The bindings the rename replaced. No workflow may set them again: two writers for one name
#: would make precedence load-bearing, and the job-level one wins over `$GITHUB_ENV`.
_OLD_NAMES = ("TF_VAR_env", "TF_VAR_region", "TF_WORKSPACE")

_CELL_ACTIONS = ("plan-cell", "apply-cell", "drift-cell", "unlock-cell")

_INJECT_STEP = "Inject identity variables"
_INJECT_RUN = 'python3 "$GITHUB_ACTION_PATH/../../scripts/env-inject"'
_INJECT_ENV = {"SHIPMATE_CONFIG_MODE": "${{ inputs.config-mode }}"}

_ELEVEN = [(wf, job) for wf, jobs in _CELL_JOBS.items() for job in jobs]


def _doc(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _jobs(doc):
    return doc.get("jobs") or {}


def _runs_a_cell(step):
    """True for a step that actually runs a cell action.

    `manifest-load.yml` references all four under `if: false`, purely so GitHub parses their
    manifests; that job runs no cell and carries no identity variables.
    """
    return "-cell@" in str(step.get("uses", "")) and step.get("if") is not False


def test_the_registry_names_every_job_that_runs_a_cell():
    """Both sides derived: the registry against the workflows themselves. Without this the other
    guards only check the eleven jobs someone remembered to list.

    Mutation: drop `unlock.yml` from `_CELL_JOBS`, or add a ninth wave job to the registry.
    """
    found = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job_id, job in _jobs(_doc(path.name)).items():
            if any(_runs_a_cell(s) for s in (job.get("steps") or [])):
                found.add((path.name, job_id))
    assert found == set(_ELEVEN)


@pytest.mark.parametrize(("workflow", "job_id"), _ELEVEN, ids=lambda v: v)
def test_every_cell_job_carries_the_six_legacy_bindings(workflow, job_id):
    """The whole block against one hand-written constant, so all eleven copies stay identical.
    A wave job that loses `SHIPMATE_LEGACY_TF_WORKSPACE` injects no workspace and plans the wrong
    one, and a wave whose expressions drift from the constant is a wave nobody reviewed against
    the other ten.

    Mutations: delete one binding from one wave job; change one wave's `vars.TF_VAR_env` to
    `vars.TF_VAR_ENV`.
    """
    assert _jobs(_doc(workflow))[job_id]["env"] == _LEGACY_ENV


def test_no_workflow_binds_the_old_names():
    """Absence, across every workflow rather than the four this file otherwise names: a fifth
    workflow that gains one later must red. The coverage assertions below are what keep an
    absence check from passing over an empty scan.

    Mutation: restore `TF_VAR_env: ${{ vars.TF_VAR_env }}` beside its replacement in any job.
    """
    visited, offenders = set(), []

    def scan(where, env):
        for name in _OLD_NAMES:
            if name in (env or {}):
                offenders.append(f"{where}: {name}")

    files = sorted(WORKFLOWS.glob("*.yml"))
    for path in files:
        doc = _doc(path.name)
        scan(path.name, doc.get("env"))
        for job_id, job in _jobs(doc).items():
            visited.add((path.name, job_id))
            scan(f"{path.name}:{job_id}", job.get("env"))
            for i, step in enumerate(job.get("steps") or []):
                scan(f"{path.name}:{job_id}:step{i}", step.get("env"))

    assert {p.name for p in files} >= set(_CELL_JOBS)
    assert visited >= set(_ELEVEN)
    assert offenders == []


@pytest.mark.parametrize(("workflow", "job_id"), _ELEVEN, ids=lambda v: v)
def test_every_cell_step_passes_the_mode_from_its_matrix_row(workflow, job_id):
    """The last hop: `config_mode` is stamped on every matrix row, and a cell step that does not
    forward it hands the action an empty input, which `env-inject` refuses. A grep run once at
    authoring time does not stop a twelfth step from omitting it, so the eleven are checked
    against the same registry every other property here uses.

    Mutation: delete the `config-mode:` line from one wave job's `with:`, or bind it from
    `matrix.environment`.
    """
    steps = [s for s in (_jobs(_doc(workflow))[job_id].get("steps") or []) if _runs_a_cell(s)]
    assert len(steps) == 1, f"{workflow}:{job_id}: {len(steps)} cell steps"
    assert steps[0]["with"]["config-mode"] == "${{ matrix.config_mode }}"


@pytest.mark.parametrize("action", _CELL_ACTIONS)
def test_each_cell_action_injects_before_it_runs_terramate(action):
    """Order is the property, not presence: a correctly written step placed after the plan
    injects nothing that matters. The first `terramate run` is the bound -- `unlock-cell` has
    three, and its init is already too late.

    Mutation: move the `env-inject` step to the end of one action's step list.
    """
    steps = action_steps(action)
    injects = [i for i, s in enumerate(steps) if s.get("name") == _INJECT_STEP]
    terramate = [
        i for i, s in enumerate(steps) if any("terramate run" in ln for ln in run_lines(s))
    ]
    assert len(injects) == 1, f"{action}: {len(injects)} '{_INJECT_STEP}' steps"
    assert terramate, f"{action}: no step runs terramate"
    assert injects[0] < min(terramate)


@pytest.mark.parametrize("action", _CELL_ACTIONS)
def test_each_cell_action_forwards_its_mode_input(action):
    """The step's whole `env:` mapping, which is one entry: the six `SHIPMATE_LEGACY_*` names are
    set on the job and inherited, so they never appear here. Nothing else pins this hop -- delete
    the binding and the job still sets all six, the workflow still passes `config-mode`, and every
    cell of this action refuses because the script never sees the mode.

    Mutations: delete the binding; bind it from `env.SHIPMATE_CONFIG_MODE` instead of
    `inputs.config-mode`.
    """
    hits = [s for s in action_steps(action) if s.get("name") == _INJECT_STEP]
    assert len(hits) == 1, f"{action}: {len(hits)} '{_INJECT_STEP}' steps"
    assert hits[0].get("env") == _INJECT_ENV
    assert hits[0]["run"].strip() == _INJECT_RUN


@pytest.mark.parametrize("action", _CELL_ACTIONS)
def test_each_cell_action_declares_the_mode_input_without_a_default(action):
    """No default is what makes an omitted `config-mode:` fail closed. GHA does not enforce
    `required: true` on a composite action input, so the value arrives empty and `env-inject`
    refuses -- a default would make it silently run legacy instead.

    Mutation: add `default: legacy` to one action's input.
    """
    spec = yaml.safe_load((ACTIONS / action / "action.yml").read_text(encoding="utf-8"))
    assert "default" not in spec["inputs"]["config-mode"]
    assert spec["inputs"]["config-mode"]["required"] is True
