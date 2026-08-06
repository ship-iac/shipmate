"""Guards the AWS OIDC wiring in the apply-path reusable workflows.

Invariants:
- every wave job in apply-env-level.yml carries id-token: write, exactly one
  credentials step gated on vars.AWS_ROLE_ARN, placed BEFORE the apply-cell
  step, and the empty-suffix state-path expression;
- the snapshot job does NOT get id-token (it never touches the cloud).

Whole parsed values, never substrings (an inverted gate must fail here).
"""

import yaml
from _loader import WORKFLOWS

CRED_ACTION = "aws-actions/configure-aws-credentials"
CRED_IF = "${{ vars.AWS_ROLE_ARN != '' }}"
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


def test_snapshot_job_does_not_get_id_token():
    snapshot = _load("apply-env-level.yml")["jobs"]["snapshot"]
    perms = snapshot.get("permissions") or {}
    assert perms.get("id-token") is None, (
        f"snapshot must request no id-token scope at all, got {perms.get('id-token')!r} "
        "-- it never touches the cloud"
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
            f"{wave}: credentials step must be gated on vars.AWS_ROLE_ARN"
        )
        assert cred["with"]["role-to-assume"] == "${{ vars.AWS_ROLE_ARN }}"
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


def test_apply_env_level_state_suffix_is_optional():
    spec = _load("apply-env-level.yml")
    on = spec.get("on") or spec.get(True)  # pyyaml parses bare `on:` as boolean True
    inp = on["workflow_call"]["inputs"]["state_suffix"]
    assert inp.get("required") is False, "apply-env-level.yml: state_suffix must be required: false"
    assert inp.get("default", "") == "", "apply-env-level.yml: state_suffix default must be ''"


def _jobs_calling(workflow, callee):
    jobs = _load(workflow)["jobs"]
    return {name: job for name, job in jobs.items() if callee in str(job.get("uses", ""))}


def test_every_job_calling_apply_env_level_grants_id_token():
    for wf in ("apply.yml", "apply-all.yml", "deploy.yml"):
        callers = _jobs_calling(wf, "/apply-env-level.yml@")
        assert callers, f"{wf}: expected at least one job calling apply-env-level.yml"
        for name, job in callers.items():
            perms = job.get("permissions") or {}
            assert perms.get("id-token") == "write", (
                f"{wf}:{name}: caller job must grant id-token: write -- a called "
                "workflow requesting a permission its caller job didn't grant "
                "fails at workflow-resolution time (same behavior apply-env-level.yml "
                "already documents for checks:read), taking every wave job with it"
            )


def test_state_suffix_inputs_are_optional_everywhere():
    for wf in ("apply.yml", "apply-all.yml", "deploy.yml"):
        spec = _load(wf)
        on = spec.get("on") or spec.get(True)  # pyyaml parses bare `on:` as boolean True
        inp = on["workflow_call"]["inputs"]["state_suffix"]
        assert inp.get("required") is False, f"{wf}: state_suffix must be required: false"
        assert inp.get("default", "") == "", f"{wf}: state_suffix default must be ''"
