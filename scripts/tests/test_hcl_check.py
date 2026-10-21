"""`scripts/hcl-check`: what it prints and writes for each `terramate fmt --check` and
`terramate generate --detailed-exit-code` outcome, and the `detect` wiring that runs it.

The `fmt-*` and `generate-*` fixtures are raw captures: Terramate 0.17.1 on Windows, in a clone
of `repo-example-stacks`, cwd the clone's root, stdout and stderr redirected to separate files
(no TTY), `<name>.exit` the exit status. Two substitutions make them Linux-shaped, and no other
byte changed: `fmt-two-files.stdout`'s `\\` separators became `/`, and in
`generate-eval-error.stdout` and `fmt-parse-error.stderr` the Windows scratch-clone prefix became
`/home/runner/work/ex/ex`, its `\\` separators `/`. The synthetic cases are built here.

Every case drives `main` through `run_annotated`, with `subprocess.run` replaced by a double that
takes the call the script makes and fails on any other argv, and compares the whole stderr and
the whole `refusal` output against hand-written constants.
"""

import pathlib
import types

import pytest
from _loader import action_steps, action_yaml, github_outputs, load_script, workflow_jobs

hc = load_script("hcl-check")

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FMT = ("terramate", "fmt", "--check")
GENERATE = ("terramate", "generate", "--detailed-exit-code")

FMT_LINE = "not formatted; run terramate fmt"
STALE_LINE = "generated code is stale; run terramate generate"
FMT_TWO = (
    "2 file(s) not formatted; run terramate fmt and commit the result: "
    "stacks/app/globals.tm.hcl, stacks/dns/globals.tm.hcl"
)
STALE_TWO = (
    "2 generated file(s) stale; run terramate generate and commit the result: "
    "stacks/app/_variables.tf, stacks/dns/_variables.tf"
)
FMT_TWO_FILES = (
    f"::error file=stacks/app/globals.tm.hcl::{FMT_LINE}\n"
    f"::error file=stacks/dns/globals.tm.hcl::{FMT_LINE}\n"
)
STALE_TWO_FILES = (
    f"::error file=stacks/app/_variables.tf::{STALE_LINE}\n"
    f"::error file=stacks/dns/_variables.tf::{STALE_LINE}\n"
)
FMT_TWELVE = (
    "12 file(s) not formatted; run terramate fmt and commit the result: f01.hcl, f02.hcl, "
    "f03.hcl, f04.hcl, f05.hcl, f06.hcl, f07.hcl, f08.hcl, f09.hcl, f10.hcl, and 2 more"
)
PARSE_ERROR = (
    "Error: unable to parse configuration%0A> /home/runner/work/ex/ex/stacks/dns/globals.tm.hcl"
    ":4,9-10: HCL syntax error: loading from /home/runner/work/ex/ex/stacks: loading from "
    "/home/runner/work/ex/ex/stacks/dns: There is no closing brace for this block before the end "
    "of the file. This may be caused by incorrect brace nesting elsewhere in this file."
)
EVAL_ERROR = (
    "terramate generate failed; fix the first error it reports: "
    "/home/runner/work/ex/ex/stacks/root.tm.hcl:34,17-28: evaluating content: generate_hcl "
    '"_variables.tf": partial evaluation failed: /home/runner/work/ex/ex/stacks/root.tm.hcl:'
    '34,23-28: eval expression: This object does not have an attribute named "nope".'
)
MISSING = "[Errno 2] No such file or directory: 'terramate'"


def _capture(name):
    """(exit, stdout bytes, stderr bytes) of fixture capture `name`."""
    rc = int((FIXTURES / f"{name}.exit").read_text(encoding="utf-8"))
    return (
        rc,
        (FIXTURES / f"{name}.stdout").read_bytes(),
        (FIXTURES / f"{name}.stderr").read_bytes(),
    )


def _twelve_unformatted():
    return 1, b"".join(b"f%02d.hcl\n" % i for i in range(1, 13)), b""


def _check(monkeypatch, tmp_path, capsys, fmt, generate):
    """(exit code or None, stderr, `refusal` or None when no output was written) of one run;
    `fmt` and `generate` are each an (exit, stdout, stderr) answer or an exception to raise."""
    answers = {FMT: fmt, GENERATE: generate}
    calls = []

    def run(args, *, capture_output):
        assert capture_output is True
        calls.append(tuple(args))
        if tuple(args) not in answers:
            pytest.fail(f"unexpected argv {args!r}")
        answer = answers[tuple(args)]
        if isinstance(answer, BaseException):
            raise answer
        rc, out, err = answer
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    monkeypatch.setattr(hc, "subprocess", types.SimpleNamespace(run=run))
    outputs = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    try:
        hc.run_annotated(hc.main, refusal_output=True)
        code = None
    except SystemExit as exc:
        code = exc.code
    assert calls == [FMT, GENERATE]
    refusal = github_outputs(outputs)["refusal"] if outputs.exists() else None
    return code, capsys.readouterr().err, refusal


