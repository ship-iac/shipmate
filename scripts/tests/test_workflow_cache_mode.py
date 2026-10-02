"""`cache-mode` placement across every engine workflow, both levels, pinned whole.

`plan.yml` and `unlock.yml` run pull-request HCL with the default branch's cache scope. A missing
workflow-level `read` there, or a job-level `write` (the job-level key wins over the workflow
key), lets that HCL save an Actions-cache entry that drift and apply jobs restore. An added `read`
on a job that saves state or providers denies the save with the step still green.

Mutations: delete `cache-mode: read` from `unlock.yml`; set `plan.yml`'s to `none`; add a
job-level `cache-mode: write` to `plan.yml`'s `plan` job; add a job-level `cache-mode: read` to
`apply-env-level.yml`'s `wave0` job; add a new `.github/workflows/probe.yaml`.
"""

from _loader import WORKFLOWS, workflow_yaml

#: Hand-written: every workflow file by name, so a new file reds until its mode is decided.
_EXPECTED = {
    "apply-all.yml": (None, {}),
    "apply-env-level.yml": (None, {}),
    "apply-review.yml": (None, {}),
    "apply.yml": (None, {}),
    "ci.yml": (None, {}),
    "comment-ops.yml": (None, {}),
    "deploy.yml": (None, {}),
    "drift.yml": (None, {}),
    "manifest-load.yml": (None, {}),
    "plan.yml": ("read", {}),
    "unlock.yml": ("read", {}),
}


def _placement(path):
    spec = workflow_yaml(path)
    jobs = {
        job_id: job["cache-mode"]
        for job_id, job in (spec.get("jobs") or {}).items()
        if "cache-mode" in job
    }
    return spec.get("cache-mode"), jobs


def test_cache_mode_placement_is_pinned():
    paths = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
    assert {p.name: _placement(p) for p in paths} == _EXPECTED
