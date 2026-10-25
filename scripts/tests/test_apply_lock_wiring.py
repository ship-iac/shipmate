"""`apply.yml`'s `lock` job acquires every cell the waves apply, before the first wave.

Threat model: accidental regression in the job's wiring. A dropped `envlevelN-waves` input leaves
that level's cells applying unlocked; a dropped `environment` leaves the mint with no App key; an
`always()` `if:` acquires after `detect` failed. The job is compared whole against a
hand-written constant; `DOWNSTREAM_NEEDS` in `test_apply_dispatch_actor_guard.py` pins that every
wave level needs `lock`.
"""

from _loader import ENGINE_ENVIRONMENT, workflow_jobs

LOCK_JOB = {
    "needs": ["guard", "review", "detect"],
    "if": "${{ !failure() && !cancelled() }}",
    "runs-on": "${{ inputs.runs_on }}",
    "environment": ENGINE_ENVIRONMENT,
    "permissions": {},
    "outputs": {"refusal": "${{ steps.l.outputs.refusal }}"},
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
    an `actions/checkout` step."""
    assert workflow_jobs("apply.yml")["lock"] == LOCK_JOB
