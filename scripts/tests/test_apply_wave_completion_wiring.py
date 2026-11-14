"""Guards the per-wave apply-check flip and completion in `apply-env-level.yml`.

Each `waveN-release` job flips its wave's checks to `in_progress` right before that wave, from
`snapshot`'s `by_wave` slice; the wave needs it and runs only when it succeeded, and it runs only
when every earlier wave and release succeeded or was skipped. Each `waveN-complete` job writes
its own wave's checks right after that wave. It runs only when its wave reached a result: a
skipped wave's listing row carries the literal matrix template, so a completion over it matches
no cell and fails after the action's retry. It never fails the level (job-level
`continue-on-error`); `complete` waits for every release, wave and completion, rewrites every
check and still fails the level on a check it cannot write. No wave waits for a completion, so a
completion never delays an apply, and no completion runs before its wave.

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


def _completion(n):
    return {
        "needs": ["snapshot", f"wave{n}-release", f"wave{n}"],
        "if": (
            "${{ always() && needs.snapshot.result == 'success' && "
            f"(needs.wave{n}.result == 'success' || needs.wave{n}.result == 'failure') }}}}"
        ),
        "runs-on": "ubuntu-slim",
        "continue-on-error": True,
        "permissions": {"actions": "read", "checks": "read"},
        "environment": {"name": "shipmate-engine", "deployment": False},
        "steps": [
            {
                "uses": "$/actions/apply-complete",
                "with": {
                    "by-wave": "${{ needs.snapshot.outputs.by_wave }}",
                    "releases": f'{{"wave{n}": "${{{{ needs.wave{n}-release.result }}}}"}}',
                    "deploy": "${{ inputs.deploy }}",
                    "pr-number": "${{ inputs.pr_number }}",
                    **_WITH_KEY,
                },
            }
        ],
    }


#: Written out in full: a template-only guard cannot catch a wrong template.
WAVE3_COMPLETE = {
    "needs": ["snapshot", "wave3-release", "wave3"],
    "if": (
        "${{ always() && needs.snapshot.result == 'success' && "
        "(needs.wave3.result == 'success' || needs.wave3.result == 'failure') }}"
    ),
    "runs-on": "ubuntu-slim",
    "continue-on-error": True,
    "permissions": {"actions": "read", "checks": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "steps": [
        {
            "uses": "$/actions/apply-complete",
            "with": {
                "by-wave": "${{ needs.snapshot.outputs.by_wave }}",
                "releases": '{"wave3": "${{ needs.wave3-release.result }}"}',
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
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
        "wave0-complete",
        "wave1-complete",
        "wave2-complete",
        "wave3-complete",
        "wave4-complete",
        "wave5-complete",
        "wave6-complete",
        "wave7-complete",
    ],
    "if": "${{ always() && needs.snapshot.result == 'success' }}",
    "runs-on": "ubuntu-slim",
    "permissions": {"actions": "read", "checks": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "steps": [
        {
            "uses": "$/actions/apply-complete",
            "with": {
                "by-wave": "${{ needs.snapshot.outputs.by_wave }}",
                "releases": (
                    '{"wave0": "${{ needs.wave0-release.result }}", '
                    '"wave1": "${{ needs.wave1-release.result }}", '
                    '"wave2": "${{ needs.wave2-release.result }}", '
                    '"wave3": "${{ needs.wave3-release.result }}", '
                    '"wave4": "${{ needs.wave4-release.result }}", '
                    '"wave5": "${{ needs.wave5-release.result }}", '
                    '"wave6": "${{ needs.wave6-release.result }}", '
                    '"wave7": "${{ needs.wave7-release.result }}"}'
                ),
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}

JOB_IDS = sorted(
    ["complete", "snapshot", *WAVES]
    + [f"wave{n}-complete" for n in range(8)]
    + [f"wave{n}-release" for n in range(8)]
)

WAVE_NEEDS = {
    "wave0": ["snapshot", "wave0-release"],
    "wave1": ["wave0", "wave1-release"],
    "wave2": ["wave0", "wave1", "wave2-release"],
    "wave3": ["wave0", "wave1", "wave2", "wave3-release"],
    "wave4": ["wave0", "wave1", "wave2", "wave3", "wave4-release"],
    "wave5": ["wave0", "wave1", "wave2", "wave3", "wave4", "wave5-release"],
    "wave6": ["wave0", "wave1", "wave2", "wave3", "wave4", "wave5", "wave6-release"],
    "wave7": ["wave0", "wave1", "wave2", "wave3", "wave4", "wave5", "wave6", "wave7-release"],
}

#: Each wave's `if:`, whole: its own eligibility, then its release's success.
WAVE_IF = {
    "wave0": (
        "${{ !failure() && !cancelled() && toJSON(fromJSON(inputs.waves_json).wave0) != '[]' "
        "&& needs.wave0-release.result == 'success' }}"
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
    earlier = "".join(
        f" && (needs.wave{k}.result == 'success' || needs.wave{k}.result == 'skipped')"
        f" && (needs.wave{k}-release.result == 'success'"
        f" || needs.wave{k}-release.result == 'skipped')"
        for k in range(n)
    )
    return {
        "needs": ["snapshot", *(j for k in range(n) for j in (f"wave{k}", f"wave{k}-release"))],
        "if": (
            "${{ !cancelled() && needs.snapshot.result == 'success' && "
            f"toJSON(fromJSON(inputs.waves_json).wave{n}) != '[]'{earlier} }}}}"
        ),
        "runs-on": "ubuntu-slim",
        "permissions": {"actions": "read"},
        "environment": {"name": "shipmate-engine", "deployment": False},
        "steps": [
            {
                "uses": "$/actions/apply-release",
                "with": {
                    "snapshot": (
                        f"${{{{ toJSON(fromJSON(needs.snapshot.outputs.by_wave).wave{n}) }}}}"
                    ),
                    "deploy": "${{ inputs.deploy }}",
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
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}

WAVE2_RELEASE = {
    "needs": ["snapshot", "wave0", "wave0-release", "wave1", "wave1-release"],
    "if": (
        "${{ !cancelled() && needs.snapshot.result == 'success' && "
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
                "snapshot": "${{ toJSON(fromJSON(needs.snapshot.outputs.by_wave).wave2) }}",
                "deploy": "${{ inputs.deploy }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}


@pytest.mark.parametrize("n", range(8))
def test_each_wave_completion_job_is_exactly(n):
    """Mutations: drop the `result` clause from `wave2-complete`'s `if:`; `.wave1` in
    `wave2-complete`'s `releases`; `needs: [snapshot]` on `wave5-complete`; add `checks: write`
    to `wave5-complete`'s permissions; drop `continue-on-error` from `wave3-complete`."""
    assert workflow_jobs("apply-env-level.yml")[f"wave{n}-complete"] == _completion(n)


