"""`drop-routine-lines` drops terramate's two routine lines, and apply-cell runs it on the posted
log only.

The filter is reviewer-facing: apply-cell runs it in a step of its own after the apply, so it
cannot change the apply step's status, and the job log keeps the lines. The routine lines here
follow a raw capture of `terramate run ... -- tofu show -no-color stack.otplan` stderr
(Terramate 0.17.1, Windows, redirected to a file), as in `test_plan_cell_render.py`.
"""

import os
import subprocess
import sys

from _loader import (
    ACTIONS,
    SCRIPTS,
    action_steps,
    bash_only,
    run_lines,
    run_step,
    step_by,
    write_python3_shim,
)

_LOG = (
    b"terramate: Entering stack in /stacks/app\n"
    b'terramate: Executing command "tofu apply -input=false stack.otplan"\n'
    b"aws_s3_bucket.b: Creating...\n"
    b"note: terramate: Entering stack in /stacks/app\n"
    b"Warning: bad \xff byte\n"
    b"Apply complete! Resources: 1 added, 0 changed, 0 destroyed.\r\n"
    b"Outputs: none"
)
_FILTERED = (
    b"aws_s3_bucket.b: Creating...\n"
    b"note: terramate: Entering stack in /stacks/app\n"
    b"Warning: bad \xff byte\n"
    b"Apply complete! Resources: 1 added, 0 changed, 0 destroyed.\r\n"
    b"Outputs: none"
)
_STEP = "Drop terramate's routine lines from the posted log"


def test_the_filter_drops_only_lines_starting_with_a_routine_prefix():
    """Mutations: drop either prefix from `ROUTINE` (its line passes); match a prefix anywhere in
    the line (the mid-line case drops); decode each line as strict UTF-8 (the `\\xff` line fails
    the run) -- each red."""
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "drop-routine-lines")],
        input=_LOG,
        capture_output=True,
        timeout=30,
    )
    assert (r.returncode, r.stdout, r.stderr) == (0, _FILTERED, b"")


def test_apply_cell_filters_the_log_in_its_own_always_step_before_the_upload():
    """Mutations: drop `if: always()`; drop `continue-on-error`; `mv` unconditionally
    (`... > "$log.tmp" || true`); move the step after `Upload apply summary` -- each red."""
    names = [s.get("name") for s in action_steps("apply-cell")]
    at = names.index(_STEP)
    assert names[at - 1 : at + 2] == ["Compose cell summary", _STEP, "Upload apply summary"]
    step = step_by("apply-cell", name=_STEP)
    assert {k: step.get(k) for k in ("if", "continue-on-error", "shell")} == {
        "if": "always()",
        "continue-on-error": True,
        "shell": "bash",
    }
    assert run_lines(step) == [
        "set -euo pipefail",
        'log="$RUNNER_TEMP/apply.txt"',
        '[ -f "$log" ] || exit 0',
        'python3 "$GITHUB_ACTION_PATH/../../scripts/drop-routine-lines" < "$log" > "$log.tmp"',
        'mv "$log.tmp" "$log"',
    ]


def test_the_apply_step_still_tees_the_raw_output():
    """The filter must not move into the apply step, where its failure could fail the apply.
    Mutation: pipe the apply through `drop-routine-lines` before `tee` -- red."""
    assert run_lines(step_by("apply-cell", id="apply")) == [
        "set -euo pipefail",
        'echo "::group::terramate apply: $STACK"',
        "set +e",
        (
            'terramate run --disable-safeguards=git-out-of-sync --no-recursive -C "$STACK" -- tofu'
            ' apply -input=false stack.otplan 2>&1 | tee "$RUNNER_TEMP/apply.txt"'
        ),
        'status="${PIPESTATUS[0]}"',
        "set -e",
        'echo "::endgroup::"',
        'exit "$status"',
    ]


def _run_filter_step(tmp_path, log, prefix=""):
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir()
    if log is not None:
        (runner_temp / "apply.txt").write_bytes(log)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_python3_shim(bin_dir)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_ACTION_PATH": str(ACTIONS / "apply-cell"),
    }
    r = run_step(tmp_path, prefix + step_by("apply-cell", name=_STEP)["run"], env)
    return r, runner_temp


@bash_only
def test_the_step_rewrites_apply_txt_without_the_routine_lines(tmp_path):
    """Mutations: drop the `mv` (apply.txt keeps the routine lines); name the script
    `drop-routine-line` -- each red."""
    r, rt = _run_filter_step(tmp_path, _LOG)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert (rt / "apply.txt").read_bytes() == _FILTERED


@bash_only
def test_a_failing_filter_leaves_apply_txt_whole(tmp_path):
    """Mutation: `mv` unconditionally (`... > "$log.tmp" || true`; the log is emptied) -- red."""
    r, rt = _run_filter_step(tmp_path, _LOG, prefix="python3() { return 1 ; }\n")
    assert r.returncode != 0
    assert (rt / "apply.txt").read_bytes() == _LOG


@bash_only
def test_a_cell_with_no_apply_txt_passes_and_writes_none(tmp_path):
    """Mutation: drop the `[ -f "$log" ] || exit 0` line (the redirect fails the step) -- red."""
    r, rt = _run_filter_step(tmp_path, None)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert sorted(p.name for p in rt.iterdir()) == []
