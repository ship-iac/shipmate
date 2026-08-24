"""actions/apply-cell must refuse a reviewed plan that was produced from a
different commit than the one the job has checked out.

The exact-plan invariant only holds if the plan and the tree agree. The
fingerprint step compares the *variables* a plan was produced with; nothing
compared the *tree*. A plan produced from commit A and applied on a checkout
of commit B applies A's diff while the reviewed check-run, the summary and
the merge all speak of B.

The comparison must happen before the decrypt, the state restore and the
apply, so a wrong-tree plan is refused at the cheapest point rather than
after mutating real infrastructure -- hence the ordering guard here. And the
refusal must be attributable in the cell summary, so a blocked apply names
its cause instead of reporting a bare failure.

An *absent* record is refused too, not tolerated: there is nothing to compare,
and the likeliest cause -- an artifact predating the release that records
provenance -- is stated as a likelihood, since a mismatched engine revision
produces the same absence.
"""

import ast
import os
import re
import subprocess

import pytest
from _loader import action_steps, usable_bash

_BASH = usable_bash()

# Any 40-char lowercase hex will do; these only ever meet each other.
_PLANNED = "a" * 40
_OTHER = "b" * 40


def _steps():
    return action_steps("apply-cell")


def _step_by_id(step_id):
    matches = [s for s in _steps() if s.get("id") == step_id]
    assert len(matches) == 1, f"expected exactly one step with id {step_id!r}, got {len(matches)}"
    return matches[0]


def _index_of(step_id):
    return [s.get("id") for s in _steps()].index(step_id)


def _compose_step():
    matches = [s for s in _steps() if s.get("name") == "Compose cell summary"]
    assert len(matches) == 1, f"expected exactly one Compose cell summary step, got {len(matches)}"
    return matches[0]


def _failsafe_env_keys():
    """The env-var names of the Compose step heredoc's FAILSAFES list, in
    order, read via ast.literal_eval -- a parsed value, not file text."""
    run = _compose_step()["run"]
    _, _, rest = run.partition("<<'PY'\n")
    body, sep, _ = rest.rpartition("\nPY")
    assert sep, "Compose cell summary heredoc has no closing PY terminator"
    for node in ast.parse(body).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FAILSAFES" for t in node.targets
        ):
            return [env_key for env_key, _message in ast.literal_eval(node.value)]
    raise AssertionError("no FAILSAFES assignment found in the Compose cell summary heredoc")


def test_verification_sits_between_the_download_and_the_decrypt():
    # Not merely "before the apply": after the decrypt it would already have
    # spent a decrypt, and after restore-state a state restore, on a plan of
    # another tree.
    assert _index_of("download") < _index_of("planned-head") < _index_of("decrypt")


def test_the_step_is_attributable_in_the_cell_summary():
    env = _compose_step().get("env") or {}
    assert env.get("PLANNED_HEAD_OUTCOME") == "${{ steps.planned-head.outcome }}"
    # The whole ordered vector, hand-written: FAILSAFES is checked in pipeline
    # order, so the new row belongs where the step does -- after the download's
    # and before the decrypt's -- and a partial check would not pin that.
    assert _failsafe_env_keys() == [
        "DOWNLOAD_OUTCOME",
        "PLANNED_HEAD_OUTCOME",
        "DECRYPT_OUTCOME",
        "FINGERPRINT_OUTCOME",
        "RESTORE_OUTCOME",
    ]


def _run_step(tmp_path, *, record=None, observed=_PLANNED):
    """Execute the real, unmodified step script from action.yml with `git`
    replaced by a bash function -- bash resolves a function before searching
    PATH, so this needs no fake executables. `record` is the content of
    planned-head.txt in the working directory, or None to leave it absent.

    The stub `return`s rather than `exit`s: `git rev-parse` is called in a
    command substitution here, but an `exit` body would kill the harness
    script outright if the call site ever moved out of one, and the test
    could then no longer fail on the regression it names."""
    assert _BASH is not None  # callers are skipif-gated on this; narrows the type too
    harness = (
        f'git() {{ printf "%s\n" "{observed}" ; return 0 ; }}\n'
        + _step_by_id("planned-head")["run"]
    )
    work = tmp_path / "work"
    work.mkdir()
    script = tmp_path / "step.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    if record is not None:
        (work / "planned-head.txt").write_text(record, encoding="utf-8", newline="\n")
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir()
    env = dict(os.environ)
    env["RUNNER_TEMP"] = str(runner_temp)
    env["ENV"] = "dev-eu"
    env["STACK_NAME"] = "app"
    return subprocess.run(
        [_BASH, str(script)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        # Explicit, not the locale default: the step's messages carry em dashes,
        # and a cp1252 console would decode them to replacement characters and
        # make the whole-message assertion below unfailable on the real text.
        encoding="utf-8",
        timeout=30,
    ), work


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_an_absent_record_aborts_and_says_to_re_plan(tmp_path):
    # Whole message, written by hand: the two faults this pins against are a
    # presumed cause asserted as fact and a remedy that only exists pre-merge.
    # A substring check on either half leaves the other free to regress.
    r, _ = _run_step(tmp_path, record=None)
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    out = r.stdout + r.stderr
    assert out.strip() == (
        "::error::apply aborted for dev-eu/app: this reviewed plan records no planned "
        "commit, so there is nothing to compare against this checkout — most likely "
        "the plan predates the release that binds a plan to the tree it was produced from, "
        "though a mismatched engine revision produces the same absence. Re-plan this stack "
        "on its pull request and apply the fresh plan; if that pull request has already "
        "merged, a new pull request touching the stack plans and applies it afresh."
    )


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_record_disagreeing_with_head_aborts_naming_both_commits(tmp_path):
    r, _ = _run_step(tmp_path, record=_PLANNED + "\n", observed=_OTHER)
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    out = r.stdout + r.stderr
    assert "::error::" in out
    # Both commits, so the reader can tell which plan and which checkout --
    # one of them alone is not actionable.
    assert _PLANNED in out and _OTHER in out
    assert re.search(r"re-plan", out, re.IGNORECASE)


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_matching_record_proceeds_and_leaves_no_file_in_the_checkout(tmp_path):
    r, work = _run_step(tmp_path, record=_PLANNED + "\n", observed=_PLANNED)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    # Moved, not copied: the action runs in the consumer's checkout and must
    # not leave a stray file at the repo root.
    assert not (work / "planned-head.txt").exists(), (
        "planned-head.txt was left in the working directory"
    )
    assert (tmp_path / "rt" / "planned-head.txt").read_text(encoding="utf-8") == _PLANNED + "\n"
