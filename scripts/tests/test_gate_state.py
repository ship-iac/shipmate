"""Unit tests for scripts/gate-state."""

import json

import pytest
from _loader import load_script

gate_state = load_script("gate-state")


def d(**kw):
    base = {
        "run_conclusion": "success",
        "artifact_count": 3,
        "cell_count": 3,
        "matrix_count": 3,
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


def test_failed_plan_run_fails_the_gate_and_holds_the_comment():
    state, desc, mode = d(run_conclusion="failure")
    assert state == "failure"
    assert "plan incomplete" in desc
    # The previous push's reviewed plan must not be overwritten by a run that
    # read no plan.
    assert mode == "hold"


@pytest.mark.parametrize("conclusion", ["skipped", "cancelled"])
def test_skipped_or_cancelled_run_writes_nothing(conclusion):
    # Draft pull requests skip every job, and cancel-in-progress cancels the
    # loser of a rapid re-push. A red gate for either would be a regression.
    state, _, mode = d(run_conclusion=conclusion)
    assert state is None
    assert mode == "hold"


def test_artifacts_exist_but_none_parsed_is_a_download_failure():
    state, desc, mode = d(artifact_count=3, cell_count=0)
    assert state == "failure"
    assert "no cell summaries" in desc
    assert mode == "hold"


def test_partial_cell_loss_fails_the_gate_and_holds_the_comment():
    # 2 of 3 cell summaries missing must not be mistaken for "nothing changed"
    # or, worse, green the gate while a stack still awaits apply.
    state, _, mode = d(artifact_count=3, cell_count=1)
    assert state == "failure"
    assert mode == "hold"


@pytest.mark.parametrize("count", ["unknown", "", " ", "1.0", "0x1", "many", None])
def test_an_uncountable_artifact_list_holds_the_gate(count):
    # The workflow reports `unknown` when the plan run's artifact listing could
    # not be read. Any *number* there is comparable against the parsed cell
    # count and the one that matches (1 reported, 1 cell downloaded) would green
    # the gate over stacks whose summaries were never seen -- they would then
    # land on main with no apply check and never be applied at all.
    state, desc, mode = d(artifact_count=count, cell_count=1, pending=False)
    assert state == "failure"
    assert "artifact list could not be read" in desc
    assert mode == "hold"


def test_the_uncountable_check_precedes_the_shortfall_comparison():
    # A cell count that would satisfy `cell_count < artifact_count` on any
    # numeric reading must not be able to defeat the hold.
    for cells in (0, 1, 999):
        assert d(artifact_count="unknown", cell_count=cells, pending=False)[0] == "failure"


def test_a_countable_artifact_list_is_accepted_as_a_string():
    # The value arrives from a step output, so it is a string on the real path.
    state, _, mode = d(artifact_count="3", cell_count=3, pending=False)
    assert state == "success"
    assert mode == "post"


def test_an_empty_plan_matrix_means_nothing_changed():
    # The marker says the matrix really was empty -- a docs-only or pin-bump
    # pull request. This is the ONLY zero that greens the gate quietly.
    state, desc, mode = d(artifact_count=0, cell_count=0, matrix_count=0, pending=False)
    assert state == "success"
    assert "no pending applies" in desc
    assert mode == "nothing-changed"


def test_a_zero_listing_without_a_marker_holds_the_gate():
    # The defect this guard exists for: the artifact listing is
    # read-after-write eventually consistent and is read seconds after the plan
    # run ends, so an empty array can describe a run that planned five stacks.
    # Greening here creates no apply checks, and deploy-detect's work queue IS
    # those checks -- the reviewed change would merge and never be applied.
    state, desc, mode = d(artifact_count=0, cell_count=0, matrix_count="unknown", pending=False)
    assert state == "failure"
    assert "no plan-matrix marker" in desc
    assert mode == "hold"


def test_a_short_listing_holds_the_gate():
    # 3 of 5 cell summaries listed: all three download and parse, so the
    # cell/artifact shortfall check cannot see it. Only the planned count can.
    state, desc, mode = d(artifact_count=3, cell_count=3, matrix_count=5, pending=False)
    assert state == "failure"
    assert "planned 5" in desc
    assert mode == "hold"


def test_more_summaries_than_planned_cells_holds_the_gate():
    # A state nobody can explain gets the fail-closed default too.
    state, _, mode = d(artifact_count=5, cell_count=5, matrix_count=3, pending=False)
    assert state == "failure"
    assert mode == "hold"


@pytest.mark.parametrize("marker", ["unknown", "", " ", "3.0", "0x1", "many", None])
def test_an_unparseable_marker_holds_the_gate(marker):
    # `unknown` is what the workflow reports when no marker was found, or when
    # two markers disagreed. Anything that is not a base-10 integer is that
    # same state, and none of them may be comparable against a cell count.
    state, desc, mode = d(artifact_count=0, cell_count=0, matrix_count=marker, pending=False)
    assert state == "failure"
    assert "no plan-matrix marker" in desc
    assert mode == "hold"


def test_the_marker_check_precedes_every_comparison_a_count_could_satisfy():
    # The description matters as much as the state: with the marker check moved
    # below the mismatch comparison, `counted != None` is True in Python, so the
    # gate still holds -- but it holds telling the reader the run "planned None
    # cell(s)", which sends them looking for the wrong problem.
    for artifacts, cells in ((0, 0), (1, 1), (999, 999)):
        state, desc, _ = d(artifact_count=artifacts, cell_count=cells, matrix_count="unknown")
        assert state == "failure"
        assert "no plan-matrix marker" in desc


def test_an_empty_matrix_with_summaries_present_holds_the_gate():
    # A marker claiming zero while summaries exist is a contradiction, not a
    # quiet pull request.
    state, _, mode = d(artifact_count=1, cell_count=1, matrix_count=0, pending=False)
    assert state == "failure"
    assert mode == "hold"


def test_an_unreadable_listing_still_holds_before_the_marker_is_consulted():
    # A readable marker must not rescue an unreadable listing.
    state, desc, _ = d(artifact_count="unknown", cell_count=0, matrix_count=0, pending=False)
    assert state == "failure"
    assert "artifact list could not be read" in desc


@pytest.mark.parametrize(
    "kw",
    [
        {"run_conclusion": "failure"},
        {"artifact_count": "unknown"},
        {"matrix_count": "unknown"},
        {"artifact_count": 3, "cell_count": 3, "matrix_count": 5},
        {"artifact_count": 3, "cell_count": 0, "matrix_count": 3},
    ],
)
def test_every_description_fits_the_statuses_api(kw):
    assert len(d(**kw)[1]) <= 140


def test_description_is_capped_for_the_statuses_api():
    _, desc, _ = d(run_conclusion="failure")
    assert len(desc) <= 140


def _main_body(tmp_path, monkeypatch, capsys, **env):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("SHIPMATE_RUN_CONCLUSION", "success")
    monkeypatch.setenv("SHIPMATE_ARTIFACT_COUNT", "3")
    monkeypatch.setenv("SHIPMATE_CELL_COUNT", "3")
    monkeypatch.setenv("SHIPMATE_MATRIX_COUNT", "3")
    monkeypatch.setenv("SHIPMATE_PENDING", "true")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://example.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    monkeypatch.delenv("SHIPMATE_PLAN_RUN_URL", raising=False)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    gate_state.main()
    return json.loads(capsys.readouterr().out)


def test_gate_links_to_the_plan_run_when_one_is_supplied(tmp_path, monkeypatch, capsys):
    # GITHUB_RUN_ID is the trusted summary run -- a short job with no plan
    # output and no artifacts -- so the gate must link to the plan run instead.
    body = _main_body(
        tmp_path,
        monkeypatch,
        capsys,
        SHIPMATE_PLAN_RUN_URL="https://example.invalid/acme/demo/actions/runs/42",
    )
    assert body["target_url"] == "https://example.invalid/acme/demo/actions/runs/42"


def test_main_holds_the_gate_on_an_unknown_artifact_count(tmp_path, monkeypatch, capsys):
    # The env value is parsed inside `decide`, not at the call site: an
    # `int(...)` on the way in raised ValueError and killed the action, writing
    # no gate at all -- the opposite of a hold.
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_ARTIFACT_COUNT="unknown")
    assert body["state"] == "failure"
    assert "artifact list could not be read" in body["description"]


def test_main_holds_the_gate_on_an_empty_marker_env(tmp_path, monkeypatch, capsys):
    # An action input that was never wired (or a step that emitted no output)
    # arrives as the empty string -- the real GHA shape. `_main_body` applies
    # **env after its own defaults, so this overrides the default "3".
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_MATRIX_COUNT="")
    assert body["state"] == "failure"
    assert "no plan-matrix marker" in body["description"]


def test_main_holds_the_gate_when_the_marker_env_is_absent(tmp_path, monkeypatch, capsys):
    # Separate from the empty-string case above, and not redundant: this one is
    # the only test that can see `main()`'s default, and a default of "0" there
    # would green a quiet gate for a run whose count nobody ever reported.
    body = _main_body(tmp_path, monkeypatch, capsys, SHIPMATE_MATRIX_COUNT=None)
    assert body["state"] == "failure"
    assert "no plan-matrix marker" in body["description"]


def test_gate_falls_back_to_this_run_when_no_plan_run_url_is_supplied(
    tmp_path, monkeypatch, capsys
):
    body = _main_body(tmp_path, monkeypatch, capsys)
    assert body["target_url"] == "https://example.invalid/acme/demo/actions/runs/999"
