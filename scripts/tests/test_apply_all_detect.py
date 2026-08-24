import json

import pytest
from _detect_fixtures import APP_ID, _apply_check, _record, check_run
from _loader import load_script

aad = load_script("apply-all-detect")

HEAD = "a" * 40
CHECK_RUNS_URL = f"repos/o/r/commits/{HEAD}/check-runs?filter=all&per_page=100"


def test_cells_are_the_tree_candidates_whose_check_is_on_the_head_in_every_env():
    # Membership is the head's apply checks across ALL envs, not one plan run's
    # artifact names: `stacks/platform` is in the dev-eu tree but carries only a
    # PLAN check, and `stacks/app` is work in two envs.
    names = {
        "apply / stacks/app / dev-eu",
        "apply / stacks/dns / dev-eu",
        "apply / stacks/app / dev-us",
        "plan / stacks/platform / dev-eu",
    }
    stacks_by_env = {
        "dev-eu": ["stacks/app", "stacks/dns", "stacks/platform"],
        "dev-us": ["stacks/app"],
    }
    cells = aad.cells_from_checks(names, stacks_by_env, {})
    assert sorted((c["environment"], c["stack"]) for c in cells) == [
        ("dev-eu", "stacks/app"),
        ("dev-eu", "stacks/dns"),
        ("dev-us", "stacks/app"),
    ]


def test_cells_forward_construct_the_check_name_and_never_parse_it():
    # `components/app` contains the '/' that makes a split-on-'/' parse wrong,
    # and `a / b` is ambiguous under any rsplit of `apply / a / b / dev-eu` --
    # is the stack "a" or "a / b"? Forward construction never has to decide.
    stacks_by_env = {"dev-eu": ["components/app", "a / b", "stacks/unplanned"]}
    names = {"apply / components/app / dev-eu", "apply / a / b / dev-eu"}
    cells = aad.cells_from_checks(names, stacks_by_env, {})
    assert [c["stack"] for c in cells] == ["components/app", "a / b"]


def test_cells_take_workload_var_from_the_tags():
    # Never from the check name, which carries no workload. A stack missing from
    # the map carries "" and applies with the environment's generic role.
    cells = aad.cells_from_checks(
        {
            "apply / stacks/app / dev-eu",
            "apply / stacks/dns / dev-eu",
            "apply / stacks/app / dev-us",
        },
        {"dev-eu": ["stacks/app", "stacks/dns"], "dev-us": ["stacks/app"]},
        {"stacks/app": ["workload/net-edge"]},  # stacks/dns absent -> ""
    )
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload_var": "NET_EDGE"},
        {"stack": "stacks/dns", "environment": "dev-eu", "workload_var": ""},
        {"stack": "stacks/app", "environment": "dev-us", "workload_var": "NET_EDGE"},
    ]


def test_env_without_a_check_contributes_nothing():
    cells = aad.cells_from_checks(
        {"apply / stacks/app / dev-eu"}, {"dev-eu": ["stacks/app"], "prod": ["stacks/app"]}, {}
    )
    assert all(c["environment"] == "dev-eu" for c in cells)


def test_slug_alike_paths_never_enrol_each_other():
    # `a/b` and `a-b` slug identically, and both are in the tree. Only `a-b`
    # carries an apply check, so only `a-b` is work. A pull request ADDING `a-b`
    # beside an unchanged `a/b` never shows the pair to build-matrix's plan-time
    # collision guard, so nothing resolving a name back to a path is the whole
    # protection -- there is no slug to collide over any more.
    cells = aad.cells_from_checks({"apply / a-b / dev-eu"}, {"dev-eu": ["a/b", "a-b"]}, {})
    assert [c["stack"] for c in cells] == ["a-b"]


def test_cells_from_checks_rejects_dotted_env():
    # Env names come from tags here, but apply-cell still downloads
    # `plan.<env>.<slug>`, so a dotted env would still render two distinct cells
    # to one artifact name. Enforced at this trust boundary, as in apply-detect.
    with pytest.raises(SystemExit):
        aad.cells_from_checks(set(), {"dev.eu": ["stacks/app"]}, {})


