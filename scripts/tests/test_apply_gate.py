import io
import json
import pathlib

import pytest
from _detect_fixtures import (
    AUTOPLAN_CREATED,
    autoplans_read,
    deferred_auto_failed_jobs,
    deferred_in_progress_jobs,
    listed_autoplan,
)
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


_EU = "apply / dev-eu / stacks/app"
_US = "apply / dev-us / stacks/app"


def test_the_cell_names_are_these_whole_values():
    """Mutations: `check_name` returns `<stack> / <env>`; `parse_check_name` returns
    `(env, stack)`, keeps the `apply / ` prefix or splits on the last ` / ` (a stack holding
    ` / ` then misparses); `plan_check_name` drops `CALLER_PREFIX`; a step name or the held
    title changes."""
    assert ag.check_name("stacks/app", "dev-eu") == "apply / dev-eu / stacks/app"
    assert ag.parse_check_name("apply / dev-eu / stacks/app") == ("stacks/app", "dev-eu")
    assert ag.parse_check_name("apply / dev / stacks/a / x") == ("stacks/a / x", "dev")
    assert ag.plan_check_name("stacks/app", "dev-eu") == "plan / dev-eu / stacks/app"
    assert (ag.PRE_APPLY_STEP, ag.APPLY_STEP, ag.HELD_TITLE) == (
        "Pre-apply check",
        "Apply the cell",
        "State not saved",
    )


@pytest.mark.parametrize(
    "name",
    [
        "apply / stacks/app / dev-eu",
        "plan / dev-eu / stacks/app",
        "apply / dev-eu",
        "apply / dev-eu / ",
        "apply / Dev / stacks/app",
        "dev-eu / stacks/app",
        "",
    ],
)
def test_a_name_outside_the_grammar_parses_to_none(name):
    """An old-order name, a plan check name, a name without a stack, an environment outside
    the charset, a name without the prefix and the empty string each parse to None.
    Mutations: fall back to the old order when the first segment is no environment name; drop
    the `is_env_name` check; drop the prefix check; drop the empty-stack check."""
    assert ag.parse_check_name(name) is None


def test_a_one_segment_stack_parses_env_first():
    """A one-segment stack parses like any other. An old-order one-segment name, `apply / dns
    / dev-us`, parses the same way, as stack `dev-us` in environment `dns`: shape cannot tell
    the two orders apart. Mutation: reject a stack without `/`."""
    assert ag.parse_check_name("apply / dev-us / dns") == ("dns", "dev-us")


@pytest.mark.parametrize(
    ("job_name", "stack", "env", "expected"),
    [
        ("L0 / dev-eu / stacks/app / apply", "stacks/app", "dev-eu", "apply"),
        ("apply / run / L2 / dev-eu / stacks/app / save", "stacks/app", "dev-eu", "save"),
        ("apply / run / detect", "detect", "run", None),
        ("L0 / dev-eu / stacks/app / apply", "stacks/app", "eu", None),
        ("dev-eu / stacks/app / apply", "stacks/app", "dev-eu", None),
        ("L0 / dev-eu / stacks/app", "stacks/app", "dev-eu", None),
        ("L0 / dev-eu / stacks/app / plan", "stacks/app", "dev-eu", None),
        ("L0 / dev-eu / stacks/app / apply / x", "stacks/app", "dev-eu", None),
    ],
)
def test_cell_part_reads_these_literal_names(job_name, stack, env, expected):
    """Mutations: drop `L[0-9]+` from the pattern; `search` for `fullmatch`; accept any last
    segment as the part."""
    assert ag.cell_part(job_name, stack, env) == expected


_LEVELED = [
    {"id": 1, "name": "apply / run / L0 / dev-eu / stacks/app / apply"},
    {"id": 2, "name": "apply / run / L0 / dev-eu / stacks/app / save"},
    {"id": 3, "name": "apply / run / L1 / wave0-complete"},
    {"id": 4, "name": "apply / run / L1 / dev-eu / stacks/app / apply"},
    {"id": 5, "name": "apply / run / L0 / dev-us / stacks/app / save"},
]


def test_level_of_reads_a_cell_row_and_a_row_directly_under_the_level():
    """Mutation: `level_of` cuts only the last segment, so a cell row's level reads
    `<level> / <env> / <stack>`."""
    assert ag.level_of(_LEVELED, 2) == "apply / run / L0"
    assert ag.level_of(_LEVELED, 3) == "apply / run / L1"
    assert ag.level_of(_LEVELED, 99) == ""


def test_cell_rows_are_the_rows_of_one_level_by_part():
    """Mutations: `cell_rows` drops the level from the name; a row of another env counts."""
    assert ag.cell_rows(_LEVELED, "apply / run / L0", "stacks/app", "dev-eu") == {
        "apply": [_LEVELED[0]],
        "save": [_LEVELED[1]],
    }
    assert ag.cell_rows(_LEVELED, "", "stacks/app", "dev-eu") == {"apply": [], "save": []}


def test_held_names_are_the_newest_in_progress_runs_titled_held():
    """Mutations: `held_names` reads the oldest run of a name; it ignores the status; it reads
    the title from the top level."""
    held = {"output": {"title": "State not saved"}}
    runs = [
        {**_run(_EU, "in_progress", None, run_id=1), **held},
        {**_run(_US, "in_progress", None, run_id=2), **held},
        _run(_US, "completed", "success", run_id=3),
        {**_run("apply / dev-eu / stacks/db", "completed", "failure", run_id=4), **held},
        {**_run("apply / dev-eu / stacks/net", "in_progress", None, run_id=5), "output": None},
    ]
    assert ag.held_names(runs) == {_EU}


# Rows copied from jobs?filter=all listings of runs 44161778000 (applied, save failed, failed
# save re-run) and 44164180908 (cancelled while pending), names re-keyed to the cell grammar.
_APPLY = "apply / run / L0 / dev-eu / stacks/app / apply"
_SAVE = "apply / run / L0 / dev-eu / stacks/app / save"
_PROBE_STEP = (
    'Run echo "PROBE {} start $(date -u +%FT%T.%3NZ) group=$GROUP attempt=$GITHUB_RUN_ATTEMPT"'
)


def _step(name, conclusion):
    return {"name": name, "status": "completed", "conclusion": conclusion}


_RR_APPLY_1 = {
    "id": 134317220991,
    "run_attempt": 1,
    "name": _APPLY,
    "status": "completed",
    "conclusion": "success",
    "started_at": "2026-11-17T19:44:19Z",
    "steps": [
        _step("Set up job", "success"),
        _step(_PROBE_STEP.format("apply"), "success"),
        _step("Complete job", "success"),
    ],
}
_RR_SAVE_1 = {
    "id": 134317522397,
    "run_attempt": 1,
    "name": _SAVE,
    "status": "completed",
    "conclusion": "failure",
    "started_at": "2026-11-17T19:44:46Z",
    "steps": [
        _step("Set up job", "success"),
        _step(_PROBE_STEP.format("saver"), "failure"),
        _step("Run actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9", "skipped"),
        _step("Complete job", "success"),
    ],
}
_RR_APPLY_2 = {
    "id": 134320891783,
    "run_attempt": 2,
    "name": _APPLY,
    "status": "completed",
    "conclusion": "success",
    "started_at": "2026-11-17T19:44:19Z",
    "steps": [],
}
_RR_SAVE_2 = {
    "id": 134322335889,
    "run_attempt": 2,
    "name": _SAVE,
    "status": "completed",
    "conclusion": "failure",
    "started_at": "2026-11-17T19:52:28Z",
    "steps": [
        _step("Set up job", "success"),
        _step(_PROBE_STEP.format("saver"), "failure"),
        _step("Run actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9", "skipped"),
        _step("Complete job", "success"),
    ],
}
_PENDING_CANCEL = {
    "id": 134326095848,
    "run_attempt": 1,
    "name": _APPLY,
    "status": "completed",
    "conclusion": "cancelled",
    "started_at": "2026-11-17T19:58:24Z",
    "steps": [],
}
_RR = [_RR_APPLY_1, _RR_SAVE_1, _RR_APPLY_2, _RR_SAVE_2]


