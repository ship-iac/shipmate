import json
import os

import pytest
from _loader import github_outputs, load_script

gate_state = load_script("gate-state")


def d(**kw):
    base = {
        "detect_result": "success",
        "plan_result": "success",
        "planned_cells": "3",
        "cell_count": 3,
        "pending": True,
        "behind": 0,
        "open_older": 0,
    }
    base.update(kw)
    return gate_state.decide(**base)


def test_pending_applies_block_the_merge():
    state, desc, mode = d()
    assert state == "pending"
    assert "waiting to be applied" in desc
    assert mode == "post"


def test_all_applied_greens_the_gate():
    state, _, mode = d(pending=False)
    assert state == "success"
    assert mode == "post"


_DETECT_SKIPPED_DESCRIPTION = (
    "change detection did not succeed (skipped); fix the plan / detect job before merging"
)


def test_a_draft_autoplan_writes_a_pending_gate_that_names_the_draft():
    """Mutation: delete the draft branch in `decide` -> the skipped detect falls to `failure`."""
    got = d(detect_result="skipped", planned_cells="", cell_count=0, pending=False, is_draft=True)
    assert got == ("pending", gate_state.DRAFT_DESCRIPTION, "draft")


def test_an_on_demand_plan_of_a_draft_with_detect_skipped_is_a_failure():
    """A person asked for this plan, so a skipped detect is a failure, not a draft.

    Mutation: drop `not on_demand` from the draft branch -> `pending`.
    """
    got = d(
        detect_result="skipped",
        planned_cells="",
        cell_count=0,
        pending=False,
        is_draft=True,
        on_demand=True,
    )
    assert got == ("failure", _DETECT_SKIPPED_DESCRIPTION, "held")


def test_a_draft_whose_detect_ran_is_judged_on_its_evidence():
    """Mutation: drop `detect_result == "skipped"` from the draft branch -> the draft reason."""
    got = d(is_draft=True)
    assert got == (
        "pending",
        "one or more stacks are waiting to be applied; merge is blocked until applies complete",
        "post",
    )


def test_a_non_draft_with_detect_skipped_is_a_failure():
    """Mutation: drop `is_draft` from the draft branch -> `pending`."""
    got = d(detect_result="skipped", planned_cells="", cell_count=0, pending=False)
    assert got == ("failure", _DETECT_SKIPPED_DESCRIPTION, "held")


def test_detect_failure_is_a_red_gate_not_a_silent_skip():
    # detect is the change detection. Without it there is no claim to make, and writing nothing
    # would leave the pull request with no gate to explain it.
    state, desc, mode = d(detect_result="failure")
    assert state == "failure"
    assert mode == "held"
    assert "detect" in desc


def test_a_detect_refusal_names_itself_in_the_gate():
    """Mutation: return the generic text when a refusal exists -- the refusal is not named."""
    got = d(detect_result="failure", refusal="stack 'x' carries 2 workload tags")
    assert got == ("failure", "detect failed: stack 'x' carries 2 workload tags", "held")


def test_a_detect_failure_without_a_refusal_keeps_the_generic_text():
    """A crash with no `::error::` line writes no refusal.

    Mutation: write `detect failed: ` for an empty refusal -- the description changes."""
    got = d(detect_result="failure", refusal="")
    assert got == (
        "failure",
        "change detection did not succeed (failure); fix the plan / detect job before merging",
        "held",
    )


#: 300 characters.
_LONG_REFUSAL = 'stack "a/b" ' + "x" * 288


def test_main_cuts_a_long_refusal_to_the_statuses_limit(tmp_path, monkeypatch, capsys):
    """`description` fits the statuses API; `reason`, the held comment's text, is whole.
    Mutations: misspell `SHIPMATE_DETECT_REFUSAL` in `main()` -- the generic text; drop `[:140]`
    in `main` -- the whole refusal as the description."""
    body = _main_body(
        tmp_path,
        monkeypatch,
        capsys,
        SHIPMATE_DETECT_RESULT="failure",
        SHIPMATE_DETECT_REFUSAL=_LONG_REFUSAL,
    )
    assert body == {
        "state": "failure",
        "description": 'detect failed: stack "a/b" ' + "x" * 113,
        "reason": 'detect failed: stack "a/b" ' + "x" * 288,
        "comment_mode": "held",
    }


