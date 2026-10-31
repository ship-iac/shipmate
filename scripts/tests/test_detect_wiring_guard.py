"""Guards what the workflows and actions hand the detect scripts.

- Each apply-side detect action hands its script exactly the names it reads. The whole `env:`
  block against a hand-written constant, so a renamed or dropped binding cannot hide behind a
  present input. `build-matrix`'s block is pinned by
  `test_build_matrix.py::test_build_matrix_action_hands_the_script_the_names_it_reads`.
- Every detect action, `comment-ops` and `summary` declares `github-vars`, and every detect call
  site passes it `toJSON(vars)`, so `env-config` can resolve a `{vars: NAME}` reference in
  the table. The
  `with:` of `plan.yml`, `drift.yml` and `unlock.yml`'s detect steps is pinned whole beside the
  rest of those workflows; the other two steps are pinned here whole, beside `apply.yml`'s
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
        "SHIPMATE_AUTO": "${{ inputs.auto }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
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

#: The whole detect steps no other guard pins whole, by workflow and action. `apply.yml`'s one
#: step serves both apply forms, with no `if:`: the action picks the script from `environment`.
_DETECT_STEPS = {
    ("apply.yml", "$/actions/apply-detect"): {
        "id": "d",
        "uses": "$/actions/apply-detect",
        "with": {
            "environment": "${{ inputs.environment }}",
            "auto": "${{ inputs.auto }}",
            "head-sha": "${{ inputs.ref }}",
            "pr-number": "${{ inputs.pr_number }}",
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
    "skipped_after": "${{ steps.d.outputs.skipped_after }}",
    "review_held_envs": "${{ steps.d.outputs.review_held_envs }}",
    "applied_exempt_envs": "${{ steps.d.outputs.applied_exempt_envs }}",
    "review_not_required_envs": "${{ steps.d.outputs.review_not_required_envs }}",
    "waiting_cells": "${{ steps.d.outputs.waiting_cells }}",
    "held_cells": "${{ steps.d.outputs.held_cells }}",
    "env_order": "${{ steps.d.outputs.env_order }}",
    "provenance_run": "${{ steps.d.outputs.provenance_run }}",
    "pusher": "${{ steps.d.outputs.pusher }}",
    "base_sha": "${{ steps.d.outputs.base_sha }}",
    "bare_envs": "${{ steps.d.outputs.bare_envs }}",
    "refusal": "${{ steps.setup.outputs.refusal || steps.d.outputs.refusal }}",
}

#: Every (workflow, job, step) that carries `toJSON(vars)`: five detect steps, eleven cell steps,
#: the `comment-ops` step and the plan `summary` step.
_VARS_HOLDERS = {
    ("plan.yml", "detect", "$/actions/build-matrix"),
    ("drift.yml", "detect", "$/actions/build-matrix"),
    ("apply.yml", "detect", "$/actions/apply-detect"),
    ("unlock.yml", "detect", "$/actions/unlock-detect"),
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
    """Mutation: delete `SHIPMATE_REVIEW_DECISION` from `apply-detect`'s script step.
    Mutation: delete `SHIPMATE_PR_NUMBER` from it. Mutation: delete `SHIPMATE_AUTO` from it."""
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
    Mutation: give `apply.yml`'s detect step an `if: ${{ inputs.environment != '' }}`.
    Mutation: delete `review-decision` from `apply.yml`'s detect step.
    Mutation: delete `pr-number` from `apply.yml`'s detect step.
    Mutation: delete `auto` from `apply.yml`'s detect step; an unwired composite input is
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
    checkouts = [
        s.get("with")
        for s in workflow_yaml("deploy.yml")["jobs"]["detect"]["steps"]
        if str(s.get("uses", "")).split("@")[0] == "actions/checkout"
    ]
    assert checkouts == [{"fetch-depth": 0, "filter": "blob:none"}]


def test_the_apply_detect_checkouts_are_shallow_and_full_history_for_auto():
    """Exactly one runs: the auto form computes the merge base of the base tip and the head, which
    a one-commit checkout does not hold. Two steps, because `fetch-depth: ${{ inputs.auto && 0 ||
    1 }}` reads `0` as falsy and always fetches one commit. PyYAML gives the int 0.

    Mutation: delete `fetch-depth` from the auto checkout.
    Mutation: drop the `!` from the first checkout's `if:`, so an auto run checks out twice and
    a bare one not at all.
    Mutation: delete the auto checkout's `if:`."""
    checkouts = [
        {k: v for k, v in s.items() if k != "uses"}
        for s in workflow_yaml("apply.yml")["jobs"]["detect"]["steps"]
        if str(s.get("uses", "")).split("@")[0] == "actions/checkout"
    ]
    assert checkouts == [
        {"if": "${{ !inputs.auto }}", "with": {"ref": "${{ inputs.ref }}"}},
        {"if": "${{ inputs.auto }}", "with": {"ref": "${{ inputs.ref }}", "fetch-depth": 0}},
    ]


