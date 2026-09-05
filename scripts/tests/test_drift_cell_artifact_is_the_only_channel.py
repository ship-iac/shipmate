"""drift-cell's cell.json artifact is the sole path by which drift is reported.

Drift reaches the world only as a `drift-summary.*` artifact consumed by the trailing `issues`
job; no in-job step upserts or closes the Issue. `scripts/drift-issues` globs whatever was
downloaded, so a cell whose artifact never arrived is not missing -- it is invisible. One
transient upload failure would mean a green matrix job, a green `issues` job, no Issue, no Slack,
and real drift unreported until someone notices by hand.

So neither the compose step nor the upload may be `continue-on-error`, unlike apply-cell's
namesakes, whose artifact carries only comment data. Asserted on the parsed action YAML:
`continue-on-error: true` inside a comment satisfies no parsed check.
"""

import pytest
from _loader import action_steps

LOAD_BEARING = ("Compose cell summary", "Upload drift summary")


def _step(name):
    matches = [s for s in action_steps("drift-cell") if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step, got {len(matches)}"
    return matches[0]


@pytest.mark.parametrize("name", LOAD_BEARING)
def test_the_cell_artifact_path_is_not_continue_on_error(name):
    step = _step(name)
    assert step.get("continue-on-error") in (None, False), (
        f"{name!r} is continue-on-error: losing the cell artifact would hide real drift"
    )


@pytest.mark.parametrize("name", LOAD_BEARING)
def test_the_cell_artifact_path_runs_even_after_a_failed_plan(name):
    # `if: always()` is the other half: drift-issues needs a result for every attempted-or-blocked
    # cell, so it never auto-closes an Issue for a stack x env whose plan attempt did not succeed.
    assert _step(name).get("if") == "always()"
