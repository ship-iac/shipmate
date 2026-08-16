"""Only a bot actor may dispatch an apply; a human with write access must not
bypass authorize by hand-rolling `gh workflow run apply.yml`/`apply-all.yml`.

The rejection is its own tiny job (`guard`), not a first step of `detect`.
Two separate outcomes have to be told apart:

- an **unauthorised dispatch** must not reach `summary`, which mints the App
  key and comments on the dispatcher-supplied pull request number; and
- a **genuine `detect` failure** -- a matrix over the 256-cell cap, an
  env_order cycle, a bad plan run id -- must still reach `summary`, or the
  developer gets no failure comment and no gate refresh at all.

`if: always() && needs.detect.result == 'success'` silenced both. Keying
`summary` on `guard` instead separates them, and every fan-out job carries
`guard` in its `needs` so the rejection reaches its `!failure()` clause: a
rejected dispatch leaves `detect` *skipped*, and a skipped `detect` reads its
outputs as empty strings, which the fan-out jobs' `!= 'true'` guards read as
"not empty" and would start on.

A guard on `deploy.yml`'s `detect` would break every post-merge deploy, where
`github.actor` is the human who merged -- that workflow must stay guard-free.

Every assertion below is on the *whole* parsed expression/string, not a
substring. The substring form this replaces passed against `if: ${{ always()
|| needs.detect.result == 'success' }}` (the hole reopened -- `||` instead of
`&&`) and against a guard step moved to the end of `detect`, after the
dispatcher-controlled ref had already been checked out.
"""

import yaml
from _loader import WORKFLOWS

GUARD_JOB = "guard"
GUARD_IF = "${{ !endsWith(github.actor, '[bot]') }}"
# The rejected-verb differs per workflow ("apply" vs "apply-all"), so the
# error line is keyed by filename rather than a single shared constant.
GUARD_ERROR_LINE = {
    "apply.yml": (
        "::error::apply must be dispatched by the shipmate App via comment-ops, "
        "not by a direct workflow_dispatch"
    ),
    "apply-all.yml": (
        "::error::apply-all must be dispatched by the shipmate App via comment-ops, "
        "not by a direct workflow_dispatch"
    ),
}
#: The whole `needs:` list each apply path's `detect` carries. Both wait on the
#: `review` job that reads the pull request's review decision server-side.
#: Compared whole, so neither `guard` dropping out nor an unreviewed extra
#: dependency can slip in.
DETECT_NEEDS = {"apply.yml": ["guard", "review"], "apply-all.yml": ["guard", "review"]}
#: The whole `needs:` list of every job downstream of `detect`, per file. Every
#: job that can fail BEFORE `detect` has to appear, or its failure never reaches
#: this job's `!failure()`: `detect` is skipped, its outputs read as empty
#: strings, the `!= 'true'` clause reads empty as "not empty", and the job fans
#: out on `fromJSON('')`. `summary` is in the map for the other half of the same
#: property -- its `results:` is `join(needs.*.result, ',')` over its OWN needs,
#: so an omitted pre-`detect` job renders the non-failure comment over a run
#: that died. Compared whole, per job, so neither a pre-`detect` job dropping
#: out nor a new one being omitted passes unnoticed.
DOWNSTREAM_NEEDS = {
    "apply.yml": {
        "apply": ["guard", "review", "detect"],
        "summary": ["guard", "review", "detect", "apply"],
    },
    "apply-all.yml": {
        "envlevel0": ["guard", "review", "detect"],
        "envlevel1": ["guard", "review", "detect", "envlevel0"],
        "envlevel2": ["guard", "review", "detect", "envlevel0", "envlevel1"],
        "envlevel3": ["guard", "review", "detect", "envlevel0", "envlevel1", "envlevel2"],
        "summary": [
            "guard",
            "review",
            "detect",
            "envlevel0",
            "envlevel1",
            "envlevel2",
            "envlevel3",
        ],
    },
}
SUMMARY_IF = "${{ always() && needs.guard.result == 'success' }}"
APPLY_PATHS = ("apply.yml", "apply-all.yml")


def _jobs(name):
    spec = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return spec["jobs"]