def test_partition_no_explicit_envs():
    assert aad.partition_envs({"dev", "stage"}, [], {"stage": ["dev"]}) == ([], [])


def test_partition_excludes_explicit_env_with_pending_work():
    excluded, skipped = aad.partition_envs({"dev", "stage"}, ["stage"], {"stage": ["dev"]})
    assert excluded == ["stage"] and skipped == []


def test_partition_skips_envs_ordered_after_unapplied_explicit():
    # stage explicit + pending; prod (transitively after stage) is skipped;
    # sbx (level 1 but independent of stage) still runs.
    order = {"stage": ["dev"], "prod": ["stage"], "sbx": ["dev"]}
    excluded, skipped = aad.partition_envs({"dev", "stage", "prod", "sbx"}, ["stage"], order)
    assert excluded == ["stage"]
    assert skipped == ["prod"]


def test_partition_applied_explicit_env_blocks_nothing():
    # prod is explicit but has NO pending cells -> not excluded, successors run.
    excluded, skipped = aad.partition_envs({"dev"}, ["prod"], {"after-prod": ["prod"]})
    assert excluded == [] and skipped == []


def test_a_forged_completed_check_does_not_mark_a_cell_applied(tmp_path, monkeypatch):
    # A completed+success check of the same name from another identity
    # (github-actions, app id 15368) must not count the cell as applied. It is
    # also the NEWER run of that name, so only the App filter keeps the cell in --
    # and main() is what feeds that filter its app id, so this is the behavioural
    # pin on the threading test_detect_app_scoping can only see structurally.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        checks=[
            _apply_check("stacks/app", "dev-eu"),
            check_run(name="apply / stacks/app / dev-eu", id=2, app={"id": 15368}),
        ],
    )
    assert [c["stack"] for c in _wave_cells(parsed)] == ["stacks/app"]


def test_reuses_single_sourced_helpers():
    # The `is not None` halves pin only that these functions still EXIST in the
    # modules this script reaches them through -- not that main() calls them
    # rather than a private copy. What pins that is the `not hasattr` halves
    # below (no second route can exist) plus the behavioural main() tests above.
    # env-level bucketing and the GITHUB_OUTPUT writer live in env-order (shared
    # with deploy-detect), so this script no longer loads deploy-detect at all.
    assert aad.ad.paths_with_checks is not None
    assert aad.ad.cells_for_env is not None
    assert aad.ad.with_plan_runs is not None
    assert aad.eo.waves_by_env_level is not None
    assert aad.eo.write_env_level_waves is not None
    # No local apply-gate alias -- test_detect_app_scoping pins which route
    # main() actually takes; this only asserts the second one does not exist.
    assert not hasattr(aad, "ag")
    assert not hasattr(aad, "dd")
    assert not hasattr(aad, "cells_from_artifacts")


PENDING = {"dev-eu", "dev-us", "prod-eu"}
UNGATED = frozenset({"dev-eu", "dev-us"})


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("NONE", []),
        ("APPROVED", []),
        ("REVIEW_REQUIRED", ["prod-eu"]),
        ("CHANGES_REQUESTED", ["dev-eu", "dev-us", "prod-eu"]),
        ("", ["dev-eu", "dev-us", "prod-eu"]),
        ("BANANA", ["dev-eu", "dev-us", "prod-eu"]),
        # The review job's sentinel for a pr_number matching no pull request:
        # GraphQL returns a null pullRequest with no errors, so the query
        # succeeds and only this value keeps the run from applying everything.
        ("MISSING_PR", ["dev-eu", "dev-us", "prod-eu"]),
    ],
)
def test_review_held_decision_table(decision, expected):
    assert aad.review_held(PENDING, UNGATED, decision) == expected


ALL_PENDING = ["dev-eu", "dev-us", "prod-eu"]


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("NONE", []),
        ("APPROVED", []),
        # The reversal: with nothing exempted, an unreviewed PR holds EVERY env.
        # The empty list used to short-circuit to "hold nothing", which is what
        # let an `ungated-envs` action input wider than the repository variable
        # apply every pending environment unreviewed.
        ("REVIEW_REQUIRED", ALL_PENDING),
        ("CHANGES_REQUESTED", ALL_PENDING),
        ("", ALL_PENDING),
        ("BANANA", ALL_PENDING),
        ("MISSING_PR", ALL_PENDING),
    ],
)
def test_review_held_holds_everything_unreviewed_when_the_variable_is_unset(decision, expected):
    assert aad.review_held(PENDING, frozenset(), decision) == expected