def test_two_unformatted_files_each_get_an_annotation_and_one_summary(
    monkeypatch, tmp_path, capsys
):
    """Mutation: return no annotations from `_fmt` -- red."""
    result = _check(
        monkeypatch, tmp_path, capsys, _capture("fmt-two-files"), _capture("generate-clean")
    )
    assert result == (1, FMT_TWO_FILES + f"::error::{FMT_TWO}\n", FMT_TWO)


def test_stale_generated_files_are_named_relative_to_the_repository(monkeypatch, tmp_path, capsys):
    """Mutation: keep the leading `/` of the stack -- red."""
    result = _check(
        monkeypatch, tmp_path, capsys, _capture("fmt-clean"), _capture("generate-stale")
    )
    assert result == (1, STALE_TWO_FILES + f"::error::{STALE_TWO}\n", STALE_TWO)


def test_both_failing_run_both_and_the_refusal_is_fmts(monkeypatch, tmp_path, capsys):
    """Mutation: skip `generate` when `fmt` failed -- red."""
    result = _check(
        monkeypatch, tmp_path, capsys, _capture("fmt-two-files"), _capture("generate-stale")
    )
    assert result == (
        1,
        FMT_TWO_FILES + STALE_TWO_FILES + f"::error::{FMT_TWO}\n::error::{STALE_TWO}\n",
        FMT_TWO,
    )


def test_an_evaluation_error_names_the_first_error_and_no_file(monkeypatch, tmp_path, capsys):
    """Mutation: keep the `error: ` prefix in the reason -- red."""
    result = _check(
        monkeypatch, tmp_path, capsys, _capture("fmt-clean"), _capture("generate-eval-error")
    )
    assert result == (1, f"::error::{EVAL_ERROR}\n", EVAL_ERROR)


def test_a_clean_tree_prints_nothing_and_writes_no_refusal(monkeypatch, tmp_path, capsys):
    """Mutation: drop `main`'s `if errors:` guard -- red."""
    result = _check(
        monkeypatch, tmp_path, capsys, _capture("fmt-clean"), _capture("generate-clean")
    )
    assert result == (None, "", None)


def test_a_path_holding_comma_and_colon_is_escaped_as_a_property(monkeypatch, tmp_path, capsys):
    """Mutation: drop `command_property` for `command_data` in `main` -- red."""
    summary = "1 file(s) not formatted; run terramate fmt and commit the result: s/a,b:c.hcl"
    result = _check(
        monkeypatch, tmp_path, capsys, (1, b"s/a,b:c.hcl\n", b""), _capture("generate-clean")
    )
    assert result == (1, f"::error file=s/a%2Cb%3Ac.hcl::{FMT_LINE}\n::error::{summary}\n", summary)


def test_twelve_unformatted_files_annotate_nine_and_keep_the_summary(monkeypatch, tmp_path, capsys):
    """GitHub keeps 10 error annotations per step. Mutation: remove the annotation budget --
    red."""
    files = "".join(f"::error file=f{i:02d}.hcl::{FMT_LINE}\n" for i in range(1, 10))
    result = _check(
        monkeypatch, tmp_path, capsys, _twelve_unformatted(), _capture("generate-clean")
    )
    assert result == (1, files + f"::error::{FMT_TWELVE}\n", FMT_TWELVE)


def test_twelve_unformatted_and_stale_annotate_eight_of_fmts_and_both_summaries(
    monkeypatch, tmp_path, capsys
):
    """Mutation: remove the annotation budget -- red."""
    files = "".join(f"::error file=f{i:02d}.hcl::{FMT_LINE}\n" for i in range(1, 9))
    result = _check(
        monkeypatch, tmp_path, capsys, _twelve_unformatted(), _capture("generate-stale")
    )
    assert result == (
        1,
        files + f"::error::{FMT_TWELVE}\n::error::{STALE_TWO}\n",
        FMT_TWELVE,
    )


def test_a_root_stack_names_the_file_alone(monkeypatch, tmp_path, capsys):
    """Mutation: keep the leading `/` of the stack -- red."""
    report = b"Code generation report\n\nSuccesses:\n\n- /\n\t[~] _variables.tf\n"
    summary = (
        "1 generated file(s) stale; run terramate generate and commit the result: _variables.tf"
    )
    result = _check(monkeypatch, tmp_path, capsys, _capture("fmt-clean"), (2, report, b""))
    assert result == (
        1,
        f"::error file=_variables.tf::{STALE_LINE}\n::error::{summary}\n",
        summary,
    )


