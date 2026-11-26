"""`apply-run.yml`'s `lock` job acquires every cell the waves apply, before the first wave, once
`review` and `detect` read the same decision.

Threat model: accidental regression in the job's wiring. A dropped `envlevelN-waves` input leaves
that level's cells applying unlocked; a dropped `environment` leaves the mint with no App key; an
`always()` `if:` acquires after `detect` failed; a compare that goes case-insensitive, accepts
an empty decision or moves after `lock-acquire` locks on a decision that changed. The job is
compared whole against a hand-written constant and its compare step executed;
`DOWNSTREAM_NEEDS` in `test_apply_dispatch_actor_guard.py` pins that `lock` needs `review` and
`detect` and every wave level needs `lock`.
"""

import os

import pytest
from _loader import ENGINE_ENVIRONMENT, action_yaml, bash_only, run_step, workflow_jobs

_MATCH_RUN = (
    "set -euo pipefail\n"
    'if [[ -z "$REVIEW" || -z "$DETECT" || "$REVIEW" != "$DETECT" ]]; then\n'
    "  msg=\"the review decision changed while this run started (review read '$REVIEW', "
    "detect read '$DETECT'); comment \\`shipmate apply\\` on the pull request again\"\n"
    '  echo "refusal=$msg" >> "$GITHUB_OUTPUT"\n'
    '  echo "::error::$msg"\n'
    "  exit 1\n"
    "fi\n"
)

LOCK_JOB = {
    "needs": ["review", "detect"],
    "if": "${{ !failure() && !cancelled() }}",
    "runs-on": "${{ inputs.runs_on }}",
    "environment": ENGINE_ENVIRONMENT,
    "permissions": {},
    "outputs": {
        "refusal": "${{ steps.match.outputs.refusal || steps.l.outputs.refusal }}",
        "refused_by_rule": "${{ steps.l.outputs.refused_by_rule }}",
    },
    "steps": [
        {
            "id": "match",
            "shell": "bash",
            "env": {
                "REVIEW": "${{ needs.review.outputs.decision }}",
                "DETECT": "${{ needs.detect.outputs.decision }}",
            },
            "run": _MATCH_RUN,
        },
        {
            "id": "l",
            "uses": "$/actions/lock-acquire",
            "with": {
                **{
                    f"envlevel{n}-waves": f"${{{{ needs.detect.outputs.envlevel{n}_waves }}}}"
                    for n in range(4)
                },
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        },
    ],
}


def test_the_lock_job_acquires_every_level_with_the_engine_key():
    """`match` is the first step, so a changed decision refuses before any lock is taken.

    Mutations: pass only `envlevel0-waves`; remove `environment`; `if:` to `always()`; add
    an `actions/checkout` step; drop the `refused_by_rule` output; swap `match` and
    `lock-acquire`."""
    assert workflow_jobs("apply-run.yml")["lock"] == LOCK_JOB


def _match(tmp_path, review, detect):
    """Run `lock`'s `match` step; return (exit code, stdout, outputs)."""
    (step,) = [s for s in workflow_jobs("apply-run.yml")["lock"]["steps"] if s.get("id") == "match"]
    out_file = tmp_path / "github_output"
    out_file.write_text("", encoding="utf-8")
    env = {**os.environ, "GITHUB_OUTPUT": str(out_file), "REVIEW": review, "DETECT": detect}
    proc = run_step(tmp_path, step["run"], env)
    return proc.returncode, proc.stdout, out_file.read_text(encoding="utf-8")


@bash_only
def test_one_decision_read_twice_passes(tmp_path):
    """Mutation: `==` for `!=` in the compare."""
    assert _match(tmp_path, "APPROVED", "APPROVED") == (0, "", "")


@bash_only
@pytest.mark.parametrize(
    ("review", "detect"),
    [("APPROVED", "approved"), ("", ""), ("APPROVED", ""), ("NONE", "APPROVED"), ("APPROVED", "*")],
)
def test_two_differing_or_empty_reads_refuse(tmp_path, review, detect):
    """Literal, case-sensitive and non-empty.

    Mutations: drop the empty check -- red on `''`/`''`; compare `${REVIEW,,}` -- red on the
    case pair; unquote the right side -- red on `*`."""
    said = (
        f"the review decision changed while this run started (review read '{review}', detect "
        f"read '{detect}'); comment `shipmate apply` on the pull request again"
    )
    assert _match(tmp_path, review, detect) == (1, f"::error::{said}\n", f"refusal={said}\n")


#: The whole `outputs:` of `actions/lock-acquire/action.yml`, hand-written.
_LOCK_ACQUIRE_OUTPUTS = {
    "refusal": {
        "description": (
            "The first error of the `::error::` message that failed the step (whatever its "
            "cause, a failed API call included), without the prefix, its lines joined into one; "
            "empty when the step raised none. Set although the step fails. Branch-controlled text "
            "(it can name a stack path), so a reader passes it through `env:`."
        ),
        "value": "${{ steps.a.outputs.refusal }}",
    },
    "refused_by_rule": {
        "description": (
            "`true` when the step failed because another pull request or an unknown holder locks "
            "a cell; empty otherwise, a failed API call or a refusal whose rollback failed "
            "included. Set although the step fails."
        ),
        "value": "${{ steps.a.outputs.refused_by_rule }}",
    },
}


def test_the_lock_acquire_action_declares_exactly_these_outputs():
    """Mutations: delete the `refused_by_rule` output; read it from `steps.l` for `steps.a`;
    read `refusal` from `steps.l`."""
    assert action_yaml("lock-acquire")["outputs"] == _LOCK_ACQUIRE_OUTPUTS
