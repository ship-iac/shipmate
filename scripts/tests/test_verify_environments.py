"""Unit tests for scripts/verify-environments and the action step that runs it.

The pre-flight is fail-closed by design, and three of its refusals are the ones
that would otherwise have landed fail-open, so each has its own test:

- a binding no environment satisfies fails the run, naming every missing one;
- a listing that could not be read fails the run (the action's bash), with text
  saying a transient failure clears on a re-run;
- a listing whose `total_count` exceeds what was read fails the run.

The bash tests execute the real, unmodified action step with `gh` replaced by a
bash function and `python3` pointed at the interpreter running pytest, so the
whole chain -- listing, refusal text, exit status -- is what ships.
"""

import json
import os
import subprocess
import sys

import pytest
from _loader import ACTIONS, action_steps, load_script, usable_bash

vf = load_script("verify-environments")

_BASH = usable_bash()

WAVES = {"wave0": [{"stack": "app", "environment": "dev-eu", "workload_var": "APP"}], "wave1": []}


def _listing(names, total=None):
    return {
        "total_count": len(names) if total is None else total,
        "environments": [{"name": n} for n in names],
    }


def test_bindings_are_the_apply_side_names_for_every_distinct_env():
    waves = {
        "wave0": [{"stack": "a", "environment": "dev-eu"}, {"stack": "b", "environment": "dev-eu"}],
        "wave1": [{"stack": "a", "environment": "prod"}],
        "wave2": None,
    }
    assert vf.required_bindings(waves, "prod") == ["dev-eu-apply", "prod"]


def test_a_payload_with_no_cell_is_refused_rather_than_passed():
    with pytest.raises(SystemExit) as exc:
        vf.required_bindings({"wave0": [], "wave1": None}, "")
    assert "no cell" in str(exc.value)


def test_a_cell_without_an_environment_is_refused_rather_than_tracebacking():
    with pytest.raises(SystemExit) as exc:
        vf.required_bindings({"wave0": [{"stack": "a"}]}, "")
    assert "carries no 'environment' key" in str(exc.value)


def test_a_truncated_listing_is_refused():
    with pytest.raises(SystemExit) as exc:
        vf.existing_names(_listing(["dev-eu-apply"], total=200))
    assert "truncated" in str(exc.value)


def test_a_listing_without_a_total_count_is_not_taken_as_complete():
    # Absence-means-complete would be fail-open: the KeyError propagates and
    # fails the step, which is the direction this guard exists to hold.
    with pytest.raises(KeyError):
        vf.existing_names({"environments": [{"name": "dev-eu-apply"}]})


def test_a_complete_listing_yields_its_names():
    assert vf.existing_names(_listing(["dev-eu-apply", "prod"])) == {"dev-eu-apply", "prod"}


def _run_step(tmp_path, listing, waves_json, shared_envs, gh_exit=0):
    assert _BASH is not None  # callers are skipif-gated on this; narrows the type too
    stub = (
        "gh() { "
        f'[ "{gh_exit}" = 0 ] || return {gh_exit}; '
        'printf "%s" "$FAKE_LISTING"; }\n'
        'python3() { "$PYTHON" "$@"; }\n'
    )
    step = [s for s in action_steps("verify-environments") if s.get("shell") == "bash"]
    assert len(step) == 1, f"expected exactly one bash step, got {len(step)}"
    script = tmp_path / "step.sh"
    script.write_text(stub + step[0]["run"], encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "x",
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_ACTION_PATH": str(ACTIONS / "verify-environments"),
            "RUNNER_TEMP": str(tmp_path),
            "FAKE_LISTING": json.dumps(listing),
            "SHIPMATE_WAVES_JSON": waves_json,
            "SHIPMATE_SHARED_ENVS": shared_envs,
            "PYTHON": sys.executable,
        }
    )
    return subprocess.run(
        [_BASH, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )


def _out(proc):
    """Both streams: the runner reads workflow commands from either, and the
    script's refusals travel on stderr through SystemExit while the action's own
    `echo` goes to stdout."""
    return proc.stdout + proc.stderr


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_an_existing_apply_environment_passes(tmp_path):
    proc = _run_step(tmp_path, _listing(["dev-eu-plan", "dev-eu-apply"]), json.dumps(WAVES), "")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::notice::" in _out(proc)


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_missing_environment_fails_the_run_naming_every_one_and_both_fixes(tmp_path):
    waves = {
        "wave0": [{"stack": "a", "environment": "dev-eu"}, {"stack": "b", "environment": "prod"}]
    }
    proc = _run_step(tmp_path, _listing(["dev-eu-plan"]), json.dumps(waves), "")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "dev-eu-apply" in _out(proc)
    assert "prod-apply" in _out(proc), "only the first missing environment was named"
    assert "create each environment" in _out(proc)
    assert "SHIPMATE_SHARED_ENVS" in _out(proc)


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_failed_listing_fails_the_run_and_says_a_re_run_clears_it(tmp_path):
    proc = _run_step(tmp_path, _listing([]), json.dumps(WAVES), "", gh_exit=1)
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in _out(proc)
    assert "re-run" in _out(proc), (
        "a transient listing failure that reads as a bug gets 'fixed' by softening "
        "this refusal -- the message must say a re-run clears it"
    )


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_truncated_listing_fails_the_run_through_the_step(tmp_path):
    proc = _run_step(tmp_path, _listing(["dev-eu-apply"], total=200), json.dumps(WAVES), "")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "truncated" in _out(proc)


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_unparseable_waves_json_fails_the_run(tmp_path):
    proc = _run_step(tmp_path, _listing(["dev-eu-apply"]), "not json", "")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "did not parse as JSON" in _out(proc)


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_shared_env_is_satisfied_by_the_bare_environment(tmp_path):
    # The other half of the branch: with dev-eu listed, `dev-eu-apply` need not
    # exist and `dev-eu` must.
    proc = _run_step(tmp_path, _listing(["dev-eu"]), json.dumps(WAVES), "dev-eu")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
