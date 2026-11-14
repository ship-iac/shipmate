"""Only a bot actor may dispatch a verb; a human with write access must not
bypass authorize by hand-rolling `gh workflow run apply.yml`/`unlock.yml`.

The rejection is its own tiny job, `guard`, not a first step of `detect`. Two separate outcomes
have to be told apart:

- an unauthorised dispatch must not reach `summary`, which mints the App key and comments on the
  dispatcher-supplied pull request number;
- a genuine `detect` failure -- a matrix over the 256-cell cap, a `needs` cycle, a cell whose
  apply check records no plan run -- must still reach `summary`, or the developer gets no failure
  comment and no gate refresh at all.

`if: always() && needs.detect.result == 'success'` silenced both. In `apply.yml`, `run` needs
`guard` and starts `apply-run.yml` only on its success, so a rejected dispatch starts no apply job;
inside, keying `summary` on `review` separates the two outcomes. In `unlock.yml` every fan-out job
carries `guard` in its `needs` so the rejection reaches its `!failure()` clause: a rejected
dispatch leaves `detect` skipped, a skipped `detect` reads its outputs as empty strings, and the
fan-out jobs' `!= 'true'` guards read empty as "not empty" and would start on it.

A guard on `deploy.yml`'s `detect` would break every post-merge deploy, where `github.actor` is
the human who merged. That workflow must stay guard-free.

Every assertion below is on the whole parsed expression or string, not a substring. The substring
form passed against `if: ${{ always() || needs.detect.result == 'success' }}` -- the hole
reopened, `||` instead of `&&` -- and against a guard step moved to the end of `detect`, after
the dispatcher-controlled ref had already been checked out.
"""

import re

import _shipmate
from _loader import workflow_jobs

GUARD_JOB = "guard"
GUARD_IF = "${{ !endsWith(github.actor, '[bot]') }}"


def _refuse(line):
    return f'echo "{line}"\nexit 1\n'


CANONICAL_PR_NUMBER_RUN = (
    'if [[ ! "$PR_NUMBER" =~ ^[1-9][0-9]{0,9}$ ]]; then\n'
    '  echo "::error::apply refused: pr_number is not a pull request number (1 to 10 digits, '
    "no leading zero); a workflow or App other than shipmate's comment-ops and autoplan sent "
    "this dispatch; comment 'shipmate apply' on the pull request instead\"\n"
    "  exit 1\n"
    "fi\n"
)
#: The canonical-number step whole: its value read through `env:`, never `${{ }}` in `run:`.
CANONICAL_PR_NUMBER_STEP = {
    "name": "Reject a pull request number that is not canonical",
    "env": {"PR_NUMBER": "${{ inputs.pr_number }}"},
    "run": CANONICAL_PR_NUMBER_RUN,
}
#: The guard job's refusal steps per workflow, as `(if, ::error:: line)`. The bot-actor
#: rejection runs first in both. `apply.yml` also refuses an `auto` dispatch reaching a caller job
#: that does not pass `auto: true`, which would run the App's dispatch as a bare apply,
#: `auto: true` on any other run, and a run without it that is not a dispatch with verb
#: `apply`, which fails closed when the dispatch inputs read empty. A called workflow's `github`
#: context is its caller's, so `github.event.inputs.verb` is the dispatch's own verb. Its last
#: step, in `GUARD_TAIL`, refuses a `pr_number` that is not canonical before it keys a group.
GUARD_STEPS = {
    "apply.yml": [
        (
            GUARD_IF,
            (
                "::error::apply refused: the dispatching actor is not a [bot]; comment "
                "'shipmate apply' on the pull request instead of running a direct workflow_dispatch"
            ),
        ),
        (
            (
                "${{ github.event_name == 'workflow_dispatch' "
                "&& github.event.inputs.verb == 'auto' && !inputs.auto }}"
            ),
            (
                "::error::apply refused: this run was dispatched with verb 'auto', but the "
                "calling job does not pass 'auto: true'; give the shipmate.yml 'auto' job the "
                "published 'with:' block (shipmate doctor names the difference)"
            ),
        ),
        (
            (
                "${{ inputs.auto && (github.event_name != 'workflow_dispatch' "
                "|| github.event.inputs.verb != 'auto') }}"
            ),
            (
                "::error::apply refused: the calling job passes 'auto: true', but this run is "
                "not a dispatch with verb 'auto'; only the shipmate.yml 'auto' job passes it"
            ),
        ),
        (
            (
                "${{ !inputs.auto && (github.event_name != 'workflow_dispatch' "
                "|| github.event.inputs.verb != 'apply') }}"
            ),
            (
                "::error::apply refused: this run is not a dispatch with verb 'apply' and the "
                "calling job does not pass 'auto: true'; comment 'shipmate apply' on the pull "
                "request, and give the shipmate.yml 'apply' job the published 'if:' (shipmate "
                "doctor names the difference)"
            ),
        ),
    ],
    "unlock.yml": [
        (
            GUARD_IF,
            (
                "::error::unlock refused: the dispatching actor is not a [bot]; comment "
                "'shipmate unlock <env>' on the pull request instead of running a direct "
                "workflow_dispatch"
            ),
        ),
    ],
}
#: The guard job's steps after `GUARD_STEPS`, as `(if, run)`.
GUARD_TAIL = {"apply.yml": [(None, CANONICAL_PR_NUMBER_RUN)], "unlock.yml": []}
#: The whole `needs:` list each dispatched workflow's `detect` carries. The apply path waits on
#: the `review` job that reads the pull request's review decision server-side; it runs in
#: `apply-run.yml`, which only `apply.yml`'s `run` starts, after `guard` succeeded. `unlock.yml`
#: carries no such job, because an approval reviews a diff and unlock applies none. Compared
#: whole, so neither `guard` dropping out nor an unreviewed extra dependency can slip in.
DETECT_NEEDS = {
    "apply-run.yml": ["review"],
    "unlock.yml": ["guard"],
}
#: The whole `needs:` list of every job downstream of `detect`, per file. Every job that can fail
#: before `detect` has to appear, or its failure never reaches this job's `!failure()`: `detect`
#: is skipped, its outputs read as empty strings, the `!= 'true'` clause reads empty as
#: "not empty", and the job fans out on `fromJSON('')`. `summary` is in the map for the other
#: half of the same property, its `results:` being `join(needs.*.result, ',')` over its own
#: needs, so an omitted pre-`detect` job renders the non-failure comment over a run that died.
#: Compared whole, per job, so neither a pre-`detect` job dropping out nor a new one being
#: omitted passes unnoticed.
DOWNSTREAM_NEEDS = {
    "apply-run.yml": {
        "lock": ["review", "detect"],
        "envlevel0": ["review", "detect", "lock"],
        "envlevel1": ["review", "detect", "lock", "envlevel0"],
        "envlevel2": ["review", "detect", "lock", "envlevel0", "envlevel1"],
        "envlevel3": ["review", "detect", "lock", "envlevel0", "envlevel1", "envlevel2"],
        "summary": [
            "review",
            "detect",
            "lock",
            "envlevel0",
            "envlevel1",
            "envlevel2",
            "envlevel3",
        ],
    },
    # No `summary` job at all: an unlock posts no comment and refreshes no gate, which is why
    # nothing here mints the App key.
    "unlock.yml": {"unlock": ["guard", "detect"]},
}
SUMMARY_IF = "${{ always() && needs.review.result == 'success' }}"
#: Every workflow `actions/dispatch` can target that carries the bot-actor rejection.
DISPATCH_PATHS = ("apply.yml", "unlock.yml")
#: The whole `needs:` of each job in `apply.yml`: `guard` gates the call into `apply-run.yml`.
APPLY_NEEDS = {"guard": [], "run": ["guard"], "outcome": ["guard", "run"]}


