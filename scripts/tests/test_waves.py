import importlib.util
import io
import json
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

_dir = pathlib.Path(__file__).resolve().parents[1]
_loader = SourceFileLoader("waves", str(_dir / "waves"))
_spec = importlib.util.spec_from_loader("waves", _loader)
w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w)

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


def test_main_treats_set_but_empty_workset_as_empty_list(monkeypatch, tmp_path):
    monkeypatch.setenv("SHIPMATE_WORKSET", "")
    out_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(FIXTURE))
    w.main([])  # must not raise json.JSONDecodeError on an empty-string env
    assert "empty=true" in out_file.read_text()


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


def _linear_chain_dot(n):
    """A dot fixture with n nodes in a straight dependency chain s1->s2->...->sn,
    i.e. n topological levels, one node per level."""
    lines = ["digraph  {"]
    for i in range(1, n + 1):
        lines.append(f'\tn{i}[label="/stacks/s{i}"];')
    for i in range(1, n):
        lines.append(f"\tn{i}->n{i + 1};")
    lines.append("}")
    return "\n".join(lines)


def test_main_guard_fires_only_after_reverse_moves_wave_past_max(monkeypatch, tmp_path):
    # 9 levels total; only the level-0 stack is in the work set. Pre-reverse it
    # sits at wave index 0 (fine). --reverse moves it to index 8 (the 9th wave)
    # -- the guard must run AFTER reversing, not before.
    chain = _linear_chain_dot(w.MAX_WAVES + 1)
    workset = json.dumps([{"stack": "stacks/s1", "environment": "dev-eu", "workload": ""}])

    monkeypatch.setattr("sys.stdin", io.StringIO(chain))
    monkeypatch.setenv("SHIPMATE_WORKSET", workset)
    out_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    w.main([])  # no --reverse: wave index 0 -- must not raise
    assert "wave0=" in out_file.read_text()

    monkeypatch.setattr("sys.stdin", io.StringIO(chain))
    with pytest.raises(SystemExit):
        w.main(["--reverse"])


def test_write_waves_emits_aggregate_waves_json():
    # apply-env-level.yml consumes a single waves_json object and errors on a
    # missing waveN key -- the aggregate must always carry all 8 keys.
    fh = io.StringIO()
    cells = [{"stack": "stacks/dns", "environment": "dev-eu"}]
    w.write_waves(fh, [cells, []])
    out = dict(line.split("=", 1) for line in fh.getvalue().strip().splitlines())
    agg = json.loads(out["waves"])
    assert set(agg) == {f"wave{i}" for i in range(w.MAX_WAVES)}
    assert agg["wave0"] == cells
    assert all(agg[f"wave{i}"] == [] for i in range(1, w.MAX_WAVES))
    # the flat outputs and the aggregate must agree
    assert json.loads(out["wave0"]) == agg["wave0"]
    assert out["empty"] == "false"