def test_review_held_matches_the_variable_case_insensitively():
    ungated = aad.az.parse_ungated_envs("DEV-EU")
    assert aad.review_held({"dev-eu", "prod-eu"}, ungated, "REVIEW_REQUIRED") == ["prod-eu"]


def _run_main(
    tmp_path,
    monkeypatch,
    *,
    envs,
    order=None,
    explicit=(),
    ungated=None,
    decision=None,
    checks=None,
    tree=None,
    tags=None,
    urls=None,
):
    """main() over the head's apply checks, everything the script reaches from
    GitHub/Terramate stubbed. Defaults to one pending `stacks/app` check per env
    in `envs`. Returns parsed GITHUB_OUTPUT; appends each `gh api` path
    requested to `urls` when one is given."""
    out = tmp_path / "out"
    for k, v in {
        "GITHUB_REPOSITORY": "o/r",
        "SHIPMATE_HEAD_SHA": HEAD,
        "GITHUB_OUTPUT": str(out),
        "SHIPMATE_APP_ID": APP_ID,
    }.items():
        monkeypatch.setenv(k, v)
    for k, v in (("SHIPMATE_UNGATED_ENVS", ungated), ("SHIPMATE_REVIEW_DECISION", decision)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    if checks is None:
        checks = [_apply_check("stacks/app", e) for e in envs]
    jsonl = "\n".join(json.dumps(c) for c in checks)

    def _run(args):
        # Every terramate call must be stubbed -- CI installs uv alone, so a
        # real invocation passes on a developer machine and fails there.
        assert args[0] == "gh", args
        if urls is not None:
            urls.append(args[-1])
        return jsonl

    tree = tree or {e: ["stacks/app"] for e in envs}
    deps = {p: set() for ps in tree.values() for p in ps}
    monkeypatch.setattr(aad.ad, "run_graph_deps", lambda: deps)
    monkeypatch.setattr(aad.ad.bm, "_run", _run)
    monkeypatch.setattr(aad.eo, "read_env_order", lambda: dict(order or {}))
    monkeypatch.setattr(aad.eo, "read_explicit_envs", lambda: list(explicit))
    monkeypatch.setattr(aad.bm, "env_membership", lambda **kw: (tree, tags or {"stacks/app": []}))
    aad.main()
    return dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())


def _wave_cells(parsed):
    return [
        c
        for lvl in range(aad.eo.MAX_ENV_LEVELS)
        for w in [json.loads(parsed[f"envlevel{lvl}_waves"])]
        for i in range(aad.wv.MAX_WAVES)
        for c in w[f"wave{i}"]
    ]


def _wave_envs(parsed):
    return sorted(c["environment"] for c in _wave_cells(parsed))


def test_main_wires_the_tag_map_into_the_cells(tmp_path, monkeypatch):
    # Without the map every cell is role-less and the suite stays green.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        checks=[_apply_check("stacks/app", "dev-eu", plan_run="42")],
        tags={"stacks/app": ["env/dev-eu", "workload/net-edge"]},
    )
    assert json.loads(parsed["envlevel0_waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload_var": "NET_EDGE",
            "plan_run_id": "42",
        }
    ]


def test_main_reads_the_head_listing_and_makes_no_artifact_lookup(tmp_path, monkeypatch):
    # No site keys a bare apply on a plan run's artifacts any more. Whole-list
    # comparison against a hand-written constant, so ANY added gh api call --
    # an artifact listing, a workflow-runs lookup, a second read of this same
    # listing -- reddens here. ONE entry: the workset, the done predicate and the
    # plan runs are all read off a single fetch, so nothing can disagree about a
    # check that changed mid-run.
    urls = []
    parsed = _run_main(tmp_path, monkeypatch, envs=["dev-eu"], decision="APPROVED", urls=urls)
    assert len(_wave_cells(parsed)) == 1  # not vacuous
    assert urls == [CHECK_RUNS_URL]


