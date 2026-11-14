"""`actions/outcome-reply` answers a command whose run ended without its result, and the apply and
plan `outcome` jobs call it only then.

The reply step picks its text from inputs only: in a job that starts after a run cancel, a
step-level `cancelled()` is false, so the apply job passes the cancel in as `run-cancelled`. The
apply `outcome` reads `apply-run.yml`'s outputs, which all read empty when the run was cancelled
while it waited in the queue, so its `run-cancelled` also reads a call that ended before
`review` reported as a cancel.
"""

import os
import re

import pytest
from _loader import (
    ACTIONS,
    ENGINE_ENVIRONMENT,
    action_steps,
    action_yaml,
    bash_only,
    read_calls,
    run_step,
    step_by,
    strings,
    workflow_jobs,
    write_python3_shim,
)

ACTION = "outcome-reply"
REPLY = "Reply that the run posted no result"

URL = "https://example.invalid/acme/demo/actions/runs/999"
HINT = "Comment `shipmate help` for the available commands."
TOKEN = "minted-token"  # noqa: S105 - a stub token, never sent

#: (inputs, whole body) for each precedence case: refused, run cancelled, summary
#: cancelled, ended. The apply case with empty outputs is what `outcome` passes after a call
#: that ended before `review` reported: cancelled while queued, or skipped by a run cancel.
SELECTIONS = {
    "refused": (
        {
            "verb": "apply",
            "environment": "dev-eu",
            "review-result": "failure",
            "summary-result": "skipped",
            "run-cancelled": "false",
        },
        (
            "### shipmate apply dev-eu\n\n"
            f"🔴 refused: its review check stopped [the apply run]({URL}) before any cell applied; "
            "the reason is in that run's log.\n\n"
            f"[comment run]({URL}). {HINT}"
        ),
    ),
    "refused over a cancelled run": (
        {
            "verb": "apply",
            "environment": "dev-eu",
            "review-result": "failure",
            "summary-result": "cancelled",
            "run-cancelled": "true",
        },
        (
            "### shipmate apply dev-eu\n\n"
            f"🔴 refused: its review check stopped [the apply run]({URL}) before any cell applied; "
            "the reason is in that run's log.\n\n"
            f"[comment run]({URL}). {HINT}"
        ),
    ),
    "run cancelled over a cancelled summary": (
        {
            "verb": "apply",
            "environment": "dev-eu",
            "review-result": "success",
            "summary-result": "cancelled",
            "run-cancelled": "true",
        },
        (
            "### shipmate apply dev-eu\n\n"
            f"🔴 failed: [the apply run]({URL}) was cancelled before it posted a result.\n\n"
            f"[comment run]({URL}). {HINT}"
        ),
    ),
    "apply summary cancelled": (
        {
            "verb": "apply",
            "environment": "dev-eu",
            "review-result": "success",
            "summary-result": "cancelled",
            "run-cancelled": "false",
        },
        (
            "### shipmate apply dev-eu\n\n"
            f"🔴 failed: [the apply run]({URL}) posted no result: its summary was cancelled "
            "before it posted a result. Its cells' results are on their apply checks.\n\n"
            f"[comment run]({URL}). {HINT}"
        ),
    ),
    "apply call ended before review": (
        {
            "verb": "apply",
            "environment": "dev-eu",
            "review-result": "",
            "summary-result": "",
            "run-cancelled": "true",
        },
        (
            "### shipmate apply dev-eu\n\n"
            f"🔴 failed: [the apply run]({URL}) was cancelled before it posted a result.\n\n"
            f"[comment run]({URL}). {HINT}"
        ),
    ),
    "plan summary cancelled": (
        {"verb": "plan", "summary-result": "cancelled", "run-cancelled": "false"},
        (
            "### shipmate plan\n\n"
            f"🔴 failed: [the plan run]({URL}) posted no result: its summary was cancelled "
            "before it posted a result.\n\n"
            f"[comment run]({URL}). {HINT}"
        ),
    ),
    "apply ended": (
        {
            "verb": "apply",
            "environment": "dev-eu",
            "review-result": "success",
            "summary-result": "failure",
            "run-cancelled": "false",
        },
        (
            "### shipmate apply dev-eu\n\n"
            f"🔴 failed: [the apply run]({URL}) ended before it posted a result; the reason is in "
            "that run's log.\n\n"
            f"[comment run]({URL}). {HINT}"
        ),
    ),
    "plan ended": (
        {"verb": "plan", "summary-result": "success", "run-cancelled": "false"},
        (
            "### shipmate plan\n\n"
            f"🔴 failed: [the plan run]({URL}) ended before it posted a result; the reason is in "
            "that run's log.\n\n"
            f"[comment run]({URL}). {HINT}"
        ),
    ),
}