def test_empty_matrix_greens_the_gate():
    # The docs-only or pin-bump pull request: detect succeeded, and the plan job was skipped
    # because nothing changed. Mapping `skipped` onto the old run_conclusion would write nothing
    # here and block every such pull request forever.
    state, _, mode = d(plan_result="skipped", planned_cells="0", cell_count=0, pending=False)
    assert state == "success"
    assert mode == "nothing-changed"


def test_skipped_plan_with_planned_cells_holds():
    state, _, mode = d(plan_result="skipped", planned_cells="3", cell_count=0)
    assert state == "failure"
    assert mode == "held"


def test_skipped_empty_matrix_with_parsed_cells_holds():
    # planned==0 must not be a shortcut past the evidence check: cells nobody planned describe a
    # run this job cannot account for.
    state, _, mode = d(plan_result="skipped", planned_cells="0", cell_count=2, pending=False)
    assert state == "failure"
    assert mode == "held"


def test_cancelled_plan_writes_nothing():
    """The one `hold`: no gate is written, so the comment is kept rather than replaced by a held
    body over a gate this run never wrote. Mutation: return `_HELD` for it -- red."""
    assert d(plan_result="cancelled") == (None, "", "hold")


def test_every_failure_state_is_held():
    """Mutations: return `_HOLD` for a detect failure, or for an evidence gap -- red."""
    assert [d(**kw)[2] for kw in ({"detect_result": "failure"}, {"plan_result": "failure"})] == [
        "held",
        "held",
    ]


def test_failed_plan_is_a_red_gate():
    state, _, mode = d(plan_result="failure")
    assert state == "failure"
    assert mode == "held"


@pytest.mark.parametrize("planned", ["", "unknown", "3.0", "None"])
def test_a_non_integer_planned_count_holds(planned):
    # The description is part of the property, not decoration. Without the explicit "not
    # reported" branch the gate still holds, `cell_count != None` being True, but it holds while
    # telling the reader the run "planned None cell(s)", which sends them to the wrong problem.
    state, desc, mode = d(planned_cells=planned)
    assert state == "failure"
    assert "planned cell count was not reported" in desc
    assert mode == "held"


def test_fewer_cells_than_planned_holds():
    """Mutations: restore the old "re-run this summary job" lead; drop either count."""
    assert d(planned_cells="3", cell_count=2) == (
        "failure",
        (
            "re-plan this pull request (comment shipmate plan or push): "
            "3 cell(s) planned but 2 cell summary/summaries read"
        ),
        "held",
    )


def test_more_cells_than_planned_holds():
    # A `<` comparison would let this through. Two directions, one `!=`.
    state, _, mode = d(planned_cells="3", cell_count=4)
    assert state == "failure"
    assert mode == "held"


@pytest.mark.parametrize(
    "kw",
    [
        {"detect_result": "failure"},
        {"plan_result": "failure"},
        {"planned_cells": "unknown"},
        {"plan_result": "skipped", "planned_cells": "3"},
        {"planned_cells": "3", "cell_count": 0},
        {"planned_cells": "256", "cell_count": 0},
        {"planned_cells": "256", "cell_count": 255},
        {"detect_result": "skipped", "is_draft": True},
        {
            "stacked_hold": (
                "stacked #123456 holds 256 cell(s); retarget it to main and merge it first"
            )
        },
    ],
)
def test_every_description_fits_the_statuses_api(kw):
    """A refusal is cut by `main`, not here (`test_main_cuts_a_long_refusal_to_the_statuses_limit`).
    Mutation: lengthen the evidence-gap remedy -- the 256-cell cases."""
    assert len(d(**kw)[1]) <= 140


_NOT_REPORTED = (
    "the planned cell count was not reported, so this run's evidence is unknown; "
    "re-plan before merging"
)


