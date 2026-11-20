"""Guards how every job that touches a cell's state binds a GitHub Environment.

Invariants:
- every cell job -- `plan`, `drift`, `unlock` and `cell.yml`'s `apply` -- binds the row's
  `env_binding` (`${{ matrix.env_binding }}`, or `fromJSON(inputs.cell)`'s in `cell.yml`) and
  nothing else; the eight waves that call `cell.yml` bind none, which a calling job cannot.
  `env-config`'s `resolve` stamps that value on every detect row, so the binding is decided in
  one place, from the default branch's environment table; a job that computes its own is the
  regression. Where the bound environment does not exist, snapshot's pre-flight refuses the
  run: the fingerprint pins nothing about the binding, because plan and apply resolve a cell's
  variables from the same table (CONTRACT.md §Env model);
- snapshot and complete each bind shipmate-engine, the environment holding the App key both
  mint from: a job that gains an env-derived binding is the regression;
- snapshot runs the environment pre-flight before it snapshots the apply checks,
  and both before any wave: the pre-flight is only a control while it can still
  refuse the run;
- every apply-cell invocation passes the reviewed plan text's digest, which the
  action refuses to apply without.

The binding guard used to pin a ternary over a repository variable, with its comma-boundary
and case-insensitive matching rules, and a second copy of that rule in `verify-environments`.
Both rules are gone: which environment is shared is `split: false` in the table, and which
tier that resolves is pinned by
`test_env_config_resolve.py::test_the_binding_and_tier_follow_the_split_key`.

The realistic failure is accidental regression -- a restored expression, one missed wave, a new
cell job with a hand-rolled binding -- not a hostile edit to these SHA-pinned files, so one
whole-value comparison per job, plus a derived job set compared to the hand-written one, covers
it.
"""

from _loader import ENGINE_ENVIRONMENT, WORKFLOWS, local_action, workflow_jobs

CELL_ENV = "${{ matrix.env_binding }}"
CALLED_CELL_ENV = "${{ fromJSON(inputs.cell).env_binding }}"
WAVES = [f"wave{i}" for i in range(8)]

#: The apply cell, and the matrix field carrying the digest of the plan text a reviewer approved.
#: A composite action's `required: true` is not enforced, so a dropped `with:` line arrives as the
#: empty string; apply-cell refuses that, which turns a wiring slip into eight failed applies.
APPLY_CELL = local_action("apply-cell")
PLAN_SHA256 = "${{ fromJSON(inputs.cell).plan_sha256 }}"

#: (workflow file, job) -> binding, for every job that binds a cell's environment, by hand.
CELL_BOUND = {
    ("plan.yml", "plan"): CELL_ENV,
    ("drift.yml", "drift"): CELL_ENV,
    ("unlock.yml", "unlock"): CELL_ENV,
    ("cell.yml", "apply"): CALLED_CELL_ENV,
}

#: snapshot's steps, in order, by action path: a reorder must redden this.
SNAPSHOT_STEPS = [
    local_action("verify-environments"),
    local_action("apply-snapshot"),
]


def test_every_cell_job_binds_the_resolved_environment():
    """Mutations: bind `${{ fromJSON(inputs.cell).environment }}` in `cell.yml`'s `apply`; bind
    `${{ matrix.environment }}` in `unlock.yml`."""
    for (workflow, job_id), binding in sorted(CELL_BOUND.items()):
        jobs = workflow_jobs(workflow)
        assert job_id in jobs, f"{workflow} no longer declares {job_id!r} (update CELL_BOUND)"
        assert jobs[job_id].get("environment") == binding, (
            f"{workflow} job {job_id!r} must bind the environment detect resolved -- the "
            "reviewer gate, the OIDC claim split and the environment secrets live there"
        )


#: Matrix jobs that bind no environment, written by hand. `warm` runs `init -backend=false` on
#: the merged commit to fill the provider cache: no backend, no credentials, and a protected apply
#: environment would hold each leg for a reviewer. The waves call `cell.yml`, whose `apply` binds.
UNBOUND_MATRIX = {("deploy.yml", "warm"), *(("apply-env-level.yml", w) for w in WAVES)}


def test_the_unbound_matrix_jobs_bind_no_environment():
    """Mutations: add `environment: ${{ matrix.env_binding }}` to `deploy.yml`'s `warm`, then to
    `apply-env-level.yml`'s `wave2`."""
    for workflow, job_id in sorted(UNBOUND_MATRIX):
        assert "environment" not in workflow_jobs(workflow)[job_id]


def test_the_cell_jobs_are_exactly_the_hand_written_set():
    """Derived from every workflow file against the hand-written sets, so a new matrix job
    reddens here rather than binding whatever it computes. `cell.yml`'s `apply` is no matrix
    job: its binding is pinned above.

    Mutation: add a job with a `strategy.matrix` and no `environment:`.
    """
    found = {
        (path.name, job_id)
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job_id, job in workflow_jobs(path.name).items()
        if (job.get("strategy") or {}).get("matrix") is not None
    }
    assert found == (set(CELL_BOUND) - {("cell.yml", "apply")}) | UNBOUND_MATRIX


def test_snapshot_and_complete_bind_the_engine_environment():
    """Mutation: drop `snapshot`'s `environment:` binding."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert {j: jobs[j].get("environment") for j in ("snapshot", "complete")} == {
        "snapshot": ENGINE_ENVIRONMENT,
        "complete": ENGINE_ENVIRONMENT,
    }


def test_snapshot_verifies_the_environments_before_snapshotting_the_checks():
    """Mutation: swap snapshot's two steps."""
    steps = workflow_jobs("apply-env-level.yml")["snapshot"]["steps"]
    assert [s["uses"].split("@")[0] for s in steps] == SNAPSHOT_STEPS, (
        "snapshot's steps changed: the environment pre-flight must run first and "
        "the whole job before wave0, or the applies it exists to refuse have "
        "already started"
    )
    assert steps[0].get("continue-on-error") in (None, False), (
        "the pre-flight step is continue-on-error: it would name the missing "
        "environments and let the waves apply into them anyway"
    )
    wave0_needs = workflow_jobs("apply-env-level.yml")["wave0"]["needs"]
    assert wave0_needs == ["snapshot", "wave0-release"], (
        "wave0 must gate on snapshot, or the pre-flight refuses a run whose first "
        "wave is already applying"
    )


def test_every_apply_cell_invocation_passes_the_reviewed_plan_digest():
    """Every workflow, so an apply-cell step outside `cell.yml` reddens too. Mutation: drop
    `plan-sha256` from `cell.yml`'s apply-cell step."""
    found = [
        (path.name, job_id, (step.get("with") or {}).get("plan-sha256"))
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job_id, job in workflow_jobs(path.name).items()
        for step in (job.get("steps") or [])
        if (step.get("uses") or "").split("@")[0] == APPLY_CELL
    ]
    assert found == [("cell.yml", "apply", PLAN_SHA256)], (
        "an apply-cell invocation does not pass the row's plan_sha256 -- apply-cell refuses an "
        "empty digest, so no cell can apply at all"
    )
