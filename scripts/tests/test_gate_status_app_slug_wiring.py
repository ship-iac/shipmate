"""Every step that reads the gate statuses hands `scripts/gate-status` the App's slug.

`newest`, `refuse-newer`, `post` and `refresh` judge only the gate statuses the bot user behind
`SHIPMATE_APP_SLUG` created, and refuse when it is empty. The slug comes from the `app-slug`
output of the same `id: token` mint whose token the step reads with: a slug from an input or a
variable could name another valid bot and hide every real hold. The env of "Hold the gate while
apply checks publish" and "Write the gate" is pinned whole by `test_pending_checks.py` and
`test_gate_name_consistency.py`.
"""

import shlex

import pytest
from _loader import engine_steps, run_lines, step_by

READING_SUBCOMMANDS = {"newest", "refuse-newer", "post", "refresh"}

#: (action, step name) of every step invoking a reading subcommand, hand-written.
READING_STEPS = {
    ("summary", "Hold the gate while apply checks publish"),
    ("summary", "Create apply checks (pending / no-changes)"),
    ("summary", "Create/refresh gate"),
    ("gate-refresh", "Write the gate"),
}

SLUG = "${{ steps.token.outputs.app-slug }}"
MINT = "actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510"
SCRIPT = "$GITHUB_ACTION_PATH/../../scripts/gate-status"


def _logical_lines(step):
    lines, pending = [], ""
    for line in run_lines(step):
        if line.endswith("\\"):
            pending += line[:-1] + " "
        else:
            lines.append(pending + line)
            pending = ""
    if pending:
        lines.append(pending)
    return lines


def gate_status_invocations(label, step):
    """The subcommand of every `gate-status` invocation in ``step``'s `run:`."""
    found = []
    for line in _logical_lines(step):
        if "gate-status" not in line and "$gate_status" not in line:
            continue
        try:
            tokens = shlex.split(line)
        except ValueError as err:
            raise AssertionError(f"{label}: {step.get('name')}: cannot tokenize {line!r}") from err
        for i, token in enumerate(tokens):
            if token in (SCRIPT, "$gate_status"):
                assert i + 1 < len(tokens), f"{label}: {step.get('name')}: {line!r}"
                found.append(tokens[i + 1])
    return found


def test_the_reading_steps_are_exactly_these():
    """Mutations: `python3 "$gate_status" newest "$HEAD_SHA"` in summary's "Mirror this run's
    per-cell plan checks onto the head";
    `python3 "$GITHUB_ACTION_PATH/../../scripts/gate-status" refresh "$HEAD_SHA"` in apply-summary;
    `out=$(python3 "$gate_status" \\` / `refresh "$HEAD_SHA")` split over a continuation in
    summary's "Build comment + gate state"."""
    reading = {
        (label, step.get("name"))
        for label, step in engine_steps()
        if READING_SUBCOMMANDS & set(gate_status_invocations(label, step))
    }
    assert reading == READING_STEPS


@pytest.mark.parametrize(
    "name", ["Create apply checks (pending / no-changes)", "Create/refresh gate"]
)
def test_summary_reading_steps_pass_the_minted_slug(name):
    """Mutations, on each step: drop `SHIPMATE_APP_SLUG`; read it from
    `steps.plantoken.outputs.app-slug`."""
    assert step_by("summary", name=name)["env"].get("SHIPMATE_APP_SLUG") == SLUG


@pytest.mark.parametrize("action", ["summary", "gate-refresh"])
def test_the_slug_comes_from_an_unconditional_app_mint(action):
    """Mutations, in each action: `if:` on the token step; `id: token` on another step; a
    different pinned SHA."""
    step = step_by(action, id="token")
    assert step["uses"] == MINT
    assert "if" not in step
