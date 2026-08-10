"""Guards the AWS OIDC wiring in the apply-path reusable workflows.

Invariants:
- every wave job in apply-env-level.yml carries id-token: write, exactly one
  credentials step gated on the workload role or vars.AWS_ROLE_ARN, placed
  BEFORE the apply-cell step, and the empty-suffix state-path expression;
- apply-env-level.yml declares a workflow-level `permissions: {}` floor, and the
  snapshot and complete jobs declare exactly the scopes they need -- neither
  gets id-token (neither touches the cloud, and complete holds the App key);
- state_suffix stays required with no default on all four apply-path workflows,
  so omitting it is a workflow-resolution error rather than a silent no-state
  apply.

Whole parsed values, never substrings (an inverted gate must fail here).
"""

import yaml
from _loader import WORKFLOWS

CRED_ACTION = "aws-actions/configure-aws-credentials"
CRED_IF = (
    "${{ (matrix.workload_var != '' "
    "&& vars[format('AWS_ROLE_ARN_{0}', matrix.workload_var)]) != '' "
    "|| vars.AWS_ROLE_ARN != '' }}"
)
ROLE_TO_ASSUME = (
    "${{ matrix.workload_var != '' "
    "&& vars[format('AWS_ROLE_ARN_{0}', matrix.workload_var)] "
    "|| vars.AWS_ROLE_ARN }}"
)
STATE_PATH_EXPR = (
    "${{ inputs.state_suffix != '' && format('{0}/{1}', matrix.stack, inputs.state_suffix) || '' }}"
)
WAVES = [f"wave{i}" for i in range(8)]


def _load(name):
    spec = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(spec, dict), f"{name} did not parse to a mapping"
    return spec


def _is_cell(step):
    return "/actions/apply-cell@" in str(step.get("uses", ""))


def _wave_jobs():
    jobs = _load("apply-env-level.yml")["jobs"]
    missing = [w for w in WAVES if w not in jobs]
    assert not missing, f"apply-env-level.yml lost wave jobs: {missing}"
    return {w: jobs[w] for w in WAVES}


def test_every_wave_job_grants_id_token_write():
    for wave, job in _wave_jobs().items():
        perms = job.get("permissions") or {}
        assert perms.get("id-token") == "write", f"{wave}: permissions must include id-token: write"


def test_workflow_level_permissions_are_an_empty_floor():
    spec = _load("apply-env-level.yml")
    assert spec.get("permissions") == {}, (
        "apply-env-level.yml must declare a workflow-level `permissions: {}` floor "
        "-- without it a job that loses its own block inherits everything the "
        f"caller granted, id-token: write included; got {spec.get('permissions')!r}"
    )


def test_snapshot_and_complete_jobs_get_exactly_their_declared_permissions():
    # Whole-mapping comparison, not `id-token is None`: the realistic break is
    # the block being deleted, and an absent block is not an absent scope -- the
    # job then inherits the caller's grants (or, with the floor above, nothing).
    expected = {"snapshot": {"checks": "read"}, "complete": {"actions": "read"}}
    jobs = _load("apply-env-level.yml")["jobs"]
    for name, perms in expected.items():
        assert jobs[name].get("permissions") == perms, (
            f"{name} must declare exactly {perms} -- it never touches the cloud, "
            "and complete holds the App private key"
        )


def test_every_wave_has_exactly_one_gated_cred_step_before_apply_cell():
    for wave, job in _wave_jobs().items():
        steps = job["steps"]
        cred_idx = [i for i, s in enumerate(steps) if CRED_ACTION in str(s.get("uses", ""))]
        assert len(cred_idx) == 1, (
            f"{wave}: expected exactly one credentials step, got {len(cred_idx)}"
        )
        cred = steps[cred_idx[0]]
        assert cred.get("if") == CRED_IF, (
            f"{wave}: credentials step must be gated on the workload role or AWS_ROLE_ARN"
        )
        assert cred["with"]["role-to-assume"] == ROLE_TO_ASSUME
        assert cred["with"]["aws-region"] == "${{ vars.AWS_REGION }}"
        cell_idx = [i for i, s in enumerate(steps) if _is_cell(s)]
        assert len(cell_idx) == 1, f"{wave}: expected exactly one apply-cell step"
        assert cred_idx[0] < cell_idx[0], (
            f"{wave}: credentials must be configured before apply-cell"
        )


def test_every_wave_passes_empty_state_path_when_suffix_empty():
    for wave, job in _wave_jobs().items():
        cell = next(s for s in job["steps"] if _is_cell(s))
        assert cell["with"]["state-path"] == STATE_PATH_EXPR, (
            f"{wave}: state-path must collapse to '' when state_suffix is empty "
            "(a bare trailing-slash path would defeat the optional-state skip)"
        )


def _state_suffix_input(workflow):
    spec = _load(workflow)
    on = spec.get("on") or spec.get(True)  # pyyaml parses bare `on:` as boolean True
    return on["workflow_call"]["inputs"]["state_suffix"]


def _assert_state_suffix_required_with_no_default(workflow):
    inp = _state_suffix_input(workflow)
    assert inp.get("required") is True, (
        f"{workflow}: state_suffix must stay required: true -- a remote-backend "
        "consumer opts in by passing '' explicitly, so omission stays a "
        "workflow-resolution error instead of silently skipping state"
    )
    assert "default" not in inp, (
        f"{workflow}: state_suffix must declare no default -- a default '' turns "
        "a forgotten state configuration into a silent no-state apply that the "
        "gate then reports green"
    )


def test_apply_env_level_state_suffix_is_required_with_no_default():
    _assert_state_suffix_required_with_no_default("apply-env-level.yml")


def test_state_suffix_inputs_are_required_with_no_default_everywhere():
    for wf in ("apply.yml", "apply-all.yml", "deploy.yml"):
        _assert_state_suffix_required_with_no_default(wf)