def _needs(job):
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def test_dispatched_workflows_reject_dispatches_missing_a_bot_actor():
    """Each guard step's whole `if:` and whole run body, in order: the bot-actor rejection
    first, so nothing runs in that job before it. That is why it is not a step of `detect`,
    where `actions/checkout` of the dispatcher-supplied ref sits.

    Mutations (each run, red): drop `&& !inputs.auto` from apply.yml's second step; replace
    `||` with `&&` in its third; delete the third step; swap `exit 1` for `exit 0` in the
    second; `==` for `!=` in the fourth; drop `!inputs.auto` from the fourth (every auto run
    refuses there); `&&` for its `||` (a non-dispatch call passes); delete the fourth step."""
    for name, expected in GUARD_STEPS.items():
        steps = workflow_jobs(name)[GUARD_JOB]["steps"]
        got = [(step.get("if"), step.get("run")) for step in steps]
        want = [(cond, _refuse(line)) for cond, line in expected] + GUARD_TAIL[name]
        assert got == want, f"{name}: {GUARD_JOB} steps differ from the pinned list: {got!r}"


def test_guard_job_holds_no_credentials_and_runs_no_repository_code():
    # It gates the App key, so it must not be able to reach one, name the environment that holds
    # one, or execute the dispatcher's ref.
    for name in DISPATCH_PATHS:
        job = workflow_jobs(name)[GUARD_JOB]
        assert job.get("permissions") == {}, (
            f"{name}: {GUARD_JOB} must grant no token permissions, got {job.get('permissions')!r}"
        )
        assert job.get("environment") is None, f"{name}: {GUARD_JOB} must name no environment"
        assert job.get("secrets") is None, f"{name}: {GUARD_JOB} must be passed no secrets"
        for step in job["steps"]:
            assert "checkout" not in str(step.get("uses", "")).lower()
            assert "secrets." not in str(step.get("run", ""))


