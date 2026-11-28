"""`cache-mode` placement across every engine workflow, both levels, pinned whole.

`plan.yml` and `unlock.yml` run pull-request HCL with the default branch's cache scope. A missing
workflow-level `read` there, or a job-level `write` (the job-level key wins over the workflow
key), lets that HCL save an Actions-cache entry that drift and apply jobs restore. An added `read`
on a job that saves state or providers denies the save with the step still green.

`cell.yml`'s `apply` runs pull-request code with `read`; its `save`, which runs none, writes the
state entry. A caller's job-level `cache-mode` applies to every job it calls, so a `read` on a
calling job (`waves`, `stage`, `cell`) fails `save` at startup. `apply-run.yml`'s `detect` checks
out the pull request's tree and restores only.

Mutations: delete `cache-mode: read` from `unlock.yml`; set `plan.yml`'s to `none`; add a
job-level `cache-mode: write` to `plan.yml`'s `plan` job; add a job-level `cache-mode: read` to
`stage.yml`'s `cell` job; delete `apply-run.yml`'s `detect` `cache-mode: read`;
move it to `apply-run.yml`'s `waves` job; set `cell.yml`'s `save` to `read`; add a new
`.github/workflows/probe.yaml`; add an empty top-level `cache-mode:` to `apply.yml`.
"""

from _loader import WORKFLOWS, workflow_yaml

#: Distinct from `None` (an empty `cache-mode:` key) and from every string value.
_ABSENT = object()

#: Hand-written: every workflow file by name, so a new file reds until its mode is decided.
_EXPECTED = {
    "apply-run.yml": (_ABSENT, {"detect": "read"}),
    "apply.yml": (_ABSENT, {}),
    "cell.yml": (_ABSENT, {"apply": "read", "save": "write"}),
    "ci.yml": (_ABSENT, {}),
    "comment-ops.yml": (_ABSENT, {}),
    "deploy.yml": (_ABSENT, {}),
    "drift.yml": (_ABSENT, {}),
    "manifest-load.yml": (_ABSENT, {}),
    "plan.yml": ("read", {}),
    "release.yml": (_ABSENT, {}),
    "stage.yml": (_ABSENT, {}),
    "unlock.yml": ("read", {}),
    "waves.yml": (_ABSENT, {}),
}


def _placement(path):
    spec = workflow_yaml(path)
    jobs = {
        job_id: job["cache-mode"]
        for job_id, job in (spec.get("jobs") or {}).items()
        if "cache-mode" in job
    }
    return spec.get("cache-mode", _ABSENT), jobs


def test_cache_mode_placement_is_pinned():
    paths = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
    assert {p.name: _placement(p) for p in paths} == _EXPECTED
