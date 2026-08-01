"""Unit tests for scripts/gate-state."""

import pytest
from _loader import load_script

gate_state = load_script("gate-state")


def d(**kw):
    base = {
        "run_conclusion": "success",
        "artifact_count": 3,
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


def test_no_artifacts_at_all_means_nothing_changed():
    # plan-cell's upload has no `if: always()`, so a dying cell fails the run.
    # A successful run with zero cell-summary artifacts therefore means the
    # plan matrix was empty, not that evidence was lost.
    state, desc, mode = d(artifact_count=0, cell_count=0, pending=False)
    assert state == "success"
    assert "no pending applies" in desc
    assert mode == "nothing-changed"


def test_description_is_capped_for_the_statuses_api():
    _, desc, _ = d(run_conclusion="failure")
    assert len(desc) <= 140
