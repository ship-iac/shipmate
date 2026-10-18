"""The `runs_on` input every heavy engine job takes, and its forwarding into the env-level waves.

A consumer file that passes no `with:` to `deploy` or `comment-ops` still loads, because every
consumer-called `runs_on` is optional with a default. `apply-env-level.yml` requires it instead: a
caller that omits a required input fails at load with no job and no log, so every `envlevelN` in
`apply.yml` and `deploy.yml` must forward it.
"""

from _loader import workflow_yaml

OPTIONAL = {"required": False, "default": "ubuntu-26.04-arm", "type": "string"}
FORWARD = "${{ inputs.runs_on }}"
ENV_LEVELS = ["envlevel0", "envlevel1", "envlevel2", "envlevel3"]


def _runs_on(name):
    return workflow_yaml(name)["on"]["workflow_call"]["inputs"]["runs_on"]


def test_plan_runs_on_default():
    assert _runs_on("plan.yml") == OPTIONAL


def test_drift_runs_on_default():
    assert _runs_on("drift.yml") == OPTIONAL


def test_apply_runs_on_default():
    assert _runs_on("apply.yml") == OPTIONAL


def test_deploy_runs_on_default():
    """Mutation: `deploy.yml` `runs_on` to `required: true`."""
    assert _runs_on("deploy.yml") == OPTIONAL


def test_unlock_runs_on_default():
    assert _runs_on("unlock.yml") == OPTIONAL


def test_comment_ops_runs_on_default():
    """Mutation: `comment-ops.yml` `runs_on` default to `ubuntu-latest`."""
    assert _runs_on("comment-ops.yml") == OPTIONAL


def test_apply_env_level_requires_runs_on():
    """Mutation: drop `required: true` and add `default: ubuntu-latest`."""
    assert _runs_on("apply-env-level.yml") == {"required": True, "type": "string"}


def test_apply_forwards_runs_on_to_every_env_level():
    """Mutation: `apply.yml` `envlevel1`'s `runs_on` to `ubuntu-latest`."""
    jobs = workflow_yaml("apply.yml")["jobs"]
    assert {j: jobs[j]["with"].get("runs_on") for j in ENV_LEVELS} == dict.fromkeys(
        ENV_LEVELS, FORWARD
    )


def test_deploy_forwards_runs_on_to_every_env_level():
    """Mutation: delete `runs_on` from `deploy.yml` `envlevel2`'s `with:`."""
    jobs = workflow_yaml("deploy.yml")["jobs"]
    assert {j: jobs[j]["with"].get("runs_on") for j in ENV_LEVELS} == dict.fromkeys(
        ENV_LEVELS, FORWARD
    )
