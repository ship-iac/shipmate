import io
import json

import pytest
from _loader import load_script

ag = load_script("apply-gate")


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
        _run("apply / stacks/app / dev-eu", "completed", "success"),
        _run("apply / stacks/app / dev-us", "completed", "success"),
    ]
    assert ag.verdict(runs) == "complete"


def test_one_apply_still_queued():
    runs = [
        _run("apply / stacks/app / dev-eu", "completed", "success"),
        _run("apply / stacks/app / dev-us", "queued", None),
    ]
    assert ag.verdict(runs) == "pending"


def test_failed_apply_does_not_complete_gate():
    runs = [
        _run("apply / stacks/app / dev-eu", "completed", "failure"),
        _run("apply / stacks/app / dev-us", "completed", "success"),
    ]
    assert ag.verdict(runs) == "pending"


def test_neutral_no_change_apply_counts_as_done():
    # plan-cell completes a no-changes cell's apply check with conclusion=neutral.
    runs = [
        _run("apply / stacks/app / dev-eu", "completed", "neutral"),
        _run("apply / stacks/app / dev-us", "completed", "success"),
    ]
    assert ag.verdict(runs) == "complete"


def test_latest_run_per_name_wins():
    # A re-created check (e.g. plan rerun superseding an older pending one)
    # must be judged by its newest run, picked by check-run id (creation order).
    runs = [
        _run(
            "apply / stacks/app / dev-eu",
            "queued",
            None,
            started_at="2026-07-18T10:00:00Z",
            run_id=1,
        ),
        _run(
            "apply / stacks/app / dev-eu",
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
            "apply / stacks/app / dev-eu",
            "completed",
            "success",
            started_at="2026-07-18T10:00:00Z",
            run_id=1,
        ),
        _run(
            "apply / stacks/app / dev-eu",
            "queued",
            None,
            started_at="2026-07-18T11:00:00Z",
            run_id=2,
        ),
    ]
    assert ag.verdict(runs) == "pending"


def test_non_apply_checks_ignored():
    runs = [
        _run("stacks/app / dev-eu", "completed", "success"),
        _run("shipmate / gate", "queued", None),
        _run("apply / stacks/app / dev-eu", "completed", "success"),
    ]
    assert ag.verdict(runs) == "complete"


def test_no_apply_checks_at_all():
    runs = [_run("stacks/app / dev-eu", "completed", "success")]
    assert ag.verdict(runs) == "no-applies"


def test_cancelled_apply_stays_pending():
    runs = [_run("apply / stacks/app / dev-eu", "completed", "cancelled")]
    assert ag.verdict(runs) == "pending"


def test_done_names_excludes_completed_failure():
    runs = [_run("apply / stacks/app / dev-eu", "completed", "failure")]
    assert ag.done_names(runs) == set()


def test_done_names_includes_completed_success_and_neutral():
    runs = [
        _run("apply / stacks/app / dev-eu", "completed", "success"),
        _run("apply / stacks/app / dev-us", "completed", "neutral"),
    ]
    assert ag.done_names(runs) == {"apply / stacks/app / dev-eu", "apply / stacks/app / dev-us"}


def test_done_names_uses_latest_run_per_name():
    runs = [
        _run(
            "apply / stacks/app / dev-eu",
            "completed",
            "success",
            started_at="2026-07-18T10:00:00Z",
            run_id=1,
        ),
        _run(
            "apply / stacks/app / dev-eu",
            "queued",
            None,
            started_at="2026-07-18T11:00:00Z",
            run_id=2,
        ),
    ]
    assert ag.done_names(runs) == set()


def test_latest_by_name_ignores_non_apply_checks():
    runs = [_run("stacks/app / dev-eu", "completed", "success")]
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
            "name": "apply / stacks/app / dev-eu",
            "status": "completed",
            "conclusion": "success",
            "id": 1,
        },
    ]
    latest = ag.latest_by_name(runs)
    assert latest["apply / stacks/app / dev-eu"]["id"] == 1


def test_latest_by_name_handles_missing_id_key():
    # A run missing the 'id' key entirely must not KeyError -- a refactor from
    # `run.get("id") or 0` to plain `run["id"]` must fail this test.
    runs = [
        {
            "name": "apply / stacks/app / dev-eu",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-18T10:00:00Z",
        },
    ]
    latest = ag.latest_by_name(runs)
    assert latest["apply / stacks/app / dev-eu"]["started_at"] == "2026-07-18T10:00:00Z"