def test_main_never_enrols_a_slug_alike_stack(tmp_path, monkeypatch):
    # The slug property through the real entry point: `a/b` and `a-b` slug
    # identically, both are in the dev-eu tree, and only `a-b` has a check.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        tree={"dev-eu": ["a/b", "a-b"]},
        tags={"a/b": [], "a-b": []},
        checks=[_apply_check("a-b", "dev-eu")],
    )
    assert [c["stack"] for c in _wave_cells(parsed)] == ["a-b"]


def test_main_gives_each_cell_the_plan_run_its_own_check_names(tmp_path, monkeypatch):
    # The recovery shape, across envs: dev-us was re-planned by a later run while
    # dev-eu is still named by the first. Each must apply from the run that
    # planned it, so one shared id is not enough.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us"],
        decision="APPROVED",
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="111"),
            _apply_check("stacks/app", "dev-us", plan_run="222"),
        ],
    )
    assert sorted((c["environment"], c["plan_run_id"]) for c in _wave_cells(parsed)) == [
        ("dev-eu", "111"),
        ("dev-us", "222"),
    ]


def test_main_refuses_a_cell_whose_check_records_no_plan_run(tmp_path, monkeypatch):
    # Not skipped and not defaulted: a legacy bare-hex record names no run, and
    # a silent default would apply that cell from nowhere. The refusal names the
    # cell so an operator knows what to re-plan.
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            envs=["dev-eu", "dev-us"],
            decision="APPROVED",
            checks=[
                _apply_check("stacks/app", "dev-eu", plan_run="42"),
                check_run(
                    name="apply / stacks/app / dev-us",
                    status="queued",
                    conclusion=None,
                    external_id="b" * 64,
                ),
            ],
        )
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan run recorded for apply / stacks/app / dev-us — the "
        "apply check names no plan run to apply from (a check written before this engine "
        "version records none). Re-plan these stacks on this pull request, then apply again."
    )


def test_main_lets_a_record_less_completed_check_through(tmp_path, monkeypatch):
    # The upgrade shape: dev-us was applied by an older engine version, so its
    # completed check carries a legacy bare-hex record. Only cells still to be
    # applied need a plan run -- the attachment runs AFTER the pending filter, or
    # every pull request open across the upgrade is stranded.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us"],
        decision="APPROVED",
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="42"),
            check_run(name="apply / stacks/app / dev-us", external_id="b" * 64),
        ],
    )
    assert _wave_envs(parsed) == ["dev-eu"]


def test_main_lets_a_record_less_check_in_an_excluded_env_through(tmp_path, monkeypatch):
    # prod-eu is explicit: its check deliberately stays pending for a later
    # targeted `shipmate apply prod-eu`, which refuses there if the record is
    # still missing. Refusing HERE would strand every env that can apply.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        explicit=["prod-eu"],
        decision="APPROVED",
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="42"),
            check_run(
                name="apply / stacks/app / prod-eu",
                status="queued",
                conclusion=None,
                external_id="b" * 64,
            ),
        ],
    )
    assert _wave_envs(parsed) == ["dev-eu"]
    assert json.loads(parsed["excluded_envs"]) == ["prod-eu"]


def test_main_keeps_a_failed_apply_check_re_appliable(tmp_path, monkeypatch):
    # completed with a failing conclusion: done being RUN, but not applied. Such
    # a cell is not pending, so membership by pending-ness alone would silently
    # drop the one cell an operator is retrying.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        checks=[
            check_run(
                name="apply / stacks/app / dev-eu",
                conclusion="failure",
                external_id=_record("42"),
            )
        ],
    )
    assert [c["stack"] for c in _wave_cells(parsed)] == ["stacks/app"]


def test_main_holds_unlisted_envs_and_skips_their_successors(tmp_path, monkeypatch):
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us", "prod-eu"],
        order={"dev-us": ["prod-eu"]},
        ungated="dev-eu,dev-us",
        decision="REVIEW_REQUIRED",
    )
    assert _wave_envs(parsed) == ["dev-eu"]
    assert json.loads(parsed["review_held_envs"]) == ["prod-eu"]
    assert json.loads(parsed["applied_ungated_envs"]) == ["dev-eu"]
    assert json.loads(parsed["excluded_envs"]) == []
    assert json.loads(parsed["skipped_envs"]) == ["dev-us"]