def _main_body(tmp_path, monkeypatch, capsys, **env):
    """The outputs `main()` writes, after asserting it printed nothing: the gate is written by
    `scripts/gate-status`, never from this script's stdout. Mutation: print the old JSON body."""
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("SHIPMATE_DETECT_RESULT", "success")
    monkeypatch.setenv("SHIPMATE_PLAN_RESULT", "success")
    monkeypatch.setenv("SHIPMATE_PLANNED_CELLS", "3")
    monkeypatch.setenv("SHIPMATE_CELL_COUNT", "3")
    monkeypatch.setenv("SHIPMATE_PENDING", "true")
    monkeypatch.setenv("SHIPMATE_BEHIND", json.dumps(NO_BEHIND))
    (tmp_path / "checks.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "bodies.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setenv("SHIPMATE_CHECK_RUNS", str(tmp_path / "checks.jsonl"))
    monkeypatch.setenv("SHIPMATE_BODIES", str(tmp_path / "bodies.jsonl"))
    monkeypatch.setenv("SHIPMATE_APP_ID", "4242")
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    gate_state.main()
    assert capsys.readouterr().out == ""
    return github_outputs(tmp_path / "out")


def test_main_holds_the_gate_on_an_empty_planned_count_env(tmp_path, monkeypatch, capsys):
    # An action input that was never wired, or a step that emitted no output, arrives as the
    # empty string, which is the real GHA shape. `_main_body` applies **env after its own
    # defaults, so this overrides the default "3".
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_PLANNED_CELLS="")
    assert body == {
        "state": "failure",
        "description": _NOT_REPORTED,
        "reason": _NOT_REPORTED,
        "comment_mode": "held",
    }


def test_main_holds_the_gate_when_the_planned_count_env_is_absent(tmp_path, monkeypatch, capsys):
    # Separate from test_main_holds_the_gate_on_an_empty_planned_count_env, and not redundant:
    # this is the only test that can see `main()`'s default, and a default of "0" there would
    # green a quiet gate for a run whose count nobody ever reported.
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_PLANNED_CELLS=None)
    assert body == {
        "state": "failure",
        "description": _NOT_REPORTED,
        "reason": _NOT_REPORTED,
        "comment_mode": "held",
    }


def _main_draft(tmp_path, monkeypatch, capsys, on_demand):
    return _main_body(
        tmp_path,
        monkeypatch,
        capsys,
        SHIPMATE_DETECT_RESULT="skipped",
        SHIPMATE_PLAN_RESULT="skipped",
        SHIPMATE_PLANNED_CELLS="",
        SHIPMATE_CELL_COUNT="0",
        SHIPMATE_PENDING="false",
        SHIPMATE_IS_DRAFT="true",
        SHIPMATE_ON_DEMAND=on_demand,
    )


def test_main_reads_the_draft_flag(tmp_path, monkeypatch, capsys):
    """Mutation: misspell `SHIPMATE_IS_DRAFT` in `main()` -> `state=failure`."""
    assert _main_draft(tmp_path, monkeypatch, capsys, "false") == {
        "state": "pending",
        "description": gate_state.DRAFT_DESCRIPTION,
        "reason": gate_state.DRAFT_DESCRIPTION,
        "comment_mode": "draft",
    }


def test_main_reads_the_on_demand_flag(tmp_path, monkeypatch, capsys):
    """Mutation: misspell `SHIPMATE_ON_DEMAND` in `main()` -> `state=pending`."""
    assert _main_draft(tmp_path, monkeypatch, capsys, "true") == {
        "state": "failure",
        "description": _DETECT_SKIPPED_DESCRIPTION,
        "reason": _DETECT_SKIPPED_DESCRIPTION,
        "comment_mode": "held",
    }


#: R4's no-names texts, hand-written.
_PLAN_FAILED = "plan incomplete (plan job: failure); fix the failing plan checks before merging"
_PLAN_CANCELLED = (
    "plan incomplete (plan job: cancelled); fix the failing plan checks before merging"
)
_A, _B, _C = ({"stack": f"stacks/{s}", "environment": "dev"} for s in ("a", "b", "c"))
#: The one-cell text around a stack path; 140 characters at `_FITS`.
_ONE = "plan / dev / {} has no plan summary; fix its plan or re-plan"
_FITS = "s" * (140 - len(_ONE.format("")))


