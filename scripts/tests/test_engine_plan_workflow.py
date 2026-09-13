"""Engine `plan.yml`: the wiring a consumer's wrapper used to own.

Five `shipmate doctor` probes and four docs-snippet guards existed because a consumer wrapper
could wire a constant where a fact belongs -- `head-repo: ${{ github.repository }}` states the
safe answer for every run, fork pull requests included, and nothing else in the system can see
it. The wiring is engine YAML now, SHA-pinned and reviewed, so the checks move here.

Every assertion is a whole hand-written value against `yaml.safe_load` output. A key-set test
passes an entry whose expression was mistyped; a substring test is satisfied by a comment.
"""

import re

import yaml
from _loader import WORKFLOWS

WF = WORKFLOWS / "plan.yml"


def _doc():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _job(job_id):
    return _doc()["jobs"][job_id]


def _step(job_id, needle):
    steps = _job(job_id)["steps"]
    hits = [s for s in steps if needle in str(s.get("uses", ""))]
    assert len(hits) == 1, f"{job_id} has {len(hits)} steps using {needle}"
    return hits[0]


def test_the_workflow_call_inputs_are_exactly_these():
    """A `default:` on `state_suffix` is what makes a caller that stops passing it silent: the
    local-backend flavors would plan against no restored state, so every existing resource reads
    as absent and the cell plans a full create. `runs_on` is the deliberate inverse."""
    # `doc[True]` is not a typo: PyYAML parses the bare key `on:` as the boolean True.
    assert _doc()[True]["workflow_call"]["inputs"] == {
        "state_suffix": {"required": True, "type": "string"},
        "runs_on": {"required": False, "default": "ubuntu-latest", "type": "string"},
    }


def test_the_workflow_call_secrets_are_exactly_these():
    """`required: true` on either would fail at load time for every consumer who scopes the key
    to an environment rather than the repository."""
    assert _doc()[True]["workflow_call"]["secrets"] == {
        "SHIPMATE_APP_PRIVATE_KEY": {"required": False},
        "SHIPMATE_PLAN_PASSPHRASE": {"required": False},
    }


def test_the_workflow_permissions_floor_is_empty():
    """Mutation: `permissions: { contents: read }` at workflow level. A job that then loses its
    own block silently inherits instead of getting nothing."""
    assert _doc()["permissions"] == {}


def test_every_job_declares_its_own_permissions():
    jobs = _doc()["jobs"]
    assert {j: v.get("permissions") for j, v in jobs.items()} == {
        "facts": {"pull-requests": "read"},
        "detect": {"contents": "read"},
        "plan": {"contents": "read", "id-token": "write"},
        "summary": {"contents": "read"},
    }


def test_facts_is_the_single_producer_of_every_pull_request_fact():
    """One producer, or two producers of one fact disagree eventually. Mutation: add a second
    `actions/pr-facts` step to `detect`."""
    doc = _doc()
    producers = [
        job_id
        for job_id, job in doc["jobs"].items()
        for s in (job.get("steps") or [])
        if "actions/pr-facts@" in str(s.get("uses", ""))
    ]
    assert producers == ["facts"]
    assert _job("facts")["outputs"] == {
        "head-sha": "${{ steps.facts.outputs.head-sha }}",
        "head-repo": "${{ steps.facts.outputs.head-repo }}",
        "base-sha": "${{ steps.facts.outputs.base-sha }}",
        "pr-number": "${{ steps.facts.outputs.pr-number }}",
        "is-draft": "${{ steps.facts.outputs.is-draft }}",
        "on-demand": "${{ steps.facts.outputs.on-demand }}",
    }


def test_build_matrix_reads_the_facts_job_and_states_no_constant():
    """The half that matters more than the summary call's: a wrong value here passes the fork
    refusal itself. Mutation: `head-repo: ${{ github.repository }}`."""
    assert _step("detect", "actions/build-matrix@")["with"] == {
        "base-sha": "${{ needs.facts.outputs.base-sha }}",
        "head-repo": "${{ needs.facts.outputs.head-repo }}",
        "head-sha": "${{ needs.facts.outputs.head-sha }}",
    }


def test_the_plan_workflow_never_sets_no_pull_request():
    """`no-pull-request: "true"` skips build-matrix's head-repository and head-commit refusals.
    drift.yml is required to carry it; a plan workflow must never. Mutation: add the key."""
    for job in _doc()["jobs"].values():
        for step in job.get("steps") or []:
            assert "no-pull-request" not in (step.get("with") or {})


def test_every_checkout_takes_the_head_the_facts_job_named():
    """Both jobs that check out, compared whole. plan-cell refuses a checkout that is not
    `expected-head`, so its cell and its checkout must agree; `detect` has no such refusal, and a
    `github.sha` there builds the matrix from base-branch content while build-matrix's own
    refusals still pass, because they read the facts job. `fetch-depth: 0` is load-bearing in
    both: without the full history `terramate list --changed` finds nothing and reports it as no
    change.

    Mutations: `ref: ${{ github.sha }}` on `detect`, the same on `plan`, `fetch-depth` deleted
    from each, and `expected-head: ${{ github.sha }}` on the cell.
    """
    # PyYAML gives the int 0, not "0".
    expected = {"ref": "${{ needs.facts.outputs.head-sha }}", "fetch-depth": 0}
    for job_id in ("detect", "plan"):
        assert _step(job_id, "actions/checkout@")["with"] == expected, job_id
    cell = _step("plan", "actions/plan-cell@")
    assert cell["with"]["expected-head"] == "${{ needs.facts.outputs.head-sha }}"


