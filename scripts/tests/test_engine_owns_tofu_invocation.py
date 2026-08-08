"""The engine, not branch content, owns what a cell runs.

`terramate script run <name>` executes a command list defined in the consumer
repository's HCL, which on a pull request is author-controlled: the exact-plan
invariant (apply the reviewed `stack.otplan`, nothing else) was enforced by the
branch rather than by the engine, and a plan cell's command list was equally
open. Every cell now names its own `tofu` command after `--`, so a pull request
can change what tofu *sees* but not what tofu is *asked to do*.

Asserted per cell on the parsed step bodies, not on file text: a `script run`
mention surviving in a comment is fine, a live one is not.
"""

import re
import shlex

from _loader import action_steps

_CELLS = ("plan-cell", "apply-cell", "drift-cell")

# The expected invocations, HAND-WRITTEN token by token against the flag set the
# engine means to run -- never derived from the action files. A vector computed
# from the file under test passes whatever that file says and pins nothing, which
# is the tautology version of every hole found in this guard so far.
#
# Whole lines, not a window inside them. Comparing a slice (the tokens between
# `apply` and `2>&1`) asserted nothing about either edge, and four fail-open
# shapes followed: a second `&& terramate run … -- tofu apply` appended past the
# truncation point; a `true apply … ;` decoy ahead of it, whose tokens matched the
# expected slice so the real apply was never examined; a `-lock=false` smuggled
# into that appended copy, which a line-wide scan had caught before the window
# replaced it; and a `./evil.sh ;` prefix, which a tail-only check also misses.
#
# These duplicate the --disable-safeguards literal that test_safeguards.py pins.
# That is deliberate: one duplicated literal against four proved fail-open shapes
# is the right trade. Do NOT "de-duplicate" it by narrowing back to a window.
_WRAPPER = [
    "terramate",
    "run",
    "--disable-safeguards=git-out-of-sync",
    "--no-recursive",
    "-C",
    "$STACK",
    "--",
]
_INIT = [*_WRAPPER, "tofu", "init", "-input=false", "-reconfigure"]
_PLAN = [*_WRAPPER, "tofu", "plan", "-input=false", "-lock=false", "-out=stack.otplan"]
# The apply takes the backend's lock (no -lock=false) and applies the reviewed
# plan file. Teed to RUNNER_TEMP -- never the repo tree, where the live
# git-untracked safeguard runs.
_APPLY = [
    *_WRAPPER,
    "tofu",
    "apply",
    "-input=false",
    "-auto-approve",
    "stack.otplan",
    "2>&1",
    "|",
    "tee",
    "$RUNNER_TEMP/apply.txt",
]


#: Every `terramate run` each cell is expected to make, in order.
_EXPECTED = {
    "plan-cell": [_INIT, _PLAN],
    "drift-cell": [_INIT, _PLAN],
    "apply-cell": [_INIT, _APPLY],
}


def _command_lines(cell):
    """Live command lines from every `run:` body in the cell.

    Every step with a `run:`, not only `shell: bash` ones -- selecting on the
    shell lets a delegation evade the guard by moving to `shell: sh`, which runs
    the same command. Comments and blanks are dropped first (a `script run`
    mention in prose is not an invocation), then backslash continuations are
    joined, so a command split across two lines is matched as the one command it
    is. Order matters: comments end at the newline in shell, so a trailing `\\`
    inside one does not continue it.
    """
    runs = [s["run"] for s in action_steps(cell) if "run" in s]
    kept = [
        stripped
        for text in runs
        for ln in text.splitlines()
        if (stripped := ln.strip()) and not stripped.startswith("#")
    ]
    return "\n".join(kept).replace("\\\n", " ").splitlines()


def test_no_cell_delegates_to_a_branch_defined_script():
    for cell in _CELLS:
        # `script\s+run`, not the literal: any run of whitespace joins the two
        # words, including the space a joined line continuation leaves behind.
        offenders = [ln for ln in _command_lines(cell) if re.search(r"script\s+run", ln)]
        assert not offenders, f"{cell}: delegates the command to branch HCL: {offenders}"


def test_each_cell_runs_exactly_the_engines_own_invocations():
    """Every `terramate run` in the cell, in order, argument for argument.

    One comparison per cell rather than one per command. Selecting the lines to
    check by the tofu subcommand on them (`tofu apply`, `tofu plan`) left an
    *extra* invocation examined by nothing but a separate count, and that count
    selected on the substring `"terramate run "` -- which a doubled space
    defeats, including the one `_command_lines` itself produces when it joins
    `terramate \\` + newline + `run`. A live `tofu apply` with no plan file could
    be added to any cell that way with every test green.

    So: one regex selector, one expected list, no second selector to disagree
    with the first. Comparing the whole list also pins the count, and pins
    init-before-plan/apply ordering, without either being asserted separately.
    """
    for cell, expected in _EXPECTED.items():
        lines = [ln for ln in _command_lines(cell) if re.search(r"terramate\s+run\b", ln)]
        got = [shlex.split(ln, comments=True) for ln in lines]
        assert got == expected, f"{cell}: unexpected invocations, got {got}"
