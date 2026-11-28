"""Guards what the workflows and actions hand the detect scripts.

- Each apply-side detect action hands its script exactly the names it reads. The whole `env:`
  block against a hand-written constant, so a renamed or dropped binding cannot hide behind a
  present input. `build-matrix`'s block is pinned by
  `test_build_matrix.py::test_build_matrix_action_hands_the_script_the_names_it_reads`.
- Every detect action, `comment-ops` and `summary` declares `github-vars`, and every detect call
  site passes it `toJSON(vars)`, so `env-config` can resolve a `{vars: NAME}` reference in
  the table. The
  `with:` of `plan.yml`, `drift.yml` and `unlock.yml`'s detect steps is pinned whole beside the
  rest of those workflows; the other two steps are pinned here whole, beside `apply-run.yml`'s
  `detect` outputs.
- The set of workflow steps carrying `toJSON(vars)` is exactly the detect and cell steps. The
  enumeration holds every repository and organization variable, so a new holder is a decision.
"""

import pytest
import yaml
from _loader import WORKFLOWS, action_yaml, checkouts, workflow_yaml

#: The whole `env:` of the step that runs each detect script.
_SCRIPT_ENV = {
    "apply-detect": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_ENV": "${{ inputs.environment }}",
        "SHIPMATE_AUTO": "${{ inputs.auto }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_REVIEW_DECISION": "${{ inputs.review-decision }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
        "SHIPMATE_REPORT_PATH": "${{ inputs.report-path }}",
    },
    "deploy-detect": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
    },
    "unlock-detect": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_ENV": "${{ inputs.environment }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
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
    "unlock-detect",
    "comment-ops",
    "summary",
)

