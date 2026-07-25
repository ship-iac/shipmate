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


def _gate_writing_run_blocks(text):
    """Yield each `run:` block (as a single string) that references the gate
    context AND posts (contains `--input`, a `gh api ... --input` call). A step
    that merely names the gate in a comment/echo does not count; the writer
    files each have exactly one such block, but this scans generically instead
    of assuming a fixed line layout."""
    blocks = []
    current = []
    in_run = False
    run_indent = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:") or stripped == "run: |":
            if current:
                blocks.append("\n".join(current))
            current = []
            in_run = True
            run_indent = len(line) - len(line.lstrip())
            continue
        if in_run:
            indent = len(line) - len(line.lstrip()) if line.strip() else run_indent + 1
            if line.strip() and indent <= run_indent:
                blocks.append("\n".join(current))
                current = []
                in_run = False
            else:
                current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [b for b in blocks if GATE in b and "--input" in b]


def test_gate_written_as_commit_status_not_check_run():
    """The gate must be a commit STATUS, not a check-run.

    A check-run binds to a check-suite; an imperatively-created one lands in an
    arbitrary suite when a commit carries two plan runs (draft->ready, or a
    rapid re-push). The merge evaluator reads the live suite, finds no gate, and
    blocks the PR forever while the green gate sits in the stale suite. A commit
    status is commit-scoped and immune. Lock every writer onto the statuses API.

    Scoped to the GATE-writing step(s) only (the block that references the
    `shipmate / gate` context and POSTs it) -- NOT every line in the writer
    file. `actions/summary` also legitimately creates apply check-runs in a
    separate step; that step must not be flagged by this guard.
    """
    for rel in WRITERS:
        text = (ENGINE / rel).read_text(encoding="utf-8")
        gate_blocks = _gate_writing_run_blocks(text)
        assert gate_blocks, f"{rel}: no gate-writing run block found (context+POST)"
        for block in gate_blocks:
            assert "statuses/" in block, (
                f"{rel}: gate-writing step must POST to the commit statuses API: {block!r}"
            )
            for line in block.splitlines():
                assert not ("check-runs" in line and "--input" in line), (
                    f"{rel}: gate POST still targets the check-runs API: {line.strip()}"
                )


WORKFLOWS = ENGINE / ".github" / "workflows"
GATE_WRITER_ACTIONS = ("actions/gate-refresh", "actions/summary")
CREDENTIALED_ACTIONS = (
    "actions/gate-refresh",
    "actions/summary",
    "actions/apply-cell",
    "actions/drift-cell",
    "actions/apply-summary",
)


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


def _inline_gate_steps(job):
    """Steps within a job that inline-POST the gate status (statuses/ + GATE
    in the same run block) -- the deploy.yml style writer, as opposed to a
    job that delegates to the gate-refresh/summary composite actions (which
    mint and use their own App token internally)."""
    return [
        step
        for step in (job.get("steps") or [])
        if "statuses/" in (step.get("run") or "") and GATE in (step.get("run") or "")
    ]


def _app_statuses_token_step_id(job):
    """The id of a step in the job that mints a shipmate App installation
    token scoped `permission-statuses: write`, or None if there is none."""
    for step in job.get("steps") or []:
        if "create-github-app-token" not in (step.get("uses") or ""):
            continue
        if (step.get("with") or {}).get("permission-statuses") == "write":
            return step.get("id")
    return None


def test_inline_gate_write_job_mints_app_statuses_token():
    """Every job that writes the gate INLINE (a `run:` step posting to the
    commit-statuses API with the `shipmate / gate` context, rather than
    delegating to the gate-refresh/summary composite actions) must mint its
    own shipmate App installation token scoped `permission-statuses: write`,
    and the inline POST step's `GH_TOKEN` must be that minted token -- never
    the ambient `github.token`, which cannot write a commit status pinned by
    `integration_id` to the shipmate App (see test_no_engine_job_grants_stale_
    checks_or_statuses_write for the GITHUB_TOKEN-scope side of this).

    Uses `_job_writes_gate` to locate candidate jobs (it detects exactly this:
    a gate-refresh/summary delegate OR an inline statuses/+GATE run step),
    then narrows to the inline-writer jobs specifically -- a job that merely
    delegates to gate-refresh/summary is skipped here, since those actions
    mint and scope their own token internally.
    """
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        for job_name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict) or not _job_writes_gate(job):
                continue
            inline_steps = _inline_gate_steps(job)
            if not inline_steps:
                continue  # delegates to gate-refresh/summary; not this guard's concern
            token_id = _app_statuses_token_step_id(job)
            if token_id is None:
                offenders.append(
                    f"{wf.name}:{job_name}: no create-github-app-token step scoped "
                    "permission-statuses: write"
                )
                continue
            expected = "${{ steps." + token_id + ".outputs.token }}"
            for step in inline_steps:
                got = (step.get("env") or {}).get("GH_TOKEN")
                if got != expected:
                    offenders.append(
                        f"{wf.name}:{job_name}: inline gate-POST step GH_TOKEN is "
                        f"{got!r}, expected the minted App token {expected!r}"
                    )
    assert not offenders, (
        f"inline gate-writing job(s) not using a minted App statuses token: {offenders}"
    )