def test_latest_by_name_newer_queued_null_started_at_beats_older_completed():
    # The [0] regression: a queued duplicate created AFTER an apply completed
    # carries a null started_at but a higher (newer) id. Ordering by id means
    # the newer queued run wins, so the name is judged pending and is NOT masked
    # by the older completed run. Under the old (started_at, id) ordering the
    # null started_at ('') sorted below the completed run's real timestamp and
    # the completed run wrongly won -- silently marking unapplied work done.
    older_completed = {
        "name": "apply / stacks/app / dev-eu",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-07-18T10:00:00Z",
        "id": 1,
    }
    newer_queued_null = {
        "name": "apply / stacks/app / dev-eu",
        "status": "queued",
        "conclusion": None,
        "started_at": None,
        "id": 2,
    }
    latest = ag.latest_by_name([older_completed, newer_queued_null])
    assert latest["apply / stacks/app / dev-eu"]["id"] == 2
    assert ag.done_names([older_completed, newer_queued_null]) == set()


def test_verdict_does_not_crash_on_runs_missing_started_at_and_id():
    runs = [{"name": "apply / stacks/app / dev-eu", "status": "completed", "conclusion": "success"}]
    assert ag.verdict(runs) == "complete"


def test_parse_jsonl_truncates_long_offending_line():
    long_garbage = "x" * 500
    with pytest.raises(SystemExit) as exc_info:
        ag.parse_jsonl([long_garbage])
    msg = str(exc_info.value)
    assert len(msg) < 300
    assert "xxx" in msg


def test_latest_by_name_empty_prefix_gathers_all_latest_per_name():
    # summary-comment calls latest_by_name(prefix="") to gather every check-run
    # on the head SHA; the plan-link anchor is check_url's exact `<stack> / <env>`
    # lookup, not a prefix filter. Latest-id-per-name still applies, and the
    # coexisting apply check keeps its distinct `apply / ` name.
    runs = [
        {"name": "stacks/app / dev-eu", "id": 1, "html_url": "u1"},
        {"name": "stacks/app / dev-eu", "id": 3, "html_url": "u3"},
        {"name": "apply / stacks/app / dev-eu", "id": 2, "html_url": "u2"},
    ]
    latest = ag.latest_by_name(runs, prefix="")
    assert set(latest) == {"stacks/app / dev-eu", "apply / stacks/app / dev-eu"}
    assert latest["stacks/app / dev-eu"]["html_url"] == "u3"


def test_latest_by_name_default_prefix_unchanged():
    runs = [
        {"name": "stacks/app / dev-eu", "id": 1},
        {
            "name": "apply / stacks/app / dev-eu",
            "id": 2,
            "status": "completed",
            "conclusion": "success",
        },
    ]
    assert set(ag.latest_by_name(runs)) == {"apply / stacks/app / dev-eu"}


def _run_obj(name, status="completed", conclusion="success", id=1, app_id=999):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "id": id,
        "app": {"id": app_id},
    }


def test_from_app_filters_foreign_and_missing_app():
    ours = _run_obj("apply / stacks/app / dev-eu", app_id=999)
    foreign = _run_obj("apply / stacks/app / dev-eu", id=2, app_id=15368)
    no_app = {
        "name": "apply / stacks/app / dev-eu",
        "status": "completed",
        "conclusion": "success",
        "id": 3,
    }
    assert ag.from_app([ours, foreign, no_app], "999") == [ours]


def test_forged_newer_completed_duplicate_cannot_green_a_pending_name():
    pending = _run_obj(
        "apply / stacks/app / dev-eu", status="queued", conclusion=None, id=10, app_id=999
    )
    forged = _run_obj("apply / stacks/app / dev-eu", id=11, app_id=15368)
    runs = ag.from_app([pending, forged], "999")
    assert ag.verdict(runs) == "pending"


def test_from_app_empty_app_id_fails_loud():
    # An unset SHIPMATE_APP_ID renders as '' -- int('') must not raw-traceback
    # (ValueError) and take down the whole detect/gate/apply run; fail loud
    # with a message naming the variable instead.
    runs = [_run_obj("apply / stacks/app / dev-eu")]
    with pytest.raises(SystemExit, match="SHIPMATE_APP_ID"):
        ag.from_app(runs, "")


def test_app_done_names_excludes_foreign_app_completed():
    # The real guard for detect scripts' main(): if app_done_names ever stops
    # calling from_app internally, this must go red. A foreign-App (15368,
    # github-actions) completed check must never appear in the result, even
    # though an App-authored (999) completed check for a different name does.
    ours = json.dumps(_run_obj("apply / stacks/app / dev-eu", id=1, app_id=999))
    foreign = json.dumps(_run_obj("apply / stacks/app / dev-us", id=2, app_id=15368))
    names = ag.app_done_names([ours, foreign], "999")
    assert names == {"apply / stacks/app / dev-eu"}
    assert "apply / stacks/app / dev-us" not in names


def _ext(name, id, external_id, app_id=999, started_at="2026-07-18T10:00:00Z"):
    run = _run_obj(name, id=id, app_id=app_id)
    run["external_id"] = external_id
    run["started_at"] = started_at
    return json.dumps(run)


