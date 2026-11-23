"""The plan summary dispatches `verb: auto` only after a complete, non-draft autoplan's first
attempt changed a cell of an `apply_mode: auto` environment, and a failed dispatch replies with
the remedy without touching the gate.

The gate conditions are fed from `gate-state.decide`'s real outputs, so a change to what
`decide` returns for a held, cancelled, draft or nothing-changed run reaches these rows.
"""

import base64
import json
import os
import re

import pytest
from _detect_fixtures import (
    APP_ID,
    _apply_check,
    deferred_in_progress_jobs,
    deferred_jobs,
    listed_autoplan,
)
from _loader import (
    ACTIONS,
    action_yaml,
    bash_only,
    fail_script,
    github_outputs,
    jsonl,
    load_script,
    local_action,
    read_calls,
    run_step,
    step_by,
    write_python3_shim,
)

ad = load_script("auto-dispatch")
gs = load_script("gate-state")

AD = "auto-dispatch"
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


def test_the_summary_decides_and_calls_the_dispatch_composite_exactly():
    """Only a run whose upsert did not report a newer head decides, and only a decision naming
    environments calls `actions/auto-dispatch`, which replies with the summary's main token.

    Mutations: drop `&& steps.upsert.outputs.result != 'moved'`; drop `SHIPMATE_AUTO_ENVS_TABLE`
    from the decision; drop the decision's `GH_TOKEN`; drop the call's `if:`; pass
    `deferred-to` to the call; reply with another token."""
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
    assert step_by("summary", name=DISPATCH) == {
        "name": DISPATCH,
        "if": "${{ steps.autodecide.outputs.envs != '' }}",
        "uses": local_action(AD),
        "with": {
            "envs": "${{ steps.autodecide.outputs.envs }}",
            "head-sha": "${{ inputs.head-sha }}",
            "pr-number": "${{ inputs.pr-number }}",
            "app-id": "${{ inputs.app-id }}",
            "private-key": "${{ inputs.private-key }}",
            "reply-token": "${{ steps.token.outputs.token }}",
        },
    }


_DEFERRED = "${{ inputs.deferred-to != '' }}"
_EITHER = "${{ inputs.envs != '' || steps.decide.outputs.envs != '' }}"
_MINT = "actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510"
_APP = {
    "client-id": "${{ inputs.app-id }}",
    "private-key": "${{ inputs.private-key }}",
    "owner": "${{ github.repository_owner }}",
    "repositories": "${{ github.event.repository.name }}",
}


def test_the_composite_runs_its_steps_in_order_each_under_its_mode():
    """The deferred decision verifies the key, installs PyYAML for the table and mints its own
    token before deciding; the dispatch mint and dispatch run for either mode's environments.

    Mutations: delete the `python-yaml` step; swap the two mints; drop the deferred `if:` of
    the main mint; dispatch on `steps.decide.outputs.envs != ''` alone (a summary call, which
    passes `envs`, dispatches nothing)."""
    steps = [
        (s.get("name") or s.get("uses"), s.get("id"), s.get("if"))
        for s in action_yaml(AD)["runs"]["steps"]
    ]
    assert steps == [
        ("Verify the App key arrived", None, _DEFERRED),
        (local_action("python-yaml"), None, _DEFERRED),
        ("Mint App installation token", "maintoken", _DEFERRED),
        (DECIDE, "decide", _DEFERRED),
        (MINT, "autotoken", _EITHER),
        (DISPATCH, None, _EITHER),
    ]
    assert step_by(AD, name="Verify the App key arrived")["uses"] == local_action("verify-app-key")


def test_the_main_mint_reads_and_replies_but_never_dispatches():
    """The decision reads runs, checks, the table and the gate, and replies; only the dispatch
    mint holds `actions: write`. A failed main mint degrades: the decision's first read then
    fails, warns and dispatches nothing.

    Mutations: add `permission-actions: write`; drop `permission-pull-requests: write`; drop
    the main mint's `continue-on-error`."""
    mint = step_by(AD, id="maintoken")
    assert mint["uses"].split(" ")[0] == _MINT
    assert mint["continue-on-error"] is True
    assert mint["with"] == _APP | {
        "permission-actions": "read",
        "permission-checks": "read",
        "permission-contents": "read",
        "permission-statuses": "read",
        "permission-pull-requests": "write",
    }


