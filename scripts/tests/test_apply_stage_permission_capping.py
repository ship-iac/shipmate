"""A reusable workflow's jobs cannot request a `GITHUB_TOKEN` scope above what the calling job
granted: permissions only ever narrow across a `uses:` boundary, never widen.
`stage.yml`'s `entry` job requests `checks: read`, so every caller job that `uses:` it, and
every caller of `waves.yml` above it, must grant at least that, and `apply.yml`'s `run` must grant
everything `apply-run.yml`'s jobs request. Otherwise GitHub fails the run at workflow-resolution
time, and every `shipmate apply` and post-merge `deploy` breaks before a single stage runs.

The ceiling is the consumer's published grant on its `apply`, `auto` and `deploy` jobs: a hop that
requests more start-fails every consumer, and every sample following `@main`, until each re-pins.

Source-derived, not a hardcoded list of caller sites. Per callee, it reads the union of every
job's `permissions:` inside it and checks it against each caller found by scanning
every engine workflow for a `uses:` pointing at it, so a job added to the callee, or a new
caller, is covered instead of silently falling outside a stale list.
"""

import re

import pytest
from _loader import ENGINE, WORKFLOWS, doc_fences, load_script, workflow_yaml
from _shipmate import load_workflow_text

#: Each callee with its own `uses:` match: `apply-run.yml` must not match `apply.yml` or a
#: longer name ending in it.
CALLEES = {
    "apply-run.yml": re.compile(r"/apply-run\.yml(?:@|$)"),
    "waves.yml": re.compile(r"/waves\.yml(?:@|$)"),
    "stage.yml": re.compile(r"/stage\.yml(?:@|$)"),
    "cell.yml": re.compile(r"/cell\.yml(?:@|$)"),
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
    "apply-run.yml": {("apply.yml", "run")},
    "waves.yml": {("apply-run.yml", "waves"), ("deploy.yml", "waves")},
    "stage.yml": {("waves.yml", "stage")},
    "cell.yml": {("stage.yml", "cell")},
}


@pytest.mark.parametrize("callee", sorted(CALLEES))
def test_each_callee_has_exactly_its_callers(callee):
    """Otherwise test_every_caller_grants_at_least_the_callees_permission_union would pass
    vacuously over an empty list. Mutations: point `apply.yml`'s `run` at `./.github/workflows/
    apply-run.yaml` -- red; point `deploy.yml`'s `waves` at `./.github/workflows/waves.yaml`."""
    got = {(wf, job_id) for wf, job_id, _ in _callers(callee)}
    assert got == EXPECTED_CALLERS[callee], f"{callee} callers are {sorted(got)}"


@pytest.mark.parametrize("callee", sorted(CALLEES))
def test_every_caller_grants_at_least_the_callees_permission_union(callee):
    """Mutations: add `issues: write` to `apply-run.yml`'s `detect` -- red for `apply-run.yml`;
    drop `actions: read` from `stage.yml`'s `cell` -- red for `cell.yml`."""
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


#: The consumer's published grant on every job that calls `apply.yml` or `deploy.yml`,
#: hand-written.
GRANT = {
    "contents": "read",
    "checks": "read",
    "pull-requests": "read",
    "actions": "read",
    "id-token": "write",
}

#: Each engine hop from the consumer's call to `cell.yml`, as (workflow, calling job).
HOPS = [
    ("apply.yml", "run"),
    ("apply-run.yml", "waves"),
    ("deploy.yml", "waves"),
    ("waves.yml", "stage"),
    ("stage.yml", "cell"),
]


def _published_calls():
    """(job name, callee, permissions) per job of the published `shipmate.yml` fence that calls
    `apply.yml` or `deploy.yml`."""
    page = ENGINE / "docs" / "getting-started.md"
    for _, _, body in doc_fences([page], load_script("onboard")._FENCE):
        jobs = (load_workflow_text(body) or {}).get("jobs") or {}
        for name, job in jobs.items():
            callee = str(job.get("uses", "")).split("/.github/workflows/")[-1].split("@")[0]
            if callee in ("apply.yml", "deploy.yml"):
                yield name, callee, job.get("permissions")


def test_the_published_consumer_grant_is_exactly_this():
    """Mutation: `checks: write` on the published `auto` job."""
    assert sorted(_published_calls()) == [
        ("apply", "apply.yml", GRANT),
        ("auto", "apply.yml", GRANT),
        ("deploy", "deploy.yml", GRANT),
    ]


def test_every_hop_grants_exactly_the_published_grant():
    """Mutations: `checks: write` on `stage.yml`'s `cell`; drop `id-token: write` from
    `waves.yml`'s `stage`."""
    found = {hop: workflow_yaml(hop[0])["jobs"][hop[1]].get("permissions") for hop in HOPS}
    assert found == dict.fromkeys(HOPS, GRANT)


@pytest.mark.parametrize(
    "workflow", ["apply.yml", "apply-run.yml", "deploy.yml", "waves.yml", "stage.yml", "cell.yml"]
)
def test_no_job_on_the_apply_path_requests_more_than_the_published_grant(workflow):
    """Mutation: `checks: write` on `stage.yml`'s `entry`."""
    for scope, level in _callee_permission_union(workflow).items():
        assert _rank(level) <= _rank(GRANT.get(scope, "none")), (
            f"{workflow} requests {scope}: {level}, above the published consumer grant"
        )
