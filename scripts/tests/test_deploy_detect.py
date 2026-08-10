from _detect_fixtures import check_run as _check
from _detect_fixtures import completed_names
from _loader import load_script

dd = load_script("deploy-detect")


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


def test_foreign_app_completed_check_stays_pending(monkeypatch):
    # A completed+success check authored by another identity (github-actions,
    # app id 15368) must not count as done for the merge-deploy queue.
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    done = _completed(monkeypatch, [_check(app={"id": 15368})])
    assert dd.filter_pending(cells, done) == cells


def test_main_emits_the_dag_shape_notice(monkeypatch, tmp_path, capsys):
    # The post-merge path is where a flat DAG applies the whole repository at
    # once, so this is the run that most needs the line. deploy-detect has its
    # own main(); importing apply-detect emits nothing on its own.
    deps = {"stacks/a": set(), "stacks/b": {"stacks/a"}}
    monkeypatch.setattr(dd, "_merged_head", lambda repo, merge_sha: "headsha")
    monkeypatch.setattr(dd, "_gh_json", lambda path: {"workflow_runs": []})
    monkeypatch.setattr(
        dd.bm,
        "compute_cells",
        lambda all_stacks, base: [{"stack": "stacks/a", "environment": "dev-eu", "workload": ""}],
    )
    monkeypatch.setattr(dd.ad, "completed_apply_names", lambda repo, head: set())
    monkeypatch.setattr(dd.ad, "run_graph_deps", lambda: deps)
    monkeypatch.setattr(dd.eo, "read_env_order", lambda: {})
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/iac")
    monkeypatch.setenv("GITHUB_SHA", "merge123")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    dd.main()
    assert (
        "::notice::2 stacks, 1 after edges, 2 wave levels; 1 stacks would apply concurrently"
        in capsys.readouterr().out.splitlines()
    )
