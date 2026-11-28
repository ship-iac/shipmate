"""Only a bot actor may dispatch a verb; a human with write access must not
bypass authorize by hand-rolling `gh workflow run apply.yml`/`unlock.yml`.

In `apply.yml`, the fence expression decides whether a dispatch reaches the `shipmate-engine`
environment: it is each `guard` check step's condition negated, then the canonical `pr_number`
test, and it is `queue.if` whole and `run.if` after `!cancelled()`, so a dispatch any check
refuses skips both and binds no key. The bash steps stay authoritative: `queue` runs `guard`'s
six check steps by YAML alias, and `apply-run.yml`'s `review` runs a copy of them, before any
step reads the App key. `guard` runs only when `run` was skipped, and names the rule that
refused, or refuses as no rule does.

In `unlock.yml` the rejection is its own tiny job, `guard`, not a first step of `detect`, and
every fan-out job carries `guard` in its `needs` so the rejection reaches its `!failure()`
clause: a rejected dispatch leaves `detect` skipped, a skipped `detect` reads its outputs as
empty strings, and the fan-out jobs' `!= 'true'` guards read empty as "not empty" and would start
on it. Inside `apply-run.yml`, keying `summary` on `review` keeps a genuine `detect` failure
reported while an unauthorised dispatch never reaches it.

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
#: The guard job's check steps per workflow, as `(if, ::error:: line)`. The bot-actor
#: rejection runs first in both. `apply.yml` also refuses an `auto` dispatch reaching a caller job
#: that does not pass `auto: true`, which would run the App's dispatch as a bare apply,
#: `auto: true` on any other run, and a run without it that is not a dispatch with verb
#: `apply`, which fails closed when the dispatch inputs read empty. A called workflow's `github`
#: context is its caller's, so `github.event.inputs.verb` is the dispatch's own verb. Its last
#: check, in `GUARD_TAIL`, refuses a `pr_number` that is not canonical before it keys a group.
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
        (
            (
                "${{ github.event_name == 'workflow_dispatch' && "
                "github.event.inputs.verb == 'apply' && github.event.inputs.comment_id == '' }}"
            ),
            (
                "::error::apply refused: this apply dispatch carries no comment_id, so no comment "
                "can be claimed for it; comment 'shipmate apply' on the pull request instead"
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
#: The line `apply.yml`'s `guard` refuses with when `run` was skipped and every check passed:
#: `run.if` and the check steps disagree, or the expression failed to evaluate.
NO_RULE_LINE = (
    "::error::apply refused: this dispatch did not meet the apply job's conditions and no rule "
    "names why; comment 'shipmate apply' on the pull request"
)
#: The guard job's steps after `GUARD_STEPS`, as `(if, run)`. Unlock's is the started reaction,
#: a `uses:` step that `test_claim_wiring.py` pins whole.
GUARD_TAIL = {
    "apply.yml": [(None, CANONICAL_PR_NUMBER_RUN), (None, _refuse(NO_RULE_LINE))],
    "unlock.yml": [(None, None)],
}
#: How many of `apply.yml`'s `guard` steps are checks `queue` and `review` repeat; the no-rule
#: step follows.
CHECK_STEP_COUNT = 6
#: `inputs.pr_number` in canonical form, as an expression: it survives a number round trip
#: unchanged (no leading zero, sign, exponent or space), is not the boolean `true` that
#: `fromJSON` would also round-trip, lies in 1 to 10 digits, and holds no `.`: a fraction such
#: as `1.5` also round-trips unchanged.
CANONICAL_PR_NUMBER_CLAUSE = (
    "format('{0}', fromJSON(inputs.pr_number)) == inputs.pr_number && inputs.pr_number != 'true' "
    "&& fromJSON(inputs.pr_number) >= 1 && fromJSON(inputs.pr_number) < 1e10 "
    "&& !contains(inputs.pr_number, '.')"
)
#: `apply.yml`'s whole `queue.if`, hand-written: every check step's condition negated, then the
#: canonical number clause. An expression error skips the job like a false one.
FENCE_IF = (
    "${{ !(!endsWith(github.actor, '[bot]')) "
    "&& !(github.event_name == 'workflow_dispatch' && github.event.inputs.verb == 'auto' "
    "&& !inputs.auto) "
    "&& !(inputs.auto && (github.event_name != 'workflow_dispatch' "
    "|| github.event.inputs.verb != 'auto')) "
    "&& !(!inputs.auto && (github.event_name != 'workflow_dispatch' "
    "|| github.event.inputs.verb != 'apply')) "
    "&& !(github.event_name == 'workflow_dispatch' && github.event.inputs.verb == 'apply' "
    "&& github.event.inputs.comment_id == '') "
    "&& format('{0}', fromJSON(inputs.pr_number)) == inputs.pr_number "
    "&& inputs.pr_number != 'true' "
    "&& fromJSON(inputs.pr_number) >= 1 && fromJSON(inputs.pr_number) < 1e10 "
    "&& !contains(inputs.pr_number, '.') }}"
)
#: `apply.yml`'s whole `run.if`: the fence after `!cancelled()`, which keeps a cancelled run from
#: queueing an apply.
RUN_IF = (
    "${{ !cancelled() && !(!endsWith(github.actor, '[bot]')) "
    "&& !(github.event_name == 'workflow_dispatch' && github.event.inputs.verb == 'auto' "
    "&& !inputs.auto) "
    "&& !(inputs.auto && (github.event_name != 'workflow_dispatch' "
    "|| github.event.inputs.verb != 'auto')) "
    "&& !(!inputs.auto && (github.event_name != 'workflow_dispatch' "
    "|| github.event.inputs.verb != 'apply')) "
    "&& !(github.event_name == 'workflow_dispatch' && github.event.inputs.verb == 'apply' "
    "&& github.event.inputs.comment_id == '') "
    "&& format('{0}', fromJSON(inputs.pr_number)) == inputs.pr_number "
    "&& inputs.pr_number != 'true' "
    "&& fromJSON(inputs.pr_number) >= 1 && fromJSON(inputs.pr_number) < 1e10 "
    "&& !contains(inputs.pr_number, '.') }}"
)
#: `apply.yml`'s whole `guard.if`. A run cancelled before `run` starts also reads it
#: skipped, and must not reach the no-rule step.
GUARD_JOB_IF = "${{ !cancelled() && needs.run.result == 'skipped' }}"
#: The whole `needs:` list each dispatched workflow's `detect` carries. The apply path's `detect`
#: reads the pull request's review decision itself, beside `review`, and `lock` needs both; it
#: runs in `apply-run.yml`, which only `apply.yml`'s `run` starts, after the fence passed the
#: dispatch. `unlock.yml` has no `review` job: an approval reviews a diff and unlock applies none.
#: Compared whole, so neither `guard` dropping out nor an unreviewed extra dependency can slip in.
DETECT_NEEDS = {
    "apply-run.yml": [],
    "unlock.yml": ["guard"],
}
#: The whole `needs:` list of every job downstream of `detect`, per file. Every job that can fail
#: before or beside `detect` has to appear, or its failure never reaches this job's `!failure()`.
#: In `unlock.yml` a failed `guard` leaves `detect` skipped, its outputs read as empty strings,
#: the `!= 'true'` clause reads empty as "not empty", and the job fans out on `fromJSON('')`.
#: `summary` is in the map for the other half of the same property, its `results:` being
#: `join(needs.*.result, ',')` over its own needs, so an omitted job renders the non-failure
#: comment over a run that died. Compared whole, per job, so neither such a job dropping out nor
#: a new one being omitted passes unnoticed.
DOWNSTREAM_NEEDS = {
    "apply-run.yml": {
        "lock": ["review", "detect"],
        "waves": ["review", "detect", "lock"],
        "unflip": ["review", "detect", "lock", "waves"],
        "summary": ["review", "detect", "lock", "waves", "unflip"],
    },
    # No `summary` job: an unlock refreshes no gate and `result` replies with the job token, so
    # nothing here mints the App key. `result` needs `guard` so a rejected dispatch skips it.
    "unlock.yml": {"unlock": ["guard", "detect"], "result": ["guard", "detect", "unlock"]},
}
SUMMARY_IF = (
    "${{ always() && needs.review.result == 'success' && needs.review.outputs.admitted == 'true' }}"
)
#: Every workflow `actions/dispatch` can target that carries the bot-actor rejection.
DISPATCH_PATHS = ("apply.yml", "unlock.yml")
#: The whole `needs:` of each job in `apply.yml`: `run` and `queue` start at once, `guard` reads
#: only whether `run` was skipped, and `outcome` reads `run`'s outputs.
APPLY_NEEDS = {
    "guard": ["run"],
    "run": [],
    "queue": [],
    "outcome": ["run"],
}


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
    refuses there); `&&` for its `||` (a non-dispatch call passes); delete the fourth step;
    `== ''` for `!= ''` in the fifth (an apply dispatch with no comment would bypass the claim);
    delete the fifth step; `!= 'apply'` for `!= 'plan'` in the fourth; delete the no-rule step;
    swap its `exit 1` for `exit 0`."""
    for name, expected in GUARD_STEPS.items():
        steps = workflow_jobs(name)[GUARD_JOB]["steps"]
        got = [(step.get("if"), step.get("run")) for step in steps]
        want = [(cond, _refuse(line)) for cond, line in expected] + GUARD_TAIL[name]
        assert got == want, f"{name}: {GUARD_JOB} steps differ from the pinned list: {got!r}"


