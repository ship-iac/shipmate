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


def test_env_order_has_no_private_run_and_reuses_build_matrix():
    # env-order must not define its own subprocess wrapper (it swallowed
    # stderr on failure) -- it delegates to build-matrix's _run instead,
    # which surfaces stderr and raises `::error::` on nonzero exit.
    assert not hasattr(eo, "_run")
    assert eo.bm._run.__module__ == "build_matrix"


def test_read_env_order_default_run_is_bm_run(monkeypatch):
    captured = {}
    monkeypatch.setattr(eo.bm, "_run", lambda args: captured.update(args=args) or "{}")
    assert eo.read_env_order() == {}
    assert captured["args"] == [
        "terramate",
        "experimental",
        "eval",
        "--as-json",
        "tm_try(global.shipmate.env_order, {})",
    ]


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


def test_waves_by_env_level_buckets_and_orders():
    # Env-level bucketing lives in env-order (shared by deploy-detect and
    # apply-all-detect); it buckets cells by their env's level, then
    # stack-wave-orders within each level.
    pending = [
        {"stack": "stacks/dns", "environment": "dev-eu"},
        {"stack": "stacks/app", "environment": "dev-eu"},
        {"stack": "stacks/dns", "environment": "dev-us"},
    ]
    deps = {"stacks/dns": set(), "stacks/app": {"stacks/dns"}}
    levels = {"dev-eu": 0, "dev-us": 1}
    out = eo.waves_by_env_level(pending, deps, levels)
    assert out[0]["wave0"] == [{"stack": "stacks/dns", "environment": "dev-eu"}]
    assert out[0]["wave1"] == [{"stack": "stacks/app", "environment": "dev-eu"}]
    assert out[1]["wave0"] == [{"stack": "stacks/dns", "environment": "dev-us"}]
    assert out[1]["wave1"] == []


def test_waves_by_env_level_backward_compat_single_level():
    pending = [
        {"stack": "stacks/dns", "environment": "dev-eu"},
        {"stack": "stacks/dns", "environment": "dev-us"},
    ]
    deps = {"stacks/dns": set()}
    levels = {"dev-eu": 0, "dev-us": 0}  # no env-order -> all level 0
    out = eo.waves_by_env_level(pending, deps, levels)
    assert sorted(out[0]["wave0"], key=str) == sorted(pending, key=str)
    assert out[1]["wave0"] == [] and out[2]["wave0"] == [] and out[3]["wave0"] == []


def test_write_env_level_waves_emits_waves_and_empty_flags(tmp_path):
    # The shared GITHUB_OUTPUT writer must emit envlevelN_waves (JSON) plus an
    # envlevelN_empty flag per level: 'false' for a level with any cell,
    # 'true' for an empty one. Single-sourced so deploy-detect and
    # apply-all-detect cannot drift apart on the shared apply-env-level.yml
    # output contract.
    cell = {"stack": "s", "environment": "dev-eu"}
    per_level = [
        {f"wave{i}": ([cell] if i == 0 else []) for i in range(8)},
        {f"wave{i}": [] for i in range(8)},
    ]
    out = tmp_path / "gh_output"
    with out.open("a", encoding="utf-8") as fh:
        eo.write_env_level_waves(fh, per_level)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert 'envlevel0_waves={"wave0": [{"stack": "s", "environment": "dev-eu"}]' in lines[0]
    assert "envlevel0_empty=false" in lines
    assert "envlevel1_empty=true" in lines


def test_read_explicit_envs_default_invocation(monkeypatch):
    captured = {}
    monkeypatch.setattr(eo.bm, "_run", lambda args: captured.update(args=args) or "[]")
    assert eo.read_explicit_envs() == []
    assert captured["args"] == [
        "terramate",
        "experimental",
        "eval",
        "--as-json",
        "tm_try(global.shipmate.explicit_envs, [])",
    ]


def test_read_explicit_envs_parses_list():
    assert eo.read_explicit_envs(run=lambda args: '["prod"]') == ["prod"]


def test_read_explicit_envs_absent_is_empty():
    assert eo.read_explicit_envs(run=lambda args: "[]") == []


def test_read_explicit_envs_rejects_bare_string():
    # HCL author typo: "prod" instead of ["prod"] -- must not silently iterate
    # the string char-by-char (mirror the env_order validation posture).
    with pytest.raises(SystemExit):
        eo.read_explicit_envs(run=lambda args: '"prod"')


def test_read_explicit_envs_rejects_dict():
    with pytest.raises(SystemExit):
        eo.read_explicit_envs(run=lambda args: '{"prod": true}')


def test_read_explicit_envs_rejects_non_str_element():
    with pytest.raises(SystemExit):
        eo.read_explicit_envs(run=lambda args: '["prod", 123]')


def test_blocked_envs_direct_predecessor():
    order = {"stage": ["dev"], "prod": ["stage"]}
    assert eo.blocked_envs(order, {"stage"}, {"dev", "prod"}) == {"prod"}


def test_blocked_envs_transitive():
    # prod -> stage -> dev; dev unavailable blocks prod through stage.
    order = {"stage": ["dev"], "prod": ["stage"]}
    assert eo.blocked_envs(order, {"dev"}, {"stage", "prod"}) == {"stage", "prod"}


def test_blocked_envs_unrelated_env_not_blocked():
    # sbx does not list stage anywhere in its predecessor chain.
    order = {"prod": ["stage"], "sbx": ["dev"]}
    assert eo.blocked_envs(order, {"stage"}, {"dev", "sbx", "prod"}) == {"prod"}


def test_blocked_envs_nothing_unavailable():
    assert eo.blocked_envs({"prod": ["stage"]}, set(), {"stage", "prod"}) == set()


def test_blocked_envs_validates_order_shape():
    with pytest.raises(SystemExit):
        eo.blocked_envs({"prod": "stage"}, {"stage"}, {"prod"})
