"""The planned cell count must survive the `workflow_run` boundary.

`detect` knows how many cells the plan matrix had; the trusted post-plan job
does not, because a `workflow_run` event carries no job outputs. The count
therefore travels as an artifact NAME (`plan-matrix.<N>`) that the trusted job
reads out of the artifact listing it already makes. Writer grammar and reader
regex are two sides that break silently when either moves, so both are asserted
here, from the parsed files rather than from their text.

Every assertion is on a parsed value (`yaml.safe_load`, then a whole field) or
on the real shell body executed with `gh` stubbed -- a substring assertion is
satisfied by the same words appearing in a comment, and by an inverted operator
that leaves the text intact.
"""

import re

import yaml
from _loader import ACTIONS, action_steps


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


def test_the_marker_payload_is_written_outside_the_consumers_checkout():
    # The action runs in the consumer's workspace; a file left there is an
    # untracked change in their repository.
    run = _record_step()["run"]
    assert "$RUNNER_TEMP/plan-matrix" in run
    assert _upload_step()["with"]["path"] == "${{ runner.temp }}/plan-matrix/count.txt"


def test_the_count_is_validated_before_it_becomes_a_name():
    # A non-numeric count would publish a marker the reader rejects two jobs
    # later, holding the gate with no explanation on the plan run.
    assert re.search(r"=~ \^\[0-9\]\+\$", _record_step()["run"])
    assert "exit 1" in _record_step()["run"]


def test_the_marker_retention_is_short():
    # The reader consults it seconds later; a 90-day default would keep one per
    # plan run in every consumer repository.
    assert _upload_step()["with"]["retention-days"] == 1


def test_the_action_declares_no_new_input():
    # Adoption must stay a re-pin: no consumer YAML change, no opt-out.
    doc = yaml.safe_load((ACTIONS / "build-matrix/action.yml").read_text(encoding="utf-8"))
    assert sorted(doc["inputs"]) == ["all-stacks", "base-sha"]
