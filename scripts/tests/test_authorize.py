import json

import pytest
from _loader import SCRIPTS as _D
from _loader import load_script

az = load_script("authorize")

PR_OK = {"mergeable": True, "mergeable_state": "clean", "head": {"sha": "abc123"}}
RUN_OK = {"id": 555, "head_sha": "abc123"}
UNGATED_DEV = frozenset({"dev-eu"})


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
    reasons.add(
        _decide(
            review_decision="REVIEW_REQUIRED",
            environment="prod-eu",
            ungated_envs=UNGATED_DEV,
        )[1]
    )
    assert len(reasons) == 4


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


def test_parse_ungated_envs_empty_yields_empty_set():
    assert az.parse_ungated_envs("") == frozenset()


def test_parse_ungated_envs_splits_on_commas():
    assert az.parse_ungated_envs("dev-eu,dev-us") == frozenset({"dev-eu", "dev-us"})


def test_parse_ungated_envs_ignores_empty_fields():
    assert az.parse_ungated_envs("dev-eu,") == frozenset({"dev-eu"})


def test_ungated_env_match_is_case_insensitive():
    # Pinned through _review_reason, where the match is used: an uppercase
    # variable entry must exempt a lowercase env and vice versa.
    assert az._review_reason("REVIEW_REQUIRED", "dev-eu", az.parse_ungated_envs("DEV-EU")) is None
    assert az._review_reason("REVIEW_REQUIRED", "DEV-EU", az.parse_ungated_envs("dev-eu")) is None


def test_parse_ungated_envs_rejects_environment_suffix():
    # A `-plan`/`-apply` suffixed entry would exempt nothing — refuse loudly and
    # name the bare env to write instead.
    for entry, suffix in (("dev-eu-plan", "-plan"), ("dev-eu-apply", "-apply")):
        with pytest.raises(SystemExit) as exc:
            az.parse_ungated_envs(entry)
        message = str(exc.value)
        assert repr(entry) in message
        assert repr(suffix) in message
        assert repr("dev-eu") in message


@pytest.mark.parametrize("entry", ['"dev-eu"', "dev eu", "dev/eu", "dev.eu", "-dev-eu", "$dev"])
def test_parse_ungated_envs_rejects_an_entry_that_is_not_an_env_name(entry):
    # The suffix and whitespace checks name two shapes of silently-inert entry;
    # the promise ("no silently inert entry") covers the class. A pasted quote,
    # an internal space or a path separator matches no environment either, and
    # the operator would believe the environment is ungated when it is not.
    with pytest.raises(SystemExit) as exc:
        az.parse_ungated_envs(f"dev-us,{entry}")
    assert repr(entry) in str(exc.value)


@pytest.mark.parametrize("entry", ["dev-eu", "DEV-EU", "dev_eu", "env1"])
def test_parse_ungated_envs_accepts_every_env_name_shape(entry):
    # The other half of the allow-list: refusing a legal env name would refuse
    # applies the operator opted in for.
    assert az.parse_ungated_envs(entry) == frozenset({entry.casefold()})


def test_parse_ungated_envs_rejects_padded_entry():
    # A space-padded entry would silently match nothing; refuse and name it.
    for entry in (" dev-eu", "dev-eu "):
        with pytest.raises(SystemExit) as exc:
            az.parse_ungated_envs(f"dev-us,{entry}")
        message = str(exc.value)
        assert repr(entry) in message
        assert repr("dev-eu") in message