@pytest.mark.parametrize(
    ("plan_result", "planned_cells", "cell_count", "missing", "description"),
    [
        ("success", "2", 2, (), None),
        ("success", "2", 2, [_A], None),
        ("failure", "2", 1, (), _PLAN_FAILED),
        ("failure", "2", 1, [_A], _ONE.format("stacks/a")),
        (
            "failure",
            "3",
            0,
            [_A, _B, _C],
            "plan / dev / stacks/a and 2 more have no plan summary; fix their plans or re-plan",
        ),
        (
            "failure",
            "2",
            1,
            [{"stack": _FITS, "environment": "dev"}],
            _ONE.format(_FITS),
        ),
        ("failure", "2", 1, [{"stack": _FITS + "s", "environment": "dev"}], _PLAN_FAILED),
        (
            "success",
            "2",
            1,
            (),
            (
                "re-plan this pull request (comment shipmate plan or push): "
                "2 cell(s) planned but 1 cell summary/summaries read"
            ),
        ),
        (
            "success",
            "1",
            2,
            (),
            (
                "re-plan this pull request (comment shipmate plan or push): "
                "1 cell(s) planned but 2 cell summary/summaries read"
            ),
        ),
        (
            "skipped",
            "2",
            0,
            (),
            "the plan job did not run for 2 planned cell(s); re-plan before merging",
        ),
        ("skipped", "0", 0, (), None),
        ("success", "", 0, (), _NOT_REPORTED),
        ("success", "x", 0, (), _NOT_REPORTED),
        ("cancelled", "1", 1, (), _PLAN_CANCELLED),
        ("cancelled", "1", 1, [_A], _PLAN_CANCELLED),
    ],
)
def test_evidence_gap_is_the_one_evidence_rule(
    plan_result, planned_cells, cell_count, missing, description
):
    """`missing` changes only a failed plan job's text: one name, or the first and a count, when
    the text fits the statuses API whole, else the no-names text.
    Mutations: drop the not-an-integer branch in `evidence_gap` -- the `""` and `"x"` rows
    (they still hold, as "None cell(s) planned"); `!=` -> `<` in `_evidence_gap` -- the
    `("success", "1", 2)` row; drop the name from the one-cell f-string -- the one-cell and
    140-character rows; name `missing[-1]` -- the three-name row; pass names for `cancelled` too
    -- the named `cancelled` row; hold on any non-empty `missing` (a gap of
    `_missing_description` when `_plan_gap` finds none) -- the named `success` row; drop the
    140-character bound -- the 141-character row."""
    assert (
        gate_state.evidence_gap(
            plan_result=plan_result,
            planned_cells=planned_cells,
            cell_count=cell_count,
            behind=0,
            missing=missing,
        )
        == description
    )


def test_pending_checks_call_without_names_gets_the_no_names_text():
    """`pending-checks` passes no `missing`. Mutation: default `missing` to a name -- red."""
    assert (
        gate_state.evidence_gap(plan_result="failure", planned_cells="2", cell_count=1, behind=0)
        == _PLAN_FAILED
    )


def test_main_reads_the_missing_cells_and_flattens_a_line_break(tmp_path, monkeypatch, capsys):
    """A stack path's CR and LF reach `GITHUB_OUTPUT` as spaces, on one line each.
    Mutations: misspell `SHIPMATE_MISSING` in `main` -- the no-names text; drop the CR/LF
    replacement -- `github_outputs` raises on the broken line."""
    missing = json.dumps([{"stack": "stacks/a\r\nb", "environment": "dev"}])
    body = _main_body(
        tmp_path,
        monkeypatch,
        capsys,
        SHIPMATE_PLAN_RESULT="failure",
        SHIPMATE_CELL_COUNT="2",
        SHIPMATE_MISSING=missing,
    )
    text = "plan / dev / stacks/a  b has no plan summary; fix its plan or re-plan"
    assert body == {
        "state": "failure",
        "description": text,
        "reason": text,
        "comment_mode": "held",
    }