def _needs(job):
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def test_apply_paths_reject_dispatches_missing_a_bot_actor():
    # The rejection must be the guard job's one and only step, so nothing at
    # all can run in that job before it -- the reason it is not a step of
    # `detect`, where `actions/checkout` of the dispatcher-supplied ref sits.
    for name in APPLY_PATHS:
        job = _jobs(name)[GUARD_JOB]
        steps = job["steps"]
        assert len(steps) == 1, f"{name}: {GUARD_JOB} must carry exactly one step, got {len(steps)}"
        step = steps[0]
        assert step.get("if") == GUARD_IF, (
            f"{name}: the guard step must have if: {GUARD_IF!r}, got {step.get('if')!r}"
        )
        run = step.get("run", "")
        assert run.lstrip().startswith(f'echo "{GUARD_ERROR_LINE[name]}"'), (
            f"{name}: guard step's run must begin with the ::error:: line, got {run!r}"
        )
        assert run.rstrip().endswith("exit 1"), (
            f"{name}: guard step must actually fail, not just be conditioned: {run!r}"
        )


def test_guard_job_holds_no_credentials_and_runs_no_repository_code():
    # It gates the App key; it must not be able to reach one, name the
    # environment that holds one, or execute the dispatcher's ref.
    for name in APPLY_PATHS:
        job = _jobs(name)[GUARD_JOB]
        assert job.get("permissions") == {}, (
            f"{name}: {GUARD_JOB} must grant no token permissions, got {job.get('permissions')!r}"
        )
        assert job.get("environment") is None, f"{name}: {GUARD_JOB} must name no environment"
        assert job.get("secrets") is None, f"{name}: {GUARD_JOB} must be passed no secrets"
        for step in job["steps"]:
            assert "checkout" not in str(step.get("uses", "")).lower()
            assert "secrets." not in str(step.get("run", ""))


def test_every_apply_fan_out_job_carries_the_guard_in_its_needs():
    # A rejected dispatch leaves `detect` skipped, and a skipped `detect`'s
    # outputs are empty strings -- which `needs.detect.outputs.*_empty !=
    # 'true'` reads as "not empty". `guard` in `needs` is what makes the
    # rejection visible to each job's `!failure()` clause.
    for name in APPLY_PATHS:
        jobs = _jobs(name)
        assert _needs(jobs["detect"]) == DETECT_NEEDS[name], (
            f"{name}: detect must need exactly {DETECT_NEEDS[name]!r}, "
            f"got {_needs(jobs['detect'])!r}"
        )
        for job_id, job in jobs.items():
            if job_id in (GUARD_JOB, "detect"):
                continue
            assert GUARD_JOB in _needs(job), (
                f"{name}: job {job_id!r} must carry {GUARD_JOB!r} in its needs, got {_needs(job)!r}"
            )


def test_every_apply_fan_out_job_needs_every_job_that_runs_before_detect():
    for name, expected_by_job in DOWNSTREAM_NEEDS.items():
        jobs = _jobs(name)
        # Discovery decides the scope: a job added downstream of `detect` and
        # left out of the map is exactly the omission this pins.
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
    jobs = _jobs("deploy.yml")
    assert GUARD_JOB not in jobs, (
        "deploy.yml must not carry the dispatch guard -- it runs on `push`, "
        "where github.actor is the human who merged"
    )
    first_step = jobs["detect"]["steps"][0]
    assert first_step.get("if") != GUARD_IF, (
        "deploy.yml's detect must not carry the dispatch guard -- it runs on "
        "`push`, where github.actor is the human who merged"
    )


def test_apply_paths_summary_is_gated_on_the_guard_and_not_on_detect():
    # `if: always()` alone runs `summary` regardless of every `needs`
    # conclusion, and `summary`'s steps read raw workflow_call inputs
    # (pr_number, ref) rather than anything gated on `detect` -- so a bare
    # `always()`, or `always() || needs.guard.result == 'success'` (still
    # `true` unconditionally), would let a rejected dispatch mint the App key
    # and post a pull request comment. Gating on `needs.detect.result` instead
    # is the opposite regression: it also silences every genuine detect
    # failure, so the developer gets no failure comment and no gate refresh.
    # Pin the exact whole expression so either escape fails this test.
    for name in APPLY_PATHS:
        job = _jobs(name)["summary"]
        summary_if = job.get("if")
        assert summary_if == SUMMARY_IF, (
            f"{name}: summary's if: must be exactly {SUMMARY_IF!r}, got {summary_if!r}"
        )
        assert "detect" in _needs(job), (
            f"{name}: summary still reads detect's outputs, so detect must stay in its needs"
        )
