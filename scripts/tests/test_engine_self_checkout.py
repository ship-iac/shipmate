"""Every engine action an engine job runs comes from the engine checked out at the commit that
defines the job, and from nowhere else.

`job.workflow_sha` names the reusable workflow's own commit, so the checkout below is the engine
at exactly the SHA the consumer pinned. A `ship-iac/shipmate/<path>@<sha>` reference would
reintroduce a second commit into the run and, with it, the pin cascade this replaced.
"""

import re

import yaml
from _loader import (
    ACTIONS,
    ENGINE,
    ENGINE_CHECKOUT_WITH,
    ENGINE_DIR,
    SETUP_EXCLUDE_RUN,
    WORKFLOWS,
    local_action,
)

MANIFEST_LOAD = "manifest-load.yml"
CHECKOUT = "actions/checkout"
LOCAL_PREFIX = f"./{ENGINE_DIR}/actions/"
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


def _steps():
    """(where, step) for every step of every workflow but the manifest probe, then of every
    composite action."""
    for name, doc in _docs():
        if name == MANIFEST_LOAD:
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


def test_every_checkout_of_another_repository_is_the_engine_checkout():
    """Whole `with:` block. Mutations: drop `persist-credentials`; `ref: main`; `path: engine`."""
    for name, doc in _docs():
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                with_ = step.get("with") or {}
                if _uses(step).split("@")[0] == CHECKOUT and "repository" in with_:
                    assert with_ == ENGINE_CHECKOUT_WITH, f"{name}:{job_name}: {with_}"


def test_every_job_running_a_local_engine_action_checks_the_engine_out_first():
    """Mutations: delete the engine checkout from `complete` in apply-env-level.yml; move it
    below the first local step; add a second one. The count reddens on a job losing its local
    steps, and on the `*.yml` glob in `_docs` becoming `*.yaml`, which matches nothing and
    leaves every assertion below unreached."""
    covered = 0
    for name, doc in _docs():
        if name == MANIFEST_LOAD:
            continue
        for job_name, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            local = [i for i, s in enumerate(steps) if _uses(s).startswith(LOCAL_PREFIX)]
            if not local:
                continue
            covered += 1
            engine = [
                i
                for i, s in enumerate(steps)
                if _uses(s).split("@")[0] == CHECKOUT and "repository" in (s.get("with") or {})
            ]
            assert len(engine) == 1, f"{name}:{job_name}: {len(engine)} engine checkouts"
            assert engine[0] < local[0], f"{name}:{job_name}: local step before engine checkout"
    # Hand-written, never derived: every assertion above is inside the loop, so a glob that
    # matches nothing passes this and the four tests around it while checking nothing.
    assert covered == 25, f"{covered} jobs run a local engine action"


def test_a_consumer_checkout_precedes_the_engine_checkout():
    """The workspace-root checkout deletes what is already there when the directory is not that
    repository, so an engine checkout placed first is wiped. Mutation: swap the two checkouts
    in any wave job."""
    for name, doc in _docs():
        for job_name, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            checkouts = [
                (i, "repository" in (s.get("with") or {}))
                for i, s in enumerate(steps)
                if _uses(s).split("@")[0] == CHECKOUT
            ]
            if len(checkouts) == 2:
                assert [is_engine for _, is_engine in checkouts] == [False, True], (
                    f"{name}:{job_name}: engine checkout must follow the consumer checkout"
                )


def test_nested_reusable_calls_are_local():
    """Mutation: `uses: ship-iac/shipmate/.github/workflows/apply-env-level.yml@main`."""
    for name, doc in _docs():
        for job_name, job in (doc.get("jobs") or {}).items():
            if "uses" in job:
                assert job["uses"].startswith("./.github/workflows/"), (
                    f"{name}:{job_name}: {job['uses']}"
                )


def test_composite_actions_reach_state_through_the_local_path_only():
    """Mutation: `uses: ./actions/state` in plan-cell; or glob `*/action.yaml`, which matches
    nothing and trips ACTION_COUNT."""
    manifests = sorted(ACTIONS.glob("*/action.yml"))
    assert len(manifests) == ACTION_COUNT, f"{len(manifests)} action manifests"
    local = set()
    for path in manifests:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in doc["runs"].get("steps") or []:
            if _uses(step).startswith("./"):
                local.add(_uses(step))
    assert local == {local_action("state")}


def test_setup_hides_the_engine_directory_from_the_consumers_git():
    """Whole run string. Mutation: rename the directory in the exclude line."""
    doc = yaml.safe_load((ACTIONS / "setup" / "action.yml").read_text(encoding="utf-8"))
    runs = [s["run"] for s in doc["runs"]["steps"] if "info/exclude" in str(s.get("run", ""))]
    assert runs == [SETUP_EXCLUDE_RUN]