def test_every_fan_out_job_carries_the_guard_in_its_needs():
    """A rejected dispatch leaves `detect` skipped, and a skipped `detect`'s outputs are empty
    strings, which `needs.detect.outputs.*_empty != 'true'` reads as "not empty". In `unlock.yml`
    `guard` in each job's `needs` is what makes the rejection visible to its `!failure()`
    clause. In `apply.yml` every apply job sits behind `run`, which needs `guard`; `apply-run.yml`
    holds no `guard`, so its jobs' needs are pinned by `DOWNSTREAM_NEEDS` instead.

    Mutation: drop `guard` from `apply.yml`'s `run` `needs:` -- red."""
    for name, expected in DETECT_NEEDS.items():
        got = _needs(workflow_jobs(name)["detect"])
        assert got == expected, f"{name}: detect must need exactly {expected!r}, got {got!r}"
    for job_id, job in workflow_jobs("unlock.yml").items():
        if job_id in (GUARD_JOB, "detect"):
            continue
        assert GUARD_JOB in _needs(job), (
            f"unlock.yml: job {job_id!r} must carry {GUARD_JOB!r} in its needs, got {_needs(job)!r}"
        )
    got = {job_id: _needs(job) for job_id, job in workflow_jobs("apply.yml").items()}
    assert got == APPLY_NEEDS, f"apply.yml: jobs and needs must be exactly {APPLY_NEEDS!r}"


def test_the_canonical_pr_number_step_is_this_whole_step():
    """The step is pinned whole, and its regex equals the pattern `_shipmate.is_pr_number`
    accepts, anchored, so the two readings of a pull request number cannot drift apart.

    Mutations: delete the step -- red; loosen its pattern to `^[0-9]+$` -- red."""
    steps = workflow_jobs("apply.yml")[GUARD_JOB]["steps"]
    assert steps[-1] == CANONICAL_PR_NUMBER_STEP
    regex = re.search(r"=~ (\S+) \]\]", steps[-1]["run"]).group(1)
    assert regex == "^" + _shipmate._PR_NUMBER.pattern + "$"


def test_every_fan_out_job_needs_every_job_that_runs_before_detect():
    """`DOWNSTREAM_NEEDS`, whole list per job, also pins the env-level order: each level needs
    every level before it, so a bare apply runs them in `needs` order.

    Mutation: drop `envlevel1` from `apply-run.yml`'s `envlevel2` `needs:` -- red."""
    for name, expected_by_job in DOWNSTREAM_NEEDS.items():
        jobs = workflow_jobs(name)
        # Discovery decides the scope: a job added downstream of `detect` and left out of the map
        # is the omission this pins.
        downstream = {j for j in jobs if j not in (GUARD_JOB, "detect", "review")}
        assert downstream == set(expected_by_job), (
            f"{name}: jobs downstream of detect are {sorted(downstream)}, but this "
            f"guard knows {sorted(expected_by_job)} -- add the new job's whole needs list"
        )
        for job_id, expected in expected_by_job.items():
            assert _needs(jobs[job_id]) == expected, (
                f"{name}: {job_id} must need exactly {expected!r}, "
                f"got {_needs(jobs[job_id])!r} -- a job that can fail before `detect` "
                "and is missing here has its failure invisible to this job's "
                "`!failure()`, and the job fans out on an empty waves_json"
            )


def test_deploy_detect_carries_no_bot_actor_guard():
    jobs = workflow_jobs("deploy.yml")
    assert GUARD_JOB not in jobs, (
        "deploy.yml must not carry the dispatch guard -- it runs on `push`, "
        "where github.actor is the human who merged"
    )
    first_step = jobs["detect"]["steps"][0]
    assert first_step.get("if") != GUARD_IF, (
        "deploy.yml's detect must not carry the dispatch guard -- it runs on "
        "`push`, where github.actor is the human who merged"
    )


def test_apply_summary_is_gated_on_the_review_and_not_on_detect():
    """`if: always()` alone runs `summary` regardless of every `needs` conclusion, and
    `summary`'s steps read raw workflow_call inputs (pr_number, ref) rather than anything gated
    on `detect`. So a bare `always()` would let a dispatch whose `review` refused the
    `pr_number`/`ref` binding mint the App key, comment on that pull request and refresh its
    gate. `apply-run.yml` starts only after `guard` succeeded, so `review`'s success implies the
    guard's. Gating on
    `needs.detect.result` instead is the opposite regression: it also silences every genuine
    detect failure, so the developer gets no failure comment and no gate refresh. The whole
    expression is pinned so either escape fails.
    Mutation: replace the review clause with `needs.detect.result == 'success'` -> red."""
    job = workflow_jobs("apply-run.yml")["summary"]
    summary_if = job.get("if")
    assert summary_if == SUMMARY_IF, (
        f"summary's if: must be exactly {SUMMARY_IF!r}, got {summary_if!r}"
    )
    assert "detect" in _needs(job), (
        "summary still reads detect's outputs, so detect must stay in its needs"
    )
