# scripts/tests/test_plan_classify.py
import importlib.util, pathlib
from importlib.machinery import SourceFileLoader

_p = pathlib.Path(__file__).resolve().parents[1] / "plan-classify"
_loader = SourceFileLoader("plan_classify", str(_p))
_spec = importlib.util.spec_from_loader("plan_classify", _loader)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)


def _rc(*actions):
    return {"change": {"actions": list(actions)}}


def test_classify_counts_add_change_destroy():
    plan = {"resource_changes": [
        _rc("create"), _rc("update"), _rc("delete"),
        _rc("no-op"), _rc("read"),
    ]}
    assert pc.classify(plan) == {"changed": True, "add": 1, "change": 1, "destroy": 1}


def test_classify_no_op_and_read_are_not_changes():
    plan = {"resource_changes": [_rc("no-op"), _rc("read")]}
    assert pc.classify(plan) == {"changed": False, "add": 0, "change": 0, "destroy": 0}


def test_classify_output_only_change_is_a_change():
    plan = {"resource_changes": [], "output_changes": {"name": {"actions": ["update"]}}}
    assert pc.classify(plan)["changed"] is True


def test_classify_empty_plan():
    assert pc.classify({}) == {"changed": False, "add": 0, "change": 0, "destroy": 0}


def test_fingerprint_excludes_non_tfvar_and_is_sorted_deterministic():
    a = pc.fingerprint({"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu", "AWS_SECRET": "x", "PATH": "/"})
    b = pc.fingerprint({"TF_VAR_region": "eu", "TF_VAR_env": "dev-eu"})
    assert a == b and len(a) == 64


def test_fingerprint_includes_tf_workspace_only_when_set():
    with_ws = pc.fingerprint({"TF_WORKSPACE": "dev-us"})
    with_ws2 = pc.fingerprint({"TF_WORKSPACE": "dev-eu"})
    empty = pc.fingerprint({})
    empty_blank_ws = pc.fingerprint({"TF_WORKSPACE": ""})
    assert with_ws != with_ws2          # env identity disambiguates workspaces
    assert empty == empty_blank_ws      # unset/blank == excluded (stacks/folders unchanged)


def test_fingerprint_stacks_flavor_matches_tfvar_only_algo():
    # PRD-1 algo was sorted TF_VAR_* name->value JSON; TF_WORKSPACE unset must not change it.
    import json, hashlib
    env = {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}
    prd1 = hashlib.sha256(json.dumps(dict(sorted(env.items())), sort_keys=True).encode()).hexdigest()
    assert pc.fingerprint(env) == prd1
