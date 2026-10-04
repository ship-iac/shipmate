"""drift-cell's cell.json artifact is the sole path by which drift is reported.

Drift reaches the world only as a `drift-summary.*` artifact consumed by the trailing `issues`
job; no in-job step upserts or closes the Issue. `scripts/drift-issues` globs whatever was
downloaded, so a cell whose artifact never arrived is not missing -- it is invisible. One
transient upload failure would mean a green matrix job, a green `issues` job, no Issue, and
real drift unreported until someone notices by hand.

So neither the compose step nor the upload may be `continue-on-error`, unlike apply-cell's
namesakes, whose artifact carries only comment data. Asserted on the parsed action YAML:
`continue-on-error: true` inside a comment satisfies no parsed check.
"""

import json

import pytest
from _loader import load_script, run_lines, step_by

LOAD_BEARING = ("Compose cell summary", "Upload drift summary")


def test_the_compose_step_runs_the_cell_summary_writer():
    # The step's whole job is running that script; a step that stopped calling it would upload
    # no cell.json at all, and drift-issues reads an absent cell as a cell that does not exist.
    # A whole run line, not a substring, so a commented-out invocation cannot satisfy it.
    assert 'python3 "$GITHUB_ACTION_PATH/../../scripts/drift-cell-summary"' in run_lines(
        step_by("drift-cell", name="Compose cell summary")
    )


@pytest.mark.parametrize("name", LOAD_BEARING)
def test_the_cell_artifact_path_is_not_continue_on_error(name):
    step = step_by("drift-cell", name=name)
    assert step.get("continue-on-error") in (None, False), (
        f"{name!r} is continue-on-error: losing the cell artifact would hide real drift"
    )


@pytest.mark.parametrize("name", LOAD_BEARING)
def test_the_cell_artifact_path_runs_even_after_a_failed_plan(name):
    # `if: always()` is the other half: drift-issues needs a result for every attempted-or-blocked
    # cell, so it never auto-closes an Issue for a stack x env whose plan attempt did not succeed.
    assert step_by("drift-cell", name=name).get("if") == "always()"


def test_the_writer_emits_exactly_the_drift_cell_keys(monkeypatch, tmp_path):
    """drift-issues reads this cell, and names the stack in an Issue title that is that Issue's
    identity across engine releases. Mutation: write `"name": os.environ["STACK"]` beside
    `stack` in drift-cell-summary."""
    env = {
        "STACK": "stacks/app",
        "ENV": "dev-eu",
        "PLAN_OUTCOME": "success",
        "DRIFTED": "true",
        "ADD": "1",
        "CHANGE": "2",
        "DESTROY": "3",
        "RUNNER_TEMP": str(tmp_path),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    load_script("drift-cell-summary").main()
    assert json.loads((tmp_path / "cell.json").read_text(encoding="utf-8")) == {
        "stack": "stacks/app",
        "environment": "dev-eu",
        "plan_ok": True,
        "drifted": True,
        "add": 1,
        "change": 2,
        "destroy": 3,
    }
