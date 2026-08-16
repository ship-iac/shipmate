import json

import pytest
from _detect_fixtures import check_run, completed_names
from _loader import load_script

aad = load_script("apply-all-detect")


def test_cells_from_artifacts_all_envs():
    names = [
        "plan.dev-eu.stacks-app",
        "plan.dev-eu.stacks-dns",
        "plan.dev-us.stacks-app",
        "cell-summary.dev-eu.stacks-app",  # not a plan artifact — excluded
    ]
    stacks_by_env = {
        "dev-eu": ["stacks/app", "stacks/dns", "stacks/platform"],
        "dev-us": ["stacks/app"],
    }
    cells = aad.cells_from_artifacts(names, stacks_by_env, {})
    assert sorted((c["environment"], c["stack"]) for c in cells) == [
        ("dev-eu", "stacks/app"),
        ("dev-eu", "stacks/dns"),
        ("dev-us", "stacks/app"),
    ]


def test_cells_from_artifacts_attaches_workload_var_from_the_tags():
    cells = aad.cells_from_artifacts(
        ["plan.dev-eu.stacks-app", "plan.dev-us.stacks-app", "plan.dev-eu.stacks-dns"],
        {"dev-eu": ["stacks/app", "stacks/dns"], "dev-us": ["stacks/app"]},
        {"stacks/app": ["workload/net-edge"]},  # stacks/dns absent -> ""
    )
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload_var": "NET_EDGE"},
        {"stack": "stacks/dns", "environment": "dev-eu", "workload_var": ""},
        {"stack": "stacks/app", "environment": "dev-us", "workload_var": "NET_EDGE"},
    ]


def test_cells_from_artifacts_env_without_artifacts_contributes_nothing():
    cells = aad.cells_from_artifacts(
        ["plan.dev-eu.stacks-app"], {"dev-eu": ["stacks/app"], "prod": ["stacks/app"]}, {}
    )
    assert all(c["environment"] == "dev-eu" for c in cells)


def test_cells_from_artifacts_slug_collision_fails_loud():
    with pytest.raises(SystemExit):
        aad.cells_from_artifacts(
            ["plan.dev-eu.stacks-a-b"], {"dev-eu": ["stacks/a/b", "stacks-a/b"]}, {}
        )


def test_cells_from_artifacts_rejects_dotted_env():
    # env names come from tags here, but the artifact-name boundary invariant
    # is enforced at this trust boundary too, like apply-detect's main().
    with pytest.raises(SystemExit):
        aad.cells_from_artifacts([], {"dev.eu": ["stacks/app"]}, {})


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


def test_foreign_app_completed_check_stays_pending(monkeypatch):
    # A completed+success check authored by another identity (github-actions,
    # app id 15368) must not count as done for the bare-apply queue.
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    done = completed_names(aad.ad, monkeypatch, [check_run(app={"id": 15368})])
    assert aad.ad.filter_pending(cells, done) == cells


def test_reuses_single_sourced_helpers():
    # Workset matching, pending filter, env-level bucketing and the done
    # predicate must come from the shared implementations, not private copies.
    # env-level bucketing and the GITHUB_OUTPUT writer live in env-order (shared
    # with deploy-detect), so this script no longer loads deploy-detect at all.
    assert aad.ad.workset_from_artifacts is not None
    assert aad.eo.waves_by_env_level is not None
    assert aad.eo.write_env_level_waves is not None
    # No local apply-gate alias -- test_detect_app_scoping pins which route
    # main() actually takes; this only asserts the second one does not exist.
    assert not hasattr(aad, "ag")
    assert not hasattr(aad, "dd")
    assert not hasattr(aad, "workset_from_artifacts_impl")


def test_main_wires_the_tag_map_into_the_cells(tmp_path, monkeypatch):
    out = tmp_path / "out"
    for k, v in {
        "GITHUB_REPOSITORY": "o/r",
        "SHIPMATE_PLAN_RUN_ID": "42",
        "SHIPMATE_HEAD_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(out),
    }.items():
        monkeypatch.setenv(k, v)
    for k in ("SHIPMATE_UNGATED_ENVS", "SHIPMATE_REVIEW_DECISION"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(aad.ad, "verify_plan_run", lambda *a: None)
    monkeypatch.setattr(aad.ad, "run_graph_deps", lambda: {"stacks/app": set()})
    monkeypatch.setattr(aad.ad, "_artifact_names", lambda *a: ["plan.dev-eu.stacks-app"])
    monkeypatch.setattr(aad.ad, "completed_apply_names", lambda *a: set())
    monkeypatch.setattr(aad.eo, "read_env_order", lambda: {})
    monkeypatch.setattr(aad.eo, "read_explicit_envs", lambda: [])
    monkeypatch.setattr(
        aad.bm,
        "env_membership",
        lambda **kw: (
            {"dev-eu": ["stacks/app"]},
            {"stacks/app": ["env/dev-eu", "workload/net-edge"]},
        ),
    )
    aad.main()
    parsed = dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())
    assert json.loads(parsed["envlevel0_waves"])["wave0"] == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload_var": "NET_EDGE"}
    ]


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


