import json

import pytest
from _detect_fixtures import APP_ID, _apply_check, completed_names
from _detect_fixtures import check_run as _check
from _loader import load_script

dd = load_script("deploy-detect")

HEAD = "a" * 40
CHECK_RUNS_URL = f"repos/acme/iac/commits/{HEAD}/check-runs?filter=all&per_page=100"


def _completed(monkeypatch, checks, **kw):
    """deploy-detect's "already applied" set, through the query main() calls.

    That main() *does* call it is a separate, structural claim -- pinned by
    test_detect_app_scoping, not by this helper."""
    return completed_names(dd.ad, monkeypatch, checks, **kw)


def test_filter_pending_drops_completed_applies():
    cells = [
        {"stack": "stacks/dns", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
    ]
    completed = {"apply / stacks/dns / dev-eu"}  # applied pre-merge -> skip
    assert dd.filter_pending(cells, completed) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
    ]


def test_filter_pending_keeps_all_when_none_completed():
    cells = [{"stack": "stacks/dns", "environment": "dev-eu", "workload": ""}]
    assert dd.filter_pending(cells, set()) == cells


def test_completed_failure_apply_stays_pending(monkeypatch):
    # A "completed" status with a failing conclusion must not count as done —
    # deploy-detect must share apply-gate's success/neutral predicate, not just
    # check status=="completed".
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    done = _completed(monkeypatch, [_check(conclusion="failure")])
    assert dd.filter_pending(cells, done) == cells


def test_duplicate_run_newer_queued_stays_pending(monkeypatch):
    # An old completed+success run must not mask a newer queued run of the same
    # check name (re-created check) — the latest run per name governs.
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    done = _completed(
        monkeypatch,
        [
            _check(),
            _check(status="queued", conclusion=None, started_at="2026-07-18T11:00:00Z", id=2),
        ],
    )
    assert dd.filter_pending(cells, done) == cells


def test_merged_head_exact_match_wins(monkeypatch):
    # Exact merge_commit_sha match should still be picked over everything else.
    pulls = [
        {
            "merge_commit_sha": "other",
            "merged_at": "2024-01-01T00:00:00Z",
            "head": {"sha": "wrong"},
        },
        {
            "merge_commit_sha": "merge123",
            "merged_at": "2024-01-02T00:00:00Z",
            "head": {"sha": "right"},
        },
    ]
    monkeypatch.setattr(dd, "_gh_json", lambda path: pulls)
    assert dd._merged_head("o/r", "merge123") == "right"


def test_merged_head_only_open_pr_falls_back_to_merge_sha(monkeypatch):
    # Sole candidate is an OPEN (unmerged) PR that merely contains the pushed
    # commit -> must NOT deploy that PR's plans; fall back to the merge SHA.
    pulls = [
        {"merge_commit_sha": "unrelated", "merged_at": None, "head": {"sha": "open-pr-head"}},
    ]
    monkeypatch.setattr(dd, "_gh_json", lambda path: pulls)
    assert dd._merged_head("o/r", "merge123") == "merge123"


def test_merged_head_mix_of_merged_and_open_picks_merged(monkeypatch):
    # No pull matches merge_commit_sha exactly (e.g. squash merge), but one
    # candidate is merged and one is still open -> pick the merged one.
    pulls = [
        {"merge_commit_sha": "unrelated1", "merged_at": None, "head": {"sha": "open-pr-head"}},
        {
            "merge_commit_sha": "unrelated2",
            "merged_at": "2024-01-01T00:00:00Z",
            "head": {"sha": "merged-pr-head"},
        },
    ]
    monkeypatch.setattr(dd, "_gh_json", lambda path: pulls)
    assert dd._merged_head("o/r", "merge123") == "merged-pr-head"


def test_merged_head_no_pulls_returns_merge_sha(monkeypatch):
    monkeypatch.setattr(dd, "_gh_json", lambda path: [])
    assert dd._merged_head("o/r", "merge123", _attempts=1, _sleep=0) == "merge123"


