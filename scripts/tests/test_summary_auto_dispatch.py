"""The plan summary dispatches `verb: auto` only after a complete, non-draft autoplan's first
attempt changed a cell of an `apply_mode: auto` environment, and a failed dispatch replies with
the remedy without touching the gate.

The gate conditions are fed from `gate-state.decide`'s real outputs, so a change to what
`decide` returns for a held, cancelled, draft or nothing-changed run reaches these rows.
"""

import base64
import json
import os

import pytest
from _loader import (
    ACTIONS,
    bash_only,
    github_outputs,
    load_script,
    read_calls,
    run_step,
    step_by,
    write_python3_shim,
)

ad = load_script("auto-dispatch")
gs = load_script("gate-state")

DECIDE = "Decide whether to dispatch an auto apply"
MINT = "Mint the auto apply dispatch token"
DISPATCH = "Dispatch an auto apply"
HEAD_SHA = "a" * 40
NOT_PENDING = "the gate is not pending over a complete plan"


def _gate(**overrides):
    """`decide`'s (state, comment_mode) for a complete one-cell plan with a change, overridden."""
    kwargs = {
        "detect_result": "success",
        "plan_result": "success",
        "planned_cells": "1",
        "cell_count": 1,
        "pending": True,
    } | overrides
    state, _, mode = gs.decide(**kwargs)
    return state or "", mode


_AUTOPLAN = ("pull_request_target", "synchronize", "1", "")
_NOT_A_PUSH = "not a push (opened or synchronize)"

#: name -> (event, action, attempt, is_draft, decide overrides, whole reason); each row breaks
#: one condition.
SKIPS = {
    "on-demand plan": (
        ("workflow_dispatch", "", "1", ""),
        {},
        "a workflow_dispatch run is not an autoplan",
    ),
    "ready for review": (
        ("pull_request_target", "ready_for_review", "1", ""),
        {},
        f"the autoplan's event action is 'ready_for_review', {_NOT_A_PUSH}",
    ),
    "reopened": (
        ("pull_request_target", "reopened", "1", ""),
        {},
        f"the autoplan's event action is 'reopened', {_NOT_A_PUSH}",
    ),
    "unreadable action": (
        ("pull_request_target", "", "1", ""),
        {},
        f"the autoplan's event action is '', {_NOT_A_PUSH}",
    ),
    "re-run": (
        ("pull_request_target", "synchronize", "2", ""),
        {},
        "run attempt 2 is a re-run",
    ),
    "draft": (
        ("pull_request_target", "synchronize", "1", "true"),
        {},
        "the pull request is a draft",
    ),
    "detect failed": (
        _AUTOPLAN,
        {"detect_result": "failure"},
        f"{NOT_PENDING} (state 'failure', mode 'held')",
    ),
    "plan cancelled": (
        _AUTOPLAN,
        {"plan_result": "cancelled"},
        f"{NOT_PENDING} (state '', mode 'hold')",
    ),
    "evidence gap": (
        _AUTOPLAN,
        {"planned_cells": "2"},
        f"{NOT_PENDING} (state 'failure', mode 'held')",
    ),
    "draft gate": (
        _AUTOPLAN,
        {"detect_result": "skipped", "is_draft": True},
        f"{NOT_PENDING} (state 'pending', mode 'draft')",
    ),
    "nothing pending": (
        _AUTOPLAN,
        {"pending": False},
        f"{NOT_PENDING} (state 'success', mode 'post')",
    ),
    "nothing changed": (
        _AUTOPLAN,
        {"pending": False, "planned_cells": "0", "cell_count": 0},
        f"{NOT_PENDING} (state 'success', mode 'nothing-changed')",
    ),
}


@pytest.mark.parametrize("name", list(SKIPS))
def test_each_failed_condition_dispatches_nothing(name):
    """Mutations: delete any one `if` of `skip_reason` (its row reddens); compare only `state`
    with `pending` (the draft-gate row reddens: a draft autoplan's gate is pending too); admit
    `reopened` (its row reddens)."""
    (event, action, attempt, is_draft), overrides, reason = SKIPS[name]
    state, mode = _gate(**overrides)
    got = ad.skip_reason(
        event=event, action=action, attempt=attempt, is_draft=is_draft, state=state, mode=mode
    )
    assert got == reason


@pytest.mark.parametrize("action", ["opened", "synchronize"])
def test_every_condition_holding_dispatches(action):
    """Mutation: compare `mode` with `nothing-changed` in place of `post`. Mutation: drop
    `opened` from the admitted actions."""
    state, mode = _gate()
    assert (state, mode) == ("pending", "post")
    got = ad.skip_reason(
        event="pull_request_target",
        action=action,
        attempt="1",
        is_draft="false",
        state=state,
        mode=mode,
    )
    assert got is None


