"""Every step that reads the gate statuses hands `scripts/gate-status` the App's slug.

`newest`, `refuse-newer`, `post` and `refresh` judge only the gate statuses the bot user behind
`SHIPMATE_APP_SLUG` created, and refuse when it is empty. The slug comes from the `app-slug`
output of the same `id: token` mint whose token the step reads with: a slug from an input or a
variable could name another valid bot and hide every real hold. This file asserts the slug of
every reading step; `test_pending_checks.py` and `test_gate_name_consistency.py` also pin the
whole env of "Hold the gate while apply checks publish" and "Write the gate".
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
ASSIGNMENT = f"gate_status={SCRIPT}"
#: Every `gate-status` subcommand, hand-written.
SUBCOMMANDS = {
    "newest",
    "refuse-newer",
    "post",
    "hold",
    "refresh",
    "deploy",
    "checks",
    "moved",
    "replan",
}


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
    """The subcommand of every `gate-status` invocation in ``step``'s `run:`.

    Every logical line naming `gate-status` or `gate_status`, except the assignment of the script
    path, must invoke it through `SCRIPT` or `$gate_status` with a known subcommand: a spelling
    this does not recognise fails rather than going unchecked."""
    found = []
    for line in _logical_lines(step):
        if "gate-status" not in line and "gate_status" not in line:
            continue
        where = f"{label}: {step.get('name')}: {line!r}"
        try:
            tokens = shlex.split(line)
        except ValueError as err:
            raise AssertionError(f"{where}: cannot tokenize") from err
        if tokens == [ASSIGNMENT]:
            continue
        mentions = [i for i, t in enumerate(tokens) if "gate-status" in t or "gate_status" in t]
        subcommands = [
            tokens[i + 1]
            for i in mentions
            if tokens[i] in (SCRIPT, "$gate_status")
            and i + 1 < len(tokens)
            and tokens[i + 1] in SUBCOMMANDS
        ]
        assert len(subcommands) == len(mentions), f"{where}: unrecognised gate-status invocation"
        found += subcommands
    return found


def test_the_reading_steps_are_exactly_these():
    """Mutations: `python3 "$gate_status" newest "$HEAD_SHA"` in summary's "Mirror this run's
    per-cell plan checks onto the head";
    `python3 "$GITHUB_ACTION_PATH/../../scripts/gate-status" refresh "$HEAD_SHA"` in apply-summary;
    `out=$(python3 "$gate_status" \\` / `refresh "$HEAD_SHA")` split over a continuation in
    summary's "Build comment + gate state". The selector fails on a spelling it does not
    recognise: `python3 "${gate_status}" newest "$HEAD_SHA"` in summary's "Mirror this run's
    per-cell plan checks onto the head";
    `python3 "${GITHUB_ACTION_PATH}/../../scripts/gate-status" refresh "$HEAD_SHA"` in
    apply-summary; `python3 "$gate_status" hold "$S" x || python3 "${gate_status}" newest "$S"`
    in summary's "Build comment + gate state" (every mention on a line must be an invocation). A
    `hold` through `"$gate_status"` in an unlisted step stays green."""
    reading = {
        (label, step.get("name"))
        for label, step in engine_steps()
        if READING_SUBCOMMANDS & set(gate_status_invocations(label, step))
    }
    assert reading == READING_STEPS


@pytest.mark.parametrize(("action", "name"), sorted(READING_STEPS))
def test_each_reading_step_passes_the_slug_of_an_unconditional_app_mint(action, name):
    """Mutations: `${{ inputs.app-slug }}` as one step's slug; drop one step's slug env line; add
    a step with no slug env to `READING_STEPS`; `if:` on the token step; a different pinned SHA."""
    env = step_by(action, name=name).get("env") or {}
    assert env.get("SHIPMATE_APP_SLUG") == SLUG
    token = step_by(action, id="token")
    assert (token["uses"], "if" in token) == (MINT, False)
