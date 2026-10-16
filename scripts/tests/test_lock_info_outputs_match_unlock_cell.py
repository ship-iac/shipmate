"""The keys `scripts/lock-info` writes are exactly the probe outputs
`actions/unlock-cell` consumes.

Nothing else pins this pair: `test_lock_info.py` covers `parse()` only, and
`test_unlock_cell.py` reads the consumer side alone. So renaming `lock_id` in
`main()` leaves the whole suite green while the release step passes an empty
`LOCK_ID` to `tofu force-unlock` -- a destructive command, on the apply path.

Both sides are derived, never transcribed: a hand-written copy of either list is
the thing that rots, and a copy of the producer's keys would pass whatever the
producer says.

`probe_status` is consumed but not produced by lock-info -- the probe step echoes it itself. It
is excluded by deriving the outputs that step's own `run:` body writes, so the exclusion cannot
swallow a lock-info key: lock-info writes through python, not through an
`echo ... >> "$GITHUB_OUTPUT"` in the shell body. That leaves an equality, which is the direction
that catches the failure: a rename on either side moves a name out of one set and not the other,
and a dropped write shrinks one side.

`step_written` is deliberately not asserted non-empty. The guard must still hold if the probe
step ever legitimately stops echoing an output of its own, and an exclusion regex that breaks
today fails loud anyway, because `probe_status` then survives into the equality.

A probe output counts as consumed only where it reaches a step through an `env:` value or `if:`.
A mention elsewhere (a mapping key, a step name, `run:` text) does not count, so it cannot keep
`LOCK_ID: ""` green. A direct `${{ }}` read inside `run:` is out of scope and must be written as
an `env:` entry instead.
"""

import re

from _loader import SCRIPTS, action_steps, step_by

_MAIN = (SCRIPTS / "lock-info").read_text(encoding="utf-8").split("\ndef main():", 1)[1]
_ACTION = "unlock-cell"


def _consumed(steps):
    wired = [v for s in steps for v in [*(s.get("env") or {}).values(), s.get("if", "")]]
    return set(re.findall(r"steps\.probe\.outputs\.([A-Za-z0-9_-]+)", "\n".join(map(str, wired))))


def test_lock_info_writes_exactly_the_probe_outputs_unlock_cell_consumes():
    """Renaming `steps.probe.outputs.lock_id` to `lock_idx` in the action reddens this."""
    calls = "".join(re.findall(r"write_outputs\(([^)]*)\)", _MAIN))
    written = set(re.findall(r"\b([a-z_]+)=", calls))
    consumed = _consumed(action_steps(_ACTION))
    probe = step_by(_ACTION, id="probe")
    step_written = set(
        re.findall(r'([a-z_]+)=[^"\n]*" *>> *"\$GITHUB_OUTPUT"', probe.get("run", ""))
    )
    from_lock_info = consumed - step_written

    assert written, "derived no written keys from lock-info's main() -- guard asserts nothing"
    assert consumed, (
        f"derived no steps.probe.outputs.* uses from {_ACTION} -- guard asserts nothing"
    )
    assert from_lock_info, (
        f"every consumed output ({sorted(consumed)}) was excluded as written by the probe step "
        f"itself ({sorted(step_written)}) -- guard asserts nothing"
    )
    assert written == from_lock_info, (
        f"lock-info writes {sorted(written)} but {_ACTION} consumes "
        f"{sorted(from_lock_info)} from the probe (excluding the step's own "
        f"{sorted(step_written)})"
    )


def test_consumed_counts_only_env_values_and_if():
    """Making `_consumed` scan the whole step, or read only one step, reddens this. So does
    reverting `_consumed`'s capture to `[a-z_]+`: `in_env2` reads as `in_env`. The digit in
    `in_env2` is load-bearing."""
    step = {
        "name": "${{ steps.probe.outputs.name_ref }}",
        "if": "${{ steps.probe.outputs.in_if == 'true' }}",
        "env": {
            "IN_ENV": "${{ steps.probe.outputs.in_env }}",
            "${{ steps.probe.outputs.key_ref }}": "",
        },
        "run": "# was ${{ steps.probe.outputs.run_ref }}\ntrue\n",
    }
    other = {"env": {"IN_ENV2": "${{ steps.probe.outputs.in_env2 }}"}}
    assert _consumed([step, other]) == {"in_env", "in_if", "in_env2"}
