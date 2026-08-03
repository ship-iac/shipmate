"""The planned cell count must survive the `workflow_run` boundary.

`detect` knows how many cells the plan matrix had; the trusted post-plan job
does not, because a `workflow_run` event carries no job outputs. The count
therefore travels as an artifact NAME (`plan-matrix.<N>`) that the trusted job
reads out of the artifact listing it already makes. Writer grammar and reader
regex are two sides that break silently when either moves, so both are asserted
here, from the parsed files rather than from their text.

Every assertion is on a parsed value (`yaml.safe_load`, then a whole field) or
on the real step body executed under bash -- with `gh` and `sleep` replaced by
shell functions for the reader, so what runs is the step's own text and only its
network call is faked. A substring assertion is satisfied by the same words
appearing in a comment, and by an inverted operator that leaves the text intact;
the few substring checks below are each backed by a behavioural twin.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml
from _loader import ACTIONS, SCRIPTS, WORKFLOWS, action_steps, usable_bash

_BASH = usable_bash()
_JQ = shutil.which("jq")

WF = WORKFLOWS / "summary.yml"


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


def _summary_job():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]["summary"]


def _wf_step(step_id):
    matches = [s for s in _summary_job()["steps"] if s.get("id") == step_id]
    assert len(matches) == 1, f"expected exactly one step with id {step_id!r}"
    return matches[0]


def _reader_prefix():
    """The marker prefix the reader strips, lifted out of its jq program.

    Deliberately backslash-free on both sides (`startswith`/`ltrimstr`, then an
    anchored digits-only test): a regex-escaped `\\.` has to survive the YAML
    block scalar, the shell's single quotes and jq's own string parsing, and one
    lost backslash turns the marker match into "any character here" or a jq
    compile error that reads as an unreadable listing.
    """
    run = _wf_step("artifacts")["run"]
    starts = re.findall(r'startswith\("([^"]+)"\)\s*\)\s*\|\s*ltrimstr\("([^"]+)"\)', run)
    assert len(starts) == 1, f"expected one startswith/ltrimstr marker pair, got {starts}"
    assert starts[0][0] == starts[0][1], f"prefix tested and prefix stripped differ: {starts[0]}"
    return starts[0][0]


def test_the_reader_strips_exactly_the_prefix_the_writer_produces():
    template = _upload_step()["with"]["name"]
    assert template == _reader_prefix() + "${{ steps.build.outputs.count }}"


def test_the_reader_accepts_only_digits_as_a_count():
    # Anchored at both ends. Unanchored, `plan-matrix.1x` would read as 1 and a
    # forged or malformed name would set the expected cell count.
    run = _wf_step("artifacts")["run"]
    assert 'select(test("^[0-9]+$"))' in run


@pytest.mark.parametrize(
    "name,expected",
    [
        ("plan-matrix.0", "0"),
        ("plan-matrix.7", "7"),
        ("plan-matrix.256", "256"),
        ("cell-summary.dev-eu.app", None),
        ("plan.dev-eu.app", None),
        ("plan-matrix.", None),
        ("plan-matrix.x", None),
        ("plan-matrix.1.2", None),
        ("plan-matrix.-1", None),
        ("xplan-matrix.1", None),
        ("plan-matrix.1x", None),
    ],
)
def test_the_readers_grammar_admits_a_count_only_from_a_marker_name(name, expected):
    # Python mirror of the jq pipeline's decision, derived from the prefix the
    # reader itself declares -- the behavioural tests below run the real jq, but
    # only where jq exists, and this claim must hold everywhere.
    prefix = _reader_prefix()
    got = None
    if name.startswith(prefix):
        rest = name[len(prefix) :]
        if re.fullmatch("[0-9]+", rest):
            got = rest
    assert got == expected


def test_two_disagreeing_markers_are_not_a_number():
    # `unique | if length == 1` -- not `first` or `max`. Two markers mean the
    # run's evidence is not describable by one number, and gate-state holds on
    # any non-integer.
    run = _wf_step("artifacts")["run"]
    assert "unique" in run
    assert 'if length == 1 then .[0] else "unknown" end' in run


def test_the_counting_step_does_not_stop_at_the_first_attempt():
    # The listing is read-after-write eventually consistent and is read seconds
    # after the plan run ends; with strict equality a lagging listing now HOLDS
    # the gate, so the quiet pull request this feature protects would go red on
    # a transient. The retry narrows that window (it cannot close it).
    run = _wf_step("artifacts")["run"]
    assert "while" in run
    assert re.search(r'attempt" -lt 3', run)
    assert "sleep" in run


def test_the_workflow_passes_the_marker_count_to_the_summary_action():
    call = [
        s
        for s in _summary_job()["steps"]
        if "ship-iac/shipmate/actions/summary@" in str(s.get("uses", ""))
    ]
    assert len(call) == 1
    assert (
        call[0]["with"]["matrix-count"]
        == "${{ steps.artifacts.outputs.matrix_count || 'unknown' }}"
    )


def test_the_summary_action_threads_the_marker_count_to_gate_state():
    doc = yaml.safe_load((ACTIONS / "summary/action.yml").read_text(encoding="utf-8"))
    assert "matrix-count" in doc["inputs"]
    gate = [s for s in doc["runs"]["steps"] if s.get("id") == "gate"]
    assert len(gate) == 1
    assert gate[0]["env"]["SHIPMATE_MATRIX_COUNT"] == "${{ inputs.matrix-count }}"


def test_gate_state_reads_the_env_var_the_action_supplies():
    # Source-derived: the reader side is the script's own text, so a rename on
    # either side reds this immediately.
    source = (SCRIPTS / "gate-state").read_text(encoding="utf-8")
    assert 'os.environ.get("SHIPMATE_MATRIX_COUNT"' in source


GH_STUB = """
gh() { printf '%s' "$FAKE_LISTING" ; }
sleep() { : ; }
"""


def _run_reader(tmp_path, listing):
    assert _BASH is not None
    script = tmp_path / "step.sh"
    script.write_text(GH_STUB + _wf_step("artifacts")["run"], encoding="utf-8", newline="\n")
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "x",
            "RUN_ID": "42",
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_OUTPUT": str(out),
            "FAKE_LISTING": json.dumps(listing),
        }
    )
    proc = subprocess.run(
        [_BASH, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )
    written = dict(
        line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    return proc, written


def _page(*names):
    return [{"artifacts": [{"name": n} for n in names]}]


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_the_reader_reports_a_consistent_listing(tmp_path):
    _, written = _run_reader(
        tmp_path, _page("plan-matrix.2", "cell-summary.dev-eu.a", "cell-summary.dev-eu.b")
    )
    assert written["count"] == "2"
    assert written["matrix_count"] == "2"


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_the_reader_reports_an_empty_matrix(tmp_path):
    _, written = _run_reader(tmp_path, _page("plan-matrix.0"))
    assert written["count"] == "0"
    assert written["matrix_count"] == "0"


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_a_listing_with_no_marker_reports_unknown(tmp_path):
    # The defect: this listing is byte-identical to an empty matrix without the
    # marker, and gate-state must be told `unknown`, never 0.
    proc, written = _run_reader(tmp_path, _page())
    assert written["count"] == "0"
    assert written["matrix_count"] == "unknown"
    # The step must still succeed: every later step's plain `if:` is implicitly
    # `success() && ...`, so a non-zero exit here writes no gate at all, and a
    # pull request with no gate cannot merge and cannot be told why.
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_a_short_listing_is_reported_as_it_is(tmp_path):
    proc, written = _run_reader(tmp_path, _page("plan-matrix.5", "cell-summary.dev-eu.a"))
    assert written["count"] == "1"
    assert written["matrix_count"] == "5"
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_the_reader_retries_an_inconsistent_listing_and_stops_when_it_converges(tmp_path):
    # Behavioural, so the retry is pinned by what the step DOES rather than by
    # the word "while" appearing in it. The stub answers with a short listing
    # twice and a complete one on the third call.
    script = tmp_path / "step.sh"
    stub = """
