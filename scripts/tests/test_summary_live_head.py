"""A plan run whose pull request moved past its head dispatches `shipmate plan` for the live head
when that head is not a draft and no run of it planned or is about to: no autoplan run that is
unfinished or succeeded with its `plan / detect` succeeded, and no other live `shipmate plan` run
of the pull request. The re-lists' sleeps are skipped (`PYTHON3_SHIM_NO_SLEEP`).

The steps run for real with `_loader.write_python3_shim`'s `python3` running the real
`gate-status replan` and `dispatch-body`. Each step's environment is its parsed `env:` mapping
with every expression resolved from `_EXPRESSIONS`, so a dropped binding reaches the step as an
unset variable. The `gh` stub refuses a call without `GH_TOKEN`.
"""

import json
import os
import pathlib

import pytest
from _loader import (
    ACTIONS,
    bash_only,
    fail_script,
    github_outputs,
    read_calls,
    run_step,
    step_by,
    write_python3_shim,
)

LIVEHEAD = "Check whether the live head needs a plan"
MINT = "Mint the plan dispatch token"
DISPATCH = "Dispatch a plan of the live head"
HEAD_SHA = "a" * 40
LIVE = "b" * 40
MOVED = "${{ steps.hold.outputs.moved == 'true' || steps.upsert.outputs.result == 'moved' }}"
HAS_LIVE = "${{ steps.livehead.outputs.live != '' }}"
PULL_READ = ["api", "repos/acme/demo/pulls/42"]
RUNS_LIST = [
    "api",
    "--paginate",
    (
        "repos/acme/demo/actions/workflows/shipmate.yml/runs?"
        f"event=pull_request_target&head_sha={LIVE}&per_page=100"
    ),
    "--jq",
    (
        ".workflow_runs[] | {id, event, head_sha, status, conclusion, run_attempt, "
        "actor: {login: .actor.login}, triggering_actor: {login: .triggering_actor.login}} "
        "| tojson"
    ),
]
DISPATCH_LISTS = [
    [
        "api",
        (
            "repos/acme/demo/actions/workflows/shipmate.yml/runs"
            f"?event=workflow_dispatch&status={status}&per_page=100&page=1"
        ),
    ]
    for status in ("queued", "in_progress", "waiting", "pending", "requested")
]
#: Every read of a replan that found nothing blocking: the head, the autoplans, the live plan
#: runs, three re-lists of the autoplans, the head and the live plan runs again.
REPLAN_READS = [
    PULL_READ,
    RUNS_LIST,
    *DISPATCH_LISTS,
    *[RUNS_LIST] * 3,
    PULL_READ,
    *DISPATCH_LISTS,
]
JOBS_READ = ["api", "repos/acme/demo/actions/runs/7/jobs?filter=latest&per_page=100&page=1"]
ZERO_CELL_JOBS = pathlib.Path(__file__).parent / "fixtures" / "jobs-zero-cell.json"
DISPATCH_POST = [
    "api",
    "-X",
    "POST",
    "repos/acme/demo/actions/workflows/shipmate.yml/dispatches",
    "--input",
    "-",
]
NOTICE = (
    f"::notice title=dispatch::pull request #42 moved to {LIVE}, which has no autoplan run that "
    "planned it or is in progress; dispatched shipmate plan (on demand, so no auto apply follows "
    "it)"
)


def test_the_live_head_steps_are_wired_exactly():
    """Mutations: drop either alternative of the livehead `if:`; bind the livehead `GH_TOKEN` to
    `steps.plantoken.outputs.token`; add `permission-pull-requests: write` to the mint; drop the
    mint's `continue-on-error`; dispatch with `steps.token.outputs.token`; drop `VERB: plan`;
    add `SHIPMATE_APP_SLUG: ${{ steps.token.outputs.app-slug }}` to the livehead step: the
    replan reads no slug."""
    livehead = step_by("summary", name=LIVEHEAD)
    assert (livehead["id"], livehead["if"]) == ("livehead", MOVED)
    assert livehead["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
    }
    mint = step_by("summary", name=MINT)
    assert (mint["id"], mint["if"], mint["continue-on-error"]) == ("plantoken", HAS_LIVE, True)
    assert mint["uses"] == step_by("summary", id="autotoken")["uses"]
    assert mint["with"] == {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-actions": "write",
    }
    dispatch = step_by("summary", name=DISPATCH)
    assert dispatch["if"] == HAS_LIVE
    assert dispatch["env"] == {
        "GH_TOKEN": "${{ steps.plantoken.outputs.token }}",
        "VERB": "plan",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        "DISPATCH_REF": "${{ github.event.repository.default_branch }}",
        "SHIPMATE_LIVE": "${{ steps.livehead.outputs.live }}",
    }


