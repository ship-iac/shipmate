import io
import json
import os

import pytest
from _loader import ACTIONS, bash_only, load_script, run_with_gate_gh, step_by

apply_snapshot = load_script("apply-snapshot")
apply_complete = load_script("apply-complete")

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
        "apply / stacks/dns / dev-eu": [1],
        "apply / stacks/app / dev-eu": [2],
    }


def test_ignores_same_name_checks_authored_by_another_identity():
    runs = [
        check("apply / stacks/dns / dev-eu", 1),
        check("apply / stacks/dns / dev-eu", 99, app_id=OTHER_APP_ID),
        check("apply / stacks/app / dev-eu", 2),
    ]
    out = apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert out["apply / stacks/dns / dev-eu"] == [1]


def test_keeps_every_pre_existing_duplicate_for_a_cell():
    runs = [
        check("apply / stacks/dns / dev-eu", 1),
        check("apply / stacks/dns / dev-eu", 5),
        check("apply / stacks/app / dev-eu", 2),
    ]
    out = apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert out["apply / stacks/dns / dev-eu"] == [1, 5]


def test_a_completed_duplicate_is_dropped_and_the_open_one_kept():
    """A later write would relink or retitle a completed check. Mutation: keep every id,
    whatever its status -> dns maps to [1, 5]."""
    runs = [
        {**check("apply / stacks/dns / dev-eu", 1), "status": "queued"},
        {**check("apply / stacks/dns / dev-eu", 5), "status": "completed"},
        {**check("apply / stacks/app / dev-eu", 2), "status": "in_progress"},
    ]
    assert apply_snapshot.snapshot(WAVES, runs, APP_ID) == {
        "apply / stacks/dns / dev-eu": [1],
        "apply / stacks/app / dev-eu": [2],
    }


def test_a_cell_whose_only_check_is_completed_fails_the_whole_run():
    """Mutation: keep every id, whatever its status -> no refusal."""
    runs = [
        check("apply / stacks/dns / dev-eu", 1),
        {**check("apply / stacks/app / dev-eu", 2), "status": "completed"},
    ]
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert str(exc.value) == (
        "::error::no App-authored apply check named 'apply / stacks/app / dev-eu' that is not "
        "yet completed on the head SHA before apply; this cell would apply with nothing to "
        "complete afterwards, stranding its check pending. Re-plan."
    )


def test_a_single_cell_with_no_apply_check_fails_the_whole_run():
    # Excluding the cell instead would let it apply for real with no id for the trailing
    # completer to PATCH: its check stays pending forever, the gate stays pending with it, and a
    # re-run hits the exact-plan stale-plan fail-safe against the state that cell already
    # advanced. This runs pre-flight, before wave0, so failing costs the run but applies nothing.
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
    # A whole-run signal: a wrong head SHA, no plan, a misconfigured App id. The strict per-cell
    # check needs no separate whole-run branch, because the first wanted cell already fails it.
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, [check("stacks/dns / dev-eu", 3)], APP_ID)
    assert "apply / stacks/dns / dev-eu" in str(exc.value)


def test_an_empty_wave_set_needs_no_checks():
    assert apply_snapshot.snapshot({"wave0": [], "wave1": None}, [], APP_ID) == {}


