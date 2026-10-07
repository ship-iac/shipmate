import json

import pytest
from _loader import load_script

gate_state = load_script("gate-state")


def d(**kw):
    base = {
        "detect_result": "success",
        "plan_result": "success",
        "planned_cells": "3",
        "cell_count": 3,
        "pending": True,
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
    "change detection did not succeed (skipped); fix the shipmate / detect job before merging"
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
    assert got == ("failure", _DETECT_SKIPPED_DESCRIPTION, "hold")


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
    assert got == ("failure", _DETECT_SKIPPED_DESCRIPTION, "hold")


def test_detect_failure_is_a_red_gate_not_a_silent_skip():
    # detect is the change detection. Without it there is no claim to make, and writing nothing
    # would leave the pull request with no gate to explain it.
    state, desc, mode = d(detect_result="failure")
    assert state == "failure"
    assert mode == "hold"
    assert "detect" in desc


def test_a_detect_refusal_names_itself_in_the_gate():
    """Mutation: return the generic text when a refusal exists -- the refusal is not named."""
    got = d(detect_result="failure", refusal="stack 'x' carries 2 workload tags")
    assert got == ("failure", "detect failed: stack 'x' carries 2 workload tags", "hold")


def test_a_detect_failure_without_a_refusal_keeps_the_generic_text():
    """A fmt or codegen failure writes no refusal.

    Mutation: write `detect failed: ` for an empty refusal -- the description changes."""
    got = d(detect_result="failure", refusal="")
    assert got == (
        "failure",
        "change detection did not succeed (failure); fix the shipmate / detect job before merging",
        "hold",
    )


#: 300 characters, with a `"` the JSON body must escape.
_LONG_REFUSAL = 'stack "a/b" ' + "x" * 288


def test_main_cuts_a_long_refusal_to_the_statuses_limit(tmp_path, monkeypatch, capsys):
    """Mutation: misspell `SHIPMATE_DETECT_REFUSAL` in `main()` -- the generic text."""
    body = _main_body(
        tmp_path,
        monkeypatch,
        capsys,
        SHIPMATE_DETECT_RESULT="failure",
        SHIPMATE_DETECT_REFUSAL=_LONG_REFUSAL,
    )
    assert (body["state"], body["description"]) == (
        "failure",
        'detect failed: stack "a/b" ' + "x" * 113,
    )


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
    assert mode == "hold"


def test_skipped_empty_matrix_with_parsed_cells_holds():
    # planned==0 must not be a shortcut past the evidence check: cells nobody planned describe a
    # run this job cannot account for.
    state, _, mode = d(plan_result="skipped", planned_cells="0", cell_count=2, pending=False)
    assert state == "failure"
    assert mode == "hold"


def test_cancelled_plan_writes_nothing():
    state, _, mode = d(plan_result="cancelled")
    assert state is None
    assert mode == "hold"


def test_failed_plan_is_a_red_gate():
    state, _, mode = d(plan_result="failure")
    assert state == "failure"
    assert mode == "hold"


@pytest.mark.parametrize("planned", ["", "unknown", "3.0", "None"])
def test_a_non_integer_planned_count_holds(planned):
    # The description is part of the property, not decoration. Without the explicit "not
    # reported" branch the gate still holds, `cell_count != None` being True, but it holds while
    # telling the reader the run "planned None cell(s)", which sends them to the wrong problem.
    state, desc, mode = d(planned_cells=planned)
    assert state == "failure"
    assert "planned cell count was not reported" in desc
    assert mode == "hold"


def test_fewer_cells_than_planned_holds():
    state, desc, mode = d(planned_cells="3", cell_count=2)
    assert state == "failure"
    assert mode == "hold"
    # The recovery has to survive the 140-char truncation, and it must not be "re-plan":
    # plan-cell's uploads are not `overwrite:`, so re-running a plan job that already published
    # its artifacts 409s.
    assert "re-run this summary job" in desc[:140]
    assert "re-plan" not in desc


def test_more_cells_than_planned_holds():
    # A `<` comparison would let this through. Two directions, one `!=`.
    state, _, mode = d(planned_cells="3", cell_count=4)
    assert state == "failure"
    assert mode == "hold"


@pytest.mark.parametrize(
    "kw",
    [
        {"detect_result": "failure"},
        {"plan_result": "failure"},
        {"planned_cells": "unknown"},
        {"plan_result": "skipped", "planned_cells": "3"},
        {"planned_cells": "3", "cell_count": 0},
        {"detect_result": "skipped", "is_draft": True},
        {"detect_result": "failure", "refusal": _LONG_REFUSAL},
    ],
)
def test_every_description_fits_the_statuses_api(kw):
    """Mutation: drop the `[:140]` in `_detect_gap` -- the long-refusal case."""
    assert len(d(**kw)[1]) <= 140


def _main_body(tmp_path, monkeypatch, capsys, **env):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("SHIPMATE_DETECT_RESULT", "success")
    monkeypatch.setenv("SHIPMATE_PLAN_RESULT", "success")
    monkeypatch.setenv("SHIPMATE_PLANNED_CELLS", "3")
    monkeypatch.setenv("SHIPMATE_CELL_COUNT", "3")
    monkeypatch.setenv("SHIPMATE_PENDING", "true")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    gate_state.main()
    return json.loads(capsys.readouterr().out)


def test_main_holds_the_gate_on_an_empty_planned_count_env(tmp_path, monkeypatch, capsys):
    # An action input that was never wired, or a step that emitted no output, arrives as the
    # empty string, which is the real GHA shape. `_main_body` applies **env after its own
    # defaults, so this overrides the default "3".
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_PLANNED_CELLS="")
    assert body["state"] == "failure"
    assert "planned cell count was not reported" in body["description"]


def test_main_holds_the_gate_when_the_planned_count_env_is_absent(tmp_path, monkeypatch, capsys):
    # Separate from test_main_holds_the_gate_on_an_empty_planned_count_env, and not redundant:
    # this is the only test that can see `main()`'s default, and a default of "0" there would
    # green a quiet gate for a run whose count nobody ever reported.
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_PLANNED_CELLS=None)
    assert body["state"] == "failure"
    assert "planned cell count was not reported" in body["description"]


def test_gate_links_to_this_run(tmp_path, monkeypatch, capsys):
    # The summary job runs inside the plan run, so this run's URL holds the plan logs and the
    # plan artifacts the gate points at.
    body = _main_body(tmp_path, monkeypatch, capsys)
    assert body["target_url"] == "https://example.invalid/acme/demo/actions/runs/999"


def _main_draft(tmp_path, monkeypatch, capsys, on_demand):
    body = _main_body(
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
    return body, (tmp_path / "out").read_text(encoding="utf-8")


def test_main_reads_the_draft_flag(tmp_path, monkeypatch, capsys):
    """Mutation: misspell `SHIPMATE_IS_DRAFT` in `main()` -> `state=failure`."""
    body, out = _main_draft(tmp_path, monkeypatch, capsys, "false")
    assert out == "state=pending\ncomment_mode=draft\n"
    assert body["description"] == gate_state.DRAFT_DESCRIPTION


def test_main_reads_the_on_demand_flag(tmp_path, monkeypatch, capsys):
    """Mutation: misspell `SHIPMATE_ON_DEMAND` in `main()` -> `state=pending`."""
    body, out = _main_draft(tmp_path, monkeypatch, capsys, "true")
    assert out == "state=failure\ncomment_mode=hold\n"
    assert body["description"] == _DETECT_SKIPPED_DESCRIPTION
