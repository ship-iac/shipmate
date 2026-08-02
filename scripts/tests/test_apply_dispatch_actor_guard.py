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

Every assertion below is on the *whole* parsed expression/string, not a
substring -- the whole point of this rewrite. The substring form this replaces
passed against `if: ${{ always() || needs.detect.result == 'success' }}` (the
hole reopened -- `||` instead of `&&`) and against a guard step moved to the
end of `detect`, after the dispatcher-controlled ref had already been checked
out; both are exactly the shape a future edit could reintroduce.
"""

import yaml
from _loader import WORKFLOWS

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
SUMMARY_IF = "${{ always() && needs.detect.result == 'success' }}"


def _jobs(name):
    spec = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return spec["jobs"]


def test_apply_paths_reject_dispatches_missing_a_bot_actor():
    # The guard must be `detect`'s literal FIRST step: `apply-detect` (and, in
    # apply-all.yml, nothing else) runs after it, and a later position would
    # let steps earlier in the job act on the dispatcher-controlled ref before
    # the rejection has a chance to fire.
    for name in ("apply.yml", "apply-all.yml"):
        step = _jobs(name)["detect"]["steps"][0]
        assert step.get("if") == GUARD_IF, (
            f"{name}: detect's first step must have if: {GUARD_IF!r}, got {step.get('if')!r}"
        )
        run = step.get("run", "")
        assert run.lstrip().startswith(f'echo "{GUARD_ERROR_LINE[name]}"'), (
            f"{name}: guard step's run must begin with the ::error:: line, got {run!r}"
        )
        assert run.rstrip().endswith("exit 1"), (
            f"{name}: guard step must actually fail, not just be conditioned: {run!r}"
        )


def test_deploy_detect_carries_no_bot_actor_guard():
    first_step = _jobs("deploy.yml")["detect"]["steps"][0]
    assert first_step.get("if") != GUARD_IF, (
        "deploy.yml's detect must not carry the dispatch guard -- it runs on "
        "`push`, where github.actor is the human who merged"
    )


def test_apply_paths_summary_requires_detect_to_have_succeeded():
    # `if: always()` alone runs `summary` regardless of `detect`'s outcome, and
    # `summary`'s steps read raw workflow_call inputs (pr_number, ref) rather
    # than anything gated on `detect` -- so a bare `always()`, or `always() ||
    # needs.detect.result == 'success'` (still `true` unconditionally), would
    # let a rejected dispatch still mint the App key and post a PR comment.
    # Pin the exact whole expression so either escape fails this test.
    for name in ("apply.yml", "apply-all.yml"):
        summary_if = _jobs(name)["summary"].get("if")
        assert summary_if == SUMMARY_IF, (
            f"{name}: summary's if: must be exactly {SUMMARY_IF!r}, got {summary_if!r}"
        )