_GH = """\
#!/bin/bash
printf '%s\\n' "$@" -- >> "$CALLS"
cat > "$REPLY_BODY"
printf '%s' "$GH_TOKEN" > "$REPLY_BODY.token"
[ "${FAKE_RC:-0}" = 0 ] || echo "HTTP 500" >&2
exit "${FAKE_RC:-0}"
"""


def _render(value, inputs):
    """A step `env` value with `${{ inputs.<name> }}` and the mint's token substituted."""
    value = value.replace("${{ steps.token.outputs.token }}", TOKEN)
    value = re.sub(r"\$\{\{ inputs\.([a-z-]+) \}\}", lambda m: inputs.get(m[1], ""), value)
    assert "${{" not in value, value
    return value


def _run(tmp_path, inputs, *, rc=0):
    """Run the reply step with its env rendered for `inputs`; return (process, gh calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_python3_shim(bin_dir)
    (bin_dir / "gh").write_text(_GH, encoding="utf-8", newline="\n")
    (bin_dir / "gh").chmod(0o755)
    step = step_by(ACTION, name=REPLY)
    inputs = {"pr-number": "42", **inputs}
    env = {
        **os.environ,
        **{k: _render(v, inputs) for k, v in step["env"].items()},
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CALLS": str(tmp_path / "calls"),
        "REPLY_BODY": str(tmp_path / "reply.md"),
        "FAKE_RC": str(rc),
        "GITHUB_ACTION_PATH": str(ACTIONS / ACTION),
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_SERVER_URL": "https://example.invalid",
        "GITHUB_RUN_ID": "999",
    }
    proc = run_step(tmp_path, step["run"], env)
    return proc, read_calls(tmp_path / "calls")


@bash_only
@pytest.mark.parametrize("case", list(SELECTIONS))
def test_each_case_posts_its_whole_body(tmp_path, case):
    """Mutations: swap the run-cancelled and summary-cancelled texts in the `elif` chain; swap the
    run-cancelled and summary-cancelled conditions; drop the run-cancelled branch (a cancelled run
    whose summary was cancelled reads as a summary cancel); drop the `verb` check from the
    summary-cancelled text; post with `github.token` in place of the minted token; drop the
    run-cancelled branch (the call that ended before review reads as ended)."""
    inputs, body = SELECTIONS[case]
    proc, calls = _run(tmp_path, inputs)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == [["api", "-X", "POST", "repos/acme/demo/issues/42/comments", "-F", "body=@-"]]
    assert (tmp_path / "reply.md").read_text(encoding="utf-8") == body
    assert (tmp_path / "reply.md.token").read_text(encoding="utf-8") == TOKEN


def test_the_steps_run_in_order():
    """Mutations: give the reply step an `if:`; add a second reply step."""
    assert [s.get("name") for s in action_steps(ACTION)] == [
        "Verify the App key arrived",
        "Mint App installation token",
        REPLY,
    ]
    assert "if" not in step_by(ACTION, name=REPLY)


@bash_only
def test_a_failed_post_warns_once_and_exits_zero(tmp_path):
    """Mutation: drop the `||` degrade (the step exits 1)."""
    proc, _ = _run(tmp_path, SELECTIONS["apply ended"][0], rc=1)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.splitlines().count("::warning::could not post the no-result reply") == 1


def test_the_composite_mints_only_a_pull_request_write_token():
    """Mutations: add `permission-issues: write` to the mint; give the mint or the verify step an
    `if:`; set the reply step's `GH_TOKEN: ${{ github.token }}`."""
    verify = step_by(ACTION, name="Verify the App key arrived")
    assert verify == {
        "name": "Verify the App key arrived",
        "uses": "$/actions/verify-app-key",
        "with": {"app-id": "${{ inputs.app-id }}", "private-key": "${{ inputs.private-key }}"},
    }
    mint = step_by(ACTION, name="Mint App installation token")
    assert "if" not in mint
    assert mint["id"] == "token"
    assert mint["uses"].startswith("actions/create-github-app-token@")
    assert mint["with"] == {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-pull-requests": "write",
    }
    assert step_by(ACTION, name=REPLY)["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        "SHIPMATE_REPLY_VERB": "${{ inputs.verb }}",
        "SHIPMATE_REPLY_ENV": "${{ inputs.environment }}",
        "REVIEW_RESULT": "${{ inputs.review-result }}",
        "RUN_CANCELLED": "${{ inputs.run-cancelled }}",
        "SUMMARY_RESULT": "${{ inputs.summary-result }}",
    }
    assert not [s for s in strings(action_yaml(ACTION)) if "github.token" in s]


