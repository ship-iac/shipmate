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
    cells = aad.cells_from_artifacts(names, stacks_by_env)
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
        ["plan.dev-eu.stacks-app"], {"dev-eu": ["stacks/app"], "prod": ["stacks/app"]}
    )
    assert all(c["environment"] == "dev-eu" for c in cells)


def test_cells_from_artifacts_slug_collision_fails_loud():
    with pytest.raises(SystemExit):
        aad.cells_from_artifacts(
            ["plan.dev-eu.stacks-a-b"], {"dev-eu": ["stacks/a/b", "stacks-a/b"]}
        )


def test_cells_from_artifacts_rejects_dotted_env():
    # env names come from tags here, but the artifact-name boundary invariant
    # is enforced at this trust boundary too, like apply-detect's main().
    with pytest.raises(SystemExit):
        aad.cells_from_artifacts([], {"dev.eu": ["stacks/app"]})


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
