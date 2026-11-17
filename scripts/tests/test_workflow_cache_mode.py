"""`cache-mode` placement across every engine workflow, both levels, pinned whole.

`plan.yml` and `unlock.yml` run pull-request HCL with the default branch's cache scope, and so does
each `apply-env-level.yml` wave cell. A missing `read` there, or a job-level `write` (the
job-level key wins over the workflow key), lets that HCL save an Actions-cache entry that drift,
plan and apply jobs restore. Only the `waveN-save` legs, which run no pull-request code, save state;
an added `read` on one denies its save, which then fails the leg.

Mutations: delete `cache-mode: read` from `unlock.yml`; set `plan.yml`'s to `none`; add a
job-level `cache-mode: write` to `plan.yml`'s `plan` job; set `wave3`'s to `write`; delete
`wave5-save`'s; add a new `.github/workflows/probe.yaml`; add an empty top-level
`cache-mode:` to `apply.yml`.
"""

from _loader import WORKFLOWS, workflow_yaml

#: Distinct from `None` (an empty `cache-mode:` key) and from every string value.
_ABSENT = object()

#: Hand-written: every workflow file by name, so a new file reds until its mode is decided.
_EXPECTED = {
    "apply-env-level.yml": (
        _ABSENT,
        {
            "wave0": "read",
            "wave1": "read",
            "wave2": "read",
            "wave3": "read",
            "wave4": "read",
            "wave5": "read",
            "wave6": "read",
            "wave7": "read",
            "wave0-read": "read",
            "wave1-read": "read",
            "wave2-read": "read",
            "wave3-read": "read",
            "wave4-read": "read",
            "wave5-read": "read",
            "wave6-read": "read",
            "wave7-read": "read",
            "wave0-save": "write",
            "wave1-save": "write",
            "wave2-save": "write",
            "wave3-save": "write",
            "wave4-save": "write",
            "wave5-save": "write",
            "wave6-save": "write",
            "wave7-save": "write",
            "wave0-verdict": "read",
            "wave1-verdict": "read",
            "wave2-verdict": "read",
            "wave3-verdict": "read",
            "wave4-verdict": "read",
            "wave5-verdict": "read",
            "wave6-verdict": "read",
            "wave7-verdict": "read",
        },
    ),
    "apply-run.yml": (_ABSENT, {}),
    "apply.yml": (_ABSENT, {}),
    "ci.yml": (_ABSENT, {}),
    "comment-ops.yml": (_ABSENT, {}),
    "deploy.yml": (_ABSENT, {}),
    "drift.yml": (_ABSENT, {}),
    "manifest-load.yml": (_ABSENT, {}),
    "plan.yml": ("read", {}),
    "release.yml": (_ABSENT, {}),
    "unlock.yml": ("read", {}),
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