def test_a_parse_error_is_the_generic_error_for_both(monkeypatch, tmp_path, capsys):
    """An `fmt` exit 1 with empty stdout. Mutation: drop the tool's output from the generic
    error -- red."""
    parse = _capture("fmt-parse-error")
    fmt = "terramate fmt exited 1; run it locally to see why"
    generate = "terramate generate exited 1; run it locally to see why"
    result = _check(monkeypatch, tmp_path, capsys, parse, (1, b"", parse[2]))
    assert result == (
        1,
        f"::error::{fmt}%0A{PARSE_ERROR}\n::error::{generate}%0A{PARSE_ERROR}\n",
        f"{fmt} {PARSE_ERROR.replace('%0A', ' ')}",
    )


def test_a_file_line_before_any_stack_is_the_generic_error(monkeypatch, tmp_path, capsys):
    """Mutation: skip a file line before any stack line instead of failing the parse -- red."""
    report = b"\t[~] _variables.tf\n- /stacks/app\n\t[~] main.tf\n"
    first = "terramate generate exited 2; run it locally to see why"
    result = _check(monkeypatch, tmp_path, capsys, _capture("fmt-clean"), (2, report, b""))
    assert result == (
        1,
        f"::error::{first}%0A\t[~] _variables.tf%0A- /stacks/app%0A\t[~] main.tf\n",
        f"{first} [~] _variables.tf - /stacks/app [~] main.tf",
    )


def test_non_utf8_stderr_is_the_generic_error(monkeypatch, tmp_path, capsys):
    """Mutation: decode stderr with `errors="strict"` -- red, a UnicodeDecodeError traceback."""
    first = "terramate fmt exited 1; run it locally to see why"
    result = _check(
        monkeypatch, tmp_path, capsys, (1, b"", b"Error: \xff bad\n"), _capture("generate-clean")
    )
    assert result == (1, f"::error::{first}%0AError: � bad\n", f"{first} Error: � bad")


def test_a_missing_terramate_is_the_generic_error_for_both(monkeypatch, tmp_path, capsys):
    """Mutation: drop the `OSError` handler -- red, a FileNotFoundError traceback."""

    def missing():
        return FileNotFoundError(2, "No such file or directory", "terramate")

    fmt = "terramate fmt did not start; run it locally to see why"
    generate = "terramate generate did not start; run it locally to see why"
    result = _check(monkeypatch, tmp_path, capsys, missing(), missing())
    assert result == (
        1,
        f"::error::{fmt}%0A{MISSING}\n::error::{generate}%0A{MISSING}\n",
        f"{fmt} {MISSING}",
    )


def test_generate_exit_1_with_empty_stdout_is_the_generic_error(monkeypatch, tmp_path, capsys):
    """Mutation: take `lines[0]` of the `error:` search -- red, an IndexError traceback."""
    first = "terramate generate exited 1; run it locally to see why"
    result = _check(
        monkeypatch, tmp_path, capsys, _capture("fmt-clean"), (1, b"", b"Error: boom\n")
    )
    assert result == (1, f"::error::{first}%0AError: boom\n", f"{first} Error: boom")


def test_generate_exit_2_with_nothing_parsed_still_fails(monkeypatch, tmp_path, capsys):
    """Mutation: return no error when `generate` exits 2 with nothing parsed -- red, exit 0."""
    first = "terramate generate exited 2; run it locally to see why"
    result = _check(
        monkeypatch, tmp_path, capsys, _capture("fmt-clean"), (2, b"something else\n", b"")
    )
    assert result == (1, f"::error::{first}%0Asomething else\n", f"{first} something else")


#: `detect`'s whole step list, each step without its `with:` (pinned beside the rest of
#: `plan.yml` in `test_engine_plan_workflow.py`). `checks` has no `if:`: it runs only when
#: `matrix`, which holds the fork refusal, succeeded.
DETECT_STEPS = [
    {"uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"},
    {"uses": "$/actions/setup"},
    {"id": "matrix", "uses": "$/actions/build-matrix"},
    {"id": "checks", "uses": "$/actions/hcl-check"},
]


def test_detect_runs_hcl_check_after_build_matrix():
    """Mutations: swap `matrix` and `checks`; restore `plan.yml`'s `fmt check` step; add
    `if: always()` to `checks` -- each red."""
    steps = workflow_jobs("plan.yml")["detect"]["steps"]
    assert [{k: v for k, v in s.items() if k != "with"} for s in steps] == DETECT_STEPS


def test_the_action_runs_the_script_and_outputs_its_refusal():
    """Mutation: point the action's `refusal` output at `steps.build` -- red."""
    assert action_steps("hcl-check") == [
        {
            "name": "Check formatting and generated code",
            "id": "check",
            "shell": "bash",
            "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/hcl-check"',
        }
    ]
    outputs = action_yaml("hcl-check")["outputs"]
    assert {k: v["value"] for k, v in outputs.items()} == {
        "refusal": "${{ steps.check.outputs.refusal }}"
    }