def test_only_changed_cells_of_auto_environments_count():
    """Mutation: drop `if c["changed"]`."""
    cells = [
        {"stack": "a", "environment": "dev", "changed": True},
        {"stack": "b", "environment": "stage", "changed": False},
        {"stack": "c", "environment": "prod", "changed": True},
    ]
    assert ad.changed_auto_envs(cells, ["dev", "stage"]) == ["dev"]


def test_the_dispatch_steps_are_wired_exactly():
    """Only a run whose upsert did not report a newer head decides, and only a decision naming
    environments mints the `actions: write` token and dispatches. No `ENVIRONMENT`:
    dispatch-body adds that key when it is set, and `auto` takes none.

    Mutations: drop `&& steps.upsert.outputs.result != 'moved'`; add `ENVIRONMENT: x` to the
    dispatch step; drop `SHIPMATE_GITHUB_VARS` from the decision; drop the mint's `if:`; add
    `permission-pull-requests: write` to the mint; dispatch with `steps.token.outputs.token`;
    reply with `steps.autotoken.outputs.token`."""
    decide = step_by("summary", name=DECIDE)
    assert decide["id"] == "autodecide"
    assert decide["if"] == (
        "${{ steps.hold.outputs.superseded != 'true' && steps.upsert.outputs.result != 'moved' }}"
    )
    assert decide["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
        "SHIPMATE_IS_DRAFT": "${{ inputs.is-draft }}",
        "SHIPMATE_GATE_STATE": "${{ steps.gate.outputs.state }}",
        "SHIPMATE_COMMENT_MODE": "${{ steps.gate.outputs.comment_mode }}",
    }
    mint = step_by("summary", name=MINT)
    assert mint["id"] == "autotoken"
    assert mint["if"] == "${{ steps.autodecide.outputs.envs != '' }}"
    assert mint["continue-on-error"] is True
    assert mint["uses"].startswith("actions/create-github-app-token@")
    assert mint["with"] == {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-actions": "write",
    }
    dispatch = step_by("summary", name=DISPATCH)
    assert dispatch["if"] == "${{ steps.autodecide.outputs.envs != '' }}"
    assert dispatch["env"] == {
        "GH_TOKEN": "${{ steps.autotoken.outputs.token }}",
        "REPLY_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_AUTO_ENVS": "${{ steps.autodecide.outputs.envs }}",
        "VERB": "auto",
        "REF": "${{ inputs.head-sha }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        "DISPATCH_REF": "${{ github.event.repository.default_branch }}",
    }


# A stub, not `_GH_RECORDER`: that one discards stdin and answers the contents read with no blob.
_GH = """\
#!/bin/bash
printf '%s\\n' "$@" -- >> "$CALLS"
if [[ "$*" == *contents/.github/shipmate-config.yml* ]]; then
  printf '%s' "$FAKE_CONTENTS"
elif [[ "$*" == *dispatches* ]]; then
  cat > "$DISPATCH_BODY"
  exit "$FAKE_DISPATCH_RC"
elif [[ "$*" == *comments* ]]; then
  cat > "$REPLY_BODY"
  printf '%s' "$GH_TOKEN" > "$REPLY_BODY.token"
fi
"""

TABLE = "environments:\n  dev:\n    apply_mode: auto\n    review: false\n  prod: {}\n"


