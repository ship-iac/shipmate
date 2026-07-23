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


def test_rejects_stack_path_exactly_apply():
    # A stack literally named `apply` renders a plan check `apply / <env>`, which
    # collides with the apply-check namespace apply-gate selects the queue by.
    with pytest.raises(SystemExit, match="may not be exactly 'apply'"):
        bm.build_matrix(["dev-eu"], {"dev-eu": ["apply"]}, {"apply": ["env/dev-eu"]})


def test_nested_apply_stack_is_allowed():
    # Only an exact top-level `apply` collides; `infra/apply` renders
    # `infra/apply / <env>`, outside the `apply / ` namespace.
    cells = bm.build_matrix(
        ["dev-eu"], {"dev-eu": ["infra/apply"]}, {"infra/apply": ["env/dev-eu"]}
    )
    assert cells == [{"stack": "infra/apply", "environment": "dev-eu", "workload": ""}]


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


def test_tags_evals_with_as_json(monkeypatch):
    captured = {}
    monkeypatch.setattr(bm, "_run", lambda args: captured.update(args=args) or '["env/dev-eu"]')
    assert bm._tags("stacks/app") == ["env/dev-eu"]
    assert captured["args"] == [
        "terramate",
        "-C",
        "stacks/app",
        "experimental",
        "eval",
        "--as-json",
        "terramate.stack.tags",
    ]


def test_compute_cells_fans_out_multi_env(monkeypatch):
    # Happy path only -- does NOT exercise the untagged-stack guard.
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["env/dev-eu", "env/dev-us", "workload/app"])
    cells = bm.compute_cells(all_stacks=True)
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
        {"stack": "stacks/app", "environment": "dev-us", "workload": "app"},
    ]


def test_compute_cells_raises_on_untagged_stack(monkeypatch):
    # A stack with no env/* tag would silently vanish from plan/apply/drift
    # -- compute_cells must fail loud instead (scripts/build-matrix lines ~76-84).
    monkeypatch.setattr(
        bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/orphan"]
    )
    monkeypatch.setattr(
        bm,
        "_tags",
        lambda s: ["env/dev-eu"] if s == "stacks/app" else ["workload/net"],
    )
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True)
    assert "stacks/orphan" in str(exc_info.value)
    assert "stacks/app" not in str(exc_info.value)


def test_env_membership_groups_stacks_by_env_tag(monkeypatch):
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/dns"])
    tags = {
        "stacks/app": ["env/dev-eu", "env/dev-us"],
        "stacks/dns": ["env/dev-eu", "workload/dns"],
    }
    monkeypatch.setattr(bm, "_tags", lambda s: tags[s])
    stacks_by_env, tags_by_stack = bm.env_membership(all_stacks=True)
    assert stacks_by_env == {"dev-eu": ["stacks/app", "stacks/dns"], "dev-us": ["stacks/app"]}
    assert tags_by_stack == tags


def test_env_membership_fails_loud_on_untagged_stack(monkeypatch):
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/orphan"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["workload/app"])
    with pytest.raises(SystemExit):
        bm.env_membership(all_stacks=True)


def test_env_membership_require_env_tag_false_ignores_untagged(monkeypatch):
    # The artifact-sourced bare-apply path passes require_env_tag=False: an
    # untagged stack anywhere in the repo must NOT abort membership — it simply
    # produces no plan.<env>.<slug> artifact and contributes no cell. The tagged
    # stacks still bucket normally; the untagged one just vanishes from the map.
    stacks = ["stacks/app", "stacks/orphan"]
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: stacks)
    tags = {"stacks/app": ["env/dev-eu"], "stacks/orphan": ["workload/util"]}
    monkeypatch.setattr(bm, "_tags", lambda s: tags[s])
    stacks_by_env, tags_by_stack = bm.env_membership(all_stacks=True, require_env_tag=False)
    assert stacks_by_env == {"dev-eu": ["stacks/app"]}
    assert tags_by_stack == tags  # orphan still reported in tags, just not bucketed
