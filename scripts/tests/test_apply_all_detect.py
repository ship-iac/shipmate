import json

import pytest
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


def test_foreign_app_completed_check_stays_pending():
    # A completed+success check created by a foreign identity (github-actions,
    # app id 15368) must not count as done once SHIPMATE_APP_ID scopes the
    # detect to the shipmate App (999) -- main() calls ag.app_done_names on the
    # raw JSONL lines; reproduce that exact call here so this test would go
    # red if main() ever stopped routing through app_done_names.
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    line = json.dumps(
        {
            "name": "apply / stacks/app / dev-eu",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-18T10:00:00Z",
            "id": 1,
            "app": {"id": 15368},
        }
    )
    done = aad.ag.app_done_names([line], "999")
    assert aad.ad.filter_pending(cells, done) == cells


def test_reuses_single_sourced_helpers():
    # Workset matching, pending filter, env-level bucketing and the done
    # predicate must come from the existing single-sourced implementations,
    # not private copies (complexity-budget rows 6 and 9). env-level bucketing
    # and the GITHUB_OUTPUT writer live in env-order (shared with deploy-detect),
    # so this script no longer loads the deploy-detect entry-point module at all.
    assert aad.ad.workset_from_artifacts is not None
    assert aad.eo.waves_by_env_level is not None
    assert aad.eo.write_env_level_waves is not None
    assert aad.ag.done_names is not None
    assert not hasattr(aad, "dd")
    assert not hasattr(aad, "workset_from_artifacts_impl")
