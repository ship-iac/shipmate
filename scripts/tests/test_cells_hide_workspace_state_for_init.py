"""Every cell hides restored workspace state across `tofu init`.

A local backend at its default paths keeps named-workspace state in
`<stack>/terraform.tfstate.d`. `actions/state` restores that directory into a
fresh checkout, which has no `.terraform` record marking the backend
initialised -- so `tofu init` classifies it as legacy state awaiting migration,
asks whether to copy it, and `-input=false` turns the question into
`Error asking for state migration action: input is disabled`. The cell never
plans. (The single-state case -- the `folders` flavour's `terraform.tfstate` --
is short-circuited by OpenTofu and is deliberately not hidden.)

So each cell moves the directory aside for the init and moves it back
afterwards, on the failure path too. Both halves are load-bearing: without the
move-aside the init dies, and without the move-back the cell plans or applies
against no state at all.
"""

import re

from _loader import action_steps

#: The whole hide/init/restore sequence, hand-written line by line, never
#: derived from the action files. Order is pinned by comparing the list: a
#: move-back that drifts above the init, or a lost `rm -rf` letting the real
#: state be moved *inside* the empty directory init recreates, both red.
#: `mktemp -du` rather than a fixed name so the hidden path cannot collide with
#: anything already in RUNNER_TEMP and swallow the state it holds.
#: Bound to a name rather than wrapped across two lines inside the list below:
#: adjacent string literals in a list are one dropped comma away from silently
#: merging two pinned lines into one.
_INIT_LINE = (
    "terramate run --disable-safeguards=git-out-of-sync --no-recursive -C "
    '"$STACK" -- tofu init -input=false -reconfigure'
)

_HIDE_INIT_RESTORE = [
    'ws="$STACK/terraform.tfstate.d"',
    'hidden=$(mktemp -du "$RUNNER_TEMP/tfstate.d.XXXXXX")',
    'if [ -d "$ws" ]; then mv "$ws" "$hidden"; fi',
    _INIT_LINE,
    'if [ -d "$hidden" ]; then rm -rf "$ws"; mv "$hidden" "$ws"; fi',
    '[ "$init_status" -eq 0 ] || exit "$init_status"',
]

_CELLS = ("plan-cell", "drift-cell", "apply-cell")

#: One selector: every line naming the state directory, the hidden path, or the
#: init itself. A second selector for the same property would eventually
#: disagree with the first.
_SELECT = re.compile(
    r"\bws=|\bhidden=|\$ws\b|\$hidden\b|\$init_status\b|tofu init|terraform\.tfstate\.d"
)


def _live_lines(cell):
    """Non-comment, non-blank lines from every `run:` body in the cell."""
    return [
        stripped
        for step in action_steps(cell)
        if "run" in step
        for ln in step["run"].splitlines()
        if (stripped := ln.strip()) and not stripped.startswith("#")
    ]


def test_each_cell_hides_workspace_state_across_init():
    for cell in _CELLS:
        got = [ln for ln in _live_lines(cell) if _SELECT.search(ln)]
        assert got == _HIDE_INIT_RESTORE, f"{cell}: unexpected init sequence, got {got}"
