"""Guards the `unlock` job in `.github/workflows/apply.yml` and the mode split
that keeps it from ever applying anything.

Unlock is its OWN job rather than a mode inside the wave jobs, because an `if:`
on an `apply-cell` step is a seam whose deletion silently turns an unlock into
an apply. The properties that keep the two paths apart are all one-edit-away
fail-open, so each is pinned WHOLE against a hand-written constant:

- the job's `if:` -- it runs only in unlock mode, only with a non-empty queue,
  and its skip-propagation form (`!failure() && !cancelled()` over
  `guard`/`review`/`detect`) is what stops a rejected dispatch from fanning out;
- its `strategy` -- `fail-fast: false`, so one cell that cannot determine its
  lock state does not stop its siblings from releasing theirs, and the matrix
  source is `cells` (the `waves` output is the EMPTY STRING in unlock mode, not
  `{}`, so a `fromJSON(... .waves)` matrix here would be a runtime death);
- the actions its steps call: `unlock-cell` and nothing from the apply family;
- the waves job's `if:`, which must carry `inputs.mode != 'unlock'` -- without
  that clause an unlock dispatch would also apply;
- both `summary` steps' `if:` -- unlock completes no apply check, so a gate
  refresh is a no-op and an apply-result comment has no cells to report;
- `mode`'s declared default, which is what makes every existing apply dispatch
  (which sends no mode at all) keep taking the apply path.

Two properties this file deliberately does NOT re-pin, to keep one selector per
property:

- the unlock job's `concurrency` block. It is byte-identical to the wave jobs'
  and that is the whole feature's liveness proof (a live apply for the cell
  makes the unlock queue behind it, so the lock is either gone or orphaned by
  the time unlock runs) -- so it is pinned by the ONE hand-written constant in
  `test_apply_cell_concurrency_guard.py`, which now covers this job too. A
  second constant here would be a second selector for one property.
- the `review` job carrying no `if:`. Already pinned for both apply paths by
  `test_apply_review_jobs.py::test_the_review_job_carries_no_if_and_so_always_runs`.

Threat model is accidental regression -- a clause dropped in a refactor, a
matrix pointed at the wrong output, a step re-aimed at `apply-cell` -- not a
hostile edit to a file every consumer reviews.
"""

import yaml
from _loader import WORKFLOWS

WORKFLOW = "apply.yml"

#: The whole `if:` of the `unlock` job, hand-written. `guard` and `review` are in
#: its `needs` too, so a rejected dispatch reaches `!failure()` here instead of
#: leaving `detect` skipped -- whose outputs read as empty strings, and `'' !=
#: '[]'` is true.
UNLOCK_IF = (
    "${{ !failure() && !cancelled() && inputs.mode == 'unlock' "
    "&& needs.detect.outputs.cells != '[]' }}"
)
UNLOCK_NEEDS = ["guard", "review", "detect"]

#: The whole `strategy:` mapping. `fail-fast: false` and the matrix source are
#: one comparison: a `true` fail-fast strands sibling locks, and `waves` in
#: place of `cells` is `fromJSON('')` in unlock mode.
UNLOCK_STRATEGY = {
    "fail-fast": False,
    "matrix": {"include": "${{ fromJSON(needs.detect.outputs.cells) }}"},
}

#: The whole `if:` of the waves job. Deleting the mode clause makes an unlock
#: dispatch apply every pending cell.
APPLY_IF = (
    "${{ !failure() && !cancelled() && inputs.mode != 'unlock' "
    "&& needs.detect.outputs.empty != 'true' }}"
)

#: The whole `if:` both `summary` steps carry.
SUMMARY_STEP_IF = "${{ inputs.mode != 'unlock' }}"

#: The engine actions the unlock job's steps may call. Apply-family actions are
#: named so the failure message says which one leaked in.
FORBIDDEN = ("apply-cell", "apply-complete")


