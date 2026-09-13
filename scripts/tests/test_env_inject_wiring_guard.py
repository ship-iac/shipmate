"""The route a cell's identity variables take: the matrix row, then `env-inject`.

`TF_VAR_env`, `TF_VAR_region` and `TF_WORKSPACE` used to be job-level `env:` bindings read
straight from `vars.*`. They now reach the process through `scripts/env-inject`, which writes
`$GITHUB_ENV` from the matrix row's `tf_vars`, carried in as `SHIPMATE_TF_VARS`. Three things can
silently break that route: a cell step or action dropping the identity input, a workflow binding
one of the old names on the job (which beats `$GITHUB_ENV`), and an `env-inject` step that runs
after the plan it was supposed to feed. All leave a green run and a wrong fingerprint.

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

#: The names `env-inject` writes to `$GITHUB_ENV`. No workflow may bind them on a job: a
#: job-level `env:` entry wins over `$GITHUB_ENV`, so one would silently outrank the table.
_OLD_NAMES = ("TF_VAR_env", "TF_VAR_region", "TF_WORKSPACE")

_CELL_ACTIONS = ("plan-cell", "apply-cell", "drift-cell", "unlock-cell")

_INJECT_STEP = "Inject identity variables"
_INJECT_RUN = 'python3 "$GITHUB_ACTION_PATH/../../scripts/env-inject"'
_INJECT_ENV = {
    "SHIPMATE_TF_VARS": "${{ inputs.tf-vars }}",
    "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
    "SHIPMATE_SECRETS": "${{ inputs.consumer-secrets }}",
}

#: The identity input every cell step passes, and the only `with:` entry this file owns.
#: `toJSON` is load-bearing: `tf_vars` is a mapping, and a mapping interpolated into a string
#: input arrives as GHA's own `Object` rendering, which is not JSON.
_CELL_STEP_WITH = {"tf-vars": "${{ toJSON(matrix.tf_vars) }}"}

_IDENTITY_INPUTS = ("tf-vars",)

#: The two consumer channels every cell step binds beside the identity input, and the whole
#: `inputs:` shape each cell action must declare for them. `required: false` with an empty
#: default is the opposite of `tf-vars`: absent is the normal case for a consumer with no extras,
#: and `env-inject` reads empty as `{}`. `toJSON` is load-bearing on the enumeration for the same
#: reason it is on `tf_vars` -- `vars` is a context object, and interpolating one into a string
#: input yields GHA's `Object` rendering rather than JSON.
_CHANNEL_STEP_WITH = {
    "github-vars": "${{ toJSON(vars) }}",
    "consumer-secrets": "${{ secrets.SHIPMATE_SECRETS }}",
}
_CHANNEL_INPUT_SPEC = {"required": False, "default": ""}

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


def test_no_workflow_binds_the_old_names():
    """Absence, across every workflow rather than the four this file otherwise names: a fifth
    workflow that gains one later must red. The coverage assertions below are what keep an
    absence check from passing over an empty scan.

    Mutation: add `TF_VAR_env: ${{ vars.TF_VAR_env }}` to any job's `env:` block.
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
def test_every_cell_step_passes_the_identity_input_from_its_matrix_row(workflow, job_id):
    """The last hop: `tf_vars` is stamped on every matrix row, and a cell step that does not
    forward it hands the action an empty input, which `env-inject` refuses. A grep run once at
    authoring time does not stop a twelfth step from omitting it, so the eleven are checked
    against the same registry every other property here uses.

    Mutations: delete the `tf-vars:` line from one wave job's `with:`; drop the `toJSON()` and
    pass `${{ matrix.tf_vars }}`.
    """
    steps = [s for s in (_jobs(_doc(workflow))[job_id].get("steps") or []) if _runs_a_cell(s)]
    assert len(steps) == 1, f"{workflow}:{job_id}: {len(steps)} cell steps"
    with_ = steps[0]["with"]
    assert {k: with_.get(k) for k in _IDENTITY_INPUTS} == _CELL_STEP_WITH


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
def test_each_cell_action_forwards_its_identity_inputs(action):
    """The step's whole `env:` mapping, which is this one entry. This is the second hop and
    nothing else pins it -- drop the binding and the workflow still passes the input, and every
    cell of this action refuses.

    Mutations: delete the binding; bind it from `env.SHIPMATE_TF_VARS` instead of
    `inputs.tf-vars`.
    """
    hits = [s for s in action_steps(action) if s.get("name") == _INJECT_STEP]
    assert len(hits) == 1, f"{action}: {len(hits)} '{_INJECT_STEP}' steps"
    assert hits[0].get("env") == _INJECT_ENV
    assert hits[0]["run"].strip() == _INJECT_RUN


@pytest.mark.parametrize("action", _CELL_ACTIONS)
def test_each_cell_action_declares_the_identity_input_without_a_default(action):
    """No default is what makes an omitted `tf-vars:` fail closed. GHA does not enforce
    `required: true` on a composite action input, so the value arrives empty and `env-inject`
    refuses -- a default would inject nothing where the table resolved something.

    Mutation: add `default: "{}"` to one action's `tf-vars`.
    """
    spec = yaml.safe_load((ACTIONS / action / "action.yml").read_text(encoding="utf-8"))
    for name in _IDENTITY_INPUTS:
        assert "default" not in spec["inputs"][name], name
        assert spec["inputs"][name]["required"] is True, name


@pytest.mark.parametrize(("workflow", "job_id"), _ELEVEN, ids=lambda v: v)
def test_every_cell_step_binds_both_consumer_channels(workflow, job_id):
    """Both channels, at every one of the eleven sites, from the same registry the identity
    input is checked against. One job drifting from the other ten is the realistic failure: an
    unbound channel reaches the action as its empty default, so that cell silently carries none
    of the consumer's variables or secrets while the other ten do.

    Mutation: delete the `consumer-secrets:` line from ONE wave job's `with:`.
    """
    steps = [s for s in (_jobs(_doc(workflow))[job_id].get("steps") or []) if _runs_a_cell(s)]
    assert len(steps) == 1, f"{workflow}:{job_id}: {len(steps)} cell steps"
    with_ = steps[0]["with"]
    assert {k: with_.get(k) for k in _CHANNEL_STEP_WITH} == _CHANNEL_STEP_WITH


@pytest.mark.parametrize("action", _CELL_ACTIONS)
def test_each_cell_action_declares_both_consumer_channels(action):
    """Each channel's whole `inputs:` entry, description aside. An undeclared `with:` key on a
    composite action is ignored with a warning rather than refused, so a workflow can bind a
    channel the action never declares and every cell of it runs without one.

    Mutation: delete the `github-vars:` input block from one action's `inputs:`.
    """
    spec = yaml.safe_load((ACTIONS / action / "action.yml").read_text(encoding="utf-8"))
    for name in _CHANNEL_STEP_WITH:
        declared = dict(spec["inputs"][name])
        declared.pop("description", None)
        assert declared == _CHANNEL_INPUT_SPEC, name
