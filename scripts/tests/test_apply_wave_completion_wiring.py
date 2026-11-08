"""Guards the per-wave apply-check completion in `apply-env-level.yml`.

Each `waveN-complete` job completes its own wave's checks right after that wave, from
`snapshot`'s `by_wave` slice. It runs only when its wave reached a result: a skipped wave's
listing row carries the literal matrix template, so a completion over it matches no cell and
fails after the action's retry. It never fails the level (job-level `continue-on-error`);
`complete` waits for every wave and every completion and still fails the level on a check it
cannot complete. No wave waits for a completion, so a completion never delays an apply.

Threat model: an accidental edit to the workflow (a dropped clause, a wrong slice, a moved
`needs:` entry). Whole parsed values against hand-written constants cover it.
"""

import pytest
from _loader import workflow_jobs

WAVES = [f"wave{n}" for n in range(8)]


def _completion(n):
    return {
        "needs": ["snapshot", f"wave{n}"],
        "if": (
            "${{ always() && needs.snapshot.result == 'success' && "
            f"(needs.wave{n}.result == 'success' || needs.wave{n}.result == 'failure') }}}}"
        ),
        "runs-on": "ubuntu-slim",
        "continue-on-error": True,
        "permissions": {"actions": "read"},
        "environment": {"name": "shipmate-engine", "deployment": False},
        "steps": [
            {
                "uses": "$/actions/apply-complete",
                "with": {
                    "snapshot": (
                        f"${{{{ toJSON(fromJSON(needs.snapshot.outputs.by_wave).wave{n}) }}}}"
                    ),
                    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
                },
            }
        ],
    }


#: Written out in full: a template-only guard cannot catch a wrong template.
WAVE3_COMPLETE = {
    "needs": ["snapshot", "wave3"],
    "if": (
        "${{ always() && needs.snapshot.result == 'success' && "
        "(needs.wave3.result == 'success' || needs.wave3.result == 'failure') }}"
    ),
    "runs-on": "ubuntu-slim",
    "continue-on-error": True,
    "permissions": {"actions": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "steps": [
        {
            "uses": "$/actions/apply-complete",
            "with": {
                "snapshot": "${{ toJSON(fromJSON(needs.snapshot.outputs.by_wave).wave3) }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}

COMPLETE = {
    "needs": [
        "snapshot",
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
    "permissions": {"actions": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "steps": [
        {
            "uses": "$/actions/apply-complete",
            "with": {
                "snapshot": "${{ needs.snapshot.outputs.snapshot }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}

JOB_IDS = [
    "complete",
    "snapshot",
    "wave0",
    "wave0-complete",
    "wave1",
    "wave1-complete",
    "wave2",
    "wave2-complete",
    "wave3",
    "wave3-complete",
    "wave4",
    "wave4-complete",
    "wave5",
    "wave5-complete",
    "wave6",
    "wave6-complete",
    "wave7",
    "wave7-complete",
]

WAVE_NEEDS = {
    "wave0": ["snapshot"],
    "wave1": ["wave0"],
    "wave2": ["wave0", "wave1"],
    "wave3": ["wave0", "wave1", "wave2"],
    "wave4": ["wave0", "wave1", "wave2", "wave3"],
    "wave5": ["wave0", "wave1", "wave2", "wave3", "wave4"],
    "wave6": ["wave0", "wave1", "wave2", "wave3", "wave4", "wave5"],
    "wave7": ["wave0", "wave1", "wave2", "wave3", "wave4", "wave5", "wave6"],
}


@pytest.mark.parametrize("n", range(8))
def test_each_wave_completion_job_is_exactly(n):
    """Mutations: drop the `result` clause from `wave2-complete`'s `if:`; `.wave1` in
    `wave2-complete`'s slice; `needs: [snapshot]` on `wave5-complete`; add `checks: write` to
    `wave5-complete`'s permissions; drop `continue-on-error` from `wave3-complete`."""
    assert workflow_jobs("apply-env-level.yml")[f"wave{n}-complete"] == _completion(n)


def test_wave3_completion_job_is_written_out():
    """Mutation: drop `continue-on-error` from `wave3-complete`."""
    assert workflow_jobs("apply-env-level.yml")["wave3-complete"] == WAVE3_COMPLETE


def test_complete_waits_for_every_wave_and_every_completion():
    """`complete` still fails the level, so it carries no `continue-on-error`.

    Mutations: drop `wave7-complete` from `complete.needs`; add `continue-on-error: true` to
    `complete`."""
    assert workflow_jobs("apply-env-level.yml")["complete"] == COMPLETE


def test_no_wave_waits_for_a_completion():
    """Mutation: add `wave0-complete` to `wave1.needs`."""
    assert {w: workflow_jobs("apply-env-level.yml")[w]["needs"] for w in WAVES} == WAVE_NEEDS


def test_snapshot_publishes_both_maps():
    """Mutation: drop `snapshot`'s `by_wave` output."""
    assert workflow_jobs("apply-env-level.yml")["snapshot"]["outputs"] == {
        "snapshot": "${{ steps.snap.outputs.snapshot }}",
        "by_wave": "${{ steps.snap.outputs.by_wave }}",
    }


def test_the_job_set_is_exactly():
    """Mutation: rename `wave4-complete`."""
    assert sorted(workflow_jobs("apply-env-level.yml")) == JOB_IDS
