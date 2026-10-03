"""Guards what the workflows and actions hand the detect scripts.

- Each apply-side detect action hands its script exactly the names it reads. The whole `env:`
  block against a hand-written constant, so a renamed or dropped binding cannot hide behind a
  present input. `build-matrix`'s block is pinned by
  `test_build_matrix.py::test_build_matrix_action_hands_the_script_the_names_it_reads`.
- Every detect action, `comment-ops` and `summary` declares `github-vars`, and every detect call
  site passes it `toJSON(vars)`, so `env-config` can resolve a `{ vars = "NAME" }` reference in
  the table. The
  `with:` of `plan.yml`, `drift.yml` and `unlock.yml`'s detect steps is pinned whole beside the
  rest of those workflows; the other three steps are pinned here whole, beside `apply.yml`'s
  `detect` outputs.
- The set of workflow steps carrying `toJSON(vars)` is exactly the detect and cell steps. The
  enumeration holds every repository and organization variable, so a new holder is a decision.
"""

import pytest
import yaml
from _loader import WORKFLOWS, action_yaml, workflow_yaml

#: The whole `env:` of the step that runs each detect script.
_SCRIPT_ENV = {
    "apply-detect": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_ENV": "${{ inputs.environment }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_REVIEW_DECISION": "${{ inputs.review-decision }}",
        "SHIPMATE_MODE": "${{ inputs.mode }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
    },
    "apply-all-detect": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_REVIEW_DECISION": "${{ inputs.review-decision }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
    },
    "deploy-detect": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
    },
}

#: Every non-cell action that reads the table. The cell actions are pinned by
#: `test_env_inject_wiring_guard.py::test_each_cell_action_declares_both_consumer_channels`.
_TABLE_READERS = (
    "build-matrix",
    "apply-detect",
    "apply-all-detect",
    "deploy-detect",
    "comment-ops",
    "summary",
)

#: The whole detect steps no other guard pins whole, by workflow and action. `apply.yml` runs
#: exactly one of its two by `if:`, and its job outputs read each by `id`.
_DETECT_STEPS = {
    ("apply.yml", "$/actions/apply-detect"): {
        "id": "t",
        "if": "${{ inputs.environment != '' }}",
        "uses": "$/actions/apply-detect",
        "with": {
            "environment": "${{ inputs.environment }}",
            "head-sha": "${{ inputs.ref }}",
            "app-id": "${{ vars.SHIPMATE_APP_ID }}",
            "review-decision": "${{ needs.review.outputs.decision }}",
            "github-vars": "${{ toJSON(vars) }}",
        },
    },
    ("apply.yml", "$/actions/apply-all-detect"): {
        "id": "d",
        "if": "${{ inputs.environment == '' }}",
        "uses": "$/actions/apply-all-detect",
        "with": {
            "head-sha": "${{ inputs.ref }}",
            "app-id": "${{ vars.SHIPMATE_APP_ID }}",
            "review-decision": "${{ needs.review.outputs.decision }}",
            "github-vars": "${{ toJSON(vars) }}",
        },
    },
    ("deploy.yml", "$/actions/deploy-detect"): {
        "id": "d",
        "uses": "$/actions/deploy-detect",
        "with": {
            "base-sha": "${{ github.event.before }}",
            "app-id": "${{ vars.SHIPMATE_APP_ID }}",
            "github-vars": "${{ toJSON(vars) }}",
        },
    },
}

#: The whole `outputs:` of `apply.yml`'s `detect`. A skipped step's outputs read as '', so each
#: two-sourced value is whichever step ran. Levels 1-3 read 'true' from the input on a targeted
#: apply: `apply-detect` writes none, and a missing `_empty` must run its level red, never skip.
#: The four disposition sets read `d` alone, so a targeted apply's comment renders none of them.
_APPLY_DETECT_OUTPUTS = {
    "envlevel0_waves": "${{ steps.t.outputs.waves || steps.d.outputs.envlevel0_waves }}",
    "envlevel1_waves": "${{ steps.d.outputs.envlevel1_waves }}",
    "envlevel2_waves": "${{ steps.d.outputs.envlevel2_waves }}",
    "envlevel3_waves": "${{ steps.d.outputs.envlevel3_waves }}",
    "envlevel0_empty": "${{ steps.t.outputs.empty || steps.d.outputs.envlevel0_empty }}",
    "envlevel1_empty": (
        "${{ inputs.environment != '' && 'true' || steps.d.outputs.envlevel1_empty }}"
    ),
    "envlevel2_empty": (
        "${{ inputs.environment != '' && 'true' || steps.d.outputs.envlevel2_empty }}"
    ),
    "envlevel3_empty": (
        "${{ inputs.environment != '' && 'true' || steps.d.outputs.envlevel3_empty }}"
    ),
    "head_sha": "${{ steps.t.outputs.head_sha || steps.d.outputs.head_sha }}",
    "excluded_envs": "${{ steps.d.outputs.excluded_envs }}",
    "skipped_envs": "${{ steps.d.outputs.skipped_envs }}",
    "review_held_envs": "${{ steps.d.outputs.review_held_envs }}",
    "applied_ungated_envs": "${{ steps.d.outputs.applied_ungated_envs }}",
    "review_not_required_envs": (
        "${{ steps.t.outputs.review_not_required_envs"
        " || steps.d.outputs.review_not_required_envs }}"
    ),
}

