"""Engine `drift.yml`: the gates that keep a sweep off a feature branch, and the artifact
handoff that must fail rather than go quiet.

Two properties carried most of the risk when this graph lived in consumer YAML. The two jobs
that run cells and mint tokens gate on the API-resolved default branch, not on
`github.event.repository.default_branch` -- whether that field is populated under `schedule` is
the question the gate must not depend on. And the `issues` job's `if:` distinguishes an empty
matrix (skip) from a lost artifact (fail); collapsing the two greens a run that opened no Issue.
"""

import yaml
from _loader import WORKFLOWS

WF = WORKFLOWS / "drift.yml"

_BRANCH_GATE = "github.ref == format('refs/heads/{0}', needs.detect.outputs.default_branch)"

#: Both gated jobs' whole `if:`, hand-written rather than read back from the file. A substring
#: check over either survives `&&` -> `||`, which keeps every word and inverts the gate.
_GATED_IF = {
    "drift": "${{ needs.detect.outputs.empty == 'false' && " + _BRANCH_GATE + " }}",
    "issues": "${{ always() && needs.detect.outputs.empty == 'false' && " + _BRANCH_GATE + " }}",
}


#: The `drift` cell's whole `environment:` expression, whitespace-collapsed. Hand-written: a
#: value read back from the file passes whatever the file says, literal env name included.
_SHARED_PLAN_ENV = (
    "${{ contains(format(',{0},', vars.SHIPMATE_SHARED_ENVS), "
    "format(',{0},', matrix.environment)) && matrix.environment "
    "|| format('{0}-plan', matrix.environment) }}"
)


def _doc():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _job(job_id):
    return _doc()["jobs"][job_id]


def _step(job_id, needle):
    hits = [s for s in _job(job_id)["steps"] if needle in str(s.get("uses", ""))]
    assert len(hits) == 1, f"{job_id} has {len(hits)} steps using {needle}"
    return hits[0]


def test_the_workflow_call_inputs_are_exactly_these():
    assert _doc()[True]["workflow_call"]["inputs"] == {
        "state_suffix": {"required": True, "type": "string"},
        "runs_on": {"required": False, "default": "ubuntu-latest", "type": "string"},
        "tags": {"required": False, "default": "", "type": "string"},
    }


def test_the_workflow_call_secrets_are_exactly_these():
    assert _doc()[True]["workflow_call"]["secrets"] == {
        "SHIPMATE_APP_PRIVATE_KEY": {"required": False}
    }


def test_the_jobs_are_exactly_these():
    """Each job id is also a check-run name segment."""
    assert list(_doc()["jobs"]) == ["detect", "drift", "issues"]


def test_the_cell_and_issue_jobs_gate_on_the_api_resolved_default_branch():
    """Both `if:` values compared whole. Mutations, each on either job: delete the branch
    clause; read `github.event.repository.default_branch` instead; turn a `&&` into `||`.
    On `issues` also: dropping `always()` leaves no status function, so GHA adds the implicit
    `success()` and a failed cell skips the job -- exactly when an Issue is owed; dropping the
    emptiness clause instead turns a lost artifact into a silent success.
    """
    jobs = _doc()["jobs"]
    assert {j: " ".join(jobs[j]["if"].split()) for j in _GATED_IF} == _GATED_IF


def test_the_detect_job_is_deliberately_ungated():
    """It runs no consumer code and holds no secret. A gate here would make a feature-branch
    dispatch silently do nothing instead of failing visibly at the two jobs below."""
    assert "if" not in _doc()["jobs"]["detect"]


def test_the_artifact_download_has_no_continue_on_error():
    """Mutation: add `continue-on-error: true`. The gate for the empty case is the job's `if:`;
    degrading the download too makes a lost artifact indistinguishable from no drift."""
    step = next(
        s
        for s in _doc()["jobs"]["issues"]["steps"]
        if "actions/download-artifact@" in str(s.get("uses", ""))
    )
    assert "continue-on-error" not in step


