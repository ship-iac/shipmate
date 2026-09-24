"""Every engine action an engine job runs resolves through `$/` to the commit that defines the
job, and from nowhere else.

A `ship-iac/shipmate/<path>@<sha>` step would reintroduce a second commit into the run and, with
it, the pin cascade this replaced. A `./` step would resolve in the consumer's workspace.
"""

import re

import yaml
from _loader import (
    ACTIONS,
    ENGINE,
    WORKFLOWS,
    local_action,
)

MANIFEST_LOAD = "manifest-load.yml"
CHECKOUT = "actions/checkout"
LOCAL_PREFIX = "$/actions/"
REMOTE_PREFIX = "ship-iac/shipmate/"
#: Composite actions today. Hand-written: the two tests globbing `actions/*/action.yml` assert
#: nothing at all if that glob matches nothing.
ACTION_COUNT = 20
SHA_PIN = re.compile(r"ship-iac/shipmate/[^@\s'\"]+@[0-9a-f]{40}")


def _docs():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        yield path.name, yaml.safe_load(path.read_text(encoding="utf-8"))


def _uses(step):
    return str(step.get("uses", ""))


def test_no_engine_reference_is_pinned_by_sha():
    """Mutation: put `ship-iac/shipmate/actions/setup@` + 40 hex back into any workflow or
    action.yml; or glob `*/action.yaml`, which matches nothing and trips ACTION_COUNT."""
    manifests = sorted(ACTIONS.glob("*/action.yml"))
    assert len(manifests) == ACTION_COUNT, f"{len(manifests)} action manifests"
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")) + manifests:
        for m in SHA_PIN.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(ENGINE).as_posix()}: {m.group(0)[:64]}")
    assert offenders == [], "\n".join(offenders)


