"""actions/apply-cell's fingerprint and digest-input refusals, each pinned as a whole message.

The remedy must hold after the merge too, where a `shipmate plan` comment re-plans nothing, so
each test compares the entire annotation to a hand-written constant.
"""

import os

from _loader import bash_only, run_step, step_by


def _env(tmp_path, **extra):
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir(exist_ok=True)
    return {
        **os.environ,
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_ACTION_PATH": str(tmp_path / "action"),
        "ENV": "dev-eu",
        "STACK": "stacks/app",
        **extra,
    }


@bash_only
def test_a_fingerprint_mismatch_refuses_with_the_two_branch_remedy(tmp_path):
    """Mutation: end the `fingerprint` step's refusal with a bare `Re-plan.`."""
    # `python3` is a bash function: the regenerated fingerprint differs from the reviewed one.
    harness = (
        "python3() {\n"
        '  case "$2" in\n'
        "    --fingerprint-only) printf 'regenerated\\n' > fingerprint.txt ;;\n"
        "    --keys) printf 'TF_VAR_region' ;;\n"
        "  esac\n"
        "}\n" + step_by("apply-cell", id="fingerprint")["run"]
    )
    (tmp_path / "fingerprint.txt").write_text("reviewed\n", encoding="utf-8", newline="\n")
    r = run_step(tmp_path, harness, _env(tmp_path))
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        "::error::apply aborted for dev-eu/stacks/app: current env does not match the reviewed"
        " plan's fingerprint. Current variable names: TF_VAR_region. Values differ or a variable"
        " was added/removed. Re-plan (comment shipmate plan or push), or after the merge open a"
        " new pull request touching the stack.\n"
    )


@bash_only
def test_a_missing_digest_refuses_with_re_pin_then_the_two_branch_remedy(tmp_path):
    """Mutation: end the `digest-input` step's refusal with a bare `Re-plan.`."""
    harness = step_by("apply-cell", id="digest-input")["run"]
    r = run_step(tmp_path, harness, _env(tmp_path, PLAN_SHA256=""))
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        "::error::apply aborted for dev-eu/stacks/app: no usable plan-text digest reached this"
        " action. Re-pin every engine reference to one commit. Then re-plan (comment shipmate"
        " plan or push), or after the merge open a new pull request touching the stack.\n"
    )
