"""actions/plan-cell must record the commit it actually planned, and the two
steps that do it must stay where they are.

A reviewed plan is trusted today because GitHub reports the plan run's head SHA equal to the
pull request's head, which is a property of the trigger and not of the plan. `plan-cell` therefore
reads `git rev-parse HEAD` itself and carries the answer as `planned-head.txt` inside the plan
artifact.

Both orderings guarded here are the substance of that, not house style:

* `id: planned` must precede `Plan`. `Plan` runs `terramate run ... tofu plan`, which executes
  author-controlled HCL and provider binaries, so a `rev-parse` after it reports whatever git
  state that content left behind and the record could describe a tree that was never planned.
* `id: record-head` must follow `Plan` and read `steps.planned.outputs.head`. A
  `planned-head.txt` written before `Plan` sits at repo root while that same author-controlled
  content runs, and is rewritable by it; a step output is not. Reading `inputs.expected-head`
  there instead would re-derive the record from the claim it is supposed to corroborate.

Move either step, or point `record-head` at the input, and the artifact stops being evidence
about the planned tree while still looking exactly like it is.
"""

import os
import subprocess

import pytest
from _loader import action_steps, action_yaml, usable_bash

_BASH = usable_bash()

_SHA = "0123456789abcdef0123456789abcdef01234567"
_OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"


def _steps():
    return action_steps("plan-cell")


def _index(pred, what):
    hits = [i for i, s in enumerate(_steps()) if pred(s)]
    assert len(hits) == 1, f"expected exactly one {what} step, got {len(hits)}"
    return hits[0]


def _by_id(step_id):
    return _index(lambda s: s.get("id") == step_id, f"id: {step_id}")


def _by_name(name):
    return _index(lambda s: s.get("name") == name, f"name: {name}")


def test_expected_head_is_a_required_input():
    inputs = action_yaml("plan-cell")["inputs"]
    assert "expected-head" in inputs, "plan-cell must take the commit it is planning"
    assert inputs["expected-head"].get("required") is True


def test_the_planned_step_runs_before_the_plan_step():
    assert _by_id("planned") < _by_name("Plan")


def test_record_head_sits_between_the_plan_and_its_upload():
    assert _by_name("Plan") < _by_id("record-head") < _by_name("Upload plan artifact")


def test_record_head_reads_the_captured_output_and_nothing_else():
    # Whole-value: one hand-written constant pins both that the file comes from the captured
    # output and that `inputs.expected-head` is not in reach here.
    assert _steps()[_by_id("record-head")]["env"] == {
        "PLANNED_HEAD": "${{ steps.planned.outputs.head }}"
    }


def test_the_emitted_output_is_the_observed_commit():
    # Pinned textually rather than behaviourally on purpose: the emit line is only reached once
    # `$observed` and `$EXPECTED_HEAD` have compared equal, so emitting the input instead is
    # indistinguishable from outside. The whole line is compared to a hand-written constant.
    lines = [ln.strip() for ln in _steps()[_by_id("planned")]["run"].strip().splitlines()]
    assert lines[-1] == 'echo "head=$observed" >> "$GITHUB_OUTPUT"'


def test_the_plan_upload_carries_the_record():
    upload = _steps()[_by_name("Upload plan artifact")]
    entries = [ln.strip() for ln in upload["with"]["path"].strip().splitlines()]
    assert entries == [
        "${{ inputs.stack }}/stack.otplan",
        "fingerprint.txt",
        "planned-head.txt",
    ]


def _run(tmp_path, script_body, env):
    """Execute a real, unmodified `run:` body from action.yml with `git` replaced by a bash
    function. Bash resolves a function before searching PATH, so this needs no fake executable
    and no exec bit."""
    assert _BASH is not None  # callers are skipif-gated on this; narrows the type too
    script = tmp_path / "step.sh"
    script.write_text(script_body, encoding="utf-8", newline="\n")
    return subprocess.run(
        [_BASH, str(script)],
        env={**os.environ, **env},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_planned(tmp_path, *, expected_head, rev_parse=_SHA):
    body = f'git() {{ printf "%s\n" "{rev_parse}" ; }}\n' + _steps()[_by_id("planned")]["run"]
    out = tmp_path / "gh_output"
    out.write_text("", encoding="utf-8")
    r = _run(tmp_path, body, {"EXPECTED_HEAD": expected_head, "GITHUB_OUTPUT": str(out)})
    return r, out.read_text(encoding="utf-8")


def _run_record(tmp_path, *, planned_head):
    body = _steps()[_by_id("record-head")]["run"]
    return _run(tmp_path, body, {"PLANNED_HEAD": planned_head})


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_an_empty_expected_head_is_refused_and_emits_nothing(tmp_path):
    r, written = _run_planned(tmp_path, expected_head="")
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "expected-head" in r.stdout, r.stdout
    # Nothing emitted: a downstream step reading an output that was never set gets the empty
    # string, which `record-head` refuses, but only if this step wrote no plausible value first.
    assert written == "", written


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_head_the_step_did_not_check_out_is_refused_naming_both_commits(tmp_path):
    r, written = _run_planned(tmp_path, expected_head=_OTHER_SHA, rev_parse=_SHA)
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _SHA in r.stdout and _OTHER_SHA in r.stdout, r.stdout
    assert written == "", written


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_matching_head_is_emitted_as_the_step_output(tmp_path):
    r, written = _run_planned(tmp_path, expected_head=_SHA, rev_parse=_SHA)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert written == f"head={_SHA}\n", written


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_record_head_writes_the_captured_commit_with_a_trailing_newline(tmp_path):
    r = _run_record(tmp_path, planned_head=_SHA)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert (tmp_path / "planned-head.txt").read_bytes() == f"{_SHA}\n".encode()


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_an_uncaptured_commit_writes_no_record(tmp_path):
    r = _run_record(tmp_path, planned_head="")
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert not (tmp_path / "planned-head.txt").exists()