def _body(expression):
    assert expression.startswith("${{ ") and expression.endswith(" }}"), expression
    return expression[len("${{ ") : -len(" }}")]


def test_the_fence_ifs_are_these_whole_expressions():
    """Mutations: delete the actor clause from `queue.if` -- red; delete the `< 1e10` clause
    from `run.if` -- red; drop `!cancelled() && ` from `run.if` -- red."""
    jobs = workflow_jobs("apply.yml")
    assert jobs["queue"]["if"] == FENCE_IF
    assert jobs["run"]["if"] == RUN_IF


def test_the_fence_negates_each_guard_check_step_in_order():
    """Derived from the file, beside the hand-written pins above: a check step edited without the
    fence lets the expression bind the key for a dispatch the step refuses, or skip `run` for
    one it accepts. The canonical-number step has no `if:`; its clause is the hand-written
    constant.

    Mutation: `!= 'apply'` for `!= 'plan'` in `guard`'s fourth step, both `if:`s untouched --
    red."""
    jobs = workflow_jobs("apply.yml")
    checks = jobs[GUARD_JOB]["steps"][:CHECK_STEP_COUNT]
    assert "if" not in checks[-1]
    clauses = [f"!({_body(step['if'])})" for step in checks[:-1]]
    body = " && ".join([*clauses, CANONICAL_PR_NUMBER_CLAUSE])
    assert jobs["queue"]["if"] == "${{ " + body + " }}"
    assert jobs["run"]["if"] == "${{ !cancelled() && " + body + " }}"


