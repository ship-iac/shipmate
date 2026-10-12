"""A draft autoplan's gate is written only over a head that carries none; a `pending` gate is
refreshed in the same step.

gate-state decides a draft autoplan `pending` with comment mode `draft`. `gate-refresh` treats
only `failure` as a hold and greens `pending` once every apply check is done, so a draft status
written over a held head -- a re-run of an old draft run, or a draft reopened at the same head --
would turn the hold into something `gate-refresh` can green. `Create/refresh gate` therefore reads
the head's gate first with `gate-status newest`, and writes the draft status only when that read
succeeds and finds none. Every other mode writes without the read.

A non-draft `pending` write is followed by `gate-status refresh`: an apply's refresh serialized
before this write would otherwise leave applied cells behind a `pending` nobody upgrades. The
refresh only upgrades a fail-closed write, so its failure warns and the step passes.

The upsert step leaves the sticky comment as it is on a draft run and makes no comment API call.

Both steps run for real: `Create/refresh gate` through `_loader.run_with_gate_gh`, the upsert step
through `run_with_gh_recorder`.
"""

import json
import os

import pytest
from _loader import ACTIONS, bash_only, run_with_gate_gh, run_with_gh_recorder, step_by

HEAD_SHA = "a" * 40
RUN_URL = "https://example.invalid/acme/demo/actions/runs/999"
READ_ARGV = [
    "api",
    "--paginate",
    f"repos/acme/demo/commits/{HEAD_SHA}/statuses?per_page=100",
    "--jq",
    ".[] | {context, state, target_url}",
]
CHECKS_ARGV = [
    "api",
    "--paginate",
    f"repos/acme/demo/commits/{HEAD_SHA}/check-runs?filter=all&per_page=100",
    "--jq",
    ".check_runs[] | {name, status, conclusion, started_at, id, app: {id: .app.id}}",
]
DONE = json.dumps(
    {
        "name": "apply / a / dev",
        "status": "completed",
        "conclusion": "success",
        "id": 1,
        "app": {"id": 7},
    }
)


def _write_argv(state, description):
    return [
        "api",
        f"repos/acme/demo/statuses/{HEAD_SHA}",
        "-f",
        f"state={state}",
        "-f",
        "context=shipmate / gate",
        "-f",
        f"description={description}",
        "-f",
        f"target_url={RUN_URL}",
    ]


def _gate(state):
    return json.dumps({"context": "shipmate / gate", "state": state, "target_url": RUN_URL}) + "\n"


def _run_gate(tmp_path, mode, head_state, *, state="pending", read_fails=False, checks_rc=0):
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "GITHUB_ACTION_PATH": str(ACTIONS / "summary"),
        "HEAD_SHA": HEAD_SHA,
        "STATE": state,
        "DESCRIPTION": "the reason",
        "MODE": mode,
        "PLAN_RESULT": "skipped",
        "SHIPMATE_APP_ID": "7",
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_SERVER_URL": "https://example.invalid",
        "GITHUB_RUN_ID": "999",
    }
    return run_with_gate_gh(
        tmp_path,
        step_by("summary", name="Create/refresh gate")["run"],
        env,
        sha=HEAD_SHA,
        checks=DONE + "\n",
        checks_rc=checks_rc,
        page1=_gate(head_state) if head_state else "",
        statuses_rc=1 if read_fails else 0,
    )


@bash_only
@pytest.mark.parametrize("head_state", ["failure", "pending", "success"])
def test_a_draft_run_keeps_an_existing_gate(tmp_path, head_state):
    """Mutation: delete the read -> the draft status overwrites the `failure` hold."""
    proc, calls = _run_gate(tmp_path, "draft", head_state)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [READ_ARGV]
    assert proc.stdout == (
        f"{HEAD_SHA} keeps its {head_state} gate status; the draft gate status was not written\n"
    )


@bash_only
def test_a_draft_run_writes_the_gate_on_a_head_with_none_and_does_not_refresh(tmp_path):
    """Mutations: invert the emptiness test (`-n` -> `-z`) -> no write; drop
    `[ "$MODE" != "draft" ]` from the refresh condition -> a check-runs listing follows."""
    proc, calls = _run_gate(tmp_path, "draft", "")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [READ_ARGV, _write_argv("pending", "the reason")]


@bash_only
def test_a_failed_read_writes_nothing_and_warns(tmp_path):
    """Mutation: drop the `if !` around the read -> `set -e` fails the step."""
    proc, calls = _run_gate(tmp_path, "draft", "", read_fails=True)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [READ_ARGV]
    assert proc.stdout == (
        f"::warning::could not read the gate status of {HEAD_SHA}; the draft gate status was "
        "not written\n"
    )


@bash_only
def test_a_post_run_writes_without_reading(tmp_path):
    """Mutation: key the read on `post` instead of `draft` -> a read, and no write."""
    proc, calls = _run_gate(tmp_path, "post", "failure", state="success")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [_write_argv("success", "the reason")]


@bash_only
def test_a_pending_post_run_refreshes_the_gate_it_wrote(tmp_path):
    """Mutation: remove the refresh call -> only the pending POST."""
    proc, calls = _run_gate(tmp_path, "post", "pending")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [
        _write_argv("pending", "the reason"),
        CHECKS_ARGV,
        READ_ARGV,
        _write_argv("success", "all applies complete, nothing left to apply"),
    ]


@bash_only
def test_a_failed_refresh_warns_and_keeps_the_pending_gate(tmp_path):
    """Mutation: drop the `if !` around the refresh -> `set -e` fails the step."""
    proc, calls = _run_gate(tmp_path, "post", "pending", checks_rc=1)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [_write_argv("pending", "the reason"), CHECKS_ARGV]
    assert proc.stdout == (
        f"::warning::could not refresh the gate of {HEAD_SHA}; it stays pending until an apply "
        "refreshes it\n"
    )


def _run_upsert(tmp_path, mode):
    (tmp_path / "comment.md").write_text("<!-- shipmate:summary -->\n", encoding="utf-8")
    return run_with_gh_recorder(
        tmp_path,
        step_by("summary", name="Upsert sticky comment")["run"],
        {
            **os.environ,
            "GH_TOKEN": "x",
            "PR": "7",
            "MODE": mode,
            "PLAN_RESULT": "skipped",
            "DOCTOR_WARNED": "false",
            "UNMANAGED": "false",
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_ACTION_PATH": str(ACTIONS / "summary"),
        },
    )


@bash_only
def test_a_draft_run_makes_no_comment_call(tmp_path):
    """Mutation: delete the upsert step's `draft` branch -> the stub records the listing call."""
    proc, made = _run_upsert(tmp_path, "draft")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert made == []
    assert "::warning::" not in proc.stdout


@bash_only
def test_the_upsert_stub_records_a_post_run(tmp_path):
    """Control for the draft case: the same stub sees a `post` run's listing and create calls, so
    an empty record means the step made no call rather than that the stub was never reached."""
    proc, made = _run_upsert(tmp_path, "post")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert made == [
        [
            "api",
            "--paginate",
            "--jq",
            '.[] | select(.user.type == "Bot") | {id, head: ((.body // "")[0:64])}',
            "repos/acme/demo/issues/7/comments?per_page=100",
        ],
        ["api", "repos/acme/demo/issues/7/comments", "-F", "body=@comment.md"],
    ]
