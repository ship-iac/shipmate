"""`scripts/verify-environments` and the action step that runs it.

The pre-flight is fail-closed by design, and three of its refusals are the ones that would
otherwise have landed fail-open, so each has its own test:

- a binding no environment satisfies fails the run, naming every missing one;
- a listing that could not be read fails the run, in the action's bash, with text saying a
  transient failure clears on a re-run;
- a listing whose `total_count` exceeds what was read fails the run;
- a binding whose environment has no deployment branch policy fails the run, naming every one.

The bash tests execute the real, unmodified action step with `gh` replaced by a bash function and
`python3` pointed at the interpreter running pytest, so the whole chain -- listing, refusal text,
exit status -- is what ships.
"""

import json
import os
import sys

import pytest
from _loader import ACTIONS, action_steps, bash_only, load_script, run_step

vf = load_script("verify-environments")


WAVES = {
    "wave0": [{"stack": "app", "environment": "dev-eu", "env_binding": "dev-eu-apply"}],
    "wave1": [],
}


#: The policy `scripts/onboard` sets on every apply and shared environment.
CUSTOM = {"protected_branches": False, "custom_branch_policies": True}
PROTECTED = {"protected_branches": True, "custom_branch_policies": False}


def _listing(names, total=None, policies=None):
    """Each environment carries `CUSTOM` unless `policies` names another (None: no policy)."""
    policies = policies or {}
    return {
        "total_count": len(names) if total is None else total,
        "environments": [
            {"name": n, "deployment_branch_policy": policies.get(n, CUSTOM)} for n in names
        ],
    }


def test_bindings_are_the_distinct_env_bindings_detect_resolved():
    """`prod` is a shared environment, so its binding is the bare name; the script must take
    that from the cell, not recompute `<env>-apply`."""
    waves = {
        "wave0": [
            {"stack": "a", "environment": "dev-eu", "env_binding": "dev-eu-apply"},
            {"stack": "b", "environment": "dev-eu", "env_binding": "dev-eu-apply"},
        ],
        "wave1": [{"stack": "a", "environment": "prod", "env_binding": "prod"}],
        "wave2": None,
    }
    assert vf.required_bindings(waves) == ["dev-eu-apply", "prod"]


def test_a_payload_with_no_cell_is_refused_rather_than_passed():
    with pytest.raises(SystemExit) as exc:
        vf.required_bindings({"wave0": [], "wave1": None})
    assert "no cell" in str(exc.value)


def test_a_cell_without_an_env_binding_is_refused_as_a_malformed_hand_off():
    """The cell carries `environment`, so a fallback that recomputes `<env>-apply` from it has
    something to compute from.

    Mutation: read `cell.get("env_binding", f"{cell['environment']}-apply")` -- no refusal.
    """
    with pytest.raises(SystemExit) as exc:
        vf.required_bindings({"wave0": [{"stack": "a", "environment": "dev-eu"}]})
    assert str(exc.value) == (
        "::error::a wave matrix cell carries no 'env_binding' key, so the environment it "
        "would bind could not be checked; this is a malformed hand-off rather than "
        "an empty run; refusing to apply."
    )


def test_a_truncated_listing_is_refused():
    with pytest.raises(SystemExit) as exc:
        vf.existing_names(_listing(["dev-eu-apply"], total=200))
    assert "truncated" in str(exc.value)


def test_a_listing_without_a_total_count_is_not_taken_as_complete():
    # Absence-means-complete would be fail-open. The KeyError propagates and fails the step,
    # which is the direction this guard exists to hold.
    with pytest.raises(KeyError):
        vf.existing_names({"environments": [{"name": "dev-eu-apply"}]})


def test_a_complete_listing_yields_each_name_with_its_policy():
    """An environment the listing shows with no policy key reads as having no policy.

    Mutation: read `e["deployment_branch_policy"]` (KeyError on `prod`)."""
    listing = _listing(["dev-eu-plan", "dev-eu-apply", "prod"], policies={"dev-eu-plan": None})
    del listing["environments"][2]["deployment_branch_policy"]
    assert vf.existing_names(listing) == {
        "dev-eu-plan": None,
        "dev-eu-apply": {"protected_branches": False, "custom_branch_policies": True},
        "prod": None,
    }


def _run_step(tmp_path, listing, waves_json, gh_exit=0):
    stub = (
        "gh() { "
        f'[ "{gh_exit}" = 0 ] || return {gh_exit}; '
        'printf "%s" "$FAKE_LISTING"; }\n'
        'python3() { "$PYTHON" "$@"; }\n'
    )
    step = [s for s in action_steps("verify-environments") if s.get("shell") == "bash"]
    assert len(step) == 1, f"expected exactly one bash step, got {len(step)}"
    return run_step(
        tmp_path,
        stub + step[0]["run"],
        {
            **os.environ,
            "GH_TOKEN": "x",
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_ACTION_PATH": str(ACTIONS / "verify-environments"),
            "RUNNER_TEMP": str(tmp_path),
            "FAKE_LISTING": json.dumps(listing),
            "SHIPMATE_WAVES_JSON": waves_json,
            "PYTHON": sys.executable,
            # The refusals carry an em dash; pinned both ends so the host locale cannot garble it.
            "PYTHONIOENCODING": "utf-8",
        },
        timeout=60,
    )


def _out(proc):
    """Both streams: the runner reads workflow commands from either, and the script's refusals
    travel on stderr through SystemExit while the action's own `echo` goes to stdout."""
    return proc.stdout + proc.stderr


