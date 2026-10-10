import io
import os
import subprocess
import sys
import types

import pytest
from _loader import ENGINE
from _shipmate import _run, approval_rules, gate_check, is_pr_number, review_count, utf8_output


def test_utf8_output_switches_both_streams_from_cp1252_to_utf8(monkeypatch):
    """Mutations: drop `sys.stdout` from the loop's tuple, or `sys.stderr`; either stream then
    writes `—` as cp1252's `\\x97`."""
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    utf8_output()
    for stream in (out, err):
        stream.write("—")
        stream.flush()
    assert (out.buffer.getvalue(), err.buffer.getvalue()) == (b"\xe2\x80\x94", b"\xe2\x80\x94")


@pytest.mark.parametrize(
    "script, argv, refusal",
    [
        (
            "onboard",
            ["--app-id", "é"],
            "--app-id must be all digits: 'é'\n",
        ),
        (
            "register-app",
            ["--name", "x", "--repo", "é", "--out", "key.pem"],
            "--repo must be <owner>/<repo>: 'é'\n",
        ),
    ],
)
def test_a_script_run_as_a_file_writes_its_refusal_as_utf8(tmp_path, script, argv, refusal):
    """Both refusals run before any `gh` or `git` call. Mutation: delete `utf8_output()` from
    the script's `__main__` block; `é` then leaves as cp1252's `\\xe9`."""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    proc = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / script), *argv],
        cwd=tmp_path,
        env=env,
        capture_output=True,
    )
    assert (proc.returncode, proc.stderr.replace(b"\r\n", b"\n")) == (1, refusal.encode("utf-8"))


def test_gate_check_returns_the_gate_entry_and_its_rules_parameters():
    """Mutation: return the first required-status-check entry -- the `lint` rule's comes back."""
    lint = {
        "required_status_checks": [{"context": "lint"}],
        "strict_required_status_checks_policy": False,
    }
    gate = {
        "required_status_checks": [{"context": "shipmate / gate", "integration_id": 7}],
        "strict_required_status_checks_policy": True,
    }
    rules = [
        {"type": "required_status_checks", "parameters": lint},
        {"type": "required_status_checks", "parameters": gate},
    ]
    assert gate_check(rules, "shipmate / gate") == (
        {"context": "shipmate / gate", "integration_id": 7},
        {
            "required_status_checks": [{"context": "shipmate / gate", "integration_id": 7}],
            "strict_required_status_checks_policy": True,
        },
    )


def test_review_count_is_the_highest_over_every_pull_request_rule():
    """Mutation: `min` for `max` -- the count reads 0."""
    rules = [
        {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
        {"type": "pull_request", "parameters": {"required_approving_review_count": 2}},
    ]
    assert review_count(rules) == 2


def test_approval_rules_drop_the_branch_policy_and_list_each_type_once():
    """Mutation: keep `branch_policy` -- it appears in the list; build `types` as a list and
    filter `branch_policy` out of it -- `wait_timer` appears twice."""
    env = {
        "protection_rules": [
            {"type": "wait_timer"},
            {"type": "branch_policy"},
            {"type": "required_reviewers"},
            {"type": "wait_timer"},
        ]
    }
    assert approval_rules(env) == ["required_reviewers", "wait_timer"]


def test_approval_rules_report_a_typeless_rule_as_unknown():
    """Mutation: `or "?"` -> `or "x"` -- `x` is listed instead; subtract `"?"` too -- the
    typeless rules vanish."""
    env = {"protection_rules": [{"type": "wait_timer"}, {}, {"type": ""}, {"type": None}]}
    assert approval_rules(env) == ["?", "wait_timer"]


def test_is_pr_number_accepts_github_numbers_only():
    """Mutations: pattern `[0-9]+` (`0`, `007` and the eleven digits red); `match` for
    `fullmatch` (`7a` and `7\\n` red)."""
    table = ["1", "7", "42", "1234567890", "0", "007", "12345678901", "", "7a", "-1", "7\n", "١"]
    assert {v: is_pr_number(v) for v in table} == {
        "1": True,
        "7": True,
        "42": True,
        "1234567890": True,
        "0": False,
        "007": False,
        "12345678901": False,
        "": False,
        "7a": False,
        "-1": False,
        "7\n": False,
        "١": False,
    }


def test_run_annotates_the_shared_runners_failure(monkeypatch, capsys):
    """`_run` re-raises `run`'s failure with `::error::` prepended and prints
    nothing itself: the stderr rides in the message. Reddens on returning `run(args)` with no
    `except` (the prefix is gone), and on writing stderr before raising."""

    def fake_subprocess_run(args, capture_output=False, input=None):
        return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"gh: boom\n")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    with pytest.raises(SystemExit) as exc:
        _run(["gh", "api", "repos/an-org/a-repo"])
    assert str(exc.value) == "::error::command failed (1): gh api repos/an-org/a-repo\ngh: boom"
    assert capsys.readouterr().err == ""
