"""actions/unlock-cell releases a stranded state lock -- and can do nothing else.

Breaking a lock whose holder is still running corrupts state, so every guard
here is load-bearing:

- the action carries no `tofu apply` and no apply-completion step, checked over
  the whole file text so "unlock cannot apply" is structural rather than
  intentional;
- its three `terramate run` lines are compared, in order and as raw lines,
  against hand-written constants -- raw, not shlex tokens, because the quoting
  of `"$LOCK_ID"` is part of the contract (it reaches a `force-unlock` argv) and
  shlex would drop it;
- the release step runs only on the probe's `held == 'true'` output, the whole
  expression compared to a constant;
- `$STACK` and `$LOCK_ID` reach the shell through `env:`, never `${{ }}`.

Two guards execute the shipped shell bodies rather than reading them: the
three-state report (no lock / could-not-determine, which must never collapse
into one message, and which succeeds only on the first of those) and the
conditional rendering of `lock_created` / `lock_operation`, which
`scripts/lock-info` may legitimately return empty.

Every constant here is hand-written and never derived from action.yml -- an
expectation read back out of the file under test passes whatever that file says.
"""

import os
import re
import subprocess

import pytest
from _loader import ACTIONS, action_steps, action_yaml, usable_bash

_BASH = usable_bash()
_ACTION = ACTIONS / "unlock-cell" / "action.yml"

_WRAPPER = 'terramate run --disable-safeguards=git-out-of-sync --no-recursive -C "$STACK" --'

#: Every `terramate run` the cell is expected to make, in order. Whole lines:
#: a window inside them asserts nothing about either edge, and the flags at both
#: edges are the load-bearing part -- `--disable-safeguards=git-out-of-sync` on
#: the left, `-force "$LOCK_ID"` on the right.
_EXPECTED_RUNS = [
    f"{_WRAPPER} tofu init -input=false -reconfigure",
    f'{_WRAPPER} tofu plan -input=false -refresh=false 2>&1 | tee "$RUNNER_TEMP/probe.txt"',
    f'{_WRAPPER} tofu force-unlock -force "$LOCK_ID"',
]

#: The release step's gate, whole. `-force` above and this below are the pair
#: that keeps the destructive command both possible and conditional.
_RELEASE_IF = "${{ steps.probe.outputs.held == 'true' }}"

_NO_LOCK = "::notice::no state lock held for app / dev-eu"
_UNDETERMINED = (
    "::warning::could not determine whether a lock is held for app / dev-eu — see the log"
)


def _step(step_id):
    matches = [s for s in action_steps(_ACTION) if s.get("id") == step_id]
    assert len(matches) == 1, f"expected exactly one step with id {step_id!r}, got {len(matches)}"
    return matches[0]


def _live_lines():
    """Non-comment, non-blank lines from every `run:` body, continuations joined.

    Every step with a `run:`, not only `shell: bash` ones -- selecting on the
    shell would let a command move to `shell: sh` and run unexamined. Comments
    go first (a mention in prose is not an invocation), then backslash joins, so
    a command split across lines is matched as the one command it is.
    """
    runs = [s["run"] for s in action_steps(_ACTION) if "run" in s]
    kept = [
        stripped
        for text in runs
        for ln in text.splitlines()
        if (stripped := ln.strip()) and not stripped.startswith("#")
    ]
    return "\n".join(kept).replace("\\\n", " ").splitlines()


def test_the_unlock_cell_can_never_apply():
    """Whole file text, comments and description included: the absence has to be
    structural, so even a commented-out apply reds rather than sitting there as
    the next refactor's starting point."""
    text = _ACTION.read_text(encoding="utf-8")
    assert text.strip(), f"{_ACTION} is empty -- every guard here would assert nothing"
    assert "tofu apply" not in text, "unlock-cell must never apply"
    assert "apply-complete" not in text, "unlock-cell must not complete an apply check"


def test_the_cell_runs_exactly_these_three_terramate_invocations():
    """One selector, one comparison. Comparing the whole list also pins the
    count and the init-probe-release order without either being a second,
    disagreeable check."""
    got = [ln for ln in _live_lines() if re.search(r"terramate\s+run\b", ln)]
    assert got == _EXPECTED_RUNS, f"unexpected invocations: {got}"


def test_the_release_step_is_gated_on_the_probes_held_output():
    _step("probe")  # the expression must name a step that exists
    assert _step("release").get("if") == _RELEASE_IF


def test_stack_and_lock_id_arrive_through_env():
    release = _step("release")
    assert _step("probe")["env"]["STACK"] == "${{ inputs.stack }}"
    assert release["env"]["STACK"] == "${{ inputs.stack }}"
    assert release["env"]["LOCK_ID"] == "${{ steps.probe.outputs.lock_id }}"
    # Non-vacuous: the env pin means nothing over a body that never uses it.
    assert '"$LOCK_ID"' in release["run"]
    for step in action_steps(_ACTION):
        assert "${{" not in step.get("run", ""), f"{step.get('name')!r} interpolates into its run"