def test_the_dispatch_steps_are_wired_exactly():
    """Only a decision naming environments, the caller's or the deferred one's, mints the
    `actions: write` token and dispatches. A failed dispatch mint still runs the dispatch step,
    whose empty-token branch replies. No `ENVIRONMENT`: dispatch-body adds that key when it is
    set, and `auto` takes none.

    Mutations: add `&& steps.autotoken.outputs.token != ''` to the dispatch `if:`; add
    `ENVIRONMENT: x` to the dispatch step; add `permission-pull-requests: write` to the mint;
    dispatch with `steps.maintoken.outputs.token`; reply with `steps.autotoken.outputs.token`;
    drop `inputs.envs ||` from `SHIPMATE_AUTO_ENVS`."""
    mint = step_by(AD, name=MINT)
    assert mint["continue-on-error"] is True
    assert mint["uses"].split(" ")[0] == _MINT
    assert mint["with"] == _APP | {"permission-actions": "write"}
    dispatch = step_by(AD, name=DISPATCH)
    assert dispatch["if"] == _EITHER
    assert dispatch["env"] == {
        "GH_TOKEN": "${{ steps.autotoken.outputs.token }}",
        "REPLY_TOKEN": "${{ inputs.reply-token || steps.maintoken.outputs.token }}",
        "SHIPMATE_AUTO_ENVS": "${{ inputs.envs || steps.decide.outputs.envs }}",
        "VERB": "auto",
        "REF": "${{ inputs.head-sha }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        "DISPATCH_REF": "${{ github.event.repository.default_branch }}",
    }


_DECIDE_RUN = """\
set -euo pipefail
# Reviewer-facing degrade only: the plan run this one deferred to wrote the gate.
rc=0
envs=$(python3 "$GITHUB_ACTION_PATH/../../scripts/auto-dispatch" deferred "$DEFERRED_TO" 2>decide.err) || rc=$?
if [ "$rc" -ne 0 ]; then
  sed 's/^::error::/::warning::/' decide.err >&2
  echo "::warning::could not decide whether to dispatch an auto apply; no auto apply started"
  # Exit 3 (UNREAD in scripts/auto-dispatch): a change called for an auto apply, so the
  # pull request gets the same reply as a failed dispatch.
  if [ "$rc" -eq 3 ]; then
    SHIPMATE_REPLY_VERB=apply SHIPMATE_REPLY_LABEL="plan run" SHIPMATE_REPLY_OUTCOME=failed \\
      SHIPMATE_REPLY_TEXT="auto apply not started: what decides it for this ready-for-review run could not be read; comment \\`shipmate apply\\`" \\
      python3 "$GITHUB_ACTION_PATH/../../scripts/reply-comment" || echo "::warning::could not post the auto apply reply"
  fi
  exit 0
fi
cat decide.err >&2
echo "envs=$envs" >> "$GITHUB_OUTPUT"
"""  # noqa: E501 - the step's lines, compared whole


def test_the_deferred_decision_is_wired_exactly():
    """The decision reads with the main token, finds the App's gate through its slug, and
    resolves the table's variable references.

    Mutations: drop `SHIPMATE_APP_SLUG`; drop `DEFERRED_TO`; drop `SHIPMATE_GITHUB_VARS`; call
    `scripts/auto-dispatch` by a relative path; drop the failure branch's `exit 0`; drop the
    `rc -eq 3` reply; drop `cat decide.err >&2`."""
    decide = step_by(AD, name=DECIDE)
    assert decide["env"] == {
        "GH_TOKEN": "${{ steps.maintoken.outputs.token }}",
        "SHIPMATE_APP_SLUG": "${{ steps.maintoken.outputs.app-slug }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        "DEFERRED_TO": "${{ inputs.deferred-to }}",
    }
    assert decide["run"] == _DECIDE_RUN


