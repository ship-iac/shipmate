"""Guards the two hops that carry `SHIPMATE_SHARED_ENVS` into the detect scripts.

A script cannot read `vars` itself, so the value travels workflow -> detect action -> process
environment. Neither hop is pinned by anything else, and `test_row_stamp_guard.py` sets the
variable in its own fixture environment, so it stays green while a workflow omits the input or
an action forgets to bind it. The production result of either omission is an empty value: every
environment reads as unshared, a shared environment's plan cells resolve `aws.plan` instead of
`aws.apply`, and `env-config`'s refusal of a shared environment that declares `aws.plan` stops
firing. Silent, and in the open direction.

Whole parsed values against hand-written constants. The step list is derived rather than
written down, so a seventh detect step reddens here on its own.
"""

import pytest
import yaml
from _loader import ACTIONS, WORKFLOWS

_DETECT_ACTIONS = ("build-matrix", "apply-detect", "apply-all-detect", "deploy-detect")

_SHARED = "${{ vars.SHIPMATE_SHARED_ENVS }}"

#: Every workflow step that runs a detect action, hand-written. Six steps, four actions: `plan`
#: and `drift` share `build-matrix`, and `apply` and `unlock` share `apply-detect`.
_DETECT_STEPS = {
    ("plan.yml", "build-matrix"),
    ("drift.yml", "build-matrix"),
    ("apply.yml", "apply-detect"),
    ("unlock.yml", "apply-detect"),
    ("apply-all.yml", "apply-all-detect"),
    ("deploy.yml", "deploy-detect"),
}

#: The whole `env:` of the step that runs each detect script. `GH_TOKEN` is on all four because
#: the table's source branch is resolved through the API; three of them already held one for
#: their own check-run listing.
_SCRIPT_ENV = {
    "build-matrix": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_ALL_STACKS": "${{ inputs.all-stacks }}",
        "SHIPMATE_HEAD_REPO": "${{ inputs.head-repo }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_NO_PULL_REQUEST": "${{ inputs.no-pull-request }}",
        "SHIPMATE_TAGS": "${{ inputs.tags }}",
        "SHIPMATE_SHARED_ENVS": "${{ inputs.shared-envs }}",
    },
    "apply-detect": {
        "GH_TOKEN": "${{ inputs.github-token }}",
        "SHIPMATE_ENV": "${{ inputs.environment }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_UNGATED_ENVS": "${{ inputs.ungated-envs }}",
        "SHIPMATE_REVIEW_DECISION": "${{ inputs.review-decision }}",
        "SHIPMATE_MODE": "${{ inputs.mode }}",
        "SHIPMATE_SHARED_ENVS": "${{ inputs.shared-envs }}",
    },
    "apply-all-detect": {
        "GH_TOKEN": "${{ inputs.github-token }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_UNGATED_ENVS": "${{ inputs.ungated-envs }}",
        "SHIPMATE_REVIEW_DECISION": "${{ inputs.review-decision }}",
        "SHIPMATE_SHARED_ENVS": "${{ inputs.shared-envs }}",
    },
    "deploy-detect": {
        "GH_TOKEN": "${{ inputs.github-token }}",
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_SHARED_ENVS": "${{ inputs.shared-envs }}",
    },
}


def _doc(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _detect_action(step):
    """The detect action a step runs, or None.

    `manifest-load.yml` names all four under `if: false`, purely so GitHub parses their
    manifests; that job runs no detect and reads no repository variable. Excluding it by the
    `if` rather than by filename is what makes a seventh detect step red on its own.
    """
    if step.get("if") is False:
        return None
    uses = str(step.get("uses", ""))
    return next((a for a in _DETECT_ACTIONS if f"/actions/{a}@" in uses), None)


def _found_steps():
    found = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in (_doc(path).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                action = _detect_action(step)
                if action:
                    found.add((path.name, action))
    return found


def test_exactly_six_workflow_steps_run_a_detect_action():
    """Derived against the hand-written six. A new detect step, or a detect action referenced
    from a workflow nobody added the input to, reddens here rather than resolving every
    environment as unshared in production.

    Mutation: point a seventh step at `actions/deploy-detect`, or drop `if: false` from one of
    `manifest-load.yml`'s four references.
    """
    assert _found_steps() == _DETECT_STEPS


@pytest.mark.parametrize(("workflow", "action"), sorted(_DETECT_STEPS), ids=lambda v: v)
def test_every_detect_step_passes_the_shared_env_variable(workflow, action):
    """Hop 1. Without it the input falls back to its own empty default and the script reads an
    empty value -- no error, every environment unshared.

    Mutation: delete the line from `plan.yml`'s step, and from `apply.yml`'s.
    """
    steps = [
        s
        for job in (_doc(WORKFLOWS / workflow).get("jobs") or {}).values()
        for s in job.get("steps") or []
        if _detect_action(s) == action
    ]
    assert len(steps) == 1, f"{workflow}: expected one {action} step, got {len(steps)}"
    assert (steps[0].get("with") or {}).get("shared-envs") == _SHARED


@pytest.mark.parametrize("action", _DETECT_ACTIONS)
def test_every_detect_action_declares_the_input_and_binds_it(action):
    """Hop 2, the one that looks done and is not: the workflow passes the value, the action
    accepts it, and the script reads nothing. The whole `env:` block against a hand-written
    constant, so a renamed or dropped binding cannot hide behind a present input.

    Mutations: delete the `env:` binding from `deploy-detect`'s script step while leaving the
    input declared; delete the `shared-envs` input from `apply-detect`.
    """
    doc = _doc(ACTIONS / action / "action.yml")
    assert doc["inputs"]["shared-envs"]["required"] is True
    steps = [s for s in doc["runs"]["steps"] if "/../../scripts/" in str(s.get("run", ""))]
    assert len(steps) == 1, f"{action}: expected one script step, got {len(steps)}"
    assert steps[0]["env"] == _SCRIPT_ENV[action]
