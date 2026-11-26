import io
import json
import os

import pytest
from _loader import ACTIONS, action_yaml, bash_only, load_script, run_with_gate_gh, step_by

apply_snapshot = load_script("apply-snapshot")
apply_complete = load_script("apply-complete")

PLAN_RUN = "7"
DIGEST = "e" * 64
PR = "12"
OTHER_PR = "13"


def row(stack, env):
    """A wave row as detect writes it, applying plan run `PLAN_RUN` with digest `DIGEST`."""
    return {"stack": stack, "environment": env, "plan_run_id": PLAN_RUN, "plan_sha256": DIGEST}


WAVES = {
    "wave0": [row("stacks/dns", "dev-eu")],
    "wave1": [row("stacks/app", "dev-eu")],
    "wave2": [],
}


APP_ID = 111222  # arbitrary: the engine repo is public, no real ids in fixtures
OTHER_APP_ID = 333444


def _job(jid, name, conclusion, steps=()):
    status = "in_progress" if conclusion is None else "completed"
    return {
        "id": jid,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": 1,
        "started_at": f"2026-11-20T10:00:{jid % 60:02d}Z",
        "steps": list(steps),
        "html_url": "u" if jid == 9 else f"https://gh/job/{jid}",
    }


#: A wave completion job's listing: itself, `L0 / complete` (id 900), and the `dns` / `dev`
#: cell's `apply` (id 9, its apply step ran) and `save`, both succeeded.
JOBS = [
    _job(900, "L0 / complete", None),
    _job(
        9,
        "L0 / dev / dns / apply",
        "success",
        [
            {"name": "Pre-apply check", "conclusion": "success"},
            {"name": "Apply the cell", "conclusion": "success"},
        ],
    ),
    _job(10, "L0 / dev / dns / save", "success"),
]


def check(name, cid, app_id=APP_ID, **record):
    """An App check run whose record names `PLAN_RUN`, `DIGEST` and pull request `PR`, `record`
    over them."""
    body = {"plan_run": PLAN_RUN, "plan_sha256": DIGEST, "pr": PR, **record}
    return {"name": name, "id": cid, "app": {"id": app_id}, "external_id": json.dumps(body)}


def test_maps_each_cell_to_its_app_authored_check_ids():
    runs = [
        check("apply / dev-eu / stacks/dns", 1),
        check("apply / dev-eu / stacks/app", 2),
        check("stacks/dns / dev-eu", 3),
    ]
    out = apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert out == {
        "apply / dev-eu / stacks/dns": [1],
        "apply / dev-eu / stacks/app": [2],
    }


def test_ignores_same_name_checks_authored_by_another_identity():
    runs = [
        check("apply / dev-eu / stacks/dns", 1),
        check("apply / dev-eu / stacks/dns", 99, app_id=OTHER_APP_ID),
        check("apply / dev-eu / stacks/app", 2),
    ]
    out = apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert out["apply / dev-eu / stacks/dns"] == [1]


def test_keeps_every_pre_existing_duplicate_for_a_cell():
    runs = [
        check("apply / dev-eu / stacks/dns", 1),
        check("apply / dev-eu / stacks/dns", 5),
        check("apply / dev-eu / stacks/app", 2),
    ]
    out = apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert out["apply / dev-eu / stacks/dns"] == [1, 5]


#: The refusal for `stacks/app` with no matching unfinished check, hand-written.
_NONE_APP = (
    "::error::no App-authored apply check named 'apply / dev-eu / stacks/app' that is not yet "
    f"completed and records plan run {PLAN_RUN} with its plan-text digest on the head SHA before "
    "apply; this cell would apply with nothing to complete afterwards, stranding its check "
    "pending. Re-plan."
)


def test_a_completed_duplicate_is_dropped_and_the_open_one_kept():
    """A later write would relink or retitle a completed check; an `in_progress` one, a dead
    run's flip, is still pending. Mutations: keep every id, whatever its status -> dns maps to
    [1, 5]; keep only `queued` ids -> app has none and the run refuses."""
    runs = [
        {**check("apply / dev-eu / stacks/dns", 1), "status": "queued"},
        {**check("apply / dev-eu / stacks/dns", 5), "status": "completed"},
        {**check("apply / dev-eu / stacks/app", 2), "status": "in_progress"},
    ]
    assert apply_snapshot.snapshot(WAVES, runs, APP_ID) == {
        "apply / dev-eu / stacks/dns": [1],
        "apply / dev-eu / stacks/app": [2],
    }