def test_merged_head_retries_until_pulls_populated(monkeypatch):
    # commits/{sha}/pulls can transiently return [] for a few seconds after the
    # push (association indexing lag) -- must retry rather than giving up on
    # the first empty response.
    responses = [
        [],
        [],
        [
            {
                "merge_commit_sha": "merge123",
                "merged_at": "2024-01-01T00:00:00Z",
                "head": {"sha": "right"},
            }
        ],
    ]
    calls = {"n": 0}

    def fake_gh_json(path):
        result = responses[calls["n"]]
        calls["n"] += 1
        return result

    slept = []
    monkeypatch.setattr(dd, "_gh_json", fake_gh_json)
    monkeypatch.setattr(dd.time, "sleep", lambda s: slept.append(s))
    assert dd._merged_head("o/r", "merge123", _attempts=5, _sleep=2) == "right"
    assert calls["n"] == 3
    assert slept == [2, 2]  # slept before the 2nd and 3rd attempts only


def test_merged_head_gives_up_after_attempts_exhausted_all_empty(monkeypatch):
    # If every attempt returns [] the retry loop must stop at _attempts and
    # fall back to the merge SHA, not loop forever or raise.
    calls = {"n": 0}

    def fake_gh_json(path):
        calls["n"] += 1
        return []

    monkeypatch.setattr(dd, "_gh_json", fake_gh_json)
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)
    assert dd._merged_head("o/r", "merge123", _attempts=3, _sleep=0) == "merge123"
    assert calls["n"] == 3


def _cell(stack, env="dev-eu"):
    return {"stack": stack, "environment": env, "workload_var": ""}


def _run_main(tmp_path, monkeypatch, *, cells, checks, urls=None, deps=None, order=None):
    """main() over the merged pull request's head, every GitHub and Terramate call
    stubbed. `_merged_head` is stubbed rather than fed, so the only `gh api` paths
    collected into `urls` are the ones the work set itself asks for.

    Returns the parsed GITHUB_OUTPUT."""
    out = tmp_path / "out.txt"
    for k, v in {
        "GITHUB_REPOSITORY": "acme/iac",
        "GITHUB_SHA": "merge123",
        "GITHUB_OUTPUT": str(out),
        "SHIPMATE_APP_ID": APP_ID,
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SHIPMATE_BASE_SHA", raising=False)
    jsonl = "\n".join(json.dumps(c) for c in checks)

    def _run(args):
        # Every terramate call must be stubbed -- CI installs uv alone, so a real
        # invocation passes on a developer machine and fails there.
        assert args[0] == "gh", args
        if urls is not None:
            urls.append(args[-1])
        return jsonl

    monkeypatch.setattr(dd, "_merged_head", lambda repo, merge_sha: HEAD)
    monkeypatch.setattr(dd.bm, "compute_cells", lambda all_stacks, base: cells)
    # deploy-detect and the apply-detect it loads hold separate build-matrix
    # instances; the check-run listing is fetched through apply-detect's. Both are
    # stubbed so a `gh api` call from either module lands in `urls`.
    monkeypatch.setattr(dd.ad.bm, "_run", _run)
    monkeypatch.setattr(dd.bm, "_run", _run)
    monkeypatch.setattr(dd.ad, "run_graph_deps", lambda: deps or {c["stack"]: set() for c in cells})
    monkeypatch.setattr(dd.eo, "read_env_order", lambda: dict(order or {}))
    dd.main()
    return dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())


def _wave_cells(parsed):
    return [
        c
        for lvl in range(dd.eo.MAX_ENV_LEVELS)
        for w in [json.loads(parsed[f"envlevel{lvl}_waves"])]
        for i in range(dd.wv.MAX_WAVES)
        for c in w[f"wave{i}"]
    ]


