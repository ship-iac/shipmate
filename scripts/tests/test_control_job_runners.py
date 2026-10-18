"""Every engine job's `runs-on:`, whole map.

Jobs that run `terramate` or `tofu`, plus `plan.yml`'s `summary` and `comment-ops.yml`'s `ops`
(both parse YAML), take the consumer's `runs_on:` input. The control jobs run neither tool and take
the 1-CPU `ubuntu-slim` image. Engine CI (`ci.yml`, `manifest-load.yml`) stays on `ubuntu-latest`.
A new job, a moved job or a flipped label fails here. Mutations: `apply-env-level.yml` `complete`
to `${{ inputs.runs_on }}`; `plan.yml` `summary` to `ubuntu-slim`; `plan.yml` `facts` to
`${{ inputs.runs_on }}`; the `apply-env-level.yml` `wave0` cell to `ubuntu-latest`;
`comment-ops.yml` `ops` to `ubuntu-slim`; `unlock.yml` `unlock` to `ubuntu-latest`.
"""

from _loader import WORKFLOWS, workflow_yaml

SLIM = "ubuntu-slim"
LATEST = "ubuntu-latest"
INPUT = "${{ inputs.runs_on }}"

EXPECTED = {
    ("apply-env-level.yml", "snapshot"): SLIM,
    **{("apply-env-level.yml", f"wave{n}"): INPUT for n in range(8)},
    ("apply-env-level.yml", "complete"): SLIM,
    ("apply.yml", "guard"): SLIM,
    ("apply.yml", "review"): SLIM,
    ("apply.yml", "detect"): INPUT,
    ("apply.yml", "summary"): SLIM,
    ("ci.yml", "lint-test"): LATEST,
    ("comment-ops.yml", "ops"): INPUT,
    ("deploy.yml", "detect"): INPUT,
    ("deploy.yml", "summary"): SLIM,
    ("drift.yml", "detect"): INPUT,
    ("drift.yml", "drift"): INPUT,
    ("drift.yml", "issues"): INPUT,
    ("manifest-load.yml", "load"): LATEST,
    ("plan.yml", "facts"): SLIM,
    ("plan.yml", "detect"): INPUT,
    ("plan.yml", "plan"): INPUT,
    ("plan.yml", "summary"): INPUT,
    ("plan.yml", "gate-hold"): SLIM,
    ("unlock.yml", "guard"): SLIM,
    ("unlock.yml", "detect"): INPUT,
    ("unlock.yml", "unlock"): INPUT,
}


def test_every_job_runs_on_its_expected_label():
    actual = {
        (path.name, job): spec["runs-on"]
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job, spec in workflow_yaml(path)["jobs"].items()
        if "runs-on" in spec
    }
    assert actual == EXPECTED
