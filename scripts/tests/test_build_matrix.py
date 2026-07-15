import importlib.util
import pathlib
import pytest
from importlib.machinery import SourceFileLoader

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