@pytest.mark.parametrize(
    ("review_decision", "environment", "ungated_envs", "authorized"),
    [
        ("REVIEW_REQUIRED", "dev-eu", UNGATED_DEV, True),
        ("REVIEW_REQUIRED", "prod-eu", UNGATED_DEV, False),
        ("REVIEW_REQUIRED", "dev-eu", frozenset(), False),
        ("REVIEW_REQUIRED", "", UNGATED_DEV, True),
        ("REVIEW_REQUIRED", "", frozenset(), False),
        ("CHANGES_REQUESTED", "dev-eu", UNGATED_DEV, False),
        ("", "dev-eu", UNGATED_DEV, False),
        ("BANANA", "dev-eu", UNGATED_DEV, False),
        ("NONE", "dev-eu", UNGATED_DEV, True),
        ("APPROVED", "dev-eu", UNGATED_DEV, True),
    ],
    ids=[
        "listed-env-exempt",
        "unlisted-env-refused",
        "no-list-refused",
        "bare-apply-exempt",
        "bare-apply-no-list-refused",
        "changes-requested-not-exempt",
        "absent-decision-fails-closed",
        "unknown-decision-fails-closed",
        "none-authorizes",
        "approved-authorizes",
    ],
)
def test_ungated_env_decision_table(review_decision, environment, ungated_envs, authorized):
    ok, reason = _decide(
        review_decision=review_decision,
        environment=environment,
        ungated_envs=ungated_envs,
    )
    assert ok is authorized
    assert (reason == "") is authorized


def test_unlisted_env_reason_names_the_variable():
    ok, reason = _decide(
        review_decision="REVIEW_REQUIRED", environment="prod-eu", ungated_envs=UNGATED_DEV
    )
    assert not ok
    assert "SHIPMATE_UNGATED_ENVS" in reason
    assert "`prod-eu`" in reason


@pytest.mark.parametrize("environment", ["dev-eu", ""])
def test_empty_list_keeps_todays_review_required_message(environment):
    # With no variable set, the message must not mention the opt-out at all.
    ok, reason = _decide(review_decision="REVIEW_REQUIRED", environment=environment)
    assert not ok
    assert reason == (
        "not authorized: PR review is required by the branch ruleset and "
        "has not been satisfied; obtain the required approving review(s), "
        "then re-run `shipmate apply`."
    )


def test_exemption_does_not_reach_the_other_checks():
    # An exempting decision must not authorize anything the other predicates
    # refuse — the exemption sits inside the review check, not around it.
    exempt = dict(review_decision="REVIEW_REQUIRED", environment="dev-eu", ungated_envs=UNGATED_DEV)
    ok, reason = _decide(is_member=False, **exempt)
    assert not ok and "not a member" in reason
    ok, reason = _decide(
        pr={"mergeable": False, "mergeable_state": "dirty", "head": {"sha": "abc123"}}, **exempt
    )
    assert not ok and "not mergeable" in reason
    ok, reason = _decide(plan_run={}, **exempt)
    assert not ok and "no reviewed plan" in reason
    ok, reason = _decide(plan_run={"id": 9, "head_sha": "OLD"}, **exempt)
    assert not ok and "stale" in reason


def test_main_reads_ungated_envs_and_environment(tmp_path, monkeypatch):
    # Pins that both SHIPMATE_UNGATED_ENVS and SHIPMATE_ENV reach decide():
    # the env is deliberately NOT in the list, so the refusal carries the
    # variable-aware message only if both values arrived.
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
        "REVIEW_DECISION": "REVIEW_REQUIRED",
        "SHIPMATE_ENV": "prod-eu",
        "SHIPMATE_UNGATED_ENVS": "dev-eu",
    }.items():
        monkeypatch.setenv(key, value)
    az.main()
    text = out.read_text(encoding="utf-8")
    assert "authorized=false" in text
    assert "SHIPMATE_UNGATED_ENVS" in text
    assert "`prod-eu`" in text
    assert "environment=prod-eu" in text


def _main_output(tmp_path, monkeypatch, *, pr=PR_OK, plan_run=RUN_OK, **env):
    pr_json = tmp_path / "pr.json"
    pr_json.write_text(json.dumps(pr), encoding="utf-8")
    run_json = tmp_path / "plan_run.json"
    run_json.write_text(json.dumps(plan_run), encoding="utf-8")
    out = tmp_path / "out.txt"
    out.touch()
    base = {
        "IS_MEMBER": "true",
        "APPROVERS_TEAM": "deployers",
        "PR_JSON": str(pr_json),
        "PLAN_RUN_JSON": str(run_json),
        "GITHUB_OUTPUT": str(out),
        "REVIEW_DECISION": "NONE",
        "SHIPMATE_ENV": "",
        "SHIPMATE_UNGATED_ENVS": "",
    }
    base.update(env)
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    az.main()
    return dict(
        ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines() if "=" in ln
    )