# A stub, not `_GH_RECORDER`: that one discards stdin and has no draft or run listing answer.
_GH = """\
#!/bin/bash
printf '%s\\n' "$@" -- >> "$CALLS"
if [ -z "${GH_TOKEN:-}" ]; then
  echo "missing GH_TOKEN" >&2
  exit 1
fi
if [[ "$*" == *dispatches* ]]; then
  cat > "$DISPATCH_BODY"
  printf '%s' "$GH_TOKEN" > "$DISPATCH_BODY.token"
  [ "$FAKE_DISPATCH_RC" = 0 ] || echo "gh: Validation Failed (HTTP 422)" >&2
  exit "$FAKE_DISPATCH_RC"
elif [[ "$2" == *event=workflow_dispatch* ]]; then
  printf '{"workflow_runs": %s}\\n' "${FAKE_DISPATCHED:-[]}"
elif [[ "$2" == */jobs\\?* ]]; then
  cat "$FAKE_JOBS"
elif [ "$2" = "--paginate" ]; then
  printf '%s' "$FAKE_RUNS"
  exit "$FAKE_RUNS_RC"
elif [[ "$2" =~ ^repos/[^/]+/[^/]+/pulls/[0-9]+$ ]]; then
  printf '{"head": {"sha": "%s"}, "draft": %s, "state": "%s"}\\n' \
    "$FAKE_HEAD" "$FAKE_DRAFT" "$FAKE_STATE"
elif [[ "$*" == *status* ]]; then
  echo '{"state": "failure", "statuses": [{"state": "failure", "context": "shipmate / gate"}]}'
else
  exit 9
fi
"""


def _listed(status, conclusion):
    return json.dumps(
        {
            "id": 7,
            "event": "pull_request_target",
            "head_sha": LIVE,
            "status": status,
            "conclusion": conclusion,
            "run_attempt": 1,
            "actor": {"login": "dev"},
            "triggering_actor": {"login": "dev"},
        }
    )


def _step_env(step, expressions):
    """The step's parsed `env:` with each `${{ }}` value resolved; an unknown one is a KeyError."""
    return {
        k: expressions[v] if v.startswith("${{") else v
        for k, v in step_by("summary", name=step)["env"].items()
    }


