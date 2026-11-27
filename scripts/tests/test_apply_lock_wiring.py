"""`apply-run.yml`'s `lock` job acquires every cell the waves apply, before the first wave, once
`review` and `detect` read the same decision, then verifies the first populated env-level's
environments, snapshots its apply checks and releases its wave0; that level's call takes the
snapshot as `by_wave`. `unflip` returns that wave0 to `queued` when the level's call never
started.

Threat model: accidental regression in the job's wiring. A dropped `envlevelN-waves` input leaves
that level's cells applying unlocked; a dropped `environment` leaves the mint with no App key; an
`always()` `if:` acquires after `detect` failed; a compare that goes case-insensitive, accepts
an empty decision or moves after `lock-acquire` locks on a decision that changed. A release
before `lock-acquire` flips checks of cells another pull request holds. A pre-flight after the
snapshot, or a pre-flight, snapshot or release reading another level's waves than the level call
uses, flips checks no wave applies. A widened token reaches more than the three actions need. A
`by_wave` reaching a non-first level skips that level's own snapshot. An `unflip` that writes
over a recorded `lock` refusal, runs on the happy path or on a cancelled call, keys on `lock`'s
success, or re-snapshots when `lock` delivered `by_wave`, writes checks it does not own. The
jobs are compared whole against hand-written constants and the compare
step executed; `DOWNSTREAM_NEEDS` in `test_apply_dispatch_actor_guard.py` pins that `lock` needs
`review` and `detect`, every wave level needs `lock`, and `summary` needs `unflip`;
`test_matrix_max_parallel_guard.py` pins each level call's whole `with`, `by_wave` included.
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

#: The first populated level's waves: a four-way on `first_level`, hand-written.
FIRST_WAVES = (
    "${{ needs.detect.outputs.first_level == '0' && needs.detect.outputs.envlevel0_waves || "
    "needs.detect.outputs.first_level == '1' && needs.detect.outputs.envlevel1_waves || "
    "needs.detect.outputs.first_level == '2' && needs.detect.outputs.envlevel2_waves || "
    "needs.detect.outputs.first_level == '3' && needs.detect.outputs.envlevel3_waves || '' }}"
)
FIRST_LEVEL_SET = "${{ needs.detect.outputs.first_level != '' }}"
APP_KEY = {
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}

LOCK_JOB = {
    "needs": ["review", "detect"],
    "if": "${{ !failure() && !cancelled() && needs.review.outputs.admitted == 'true' }}",
    "runs-on": "${{ inputs.runs_on }}",
    "environment": ENGINE_ENVIRONMENT,
    "permissions": {"actions": "read", "checks": "read"},
    "outputs": {
        "refusal": (
            "${{ steps.match.outputs.refusal || steps.l.outputs.refusal || "
            "steps.verify.outputs.refusal || steps.snap.outputs.refusal }}"
        ),
        "refused_by_rule": "${{ steps.l.outputs.refused_by_rule }}",
        "by_wave": "${{ steps.snap.outputs.by_wave }}",
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
                **APP_KEY,
            },
        },
        {
            "id": "verify",
            "if": FIRST_LEVEL_SET,
            "uses": "$/actions/verify-environments",
            "with": {"waves-json": FIRST_WAVES},
        },
        {
            "id": "snap",
            "if": FIRST_LEVEL_SET,
            "uses": "$/actions/apply-snapshot",
            "with": {
                "head-sha": "${{ needs.detect.outputs.head_sha }}",
                "waves-json": FIRST_WAVES,
                **APP_KEY,
            },
        },
        {
            "if": FIRST_LEVEL_SET,
            "uses": "$/actions/apply-release",
            "with": {
                "snapshot": "${{ toJSON(fromJSON(steps.snap.outputs.by_wave).wave0) }}",
                "deploy": False,
                "pr-number": "${{ inputs.pr_number }}",
                **APP_KEY,
            },
        },
    ],
}


def test_the_lock_job_acquires_every_level_with_the_engine_key():
    """`match` is the first step, so a changed decision refuses before any lock is taken; the
    first level's pre-flight, snapshot and release follow `lock-acquire`, in that order.

    Mutations: pass only `envlevel0-waves`; remove `environment`; `if:` to `always()`; add
    an `actions/checkout` step; drop the `refused_by_rule` output; swap `match` and
    `lock-acquire`; move the release before `l`; move `verify` after `snap`; key the
    snapshot's four-way on `envlevel1_empty`; `permissions: { actions: read, checks: write }`;
    drop `steps.verify.outputs.refusal` from `refusal`; drop the `admitted` clause from `if:`, so
    a `taken` run locks."""
    assert workflow_jobs("apply-run.yml")["lock"] == LOCK_JOB


#: `unflip`'s whole `if:`, hand-written: the first level's call skipped, `lock` ran, and no
#: refusal recorded before `lock` flipped anything.
UNFLIP_IF = (
    "${{ always() && needs.detect.outputs.first_level != '' && "
    "(needs.detect.outputs.first_level == '0' && needs.envlevel0.result || "
    "needs.detect.outputs.first_level == '1' && needs.envlevel1.result || "
    "needs.detect.outputs.first_level == '2' && needs.envlevel2.result || "
    "needs.detect.outputs.first_level == '3' && needs.envlevel3.result || '') == 'skipped' && "
    "needs.lock.result != 'skipped' && needs.lock.outputs.refusal == '' }}"
)

UNFLIP_JOB = {
    "needs": ["review", "detect", "lock", "envlevel0", "envlevel1", "envlevel2", "envlevel3"],
    "if": UNFLIP_IF,
    "runs-on": "ubuntu-slim",
    "environment": ENGINE_ENVIRONMENT,
    "permissions": {"actions": "read", "checks": "read"},
    "steps": [
        {
            "id": "resnap",
            "if": "${{ needs.lock.outputs.by_wave == '' }}",
            "uses": "$/actions/apply-snapshot",
            "with": {
                "head-sha": "${{ needs.detect.outputs.head_sha }}",
                "waves-json": FIRST_WAVES,
                **APP_KEY,
                "counted": "true",
            },
        },
        {
            "uses": "$/actions/apply-complete",
            "with": {
                "by-wave": "${{ needs.lock.outputs.by_wave || steps.resnap.outputs.by_wave }}",
                "wave-results": '{"wave0": "skipped"}',
                "job-id": "${{ job.check_run_id }}",
                "deploy": False,
                "pr-number": "${{ inputs.pr_number }}",
                **APP_KEY,
            },
        },
    ],
}


def test_unflip_returns_the_released_wave0_only_when_the_first_levels_call_never_started():
    """Mutations: remove the parentheses around the four-way; drop
    `&& needs.lock.outputs.refusal == ''`; `== 'skipped'` to `!= 'success'`; `always()` to
    `!cancelled()`; `wave-results` `"cancelled"`; drop `needs.lock.outputs.by_wave ||` from
    `by-wave`; drop `counted: "true"` from `resnap`."""
    assert workflow_jobs("apply-run.yml")["unflip"] == UNFLIP_JOB


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