_STACKED_HOLD = "stacked #5 holds 1 cell(s); retarget it to main and merge it first"


def test_a_pending_gate_with_a_stacked_holder_says_to_retarget_it():
    """Only the pending branch reads it. Mutation: return `stacked_hold` as the description of
    every state -- the failure and success cases name the holder, red; drop it from the pending
    branch -- red."""
    assert d(stacked_hold=_STACKED_HOLD) == ("pending", _STACKED_HOLD, "post")
    assert d(stacked_hold="") == (
        "pending",
        "one or more stacks are waiting to be applied; merge is blocked until applies complete",
        "post",
    )
    assert d(stacked_hold=_STACKED_HOLD, pending=False) == (
        "success",
        "no pending applies for this PR",
        "post",
    )
    assert d(stacked_hold=_STACKED_HOLD, plan_result="failure") == (
        "failure",
        "plan incomplete (plan job: failure); fix the failing plan checks before merging",
        "held",
    )


def test_main_reads_the_stacked_hold(tmp_path, monkeypatch, capsys):
    """Mutation: drop the `SHIPMATE_STACKED_HOLD` read from `main` -- the generic text, red."""
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_STACKED_HOLD=_STACKED_HOLD)
    assert body == {
        "state": "pending",
        "description": _STACKED_HOLD,
        "reason": _STACKED_HOLD,
        "comment_mode": "post",
    }


NO_BEHIND = {"base": "main", "tip": "", "since": "", "commits": {}, "cells": []}
_ROW = {
    "stack": "stacks/a",
    "environment": "dev",
    "cause": "changed",
    "shas": ["abc1234"],
    "more": 2,
}
ONE_BEHIND = {
    "base": "main",
    "tip": "c" * 40,
    "since": "d" * 40,
    "commits": {"abc1234": "edit a"},
    "cells": [_ROW],
}


def test_the_behind_reader_accepts_the_no_behind_value_and_one_behind_cell():
    """Mutation: require a non-empty `tip` -- the no-behind value is unread, red."""
    assert gate_state.behind_cells(json.dumps(NO_BEHIND)) == NO_BEHIND
    assert gate_state.behind_cells(json.dumps(ONE_BEHIND)) == ONE_BEHIND


@pytest.mark.parametrize(
    "report",
    [
        pytest.param({**ONE_BEHIND, "commits": {}}, id="sha-missing-from-commits"),
        pytest.param({**ONE_BEHIND, "commits": {"abc1234": "a", "def5678": "b"}}, id="extra-key"),
        pytest.param({**ONE_BEHIND, "tip": ""}, id="cells-without-a-tip"),
        pytest.param({**NO_BEHIND, "tip": "c" * 40}, id="a-tip-without-cells"),
        pytest.param({**NO_BEHIND, "base": ""}, id="no-base"),
        pytest.param({**ONE_BEHIND, "cells": [{**_ROW, "cause": "moved"}]}, id="cause"),
        pytest.param({**ONE_BEHIND, "cells": [{**_ROW, "more": True}]}, id="bool-more"),
        pytest.param(
            {**ONE_BEHIND, "cells": [{**_ROW, "cause": "conflict"}]}, id="conflict-with-shas"
        ),
        pytest.param({k: v for k, v in NO_BEHIND.items() if k != "cells"}, id="no-cells-key"),
        pytest.param([], id="not-an-object"),
    ],
)
def test_the_behind_reader_refuses_any_other_shape(report):
    """Mutations: skip the check that every listed sha is a key of `commits` --
    `sha-missing-from-commits` red; accept cells with an empty tip -- `cells-without-a-tip`
    red."""
    assert gate_state.behind_cells(json.dumps(report)) is None


@pytest.mark.parametrize("raw", ["", "{", "null"])
def test_an_unreadable_behind_value_is_an_evidence_gap(raw):
    """Mutation: drop the `behind is None` check from `evidence_gap` -- no gap, red."""
    got = gate_state.evidence_gap(
        plan_result="success",
        planned_cells="1",
        cell_count=1,
        behind=gate_state.behind_cells(raw) and 0,
    )
    assert got == gate_state.BEHIND_UNREAD


