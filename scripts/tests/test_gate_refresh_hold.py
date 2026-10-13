"""A gate held by `gate-state` must not be greened by `gate-refresh`.

`gate-state`'s hold verdict lives only in the transient commit status, and `shipmate apply` never
consults the gate -- `scripts/authorize` reads `reviewDecision`. So a partial plan download holds
the gate, the parsed cells apply anyway, and every apply check reads done. Without this guard
`gate-refresh` then greens `shipmate / gate`, and the pull request merges with planned stacks that
were never applied and never would be: `deploy-detect`'s work queue is the pending apply checks,
and stacks whose summaries were never read have no check at all.

Invariant: the only exit from a hold is a fresh plan run.

The tests run the real `Write the gate` step through `_loader.run_with_gate_gh`, so the step's
dispatch and `scripts/gate-status` both execute.
"""

import json
import os

import pytest
from _loader import ACTIONS, bash_only, run_with_gate_gh, step_by

HEAD_SHA = "a" * 40
RUN_URL = "https://example.invalid/acme/demo/actions/runs/999"
DONE = json.dumps(
    {
        "name": "apply / a / dev",
        "status": "completed",
        "conclusion": "success",
        "id": 1,
        "app": {"id": 7},
    }
)


def _gate(state):
    return json.dumps({"context": "shipmate / gate", "state": state, "target_url": RUN_URL}) + "\n"


def _status_argv(state, description):
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


GREEN_ARGV = _status_argv("success", "all applies complete, nothing left to apply")


def _run_step(tmp_path, *, gate="", detect_result="", deploy_results="", hold_description=""):
    proc, calls = run_with_gate_gh(
        tmp_path,
        step_by("gate-refresh", name="Write the gate")["run"],
        {
            **os.environ,
            "GH_TOKEN": "x",
            "GITHUB_ACTION_PATH": str(ACTIONS / "gate-refresh"),
            "HEAD_SHA": HEAD_SHA,
            "SHIPMATE_APP_ID": "7",
            "DETECT_RESULT": detect_result,
            "DEPLOY_RESULTS": deploy_results,
            "HOLD_DESCRIPTION": hold_description,
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_SERVER_URL": "https://example.invalid",
            "GITHUB_RUN_ID": "999",
        },
        sha=HEAD_SHA,
        checks=DONE + "\n",
        page1=gate,
    )
    return proc, [c for c in calls if c[1].startswith("repos/acme/demo/statuses/")]


@bash_only
def test_refuses_to_green_a_held_gate(tmp_path):
    """The step turns `gate-status`'s hold exit into one `::error::` line and exit 1.
    Mutations: delete the hold check in `gate-status refresh` -> the hold is greened; drop the
    step's `rc = 3` branch -> exit 3 and no annotation."""
    proc, posted = _run_step(tmp_path, gate=_gate("failure"))
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        1,
        (
            f"::error::shipmate / gate is held for {HEAD_SHA}; its status description on the pull"
            " request names the cause. A hold clears only when a new plan run rewrites the gate:"
            " comment shipmate plan, or push.\n"
        ),
        "",
    )
    assert posted == []


@bash_only
@pytest.mark.parametrize("gate", [_gate("pending"), ""])
def test_a_pending_or_absent_gate_still_greens(tmp_path, gate):
    # The legitimate transition the refusal must not break: gate-state writes `pending` while
    # applies are outstanding, and completing them is what gate-refresh exists to record.
    proc, posted = _run_step(tmp_path, gate=gate)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert posted == [GREEN_ARGV]


@bash_only
def test_a_hold_description_writes_a_hold(tmp_path):
    """Mutation: swap the `hold` and `refresh` branches of the dispatch -> a green POST."""
    proc, posted = _run_step(tmp_path, hold_description="re-plan this pull request")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert posted == [_status_argv("failure", "re-plan this pull request")]


@bash_only
def test_deploy_results_write_the_deploy_verdict(tmp_path):
    """Mutation: drop the `deploy` branch of the dispatch -> a refresh greens the head."""
    proc, posted = _run_step(tmp_path, detect_result="success", deploy_results="success,cancelled")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert posted == [
        _status_argv("failure", "deploy incomplete (deploy env-level applies completed)")
    ]


@bash_only
def test_two_modes_at_once_are_refused(tmp_path):
    """Mutation: drop the two-modes check -> the hold is written and the step passes."""
    proc, posted = _run_step(tmp_path, deploy_results="success", hold_description="re-plan")
    assert proc.returncode == 1
    assert proc.stdout == "::error::set at most one of deploy-results and hold-description\n"
    assert posted == []


@bash_only
def test_deploy_results_without_a_detect_result_are_refused(tmp_path):
    """Mutation: drop the empty detect-result check -> a stopped-at-detect verdict is written and
    the step passes."""
    proc, posted = _run_step(tmp_path, deploy_results="success")
    assert proc.returncode == 1
    assert proc.stdout == "::error::deploy-results needs detect-result, which is empty\n"
    assert posted == []
