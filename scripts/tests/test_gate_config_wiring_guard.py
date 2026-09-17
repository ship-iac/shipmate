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
from _loader import action_steps, load_script

#: The one expression `gather` and `authorize` must both read for the team, hand-written.
_TEAM = "${{ steps.gate.outputs.approvers_team }}"

#: The whole `env:` of the resolve step, hand-written. `GH_TOKEN` is the workflow token: the
#: contents read needs no App token. Both fallback inputs are bound here and nowhere else on
#: the apply path, so this block is the migration's only channel.
_GATE_ENV = {
    "GH_TOKEN": "${{ inputs.github-token }}",
    "SHIPMATE_UNGATED_ENVS": "${{ inputs.ungated-envs }}",
    "APPROVERS_TEAM": "${{ inputs.approvers-team }}",
}


def _step(name):
    return next(s for s in action_steps("comment-ops") if s.get("name") == name)


def test_the_resolve_step_runs_the_gate_config_script_on_the_workflow_token():
    """Mutation: drop a binding from the `env:` block, or point `run:` at another script."""
    step = _step("Resolve gate configuration")
    assert step["env"] == _GATE_ENV
    assert step["run"].strip() == 'python3 "$GITHUB_ACTION_PATH/../../scripts/gate-config"'


def test_gather_and_authorize_read_one_identical_team_expression():
    """The P1's guard. Both against one hand-written constant, and against each other, so
    neither the membership lookup nor the refusal label can be moved back to the input on its
    own.

    Mutations: restore `${{ inputs.approvers-team }}` on `Gather authorization inputs`; do it
    on `Authorize` instead; do it on both.
    """
    gather = _step("Gather authorization inputs")["env"]["TEAM"]
    authorize = _step("Authorize")["env"]["APPROVERS_TEAM"]
    assert gather == _TEAM
    assert authorize == _TEAM
    assert gather == authorize


#: The doctor step's own team binding, hand-written. It is a separate reader with a separate
#: source and is deliberately NOT the resolve step's output: `doctor` fetches the file itself.
#: Named here so a sweep of `inputs.approvers-team` readers does not mistake it for a site
#: this wiring missed.
_DOCTOR_TEAM = "${{ inputs.approvers-team }}"


def test_the_doctor_step_keeps_its_own_team_binding():
    """Mutation: point it at `steps.gate.outputs.approvers_team`, which is unset on the doctor
    route -- the resolve step's `if:` admits only apply and unlock."""
    assert _step("Doctor — render and upsert the sticky comment")["env"]["SHIPMATE_TEAM"] == (
        _DOCTOR_TEAM
    )


def _resolve(monkeypatch, tmp_path, table, ungated="", team=""):
    gc = load_script("gate-config")
    monkeypatch.setattr(gc.ec, "read_table_at_default_branch", lambda *a, **k: table)
    monkeypatch.setenv("SHIPMATE_UNGATED_ENVS", ungated)
    monkeypatch.setenv("APPROVERS_TEAM", team)
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    gc.main()
    return dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())


def test_the_file_wins_over_both_fallbacks(monkeypatch, tmp_path):
    """Mutation: swap either resolver's arguments, or return the fallback unconditionally."""
    table = {
        "layout": "dry",
        "gate": {"approvers_team": "platform", "ungated_envs": ["dev-us", "dev-eu"]},
    }
    assert _resolve(monkeypatch, tmp_path, table, ungated="stale", team="stale") == {
        "ungated_envs": "dev-eu,dev-us",
        "approvers_team": "platform",
    }


def test_the_variables_are_read_when_the_file_declares_no_gate(monkeypatch, tmp_path):
    """The migration's whole point: a repository that has not yet added the table keeps
    working. Mutation: drop the fallback argument from either call."""
    assert _resolve(
        monkeypatch, tmp_path, {"layout": "dry"}, ungated="dev-eu", team="deployers"
    ) == {"ungated_envs": "dev-eu", "approvers_team": "deployers"}


def test_the_table_is_validated_before_it_is_resolved(monkeypatch, tmp_path):
    """`validate_structure` is what the resolvers assume and do not enforce. A bare string
    under `ungated_envs` iterates character by character into a frozenset of single letters,
    which exempts nothing while reading as a declared list -- fail-open, and invisible.

    Mutation: resolve the table straight from `read_table_at_default_branch` without
    validating it; this test then reports `d,e,u,-` instead of refusing.
    """
    table = {"layout": "dry", "gate": {"ungated_envs": "dev-eu"}}
    with pytest.raises(SystemExit) as exc:
        _resolve(monkeypatch, tmp_path, table)
    assert "gate.ungated_envs" in str(exc.value)