def test_main_reports_every_env_applied_when_all_of_them_are_listed(tmp_path, monkeypatch):
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        ungated="dev-eu,prod-eu",
        decision="REVIEW_REQUIRED",
    )
    assert _wave_envs(parsed) == ["dev-eu", "prod-eu"]
    assert json.loads(parsed["review_held_envs"]) == []
    assert json.loads(parsed["applied_ungated_envs"]) == ["dev-eu", "prod-eu"]


def test_main_omits_a_listed_explicit_env_from_the_applied_report(tmp_path, monkeypatch):
    # dev-us is ungated but explicit: it never ran, so the comment must not
    # claim it applied.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us"],
        explicit=["dev-us"],
        ungated="dev-eu,dev-us",
        decision="REVIEW_REQUIRED",
    )
    assert json.loads(parsed["excluded_envs"]) == ["dev-us"]
    assert json.loads(parsed["applied_ungated_envs"]) == ["dev-eu"]
    assert _wave_envs(parsed) == ["dev-eu"]


def test_main_holds_every_env_unreviewed_when_the_variable_is_unset(tmp_path, monkeypatch):
    # REVIEW_REQUIRED with no list exempts nothing, so nothing applies. The
    # audit line stays empty: no env was permitted to apply without a review.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        decision="REVIEW_REQUIRED",
    )
    assert _wave_envs(parsed) == []
    assert json.loads(parsed["review_held_envs"]) == ["dev-eu", "prod-eu"]
    assert json.loads(parsed["applied_ungated_envs"]) == []


def test_main_holds_every_env_when_the_decision_variable_is_absent(tmp_path, monkeypatch):
    # `decision=None` deletes SHIPMATE_REVIEW_DECISION, so this is the only test
    # that exercises the `os.environ.get(..., "")` default -- the whole
    # fail-closed behaviour when the review job's output never reaches the
    # action. Without it the default could be flipped to "APPROVED" unnoticed.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        decision=None,
    )
    assert _wave_envs(parsed) == []
    assert json.loads(parsed["review_held_envs"]) == ["dev-eu", "prod-eu"]


def test_main_applies_every_env_on_an_approved_pr_with_no_variable(tmp_path, monkeypatch):
    # The un-opted-in consumer's ordinary run: APPROVED applies every pending
    # env regardless of the list, so the unconditional review job costs them a
    # deployment record and nothing else.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        decision="APPROVED",
    )
    assert _wave_envs(parsed) == ["dev-eu", "prod-eu"]
    assert json.loads(parsed["review_held_envs"]) == []


def test_main_claims_nothing_applied_ungated_on_a_reviewed_pull_request(tmp_path, monkeypatch):
    # The variable is set and the envs are listed, but the PR was APPROVED --
    # the exemption never fired, so "permitted to apply without an approving
    # review" over a reviewed run would be a false audit line.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        ungated="dev-eu,prod-eu",
        decision="APPROVED",
    )
    assert _wave_envs(parsed) == ["dev-eu", "prod-eu"]
    assert json.loads(parsed["review_held_envs"]) == []
    assert json.loads(parsed["applied_ungated_envs"]) == []


def test_main_without_the_variable_holds_every_env_on_changes_requested(tmp_path, monkeypatch):
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us", "prod-eu"],
        order={"dev-us": ["prod-eu"]},
        decision="CHANGES_REQUESTED",
    )
    assert _wave_envs(parsed) == []
    assert json.loads(parsed["review_held_envs"]) == ["dev-eu", "dev-us", "prod-eu"]
    assert json.loads(parsed["applied_ungated_envs"]) == []
    # Held, not excluded and not skipped: the fix is "get a review", and the
    # comment must not tell the developer to run `shipmate apply <env>` instead.
    assert json.loads(parsed["excluded_envs"]) == []
    assert json.loads(parsed["skipped_envs"]) == []
