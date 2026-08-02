"""Only a bot actor may dispatch an apply; a human with write access must not
bypass authorize by hand-rolling `gh workflow run apply.yml`/`apply-all.yml`.

The guard is a failing first step inside `detect` (not a job-level `if:`) --
a skipped `detect` reads its outputs as empty strings, which the fan-out jobs'
`!= 'true'` guards read as "not empty" and start anyway. `failure()`
propagating from that step blocks `apply`/`envlevelN` (their `if:` starts with
`!failure()`), but NOT `summary`: `if: always()` runs regardless of every
`needs` conclusion, and `summary`'s steps take `inputs.ref`/`inputs.pr_number`
straight from the dispatcher rather than reading anything from `detect`'s
outputs. So `summary` additionally requires `needs.detect.result == 'success'`
-- without that, an unauthorised dispatch still reaches the job that mints the
App key and posts a PR comment, which is the whole hole this guard exists to
close. See apply.yml/apply-all.yml `detect` and `summary` for the full
rationale.

A guard on `deploy.yml`'s `detect` would break every post-merge deploy, where
`github.actor` is the human who merged -- that workflow must stay guard-free.
"""

import yaml
from _loader import WORKFLOWS

GUARD_EXPR = "!endsWith(github.actor, '[bot]')"
DETECT_SUCCESS_EXPR = "needs.detect.result == 'success'"


def _jobs(name):
    spec = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return spec["jobs"]


def _guard_step(steps):
    return next(
        (s for s in steps if GUARD_EXPR in str(s.get("if", ""))),
        None,
    )


def test_apply_paths_reject_dispatches_missing_a_bot_actor():
    for name in ("apply.yml", "apply-all.yml"):
        step = _guard_step(_jobs(name)["detect"]["steps"])
        assert step is not None, f"{name}: detect has no bot-actor rejection step"
        assert "exit 1" in step.get("run", ""), (
            f"{name}: rejection step must actually fail, not just be conditioned"
        )


def test_deploy_detect_carries_no_bot_actor_guard():
    step = _guard_step(_jobs("deploy.yml")["detect"]["steps"])
    assert step is None, (
        "deploy.yml's detect must not carry the dispatch guard -- it runs on "
        "`push`, where github.actor is the human who merged"
    )


def test_apply_paths_summary_requires_detect_to_have_succeeded():
    # `if: always()` alone runs `summary` regardless of `detect`'s outcome, and
    # `summary`'s steps read raw workflow_call inputs (pr_number, ref) rather
    # than anything gated on `detect` -- so a bare `always()` would let a
    # rejected dispatch still mint the App key and post a PR comment. Pin the
    # `needs.detect.result == 'success'` clause so a future edit back to bare
    # `always()` fails this test.
    for name in ("apply.yml", "apply-all.yml"):
        summary_if = str(_jobs(name)["summary"].get("if", ""))
        assert DETECT_SUCCESS_EXPR in summary_if, (
            f"{name}: summary's if: must require {DETECT_SUCCESS_EXPR}, got {summary_if!r}"
        )