def test_a_carried_row_is_a_copy_of_an_earlier_attempts_row():
    """Mutations: `is_carried` matches on the name alone; it matches a later attempt's row."""
    assert ag.is_carried(_RR_APPLY_2, _RR)
    assert not ag.is_carried(_RR_APPLY_1, _RR)
    assert not ag.is_carried({**_RR_APPLY_2, "started_at": "2026-11-17T19:52:18Z"}, _RR)


def test_a_later_attempts_successful_save_credits_the_earlier_attempt():
    """Mutation: `saved` reads only the attempt's own `save` row."""
    credited = [*_RR[:3], {**_RR_SAVE_2, "conclusion": "success"}]
    assert ag.attempts(credited, "stacks/app", "dev-eu") == {
        1: {"applied": True, "saved": True},
        2: {"applied": False, "saved": True},
    }
    assert ag.unsaved(credited, "stacks/app", "dev-eu") == []


def test_a_later_attempts_failed_save_leaves_the_earlier_attempt_unsaved():
    """A carried `apply` row is attempt 1's apply, not attempt 2's. Mutations: count a carried
    row as an apply of its re-run attempt; `unsaved` counts `before` itself."""
    assert ag.attempts(_RR, "stacks/app", "dev-eu") == {
        1: {"applied": True, "saved": False},
        2: {"applied": False, "saved": False},
    }
    assert ag.unsaved(_RR, "stacks/app", "dev-eu") == [1]
    assert ag.unsaved(_RR, "stacks/app", "dev-eu", before=1) == []
    assert ag.unsaved(_RR, "stacks/us", "dev-eu") == []


def test_steps_free_apply_rows_read_by_whether_an_earlier_row_explains_them():
    """A row cancelled while pending reads not applied; a steps-free row an earlier same-name
    row does not explain reads unknown, and an unknown apply with no save is unsaved. Mutation:
    read every `steps: []` row as not applied."""
    assert ag.attempts([_PENDING_CANCEL], "stacks/app", "dev-eu") == {
        1: {"applied": False, "saved": None}
    }
    assert ag.unsaved([_PENDING_CANCEL], "stacks/app", "dev-eu") == []
    unexplained = {**_RR_APPLY_2, "started_at": "2026-11-17T19:52:18Z"}
    assert ag.attempts([_RR_APPLY_1, unexplained], "stacks/app", "dev-eu") == {
        1: {"applied": True, "saved": None},
        2: {"applied": None, "saved": None},
    }
    assert ag.unsaved([_RR_APPLY_1, unexplained], "stacks/app", "dev-eu") == [1, 2]


@pytest.mark.parametrize(
    ("status", "conclusion", "applied"),
    [
        ("completed", "cancelled", False),
        ("completed", "skipped", False),
        ("completed", "failure", False),
        ("completed", "success", None),
        ("queued", None, None),
        ("waiting", None, None),
        ("in_progress", None, None),
    ],
)
def test_a_steps_free_row_with_no_earlier_row_reads_not_applied_only_once_it_ended(
    status, conclusion, applied
):
    """`failure` is a job that never started: the probe's group-conflict job failed with no
    steps and no runner, as an `<env>-apply` deployment rejected at the job's start does (that
    shape is unprobed). Mutations: read a steps-free row as not applied whatever its status and
    conclusion; drop `failure` from the ended conclusions."""
    row = {**_PENDING_CANCEL, "status": status, "conclusion": conclusion}
    assert ag.attempts([row], "stacks/app", "dev-eu") == {1: {"applied": applied, "saved": None}}


def test_a_rerun_row_cancelled_while_pending_reads_not_applied():
    """Attempt 2 re-ran the cell's `apply`, cancelled before it started: its own row, under a
    new start, is no carried copy and never applied. Mutation: read a steps-free row whose name an
    earlier attempt lists as unknown -- attempt 2 reads None, red."""
    pending = {**_PENDING_CANCEL, "run_attempt": 2}
    assert ag.attempts([_RR_APPLY_1, pending], "stacks/app", "dev-eu") == {
        1: {"applied": True, "saved": None},
        2: {"applied": False, "saved": None},
    }


@pytest.mark.parametrize(
    ("steps", "applied"),
    [
        ([_step("Pre-apply check", "failure"), _step("Apply the cell", "failure")], False),
        ([_step("Pre-apply check", "success"), _step("Apply the cell", "skipped")], False),
        ([_step("Pre-apply check", "success"), _step("Apply the cell", "failure")], True),
    ],
)
def test_a_refused_or_skipped_apply_step_reads_not_applied(steps, applied):
    """Mutations: `Pre-apply check` failure reads applied; a skipped `Apply the cell` reads
    applied."""
    row = {**_RR_APPLY_1, "steps": steps}
    assert ag.attempts([row], "stacks/app", "dev-eu") == {1: {"applied": applied, "saved": None}}


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        pytest.param(
            [_run(_EU, "completed", "success"), _run(_US, "completed", "success")],
            "complete",
            id="all_applies_succeeded",
        ),
        pytest.param(
            [_run(_EU, "completed", "success"), _run(_US, "queued", None)],
            "pending",
            id="one_apply_still_queued",
        ),
        pytest.param(
            [_run(_EU, "completed", "failure"), _run(_US, "completed", "success")],
            "pending",
            id="failed_apply_does_not_complete_gate",
        ),
        # plan-cell completes a no-changes cell's apply check with conclusion=neutral.
        pytest.param(
            [_run(_EU, "completed", "neutral"), _run(_US, "completed", "success")],
            "complete",
            id="neutral_no_change_apply_counts_as_done",
        ),
        # A re-created check, a plan rerun superseding an older pending one for instance, must
        # be judged by its newest run, picked by check-run id, which is creation order.
        pytest.param(
            [
                _run(_EU, "queued", None, started_at="2026-07-18T10:00:00Z", run_id=1),
                _run(_EU, "completed", "success", started_at="2026-07-18T11:00:00Z", run_id=2),
            ],
            "complete",
            id="latest_run_per_name_wins",
        ),
        pytest.param(
            [
                _run(_EU, "completed", "success", started_at="2026-07-18T10:00:00Z", run_id=1),
                _run(_EU, "queued", None, started_at="2026-07-18T11:00:00Z", run_id=2),
            ],
            "pending",
            id="stale_completed_run_does_not_mask_newer_pending",
        ),
        pytest.param(
            [
                _run("stacks/app / dev-eu", "completed", "success"),
                _run("shipmate / gate", "queued", None),
                _run(_EU, "completed", "success"),
            ],
            "complete",
            id="non_apply_checks_ignored",
        ),
        # `all([])` is True, so an empty listing must not read complete. Mutation: drop
        # `latest and` from `verdict` -- the empty listing reads `complete`.
        pytest.param(
            [_run("stacks/app / dev-eu", "completed", "success")],
            "pending",
            id="no_apply_checks_at_all_is_pending",
        ),
        pytest.param(
            [_run(_EU, "completed", "cancelled")],
            "pending",
            id="cancelled_apply_stays_pending",
        ),
        pytest.param(
            [{"name": _EU, "status": "completed", "conclusion": "success"}],
            "complete",
            id="verdict_does_not_crash_on_runs_missing_started_at_and_id",
        ),
    ],
)
def test_verdict(runs, expected):
    assert ag.verdict(runs) == expected


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
    # A run missing the 'started_at' key entirely, not merely None or "", must not KeyError.
    # Ordering does not use started_at at all, but a run may still arrive without it, so
    # latest_by_name must tolerate its absence.
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
    # A run missing the 'id' key entirely must not KeyError. Fails when `run.get("id") or 0`
    # becomes a plain `run["id"]`.
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
    """A queued duplicate created after an apply completed carries a null started_at but a
    higher, newer id. Ordering by id means the newer queued run wins, so the name is judged
    pending and is not masked by the older completed run.

    Fails under a (started_at, id) ordering: the null started_at ('') sorts below the completed
    run's real timestamp, the completed run wins, and unapplied work is silently marked
    done."""
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


