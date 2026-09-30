"""Every engine job's `runs-on:`, whole map.

The control jobs run no `tofu` and no `terramate`, so they take the 1-CPU `ubuntu-slim` image.
Detect jobs run `terramate` and cells run `tofu`: they stay on `ubuntu-latest`. `plan.yml` and
`drift.yml` take the consumer's `runs_on:` input. A new job, a moved job or a flipped label
fails here. Mutations: `apply-env-level.yml` `complete` to `ubuntu-latest`; a `plan.yml` job to
`ubuntu-slim`; the `apply-env-level.yml` `wave0` cell to `ubuntu-slim`.
"""

from _loader import WORKFLOWS, workflow_yaml

SLIM = "ubuntu-slim"
LATEST = "ubuntu-latest"
INPUT = "${{ inputs.runs_on }}"

EXPECTED = {
    ("apply-all.yml", "guard"): SLIM,
    ("apply-all.yml", "detect"): LATEST,
    ("apply-all.yml", "summary"): SLIM,
    ("apply-env-level.yml", "snapshot"): SLIM,
    **{("apply-env-level.yml", f"wave{n}"): LATEST for n in range(8)},
    ("apply-env-level.yml", "complete"): SLIM,
    ("apply-review.yml", "review"): SLIM,
    ("apply.yml", "guard"): SLIM,
    ("apply.yml", "detect"): LATEST,
    ("apply.yml", "summary"): SLIM,
    ("ci.yml", "lint-test"): LATEST,
    ("comment-ops.yml", "ops"): SLIM,
    ("deploy.yml", "detect"): LATEST,
    ("deploy.yml", "summary"): SLIM,
    ("drift.yml", "detect"): INPUT,
    ("drift.yml", "drift"): INPUT,
    ("drift.yml", "issues"): INPUT,
    ("manifest-load.yml", "load"): LATEST,
    ("plan.yml", "facts"): INPUT,
    ("plan.yml", "detect"): INPUT,
    ("plan.yml", "plan"): INPUT,
    ("plan.yml", "summary"): INPUT,
    ("unlock.yml", "guard"): SLIM,
    ("unlock.yml", "detect"): LATEST,
    ("unlock.yml", "unlock"): LATEST,
}


def test_every_job_runs_on_its_expected_label():
    actual = {
        (path.name, job): spec["runs-on"]
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job, spec in workflow_yaml(path)["jobs"].items()
        if "runs-on" in spec
    }
    assert actual == EXPECTED
