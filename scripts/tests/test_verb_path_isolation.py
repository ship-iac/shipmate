"""One workflow file per verb: `apply.yml` applies, `unlock.yml` unlocks, and neither file can
do the other's work.

This replaces the `mode`-input split that kept the two paths apart inside a single file, so the
guard holds in both directions -- a test that only read `unlock.yml` would go quiet on
`apply.yml` growing an unlock job back, the exact regression the file split rules out.

Pinned on the apply side:

- its `workflow_call` inputs, whole. The retired `mode` rail coming back is the regression, and
  an absence nothing compares is fail-open by construction;
- its job ids, whole, and that no step anywhere in the file calls `unlock-cell`;
- the waves job's `if:`, which must carry no verb clause;
- both `summary` steps running unconditionally: an `if:` there was only ever the unlock skip.

Pinned on the unlock side:

- its `workflow_call` inputs, whole. The dispatch body `actions/dispatch` sends is exactly what
  this set declares, and an undeclared input on a reusable workflow is a load-time rejection
  with no job and no log;
- every `uses:` its jobs call, in order and with the SHA dropped, so a step re-aimed at
  `apply-cell`, or a `gate-refresh` or `apply-summary` bolted on, reds rather than passing on
  the absence of a name;
- the unlock job's `if:`. Its skip-propagation form over `guard` and `detect` is what stops a
  rejected dispatch from fanning out, since a skipped `detect` reads its outputs as empty
  strings and `'' != '[]'` is true;
- its `strategy`: `fail-fast: false`, so one cell that cannot determine its lock state does not
  strand its siblings' locks, and the matrix source is `cells` (`waves` is the empty string on
  the unlock path, not `{}`, so a matrix over it dies at `fromJSON`);
- the concurrency group, against the wave jobs' and a hand-written constant;
- `detect`'s `apply-detect` inputs, whole, including the literal `unlock` mode;
- `detect`'s environment pre-flight. The unlock job binds `<env>-apply`, and GitHub creates a
  missing environment on demand with no reviewers and no branch policy, then keeps it, so an
  unlock into a missing environment would retire the reviewer gate for every later apply of
  that env. The apply path gets this refusal from apply-env-level's `snapshot` job.

Not re-pinned here, to keep one selector per property: the bot-actor `guard` job
(`test_apply_dispatch_actor_guard.py` covers both files), the `<env>-apply` environment binding
(`test_apply_env_binding_guard.py`), and each job's `needs` list (the actor guard's
whole-list-per-job map).

Threat model is accidental regression -- a clause dropped in a refactor, a matrix pointed at the
wrong output, a step re-aimed at the apply family -- not a hostile edit to files every consumer
reviews.
"""

import yaml
from _loader import WORKFLOWS

APPLY = "apply.yml"
UNLOCK = "unlock.yml"
ENV_LEVEL = "apply-env-level.yml"

#: The whole `workflow_call.inputs` mapping of each file, hand-written. `mode` is absent from
#: the apply side and never existed on the unlock side. On the unlock side this set is also the
#: dispatch contract: an input the body sends that this file does not declare kills the run at
#: startup with no job, no check-run and no retrievable log.
APPLY_INPUTS = {
    "environment": {"required": True, "type": "string"},
    "ref": {"required": True, "type": "string"},
    "pr_number": {"required": True, "type": "string"},
    "state_suffix": {"required": True, "type": "string"},
}
UNLOCK_INPUTS = {
    "environment": {"required": True, "type": "string"},
    "ref": {"required": True, "type": "string"},
}

#: The whole job-id set of each file. The apply side is listed so pasting an `unlock` job back
#: in reds on the job list as well as on the action check below: a guard that only looks for a
#: forbidden action name passes whenever the leaked job reaches for a different one.
APPLY_JOBS = {"guard", "review", "detect", "apply", "summary"}
UNLOCK_JOBS = {"guard", "detect", "unlock"}

#: Every `uses:` each unlock job declares, in order, SHA dropped: a repin must not redden this,
#: a reordered or added step must. This is the positive form of "calls nothing from the apply
#: family" -- there is no room in the list for one.
UNLOCK_STEP_ACTIONS = {
    "guard": [],
    "detect": [
        "actions/checkout",
        "ship-iac/shipmate/actions/setup",
        "ship-iac/shipmate/actions/apply-detect",
        "ship-iac/shipmate/actions/verify-environments",
    ],
    "unlock": [
        "actions/checkout",
        "ship-iac/shipmate/actions/setup",
        "aws-actions/configure-aws-credentials",
        "ship-iac/shipmate/actions/unlock-cell",
    ],
}

