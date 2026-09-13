"""Guards the AWS OIDC wiring in the reusable workflows that run a cell.

Invariants:
- every cell-running job -- apply-env-level.yml's waves, plan.yml's `plan`, drift.yml's `drift`,
  unlock.yml's `unlock` -- carries exactly one credentials step placed before its cell step, and
  its gate, role and region select on `matrix.config_mode` alone, byte-identical across the four
  files. Byte-identity is the property: every cell resolves the same identity, and a collapse to
  a bare `vars.AWS_ROLE_ARN` on any one of them hands that cell a role the table did not choose
  while the run stays green;
- no expression falls back on a value being empty, and none reaches a `vars.*` value unless the
  mode is exactly `legacy`. `!= 'table'` would route an absent or misspelled mode into the legacy
  credentials, minting real credentials for a cell `env-inject` only refuses afterwards;
- every wave job in apply-env-level.yml carries id-token: write and the empty-suffix state-path
  expression;
- apply-env-level.yml and unlock.yml declare a workflow-level `permissions: {}` floor, and
  apply-env-level's snapshot and complete jobs declare exactly the scopes they need. Neither gets
  id-token: neither touches the cloud, and complete holds the App key;
- state_suffix stays required with no default on all four apply-path workflows, so omitting it is
  a workflow-resolution error rather than a silent no-state apply.

Whole parsed values, never substrings. An inverted gate must fail here.
"""

import pytest
import yaml
from _loader import WORKFLOWS

CRED_ACTION = "aws-actions/configure-aws-credentials"
#: Hand-written, never derived from the workflow files: a derived constant passes whatever the
#: files say. `== 'legacy'` and not `!= 'table'` -- they differ on exactly the unknown mode.
CRED_IF = (
    "${{ matrix.config_mode == 'table' && matrix.role_arn != '' "
    "|| matrix.config_mode == 'legacy' "
    "&& ((matrix.workload_var != '' "
    "&& vars[format('AWS_ROLE_ARN_{0}', matrix.workload_var)]) != '' "
    "|| vars.AWS_ROLE_ARN != '') }}"
)
ROLE_TO_ASSUME = (
    "${{ matrix.config_mode == 'table' && matrix.role_arn "
    "|| matrix.config_mode == 'legacy' "
    "&& (matrix.workload_var != '' "
    "&& vars[format('AWS_ROLE_ARN_{0}', matrix.workload_var)] "
    "|| vars.AWS_ROLE_ARN) "
    "|| '' }}"
)
AWS_REGION = (
    "${{ matrix.config_mode == 'table' && matrix.cred_region "
    "|| matrix.config_mode == 'legacy' && vars.AWS_REGION "
    "|| '' }}"
)
MODE_GUARD = "matrix.config_mode == 'legacy' &&"
STATE_PATH_EXPR = (
    "${{ inputs.state_suffix != '' && format('{0}/{1}', matrix.stack, inputs.state_suffix) || '' }}"
)
WAVES = [f"wave{i}" for i in range(8)]
#: (workflow, cell-running job ids, cell action) -- the four files that must agree. unlock.yml
#: carries the same step doing the same job; leaving it out is how byte-identity stops being true.
CELL_JOBS = [
    ("apply-env-level.yml", WAVES, "apply-cell"),
    ("plan.yml", ["plan"], "plan-cell"),
    ("drift.yml", ["drift"], "drift-cell"),
    ("unlock.yml", ["unlock"], "unlock-cell"),
]


def _load(name):
    spec = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(spec, dict), f"{name} did not parse to a mapping"
    return spec


def _is_cell(step, action="apply-cell"):
    return f"/actions/{action}@" in str(step.get("uses", ""))


def _cell_jobs(workflow, job_ids):
    jobs = _load(workflow)["jobs"]
    missing = [j for j in job_ids if j not in jobs]
    assert not missing, f"{workflow} lost cell jobs: {missing}"
    return {j: jobs[j] for j in job_ids}


def _wave_jobs():
    return _cell_jobs("apply-env-level.yml", WAVES)


def test_every_wave_job_grants_id_token_write():
    for wave, job in _wave_jobs().items():
        perms = job.get("permissions") or {}
        assert perms.get("id-token") == "write", f"{wave}: permissions must include id-token: write"


@pytest.mark.parametrize("name", ["apply-env-level.yml", "unlock.yml"])
def test_workflow_level_permissions_are_an_empty_floor(name):
    spec = _load(name)
    assert spec.get("permissions") == {}, (
        f"{name} must declare a workflow-level `permissions: {{}}` floor "
        "-- without it a job that loses its own block inherits everything the "
        f"caller granted, id-token: write included; got {spec.get('permissions')!r}"
    )


