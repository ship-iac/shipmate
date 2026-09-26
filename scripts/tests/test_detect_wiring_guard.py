"""Guards what the workflows and actions hand the detect scripts.

- No workflow or action names the retired shared-environment repository variable or the
  `shared-envs` input that carried it. Which environment is shared is `shared = true` in the
  environment table, resolved by `env-config`; a restored hop would be a second source that
  nothing reads, or worse, one somebody wires back in.
- Each apply-side detect action hands its script exactly the names it reads. The whole `env:`
  block against a hand-written constant, so a renamed or dropped binding cannot hide behind a
  present input. `build-matrix`'s block is pinned by
  `test_build_matrix.py::test_build_matrix_action_hands_the_script_the_names_it_reads`.
- Every detect action declares `github-vars` and every detect call site passes it
  `toJSON(vars)`, so `env-config` can resolve a `{ var = "NAME" }` reference in the table. The
  `with:` of `plan.yml`, `drift.yml` and `unlock.yml`'s detect steps is pinned whole beside the
  rest of those workflows; the other three are pinned here.
- The set of workflow steps carrying `toJSON(vars)` is exactly the detect and cell steps. The
  enumeration holds every repository and organization variable, so a new holder is a decision.
"""

import pytest
import yaml
from _loader import ACTIONS, ENGINE

RETIRED = ("SHIPMATE_SHARED_ENVS", "shared-envs")

#: The whole `env:` of the step that runs each detect script.
_SCRIPT_ENV = {
    "apply-detect": {
        "GH_TOKEN": "${{ inputs.github-token }}",
        "SHIPMATE_ENV": "${{ inputs.environment }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_REVIEW_DECISION": "${{ inputs.review-decision }}",
        "SHIPMATE_MODE": "${{ inputs.mode }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
    },
    "apply-all-detect": {
        "GH_TOKEN": "${{ inputs.github-token }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_REVIEW_DECISION": "${{ inputs.review-decision }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
    },
    "deploy-detect": {
        "GH_TOKEN": "${{ inputs.github-token }}",
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
    },
}

_DETECT_ACTIONS = ("build-matrix", "apply-detect", "apply-all-detect", "deploy-detect")

#: The whole `with:` of the detect steps no other guard pins whole, by workflow.
_DETECT_WITH = {
    "apply.yml": (
        "$/actions/apply-detect",
        {
            "environment": "${{ inputs.environment }}",
            "head-sha": "${{ inputs.ref }}",
            "github-token": "${{ github.token }}",
            "app-id": "${{ vars.SHIPMATE_APP_ID }}",
            "review-decision": "${{ needs.review.outputs.decision }}",
            "github-vars": "${{ toJSON(vars) }}",
        },
    ),
    "apply-all.yml": (
        "$/actions/apply-all-detect",
        {
            "head-sha": "${{ inputs.ref }}",
            "github-token": "${{ github.token }}",
            "app-id": "${{ vars.SHIPMATE_APP_ID }}",
            "review-decision": "${{ needs.review.outputs.decision }}",
            "github-vars": "${{ toJSON(vars) }}",
        },
    ),
    "deploy.yml": (
        "$/actions/deploy-detect",
        {
            "base-sha": "${{ github.event.before }}",
            "github-token": "${{ github.token }}",
            "app-id": "${{ vars.SHIPMATE_APP_ID }}",
            "github-vars": "${{ toJSON(vars) }}",
        },
    ),
}

#: Every (workflow, job, step) that carries `toJSON(vars)`: six detect steps, eleven cell steps.
_VARS_HOLDERS = {
    ("plan.yml", "detect", "$/actions/build-matrix"),
    ("drift.yml", "detect", "$/actions/build-matrix"),
    ("apply.yml", "detect", "$/actions/apply-detect"),
    ("unlock.yml", "detect", "$/actions/apply-detect"),
    ("apply-all.yml", "detect", "$/actions/apply-all-detect"),
    ("deploy.yml", "detect", "$/actions/deploy-detect"),
    ("plan.yml", "plan", "$/actions/plan-cell"),
    ("drift.yml", "drift", "$/actions/drift-cell"),
    ("unlock.yml", "unlock", "$/actions/unlock-cell"),
    *(("apply-env-level.yml", f"wave{n}", "$/actions/apply-cell") for n in range(8)),
}


def test_no_workflow_or_action_names_the_retired_shared_env_variable():
    """Raw text, comments included: a mention is where a re-added hop starts.

    Mutation: restore `shared-envs: ${{ vars.SHIPMATE_SHARED_ENVS }}` in `plan.yml`'s
    `build-matrix` step.
    """
    files = sorted((ENGINE / ".github/workflows").rglob("*")) + sorted(ACTIONS.rglob("*"))
    hits = [
        (path.relative_to(ENGINE).as_posix(), word)
        for path in files
        if path.is_file()
        for word in RETIRED
        if word in path.read_text(encoding="utf-8")
    ]
    assert hits == []


@pytest.mark.parametrize("action", sorted(_SCRIPT_ENV))
def test_every_apply_side_detect_action_hands_its_script_exactly_these_names(action):
    """Mutation: delete `SHIPMATE_REVIEW_DECISION` from `apply-all-detect`'s script step."""
    doc = yaml.safe_load((ACTIONS / action / "action.yml").read_text(encoding="utf-8"))
    steps = [s for s in doc["runs"]["steps"] if "/../../scripts/" in str(s.get("run", ""))]
    assert len(steps) == 1, f"{action}: expected one script step, got {len(steps)}"
    assert steps[0]["env"] == _SCRIPT_ENV[action]


@pytest.mark.parametrize("action", _DETECT_ACTIONS)
def test_every_detect_action_declares_the_variables_input(action):
    """An undeclared `with:` key on a composite action is dropped with a warning, so the
    input would arrive empty. Description aside, the whole entry.

    Mutation: delete the `github-vars:` input block from `apply-all-detect`.
    """
    doc = yaml.safe_load((ACTIONS / action / "action.yml").read_text(encoding="utf-8"))
    declared = dict(doc["inputs"].get("github-vars") or {})
    declared.pop("description", None)
    assert declared == {"required": False, "default": ""}


@pytest.mark.parametrize("workflow", sorted(_DETECT_WITH))
def test_the_detect_step_passes_exactly_these_inputs(workflow):
    """Mutation: delete the `github-vars:` line from `deploy.yml`'s detect step."""
    uses, expected = _DETECT_WITH[workflow]
    doc = yaml.safe_load((ENGINE / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    steps = [s for s in doc["jobs"]["detect"]["steps"] if s.get("uses") == uses]
    assert len(steps) == 1, f"{workflow}: {len(steps)} {uses} steps"
    assert steps[0]["with"] == expected


def test_only_the_detect_and_cell_steps_carry_the_variables():
    """Every workflow, every job and step, the whole parsed node searched, so a holder in a
    job-level `with:` or `env:` counts as well as one in a step.

    Mutation: add `github-vars: ${{ toJSON(vars) }}` to `manifest-load.yml`'s `build-matrix`
    step.
    """
    found = set()
    for path in sorted((ENGINE / ".github/workflows").glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            rest = {k: v for k, v in job.items() if k != "steps"}
            if "toJSON(vars)" in yaml.safe_dump(rest):
                found.add((path.name, job_id, None))
            for step in steps:
                if "toJSON(vars)" in yaml.safe_dump(step):
                    found.add((path.name, job_id, step.get("uses") or step.get("name")))
    assert found == _VARS_HOLDERS
