import subprocess

import pytest
from _loader import SCRIPTS as _dir
from _loader import load_script

w = load_script("waves")

FIXTURE = (_dir / "tests" / "fixtures" / "run-graph-stacks.dot").read_text()


def test_parse_dot_matches_fixture_dag():
    deps = w.parse_dot(FIXTURE)
    assert deps["stacks/platform"] == {"stacks/dns"}  # n4->n3
    assert deps["stacks/app"] == {"stacks/auth", "stacks/workers"}  # n2->n1, n5->n1
    assert deps["stacks/dns"] == set()
    assert deps["stacks/sandbox/box"] == set()  # isolated node, still present


ROOT_FIXTURE = (_dir / "tests" / "fixtures" / "run-graph-root-stack.dot").read_text()


def test_parse_dot_keys_the_root_stack_dot():
    """The fixture is `terramate experimental run-graph --label stack.dir` captured raw to a file
    (not a TTY) with Terramate 0.17.3 (`VERSIONS` pins 0.17.1), from a root stack `after =
    ["/child"]`. A stack `after` the root is a Terramate cycle, so the root takes the edge.
    Mutation: drop `or "."` from `parse_dot` (the root keys `""`)."""
    assert w.parse_dot(ROOT_FIXTURE) == {".": {"child"}, "child": set()}


def test_a_root_stack_cell_gets_a_wave():
    """`terramate list` and the tag metadata name the root stack `.`, so its cell must find that
    node. Mutation: drop `or "."` from `parse_dot` -> `assign_waves` refuses "stack '.' is not
    a node in the run-graph"."""
    cell = {"stack": ".", "environment": "dev-eu", "workload": ""}
    assert w.assign_waves(w.stack_levels(w.parse_dot(ROOT_FIXTURE)), [cell]) == [[], [cell]]


def test_levels_are_topological():
    deps = w.parse_dot(FIXTURE)
    lv = w.levels(deps)
    # level 0 is the roots: dns and the isolated sandbox/box, both dependency-free.
    assert "stacks/dns" in lv[0] and "stacks/sandbox/box" in lv[0]
    assert lv[1] == ["stacks/platform"]
    assert set(lv[2]) == {"stacks/auth", "stacks/workers"}
    assert lv[3] == ["stacks/app"]
    assert set(lv[4]) == {"stacks/tenant-a", "stacks/tenant-b"}


def test_assign_waves_preserves_transitive_order_with_empty_middle():
    # Only dns (level 0) and app (level 3) in the work set, so waves 1 and 2 are empty.
    deps = w.parse_dot(FIXTURE)
    lv = w.levels(deps)
    cells = [
        {"stack": "stacks/dns", "environment": "dev-us", "workload": "net"},
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
    ]
    waves = w.assign_waves(lv, cells)
    assert waves[0] == [{"stack": "stacks/dns", "environment": "dev-us", "workload": "net"}]
    assert waves[1] == [] and waves[2] == []
    assert waves[3] == [{"stack": "stacks/app", "environment": "dev-eu", "workload": "app"}]