def test_wave3_completion_job_is_written_out():
    """Mutation: drop `continue-on-error` from `wave3-complete`."""
    assert workflow_jobs("apply-env-level.yml")["wave3-complete"] == WAVE3_COMPLETE


def test_complete_waits_for_every_release_wave_and_completion():
    """`complete` still fails the level, so it carries no `continue-on-error`, and no flip may
    land after it starts.

    Mutations: drop `wave7-complete` from `complete.needs`; add `continue-on-error: true` to
    `complete`; drop `wave4-release` from `complete.needs`; `wave5-release` under `"wave6"` in
    its `releases`."""
    assert workflow_jobs("apply-env-level.yml")["complete"] == COMPLETE


def test_each_wave_needs_its_release_and_no_completion():
    """Mutations: add `wave0-complete` to `wave1.needs`; drop `wave3-release` from
    `wave3.needs`."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert {w: jobs[w]["needs"] for w in WAVES} == WAVE_NEEDS


def test_each_wave_runs_only_after_its_release_succeeded():
    """Mutations: drop `&& needs.wave5-release.result == 'success'` from `wave5.if`; `!=` for
    `==` in it."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert {w: jobs[w]["if"] for w in WAVES} == WAVE_IF


@pytest.mark.parametrize("n", range(8))
def test_each_release_job_is_exactly(n):
    """Mutations: drop `snapshot` from `wave2-release.needs`; drop the `!cancelled()` prefix from
    `wave4-release.if`; `'failure'` for `'skipped'` in one clause of `wave6-release.if`; add
    `continue-on-error: true` to `wave1-release`; `.wave2` in `wave3-release`'s slice."""
    assert workflow_jobs("apply-env-level.yml")[f"wave{n}-release"] == _release(n)


@pytest.mark.parametrize(("n", "expected"), [(0, WAVE0_RELEASE), (2, WAVE2_RELEASE)])
def test_release_jobs_are_written_out(n, expected):
    """Mutations: drop `snapshot` from `wave2-release.needs`; drop the `!cancelled()` prefix
    from `wave0-release.if`."""
    assert workflow_jobs("apply-env-level.yml")[f"wave{n}-release"] == expected


def test_snapshot_publishes_both_maps():
    """Mutation: drop `snapshot`'s `by_wave` output."""
    assert workflow_jobs("apply-env-level.yml")["snapshot"]["outputs"] == {
        "snapshot": "${{ steps.snap.outputs.snapshot }}",
        "by_wave": "${{ steps.snap.outputs.by_wave }}",
    }


def test_the_job_set_is_exactly():
    """Mutation: rename `wave4-complete`; delete `wave6-release`."""
    assert sorted(workflow_jobs("apply-env-level.yml")) == JOB_IDS
