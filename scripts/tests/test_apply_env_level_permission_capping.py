"""A reusable workflow's jobs cannot request a `GITHUB_TOKEN` scope above what the calling job
granted: permissions only ever narrow across a `uses:` boundary, never widen.
`apply-env-level.yml`'s `snapshot` job requests `checks: read`, so every caller job that `uses:`
it must grant at least that, and `apply.yml`'s `run` must grant everything `apply-run.yml`'s jobs
request. Otherwise GitHub fails the run at workflow-resolution time, and every `shipmate apply`
and post-merge `deploy` breaks before a single wave runs.

Source-derived, not a hardcoded list of caller sites. Per callee, it reads the union of every
job's `permissions:` inside it and checks it against each caller found by scanning
every engine workflow for a `uses:` pointing at it, so a job added to the callee, or a new
caller, is covered instead of silently falling outside a stale list.
"""

import re

import pytest
from _loader import WORKFLOWS, workflow_yaml

#: Each callee with its own `uses:` match: `apply-run.yml` must not match `apply.yml` or a
#: longer name ending in it.
CALLEES = {
    "apply-env-level.yml": re.compile(r"/apply-env-level\.yml(?:@|$)"),
    "apply-run.yml": re.compile(r"/apply-run\.yml(?:@|$)"),
}

# GitHub's permission scale for a single scope: absence and `none` grant nothing, `read` and
# `write` are strictly ordered above it. For every scope, a caller must grant a level at least
# what the callee's jobs collectively require.
_RANK = {"none": 0, "read": 1, "write": 2}


def _rank(level):
    return _RANK.get(level, 0)


def _callee_permission_union(callee):
    """The union, per scope, of every job's `permissions:` inside the callee: the ceiling a
    caller must clear for every one of the callee's jobs to run."""
    jobs = workflow_yaml(callee)["jobs"]
    union = {}
    for job in jobs.values():
        for scope, level in (job.get("permissions") or {}).items():
            if _rank(level) > _rank(union.get(scope, "none")):
                union[scope] = level
    return union


def _callers(callee):
    """(workflow filename, job id, job dict) per job in any engine workflow whose `uses:` targets
    the callee."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name == callee:
            continue
        spec = workflow_yaml(path.name)
        for job_id, job in (spec.get("jobs") or {}).items():
            uses = job.get("uses")
            if uses and CALLEES[callee].search(uses):
                found.append((path.name, job_id, job))
    return found


#: The callers each callee must have, hand-written, so a `uses:` match that stops matching
#: reds here instead of leaving the grant check vacuous.
EXPECTED_CALLERS = {
    "apply-env-level.yml": {("apply-run.yml", f"envlevel{n}") for n in range(4)}
    | {("deploy.yml", f"envlevel{n}") for n in range(4)},
    "apply-run.yml": {("apply.yml", "run")},
}


@pytest.mark.parametrize("callee", sorted(CALLEES))
def test_each_callee_has_exactly_its_callers(callee):
    """Otherwise test_every_caller_grants_at_least_the_callees_permission_union would pass
    vacuously over an empty list. Mutation: point `apply.yml`'s `run` at `./.github/workflows/
    apply-run.yaml` -- red."""
    got = {(wf, job_id) for wf, job_id, _ in _callers(callee)}
    assert got == EXPECTED_CALLERS[callee], f"{callee} callers are {sorted(got)}"


@pytest.mark.parametrize("callee", sorted(CALLEES))
def test_every_caller_grants_at_least_the_callees_permission_union(callee):
    """Mutation: add `issues: write` to `apply-run.yml`'s `detect` -- red for `apply-run.yml`."""
    ceiling = _callee_permission_union(callee)
    assert ceiling, f"{callee} declares no job permissions -- guard is vacuous"
    for wf_name, job_id, job in _callers(callee):
        granted = job.get("permissions") or {}
        for scope, required_level in ceiling.items():
            got = granted.get(scope, "none")
            assert _rank(got) >= _rank(required_level), (
                f"{wf_name}:{job_id} `uses: {callee}` but grants "
                f"{scope}: {got!r}, which is below the callee's own "
                f"{scope}: {required_level!r} -- the run will fail at "
                "workflow-resolution time (permissions only narrow across a "
                "`uses:` boundary, never widen)"
            )
