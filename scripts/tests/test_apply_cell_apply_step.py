"""actions/apply-cell's apply step must not let a cosmetic `tee` failure strand an
otherwise-successful apply's check pending, nor let a `tee` success paper over a real apply
failure.

The step pipes `terramate run ... -- tofu apply ... 2>&1 | tee "$RUNNER_TEMP/apply.txt"` under
`set -euo pipefail`. With pipefail live across that pipeline -- or plain errexit reacting to the
pipeline's last-command status -- a `tee` failure alone fails the whole step: a full
$RUNNER_TEMP, a disk hiccup mid-write, nothing to do with tofu. `Save state` has already
persisted the advanced state by the time `Complete the apply check` would run, so a
stranded-pending check here is unrecoverable without a re-plan, the saved plan now being stale
against the advanced state.

The step therefore captures tofu's own exit code via `PIPESTATUS[0]`, with errexit turned off for
that one pipeline, and exits on that captured code explicitly. A `tee` failure can then never
mask a real success, and a real apply failure still fails the step -- the
pending-apply-check-as-work-queue invariant, where a failed apply must still leave its check
pending.
"""

import os
import subprocess

import pytest
from _loader import action_steps, usable_bash

_BASH = usable_bash()


def _apply_step():
    matches = [s for s in action_steps("apply-cell") if s.get("id") == "apply"]
    assert len(matches) == 1, f"expected exactly one apply step (id: apply), got {len(matches)}"
    return matches[0]


def test_apply_step_captures_pipestatus_and_exits_on_it():
    run = _apply_step()["run"]
    assert "PIPESTATUS[0]" in run
    last_line = run.strip().splitlines()[-1].strip()
    assert last_line in ('exit "$status"', "exit $status")


def _run_step(tmp_path, *, terramate_body, tee_body, init_body="return 0"):
    """Execute the real, unmodified step script from action.yml with `terramate` and `tee`
    replaced by bash functions. Bash resolves a function before searching PATH, so this needs no
    fake executables and no exec bits, which are fragile to set up portably on a Windows dev
    box."""
    assert _BASH is not None  # callers are skipif-gated on this; narrows the type too
    run = _apply_step()["run"]
    # The step calls terramate twice: a plain `init` line, then the teed apply. `terramate_body`
    # ends in `exit`, which dies in a subshell inside the pipeline but would kill this whole
    # script on the init line, so the stub dispatches on the tofu subcommand.
    harness = (
        f'terramate() {{ case "$*" in *"tofu apply"*) {terramate_body} ;; '
        f"*) {init_body} ;; esac ; }}\n"
        f"tee() {{ {tee_body} ; }}\n"
    ) + run
    script = tmp_path / "step.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir()
    env = dict(os.environ)
    env["STACK"] = "stacks/app"
    env["RUNNER_TEMP"] = str(runner_temp)
    return subprocess.run([_BASH, str(script)], env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_successful_apply_survives_a_failing_tee(tmp_path):
    # tee copies the output but reports failure, a disk-full write for instance. The apply itself
    # was fine.
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1" ; exit 1',
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_failed_apply_still_fails_the_step_even_if_tee_succeeds(tmp_path):
    # The pending-check invariant: a real apply failure must still fail the step, leaving the
    # apply check pending, regardless of tee's own outcome.
    r = _run_step(
        tmp_path,
        terramate_body="echo boom >&2 ; exit 7",
        tee_body='cat > "$1" ; exit 0',
    )
    assert r.returncode == 7, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_failed_apply_and_failed_tee_still_fails_the_step(tmp_path):
    r = _run_step(
        tmp_path,
        terramate_body="echo boom >&2 ; exit 7",
        tee_body='cat > "$1" ; exit 1',
    )
    assert r.returncode == 7, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_failed_init_fails_the_step_before_the_apply(tmp_path):
    """init runs outside the pipeline, and errexit must stop the step there rather than fall
    through to an apply of a plan against an uninitialized directory.

    The stub uses `return 5`, not `exit 5`: the init line is a plain function call in the current
    shell, so an `exit` body terminates the script whatever the ordering, and this test could
    then not fail on the regression it names -- moving `set +e` above the init line. Returning
    leaves errexit to do the work."""
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='echo TEE_RAN >&2 ; cat > "$1" ; exit 0',
        init_body="echo init boom >&2 ; return 5",
    )
    assert r.returncode == 5, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    # The apply is piped into tee, whose stub swallows that output into apply.txt, so
    # `"applied" not in r.stdout` holds even when the pipeline did run. tee's own stderr escapes
    # the pipe, and tee runs if and only if the pipeline did.
    assert "TEE_RAN" not in r.stderr, f"apply ran despite a failed init: {r.stderr!r}"