#: `run:` steps per unlock job, hand-written. The `uses:` list above cannot see a shell step,
#: and `tofu apply` pasted into the unlock job would run with the `<env>-apply` binding,
#: id-token: write and an already-assumed apply role. The apply side takes no mirror of this,
#: because `apply.yml` legitimately runs shell, so a `tofu force-unlock` pasted there stays a
#: blind spot rather than a second selector here.
UNLOCK_RUN_STEPS = {"guard": 1, "detect": 0, "unlock": 0}

#: The whole `outputs:` mapping of unlock's `detect`. `UNLOCK_STRATEGY` pins the consumer end
#: of this wire, and without this one the producer end can be re-aimed at `waves`, the empty
#: string here, which fans the job out and dies at fromJSON.
DETECT_OUTPUTS = {"cells": "${{ steps.d.outputs.cells }}"}

#: Named so a failure says which one leaked in.
APPLY_FAMILY = ("apply-cell", "apply-complete", "gate-refresh", "apply-summary")
APPLY_FAMILY_WORKFLOW = ".github/workflows/apply-env-level.yml"

#: The whole `if:` of the waves job. The mode clause went with the input, and it must not come
#: back as a verb clause either.
WAVES_IF = "${{ !failure() && !cancelled() && needs.detect.outputs.empty != 'true' }}"

#: The whole `if:` of the unlock job. `guard` is in its `needs` too, so a rejected dispatch
#: reaches `!failure()` here instead of leaving `detect` skipped, whose outputs read as empty
#: strings, and `'' != '[]'` is true.
UNLOCK_IF = "${{ !failure() && !cancelled() && needs.detect.outputs.cells != '[]' }}"

#: The whole `strategy:` mapping of the unlock job.
UNLOCK_STRATEGY = {
    "fail-fast": False,
    "matrix": {"include": "${{ fromJSON(needs.detect.outputs.cells) }}"},
}

#: The per-cell serialization group, hand-written. Asserted equal to both the unlock job's and
#: every wave job's: a live apply for a cell makes the unlock queue behind it, so by the time it
#: runs the lock is either gone or genuinely orphaned. Deriving it from either file would pass
#: whatever that file says.
CONCURRENCY_GROUP = "apply-${{ matrix.environment }}-${{ matrix.stack }}"
WAVES = [f"wave{i}" for i in range(8)]

#: The whole `with:` of unlock's `apply-detect` step. `mode` is a literal, not an expression:
#: the file is the verb, so nothing may make it configurable. `review-decision` and
#: `ungated-envs` are absent because `run_unlock` reads neither -- an approval reviews a diff
#: and unlock applies none.
DETECT_WITH = {
    "environment": "${{ inputs.environment }}",
    "mode": "unlock",
    "head-sha": "${{ inputs.ref }}",
    "github-token": "${{ github.token }}",
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
}

#: The whole `if:` and `with:` of the environment pre-flight. The queue is one flat array and
#: the script's input shape is the waves object, hence the single-wave wrapper. `!= '[]'` is
#: load-bearing because the script refuses an empty cell set by design.
PREFLIGHT_ACTION = "ship-iac/shipmate/actions/verify-environments"
PREFLIGHT_IF = "${{ steps.d.outputs.cells != '[]' }}"
PREFLIGHT_WITH = {
    "waves-json": "${{ format('{{\"wave0\":{0}}}', steps.d.outputs.cells) }}",
    "shared-envs": "${{ vars.SHIPMATE_SHARED_ENVS }}",
    "github-token": "${{ github.token }}",
}


def _spec(workflow):
    spec = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    assert isinstance(spec, dict), f"{workflow} did not parse to a mapping"
    return spec


def _inputs(workflow):
    # pyyaml parses a bare `on:` key as boolean True.
    spec = _spec(workflow)
    on = spec.get("on") or spec.get(True)
    return (on["workflow_call"].get("inputs") or {}) if isinstance(on, dict) else {}


def _jobs(workflow):
    return _spec(workflow)["jobs"]


def _job(workflow, job_id):
    jobs = _jobs(workflow)
    assert job_id in jobs, f"{workflow} declares no {job_id!r} job"
    return jobs[job_id]


def _uses(job):
    return [str(s["uses"]) for s in (job.get("steps") or []) if s.get("uses")]


