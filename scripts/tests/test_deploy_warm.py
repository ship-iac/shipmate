"""deploy.yml's `warm` job saves the OpenTofu cache entry on a merge deploy with no cells.

`detect` installs no OpenTofu, so a merge that runs no cell (a `tools.tofu` bump changes no
stack) would leave the new version's entry missing until the next cell, and every plan cell
downloads OpenTofu until then. `warm` runs only when every env-level is empty, because a cell
that reaches its `setup` step saves the entry. It installs OpenTofu from the versions `detect` resolved, on the
cells' runner, because the cache key carries the runner's OS and `runner.arch`. The deploy run
waits for it, so it holds the deploy concurrency group up to its 15-minute timeout. It is a
degrade job: no job needs it and the gate never reads it, so `continue-on-error` keeps its
failure off the run's conclusion, the cells and the lock release.
"""

from _loader import workflow_jobs

#: The whole job, hand-written: a value read back from the workflow passes whatever it says.
EXPECTED_WARM = {
    "needs": ["detect"],
    "if": (
        "${{ needs.detect.outputs.tools != ''"
        " && needs.detect.outputs.envlevel0_empty == 'true'"
        " && needs.detect.outputs.envlevel1_empty == 'true'"
        " && needs.detect.outputs.envlevel2_empty == 'true'"
        " && needs.detect.outputs.envlevel3_empty == 'true' }}"
    ),
    "runs-on": "${{ inputs.runs_on }}",
    "timeout-minutes": 15,
    "permissions": {},
    "continue-on-error": True,
    "steps": [
        {
            "uses": "$/actions/setup",
            "with": {"versions": "${{ needs.detect.outputs.tools }}"},
        }
    ],
}


def test_warm_installs_opentofu_from_detects_versions_as_a_degrade_job():
    """Mutations: add `tofu: "false"` to warm's step; drop the `warm` job; `runs-on:
    ubuntu-slim`; drop `continue-on-error`; drop the `envlevel2_empty` term
    from its `if:`; restore `!cancelled() && ` to its `if:`; `timeout-minutes: 10`."""
    assert workflow_jobs("deploy.yml").get("warm") == EXPECTED_WARM


def test_no_deploy_job_needs_warm():
    """A job needing `warm` would skip on a failed `warm`, or tie its own run to OpenTofu.

    Mutation: add `warm` to `summary`'s `needs`."""
    needing = [
        name
        for name, job in workflow_jobs("deploy.yml").items()
        if "warm" in ([job["needs"]] if isinstance(job.get("needs"), str) else job.get("needs", []))
    ]
    assert needing == []