def _steps(skip=MANIFEST_LOAD):
    """(where, step) for every step of every workflow but `skip`, then of every composite
    action."""
    for name, doc in _docs():
        if name == skip:
            continue
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                yield f"{name}:{job_name}", step
    for path in sorted(ACTIONS.glob("*/action.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in doc["runs"].get("steps") or []:
            yield path.relative_to(ENGINE).as_posix(), step


def test_no_step_outside_the_manifest_probe_reaches_the_engine_remotely():
    """The SHA scan above reads only 40-hex pins, and a step can name the engine by branch or
    tag instead. Mutation: `uses: ship-iac/shipmate/actions/setup@main` in plan.yml."""
    offenders = [
        f"{where}: {_uses(step)}"
        for where, step in _steps()
        if _uses(step).startswith(REMOTE_PREFIX)
    ]
    assert offenders == [], "\n".join(offenders)


def test_no_step_checks_out_another_repository():
    """`$/` fetches the engine itself, so no job checks it out. Mutation: restore the engine
    checkout (`repository: ship-iac/shipmate`) to the `complete` job of apply-env-level.yml."""
    offenders = [
        f"{where}: {step['with']}"
        for where, step in _steps(skip=None)
        if _uses(step).split("@")[0] == CHECKOUT and "repository" in (step.get("with") or {})
    ]
    assert offenders == [], "\n".join(offenders)


def test_no_step_resolves_in_the_consumer_workspace():
    """A `./` step resolves against the workspace, which holds the consumer's checkout, not the
    engine. Job-level nested workflow calls are not steps and stay `./`. Mutations:
    `uses: ./actions/setup` in plan.yml's `plan` job; `uses: ./actions/state` in plan-cell."""
    offenders = [
        f"{where}: {_uses(step)}"
        for where, step in _steps(skip=None)
        if _uses(step).startswith("./")
    ]
    assert offenders == [], "\n".join(offenders)


def test_every_job_running_an_engine_action_is_counted():
    """Hand-written, never derived: the tests around this one assert inside loops, so a glob that
    matches nothing passes them while checking nothing. Mutations: the `_docs` glob as `*.yaml`;
    every `$/` step deleted from unlock.yml's `detect` job."""
    covered = [
        f"{name}:{job_name}"
        for name, doc in _docs()
        if name != MANIFEST_LOAD
        for job_name, job in (doc.get("jobs") or {}).items()
        if any(_uses(s).startswith(LOCAL_PREFIX) for s in job.get("steps") or [])
    ]
    assert len(covered) == 25, f"{len(covered)} jobs run an engine action: {covered}"


#: The steps that run an engine action calling `scripts/doctor`, and so must hand it the
#: engine's own slug. Hand-written whole-set, never derived from the workflows.
DOCTOR_STEPS = {
    "comment-ops.yml:ops:" + local_action("comment-ops"),
    "plan.yml:summary:" + local_action("summary"),
}
DOCTOR_ACTIONS = {local_action("comment-ops"), local_action("summary")}
ENGINE_REPO_ENV = {"SHIPMATE_ENGINE_REPO": "${{ job.workflow_repository }}"}


def test_the_doctor_steps_pass_the_engine_repository():
    """`scripts/doctor`'s pin probe reads `SHIPMATE_ENGINE_REPO`; a composite action cannot read
    the `job` context, and the `job` context is unavailable in a job-level `env:` -- GitHub
    rejects such a workflow file at load, measured on this branch. The calling step carries it
    instead, and the composite's `run` steps inherit it. Both sets are compared whole, so a step
    that gains a doctor action without the env entry, or keeps the entry after losing the action,
    reddens. Mutation: delete the `env:` block from plan.yml's summary step."""
    runs_doctor, carries_env = set(), set()
    for name, doc in _docs():
        if name == MANIFEST_LOAD:
            continue
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                where = f"{name}:{job_name}:{_uses(step)}"
                if _uses(step) in DOCTOR_ACTIONS:
                    runs_doctor.add(where)
                if (step.get("env") or {}) == ENGINE_REPO_ENV:
                    carries_env.add(where)
    assert runs_doctor == DOCTOR_STEPS
    assert carries_env == DOCTOR_STEPS


def test_nested_reusable_calls_are_local():
    """Mutation: `uses: ship-iac/shipmate/.github/workflows/apply-env-level.yml@main`."""
    for name, doc in _docs():
        for job_name, job in (doc.get("jobs") or {}).items():
            if "uses" in job:
                assert job["uses"].startswith("./.github/workflows/"), (
                    f"{name}:{job_name}: {job['uses']}"
                )


def test_composite_actions_reach_state_through_the_local_path_only():
    """Mutation: `uses: $/actions/stat` in plan-cell; or glob `*/action.yaml`, which matches
    nothing and trips ACTION_COUNT."""
    manifests = sorted(ACTIONS.glob("*/action.yml"))
    assert len(manifests) == ACTION_COUNT, f"{len(manifests)} action manifests"
    local = set()
    for path in manifests:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in doc["runs"].get("steps") or []:
            if _uses(step).startswith("$/"):
                local.add(_uses(step))
    assert local == {local_action("state")}


#: Every engine action referenced from a workflow step or a composite action step, hand-written
#: rather than derived from the files under test.
LOCAL_ACTIONS = {
    "apply-all-detect",
    "apply-cell",
    "apply-complete",
    "apply-detect",
    "apply-snapshot",
    "apply-summary",
    "build-matrix",
    "comment-ops",
    "deploy-detect",
    "dispatch",
    "drift-cell",
    "drift-issues",
    "gate-refresh",
    "plan-cell",
    "pr-facts",
    "setup",
    "state",
    "summary",
    "unlock-cell",
    "verify-environments",
}


def test_every_local_action_reference_names_an_action_that_exists():
    """The whole set of referenced names, and each one's manifest. A `uses:` naming a directory
    that is not there fails only at run time, in the job that needed it. Mutation: add a step
    `uses: $/actions/stat` to any workflow."""
    referenced = {
        _uses(step)[len(LOCAL_PREFIX) :]
        for _, step in _steps()
        if _uses(step).startswith(LOCAL_PREFIX)
    }
    assert referenced == LOCAL_ACTIONS
    missing = [n for n in sorted(LOCAL_ACTIONS) if not (ACTIONS / n / "action.yml").is_file()]
    assert missing == [], missing