def test_main_reads_the_head_listing_once_and_makes_no_plan_run_lookup(tmp_path, monkeypatch):
    # The post-merge path no longer resolves a plan run from the plan workflow's
    # runs, and it reads the head's check-run listing exactly once: two reads of
    # one listing disagree when a re-plan lands between them, re-opening a name
    # the first read saw completed while the cell still carries the first read's
    # record. Whole-list comparison against a hand-written constant, so a
    # reinstated `actions/workflows/plan.yml/runs` lookup AND a second fetch of
    # the listing each redden this one assertion.
    urls = []
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app")],
        checks=[_apply_check("stacks/app")],
        urls=urls,
    )
    assert len(_wave_cells(parsed)) == 1  # not vacuous
    assert urls == [CHECK_RUNS_URL]


def test_main_gives_each_cell_the_plan_run_its_own_check_names(tmp_path, monkeypatch):
    # The recovery shape: dev-us was re-planned by a later run while dev-eu is
    # still named by the first. Each applies from the run that planned it, so one
    # shared id -- which is all the deleted lookup could give -- is not enough.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
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
    # The loud refusal this path most needs: nobody is watching a post-merge run,
    # so a default would apply a cell from nowhere and a skip would leave the
    # check pending with nothing said. The message names the cell to re-plan.
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
            checks=[
                _apply_check("stacks/app", "dev-eu", plan_run="42"),
                _check(
                    name="apply / stacks/app / dev-us",
                    status="queued",
                    conclusion=None,
                    external_id="b" * 64,
                ),
            ],
        )
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan run recorded for apply / stacks/app / dev-us — the "
        "apply check names no plan run to apply from — most likely a check written "
        "before this engine version, and post-merge possibly no apply check for that "
        "cell at all. Re-plan these stacks on their pull request, then apply again; if "
        "that pull request has already merged, a new pull request touching them plans "
        "and applies them afresh."
    )


def test_main_lets_a_record_less_completed_check_through(tmp_path, monkeypatch):
    # The upgrade shape: dev-us was applied pre-merge by an older engine version,
    # so its completed check carries a legacy bare-hex record. The attachment runs
    # AFTER the pending filter, or every pull request merged across the upgrade
    # refuses its whole deploy over a cell that is already applied.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="42"),
            _check(name="apply / stacks/app / dev-us", external_id="b" * 64),
        ],
    )
    assert [c["environment"] for c in _wave_cells(parsed)] == ["dev-eu"]


def test_main_notice_reports_the_no_op_evidence(tmp_path, monkeypatch, capsys):
    # A no-op post-merge deploy is indistinguishable from a broken work set
    # without these counts, and acceptance reads them off this one line. Whole
    # line against a hand-written constant, so a dropped field reddens here.
    _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/dns", "dev-eu")],
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="42"),
            _check(name="apply / stacks/dns / dev-eu"),
        ],
    )
    assert (
        f"::notice title=deploy-detect::base= head={HEAD} cells=2 completed=1 pending=1 "
        "envlevels=[1, 0, 0, 0] empty=False" in capsys.readouterr().out.splitlines()
    )


def test_a_forged_completed_check_does_not_mark_a_cell_applied(tmp_path, monkeypatch):
    # A completed+success check of the same name from another identity
    # (github-actions, app id 15368) must not count the cell as applied. It is
    # also the NEWER run of that name, so only the App filter keeps the cell in --
    # and main() is what feeds that filter its app id, so this is the behavioural
    # pin on the threading test_detect_app_scoping can only see structurally.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app")],
        checks=[
            _apply_check("stacks/app"),
            _check(name="apply / stacks/app / dev-eu", id=2, app={"id": 15368}),
        ],
    )
    assert [c["stack"] for c in _wave_cells(parsed)] == ["stacks/app"]


def test_main_emits_the_dag_shape_notice(tmp_path, monkeypatch, capsys):
    # The post-merge path is where a flat DAG applies the whole repository at
    # once, so this is the run that most needs the line. deploy-detect has its
    # own main(); importing apply-detect emits nothing on its own.
    _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/a")],
        checks=[_apply_check("stacks/a")],
        deps={"stacks/a": set(), "stacks/b": {"stacks/a"}},
    )
    assert (
        "::notice::2 stacks, 1 after edges, 2 wave levels; 1 stacks would apply concurrently"
        in capsys.readouterr().out.splitlines()
    )