def _spec():
    spec = yaml.safe_load((WORKFLOWS / WORKFLOW).read_text(encoding="utf-8"))
    assert isinstance(spec, dict), f"{WORKFLOW} did not parse to a mapping"
    return spec


def _job(job_id):
    jobs = _spec()["jobs"]
    assert job_id in jobs, f"{WORKFLOW} declares no {job_id!r} job"
    return jobs[job_id]


def test_the_unlock_job_runs_only_for_an_unlock_with_a_non_empty_queue():
    unlock = _job("unlock")
    assert unlock.get("needs") == UNLOCK_NEEDS, (
        f"unlock's needs changed to {unlock.get('needs')!r}: `guard` and `review` are in "
        "the list so a rejected dispatch reaches `!failure()` -- without them `detect` is "
        "skipped and its empty outputs read as a non-empty queue"
    )
    # `.get`, not `[...]`: a DELETED `if:` is the fail-open mutation, and a
    # KeyError would red without naming what went missing.
    assert unlock.get("if") == UNLOCK_IF, (
        f"unlock's `if:` is {unlock.get('if')!r}, not {UNLOCK_IF!r} -- an unlock job that "
        "runs on an apply dispatch, or on a rejected one, force-unlocks state nobody asked "
        "about"
    )


def test_the_unlock_matrix_reads_cells_and_never_fails_fast():
    assert _job("unlock").get("strategy") == UNLOCK_STRATEGY, (
        f"unlock's strategy is {_job('unlock').get('strategy')!r}, not {UNLOCK_STRATEGY!r} "
        "-- `fail-fast: true` lets one cell that cannot read its lock state strand every "
        "sibling's lock, and the `waves` output is the empty string in unlock mode, so a "
        "matrix over it dies at fromJSON"
    )


def test_the_unlock_job_calls_unlock_cell_and_nothing_from_the_apply_family():
    steps = _job("unlock")["steps"]
    uses = [str(s["uses"]) for s in steps if s.get("uses")]
    assert uses, "the unlock job declares no `uses:` steps -- this guard would assert nothing"
    engine = [u for u in uses if "ship-iac/shipmate/actions/" in u]
    assert [u for u in engine if "/actions/unlock-cell@" in u], (
        f"the unlock job calls no unlock-cell action; its engine steps are {engine}"
    )
    leaked = [u for u in uses if any(f"/actions/{a}@" in u for a in FORBIDDEN)]
    assert not leaked, (
        f"the unlock job calls apply-family actions {leaked} -- releasing a lock must never "
        "mutate infrastructure or complete an apply check"
    )


def test_the_waves_job_refuses_to_run_for_an_unlock():
    apply = _job("apply")
    assert apply.get("if") == APPLY_IF, (
        f"the waves job's `if:` is {apply.get('if')!r}, not {APPLY_IF!r} -- without the "
        "mode clause an unlock dispatch applies every pending cell as well"
    )


def test_both_summary_steps_are_skipped_for_an_unlock():
    steps = _job("summary")["steps"]
    assert len(steps) == 2, f"summary no longer has exactly two steps: {len(steps)}"
    for step in steps:
        assert step.get("if") == SUMMARY_STEP_IF, (
            f"summary step {step.get('name') or step.get('uses')!r} has `if:` "
            f"{step.get('if')!r}, not {SUMMARY_STEP_IF!r} -- unlock completes no apply "
            "check, so a gate refresh is a no-op and a result comment has no cells to report"
        )


def test_mode_defaults_to_apply():
    spec = _spec()
    # pyyaml parses a bare `on:` key as boolean True.
    on = spec.get("on") or spec.get(True)
    mode = on["workflow_call"]["inputs"]["mode"]
    assert mode == {"required": False, "type": "string", "default": "apply"}, (
        f"the mode input is declared {mode!r} -- every existing apply dispatch sends no "
        "mode at all, so any other default turns them into unlocks or rejects them"
    )