@pytest.mark.parametrize("action", ["summary", AD])
def test_the_decision_replies_on_auto_dispatchs_unread_exit_code(action):
    """The step keys its reply on a bare exit-code literal, which must be `UNREAD`; every
    `-eq` comparison of `$rc` in the step is that one. Mutation: renumber `UNREAD` to 4 -- red."""
    run = step_by(action, name=DECIDE)["run"]
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
    action="summary",
):
    """Run `step`'s `run:` with the gh stub; return (process, gh calls, step outputs). `event`
    is the payload text at `GITHUB_EVENT_PATH`, a `synchronize` one by default; `auto` is
    build-matrix's `auto_envs` output; `dispatch_err`, when set, is gh's stderr for a failed
    dispatch; `post_rc` makes `python3` exit with it for
    `dispatch-body` in place of running it. `runs` is the head's autoplan listing as `gh --jq`
    prints it, this run (999) alone by default; `runs_rc` is that listing's exit code and
    `runs_err` its stderr. `action` names the composite holding `step`."""
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
        "GITHUB_ACTION_PATH": str(ACTIONS / action),
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
    proc = run_step(tmp_path, step_by(action, name=step)["run"], env)
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
    proc, calls, _ = _run(tmp_path, DISPATCH, action=AD)
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
    proc, calls, _ = _run(tmp_path, DISPATCH, action=AD, token="")
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
    proc, calls, _ = _run(tmp_path, DISPATCH, action=AD, dispatch_rc=1)
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
        action=AD,
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
    proc, calls, _ = _run(tmp_path, DISPATCH, action=AD, post_rc=post_rc)
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


# A stub for the deferred decision: each read answers the file `$FAKE/<key>`, or fails with a 404
# when it is absent; a reply is saved to `$FAKE/reply.md`.
_DEFERRED_GH = """\
#!/bin/bash
printf '%s\\n' "$@" -- >> "$CALLS"
case "$*" in
  *users/*) key=user ;;
  *statuses*) key=statuses ;;
  *check-runs*) key=checks ;;
  *contents/*) key=table ;;
  *workflows/shipmate.yml/runs*) key=runs ;;
  */jobs[?]*) key=jobs ;;
  *collaborators/*) key=permission ;;
  *issues/*/comments*) cat > "$FAKE/reply.md"; exit 0 ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
[ -e "$FAKE/$key" ] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
cat "$FAKE/$key"
"""

P, R = 1000, 1001
_TABLE = """\
layout: folder
environments:
  sbx:
    apply_mode: auto
    region: {vars: SBX_REGION}
  dev-eu:
    apply_mode: targeted
"""


def _table(text):
    """The contents-API body of the environment table `text`."""
    return json.dumps({"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"})


_NO_AUTO_TABLE = "layout: folder\nenvironments:\n  sbx:\n    apply_mode: targeted\n"


def _sbx(env="sbx", *, check=None, **kw):
    """A queued apply check of `env` for plan run P, a push onto a draft: `kw` overrides the
    record's arguments and `check` the check-run's keys."""
    record = {"plan_run": str(P), "cells": 2, "draft": True} | kw
    return _apply_check("stacks/app", env, **record) | (check or {})


def _gate_status(state="pending", run_id=P):
    return {
        "context": "shipmate / gate",
        "state": state,
        "target_url": f"https://example.invalid/acme/demo/actions/runs/{run_id}",
        "creator": 7,
    }


def _ready(run_id=R, login="octocat"):
    """The ready-for-review run, still dispatching, clicked by `login`."""
    actor = {"login": login}
    return listed_autoplan(
        run_id, HEAD_SHA, status="in_progress", conclusion=None, run_attempt=1
    ) | {"actor": actor, "triggering_actor": actor}


def _push(run_id=P):
    return listed_autoplan(
        run_id, HEAD_SHA, status="completed", conclusion="success", run_attempt=1
    )


def _deferred_answers(**overrides):
    """What each read answers for a deferred run R of plan run P: a pending gate of P, a queued
    `sbx` check and a queued `dev-eu` one of P, a table where only `sbx` is `auto`, R
    deferred above P, and `write` for R's clicker `octocat`. An override of None makes that read
    fail."""
    answers = {
        "user": json.dumps({"id": 7}),
        "statuses": jsonl(_gate_status()),
        "checks": jsonl(_sbx(), _sbx("dev-eu", check={"id": 2})),
        "table": _table(_TABLE),
        "runs": jsonl(_ready(), _push()),
        "jobs": deferred_in_progress_jobs(),
        "permission": json.dumps({"permission": "write"}),
    }
    return answers | overrides


