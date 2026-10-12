"""Freeze the aggregate gate's context to one definition, and its writes to one script.

`_shipmate.GATE_CONTEXT` is the only place the `shipmate / gate` context is spelled as a value,
and `scripts/gate-status` is the only script that reads or writes the status. A second spelling
greens the gate on one path and leaves it stuck on another. `test_gate_status.py` pins the
whole argv of every write, the commit-statuses endpoint included.
"""

import ast

from _loader import (
    ACTIONS,
    ENGINE,
    ENGINE_CALL_SECRETS,
    SCRIPTS,
    WORKFLOWS,
    action_yaml,
    step_by,
    workflow_yaml,
)

GATE = "shipmate / gate"


def _gate_literals():
    """The file name of every string constant equal to the whole gate literal, once per
    occurrence, in the scripts directly under `scripts/` that parse as Python, extensionless
    helpers included."""
    found = []
    for path in sorted(SCRIPTS.glob("*")):
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == GATE:
                found.append(path.name)
    return sorted(found)


def test_the_gate_literal_is_defined_once():
    """Mutations: restore `GATE = "shipmate / gate"` in `scripts/doctor` -> `doctor` is listed; a
    second definition in `_shipmate.py` -> `_shipmate.py` is listed twice."""
    assert _gate_literals() == ["_shipmate.py"]


