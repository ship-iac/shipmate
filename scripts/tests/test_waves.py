import ast
import io
import json

import pytest
from _loader import SCRIPTS as _dir
from _loader import load_script

w = load_script("waves")
eo = load_script("env-order")

FIXTURE = (_dir / "tests" / "fixtures" / "run-graph-stacks.dot").read_text()


def test_parse_dot_matches_fixture_dag():
    deps = w.parse_dot(FIXTURE)
    assert deps["stacks/platform"] == {"stacks/dns"}  # n4->n3
    assert deps["stacks/app"] == {"stacks/auth", "stacks/workers"}  # n2->n1, n5->n1
    assert deps["stacks/dns"] == set()
    assert deps["stacks/sandbox/box"] == set()  # isolated node, still present


def test_levels_are_topological():
    deps = w.parse_dot(FIXTURE)
    lv = w.levels(deps)
    # level 0 = roots: dns AND the isolated sandbox/box (both dependency-free)
    assert "stacks/dns" in lv[0] and "stacks/sandbox/box" in lv[0]
    assert lv[1] == ["stacks/platform"]
    assert set(lv[2]) == {"stacks/auth", "stacks/workers"}
    assert lv[3] == ["stacks/app"]
    assert set(lv[4]) == {"stacks/tenant-a", "stacks/tenant-b"}


def test_assign_waves_preserves_transitive_order_with_empty_middle():
    # Only dns (level 0) and app (level 3) in the work set -> empty waves 1,2.
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
    # dns@dev-us must be an earlier wave than platform@dev-eu (cross-env edge).
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
    # An empty run-graph (failed or returned nothing) with a non-empty work set
    # must fail loud rather than silently dropping pending applies.
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    with pytest.raises(SystemExit):
        w.assign_waves([], cells)


def test_assign_waves_allows_empty_levels_with_empty_workset():
    assert w.assign_waves([], []) == []


def test_assign_waves_raises_when_stack_missing_from_graph():
    # A work-set stack that isn't a node in the run-graph can't be ordered --
    # fail loud instead of KeyError-ing or dropping it silently.
    deps = w.parse_dot(FIXTURE)
    lv = w.levels(deps)
    cells = [{"stack": "stacks/does-not-exist", "environment": "dev-eu", "workload": ""}]
    with pytest.raises(SystemExit) as exc_info:
        w.assign_waves(lv, cells)
    assert "stacks/does-not-exist" in str(exc_info.value)


def test_guard_max_waves_allows_exactly_max_waves():
    # Populated waves at indices 0..MAX_WAVES-1 (8 waves total) are fine.
    waves = [[f"cell{i}"] for i in range(w.MAX_WAVES)]
    w.guard_max_waves(waves)  # must not raise


def test_guard_max_waves_raises_when_ninth_wave_populated():
    # A populated wave at index MAX_WAVES (the 9th wave) has no pre-declared
    # wave{MAX_WAVES} job -- must fail loud.
    waves = [[f"cell{i}"] for i in range(w.MAX_WAVES)] + [["cell8"]]
    with pytest.raises(SystemExit):
        w.guard_max_waves(waves)


def test_guard_max_waves_ignores_empty_trailing_levels():
    # A deep FULL graph with only low-level cells in the work set is fine --
    # empty trailing levels beyond MAX_WAVES must not trip the guard.
    waves = [["cell0"]] + [[] for _ in range(w.MAX_WAVES + 3)]
    w.guard_max_waves(waves)  # must not raise


def test_write_waves_emits_aggregate_waves_json():
    # apply-env-level.yml indexes fromJSON(waves_json).waveN for every N and
    # errors on a missing key -- the aggregate must always carry all 8.
    fh = io.StringIO()
    cells = [{"stack": "stacks/dns", "environment": "dev-eu"}]
    w.write_waves(fh, [cells, []])
    out = dict(line.split("=", 1) for line in fh.getvalue().strip().splitlines())
    agg = json.loads(out["waves"])
    assert set(agg) == {f"wave{i}" for i in range(w.MAX_WAVES)}
    assert agg["wave0"] == cells
    assert all(agg[f"wave{i}"] == [] for i in range(1, w.MAX_WAVES))
    assert out["empty"] == "false"


def test_write_waves_pads_short_and_flags_empty():
    fh = io.StringIO()
    w.write_waves(fh, [])
    out = dict(line.split("=", 1) for line in fh.getvalue().strip().splitlines())
    assert json.loads(out["waves"]) == {f"wave{i}": [] for i in range(w.MAX_WAVES)}
    assert out["empty"] == "true"


def _linear_chain_dot(n):
    """A dot fixture of n nodes in a straight chain s1->...->sn: n topological
    levels, one node per level."""
    lines = ["digraph  {"]
    lines += [f'\tn{i}[label="/stacks/s{i}"];' for i in range(1, n + 1)]
    lines += [f"\tn{i}->n{i + 1};" for i in range(1, n)]
    return "\n".join(lines + ["}"])


# Every function that reaches `pad_waves`, and the module it lives in.
# pad_waves TRUNCATES past MAX_WAVES, so each must call guard_max_waves first
# or a too-deep change applies nothing for the dropped cells and reports
# success. Add a row here whenever a new caller lands.
_PAD_CALLERS = (("apply-detect", "main"), ("env-order", "waves_by_env_level"))


def _call_order(script, func):
    """Names of the `wv.`/module-level calls in `func`, in source order."""
    tree = ast.parse((_dir / script).read_text(encoding="utf-8"))
    body = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    return [
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(body)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    ]


@pytest.mark.parametrize(("script", "func"), _PAD_CALLERS)
def test_every_pad_waves_caller_guards_first(script, func):
    """The guard-before-truncate ordering, pinned at the CALL SITE.

    Unit-testing guard_max_waves on hand-built lists does not pin that any
    production path still calls it: deleting the call from both callers left
    the whole suite green. Asserted over the parsed AST, so a guard call moved
    below the pad -- or commented out -- fails."""
    order = _call_order(script, func)
    pad = next(
        (i for i, name in enumerate(order) if name in ("pad_waves", "write_waves")),
        None,
    )
    assert pad is not None, f"{script}.{func} no longer reaches pad_waves"
    guard = order.index("guard_max_waves") if "guard_max_waves" in order else None
    assert guard is not None, f"{script}.{func} does not call guard_max_waves"
    assert guard < pad, f"{script}.{func} pads before guarding"


def test_env_level_waves_refuses_a_change_deeper_than_max_waves():
    """The behavioural half: the guard actually fires through a real caller,
    rather than dropping the level-8 cells into a silent no-op apply."""
    deps = w.parse_dot(_linear_chain_dot(w.MAX_WAVES + 1))
    deep = f"stacks/s{w.MAX_WAVES + 1}"
    pending = [{"stack": deep, "environment": "dev-eu"}]

    with pytest.raises(SystemExit) as exc:
        eo.waves_by_env_level(pending, deps, {"dev-eu": 0})

    assert "dependency levels" in str(exc.value)
