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
import yaml
from _loader import ENGINE_ENVIRONMENT, WORKFLOWS, action_steps, action_yaml, strings, workflow_jobs

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
#: Every job in plan.yml that runs repository content. `summary` and `gate-hold` are
#: deliberately absent: they hold the key, and neither checks anything out.
UNTRUSTED_PLAN_JOBS = ("facts", "detect", "plan")


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


def test_only_the_completer_job_reads_the_app_key():
    # Parse, do not string-split: the secret stays declared at the top of the file, because the
    # complete job consumes it and test_gate_name_consistency requires reusable targets called
    # with `secrets: inherit` to declare it. A textual "not in" assertion checks the wrong thing.
    for name, job in workflow_jobs(LEVEL).items():
        body = yaml.safe_dump(job)
        if name == "complete":
            assert APP_KEY_ENV in body
        else:
            assert APP_KEY_ENV not in body, name


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


def test_the_completer_is_the_only_job_naming_the_engine_environment():
    # Parsed, not a `text.count(...) == 1` textual check: that form is satisfied by any single
    # job naming the environment, so moving it onto wave0, which runs tofu over branch content,
    # would keep the count at 1 and the test green while releasing the key to consumer code.
    # Assert the specific job, not the count.
    named = {
        n for n, j in workflow_jobs(LEVEL).items() if "shipmate-engine" in str(j.get("environment"))
    }
    assert named == {"complete"}


#: Every (workflow, job) binding `shipmate-engine`, hand-written. Each holds the App key.
ENGINE_BOUND_JOBS = {
    ("apply-env-level.yml", "complete"),
    ("apply.yml", "review"),
    ("apply.yml", "summary"),
    ("comment-ops.yml", "ops"),
    ("deploy.yml", "summary"),
    ("drift.yml", "issues"),
    ("plan.yml", "summary"),
    ("plan.yml", "gate-hold"),
}


def test_exactly_these_jobs_bind_the_engine_environment_and_create_no_deployment():
    """Whole map over every engine workflow, so a new job holding the App key is a decision this
    test is edited for. Each binding is the whole `ENGINE_ENVIRONMENT` mapping: a plain
    `environment: shipmate-engine` creates a deployment on every run.

    Mutations: bind a ninth job; revert one binding to the plain string; set `deployment: true`.
    """
    bound = {
        (wf.name, n): j["environment"]
        for wf in sorted(WORKFLOWS.glob("*.yml"))
        for n, j in workflow_jobs(wf).items()
        if "shipmate-engine" in str(j.get("environment"))
    }
    assert bound == dict.fromkeys(ENGINE_BOUND_JOBS, ENGINE_ENVIRONMENT)
