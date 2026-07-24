import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parents[1]


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


az = _load("authorize")

PR_OK = {"mergeable": True, "mergeable_state": "clean", "head": {"sha": "abc123"}}
RUN_OK = {"id": 555, "head_sha": "abc123"}


def _decide(**kw):
    base = dict(
        is_member=True,
        approvers_team="deployers",
        review_decision="",
        pr=PR_OK,
        plan_run=RUN_OK,
    )
    base.update(kw)
    return az.decide(**base)


def test_all_conditions_met_authorizes():
    ok, reason = _decide()
    assert ok and reason == ""


def test_non_member_rejected_first():
    ok, reason = _decide(is_member=False)
    assert not ok and "team `deployers`" in reason and "not a member" in reason


def test_unmergeable_rejected():
    ok, reason = _decide(
        pr={"mergeable": False, "mergeable_state": "dirty", "head": {"sha": "abc123"}}
    )
    assert not ok and "not mergeable" in reason and "dirty" in reason


def test_review_decision_empty_authorizes():
    ok, reason = _decide(review_decision="")
    assert ok and reason == ""


def test_review_decision_approved_authorizes():
    ok, reason = _decide(review_decision="APPROVED")
    assert ok and reason == ""


def test_review_decision_literal_null_string_authorizes():
    # GraphQL null serialized as the literal string "null" — same as absent.
    ok, reason = _decide(review_decision="null")
    assert ok and reason == ""


def test_review_decision_review_required_rejected():
    ok, reason = _decide(review_decision="REVIEW_REQUIRED")
    assert not ok
    assert "review is required" in reason
    assert "shipmate apply" in reason


def test_review_decision_changes_requested_rejected():
    ok, reason = _decide(review_decision="CHANGES_REQUESTED")
    assert not ok
    assert "changes were requested" in reason


def test_review_reject_reasons_are_distinct():
    _, review_required_reason = _decide(review_decision="REVIEW_REQUIRED")
    _, changes_requested_reason = _decide(review_decision="CHANGES_REQUESTED")
    assert review_required_reason != changes_requested_reason


def test_no_reviewed_plan_rejected_as_stale():
    ok, reason = _decide(plan_run={})
    assert not ok and "re-plan" in reason
    assert "no reviewed plan" in reason or "no successful plan" in reason


def test_stale_head_sha_rejected():
    # a reviewed plan exists but for an older head than the PR's current head
    ok, reason = _decide(
        plan_run={"id": 9, "head_sha": "OLD"},
        pr={"mergeable": True, "mergeable_state": "clean", "head": {"sha": "NEW"}},
    )
    assert not ok and "stale" in reason and "re-plan" in reason


def test_mergeable_null_reports_still_computing_not_conflict():
    # null/unknown = GitHub hasn't finished computing → distinct, non-blaming
    # message (must NOT say conflicts/not-mergeable).
    ok, reason = _decide(
        pr={"mergeable": None, "mergeable_state": "unknown", "head": {"sha": "abc123"}}
    )
    assert not ok and "computing" in reason and "conflict" not in reason
