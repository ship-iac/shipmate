import pytest
from _loader import load_script

eo = load_script("env-order")


def _boom(*args, **kwargs):
    raise AssertionError(f"env-order ran a subprocess: {args}")


def test_the_ordering_fields_come_from_the_table_and_nothing_is_evaluated(monkeypatch):
    """env-order's own Terramate evaluation is gone: both fields come from the mapping the
    operation already loaded. Both subprocess seams raise, so a reader that fetched anything
    itself reds here instead of answering; the returned values are populated, so a reader
    that stopped reading the table and returned its empty default reds too.
    """
    monkeypatch.setattr(eo.bm, "_run", _boom)
    monkeypatch.setattr(eo.bm.ec, "_run", _boom)
    table = {"layout": "folder", "env_order": {"prod": ["dev-eu"]}, "explicit_envs": ["prod"]}
    assert eo.read_env_order(table) == {"prod": ["dev-eu"]}
    assert eo.read_explicit_envs(table) == ["prod"]


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
    # dev-eu has no changed cells this run, but still constrains dev-us.
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


def test_waves_by_env_level_refuses_an_env_beyond_the_cap():
    """The guard is inside the shared function, so no caller can omit it and drop an
    over-deep env's cells out of every `range(MAX_ENV_LEVELS)` bucket.

    Mutation: delete `guard_max_env_levels(levels)` from `waves_by_env_level` -- the call
    returns MAX_ENV_LEVELS empty wave dicts instead of raising.
    """
    with pytest.raises(SystemExit, match="env order spans"):
        eo.waves_by_env_level(
            [{"stack": "stacks/app", "environment": "prod"}],
            {"stacks/app": set()},
            {"prod": eo.MAX_ENV_LEVELS},
        )


def test_read_env_order_absent_key_is_empty():
    assert eo.read_env_order({"layout": "folder"}) == {}


def test_env_levels_rejects_string_predecessor():
    # HCL author typo, "dev-eu" instead of ["dev-eu"]: it must not silently iterate the string
    # character by character.
    with pytest.raises(SystemExit):
        eo.env_levels({"dev-us": "dev-eu"}, ["dev-eu", "dev-us"])


def test_env_levels_rejects_non_dict_order():
    with pytest.raises(SystemExit):
        eo.env_levels(["dev-us", "dev-eu"], ["dev-eu", "dev-us"])


def test_env_levels_rejects_non_str_predecessor_element():
    with pytest.raises(SystemExit):
        eo.env_levels({"dev-us": ["dev-eu", 123]}, ["dev-eu", "dev-us"])


def test_read_env_order_revalidates_the_field():
    """The docstring claims the field is re-validated rather than trusted, so a caller
    reaching here with an unvalidated mapping cannot build the graph from a bare string.
    Mutation: drop the `validate_env_order` call -- the string is returned as written."""
    with pytest.raises(SystemExit):
        eo.read_env_order({"env_order": {"dev-us": "dev-eu"}})


def test_waves_by_env_level_buckets_and_orders():
    # Env-level bucketing lives in env-order, shared by deploy-detect and apply-all-detect. It
    # buckets cells by their env's level, then stack-wave-orders within each level.
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
    # The shared GITHUB_OUTPUT writer must emit envlevelN_waves as JSON plus an envlevelN_empty
    # flag per level: 'false' for a level with any cell, 'true' for an empty one. Single-sourced,
    # so deploy-detect and apply-all-detect cannot drift apart on apply-env-level.yml's contract.
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


def test_read_explicit_envs_absent_key_is_empty():
    assert eo.read_explicit_envs({"layout": "folder"}) == []


def test_read_explicit_envs_revalidates_the_field():
    """The exclusion list is re-validated here for the same reason the ordering map is: a
    bare string would otherwise be iterated character by character into a set of exclusions
    nobody wrote. The message cases themselves are `test_env_config_schema.py`'s.
    Mutation: drop the `validate_explicit_envs` call -- the string is returned as written."""
    with pytest.raises(SystemExit):
        eo.read_explicit_envs({"explicit_envs": "prod"})


def test_blocked_envs_direct_predecessor():
    order = {"stage": ["dev"], "prod": ["stage"]}
    assert eo.blocked_envs(order, {"stage"}, {"dev", "prod"}) == {"prod"}


def test_blocked_envs_transitive():
    # prod -> stage -> dev, so dev being unavailable blocks prod through stage.
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
