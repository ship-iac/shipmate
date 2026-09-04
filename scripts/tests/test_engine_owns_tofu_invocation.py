"""The engine, not branch content, owns what a cell runs.

`terramate script run <name>` executes a command list defined in the consumer repository's HCL,
which on a pull request is author-controlled. Under it the exact-plan invariant -- apply the
reviewed `stack.otplan`, nothing else -- was enforced by the branch rather than by the engine,
and a plan cell's command list was equally open. Every cell names its own `tofu` command after
`--`, so a pull request can change what tofu sees but not what tofu is asked to do.

Asserted per cell on the parsed step bodies, not on file text: a `script run` mention surviving
in a comment is fine, a live one is not. Which actions are cells is read off the tree, so a new
one cannot escape the guard by not being added to a list here.

Whole lines, not a window inside them. Comparing a slice -- the tokens between `apply` and
`2>&1` -- asserted nothing about either edge, and four fail-open shapes followed: a second
`&& terramate run … -- tofu apply` appended past the truncation point; a `true apply … ;` decoy
ahead of it, whose tokens matched the expected slice so the real apply was never examined; a
`-lock=false` smuggled into that appended copy, which a line-wide scan had caught before the
window replaced it; and a `./evil.sh ;` prefix, which a tail-only check also misses.
"""

import re
import shlex

from _loader import ACTIONS, action_steps

#: The expected invocations, hand-written token by token against the flag set the engine means to
#: run, never derived from the action files: a vector computed from the file under test passes
#: whatever that file says and pins nothing, which is the tautology version of every hole found in
#: this guard so far. Whole lines, never a window inside them; the module docstring says which
#: fail-open shapes a window let through.
#:
#: This is the only guard on --disable-safeguards=git-out-of-sync, a flag CONTRACT.md documents
#: as load-bearing rather than belt-and-suspenders. Do not narrow back to a window.
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
#: The apply takes the backend's lock, so no -lock=false, and applies the reviewed plan file. No
#: -auto-approve either: a stored plan never prompts, so the flag would be inert, and without it
#: a lost `stack.otplan` argument fails on "Error asking for approval" instead of re-planning from
#: branch content and applying that unattended. Teed to RUNNER_TEMP, never the repo tree, which
#: is the consumer's checkout.
_APPLY = [
    *_WRAPPER,
    "tofu",
    "apply",
    "-input=false",
    "stack.otplan",
    "2>&1",
    "|",
    "tee",
    "$RUNNER_TEMP/apply.txt",
]


#: The unlock cell's probe takes the backend's lock to discover the id of a lock already held,
#: and refreshes no provider on the way there. No -out, because nothing in that cell applies what
#: it plans. The release names the id it found and carries -force, because no cell has a TTY to
#: answer the confirmation prompt.
_PROBE = [
    *_WRAPPER,
    "tofu",
    "plan",
    "-input=false",
    "-refresh=false",
    "2>&1",
    "|",
    "tee",
    "$RUNNER_TEMP/probe.txt",
]
_FORCE_UNLOCK = [*_WRAPPER, "tofu", "force-unlock", "-force", "$LOCK_ID"]


#: Every `terramate run` each cell is expected to make, in order.
_EXPECTED = {
    "plan-cell": [_INIT, _PLAN],
    "drift-cell": [_INIT, _PLAN],
    "apply-cell": [_INIT, _APPLY],
    "unlock-cell": [_INIT, _PROBE, _FORCE_UNLOCK],
}
#: One source for the cell list, so a cell added here cannot be guarded by one test and silently
#: skipped by the other. Which cells belong in it is derived from the tree, by
#: `_tofu_invoking_actions`, never hand-maintained here.
_CELLS = tuple(_EXPECTED)

#: A live `tofu` invocation: the word at command position, so `-C "$STACK"` paths and
#: actions/setup's `$RUNNER_TEMP/.tofu-plugin-cache` are not one.
_TOFU_RE = re.compile(r"(?<![\w./-])tofu\s+[a-z-]")