def test_the_cell_binds_the_shared_or_plan_environment_from_the_repository_variable():
    """Hand-written whole expression, whitespace-collapsed. `-apply` here would hand a plan the
    apply role; a literal env name here is the thing CLAUDE.md forbids outright.

    Mutations: `-plan` -> `-apply`, and the whole expression replaced by a literal `dev-eu`.
    """
    expected = (
        "${{ contains(format(',{0},', vars.SHIPMATE_SHARED_ENVS), "
        "format(',{0},', matrix.environment)) && matrix.environment "
        "|| format('{0}-plan', matrix.environment) }}"
    )
    assert " ".join(_job("plan")["environment"].split()) == expected


def test_the_cell_binds_the_legacy_variables_it_injects_from():
    """The three flavor variables still reach the cell unconditionally, as the apply path does;
    they take the renamed route now, read by `scripts/env-inject` and written to `$GITHUB_ENV`
    rather than named directly here. An unset variable is still the empty string, which the
    TF_VAR fingerprint excludes -- so plan and apply fingerprint identically. The last three
    bindings inject nothing and exist so the renaming happens once.

    Mutation: drop `SHIPMATE_LEGACY_TF_WORKSPACE`, or rename one to the name it injects.
    """
    assert _job("plan")["env"] == {
        "SHIPMATE_LEGACY_TF_VAR_ENV": "${{ vars.TF_VAR_env }}",
        "SHIPMATE_LEGACY_TF_VAR_REGION": "${{ vars.TF_VAR_region }}",
        "SHIPMATE_LEGACY_TF_WORKSPACE": "${{ vars.TF_WORKSPACE }}",
        "SHIPMATE_LEGACY_AWS_ROLE_ARN": "${{ vars.AWS_ROLE_ARN }}",
        "SHIPMATE_LEGACY_AWS_REGION": "${{ vars.AWS_REGION }}",
        "SHIPMATE_LEGACY_AWS_ROLE_ARN_WORKLOAD": "${{ matrix.workload_var != '' && "
        "vars[format('AWS_ROLE_ARN_{0}', matrix.workload_var)] || '' }}",
    }


def test_the_cell_passes_this_whole_with_block():
    """The whole mapping against a hand-written constant, not the keys this file happens to
    reason about elsewhere: a key checked one at a time relocates the hole to whichever key is
    not named, and a dropped `with:` line reaches a composite action as the empty string rather
    than as an error. plan-cell refuses an empty `expected-head`; an empty `plan-passphrase`
    stores every plan artifact in the clear and fails no cell.

    Mutations: `plan-passphrase` deleted, `expected-head: ${{ github.sha }}`, and the state path's
    `!=` inverted to `==`.
    """
    assert _step("plan", "actions/plan-cell@")["with"] == {
        "stack": "${{ matrix.stack }}",
        "stack-name": "${{ matrix.stack }}",
        "env": "${{ matrix.environment }}",
        "expected-head": "${{ needs.facts.outputs.head-sha }}",
        "state-path": "${{ inputs.state_suffix != '' && "
        "format('{0}/{1}', matrix.stack, inputs.state_suffix) || '' }}",
        "plan-passphrase": "${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}",
    }


def _strings(node):
    """Every scalar string reachable in the parsed workflow, mapping keys included.

    Parsed, not file text: a payload expression inside a comment teaches the next reader to write
    it back, but `safe_load` discards comments, so this cannot be satisfied by one.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif isinstance(node, str):
        yield node


def test_the_workflow_reads_the_event_payload_nowhere():
    """`facts` is the single producer of every pull-request fact, and a fact read from the
    payload beside it is a second producer of the same fact. This file answers to whatever
    trigger its caller declares -- a `workflow_dispatch` run's payload carries no
    `github.event.pull_request` at all, so every expression reading it renders empty, and an
    empty checkout `ref` plans the dispatch ref while an empty `head-repo` refuses `summary`.
    Both fail toward a green gate over a pull request nobody planned.

    The shim owns the one read that has to exist -- the concurrency group, where `needs` is not
    in scope -- so this file's count is zero, compared whole rather than per expression.

    Mutation: `env: { PR: ${{ github.event.pull_request.number }} }` on the `detect` job.
    """
    found = sorted({m for s in _strings(_doc()) for m in re.findall(r"github\.event\.[\w.]*", s)})
    assert found == [], f"plan.yml reads the event payload: {found}"


#: Both gated jobs' whole `if:`, hand-written rather than read back from the file, as
#: `test_engine_drift_workflow.py` pins drift's pair. A substring check over either survives
#: `||` -> `&&`, which keeps every word and inverts the gate.
_GATED_IF = {
    "detect": "needs.facts.outputs.is-draft == 'false' || needs.facts.outputs.on-demand == 'true'",
    "plan": "needs.detect.outputs.empty == 'false'",
}


def test_the_detect_and_plan_jobs_carry_exactly_these_gates():
    """Mutations, each reddening only its own case: drop `detect`'s `on-demand` clause, and a
    `shipmate plan` on a draft skips `detect` and `plan` while `summary` still runs on its own
    on-demand clause, writing a gate over cells nobody planned; turn either `||` into `&&`;
    invert `plan`'s `empty` comparison, and a pull request with changed stacks plans no cell
    while the gate greens over it.

    `facts` and `summary` are absent by design: `facts` carries no gate, and
    `test_summary_workflow_guards.py` owns `summary`'s. The job-id list there is what fails when
    a fourth gated job appears.
    """
    jobs = _doc()["jobs"]
    assert {j: " ".join(jobs[j]["if"].split()) for j in _GATED_IF} == _GATED_IF