def _run(tmp_path, step, *, cells=(), dispatch_rc=0, token="x", event=None):  # noqa: S107 - a stub token, never sent
    """Run `step`'s `run:` with the gh stub; return (process, gh calls, step outputs). `event`
    is the payload text at `GITHUB_EVENT_PATH`, a `synchronize` one by default."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_python3_shim(bin_dir)
    (bin_dir / "gh").write_text(_GH, encoding="utf-8", newline="\n")
    (bin_dir / "gh").chmod(0o755)
    for i, (env, changed) in enumerate(cells):
        d = tmp_path / "cells" / str(i)
        d.mkdir(parents=True)
        cell = {"stack": f"s{i}", "environment": env, "changed": changed}
        (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    blob = {"encoding": "base64", "content": base64.b64encode(TABLE.encode()).decode()}
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    payload = tmp_path / "event.json"
    payload.write_text(
        json.dumps({"action": "synchronize"}) if event is None else event, encoding="utf-8"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CALLS": str(tmp_path / "calls"),
        "DISPATCH_BODY": str(tmp_path / "dispatch.json"),
        "REPLY_BODY": str(tmp_path / "reply.md"),
        "FAKE_CONTENTS": json.dumps(blob),
        "FAKE_DISPATCH_RC": str(dispatch_rc),
        "GITHUB_ACTION_PATH": str(ACTIONS / "summary"),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_SERVER_URL": "https://example.invalid",
        "GITHUB_RUN_ID": "999",
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_EVENT_PATH": str(payload),
        "GITHUB_RUN_ATTEMPT": "1",
        "GH_TOKEN": token,
        "REPLY_TOKEN": "main-token",
        "SHIPMATE_GITHUB_VARS": "",
        "SHIPMATE_IS_DRAFT": "false",
        "SHIPMATE_GATE_STATE": "pending",
        "SHIPMATE_COMMENT_MODE": "post",
        "SHIPMATE_AUTO_ENVS": "dev",
        "VERB": "auto",
        "REF": HEAD_SHA,
        "PR_NUMBER": "42",
        "DISPATCH_REF": "main",
    }
    env.pop("ENVIRONMENT", None)
    proc = run_step(tmp_path, step_by("summary", name=step)["run"], env)
    return proc, read_calls(tmp_path / "calls"), github_outputs(out)


def _paths(calls):
    return [next(a for a in c if a.startswith("repos/")) for c in calls]


@bash_only
def test_a_changed_auto_cell_decides_to_dispatch(tmp_path):
    """Mutation: write `envs=` with no value."""
    proc, calls, outputs = _run(tmp_path, DECIDE, cells=[("dev", True), ("prod", True)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _paths(calls) == ["repos/acme/demo/contents/.github/shipmate-config.yml"]
    assert outputs == {"envs": "dev"}


@bash_only
def test_no_changed_auto_cell_decides_nothing(tmp_path):
    """Mutation: drop `if c["changed"]` (the output names `dev`)."""
    proc, _, outputs = _run(tmp_path, DECIDE, cells=[("dev", False), ("prod", True)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs == {"envs": ""}


@bash_only
@pytest.mark.parametrize(
    ("event", "action"),
    [
        pytest.param(json.dumps({"action": "ready_for_review"}), "'ready_for_review'", id="ready"),
        pytest.param("not json", "''", id="unparseable"),
        pytest.param(json.dumps(["synchronize"]), "''", id="not-an-object"),
    ],
)
def test_an_autoplan_not_started_by_a_push_decides_nothing(tmp_path, event, action):
    """The payload is read from `GITHUB_EVENT_PATH`; an unreadable one dispatches nothing and
    makes no API call. Mutation: return `synchronize` from `event_action`'s `except` (the
    unparseable and not-an-object rows dispatch). Mutation: pass `GITHUB_EVENT_NAME` as the
    action (every row names `'pull_request_target'`)."""
    proc, calls, outputs = _run(tmp_path, DECIDE, cells=[("dev", True)], event=event)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (calls, outputs) == ([], {"envs": ""})
    assert (
        f"no auto apply dispatched: the autoplan's event action is {action}, {_NOT_A_PUSH}"
        in proc.stderr
    )


@bash_only
def test_a_dispatch_sends_the_exact_body(tmp_path):
    """Mutations: send `ENVIRONMENT` into dispatch-body; dispatch to another file than
    `shipmate.yml`."""
    proc, calls, _ = _run(tmp_path, DISPATCH)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _paths(calls) == ["repos/acme/demo/actions/workflows/shipmate.yml/dispatches"]
    assert json.loads((tmp_path / "dispatch.json").read_text(encoding="utf-8")) == {
        "ref": "main",
        "inputs": {"verb": "auto", "ref": HEAD_SHA, "pr_number": "42"},
        "return_run_details": True,
    }


_REPLY_HEAD = [
    "### shipmate apply",
    "",
    "🔴 failed: auto apply not started: the dispatch failed; comment `shipmate apply`",
]


@bash_only
def test_a_failed_mint_replies_with_the_main_token_and_dispatches_nothing(tmp_path):
    """Mutations: drop the empty-token check (the dispatch is attempted); reply with `GH_TOKEN`,
    the empty dispatch token, in place of `REPLY_TOKEN`."""
    proc, calls, _ = _run(tmp_path, DISPATCH, token="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _paths(calls) == ["repos/acme/demo/issues/42/comments"]
    assert (
        "::warning::the auto apply dispatch token mint for dev failed; comment shipmate apply to "
        "apply" in proc.stdout
    )
    assert (tmp_path / "reply.md.token").read_text(encoding="utf-8") == "main-token"
    assert (tmp_path / "reply.md").read_text(encoding="utf-8").splitlines()[:3] == _REPLY_HEAD


@bash_only
def test_a_failed_dispatch_replies_and_warns_and_leaves_the_gate(tmp_path):
    """The step makes no status or check-run call, so the gate stays as written.

    Mutations: `exit 1` in place of the failure branch's `exit 0`; drop the reply call; change
    the reply text; reply with `GH_TOKEN`, the dispatch token."""
    proc, calls, _ = _run(tmp_path, DISPATCH, dispatch_rc=1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _paths(calls) == [
        "repos/acme/demo/actions/workflows/shipmate.yml/dispatches",
        "repos/acme/demo/issues/42/comments",
    ]
    assert (
        "::warning::the auto apply dispatch for dev failed; comment shipmate apply to apply"
        in proc.stdout
    )
    assert (tmp_path / "reply.md.token").read_text(encoding="utf-8") == "main-token"
    assert (tmp_path / "reply.md").read_text(encoding="utf-8").splitlines()[:3] == _REPLY_HEAD
