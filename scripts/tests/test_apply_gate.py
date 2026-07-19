import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

_D = pathlib.Path(__file__).resolve().parents[1]


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ag = _load("apply-gate")


def _run(name, status, conclusion, started_at="2026-07-18T10:00:00Z", run_id=1):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started_at,
        "id": run_id,
    }


def test_all_applies_succeeded():
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "success"),
        _run("apply / dev-us / stacks/app", "completed", "success"),
    ]
    assert ag.verdict(runs) == "complete"


def test_one_apply_still_queued():
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "success"),
        _run("apply / dev-us / stacks/app", "queued", None),
    ]
    assert ag.verdict(runs) == "pending"


def test_failed_apply_does_not_complete_gate():
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "failure"),
        _run("apply / dev-us / stacks/app", "completed", "success"),
    ]
    assert ag.verdict(runs) == "pending"


def test_neutral_no_change_apply_counts_as_done():
    # plan-cell completes a no-changes cell's apply check with conclusion=neutral.
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "neutral"),
        _run("apply / dev-us / stacks/app", "completed", "success"),
    ]
    assert ag.verdict(runs) == "complete"


def test_latest_run_per_name_wins():
    # A re-created check (e.g. preview rerun superseding an older pending one)
    # must be judged by its newest run, picked by check-run id (creation order).
    runs = [
        _run(
            "apply / dev-eu / stacks/app",
            "queued",
            None,
            started_at="2026-07-18T10:00:00Z",
            run_id=1,
        ),
        _run(
            "apply / dev-eu / stacks/app",
            "completed",
            "success",
            started_at="2026-07-18T11:00:00Z",
            run_id=2,
        ),
    ]
    assert ag.verdict(runs) == "complete"


def test_stale_completed_run_does_not_mask_newer_pending():
    runs = [
        _run(
            "apply / dev-eu / stacks/app",
            "completed",
            "success",
            started_at="2026-07-18T10:00:00Z",
            run_id=1,
        ),
        _run(
            "apply / dev-eu / stacks/app",
            "queued",
            None,
            started_at="2026-07-18T11:00:00Z",
            run_id=2,
        ),
    ]
    assert ag.verdict(runs) == "pending"


def test_non_apply_checks_ignored():
    runs = [
        _run("plan / dev-eu / stacks/app", "completed", "success"),
        _run("shipmate / checkmate", "queued", None),
        _run("apply / dev-eu / stacks/app", "completed", "success"),
    ]
    assert ag.verdict(runs) == "complete"


def test_no_apply_checks_at_all():
    runs = [_run("plan / dev-eu / stacks/app", "completed", "success")]
    assert ag.verdict(runs) == "no-applies"


def test_cancelled_apply_stays_pending():
    runs = [_run("apply / dev-eu / stacks/app", "completed", "cancelled")]
    assert ag.verdict(runs) == "pending"


def test_done_names_excludes_completed_failure():
    runs = [_run("apply / dev-eu / stacks/app", "completed", "failure")]
    assert ag.done_names(runs) == set()


def test_done_names_includes_completed_success_and_neutral():
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "success"),
        _run("apply / dev-us / stacks/app", "completed", "neutral"),
    ]
    assert ag.done_names(runs) == {"apply / dev-eu / stacks/app", "apply / dev-us / stacks/app"}


def test_done_names_uses_latest_run_per_name():
    runs = [
        _run(
            "apply / dev-eu / stacks/app",
            "completed",
            "success",
            started_at="2026-07-18T10:00:00Z",
            run_id=1,
        ),
        _run(
            "apply / dev-eu / stacks/app",
            "queued",
            None,
            started_at="2026-07-18T11:00:00Z",
            run_id=2,
        ),
    ]
    assert ag.done_names(runs) == set()


def test_latest_by_name_ignores_non_apply_checks():
    runs = [_run("plan / dev-eu / stacks/app", "completed", "success")]
    assert ag.latest_by_name(runs) == {}


def test_parse_jsonl_returns_objects_for_valid_lines():
    lines = ['{"a": 1}', "", '{"b": 2}']
    assert ag.parse_jsonl(lines) == [{"a": 1}, {"b": 2}]


def test_parse_jsonl_malformed_line_raises_systemexit_naming_line():
    lines = ['{"a": 1}', "not-json-garbage-{{{", '{"b": 2}']
    with pytest.raises(SystemExit) as exc_info:
        ag.parse_jsonl(lines)
    assert "not-json-garbage" in str(exc_info.value)


def test_latest_by_name_handles_missing_started_at_key():
    # A run missing the 'started_at' key entirely (not merely None/"") must not
    # KeyError. Ordering no longer uses started_at at all, but a run may still
    # arrive without it, so latest_by_name must tolerate its absence.
    runs = [
        {
            "name": "apply / dev-eu / stacks/app",
            "status": "completed",
            "conclusion": "success",
            "id": 1,
        },
    ]
    latest = ag.latest_by_name(runs)
    assert latest["apply / dev-eu / stacks/app"]["id"] == 1


def test_latest_by_name_handles_missing_id_key():
    # A run missing the 'id' key entirely must not KeyError -- a refactor from
    # `run.get("id") or 0` to plain `run["id"]` must fail this test.
    runs = [
        {
            "name": "apply / dev-eu / stacks/app",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-18T10:00:00Z",
        },
    ]
    latest = ag.latest_by_name(runs)
    assert latest["apply / dev-eu / stacks/app"]["started_at"] == "2026-07-18T10:00:00Z"


def test_latest_by_name_newer_queued_null_started_at_beats_older_completed():
    # The [0] regression: a queued duplicate created AFTER an apply completed
    # carries a null started_at but a higher (newer) id. Ordering by id means
    # the newer queued run wins, so the name is judged pending and is NOT masked
    # by the older completed run. Under the old (started_at, id) ordering the
    # null started_at ('') sorted below the completed run's real timestamp and
    # the completed run wrongly won -- silently marking unapplied work done.
    older_completed = {
        "name": "apply / dev-eu / stacks/app",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-07-18T10:00:00Z",
        "id": 1,
    }
    newer_queued_null = {
        "name": "apply / dev-eu / stacks/app",
        "status": "queued",
        "conclusion": None,
        "started_at": None,
        "id": 2,
    }
    latest = ag.latest_by_name([older_completed, newer_queued_null])
    assert latest["apply / dev-eu / stacks/app"]["id"] == 2
    assert ag.done_names([older_completed, newer_queued_null]) == set()


def test_verdict_does_not_crash_on_runs_missing_started_at_and_id():
    runs = [{"name": "apply / dev-eu / stacks/app", "status": "completed", "conclusion": "success"}]
    assert ag.verdict(runs) == "complete"


def test_parse_jsonl_truncates_long_offending_line():
    long_garbage = "x" * 500
    with pytest.raises(SystemExit) as exc_info:
        ag.parse_jsonl([long_garbage])
    msg = str(exc_info.value)
    assert len(msg) < 300
    assert "xxx" in msg