def _runs(job):
    return [s for s in (job.get("steps") or []) if s.get("run")]


def test_the_apply_workflow_declares_no_mode_rail():
    got = _inputs(APPLY)
    assert got == APPLY_INPUTS, (
        f"{APPLY}'s workflow_call inputs are {got!r}, not {APPLY_INPUTS!r} -- the verb "
        "is the workflow file now, so a `mode` input coming back is a second rail "
        "deciding what an apply dispatch does"
    )


def test_the_apply_workflow_can_release_no_lock():
    jobs = _jobs(APPLY)
    assert set(jobs) == APPLY_JOBS, (
        f"{APPLY}'s jobs are {sorted(jobs)}, not {sorted(APPLY_JOBS)} -- an unlock job "
        "back in this file force-unlocks state on the apply dispatch's authority"
    )
    leaked = {
        job_id: [u for u in _uses(job) if "/actions/unlock-cell@" in u]
        for job_id, job in jobs.items()
    }
    leaked = {k: v for k, v in leaked.items() if v}
    assert not leaked, (
        f"{APPLY} calls unlock-cell from {leaked} -- releasing a lock is its own "
        "workflow, reached by its own dispatch"
    )


def test_the_waves_job_runs_on_every_apply_dispatch():
    got = _job(APPLY, "apply").get("if")
    assert got == WAVES_IF, (
        f"the waves job's `if:` is {got!r}, not {WAVES_IF!r} -- a verb clause here would "
        "be a second place deciding what this file does, and the file already decided"
    )


def test_both_summary_steps_run_on_every_apply_dispatch():
    steps = _job(APPLY, "summary")["steps"]
    assert len(steps) == 2, f"summary no longer has exactly two steps: {len(steps)}"
    conditions = [s.get("if") for s in steps]
    assert conditions == [None, None], (
        f"summary's steps carry `if:` {conditions!r} -- the only condition they ever had "
        "was the unlock skip, and a gate refresh or result comment that silently stops "
        "running leaves the developer no feedback and the gate unrefreshed"
    )


def test_the_unlock_workflow_declares_exactly_the_dispatch_body():
    got = _inputs(UNLOCK)
    assert got == UNLOCK_INPUTS, (
        f"{UNLOCK}'s workflow_call inputs are {got!r}, not {UNLOCK_INPUTS!r} -- the "
        "dispatch body is exactly ref and environment, and an input this file declares "
        "that the body does not fill (or the reverse) is a load-time rejection with no "
        "job, no check-run and no retrievable log"
    )


def test_the_unlock_workflow_applies_nothing():
    jobs = _jobs(UNLOCK)
    assert set(jobs) == UNLOCK_JOBS, (
        f"{UNLOCK}'s jobs are {sorted(jobs)}, not {sorted(UNLOCK_JOBS)}"
    )
    got = {j: [u.split("@")[0] for u in _uses(job)] for j, job in jobs.items()}
    assert got == UNLOCK_STEP_ACTIONS, (
        f"{UNLOCK}'s steps call {got!r}, not {UNLOCK_STEP_ACTIONS!r} -- releasing a lock "
        "must never mutate infrastructure, complete an apply check, refresh the gate or "
        "post a result comment"
    )
    named = {
        job_id: [u for u in uses if any(u.endswith(f"/actions/{a}") for a in APPLY_FAMILY)]
        for job_id, uses in got.items()
    }
    named = {k: v for k, v in named.items() if v}
    assert not named, f"{UNLOCK} calls apply-family actions {named}"
    runs = {job_id: len(_runs(job)) for job_id, job in jobs.items()}
    assert runs == UNLOCK_RUN_STEPS, (
        f"{UNLOCK}'s jobs carry {runs!r} `run:` steps, not {UNLOCK_RUN_STEPS!r} -- a shell "
        "step is invisible to the `uses:` comparison above, and a `tofu apply` in the "
        "unlock job would run with the apply environment bound and its role assumed"
    )
    callers = {
        job_id: job["uses"]
        for job_id, job in jobs.items()
        if APPLY_FAMILY_WORKFLOW in str(job.get("uses", ""))
    }
    assert not callers, (
        f"{UNLOCK} calls the apply wave engine from {callers} -- that workflow applies "
        "every cell it is handed"
    )