def test_ungated_exemption_is_not_reported_when_a_later_requirement_refused(tmp_path, monkeypatch):
    # The exemption passed the review check and the apply was still refused
    # (stale plan, checked after it). The report claims permission to apply, so
    # it must not post over a refusal.
    parsed = _main_output(
        tmp_path,
        monkeypatch,
        plan_run={"id": 555, "head_sha": "older"},
        REVIEW_DECISION="REVIEW_REQUIRED",
        SHIPMATE_ENV="dev-eu",
        SHIPMATE_UNGATED_ENVS="dev-eu",
    )
    assert parsed["authorized"] == "false"
    assert parsed["ungated_exemption"] == "false"


@pytest.mark.parametrize(
    ("decision", "environment", "ungated", "pr", "expected"),
    [
        # The exemption fired: this apply proceeds with no approving review and
        # the comment-ops report is its only trace.
        ("REVIEW_REQUIRED", "dev-eu", "dev-eu", PR_OK, "true"),
        ("REVIEW_REQUIRED", "DEV-EU", "dev-eu", PR_OK, "true"),
        # Authorized, but not by the exemption -- an ordinary reviewed apply.
        ("APPROVED", "dev-eu", "dev-eu", PR_OK, "false"),
        ("NONE", "dev-eu", "dev-eu", PR_OK, "false"),
        # Not exempt at all.
        ("REVIEW_REQUIRED", "prod-eu", "dev-eu", PR_OK, "false"),
        ("REVIEW_REQUIRED", "dev-eu", "", PR_OK, "false"),
        # Bare apply: partitioned per env by apply-all-detect, which reports it.
        ("REVIEW_REQUIRED", "", "dev-eu", PR_OK, "false"),
        # Exempt from review, refused anyway (unmergeable) -- reporting a
        # permitted apply over a refused one would be a false audit line.
        (
            "REVIEW_REQUIRED",
            "dev-eu",
            "dev-eu",
            {"mergeable": False, "mergeable_state": "dirty", "head": {"sha": "abc123"}},
            "false",
        ),
    ],
)
def test_ungated_exemption_output_is_set_only_when_the_exemption_fired(
    tmp_path, monkeypatch, decision, environment, ungated, pr, expected
):
    parsed = _main_output(
        tmp_path,
        monkeypatch,
        pr=pr,
        REVIEW_DECISION=decision,
        SHIPMATE_ENV=environment,
        SHIPMATE_UNGATED_ENVS=ungated,
    )
    assert parsed["ungated_exemption"] == expected


def test_unlock_needs_only_team_membership():
    ok, reason = az.decide(
        is_member=True,
        approvers_team="infra",
        review_decision="",  # no decision at all
        pr={"mergeable": None, "head": {"sha": "a" * 40}},  # a merged PR
        plan_run={},  # no reviewed plan
        environment="dev-eu",
        verb="unlock",
    )
    assert (ok, reason) == (True, "")


def test_unlock_still_refuses_a_non_member():
    ok, reason = az.decide(
        is_member=False,
        approvers_team="infra",
        review_decision="APPROVED",
        pr={"mergeable": True, "head": {"sha": "a" * 40}},
        plan_run={"head_sha": "a" * 40},
        environment="dev-eu",
        verb="unlock",
    )
    assert not ok and "not a member" in reason


def test_apply_is_unchanged_by_the_verb_default():
    # The apply path must not become laxer: same inputs as the unlock case above.
    ok, reason = az.decide(
        is_member=True,
        approvers_team="infra",
        review_decision="",
        pr={"mergeable": None, "head": {"sha": "a" * 40}},
        plan_run={},
        environment="dev-eu",
    )
    assert not ok
