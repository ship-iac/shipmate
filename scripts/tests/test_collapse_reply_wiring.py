"""Guard the steps that run scripts/collapse-reply once a dispatched run posted its result.

Threat model: an accidental regression of the step's condition, order, env or command line. The
collapse only decides what a reader sees, so it runs last, after anything the job's result rests
on, and never fails the job.
"""

from _loader import action_steps, run_lines, step_by

COLLAPSE = "Collapse the started reply"

#: The whole `run:` of both collapse steps, hand-written.
COLLAPSE_RUN = [
    "set -euo pipefail",
    (
        'timeout 120 python3 "$GITHUB_ACTION_PATH/../../scripts/collapse-reply" || echo '
        '"::warning::the started reply was not collapsed (exit $?); the result comment is posted"'
    ),
]


def _env(verb):
    return {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        "SHIPMATE_REPLY_VERB": verb,
    }


def test_the_summary_collapse_runs_last_on_a_written_on_demand_plan_comment():
    """Only a `shipmate plan` run collapses, only once its comment was created or updated, and
    after `Create/refresh gate`, so no cosmetic call delays the gate.

    Mutations: drop `inputs.on-demand == 'true' &&`; drop the `updated` alternative; swap the
    step above `Create/refresh gate`; rename the upsert step's `id`; drop `timeout 120`; drop the
    `|| echo` fallback; `SHIPMATE_REPLY_VERB: apply`.
    """
    names = [s.get("name") for s in action_steps("summary")]
    assert names[-3:] == ["Create/refresh gate", "Dispatch an auto apply", COLLAPSE]
    collapse = step_by("summary", name=COLLAPSE)
    assert collapse["if"] == (
        "${{ inputs.on-demand == 'true' && steps.hold.outputs.superseded != 'true'"
        " && (steps.upsert.outputs.result == 'created'"
        " || steps.upsert.outputs.result == 'updated') }}"
    )
    assert step_by("summary", id="upsert")["name"] == "Upsert sticky comment"
    assert run_lines(collapse) == COLLAPSE_RUN
    assert collapse["env"] == _env("plan")


def test_the_apply_summary_collapse_runs_last_directly_after_the_result_post():
    """No `if:`: the default `success()` collapses only when the post succeeded.

    Mutations: move the step above `Post apply result comment`; add `if: always()`; drop
    `timeout 120`; `SHIPMATE_REPLY_VERB: plan`.
    """
    names = [s.get("name") for s in action_steps("apply-summary")]
    assert names[-2:] == ["Post apply result comment", COLLAPSE]
    collapse = step_by("apply-summary", name=COLLAPSE)
    assert "if" not in collapse
    assert run_lines(collapse) == COLLAPSE_RUN
    assert collapse["env"] == _env("apply")