def test_the_unlock_detect_publishes_the_queue_and_nothing_else():
    got = _job(UNLOCK, "detect").get("outputs")
    assert got == DETECT_OUTPUTS, (
        f"unlock's detect publishes {got!r}, not {DETECT_OUTPUTS!r} -- the matrix reads "
        "`cells`, and a producer re-aimed at `waves` hands it the empty string, which "
        "`!= '[]'` reads as a queue and fromJSON then kills"
    )


def test_the_unlock_job_runs_only_with_a_non_empty_queue():
    unlock = _job(UNLOCK, "unlock")
    # `.get`, not `[...]`: a deleted `if:` is the fail-open mutation, and a KeyError would red
    # without naming what went missing.
    got = unlock.get("if")
    assert got == UNLOCK_IF, (
        f"unlock's `if:` is {got!r}, not {UNLOCK_IF!r} -- without `!failure()` a rejected "
        "dispatch leaves `detect` skipped, whose outputs read as empty strings, and "
        "`'' != '[]'` fans out over a queue nobody computed"
    )


def test_the_unlock_matrix_reads_cells_and_never_fails_fast():
    got = _job(UNLOCK, "unlock").get("strategy")
    assert got == UNLOCK_STRATEGY, (
        f"unlock's strategy is {got!r}, not {UNLOCK_STRATEGY!r} -- `fail-fast: true` lets "
        "one cell that cannot read its lock state strand every sibling's lock, and the "
        "`waves` output is the empty string on this path, so a matrix over it dies at "
        "fromJSON"
    )


def test_the_unlock_job_shares_the_wave_jobs_serialization_queue():
    unlock_group = (_job(UNLOCK, "unlock").get("concurrency") or {}).get("group")
    wave_groups = {w: (_job(ENV_LEVEL, w).get("concurrency") or {}).get("group") for w in WAVES}
    assert unlock_group == CONCURRENCY_GROUP, (
        f"the unlock job's concurrency group is {unlock_group!r}, not "
        f"{CONCURRENCY_GROUP!r} -- a group that differs from the wave jobs' puts an "
        "unlock and a live apply for one cell into different queues, so the unlock can "
        "break a lock the apply is holding"
    )
    assert set(wave_groups.values()) == {CONCURRENCY_GROUP}, (
        f"{ENV_LEVEL}'s wave jobs use {wave_groups!r}, not {CONCURRENCY_GROUP!r} -- the "
        "identity with the unlock job's group is what makes an unlock wait behind a live "
        "apply for the same cell"
    )


def test_the_unlock_detect_passes_the_verb_as_a_literal():
    step = next(
        s for s in _job(UNLOCK, "detect")["steps"] if "/actions/apply-detect@" in str(s.get("uses"))
    )
    got = step.get("with")
    assert got == DETECT_WITH, (
        f"unlock's apply-detect inputs are {got!r}, not {DETECT_WITH!r} -- `mode` is a "
        "literal because the file is the verb; an expression there would make an unlock "
        "run computable into an apply work set"
    )


def test_the_unlock_detect_refuses_an_unlock_into_a_missing_environment():
    steps = _job(UNLOCK, "detect")["steps"]
    preflight = [s for s in steps if PREFLIGHT_ACTION in str(s.get("uses", ""))]
    assert len(preflight) == 1, (
        f"{UNLOCK}'s detect job declares {len(preflight)} {PREFLIGHT_ACTION} steps, not 1 "
        "-- without it an unlock binding an environment nobody created gets one "
        "auto-created with no reviewers, which then satisfies the apply path's own "
        "pre-flight forever"
    )
    step = preflight[0]
    assert step.get("if") == PREFLIGHT_IF, (
        f"the pre-flight's `if:` is {step.get('if')!r}, not {PREFLIGHT_IF!r} -- it must run "
        "for every unlock with a queue, and the script refuses an empty cell set by design"
    )
    assert step.get("with") == PREFLIGHT_WITH, (
        f"the pre-flight's inputs are {step.get('with')!r}, not {PREFLIGHT_WITH!r} -- a "
        "queue wrapped into the wrong shape, or a shared-envs value that is not the "
        "repository variable, checks environments the unlock job does not bind"
    )
    assert step.get("continue-on-error") in (None, False), (
        "the pre-flight is continue-on-error: it would name the missing environment and "
        "let the unlock bind (and so create) it anyway"
    )
    assert "detect" in (_job(UNLOCK, "unlock").get("needs") or []), (
        "the unlock job no longer needs `detect`, so the environment pre-flight can no "
        "longer refuse it"
    )