def test_a_cell_whose_only_check_is_completed_fails_the_whole_run():
    """Mutation: keep every id, whatever its status -> no refusal."""
    runs = [
        check("apply / dev-eu / stacks/dns", 1),
        {**check("apply / dev-eu / stacks/app", 2), "status": "completed"},
    ]
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert str(exc.value) == _NONE_APP


def test_a_single_cell_with_no_apply_check_fails_the_whole_run():
    # Excluding the cell instead would let it apply for real with no id for the trailing
    # completer to PATCH: its check stays pending forever, the gate stays pending with it, and a
    # re-run hits the exact-plan stale-plan fail-safe against the state that cell already
    # advanced. This runs pre-flight, before wave0, so failing costs the run but applies nothing.
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, [check("apply / dev-eu / stacks/dns", 1)], APP_ID)
    assert "apply / dev-eu / stacks/app" in str(exc.value)
    assert str(exc.value).startswith("::error::")


def test_a_cell_whose_only_check_belongs_to_another_identity_fails_too():
    # A forged same-name check is not a check this run can complete.
    runs = [
        check("apply / dev-eu / stacks/dns", 1),
        check("apply / dev-eu / stacks/app", 99, app_id=OTHER_APP_ID),
    ]
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert "apply / dev-eu / stacks/app" in str(exc.value)


