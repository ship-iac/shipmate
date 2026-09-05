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
"""

import re

from _loader import SCRIPTS, action_steps, action_yaml

_MAIN = (SCRIPTS / "lock-info").read_text(encoding="utf-8").split("\ndef main():", 1)[1]
_ACTION = "unlock-cell"


def _strings(node):
    """Every string anywhere in the parsed action, `if:` expressions included and not only the
    `env:` blocks. Parsed, so a comment naming an output name -- this action has several --
    cannot stand in for a real use of it."""
    if isinstance(node, dict):
        node = list(node.values())
    if isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif isinstance(node, str):
        yield node


def test_lock_info_writes_exactly_the_probe_outputs_unlock_cell_consumes():
    written = set(re.findall(r'\.write\(f?"([a-z_]+)=', _MAIN))
    consumed = set(
        re.findall(r"steps\.probe\.outputs\.([a-z_]+)", "\n".join(_strings(action_yaml(_ACTION))))
    )
    probe = [s for s in action_steps(_ACTION) if s.get("id") == "probe"]
    assert len(probe) == 1, f"expected exactly one step with id 'probe', got {len(probe)}"
    step_written = set(
        re.findall(r'([a-z_]+)=[^"\n]*" *>> *"\$GITHUB_OUTPUT"', probe[0].get("run", ""))
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
