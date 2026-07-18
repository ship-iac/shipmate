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


eo = _load("env-order")


def test_linear_order():
    lv = eo.env_levels({"dev-us": ["dev-eu"]}, ["dev-eu", "dev-us"])
    assert lv == {"dev-eu": 0, "dev-us": 1}


def test_unlisted_is_level_zero():
    lv = eo.env_levels({"dev-us": ["dev-eu"]}, ["dev-eu", "dev-us", "sbx"])
    assert lv["sbx"] == 0


def test_diamond_partial_order():
    order = {"prod": ["dev-eu", "dev-us"]}
    lv = eo.env_levels(order, ["dev-eu", "dev-us", "prod"])
    assert lv["dev-eu"] == 0 and lv["dev-us"] == 0 and lv["prod"] == 1


def test_predecessor_absent_from_envs_still_orders():
    # dev-eu has no changed cells this run, but still constrains dev-us
    lv = eo.env_levels({"dev-us": ["dev-eu"]}, ["dev-us"])
    assert lv == {"dev-us": 1}


def test_empty_order_all_level_zero():
    lv = eo.env_levels({}, ["dev-eu", "dev-us"])
    assert lv == {"dev-eu": 0, "dev-us": 0}


def test_cycle_raises():
    from graphlib import CycleError

    with pytest.raises(CycleError):
        eo.env_levels({"a": ["b"], "b": ["a"]}, ["a", "b"])


def test_guard_max_env_levels_ok():
    eo.guard_max_env_levels({"a": 0, "b": 3})  # 4 levels 0..3, within cap


def test_guard_max_env_levels_exceeded():
    with pytest.raises(SystemExit):
        eo.guard_max_env_levels({"a": 4})


def test_read_env_order_parses_json(monkeypatch):
    eo_map = eo.read_env_order(run=lambda args: '{"dev-us":["dev-eu"]}')
    assert eo_map == {"dev-us": ["dev-eu"]}


def test_read_env_order_absent_is_empty():
    assert eo.read_env_order(run=lambda args: "{}") == {}


def test_env_levels_rejects_string_predecessor():
    # HCL author typo: "dev-eu" instead of ["dev-eu"] -- must not silently
    # iterate the string char-by-char.
    with pytest.raises(SystemExit):
        eo.env_levels({"dev-us": "dev-eu"}, ["dev-eu", "dev-us"])


def test_env_levels_rejects_non_dict_order():
    with pytest.raises(SystemExit):
        eo.env_levels(["dev-us", "dev-eu"], ["dev-eu", "dev-us"])


def test_env_levels_rejects_non_str_predecessor_element():
    with pytest.raises(SystemExit):
        eo.env_levels({"dev-us": ["dev-eu", 123]}, ["dev-eu", "dev-us"])


def test_read_env_order_rejects_string_predecessor():
    with pytest.raises(SystemExit):
        eo.read_env_order(run=lambda args: '{"dev-us":"dev-eu"}')


def test_read_env_order_rejects_non_dict_global():
    with pytest.raises(SystemExit):
        eo.read_env_order(run=lambda args: '["dev-us","dev-eu"]')


def test_read_env_order_rejects_non_str_predecessor_element():
    with pytest.raises(SystemExit):
        eo.read_env_order(run=lambda args: '{"dev-us":["dev-eu", 123]}')
