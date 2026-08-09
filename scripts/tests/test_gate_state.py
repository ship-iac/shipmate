"""Unit tests for scripts/gate-state."""

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


def test_detect_failure_is_a_red_gate_not_a_silent_skip():
    # detect IS the change detection; without it there is no claim to make, and
    # writing nothing would leave the pull request with no gate to explain it.
    state, desc, mode = d(detect_result="failure")
    assert state == "failure"
    assert mode == "hold"
    assert "detect" in desc


def test_empty_matrix_greens_the_gate():
    # The docs-only / pin-bump pull request: detect succeeded, the plan job was
    # skipped because nothing changed. Mapping `skipped` onto the old
    # run_conclusion would write NOTHING here and block every such PR forever.
    state, _, mode = d(plan_result="skipped", planned_cells="0", cell_count=0, pending=False)
    assert state == "success"
    assert mode == "nothing-changed"


def test_skipped_plan_with_planned_cells_holds():
    state, _, mode = d(plan_result="skipped", planned_cells="3", cell_count=0)
    assert state == "failure"
    assert mode == "hold"


def test_skipped_empty_matrix_with_parsed_cells_holds():
    # planned==0 must not be a shortcut past the evidence check: cells nobody
    # planned describe a run this job cannot account for.
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
    # The description is part of the property, not decoration: without the
    # explicit "not reported" branch the gate still holds -- `cell_count !=
    # None` is True -- but it holds telling the reader the run "planned None
    # cell(s)", which sends them looking for the wrong problem.
    state, desc, mode = d(planned_cells=planned)
    assert state == "failure"
    assert "planned cell count was not reported" in desc
    assert mode == "hold"


def test_fewer_cells_than_planned_holds():
    state, desc, mode = d(planned_cells="3", cell_count=2)
    assert state == "failure"
    assert mode == "hold"
    # The recovery has to survive the 140-char truncation, and it must not be
    # "re-plan": plan-cell's uploads are not `overwrite:`, so re-running a plan
    # job that already published its artifacts 409s.
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
    ],
)
def test_every_description_fits_the_statuses_api(kw):
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
    # An action input that was never wired (or a step that emitted no output)
    # arrives as the empty string -- the real GHA shape. `_main_body` applies
    # **env after its own defaults, so this overrides the default "3".
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_PLANNED_CELLS="")
    assert body["state"] == "failure"
    assert "planned cell count was not reported" in body["description"]


def test_main_holds_the_gate_when_the_planned_count_env_is_absent(tmp_path, monkeypatch, capsys):
    # Separate from the empty-string case above, and not redundant: this one is
    # the only test that can see `main()`'s default, and a default of "0" there
    # would green a quiet gate for a run whose count nobody ever reported.
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_PLANNED_CELLS=None)
    assert body["state"] == "failure"
    assert "planned cell count was not reported" in body["description"]


def test_gate_links_to_this_run(tmp_path, monkeypatch, capsys):
    # The summary job now runs inside the plan run, so this run's URL holds the
    # plan logs and the plan artifacts the gate points at.
    body = _main_body(tmp_path, monkeypatch, capsys)
    assert body["target_url"] == "https://example.invalid/acme/demo/actions/runs/999"