def test_an_empty_app_id_refuses_before_any_check_is_read(monkeypatch):
    """An unset App id is refused as the misconfiguration it is, not reported as a missing
    check on every cell.

    Mutation: filter on `(c.get("app") or {}).get("id") == app_id` inline in `snapshot` instead
    of calling `ag.from_app` -- the run fails with the no-check message, not this one."""
    monkeypatch.setenv("SHIPMATE_APP_ID", "")
    monkeypatch.setenv("SHIPMATE_WAVES_JSON", json.dumps(WAVES))
    stdin = "\n".join(
        json.dumps(check(n, i))
        for i, n in enumerate(("apply / stacks/dns / dev-eu", "apply / stacks/app / dev-eu"))
    )
    monkeypatch.setattr(apply_snapshot.sys, "stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.main()
    assert str(exc.value) == (
        "::error::SHIPMATE_APP_ID is empty; set the SHIPMATE_APP_ID repo/org "
        "variable to the shipmate App id (see docs/github-app.md)."
    )


def test_an_empty_app_id_is_reported_before_malformed_input(monkeypatch):
    """Mutation: delete the early `ag.from_app([], app_id)` call from `main` -- the waves JSON is
    parsed first and a `JSONDecodeError` escapes instead."""
    monkeypatch.setenv("SHIPMATE_APP_ID", "")
    monkeypatch.setenv("SHIPMATE_WAVES_JSON", "{not json")
    monkeypatch.setattr(apply_snapshot.sys, "stdin", io.StringIO("{not json either"))
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.main()
    assert str(exc.value) == (
        "::error::SHIPMATE_APP_ID is empty; set the SHIPMATE_APP_ID repo/org "
        "variable to the shipmate App id (see docs/github-app.md)."
    )


_HEAD = "d" * 40
_LISTING_FAILED = (
    f"::error::could not list the check runs on {_HEAD}; the step log names the cause\n"
)


@bash_only
@pytest.mark.parametrize(
    ("checks_rc", "expected_rc", "expected_stdout"),
    [(0, 0, ""), (1, 1, _LISTING_FAILED)],
    ids=["listed", "failed"],
)
def test_a_failed_listing_fails_the_snapshot_step(
    tmp_path, checks_rc, expected_rc, expected_stdout
):
    """An empty wave set needs no check, so the listing is the step's only way to fail; the
    `listed` case is the control. Mutations: delete the failed listing's `exit 1` -> `failed`
    exits 0; delete its `::error::` line -> `failed` prints nothing on stdout."""
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "HEAD_SHA": _HEAD,
        "SHIPMATE_WAVES_JSON": "{}",
        "SHIPMATE_APP_ID": str(APP_ID),
        "GITHUB_ACTION_PATH": str(ACTIONS / "apply-snapshot"),
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        "RUNNER_TEMP": str(tmp_path),
    }
    proc, calls = run_with_gate_gh(
        tmp_path,
        step_by("apply-snapshot", id="snap")["run"],
        env,
        sha=_HEAD,
        checks_rc=checks_rc,
        checks_jq=".check_runs[]",
    )
    assert (proc.returncode, proc.stdout) == (expected_rc, expected_stdout), proc.stderr
    assert calls == [
        [
            "api",
            "--paginate",
            f"repos/acme/demo/commits/{_HEAD}/check-runs?filter=all&per_page=100",
            "--jq",
            ".check_runs[]",
        ]
    ]


_EIGHT_WAVES = {
    "wave0": [{"stack": "dns", "environment": "dev"}],
    "wave1": [{"stack": "app", "environment": "dev"}, {"stack": "web", "environment": "dev"}],
    **{f"wave{n}": [] for n in range(2, 8)},
}
_EIGHT_SNAPSHOT = {
    "apply / dns / dev": [1],
    "apply / app / dev": [2, 5],
    "apply / web / dev": [3],
}


def test_by_wave_slices_the_snapshot_per_wave():
    """Mutations: put the whole snapshot under every key -> wave0 holds wave1's cells; drop the
    keys whose wave is empty -> wave2..wave7 are missing."""
    assert apply_snapshot.by_wave(_EIGHT_WAVES, _EIGHT_SNAPSHOT) == {
        "wave0": {"apply / dns / dev": [1]},
        "wave1": {"apply / app / dev": [2, 5], "apply / web / dev": [3]},
        "wave2": {},
        "wave3": {},
        "wave4": {},
        "wave5": {},
        "wave6": {},
        "wave7": {},
    }


def test_a_wave_slice_reads_no_later_wave_as_unmatched():
    """wave0's completion runs before wave1 has a job in the listing. Mutation: hand
    `to_complete` the whole snapshot instead of wave0's slice -> wave1's cells read unmatched."""
    jobs = [{"name": "L0 / dev / dns", "conclusion": "success", "status": "completed"}]
    wave0 = apply_snapshot.by_wave(_EIGHT_WAVES, _EIGHT_SNAPSHOT)["wave0"]
    assert apply_complete.to_complete(wave0, jobs) == ([1], [], [], [])


def test_main_writes_the_snapshot_and_its_slices(monkeypatch, tmp_path, capsys):
    """Mutation: write the snapshot unsliced as `by_wave` -> its line holds the whole snapshot."""
    waves = {"wave0": [{"stack": "dns", "environment": "dev"}], "wave1": []}
    out = tmp_path / "out.txt"
    monkeypatch.setenv("SHIPMATE_APP_ID", str(APP_ID))
    monkeypatch.setenv("SHIPMATE_WAVES_JSON", json.dumps(waves))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    stdin = io.StringIO(json.dumps(check("apply / dns / dev", 1)))
    monkeypatch.setattr(apply_snapshot.sys, "stdin", stdin)
    apply_snapshot.main()
    assert out.read_text(encoding="utf-8") == (
        'snapshot={"apply / dns / dev": [1]}\n'
        'by_wave={"wave0": {"apply / dns / dev": [1]}, "wave1": {}}\n'
    )
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("argv", [["ids"], ["by-wave"], ["foo"]])
def test_an_unknown_argument_list_refuses(argv):
    """Mutation: keep a `by-wave` mode -> `by-wave` runs instead of refusing; drop the `if argv:`
    refusal -> `foo` runs the snapshot and fails on its environment."""
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.main(argv)
    assert str(exc.value) == "::error::usage: apply-snapshot"