def test_the_apply_outcome_job_is_wired_exactly():
    """`always()`: it must run after a run cancel, including one while the call waited in
    `apply-run.yml`'s queue. Every fact about the inner run arrives through `run`'s outputs, which
    read empty when they did not arrive; `guard` refuses an empty `pr_number`. `run-cancelled`
    also reads a call that did not succeed and whose `review_result` is empty as a cancel: the
    call was cancelled while queued, or skipped by a run cancel.

    A failed `review` replies unless it found no open pull request, and never in an `auto` run.

    Mutations: drop `&& needs.run.outputs.posted != 'true'`; drop
    `needs.run.outputs.unbound != 'true' &&`; flip its `!=` to `==`; drop `&& !inputs.auto`;
    drop `run` from `needs`; `permissions: { pull-requests: write }`; pass
    `needs.run.outputs.review_result` as `summary-result`; replace `always()` with
    `!cancelled()`; drop `needs.run.result != 'success' &&` from `run-cancelled` (a queued
    cancel and a success read alike); drop its `review_result == ''` clause (a failed review
    reads as cancelled); drop its `run_cancelled == 'true' ||` clause (a mid-run cancel reads
    as ended)."""
    assert workflow_jobs("apply.yml")["outcome"] == {
        "needs": ["guard", "run"],
        "if": (
            "${{ always() && needs.guard.result == 'success' && "
            "needs.run.outputs.posted != 'true' && "
            "(needs.run.outputs.review_result != 'failure' || "
            "(needs.run.outputs.unbound != 'true' && !inputs.auto)) }}"
        ),
        "runs-on": "ubuntu-slim",
        "environment": ENGINE_ENVIRONMENT,
        "permissions": {},
        "steps": [
            {
                "uses": "$/actions/outcome-reply",
                "with": {
                    "verb": "apply",
                    "environment": "${{ inputs.environment }}",
                    "pr-number": "${{ inputs.pr_number }}",
                    "review-result": "${{ needs.run.outputs.review_result }}",
                    "summary-result": "${{ needs.run.outputs.summary_result }}",
                    "run-cancelled": (
                        "${{ needs.run.outputs.run_cancelled == 'true' || "
                        "(needs.run.result != 'success' && "
                        "needs.run.outputs.review_result == '') }}"
                    ),
                    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
                },
            }
        ],
    }


def test_the_plan_outcome_job_is_wired_exactly():
    """`!cancelled()`: a push's autoplan cancels a dispatched plan and answers it.

    Mutations: drop the `superseded` clause; drop the `moved` clause; replace `!cancelled()` with
    `always()`; drop the fork clause's `head-repo == github.repository`; drop `summary-result`
    from `with:`; pass `run-cancelled: 'true'`; add `plan` to `needs`."""
    assert workflow_jobs("plan.yml")["outcome"] == {
        "needs": ["facts", "summary"],
        "if": (
            "${{ !cancelled() && needs.facts.outputs.on-demand == 'true' && "
            "needs.facts.outputs.head-repo != '' && "
            "needs.facts.outputs.head-repo == github.repository && "
            "needs.summary.outputs.superseded != 'true' && "
            "needs.summary.outputs.upsert != 'created' && "
            "needs.summary.outputs.upsert != 'moved' }}"
        ),
        "runs-on": "ubuntu-slim",
        "environment": ENGINE_ENVIRONMENT,
        "permissions": {},
        "steps": [
            {
                "uses": "$/actions/outcome-reply",
                "with": {
                    "verb": "plan",
                    "pr-number": "${{ needs.facts.outputs.pr-number }}",
                    "summary-result": "${{ needs.summary.result }}",
                    "run-cancelled": "false",
                    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
                },
            }
        ],
    }
