"""scripts/tofu-error: annotate a failed plan with OpenTofu's first error diagnostic.

Fixture provenance: `plan_error_two_ansi.txt` is OpenTofu v1.12.4's stderr from
`tofu plan` over two outputs reading undeclared variables, on Windows, written
straight to a file (no TTY). The escape bytes are intact: OpenTofu colours its
diagnostics into a file too, as `lock-info` records for a runner.
"""

import io
import pathlib

import pytest
from _loader import load_script, run_lines, step_by

te = load_script("tofu-error")
FIX = pathlib.Path(__file__).parent / "fixtures"

FIRST = (
    "::error::Error: Reference to undeclared input variable%0A"
    "%0A"
    'on main.tf line 2, in output "x":%0A'
    "2:   value = var.first_missing%0A"
    "%0A"
    'An input variable with the name "first_missing" has not been declared. This%0A'
    'variable can be declared with a variable "first_missing" {} block.\n'
)


def _run(monkeypatch, capsys, text):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    te.main()
    return capsys.readouterr().out


def test_annotates_the_first_diagnostic_whole(monkeypatch, capsys):
    """One annotation, the first block only, no colour, newlines escaped. Mutations: drop the
    ANSI strip (`li._strip` -> `str.strip`); print `error` without `command_data`; take the last
    `Error:` line instead of the first; drop the `╵` stop."""
    raw = (FIX / "plan_error_two_ansi.txt").read_text(encoding="utf-8")
    assert _run(monkeypatch, capsys, raw) == FIRST


def test_prints_nothing_without_an_error_line(monkeypatch, capsys):
    """A failure outside OpenTofu keeps only the runner's own annotation. Mutation: print an
    `::error::` when `first_error` is empty."""
    assert _run(monkeypatch, capsys, "terramate: stack not found\n") == ""


def test_escapes_percent_and_cuts_at_cap(monkeypatch, capsys):
    """Mutations: drop the `%` escape (`command_data` -> identity); drop the `[:CAP]` cut."""
    out = _run(monkeypatch, capsys, "Error: 100% " + "x" * 5000 + "\n")
    assert out == "::error::Error: 100%25 " + "x" * (te.CAP - len("Error: 100% ")) + "\n"


#: Hand-written: the line both plan steps run after a failed plan.
ANNOTATE = (
    '[ "$status" -eq 0 ] || python3 "$GITHUB_ACTION_PATH/../../scripts/tofu-error"'
    ' < "$RUNNER_TEMP/plan-run.txt" || true'
)


@pytest.mark.parametrize(
    ("action", "step"), [("plan-cell", "Plan"), ("drift-cell", "Plan + classify drift")]
)
def test_each_plan_step_annotates_after_the_group_and_never_masks_the_status(action, step):
    """The annotation runs once, right after `::endgroup::`, and an extractor failure cannot
    change the step's exit status. Mutations: drop `|| true`; move the line above
    `echo "::endgroup::"`; delete it from drift-cell."""
    lines = run_lines(step_by(action, name=step))
    end = lines.index('echo "::endgroup::"')
    assert lines[end + 1] == ANNOTATE
    assert lines.count(ANNOTATE) == 1