def test_queue_and_review_run_guards_check_steps_before_anything_else():
    """`queue`'s and `apply-run.yml` `review`'s first six steps equal `guard`'s check steps,
    parsed, so a dispatch the fence wrongly accepts still fails before any step reads the App
    key. `review`'s are a copy: YAML anchors do not cross files.

    Mutations: drop the canonical-number alias from `queue` -- red; `== 'auto'` for
    `== 'plan'` in `review`'s second step -- red; move `review`'s six steps after `claim` --
    red."""
    want = workflow_jobs("apply.yml")[GUARD_JOB]["steps"][:CHECK_STEP_COUNT]
    assert workflow_jobs("apply.yml")["queue"]["steps"][:CHECK_STEP_COUNT] == want
    assert workflow_jobs("apply-run.yml")["review"]["steps"][:CHECK_STEP_COUNT] == want


def test_guard_runs_only_when_run_is_skipped():
    """Mutations: `!cancelled()` for `always()` -- red; `== 'skipped'` for `!= 'skipped'`
    -- red."""
    assert workflow_jobs("apply.yml")[GUARD_JOB]["if"] == GUARD_JOB_IF


#: The guard job's whole `permissions:` per file. Unlock's writes the started reaction on the
#: command comment; the job checks nothing out, so no pull-request code holds that grant.
GUARD_PERMISSIONS = {"apply.yml": {}, "unlock.yml": {"pull-requests": "write"}}


