import json

from _loader import SCRIPTS as _D
from _loader import load_script

az = load_script("authorize")

PR_OK = {"mergeable": True, "mergeable_state": "clean", "head": {"sha": "abc123"}}
RUN_OK = {"id": 555, "head_sha": "abc123"}


def _decide(**kw):
    base = dict(
        is_member=True,
        approvers_team="deployers",
        review_decision="NONE",
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


def test_review_decision_none_authorizes():
    # NONE is comment-ops' sentinel for a null reviewDecision — the ruleset
    # requires no review.
    ok, reason = _decide(review_decision="NONE")
    assert ok and reason == ""


def test_review_decision_approved_authorizes():
    ok, reason = _decide(review_decision="APPROVED")
    assert ok and reason == ""


def test_review_decision_empty_fails_closed():
    # An empty value means the review signal never arrived (missing env var,
    # wiring drift) — it must NOT authorize, unlike the explicit NONE sentinel.
    ok, reason = _decide(review_decision="")
    assert not ok and "could not determine" in reason


def test_review_decision_unknown_value_fails_closed():
    # Unrecognized values (a future GitHub enum, the literal text "null")
    # fail closed rather than silently authorizing.
    ok, reason = _decide(review_decision="null")
    assert not ok and "could not determine" in reason and "'null'" in reason


def test_review_decision_review_required_rejected():
    ok, reason = _decide(review_decision="REVIEW_REQUIRED")
    assert not ok
    assert "review is required" in reason
    assert "shipmate apply" in reason


def test_review_decision_changes_requested_rejected():
    ok, reason = _decide(review_decision="CHANGES_REQUESTED")
    assert not ok
    assert "changes were requested" in reason
    # The unblock path must not dead-end a sole maintainer (who cannot
    # self-approve): dismissing the review must be named as an option.
    assert "dismiss" in reason


def test_review_reject_reasons_are_distinct():
    reasons = {
        _decide(review_decision=value)[1] for value in ("REVIEW_REQUIRED", "CHANGES_REQUESTED", "")
    }
    assert len(reasons) == 3


def test_main_reads_review_decision_env(tmp_path, monkeypatch):
    # Pins main()'s env-var wiring: REVIEW_DECISION is the name the action
    # sends; a rename on either side must fail this test, not fail open.
    pr_json = tmp_path / "pr.json"
    pr_json.write_text(json.dumps(PR_OK), encoding="utf-8")
    run_json = tmp_path / "plan_run.json"
    run_json.write_text(json.dumps(RUN_OK), encoding="utf-8")
    out = tmp_path / "out.txt"
    out.touch()
    for key, value in {
        "IS_MEMBER": "true",
        "APPROVERS_TEAM": "deployers",
        "PR_JSON": str(pr_json),
        "PLAN_RUN_JSON": str(run_json),
        "GITHUB_OUTPUT": str(out),
        "REVIEW_DECISION": "CHANGES_REQUESTED",
    }.items():
        monkeypatch.setenv(key, value)
    az.main()
    text = out.read_text(encoding="utf-8")
    assert "authorized=false" in text and "changes were requested" in text


def test_action_wires_review_decision():
    # Pins the action.yml side of the coupling: gather must emit the
    # review_decision output (NONE-normalized) and authz must map it to the
    # REVIEW_DECISION env var main() reads.
    action = (_D.parent / "actions" / "comment-ops" / "action.yml").read_text(encoding="utf-8")
    assert '// "NONE"' in action
    assert 'echo "review_decision=$rd"' in action
    assert "REVIEW_DECISION: ${{ steps.gather.outputs.review_decision }}" in action


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
