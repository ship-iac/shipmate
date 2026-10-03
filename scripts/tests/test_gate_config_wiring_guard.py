"""Guards the hop that carries the gate settings from `.github/shipmate.toml` into
comment-ops' `Authorize` step.

`Authorize` reads `ungated_envs` from the resolve step. A resolve that skips while
authorization runs, or whose `id:` no longer matches the expression, hands it an empty list,
which exempts nothing: every `gated = false` environment silently needs a review again. So the
resolve step sits before `gather` -- pinned by `_STEP_NAMES` in `test_comment_ops_action.py`,
which compares the whole step list -- and shares `gather`'s one condition, pinned by the `if:`
entry in that module's `_SHARED_ROUTE_IFS` and by the comparison below.
"""

import pytest
from _loader import load_script, step_by

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
    # The reply's own variables are pinned whole by `_REPLIES` in test_comment_ops_action.py.
    assert {k: v for k, v in report["env"].items() if not k.startswith("SHIPMATE_REPLY_")} == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
    }
    run = report["run"]
    assert 'gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments"' in run
    assert run.strip().endswith("exit 1")


def test_the_resolve_step_carries_the_id_authorize_reads():
    """The producer end of the coupling. `Authorize` hard-codes
    `steps.gate.outputs.ungated_envs`, and a renamed or deleted `id:` leaves it resolving to
    the empty string: no environment is exempt, and every `gated = false` apply is refused
    for want of a review.

    Mutation: rename the step's `id:` to `gateconfig`, or delete it.
    """
    assert step_by("comment-ops", name="Resolve gate configuration").get("id") == "gate"


def test_the_resolve_step_and_gather_share_one_condition():
    """Byte-identity between two `if:`s, asserted as a comparison rather than stated in a
    comment beside them. `_SHARED_ROUTE_IFS` in test_comment_ops_action.py owns the VALUE and
    reddens on either single-sided edit; what it cannot see is a coordinated one -- change
    `gather`'s condition and its constant together and the two steps diverge while every
    guard stays green. Resolve then skips where `gather` runs, `Authorize` reads an empty
    `ungated_envs`, and no environment is exempt.

    Mutation: append ` && github.event_name == 'issue_comment'` to `gather`'s `if:` and to
    its `_SHARED_ROUTE_IFS` entry, leaving the resolve step alone.
    """
    assert (
        step_by("comment-ops", name="Resolve gate configuration")["if"]
        == step_by("comment-ops", name="Gather authorization inputs")["if"]
    )


def _resolve(monkeypatch, tmp_path, table):
    gc = load_script("gate-config")
    monkeypatch.setattr(gc.ec, "read_table", lambda: table)
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    gc.main()
    return dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())


def test_the_file_is_the_only_source(monkeypatch, tmp_path):
    """The output comes from the table, sorted. The retired `SHIPMATE_UNGATED_ENVS` is set
    both bare and in `SHIPMATE_GITHUB_VARS`, so a fallback an admin could set without a pull
    request changes the output. Mutations: union `SHIPMATE_UNGATED_ENVS` from the parsed
    `SHIPMATE_GITHUB_VARS`, or from the process environment, into `ungated_envs`' result --
    `prod` joins; or have `ungated_envs` return the `gated = true` entries -- `prod` replaces
    both dev entries."""
    monkeypatch.setenv("SHIPMATE_GITHUB_VARS", '{"SHIPMATE_UNGATED_ENVS": "prod"}')
    monkeypatch.setenv("SHIPMATE_UNGATED_ENVS", "prod")
    table = {
        "layout": "folder",
        "environments": {
            "dev-us": {"gated": False},
            "dev-eu": {"gated": False},
            "prod": {"gated": True},
            "stage": {},
        },
    }
    assert _resolve(monkeypatch, tmp_path, table) == {
        "ungated_envs": "dev-eu,dev-us",
    }


def test_a_file_declaring_no_exemption_resolves_to_empty(monkeypatch, tmp_path):
    """The minimum configuration: a file with no `gated = false` entry is valid and exempts
    no environment. Mutation: return a non-empty default."""
    assert _resolve(monkeypatch, tmp_path, {"layout": "tf_vars"}) == {"ungated_envs": ""}


def test_the_table_is_validated_before_it_is_resolved(monkeypatch, tmp_path):
    """`validate_structure` is what the resolvers assume and do not enforce. A quoted
    `gated = "false"` reads as ungated to a person and resolves as gated -- invisible unless
    the run refuses it.

    Mutation: resolve the table straight from `read_table` without
    validating it; this test then writes an empty exemption instead of refusing.
    """
    table = {"layout": "folder", "environments": {"dev-eu": {"gated": "false"}}}
    with pytest.raises(SystemExit) as exc:
        _resolve(monkeypatch, tmp_path, table)
    assert str(exc.value) == (
        "::error::environments.dev-eu.gated must be a boolean, got str. Write "
        "gated = true or gated = false, unquoted."
    )
