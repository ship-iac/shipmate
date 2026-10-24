"""`apply.yml`'s `lock` job acquires every cell the waves apply, before the first wave.

Threat model: accidental regression in the job's wiring. A dropped `envlevelN-waves` input leaves
that level's cells applying unlocked; a dropped `environment` leaves the mint with no App key; an
`always()` `if:` acquires after `detect` failed; a wave job that stops needing `lock` starts
before the cells are held. Each is compared whole against a hand-written constant.
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

NEEDS = {
    "envlevel0": ["guard", "review", "detect", "lock"],
    "envlevel1": ["guard", "review", "detect", "lock", "envlevel0"],
    "envlevel2": ["guard", "review", "detect", "lock", "envlevel0", "envlevel1"],
    "envlevel3": ["guard", "review", "detect", "lock", "envlevel0", "envlevel1", "envlevel2"],
    "summary": [
        "guard",
        "review",
        "detect",
        "lock",
        "envlevel0",
        "envlevel1",
        "envlevel2",
        "envlevel3",
    ],
}


def test_the_lock_job_acquires_every_level_with_the_engine_key():
    """Mutations: pass only `envlevel0-waves`; remove `environment`; `if:` to `always()`; add
    an `actions/checkout` step."""
    assert workflow_jobs("apply.yml")["lock"] == LOCK_JOB


def test_every_wave_level_and_the_summary_need_the_lock():
    """Mutation: drop `lock` from `envlevel0`'s `needs`."""
    jobs = workflow_jobs("apply.yml")
    assert {job: jobs[job]["needs"] for job in NEEDS} == NEEDS