#: Every (workflow, job, step) that carries `toJSON(vars)`: six detect steps, eleven cell steps,
#: the `comment-ops` step and the plan `summary` step.
_VARS_HOLDERS = {
    ("plan.yml", "detect", "$/actions/build-matrix"),
    ("drift.yml", "detect", "$/actions/build-matrix"),
    ("apply.yml", "detect", "$/actions/apply-detect"),
    ("apply.yml", "detect", "$/actions/apply-all-detect"),
    ("unlock.yml", "detect", "$/actions/apply-detect"),
    ("deploy.yml", "detect", "$/actions/deploy-detect"),
    ("plan.yml", "plan", "$/actions/plan-cell"),
    ("drift.yml", "drift", "$/actions/drift-cell"),
    ("unlock.yml", "unlock", "$/actions/unlock-cell"),
    *(("apply-env-level.yml", f"wave{n}", "$/actions/apply-cell") for n in range(8)),
    ("comment-ops.yml", "ops", "$/actions/comment-ops"),
    ("plan.yml", "summary", "$/actions/summary"),
}


@pytest.mark.parametrize("action", sorted(_SCRIPT_ENV))
def test_every_apply_side_detect_action_hands_its_script_exactly_these_names(action):
    """Mutation: delete `SHIPMATE_REVIEW_DECISION` from `apply-all-detect`'s script step."""
    doc = action_yaml(action)
    steps = [s for s in doc["runs"]["steps"] if "/../../scripts/" in str(s.get("run", ""))]
    assert len(steps) == 1, f"{action}: expected one script step, got {len(steps)}"
    assert steps[0]["env"] == _SCRIPT_ENV[action]


@pytest.mark.parametrize("action", _TABLE_READERS)
def test_every_table_reader_declares_the_variables_input(action):
    """An undeclared `with:` key on a composite action is dropped with a warning, so the
    input would arrive empty and every reference would refuse as unset. Description aside,
    the whole entry.

    Mutation: delete the `github-vars:` input block from `apply-all-detect`, `comment-ops` or
    `summary`.
    """
    doc = action_yaml(action)
    declared = dict(doc["inputs"].get("github-vars") or {})
    declared.pop("description", None)
    assert declared == {"required": False, "default": ""}


@pytest.mark.parametrize(("workflow", "uses"), sorted(_DETECT_STEPS))
def test_the_detect_step_is_exactly_this_step(workflow, uses):
    """Mutation: delete the `github-vars:` line from `deploy.yml`'s detect step.
    Mutation: swap the `if:` of `apply.yml`'s two detect steps.
    Mutation: delete `review-decision` from `apply.yml`'s step `t`."""
    doc = workflow_yaml(workflow)
    steps = [s for s in doc["jobs"]["detect"]["steps"] if s.get("uses") == uses]
    assert len(steps) == 1, f"{workflow}: {len(steps)} {uses} steps"
    assert steps[0] == _DETECT_STEPS[(workflow, uses)]


def test_the_apply_detect_job_outputs_exactly_these_expressions():
    """Mutation: drop `&& 'true'` from `envlevel1_empty`'s targeted arm."""
    assert workflow_yaml("apply.yml")["jobs"]["detect"]["outputs"] == _APPLY_DETECT_OUTPUTS


def test_only_the_listed_steps_carry_the_variables():
    """Every workflow, every job and step, the whole parsed node searched, so a holder in a
    job-level `with:` or `env:` counts as well as one in a step.

    Mutation: add `github-vars: ${{ toJSON(vars) }}` to `manifest-load.yml`'s `build-matrix`
    step.
    """
    found = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = workflow_yaml(path)
        for job_id, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            rest = {k: v for k, v in job.items() if k != "steps"}
            if "toJSON(vars)" in yaml.safe_dump(rest):
                found.add((path.name, job_id, None))
            for step in steps:
                if "toJSON(vars)" in yaml.safe_dump(step):
                    found.add((path.name, job_id, step.get("uses") or step.get("name")))
    assert found == _VARS_HOLDERS
