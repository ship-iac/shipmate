"""Terramate safeguard-policy contract for the three script-run cells.

plan-cell / apply-cell / drift-cell each run `terramate script run`. The engine
disables exactly one safeguard on that invocation — `git-out-of-sync` — because
shipmate checks out a chosen reviewed SHA that is legitimately behind `main`
(remote-freshness is the wrong assertion for the exact-plan model). Every other
safeguard stays live. This test pins that policy so it cannot drift between the
three cells or silently widen to the meta `git`/`all` keywords (which would drop
`outdated-code`/`git-untracked`/`git-uncommitted`).
"""

import pathlib
import re

import yaml

_ACTIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "actions"
_CELLS = ("plan-cell", "apply-cell", "drift-cell")
_EXPECTED = frozenset({"git-out-of-sync"})


def _script_run_flags(cell):
    """The --disable-safeguards value(s) on the cell's `terramate script run`.

    Returns the parsed frozenset of safeguard keywords, or None if the cell has
    no disable flag on its script-run line.
    """
    spec = yaml.safe_load((_ACTIONS_DIR / cell / "action.yml").read_text(encoding="utf-8"))
    steps = (spec.get("runs") or {}).get("steps") or []
    runs = [s["run"] for s in steps if s.get("shell") == "bash" and "run" in s]
    lines = [ln for text in runs for ln in text.splitlines() if "terramate script run" in ln]
    assert len(lines) == 1, (
        f"{cell}: expected exactly one `terramate script run` line, got {len(lines)}"
    )
    line = lines[0]
    # Reject the short meta-form outright: -X == --disable-safeguards=all.
    assert " -X" not in line and not line.endswith("-X"), (
        f"{cell}: uses -X (disable all) — forbidden"
    )
    m = re.search(r"--disable-safeguards=(\S+)", line)
    if not m:
        return None
    return frozenset(m.group(1).split(","))


def test_each_cell_disables_exactly_git_out_of_sync():
    for cell in _CELLS:
        assert _script_run_flags(cell) == _EXPECTED, (
            f"{cell}: must disable exactly {set(_EXPECTED)} on `terramate script run`"
        )


def test_disable_set_is_identical_across_cells():
    sets = {cell: _script_run_flags(cell) for cell in _CELLS}
    distinct = set(sets.values())
    assert len(distinct) == 1, f"disable-set drifts between cells: {sets}"


def test_never_over_disables_git_or_all():
    for cell in _CELLS:
        flags = _script_run_flags(cell) or frozenset()
        for forbidden in ("all", "git", "none"):
            assert forbidden not in flags, (
                f"{cell}: disables '{forbidden}' — over-disabling drops real safeguards"
            )
