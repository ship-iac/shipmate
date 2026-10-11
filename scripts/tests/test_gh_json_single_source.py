"""`_run` and `gh_json` have one definition each, in `_shipmate`, and every CI script holds
that object rather than a copy: one definition decides how a nonzero `gh api` exit surfaces."""

import _shipmate
from _loader import load_script

ad = load_script("apply-detect")
bm = load_script("build-matrix")
dd = load_script("deploy-detect")
di = load_script("drift-issues")
doctor = load_script("doctor")
ec = load_script("env-config")
mc = load_script("mirror-checks")
ob = load_script("onboard")
pf = load_script("pr-facts")


def test_every_gh_json_holder_holds_the_shipmate_definition():
    """Mutation: give `env-config` its own `def gh_json` -- `env-config.gh_json` turns False.
    Mutation: give `pr-facts` a local `def _gh_json(path)` -- `pr-facts._gh_json` turns False.
    Mutation: give `onboard` its own `def gh_json` -- `onboard.gh_json` turns False."""
    holders = {
        "deploy-detect._gh_json": dd._gh_json,
        "doctor._gh_json": doctor._gh_json,
        "env-config.gh_json": ec.gh_json,
        "mirror-checks.gh_json": mc.gh_json,
        "onboard.gh_json": ob.gh_json,
        "pr-facts._gh_json": pf._gh_json,
    }
    assert {name: obj is _shipmate.gh_json for name, obj in holders.items()} == {
        "deploy-detect._gh_json": True,
        "doctor._gh_json": True,
        "env-config.gh_json": True,
        "mirror-checks.gh_json": True,
        "onboard.gh_json": True,
        "pr-facts._gh_json": True,
    }


def test_every_run_holder_holds_the_shipmate_definition():
    """Mutation: give `build-matrix` its own `def _run` -- `build-matrix._run` turns False.
    Mutation: give `apply-detect` its own `def _run` -- `apply-detect._run` turns False."""
    holders = {
        "apply-detect._run": ad._run,
        "build-matrix._run": bm._run,
        "drift-issues._run": di._run,
    }
    assert {name: obj is _shipmate._run for name, obj in holders.items()} == {
        "apply-detect._run": True,
        "build-matrix._run": True,
        "drift-issues._run": True,
    }


def test_pr_facts_and_mirror_checks_load_no_build_matrix(monkeypatch):
    """Every `_load` a module makes while loading, nested loads included, against a
    hand-written constant. Mutation: re-add `bm = _load("build-matrix")` to `pr-facts`."""
    real = _shipmate._load
    calls = []

    def record(fname):
        calls.append(fname)
        return real(fname)

    monkeypatch.setattr(_shipmate, "_load", record)
    loaded = {}
    for fname in ("pr-facts", "mirror-checks"):
        calls.clear()
        real(fname)
        loaded[fname] = list(calls)
    assert loaded == {"pr-facts": [], "mirror-checks": ["apply-gate"]}
