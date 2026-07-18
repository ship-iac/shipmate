import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

_D = pathlib.Path(__file__).resolve().parents[1]


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ad = _load("apply-detect")


def test_workset_matches_plan_artifacts_for_env():
    names = [
        "plan-stacks-app-dev-eu",
        "plan-stacks-dns-dev-eu",
        "plan-stacks-app-dev-us",  # other env — excluded
        "cell-summary-stacks-app-dev-eu",
    ]  # not a plan artifact — excluded
    graph_paths = ["stacks/app", "stacks/dns", "stacks/platform"]
    cells = ad.workset_from_artifacts(names, "dev-eu", graph_paths)
    stacks = sorted(c["stack"] for c in cells)
    assert stacks == ["stacks/app", "stacks/dns"]
    assert all(c["environment"] == "dev-eu" for c in cells)


def test_workset_ignores_slug_with_wrong_env_suffix():
    names = ["plan-stacks-app-dev-eu-apply"]  # not the plain env
    cells = ad.workset_from_artifacts(names, "dev-eu", ["stacks/app"])
    assert cells == []


def test_workset_env_suffix_no_cross_match():
    # env "eu" must NOT match "dev-eu" artifacts (forward-construct, no reverse split)
    names = ["plan-stacks-app-dev-eu"]
    assert ad.workset_from_artifacts(names, "eu", ["stacks/app"]) == []


def test_workset_slug_collision_fails_loud():
    # two distinct paths slug identically -> ambiguous artifact match -> fail loud
    names = ["plan-stacks-a-b-dev-eu"]
    with pytest.raises(SystemExit):
        ad.workset_from_artifacts(names, "dev-eu", ["stacks/a/b", "stacks-a/b"])


def test_filter_pending_drops_completed():
    cells = [
        {"stack": "stacks/app", "environment": "dev-eu"},
        {"stack": "stacks/dns", "environment": "dev-eu"},
    ]
    completed = {"apply / dev-eu / stacks/dns"}
    kept = ad.filter_pending(cells, completed)
    assert [c["stack"] for c in kept] == ["stacks/app"]


def test_completed_failure_apply_stays_pending():
    # "completed" status with a failing conclusion must not count as done —
    # apply-detect must share apply-gate's success/neutral predicate.
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    checks = [
        {
            "name": "apply / dev-eu / stacks/app",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-07-18T10:00:00Z",
            "id": 1,
        },
    ]
    done = ad.ag.done_names(checks)
    assert ad.filter_pending(cells, done) == cells


def test_verify_plan_run_rejects_mismatched_head_sha(monkeypatch):
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "aaa",
            "conclusion": "success",
            "path": ".github/workflows/preview.yml",
        },
    )
    with pytest.raises(SystemExit):
        ad.verify_plan_run("o/r", "123", "bbb")


def test_verify_plan_run_rejects_non_success_conclusion(monkeypatch):
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "bbb",
            "conclusion": "failure",
            "path": ".github/workflows/preview.yml",
        },
    )
    with pytest.raises(SystemExit):
        ad.verify_plan_run("o/r", "123", "bbb")


def test_verify_plan_run_rejects_wrong_workflow_path(monkeypatch):
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "bbb",
            "conclusion": "success",
            "path": ".github/workflows/deploy.yml",
        },
    )
    with pytest.raises(SystemExit):
        ad.verify_plan_run("o/r", "123", "bbb")


def test_verify_plan_run_rejects_lookalike_workflow_name(monkeypatch):
    # "evil-preview.yml" / "not-preview.yml" end with the substring "preview.yml"
    # but are not THE preview.yml at the repo root of workflows -- endswith on
    # the raw string is bypassable by a same-named-suffix workflow.
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "bbb",
            "conclusion": "success",
            "path": ".github/workflows/evil-preview.yml",
        },
    )
    with pytest.raises(SystemExit):
        ad.verify_plan_run("o/r", "123", "bbb")


def test_verify_plan_run_passes_when_all_match(monkeypatch):
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "bbb",
            "conclusion": "success",
            "path": ".github/workflows/preview.yml",
        },
    )
    ad.verify_plan_run("o/r", "123", "bbb")  # must not raise


def test_duplicate_run_newer_queued_stays_pending():
    # An old completed+success run must not mask a newer queued run of the same
    # check name — the latest run per name governs.
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    checks = [
        {
            "name": "apply / dev-eu / stacks/app",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-18T10:00:00Z",
            "id": 1,
        },
        {
            "name": "apply / dev-eu / stacks/app",
            "status": "queued",
            "conclusion": None,
            "started_at": "2026-07-18T11:00:00Z",
            "id": 2,
        },
    ]
    done = ad.ag.done_names(checks)
    assert ad.filter_pending(cells, done) == cells
