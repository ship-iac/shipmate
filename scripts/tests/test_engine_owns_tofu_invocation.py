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

from _loader import action_steps

_CELLS = ("plan-cell", "apply-cell", "drift-cell")


def _command_lines(cell):
    """Non-comment, non-blank lines from the cell's bash step bodies."""
    runs = [s["run"] for s in action_steps(cell) if s.get("shell") == "bash" and "run" in s]
    return [
        stripped
        for text in runs
        for ln in text.splitlines()
        if (stripped := ln.strip()) and not stripped.startswith("#")
    ]


def test_no_cell_delegates_to_a_branch_defined_script():
    for cell in _CELLS:
        offenders = [ln for ln in _command_lines(cell) if "script run" in ln]
        assert not offenders, f"{cell}: delegates the command to branch HCL: {offenders}"


def test_every_terramate_run_names_tofu_after_a_double_dash():
    for cell in _CELLS:
        # Non-empty only: that each cell runs *two* invocations (an init, then
        # the plan/apply) is pinned by test_safeguards.py, which asserts the
        # identical --disable-safeguards value on every one of them.
        lines = [ln for ln in _command_lines(cell) if "terramate run " in ln]
        assert lines, f"{cell}: no `terramate run` invocation"
        for line in lines:
            head, sep, tail = line.partition(" -- ")
            assert sep, f"{cell}: no `--` separator, terramate may parse tofu's flags: {line}"
            assert tail.startswith("tofu "), f"{cell}: command after `--` is not tofu: {line}"


def test_apply_cell_applies_the_reviewed_plan_file():
    lines = [ln for ln in _command_lines("apply-cell") if "tofu apply" in ln]
    assert len(lines) == 1, f"expected exactly one `tofu apply` line, got {lines}"
    line = lines[0]
    assert line.rstrip().endswith("stack.otplan") or "stack.otplan 2>&1" in line, (
        f"apply-cell must apply the stored plan file: {line}"
    )
    assert "-auto-approve" in line and "-input=false" in line, line


def test_plan_cells_write_the_plan_file_the_apply_reads():
    for cell in ("plan-cell", "drift-cell"):
        lines = [ln for ln in _command_lines(cell) if "tofu plan" in ln]
        assert len(lines) == 1, f"{cell}: expected exactly one `tofu plan` line, got {lines}"
        assert "-out=stack.otplan" in lines[0], f"{cell}: {lines[0]}"
