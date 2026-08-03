"""The planned cell count must survive the `workflow_run` boundary.

`detect` knows how many cells the plan matrix had; the trusted post-plan job
does not, because a `workflow_run` event carries no job outputs. The count
therefore travels as an artifact NAME (`plan-matrix.<N>`) that the trusted job
reads out of the artifact listing it already makes. Writer grammar and reader
regex are two sides that break silently when either moves, so both are asserted
here, from the parsed files rather than from their text.

Every assertion is on a parsed value (`yaml.safe_load`, then a whole field) or
on the real step body executed under bash -- a substring assertion is satisfied
by the same words appearing in a comment, and by an inverted operator that
leaves the text intact.
"""

import os
import pathlib
import subprocess

import pytest
import yaml
from _loader import ACTIONS, action_steps, usable_bash

_BASH = usable_bash()


def _upload_step():
    steps = [
        s
        for s in action_steps("build-matrix")
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(steps) == 1, f"expected exactly one upload step, got {len(steps)}"
    return steps[0]


def _record_step():
    steps = [
        s for s in action_steps("build-matrix") if s.get("name") == "Record the planned cell count"
    ]
    assert len(steps) == 1, f"expected exactly one record step, got {len(steps)}"
    return steps[0]


def _upload_path(runner_temp):
    """The upload step's declared `path:`, resolved against a real `runner.temp`.

    Derived rather than restated: the writer and the uploader must agree on one
    path, and a test holding its own copy of it cannot notice them diverging.
    """
    declared = _upload_step()["with"]["path"]
    assert "${{ runner.temp }}" in declared, (
        f"upload path no longer resolves against runner.temp ({declared!r}) -- "
        "the payload would be read from the consumer's checkout"
    )
    return pathlib.Path(declared.replace("${{ runner.temp }}", str(runner_temp)))


def _run_record_step(tmp_path, count):
    """Execute the real `Record the planned cell count` body.

    `cwd` and `RUNNER_TEMP` are deliberately different directories: pointed at
    the same one, a workspace-relative write would land exactly where the
    `$RUNNER_TEMP` one does and the payload-location guard could not fail.
    """
    assert _BASH is not None  # callers are skipif-gated on this; narrows the type too
    workspace = tmp_path / "workspace"
    runner_temp = tmp_path / "runner-temp"
    workspace.mkdir()
    runner_temp.mkdir()
    script = tmp_path / "step.sh"
    script.write_text(_record_step()["run"], encoding="utf-8", newline="\n")
    declared = _record_step().get("env") or {}
    assert list(declared) == ["SHIPMATE_MATRIX_COUNT"], (
        f"the count no longer reaches the step as SHIPMATE_MATRIX_COUNT ({declared!r})"
    )
    env = dict(os.environ)
    env.update({"RUNNER_TEMP": str(runner_temp), **dict.fromkeys(declared, count)})
    proc = subprocess.run(
        [_BASH, str(script)], cwd=workspace, env=env, capture_output=True, text=True, timeout=30
    )
    return proc, runner_temp, workspace


def test_the_marker_name_carries_the_planned_cell_count():
    # The name IS the signal: no file is downloaded to learn N.
    assert _upload_step()["with"]["name"] == "plan-matrix.${{ steps.build.outputs.count }}"


def test_the_marker_upload_fails_when_there_is_nothing_to_upload():
    # upload-artifact's default is `warn`: it would publish NOTHING and hold
    # every gate in the consumer repo, with only a warning on the plan run.
    assert _upload_step()["with"]["if-no-files-found"] == "error"


def test_the_marker_upload_survives_a_rerun_of_the_detect_job():
    # Artifacts are per-run and immutable, so a re-run would 409 and fail the
    # job -- which holds every gate rather than greening one, but for a reason
    # nobody could read off the run.
    assert _upload_step()["with"]["overwrite"] is True


def test_the_marker_is_published_unconditionally():
    # One rule for the reader: no parseable marker means hold. A conditional
    # upload reintroduces the empty/non-empty asymmetry this design removes,
    # and absence-means-safe is fail-open here: the lagging listing being
    # defended against hides the marker along with the cell summaries.
    for step in (_record_step(), _upload_step()):
        assert "if" not in step


def test_the_count_is_recorded_before_it_is_published():
    # Swapping the two steps leaves every field above intact and breaks the
    # action only at runtime: the upload would look for a file not yet written,
    # and `if-no-files-found: error` would fail every plan run.
    steps = action_steps("build-matrix")
    assert steps.index(_record_step()) < steps.index(_upload_step())


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_numeric_count_lands_where_the_upload_step_looks_for_it(tmp_path):
    # The action runs in the consumer's checkout, so the payload goes to
    # $RUNNER_TEMP -- a file left in the workspace is an untracked change in
    # their repository.
    proc, runner_temp, workspace = _run_record_step(tmp_path, "7")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = _upload_path(runner_temp)
    assert payload.is_file(), (
        f"nothing at {payload}; runner_temp holds {list(runner_temp.rglob('*'))}"
    )
    assert payload.read_text(encoding="utf-8").strip() == "7"
    assert list(workspace.iterdir()) == [], "the consumer's checkout was written to"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
@pytest.mark.parametrize("count", ["abc", "", "1 2", "-1", "unknown"])
def test_a_non_numeric_count_refuses_rather_than_naming_an_artifact(tmp_path, count):
    # A non-numeric count would publish a marker the reader rejects two jobs
    # later, holding the gate with no explanation on the plan run.
    proc, runner_temp, workspace = _run_record_step(tmp_path, count)
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "::error::" in proc.stdout
    assert not _upload_path(runner_temp).exists(), "a marker payload was written anyway"
    assert list(workspace.iterdir()) == [], "the consumer's checkout was written to"


def test_the_marker_retention_is_short():
    # The reader consults it seconds later; a 90-day default would keep one per
    # plan run in every consumer repository.
    assert _upload_step()["with"]["retention-days"] == 1


def test_the_action_declares_no_new_input():
    # Adoption must stay a re-pin: no consumer YAML change, no opt-out.
    doc = yaml.safe_load((ACTIONS / "build-matrix/action.yml").read_text(encoding="utf-8"))
    assert sorted(doc["inputs"]) == ["all-stacks", "base-sha"]
