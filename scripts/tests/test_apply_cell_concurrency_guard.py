"""Guards the per-cell apply serialization: one `apply-<env>-<stack>` group, which `cell.yml`
holds from a cell's `apply` through its `save` and the unlock of that cell shares.

Each group is compared whole against a hand-written constant:

- the group names the environment and the stack and nothing else. Dropping the stack queues a
  whole environment behind one cell; a group that varies by mode or verb puts two jobs touching
  one stack's state into different queues;
- `cancel-in-progress: false`, so a queued run waits instead of killing a running apply
  mid-`tofu apply`;
- `queue: max`: without it, a new arrival evicts the run already waiting for the group, and a
  waiting cell or unlock is cancelled.

Placement is the fourth claim. `cell.yml` declares the group at the top level, so no other run
restores the cell's state between `apply` and `save`. A job of `cell.yml` that declares its own
group, or a calling job that repeats it, fails the call at once, so neither may, and no other
engine job declares an `apply-` group.

The realistic failure is accidental regression -- a dropped key, a group restored on a calling
job -- not a hostile edit to a SHA-pinned file every consumer reviews.

Mutations: drop `queue: max` from `unlock.yml`; add the group to `cell.yml`'s `apply`;
restore a job-level group on `stage.yml`'s `cell`.
"""

from _loader import WORKFLOWS, workflow_jobs, workflow_yaml

CELL_GROUP = {
    "group": "apply-${{ inputs.environment }}-${{ inputs.stack }}",
    "cancel-in-progress": False,
    "queue": "max",
}
UNLOCK_GROUP = {
    "group": "apply-${{ matrix.environment }}-${{ matrix.stack }}",
    "cancel-in-progress": False,
    "queue": "max",
}


def test_every_cell_group_is_whole():
    assert (
        workflow_yaml("cell.yml").get("concurrency"),
        workflow_jobs("unlock.yml")["unlock"].get("concurrency"),
    ) == (CELL_GROUP, UNLOCK_GROUP)


def _apply_group(concurrency):
    group = concurrency.get("group") if isinstance(concurrency, dict) else concurrency
    return str(group or "").startswith("apply-")


def test_only_unlock_declares_a_job_level_apply_group():
    """Every job of every engine workflow; any job of `cell.yml` with a `concurrency` key counts,
    whatever its group."""
    found = {
        (path.name, job_id)
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job_id, job in workflow_jobs(path.name).items()
        if "concurrency" in job and (path.name == "cell.yml" or _apply_group(job["concurrency"]))
    }
    assert found == {("unlock.yml", "unlock")}
