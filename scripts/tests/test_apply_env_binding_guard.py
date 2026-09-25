"""Guards how every job that touches a cell's state binds a GitHub Environment.

Invariants:
- every cell job -- `plan`, `drift`, `unlock` and the eight waves -- binds
  `${{ matrix.env_binding }}` and nothing else. `env-config`'s `resolve` stamps that value on
  every detect row, so the binding is decided in one place, from the default branch's
  environment table; a job that computes its own is the regression. Where the bound
  environment does not exist, snapshot's pre-flight refuses the run: the fingerprint pins
  nothing about the binding, because plan and apply resolve a cell's variables from the same
  table (CONTRACT.md §Env model);
- snapshot binds no environment at all and complete binds shipmate-engine: a job
  that gains an env-derived binding is the regression;
- snapshot runs the environment pre-flight before it snapshots the apply checks,
  and both before any wave: the pre-flight is only a control while it can still
  refuse the run;
- every apply-cell invocation passes the reviewed plan text's digest, which the
  action refuses to apply without.

The binding guard used to pin a ternary over a repository variable, with its comma-boundary
and case-insensitive matching rules, and a second copy of that rule in `verify-environments`.
Both rules are gone: which environment is shared is `shared = true` in the table, and which
tier that resolves is pinned by
`test_env_config_resolve.py::test_the_binding_and_tier_follow_the_shared_key`.

The realistic failure is accidental regression -- a restored expression, one missed wave, a new
cell job with a hand-rolled binding -- not a hostile edit to these SHA-pinned files, so one
whole-value comparison per job, plus a derived job set compared to the hand-written one, covers
it.
"""

import yaml
from _loader import WORKFLOWS, local_action

CELL_ENV = "${{ matrix.env_binding }}"
WAVES = [f"wave{i}" for i in range(8)]

#: The apply cell, and the matrix field carrying the digest of the plan text a reviewer approved.
#: A composite action's `required: true` is not enforced, so a dropped `with:` line arrives as the
#: empty string; apply-cell refuses that, which turns a wiring slip into eight failed applies.
APPLY_CELL = local_action("apply-cell")
PLAN_SHA256 = "${{ matrix.plan_sha256 }}"

#: (workflow file, job) for every job that binds a cell's environment, written by hand.
CELL_BOUND = {
    ("plan.yml", "plan"),
    ("drift.yml", "drift"),
    ("unlock.yml", "unlock"),
    *(("apply-env-level.yml", w) for w in WAVES),
}

#: snapshot's steps, in order, by action path: a reorder must redden this.
SNAPSHOT_STEPS = [
    local_action("verify-environments"),
    local_action("apply-snapshot"),
]


def _jobs(workflow="apply-env-level.yml"):
    spec = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    assert isinstance(spec, dict), f"{workflow} did not parse to a mapping"
    return spec["jobs"]


def _wave_jobs():
    jobs = _jobs()
    missing = [w for w in WAVES if w not in jobs]
    assert not missing, f"apply-env-level.yml lost wave jobs: {missing}"
    return {w: jobs[w] for w in WAVES}


def test_every_cell_job_binds_the_resolved_environment():
    """Mutations: restore the ternary in one wave; bind `${{ matrix.environment }}` in `wave7`."""
    for workflow, job_id in sorted(CELL_BOUND):
        jobs = _jobs(workflow)
        assert job_id in jobs, f"{workflow} no longer declares {job_id!r} (update CELL_BOUND)"
        assert jobs[job_id].get("environment") == CELL_ENV, (
            f"{workflow} job {job_id!r} must bind the environment detect resolved -- the "
            "reviewer gate, the OIDC claim split and the environment secrets live there"
        )


def test_the_cell_jobs_are_exactly_the_hand_written_set():
    """Derived from every workflow file against the hand-written set, so a twelfth cell job
    reddens here rather than binding whatever it computes.

    Mutation: add a job with a `strategy.matrix` and no `environment:`.
    """
    found = {
        (path.name, job_id)
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job_id, job in _jobs(path.name).items()
        if (job.get("strategy") or {}).get("matrix") is not None
    }
    assert found == CELL_BOUND


def test_snapshot_binds_no_environment_and_complete_binds_the_engine_environment():
    jobs = _jobs()
    # Absence asserted as absence: an explicit `environment:` with a null value is a binding the
    # caller can be made to resolve, and `.get()` would read it as no key at all.
    assert "environment" not in jobs["snapshot"], (
        "snapshot must bind no environment -- it needs no secret, and an "
        "env-derived binding would subject the check snapshot to protection rules"
    )
    assert jobs["complete"].get("environment") == "shipmate-engine"


def test_snapshot_verifies_the_environments_before_snapshotting_the_checks():
    steps = _jobs()["snapshot"]["steps"]
    assert [s["uses"].split("@")[0] for s in steps] == SNAPSHOT_STEPS, (
        "snapshot's steps changed: the environment pre-flight must run first and "
        "the whole job before wave0, or the applies it exists to refuse have "
        "already started"
    )
    assert steps[0].get("continue-on-error") in (None, False), (
        "the pre-flight step is continue-on-error: it would name the missing "
        "environments and let the waves apply into them anyway"
    )
    assert _jobs()["wave0"]["needs"] == ["snapshot"], (
        "wave0 must gate on snapshot, or the pre-flight refuses a run whose first "
        "wave is already applying"
    )


def test_every_apply_cell_invocation_passes_the_reviewed_plan_digest():
    """The count and the per-site input, together: a count alone is satisfied by eight steps of
    which one dropped the digest, and a per-site check alone is satisfied by seven sites plus a
    ninth wave that never got one."""
    steps = [
        step
        for job in _jobs().values()
        for step in (job.get("steps") or [])
        if (step.get("uses") or "").split("@")[0] == APPLY_CELL
    ]
    assert len(steps) == 8, f"apply-env-level.yml invokes apply-cell {len(steps)} times, not 8"
    assert [(s.get("with") or {}).get("plan-sha256") for s in steps] == [PLAN_SHA256] * 8, (
        "an apply-cell invocation does not pass matrix.plan_sha256 -- apply-cell refuses an "
        "empty digest, so that wave's cells cannot apply at all"
    )
