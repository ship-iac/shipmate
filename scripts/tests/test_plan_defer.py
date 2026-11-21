"""`actions/plan-defer`: a ready-for-review run asks `gate-status published` whether an earlier
autoplan run planned and published its head, and outputs that run's id or nothing.

Every failure plans: the action decides only whether to repeat a plan, so a failed verify, mint
or read leaves `run` empty and the step passes. The bash step runs for real with a `python3`
stub standing in for `gate-status`; stdout and `GITHUB_OUTPUT` are asserted separately.
"""

import os

from _loader import ACTIONS, action_steps, bash_only, run_step, step_by, write_stub

HEAD_SHA = "a" * 40
VERIFY = "Verify the App key arrived"
MINT = "Mint App installation token"
STEP = "Read the earlier plan run"
NOTICE = (
    f"::notice::plan run 12345 planned and published {HEAD_SHA}; this ready-for-review run "
    "plans nothing\n"
)
CAUSE = "jobs listing of run 7 is incomplete"
READ_WARNING = (
    f"::warning::could not read whether an earlier plan run published {HEAD_SHA}: {CAUSE}; "
    "this run plans\n"
)
TOKEN_WARNING = (
    f"::warning::no App token to read whether an earlier plan run published {HEAD_SHA}; "
    "this run plans\n"
)
GATE_STATUS = f"{ACTIONS / 'plan-defer'}/../../scripts/gate-status"


def test_the_read_step_takes_exactly_this_env():
    """Mutation: drop `SHIPMATE_APP_SLUG` -> `gate-status` cannot name the App's plan comment."""
    assert step_by("plan-defer", name=STEP)["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_APP_SLUG": "${{ steps.token.outputs.app-slug }}",
        "HEAD_SHA": "${{ inputs.head-sha }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
    }


def test_the_mint_requests_exactly_these_permissions():
    """Mutation: drop `permission-pull-requests` -> the comment listing is refused."""
    assert step_by("plan-defer", name=MINT)["with"] == {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-actions": "read",
        "permission-statuses": "read",
        "permission-pull-requests": "read",
    }


def test_only_the_verify_and_mint_steps_continue_on_error():
    """A failed verify or mint must reach the read step as an empty token, which plans.
    Mutation: remove `continue-on-error` from the mint step."""
    degrading = [s.get("name") for s in action_steps("plan-defer") if s.get("continue-on-error")]
    assert degrading == [VERIFY, MINT]


def _run(tmp_path, *, minted=True, rc=0, out="", err=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    write_stub(
        bin_dir,
        "python3",
        'printf \'%s\\n\' "$@" >> "$CALLS"\n'
        "printf '%s' \"$STUB_OUT\"\n"
        "printf '%s' \"$STUB_ERR\" >&2\n"
        'exit "$STUB_RC"',
    )
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GITHUB_ACTION_PATH": str(ACTIONS / "plan-defer"),
        "GITHUB_OUTPUT": str(output),
        "GH_TOKEN": "t" if minted else "",
        "SHIPMATE_APP_SLUG": "shipmate-test",
        "HEAD_SHA": HEAD_SHA,
        "PR_NUMBER": "42",
        "CALLS": str(calls),
        "STUB_RC": str(rc),
        "STUB_OUT": out,
        "STUB_ERR": err,
    }
    proc = run_step(tmp_path, step_by("plan-defer", name=STEP)["run"], env)
    read = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else None
    return proc, output.read_text(encoding="utf-8"), read


@bash_only
def test_a_published_head_outputs_the_earlier_run(tmp_path):
    """The retry notice on stderr stays in the log and out of `run`. Mutation: write `run` from
    stderr (`2>&1` on the call) -> `run` carries the notice."""
    notice = "::notice::command failed (1): gh api x%0Agh: Bad Gateway (HTTP 502)\n"
    proc, output, calls = _run(tmp_path, out="12345\n", err=notice)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, NOTICE, notice)
    assert output == "run=12345\n"
    assert calls == [GATE_STATUS, "published", HEAD_SHA, "42"]


@bash_only
def test_no_earlier_run_outputs_nothing(tmp_path):
    proc, output, calls = _run(tmp_path)
    assert (proc.returncode, proc.stdout, proc.stderr, output) == (0, "", "", "")
    assert calls == [GATE_STATUS, "published", HEAD_SHA, "42"]


@bash_only
def test_a_failed_read_warns_and_plans(tmp_path):
    """Mutation: `|| exit 1` on the call -> the step fails."""
    proc, output, _ = _run(tmp_path, rc=1, err=f"{CAUSE}\n")
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, READ_WARNING, f"{CAUSE}\n")
    assert output == ""


@bash_only
def test_an_empty_token_warns_plans_and_reads_nothing(tmp_path):
    """The verify or the mint failed. Mutation: drop the empty-token branch -> the stub is
    called."""
    proc, output, calls = _run(tmp_path, minted=False, out="12345\n")
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, TOKEN_WARNING, "")
    assert (output, calls) == ("", None)
