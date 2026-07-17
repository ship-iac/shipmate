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