def test_parse_jsonl_truncates_long_offending_line():
    long_garbage = "x" * 500
    with pytest.raises(SystemExit) as exc_info:
        ag.parse_jsonl([long_garbage])
    msg = str(exc_info.value)
    assert len(msg) < 300
    assert "xxx" in msg


def test_latest_by_name_empty_prefix_gathers_all_latest_per_name():
    """summary-comment calls latest_by_name(prefix="") to gather every check-run on the head
    SHA, because the plan-link anchor is check_url's exact `plan / <env> / <stack>` lookup
    rather than a prefix filter. Latest-id-per-name still applies, and the coexisting apply check
    keeps its distinct `apply / ` name."""
    runs = [
        {"name": "plan / dev-eu / stacks/app", "id": 1, "html_url": "u1"},
        {"name": "plan / dev-eu / stacks/app", "id": 3, "html_url": "u3"},
        {"name": "apply / dev-eu / stacks/app", "id": 2, "html_url": "u2"},
    ]
    latest = ag.latest_by_name(runs, prefix="")
    assert set(latest) == {"plan / dev-eu / stacks/app", "apply / dev-eu / stacks/app"}
    assert latest["plan / dev-eu / stacks/app"]["html_url"] == "u3"


def test_latest_by_name_default_prefix_unchanged():
    runs = [
        {"name": "stacks/app / dev-eu", "id": 1},
        {
            "name": "apply / dev-eu / stacks/app",
            "id": 2,
            "status": "completed",
            "conclusion": "success",
        },
    ]
    assert set(ag.latest_by_name(runs)) == {"apply / dev-eu / stacks/app"}


def _run_obj(name, status="completed", conclusion="success", id=1, app_id=999):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "id": id,
        "app": {"id": app_id},
    }


def test_from_app_filters_foreign_and_missing_app():
    ours = _run_obj("apply / dev-eu / stacks/app", app_id=999)
    foreign = _run_obj("apply / dev-eu / stacks/app", id=2, app_id=15368)
    no_app = {
        "name": "apply / dev-eu / stacks/app",
        "status": "completed",
        "conclusion": "success",
        "id": 3,
    }
    assert ag.from_app([ours, foreign, no_app], "999") == [ours]


def test_forged_newer_completed_duplicate_cannot_green_a_pending_name():
    pending = _run_obj(
        "apply / dev-eu / stacks/app", status="queued", conclusion=None, id=10, app_id=999
    )
    forged = _run_obj("apply / dev-eu / stacks/app", id=11, app_id=15368)
    runs = ag.from_app([pending, forged], "999")
    assert ag.verdict(runs) == "pending"


def test_the_running_apply_run_check_never_holds_the_verdict():
    """The App's `shipmate / apply run` stays in progress while the gate is refreshed.

    Mutation: `latest_by_name(check_runs, prefix="")` in `verdict` -- `pending`."""
    done = _run_obj("apply / dev-eu / stacks/app", id=1)
    running = _run_obj("shipmate / apply run", status="in_progress", conclusion=None, id=2)
    assert ag.verdict([done, running]) == "complete"
    assert ag.latest_by_name([done, running]) == {
        "apply / dev-eu / stacks/app": {
            "name": "apply / dev-eu / stacks/app",
            "status": "completed",
            "conclusion": "success",
            "id": 1,
            "app": {"id": 999},
        }
    }


def test_from_app_empty_app_id_fails_loud():
    # An unset SHIPMATE_APP_ID renders as '', and int('') must not raw-traceback with a
    # ValueError that takes down the whole detect, gate or apply run. It fails loud with a
    # message naming the variable instead.
    runs = [_run_obj("apply / dev-eu / stacks/app")]
    with pytest.raises(SystemExit, match="SHIPMATE_APP_ID"):
        ag.from_app(runs, "")


def test_app_done_names_excludes_foreign_app_completed():
    """The real guard for detect scripts' main(): app_done_names ever ceasing to call from_app
    internally must redden here. A foreign-App completed check (15368, github-actions) must
    never appear in the result, even though an App-authored one (999) for a different name
    does."""
    ours = json.dumps(_run_obj("apply / dev-eu / stacks/app", id=1, app_id=999))
    foreign = json.dumps(_run_obj("apply / dev-us / stacks/app", id=2, app_id=15368))
    names = ag.app_done_names([ours, foreign], "999")
    assert names == {"apply / dev-eu / stacks/app"}
    assert "apply / dev-us / stacks/app" not in names


def _ext(name, id, external_id, app_id=999, started_at="2026-07-18T10:00:00Z"):
    run = _run_obj(name, id=id, app_id=app_id)
    run["external_id"] = external_id
    run["started_at"] = started_at
    return json.dumps(run)


# Must contain letters: an all-digit "hex" string parses as a JSON int, so it exercises the
# non-dict guard instead of the JSONDecodeError path this pins.
LEGACY_HEX = "a3f" + "b" * 61


def _record(plan_run="111", pr="8", **extra):
    """A record as pending-checks writes it, `pr` included unless None."""
    record = {"plan_run": plan_run, **extra}
    if pr is not None:
        record["pr"] = pr
    return json.dumps(record)


def test_plan_records_newest_app_run_supplies_the_record():
    """id order and started_at order deliberately disagree, and the higher id is listed first:
    the higher id wins although it started earlier. Mutation: iterate
    `{r["name"]: r for r in app_runs(...)}` (last seen per name) instead of latest_by_name."""
    older = _ext(
        "apply / dev-eu / stacks/app",
        1,
        _record(plan_run="111", plan_sha256="a" * 64),
        started_at="2026-07-18T12:00:00Z",
    )
    newer = _ext(
        "apply / dev-eu / stacks/app",
        2,
        _record(plan_run="222", plan_sha256="b" * 64),
        started_at="2026-07-18T09:00:00Z",
    )
    assert ag.plan_records([newer, older], "999") == {
        "apply / dev-eu / stacks/app": ("222", "b" * 64, "8")
    }