def test_no_step_swallows_its_own_failure():
    """Every step here fails the cell it belongs to. `continue-on-error` on the
    init in particular would turn "the engine could not look at the lock" into a
    green cell carrying a warning nobody reads across dozens of cells."""
    for step in action_steps(_ACTION):
        assert "continue-on-error" not in step, f"{step.get('name')!r} swallows its failure"


def _run_body(tmp_path, step_id, env, *, terramate_body="return 0"):
    """Execute the real, unmodified `run:` body with `terramate` replaced by a
    bash function -- bash resolves a function before PATH, so this needs no fake
    executable or exec bit (fragile on a Windows dev box).

    The stub `return`s rather than `exit`s: the release step calls terramate as a
    plain command in the current shell, so an `exit` body would end the script
    there and every assertion below would read an empty stdout as agreement.
    Returning leaves errexit to decide, which is the behaviour under test."""
    assert _BASH is not None  # callers are skipif-gated; also narrows the type
    script = tmp_path / f"{step_id}.sh"
    body = f"terramate() {{ {terramate_body} ; }}\n" + _step(step_id)["run"]
    script.write_text(body, encoding="utf-8", newline="\n")
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    full = dict(os.environ)
    full["GITHUB_STEP_SUMMARY"] = str(summary)
    full["GITHUB_OUTPUT"] = str(tmp_path / "out.txt")
    full.update(env)
    # encoding, not text=True: the messages carry an em dash, and the default
    # locale decode mangles it into a mismatch that looks like a real diff.
    r = subprocess.run(
        [_BASH, str(script)], env=full, capture_output=True, encoding="utf-8", timeout=30
    )
    return r, summary.read_text(encoding="utf-8")


_REPORT_ENV = {"STACK_NAME": "app", "ENV": "dev-eu"}


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_clean_probe_reports_no_lock_held(tmp_path):
    r, _ = _run_body(tmp_path, "report", {**_REPORT_ENV, "PROBE_STATUS": "0"})
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout.strip() == _NO_LOCK


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_failed_probe_with_no_lock_reports_undetermined_never_no_lock(tmp_path):
    """The distinction that must not collapse: telling an operator "no lock
    held" when the truth is "could not look" reads as a clean cell."""
    r, _ = _run_body(tmp_path, "report", {**_REPORT_ENV, "PROBE_STATUS": "1"})
    # ...and it fails the cell: not looking is not a success.
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout.strip() == _UNDETERMINED
    assert "no state lock held" not in r.stdout


_RELEASE_ENV = {
    "STACK": "stacks/app",
    "STACK_NAME": "app",
    "ENV": "dev-eu",
    "LOCK_ID": "0123abcd-4567-89ef-0123-456789abcdef",
}


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_the_release_notice_renders_the_optional_fields_when_present(tmp_path):
    r, summary = _run_body(
        tmp_path,
        "release",
        {
            **_RELEASE_ENV,
            "LOCK_CREATED": "2026-08-20 09:00:00",
            "LOCK_OPERATION": "OperationTypeApply",
        },
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "2026-08-20 09:00:00" in r.stdout
    assert "OperationTypeApply" in r.stdout
    assert _RELEASE_ENV["LOCK_ID"] in summary


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_the_release_notice_omits_the_optional_fields_when_blank(tmp_path):
    """`scripts/lock-info` blanks `created`/`operation` rather than rejecting a
    lock over a display-only field, so neither may be rendered unconditionally."""
    r, summary = _run_body(
        tmp_path, "release", {**_RELEASE_ENV, "LOCK_CREATED": "", "LOCK_OPERATION": ""}
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "created" not in r.stdout
    assert "operation" not in r.stdout
    assert _RELEASE_ENV["LOCK_ID"] in summary


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_failed_force_unlock_fails_the_cell(tmp_path):
    """Finding a lock and failing to release it is a real failure -- the
    operator must not read a green cell as a released lock."""
    r, _ = _run_body(
        tmp_path,
        "release",
        _RELEASE_ENV | {"LOCK_CREATED": "", "LOCK_OPERATION": ""},
        terramate_body="return 7",
    )
    assert r.returncode == 7, f"stdout={r.stdout!r} stderr={r.stderr!r}"


def test_the_cell_takes_no_credential_or_artifact_inputs():
    """Inputs are the whole surface, compared as a set: unlock needs no token,
    no state path, no plan artifact and no passphrase, and gaining one would
    mean it had grown a second job."""
    assert set(action_yaml(_ACTION).get("inputs") or {}) == {"stack", "stack-name", "env"}