def test_a_run_where_no_cell_has_a_check_fails_loudly():
    # A whole-run signal: a wrong head SHA, no plan, a misconfigured App id. The strict per-cell
    # check needs no separate whole-run branch, because the first wanted cell already fails it.
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, [check("stacks/dns / dev-eu", 3)], APP_ID)
    assert "apply / dev-eu / stacks/dns" in str(exc.value)


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
        for i, n in enumerate(("apply / dev-eu / stacks/dns", "apply / dev-eu / stacks/app"))
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
    exits 0; delete its `::error::` line -> `failed` prints nothing on stdout; pass `--counted`
    whatever `COUNTED` holds -> the counted page call is sent."""
    proc, calls = run_with_gate_gh(
        tmp_path,
        step_by("apply-snapshot", id="snap")["run"],
        _snap_env(tmp_path, "false"),
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


@bash_only
def test_counted_true_lists_through_the_counted_pages(tmp_path):
    """The stub answers no page call, so the counted listing fails the step. Mutation: drop the
    `flag=(--counted)` branch -> the `--paginate` listing is sent and the step passes."""
    proc, calls = run_with_gate_gh(
        tmp_path,
        step_by("apply-snapshot", id="snap")["run"],
        _snap_env(tmp_path, "true"),
        sha=_HEAD,
        checks_jq=".check_runs[]",
    )
    assert (proc.returncode, proc.stdout) == (1, _LISTING_FAILED), proc.stderr
    assert calls[0] == [
        "api",
        f"repos/acme/demo/commits/{_HEAD}/check-runs?filter=all&per_page=100&page=1",
    ]


def test_counted_defaults_to_false():
    """Mutation: default `counted` to `"true"` -> every caller that omits it lists counted."""
    inputs = action_yaml(ACTIONS / "apply-snapshot" / "action.yml")["inputs"]
    assert inputs["counted"] == {
        "description": (
            '"true" refuses a check-run listing that holds fewer rows than its total_count.'
        ),
        "required": False,
        "default": "false",
    }


def _snap_env(tmp_path, counted):
    return {
        **os.environ,
        "GH_TOKEN": "x",
        "HEAD_SHA": _HEAD,
        "SHIPMATE_WAVES_JSON": "{}",
        "SHIPMATE_APP_ID": str(APP_ID),
        "GITHUB_ACTION_PATH": str(ACTIONS / "apply-snapshot"),
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        "RUNNER_TEMP": str(tmp_path),
        "COUNTED": counted,
    }


_EIGHT_WAVES = {
    "wave0": [row("dns", "dev")],
    "wave1": [row("app", "dev"), row("web", "dev")],
    **{f"wave{n}": [] for n in range(2, 8)},
}
_EIGHT_SNAPSHOT = {
    "apply / dev / dns": [1],
    "apply / dev / app": [2, 5],
    "apply / dev / web": [3],
}


def test_by_wave_slices_the_snapshot_per_wave():
    """Mutations: put the whole snapshot under every key -> wave0 holds wave1's cells; drop the
    keys whose wave is empty -> wave2..wave7 are missing."""
    assert apply_snapshot.by_wave(_EIGHT_WAVES, _EIGHT_SNAPSHOT) == {
        "wave0": {"apply / dev / dns": [1]},
        "wave1": {"apply / dev / app": [2, 5], "apply / dev / web": [3]},
        "wave2": {},
        "wave3": {},
        "wave4": {},
        "wave5": {},
        "wave6": {},
        "wave7": {},
    }


def test_a_wave_slice_reads_no_later_wave_as_unmatched():
    """wave0's completion runs before wave1 has a job in the listing. Mutation: `classify` walks
    every key of `by_wave` instead of those of its wave results -> wave1's cells read
    unresolved."""
    by_wave = apply_snapshot.by_wave(_EIGHT_WAVES, _EIGHT_SNAPSHOT)
    assert apply_complete.classify(by_wave, {"wave0": "success"}, JOBS, "900") == (
        [
            {
                "check": "apply / dev / dns",
                "ids": [1],
                "conclusion": "success",
                "job_id": 9,
                "url": "u",
            }
        ],
        [],
    )


def test_main_writes_only_the_per_wave_slices(monkeypatch, tmp_path, capsys):
    """Mutations: write the snapshot unsliced as `by_wave` -> its line holds the whole snapshot;
    write a `snapshot` output too -> an extra line."""
    waves = {"wave0": [row("dns", "dev")], "wave1": []}
    out = tmp_path / "out.txt"
    monkeypatch.setenv("SHIPMATE_APP_ID", str(APP_ID))
    monkeypatch.setenv("SHIPMATE_WAVES_JSON", json.dumps(waves))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    stdin = io.StringIO(json.dumps(check("apply / dev / dns", 1)))
    monkeypatch.setattr(apply_snapshot.sys, "stdin", stdin)
    apply_snapshot.main()
    assert out.read_text(encoding="utf-8") == (
        'by_wave={"wave0": {"apply / dev / dns": [1]}, "wave1": {}}\n'
    )
    assert capsys.readouterr() == ("", "")


def test_only_a_check_recording_the_rows_plan_is_collected():
    """An older unfinished check records the row's plan; a newer unfinished behind check of the
    same name, published after detect, records another run and no digest: only the older id is
    collected, so the wave's success never completes the behind check.

    Mutation: collect by name only -- `[1, 2]`, red.
    Mutation: collect a check whose record carries no `behind` -- the other-run row's id is
    collected, red."""
    runs = [
        check("apply / dev-eu / stacks/dns", 1),
        check("apply / dev-eu / stacks/dns", 2, plan_run="8", plan_sha256=None, behind=True),
        check("apply / dev-eu / stacks/app", 3),
        check("apply / dev-eu / stacks/app", 4, plan_run="8"),
    ]
    assert apply_snapshot.snapshot(WAVES, runs, APP_ID) == {
        "apply / dev-eu / stacks/dns": [1],
        "apply / dev-eu / stacks/app": [3],
    }


@pytest.mark.parametrize(
    "record",
    [
        pytest.param({"plan_run": "8"}, id="other-run"),
        pytest.param({"plan_sha256": "f" * 64}, id="other-digest"),
        pytest.param({"plan_run": "8", "plan_sha256": None, "behind": True}, id="behind"),
        pytest.param({"plan_run": "6"}, id="older-run"),
    ],
)
def test_a_cell_with_only_a_check_of_another_plan_refuses_before_wave0(record):
    """An older plan's check is collected only beside a matching one, never in its place.

    Mutation: warn and skip the cell instead of refusing -- no refusal, red.
    Mutation: let an older run's check keep the cell from refusing -- older-run, red."""
    runs = [
        check("apply / dev-eu / stacks/dns", 1),
        check("apply / dev-eu / stacks/app", 2, **record),
    ]
    with pytest.raises(SystemExit) as exc:
        apply_snapshot.snapshot(WAVES, runs, APP_ID)
    assert str(exc.value) == _NONE_APP


@pytest.mark.parametrize("newer", ["behind", "newer-plan"])
def test_a_check_published_after_detect_is_never_completed_by_the_older_apply(newer):
    """Detect bound plan run `PLAN_RUN`; a behind check, or a newer plan's check, of the same
    name lands before the snapshot. The wave succeeds and `apply-complete` writes only to the
    bound check's id.

    Mutation: collect by name only -- the newer check's id is written `success`, red."""
    waves = {"wave0": [row("dns", "dev")]}
    extra = (
        {"plan_run": "8", "plan_sha256": None, "behind": True}
        if newer == "behind"
        else {"plan_run": "8"}
    )
    runs = [check("apply / dev / dns", 1), check("apply / dev / dns", 2, **extra)]
    by_wave = apply_snapshot.by_wave(waves, apply_snapshot.snapshot(waves, runs, APP_ID))
    rows, _ = apply_complete.classify(by_wave, {"wave0": "success"}, JOBS, "900")
    writes = [w for r in rows for w in apply_complete.writes(r, "acme/demo")]
    assert [w["id"] for w in writes] == [1]