def test_behind_names_reads_only_a_newest_app_record_whose_behind_is_true():
    """`a` is behind on its newest run; `b`'s behind record sits under a newer plain one; `c`
    and `d` hold `"true"` and `1`; `e`'s newest behind record is another App's, over ours.

    Mutation: read every App run, not the newest per name -- `b` is listed, red.
    Mutation: test `record.get("behind")` for truth -- `c` and `d` are listed, red.
    Mutation: parse the lines without `from_app` -- `e` is listed, red."""
    behind = _record(behind=True, cells=1)
    lines = [
        _ext("apply / dev-eu / a", 1, behind),
        _ext("apply / dev-eu / b", 2, behind),
        _ext("apply / dev-eu / b", 3, _record(plan_sha256="a" * 64)),
        _ext("apply / dev-eu / c", 4, _record(behind="true")),
        _ext("apply / dev-eu / d", 5, _record(behind=1)),
        _ext("apply / dev-eu / e", 6, _record(plan_sha256="a" * 64)),
        _ext("apply / dev-eu / e", 7, behind, app_id=15368),
    ]
    assert ag.behind_names(lines, "999") == {"apply / dev-eu / a"}


def test_draft_names_reads_only_a_newest_app_record_whose_draft_is_true():
    """`a` is draft on its newest run; `b`'s draft record sits under a newer plain one; `c` and
    `d` hold `"true"` and `1`; `e` holds no `draft` key, as an older engine's record does;
    `f`'s newest draft record is another App's, over ours.

    Mutation: test `record.get("draft")` for truth -- `c` and `d` are listed, red.
    Mutation: read an absent key as draft (`record.get("draft", True) is True`) -- `b`, `e` and
    `f` are listed, red.
    Mutation: read every App run, not the newest per name -- `b` is listed, red.
    Mutation: parse the lines without `from_app` -- `f` is listed, red."""
    draft = _record(action="synchronize", draft=True)
    lines = [
        _ext("apply / sbx / a", 1, draft),
        _ext("apply / sbx / b", 2, draft),
        _ext("apply / sbx / b", 3, _record(action="synchronize")),
        _ext("apply / sbx / c", 4, _record(draft="true")),
        _ext("apply / sbx / d", 5, _record(draft=1)),
        _ext("apply / sbx / e", 6, _record(action="synchronize")),
        _ext("apply / sbx / f", 7, _record(action="synchronize")),
        _ext("apply / sbx / f", 8, draft, app_id=15368),
    ]
    assert ag.draft_names(lines, "999") == {"apply / sbx / a"}


def test_plan_records_ignores_foreign_app_even_when_newest():
    """Mutation: iterate `parse_jsonl(check_run_lines)` instead of `app_runs(...)`."""
    ours = _ext("apply / dev-eu / stacks/app", 1, _record(plan_run="111", plan_sha256="a" * 64))
    foreign = _ext(
        "apply / dev-eu / stacks/app",
        2,
        _record(plan_run="222", plan_sha256="b" * 64),
        app_id=15368,
    )
    assert ag.plan_records([ours, foreign], "999") == {
        "apply / dev-eu / stacks/app": ("111", "a" * 64, "8")
    }


# "1" * 64 is the only case that reaches the isinstance(record, dict) guard, because it parses
# as a JSON int rather than a dict. A bare 64-hex LEGACY_HEX is not JSON: it reads as absent,
# never as a JSONDecodeError traceback.
@pytest.mark.parametrize("external_id", [None, "", "not json at all", "1" * 64, LEGACY_HEX])
def test_plan_records_unusable_external_id_is_absent(external_id):
    """Mutations: drop the `except json.JSONDecodeError` arm (the non-JSON cases raise); drop
    the `isinstance(record, dict)` guard (`"1" * 64` raises on `.get`)."""
    line = _ext("apply / dev-eu / stacks/app", 1, external_id)
    assert ag.plan_records([line], "999") == {}


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"plan_run": "12x4"},
        {"plan_run": ""},
        {"plan_run": 1234},
    ],
)
def test_plan_records_bad_plan_run_value_reads_as_none(record):
    """Mutation: keep any truthy `plan_run` (`plan_run if plan_run else None`); `"12x4"` and
    `1234` turn red."""
    line = _ext("apply / dev-eu / stacks/app", 1, json.dumps(record))
    assert ag.plan_records([line], "999") == {"apply / dev-eu / stacks/app": (None, None, None)}


def test_plan_records_only_apply_prefixed_names():
    """latest_by_name's default prefix must not be overridden: a plan check with a well-formed
    record contributes nothing. Mutation: `latest_by_name(..., prefix="")`."""
    apply_line = _ext("apply / dev-eu / stacks/app", 1, _record())
    plan_line = _ext("stacks/app / dev-eu", 2, _record())
    assert ag.plan_records([apply_line, plan_line], "999") == {
        "apply / dev-eu / stacks/app": ("111", None, "8")
    }


def test_plan_records_reads_the_recorded_digest():
    """Mutation: read the digest from `record.get("fingerprint")`, also 64 lowercase hex."""
    line = _ext("apply / dev-eu / stacks/app", 1, _record(plan_sha256="c" * 64))
    assert ag.plan_records([line], "999") == {"apply / dev-eu / stacks/app": ("111", "c" * 64, "8")}


# 64 chars where the value is merely not lowercase hex, so a length-only substitute for the
# fullmatch cannot absorb the case. The int exercises the isinstance guard: without it,
# re.fullmatch would raise TypeError rather than read as None.
@pytest.mark.parametrize(
    "digest",
    [None, "", "z" * 64, "c" * 63, "c" * 65, "C" * 64, 1234],
)
def test_plan_records_unusable_digest_reads_as_none(digest):
    """Mutation: the digest pattern `.{64}`; `"z" * 64` and `"C" * 64` turn red."""
    line = _ext("apply / dev-eu / stacks/app", 1, _record(plan_sha256=digest))
    assert ag.plan_records([line], "999") == {"apply / dev-eu / stacks/app": ("111", None, "8")}


def test_plan_records_uppercase_digest_reads_as_none():
    """Explicit literal beside the parametrization: sha256sum on the runner prints lowercase,
    and the comparison at apply is a string equality, so an uppercase record would never match
    anyway. Mutation: the digest pattern `.{64}`."""
    line = _ext("apply / dev-eu / stacks/app", 1, _record(plan_sha256="ABCDEF" + "a" * 58))
    assert ag.plan_records([line], "999") == {"apply / dev-eu / stacks/app": ("111", None, "8")}


def test_plan_records_missing_digest_reads_as_none():
    """A record that carries no digest; with_plan_runs refuses on a falsy digest, so the slot
    is None, not a placeholder. Mutation: `else ""` for the digest slot."""
    line = _ext("apply / dev-eu / stacks/app", 1, _record())
    assert ag.plan_records([line], "999") == {"apply / dev-eu / stacks/app": ("111", None, "8")}


@pytest.mark.parametrize("pr", ["8", "1", "4096"])
def test_plan_records_reads_the_recorded_pull_request(pr):
    """Mutation: read the pull request from `record.get("plan_run")`, also decimal -- red."""
    line = _ext("apply / dev-eu / stacks/app", 1, _record(plan_sha256="c" * 64, pr=pr))
    assert ag.plan_records([line], "999") == {"apply / dev-eu / stacks/app": ("111", "c" * 64, pr)}


# Each value is truthy or a non-string: "0" and "012" are not pull request numbers, 8 is a JSON
# number where pending-checks writes a string, "8 " fails the fullmatch, and eleven digits
# exceed `is_pr_number`'s bound.
@pytest.mark.parametrize("pr", [None, "", "0", "012", "12a", "8 ", "12345678901", 8, True])
def test_plan_records_unusable_pull_request_reads_as_none(pr):
    """Mutation: keep any truthy `pr` (`pr if pr else None`); `"0"`, `"012"`, `"12a"`, `8` and
    `True` turn red. Mutation: drop the `isinstance(pr, str)` guard; `8` and `True` raise.
    Mutation: `re.fullmatch(r"[1-9][0-9]*", pr)` for `is_pr_number(pr)`; `"12345678901"` red."""
    line = _ext("apply / dev-eu / stacks/app", 1, _record(plan_sha256="c" * 64, pr=pr))
    assert ag.plan_records([line], "999") == {
        "apply / dev-eu / stacks/app": ("111", "c" * 64, None)
    }


