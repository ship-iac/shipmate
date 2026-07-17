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

APPROVED = [{"user": {"login": "rev"}, "state": "APPROVED"}]
PR_OK = {"mergeable": True, "mergeable_state": "clean", "head": {"sha": "abc123"}}
RUN_OK = {"id": 555, "head_sha": "abc123"}


def _decide(**kw):
    base = dict(
        is_member=True, approvers_team="deployers", reviews=APPROVED, pr=PR_OK, plan_run=RUN_OK
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


def test_unapproved_rejected():
    ok, reason = _decide(reviews=[])
    assert not ok and "not approved" in reason


def test_changes_requested_outranks_approval():
    reviews = [
        {"user": {"login": "a"}, "state": "APPROVED"},
        {"user": {"login": "b"}, "state": "CHANGES_REQUESTED"},
    ]
    ok, reason = _decide(reviews=reviews)
    assert not ok and "not approved" in reason


def test_latest_review_per_user_wins():
    # same user requested changes then approved -> approved
    reviews = [
        {"user": {"login": "a"}, "state": "CHANGES_REQUESTED"},
        {"user": {"login": "a"}, "state": "APPROVED"},
    ]
    ok, _ = _decide(reviews=reviews)
    assert ok


def test_no_reviewed_plan_rejected_as_stale():
    ok, reason = _decide(plan_run={})
    assert not ok and "re-plan" in reason
    assert "no reviewed plan" in reason or "no successful preview" in reason


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


def test_approved_ignores_null_user_review():
    # a review from a deleted account arrives with "user": null; it must be
    # skipped rather than crashing, and a valid approval alongside it still
    # authorizes.
    reviews = [
        {"user": None, "state": "APPROVED"},
        {"user": {"login": "rev"}, "state": "APPROVED"},
    ]
    ok, reason = _decide(reviews=reviews)
    assert ok and reason == ""