def _run_blocks():
    """(file, `run:` value) for every step in the composite actions and engine workflows."""
    for path in sorted(ACTIONS.glob("*/action.yml")):
        for step in action_yaml(path)["runs"].get("steps") or []:
            yield path.parent.name, step.get("run") or ""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in (workflow_yaml(path).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                yield path.name, step.get("run") or ""


def test_no_run_block_names_the_gate():
    """Every gate read and write goes through `scripts/gate-status`, so no shell body spells the
    context. Mutations: re-inline deploy.yml's status write; a `select(.context == "shipmate /
    gate")` read in a summary step."""
    offenders = [where for where, run in _run_blocks() if GATE in run]
    assert offenders == []


CREDENTIALED_ACTIONS = (
    "actions/gate-refresh",
    "actions/summary",
    "actions/apply-complete",
    "actions/apply-summary",
    "actions/drift-issues",
)
# Actions that will need the same credential-threading guard once they exist. Kept out of
# CREDENTIALED_ACTIONS, a bare substring allowlist with no existence check, so a not-yet-created
# action cannot sit there silently: see test_credentialed_actions_exist_or_are_pending.
PENDING_CREDENTIALED_ACTIONS = ()


def test_credentialed_actions_exist_or_are_pending():
    """A bare substring allowlist has no existence check of its own: a typo in an entry here,
    or in PENDING_CREDENTIALED_ACTIONS, would silently disable the credential-threading guard
    for every step that calls the real action, with nothing failing anywhere. Every live entry
    must resolve to a real action.yml, and every pending entry must not -- once it exists, move
    it up."""
    for action in CREDENTIALED_ACTIONS:
        path = ENGINE / action / "action.yml"
        assert path.is_file(), f"CREDENTIALED_ACTIONS entry {action!r} has no {path}"
    for action in PENDING_CREDENTIALED_ACTIONS:
        path = ENGINE / action / "action.yml"
        assert not path.is_file(), (
            f"PENDING_CREDENTIALED_ACTIONS entry {action!r} now has a real {path} "
            "-- move it into CREDENTIALED_ACTIONS"
        )


#: gate-refresh's mint, whole: the gate write needs `statuses: write`, the refresh's check scan
#: `checks: read`.
EXPECTED_GATE_MINT = {
    "client-id": "${{ inputs.app-id }}",
    "private-key": "${{ inputs.private-key }}",
    "owner": "${{ github.repository_owner }}",
    "repositories": "${{ github.event.repository.name }}",
    "permission-checks": "read",
    "permission-statuses": "write",
}


#: The gate write's whole env: `GH_TOKEN` is the minted token, never `github.token`.
EXPECTED_WRITE_ENV = {
    "GH_TOKEN": "${{ steps.token.outputs.token }}",
    "HEAD_SHA": "${{ inputs.head-sha }}",
    "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
    "DEPLOY_RESULTS": "${{ inputs.deploy-results }}",
    "HOLD_DESCRIPTION": "${{ inputs.hold-description }}",
}


def test_gate_refresh_mints_an_app_statuses_token():
    """The ambient `github.token` cannot write a commit status the ruleset pins by
    `integration_id` to the App, so the gate is written with the App token gate-refresh mints.
    test_no_engine_job_grants_stale_checks_or_statuses_write covers the GITHUB_TOKEN side.

    Mutations: drop `permission-statuses` from the mint; `GH_TOKEN: ${{ github.token }}`.
    """
    assert step_by("gate-refresh", name="Mint App installation token")["with"] == (
        EXPECTED_GATE_MINT
    )
    assert step_by("gate-refresh", name="Write the gate")["env"] == EXPECTED_WRITE_ENV


def _grants_stale_perm(job, workflow_perms):
    # A job-level `permissions:` fully replaces the workflow default, per GHA semantics, so a
    # job that sets any permissions must set the scope itself. Only a job that omits
    # `permissions:` inherits the workflow block.
    perms = job.get("permissions", workflow_perms)
    if perms == "write-all":
        return True
    if not isinstance(perms, dict):
        return False
    return perms.get("checks") == "write" or perms.get("statuses") == "write"


def test_no_engine_job_grants_stale_checks_or_statuses_write():
    """No engine workflow job grants `checks: write` or `statuses: write` on GITHUB_TOKEN.

    Every writer that needs those scopes mints a shipmate App installation token instead, and
    the App manifest carries `checks: write` and `statuses: write`. A job-level grant of either
    scope on GITHUB_TOKEN is therefore unused, because the writer steps use the App token, and
    widens the default token's blast radius for no reason. The exception list is empty by
    design: a genuine GITHUB_TOKEN need for one of these scopes is a design question, not
    something this guard should silently exempt.
    """
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        doc = workflow_yaml(wf)
        workflow_perms = doc.get("permissions")
        for name, job in (doc.get("jobs") or {}).items():
            if isinstance(job, dict) and _grants_stale_perm(job, workflow_perms):
                offenders.append(f"{wf.name}:{name}")
    assert not offenders, (
        f"job(s) still grant a stale GITHUB_TOKEN checks:write/statuses:write: {offenders}"
    )


def _is_reusable_caller(job):
    uses = job.get("uses") or ""
    return uses.startswith("ship-iac/shipmate/.github/workflows/") or (
        uses.startswith("./.github/workflows/")
    )


def _reusable_target_name(uses):
    # "ship-iac/shipmate/.github/workflows/apply-env-level.yml@<sha>" becomes
    # "apply-env-level.yml".
    path_part = uses.split("@", 1)[0]
    return path_part.rsplit("/", 1)[-1]


def _declares_app_private_key_secret(workflow_doc):
    on = workflow_doc.get("on") or {}
    wc = (on or {}).get("workflow_call") or {}
    secrets = wc.get("secrets") or {}
    return "SHIPMATE_APP_PRIVATE_KEY" in secrets


def test_app_key_secret_interface_is_never_required():
    """No `workflow_call` interface may declare `SHIPMATE_APP_PRIVATE_KEY` required.

    Consumers scope that key to the `shipmate-engine` environment (docs/github-app.md §5), and
    `secrets: inherit` cannot satisfy a required declaration from an environment: GHA rejects
    the call before any step of the first callee job that binds no environment, killing the
    whole run. The jobs that mint the App token bind the environment and read the key from
    there, so requiring it buys nothing and breaks the documented storage model.
    """
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        doc = workflow_yaml(wf)
        on = doc.get("on") or {}
        decl = ((on.get("workflow_call") or {}).get("secrets") or {}).get(
            "SHIPMATE_APP_PRIVATE_KEY"
        )
        if isinstance(decl, dict) and decl.get("required") is True:
            offenders.append(wf.name)
    assert not offenders, (
        f"workflow(s) declare SHIPMATE_APP_PRIVATE_KEY required, which an "
        f"environment-scoped consumer secret can never satisfy: {offenders}"
    )


def _reusable_caller_offense(wf_name, job_name, job, workflow_docs):
    """Check one reusable-workflow-caller job; return an offense string, or None if
    credential-threading into the callee is sound."""
    target = _reusable_target_name(job.get("uses") or "")
    target_doc = workflow_docs.get(target)
    if target_doc is None:
        # The target lives outside this glob, which should not happen for the engine's own
        # reusable workflows, so there is nothing to check here.
        return None
    if not _declares_app_private_key_secret(target_doc):
        # Flag any reusable target missing the secret declaration, so a future caller does
        # not silently lose the credential thread.
        return (
            f"{wf_name}:{job_name} -> {target} missing "
            "SHIPMATE_APP_PRIVATE_KEY in on.workflow_call.secrets"
        )
    expected = ENGINE_CALL_SECRETS.get(target)
    if expected is None:
        return (
            f"{wf_name}:{job_name} -> {target}: _loader.ENGINE_CALL_SECRETS holds no "
            "expected secrets block for this callee -- add its whole block there"
        )
    if job.get("secrets") != expected:
        return (
            f"{wf_name}:{job_name} -> {target}: must pass exactly {expected}; "
            f"got {job.get('secrets')!r}"
        )
    return None


def _never_runs(step):
    """`if: false` marks a step GitHub always skips, so it threads nothing at runtime.

    manifest-load.yml is built entirely of them: they exist so the runner parses each action's
    manifest while setting the job up, which happens before any `if:` is evaluated, and they
    deliberately carry no `with:`.
    """
    return step.get("if") is False


def _credentialed_step_offenses(wf_name, job_name, job):
    """Check every credentialed-action step in one non-reusable job; return a list of offense
    strings, empty when every such step passes both credentials."""
    offenses = []
    for step in job.get("steps") or []:
        uses = step.get("uses") or ""
        if _never_runs(step) or not any(action in uses for action in CREDENTIALED_ACTIONS):
            continue
        with_ = step.get("with") or {}
        missing = [k for k in ("app-id", "private-key") if k not in with_]
        if missing:
            offenses.append(f"{wf_name}:{job_name} ({uses}) missing with: {missing}")
    return offenses


def test_credentialed_action_steps_thread_app_credentials():
    """Every step calling an action in CREDENTIALED_ACTIONS -- gate-refresh, summary,
    apply-complete, apply-summary, drift-issues -- passes both `app-id` and `private-key` in
    its `with:`, because each mints its own App installation token and 403s, or silently no-ops,
    without both.

    A job that is itself a reusable-workflow caller (`uses:` points at another
    `.github/workflows/*.yml`) threads the credential through the `secrets:` block rather than a
    `with:` input, so the assertion there is that the called workflow declares
    `SHIPMATE_APP_PRIVATE_KEY` under `on.workflow_call.secrets` and that the caller passes the
    whole block `_loader.ENGINE_CALL_SECRETS` names for that callee.

    Never `secrets: inherit`, including on these engine-internal hops. The files sit in one
    organization but the run belongs to the consumer, and inherit is evaluated against the run:
    a cross-organization consumer's applies would execute and then strand their
    `apply / <stack> / <env>` checks pending forever, because `apply-env-level.yml`'s `complete`
    job would hold no key to complete them with.
    """
    offenders = []
    workflow_docs = {wf.name: workflow_yaml(wf) for wf in sorted(WORKFLOWS.glob("*.yml"))}
    for wf_name, doc in workflow_docs.items():
        for job_name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            if _is_reusable_caller(job):
                offense = _reusable_caller_offense(wf_name, job_name, job, workflow_docs)
                if offense:
                    offenders.append(offense)
            else:
                offenders.extend(_credentialed_step_offenses(wf_name, job_name, job))
    assert not offenders, f"credential-threading gap(s): {offenders}"


DETECT_ACTIONS = (
    "actions/deploy-detect",
    "actions/apply-detect",
    "actions/unlock-detect",
)


def _detect_step_offenses(wf_name, job_name, job):
    """Check every detect-action step in one job; return a list of offense strings, empty when
    each such step passes `app-id`."""
    offenses = []
    for step in job.get("steps") or []:
        uses = step.get("uses") or ""
        if _never_runs(step) or not any(action in uses for action in DETECT_ACTIONS):
            continue
        with_ = step.get("with") or {}
        if "app-id" not in with_:
            offenses.append(f"{wf_name}:{job_name} ({uses}) missing with: app-id")
    return offenses


def test_detect_action_steps_thread_app_id():
    """Every step calling a detect action passes `app-id` in
    its `with:`, because those scripts read `os.environ["SHIPMATE_APP_ID"]` and KeyError at
    runtime without it. Nothing else guards this threading, so a call site dropping the input
    would only surface as a runtime crash."""
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        doc = workflow_yaml(wf)
        for job_name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            offenders.extend(_detect_step_offenses(wf.name, job_name, job))
    assert not offenders, f"detect action(s) missing app-id threading: {offenders}"
