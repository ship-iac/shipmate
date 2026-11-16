"""The unlock result reply's wiring: each leg's record, the `result` job that reads it, and the
`unlock-cell` outcome the record carries. Each job, step and expression is compared whole
against a hand-written constant.

Threat model: accidental regression -- a status function dropped (a failed leg uploads no
record, a cancelled run posts), a grant widened, a checkout added to `result`, a record keyed by
something other than its leg's index, a failed force-unlock read as released.
"""

from _loader import action_yaml, workflow_jobs

RESULT_JOB = {
    "needs": ["guard", "detect", "unlock"],
    "if": "${{ !cancelled() && needs.detect.result != 'skipped' }}",
    "runs-on": "ubuntu-slim",
    "permissions": {"pull-requests": "write"},
    "steps": [
        {
            "uses": "$/actions/unlock-result",
            "with": {
                "comment-id": "${{ github.event.inputs.comment_id }}",
                "environment": "${{ inputs.environment }}",
                "cells": "${{ needs.detect.outputs.cells }}",
                "detect-result": "${{ needs.detect.result }}",
            },
        }
    ],
}

RECORD_STEPS = [
    {
        "name": "Record the cell's outcome",
        "if": "${{ always() }}",
        "shell": "bash",
        "env": {
            "INDEX": "${{ strategy.job-index }}",
            "STACK": "${{ matrix.stack }}",
            "ENVIRONMENT": "${{ matrix.environment }}",
            "OUTCOME": "${{ steps.cell.outputs.outcome }}",
            "LOCK_ID": "${{ steps.cell.outputs.lock_id }}",
        },
        "run": (
            "python3 -c 'import json, os; e = os.environ; print(json.dumps("
            '{"index": int(e["INDEX"]), "stack": e["STACK"], "environment": e["ENVIRONMENT"], '
            '"outcome": e["OUTCOME"] or "failed", "lock_id": e["LOCK_ID"]}))\' '
            '> "$RUNNER_TEMP/shipmate-unlock-$INDEX.json"'
        ),
    },
    {
        "name": "Upload the cell's outcome",
        "if": "${{ always() }}",
        "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
        "with": {
            "name": "shipmate-unlock-${{ strategy.job-index }}",
            "path": "${{ runner.temp }}/shipmate-unlock-${{ strategy.job-index }}.json",
            "overwrite": True,
        },
    },
]

OUTCOME = (
    "${{ steps.release.outcome == 'success' && 'released' "
    "|| steps.report.outcome == 'success' && 'none' || 'failed' }}"
)


def test_the_result_job_is_this_whole_job():
    """It checks nothing out, so it runs no pull-request code; its one step is the engine
    action. Mutations: `always()` for `!cancelled()` (a cancelled run posts); drop the
    `skipped` clause (a rejected dispatch posts); drop `unlock` from `needs` (the reply races the
    legs); add `actions: read` to `permissions`; add an `actions/checkout` step; pass
    `inputs.ref` as `comment-id`."""
    assert workflow_jobs("unlock.yml")["result"] == RESULT_JOB


def test_each_leg_records_its_outcome_under_its_own_index_even_when_it_failed():
    """The last two steps of the `unlock` job, after `unlock-cell` with `id: cell`. Mutations:
    drop `if: ${{ always() }}` from either (a failed leg reads `did not run`); drop `or
    "failed"`; drop `matrix.stack` from `env` (the record checks against an empty stack); name
    the artifact by `matrix.stack`; upload `unlock-record.json` (the file name
    carries the index the reply reads); drop `overwrite: true` (a re-run of a
    failed leg cannot replace its record); drop `id: cell` (both outputs read empty)."""
    steps = workflow_jobs("unlock.yml")["unlock"]["steps"]
    assert steps[-3]["id"] == "cell"
    assert steps[-3]["uses"] == "$/actions/unlock-cell"
    assert steps[-2:] == RECORD_STEPS


def test_unlock_cell_reads_released_only_when_the_force_unlock_succeeded():
    """Mutations: `steps.release.outcome != 'failure'` (a skipped release reads `released`);
    `steps.report.conclusion` for `outcome`; `steps.probe.outputs.held == 'true'` for the
    release clause (a failed force-unlock reads `released`)."""
    outputs = action_yaml("unlock-cell")["outputs"]
    assert outputs["outcome"]["value"] == OUTCOME
    assert outputs["lock_id"]["value"] == "${{ steps.probe.outputs.lock_id }}"


def test_the_unlock_result_action_downloads_without_a_token_and_never_fails_on_it():
    """Mutations: drop `continue-on-error` (a failed download reds `result`); `merge-multiple:
    true` (records lose the artifact directory whose name gives their index); add
    `github-token` (the API path needs `actions: read`, which `result` lacks); drop the `[]`
    condition; pass `steps.download.conclusion` (always `success` under `continue-on-error`);
    the sentinel: rename it `shipmate-unlock-0` (it would read as cell 0's record), drop its
    `overwrite` (a re-run of `result` fails its upload), drop its `continue-on-error` (a failed
    upload skips the reply), read the download's outcome without it (a lone record downloaded
    without its directory reads `did not run` rather than the download failing)."""
    steps = action_yaml("unlock-result")["runs"]["steps"]
    assert steps == [
        {
            "name": "Write the download sentinel",
            "if": "${{ inputs.cells != '[]' }}",
            "shell": "bash",
            "run": 'printf x > "$RUNNER_TEMP/shipmate-unlock-sentinel"',
        },
        {
            "name": "Upload the download sentinel",
            "id": "sentinel",
            "if": "${{ inputs.cells != '[]' }}",
            "continue-on-error": True,
            "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
            "with": {
                "name": "shipmate-unlock-sentinel",
                "path": "${{ runner.temp }}/shipmate-unlock-sentinel",
                "overwrite": True,
            },
        },
        {
            "name": "Download the cells' records",
            "id": "download",
            "if": "${{ inputs.cells != '[]' }}",
            "continue-on-error": True,
            "uses": "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333",
            "with": {
                "pattern": "shipmate-unlock-*",
                "path": "${{ runner.temp }}/unlock-records",
                "merge-multiple": False,
            },
        },
        {
            "name": "Reply with the unlock result",
            "shell": "bash",
            "env": {
                "GH_TOKEN": "${{ github.token }}",
                "COMMENT_ID": "${{ inputs.comment-id }}",
                "SHIPMATE_ENV": "${{ inputs.environment }}",
                "SHIPMATE_CELLS": "${{ inputs.cells }}",
                "SHIPMATE_DETECT_RESULT": "${{ inputs.detect-result }}",
                "SHIPMATE_DOWNLOAD_OUTCOME": (
                    "${{ steps.sentinel.outcome == 'success' && steps.download.outcome "
                    "|| 'failure' }}"
                ),
                "SHIPMATE_RECORDS": "${{ runner.temp }}/unlock-records",
            },
            "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/unlock-result"',
        },
    ]
