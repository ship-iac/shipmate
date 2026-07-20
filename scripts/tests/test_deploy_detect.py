import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "deploy-detect"
_loader = SourceFileLoader("deploy_detect", str(_p))
_spec = importlib.util.spec_from_loader("deploy_detect", _loader)
dd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dd)


def test_filter_pending_drops_completed_applies():
    cells = [
        {"stack": "stacks/dns", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
    ]
    completed = {"apply / dev-eu / stacks/dns"}  # applied pre-merge -> skip
    assert dd.filter_pending(cells, completed) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
    ]


def test_filter_pending_keeps_all_when_none_completed():
    cells = [{"stack": "stacks/dns", "environment": "dev-eu", "workload": ""}]
    assert dd.filter_pending(cells, set()) == cells


def test_completed_failure_apply_stays_pending():
    # A "completed" status with a failing conclusion must not count as done —
    # deploy-detect must share apply-gate's success/neutral predicate, not just
    # check status=="completed".
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    checks = [
        {
            "name": "apply / dev-eu / stacks/app",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-07-18T10:00:00Z",
            "id": 1,
        },
    ]
    done = dd.ag.done_names(checks)
    assert dd.filter_pending(cells, done) == cells


def test_duplicate_run_newer_queued_stays_pending():
    # An old completed+success run must not mask a newer queued run of the same
    # check name (re-created check) — the latest run per name governs.
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    checks = [
        {
            "name": "apply / dev-eu / stacks/app",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-18T10:00:00Z",
            "id": 1,
        },
        {
            "name": "apply / dev-eu / stacks/app",
            "status": "queued",
            "conclusion": None,
            "started_at": "2026-07-18T11:00:00Z",
            "id": 2,
        },
    ]
    done = dd.ag.done_names(checks)
    assert dd.filter_pending(cells, done) == cells


def test_merged_head_exact_match_wins(monkeypatch):
    # Exact merge_commit_sha match should still be picked over everything else.
    pulls = [
        {
            "merge_commit_sha": "other",
            "merged_at": "2024-01-01T00:00:00Z",
            "head": {"sha": "wrong"},
        },
        {
            "merge_commit_sha": "merge123",
            "merged_at": "2024-01-02T00:00:00Z",
            "head": {"sha": "right"},
        },
    ]
    monkeypatch.setattr(dd, "_gh_json", lambda path: pulls)
    assert dd._merged_head("o/r", "merge123") == "right"


def test_merged_head_only_open_pr_falls_back_to_merge_sha(monkeypatch):
    # Sole candidate is an OPEN (unmerged) PR that merely contains the pushed
    # commit -> must NOT deploy that PR's plans; fall back to the merge SHA.
    pulls = [
        {"merge_commit_sha": "unrelated", "merged_at": None, "head": {"sha": "open-pr-head"}},
    ]
    monkeypatch.setattr(dd, "_gh_json", lambda path: pulls)
    assert dd._merged_head("o/r", "merge123") == "merge123"


def test_merged_head_mix_of_merged_and_open_picks_merged(monkeypatch):
    # No pull matches merge_commit_sha exactly (e.g. squash merge), but one
    # candidate is merged and one is still open -> pick the merged one.
    pulls = [
        {"merge_commit_sha": "unrelated1", "merged_at": None, "head": {"sha": "open-pr-head"}},
        {
            "merge_commit_sha": "unrelated2",
            "merged_at": "2024-01-01T00:00:00Z",
            "head": {"sha": "merged-pr-head"},
        },
    ]
    monkeypatch.setattr(dd, "_gh_json", lambda path: pulls)
    assert dd._merged_head("o/r", "merge123") == "merged-pr-head"


def test_merged_head_no_pulls_returns_merge_sha(monkeypatch):
    monkeypatch.setattr(dd, "_gh_json", lambda path: [])
    assert dd._merged_head("o/r", "merge123", _attempts=1, _sleep=0) == "merge123"


def test_merged_head_retries_until_pulls_populated(monkeypatch):
    # commits/{sha}/pulls can transiently return [] for a few seconds after the
    # push (association indexing lag) -- must retry rather than giving up on
    # the first empty response.
    responses = [
        [],
        [],
        [
            {
                "merge_commit_sha": "merge123",
                "merged_at": "2024-01-01T00:00:00Z",
                "head": {"sha": "right"},
            }
        ],
    ]
    calls = {"n": 0}

    def fake_gh_json(path):
        result = responses[calls["n"]]
        calls["n"] += 1
        return result

    slept = []
    monkeypatch.setattr(dd, "_gh_json", fake_gh_json)
    monkeypatch.setattr(dd.time, "sleep", lambda s: slept.append(s))
    assert dd._merged_head("o/r", "merge123", _attempts=5, _sleep=2) == "right"
    assert calls["n"] == 3
    assert slept == [2, 2]  # slept before the 2nd and 3rd attempts only


def test_merged_head_gives_up_after_attempts_exhausted_all_empty(monkeypatch):
    # If every attempt returns [] the retry loop must stop at _attempts and
    # fall back to the merge SHA, not loop forever or raise.
    calls = {"n": 0}

    def fake_gh_json(path):
        calls["n"] += 1
        return []

    monkeypatch.setattr(dd, "_gh_json", fake_gh_json)
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)
    assert dd._merged_head("o/r", "merge123", _attempts=3, _sleep=0) == "merge123"
    assert calls["n"] == 3


def test_check_runs_jsonl_parsing_reuses_apply_gates_parse_jsonl():
    # deploy-detect's check-runs JSONL parsing must not roll its own
    # json.loads-per-line loop -- a malformed line should raise SystemExit
    # naming the offending line, via the single shared implementation.
    with pytest.raises(SystemExit) as exc_info:
        dd.ag.parse_jsonl(['{"a": 1}', "not-json-garbage-{{{"])
    assert "not-json-garbage" in str(exc_info.value)
