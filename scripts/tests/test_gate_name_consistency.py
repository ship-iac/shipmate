"""Freeze the aggregate gate check name across its writers.

The gate `shipmate / gate` is created pending by `actions/summary`, completed
pre-merge by `actions/gate-refresh`, and completed post-merge inline in
`deploy.yml`. All three must emit the byte-identical name, or the gate greens
on one path and sticks on another. This guards that invariant the same way
test_check_runs_filter_aligned guards the check-runs read discipline.
"""

import pathlib

import yaml

ENGINE = pathlib.Path(__file__).resolve().parents[2]
# Generated / third-party / VCS dirs: never shipmate source, and their contents
# (compiled .pyc constant pools, vendored packages) can carry the retired token
# for reasons unrelated to this repo -- scanning them would false-fail the guard.
SKIP_DIRS = {
    ".git",
    ".superpowers",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}
GATE = "shipmate / gate"
WRITERS = [
    "actions/summary/action.yml",
    "actions/gate-refresh/action.yml",
    ".github/workflows/deploy.yml",
]


def test_gate_literal_present_in_every_writer():
    for rel in WRITERS:
        text = (ENGINE / rel).read_text(encoding="utf-8")
        assert GATE in text, f"{rel} is missing the gate literal {GATE!r}"


def test_gate_written_as_commit_status_not_check_run():
    """The gate must be a commit STATUS, not a check-run.

    A check-run binds to a check-suite; an imperatively-created one lands in an
    arbitrary suite when a commit carries two plan runs (draft->ready, or a
    rapid re-push). The merge evaluator reads the live suite, finds no gate, and
    blocks the PR forever while the green gate sits in the stale suite. A commit
    status is commit-scoped and immune. Lock every writer onto the statuses API.
    """
    for rel in WRITERS:
        text = (ENGINE / rel).read_text(encoding="utf-8")
        assert "statuses/" in text, f"{rel} must POST the gate to the commit statuses API"
        # A gate POST (`--input`) must never target check-runs again. Reads of
        # the check-runs listing (`commits/<sha>/check-runs`, no --input) stay.
        for line in text.splitlines():
            assert not ("check-runs" in line and "--input" in line), (
                f"{rel}: gate POST still targets the check-runs API: {line.strip()}"
            )


WORKFLOWS = ENGINE / ".github" / "workflows"
GATE_WRITER_ACTIONS = ("actions/gate-refresh", "actions/summary")


def _job_writes_gate(job):
    """A workflow job writes the gate when a step calls gate-refresh/summary
    (which POST the status internally) or posts the gate status inline in the
    same run block (deploy.yml). Prose that merely mentions the gate name in a
    comment body does not count -- inline detection requires the statuses API
    call and the gate context in the *same* step."""
    for step in job.get("steps") or []:
        uses = step.get("uses") or ""
        if any(action in uses for action in GATE_WRITER_ACTIONS):
            return True
        run = step.get("run") or ""
        if "statuses/" in run and GATE in run:
            return True
    return False


def _grants_statuses_write(job, workflow_perms):
    # A job-level `permissions:` fully REPLACES the workflow default (GHA
    # semantics), so a job that sets any permissions must set statuses:write
    # itself; only a job that omits `permissions:` inherits the workflow block.
    perms = job.get("permissions", workflow_perms)
    if perms == "write-all":
        return True
    return isinstance(perms, dict) and perms.get("statuses") == "write"


def test_gate_writer_jobs_grant_statuses_write():
    """Every ENGINE workflow job that writes the gate must grant `statuses: write`.

    The gate is a commit status; posting one needs `statuses: write`, not the
    `checks: write` the check-run era used. A gate-refresh caller left on the old
    grant (as apply-all.yml was) 403s the status POST at runtime and the gate
    never completes -- a failure the writer-file tests above cannot see, because
    the missing grant lives in the *caller* workflow.

    Scope limit: this sweep globs only the engine's own `.github/workflows`. The
    primary gate writer, `actions/summary`, is invoked exclusively from consumer
    `plan.yml` (the sample repos), which lives in other repositories and is not
    reachable from here -- those callers must be migrated to `statuses: write`
    separately (see docs/branch-protection.md's upgrade note). So this guards the
    gate-refresh/deploy paths, NOT the summary/plan path.
    """
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        workflow_perms = doc.get("permissions")
        for name, job in (doc.get("jobs") or {}).items():
            if isinstance(job, dict) and _job_writes_gate(job):
                if not _grants_statuses_write(job, workflow_perms):
                    offenders.append(f"{wf.name}:{name}")
    assert not offenders, f"gate-writer job(s) missing `statuses: write`: {offenders}"


# Assembled so THIS file never contains the retired token as a literal
# substring -- writing it out would self-match and the test could never pass.
RETIRED = "check" + "mate"


def test_no_retired_gate_token_survivors():
    hits = []
    for p in ENGINE.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        if RETIRED in text:
            hits.append(str(p.relative_to(ENGINE)))
    assert not hits, f"stale {RETIRED!r} token in: {hits}"
