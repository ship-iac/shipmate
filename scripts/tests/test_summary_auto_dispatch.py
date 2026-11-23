"""The plan summary dispatches `verb: auto` only after a complete, non-draft autoplan's first
attempt changed a cell of an `apply_mode: auto` environment, and a failed dispatch replies with
the remedy without touching the gate.

The gate conditions are fed from `gate-state.decide`'s real outputs, so a change to what
`decide` returns for a held, cancelled, draft or nothing-changed run reaches these rows.
"""

import json
import os
import re

import pytest
from _detect_fixtures import deferred_jobs
from _loader import (
    ACTIONS,
    bash_only,
    fail_script,
    github_outputs,
    jsonl,
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
        "behind": 0,
        "open_older": 0,
    } | overrides
    state, _, mode = gs.decide(**kwargs)
    return state or "", mode


_AUTOPLAN = ("pull_request_target", "synchronize", "1", "", False)

#: name -> ((event, action, attempt, is_draft, payload draft), decide overrides, whole reason);
#: each row breaks one condition.
SKIPS = {
    "on-demand plan": (
        ("workflow_dispatch", "", "1", "", False),
        {},
        "a workflow_dispatch run is not an autoplan",
    ),
    "reopened": (
        ("pull_request_target", "reopened", "1", "", False),
        {},
        "this plan was started by reopening the pull request, not by a push",
    ),
    "unreadable action": (
        ("pull_request_target", "", "1", "", False),
        {},
        "this plan's event could not be read",
    ),
    "other action": (
        ("pull_request_target", "edited", "1", "", False),
        {},
        "this plan was started by the `edited` event, not by a push",
    ),
    "push onto a draft": (
        ("pull_request_target", "synchronize", "1", "false", True),
        {},
        (
            "this plan's push landed on a draft; the run that marking it ready started decides "
            "the auto apply"
        ),
    ),
    "re-run": (
        ("pull_request_target", "synchronize", "2", "", False),
        {},
        "run attempt 2 is a re-run",
    ),
    "draft": (
        ("pull_request_target", "synchronize", "1", "true", False),
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
    """The reasons are compared whole: the plan comment shows the action reasons verbatim.

    Mutations: delete any one `if` of `action_reason` or `skip_reason` (its row reddens); compare
    only `state` with `pending` (the draft-gate row reddens: a draft autoplan's gate is pending
    too); admit `reopened` (its row reddens); swap the `reopened` and `""` reasons (both rows
    redden)."""
    (event, action, attempt, is_draft, payload_draft), overrides, reason = SKIPS[name]
    state, mode = _gate(**overrides)
    got = ad.skip_reason(
        event=event,
        action=action,
        attempt=attempt,
        payload_draft=payload_draft,
        is_draft=is_draft,
        state=state,
        mode=mode,
    )
    assert got == reason


@pytest.mark.parametrize("action", ["opened", "synchronize", "ready_for_review"])
def test_every_condition_holding_dispatches(action):
    """Mutation: compare `mode` with `nothing-changed` in place of `post`. Mutation: drop
    `opened` from the admitted actions. Mutation: drop `ready_for_review` from `AUTO_ACTIONS`
    (the ready row reddens)."""
    state, mode = _gate()
    assert (state, mode) == ("pending", "post")
    got = ad.skip_reason(
        event="pull_request_target",
        action=action,
        attempt="1",
        payload_draft=False,
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
    dispatch step; drop `SHIPMATE_AUTO_ENVS_TABLE` from the decision; drop the decision's
    `GH_TOKEN`; drop the mint's `if:`; add
    `permission-pull-requests: write` to the mint; dispatch with `steps.token.outputs.token`;
    reply with `steps.autotoken.outputs.token`."""
    decide = step_by("summary", name=DECIDE)
    assert decide["id"] == "autodecide"
    assert decide["if"] == (
        "${{ steps.hold.outputs.superseded != 'true' && steps.upsert.outputs.result != 'moved' }}"
    )
    assert decide["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_AUTO_ENVS_TABLE": "${{ inputs.auto-envs }}",
        "SHIPMATE_IS_DRAFT": "${{ inputs.is-draft }}",
        "SHIPMATE_GATE_STATE": "${{ steps.gate.outputs.state }}",
        "SHIPMATE_COMMENT_MODE": "${{ steps.gate.outputs.comment_mode }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
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


def test_the_decision_replies_on_auto_dispatchs_unread_exit_code():
    """The step keys its reply on a bare exit-code literal, which must be `UNREAD`; every
    `-eq` comparison of `$rc` in the step is that one. Mutation: renumber `UNREAD` to 4 -- red."""
    run = step_by("summary", name=DECIDE)["run"]
    assert re.findall(r'"\$rc" -eq ([0-9]+)', run) == [str(ad.UNREAD)]


# A stub, not `_GH_RECORDER`: that one discards stdin.
_GH = """\
#!/bin/bash
printf '%s\\n' "$@" -- >> "$CALLS"
if [[ "$*" == *dispatches* ]]; then
  cat > "$DISPATCH_BODY"
  [ "$FAKE_DISPATCH_RC" = 0 ] || echo "${FAKE_DISPATCH_ERR:-gh: Validation Failed (HTTP 422)}" >&2
  exit "$FAKE_DISPATCH_RC"
elif [[ "$*" == *actions/workflows/shipmate.yml/runs* ]]; then
  printf '%s' "$FAKE_RUNS"
  printf '%s' "${FAKE_RUNS_ERR:-}" >&2
  exit "${FAKE_RUNS_RC:-0}"
elif [[ "$*" == *comments* ]]; then
  cat > "$REPLY_BODY"
  printf '%s' "$GH_TOKEN" > "$REPLY_BODY.token"
fi
"""


def _run(
    tmp_path,
    step,
    *,
    cells=(),
    dispatch_rc=0,
    post_rc=None,
    token="x",  # noqa: S107 - a stub token, never sent
    event=None,
    auto='["dev"]',
    dispatch_err="",
    runs=None,
    runs_rc=0,
    runs_err="",
):
    """Run `step`'s `run:` with the gh stub; return (process, gh calls, step outputs). `event`
    is the payload text at `GITHUB_EVENT_PATH`, a `synchronize` one by default; `auto` is
    build-matrix's `auto_envs` output; `dispatch_err`, when set, is gh's stderr for a failed
    dispatch; `post_rc` makes `python3` exit with it for
    `dispatch-body` in place of running it. `runs` is the head's autoplan listing as `gh --jq`
    prints it, this run (999) alone by default; `runs_rc` is that listing's exit code and
    `runs_err` its stderr."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_python3_shim(bin_dir)
    if post_rc is not None:
        fail_script(bin_dir, "dispatch-body", post_rc)
    (bin_dir / "gh").write_text(_GH, encoding="utf-8", newline="\n")
    (bin_dir / "gh").chmod(0o755)
    for i, (env, changed) in enumerate(cells):
        d = tmp_path / "cells" / str(i)
        d.mkdir(parents=True)
        cell = {"stack": f"s{i}", "environment": env, "changed": changed}
        (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
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
        "FAKE_DISPATCH_RC": str(dispatch_rc),
        "FAKE_DISPATCH_ERR": dispatch_err,
        "FAKE_RUNS": jsonl(_autoplan(999)) if runs is None else runs,
        "FAKE_RUNS_RC": str(runs_rc),
        "FAKE_RUNS_ERR": runs_err,
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
        "SHIPMATE_AUTO_ENVS_TABLE": auto,
        "SHIPMATE_IS_DRAFT": "false",
        "SHIPMATE_GATE_STATE": "pending",
        "SHIPMATE_COMMENT_MODE": "post",
        "SHIPMATE_AUTO_ENVS": "dev",
        "VERB": "auto",
        "REF": HEAD_SHA,
        "SHIPMATE_HEAD_SHA": HEAD_SHA,
        "PR_NUMBER": "42",
        "DISPATCH_REF": "main",
    }
    env.pop("ENVIRONMENT", None)
    proc = run_step(tmp_path, step_by("summary", name=step)["run"], env)
    return proc, read_calls(tmp_path / "calls"), github_outputs(out)


def _paths(calls):
    return [next(a for a in c if a.startswith("repos/")) for c in calls]


_LISTING = (
    "repos/acme/demo/actions/workflows/shipmate.yml/runs"
    f"?event=pull_request_target&head_sha={HEAD_SHA}&per_page=100"
)


_COMMENTS = "repos/acme/demo/issues/42/comments"

_JQ = (
    ".workflow_runs[] | {id, event, head_sha, status, conclusion, run_attempt, created_at, "
    "actor: {login: .actor.login}, triggering_actor: {login: .triggering_actor.login}} | tojson"
)


def _autoplan(run_id):
    """One autoplan run of the head as `apply-gate.autoplan_runs`' `--jq` projection prints it:
    an int `id`, while `GITHUB_RUN_ID` is a str."""
    return {
        "id": run_id,
        "event": "pull_request_target",
        "head_sha": HEAD_SHA,
        "status": "in_progress",
        "conclusion": None,
        "actor": {"login": "dev"},
        "triggering_actor": {"login": "dev"},
    }


@bash_only
def test_a_changed_auto_cell_decides_to_dispatch(tmp_path):
    """The auto set comes from `SHIPMATE_AUTO_ENVS_TABLE`; the decision's one API call is the
    head's autoplan listing, where this run (998 listed before it) is the newest.

    Mutations: write `envs=` with no value; restore `ec.mode_envs(ec.read_table(), "auto")` (the
    stub answers the contents read with nothing, the read fails and a call is recorded); compare
    the listed int id with `GITHUB_RUN_ID` unconverted; compare with `!=`."""
    runs = jsonl(_autoplan(998), _autoplan(999))
    proc, calls, outputs = _run(tmp_path, DECIDE, cells=[("dev", True), ("prod", True)], runs=runs)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (_paths(calls), outputs) == ([_LISTING], {"envs": "dev"})
    assert proc.stderr == ""


@bash_only
def test_a_newer_autoplan_of_the_head_decides_nothing(tmp_path):
    """A ready-for-review run of the same head is its newest autoplan unless it completed after
    deferring, and the `auto` run would refuse this run's plan as older.
    Mutations: drop the newest check; compare with `!=`."""
    runs = jsonl(_autoplan(999), _autoplan(1000))
    proc, calls, outputs = _run(tmp_path, DECIDE, cells=[("dev", True)], runs=runs)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (_paths(calls), outputs) == ([_LISTING], {"envs": ""})
    assert proc.stderr.splitlines() == [
        (
            f"::notice::autoplan run 1000 of {HEAD_SHA} is newer than this run; no auto apply "
            "started; comment shipmate apply to apply"
        )
    ]


@bash_only
@pytest.mark.parametrize(
    "runs", [pytest.param("", id="empty"), pytest.param(jsonl(_autoplan(998)), id="older")]
)
def test_a_listing_that_lags_this_run_decides_to_dispatch_and_warns(tmp_path, runs):
    """The listing can lag this run, empty or showing only an older autoplan; the `auto` run's
    own provenance check still refuses if this run is not the newest. Mutations: treat an empty
    listing as not newest -- empty decides nothing; count any other id as newer (`!=`) -- older
    decides nothing."""
    proc, calls, outputs = _run(tmp_path, DECIDE, cells=[("dev", True)], runs=runs)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (_paths(calls), outputs) == ([_LISTING], {"envs": "dev"})
    assert proc.stderr.splitlines() == [
        (
            f"::warning::autoplan run 999 of {HEAD_SHA} is not listed yet; dispatching the auto "
            "apply, whose own provenance check refuses if this run is not the newest"
        )
    ]


@bash_only
def test_a_failed_autoplan_listing_warns_on_one_line(tmp_path):
    """gh's multi-line stderr stays inside one warning. Mutation: let the listing's `SystemExit`
    pass uncaught -- gh's second line prints on its own."""
    proc, calls, outputs = _run(
        tmp_path, DECIDE, cells=[("dev", True)], runs="", runs_rc=1, runs_err="HTTP 404\nnext"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (_paths(calls), outputs) == ([_LISTING, _COMMENTS], {})
    assert proc.stderr.splitlines() == [
        (
            f"::warning::the autoplan runs of {HEAD_SHA} could not be read (command failed (1): gh "
            f"api --paginate {_LISTING} --jq {_JQ} | HTTP 404 | next)"
        )
    ]


@bash_only
@pytest.mark.parametrize(
    ("runs", "rc"),
    [
        ("", 1),
        ("not json\n", 0),
        (json.dumps(_autoplan(999) | {"id": "999"}) + "\n", 0),
        (json.dumps({"id": 999}) + "\n", 0),
    ],
    ids=["failed", "not-json", "str-id", "missing-keys"],
)
def test_an_unreadable_autoplan_listing_decides_nothing_warns_and_replies(tmp_path, runs, rc):
    """Toward no apply: the step's degrade, a warning, no `envs`, and a reply with the step's
    token saying that no auto apply started and to comment `shipmate apply`.

    Mutations: treat a failed read as newest (the `failed` row dispatches); catch `ValueError`
    as newest (the malformed rows dispatch); drop the reply call -- no comment call, red."""
    proc, calls, outputs = _run(tmp_path, DECIDE, cells=[("dev", True)], runs=runs, runs_rc=rc)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (_paths(calls), outputs) == ([_LISTING, _COMMENTS], {})
    assert proc.stdout.splitlines() == [
        "::warning::could not decide whether to dispatch an auto apply; no auto apply started"
    ]
    assert (tmp_path / "reply.md.token").read_text(encoding="utf-8") == "x"
    assert (tmp_path / "reply.md").read_text(encoding="utf-8").splitlines()[:3] == [
        "### shipmate apply",
        "",
        (
            "🔴 failed: auto apply not started: the autoplan runs of this head could not be "
            "read; comment `shipmate apply`"
        ),
    ]


@bash_only
@pytest.mark.parametrize("auto", ["", "not json", '{"dev": 1}', '["dev", 1]'])
def test_a_missing_or_malformed_auto_set_decides_nothing_and_warns(tmp_path, auto):
    """Toward no apply: the step degrades the refusal to warnings, writes no `envs` and posts no
    reply, since no change is known to call for an auto apply.

    Mutations: treat a malformed value as `[]` (the step succeeds, `envs` is written empty);
    reply on every failure of the decision -- a comment call, red."""
    proc, calls, outputs = _run(tmp_path, DECIDE, cells=[("dev", True)], auto=auto)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (calls, outputs) == ([], {})
    assert (proc.stderr.splitlines(), proc.stdout.splitlines()) == (
        ["::warning::SHIPMATE_AUTO_ENVS_TABLE is not a JSON list of environment names"],
        ["::warning::could not decide whether to dispatch an auto apply; no auto apply started"],
    )


@bash_only
def test_no_changed_auto_cell_decides_nothing(tmp_path):
    """No changed auto cell reads no listing. Mutations: drop `if c["changed"]` (the output names
    `dev`); read the listing before the auto environments are known."""
    proc, calls, outputs = _run(tmp_path, DECIDE, cells=[("dev", False), ("prod", True)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (calls, outputs) == ([], {"envs": ""})


@bash_only
@pytest.mark.parametrize(
    ("event", "reason"),
    [
        pytest.param(
            json.dumps({"action": "reopened"}),
            "this plan was started by reopening the pull request, not by a push",
            id="reopened",
        ),
        pytest.param("not json", "this plan's event could not be read", id="unparseable"),
        pytest.param(
            json.dumps(["synchronize"]), "this plan's event could not be read", id="not-an-object"
        ),
    ],
)
def test_an_autoplan_not_started_by_a_push_decides_nothing(tmp_path, event, reason):
    """The payload is read from `GITHUB_EVENT_PATH`; an unreadable one dispatches nothing and
    makes no API call. Mutation: return `{"action": "synchronize"}` from
    `_shipmate.event_payload`'s `except` (the unparseable row dispatches) or its non-object
    fallback (the not-an-object row dispatches). Mutation: pass `GITHUB_EVENT_NAME` as the
    action (every row names the `pull_request_target` event). Mutation: read the autoplan
    listing before `skip_reason` (a call is recorded)."""
    proc, calls, outputs = _run(tmp_path, DECIDE, cells=[("dev", True)], event=event)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (calls, outputs) == ([], {"envs": ""})
    assert proc.stderr.splitlines() == [f"no auto apply dispatched: {reason}"]


def _push_by_k3np(draft):
    """A human push by `k3np`, payload `draft` as given, run 999 the only listed autoplan."""
    run = _autoplan(999) | {"actor": {"login": "k3np"}, "triggering_actor": {"login": "k3np"}}
    event = json.dumps({"action": "synchronize", "pull_request": {"draft": draft}})
    return {"cells": [("sbx", True)], "auto": '["sbx"]', "runs": jsonl(run), "event": event}


@bash_only
def test_a_push_onto_a_draft_decides_nothing(tmp_path):
    """`SHIPMATE_IS_DRAFT` is `false`: the pull request was marked ready while the run planned.
    Mutation: drop the `payload_draft` check from `skip_reason` -- `sbx` is printed, red."""
    proc, calls, outputs = _run(tmp_path, DECIDE, **_push_by_k3np(True))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (calls, outputs) == ([], {"envs": ""})
    assert proc.stderr.splitlines() == [
        (
            "no auto apply dispatched: this plan's push landed on a draft; the run that marking "
            "it ready started decides the auto apply"
        )
    ]


@bash_only
def test_a_push_onto_a_ready_pull_request_decides_to_dispatch(tmp_path):
    """Mutation: refuse whenever the payload holds `pull_request.draft` (`"draft" in pull`) --
    nothing is printed, red."""
    proc, calls, outputs = _run(tmp_path, DECIDE, **_push_by_k3np(False))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (_paths(calls), outputs) == ([_LISTING], {"envs": "sbx"})
    assert proc.stderr == ""


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
    """The reply is posted by the plan run, and its footer names that run. Mutations: drop the
    empty-token check (the dispatch is attempted); reply with `GH_TOKEN`, the empty dispatch
    token, in place of `REPLY_TOKEN`; drop `SHIPMATE_REPLY_LABEL` from `reply` (the footer reads
    `comment run`)."""
    proc, calls, _ = _run(tmp_path, DISPATCH, token="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _paths(calls) == ["repos/acme/demo/issues/42/comments"]
    assert (
        "::warning::the auto apply dispatch token mint for dev failed; comment shipmate apply to "
        "apply" in proc.stdout
    )
    assert (tmp_path / "reply.md.token").read_text(encoding="utf-8") == "main-token"
    assert (tmp_path / "reply.md").read_text(encoding="utf-8").splitlines()[:3] == _REPLY_HEAD
    assert (tmp_path / "reply.md").read_text(encoding="utf-8").splitlines()[-1] == (
        "[plan run](https://example.invalid/acme/demo/actions/runs/999). Comment `shipmate help` "
        "for the available commands."
    )


@bash_only
def test_a_failed_dispatch_replies_and_warns_and_leaves_the_gate(tmp_path):
    """The step makes no status or check-run call, so the gate stays as written. A 4xx cannot
    have started a run: one POST, no retry.

    Mutations: `exit 1` in place of the failure branch's `exit 0`; drop the reply call; change
    the reply text; reply with `GH_TOKEN`, the dispatch token; treat every nonzero exit of
    `dispatch-body` as a run that may have started -- the reply differs; send dispatch-body's
    stderr to /dev/null, and gh's answer leaves the log."""
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
    assert (
        "::warning::command failed (1): gh api -X POST "
        "repos/acme/demo/actions/workflows/shipmate.yml/dispatches --input - | "
        "gh: Validation Failed (HTTP 422)"
    ) in proc.stderr.splitlines()
    assert (tmp_path / "reply.md.token").read_text(encoding="utf-8") == "main-token"
    assert (tmp_path / "reply.md").read_text(encoding="utf-8").splitlines()[:3] == _REPLY_HEAD


@bash_only
def test_a_dispatch_to_a_fence_without_its_inputs_logs_and_replies_the_repin_remedy(tmp_path):
    """The auto dispatch prints `dispatch-body`'s skew remedy, which names `repin
    --write-fence`, and the reply carries it in place of the advice to comment `shipmate apply`,
    which would meet the same skew. The step still degrades.

    Mutations: send dispatch-body's stderr to /dev/null -> the remedy leaves the log; call
    `not_started` without `"$out"` -> the reply advises commenting."""
    proc, _, _ = _run(
        tmp_path,
        DISPATCH,
        dispatch_rc=1,
        dispatch_err='gh: Unexpected inputs provided: ["comment_id"] (HTTP 422)',
    )
    remedy = (
        "This repository's .github/workflows/shipmate.yml does not declare every input this "
        "engine's dispatch sends, so it is older than the engine pin. Write the published file "
        "over it with `python <engine>/scripts/repin <consumer> <tag> --write-fence`."
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"::error::{remedy}" in proc.stderr.splitlines(), proc.stderr
    assert (tmp_path / "reply.md").read_text(encoding="utf-8").splitlines()[:3] == [
        "### shipmate apply",
        "",
        f"🔴 failed: auto apply not started: the dispatch failed. {remedy}",
    ]


@bash_only
@pytest.mark.parametrize(("post_rc", "verdict"), [(3, "⚪ "), (4, "🔴 failed: ")])
def test_a_dispatch_that_may_have_started_a_run_replies_to_check_the_actions_tab(
    tmp_path, post_rc, verdict
):
    """`dispatch-body` exits 3 when a run started after an attempt that failed, a notice as
    in comment-ops, and 4 when it cannot tell, a failure: either way "not started" would be false.
    Mutations: reply the `auto apply not started` text on exit 3 and 4 -- both rows redden;
    reply `failed` on exit 3 -- its row reddens."""
    proc, calls, _ = _run(tmp_path, DISPATCH, post_rc=post_rc)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _paths(calls) == ["repos/acme/demo/issues/42/comments"]
    assert proc.stdout.splitlines() == [
        (
            "::warning::the auto apply dispatch for dev answered with an error, and an auto run "
            "may have started; check the Actions tab before commenting shipmate apply"
        )
    ]
    assert (tmp_path / "reply.md.token").read_text(encoding="utf-8") == "main-token"
    assert (tmp_path / "reply.md").read_text(encoding="utf-8").splitlines()[:3] == [
        "### shipmate apply",
        "",
        (
            f"{verdict}auto apply may have started: the dispatch answered with an error; check "
            "the Actions tab before commenting `shipmate apply`"
        ),
    ]


@pytest.mark.parametrize(
    ("status", "conclusion", "newer", "reads"),
    [
        pytest.param("completed", "success", None, 1, id="deferred"),
        pytest.param("in_progress", None, 1000, 0, id="ready-in-progress"),
    ],
)
def test_a_re_run_of_the_push_run_reads_past_a_deferred_ready_run_only(
    monkeypatch, status, conclusion, newer, reads
):
    """Run 999 is the push run on attempt 2 and 1000 a ready run whose jobs show it deferred: an
    ended 1000 leaves 999 the newest (a re-run, so nothing dispatches), a live one is newer and
    its jobs are not read. Mutation: read past any run whose `defer` job succeeded, whatever the
    run's status (ready-in-progress returns None)."""
    ready = _autoplan(1000) | {"status": status, "conclusion": conclusion, "run_attempt": 1}
    listing = jsonl(_autoplan(999) | {"run_attempt": 2}, ready)
    calls = []

    def run(args):
        calls.append(args)
        return deferred_jobs() if "/actions/runs/1000/jobs?" in args[2] else listing

    monkeypatch.setattr(ad, "_run", run)
    assert ad.newer_autoplan("acme/demo", HEAD_SHA, "999") == newer
    assert len(calls) == 1 + reads


@pytest.mark.parametrize("code", [None, 1], ids=["none", "int"])
def test_a_listing_exit_without_text_names_a_fixed_cause(monkeypatch, capsys, code):
    """A `SystemExit` with no text names `gh failed`, not `None` or `1`. Mutation: print
    `str(exc.code)`."""

    def fail(*_, **__):
        raise SystemExit(code)

    monkeypatch.setattr(ad.ag, "newest_autoplan", fail)
    with pytest.raises(SystemExit) as exc:
        ad.newer_autoplan("acme/demo", HEAD_SHA, "999")
    assert exc.value.code == ad.UNREAD
    assert capsys.readouterr().err == (
        f"::error::the autoplan runs of {HEAD_SHA} could not be read (gh failed)\n"
    )
