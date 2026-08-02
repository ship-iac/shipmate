"""Only a bot actor may dispatch an apply; a human with write access must not
bypass authorize by hand-rolling `gh workflow run apply.yml`/`apply-all.yml`.

The guard is a failing first step inside `detect` (not a job-level `if:`) --
a skipped `detect` reads its outputs as empty strings, which the fan-out jobs'
`!= 'true'` guards read as "not empty" and start anyway, and `summary`
(`if: always()`) always runs regardless. Only `failure()` propagating from a
real step failure blocks every downstream job. See apply.yml/apply-all.yml
`detect` for the full rationale.

A guard on `deploy.yml`'s `detect` would break every post-merge deploy, where
`github.actor` is the human who merged -- that workflow must stay guard-free.
"""

import yaml
from _loader import WORKFLOWS

GUARD_EXPR = "!endsWith(github.actor, '[bot]')"


def _detect_steps(name):
    spec = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return spec["jobs"]["detect"]["steps"]


def _guard_step(steps):
    return next(
        (s for s in steps if GUARD_EXPR in str(s.get("if", ""))),
        None,
    )


def test_apply_paths_reject_dispatches_missing_a_bot_actor():
    for name in ("apply.yml", "apply-all.yml"):
        step = _guard_step(_detect_steps(name))
        assert step is not None, f"{name}: detect has no bot-actor rejection step"
        assert "exit 1" in step.get("run", ""), (
            f"{name}: rejection step must actually fail, not just be conditioned"
        )


def test_deploy_detect_carries_no_bot_actor_guard():
    step = _guard_step(_detect_steps("deploy.yml"))
    assert step is None, (
        "deploy.yml's detect must not carry the dispatch guard -- it runs on "
        "`push`, where github.actor is the human who merged"
    )
