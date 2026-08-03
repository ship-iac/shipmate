"""Unit tests for scripts/apply-snapshot."""

import pytest
from _loader import load_script

apply_snapshot = load_script("apply-snapshot")

WAVES = {
    "wave0": [{"stack": "stacks/dns", "environment": "dev-eu"}],
    "wave1": [{"stack": "stacks/app", "environment": "dev-eu"}],
    "wave2": [],
}


APP_ID = 111222  # arbitrary: the engine repo is public, no real ids in fixtures
OTHER_APP_ID = 333444


def check(name, cid, app_id=APP_ID):
    return {"name": name, "id": cid, "app": {"id": app_id}}


def test_maps_each_cell_to_its_app_authored_check_ids():
    runs = [
        check("apply / stacks/dns / dev-eu", 1),
        check("apply / stacks/app / dev-eu", 2),
        check("stacks/dns / dev-eu", 3),
    ]
    out = apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert out == {
        "stacks/dns\x00dev-eu": [1],
        "stacks/app\x00dev-eu": [2],
    }


def test_ignores_same_name_checks_authored_by_another_identity():
    runs = [
        check("apply / stacks/dns / dev-eu", 1),
        check("apply / stacks/dns / dev-eu", 99, app_id=OTHER_APP_ID),
        check("apply / stacks/app / dev-eu", 2),
    ]
    out = apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert out["stacks/dns\x00dev-eu"] == [1]


def test_keeps_every_pre_existing_duplicate_for_a_cell():
    runs = [
        check("apply / stacks/dns / dev-eu", 1),
        check("apply / stacks/dns / dev-eu", 5),
        check("apply / stacks/app / dev-eu", 2),
    ]
    out = apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert out["stacks/dns\x00dev-eu"] == [1, 5]


def test_a_single_cell_with_no_apply_check_fails_the_whole_run():
    # Excluding the cell instead would let it apply for real with no id for the
    # trailing completer to PATCH: its check stays pending forever, the gate
    # stays pending with it, and a re-run hits the exact-plan stale-plan
    # fail-safe against the state that cell already advanced. This runs
    # pre-flight, before wave0, so failing costs the run but applies nothing.
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, [check("apply / stacks/dns / dev-eu", 1)], APP_ID)
    assert "apply / stacks/app / dev-eu" in str(exc.value)
    assert str(exc.value).startswith("::error::")


def test_a_cell_whose_only_check_belongs_to_another_identity_fails_too():
    # A forged same-name check is not a check this run can complete.
    runs = [
        check("apply / stacks/dns / dev-eu", 1),
        check("apply / stacks/app / dev-eu", 99, app_id=OTHER_APP_ID),
    ]
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert "apply / stacks/app / dev-eu" in str(exc.value)


def test_a_run_where_no_cell_has_a_check_fails_loudly():
    # A whole-run signal (wrong head SHA, no plan, a misconfigured App id).
    # No separate whole-run branch is needed now that the per-cell check is
    # strict -- the first wanted cell already fails it.
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, [check("stacks/dns / dev-eu", 3)], APP_ID)
    assert "apply / stacks/dns / dev-eu" in str(exc.value)


def test_an_empty_wave_set_needs_no_checks():
    assert apply_snapshot.snapshot({"wave0": [], "wave1": None}, [], APP_ID) == {}
