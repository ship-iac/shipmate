"""Engine `plan.yml`: the wiring a consumer's wrapper used to own.

Five `shipmate doctor` probes and four docs-snippet guards existed because a consumer wrapper
could wire a constant where a fact belongs -- `head-repo: ${{ github.repository }}` states the
safe answer for every run, fork pull requests included, and nothing else in the system can see
it. The wiring is engine YAML now, SHA-pinned and reviewed, so the checks move here.

Every assertion is a whole hand-written value against parsed output. A key-set test
passes an entry whose expression was mistyped; a substring test is satisfied by a comment.
"""

import re

from _loader import (
    ENGINE_ENVIRONMENT,
    WORKFLOWS,
    checkouts,
    local_action,
    secrets_refs,
    step_using,
    strings,
    workflow_jobs,
    workflow_yaml,
)

WF = WORKFLOWS / "plan.yml"


def test_the_workflow_call_inputs_are_exactly_these():
    """Mutation: re-declare `state_suffix: { required: true, type: string }`."""
    assert workflow_yaml(WF)["on"]["workflow_call"]["inputs"] == {
        "runs_on": {"required": False, "default": "ubuntu-26.04-arm", "type": "string"},
    }


def test_the_workflow_call_secrets_are_exactly_these():
    """`required: true` on any of the three would fail at load time: consumers scope the two
    engine keys to an environment rather than the repository, and a consumer who forwards no
    secrets of their own holds no `SHIPMATE_SECRETS` to pass at all."""
    assert workflow_yaml(WF)["on"]["workflow_call"]["secrets"] == {
        "SHIPMATE_APP_PRIVATE_KEY": {"required": False},
        "SHIPMATE_ENCRYPTION_KEY": {"required": False},
        "SHIPMATE_SECRETS": {"required": False},
    }


def test_every_job_declares_its_own_permissions():
    jobs = workflow_jobs(WF)
    assert {j: v.get("permissions") for j, v in jobs.items()} == {
        "claim": {},
        "facts": {"pull-requests": "read"},
        "defer": {},
        "defer-auto": {},
        "detect": {"contents": "read"},
        "plan": {"contents": "read", "id-token": "write"},
        "summary": {"contents": "read"},
        "gate-hold": {},
        "outcome": {},
    }


def test_facts_is_the_single_producer_of_every_pull_request_fact():
    """One producer, or two producers of one fact disagree eventually. Mutation: add a second
    `actions/pr-facts` step to `detect`. Mutation: drop the `head-ref` line from `facts`'s
    outputs. Mutation: drop the `base-ref` line from `facts`'s outputs."""
    producers = [
        job_id
        for job_id, job in workflow_jobs(WF).items()
        for s in (job.get("steps") or [])
        if "actions/pr-facts" in str(s.get("uses", ""))
    ]
    assert producers == ["facts"]
    assert workflow_jobs(WF)["facts"]["outputs"] == {
        "head-sha": "${{ steps.facts.outputs.head-sha }}",
        "head-repo": "${{ steps.facts.outputs.head-repo }}",
        "head-ref": "${{ steps.facts.outputs.head-ref }}",
        "base-sha": "${{ steps.facts.outputs.base-sha }}",
        "base-ref": "${{ steps.facts.outputs.base-ref }}",
        "pr-number": "${{ steps.facts.outputs.pr-number }}",
        "is-draft": "${{ steps.facts.outputs.is-draft }}",
        "on-demand": "${{ steps.facts.outputs.on-demand }}",
    }


def test_build_matrix_reads_the_facts_job_and_states_no_constant():
    """The half that matters more than the summary call's: a wrong value here passes the fork
    refusal itself. Mutation: `head-repo: ${{ github.repository }}`. Mutation: drop the
    `base-ref` line -- build-matrix refuses every plan."""
    assert step_using(WF, "detect", "actions/build-matrix")["with"] == {
        "base-sha": "${{ needs.facts.outputs.base-sha }}",
        "base-ref": "${{ needs.facts.outputs.base-ref }}",
        "head-repo": "${{ needs.facts.outputs.head-repo }}",
        "head-sha": "${{ needs.facts.outputs.head-sha }}",
        "github-vars": "${{ toJSON(vars) }}",
        "tools": "${{ steps.setup.outputs.tools }}",
    }


