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
    load_script,
    read_calls,
    run_step,
    step_by,
    write_python3_shim,
)

ad = load_script("auto-dispatch")
gs = load_script("gate-state")

STEP = "Dispatch an auto apply"
HEAD_SHA = "a" * 40


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


#: name -> (event, attempt, is_draft, decide overrides); each row breaks one condition.
SKIPS = {
    "on-demand plan": ("workflow_dispatch", "1", "", {}),
    "re-run": ("pull_request_target", "2", "", {}),
    "draft": ("pull_request_target", "1", "true", {}),
    "detect failed": ("pull_request_target", "1", "", {"detect_result": "failure"}),
    "plan cancelled": ("pull_request_target", "1", "", {"plan_result": "cancelled"}),
    "evidence gap": ("pull_request_target", "1", "", {"planned_cells": "2"}),
    "draft gate": (
        "pull_request_target",
        "1",
        "",
        {"detect_result": "skipped", "is_draft": True},
    ),
    "nothing pending": ("pull_request_target", "1", "", {"pending": False}),
    "nothing changed": (
        "pull_request_target",
        "1",
        "",
        {"pending": False, "planned_cells": "0", "cell_count": 0},
    ),
}


@pytest.mark.parametrize("name", list(SKIPS))
def test_each_failed_condition_dispatches_nothing(name):
    """Mutations: delete any one `if` of `skip_reason` (its row reddens); compare only `state`
    with `pending` (the draft-gate row reddens: a draft autoplan's gate is pending too)."""
    event, attempt, is_draft, overrides = SKIPS[name]
    state, mode = _gate(**overrides)
    reason = ad.skip_reason(event=event, attempt=attempt, is_draft=is_draft, state=state, mode=mode)
    assert reason is not None


def test_every_condition_holding_dispatches():
    """Mutation: compare `mode` with `nothing-changed` in place of `post`."""
    state, mode = _gate()
    assert (state, mode) == ("pending", "post")
    assert (
        ad.skip_reason(
            event="pull_request_target", attempt="1", is_draft="false", state=state, mode=mode
        )
        is None
    )


def test_only_changed_cells_of_auto_environments_count():
    """Mutation: drop `if c["changed"]`."""
    cells = [
        {"stack": "a", "environment": "dev", "changed": True},
        {"stack": "b", "environment": "stage", "changed": False},
        {"stack": "c", "environment": "prod", "changed": True},
    ]
    assert ad.changed_auto_envs(cells, ["dev", "stage"]) == ["dev"]


def test_the_step_env_is_exactly_these_values():
    """No `ENVIRONMENT`: dispatch-body adds that key when it is set, and `auto` takes none.

    Mutations: add `ENVIRONMENT: x`; drop `SHIPMATE_GITHUB_VARS` (a table holding a variable
    reference then refuses)."""
    step = step_by("summary", name=STEP)
    assert step["if"] == "${{ steps.hold.outputs.superseded != 'true' }}"
    assert step["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
        "SHIPMATE_IS_DRAFT": "${{ inputs.is-draft }}",
        "SHIPMATE_GATE_STATE": "${{ steps.gate.outputs.state }}",
        "SHIPMATE_COMMENT_MODE": "${{ steps.gate.outputs.comment_mode }}",
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
fi
"""

TABLE = "environments:\n  dev:\n    apply_mode: auto\n    review: false\n  prod: {}\n"


def _run(tmp_path, *, cells, dispatch_rc=0):
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
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CALLS": str(tmp_path / "calls"),
        "DISPATCH_BODY": str(tmp_path / "dispatch.json"),
        "REPLY_BODY": str(tmp_path / "reply.md"),
        "FAKE_CONTENTS": json.dumps(blob),
        "FAKE_DISPATCH_RC": str(dispatch_rc),
        "GITHUB_ACTION_PATH": str(ACTIONS / "summary"),
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_SERVER_URL": "https://example.invalid",
        "GITHUB_RUN_ID": "999",
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_RUN_ATTEMPT": "1",
        "GH_TOKEN": "x",
        "SHIPMATE_GITHUB_VARS": "",
        "SHIPMATE_IS_DRAFT": "false",
        "SHIPMATE_GATE_STATE": "pending",
        "SHIPMATE_COMMENT_MODE": "post",
        "VERB": "auto",
        "REF": HEAD_SHA,
        "PR_NUMBER": "42",
        "DISPATCH_REF": "main",
    }
    env.pop("ENVIRONMENT", None)
    proc = run_step(tmp_path, step_by("summary", name=STEP)["run"], env)
    return proc, read_calls(tmp_path / "calls")


def _paths(calls):
    return [next(a for a in c if a.startswith("repos/")) for c in calls]


@bash_only
def test_a_changed_auto_cell_dispatches_the_exact_body(tmp_path):
    """Mutations: send `ENVIRONMENT` into dispatch-body; dispatch to another file than
    `shipmate.yml`."""
    proc, calls = _run(tmp_path, cells=[("dev", True), ("prod", True)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _paths(calls) == [
        "repos/acme/demo/contents/.github/shipmate-config.yml",
        "repos/acme/demo/actions/workflows/shipmate.yml/dispatches",
    ]
    assert json.loads((tmp_path / "dispatch.json").read_text(encoding="utf-8")) == {
        "ref": "main",
        "inputs": {"verb": "auto", "ref": HEAD_SHA, "pr_number": "42"},
        "return_run_details": True,
    }


@bash_only
def test_no_changed_auto_cell_dispatches_nothing(tmp_path):
    """Mutation: drop the `[ -n "$envs" ] || exit 0` line."""
    proc, calls = _run(tmp_path, cells=[("dev", False), ("prod", True)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _paths(calls) == ["repos/acme/demo/contents/.github/shipmate-config.yml"]


@bash_only
def test_a_failed_dispatch_replies_and_warns_and_leaves_the_gate(tmp_path):
    """The step makes no status or check-run call, so the gate stays as written.

    Mutations: `exit 1` in place of the failure branch's `exit 0`; drop the reply call; change
    the reply text."""
    proc, calls = _run(tmp_path, cells=[("dev", True)], dispatch_rc=1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _paths(calls) == [
        "repos/acme/demo/contents/.github/shipmate-config.yml",
        "repos/acme/demo/actions/workflows/shipmate.yml/dispatches",
        "repos/acme/demo/issues/42/comments",
    ]
    assert (
        "::warning::the auto apply dispatch for dev failed; comment shipmate apply to apply"
        in proc.stdout
    )
    reply = (tmp_path / "reply.md").read_text(encoding="utf-8")
    assert reply.splitlines()[:3] == [
        "### shipmate apply",
        "",
        "🔴 failed: auto apply not started: the dispatch failed; comment `shipmate apply`",
    ]