@bash_only
def test_an_existing_apply_environment_passes(tmp_path):
    proc = _run_step(tmp_path, _listing(["dev-eu-plan", "dev-eu-apply"]), json.dumps(WAVES))
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::notice::" in _out(proc)


@bash_only
def test_a_missing_environment_fails_the_run_naming_every_one_and_both_fixes(tmp_path):
    """Whole text on stderr, where SystemExit writes it: the action's own `echo` is stdout."""
    waves = {
        "wave0": [
            {"stack": "a", "environment": "dev-eu", "env_binding": "dev-eu-apply"},
            {"stack": "b", "environment": "prod", "env_binding": "prod-apply"},
        ]
    }
    proc = _run_step(tmp_path, _listing(["dev-eu-plan"]), json.dumps(waves))
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stderr == (
        "::error::this apply would bind GitHub Environment(s) that do not exist: "
        "dev-eu-apply, prod-apply. GitHub creates a missing environment on demand with no "
        "reviewers, no wait timer and no deployment branch policy, so the apply would run "
        "outside every control that environment is meant to carry. Two ways to fix it: "
        "create each environment named above, or correct the environment's entry in "
        "`.github/shipmate-config.yml` so the apply binds the environments you did create "
        "(`shared: true` binds the bare <env>, anything else binds <env>-apply).\n"
    )


@bash_only
def test_a_failed_listing_fails_the_run_and_says_a_re_run_clears_it(tmp_path):
    proc = _run_step(tmp_path, _listing([]), json.dumps(WAVES), gh_exit=1)
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in _out(proc)
    assert "re-run" in _out(proc), (
        "a transient listing failure that reads as a bug gets 'fixed' by softening "
        "this refusal -- the message must say a re-run clears it"
    )


@bash_only
def test_a_truncated_listing_fails_the_run_through_the_step(tmp_path):
    proc = _run_step(tmp_path, _listing(["dev-eu-apply"], total=200), json.dumps(WAVES))
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "truncated" in _out(proc)


@bash_only
def test_unparseable_waves_json_fails_the_run(tmp_path):
    proc = _run_step(tmp_path, _listing(["dev-eu-apply"]), "not json")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "did not parse as JSON" in _out(proc)


@bash_only
def test_a_shared_env_is_satisfied_by_the_bare_environment(tmp_path):
    # The other half of the branch: with dev-eu shared, `dev-eu-apply` need not exist and
    # `dev-eu` must.
    waves = {"wave0": [{"stack": "app", "environment": "dev-eu", "env_binding": "dev-eu"}]}
    proc = _run_step(tmp_path, _listing(["dev-eu"]), json.dumps(waves))
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"


#: The refusal for bindings with no deployment branch policy, hand-written.
NO_POLICY_REASON = (
    "::error::GitHub Environment(s) `dev-eu-apply`, `prod` have no deployment branch policy, "
    "so a workflow on any branch can bind them and reach their secrets and OIDC role. Run "
    "`scripts/onboard` (docs/getting-started.md), which scopes each apply environment to the "
    "default branch, or set the policy in the environment's settings.\n"
)
POLICY_WAVES = {
    "wave0": [
        {"stack": "a", "environment": "dev-eu", "env_binding": "dev-eu-apply"},
        {"stack": "b", "environment": "prod", "env_binding": "prod"},
        {"stack": "c", "environment": "stg", "env_binding": "stg-apply"},
    ]
}


@bash_only
def test_a_binding_with_no_branch_policy_fails_the_run_naming_every_one(tmp_path):
    """A plan environment with no policy is not a binding here, so it is not named.

    Mutation: delete the `is None` refusal in `main` (the run passes)."""
    listing = _listing(
        ["dev-eu-plan", "dev-eu-apply", "prod", "stg-apply"],
        policies={"dev-eu-plan": None, "dev-eu-apply": None, "prod": None},
    )
    proc = _run_step(tmp_path, listing, json.dumps(POLICY_WAVES))
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stderr == NO_POLICY_REASON


@bash_only
@pytest.mark.parametrize("policy", [CUSTOM, PROTECTED], ids=["custom", "protected"])
def test_a_custom_or_protected_branches_policy_passes(tmp_path, policy):
    """Mutation: refuse unless `custom_branch_policies` is truthy, in place of `is None` (the
    protected-branches case fails)."""
    listing = _listing(
        ["dev-eu-apply", "prod", "stg-apply"],
        policies={name: policy for name in ("dev-eu-apply", "prod", "stg-apply")},
    )
    proc = _run_step(tmp_path, listing, json.dumps(POLICY_WAVES))
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stdout == (
        "::notice::apply-path environments verified: dev-eu-apply, prod, stg-apply\n"
    )


@bash_only
def test_a_missing_environment_is_refused_before_an_unscoped_one(tmp_path):
    """Mutation: move the policy refusal above the missing-environment refusal (`prod` raises
    KeyError, so stderr carries a traceback)."""
    listing = _listing(["dev-eu-apply", "stg-apply"], policies={"dev-eu-apply": None})
    proc = _run_step(tmp_path, listing, json.dumps(POLICY_WAVES))
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stderr == (
        "::error::this apply would bind GitHub Environment(s) that do not exist: prod. GitHub "
        "creates a missing environment on demand with no reviewers, no wait timer and no "
        "deployment branch policy, so the apply would run outside every control that "
        "environment is meant to carry. Two ways to fix it: create each environment named "
        "above, or correct the environment's entry in `.github/shipmate-config.yml` so the "
        "apply binds the environments you did create (`shared: true` binds the bare <env>, "
        "anything else binds <env>-apply).\n"
    )
