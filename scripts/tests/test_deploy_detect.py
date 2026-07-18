import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_p = pathlib.Path(__file__).resolve().parents[1] / "deploy-detect"
_loader = SourceFileLoader("deploy_detect", str(_p))
_spec = importlib.util.spec_from_loader("deploy_detect", _loader)
dd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dd)


def test_filter_pending_drops_completed_applies():
    cells = [
        {"stack": "stacks/dns", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
    ]
    completed = {"apply / dev-eu / stacks/dns"}  # applied pre-merge -> skip
    assert dd.filter_pending(cells, completed) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
    ]


def test_filter_pending_keeps_all_when_none_completed():
    cells = [{"stack": "stacks/dns", "environment": "dev-eu", "workload": ""}]
    assert dd.filter_pending(cells, set()) == cells


def test_waves_by_env_level_buckets_and_orders():
    pending = [
        {"stack": "stacks/dns", "environment": "dev-eu"},
        {"stack": "stacks/app", "environment": "dev-eu"},
        {"stack": "stacks/dns", "environment": "dev-us"},
    ]
    deps = {"stacks/dns": set(), "stacks/app": {"stacks/dns"}}
    levels = {"dev-eu": 0, "dev-us": 1}
    out = dd.waves_by_env_level(pending, deps, levels)
    # env-level 0 = dev-eu: dns in wave0, app in wave1
    assert out[0]["wave0"] == [{"stack": "stacks/dns", "environment": "dev-eu"}]
    assert out[0]["wave1"] == [{"stack": "stacks/app", "environment": "dev-eu"}]
    # env-level 1 = dev-us: dns in wave0
    assert out[1]["wave0"] == [{"stack": "stacks/dns", "environment": "dev-us"}]
    assert out[1]["wave1"] == []


def test_waves_by_env_level_backward_compat_single_level():
    pending = [
        {"stack": "stacks/dns", "environment": "dev-eu"},
        {"stack": "stacks/dns", "environment": "dev-us"},
    ]
    deps = {"stacks/dns": set()}
    levels = {"dev-eu": 0, "dev-us": 0}  # no env-order -> all level 0
    out = dd.waves_by_env_level(pending, deps, levels)
    assert sorted(out[0]["wave0"], key=str) == sorted(pending, key=str)
    assert out[1]["wave0"] == [] and out[2]["wave0"] == [] and out[3]["wave0"] == []


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
