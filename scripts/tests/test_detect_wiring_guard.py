"""Guards what the workflows and actions hand the detect scripts.

- Each apply-side detect action hands its script exactly the names it reads. The whole `env:`
  block against a hand-written constant, so a renamed or dropped binding cannot hide behind a
  present input. `build-matrix`'s block is pinned by
  `test_build_matrix.py::test_build_matrix_action_hands_the_script_the_names_it_reads`.
- Every detect action, `comment-ops` and `summary` declares `github-vars`, and every detect call
  site passes it `toJSON(vars)`, so `env-config` can resolve a `{ vars = "NAME" }` reference in
  the table. The
  `with:` of `plan.yml`, `drift.yml` and `unlock.yml`'s detect steps is pinned whole beside the
  rest of those workflows; the other two steps are pinned here whole, beside `apply.yml`'s
  `detect` outputs.
- The set of workflow steps carrying `toJSON(vars)` is exactly the detect and cell steps. The
  enumeration holds every repository and organization variable, so a new holder is a decision.
"""

import os

import pytest
import yaml
from _loader import WORKFLOWS, action_yaml, bash_only, run_step, workflow_yaml

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
    "deploy-detect",
    "comment-ops",
    "summary",
)

#: The whole detect steps no other guard pins whole, by workflow and action. `apply.yml`'s one
#: step serves both apply forms, with no `if:`: the action picks the script from `environment`.
_DETECT_STEPS = {
    ("apply.yml", "$/actions/apply-detect"): {
        "id": "d",
        "uses": "$/actions/apply-detect",
        "with": {
            "environment": "${{ inputs.environment }}",
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

#: The whole `outputs:` of `apply.yml`'s `detect`, each read from the one step. A targeted apply
#: gets levels 1-3 `_empty = 'true'` from `apply-detect`'s own write; a missing `_empty` reads ''
#: and runs its level red, never skips it.
_APPLY_DETECT_OUTPUTS = {
    "envlevel0_waves": "${{ steps.d.outputs.envlevel0_waves }}",
    "envlevel1_waves": "${{ steps.d.outputs.envlevel1_waves }}",
    "envlevel2_waves": "${{ steps.d.outputs.envlevel2_waves }}",
    "envlevel3_waves": "${{ steps.d.outputs.envlevel3_waves }}",
    "envlevel0_empty": "${{ steps.d.outputs.envlevel0_empty }}",
    "envlevel1_empty": "${{ steps.d.outputs.envlevel1_empty }}",
    "envlevel2_empty": "${{ steps.d.outputs.envlevel2_empty }}",
    "envlevel3_empty": "${{ steps.d.outputs.envlevel3_empty }}",
    "head_sha": "${{ steps.d.outputs.head_sha }}",
    "excluded_envs": "${{ steps.d.outputs.excluded_envs }}",
    "skipped_envs": "${{ steps.d.outputs.skipped_envs }}",
    "review_held_envs": "${{ steps.d.outputs.review_held_envs }}",
    "applied_ungated_envs": "${{ steps.d.outputs.applied_ungated_envs }}",
    "review_not_required_envs": "${{ steps.d.outputs.review_not_required_envs }}",
}

#: Every (workflow, job, step) that carries `toJSON(vars)`: five detect steps, eleven cell steps,
#: the `comment-ops` step and the plan `summary` step.
_VARS_HOLDERS = {
    ("plan.yml", "detect", "$/actions/build-matrix"),
    ("drift.yml", "detect", "$/actions/build-matrix"),
    ("apply.yml", "detect", "$/actions/apply-detect"),
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
    """Mutation: delete `SHIPMATE_REVIEW_DECISION` from `apply-detect`'s script step."""
    doc = action_yaml(action)
    steps = [s for s in doc["runs"]["steps"] if "/../../scripts/" in str(s.get("run", ""))]
    assert len(steps) == 1, f"{action}: expected one script step, got {len(steps)}"
    assert steps[0]["env"] == _SCRIPT_ENV[action]


#: The whole `run:` of `apply-detect`'s script step.
_APPLY_DETECT_RUN = """\
# An empty environment is the bare form, which only apply mode has. Any other mode, absent
# or garbled included, reaches apply-detect, which refuses an empty environment.
if [ -z "$SHIPMATE_ENV" ] && [ "$SHIPMATE_MODE" = apply ]; then
  exec python3 "$GITHUB_ACTION_PATH/../../scripts/apply-all-detect"
fi
exec python3 "$GITHUB_ACTION_PATH/../../scripts/apply-detect"
"""


def _script_step(action):
    return next(s for s in action_yaml(action)["runs"]["steps"] if s.get("id") == "d")


def test_the_apply_detect_step_runs_exactly_this_script():
    """Mutation: `-z` -> `-n` in the script step's `if`."""
    assert _script_step("apply-detect")["run"] == _APPLY_DETECT_RUN


@bash_only
@pytest.mark.parametrize(
    ("environment", "mode", "argv"),
    [
        ("dev-eu", "apply", ["/ap/../../scripts/apply-detect"]),
        ("", "apply", ["/ap/../../scripts/apply-all-detect"]),
        ("", "unlock", ["/ap/../../scripts/apply-detect"]),
        ("dev-eu", "unlock", ["/ap/../../scripts/apply-detect"]),
        ("", "", ["/ap/../../scripts/apply-detect"]),
        ("", "banana", ["/ap/../../scripts/apply-detect"]),
    ],
)
def test_the_apply_detect_action_picks_the_script_by_environment_and_mode(
    tmp_path, environment, mode, argv
):
    """Only an empty environment in apply mode reaches `apply-all-detect`; an unlock, absent or
    garbled mode reaches `apply-detect`, whose `validate_env` refuses an empty environment.

    Mutation: `-z` -> `-n`.
    Mutation: delete `&& [ "$SHIPMATE_MODE" = apply ]` -- the `("", "unlock")` row reddens.
    Mutation: `= apply` -> `!= unlock` -- the `("", "")` and `("", "banana")` rows redden.
    Mutation: swap the two script names."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    record = tmp_path / "argv.txt"
    stub = stubs / "python3"
    stub.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$@" > "$RECORD"\n', encoding="utf-8", newline="\n"
    )
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}",
        "GITHUB_ACTION_PATH": "/ap",
        "RECORD": str(record),
        "SHIPMATE_ENV": environment,
        "SHIPMATE_MODE": mode,
    }
    result = run_step(tmp_path, _script_step("apply-detect")["run"], env)
    assert result.returncode == 0, result.stderr
    assert record.read_text(encoding="utf-8").splitlines() == argv


@pytest.mark.parametrize("action", _TABLE_READERS)
def test_every_table_reader_declares_the_variables_input(action):
    """An undeclared `with:` key on a composite action is dropped with a warning, so the
    input would arrive empty and every reference would refuse as unset. Description aside,
    the whole entry.

    Mutation: delete the `github-vars:` input block from `apply-detect`, `comment-ops` or
    `summary`.
    """
    doc = action_yaml(action)
    declared = dict(doc["inputs"].get("github-vars") or {})
    declared.pop("description", None)
    assert declared == {"required": False, "default": ""}


@pytest.mark.parametrize(("workflow", "uses"), sorted(_DETECT_STEPS))
def test_the_detect_step_is_exactly_this_step(workflow, uses):
    """Mutation: delete the `github-vars:` line from `deploy.yml`'s detect step.
    Mutation: give `apply.yml`'s detect step an `if: ${{ inputs.environment != '' }}`.
    Mutation: delete `review-decision` from `apply.yml`'s detect step."""
    doc = workflow_yaml(workflow)
    steps = [s for s in doc["jobs"]["detect"]["steps"] if s.get("uses") == uses]
    assert len(steps) == 1, f"{workflow}: {len(steps)} {uses} steps"
    assert steps[0] == _DETECT_STEPS[(workflow, uses)]


def test_the_apply_detect_job_outputs_exactly_these_expressions():
    """Mutation: change `envlevel1_empty` to read `steps.d.outputs.envlevel0_empty`."""
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