def test_the_plan_workflow_never_sets_sweep():
    """`sweep: "true"` skips build-matrix's head-repository and head-commit refusals.
    drift.yml is required to carry it; a plan workflow must never. Mutation: add the key."""
    for job in workflow_jobs(WF).values():
        for step in job.get("steps") or []:
            assert "sweep" not in (step.get("with") or {})


def test_every_checkout_takes_the_head_the_facts_job_named():
    """Both jobs that check out, every checkout compared whole and in order. plan-cell refuses a
    checkout that is not `expected-head`, so its cell and its checkout must agree; `detect` has
    no such refusal, and a `github.sha` there builds the matrix from base-branch content while
    build-matrix's own refusals still pass, because they read the facts job. `fetch-depth: 0` is
    load-bearing in `detect` only: without the full history `terramate list --changed` and
    `lost_env_tags`' `git diff` find nothing and report it as no change. `plan` diffs nothing.
    `filter: blob:none` keeps that history and fetches file contents on demand.
    `persist-credentials: false` keeps the job token out of the git config of a checkout whose
    pull request code `plan` then runs; `detect` runs none of it.

    Mutations: `ref: ${{ github.sha }}` on `detect`, the same on `plan`, `fetch-depth` deleted
    from `detect`, `filter` deleted from `detect`, `fetch-depth: 0` added to `plan`,
    `persist-credentials` deleted from `plan`, `persist-credentials: false` added to `detect`,
    and `expected-head: ${{ github.sha }}` on the cell.
    """
    head = "${{ needs.facts.outputs.head-sha }}"
    # PyYAML gives the int 0, not "0".
    expected = {
        "detect": {"ref": head, "fetch-depth": 0, "filter": "blob:none"},
        "plan": {"ref": head, "persist-credentials": False},
    }
    for job_id, with_block in expected.items():
        got = [s.get("with") for s in checkouts(workflow_jobs(WF)[job_id]["steps"])]
        assert got == [with_block], job_id
    cell = step_using(WF, "plan", "actions/plan-cell")
    assert cell["with"]["expected-head"] == head


def test_the_cell_binds_the_environment_detect_resolved():
    """Hand-written whole value. A literal env name here is the thing CLAUDE.md forbids
    outright. Mutation: replace it with a literal `dev-eu`.
    """
    assert workflow_jobs(WF)["plan"]["environment"] == "${{ matrix.env_binding }}"


def test_the_cell_job_name_is_the_cell_grammar():
    """`summary-comment` and `gate-state` name a plan cell's check by
    `apply-gate.plan_check_name`, `plan / <env> / <stack>`, which exists only while this
    job's `name:` renders `<env> / <stack>`. Mutation: swap the two matrix keys."""
    assert workflow_jobs(WF)["plan"]["name"] == "${{ matrix.environment }} / ${{ matrix.stack }}"


def test_the_cell_passes_this_whole_with_block():
    """The whole mapping against a hand-written constant, not the keys this file happens to
    reason about elsewhere: a key checked one at a time relocates the hole to whichever key is
    not named, and a dropped `with:` line reaches a composite action as the empty string rather
    than as an error. plan-cell refuses an empty `expected-head` and an empty
    `encryption-key`, but only once a run reaches the cell.

    Mutations: `encryption-key` deleted, and `expected-head: ${{ github.sha }}`.
    """
    assert step_using(WF, "plan", "actions/plan-cell")["with"] == {
        "tf-vars": "${{ toJSON(matrix.tf_vars) }}",
        "github-vars": "${{ toJSON(vars) }}",
        "consumer-secrets": "${{ secrets.SHIPMATE_SECRETS }}",
        "stack": "${{ matrix.stack }}",
        "env": "${{ matrix.environment }}",
        "expected-head": "${{ needs.facts.outputs.head-sha }}",
        "encryption-key": "${{ secrets.SHIPMATE_ENCRYPTION_KEY }}",
    }


def test_the_cell_job_references_exactly_these_secrets():
    """The plan job runs pull request code, so every secret it references reaches that code.
    Every string of the whole job is walked, and within each only its `${{ }}` expressions; the
    App key's absence is `test_cells_hold_no_app_key.py`'s.

    Mutations: a job-level `env: { K: ${{ secrets.SHIPMATE_VARS }} }`; a plan-cell input
    `${{ toJSON(secrets) }}`; a plan-cell input `${{ secrets['SHIPMATE_VARS'] }}`.
    """
    assert secrets_refs(workflow_jobs(WF)["plan"]) == [
        "secrets.SHIPMATE_ENCRYPTION_KEY",
        "secrets.SHIPMATE_SECRETS",
    ]