def test_plan_records_zero_pull_request_reads_as_none():
    """Explicit literal beside the parametrization: `"0"` is a decimal string, so only the
    leading-digit rule refuses it. Mutation: the pattern `[0-9]+`."""
    line = _ext("apply / dev-eu / stacks/app", 1, _record(pr="0"))
    assert ag.plan_records([line], "999") == {"apply / dev-eu / stacks/app": ("111", None, None)}


def _plan_runs_stdout(monkeypatch, capsys, *lines):
    monkeypatch.setattr(ag.sys, "stdin", io.StringIO("\n".join(lines)))
    monkeypatch.setenv("SHIPMATE_APP_ID", "999")
    ag.main()
    return capsys.readouterr().out


def test_main_prints_the_run_projection(monkeypatch, capsys):
    """comment-ops' reviewed-plan lookup is `main`'s output, read by authorize as PLAN_RUN_JSON; its
    bytes are compared whole. Mutation: project every record, `None` runs included -- the
    record without a plan run prints as `null`, red. Mutation: project only records carrying a
    `pr` -- `stacks/web`, recorded before pull requests were, drops out, red."""
    out = _plan_runs_stdout(
        monkeypatch,
        capsys,
        _ext("apply / dev-eu / stacks/app", 1, _record(plan_run="777")),
        _ext("apply / dev-eu / stacks/api", 2, LEGACY_HEX),
        _ext("apply / dev-eu / stacks/dns", 3, json.dumps({"plan_sha256": "c" * 64})),
        _ext(
            "apply / dev-eu / stacks/web", 4, _record(plan_run="778", plan_sha256="c" * 64, pr=None)
        ),
    )
    assert out == '{"apply / dev-eu / stacks/app": "777", "apply / dev-eu / stacks/web": "778"}\n'


def test_main_is_identical_with_and_without_a_digest(monkeypatch, capsys):
    """authorize reads `main`'s membership; a name must not drop out of it because
    its record carries no digest, or an operator is told their plan does not exist. Both
    outputs are compared whole against the same hand-written literal. Mutation: project only
    records carrying both a run and a digest -- the digest-less one prints `{}`, red."""
    expected = '{"apply / dev-eu / stacks/app": "111"}\n'
    with_hash = _ext("apply / dev-eu / stacks/app", 1, _record(plan_sha256="c" * 64))
    without_hash = _ext("apply / dev-eu / stacks/app", 1, _record())
    assert _plan_runs_stdout(monkeypatch, capsys, with_hash) == expected
    assert _plan_runs_stdout(monkeypatch, capsys, without_hash) == expected


_MISSING = object()


def _counted(name, id, plan_run, cells=_MISSING, status="queued", app_id=999):
    """An App-authored check whose record names `plan_run` and declares `cells`, or no
    `cells` key when left out."""
    record = {"plan_run": plan_run, "pr": "8"}
    if cells is not _MISSING:
        record["cells"] = cells
    conclusion = "success" if status == "completed" else None
    run = _run_obj(name, status=status, conclusion=conclusion, id=id, app_id=app_id)
    run["external_id"] = json.dumps(record)
    return json.dumps(run)


_A, _B, _C = "apply / dev / a", "apply / dev / b", "apply / dev / c"


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        pytest.param([_counted(_A, 1, "7", 2), _counted(_B, 2, "7", 2)], {}, id="complete"),
        pytest.param([_counted(_A, 1, "7", 2)], {"7": (1, (2,), False)}, id="partial"),
        pytest.param([_counted(_A, 1, "7")], {"7": (1, (), True)}, id="missing"),
        pytest.param([_counted(_A, 1, "7", "1")], {"7": (1, (), True)}, id="string"),
        pytest.param([_counted(_A, 1, "7", True)], {"7": (1, (), True)}, id="true"),
        pytest.param(
            [_counted(_A, 1, "7", 2), _counted(_B, 2, "7")],
            {"7": (2, (2,), True)},
            id="uncounted-sibling",
        ),
        pytest.param(
            [_counted(_A, 1, "7", 2), _counted(_B, 2, "7", 3)],
            {"7": (2, (2, 3), False)},
            id="disagreeing",
        ),
        pytest.param(
            [_counted(_A, 1, "7", 2), _counted(_B, 2, "7", 2), _counted(_C, 3, "7", 2)],
            {"7": (3, (2,), False)},
            id="over-count",
        ),
        pytest.param(
            [
                _counted(_A, 1, "7", 3),
                _counted(_B, 2, "7", 3),
                _counted(_C, 3, "7", 3),
                _counted(_A, 4, "8", 2),
                _counted(_B, 5, "8", 2),
            ],
            {},
            id="superseded",
        ),
        pytest.param(
            [_counted(_A, 1, "7", 2, status="completed"), _counted(_B, 2, "7", 2)],
            {},
            id="completed",
        ),
        pytest.param(
            [
                _counted(_A, 1, "7", 2),
                _counted(_B, 2, "7", 2),
                _counted("plan / dev / c", 3, "7", 2),
            ],
            {},
            id="mirror",
        ),
        pytest.param(
            [
                _counted(_A, 1, "7", 2),
                _counted(_B, 2, "7", 2),
                _counted(_C, 3, "7", 2, app_id=15368),
            ],
            {},
            id="other-app",
        ),
    ],
)
def test_incomplete_runs(lines, expected):
    """A run is complete only when its declared counts are exactly {distinct names}, counted
    over every App-authored apply check, of any status, whose record names it. Mutations, each
    red on its row: `latest_by_name(app_runs(...))` for `app_runs(...)` (superseded); drop the
    `APPLY_PREFIX` filter (mirror); `parse_jsonl(check_run_lines)` for `app_runs(...)`
    (other-app); drop `not isinstance(cells, bool)` (true); `int(cells)` for a digit string
    (string); skip `completed` checks (completed); `len(named) >= max(counts)` for the equality
    (over-count); `len(named) <= min(counts)` for the equality (partial); compare only the
    first declared count (disagreeing); skip a record with no valid count instead of marking
    its run (uncounted-sibling); `False` for `None in counts[run]` (uncounted-sibling)."""
    assert ag.incomplete_runs(lines, "999") == expected


_HEAD = "a" * 40


def _newest(*lines):
    argv = []

    def run(args):
        argv.append(args)
        return "\n".join(lines)

    return ag.newest_autoplan("o/r", _HEAD, run=run), argv


def test_newest_autoplan_returns_the_whole_listed_run_of_the_greatest_id():
    """Lines from two pages, the greatest id neither first nor last. Mutation: `min` for `max`
    (run 5). Mutation: return the id instead of the object. Mutation: list again in
    `newest_autoplan` (the argv lists it twice). Mutation: drop `_walk`'s sort (run 5)."""
    newest, argv = _newest(
        json.dumps(listed_autoplan(5, _HEAD)),
        json.dumps(listed_autoplan(12, _HEAD, event="x")),
        json.dumps(listed_autoplan(7, _HEAD)),
    )
    assert newest == {
        "id": 12,
        "event": "x",
        "head_sha": _HEAD,
        "created_at": AUTOPLAN_CREATED,
        "actor": {"login": "k3np"},
        "triggering_actor": {"login": "k3np"},
    }
    assert argv == [autoplans_read("o/r", _HEAD)]