def test_a_docs_only_plan_is_not_an_evidence_gap_and_greens():
    """A plan with no changed cell and the no-behind value: no gap, no behind cell, no older
    check, so the gate greens with nothing to post. Mutation: require a non-empty `tip` in
    `behind_cells` -- the plan holds, red."""
    behind = len(gate_state.behind_cells(json.dumps(NO_BEHIND))["cells"])
    got = d(planned_cells="0", cell_count=0, pending=False, plan_result="skipped", behind=behind)
    assert got == ("success", "no pending applies for this PR", "nothing-changed")


def test_every_cell_behind_holds_the_gate_pending_and_posts():
    """An all-behind run has no cell summary and no changed cell, but a comment to post.
    Mutations: drop the `behind` branch -- `success`, red; return mode `nothing-changed` -- red."""
    got = d(
        planned_cells="0", cell_count=0, pending=False, plan_result="skipped", behind=2, base="main"
    )
    assert got == ("pending", "2 cell(s) behind main; update the branch from main", "post")


def test_the_behind_text_wins_over_a_stacked_holder():
    """The comment still shows the stacked lines; the gate names the behind cells first.
    Mutation: test `pending` before `behind` -- the stacked text, red."""
    got = d(behind=1, base="release/2", stacked_hold="stacked #3 holds 1 cell(s)")
    assert got == (
        "pending",
        "1 cell(s) behind release/2; update the branch from release/2",
        "post",
    )


_OLDER = "an earlier plan's 2 apply check(s) are still open on this head; push a commit to re-plan"


@pytest.mark.parametrize(
    ("kw", "mode"),
    [
        ({"planned_cells": "0", "cell_count": 0, "plan_result": "skipped"}, "nothing-changed"),
        ({}, "post"),
    ],
    ids=["zero-cells", "no-change-cells"],
)
def test_an_older_runs_open_apply_check_keeps_a_success_pending(kw, mode):
    """A run that planned nothing, or only no-change cells, would green over an earlier plan's
    still-queued checks. Mutation: apply the open-check rule to zero cells only --
    `no-change-cells` greens, red."""
    assert d(pending=False, open_older=2, **kw) == ("pending", _OLDER, mode)


def test_an_unread_check_listing_keeps_a_success_pending():
    """Mutation: read `open_older` None as no open check -- `success`, red."""
    got = d(pending=False, open_older=None, planned_cells="0", cell_count=0, plan_result="skipped")
    assert got == ("pending", gate_state.OLDER_UNREAD, "nothing-changed")


def test_zero_cells_and_no_open_check_greens():
    got = d(pending=False, planned_cells="0", cell_count=0, plan_result="skipped")
    assert got == ("success", "no pending applies for this PR", "nothing-changed")


def _check(cid, name, status="queued", conclusion=None, app=4242, plan_run="7"):
    return json.dumps(
        {
            "name": name,
            "id": cid,
            "status": status,
            "conclusion": conclusion,
            "app": {"id": app},
            "external_id": json.dumps({"plan_run": plan_run}),
        }
    )


def _open_older(tmp_path, checks, bodies=()):
    (tmp_path / "c").write_text("".join(f"{c}\n" for c in checks), encoding="utf-8")
    (tmp_path / "b").write_text(
        "".join(json.dumps({"name": n}) + "\n" for n in bodies), encoding="utf-8"
    )
    return gate_state.open_older(tmp_path / "c", tmp_path / "b", "4242")


def test_open_older_counts_the_newest_app_check_per_name_not_done(tmp_path):
    """`a` is queued, `b` completed `neutral` (done), `c` queued under a newer `success`, `d`
    queued but this run publishes it, `e` another App's. Only `a` is open.

    Mutations: count a completed check as open -- 3, red; drop the `bodies.jsonl` exclusion --
    2, red; drop the App filter -- 2, red."""
    checks = [
        _check(1, "apply / a / dev"),
        _check(2, "apply / b / dev", "completed", "neutral"),
        _check(3, "apply / c / dev"),
        _check(4, "apply / c / dev", "completed", "success"),
        _check(5, "apply / d / dev"),
        _check(6, "apply / e / dev", app=15368),
    ]
    assert _open_older(tmp_path, checks, bodies=["apply / d / dev"]) == 1