def test_assign_waves_cross_env_edge_same_wave_index():
    # dns@dev-us must be an earlier wave than platform@dev-eu, across the cross-env edge.
    deps = w.parse_dot(FIXTURE)
    lv = w.levels(deps)
    cells = [
        {"stack": "stacks/platform", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/dns", "environment": "dev-us", "workload": ""},
    ]
    waves = w.assign_waves(lv, cells)
    assert waves[0][0]["stack"] == "stacks/dns"
    assert waves[1][0]["stack"] == "stacks/platform"


def test_assign_waves_raises_when_levels_empty_but_workset_nonempty():
    """An empty run-graph, having failed or returned nothing, with a non-empty work set fails
    loud on its first cell rather than silently dropping pending applies.

    Mutation: restore a separate `if not levels_:` refusal ahead of the node check -- its
    message differs from this one."""
    cells = [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/dns", "environment": "dev-eu", "workload": ""},
    ]
    with pytest.raises(SystemExit) as exc:
        w.assign_waves([], cells)
    assert str(exc.value) == (
        "::error::stack 'stacks/app' is not a node in the run-graph; cannot order it into a "
        "wave (regenerate run-graph or check the label)."
    )


def test_assign_waves_allows_empty_levels_with_empty_workset():
    assert w.assign_waves([], []) == []


def test_assign_waves_raises_when_stack_missing_from_graph():
    # A work-set stack that is not a node in the run-graph cannot be ordered, so fail loud
    # instead of raising KeyError or dropping it silently.
    deps = w.parse_dot(FIXTURE)
    lv = w.levels(deps)
    cells = [{"stack": "stacks/does-not-exist", "environment": "dev-eu", "workload": ""}]
    with pytest.raises(SystemExit) as exc_info:
        w.assign_waves(lv, cells)
    assert "stacks/does-not-exist" in str(exc_info.value)


def test_pad_waves_allows_exactly_max_waves():
    # Populated waves at indices 0..MAX_WAVES-1, 8 waves in total, are fine.
    waves = [[f"cell{i}"] for i in range(w.MAX_WAVES)]
    w.pad_waves(waves)  # must not raise


def test_pad_waves_raises_when_ninth_wave_populated():
    # A populated wave at index MAX_WAVES, the 9th, has no pre-declared wave{MAX_WAVES} job, so
    # it must fail loud.
    waves = [[f"cell{i}"] for i in range(w.MAX_WAVES)] + [["cell8"]]
    with pytest.raises(SystemExit):
        w.pad_waves(waves)


def test_pad_waves_ignores_empty_trailing_levels():
    # A deep full graph with only low-level cells in the work set is fine: empty trailing levels
    # beyond MAX_WAVES must not trip the guard.
    waves = [["cell0"]] + [[] for _ in range(w.MAX_WAVES + 3)]
    w.pad_waves(waves)  # must not raise


def _linear_chain_dot(n):
    """A dot fixture of n nodes in a straight chain s1->...->sn: n topological levels, one node
    per level."""
    lines = ["digraph  {"]
    lines += [f'\tn{i}[label="/stacks/s{i}"];' for i in range(1, n + 1)]
    lines += [f"\tn{i}->n{i + 1};" for i in range(1, n)]
    return "\n".join(lines + ["}"])


def test_env_level_waves_refuses_a_change_deeper_than_max_waves():
    """The guard fires through a real caller, rather than dropping the level-8 cells into a
    silent no-op apply.

    Mutation: delete the `if over:` refusal from `pad_waves`."""
    deps = w.parse_dot(_linear_chain_dot(w.MAX_WAVES + 1))
    deep = f"stacks/s{w.MAX_WAVES + 1}"
    pending = [{"stack": deep, "environment": "dev-eu"}]

    with pytest.raises(SystemExit) as exc:
        w.env_level_waves(pending, {}, deps)

    assert "dependency levels" in str(exc.value)


def test_stack_levels_refuses_a_cycle_naming_it():
    """Mutation: return `levels(deps)` from `stack_levels` without the `CycleError` handler -- the
    raw `CycleError` is not a `SystemExit`."""
    with pytest.raises(SystemExit) as exc:
        w.stack_levels({"stacks/a": {"stacks/b"}, "stacks/b": {"stacks/a"}})
    assert str(exc.value) == (
        "::error::dependency cycle in the Terramate stack run-graph: ('nodes are in a cycle', "
        "['stacks/a', 'stacks/b', 'stacks/a']). Two or more stacks order each other through "
        "`after`/`before`; break the loop in their stack configuration."
    )


def _boom(*args, **kwargs):
    raise AssertionError(f"waves ran a subprocess: {args}")


def test_the_level_computation_reads_nothing(monkeypatch):
    """The ordering map arrives as an argument from the mapping the operation already loaded.
    `subprocess.run` raises, which every reader in the engine reaches through env-config's
    `_run` wrapper; the repository is set so a reader gets as far as its first subprocess
    instead of a `KeyError`.

    Mutation: add `_load("env-config").read_table()` to `env_levels` -- `_boom` fires.
    """
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(subprocess, "run", _boom)
    assert w.env_levels({"prod": ["dev-eu"]}, ["dev-eu", "prod"]) == {"dev-eu": 0, "prod": 1}


def test_linear_order():
    lv = w.env_levels({"dev-us": ["dev-eu"]}, ["dev-eu", "dev-us"])
    assert lv == {"dev-eu": 0, "dev-us": 1}


def test_unlisted_is_level_zero():
    lv = w.env_levels({"dev-us": ["dev-eu"]}, ["dev-eu", "dev-us", "sbx"])
    assert lv["sbx"] == 0


def test_diamond_partial_order():
    order = {"prod": ["dev-eu", "dev-us"]}
    lv = w.env_levels(order, ["dev-eu", "dev-us", "prod"])
    assert lv["dev-eu"] == 0 and lv["dev-us"] == 0 and lv["prod"] == 1


def test_predecessor_absent_from_envs_still_orders():
    # dev-eu has no changed cells this run, but still constrains dev-us.
    lv = w.env_levels({"dev-us": ["dev-eu"]}, ["dev-us"])
    assert lv == {"dev-us": 1}


def test_empty_order_all_level_zero():
    lv = w.env_levels({}, ["dev-eu", "dev-us"])
    assert lv == {"dev-eu": 0, "dev-us": 0}


def _chain(depth):
    """An order placing `prod` at env-level `depth` through predecessors e0..e{depth-1}."""
    envs = [f"e{i}" for i in range(depth)] + ["prod"]
    return {envs[i]: [envs[i - 1]] for i in range(1, len(envs))}


_EMPTY = {f"wave{i}": [] for i in range(8)}


def test_env_level_waves_places_an_env_at_the_last_level_within_the_cap():
    """Mutations: `lv >= MAX_ENV_LEVELS - 1` in `env_level_waves`' cap check -- the
    deepest permitted level refuses; bucketing the cell into level 0 as well -- level 0's
    `wave0` is no longer empty."""
    cell = {"stack": "stacks/app", "environment": "prod"}
    out = w.env_level_waves([cell], _chain(w.MAX_ENV_LEVELS - 1), {"stacks/app": set()})
    assert out == [_EMPTY, _EMPTY, _EMPTY, {**_EMPTY, "wave0": [cell]}]


def test_env_level_waves_refuses_an_env_beyond_the_cap():
    """The guard is inside the shared function, so no caller can omit it and drop an
    over-deep env's cells out of every `range(MAX_ENV_LEVELS)` bucket.

    Mutation: delete the `if over:` refusal from `env_level_waves` -- the call returns
    MAX_ENV_LEVELS empty wave dicts instead of raising.
    """
    with pytest.raises(SystemExit, match="env order spans"):
        w.env_level_waves(
            [{"stack": "stacks/app", "environment": "prod"}],
            _chain(w.MAX_ENV_LEVELS),
            {"stacks/app": set()},
        )


def test_env_level_waves_refuses_a_cyclic_stack_graph():
    """deploy-detect and both apply-detect forms sort the stack graph only here.

    Mutation: `levels` for `stack_levels` in `env_level_waves` -- a raw `CycleError`
    escapes instead of the `SystemExit`."""
    with pytest.raises(SystemExit) as exc:
        w.env_level_waves(
            [{"stack": "stacks/a", "environment": "prod"}],
            {},
            {"stacks/a": {"stacks/b"}, "stacks/b": {"stacks/a"}},
        )
    assert str(exc.value).startswith("::error::dependency cycle in the Terramate stack run-graph")


def test_env_level_waves_buckets_and_orders():
    """Cells are bucketed by their env's level, then stack-wave-ordered within each level.

    Mutation: bucket on `env_level[...] == 0` -- the dev-us cells land at level 0 beside
    dev-eu's."""
    pending = [
        {"stack": "stacks/dns", "environment": "dev-eu"},
        {"stack": "stacks/app", "environment": "dev-eu"},
        {"stack": "stacks/dns", "environment": "dev-us"},
    ]
    deps = {"stacks/dns": set(), "stacks/app": {"stacks/dns"}}
    out = w.env_level_waves(pending, {"dev-us": ["dev-eu"]}, deps)
    assert out[0]["wave0"] == [{"stack": "stacks/dns", "environment": "dev-eu"}]
    assert out[0]["wave1"] == [{"stack": "stacks/app", "environment": "dev-eu"}]
    assert out[1]["wave0"] == [{"stack": "stacks/dns", "environment": "dev-us"}]
    assert out[1]["wave1"] == []


def test_env_level_waves_levels_the_cells_by_the_env_order():
    """Mutation: compute the env levels in `env_level_waves` from `{}` instead of `order` --
    dev-us lands at level 0 beside dev-eu, red."""
    eu = {"stack": "stacks/app", "environment": "dev-eu"}
    us = {"stack": "stacks/app", "environment": "dev-us"}
    out = w.env_level_waves([eu, us], {"dev-us": ["dev-eu"]}, {"stacks/app": set()})
    assert out == [{**_EMPTY, "wave0": [eu]}, {**_EMPTY, "wave0": [us]}, _EMPTY, _EMPTY]


def test_env_level_waves_empty_order_is_a_single_level():
    """A targeted apply passes an empty order, which puts every cell at level 0.

    Mutation: in `env_level_waves`, give every env level 1 when `order` is empty -- the
    cells land in level 1."""
    pending = [
        {"stack": "stacks/dns", "environment": "dev-eu"},
        {"stack": "stacks/dns", "environment": "dev-us"},
    ]
    out = w.env_level_waves(pending, {}, {"stacks/dns": set()})
    assert out == [{**_EMPTY, "wave0": pending}, _EMPTY, _EMPTY, _EMPTY]


def test_write_env_level_waves_emits_waves_and_empty_flags(tmp_path):
    # The shared GITHUB_OUTPUT writer must emit envlevelN_waves as JSON plus an envlevelN_empty
    # flag per level: 'false' for a level with any cell, 'true' for an empty one. Single-sourced,
    # so the three detects cannot drift apart on apply-env-level.yml's contract.
    cell = {"stack": "s", "environment": "dev-eu"}
    per_level = [
        {f"wave{i}": ([cell] if i == 0 else []) for i in range(8)},
        {f"wave{i}": [] for i in range(8)},
    ]
    out = tmp_path / "gh_output"
    with out.open("a", encoding="utf-8") as fh:
        w.write_env_level_waves(fh, per_level)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert 'envlevel0_waves={"wave0": [{"stack": "s", "environment": "dev-eu"}]' in lines[0]
    assert "envlevel0_empty=false" in lines
    assert "envlevel1_empty=true" in lines


def test_blocked_envs_direct_predecessor():
    order = {"stage": ["dev"], "prod": ["stage"]}
    assert w.blocked_envs(order, {"stage"}, {"dev", "prod"}) == {"prod"}


def test_blocked_envs_transitive():
    # prod -> stage -> dev, so dev being unavailable blocks prod through stage.
    order = {"stage": ["dev"], "prod": ["stage"]}
    assert w.blocked_envs(order, {"dev"}, {"stage", "prod"}) == {"stage", "prod"}


def test_blocked_envs_unrelated_env_not_blocked():
    # sbx does not list stage anywhere in its predecessor chain.
    order = {"prod": ["stage"], "sbx": ["dev"]}
    assert w.blocked_envs(order, {"stage"}, {"dev", "sbx", "prod"}) == {"prod"}


def test_blocked_envs_nothing_unavailable():
    assert w.blocked_envs({"prod": ["stage"]}, set(), {"stage", "prod"}) == set()


def _tags(**envs):
    """`{stack: ["env/<e>", ...]}` from `stack="e1 e2"` keywords."""
    return {s: [f"env/{e}" for e in es.split()] for s, es in envs.items()}


def _cell(stack, env):
    return {"stack": stack, "environment": env}


def _wait(stack, env, after_stack, after_env):
    return {
        "stack": stack,
        "environment": env,
        "after_stack": after_stack,
        "after_environment": after_env,
    }


def test_env_stacks_and_cell_levels_read_every_env_tag():
    """Mutations: drop the `startswith("env/")` filter -- `load/app` becomes an env; or
    build `cell_levels` from `order` alone -- `qa` drops out."""
    tags = {"vpc": ["env/dev-us", "env/dev-eu"], "app": ["env/qa", "workload/app"], "m": []}
    assert w.env_stacks(tags) == {"dev-eu": ["vpc"], "dev-us": ["vpc"], "qa": ["app"]}
    assert w.cell_levels({"dev-us": ["dev-eu"]}, tags) == {"dev-eu": 0, "dev-us": 1, "qa": 0}


def test_upstream_keeps_a_shared_stack_inside_its_environment():
    """Rule 1. Mutation: drop the same-environment branch -- rule 2 then also returns
    `("vpc", "dev-eu")`."""
    tags = _tags(app="dev-eu dev-us", vpc="dev-eu dev-us")
    level = w.cell_levels({"dev-us": ["dev-eu"]}, tags)
    assert w.upstream(("app", "dev-us"), {"app": {"vpc"}}, tags, level) == {("vpc", "dev-us")}


def test_upstream_drops_instances_needs_orders_after_the_root():
    """Rule 2's level filter, prod shape. Mutation: drop the level filter -- `platform/dev-eu`
    also reaches `dns/prod-us`."""
    tags = _tags(dns="dev-us prod-us", platform="dev-eu prod-eu")
    order = {"prod-eu": ["dev-eu", "dev-us"], "prod-us": ["dev-eu", "dev-us"]}
    level = w.cell_levels(order, tags)
    deps = {"platform": {"dns"}}
    assert w.upstream(("platform", "dev-eu"), deps, tags, level) == {("dns", "dev-us")}
    assert w.upstream(("platform", "prod-eu"), deps, tags, level) == {
        ("dns", "dev-us"),
        ("dns", "prod-us"),
    }


def test_upstream_filters_by_the_root_level_across_two_hops():
    """Mutation: filter at `(a, uu)` by the walked node's level (L0) -- `(b, zz)` drops."""
    tags = _tags(v="ee", a="uu", b="ww zz")
    level = w.cell_levels({"ee": ["uu"], "zz": ["ww"]}, tags)
    deps = {"v": {"a"}, "a": {"b"}}
    assert w.upstream(("v", "ee"), deps, tags, level) == {("a", "uu"), ("b", "ww"), ("b", "zz")}


def test_upstream_walks_through_a_stack_with_no_env_tag():
    """Rule 3. Mutation: `continue` at an untagged stack -- the result is empty."""
    tags = {"s": ["env/dev-eu"], "m": ["workload/app"], "a": ["env/dev-eu"]}
    deps = {"s": {"m"}, "m": {"a"}}
    assert w.upstream(("s", "dev-eu"), deps, tags, w.cell_levels({}, tags)) == {("a", "dev-eu")}


def test_a_dead_end_reaches_the_later_instance_and_refuses():
    """Mutation: return no instances at a dead end -- no pair."""
    tags = _tags(v="eu", a="us")
    level = w.cell_levels({"us": ["eu"]}, tags)
    present = {("v", "eu"), ("a", "us")}
    pairs = w.inversions([_cell("v", "eu")], present, {"v": {"a"}}, tags, level)
    assert pairs == [(("v", "eu"), ("a", "us"))]


_PROBE_DEPS = {"dns": set(), "platform": {"dns"}, "edge": {"platform"}, "gr": {"edge", "dns"}}
_PROBE_TAGS = _tags(dns="dev-us", platform="dev-eu", edge="dev-us", gr="dev-eu")
_PROBE_CELLS = [
    _cell("dns", "dev-us"),
    _cell("platform", "dev-eu"),
    _cell("edge", "dev-us"),
    _cell("gr", "dev-eu"),
]
_PROBE_PRESENT = {(c["stack"], c["environment"]) for c in _PROBE_CELLS}


def test_the_zigzag_with_no_needs_refuses_nothing():
    """Mutation: `>=` for `>` -- level 0 = level 0 refuses."""
    level = w.cell_levels({}, _PROBE_TAGS)
    assert w.inversions(_PROBE_CELLS, _PROBE_PRESENT, _PROBE_DEPS, _PROBE_TAGS, level) == []


def test_the_zigzag_under_needs_names_every_inverted_pair():
    """Mutation: drop the level comparison -- `edge / dev-us` after `platform / dev-eu` (a
    level lower) joins the message."""
    order = {"dev-us": ["dev-eu"]}
    level = w.cell_levels(order, _PROBE_TAGS)
    pairs = w.inversions(_PROBE_CELLS, _PROBE_PRESENT, _PROBE_DEPS, _PROBE_TAGS, level)
    assert w.inversion_error(pairs, order, level, "Open a new pull request.") == (
        "::error::needs orders gr / dev-eu before dns / dev-us, which it runs after:\n"
        "- gr / dev-eu (env-level 0) after dns / dev-us (env-level 1); dev-us's level comes "
        "from dev-eu -> dev-us\n"
        "- gr / dev-eu (env-level 0) after edge / dev-us (env-level 1); dev-us's level comes "
        "from dev-eu -> dev-us\n"
        "- platform / dev-eu (env-level 0) after dns / dev-us (env-level 1); dev-us's level "
        "comes from dev-eu -> dev-us\n"
        "Env-levels apply in order, so `after` cannot be met. Drop or reverse a `needs` entry "
        "in that chain on the default branch, or retag the stacks so the edge stays inside "
        "environments that order it. Open a new pull request."
    )


def test_inversions_roots_only_run_cells():
    """Mutation: iterate `present` instead of `cells` as roots -- the `v`/`a` pair appears."""
    tags = _tags(v="eu", a="us")
    level = w.cell_levels({"us": ["eu"]}, tags)
    assert w.inversions([], {("v", "eu"), ("a", "us")}, {"v": {"a"}}, tags, level) == []


def test_a_zigzag_without_needs_refuses_nothing():
    """Six cross-environment edges over two environments, no `needs`.

    Mutation: `>=` for `>` -- every edge refuses."""
    tags = {f"s{i}": [f"env/{'eu' if i % 2 else 'us'}"] for i in range(7)}
    deps = {f"s{i}": {f"s{i - 1}"} for i in range(1, 7)}
    cells = [_cell(s, t[0][len("env/") :]) for s, t in tags.items()]
    present = {(c["stack"], c["environment"]) for c in cells}
    assert w.inversions(cells, present, deps, tags, w.cell_levels({}, tags)) == []


def test_upstream_reads_an_environment_missing_from_the_levels_as_level_zero():
    """`layout: folder`/`workspace` envs need no table entry. Mutation: `level[env]` for
    `level.get(env, 0)` -- `KeyError`."""
    tags = _tags(app="qa", vpc="stg")
    assert w.upstream(("app", "qa"), {"app": {"vpc"}}, tags, {}) == {("vpc", "stg")}


def test_upstream_visits_each_node_of_a_diamond_lattice_once():
    """40 stacks, `s_i` after `s_{i-1}` and `s_{i-2}`, environments alternating.

    Mutation: drop `seen` -- 2^n paths; the test runs past `timeout 60`."""
    tags = {f"s{i}": [f"env/{'b' if i % 2 else 'a'}"] for i in range(40)}
    deps = {f"s{i}": {f"s{j}" for j in (i - 1, i - 2) if j >= 0} for i in range(40)}
    reached = w.upstream(("s39", "b"), deps, tags, w.cell_levels({}, tags))
    assert reached == {(f"s{i}", "b" if i % 2 else "a") for i in range(39)}


def test_a_run_cell_waits_on_a_pending_cell_outside_the_run():
    """Mutation: `out = pending & run` -- the first case returns `[]`."""
    tags = _tags(platform="eu", dns="us")
    level = w.cell_levels({}, tags)
    deps = {"platform": {"dns"}}
    run = [_cell("platform", "eu")]
    pending = {("dns", "us"), ("platform", "eu")}
    assert w.waiting_cells(run, pending, deps, tags, level) == [
        _wait("platform", "eu", "dns", "us")
    ]
    assert w.waiting_cells(run, {("platform", "eu")}, deps, tags, level) == []


def test_the_blocker_is_a_cell_the_user_can_apply_now():
    """`platform / eu` sorts first but waits on `zdns / us` itself.

    Mutation: take the sorted-first member without the own-upstream test -- `platform / eu`."""
    tags = _tags(zdns="us", platform="eu", edge="us")
    deps = {"platform": {"zdns"}, "edge": {"platform"}}
    pending = {("zdns", "us"), ("platform", "eu"), ("edge", "us")}
    got = w.waiting_cells([_cell("edge", "us")], pending, deps, tags, w.cell_levels({}, tags))
    assert got == [_wait("edge", "us", "zdns", "us")]


def test_three_targeted_runs_apply_a_zigzag_without_deadlock():
    """`dns/us -> platform/eu -> edge/us`, both environments explicit: apply us, eu, us.

    Mutation: drop the `- run` subtraction from `out` -- run 1 names `dns / us`, which it
    applies, as the blocker."""
    tags = _tags(dns="us", platform="eu", edge="us")
    level = w.cell_levels({}, tags)
    deps = {"platform": {"dns"}, "edge": {"platform"}}
    run1 = [_cell("dns", "us"), _cell("edge", "us")]
    pending = {("dns", "us"), ("platform", "eu"), ("edge", "us")}
    assert w.waiting_cells(run1, pending, deps, tags, level) == [
        _wait("edge", "us", "platform", "eu")
    ]
    pending -= {("dns", "us")}
    assert w.waiting_cells([_cell("platform", "eu")], pending, deps, tags, level) == []
    pending -= {("platform", "eu")}
    assert w.waiting_cells([_cell("edge", "us")], pending, deps, tags, level) == []


#: `r` (e1) after `b` (e0) after `a` (e1 e2) after `c` (ex e1); e1 needs ez, e2 needs e1.
_DEAD_END_TAGS = _tags(r="e1", b="e0", a="e1 e2", c="ex e1")
_DEAD_END_DEPS = {"r": {"b"}, "b": {"a"}, "a": {"c"}}
_DEAD_END_ORDER = {"e1": ["ez"], "e2": ["e1"]}


def test_a_cell_after_a_waiting_cell_waits_on_its_blocker():
    """`r`'s walk reaches `b` and never `c / ex`; `b`'s walk dead-ends at `a`, reaches `a / e2`
    and through it `c / ex`. So `b` waits on `c / ex`, and `r`, which runs after `b`, waits on
    that same blocker.

    Mutation: `break` after the first pass of the loop in `waiting_cells` -- `r` is missing."""
    level = w.cell_levels(_DEAD_END_ORDER, _DEAD_END_TAGS)
    run = [_cell("r", "e1"), _cell("b", "e0")]
    pending = {("r", "e1"), ("b", "e0"), ("c", "ex")}
    assert w.waiting_cells(run, pending, _DEAD_END_DEPS, _DEAD_END_TAGS, level) == [
        _wait("b", "e0", "c", "ex"),
        _wait("r", "e1", "c", "ex"),
    ]


def test_upstream_excludes_the_root_on_a_cycle():
    """Mutation: return `reached` without `- {root}` -- `a / e` is in its own walk."""
    tags = _tags(a="e", b="e")
    got = w.upstream(("a", "e"), {"a": {"b"}, "b": {"a"}}, tags, w.cell_levels({}, tags))
    assert got == {("b", "e")}