# Must contain letters: an all-digit "hex" string parses as a JSON int, so it
# exercises the non-dict guard instead of the JSONDecodeError path this pins.
LEGACY_HEX = "a3f" + "b" * 61


def test_plan_runs_newest_app_run_supplies_the_id():
    # id order and started_at order deliberately disagree: the higher id wins
    # even though it started earlier, because latest_by_name orders by id.
    older = _ext(
        "apply / stacks/app / dev-eu",
        1,
        json.dumps({"fingerprint": "a" * 64, "plan_run": "111"}),
        started_at="2026-07-18T12:00:00Z",
    )
    newer = _ext(
        "apply / stacks/app / dev-eu",
        2,
        json.dumps({"fingerprint": "b" * 64, "plan_run": "222"}),
        started_at="2026-07-18T09:00:00Z",
    )
    assert ag.plan_runs_by_name([older, newer], "999") == {"apply / stacks/app / dev-eu": "222"}


def test_plan_runs_ignores_foreign_app_even_when_newest():
    ours = _ext(
        "apply / stacks/app / dev-eu", 1, json.dumps({"fingerprint": "a" * 64, "plan_run": "111"})
    )
    foreign = _ext(
        "apply / stacks/app / dev-eu",
        2,
        json.dumps({"fingerprint": "b" * 64, "plan_run": "222"}),
        app_id=15368,
    )
    assert ag.plan_runs_by_name([ours, foreign], "999") == {"apply / stacks/app / dev-eu": "111"}


def test_plan_runs_legacy_bare_hex_external_id_is_absent():
    # Records written by an engine version before external_id carried JSON are
    # a bare 64-hex fingerprint: absent, never a JSONDecodeError traceback.
    line = _ext("apply / stacks/app / dev-eu", 1, LEGACY_HEX)
    assert ag.plan_runs_by_name([line], "999") == {}


# "1" * 64 is the only case that reaches the isinstance(record, dict) guard --
# it parses as a JSON int, not a dict. Keep it.
@pytest.mark.parametrize("external_id", [None, "", "not json at all", "1" * 64])
def test_plan_runs_unusable_external_id_is_absent(external_id):
    line = _ext("apply / stacks/app / dev-eu", 1, external_id)
    assert ag.plan_runs_by_name([line], "999") == {}


@pytest.mark.parametrize(
    "record",
    [
        {"fingerprint": "a" * 64},
        {"fingerprint": "a" * 64, "plan_run": "12x4"},
        {"fingerprint": "a" * 64, "plan_run": ""},
        {"fingerprint": "a" * 64, "plan_run": 1234},
    ],
)
def test_plan_runs_bad_plan_run_value_is_absent(record):
    line = _ext("apply / stacks/app / dev-eu", 1, json.dumps(record))
    assert ag.plan_runs_by_name([line], "999") == {}


def test_plan_runs_only_apply_prefixed_names():
    # latest_by_name's default prefix must not be overridden: a plan check with
    # a well-formed record contributes nothing.
    record = json.dumps({"fingerprint": "a" * 64, "plan_run": "111"})
    apply_line = _ext("apply / stacks/app / dev-eu", 1, record)
    plan_line = _ext("stacks/app / dev-eu", 2, record)
    assert ag.plan_runs_by_name([apply_line, plan_line], "999") == {
        "apply / stacks/app / dev-eu": "111"
    }


def test_plan_runs_mode_prints_the_mapping_and_writes_no_verdict(monkeypatch, capsys):
    # comment-ops' reviewed-plan lookup is this mode. GITHUB_OUTPUT is
    # deliberately unset: a gate verdict written here would be a decision the
    # caller never asked for, and would abort on the missing variable instead.
    record = json.dumps({"fingerprint": "a" * 64, "plan_run": "777"})
    stdin = io.StringIO(
        _ext("apply / stacks/app / dev-eu", 1, record)
        + "\n"
        + _ext("apply / stacks/api / dev-eu", 2, LEGACY_HEX)
    )
    monkeypatch.setattr(ag.sys, "argv", ["apply-gate", "--plan-runs"])
    monkeypatch.setattr(ag.sys, "stdin", stdin)
    monkeypatch.setenv("SHIPMATE_APP_ID", "999")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    ag.main()
    assert json.loads(capsys.readouterr().out) == {"apply / stacks/app / dev-eu": "777"}


def test_an_unrecognized_argument_fails_loud(monkeypatch):
    # A typo'd flag must not fall through to the verdict path, which would write
    # a gate decision where the caller asked for the mapping.
    monkeypatch.setattr(ag.sys, "argv", ["apply-gate", "--plan-run"])
    with pytest.raises(SystemExit) as exc:
        ag.main()
    assert "--plan-run" in str(exc.value)