def test_an_earlier_attempt_of_this_run_counts_as_open(tmp_path, monkeypatch):
    """Every attempt of a run shares its run id: a "Re-run all jobs" attempt that plans fewer
    cells leaves attempt 1's queued check, which still blocks the merge. Mutation: leave out
    names whose record names `GITHUB_RUN_ID` -- 0, red."""
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    assert _open_older(tmp_path, [_check(1, "apply / a / dev", plan_run="7")]) == 1


@pytest.mark.parametrize("missing", ["c", "b"])
def test_open_older_is_unknown_without_either_file(tmp_path, missing):
    """Mutation: read a missing listing as no check (`return 0`) -- red."""
    _open_older(tmp_path, [])
    (tmp_path / missing).unlink()
    assert gate_state.open_older(tmp_path / "c", tmp_path / "b", "4242") is None


def test_main_holds_a_docs_only_plan_pending_without_the_check_listing(
    tmp_path, monkeypatch, capsys
):
    """Mutation: read the missing file as no check -- `success`, red."""
    body = _main_body(
        tmp_path,
        monkeypatch,
        capsys,
        SHIPMATE_PLANNED_CELLS="0",
        SHIPMATE_CELL_COUNT="0",
        SHIPMATE_PENDING="false",
        SHIPMATE_PLAN_RESULT="skipped",
        SHIPMATE_CHECK_RUNS=str(tmp_path / "absent.jsonl"),
    )
    assert body["state"] == "pending"
    assert body["reason"] == gate_state.OLDER_UNREAD


def test_main_reads_the_behind_report_and_names_its_base(tmp_path, monkeypatch, capsys):
    """Mutation: pass `base=""` from `main` -- the text names no branch, red."""
    report = {**ONE_BEHIND, "base": "release/2"}
    body = _main_body(
        tmp_path, monkeypatch, capsys, SHIPMATE_BEHIND=json.dumps(report), SHIPMATE_PENDING="false"
    )
    assert (body["state"], body["reason"]) == (
        "pending",
        "1 cell(s) behind release/2; update the branch from release/2",
    )


#: The gate step's env with every `${{ }}` value a docs-only run hands it.
_DOCS_ONLY = {
    "${{ inputs.detect-result }}": "success",
    "${{ inputs.detect-refusal }}": "",
    "${{ inputs.plan-result }}": "skipped",
    "${{ inputs.planned-cells }}": "0",
    "${{ steps.build.outputs.count }}": "0",
    "${{ steps.build.outputs.pending }}": "false",
    "${{ steps.build.outputs.missing }}": "[]",
    "${{ inputs.is-draft }}": "false",
    "${{ inputs.on-demand }}": "false",
    "${{ steps.build.outputs.stacked_hold }}": "",
    "${{ inputs.behind }}": json.dumps(NO_BEHIND),
    "${{ inputs.app-id }}": "4242",
}


def test_a_docs_only_plan_greens_through_the_gate_steps_own_env(tmp_path, monkeypatch, capsys):
    """The gate step's YAML env, its expressions answered for a docs-only run beside the hold
    step's two files (no apply check, no body), reaches `success`. The App id is what the open
    check count reads; without it every docs-only plan would stay pending. Mutation: drop
    `SHIPMATE_APP_ID` from the step -- pending, red."""
    from _loader import step_by

    env = step_by("summary", id="gate")["env"]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apply-check-runs.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "bodies.jsonl").write_text("", encoding="utf-8")
    for k in list(os.environ):
        if k.startswith("SHIPMATE_"):
            monkeypatch.delenv(k)
    for k, v in env.items():
        monkeypatch.setenv(k, _DOCS_ONLY.get(v, v))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    gate_state.main()
    assert github_outputs(tmp_path / "out")["state"] == "success"