def test_autoplan_runs_returns_every_listed_run_in_listing_order():
    """The rows carry `status` and `conclusion`, which `replan` judges. Mutation: return only the
    newest row; drop `status, conclusion` from the projection (the argv differs)."""
    rows = [
        listed_autoplan(5, _HEAD, status="completed", conclusion="success"),
        listed_autoplan(12, _HEAD, status="queued", conclusion=None),
    ]
    argv = []

    def run(args):
        argv.append(args)
        return "".join(json.dumps(row) + "\n" for row in rows)

    assert ag.autoplan_runs("o/r", _HEAD, run=run) == [
        {
            "id": 5,
            "event": "pull_request_target",
            "head_sha": _HEAD,
            "created_at": AUTOPLAN_CREATED,
            "actor": {"login": "k3np"},
            "triggering_actor": {"login": "k3np"},
            "status": "completed",
            "conclusion": "success",
        },
        {
            "id": 12,
            "event": "pull_request_target",
            "head_sha": _HEAD,
            "created_at": AUTOPLAN_CREATED,
            "actor": {"login": "k3np"},
            "triggering_actor": {"login": "k3np"},
            "status": "queued",
            "conclusion": None,
        },
    ]
    assert argv == [autoplans_read("o/r", _HEAD)]


def test_autoplan_runs_retries_a_transient_listing_failure(monkeypatch):
    """Mutation: call `run([...])` without `retried` -- the 502 raises."""
    slept = []
    monkeypatch.setattr("_shipmate._sleep", slept.append)
    argv = []

    def run(args):
        argv.append(args)
        if len(argv) == 1:
            raise SystemExit("::error::command failed (1): gh api x\ngh: Bad Gateway (HTTP 502)")
        return json.dumps(listed_autoplan(5, _HEAD)) + "\n"

    assert ag.autoplan_runs("o/r", _HEAD, run=run) == [listed_autoplan(5, _HEAD)]
    assert (argv, slept) == ([autoplans_read("o/r", _HEAD)] * 2, [2])


def test_newest_autoplan_of_an_empty_listing_is_none():
    """Mutation: `default=0` for `default=None` in the `max`."""
    assert _newest()[0] is None


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("7", id="not-an-object"),
        pytest.param(json.dumps(listed_autoplan(True, _HEAD)), id="bool-id"),
        pytest.param(
            json.dumps({k: v for k, v in listed_autoplan(12, _HEAD).items() if k != "head_sha"}),
            id="no-head-sha",
        ),
    ],
)
def test_newest_autoplan_raises_value_error_on_a_malformed_line(line):
    """Both callers wrap `ValueError` only. Mutation: drop the `isinstance(listed, dict)` check
    (not-an-object raises `TypeError`). Mutation: accept a bool id (`isinstance(listed["id"],
    int)`; bool-id returns). Mutation: drop the key check (no-head-sha returns)."""
    with pytest.raises(ValueError):
        _newest(json.dumps(listed_autoplan(5, _HEAD)), line)


_OTHER = "b" * 40
_PUSHED = {"x": ("7", "synchronize", False), "y": ("7", "opened", False)}


@pytest.mark.parametrize(
    ("newest", "cells", "expected"),
    [
        pytest.param(listed_autoplan(7, _HEAD), _PUSHED, None, id="pass"),
        pytest.param(listed_autoplan(7, _HEAD), {}, None, id="pass-no-cells"),
        pytest.param(None, _PUSHED, ("unlisted", None), id="unlisted"),
        pytest.param(listed_autoplan(8, _HEAD), _PUSHED, ("newer", 8), id="newer"),
        pytest.param(listed_autoplan(6, _HEAD), _PUSHED, ("newer", 6), id="older"),
        pytest.param(
            listed_autoplan(8, _HEAD, event="workflow_dispatch"), {}, ("newer", 8), id="newer-first"
        ),
        pytest.param(
            listed_autoplan(7, _HEAD, event="workflow_dispatch"),
            _PUSHED,
            ("event", "workflow_dispatch"),
            id="event",
        ),
        pytest.param(
            listed_autoplan(7, _HEAD, event="push", head_sha=_OTHER),
            {},
            ("event", "push"),
            id="event-first",
        ),
        pytest.param(
            listed_autoplan(7, _HEAD, head_sha=_OTHER), _PUSHED, ("head", _OTHER), id="head"
        ),
        pytest.param(
            listed_autoplan(7, _HEAD, head_sha=_OTHER),
            {"x": ("8", None, False)},
            ("head", _OTHER),
            id="head-first",
        ),
        pytest.param(
            listed_autoplan(7, _HEAD),
            {
                "x": ("7", "synchronize", False),
                "y": ("8", "synchronize", False),
                "z": (None, "opened", False),
            },
            ("record", [("y", "8"), ("z", None)]),
            id="record",
        ),
        pytest.param(
            listed_autoplan(7, _HEAD),
            {"x": ("7", "reopened", False), "y": ("8", "synchronize", False)},
            ("record", [("y", "8")]),
            id="record-first",
        ),
        pytest.param(
            listed_autoplan(7, _HEAD),
            {
                "x": ("7", "reopened", False),
                "y": ("7", "ready_for_review", False),
                "z": ("7", None, False),
            },
            ("action", [("x", "reopened"), ("z", None)]),
            id="action",
        ),
    ],
)
def test_provenance_reason_names_the_first_failing_check(newest, cells, expected):
    """Plan run "7" of `_HEAD`. Mutation: drop the `newest is None` check (unlisted raises
    `TypeError`). Mutation: drop the id check, or `<` for `!=` (newer and older pass or refuse
    otherwise). Mutation: drop the event check (event passes). Mutation: drop the head check
    (head passes). Mutation: drop the record check (record passes). Mutation: drop the action
    check, or `==` for `!=` in the record check (action passes, or pass refuses). Mutation:
    test the event before the id (newer-first reads `event`). Mutation: drop `ready_for_review`
    from `AUTO_ACTIONS` (action lists `y`)."""
    assert ag.provenance_reason(newest, "7", _HEAD, cells, newest) == expected


def test_provenance_reason_compares_run_ids_as_strings():
    """A record `"07"` names no run 7. Mutation: `newest["id"] != int(plan_run)` (passes)."""
    plan = listed_autoplan(7, _HEAD)
    assert ag.provenance_reason(plan, "07", _HEAD, {}, plan) == ("newer", 7)


@pytest.mark.parametrize(
    ("authorizer", "cells", "expected"),
    [
        pytest.param(
            listed_autoplan(7, _HEAD),
            {"x": ("7", "synchronize", True), "y": ("7", "opened", False)},
            ("draft", ["x"]),
            id="the-plan-authorizes",
        ),
        pytest.param(None, {"x": ("7", "synchronize", True)}, ("draft", ["x"]), id="no-authorizer"),
        pytest.param(
            listed_autoplan(8, _HEAD, actor={"login": "octocat"}),
            {"x": ("7", "synchronize", True)},
            None,
            id="a-deferred-ready-run-authorizes",
        ),
        pytest.param(
            listed_autoplan(7, _HEAD),
            {"x": ("7", "reopened", True), "y": ("7", "synchronize", True)},
            ("action", [("x", "reopened")]),
            id="action-first",
        ),
        pytest.param(
            listed_autoplan(7, _HEAD), {"x": ("7", "synchronize", False)}, None, id="not-draft"
        ),
    ],
)
def test_provenance_reason_refuses_a_draft_origin_record_no_distinct_run_authorizes(
    authorizer, cells, expected
):
    """Plan run "7" of `_HEAD`, listed. Mutation: drop the `draft` check (the-plan-authorizes and
    no-authorizer pass). Mutation: pass a draft record whenever `authorizer` is not None
    (the-plan-authorizes passes). Mutation: refuse every draft record (a-deferred-ready-run-
    authorizes refuses). Mutation: test `draft` before the action (action-first reads
    `draft`)."""
    plan = listed_autoplan(7, _HEAD)
    assert ag.provenance_reason(plan, "7", _HEAD, cells, authorizer) == expected