def _tofu_invoking_actions():
    """Every action under `actions/` whose live shell invokes tofu.

    Discovered, not listed. `_EXPECTED`'s vectors are hand-written on purpose, because an
    expectation read back out of the file it checks pins nothing, but the set of cells must not
    be: a cell added later would be one this file never looks at. That is how the unlock cell
    first escaped this guard.
    """
    found = {
        path.parent.name
        for path in ACTIONS.glob("*/action.yml")
        if any(_TOFU_RE.search(ln) for ln in _command_lines(path.parent.name))
    }
    # Non-vacuity: a broken glob or a moved tree would otherwise hand the caller an empty set,
    # which matches nothing and passes nothing.
    assert found, f"no action under {ACTIONS} invokes tofu -- this guard would assert nothing"
    return found


def _command_lines(cell):
    """Live command lines from every `run:` body in the cell.

    Every step with a `run:`, not only `shell: bash` ones: selecting on the shell lets a
    delegation evade the guard by moving to `shell: sh`, which runs the same command. Comments
    and blanks are dropped first, because a `script run` mention in prose is not an invocation,
    then backslash continuations are joined, so a command split across two lines is matched as
    the one command it is. That order matters: comments end at the newline in shell, so a
    trailing `\\` inside one does not continue it.
    """
    runs = [s["run"] for s in action_steps(cell) if "run" in s]
    kept = [
        stripped
        for text in runs
        for ln in text.splitlines()
        if (stripped := ln.strip()) and not stripped.startswith("#")
    ]
    return "\n".join(kept).replace("\\\n", " ").splitlines()


def test_every_tofu_invoking_action_has_an_expected_vector():
    """The cell list is the tree's, not this file's."""
    assert _tofu_invoking_actions() == set(_EXPECTED), (
        "an action invokes tofu with no _EXPECTED entry (or an entry names an action "
        "that no longer invokes tofu); add its whole hand-written invocation vector: "
        f"unlisted={sorted(_tofu_invoking_actions() - set(_EXPECTED))}, "
        f"listed but not invoking={sorted(set(_EXPECTED) - _tofu_invoking_actions())}"
    )


def test_no_cell_delegates_to_a_branch_defined_script():
    for cell in _CELLS:
        # `script\s+run`, not the literal: any run of whitespace joins the two words, including
        # the space a joined line continuation leaves behind.
        offenders = [ln for ln in _command_lines(cell) if re.search(r"script\s+run", ln)]
        assert not offenders, f"{cell}: delegates the command to branch HCL: {offenders}"


def test_each_cell_runs_exactly_the_engines_own_terramate_run_invocations():
    """Every `terramate run` line in the cell, in order, argument for argument.

    Scope is exactly what the selector sees: `terramate run` lines. Other ways to reach tofu -- a
    variable holding the command, an `eval`, a bare `tofu` with no wrapper, a `uses:` step -- are
    outside this test and are not claimed by it.

    One comparison per cell rather than one per command. Selecting the lines to check by the tofu
    subcommand on them (`tofu apply`, `tofu plan`) left an extra invocation examined by nothing
    but a separate count, and that count selected on the substring `"terramate run "`, which a
    doubled space defeats -- including the one `_command_lines` itself produces when it joins
    `terramate \\` + newline + `run`. A live `tofu apply` with no plan file could be added to any
    cell that way with every test green.

    So: one regex selector, one expected list, no second selector to disagree with the first.
    Comparing the whole list also pins the count, and pins init-before-plan/apply ordering,
    without either being asserted separately.

    `shlex.split` is called without `comments=True`: shlex ends a word at a `#` anywhere inside
    it, bash only at the start of a word. `-out=stack.otplan#||tofu apply …` is one bash word
    plus a live second command, and shlex reads it as the bare `-out=stack.otplan` the vector
    expects -- green, while the cell ran a second live command. Without the flag the `#` and its
    payload stay in the token, the vector differs, and it reds. The accepted, fail-closed cost is
    that a trailing inline comment on one of these lines now reds; no cell has one, and
    whole-line comments are stripped by `_command_lines`. Do not re-add `comments=True` to clear
    such a red.
    """
    for cell, expected in _EXPECTED.items():
        lines = [ln for ln in _command_lines(cell) if re.search(r"terramate\s+run\b", ln)]
        got = [shlex.split(ln) for ln in lines]
        assert got == expected, f"{cell}: unexpected invocations, got {got}"
