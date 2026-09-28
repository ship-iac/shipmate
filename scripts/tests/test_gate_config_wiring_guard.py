"""Guards the hop that carries the gate settings from `.github/shipmate.toml` into the two
comment-ops steps that authorize on them.

The team is not established where it is refused. `scripts/authorize` receives `IS_MEMBER` and
uses `APPROVERS_TEAM` only to label a refusal; membership is looked up in `Gather authorization
inputs`, against its own `TEAM`. Feeding the resolved team to `Authorize` alone therefore
changes a refusal message and nothing else -- and in both directions silently. With the
repository variable gone, `gather` looks up membership in a team named by the empty string,
the API 404s, and every commenter is refused under a message naming the file's team. With the
variable still set, `gather` authorizes against it while the file declares another.

So both steps read ONE expression, compared here to one hand-written constant and to each
other, and the resolve step sits before `gather` -- pinned by `_STEP_NAMES` in
`test_comment_ops_action.py`, which compares the whole step list, and by the `if:` entry in
that module's `_SHARED_ROUTE_IFS`, so that one condition governs the resolve and the lookup.
"""

import pytest
from _loader import load_script, step_by

#: The one expression `gather` and `authorize` must both read for the team, hand-written.
_TEAM = "${{ steps.gate.outputs.approvers_team }}"

#: The whole `env:` of the resolve step, hand-written. `GH_TOKEN` is the workflow token: the
#: contents read needs no App token. The file on the default branch is the only source;
#: `SHIPMATE_GITHUB_VARS` resolves only the variable references that file names.
_GATE_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
}


def test_the_resolve_step_runs_the_gate_config_script_on_the_workflow_token():
    """Mutation: drop a binding from the `env:` block, or point `run:` at another script."""
    step = step_by("comment-ops", name="Resolve gate configuration")
    assert step["env"] == _GATE_ENV
    assert step["run"].strip() == 'python3 "$GITHUB_ACTION_PATH/../../scripts/gate-config"'


def test_a_failed_resolve_reports_to_the_commenter_and_still_fails_the_job():
    """An unreadable file refuses, and every later step is `success()`-gated -- so the
    reaction, the exemption report and the refusal all skipped, and the commenter got no
    reaction and no comment at all, `unlock` included. The resolve step now survives its own
    failure just long enough for the report step to post one, which then re-raises it: a
    green run over a command this action never authorized is not a verdict it may render.

    Mutations: delete `continue-on-error` from the resolve step, so the report never runs;
    delete the `exit 1`, so the job ends green with nothing applied and nothing refused.
    """
    assert step_by("comment-ops", name="Resolve gate configuration")["continue-on-error"] is True
    report = step_by("comment-ops", name="Gate configuration unreadable")
    assert report["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
    }
    run = report["run"]
    assert 'gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments"' in run
    assert r"\`.github/shipmate.toml\`" in run
    assert run.strip().endswith("exit 1")


def test_the_resolve_step_carries_the_id_its_three_readers_name():
    """The producer end of the coupling. Three expressions hard-code `steps.gate.outputs.…`
    and every guard here compares them to hand-written constants, so a renamed or deleted
    `id:` leaves all three resolving to the empty string with nothing to compare against:
    membership is looked up in a team named `""`, the API 404s, and every commenter is
    refused -- the failure this whole step exists to prevent.

    Mutation: rename the step's `id:` to `gateconfig`, or delete it.
    """
    assert step_by("comment-ops", name="Resolve gate configuration").get("id") == "gate"


def test_the_resolve_step_and_gather_share_one_condition():
    """Byte-identity between two `if:`s, asserted as a comparison rather than stated in a
    comment beside them. `_SHARED_ROUTE_IFS` in test_comment_ops_action.py owns the VALUE and
    reddens on either single-sided edit; what it cannot see is a coordinated one -- change
    `gather`'s condition and its constant together and the two steps diverge while every
    guard stays green. Resolve then skips where `gather` runs, `TEAM` is empty, and every
    commenter is refused under a message naming the file's team.

    Mutation: append ` && github.event_name == 'issue_comment'` to `gather`'s `if:` and to
    its `_SHARED_ROUTE_IFS` entry, leaving the resolve step alone.
    """
    assert (
        step_by("comment-ops", name="Resolve gate configuration")["if"]
        == step_by("comment-ops", name="Gather authorization inputs")["if"]
    )


def test_gather_and_authorize_read_one_identical_team_expression():
    """The P1's guard. Both against one hand-written constant, and against each other, so
    neither the membership lookup nor the refusal label can be moved back to the input on its
    own.

    Mutations: restore `${{ inputs.approvers-team }}` on `Gather authorization inputs`; do it
    on `Authorize` instead; do it on both.
    """
    gather = step_by("comment-ops", name="Gather authorization inputs")["env"]["TEAM"]
    authorize = step_by("comment-ops", name="Authorize")["env"]["APPROVERS_TEAM"]
    assert gather == _TEAM
    assert authorize == _TEAM
    assert gather == authorize


def test_the_doctor_step_takes_no_team_binding():
    """`doctor` resolves the team from the table it already fetches, at the commit under
    examination -- which is what lets `shipmate doctor` warn about a gate table before it
    merges, where the resolve step's default-branch read cannot.

    Mutation: bind `SHIPMATE_TEAM` here again. Nothing reads it, so a binding is a second
    source for a value that has one.
    """
    assert (
        "SHIPMATE_TEAM"
        not in step_by("comment-ops", name="Doctor — render and upsert the sticky comment")["env"]
    )


def _resolve(monkeypatch, tmp_path, table):
    gc = load_script("gate-config")
    monkeypatch.setattr(gc.ec, "read_table_at_default_branch", lambda *a, **k: table)
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    gc.main()
    return dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())


def test_the_file_is_the_only_source(monkeypatch, tmp_path):
    """Both outputs come from the table, sorted. Mutations: read either value from the
    process environment -- there is no variable left to read, so the output goes empty and
    every environment holds while the file says otherwise; or have `ungated_envs` return the
    `gated = true` entries -- `prod` replaces both dev entries."""
    table = {
        "layout": "folder",
        "gate": {"approver_team": "platform"},
        "environments": {
            "dev-us": {"gated": False},
            "dev-eu": {"gated": False},
            "prod": {"gated": True},
            "stage": {},
        },
    }
    assert _resolve(monkeypatch, tmp_path, table) == {
        "ungated_envs": "dev-eu,dev-us",
        "approvers_team": "platform",
    }


def test_a_file_declaring_no_gate_resolves_to_empty(monkeypatch, tmp_path):
    """The minimum configuration: a file with no `[gate]` is valid, authorizes nobody by
    team and exempts no environment. Mutation: return a non-empty default for either."""
    assert _resolve(monkeypatch, tmp_path, {"layout": "tf_vars"}) == {
        "ungated_envs": "",
        "approvers_team": "",
    }


def test_the_table_is_validated_before_it_is_resolved(monkeypatch, tmp_path):
    """`validate_structure` is what the resolvers assume and do not enforce. A quoted
    `gated = "false"` reads as ungated to a person and resolves as gated -- invisible unless
    the run refuses it.

    Mutation: resolve the table straight from `read_table_at_default_branch` without
    validating it; this test then writes an empty exemption instead of refusing.
    """
    table = {"layout": "folder", "environments": {"dev-eu": {"gated": "false"}}}
    with pytest.raises(SystemExit) as exc:
        _resolve(monkeypatch, tmp_path, table)
    assert str(exc.value) == (
        "::error::environments.dev-eu.gated must be a boolean, got str. Write "
        "gated = true or gated = false, unquoted."
    )