def test_newest_autoplan_selects_by_id_not_by_creation():
    """The greatest id is created first here. Mutation: select by `created_at` (run 5)."""
    newest, _ = _newest(
        json.dumps(listed_autoplan(5, _HEAD, created_at="2026-11-15T22:47:49Z")),
        json.dumps(listed_autoplan(12, _HEAD, created_at="2026-11-15T22:47:48Z")),
    )
    assert newest["id"] == 12


_JOBS_DIR = pathlib.Path(__file__).parent / "fixtures"


def _jobs_body(kind, **over):
    """`fixtures/jobs-<kind>.json`, each job with `over` set on it. `deferred` is hand-built
    from the `draft-skipped` capture: `plan / defer` `success`, `plan / summary` `skipped`; no
    run had a `defer` job then. Replace it with a raw capture once the live check records one."""
    body = json.loads((_JOBS_DIR / f"jobs-{kind}.json").read_text(encoding="utf-8"))
    body["jobs"] = [job | over for job in body["jobs"]]
    return json.dumps(body)


def _jobs_argv(run_id):
    return ["gh", "api", f"repos/o/r/actions/runs/{run_id}/jobs?filter=latest&per_page=100&page=1"]


def _walk(rows, jobs=None):
    """`newest_autoplan` over listing `rows`, each jobs read of run id `k` answered by
    `jobs[k]`, and every argv it ran."""
    argv = []

    def run(args):
        argv.append(args)
        if args == autoplans_read("o/r", _HEAD):
            return "".join(json.dumps(row) + "\n" for row in rows)
        return (jobs or {})[int(args[2].split("/")[5])]

    return ag.newest_autoplan("o/r", _HEAD, run=run), argv


def _ended(run_id, conclusion="success", status="completed"):
    return listed_autoplan(run_id, _HEAD, status=status, conclusion=conclusion, run_attempt=1)


_EARLIER, _READY = _ended(5), _ended(9)


def test_newest_autoplan_reads_past_a_deferred_ready_run():
    """The listing is in ascending id order. Mutation: drop the skip (return the newest run, R).
    Mutation: drop `_walk`'s sort (run 5's jobs are read, unexpected)."""
    newest, argv = _walk([_EARLIER, _READY], {9: _jobs_body("deferred")})
    assert newest == {
        "id": 5,
        "event": "pull_request_target",
        "head_sha": _HEAD,
        "created_at": AUTOPLAN_CREATED,
        "actor": {"login": "k3np"},
        "triggering_actor": {"login": "k3np"},
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
    }
    assert argv == [autoplans_read("o/r", _HEAD), _jobs_argv(9)]


def test_newest_autoplan_returns_a_cancelled_ready_run_without_reading_its_jobs():
    """Mutation: read jobs for any completed run (R's deferred jobs skip it, E returns)."""
    ready = _ended(9, conclusion="cancelled")
    newest, argv = _walk([_EARLIER, ready], {9: _jobs_body("deferred")})
    assert (newest, argv) == (ready, [autoplans_read("o/r", _HEAD)])


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_newest_autoplan_returns_a_live_ready_run_without_reading_its_jobs(status):
    """Mutation: skip any run whose jobs show `plan / defer` success, whatever its listed state
    (R's deferred jobs skip it, E returns)."""
    ready = _ended(9, conclusion=None, status=status)
    newest, argv = _walk([_EARLIER, ready], {9: _jobs_body("deferred")})
    assert (newest, argv) == (ready, [autoplans_read("o/r", _HEAD)])


def test_newest_autoplan_returns_a_ready_run_that_did_not_defer():
    """A draft-skipped run lists `plan / detect` skipped but no `plan / defer`. Mutation:
    `deferred` reads only `detect` skipped (R is skipped, E returns)."""
    newest, argv = _walk([_EARLIER, _READY], {9: _jobs_body("draft-skipped")})
    assert (newest, argv) == (_READY, [autoplans_read("o/r", _HEAD), _jobs_argv(9)])


def test_newest_autoplan_returns_a_ready_run_whose_jobs_carry_another_attempt():
    """A re-run made the deferred run live again. Mutation: drop the attempt comparison."""
    newest, argv = _walk([_EARLIER, _READY], {9: _jobs_body("deferred", run_attempt=2)})
    assert (newest, argv) == (_READY, [autoplans_read("o/r", _HEAD), _jobs_argv(9)])


def test_newest_autoplan_reads_jobs_at_another_attempt_again():
    """Jobs at another attempt than the listing (a lagging read) are not cached: the next walk
    reads them again and, now at the listed attempt, reads past the deferred run. Mutation:
    cache the verdict whatever the jobs' attempt (the second walk returns run 9 unread)."""
    argv, answers = [], [_jobs_body("deferred", run_attempt=2), _jobs_body("deferred")]

    def run(args):
        argv.append(args)
        if args == autoplans_read("o/r", _HEAD):
            return "".join(json.dumps(row) + "\n" for row in [_EARLIER, _READY])
        return answers.pop(0)

    newest = [ag.newest_autoplan("o/r", _HEAD, run=run) for _ in range(2)]
    assert newest == [_READY, _EARLIER]
    assert argv == [autoplans_read("o/r", _HEAD), _jobs_argv(9)] * 2


def test_newest_autoplan_raises_value_error_on_an_incomplete_jobs_listing():
    """The listing counts one job more than it returns. Mutation: read an incomplete listing as
    not deferred (R returns)."""
    body = json.loads(_jobs_body("deferred"))
    body["total_count"] += 1
    with pytest.raises(ValueError, match=r"^the jobs of autoplan run 9 could not be listed$"):
        _walk([_EARLIER, _READY], {9: json.dumps(body)})


def test_newest_autoplan_returns_a_newest_run_that_did_not_defer_after_one_jobs_read():
    """Mutation: return a completed successful run before reading its jobs (the argv lacks the
    read)."""
    newest, argv = _walk([_ended(3), _EARLIER], {5: _jobs_body("zero-cell")})
    assert (newest, argv) == (_EARLIER, [autoplans_read("o/r", _HEAD), _jobs_argv(5)])


def test_newest_autoplan_reads_past_two_deferred_runs():
    """Mutation: skip at most one run (run 7 returns)."""
    deferred = _jobs_body("deferred")
    newest, argv = _walk([_EARLIER, _READY, _ended(7)], {9: deferred, 7: deferred})
    assert newest == _EARLIER
    assert argv == [autoplans_read("o/r", _HEAD), _jobs_argv(9), _jobs_argv(7)]


