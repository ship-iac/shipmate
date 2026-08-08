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

import itertools
import re
import shlex

from _loader import action_steps

_CELLS = ("plan-cell", "apply-cell", "drift-cell")
#: Shell tokens that end a command's own argument list.
_OPERATORS = frozenset({"2>&1", "|", "||", "&&", ";", "&", ">", ">>", "<"})


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


def test_every_terramate_run_names_tofu_after_a_double_dash():
    for cell in _CELLS:
        lines = [ln for ln in _command_lines(cell) if "terramate run " in ln]
        assert len(lines) == 2, (
            f"{cell}: expected exactly two `terramate run` invocations -- an init "
            f"line and a plan/apply line -- found {len(lines)}: {lines}"
        )
        for line in lines:
            head, sep, tail = line.partition(" -- ")
            assert sep, f"{cell}: no `--` separator, terramate may parse tofu's flags: {line}"
            assert tail.startswith("tofu "), f"{cell}: command after `--` is not tofu: {line}"


def test_apply_cell_applies_the_reviewed_plan_file():
    lines = [ln for ln in _command_lines("apply-cell") if "tofu apply" in ln]
    assert len(lines) == 1, f"expected exactly one `tofu apply` line, got {lines}"
    line = lines[0]
    # Positional, not "appears somewhere on the line". Both weaker forms have a
    # live counterexample that re-plans from branch configuration while staying
    # green: `-auto-approve # stack.otplan 2>&1 | tee …` (a substring or an
    # endswith-after-stripping-2>&1 check reads the comment as the argument), and
    # `-auto-approve && echo stack.otplan 2>&1 | …` (token membership reads
    # echo's argument as apply's). So: take the tokens after `apply`, cut at the
    # first shell operator, and require the plan file to be what is left last.
    # shlex handles the quoting and drops comments -- and unlike cutting at the
    # first `#` it does not mangle a `#` inside quotes (plan-cell has one).
    tokens = shlex.split(line, comments=True)
    assert "apply" in tokens, f"apply-cell: `apply` is not its own token: {line}"
    args = list(
        itertools.takewhile(lambda t: t not in _OPERATORS, tokens[tokens.index("apply") + 1 :])
    )
    assert args and args[-1] == "stack.otplan", (
        f"apply-cell must apply the stored plan file as `tofu apply`'s last "
        f"argument, got {args}: {line}"
    )
    assert "-auto-approve" in args and "-input=false" in args, line
    # Flags forbidden, not just flags present: the apply deliberately takes the
    # backend's lock, so re-adding -lock=false must not be invisible here.
    assert "-lock=false" not in tokens, f"apply must take the backend lock: {line}"


def test_plan_cells_write_the_plan_file_the_apply_reads():
    for cell in ("plan-cell", "drift-cell"):
        lines = [ln for ln in _command_lines(cell) if "tofu plan" in ln]
        assert len(lines) == 1, f"{cell}: expected exactly one `tofu plan` line, got {lines}"
        assert "-out=stack.otplan" in lines[0], f"{cell}: {lines[0]}"
