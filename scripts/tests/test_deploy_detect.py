import importlib.util, pathlib
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
    completed = {"apply / dev-eu / stacks/dns"}   # applied pre-merge -> skip
    assert dd.filter_pending(cells, completed) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
    ]


def test_filter_pending_keeps_all_when_none_completed():
    cells = [{"stack": "stacks/dns", "environment": "dev-eu", "workload": ""}]
    assert dd.filter_pending(cells, set()) == cells