def test_the_workflow_reads_the_event_payload_nowhere():
    """`facts` is the single producer of every pull-request fact, and a fact read from the
    payload beside it is a second producer of the same fact. This file answers to whatever
    trigger its caller declares -- a `workflow_dispatch` run's payload carries no
    `github.event.pull_request` at all, so every expression reading it renders empty, and an
    empty checkout `ref` plans the dispatch ref while an empty `head-repo` refuses `summary`.
    Both fail toward a green gate over a pull request nobody planned.

    The shim owns the one read that has to exist -- the concurrency group, where `needs` is not
    in scope -- so this file's count is zero outside `claim`, `defer`, `defer-auto` and `outcome`,
    compared whole
    rather than per expression. `claim` runs before `facts` exists and reads the dispatch's own
    `comment_id`, which no pull request holds, and its `pr_number` only to address the
    not-started reply; under `pull_request_target` both read empty and the job skips. `outcome`
    reads `pr_number` only to address its reply when `facts` failed, and only for a run whose
    `claim` validated that number. `facts` and `outcome` read `comment_id` for the claim gate's
    bypass, which admits a run that carries none. `defer` and `defer-auto` read `action`, the
    trigger's activity type, which no pull request fact carries.

    Mutations: `env: { PR: ${{ github.event.pull_request.number }} }` on the `detect` job;
    `github.event.inputs.ref` added to `claim`'s `with:` or to `outcome`'s.
    """
    found = {
        job_id: sorted({m for s in strings(job) for m in re.findall(r"github\.event\.[\w.]*", s)})
        for job_id, job in workflow_jobs(WF).items()
    }
    assert found == {
        "claim": ["github.event.inputs.comment_id", "github.event.inputs.pr_number"],
        "facts": ["github.event.inputs.comment_id"],
        "defer": ["github.event.action"],
        "defer-auto": ["github.event.action"],
        "detect": [],
        "plan": [],
        "summary": [],
        "gate-hold": [],
        "outcome": ["github.event.inputs.comment_id", "github.event.inputs.pr_number"],
    }, f"plan.yml reads the event payload: {found}"


#: Both gated jobs' whole `if:`, hand-written rather than read back from the file, as
#: `test_engine_drift_workflow.py` pins drift's pair. A substring check over either survives
#: `||` -> `&&`, which keeps every word and inverts the gate.
_GATED_IF = {
    "detect": (
        "${{ !cancelled() && needs.facts.result == 'success' && needs.defer.outputs.run == '' && "
        "(needs.facts.outputs.is-draft == 'false' || needs.facts.outputs.on-demand == 'true') }}"
    ),
    "plan": (
        "${{ !cancelled() && needs.detect.result == 'success' && "
        "needs.detect.outputs.empty == 'false' }}"
    ),
}


def test_the_detect_and_plan_jobs_carry_exactly_these_gates():
    """Mutations, each reddening only its own case: drop `detect`'s `on-demand` clause, and a
    `shipmate plan` on a draft skips `detect` and `plan`, so the plan a person asked for never
    runs and gate-state writes a `failure` gate (a skipped detect under an on-demand run is not
    the draft branch); turn either `||` into `&&`;
    invert `plan`'s `empty` comparison, and a pull request with changed stacks plans no cell
    while the gate greens over it; replace either `!cancelled()` with nothing (the implicit
    `success()` reads every ancestor, so an autoplan's skipped `claim` would skip both); drop
    either `result == 'success'` clause (a failed `facts` or `detect` would start the next job);
    drop `detect`'s `needs.defer.outputs.run == ''` (a ready run plans the head an earlier
    autoplan published).

    `facts` and `summary` are absent by design: `test_claim_wiring.py` owns `facts`'s gate, and
    `test_summary_workflow_guards.py` owns `summary`'s. The job-id list there is what fails when
    a fourth gated job appears.
    """
    jobs = workflow_jobs(WF)
    assert {j: " ".join(jobs[j]["if"].split()) for j in _GATED_IF} == _GATED_IF


