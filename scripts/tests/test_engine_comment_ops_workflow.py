"""Engine `comment-ops.yml`: the dispatch runs only after the authorization step said yes.

The two steps are one gate and one privileged action. `scripts/authorize` writes `head_sha`,
`environment` and a verdict of `false` on the same run, and the verb comes from the parse step
either way -- so a refused `shipmate apply` carries everything a dispatch needs. If the `if:` on
the dispatch step is lost, any commenter's apply reaches the apply path with an App token. The
authorization decision is made exactly once, in the first step, and read exactly once, here.
"""

import yaml
from _loader import WORKFLOWS

WF = WORKFLOWS / "comment-ops.yml"


def _doc():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _job():
    return _doc()["jobs"]["ops"]


def _dispatch_step():
    hits = [s for s in _job()["steps"] if "actions/dispatch@" in str(s.get("uses", ""))]
    assert len(hits) == 1, f"{len(hits)} steps use actions/dispatch"
    return hits[0]


def test_the_workflow_declares_no_inputs_and_one_secret():
    """Mutation: add any `workflow_call` input, or make the secret `required: true` -- which
    fails at load time for every consumer scoping the key to an environment."""
    call = _doc()[True]["workflow_call"]
    assert call.get("inputs") is None
    assert call["secrets"] == {"SHIPMATE_APP_PRIVATE_KEY": {"required": False}}


def test_only_pull_request_comments_are_handled():
    """issue_comment fires on issues too. Mutation: delete the `if:`, and every issue comment in
    the repository runs the comment-ops action with an issue number as `pr-number`."""
    assert _job()["if"] == "${{ github.event.issue.pull_request }}"


def test_the_dispatch_step_runs_only_when_the_guard_authorized():
    """Mutation: delete the dispatch step's `if:`, or change `'true'` to `'false'`."""
    assert _dispatch_step()["if"] == "${{ steps.authz.outputs.authorized == 'true' }}"


def test_the_guard_step_runs_before_the_dispatch_step():
    """Mutation: swap the two steps. A dispatch reading a not-yet-produced output gets the empty
    string, which the `== 'true'` comparison rejects -- but only by accident; the order is the
    property."""
    uses = [str(s.get("uses", "")).split("@")[0] for s in _job()["steps"]]
    assert uses == [
        "ship-iac/shipmate/actions/comment-ops",
        "ship-iac/shipmate/actions/dispatch",
    ]


def test_the_workflow_permissions_floor_is_empty():
    """Mutation: `permissions: { contents: read }` at workflow level. A job that then loses its
    own block silently inherits instead of getting nothing."""
    assert _doc()["permissions"] == {}


def test_every_job_declares_its_own_permissions():
    """Whole map, so the single job is pinned too. A callee's permissions cap at the caller's
    job block; granting less kills the run at load time with no job and no log, so the shim's
    block must match this set exactly.

    Mutations: drop `issues: write`, and the reaction and refusal comment fail; delete the whole
    block, and the job silently gets the empty floor.
    """
    assert {j: v.get("permissions") for j, v in _doc()["jobs"].items()} == {
        "ops": {
            "contents": "read",
            "issues": "write",
            "pull-requests": "write",
            "actions": "read",
        }
    }


def test_every_job_binds_the_engine_environment():
    """Whole map: binding the environment is what supplies the App key, so a job that lost the
    binding cannot mint a token and every `shipmate apply` comment dies quietly.

    Mutation: delete the `environment:`.
    """
    bound = {j: v.get("environment") for j, v in _doc()["jobs"].items()}
    assert bound == {"ops": "shipmate-engine"}


def test_the_dispatch_step_passes_this_whole_with_block():
    """Hand-written. `dispatch-ref` is the default branch on purpose: a dispatched workflow file
    only ever resolves there, so pointing it at the head ref dispatches nothing.

    Mutation: `dispatch-ref: ${{ github.head_ref }}`.
    """
    assert _dispatch_step()["with"] == {
        "app-id": "${{ vars.SHIPMATE_APP_ID }}",
        "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
        "verb": "${{ steps.authz.outputs.verb }}",
        "environment": "${{ steps.authz.outputs.environment }}",
        "ref": "${{ steps.authz.outputs.head-sha }}",
        "pr-number": "${{ github.event.issue.number }}",
        "dispatch-ref": "${{ github.event.repository.default_branch }}",
        "repository": "${{ github.repository }}",
    }