def test_an_older_plans_unfinished_duplicate_is_collected_and_completed():
    """Run 6's unfinished check of the cell is superseded by this apply of run 7, so it is
    collected beside run 7's and completed with it; run 8's check, run 6's behind check and run
    6's check of another pull request are not collected.

    Mutation: collect none of the older checks (`_older` answers False) -- id 1 is left, red.
    Mutation: collect a newer run's check too (`!=` for `<`) -- id 3 is collected, red.
    Mutation: drop the `pr` equality -- id 5 is collected, red."""
    waves = {"wave0": [row("dns", "dev")]}
    behind = {"plan_run": "6", "plan_sha256": None, "behind": True}
    runs = [
        check("apply / dev / dns", 1, plan_run="6", plan_sha256="f" * 64),
        check("apply / dev / dns", 2),
        check("apply / dev / dns", 3, plan_run="8"),
        check("apply / dev / dns", 4, **behind),
        check("apply / dev / dns", 5, plan_run="6", plan_sha256="f" * 64, pr=OTHER_PR),
    ]
    snap = apply_snapshot.snapshot(waves, runs, APP_ID)
    assert snap == {"apply / dev / dns": [1, 2]}
    rows, _ = apply_complete.classify(
        apply_snapshot.by_wave(waves, snap), {"wave0": "success"}, JOBS, "900"
    )
    writes = [w for r in rows for w in apply_complete.writes(r, "acme/demo")]
    assert [w["id"] for w in writes] == [1, 2]


@pytest.mark.parametrize(
    ("own_pr", "older_pr"),
    [(None, None), ("", ""), (PR, None)],
    ids=["null", "empty", "older-null"],
)
def test_an_older_check_without_the_same_valid_pr_is_not_collected(own_pr, older_pr):
    """The matching check's `pr` must be a pull-request number and the older check's equal to
    it.

    Mutation: test `is not None` instead of `isinstance` and `is_pr_number` -- empty, red.
    Mutation: drop that validation -- null, red.
    Mutation: drop the `pr` equality -- older-null, red."""
    waves = {"wave0": [row("dns", "dev")]}
    runs = [
        check("apply / dev / dns", 1, plan_run="6", pr=older_pr),
        check("apply / dev / dns", 2, pr=own_pr),
    ]
    assert apply_snapshot.snapshot(waves, runs, APP_ID) == {"apply / dev / dns": [2]}


def test_each_unfinished_check_is_decoded_once(monkeypatch):
    """Mutation: decode again when collecting (`_records(decoded(c) or {}, plan)` over the
    unfinished checks) -- ids 2 and 4 are decoded twice and id 1 three times, red."""
    seen = []
    real = apply_snapshot.decoded

    def counting(run):
        seen.append(run["id"])
        return real(run)

    monkeypatch.setattr(apply_snapshot, "decoded", counting)
    runs = [
        check("apply / dev-eu / stacks/dns", 1, plan_run="6"),
        check("apply / dev-eu / stacks/dns", 2),
        {**check("apply / dev-eu / stacks/dns", 3), "status": "completed"},
        check("apply / dev-eu / stacks/app", 4),
    ]
    assert apply_snapshot.snapshot(WAVES, runs, APP_ID) == {
        "apply / dev-eu / stacks/dns": [1, 2],
        "apply / dev-eu / stacks/app": [4],
    }
    assert seen == [1, 2, 4]


@pytest.mark.parametrize(
    ("plan_run", "other_run", "expected"),
    [("10", "9", [1, 2]), ("9", "10", [1])],
    ids=["run-10", "run-9"],
)
def test_plan_runs_compare_as_numbers(plan_run, other_run, expected):
    """Mutation: compare the `_plan_run` strings without `int` -- both cases red."""
    waves = {"wave0": [{**row("dns", "dev"), "plan_run_id": plan_run}]}
    runs = [
        check("apply / dev / dns", 1, plan_run=plan_run),
        check("apply / dev / dns", 2, plan_run=other_run),
    ]
    assert apply_snapshot.snapshot(waves, runs, APP_ID) == {"apply / dev / dns": expected}