def _run(
    tmp_path,
    *,
    live=LIVE,
    draft="false",
    state="open",
    runs=(),
    runs_rc=0,
    plan_token="plan-token",  # noqa: S107 - a stub token, never sent
    dispatch_rc=0,
    post_rc=None,
    replan_stderr=None,
    dispatched="[]",
):
    """Run the livehead step, then the dispatch step when it set `live`, as the runner would.
    `post_rc` makes `python3` exit with it for `dispatch-body` in place of running it;
    `replan_stderr` makes `gate-status replan` print it to stderr and exit 1; `dispatched` is
    the JSON list every live `workflow_dispatch` listing answers; a jobs read answers the
    zero-cell capture. Return (stdout, stderr, gh calls, livehead outputs)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_python3_shim(bin_dir)
    if post_rc is not None:
        fail_script(bin_dir, "dispatch-body", post_rc)
    if replan_stderr is not None:
        fail_script(bin_dir, "gate-status", 1, replan_stderr, subcommand="replan")
    (bin_dir / "gh").write_text(_GH, encoding="utf-8", newline="\n")
    (bin_dir / "gh").chmod(0o755)
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    base = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    base |= {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CALLS": str(tmp_path / "calls"),
        "DISPATCH_BODY": str(tmp_path / "dispatch.json"),
        "FAKE_DISPATCH_RC": str(dispatch_rc),
        "FAKE_HEAD": live,
        "FAKE_DRAFT": draft,
        "FAKE_STATE": state,
        "FAKE_RUNS": "".join(f"{_listed(*r)}\n" for r in runs),
        "FAKE_RUNS_RC": str(runs_rc),
        "GITHUB_ACTION_PATH": str(ACTIONS / "summary"),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_RUN_ID": "999",
        "FAKE_DISPATCHED": dispatched,
        "FAKE_JOBS": str(ZERO_CELL_JOBS),
        "PYTHON3_SHIM_NO_SLEEP": "1",
    }
    expressions = {
        "${{ steps.token.outputs.token }}": "main-token",
        "${{ steps.plantoken.outputs.token }}": plan_token,
        "${{ inputs.head-sha }}": HEAD_SHA,
        "${{ inputs.pr-number }}": "42",
        "${{ github.event.repository.default_branch }}": "main",
    }
    proc = run_step(
        tmp_path, step_by("summary", name=LIVEHEAD)["run"], base | _step_env(LIVEHEAD, expressions)
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    stdout, stderr, outputs = proc.stdout, proc.stderr, github_outputs(out)
    if outputs.get("live", "") != "":
        expressions["${{ steps.livehead.outputs.live }}"] = outputs["live"]
        proc = run_step(
            tmp_path,
            step_by("summary", name=DISPATCH)["run"],
            base | _step_env(DISPATCH, expressions),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        stdout, stderr = stdout + proc.stdout, stderr + proc.stderr
    return stdout, stderr, read_calls(tmp_path / "calls"), outputs


def _sent_body(tmp_path):
    return json.loads((tmp_path / "dispatch.json").read_text(encoding="utf-8"))


@bash_only
@pytest.mark.parametrize(
    "runs",
    [
        pytest.param((), id="no-run"),
        pytest.param((("completed", "cancelled"),), id="cancelled"),
        pytest.param((("completed", "failure"),), id="failure"),
    ],
)
def test_a_live_head_without_a_live_autoplan_is_planned_on_demand(tmp_path, runs):
    """The live head's own autoplan was cancelled or failed after its hold (the stub answers a
    gate read with `failure`), or never ran: one dispatch, of the exact plan body, with the plan
    token, after the replan's reads and nothing else. Mutations: count every listed run as
    unfinished in `_unfinished` (`if True:`) -- the cancelled and failure rows dispatch nothing;
    block whenever a run is listed, before judging it -- the same rows dispatch nothing; decide
    on the gate (list `statuses` instead) -- the call list differs."""
    stdout, stderr, calls, outputs = _run(tmp_path, runs=runs)
    assert (calls, outputs) == ([*REPLAN_READS, DISPATCH_POST], {"live": LIVE})
    assert _sent_body(tmp_path) == {
        "ref": "main",
        "inputs": {"verb": "plan", "pr_number": "42"},
        "return_run_details": True,
    }
    assert (tmp_path / "dispatch.json.token").read_text(encoding="utf-8") == "plan-token"
    assert (stdout.splitlines(), stderr) == ([NOTICE], "")


@bash_only
@pytest.mark.parametrize(
    ("run", "reads", "log"),
    [
        pytest.param(
            ("completed", "success"),
            [*DISPATCH_LISTS, JOBS_READ],
            "whose autoplan run 7 succeeded",
            id="succeeded",
        ),
        pytest.param(
            ("in_progress", None), [], "which has an autoplan run 7 in progress", id="in-progress"
        ),
        pytest.param(("queued", None), [], "which has an autoplan run 7 in progress", id="queued"),
    ],
)
def test_a_live_head_with_a_live_or_planned_autoplan_is_not_planned(tmp_path, run, reads, log):
    """A duplicate plan would write newer plan records under a waiting auto apply. The
    succeeded run's jobs are the zero-cell capture: its `plan / detect` succeeded. Mutation:
    never block a completed run -- succeeded dispatches."""
    stdout, stderr, calls, outputs = _run(tmp_path, runs=(run,))
    assert (calls, outputs, stdout) == ([PULL_READ, RUNS_LIST, *reads], {}, "")
    assert stderr.splitlines() == [f"pull request #42 moved to {LIVE}, {log}; nothing dispatched"]


@bash_only
def test_a_live_dispatched_plan_of_the_pull_request_is_not_planned_again(tmp_path):
    """The step runs no second plan behind one already queued. Mutation: drop the live plan
    runs read from `_replan` -- a plan is dispatched."""
    queued = json.dumps(
        [
            {
                "id": 30,
                "display_title": "shipmate \u00b7 plan pr=42 ref= comment=",
                "event": "workflow_dispatch",
                "status": "queued",
                "actor": {"login": "dev"},
            }
        ]
    )
    stdout, stderr, calls, outputs = _run(tmp_path, dispatched=queued)
    assert (calls, outputs, stdout) == ([PULL_READ, RUNS_LIST, *DISPATCH_LISTS], {}, "")
    assert stderr.splitlines() == [
        (
            f"pull request #42 moved to {LIVE}; its shipmate plan run 30 is queued and plans the "
            "live head; nothing dispatched"
        )
    ]


@bash_only
def test_a_draft_live_head_is_not_planned(tmp_path):
    """An autoplan skips a draft; an on-demand plan would not. Mutation: drop `_replan`'s draft
    check -- the runs are listed and a plan is dispatched."""
    stdout, stderr, calls, outputs = _run(tmp_path, draft="true")
    assert (calls, outputs, stdout) == ([PULL_READ], {}, "")
    assert stderr.splitlines() == [f"pull request #42 moved to {LIVE}, a draft; nothing dispatched"]


@bash_only
def test_a_closed_pull_request_is_not_planned(tmp_path):
    """A stale run that ends after its pull request merged or closed dispatches nothing.
    Mutation: drop `_replan`'s state check -- the runs are listed and a plan is dispatched."""
    stdout, stderr, calls, outputs = _run(tmp_path, state="closed")
    assert (calls, outputs, stdout) == ([PULL_READ], {}, "")
    assert stderr.splitlines() == ["pull request #42 is closed; nothing dispatched"]