def test_the_apply_detect_job_outputs_exactly_these_expressions():
    """Mutations: change `envlevel1_empty` to read `steps.d.outputs.envlevel0_empty`; delete
    `refusal`, so the apply comment never names a detect refusal; delete `env_order`, so it
    orders environments by name; delete `held_cells`; delete `provenance_run`; delete
    `base_sha`; delete `skipped_after`; delete `bare_envs`; delete `pusher`; drop
    `steps.setup.outputs.refusal ||` from `refusal`, so a setup refusal reaches the comment
    and the gate as the generic text."""
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


#: The whole `outputs:` of `actions/apply-detect`, hand-written. `apply.yml`'s `detect` reads
#: each by name, and an output the action stops declaring reads '' there: a dropped
#: `waiting_cells` renders a fully waiting targeted apply as `🟢 no pending applies`.
_APPLY_DETECT_ACTION_OUTPUTS = {
    "envlevel0_waves": {
        "description": (
            "JSON wave0..wave7 map for env-level 0. A targeted apply puts every cell here."
        ),
        "value": "${{ steps.d.outputs.envlevel0_waves }}",
    },
    **{
        f"envlevel{n}_waves": {
            "description": f"JSON wave0..wave7 map for env-level {n}",
            "value": f"${{{{ steps.d.outputs.envlevel{n}_waves }}}}",
        }
        for n in (1, 2, 3)
    },
    **{
        f"envlevel{n}_empty": {
            "description": f"'true' when env-level {n} has no cell in any wave.",
            "value": f"${{{{ steps.d.outputs.envlevel{n}_empty }}}}",
        }
        for n in (0, 1, 2, 3)
    },
    "head_sha": {
        "description": "The echoed head SHA.",
        "value": "${{ steps.d.outputs.head_sha }}",
    },
    "excluded_envs": {
        "description": (
            "Bare and auto forms: JSON list of targeted envs left pending, including held ones; "
            "the comment lists a held one under the hold. The targeted form writes none."
        ),
        "value": "${{ steps.d.outputs.excluded_envs }}",
    },
    "skipped_envs": {
        "description": (
            "Bare and auto forms: JSON list of envs skipped because they are ordered after an "
            "env not fully applying this run (held for review, excluded as targeted, holding a "
            "waiting cell, holding a cell another pull request locks, or, under the auto form, "
            "holding a cell the base also changed or not `apply_mode: auto`). The targeted form "
            "writes none."
        ),
        "value": "${{ steps.d.outputs.skipped_envs }}",
    },
    "skipped_after": {
        "description": (
            "Bare and auto forms: JSON object mapping each env in skipped_envs to its transitive "
            "`needs` predecessors not fully applying for a cause of their own (targeted, held, "
            "holding a waiting, locked or overlap cell, or, under the auto form, not "
            "`apply_mode: auto`). The targeted form writes none."
        ),
        "value": "${{ steps.d.outputs.skipped_after }}",
    },
    "review_held_envs": {
        "description": (
            "Bare and auto forms: JSON list of envs held because the PR is not reviewed. The "
            "targeted form writes none."
        ),
        "value": "${{ steps.d.outputs.review_held_envs }}",
    },
    "applied_exempt_envs": {
        "description": (
            "Bare and auto forms: JSON list of envs applied without an approving review "
            "because their entry holds `review: false`. The targeted form writes none."
        ),
        "value": "${{ steps.d.outputs.applied_exempt_envs }}",
    },
    "review_not_required_envs": {
        "description": (
            "JSON list of `review: true` envs authorized under a null review decision, because "
            "the pull request's review state required none, else []."
        ),
        "value": "${{ steps.d.outputs.review_not_required_envs }}",
    },
    "waiting_cells": {
        "description": (
            "JSON list of cells left pending because a cell they run after (CONTRACT.md "
            "§Fan-out) is pending and not applying this run, each with that cell; both forms."
        ),
        "value": "${{ steps.d.outputs.waiting_cells }}",
    },
    "held_cells": {
        "description": (
            "JSON list of pending cells left pending and not applying this run for a cause of "
            "their own, each `{stack, environment, cause}`. `cause` is `locked` when another "
            "pull request holds the cell's lock, and such a cell also carries `holder`, that "
            "pull request's number (null when the lock's holder is unknown); both forms. "
            "`cause` is `overlap` when the merge of the base into the branch changes the cell's "
            "stack, directly or through a module it sources, or removes files in it, so its plan "
            "would revert that change; auto form only. "
            "A cell both locked and overlapping is listed once per cause."
        ),
        "value": "${{ steps.d.outputs.held_cells }}",
    },
    "env_order": {
        "description": (
            "JSON list of every environment the apply checks on the head name, in apply order "
            "(the env-levels of the table's `needs`, ties by name); the targeted form names its "
            "one environment. Read by the apply result comment to order its environment headings."
        ),
        "value": "${{ steps.d.outputs.env_order }}",
    },
    "provenance_run": {
        "description": (
            "Auto form only: the autoplan run every applied cell's record names; empty when no "
            "cell applies."
        ),
        "value": "${{ steps.d.outputs.provenance_run }}",
    },
    "pusher": {
        "description": (
            "Auto form only: the login of that autoplan run's `actor`, after the pusher check "
            "admits it; empty when no cell applies. Read by the apply result comment to name the "
            "pusher."
        ),
        "value": "${{ steps.d.outputs.pusher }}",
    },
    "base_sha": {
        "description": (
            "Auto form only: the tip of the pull request's base branch, read from its ref after "
            "the mergeability wait, that the overlap cells in held_cells were computed on; empty "
            "when no environment had a pending cell to consider."
        ),
        "value": "${{ steps.d.outputs.base_sha }}",
    },
    "bare_envs": {
        "description": (
            "Auto form only: JSON list of pending envs neither `apply_mode: auto`, targeted nor "
            "held, left for a bare `shipmate apply`."
        ),
        "value": "${{ steps.d.outputs.bare_envs }}",
    },
    "refusal": {
        "description": (
            "The first error of the `::error::` message that failed the step (a refusal or a "
            "failed tool or API call), without the prefix, its lines joined into one; empty when "
            "the step raised none. Set "
            "although the step fails, and read by the apply result comment. Branch-controlled "
            "text (it can name a stack path), so a reader passes it through `env:`."
        ),
        "value": "${{ steps.d.outputs.refusal }}",
    },
}


def test_the_apply_detect_action_declares_exactly_these_outputs():
    """Mutations: delete the `waiting_cells` output from `actions/apply-detect/action.yml`;
    delete its `refusal` output; delete its `env_order` output; delete its `held_cells`
    output; restore `skipped_envs`' description without the lock cause; delete its
    `provenance_run` output; delete its `base_sha` output; drop the overlap cause from
    `held_cells`' description; restore `skipped_envs`' description without the overlap cause;
    delete its `skipped_after` output; delete its `bare_envs` output; delete its `pusher`
    output."""
    assert action_yaml("apply-detect")["outputs"] == _APPLY_DETECT_ACTION_OUTPUTS