@pytest.mark.parametrize(
    "decision", ["NONE", "APPROVED", "REVIEW_REQUIRED", "CHANGES_REQUESTED", "", "BANANA"]
)
def test_review_held_holds_nothing_when_the_variable_is_unset(decision):
    # The backward-compat hinge: an un-opted-in repository's bare apply must
    # behave exactly as it does today, including when the review job was
    # skipped and the decision arrives empty.
    assert aad.review_held(PENDING, frozenset(), decision) == []


def test_review_held_matches_the_variable_case_insensitively():
    ungated = aad.az.parse_ungated_envs("DEV-EU")
    assert aad.review_held({"dev-eu", "prod-eu"}, ungated, "REVIEW_REQUIRED") == ["prod-eu"]


def _run_main(tmp_path, monkeypatch, *, envs, order=None, explicit=(), ungated=None, decision=None):
    """main() over one `stacks/app` cell per env in `envs`, everything the
    script reaches from GitHub/Terramate stubbed. Returns parsed GITHUB_OUTPUT."""
    out = tmp_path / "out"
    for k, v in {
        "GITHUB_REPOSITORY": "o/r",
        "SHIPMATE_PLAN_RUN_ID": "42",
        "SHIPMATE_HEAD_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(out),
    }.items():
        monkeypatch.setenv(k, v)
    for k, v in (("SHIPMATE_UNGATED_ENVS", ungated), ("SHIPMATE_REVIEW_DECISION", decision)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    monkeypatch.setattr(aad.ad, "verify_plan_run", lambda *a: None)
    monkeypatch.setattr(aad.ad, "run_graph_deps", lambda: {"stacks/app": set()})
    artifacts = [f"plan.{e}.stacks-app" for e in envs]
    monkeypatch.setattr(aad.ad, "_artifact_names", lambda *a: artifacts)
    monkeypatch.setattr(aad.ad, "completed_apply_names", lambda *a: set())
    monkeypatch.setattr(aad.eo, "read_env_order", lambda: dict(order or {}))
    monkeypatch.setattr(aad.eo, "read_explicit_envs", lambda: list(explicit))
    monkeypatch.setattr(
        aad.bm,
        "env_membership",
        lambda **kw: ({e: ["stacks/app"] for e in envs}, {"stacks/app": []}),
    )
    aad.main()
    return dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())


def _wave_envs(parsed):
    return sorted(
        c["environment"]
        for lvl in range(aad.eo.MAX_ENV_LEVELS)
        for w in [json.loads(parsed[f"envlevel{lvl}_waves"])]
        for i in range(aad.wv.MAX_WAVES)
        for c in w[f"wave{i}"]
    )


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


def test_main_claims_nothing_applied_ungated_when_the_variable_is_unset(tmp_path, monkeypatch):
    # REVIEW_REQUIRED with no list is an ordinary unreviewed apply on the
    # ruleset's terms -- nothing was exempted, so the comment must not name
    # every env as applied-without-review.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        decision="REVIEW_REQUIRED",
    )
    assert _wave_envs(parsed) == ["dev-eu", "prod-eu"]
    assert json.loads(parsed["applied_ungated_envs"]) == []


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


def test_main_without_the_variable_runs_every_env_and_reports_nothing(tmp_path, monkeypatch):
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us", "prod-eu"],
        order={"dev-us": ["prod-eu"]},
        decision="CHANGES_REQUESTED",
    )
    assert _wave_envs(parsed) == ["dev-eu", "dev-us", "prod-eu"]
    assert json.loads(parsed["review_held_envs"]) == []
    assert json.loads(parsed["applied_ungated_envs"]) == []
    assert json.loads(parsed["excluded_envs"]) == []
    assert json.loads(parsed["skipped_envs"]) == []