def test_the_sweep_states_no_pull_request_and_no_head():
    """A sweep has no pull request. Mutation: drop `no-pull-request`, and build-matrix refuses
    every drift run."""
    step = next(
        s
        for s in _doc()["jobs"]["detect"]["steps"]
        if "actions/build-matrix@" in str(s.get("uses", ""))
    )
    assert step["with"] == {
        "base-sha": "",
        "all-stacks": "true",
        "tags": "${{ inputs.tags }}",
        "no-pull-request": "true",
    }


def test_only_the_issues_job_holds_the_app_key():
    """Mutations: reference the key from the `drift` job, which runs repository content; move
    `issues` off `shipmate-engine`; give `detect` an `environment:` of its own."""
    jobs = _doc()["jobs"]
    holders = [
        job_id for job_id, job in jobs.items() if "SHIPMATE_APP_PRIVATE_KEY" in yaml.safe_dump(job)
    ]
    assert holders == ["issues"]
    assert jobs["issues"]["environment"] == "shipmate-engine"
    assert "environment" not in jobs["detect"]


def test_the_workflow_permissions_floor_is_empty():
    """Mutation: `permissions: { contents: read }` at workflow level. A job that then loses its
    own block silently inherits instead of getting nothing."""
    assert _doc()["permissions"] == {}


def test_every_job_declares_its_own_permissions():
    """Whole map. Mutation: delete the `drift` job's block, and it silently gets the floor
    instead of the `id-token: write` its OIDC step needs."""
    assert {j: v.get("permissions") for j, v in _doc()["jobs"].items()} == {
        "detect": {"contents": "read"},
        "drift": {"contents": "read", "id-token": "write"},
        "issues": {"actions": "read"},
    }


def test_every_job_binds_the_environment_it_should_and_no_other():
    """Whole map, because binding an environment is what supplies its secrets -- a job that
    names no secret still holds the App key once it binds `shipmate-engine`, and `holders`
    above cannot see that. A literal env name in place of the expression is the thing
    CLAUDE.md forbids outright.

    Mutations: `drift` bound to `shipmate-engine`; `drift`'s expression replaced by a literal
    `dev-eu`; `detect` given an `environment:`.
    """
    parsed = {
        j: (" ".join(v["environment"].split()) if "environment" in v else None)
        for j, v in _doc()["jobs"].items()
    }
    assert parsed == {"detect": None, "drift": _SHARED_PLAN_ENV, "issues": "shipmate-engine"}


def test_every_checkout_takes_the_full_history_and_no_ref():
    """Whole `with:`, both jobs that check out. A sweep plans the default branch's own tip, so
    naming a `ref:` here would let a dispatch aim the sweep elsewhere; the two `if:` gates would
    then be the only thing left refusing it. `fetch-depth: 0` is load-bearing: without the full
    history `terramate list` sees no stacks.

    Mutations: add `ref: ${{ github.sha }}` to either checkout, and delete `fetch-depth` from
    either.
    """
    # PyYAML gives the int 0, not "0".
    for job_id in ("detect", "drift"):
        assert _step(job_id, "actions/checkout@")["with"] == {"fetch-depth": 0}, job_id


def test_the_cell_injects_the_three_flavor_variables():
    """All three unconditionally, as the plan and apply paths do. An unset variable is the empty
    string, which the TF_VAR fingerprint excludes."""
    assert _job("drift")["env"] == {
        "TF_VAR_env": "${{ vars.TF_VAR_env }}",
        "TF_VAR_region": "${{ vars.TF_VAR_region }}",
        "TF_WORKSPACE": "${{ vars.TF_WORKSPACE }}",
    }


def test_the_cell_state_path_is_built_from_the_state_suffix_input():
    """Mutation: `${{ matrix.stack }}/.state`. A hard-coded suffix is the per-flavor value this
    input exists to carry, and the `|| ''` branch is what keeps the S3-backend flavor restoring
    nothing."""
    assert _step("drift", "actions/drift-cell@")["with"]["state-path"] == (
        "${{ inputs.state_suffix != '' && "
        "format('{0}/{1}', matrix.stack, inputs.state_suffix) || '' }}"
    )