def _grants_stale_perm(job, workflow_perms):
    # A job-level `permissions:` fully REPLACES the workflow default (GHA
    # semantics), so a job that sets any permissions must set the scope
    # itself; only a job that omits `permissions:` inherits the workflow block.
    perms = job.get("permissions", workflow_perms)
    if perms == "write-all":
        return True
    if not isinstance(perms, dict):
        return False
    return perms.get("checks") == "write" or perms.get("statuses") == "write"


def test_no_engine_job_grants_stale_checks_or_statuses_write():
    """No ENGINE workflow job grants `checks: write` or `statuses: write` on
    GITHUB_TOKEN any more.

    Those two GITHUB_TOKEN scopes are relics of the check-run/GITHUB_TOKEN era.
    Every writer that needs them now mints a shipmate App installation token
    instead (App manifest carries `checks: write` + `statuses: write`), so a
    job-level grant of either scope on GITHUB_TOKEN is stale and should be
    removed -- it is unused (the writer steps use the App token) and widens the
    default token's blast radius for no reason. The exception list is empty by
    design: if a genuine GITHUB_TOKEN need for one of these scopes turns up,
    that is a real design question, not something this guard should silently
    exempt.
    """
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
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
    # e.g. "ship-iac/shipmate/.github/workflows/apply-env-level.yml@<sha>" -> "apply-env-level.yml"
    path_part = uses.split("@", 1)[0]
    return path_part.rsplit("/", 1)[-1]


def _declares_app_private_key_secret(workflow_doc):
    on = workflow_doc.get("on") or workflow_doc.get(True) or {}
    wc = (on or {}).get("workflow_call") or {}
    secrets = wc.get("secrets") or {}
    return "SHIPMATE_APP_PRIVATE_KEY" in secrets


def _reusable_caller_offense(wf_name, job_name, job, workflow_docs):
    """Check one reusable-workflow-caller job; return an offense string, or
    None if credential-threading into the callee is sound."""
    target = _reusable_target_name(job.get("uses") or "")
    target_doc = workflow_docs.get(target)
    if target_doc is None:
        # Target lives outside this glob (shouldn't happen for the engine's
        # own reusable workflows) -- nothing to check here.
        return None
    if not _declares_app_private_key_secret(target_doc):
        # Flag any reusable target missing the secret declaration so a future
        # caller doesn't silently lose the credential thread.
        return (
            f"{wf_name}:{job_name} -> {target} missing "
            "SHIPMATE_APP_PRIVATE_KEY in on.workflow_call.secrets"
        )
    if job.get("secrets") != "inherit":
        return (
            f"{wf_name}:{job_name} -> {target}: not using `secrets: inherit` "
            "(explicit secret mappings must include SHIPMATE_APP_PRIVATE_KEY)"
        )
    return None


def _credentialed_step_offenses(wf_name, job_name, job):
    """Check every credentialed-action step in one non-reusable job; return
    a list of offense strings (empty if all such steps pass both creds)."""
    offenses = []
    for step in job.get("steps") or []:
        uses = step.get("uses") or ""
        if not any(action in uses for action in CREDENTIALED_ACTIONS):
            continue
        with_ = step.get("with") or {}
        missing = [k for k in ("app-id", "private-key") if k not in with_]
        if missing:
            offenses.append(f"{wf_name}:{job_name} ({uses}) missing with: {missing}")
    return offenses


def test_credentialed_action_steps_thread_app_credentials():
    """Every step calling gate-refresh/summary/apply-cell/drift-cell passes
    both `app-id` and `private-key` in its `with:` -- these actions each mint
    their own App installation token and 403 (or silently no-op) without both.

    For a job that is itself a reusable-workflow CALLER (`uses:` points at
    another `.github/workflows/*.yml` and relies on `secrets: inherit` to
    forward `SHIPMATE_APP_PRIVATE_KEY` down into it), the credential is
    threaded structurally rather than passed as a `with:` input -- assert
    instead that the CALLED workflow declares `SHIPMATE_APP_PRIVATE_KEY` under
    `on.workflow_call.secrets`, and that the caller uses `secrets: inherit`
    (not a selective mapping that could omit it).
    """
    offenders = []
    workflow_docs = {
        wf.name: yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        for wf in sorted(WORKFLOWS.glob("*.yml"))
    }
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
    "actions/apply-all-detect",
)


def _detect_step_offenses(wf_name, job_name, job):
    """Check every detect-action step in one job; return a list of offense
    strings (empty if each such step passes `app-id`)."""
    offenses = []
    for step in job.get("steps") or []:
        uses = step.get("uses") or ""
        if not any(action in uses for action in DETECT_ACTIONS):
            continue
        with_ = step.get("with") or {}
        if "app-id" not in with_:
            offenses.append(f"{wf_name}:{job_name} ({uses}) missing with: app-id")
    return offenses


def test_detect_action_steps_thread_app_id():
    """Every step calling deploy-detect/apply-detect/apply-all-detect passes
    `app-id` in its `with:` -- these scripts read `os.environ["SHIPMATE_APP_ID"]`
    and KeyError at runtime without it. Nothing else guards this threading, so a
    call-site dropping the input would only surface as a runtime crash."""
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        for job_name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            offenders.extend(_detect_step_offenses(wf.name, job_name, job))
    assert not offenders, f"detect action(s) missing app-id threading: {offenders}"


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