def test_detect_and_summary_wait_for_defer():
    """Mutations: drop `defer` from `detect`'s `needs`; drop it from `summary`'s (either
    then reads `needs.defer.outputs.run` as empty and plans)."""
    jobs = workflow_jobs(WF)
    assert {j: jobs[j]["needs"] for j in ("defer", "detect", "summary")} == {
        "defer": "facts",
        "detect": ["facts", "defer"],
        "summary": ["facts", "defer", "detect", "plan"],
    }


def test_the_defer_job_is_wired_exactly():
    """It holds the App key only for a head `summary` would admit, and only on a ready-for-review
    autoplan. Mutations: drop the fork clause (the key reaches a fork's ready run); drop the
    `ready_for_review` clause (every autoplan reads); drop the `is-draft` clause; pass
    `head-sha: ${{ github.sha }}`; drop the `auto` output (`defer-auto` reads it empty and starts
    on a published `no`)."""
    assert workflow_jobs(WF)["defer"] == {
        "needs": "facts",
        "if": (
            "${{ !cancelled() && github.event_name == 'pull_request_target' && "
            "github.event.action == 'ready_for_review' && needs.facts.result == 'success' && "
            "needs.facts.outputs.is-draft == 'false' && "
            "needs.facts.outputs.head-repo == github.repository }}"
        ),
        "runs-on": "ubuntu-slim",
        "environment": ENGINE_ENVIRONMENT,
        "permissions": {},
        "outputs": {
            "run": "${{ steps.defer.outputs.run }}",
            "auto": "${{ steps.defer.outputs.auto }}",
        },
        "steps": [
            {
                "id": "defer",
                "uses": local_action("plan-defer"),
                "with": {
                    "head-sha": "${{ needs.facts.outputs.head-sha }}",
                    "pr-number": "${{ needs.facts.outputs.pr-number }}",
                    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
                },
            }
        ],
    }


#: The comment above `defer-auto`'s `continue-on-error`, which the parsed file drops.
_DEFER_AUTO_WHY = """\
    # Only a failed `verify-app-key` or `python-yaml` step fails this job; a failed decision or
    # dispatch warns or replies and ends it `success`. `continue-on-error` keeps the run
    # `success` even then, which `_deferred_run` needs to read a deferred ready run past.
    continue-on-error: true
"""


def test_the_defer_auto_job_is_wired_exactly():
    """It holds the App key only on the first attempt of a same-repository ready-for-review
    autoplan whose `defer` named an earlier plan run, and it never fails the run.

    Mutations: drop `github.run_attempt == '1'` (a re-run dispatches again); drop the
    `github.event_name` clause; drop the head-repo clause (the key reaches a fork's ready run);
    change `needs.defer.outputs.run != ''` to `==`; drop `continue-on-error` (a failed dispatch
    fails the run, and `_deferred_run` stops reading it past); drop its comment; drop `detect`
    from `needs` (the auto run can read `detect` pending and the run as not deferred); compare
    `needs.defer.outputs.auto == 'yes'` (an empty or `unread` answer skips the decision); drop
    the `auto` clause (a published `no` still starts it); drop the `auto-change` input. The
    `!cancelled()` lead lets it run past the `detect` that a deferring run skips."""
    assert workflow_jobs(WF)["defer-auto"] == {
        "needs": ["facts", "defer", "detect"],
        "if": (
            "${{ !cancelled() && github.event_name == 'pull_request_target' && "
            "github.event.action == 'ready_for_review' && github.run_attempt == '1' && "
            "needs.facts.result == 'success' && needs.facts.outputs.is-draft == 'false' && "
            "needs.facts.outputs.head-repo == github.repository && "
            "needs.defer.result == 'success' && needs.defer.outputs.run != '' && "
            "needs.defer.outputs.auto != 'no' }}"
        ),
        "runs-on": "ubuntu-slim",
        "environment": ENGINE_ENVIRONMENT,
        "permissions": {},
        "continue-on-error": True,
        "steps": [
            {
                "uses": local_action("auto-dispatch"),
                "with": {
                    "deferred-to": "${{ needs.defer.outputs.run }}",
                    "head-sha": "${{ needs.facts.outputs.head-sha }}",
                    "pr-number": "${{ needs.facts.outputs.pr-number }}",
                    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
                    "github-vars": "${{ toJSON(vars) }}",
                    "auto-change": "${{ needs.defer.outputs.auto }}",
                },
            }
        ],
    }
    assert WF.read_text(encoding="utf-8").count(_DEFER_AUTO_WHY) == 1
