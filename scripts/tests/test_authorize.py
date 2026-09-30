import json

import pytest
from _loader import SCRIPTS as _D
from _loader import load_script

az = load_script("authorize")

PR_OK = {"mergeable": True, "mergeable_state": "clean", "head": {"sha": "abc123"}}
#: What the gather step hands over: the plan run each App-authored apply check on
#: the PR head records. No head SHA anywhere -- a record read from that head's own
#: check-runs is for that head by construction.
RUNS_OK = {"apply / stacks/app / dev-eu": "555"}
#: The refusal when the head names no plan run at all, hand-written: it is the
#: sentence a commenter reads on a PR, not an implementation detail.
NO_PLAN_REASON = (
    "no reviewed plan for the current PR head — no apply check on it names a "
    "plan run. Plan this head (push; a re-run alone will not clear a plan from "
    "before an engine re-pin), then `shipmate apply` again."
)
UNGATED_DEV = frozenset({"dev-eu"})
#: The refusal an apply on a draft gets, hand-written: it is the sentence a
#: commenter reads on a PR.
DRAFT_REASON = (
    "not authorized: this pull request is a draft. A draft can be planned but not "
    "applied — mark it ready for review, then comment the apply again."
)


def _a1(verb, permission):
    return (
        f"not authorized: `shipmate {verb}` needs write access to this repository, and the "
        f"commenter's permission is `{permission}`."
    )


def _a2(verb):
    return (
        "not authorized: could not read the commenter's permission on this repository, so "
        f"`shipmate {verb}` was not run. This run's log has the API response; comment again."
    )


def _a3(verb, permission):
    return (
        f"not authorized: `shipmate {verb}` needs write access to this repository, and GitHub "
        f"reported the commenter's permission as `{permission}`, which this engine does not "
        "admit."
    )


def _decide(**kw):
    base = dict(
        permission="write",
        review_decision="NONE",
        pr=PR_OK,
        plan_runs=RUNS_OK,
    )
    base.update(kw)
    return az.decide(**base)


def test_all_conditions_met_authorizes():
    ok, reason = _decide()
    assert ok and reason == ""


#: (permission, verb) -> the whole verdict. Apply rows that pass the permission check reach
#: `_decide`'s otherwise authorizable pull request, so they authorize too.
_PERMISSION_TABLE = [
    (p, verb, expected)
    for verb in ("apply", "unlock")
    for p, expected in (
        ("admin", (True, "")),
        ("write", (True, "")),
        ("read", (False, _a1(verb, "read"))),
        ("none", (False, _a1(verb, "none"))),
        ("", (False, _a2(verb))),
        ("WRITE", (False, _a2(verb))),
        ("wr ite", (False, _a2(verb))),
        ("write\n", (False, _a2(verb))),
        ("maintain", (False, _a3(verb, "maintain"))),
        ("triage", (False, _a3(verb, "triage"))),
        ("null", (False, _a3(verb, "null"))),
    )
]


@pytest.mark.parametrize(("permission", "verb", "expected"), _PERMISSION_TABLE)
def test_permission_decision_table(permission, verb, expected):
    """`read` is every account on a public repository and `none` any other non-collaborator, so
    both refuse. An unreadable value refuses without being quoted; an unexpected readable one
    (the docs say `maintain` and `triage` fold into `write` and `read`, unprobed) refuses too.

    Mutations: admit `read` (the read rows authorize); drop the `admin` branch (the admin rows
    land on A3); admit any non-empty value (every refusal but `""` authorizes); drop the
    charset check (`WRITE` is quoted in A3); `re.match(r"^[a-z]+$", ...)` in place of
    `fullmatch` (`"write\\n"` reaches A3)."""
    assert _decide(permission=permission, verb=verb) == expected


def test_unmergeable_rejected():
    ok, reason = _decide(
        pr={"mergeable": False, "mergeable_state": "dirty", "head": {"sha": "abc123"}}
    )
    assert not ok and "not mergeable" in reason and "dirty" in reason


def test_review_decision_none_authorizes():
    # NONE is comment-ops' sentinel for a null reviewDecision, which means the ruleset requires
    # no review.
    ok, reason = _decide(review_decision="NONE")
    assert ok and reason == ""


def test_review_decision_approved_authorizes():
    ok, reason = _decide(review_decision="APPROVED")
    assert ok and reason == ""


def test_review_decision_empty_fails_closed():
    # An empty value means the review signal never arrived (missing env var, wiring drift), so
    # it must not authorize, unlike the explicit NONE sentinel.
    ok, reason = _decide(review_decision="")
    assert not ok and "could not determine" in reason


