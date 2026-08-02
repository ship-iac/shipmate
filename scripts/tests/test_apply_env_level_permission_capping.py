"""A reusable workflow's jobs cannot request a `GITHUB_TOKEN` scope above what
the *calling* job granted -- permissions only ever narrow across a `uses:`
boundary, never widen. `apply-env-level.yml`'s `snapshot` job requests
`checks: read`; every caller job that `uses:` it must grant at least that, or
GitHub fails the run at workflow-resolution time and every `shipmate apply`
and post-merge `deploy` breaks before a single wave runs.

Source-derived, not a hardcoded list of caller sites: this reads the union of
every job's `permissions:` inside `apply-env-level.yml` itself and checks it
against each caller found by scanning every engine workflow for a `uses:`
pointing at it, so a future job added to the callee (or a new caller) is
covered automatically instead of silently falling outside a stale list.
"""

import re

import yaml
from _loader import WORKFLOWS

CALLEE = "apply-env-level.yml"
_CALLEE_REF = re.compile(r"apply-env-level\.yml(?:@|$)")

# GitHub's permission scale for a single scope: absence/`none` grants nothing,
# `read` and `write` are strictly ordered above it. A caller must grant a
# level >= what the callee's jobs collectively require for every scope.
_RANK = {"none": 0, "read": 1, "write": 2}


def _rank(level):
    return _RANK.get(level, 0)


def _workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _callee_permission_union():
    """The union, per scope, of every job's `permissions:` inside the callee --
    the ceiling a caller must clear so every one of the callee's jobs can run."""
    jobs = _workflow(CALLEE)["jobs"]
    union = {}
    for job in jobs.values():
        for scope, level in (job.get("permissions") or {}).items():
            if _rank(level) > _rank(union.get(scope, "none")):
                union[scope] = level
    return union


def _callers():
    """(workflow filename, job id, job dict) for every job in every engine
    workflow whose `uses:` targets the callee."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name == CALLEE:
            continue
        spec = _workflow(path.name)
        for job_id, job in (spec.get("jobs") or {}).items():
            uses = job.get("uses")
            if uses and _CALLEE_REF.search(uses):
                found.append((path.name, job_id, job))
    return found


def test_apply_env_level_has_at_least_one_caller():
    # Otherwise the guard below would vacuously pass over an empty list.
    assert _callers(), f"no engine workflow calls {CALLEE} -- guard is vacuous"


def test_every_caller_grants_at_least_the_callees_permission_union():
    ceiling = _callee_permission_union()
    assert ceiling, f"{CALLEE} declares no job permissions -- guard is vacuous"
    for wf_name, job_id, job in _callers():
        granted = job.get("permissions") or {}
        for scope, required_level in ceiling.items():
            got = granted.get(scope, "none")
            assert _rank(got) >= _rank(required_level), (
                f"{wf_name}:{job_id} `uses: {CALLEE}` but grants "
                f"{scope}: {got!r}, which is below the callee's own "
                f"{scope}: {required_level!r} -- the run will fail at "
                "workflow-resolution time (permissions only narrow across a "
                "`uses:` boundary, never widen)"
            )
