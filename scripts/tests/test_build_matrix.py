import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

# build-matrix has no file extension, so spec_from_file_location can't infer a
# loader from the suffix (it returns None on every platform, not just Windows).
# Passing SourceFileLoader explicitly sidesteps the suffix guess entirely.
_script_path = pathlib.Path(__file__).resolve().parents[1] / "build-matrix"
_loader = SourceFileLoader("build_matrix", str(_script_path))
_spec = importlib.util.spec_from_loader("build_matrix", _loader)
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)


def test_multi_env_stack_yields_one_cell_per_env():
    cells = bm.build_matrix(
        envs=["dev-eu", "dev-us"],
        stacks_by_env={"dev-eu": ["stacks/app"], "dev-us": ["stacks/app", "stacks/dns"]},
        tags_by_stack={
            "stacks/app": ["env/dev-eu", "env/dev-us"],
            "stacks/dns": ["env/dev-us", "workload/net"],
        },
    )
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/app", "environment": "dev-us", "workload": ""},
        {"stack": "stacks/dns", "environment": "dev-us", "workload": "net"},
    ]


def test_empty_when_no_changed_stacks():
    assert bm.build_matrix(["dev-eu"], {"dev-eu": []}, {}) == []


def test_raises_above_256_cells():
    stacks = [f"stacks/s{i}" for i in range(257)]
    with pytest.raises(bm.MatrixTooLarge):
        bm.build_matrix(["dev-eu"], {"dev-eu": stacks}, {s: ["env/dev-eu"] for s in stacks})


def test_list_stacks_changed_uses_changed_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(bm, "_run", lambda args: captured.update(args=args) or "stacks/a\n")
    assert bm._list_stacks(all_stacks=False, base="deadbeef") == ["stacks/a"]
    assert captured["args"] == ["terramate", "list", "--changed", "-B", "deadbeef"]


def test_list_stacks_all_omits_changed_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        bm, "_run", lambda args: captured.update(args=args) or "stacks/a\nstacks/b\n"
    )
    assert bm._list_stacks(all_stacks=True, base="") == ["stacks/a", "stacks/b"]
    assert captured["args"] == ["terramate", "list"]


def test_compute_cells_fans_out_and_guards_untagged(monkeypatch):
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["env/dev-eu", "env/dev-us", "workload/app"])
    cells = bm.compute_cells(all_stacks=True)
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
        {"stack": "stacks/app", "environment": "dev-us", "workload": "app"},
    ]
