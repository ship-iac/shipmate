"""The cell actions run tofu over pull-request branch content (providers,
`external` data sources, modules), so they must hold no App key -- the
credentialed work lives in the trusted trailing jobs.

One parsed-assertion implementation covers all three: `apply-cell`, which runs
`tofu apply`; `drift-cell`, which runs `tofu plan` from a policy-free plan
environment reachable off any branch; and `plan-cell`, which runs `tofu plan`
over a pull request's head.

Every assertion is on the parsed action.yml. A raw-text form (`"private-key" not in text`,
`"create-github-app-token" not in text`) is vacuous twice over: a comment naming either one
satisfies it, and the way a cell would actually regain the key -- a direct
`secrets.SHIPMATE_APP_PRIVATE_KEY` reference in an `env:` value -- contains neither substring.
"""

import pytest
from _loader import (
    ENGINE_ENVIRONMENT,
    WORKFLOWS,
    action_steps,
    action_yaml,
    checkouts,
    secrets_refs,
    strings,
    workflow_jobs,
    workflow_yaml,
)

CELLS = ("apply-cell", "drift-cell", "plan-cell")

#: `uses:` repos that mint a GitHub App installation token. Matched on the repo part alone, so a
#: version bump or a SHA re-pin cannot slip past.
TOKEN_MINTERS = (
    "actions/create-github-app-token",
    "tibdex/github-app-token",
    "getsentry/action-github-app-token",
    "peter-murray/workflow-application-token-action",
)

APP_KEY_ENV = "SHIPMATE_APP_PRIVATE_KEY"

LEVEL = WORKFLOWS / "apply-env-level.yml"

PLAN_WF = WORKFLOWS / "plan.yml"
#: Every job in plan.yml that runs repository content. The key holders are absent: they hold
#: the key, and none checks anything out.
UNTRUSTED_PLAN_JOBS = ("facts", "detect", "plan")
#: Every job in plan.yml that references the App key, hand-written.
PLAN_KEY_HOLDERS = ("claim", "defer", "defer-auto", "summary", "gate-hold", "outcome")
#: Every job with steps in apply.yml that references the App key, hand-written. `run` passes it
#: to `apply-run.yml` and runs no step of its own.
APPLY_KEY_HOLDERS = ("queue", "outcome")


@pytest.mark.parametrize("action", CELLS)
def test_cell_declares_no_private_key_input(action):
    inputs = action_yaml(action).get("inputs") or {}
    assert "private-key" not in inputs, f"{action} inputs: {sorted(inputs)}"


@pytest.mark.parametrize("action", CELLS)
def test_cell_uses_no_token_minting_action(action):
    steps = action_steps(action)
    assert steps, f"{action} parsed to zero steps -- this guard would assert nothing"
    for step in steps:
        repo = str(step.get("uses", "")).split("@")[0].strip().lower()
        assert repo not in TOKEN_MINTERS, f"{action} step {step.get('name')!r} uses {repo}"


@pytest.mark.parametrize("action", CELLS)
def test_cell_never_references_the_app_private_key(action):
    steps = action_steps(action)
    assert steps, f"{action} parsed to zero steps -- this guard would assert nothing"
    for text in strings(steps):
        assert APP_KEY_ENV not in text, f"{action} references {APP_KEY_ENV}: {text!r}"


def test_every_level_job_passes_or_reads_the_app_key():
    """`snapshot`, the releases and `complete` read it, none checking out repository content;
    every wave passes it to `cell.yml`, whose `apply` reads it only to refuse
    (`test_the_cell_workflow_reaches_the_app_key_only_in_these_steps`).

    Mutations: remove `snapshot`'s `private-key` wiring; remove `wave2-release`'s.
    """
    reading = {
        n
        for n, job in workflow_jobs(LEVEL).items()
        if f"secrets.{APP_KEY_ENV}" in secrets_refs(job)
    }
    assert reading == {
        "snapshot",
        "complete",
        *(f"wave{n}-release" for n in range(8)),
        *(f"wave{n}" for n in range(8)),
    }


#: The only steps of `cell.yml` that reference the App key, by job: `apply`'s refusal, which
#: reads it only to fail when it arrives, and `save`'s check write.
CELL_KEY_STEPS = {
    "apply": ["Refuse an apply job that holds the App key"],
    "save": ["Complete this cell's apply check"],
}


def test_the_cell_workflow_reaches_the_app_key_only_in_these_steps():
    """`cell.yml` declares the key optional, and every other step of either job, `apply`'s
    pull-request code included, names it nowhere. Mutations: pass the key to the apply-cell
    step's `with:`; add it to the `Pre-apply check` step's `env:`; declare it
    `required: true`; drop the refusal step's `env:`."""
    doc = workflow_yaml("cell.yml")
    found = {
        name: [s.get("name") for s in job["steps"] if f"secrets.{APP_KEY_ENV}" in secrets_refs(s)]
        for name, job in doc["jobs"].items()
    }
    declared = doc["on"]["workflow_call"]["secrets"][APP_KEY_ENV]
    assert (found, declared) == (CELL_KEY_STEPS, {"required": False})