def test_review_decision_unknown_value_fails_closed():
    # Unrecognized values (a future GitHub enum, the literal text "null") fail closed rather
    # than silently authorizing.
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
    # The unblock path must not dead-end a sole maintainer, who cannot self-approve, so
    # dismissing the review is named as an option.
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
    # Pins main()'s env-var wiring: REVIEW_DECISION is the name the action sends, and a rename
    # on either side must fail this test rather than fail open.
    pr_json = tmp_path / "pr.json"
    pr_json.write_text(json.dumps(PR_OK), encoding="utf-8")
    run_json = tmp_path / "plan_run.json"
    run_json.write_text(json.dumps(RUNS_OK), encoding="utf-8")
    out = tmp_path / "out.txt"
    out.touch()
    for key, value in {
        "PERMISSION": "write",
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
    # Pins the action.yml side of the coupling: gather emits the review_decision output,
    # NONE-normalized, and authz maps it to the REVIEW_DECISION env var main() reads.
    action = (_D.parent / "actions" / "comment-ops" / "action.yml").read_text(encoding="utf-8")
    assert '// "NONE"' in action
    assert 'echo "review_decision=$rd"' in action
    assert "REVIEW_DECISION: ${{ steps.gather.outputs.review_decision }}" in action


def test_a_head_whose_apply_checks_name_a_plan_run_authorizes():
    # The absence branch is the only plan-run condition left, so a mapping that carries no head
    # SHA at all must authorize. Reinstating a comparison against the PR head refuses every
    # apply, because there is nothing to compare.
    ok, reason = _decide(plan_runs={"apply / stacks/app / dev-eu": "555"})
    assert (ok, reason) == (True, "")


def test_no_apply_check_naming_a_plan_run_refuses_with_the_shipped_sentence():
    ok, reason = _decide(plan_runs={})
    assert (ok, reason) == (False, NO_PLAN_REASON)


def test_mergeable_null_reports_still_computing_not_conflict():
    # null or unknown means GitHub has not finished computing, which gets its own non-blaming
    # message. It must not say conflicts or not-mergeable.
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


def test_parse_ungated_envs_casefolds_every_entry():
    """`gate-config` casefolds before joining, so this is defence at a cross-process string
    boundary rather than the only casefold. Kept because dropping it makes an authorization
    decision depend on an invariant held in another script, and pinned because an unpinned
    defence is the one a later simplification deletes.

    Mutation: return the entries verbatim -- `DEV-EU` then matches no environment, and the
    exemption silently stops applying.
    """
    assert az.parse_ungated_envs("DEV-EU,Dev-Us") == frozenset({"dev-eu", "dev-us"})


@pytest.mark.parametrize("entry", ["dev-eu", "dev_eu", "env1", "2dev"])
def test_parse_ungated_envs_accepts_every_env_name_shape(entry):
    # The other half of the allow-list: refusing a legal env name would refuse applies the
    # operator opted in for. Probed against Terramate, which accepts each of these as a tag.
    assert az.parse_ungated_envs(entry) == frozenset({entry})


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


def test_unlisted_env_reason_names_the_setting():
    """Compared whole, against a hand-written constant: the refusal has to name both the
    environment the commenter asked for and the setting that would exempt it, and a partial
    check leaves whichever half it does not read free to go stale."""
    ok, reason = _decide(
        review_decision="REVIEW_REQUIRED", environment="prod-eu", ungated_envs=UNGATED_DEV
    )
    assert not ok
    assert reason == (
        "not authorized: PR review is required by the branch ruleset and has not been "
        "satisfied, and `environments.prod-eu.gated` is not `false` in "
        "`.github/shipmate.toml`; obtain the required approving review(s), then re-run "
        "`shipmate apply`."
    )


@pytest.mark.parametrize("environment", ["dev-eu", ""])
def test_empty_list_keeps_todays_review_required_message(environment):
    # With nothing exempted, the message must not mention the opt-out at all.
    ok, reason = _decide(review_decision="REVIEW_REQUIRED", environment=environment)
    assert not ok
    assert reason == (
        "not authorized: PR review is required by the branch ruleset and "
        "has not been satisfied; obtain the required approving review(s), "
        "then re-run `shipmate apply`."
    )


def test_exemption_does_not_reach_the_other_checks():
    # An exempting decision must not authorize anything the other predicates refuse: the
    # exemption sits inside the review check, not around it.
    exempt = dict(review_decision="REVIEW_REQUIRED", environment="dev-eu", ungated_envs=UNGATED_DEV)
    ok, reason = _decide(permission="read", **exempt)
    assert (ok, reason) == (False, _a1("apply", "read"))
    ok, reason = _decide(
        pr={"mergeable": False, "mergeable_state": "dirty", "head": {"sha": "abc123"}}, **exempt
    )
    assert not ok and "not mergeable" in reason
    ok, reason = _decide(plan_runs={}, **exempt)
    assert not ok and "no reviewed plan" in reason


def test_main_reads_ungated_envs_and_environment(tmp_path, monkeypatch):
    # Pins that both SHIPMATE_GATE_UNGATED_ENVS and SHIPMATE_ENV reach decide(). The env is
    # deliberately not ungated, so the refusal carries the exemption-aware message only if
    # both values arrived.
    pr_json = tmp_path / "pr.json"
    pr_json.write_text(json.dumps(PR_OK), encoding="utf-8")
    run_json = tmp_path / "plan_run.json"
    run_json.write_text(json.dumps(RUNS_OK), encoding="utf-8")
    out = tmp_path / "out.txt"
    out.touch()
    for key, value in {
        "PERMISSION": "write",
        "PR_JSON": str(pr_json),
        "PLAN_RUN_JSON": str(run_json),
        "GITHUB_OUTPUT": str(out),
        "REVIEW_DECISION": "REVIEW_REQUIRED",
        "SHIPMATE_ENV": "prod-eu",
        "SHIPMATE_GATE_UNGATED_ENVS": "dev-eu",
    }.items():
        monkeypatch.setenv(key, value)
    az.main()
    text = out.read_text(encoding="utf-8")
    assert "authorized=false" in text.splitlines()
    assert (
        "reason=not authorized: PR review is required by the branch ruleset and has not been "
        "satisfied, and `environments.prod-eu.gated` is not `false` in `.github/shipmate.toml`; "
        "obtain the required approving review(s), then re-run `shipmate apply`."
    ) in text.splitlines()
    assert "environment=prod-eu" in text.splitlines()


def _main_output(tmp_path, monkeypatch, *, pr=PR_OK, plan_runs=RUNS_OK, **env):
    pr_json = tmp_path / "pr.json"
    pr_json.write_text(json.dumps(pr), encoding="utf-8")
    run_json = tmp_path / "plan_run.json"
    run_json.write_text(json.dumps(plan_runs), encoding="utf-8")
    out = tmp_path / "out.txt"
    out.touch()
    base = {
        "PERMISSION": "write",
        "PR_JSON": str(pr_json),
        "PLAN_RUN_JSON": str(run_json),
        "GITHUB_OUTPUT": str(out),
        "REVIEW_DECISION": "NONE",
        "SHIPMATE_ENV": "",
        "SHIPMATE_GATE_UNGATED_ENVS": "",
    }
    base.update(env)
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    az.main()
    return dict(
        ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines() if "=" in ln
    )


def test_ungated_exemption_is_not_reported_when_a_later_requirement_refused(tmp_path, monkeypatch):
    # The exemption passed the review check and the apply was still refused, for want of a plan
    # run on the head, which is checked after it. The report claims permission to apply, so it
    # must not post over a refusal.
    parsed = _main_output(
        tmp_path,
        monkeypatch,
        plan_runs={},
        REVIEW_DECISION="REVIEW_REQUIRED",
        SHIPMATE_ENV="dev-eu",
        SHIPMATE_GATE_UNGATED_ENVS="dev-eu",
    )
    assert parsed["authorized"] == "false"
    assert parsed["ungated_exemption"] == "false"


@pytest.mark.parametrize(
    ("decision", "environment", "ungated", "pr", "expected"),
    [
        # The exemption fired: this apply proceeds with no approving review, and the
        # comment-ops report is its only trace.
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
        # Exempt from review, refused anyway (unmergeable). Reporting a permitted apply over
        # a refused one would be a false audit line.
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
        SHIPMATE_GATE_UNGATED_ENVS=ungated,
    )
    assert parsed["ungated_exemption"] == expected


def test_apply_on_a_draft_is_refused_with_the_remedy():
    ok, reason = _decide(pr={**PR_OK, "draft": True})
    assert (ok, reason) == (False, DRAFT_REASON)


def test_apply_on_a_non_draft_is_unaffected():
    ok, reason = _decide(pr={**PR_OK, "draft": False})
    assert (ok, reason) == (True, "")


def test_draft_refusal_runs_after_the_permission_check():
    # A read-only commenter on a draft is told about the permission: the reason a commenter can
    # act on first, and the fail-fast order every check here relies on.
    ok, reason = _decide(permission="read", pr={**PR_OK, "draft": True})
    assert (ok, reason) == (False, _a1("apply", "read"))


def test_draft_refusal_runs_before_the_mergeable_check():
    # A draft with conflicts reports mergeable_state "dirty", so keying on that field, or
    # ordering the draft check after it, hides the draft.
    ok, reason = _decide(
        pr={"mergeable": False, "mergeable_state": "dirty", "draft": True, "head": {"sha": "abc"}}
    )
    assert (ok, reason) == (False, DRAFT_REASON)


def test_unlock_on_a_draft_is_still_authorized():
    ok, reason = az.decide(
        permission="write",
        review_decision="",
        pr={"mergeable": None, "draft": True, "head": {"sha": "a" * 40}},
        plan_runs={},
        environment="dev-eu",
        verb="unlock",
    )
    assert (ok, reason) == (True, "")


def test_unlock_needs_only_write_access():
    ok, reason = az.decide(
        permission="write",
        review_decision="",  # There is no decision at all.
        pr={"mergeable": None, "head": {"sha": "a" * 40}},  # The pull request is merged.
        plan_runs={},  # There is no reviewed plan.
        environment="dev-eu",
        verb="unlock",
    )
    assert (ok, reason) == (True, "")


def test_unlock_still_refuses_a_read_only_commenter():
    ok, reason = az.decide(
        permission="read",
        review_decision="APPROVED",
        pr={"mergeable": True, "head": {"sha": "a" * 40}},
        plan_runs=RUNS_OK,
        environment="dev-eu",
        verb="unlock",
    )
    assert (ok, reason) == (False, _a1("unlock", "read"))


def test_apply_is_unchanged_by_the_verb_default():
    # The apply path must not become laxer: same inputs as the unlock case above.
    ok, reason = az.decide(
        permission="write",
        review_decision="",
        pr={"mergeable": None, "head": {"sha": "a" * 40}},
        plan_runs={},
        environment="dev-eu",
    )
    assert not ok


def test_unlock_with_ungated_exemption_does_not_produce_false_audit_line(tmp_path, monkeypatch):
    """unlock with review_decision="REVIEW_REQUIRED", the named env in ungated_envs and
    write permission authorizes, without the exemption. The exemption is an audit record of an
    apply without review, and unlock applies nothing, so it must not produce that audit
    line."""
    pr_json = tmp_path / "pr.json"
    pr_json.write_text(json.dumps(PR_OK), encoding="utf-8")
    run_json = tmp_path / "plan_run.json"
    run_json.write_text(json.dumps(RUNS_OK), encoding="utf-8")
    out = tmp_path / "out.txt"
    out.touch()
    for key, value in {
        "PERMISSION": "write",
        "PR_JSON": str(pr_json),
        "PLAN_RUN_JSON": str(run_json),
        "GITHUB_OUTPUT": str(out),
        "REVIEW_DECISION": "REVIEW_REQUIRED",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_GATE_UNGATED_ENVS": "dev-eu",
        "SHIPMATE_VERB": "unlock",
    }.items():
        monkeypatch.setenv(key, value)
    az.main()
    text = out.read_text(encoding="utf-8")
    assert "authorized=true" in text
    assert "ungated_exemption=false" in text


def test_apply_with_ungated_exemption_still_produces_audit_line(tmp_path, monkeypatch):
    # apply, not unlock, with review_decision="REVIEW_REQUIRED", the named env in ungated_envs
    # and write permission authorizes and records the exemption. Scoping the exemption to the
    # verb must not disable it for apply.
    pr_json = tmp_path / "pr.json"
    pr_json.write_text(json.dumps(PR_OK), encoding="utf-8")
    run_json = tmp_path / "plan_run.json"
    run_json.write_text(json.dumps(RUNS_OK), encoding="utf-8")
    out = tmp_path / "out.txt"
    out.touch()
    for key, value in {
        "PERMISSION": "write",
        "PR_JSON": str(pr_json),
        "PLAN_RUN_JSON": str(run_json),
        "GITHUB_OUTPUT": str(out),
        "REVIEW_DECISION": "REVIEW_REQUIRED",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_GATE_UNGATED_ENVS": "dev-eu",
        "SHIPMATE_VERB": "apply",
    }.items():
        monkeypatch.setenv(key, value)
    az.main()
    text = out.read_text(encoding="utf-8")
    assert "authorized=true" in text
    assert "ungated_exemption=true" in text


def test_no_plan_run_id_output(tmp_path, monkeypatch):
    # Each cell applies from the run its own apply check records, so a single run id for the
    # whole command is not merely unused: a consumer wiring it would apply cells from a run that
    # never planned them.
    assert "plan_run_id" not in _main_output(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("decision", "environment", "expected"),
    [
        ("NONE", "prod-eu", True),
        ("NONE", "dev-eu", False),
        ("NONE", "DEV-EU", False),
        ("APPROVED", "prod-eu", False),
        ("REVIEW_REQUIRED", "prod-eu", False),
    ],
)
def test_review_not_required_decision_table(decision, environment, expected):
    """dev-eu holds `gated = false`; prod-eu is gated.

    Mutation: compare `review_decision != "NONE"` -- the three prod-eu rows flip.
    Mutation: drop `.casefold()` -- the `DEV-EU` row is named."""
    assert az._review_not_required(decision, environment, frozenset({"dev-eu"})) is expected
