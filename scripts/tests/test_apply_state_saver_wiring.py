"""Guards the per-wave state reader, saver and verdict jobs in `apply-env-level.yml`.

After each wave that ran, `waveN-read` classes each cell from the state line in its own job log,
one `waveN-save` leg per cell with a state saves it under a `cache-mode: write` job, and
`waveN-verdict` fails unless every cell's state was saved or it had none. None of them binds an
environment, takes a key or writes a check, and no leg name is a cell job name.

Threat model: an accidental edit to the workflow (a dropped result test, a wrong wave's output,
an added environment or permission, a renamed leg). Whole parsed values against hand-written
constants cover it.
"""

import pytest
from _loader import load_script, workflow_jobs

ag = load_script("apply-gate")

#: The saver job's `name:`, written by hand; `test_state_read.py`'s `LEG_NAME` is the same text.
LEG_NAME = "save-state (${{ matrix.environment }} ${{ matrix.stack }})"


def _read(n):
    return {
        "needs": ["snapshot", f"wave{n}"],
        "if": (
            "${{ always() && needs.snapshot.result == 'success' && "
            f"needs.wave{n}.result != 'skipped' }}}}"
        ),
        "runs-on": "${{ inputs.runs_on }}",
        "cache-mode": "read",
        "permissions": {"actions": "read"},
        "outputs": {
            "cells": "${{ steps.read.outputs.cells }}",
            "report": "${{ steps.read.outputs.report }}",
        },
        "env": {"WAVE": f"wave{n}"},
        "steps": [
            {
                "id": "read",
                "uses": "$/actions/state-read",
                "with": {
                    "by-wave": "${{ needs.snapshot.outputs.by_wave }}",
                    "wave": "${{ env.WAVE }}",
                    "job-id": "${{ job.check_run_id }}",
                },
            }
        ],
    }


def _save(n):
    return {
        "needs": [f"wave{n}-read"],
        "if": (
            f"${{{{ always() && needs.wave{n}-read.result == 'success' && "
            f"needs.wave{n}-read.outputs.cells != '[]' }}}}"
        ),
        "runs-on": "${{ inputs.runs_on }}",
        "cache-mode": "write",
        "permissions": {"actions": "read"},
        "strategy": {
            "fail-fast": False,
            "matrix": {"include": f"${{{{ fromJSON(needs.wave{n}-read.outputs.cells) }}}}"},
        },
        "name": LEG_NAME,
        "steps": [
            {
                "uses": "$/actions/state-save",
                "with": {
                    "stack": "${{ matrix.stack }}",
                    "environment": "${{ matrix.environment }}",
                    "slug": "${{ matrix.slug }}",
                    "digest": "${{ matrix.digest }}",
                    "attempt": "${{ matrix.attempt }}",
                },
            }
        ],
    }


def _verdict(n):
    return {
        "needs": ["snapshot", f"wave{n}", f"wave{n}-read", f"wave{n}-save"],
        "if": (
            "${{ always() && needs.snapshot.result == 'success' && "
            f"needs.wave{n}.result != 'skipped' }}}}"
        ),
        "runs-on": "ubuntu-slim",
        "cache-mode": "read",
        "permissions": {"actions": "read"},
        "env": {
            "WAVE_RESULTS": f'{{"wave{n}": "${{{{ needs.wave{n}.result }}}}"}}',
            "WAVE_STATE": f'{{"wave{n}": ${{{{ toJSON(needs.wave{n}-read.outputs.report) }}}}}}',
        },
        "steps": [
            {
                "uses": "$/actions/apply-complete",
                "with": {
                    "mode": "verdict",
                    "by-wave": "${{ needs.snapshot.outputs.by_wave }}",
                    "wave-results": "${{ env.WAVE_RESULTS }}",
                    "wave-state": "${{ env.WAVE_STATE }}",
                    "job-id": "${{ job.check_run_id }}",
                },
            }
        ],
    }