#: The whole detect steps no other guard pins whole, by workflow and action. `apply-run.yml`'s one
#: step serves both apply forms, with no `if:`: the action picks the script from `environment`.
_DETECT_STEPS = {
    ("apply-run.yml", "$/actions/apply-detect"): {
        "id": "d",
        "uses": "$/actions/apply-detect",
        "with": {
            "environment": "${{ inputs.environment }}",
            "auto": "${{ inputs.auto }}",
            "head-sha": "${{ inputs.ref }}",
            "pr-number": "${{ inputs.pr_number }}",
            "app-id": "${{ vars.SHIPMATE_APP_ID }}",
            "review-decision": "${{ steps.pr.outputs.decision }}",
            "github-vars": "${{ toJSON(vars) }}",
            "report-path": "${{ steps.report.outputs.path }}",
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

#: The whole `outputs:` of `apply-run.yml`'s `detect`, each read from the one step. A targeted apply
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
    "first_level": "${{ steps.d.outputs.first_level }}",
    "head_sha": "${{ steps.d.outputs.head_sha }}",
    "provenance_run": "${{ steps.d.outputs.provenance_run }}",
    "base_sha": "${{ steps.d.outputs.base_sha }}",
    "refusal": (
        "${{ steps.pr.outputs.refusal || steps.setup.outputs.refusal || steps.d.outputs.refusal }}"
    ),
    "refused_by_rule": "${{ steps.d.outputs.refused_by_rule }}",
    "tools": "${{ steps.setup.outputs.tools }}",
    "report_artifact": "${{ steps.report.outputs.name }}",
    "decision": "${{ steps.pr.outputs.decision }}",
}

#: The whole `outputs:` of `deploy.yml`'s `detect`. `tools` is the version pair every env-level's
#: cells install; dropped, each cell's `versions` reads '' and refuses.
_DEPLOY_DETECT_OUTPUTS = {
    "envlevel0_waves": "${{ steps.d.outputs.envlevel0_waves }}",
    "envlevel1_waves": "${{ steps.d.outputs.envlevel1_waves }}",
    "envlevel2_waves": "${{ steps.d.outputs.envlevel2_waves }}",
    "envlevel3_waves": "${{ steps.d.outputs.envlevel3_waves }}",
    "envlevel0_empty": "${{ steps.d.outputs.envlevel0_empty }}",
    "envlevel1_empty": "${{ steps.d.outputs.envlevel1_empty }}",
    "envlevel2_empty": "${{ steps.d.outputs.envlevel2_empty }}",
    "envlevel3_empty": "${{ steps.d.outputs.envlevel3_empty }}",
    "head_sha": "${{ steps.d.outputs.head_sha }}",
    "pr_number": "${{ steps.d.outputs.pr_number }}",
    "refused_locked": "${{ steps.d.outputs.refused_locked }}",
    "tools": "${{ steps.setup.outputs.tools }}",
    "warm": "${{ steps.d.outputs.warm }}",
    "max_parallel": "${{ steps.d.outputs.max_parallel }}",
    "push": "${{ steps.d.outputs.push }}",
}

#: Every (workflow, job, step) that carries `toJSON(vars)`: five detect steps, four cell steps,
#: the deploy `warm` step, the `comment-ops` step, the plan `summary` step and the plan
#: `defer-auto` step.
_VARS_HOLDERS = {
    ("plan.yml", "detect", "$/actions/build-matrix"),
    ("drift.yml", "detect", "$/actions/build-matrix"),
    ("apply-run.yml", "detect", "$/actions/apply-detect"),
    ("unlock.yml", "detect", "$/actions/unlock-detect"),
    ("deploy.yml", "detect", "$/actions/deploy-detect"),
    ("plan.yml", "plan", "$/actions/plan-cell"),
    ("drift.yml", "drift", "$/actions/drift-cell"),
    ("unlock.yml", "unlock", "$/actions/unlock-cell"),
    ("deploy.yml", "warm", "$/actions/warm"),
    ("cell.yml", "apply", "$/actions/apply-cell"),
    ("comment-ops.yml", "ops", "$/actions/comment-ops"),
    ("plan.yml", "summary", "$/actions/summary"),
    ("plan.yml", "defer-auto", "$/actions/auto-dispatch"),
}


@pytest.mark.parametrize("action", sorted(_SCRIPT_ENV))
def test_every_apply_side_detect_action_hands_its_script_exactly_these_names(action):
    """Mutation: delete `SHIPMATE_REVIEW_DECISION` from `apply-detect`'s script step.
    Mutation: delete `SHIPMATE_PR_NUMBER` from it. Mutation: delete `SHIPMATE_AUTO` from it.
    Mutation: read `SHIPMATE_REPORT_PATH` from anything but the `report-path` input."""
    doc = action_yaml(action)
    steps = [s for s in doc["runs"]["steps"] if "/../../scripts/" in str(s.get("run", ""))]
    assert len(steps) == 1, f"{action}: expected one script step, got {len(steps)}"
    assert steps[0]["env"] == _SCRIPT_ENV[action]


#: The whole `run:` of `apply-detect`'s script step. The script routes an empty environment
#: to the bare form itself; test_apply_detect's route test pins that.
_APPLY_DETECT_RUN = 'exec python3 "$GITHUB_ACTION_PATH/../../scripts/apply-detect"'


def _script_step(action):
    return next(s for s in action_yaml(action)["runs"]["steps"] if s.get("id") == "d")


def test_the_apply_detect_step_runs_exactly_this_script():
    """Mutation: restore the `if [ -z "$SHIPMATE_ENV" ]` branch to a second script."""
    assert _script_step("apply-detect")["run"] == _APPLY_DETECT_RUN


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
    Mutation: give `apply-run.yml`'s detect step an `if: ${{ inputs.environment != '' }}`.
    Mutation: delete `review-decision` from `apply-run.yml`'s detect step.
    Mutation: delete `pr-number` from `apply-run.yml`'s detect step.
    Mutation: read `review-decision` from `needs.review.outputs.decision`.
    Mutation: delete `auto` from `apply-run.yml`'s detect step; an unwired composite input is
    silently ignored."""
    doc = workflow_yaml(workflow)
    steps = [s for s in doc["jobs"]["detect"]["steps"] if s.get("uses") == uses]
    assert len(steps) == 1, f"{workflow}: {len(steps)} {uses} steps"
    assert steps[0] == _DETECT_STEPS[(workflow, uses)]


def test_the_deploy_detect_checkout_takes_the_full_history():
    """`deploy-detect` diffs against the previous default-branch tip, which a one-commit
    checkout does not hold. `blob:none` keeps that history and fetches file contents on demand.
    PyYAML gives the int 0, not "0".

    Mutation: delete `fetch-depth` from `deploy.yml`'s detect checkout.
    Mutation: delete `filter` from `deploy.yml`'s detect checkout.
    """
    got = [s.get("with") for s in checkouts(workflow_yaml("deploy.yml")["jobs"]["detect"]["steps"])]
    assert got == [{"fetch-depth": 0, "filter": "blob:none"}]


def test_the_apply_detect_checkout_is_one_blobless_full_history_step():
    """Every form merges the base tip into the head, which needs the merge base: one checkout
    of the full history, blobless, with no `if:`. PyYAML gives the int 0.

    Mutation: restore the shallow `!inputs.auto` checkout -- two steps, red.
    Mutation: drop `filter: blob:none` -- red."""
    got = [
        {k: v for k, v in s.items() if k != "uses"}
        for s in checkouts(workflow_yaml("apply-run.yml")["jobs"]["detect"]["steps"])
    ]
    assert got == [
        {"with": {"ref": "${{ inputs.ref }}", "fetch-depth": 0, "filter": "blob:none"}},
    ]


def test_the_apply_detect_job_outputs_exactly_these_expressions():
    """Mutations: change `envlevel1_empty` to read `steps.d.outputs.envlevel0_empty`; delete
    `refusal`, so the apply comment never names a detect refusal; add
    `report: ${{ steps.d.outputs.report }}`, a job output GitHub drops whole when any part of it
    is masked; restore `held_cells: ${{ steps.d.outputs.held_cells }}`; delete `provenance_run`;
    delete `base_sha`; drop `steps.setup.outputs.refusal ||` from `refusal`, so a setup refusal
    reaches the comment and the gate as the generic text; delete `tools`, so every wave cell
    refuses; read `refused_by_rule` as `steps.setup.outputs.refused_by_rule ||
    steps.d.outputs.refused_by_rule`, so a setup failure could read as a rule refusal; drop
    `steps.pr.outputs.refusal ||` from `refusal`; delete `decision`, so `lock` refuses every
    run; delete `first_level`, so `lock` snapshots no level and every level snapshots its own."""
    assert workflow_yaml("apply-run.yml")["jobs"]["detect"]["outputs"] == _APPLY_DETECT_OUTPUTS


def test_the_deploy_detect_job_outputs_exactly_these_expressions():
    """Mutations: delete `tools`, so every wave cell refuses; read it from `steps.d`; delete
    `push`, so `replan` re-plans no open pull request."""
    assert workflow_yaml("deploy.yml")["jobs"]["detect"]["outputs"] == _DEPLOY_DETECT_OUTPUTS


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


#: The whole `outputs:` of `actions/apply-detect`, hand-written. `apply-run.yml`'s `detect` reads
#: each by name, and an output the action stops declaring reads '' there. The comment's fields
#: are no output: they travel in the `apply-report` artifact.
_APPLY_DETECT_ACTION_OUTPUTS = {
    "levels": {
        "description": (
            "JSON list, one element per populated env-level in run order; each element is a JSON "
            'string of that level\'s `{"L<i>-W<w>": [cell row, ...]}` stage map. A targeted apply '
            "has one level."
        ),
        "value": "${{ steps.d.outputs.levels }}",
    },
    "stages": {
        "description": (
            "JSON list in run order of `{key, n, level, first}`: the stage key, its run position, "
            "its index into `levels`, and whether it is its level's first stage."
        ),
        "value": "${{ steps.d.outputs.stages }}",
    },
    "keys": {
        "description": "JSON list of the stage keys in run order.",
        "value": "${{ steps.d.outputs.keys }}",
    },
    "counts": {
        "description": "JSON list of each stage's cell count in run order.",
        "value": "${{ steps.d.outputs.counts }}",
    },
    "cells": {
        "description": "JSON list of every cell's `{stack, environment}`, in run order.",
        "value": "${{ steps.d.outputs.cells }}",
    },
    "empty": {
        "description": "'true' when no stage has a cell.",
        "value": "${{ steps.d.outputs.empty }}",
    },
    "head_sha": {
        "description": "The echoed head SHA.",
        "value": "${{ steps.d.outputs.head_sha }}",
    },
    "provenance_run": {
        "description": (
            "Auto form only: the autoplan run every applied cell's record names; empty when no "
            "cell applies."
        ),
        "value": "${{ steps.d.outputs.provenance_run }}",
    },
    "base_sha": {
        "description": (
            "The tip of the pull request's base branch, read from its ref (in the auto form "
            "after the mergeability wait), that the behind cells were computed on; empty when no "
            "cell was selected to apply."
        ),
        "value": "${{ steps.d.outputs.base_sha }}",
    },
    "refusal": {
        "description": (
            "The first error of the `::error::` message that failed the step (whatever its "
            "cause, a failed tool or API call included), without the prefix, its lines joined "
            "into one; empty when the step raised none. Set "
            "although the step fails, and read by the apply result comment. Branch-controlled "
            "text (it can name a stack path), so a reader passes it through `env:`."
        ),
        "value": "${{ steps.d.outputs.refusal }}",
    },
    "refused_by_rule": {
        "description": (
            "`true` when the step failed on a refusal by rule (a cell behind the base, or a "
            "targeted apply without its review); empty otherwise, a failed tool or API call "
            "included. Set although the step fails."
        ),
        "value": "${{ steps.d.outputs.refused_by_rule }}",
    },
}


def test_the_apply_detect_action_declares_exactly_these_outputs():
    """Mutations: add a `report` output to `actions/apply-detect/action.yml`; restore its
    `pusher` output; delete its `refusal` output; delete its `provenance_run` output; delete its
    `base_sha` output; restore `held_cells` in `base_sha`'s description; delete its
    `refused_by_rule` output; delete its `cells` output."""
    assert action_yaml("apply-detect")["outputs"] == _APPLY_DETECT_ACTION_OUTPUTS


#: The first step of `apply-run.yml`'s `detect`, hand-written.
_REPORT_SEED = {
    "id": "report",
    "shell": "bash",
    "run": (
        "set -euo pipefail\n"
        'path="$RUNNER_TEMP/apply-report/report.json"\n'
        'mkdir -p "${path%/*}"\n'
        "echo '{}' > \"$path\"\n"
        "token=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \\n')\n"
        'echo "path=$path" >> "$GITHUB_OUTPUT"\n'
        'echo "name=apply-report-$GITHUB_RUN_ATTEMPT-$token" >> "$GITHUB_OUTPUT"\n'
    ),
}

#: The last step of `apply-run.yml`'s `detect`, hand-written.
_REPORT_UPLOAD = {
    "name": "Upload the apply report",
    "if": "${{ !cancelled() && steps.report.outputs.name != '' }}",
    "continue-on-error": True,
    "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
    "with": {
        "name": "${{ steps.report.outputs.name }}",
        "path": "${{ steps.report.outputs.path }}",
        "if-no-files-found": "ignore",
        "retention-days": 7,
    },
}


def test_the_detect_job_seeds_its_report_first_and_uploads_it_last():
    """The job writes `{}` before checkout, setup or detect can refuse, and uploads the report
    after any of them failed, under a name unique to the call and attempt (`report_artifact`, in
    `_APPLY_DETECT_OUTPUTS`): every refusal leaves a report, and no re-run or second call reads
    another's.

    Mutations: move the seed step after `setup`; drop the attempt from the name; drop the random
    token; drop the upload's `if:`, so a refusal uploads nothing; upload from another path; drop
    `retention-days`."""
    steps = workflow_yaml("apply-run.yml")["jobs"]["detect"]["steps"]
    assert (steps[0], steps[-1]) == (_REPORT_SEED, _REPORT_UPLOAD)


def test_detect_reads_the_pull_request_before_it_checks_anything_out():
    """The checkout of `ref` follows the read that refuses a fork, a draft or a moved head, and
    the report comes first so that refusal leaves one.

    Mutation: move `pr` after the checkout."""
    steps = workflow_yaml("apply-run.yml")["jobs"]["detect"]["steps"]
    got = [s.get("id") or s["uses"].split("@")[0] for s in steps]
    assert got == ["report", "pr", "actions/checkout", "setup", "d", "actions/upload-artifact"]
