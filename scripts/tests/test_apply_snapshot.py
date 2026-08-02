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


def test_a_cell_with_no_apply_check_is_warned_about_and_excluded(capsys):
    # This runs pre-flight, before wave0: failing here would skip every wave --
    # and, on the deploy/apply-all paths, every successor env-level -- over one
    # cell. The offending cell is left out of the snapshot instead, so it is
    # never completed and its check stays pending: the safe direction.
    runs = [check("apply / stacks/dns / dev-eu", 1)]
    out = apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert out == {"stacks/dns\x00dev-eu": [1]}
    err = capsys.readouterr().err
    assert err.startswith("::warning::")
    assert "apply / stacks/app / dev-eu" in err


def test_a_run_where_no_cell_has_a_check_fails_loudly():
    # A whole-run signal (wrong head SHA, no plan, a misconfigured App id), not
    # one cell's problem -- nothing could be completed, so fail before applying.
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, [check("stacks/dns / dev-eu", 3)], APP_ID)
    assert "any" in str(exc.value)
