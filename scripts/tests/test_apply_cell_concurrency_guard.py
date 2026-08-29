"""Guards the per-cell apply serialization: every job that applies a cell
declares one concurrency block, byte-identical across all of them --

    group: apply-${{ matrix.environment }}-${{ matrix.stack }}
    cancel-in-progress: false

Four claims, each its own way to lose the guarantee:

- the group names the **stack**, so cells of one environment do not serialize
  against each other (dropping `-${{ matrix.stack }}` turns a parallel wave into
  a queue, and hides that regression behind a still-green suite);
- the group names **nothing else** -- in particular no mode or verb. A group
  that forks by mode puts two jobs touching the same state into different
  queues, which is the one variation that would let a shared-mode and a
  split-mode apply run against the same stack at once;
- `cancel-in-progress: false`, so a queued run waits instead of killing the
  running apply mid-`tofu apply`;
- the block is **present** on every one of them -- an absent block is no
  serialization at all, and absence-means-safe is fail-open by construction.

The whole parsed mapping is compared against one hand-written constant: no
substring, no vocabulary, no second selector, and nothing derived from the file
being checked. `SERIALIZED` names the jobs by hand for the same reason -- a
derived job list shrinks silently when a job is renamed, and a guard over zero
jobs passes while asserting nothing.

The realistic failure is accidental regression -- a dropped expression, one wave
missed in an edit, an inverted boolean -- not a hostile edit to a SHA-pinned
file every consumer reviews.
"""

import yaml
from _loader import WORKFLOWS

CONCURRENCY = {
    "group": "apply-${{ matrix.environment }}-${{ matrix.stack }}",
    "cancel-in-progress": False,
}

#: workflow file -> the jobs in it that apply a cell, hand-written. Every one of
#: them must carry `CONCURRENCY` exactly.
SERIALIZED = {
    "apply-env-level.yml": [f"wave{i}" for i in range(8)],
    # Not a cell that applies, but one that force-unlocks a cell's state --
    # which is exactly why it shares the queue: a live apply for that cell makes
    # the unlock wait behind it, so the lock it finds is either gone or orphaned.
    # In its own workflow file since the verbs were split; the group did not move
    # with it, and must not.
    "unlock.yml": ["unlock"],
}


def _jobs(workflow):
    spec = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    assert isinstance(spec, dict), f"{workflow} did not parse to a mapping"
    return spec["jobs"]


def test_every_cell_job_serializes_on_environment_and_stack():
    for workflow, job_ids in SERIALIZED.items():
        jobs = _jobs(workflow)
        missing = [j for j in job_ids if j not in jobs]
        assert not missing, (
            f"{workflow} no longer declares {missing} -- either they were renamed "
            "(update SERIALIZED) or the cells they applied are now unserialized"
        )
        for job_id in job_ids:
            assert jobs[job_id].get("concurrency") == CONCURRENCY, (
                f"{workflow} job {job_id!r}: every job applying a cell must declare "
                f"exactly {CONCURRENCY} -- a group missing the stack serializes a "
                "whole environment, a group varying by mode or verb lets two jobs "
                "touch one stack's state at once, and cancel-in-progress: true "
                "kills a running apply mid-write"
            )