gh() {
  n=$(cat "$CALLS" 2>/dev/null || printf 0)
  n=$((n + 1))
  printf '%s' "$n" > "$CALLS"
  if [ "$n" -lt 3 ]; then printf '%s' "$SHORT_LISTING"; else printf '%s' "$FULL_LISTING"; fi
}
sleep() { : ; }
"""
    script.write_text(stub + _wf_step("artifacts")["run"], encoding="utf-8", newline="\n")
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    calls = tmp_path / "calls"
    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "x",
            "RUN_ID": "42",
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_OUTPUT": str(out),
            "CALLS": str(calls),
            "SHORT_LISTING": json.dumps(_page("plan-matrix.2", "cell-summary.dev-eu.a")),
            "FULL_LISTING": json.dumps(
                _page("plan-matrix.2", "cell-summary.dev-eu.a", "cell-summary.dev-eu.b")
            ),
        }
    )
    proc = subprocess.run(
        [_BASH, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )
    written = dict(
        line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    assert calls.read_text(encoding="utf-8").strip() == "3"
    assert written["count"] == "2"
    assert written["matrix_count"] == "2"
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_the_reader_stops_at_one_attempt_on_a_consistent_listing(tmp_path):
    # The quiet path must not pay the retry cost.
    calls = tmp_path / "calls"
    script = tmp_path / "step.sh"
    stub = """
gh() {
  n=$(cat "$CALLS" 2>/dev/null || printf 0)
  printf '%s' "$((n + 1))" > "$CALLS"
  printf '%s' "$FAKE_LISTING"
}
sleep() { : ; }
"""
    script.write_text(stub + _wf_step("artifacts")["run"], encoding="utf-8", newline="\n")
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "x",
            "RUN_ID": "42",
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_OUTPUT": str(out),
            "CALLS": str(calls),
            "FAKE_LISTING": json.dumps(_page("plan-matrix.0")),
        }
    )
    subprocess.run(
        [_BASH, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )
    assert calls.read_text(encoding="utf-8").strip() == "1"


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_two_markers_report_unknown(tmp_path):
    _, written = _run_reader(tmp_path, _page("plan-matrix.1", "plan-matrix.2"))
    assert written["matrix_count"] == "unknown"


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_the_same_marker_twice_is_still_one_count(tmp_path):
    # `overwrite: true` should make this impossible, but `unique` means a
    # duplicate cannot hold the gate even if it happens.
    _, written = _run_reader(tmp_path, _page("plan-matrix.0", "plan-matrix.0"))
    assert written["matrix_count"] == "0"


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_a_marker_name_with_a_suffix_is_not_a_count(tmp_path):
    # An unanchored digits test would read this as 1.
    _, written = _run_reader(tmp_path, _page("plan-matrix.1x", "cell-summary.dev-eu.a"))
    assert written["matrix_count"] == "unknown"


@pytest.mark.skipif(_BASH is None or _JQ is None, reason="bash and jq required")
def test_the_reader_paginates(tmp_path):
    # `--slurp` yields one object per page; a jq program reading only `.[0]`
    # would undercount a >100-artifact run and hold every large plan.
    listing = _page("plan-matrix.2", "cell-summary.dev-eu.a") + _page("cell-summary.dev-eu.b")
    _, written = _run_reader(tmp_path, listing)
    assert written["count"] == "2"
    assert written["matrix_count"] == "2"