def _decide_deferred(tmp_path, *, action="ready_for_review", attempt="1", token="x", **answers):  # noqa: S107 - a stub token
    """Run the composite's decision for run R deferring to P; return (process, outputs, reply
    or None). Each keyword in `answers` replaces one read's answer (`_deferred_answers`)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_python3_shim(bin_dir)
    (bin_dir / "gh").write_text(_DEFERRED_GH, encoding="utf-8", newline="\n")
    (bin_dir / "gh").chmod(0o755)
    fake = tmp_path / "fake"
    fake.mkdir()
    for key, body in _deferred_answers(**answers).items():
        if body is not None:
            (fake / key).write_text(body, encoding="utf-8")
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    payload = tmp_path / "event.json"
    payload.write_text(
        json.dumps({"action": action, "pull_request": {"draft": False}}), encoding="utf-8"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHON3_SHIM_NO_SLEEP": "1",
        "CALLS": str(tmp_path / "calls"),
        "FAKE": str(fake),
        "GITHUB_ACTION_PATH": str(ACTIONS / AD),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_SERVER_URL": "https://example.invalid",
        "GITHUB_RUN_ID": str(R),
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_EVENT_PATH": str(payload),
        "GITHUB_RUN_ATTEMPT": attempt,
        "GH_TOKEN": token,
        "SHIPMATE_APP_SLUG": "shipmate-test" if token else "",
        "SHIPMATE_APP_ID": APP_ID,
        "SHIPMATE_HEAD_SHA": HEAD_SHA,
        "SHIPMATE_GITHUB_VARS": json.dumps({"SBX_REGION": "eu-west-1"}),
        "PR_NUMBER": "42",
        "DEFERRED_TO": str(P),
    }
    env.pop("SHIPMATE_GATE_STATE", None)
    proc = run_step(tmp_path, step_by(AD, name=DECIDE)["run"], env)
    reply = fake / "reply.md"
    return (
        proc,
        github_outputs(out),
        (reply.read_text(encoding="utf-8") if reply.exists() else None),
    )


@bash_only
def test_a_deferred_ready_run_dispatches_the_changed_auto_environments(tmp_path):
    """The queued `dev-eu` check of P is `targeted` in the table, so only `sbx` is printed; the
    table resolves a variable reference; P's records say draft, which the clicker authorizes.

    Mutation: skip the table filter (use every queued environment) -- `dev-eu sbx` is printed."""
    proc, outputs, reply = _decide_deferred(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (outputs, reply, proc.stderr) == ({"envs": "sbx"}, None, "")


_NOT_PENDING_P = (
    f"the newest gate of {HEAD_SHA} is {{}} from run {{}}, not pending from plan run {P}"
)
_SOURCE = (
    f"the autoplans of {HEAD_SHA} resolve to plan run {{}} authorized by run {{}}, not plan run "
    f"{P} authorized by this run {R}"
)
_NO_AUTO = "no changed cell is in an apply_mode auto environment"

#: name -> (decision overrides, whole logged reason); each row breaks one check.
DEFERRED_SKIPS = {
    # Mutation: drop the gate-owner check (compare only the state).
    "gate of another run": (
        {"statuses": jsonl(_gate_status(run_id=999))},
        _NOT_PENDING_P.format("pending", 999),
    ),
    # Mutation: accept a `success` gate.
    "gate success": (
        {"statuses": jsonl(_gate_status("success"))},
        _NOT_PENDING_P.format("success", P),
    ),
    # Mutation: count a check of any status.
    "only completed checks": (
        {"checks": jsonl(_sbx(check={"status": "completed", "conclusion": "success"}))},
        _NO_AUTO,
    ),
    # Mutation: drop the `behind_names` filter.
    "behind check": (
        {
            "checks": jsonl(
                _sbx(
                    check={
                        "external_id": json.dumps(
                            {"plan_run": str(P), "action": "synchronize", "behind": True}
                        )
                    }
                )
            )
        },
        _NO_AUTO,
    ),
    # Mutation: drop the record-run filter.
    "record of another run": ({"checks": jsonl(_sbx(plan_run="999"))}, _NO_AUTO),
    # Mutation: drop the `AUTO_ACTIONS` filter.
    "record action reopened": ({"checks": jsonl(_sbx(action="reopened"))}, _NO_AUTO),
    # Mutation: split the name by hand (`name.split(" / ")[1]`) in place of `parse_check_name`.
    "name outside the grammar": ({"checks": jsonl(_sbx(check={"name": "apply / sbx"}))}, _NO_AUTO),
    # Mutation: drop the plan-id check.
    "plan of another run": (
        {"runs": jsonl(_ready(), _push(999))},
        _SOURCE.format(999, R),
    ),
    # Mutation: drop the authorizer check.
    "authorizer of another run": (
        {"runs": jsonl(_ready(2002), _push())},
        _SOURCE.format(P, 2002),
    ),
    # Mutation: drop the `pusher_reason` call.
    "bot clicker": (
        {"runs": jsonl(_ready(login="renovate[bot]"), _push())},
        (
            "not authorized: the autoplan was started by @renovate[bot], a bot account, which "
            "auto never authorizes; a writer comments `shipmate apply`."
        ),
    ),
    # Mutation: drop the `pusher_reason` call.
    "read clicker": (
        {"permission": json.dumps({"permission": "read"})},
        (
            "not authorized: `shipmate apply` needs write access to this repository, and the "
            "autoplan's actor @octocat's permission is `read`."
        ),
    ),
}


@bash_only
@pytest.mark.parametrize("name", list(DEFERRED_SKIPS))
def test_a_deferred_ready_run_failing_one_check_dispatches_nothing(tmp_path, name):
    """Each row's mutation, in its comment above the row, prints `sbx` for that row."""
    answers, reason = DEFERRED_SKIPS[name]
    proc, outputs, reply = _decide_deferred(tmp_path, **answers)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (outputs, reply) == ({"envs": ""}, None)
    assert proc.stderr.splitlines() == [f"no auto apply dispatched: {reason}"]


