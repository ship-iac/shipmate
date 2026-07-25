"""actions/apply-cell's apply step must not let a cosmetic `tee` failure
strand an otherwise-successful apply's check pending -- nor let a `tee`
success paper over a real apply failure.

The step pipes `terramate script run ... apply 2>&1 | tee "$RUNNER_TEMP/apply.txt"`
under `set -euo pipefail`. With pipefail (or plain errexit reacting to the
pipeline's last-command status) live across that pipeline, a `tee` failure
alone -- a full $RUNNER_TEMP, a disk hiccup mid-write, nothing to do with
tofu -- fails the whole step. `Save state` has already persisted the
advanced state by the time `Complete the apply check` would run, so a
stranded-pending check here is unrecoverable without a re-plan (the saved
plan is now stale against the advanced state). The fix captures tofu's own
exit code via `PIPESTATUS[0]` (with errexit turned off just for that one
pipeline) and exits the step on that captured code explicitly -- so a
`tee` failure can never mask a real success, and, just as importantly, a
real apply failure still fails the step (the pending-apply-check-as-work-queue
invariant: a failed apply must still leave its check pending).
"""

import os
import pathlib
import shutil
import subprocess

import pytest
import yaml

_ACTIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "actions"
_ACTION_PATH = _ACTIONS_DIR / "apply-cell" / "action.yml"

_BASH = shutil.which("bash")


def _apply_step():
    spec = yaml.safe_load(_ACTION_PATH.read_text(encoding="utf-8"))
    steps = (spec.get("runs") or {}).get("steps") or []
    matches = [s for s in steps if s.get("id") == "apply"]
    assert len(matches) == 1, f"expected exactly one apply step (id: apply), got {len(matches)}"
    return matches[0]


def test_apply_step_captures_pipestatus_and_exits_on_it():
    run = _apply_step()["run"]
    assert "PIPESTATUS[0]" in run
    last_line = run.strip().splitlines()[-1].strip()
    assert last_line in ('exit "$status"', "exit $status")


def _run_step(tmp_path, *, terramate_body, tee_body):
    """Execute the real, unmodified step script from action.yml with
    `terramate` and `tee` replaced by bash functions -- bash resolves a
    function before searching PATH, so this needs no fake executables or
    exec bits (fragile to set up portably on a Windows dev box)."""
    assert _BASH is not None  # callers are skipif-gated on this; narrows the type too
    run = _apply_step()["run"]
    harness = f"terramate() {{ {terramate_body} ; }}\ntee() {{ {tee_body} ; }}\n" + run
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
    # tee "succeeds" at copying the output but reports failure (e.g. a
    # disk-full write) -- the apply itself was fine.
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1" ; exit 1',
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_failed_apply_still_fails_the_step_even_if_tee_succeeds(tmp_path):
    # The pending-check invariant: a real apply failure must still fail the
    # step (leaving the apply check pending), regardless of tee's own outcome.
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
