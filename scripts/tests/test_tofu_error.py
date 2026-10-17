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
    data = text if isinstance(text, bytes) else text.encode()
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))
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
    out = _run(monkeypatch, capsys, "╷\n│ Error: 100% " + "x" * 5000 + "\n╵\n")
    assert out == "::error::Error: 100%25 " + "x" * (te.CAP - len("Error: 100% ")) + "\n"


def test_skips_an_error_line_that_opens_no_box(monkeypatch, capsys):
    """A warning's detail and a log line starting `Error: ` are not the diagnostic. Mutation:
    drop the `lines[i - 1] == "╷"` condition."""
    text = (
        "Error: from a log line\n"
        "╷\n│ Warning: deprecated\n│ Error: quoted in the detail\n╵\n"
        "╷\n│ Error: the real one\n╵\n"
    )
    assert _run(monkeypatch, capsys, text) == "::error::Error: the real one\n"


def test_annotates_despite_a_byte_that_is_not_utf8(monkeypatch, capsys):
    """Provider output can carry any byte. Mutation: decode stdin strictly."""
    out = _run(monkeypatch, capsys, "╷\n│ Error: bad ".encode() + b"\xff byte\n")
    assert out == "::error::Error: bad � byte\n"


#: Hand-written: the line both plan steps run after a failed plan.
ANNOTATE = (
    '[ "$status" -eq 0 ] || python3 "$GITHUB_ACTION_PATH/../../scripts/tofu-error"'
    ' < "$RUNNER_TEMP/plan-run.txt" || true'
)


@pytest.mark.parametrize(
    ("action", "step"), [("plan-cell", "Plan"), ("drift-cell", "Plan + classify drift")]
)
def test_each_plan_step_annotates_after_the_group_and_never_masks_the_status(action, step):
    """The annotation runs once, right after `::endgroup::`, and neither an extractor failure nor
    `tee`'s own status can change the step's exit status. Mutations: drop `|| true`; move the
    line above `echo "::endgroup::"`; delete it from drift-cell; read `status=$?`."""
    lines = run_lines(step_by(action, name=step))
    plan = next(i for i, ln in enumerate(lines) if ln.startswith("terramate run"))
    assert lines[plan + 1] == "status=${PIPESTATUS[0]}"
    end = lines.index('echo "::endgroup::"')
    assert lines[end + 1] == ANNOTATE
    assert lines.count(ANNOTATE) == 1