@bash_only
@pytest.mark.parametrize(
    ("event", "reason"),
    [
        pytest.param({"attempt": "2"}, "run attempt 2 is a re-run", id="attempt-2"),
        pytest.param(
            {"action": "reopened"},
            "this plan was started by reopening the pull request, not by a push",
            id="reopened",
        ),
    ],
)
def test_a_deferred_re_run_or_reopen_dispatches_nothing(tmp_path, event, reason):
    """Mutation: pass `attempt="1"` to `skip_reason` in place of `GITHUB_RUN_ATTEMPT` (the
    attempt-2 row prints `sbx`)."""
    proc, outputs, reply = _decide_deferred(tmp_path, **event)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (outputs, reply) == ({"envs": ""}, None)
    assert proc.stderr.splitlines() == [f"no auto apply dispatched: {reason}"]


_DECIDE_FAILED = (
    "::warning::could not decide whether to dispatch an auto apply; no auto apply started"
)


@bash_only
@pytest.mark.parametrize(
    "answers",
    [
        pytest.param({"statuses": None, "table": _table(_NO_AUTO_TABLE)}, id="gate"),
        pytest.param({"checks": None}, id="check-runs"),
        pytest.param({"table": None}, id="table"),
    ],
)
def test_a_failed_read_before_a_change_is_known_warns_and_posts_nothing(tmp_path, answers):
    """Checks 1, 3 and 4 exit 1: nothing yet says an auto apply was called for, so no reply.
    The gate row's table holds no `auto` environment either.

    Mutation, per row: exit `UNREAD` when that read fails (wrap it in `_listed`) -- a reply is
    posted, red."""
    proc, outputs, reply = _decide_deferred(tmp_path, **answers)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (outputs, reply) == ({}, None)
    assert proc.stdout.splitlines() == [_DECIDE_FAILED]


@bash_only
def test_an_empty_main_token_warns_and_posts_nothing(tmp_path):
    """A failed main mint leaves the token and the slug empty: the gate read refuses, and the
    step degrades. Mutation: `exit 1` in place of the failure branch's `exit 0`."""
    proc, outputs, reply = _decide_deferred(tmp_path, token="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (outputs, reply) == ({}, None)
    assert proc.stdout.splitlines() == [_DECIDE_FAILED]
    assert proc.stderr.splitlines()[0].startswith("::warning::SHIPMATE_APP_SLUG is empty")


@bash_only
def test_a_failed_autoplan_listing_after_a_change_is_known_replies(tmp_path):
    """Mutation: exit 1 in place of `UNREAD` when `auto_source` fails -- no reply, red."""
    proc, outputs, reply = _decide_deferred(tmp_path, runs=None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs == {}
    assert proc.stdout.splitlines() == [_DECIDE_FAILED]
    assert reply.splitlines()[:3] == [
        "### shipmate apply",
        "",
        (
            "🔴 failed: auto apply not started: what decides it for this ready-for-review run "
            "could not be read; comment `shipmate apply`"
        ),
    ]
