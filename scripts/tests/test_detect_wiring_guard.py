"""Guards what the workflows and actions hand the detect scripts.

- No workflow or action names the retired shared-environment repository variable or the
  `shared-envs` input that carried it. Which environment is shared is `shared = true` in the
  environment table, resolved by `env-config`; a restored hop would be a second source that
  nothing reads, or worse, one somebody wires back in.
- Each apply-side detect action hands its script exactly the names it reads. The whole `env:`
  block against a hand-written constant, so a renamed or dropped binding cannot hide behind a
  present input. `build-matrix`'s block is pinned by
  `test_build_matrix.py::test_build_matrix_action_hands_the_script_the_names_it_reads`.
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
    },
    "apply-all-detect": {
        "GH_TOKEN": "${{ inputs.github-token }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_REVIEW_DECISION": "${{ inputs.review-decision }}",
    },
    "deploy-detect": {
        "GH_TOKEN": "${{ inputs.github-token }}",
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
    },
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