@pytest.mark.parametrize("job_id", UNTRUSTED_PLAN_JOBS)
def test_plan_workflow_untrusted_jobs_never_reach_the_app_key(job_id):
    """plan.yml declares the App key so its `summary` job can mint. Declaring it puts the secret
    in the file's scope, and the three untrusted jobs must not reference it -- by an `env:`
    value, a `with:` value or a `run:` body. Two of them check out and execute pull-request
    content; `facts` checks nothing out, but it runs on the same untrusted side of the file.

    Mutation: add `env: { K: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }} }` to the `plan` job.
    """
    job = workflow_jobs(PLAN_WF)[job_id]
    hits = [s for s in strings(job) if APP_KEY_ENV in s]
    assert not hits, f"plan.yml job `{job_id}` references the App key: {hits}"


def test_plan_workflow_key_holders_are_exactly_these_and_check_out_nothing():
    """Under `pull_request_target` a checkout takes the pull request head, so a key holder that
    checks out runs fork-authored or branch content beside the key. Mutations: add an
    `actions/checkout` step to `defer`; pass the key to `detect`."""
    jobs = workflow_jobs(PLAN_WF)
    holders = tuple(n for n, j in jobs.items() if any(APP_KEY_ENV in s for s in strings(j)))
    assert holders == PLAN_KEY_HOLDERS
    assert {n: checkouts(jobs[n]["steps"]) for n in holders} == dict.fromkeys(holders, [])


def test_apply_workflow_key_holders_are_exactly_these_and_check_out_nothing():
    """A key holder that checks out runs the dispatched ref's content beside the key. Mutations:
    add an `actions/checkout` step to `queue`; pass the key to `guard`."""
    jobs = workflow_jobs(WORKFLOWS / "apply.yml")
    holders = tuple(
        n for n, j in jobs.items() if "steps" in j and any(APP_KEY_ENV in s for s in strings(j))
    )
    assert holders == APPLY_KEY_HOLDERS
    assert {n: checkouts(jobs[n]["steps"]) for n in holders} == dict.fromkeys(holders, [])


@pytest.mark.parametrize("job_id", UNTRUSTED_PLAN_JOBS)
def test_plan_workflow_untrusted_jobs_bind_no_engine_environment(job_id):
    """`shipmate-engine` alone, not every credentialed environment: the `plan` job binds a shared
    bare `<env>` whenever its table entry sets `split: false`, which is the documented shared
    trade-off. `shipmate-engine` holds the App key and its branch policy trusts the
    base ref -- exactly the ref `pull_request_target` runs at -- so a job that binds it and checks
    out the pull request head is the canonical pull_request_target vulnerability.

    Mutation: set `environment: shipmate-engine` on the `detect` job.
    """
    job = workflow_jobs(PLAN_WF)[job_id]
    assert "shipmate-engine" not in str(job.get("environment", ""))


def test_snapshot_release_and_complete_jobs_are_the_only_level_jobs_naming_the_engine_env():
    """Mutations: drop `snapshot`'s `environment:` binding; drop `complete`'s; drop
    `wave1-release`'s."""
    # Parsed, not a `text.count(...) == 1` textual check: that form is satisfied by any single
    # job naming the environment, so moving it onto wave0, which runs tofu over branch content,
    # would keep the count at 1 and the test green while releasing the key to consumer code.
    # Assert the specific job, not the count.
    named = {
        n for n, j in workflow_jobs(LEVEL).items() if "shipmate-engine" in str(j.get("environment"))
    }
    assert named == {"snapshot", "complete", *(f"wave{n}-release" for n in range(8))}


#: Every (workflow, job) binding `shipmate-engine`, hand-written. Each holds the App key.
ENGINE_BOUND_JOBS = {
    ("apply-env-level.yml", "snapshot"),
    *(("apply-env-level.yml", f"wave{n}-release") for n in range(8)),
    ("apply-env-level.yml", "complete"),
    ("cell.yml", "save"),
    ("apply-run.yml", "review"),
    ("apply-run.yml", "waiters"),
    ("apply-run.yml", "lock"),
    ("apply-run.yml", "unflip"),
    ("apply-run.yml", "summary"),
    ("apply.yml", "queue"),
    ("apply.yml", "outcome"),
    ("comment-ops.yml", "ops"),
    ("deploy.yml", "summary"),
    ("deploy.yml", "replan"),
    ("drift.yml", "issues"),
    ("plan.yml", "claim"),
    ("plan.yml", "defer"),
    ("plan.yml", "defer-auto"),
    ("plan.yml", "summary"),
    ("plan.yml", "gate-hold"),
    ("plan.yml", "outcome"),
    ("release.yml", "release"),
}


def test_exactly_these_jobs_bind_the_engine_environment_and_create_no_deployment():
    """Whole map over every engine workflow, so a new job holding the App key is a decision this
    test is edited for. Each binding is the whole `ENGINE_ENVIRONMENT` mapping: a plain
    `environment: shipmate-engine` creates a deployment on every run.

    Mutations: bind one more job; revert one binding to the plain string; set `deployment: true`.
    """
    bound = {
        (wf.name, n): j["environment"]
        for wf in sorted(WORKFLOWS.glob("*.yml"))
        for n, j in workflow_jobs(wf).items()
        if "shipmate-engine" in str(j.get("environment"))
    }
    assert bound == dict.fromkeys(ENGINE_BOUND_JOBS, ENGINE_ENVIRONMENT)