@bash_only
def test_a_head_the_pull_request_moved_back_to_is_not_planned(tmp_path):
    """Mutation: drop `_replan`'s `live == sha` return -- the runs are listed and a plan of this
    head is dispatched."""
    assert _run(tmp_path, live=HEAD_SHA) == ("", "", [PULL_READ], {})


def _warning(cause):
    return [
        (
            f"::warning::could not decide whether pull request #42 needs a plan ({cause}); "
            "comment shipmate plan to plan it"
        )
    ]


@bash_only
def test_a_failed_run_listing_warns_with_the_remedy(tmp_path):
    """Mutation: `exit 1` in place of the failure branch's `exit 0` -- the step fails."""
    stdout, stderr, calls, outputs = _run(tmp_path, runs_rc=1)
    assert (calls, outputs, stderr) == ([PULL_READ, RUNS_LIST], {}, "")
    assert stdout.splitlines() == _warning(
        f"command failed (1): gh api --paginate {RUNS_LIST[2]} --jq {RUNS_LIST[4]}"
    )


@bash_only
def test_a_retried_failed_decision_warns_on_its_last_line(tmp_path):
    """The retry notices pass through unchanged and only the script's last line enters the
    warning. Mutation: `$(tail -n 1 replan.err)` -> `$(cat replan.err)` -> the warning holds
    every line."""
    notices = (
        "::notice::command failed (1): gh api repos/acme/demo/pulls/42%0Agh: HTTP 502\n"
        "::notice::command failed (1): gh api repos/acme/demo/pulls/42%0Agh: HTTP 503\n"
    )
    failed = "command failed (1): gh api repos/acme/demo/pulls/42 | gh: HTTP 503"
    stdout, stderr, calls, outputs = _run(tmp_path, replan_stderr=notices + failed)
    assert (calls, outputs, stderr, stdout) == ([], {}, notices, f"{_warning(failed)[0]}\n")


@bash_only
def test_the_head_read_uses_the_main_token(tmp_path):
    """`run_step` ignores `env:`, so this reads the parsed binding. Mutation: drop the livehead
    step's `GH_TOKEN` binding -- the stub refuses the read and nothing is dispatched."""
    stdout, _, calls, outputs = _run(tmp_path)
    assert (calls[0], outputs) == (PULL_READ, {"live": LIVE})
    assert stdout.splitlines() == [NOTICE]


@bash_only
def test_a_failed_mint_warns_and_dispatches_nothing(tmp_path):
    """Mutation: drop the empty-token check -- the dispatch is attempted."""
    stdout, stderr, calls, _ = _run(tmp_path, plan_token="")
    assert (calls, stderr) == (REPLAN_READS, "")
    assert stdout.splitlines() == [
        (
            f"::warning::the plan dispatch token mint failed, so {LIVE} has no plan; "
            "comment shipmate plan to plan pull request #42"
        )
    ]


@bash_only
def test_a_failed_dispatch_warns_with_the_remedy(tmp_path):
    """A 4xx cannot have started a run: one POST, no retry, and the unchanged warning.
    Mutations: `exit 1` in place of the failure branch's `exit 0`; print the notice on a
    failed POST; treat every nonzero exit of `dispatch-body` as a run that may have
    started -- the may-have-started warning prints instead."""
    stdout, stderr, calls, _ = _run(tmp_path, dispatch_rc=1)
    assert calls == [*REPLAN_READS, DISPATCH_POST]
    assert stdout.splitlines() == [
        (
            f"::warning::the plan dispatch failed, so {LIVE} has no plan; "
            "comment shipmate plan to plan pull request #42"
        )
    ]
    assert "gh: Validation Failed (HTTP 422)" in stderr.splitlines()


@bash_only
@pytest.mark.parametrize("post_rc", [3, 4])
def test_a_dispatch_that_may_have_started_a_run_warns_to_check_the_actions_tab(tmp_path, post_rc):
    """`dispatch-body` exits 3 when a run started after an attempt that failed, 4 when it
    cannot tell: either way "has no plan" would be false. Mutation: send exit 3 to `not_planned`
    -- the 3 row prints the no-plan warning."""
    stdout, stderr, calls, _ = _run(tmp_path, post_rc=post_rc)
    assert (calls, stderr) == (REPLAN_READS, "")
    assert stdout.splitlines() == [
        (
            f"::warning::the plan dispatch answered with an error, so a plan of {LIVE} may have "
            "started; check the Actions tab before commenting shipmate plan on pull request #42"
        )
    ]
