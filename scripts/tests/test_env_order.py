import subprocess

import pytest
from _loader import load_script

eo = load_script("env-order")


def _boom(*args, **kwargs):
    raise AssertionError(f"env-order ran a subprocess: {args}")


def test_the_level_computation_reads_nothing(monkeypatch):
    """env-order's own Terramate evaluation is gone: the ordering map arrives as an argument
    from the mapping the operation already loaded. `subprocess.run` raises, which every
    reader in the engine reaches through env-config's `_run` wrapper; the repository is set
    so a reader gets as far as its first subprocess instead of a `KeyError`.

    Mutation: add `_load("env-config").read_table()` to `env_levels` -- `_boom` fires.
    """
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(subprocess, "run", _boom)
    assert eo.env_levels({"prod": ["dev-eu"]}, ["dev-eu", "prod"]) == {"dev-eu": 0, "prod": 1}


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


def test_waves_by_env_level_places_an_env_at_the_last_level_within_the_cap():
    """Mutation: `lv >= MAX_ENV_LEVELS - 1` in `waves_by_env_level`'s cap check -- the
    deepest permitted level refuses."""
    cell = {"stack": "stacks/app", "environment": "prod"}
    out = eo.waves_by_env_level([cell], {"stacks/app": set()}, {"prod": eo.MAX_ENV_LEVELS - 1})
    assert out[eo.MAX_ENV_LEVELS - 1]["wave0"] == [cell]


def test_waves_by_env_level_refuses_an_env_beyond_the_cap():
    """The guard is inside the shared function, so no caller can omit it and drop an
    over-deep env's cells out of every `range(MAX_ENV_LEVELS)` bucket.

    Mutation: delete the `if over:` refusal from `waves_by_env_level` -- the call returns
    MAX_ENV_LEVELS empty wave dicts instead of raising.
    """
    with pytest.raises(SystemExit, match="env order spans"):
        eo.waves_by_env_level(
            [{"stack": "stacks/app", "environment": "prod"}],
            {"stacks/app": set()},
            {"prod": eo.MAX_ENV_LEVELS},
        )


def test_waves_by_env_level_refuses_a_cyclic_stack_graph():
    """deploy-detect and apply-all-detect sort the stack graph only here.

    Mutation: `wv.levels` for `wv.stack_levels` in `waves_by_env_level` -- a raw `CycleError`
    escapes instead of the `SystemExit`."""
    with pytest.raises(SystemExit) as exc:
        eo.waves_by_env_level(
            [{"stack": "stacks/a", "environment": "prod"}],
            {"stacks/a": {"stacks/b"}, "stacks/b": {"stacks/a"}},
            {"prod": 0},
        )
    assert str(exc.value).startswith("::error::dependency cycle in the Terramate stack run-graph")


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
