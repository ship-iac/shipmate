"""Terramate safeguard-policy contract for the three cells.

plan-cell / apply-cell / drift-cell each invoke `terramate run … -- tofu …`. The
engine disables exactly one safeguard on those invocations — `git-out-of-sync` —
because shipmate checks out a chosen reviewed SHA that is legitimately behind
`main` (remote-freshness is the wrong assertion for the exact-plan model). Every
other safeguard stays live. This test pins that policy so it cannot drift
between the three cells, cannot drift between the two invocations inside one
cell (init vs plan/apply), and cannot silently widen to the meta `git`/`all`
keywords (which would drop `outdated-code`/`git-untracked`/`git-uncommitted`).
"""

import re

from _loader import action_steps

_CELLS = ("plan-cell", "apply-cell", "drift-cell")
_EXPECTED = frozenset({"git-out-of-sync"})


def _run_flags(cell):
    """The --disable-safeguards value(s) on every `terramate run` line in a cell.

    Each cell runs terramate more than once (init, then plan/apply). Every one
    of those lines must carry the identical policy, so this returns the single
    agreed frozenset and fails if they disagree.
    """
    runs = [s["run"] for s in action_steps(cell) if s.get("shell") == "bash" and "run" in s]
    # Comment lines are dropped before counting: commenting an invocation out is
    # the ordinary way to disable it, and a filter that only looks for the text
    # counts the disabled line toward the >= 2 below and parses the policy out of
    # it -- the count would be satisfiable by a comment.
    lines = [
        ln
        for text in runs
        for raw in text.splitlines()
        if (ln := raw.strip()) and not ln.startswith("#") and "terramate run " in ln
    ]
    assert len(lines) >= 2, (
        f"{cell}: expected an init line and a plan/apply line, found {len(lines)}"
    )
    parsed = set()
    for line in lines:
        # Reject the short meta-form outright: -X == --disable-safeguards=all.
        assert " -X" not in line and not line.rstrip().endswith("-X"), (
            f"{cell}: uses -X (disable all) — forbidden: {line.strip()}"
        )
        m = re.search(r"--disable-safeguards=(\S+)", line)
        parsed.add(frozenset(m.group(1).split(",")) if m else None)
    assert len(parsed) == 1, f"{cell}: safeguard policy differs between invocations: {parsed}"
    return parsed.pop()


def test_each_cell_disables_exactly_git_out_of_sync():
    for cell in _CELLS:
        assert _run_flags(cell) == _EXPECTED, (
            f"{cell}: must disable exactly {set(_EXPECTED)} on `terramate run`"
        )


def test_disable_set_is_identical_across_cells():
    sets = {cell: _run_flags(cell) for cell in _CELLS}
    distinct = set(sets.values())
    assert len(distinct) == 1, f"disable-set drifts between cells: {sets}"


def test_never_over_disables_git_or_all():
    for cell in _CELLS:
        flags = _run_flags(cell) or frozenset()
        for forbidden in ("all", "git", "none"):
            assert forbidden not in flags, (
                f"{cell}: disables '{forbidden}' — over-disabling drops real safeguards"
            )