def test_newest_autoplan_reads_an_ended_runs_jobs_once_per_process():
    """Two walks of one listing read the listing twice and run 9's jobs once: a wave cell
    re-reads the newest autoplan. Mutation: read the jobs on every call (the cache unread)."""
    argv = []

    def run(args):
        argv.append(args)
        if args == autoplans_read("o/r", _HEAD):
            return "".join(json.dumps(row) + "\n" for row in [_EARLIER, _READY])
        return _jobs_body("deferred")

    newest = [ag.newest_autoplan("o/r", _HEAD, run=run) for _ in range(2)]
    assert newest == [_EARLIER, _EARLIER]
    assert argv == [autoplans_read("o/r", _HEAD), _jobs_argv(9), autoplans_read("o/r", _HEAD)]


def test_newest_autoplan_returns_a_lone_completed_run_without_reading_its_jobs():
    """A deferred run always has an earlier one. Mutation: drop the older-sibling condition (the
    deferred jobs skip the only run and None returns)."""
    newest, argv = _walk([_READY], {9: _jobs_body("deferred")})
    assert (newest, argv) == (_READY, [autoplans_read("o/r", _HEAD)])


#: Push run 1000 by `k3np` and the ready-for-review run 1001 its clicker `octocat` started.
_CLICKER = {"login": "octocat"}
_PUSH = listed_autoplan(1000, _HEAD, status="completed", conclusion="success", run_attempt=1)
_CLICK = listed_autoplan(
    1001,
    _HEAD,
    status="completed",
    conclusion="success",
    run_attempt=1,
    actor=_CLICKER,
    triggering_actor=_CLICKER,
)
_LIVE = {**_CLICK, "status": "in_progress", "conclusion": None}


def _source(rows, jobs):
    """`auto_source`'s `(plan id, authorizer id)` over listing `rows`, each jobs read of run
    id `k` answered by `jobs[k]`, and every argv it ran."""
    argv = []

    def run(args):
        argv.append(args)
        if args == autoplans_read("o/r", _HEAD):
            return "".join(json.dumps(row) + "\n" for row in rows)
        return jobs[int(args[2].split("/")[5])]

    plan, authorizer = ag.auto_source("o/r", _HEAD, run=run)
    return (plan and plan["id"], authorizer and authorizer["id"]), argv


def _short():
    """`deferred_in_progress_jobs` counting one job more than it lists."""
    body = json.loads(deferred_in_progress_jobs())
    body["total_count"] += 1
    return json.dumps(body)


def _defer_running():
    """`deferred_in_progress_jobs` with `plan / defer` itself still `in_progress`."""
    body = json.loads(deferred_in_progress_jobs())
    for job in body["jobs"]:
        if job["name"] == "plan / defer":
            job |= {"status": "in_progress", "conclusion": None}
    return json.dumps(body)


@pytest.mark.parametrize(
    ("rows", "jobs", "expected"),
    [
        pytest.param([_CLICK, _PUSH], _jobs_body("deferred"), (1000, 1001), id="ended-deferred"),
        pytest.param([_PUSH, _LIVE], deferred_in_progress_jobs(), (1000, 1001), id="dispatching"),
        pytest.param(
            [_PUSH, _CLICK], deferred_auto_failed_jobs(), (1000, 1001), id="dispatch-failed"
        ),
        pytest.param([_PUSH, _LIVE], _defer_running(), (1001, 1001), id="defer-running"),
        pytest.param(
            [_PUSH, {**_LIVE, "status": "queued"}],
            json.dumps({"total_count": 0, "jobs": []}),
            (1001, 1001),
            id="queued-no-jobs",
        ),
        pytest.param([_PUSH, _CLICK], _jobs_body("zero-cell"), (1001, 1001), id="planned"),
        pytest.param(
            [{**_PUSH, "conclusion": "failure"}, _CLICK],
            _jobs_body("deferred"),
            (1001, 1001),
            id="plan-failed",
        ),
        pytest.param(
            [{**_PUSH, "head_sha": "b" * 40}, _CLICK],
            _jobs_body("deferred"),
            (1001, 1001),
            id="plan-of-another-head",
        ),
    ],
)
def test_auto_source_names_the_plan_and_the_run_that_authorizes_it(rows, jobs, expected):
    """One listing, then run 1001's jobs once. Mutation: return `(plan, plan)` (every deferred row
    names 1000 twice). Mutation: require `completed` before reading a run's jobs (dispatching
    names 1001 twice). Mutation: make `deferred` require every other job `skipped` or `success`
    (dispatch-failed names 1001 twice). Mutation: treat any in-progress run as deferred
    (defer-running names 1000). Mutation: skip the jobs read and treat the run as deferred
    (planned names 1000). Mutation: drop the plan-status check (plan-failed names 1000).
    Mutation: drop the head check (plan-of-another-head names 1000). Mutation: raise on a live
    run's empty jobs listing as on an ended one (queued-no-jobs raises `ValueError`). Mutation: call
    `autoplan_runs` twice (the argv lists it twice)."""
    assert _source(rows, {1001: jobs}) == (
        expected,
        [autoplans_read("o/r", _HEAD), _jobs_argv(1001)],
    )


def test_auto_source_raises_on_a_live_runs_short_jobs_listing():
    """A short listing may hide `defer`. Mutation: treat a live run's short listing as not
    deferred (1001 names itself twice)."""
    with pytest.raises(ValueError, match="the jobs of autoplan run 1001 could not be listed"):
        _source([_PUSH, _LIVE], {1001: _short()})


def test_auto_source_of_an_empty_listing_is_none_twice():
    """Mutation: return `None` (unpacking it raises)."""
    assert _source([], {}) == ((None, None), [autoplans_read("o/r", _HEAD)])


def test_a_failed_dispatch_leaves_the_ready_run_read_past_by_newest_autoplan():
    """Its job-level `continue-on-error` leaves the run `success`. Mutation: make `deferred`
    require every other job `skipped` or `success` (run 1001 returns)."""
    newest, argv = _walk([_PUSH, _CLICK], {1001: deferred_auto_failed_jobs()})
    assert (newest, argv) == (_PUSH, [autoplans_read("o/r", _HEAD), _jobs_argv(1001)])


def test_auto_source_does_not_cache_a_running_runs_jobs():
    """Two reads while run 1001 dispatches read its jobs twice: it may yet be re-run. Mutation:
    cache the verdict of a run still running (the second read skips the jobs)."""
    argv = []

    def run(args):
        argv.append(args)
        if args == autoplans_read("o/r", _HEAD):
            return "".join(json.dumps(row) + "\n" for row in [_PUSH, _LIVE])
        return deferred_in_progress_jobs()

    sources = [ag.auto_source("o/r", _HEAD, run=run) for _ in range(2)]
    assert sources == [(_PUSH, _LIVE)] * 2
    assert argv == [autoplans_read("o/r", _HEAD), _jobs_argv(1001)] * 2


@pytest.mark.parametrize("name", ["plan / defer", "plan / detect"])
def test_deferred_raises_value_error_on_two_jobs_of_one_name(name):
    """Mutation: drop the count check (two `defer` jobs read as deferred, two `detect` as
    not)."""
    jobs = json.loads(_jobs_body("deferred"))["jobs"]
    with pytest.raises(ValueError):
        ag.deferred([*jobs, next(j for j in jobs if j["name"] == name)])


def test_newer_plan_names_a_counted_run_over_an_incomplete_listing():
    """Mutations: test `complete` first (the counted run reads as a listing failure); name the
    last counted run."""
    runs = [{"id": 4}, {"id": 9}]
    assert ag.newer_plan(runs, False) == ("newer-plan", {"id": 4})
    assert ag.newer_plan([], False) == ("plan-listing", None)
    assert ag.newer_plan([], True) is None
