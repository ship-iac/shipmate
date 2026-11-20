"""`apply-run.yml`'s `lock` job acquires every cell the waves apply, before the first wave.

Threat model: accidental regression in the job's wiring. A dropped `envlevelN-waves` input leaves
that level's cells applying unlocked; a dropped `environment` leaves the mint with no App key; an
`always()` `if:` acquires after `detect` failed. The job is compared whole against a
hand-written constant; `DOWNSTREAM_NEEDS` in `test_apply_dispatch_actor_guard.py` pins that every
wave level needs `lock`.
"""

from _loader import ENGINE_ENVIRONMENT, action_yaml, workflow_jobs

LOCK_JOB = {
    "needs": ["review", "detect"],
    "if": "${{ !failure() && !cancelled() }}",
    "runs-on": "${{ inputs.runs_on }}",
    "environment": ENGINE_ENVIRONMENT,
    "permissions": {},
    "outputs": {
        "refusal": "${{ steps.l.outputs.refusal }}",
        "refused_by_rule": "${{ steps.l.outputs.refused_by_rule }}",
    },
    "steps": [
        {
            "id": "l",
            "uses": "$/actions/lock-acquire",
            "with": {
                **{
                    f"envlevel{n}-waves": f"${{{{ needs.detect.outputs.envlevel{n}_waves }}}}"
                    for n in range(4)
                },
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}


def test_the_lock_job_acquires_every_level_with_the_engine_key():
    """Mutations: pass only `envlevel0-waves`; remove `environment`; `if:` to `always()`; add
    an `actions/checkout` step; drop the `refused_by_rule` output."""
    assert workflow_jobs("apply-run.yml")["lock"] == LOCK_JOB


#: The whole `outputs:` of `actions/lock-acquire/action.yml`, hand-written.
_LOCK_ACQUIRE_OUTPUTS = {
    "refusal": {
        "description": (
            "The first error of the `::error::` message that failed the step (whatever its "
            "cause, a failed API call included), without the prefix, its lines joined into one; "
            "empty when the step raised none. Set although the step fails. Branch-controlled text "
            "(it can name a stack path), so a reader passes it through `env:`."
        ),
        "value": "${{ steps.a.outputs.refusal }}",
    },
    "refused_by_rule": {
        "description": (
            "`true` when the step failed because another pull request or an unknown holder locks "
            "a cell; empty otherwise, a failed API call or a refusal whose rollback failed "
            "included. Set although the step fails."
        ),
        "value": "${{ steps.a.outputs.refused_by_rule }}",
    },
}


def test_the_lock_acquire_action_declares_exactly_these_outputs():
    """Mutations: delete the `refused_by_rule` output; read it from `steps.l` for `steps.a`;
    read `refusal` from `steps.l`."""
    assert action_yaml("lock-acquire")["outputs"] == _LOCK_ACQUIRE_OUTPUTS
