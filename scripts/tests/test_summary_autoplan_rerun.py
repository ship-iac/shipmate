"""The summary re-runs the superseded autoplan's failed plan cells after the gate is written, only
for a successful on-demand plan, and every failure of the decision, the mint or the re-run
degrades to a warning."""

import os

import pytest
from _loader import (
    action_steps,
    bash_only,
    github_outputs,
    read_calls,
    run_step,
    step_by,
    write_stub,
)

DECIDE = "Decide whether to re-run the superseded autoplan"
MINT = "Mint the autoplan re-run token"
POST = "Re-run the superseded autoplan's failed jobs"
SCRIPT = "/ap/../../scripts/rerun-autoplan"
WARNING = (
    "::warning::could not re-run the failed jobs of autoplan run 9; its failed plan checks stay "
    "red until a push plans a new head\n"
)
NOTICE = "::notice title=rerun::re-ran the failed jobs of autoplan run 9, superseded by this plan\n"


def test_the_decision_runs_only_for_a_successful_on_demand_plan_that_wrote_its_gate():
    """Mutations: drop `inputs.plan-result == 'success' &&`; `||` for the first `&&`; give the
    step `GH_TOKEN: ${{ steps.reruntoken.outputs.token }}`; drop `SHIPMATE_HEAD_SHA`."""
    decide = step_by("summary", name=DECIDE)
    assert (decide["id"], decide["if"], decide["env"]) == (
        "rerundecide",
        (
            "${{ inputs.on-demand == 'true' && inputs.plan-result == 'success' && "
            "steps.hold.outputs.superseded != 'true' && steps.upsert.outputs.result != 'moved' }}"
        ),
        {
            "GH_TOKEN": "${{ steps.token.outputs.token }}",
            "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
            "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
            "SHIPMATE_PLANNED_CELLS_NAMES": "${{ inputs.planned-cell-names }}",
        },
    )


def test_only_a_decided_run_mints_an_actions_write_token_and_re_runs():
    """Mutations: add `permission-checks: write` to the mint; drop its `continue-on-error`;
    re-run with `steps.token.outputs.token`."""
    mint = step_by("summary", name=MINT)
    post = step_by("summary", name=POST)
    decided = "${{ steps.rerundecide.outputs.run != '' }}"
    assert mint["uses"].startswith("actions/create-github-app-token@")
    assert (mint["id"], mint["if"], mint["continue-on-error"], mint["with"]) == (
        "reruntoken",
        decided,
        True,
        {
            "client-id": "${{ inputs.app-id }}",
            "private-key": "${{ inputs.private-key }}",
            "owner": "${{ github.repository_owner }}",
            "repositories": "${{ github.event.repository.name }}",
            "permission-actions": "write",
        },
    )
    assert (post["if"], post["env"]) == (
        decided,
        {
            "GH_TOKEN": "${{ steps.reruntoken.outputs.token }}",
            "SHIPMATE_RERUN": "${{ steps.rerundecide.outputs.run }}",
            "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
        },
    )


def test_the_re_run_follows_the_gate_write():
    """The gate write fails closed first; a failed one ends the job before the re-run. Mutation:
    move the three steps before `Create/refresh gate`."""
    names = [s.get("name") for s in action_steps("summary")]
    order = [
        "Create/refresh gate",
        DECIDE,
        MINT,
        POST,
        "Check whether the live head needs a plan",
    ]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)


def _run(tmp_path, step, *, rc=0, stdout="", stderr="", token="x"):  # noqa: S107 - a stub token
    """Run `step`'s `run:` with a `python3` stub that records its argv and prints `stdout`
    and `stderr` before exiting `rc`; return (process, python3 calls, step outputs)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(
        bin_dir,
        "python3",
        'printf \'%s\\n\' "$@" -- >> "$CALLS"\n'
        "printf '%s' \"$FAKE_STDOUT\"\n"
        "printf '%s' \"$FAKE_STDERR\" >&2\n"
        'exit "$FAKE_RC"',
    )
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CALLS": str(tmp_path / "calls"),
        "FAKE_RC": str(rc),
        "FAKE_STDOUT": stdout,
        "FAKE_STDERR": stderr,
        "GITHUB_ACTION_PATH": "/ap",
        "GITHUB_OUTPUT": str(out),
        "GH_TOKEN": token,
        "SHIPMATE_RERUN": "9",
    }
    proc = run_step(tmp_path, step_by("summary", name=step)["run"], env)
    return proc, read_calls(tmp_path / "calls"), github_outputs(out)


@bash_only
@pytest.mark.parametrize(
    ("stdout", "stderr", "outputs"),
    [
        pytest.param("9\n", "", {"run": "9"}, id="run"),
        pytest.param("", "::notice::no autoplan re-run: x\n", {}, id="none"),
    ],
)
def test_the_decision_writes_the_run_it_prints(tmp_path, stdout, stderr, outputs):
    """Mutations: write `run=` with no value -- run; drop the `cat` of the script's stderr --
    none."""
    proc, calls, got = _run(tmp_path, DECIDE, stdout=stdout, stderr=stderr)
    assert (proc.returncode, proc.stdout, proc.stderr, calls, got) == (
        0,
        "",
        stderr,
        [[SCRIPT, "decide"]],
        outputs,
    )


@bash_only
def test_a_failed_decision_warns_and_exits_0(tmp_path):
    """Mutation: `exit 1` for `exit 0` in the failure branch."""
    proc, calls, got = _run(tmp_path, DECIDE, rc=1, stderr="::error::SHIPMATE_HEAD_SHA is bad\n")
    assert (proc.returncode, proc.stdout, proc.stderr, calls, got) == (
        0,
        (
            "::warning::could not decide whether to re-run the superseded autoplan; no re-run "
            "started\n"
        ),
        "::warning::SHIPMATE_HEAD_SHA is bad\n",
        [[SCRIPT, "decide"]],
        {},
    )


@bash_only
def test_a_re_run_posts_once_with_the_decided_run(tmp_path):
    """Mutations: post `"$SHIPMATE_PR_NUMBER"` for `"$SHIPMATE_RERUN"`; drop the `cat` of the
    script's stderr."""
    proc, calls, _ = _run(tmp_path, POST, stderr=NOTICE)
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        0,
        "",
        NOTICE,
        [[SCRIPT, "post", "9"]],
    )


@bash_only
def test_a_failed_re_run_warns_and_exits_0(tmp_path):
    """Mutation: `exit 1` for `exit 0` in `not_rerun`."""
    proc, calls, _ = _run(tmp_path, POST, rc=1, stderr="::error::<id> is bad\n")
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        0,
        WARNING,
        "::warning::<id> is bad\n",
        [[SCRIPT, "post", "9"]],
    )


@bash_only
def test_an_empty_token_warns_and_posts_nothing(tmp_path):
    """A failed mint leaves the token empty. Mutation: delete the empty-token check -- the
    script is called."""
    proc, calls, _ = _run(tmp_path, POST, token="")
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (0, WARNING, "", [])