def test_snapshot_and_complete_jobs_get_exactly_their_declared_permissions():
    # Whole-mapping comparison, not `id-token is None`: the realistic break is the block being
    # deleted, and an absent block is not an absent scope. The job then inherits the caller's
    # grants, or, with the workflow-level floor, nothing.
    expected = {
        "snapshot": {"checks": "read", "actions": "read"},
        "complete": {"actions": "read"},
    }
    jobs = _load("apply-env-level.yml")["jobs"]
    for name, perms in expected.items():
        assert jobs[name].get("permissions") == perms, (
            f"{name} must declare exactly {perms} -- it never touches the cloud, "
            "and complete holds the App private key"
        )


@pytest.mark.parametrize(
    ("workflow", "job_ids", "action"), CELL_JOBS, ids=[c[0] for c in CELL_JOBS]
)
def test_every_cell_job_has_exactly_one_gated_cred_step_before_its_cell(workflow, job_ids, action):
    """The same three hand-written constants for all four files, never one per file: a plan,
    drift or unlock cell that resolves the role differently from a wave job is the defect this
    catches, and a per-file constant would follow the divergence instead of failing on it.

    Mutations, each reddening only its own case: the two branches of plan.yml's `role-to-assume`
    swapped, one mode test in drift.yml replaced by `matrix.role_arn != ''`, a stray input added
    to a wave step's `with:`, and the whole step deleted from unlock.yml.
    """
    for job_id, job in _cell_jobs(workflow, job_ids).items():
        where = f"{workflow} `{job_id}`"
        steps = job["steps"]
        cred_idx = [i for i, s in enumerate(steps) if CRED_ACTION in str(s.get("uses", ""))]
        assert len(cred_idx) == 1, (
            f"{where}: expected exactly one credentials step, got {len(cred_idx)}"
        )
        cred = steps[cred_idx[0]]
        assert cred.get("if") == CRED_IF, (
            f"{where}: credentials step must be gated on the mode the table resolved"
        )
        # The whole mapping, not two keys: a wave step gaining a wrong third input is pinned
        # nowhere else.
        assert cred["with"] == {"role-to-assume": ROLE_TO_ASSUME, "aws-region": AWS_REGION}, where
        cell_idx = [i for i, s in enumerate(steps) if _is_cell(s, action)]
        assert len(cell_idx) == 1, f"{where}: expected exactly one {action} step"
        assert cred_idx[0] < cell_idx[0], f"{where}: credentials must be configured before {action}"


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


def _top_level_clauses(expr):
    """The expression's `||` operands at bracket depth zero, `${{ }}` wrapper stripped."""
    body = expr.removeprefix("${{ ").removesuffix(" }}")
    clauses, start, depth, i = [], 0, 0, 0
    while i < len(body):
        char = body[i]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and body[i : i + 2] == "||":
            clauses.append(body[start:i].strip())
            start = i + 2
            i += 1
        i += 1
    clauses.append(body[start:].strip())
    return clauses


@pytest.mark.parametrize(
    ("name", "expr"),
    [("CRED_IF", CRED_IF), ("ROLE_TO_ASSUME", ROLE_TO_ASSUME), ("AWS_REGION", AWS_REGION)],
    ids=["cred_if", "role_to_assume", "aws_region"],
)
def test_no_clause_reaches_a_vars_value_outside_legacy_mode(name, expr):
    """Read on the constant, not the file: the threat is the fix-the-test move, where someone
    loosens the expression and retypes the constant to match without noticing what it stopped
    saying. Byte-identity then goes green again and only this property reds.

    Mutation: rewrite `matrix.config_mode == 'legacy'` to `matrix.config_mode != 'table'` in the
    workflows and in the constant. An unknown mode would reach the legacy role.
    """
    reaching = [c for c in _top_level_clauses(expr) if "vars." in c or "vars[" in c]
    assert reaching, (
        f"{name}: no clause reads a vars.* value at all -- the constant was retyped past the "
        "property this test exists to hold, and byte-identity alone would not notice"
    )
    for clause in reaching:
        assert clause.startswith(MODE_GUARD), (
            f"{name}: clause {clause!r} reaches a repository variable without "
            f"{MODE_GUARD!r} in front of it -- branch content or an unknown mode would "
            "select the legacy role"
        )