def test_guard_job_holds_no_credentials_and_runs_no_repository_code():
    # It gates the App key, so it must not be able to reach one, name the environment that holds
    # one, or execute the dispatcher's ref; `queue` and `review` run the same check steps beside
    # the key.
    # Mutation: `permissions: { contents: read }` on unlock's guard -- red.
    for name in DISPATCH_PATHS:
        job = workflow_jobs(name)[GUARD_JOB]
        assert job.get("permissions") == GUARD_PERMISSIONS[name], (
            f"{name}: {GUARD_JOB} permissions differ, got {job.get('permissions')!r}"
        )
        assert job.get("environment") is None, f"{name}: {GUARD_JOB} must name no environment"
        assert job.get("secrets") is None, f"{name}: {GUARD_JOB} must be passed no secrets"
    checks = [
        *workflow_jobs("apply.yml")["queue"]["steps"][:CHECK_STEP_COUNT],
        *workflow_jobs("apply-run.yml")["review"]["steps"][:CHECK_STEP_COUNT],
    ]
    steps = [s for name in DISPATCH_PATHS for s in workflow_jobs(name)[GUARD_JOB]["steps"]]
    for step in [*steps, *checks]:
        assert "checkout" not in str(step.get("uses", "")).lower()
        assert "secrets." not in str(step.get("run", ""))


def test_every_fan_out_job_carries_the_guard_in_its_needs():
    """A rejected dispatch leaves `detect` skipped, and a skipped `detect`'s outputs are empty
    strings, which `needs.detect.outputs.*_empty != 'true'` reads as "not empty". In `unlock.yml`
    `guard` in each job's `needs` is what makes the rejection visible to its `!failure()`
    clause. In `apply.yml` every apply job sits behind `run`, whose `if:` is the fence;
    `apply-run.yml` holds no `guard`, so its jobs' needs are pinned by `DOWNSTREAM_NEEDS` instead.

    Mutations: add `queue` to `apply.yml`'s `run` `needs:` -- red; add `needs: [review]` to
    `apply-run.yml`'s `detect` -- red."""
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
    step = workflow_jobs("apply.yml")[GUARD_JOB]["steps"][CHECK_STEP_COUNT - 1]
    assert step == CANONICAL_PR_NUMBER_STEP
    match = re.search(r"=~ (\S+) \]\]", step["run"])
    assert match
    regex = match.group(1)
    assert regex == "^" + _shipmate._PR_NUMBER.pattern + "$"


def test_every_fan_out_job_needs_every_job_that_runs_before_detect():
    """`DOWNSTREAM_NEEDS`, whole list per job.

    Mutations: drop `lock` from `apply-run.yml`'s `waves` `needs:` -- red; drop
    `review` from `lock`'s -- red; drop `unflip` from `summary`'s, so its gate refresh can read
    before `unflip` wrote -- red."""
    for name, expected_by_job in DOWNSTREAM_NEEDS.items():
        jobs = workflow_jobs(name)
        # Discovery decides the scope: a job added downstream of `detect` and left out of the map
        # is the omission this pins. `waiters` reads no `detect` output, and
        # test_apply_waiters_wiring.py pins its `needs`.
        downstream = {j for j in jobs if j not in (GUARD_JOB, "detect", "review", "waiters")}
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
    gate. `review` runs the dispatch checks before it claims, and writes `admitted` only for the
    run that owns the command, so a `taken` duplicate's successful `review` posts nothing. Gating
    on `needs.detect.result` instead is the opposite regression: it also silences every genuine
    detect failure, so the developer gets no failure comment and no gate refresh. The whole
    expression is pinned so either escape fails.
    Mutations: replace the review clause with `needs.detect.result == 'success'` -> red; drop
    the `admitted` clause -> red."""
    job = workflow_jobs("apply-run.yml")["summary"]
    summary_if = job.get("if")
    assert summary_if == SUMMARY_IF, (
        f"summary's if: must be exactly {SUMMARY_IF!r}, got {summary_if!r}"
    )
    assert "detect" in _needs(job), (
        "summary still reads detect's outputs, so detect must stay in its needs"
    )
