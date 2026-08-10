"""Guards the workload-scoped apply role in apply-env-level.yml.

Every wave job's credentials step must prefer `AWS_ROLE_ARN_<WORKLOAD>` on the
`<env>-apply` environment and fall back to `AWS_ROLE_ARN`, and must stay skipped
when neither is set. The lookup key can only come from the matrix
(`workload_var`, computed in build-matrix): GitHub upper-cases variable names
and GHA expressions have no toUpper.

Whole expressions against hand-written constants -- one selector, one
comparison, eight jobs.
"""

import yaml
from _loader import WORKFLOWS

CRED_ACTION = "aws-actions/configure-aws-credentials"

ROLE_TO_ASSUME = (
    "${{ matrix.workload_var != '' "
    "&& vars[format('AWS_ROLE_ARN_{0}', matrix.workload_var)] "
    "|| vars.AWS_ROLE_ARN }}"
)
CRED_IF = (
    "${{ (matrix.workload_var != '' "
    "&& vars[format('AWS_ROLE_ARN_{0}', matrix.workload_var)]) != '' "
    "|| vars.AWS_ROLE_ARN != '' }}"
)
WAVES = [f"wave{i}" for i in range(8)]


def _cred_steps():
    spec = yaml.safe_load((WORKFLOWS / "apply-env-level.yml").read_text(encoding="utf-8"))
    jobs = spec["jobs"]
    missing = [w for w in WAVES if w not in jobs]
    assert not missing, f"apply-env-level.yml lost wave jobs: {missing}"
    out = {}
    for wave in WAVES:
        creds = [s for s in jobs[wave]["steps"] if CRED_ACTION in str(s.get("uses", ""))]
        assert len(creds) == 1, f"{wave}: expected exactly one credentials step, got {len(creds)}"
        out[wave] = creds[0]
    return out


def test_every_wave_prefers_the_workload_role():
    for wave, cred in _cred_steps().items():
        assert cred["with"]["role-to-assume"] == ROLE_TO_ASSUME, (
            f"{wave}: role-to-assume must prefer AWS_ROLE_ARN_<WORKLOAD> and fall "
            "back to AWS_ROLE_ARN"
        )


def test_every_wave_gates_on_either_role_being_set():
    for wave, cred in _cred_steps().items():
        assert cred["if"] == CRED_IF, (
            f"{wave}: the credentials step must run when either the workload role "
            "or AWS_ROLE_ARN is set, and be skipped when neither is"
        )
