"""Engine `drift.yml`: the gates that keep a sweep off a feature branch, and the artifact
handoff that must fail rather than go quiet.

Two properties carried most of the risk when this graph lived in consumer YAML. The two jobs
that run cells and mint tokens gate on the API-resolved default branch, not on
`github.event.repository.default_branch` -- whether that field is populated under `schedule` is
the question the gate must not depend on. And the `issues` job's `if:` skips an empty sweep, so
the App key is never minted for a run with no cell to report.
"""

import yaml
from _loader import WORKFLOWS, local_action, workflow_yaml

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
_CELL_ENV = "${{ matrix.env_binding }}"


def _job(job_id):
    return workflow_yaml(WF)["jobs"][job_id]


def _step(job_id, needle):
    hits = [s for s in _job(job_id)["steps"] if needle in str(s.get("uses", ""))]
    assert len(hits) == 1, f"{job_id} has {len(hits)} steps using {needle}"
    return hits[0]


def test_the_workflow_call_inputs_are_exactly_these():
    """`tags` is an input so each drift file carries its own query. Mutation: drop `tags`."""
    assert workflow_yaml(WF)["on"]["workflow_call"]["inputs"] == {
        "runs_on": {"required": False, "default": "ubuntu-latest", "type": "string"},
        "tags": {"required": False, "default": "", "type": "string"},
    }


def test_the_workflow_call_secrets_are_exactly_these():
    """Mutation: re-add `SHIPMATE_SLACK_WEBHOOK: { required: false }`."""
    assert workflow_yaml(WF)["on"]["workflow_call"]["secrets"] == {
        "SHIPMATE_APP_PRIVATE_KEY": {"required": False},
        "SHIPMATE_SECRETS": {"required": False},
    }


def test_the_issues_job_is_one_drift_issues_step_with_the_app_id_and_key():
    """The download lives in the action. Mutations: re-add the drift-summary download step ahead
    of the action; re-add `slack-webhook: ${{ secrets.SHIPMATE_SLACK_WEBHOOK }}`; point
    `private-key` at `vars.`, which every plan cell exports and the run log prints; drop
    `cells`, which `actions/drift-issues` then refuses as unset; drop `default-branch`, and no
    Issue of a removed cell is ever closed."""
    assert _job("issues")["steps"] == [
        {
            "uses": local_action("drift-issues"),
            "with": {
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
                "cells": "${{ needs.detect.outputs.cells }}",
                "default-branch": "${{ needs.detect.outputs.default_branch }}",
            },
        }
    ]


def test_the_detect_job_outputs_exactly_these_expressions():
    """Mutation: drop `cells` from detect's `outputs:`, and the issues job receives an empty
    string."""
    assert _job("detect")["outputs"] == {
        "matrix": "${{ steps.m.outputs.matrix }}",
        "cells": "${{ steps.m.outputs.cells }}",
        "empty": "${{ steps.m.outputs.empty }}",
        "default_branch": "${{ steps.default_branch.outputs.default_branch }}",
    }


def test_the_jobs_are_exactly_these():
    """Each job id is also a check-run name segment."""
    assert list(workflow_yaml(WF)["jobs"]) == ["detect", "drift", "issues"]


def test_the_cell_and_issue_jobs_gate_on_the_api_resolved_default_branch():
    """Both `if:` values compared whole. Mutations, each on either job: delete the branch
    clause; read `github.event.repository.default_branch` instead; turn a `&&` into `||`.
    On `issues` also: dropping `always()` leaves no status function, so GHA adds the implicit
    `success()` and a failed cell skips the job -- exactly when an Issue is owed; dropping the
    emptiness clause runs the job on an empty sweep, minting the App token to download nothing.
    """
    jobs = workflow_yaml(WF)["jobs"]
    assert {j: " ".join(jobs[j]["if"].split()) for j in _GATED_IF} == _GATED_IF


def test_the_detect_job_is_deliberately_ungated():
    """It runs no consumer code and holds no secret. A gate here would make a feature-branch
    dispatch silently do nothing instead of failing visibly at the two jobs below."""
    assert "if" not in workflow_yaml(WF)["jobs"]["detect"]


def test_the_sweep_states_no_pull_request_and_no_head():
    """A sweep has no pull request, and carries its file's query to build-matrix.
    Mutations: drop `no-pull-request`, and build-matrix refuses every drift run; drop
    `tags: ${{ inputs.tags }}`, and every drift file sweeps every cell."""
    step = next(
        s
        for s in workflow_yaml(WF)["jobs"]["detect"]["steps"]
        if "actions/build-matrix" in str(s.get("uses", ""))
    )
    assert step["with"] == {
        "base-sha": "",
        "all-stacks": "true",
        "tags": "${{ inputs.tags }}",
        "no-pull-request": "true",
        "github-vars": "${{ toJSON(vars) }}",
    }


def test_only_the_issues_job_holds_the_app_key():
    """Mutations: reference the key from the `drift` job, which runs repository content; move
    `issues` off `shipmate-engine`; give `detect` an `environment:` of its own."""
    jobs = workflow_yaml(WF)["jobs"]
    holders = [
        job_id for job_id, job in jobs.items() if "SHIPMATE_APP_PRIVATE_KEY" in yaml.safe_dump(job)
    ]
    assert holders == ["issues"]
    assert jobs["issues"]["environment"] == "shipmate-engine"
    assert "environment" not in jobs["detect"]


def test_every_job_declares_its_own_permissions():
    """Whole map. Mutations: delete the `drift` job's block, and it silently gets the floor
    instead of the `id-token: write` its OIDC step needs; drop `contents: read` from `issues`,
    and its read of the default branch head fails on a private repository."""
    assert {j: v.get("permissions") for j, v in workflow_yaml(WF)["jobs"].items()} == {
        "detect": {"contents": "read"},
        "drift": {"contents": "read", "id-token": "write"},
        "issues": {"actions": "read", "contents": "read"},
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
        for j, v in workflow_yaml(WF)["jobs"].items()
    }
    assert parsed == {"detect": None, "drift": _CELL_ENV, "issues": "shipmate-engine"}


def test_every_checkout_takes_no_with_block():
    """Whole `with:`, both jobs that check out. A sweep plans the default branch's own tip, so
    naming a `ref:` here would let a dispatch aim the sweep elsewhere; the two `if:` gates would
    then be the only thing left refusing it. Nothing in a sweep diffs, so the default depth of
    one commit is enough.

    Mutations: add `ref: ${{ github.sha }}` to each checkout, and add `fetch-depth: 0` to each.
    """
    for job_id in ("detect", "drift"):
        checkouts = [
            s.get("with")
            for s in workflow_yaml(WF)["jobs"][job_id]["steps"]
            if str(s.get("uses", "")).split("@")[0] == "actions/checkout"
        ]
        assert checkouts == [None], job_id


def test_the_cell_passes_this_whole_with_block():
    """The whole mapping against a hand-written constant, as the plan side is pinned: a dropped
    `with:` line reaches a composite action as the empty string rather than as an error, so a key
    checked one at a time leaves the hole wherever it does not look. An empty `env` here plans
    every cell against the wrong environment and fails nothing.

    Mutations: `env` deleted, and a `state-path:` key added back.
    """
    assert _step("drift", "actions/drift-cell")["with"] == {
        "tf-vars": "${{ toJSON(matrix.tf_vars) }}",
        "github-vars": "${{ toJSON(vars) }}",
        "consumer-secrets": "${{ secrets.SHIPMATE_SECRETS }}",
        "stack": "${{ matrix.stack }}",
        "env": "${{ matrix.environment }}",
    }
