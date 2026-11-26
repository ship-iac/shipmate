"""Guards the per-wave apply-check flip and the level's completion in `apply-env-level.yml`.

Each `waveN-release` job flips its wave's checks to `in_progress` right before that wave, from
the `by_wave` slice the caller supplied, or else `snapshot`'s; the wave needs it and runs only
when it succeeded, and it runs only when every earlier wave and release succeeded or was
skipped. A caller that supplies `by_wave` has already flipped `wave0`, so `snapshot` and
`wave0-release` skip and `wave0` runs without them. Each cell's `save` completes its own
checks; `complete` runs only when a release or wave failed or was cancelled, or the run was
cancelled, waits for every release and wave, rewrites every check and fails the level on a check
it cannot write.

Threat model: an accidental edit to the workflow (a dropped clause, a wrong slice, a moved
`needs:` entry). Whole parsed values against hand-written constants cover it.
"""

import pytest
from _loader import workflow_jobs

WAVES = [f"wave{n}" for n in range(8)]

_WITH_KEY = {
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}


COMPLETE = {
    "needs": [
        "snapshot",
        "wave0-release",
        "wave1-release",
        "wave2-release",
        "wave3-release",
        "wave4-release",
        "wave5-release",
        "wave6-release",
        "wave7-release",
        "wave0",
        "wave1",
        "wave2",
        "wave3",
        "wave4",
        "wave5",
        "wave6",
        "wave7",
    ],
    "if": (
        "${{ always() && (needs.snapshot.result == 'success' || inputs.by_wave != '') && "
        "(cancelled() || contains(join(needs.*.result, ','), 'failure') || "
        "contains(join(needs.*.result, ','), 'cancelled')) }}"
    ),
    "runs-on": "ubuntu-slim",
    "permissions": {"actions": "read", "checks": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "env": {
        "WAVE_RESULTS": (
            '{"wave0": "${{ needs.wave0.result }}", '
            '"wave1": "${{ needs.wave1.result }}", '
            '"wave2": "${{ needs.wave2.result }}", '
            '"wave3": "${{ needs.wave3.result }}", '
            '"wave4": "${{ needs.wave4.result }}", '
            '"wave5": "${{ needs.wave5.result }}", '
            '"wave6": "${{ needs.wave6.result }}", '
            '"wave7": "${{ needs.wave7.result }}"}'
        )
    },
    "steps": [
        {
            "uses": "$/actions/apply-complete",
            "with": {
                "by-wave": "${{ inputs.by_wave || needs.snapshot.outputs.by_wave }}",
                "wave-results": "${{ env.WAVE_RESULTS }}",
                "job-id": "${{ job.check_run_id }}",
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}

JOB_IDS = sorted(["complete", "snapshot", *WAVES] + [f"wave{n}-release" for n in range(8)])

WAVE_NEEDS = {
    "wave0": ["snapshot", "wave0-release"],
    "wave1": ["snapshot", "wave0", "wave1-release"],
    "wave2": ["snapshot", "wave0", "wave1", "wave2-release"],
    "wave3": ["snapshot", "wave0", "wave1", "wave2", "wave3-release"],
    "wave4": ["snapshot", "wave0", "wave1", "wave2", "wave3", "wave4-release"],
    "wave5": ["snapshot", "wave0", "wave1", "wave2", "wave3", "wave4", "wave5-release"],
    "wave6": ["snapshot", "wave0", "wave1", "wave2", "wave3", "wave4", "wave5", "wave6-release"],
    "wave7": [
        "snapshot",
        "wave0",
        "wave1",
        "wave2",
        "wave3",
        "wave4",
        "wave5",
        "wave6",
        "wave7-release",
    ],
}

#: Each wave's `if:`, whole: its own eligibility, then its release's success.
WAVE_IF = {
    "wave0": (
        "${{ !failure() && !cancelled() && toJSON(fromJSON(inputs.waves_json).wave0) != '[]' "
        "&& (needs.wave0-release.result == 'success' || inputs.by_wave != '') }}"
    ),
    **{
        f"wave{n}": (
            "${{ !failure() && !cancelled() && !contains(join(needs.*.result, ','), 'cancelled') "
            f"&& toJSON(fromJSON(inputs.waves_json).wave{n}) != '[]' "
            f"&& needs.wave{n}-release.result == 'success' }}}}"
        )
        for n in range(1, 8)
    },
}


def _release(n):
    snapshot_ok = "needs.snapshot.result == 'success'"
    by_wave = "needs.snapshot.outputs.by_wave"
    if n:
        snapshot_ok = "(needs.snapshot.result == 'success' || inputs.by_wave != '')"
        by_wave = "inputs.by_wave || needs.snapshot.outputs.by_wave"
    earlier = "".join(
        f" && (needs.wave{k}.result == 'success' || needs.wave{k}.result == 'skipped')"
        f" && (needs.wave{k}-release.result == 'success'"
        f" || needs.wave{k}-release.result == 'skipped')"
        for k in range(n)
    )
    return {
        "needs": ["snapshot", *(j for k in range(n) for j in (f"wave{k}", f"wave{k}-release"))],
        "if": (
            f"${{{{ !cancelled() && {snapshot_ok} && "
            f"toJSON(fromJSON(inputs.waves_json).wave{n}) != '[]'{earlier} }}}}"
        ),
        "runs-on": "ubuntu-slim",
        "permissions": {"actions": "read"},
        "environment": {"name": "shipmate-engine", "deployment": False},
        "steps": [
            {
                "uses": "$/actions/apply-release",
                "with": {
                    "snapshot": f"${{{{ toJSON(fromJSON({by_wave}).wave{n}) }}}}",
                    "deploy": "${{ inputs.deploy }}",
                    "pr-number": "${{ inputs.pr_number }}",
                    **_WITH_KEY,
                },
            }
        ],
    }


#: Written out in full: a template-only guard cannot catch a wrong template.
WAVE0_RELEASE = {
    "needs": ["snapshot"],
    "if": (
        "${{ !cancelled() && needs.snapshot.result == 'success' && "
        "toJSON(fromJSON(inputs.waves_json).wave0) != '[]' }}"
    ),
    "runs-on": "ubuntu-slim",
    "permissions": {"actions": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "steps": [
        {
            "uses": "$/actions/apply-release",
            "with": {
                "snapshot": "${{ toJSON(fromJSON(needs.snapshot.outputs.by_wave).wave0) }}",
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}

WAVE2_RELEASE = {
    "needs": ["snapshot", "wave0", "wave0-release", "wave1", "wave1-release"],
    "if": (
        "${{ !cancelled() && (needs.snapshot.result == 'success' || inputs.by_wave != '') && "
        "toJSON(fromJSON(inputs.waves_json).wave2) != '[]' && "
        "(needs.wave0.result == 'success' || needs.wave0.result == 'skipped') && "
        "(needs.wave0-release.result == 'success' || needs.wave0-release.result == 'skipped') && "
        "(needs.wave1.result == 'success' || needs.wave1.result == 'skipped') && "
        "(needs.wave1-release.result == 'success' || needs.wave1-release.result == 'skipped') }}"
    ),
    "runs-on": "ubuntu-slim",
    "permissions": {"actions": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "steps": [
        {
            "uses": "$/actions/apply-release",
            "with": {
                "snapshot": (
                    "${{ toJSON(fromJSON(inputs.by_wave || needs.snapshot.outputs.by_wave)"
                    ".wave2) }}"
                ),
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}


def test_complete_runs_only_when_the_level_did_not_succeed():
    """A level that succeeded skips `complete`: each `save` completed its own cell. `complete`
    still fails the level, so it carries no `continue-on-error`, and no flip may land after it
    starts.

    Mutations: drop `cancelled() ||` from `complete.if`; drop its
    `contains(join(needs.*.result, ','), 'failure') ||` term; drop `wave4-release` from
    `complete.needs`; add `continue-on-error: true` to `complete`; `wave5` under `"wave6"` in
    its `env`; drop `|| inputs.by_wave != ''` from its `if:`."""
    assert workflow_jobs("apply-env-level.yml")["complete"] == COMPLETE


def test_each_wave_needs_snapshot_its_release_and_every_earlier_wave():
    """Mutations: drop `snapshot` from `wave2.needs`, whose `check_ids` lookup then reads an empty
    `needs.snapshot`; drop `wave3-release` from `wave3.needs`."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert {w: jobs[w]["needs"] for w in WAVES} == WAVE_NEEDS


def test_each_wave_runs_only_after_its_release_succeeded():
    """Mutations: drop `&& needs.wave5-release.result == 'success'` from `wave5.if`; `!=` for
    `==` in it; drop `|| inputs.by_wave != ''` from `wave0.if`."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert {w: jobs[w]["if"] for w in WAVES} == WAVE_IF


@pytest.mark.parametrize("n", range(8))
def test_each_release_job_is_exactly(n):
    """Mutations: drop `snapshot` from `wave2-release.needs`; drop the `!cancelled()` prefix from
    `wave4-release.if`; `'failure'` for `'skipped'` in one clause of `wave6-release.if`; add
    `continue-on-error: true` to `wave1-release`; `.wave2` in `wave3-release`'s slice;
    `wave3-release`'s slice reads `needs.snapshot.outputs.by_wave` alone."""
    assert workflow_jobs("apply-env-level.yml")[f"wave{n}-release"] == _release(n)


@pytest.mark.parametrize(("n", "expected"), [(0, WAVE0_RELEASE), (2, WAVE2_RELEASE)])
def test_release_jobs_are_written_out(n, expected):
    """Mutations: drop `snapshot` from `wave2-release.needs`; drop the `!cancelled()` prefix
    from `wave0-release.if`."""
    assert workflow_jobs("apply-env-level.yml")[f"wave{n}-release"] == expected


#: Runs only when the caller supplied no `by_wave`, and publishes only the per-wave map.
SNAPSHOT = {
    "if": "${{ inputs.by_wave == '' }}",
    "runs-on": "ubuntu-slim",
    "permissions": {"checks": "read", "actions": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "outputs": {"by_wave": "${{ steps.snap.outputs.by_wave }}"},
    "steps": [
        {
            "uses": "$/actions/verify-environments",
            "with": {"waves-json": "${{ inputs.waves_json }}"},
        },
        {
            "id": "snap",
            "uses": "$/actions/apply-snapshot",
            "with": {
                "head-sha": "${{ inputs.head_sha }}",
                "waves-json": "${{ inputs.waves_json }}",
                **_WITH_KEY,
            },
        },
    ],
}


def test_snapshot_job_is_exactly():
    """Mutations: drop `snapshot`'s `by_wave` output; add back a `snapshot` output;
    `snapshot.if` -> `inputs.by_wave != ''`."""
    assert workflow_jobs("apply-env-level.yml")["snapshot"] == SNAPSHOT


def test_the_job_set_is_exactly():
    """Mutations: delete `wave6-release`; re-add a `wave0-complete` job."""
    assert sorted(workflow_jobs("apply-env-level.yml")) == JOB_IDS