#: Written out in full: a template-only guard cannot catch a wrong template.
WAVE3 = {
    "wave3-read": {
        "needs": ["snapshot", "wave3"],
        "if": (
            "${{ always() && needs.snapshot.result == 'success' && "
            "needs.wave3.result != 'skipped' }}"
        ),
        "runs-on": "${{ inputs.runs_on }}",
        "cache-mode": "read",
        "permissions": {"actions": "read"},
        "outputs": {
            "cells": "${{ steps.read.outputs.cells }}",
            "report": "${{ steps.read.outputs.report }}",
        },
        "env": {"WAVE": "wave3"},
        "steps": [
            {
                "id": "read",
                "uses": "$/actions/state-read",
                "with": {
                    "by-wave": "${{ needs.snapshot.outputs.by_wave }}",
                    "wave": "${{ env.WAVE }}",
                    "job-id": "${{ job.check_run_id }}",
                },
            }
        ],
    },
    "wave3-save": {
        "needs": ["wave3-read"],
        "if": (
            "${{ always() && needs.wave3-read.result == 'success' && "
            "needs.wave3-read.outputs.cells != '[]' }}"
        ),
        "runs-on": "${{ inputs.runs_on }}",
        "cache-mode": "write",
        "permissions": {"actions": "read"},
        "strategy": {
            "fail-fast": False,
            "matrix": {"include": "${{ fromJSON(needs.wave3-read.outputs.cells) }}"},
        },
        "name": "save-state (${{ matrix.environment }} ${{ matrix.stack }})",
        "steps": [
            {
                "uses": "$/actions/state-save",
                "with": {
                    "stack": "${{ matrix.stack }}",
                    "environment": "${{ matrix.environment }}",
                    "slug": "${{ matrix.slug }}",
                    "digest": "${{ matrix.digest }}",
                    "attempt": "${{ matrix.attempt }}",
                },
            }
        ],
    },
    "wave3-verdict": {
        "needs": ["snapshot", "wave3", "wave3-read", "wave3-save"],
        "if": (
            "${{ always() && needs.snapshot.result == 'success' && "
            "needs.wave3.result != 'skipped' }}"
        ),
        "runs-on": "ubuntu-slim",
        "cache-mode": "read",
        "permissions": {"actions": "read"},
        "env": {
            "WAVE_RESULTS": '{"wave3": "${{ needs.wave3.result }}"}',
            "WAVE_STATE": '{"wave3": ${{ toJSON(needs.wave3-read.outputs.report) }}}',
        },
        "steps": [
            {
                "uses": "$/actions/apply-complete",
                "with": {
                    "mode": "verdict",
                    "by-wave": "${{ needs.snapshot.outputs.by_wave }}",
                    "wave-results": "${{ env.WAVE_RESULTS }}",
                    "wave-state": "${{ env.WAVE_STATE }}",
                    "job-id": "${{ job.check_run_id }}",
                },
            }
        ],
    },
}


@pytest.mark.parametrize("n", range(8))
def test_each_wave_state_job_is_exactly(n):
    """Mutations: `needs.wave2-read` in `wave3-save`'s `if:`; drop the reader result test from
    `wave6-save`; add `environment: shipmate-engine` to `wave1-read`; `checks: write` on
    `wave5-save`; `mode: complete` in the verdicts' shared steps."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert (jobs[f"wave{n}-read"], jobs[f"wave{n}-save"], jobs[f"wave{n}-verdict"]) == (
        _read(n),
        _save(n),
        _verdict(n),
    )


def test_wave3s_state_jobs_are_written_out():
    """Mutation: drop `always()` from `wave3-verdict`'s `if:`."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert {k: jobs[k] for k in WAVE3} == WAVE3


WAVE_PERMISSIONS = {
    "contents": "read",
    "actions": "read",
    "checks": "read",
    "pull-requests": "read",
    "id-token": "write",
}


def test_no_job_of_the_level_writes_a_check_and_the_cells_keep_their_grant():
    """Checks are written only with the App token. Mutations: `checks: write` on `wave2`;
    `checks: write` on `complete`."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert [
        j for j, job in jobs.items() if (job.get("permissions") or {}).get("checks") == "write"
    ] == []
    assert {jobs[f"wave{n}"]["permissions"] == WAVE_PERMISSIONS for n in range(8)} == {True}


@pytest.mark.parametrize(
    ("stack", "env"),
    [("stacks/app", "dev-eu"), (".", "dev-eu"), ("a/b/c", "prod"), ("stacks/app", "")],
)
def test_a_rendered_leg_is_its_cells_leg_and_never_its_cell_job(stack, env):
    """`apply-complete` finds a cell job by `is_cell_job` and its leg by `is_leg_job`. Mutation:
    set the saver's `name:` to `${{ matrix.environment }} / ${{ matrix.stack }}`."""
    name = workflow_jobs("apply-env-level.yml")["wave0-save"]["name"]
    rendered = name.replace("${{ matrix.environment }}", env).replace("${{ matrix.stack }}", stack)
    listed = f"apply / run / L0 / {rendered}"
    assert (ag.is_cell_job(listed, stack, env), ag.is_leg_job(listed, stack, env)) == (False, True)
