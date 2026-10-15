import io
import os
import subprocess
import sys
import types

import pytest
from _loader import ENGINE
from _shipmate import (
    _run,
    approval_rules,
    check_runs,
    command_data,
    gate_check,
    is_head_sha,
    is_pr_number,
    missing_names,
    named,
    review_count,
    run_annotated,
    utf8_output,
    validate_head_sha,
)


def test_utf8_output_switches_both_streams_from_cp1252_to_utf8(monkeypatch):
    """Mutations: drop `sys.stdout` from the loop's tuple, or `sys.stderr`; either stream then
    writes `—` as cp1252's `\\x97`."""
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    utf8_output()
    for stream in (out, err):
        stream.write("—")
        stream.flush()
    assert (out.buffer.getvalue(), err.buffer.getvalue()) == (b"\xe2\x80\x94", b"\xe2\x80\x94")


@pytest.mark.parametrize(
    "script, argv, refusal",
    [
        (
            "onboard",
            ["--app-id", "é"],
            "--app-id must be all digits: 'é'\n",
        ),
        (
            "register-app",
            ["--name", "x", "--repo", "é", "--out", "key.pem"],
            "--repo must be <owner>/<repo>: 'é'\n",
        ),
    ],
)
def test_a_script_run_as_a_file_writes_its_refusal_as_utf8(tmp_path, script, argv, refusal):
    """Both refusals run before any `gh` or `git` call. Mutation: delete `utf8_output()` from
    the script's `__main__` block; `é` then leaves as cp1252's `\\xe9`."""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    proc = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / script), *argv],
        cwd=tmp_path,
        env=env,
        capture_output=True,
    )
    assert (proc.returncode, proc.stderr.replace(b"\r\n", b"\n")) == (1, refusal.encode("utf-8"))


def test_gate_check_returns_the_gate_entry_and_its_rules_parameters():
    """Mutation: return the first required-status-check entry -- the `lint` rule's comes back."""
    lint = {
        "required_status_checks": [{"context": "lint"}],
        "strict_required_status_checks_policy": False,
    }
    gate = {
        "required_status_checks": [{"context": "shipmate / gate", "integration_id": 7}],
        "strict_required_status_checks_policy": True,
    }
    rules = [
        {"type": "required_status_checks", "parameters": lint},
        {"type": "required_status_checks", "parameters": gate},
    ]
    assert gate_check(rules, "shipmate / gate") == (
        {"context": "shipmate / gate", "integration_id": 7},
        {
            "required_status_checks": [{"context": "shipmate / gate", "integration_id": 7}],
            "strict_required_status_checks_policy": True,
        },
    )


def test_review_count_is_the_highest_over_every_pull_request_rule():
    """Mutation: `min` for `max` -- the count reads 0."""
    rules = [
        {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
        {"type": "pull_request", "parameters": {"required_approving_review_count": 2}},
    ]
    assert review_count(rules) == 2


def test_approval_rules_drop_the_branch_policy_and_list_each_type_once():
    """Mutation: keep `branch_policy` -- it appears in the list; build `types` as a list and
    filter `branch_policy` out of it -- `wait_timer` appears twice."""
    env = {
        "protection_rules": [
            {"type": "wait_timer"},
            {"type": "branch_policy"},
            {"type": "required_reviewers"},
            {"type": "wait_timer"},
        ]
    }
    assert approval_rules(env) == ["required_reviewers", "wait_timer"]


def test_approval_rules_report_a_typeless_rule_as_unknown():
    """Mutation: `or "?"` -> `or "x"` -- `x` is listed instead; subtract `"?"` too -- the
    typeless rules vanish."""
    env = {"protection_rules": [{"type": "wait_timer"}, {}, {"type": ""}, {"type": None}]}
    assert approval_rules(env) == ["?", "wait_timer"]


def test_is_pr_number_accepts_github_numbers_only():
    """Mutations: pattern `[0-9]+` (`0`, `007` and the eleven digits red); `match` for
    `fullmatch` (`7a` and `7\\n` red)."""
    table = ["1", "7", "42", "1234567890", "0", "007", "12345678901", "", "7a", "-1", "7\n", "١"]
    assert {v: is_pr_number(v) for v in table} == {
        "1": True,
        "7": True,
        "42": True,
        "1234567890": True,
        "0": False,
        "007": False,
        "12345678901": False,
        "": False,
        "7a": False,
        "-1": False,
        "7\n": False,
        "١": False,
    }


def test_run_annotates_the_shared_runners_failure(monkeypatch, capsys):
    """`_run` re-raises `run`'s failure with `::error::` prepended and prints
    nothing itself: the stderr rides in the message. Reddens on returning `run(args)` with no
    `except` (the prefix is gone), and on writing stderr before raising."""

    def fake_subprocess_run(args, capture_output=False, input=None):
        return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"gh: boom\n")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    with pytest.raises(SystemExit) as exc:
        _run(["gh", "api", "repos/an-org/a-repo"])
    assert str(exc.value) == "::error::command failed (1): gh api repos/an-org/a-repo\ngh: boom"
    assert capsys.readouterr().err == ""


def test_named_lists_cap_items_whole_and_counts_the_rest():
    """Mutation: `>=` for `>` in `named` -- red on the `len == cap` case."""
    assert [named(["a", "b"], 2), named(["a", "b", "c"], 2)] == ["a, b", "a, b, and 1 more"]


@pytest.mark.parametrize(
    ("raw", "names"),
    [
        (
            '[{"stack": "a", "environment": "dev", "x": 1}]',
            [{"stack": "a", "environment": "dev"}],
        ),
        ("[]", []),
        ("", []),
        ("not json", []),
        ('{"stack": "a", "environment": "dev"}', []),
        ('[{"stack": "a"}]', []),
        ('[{"stack": "a", "environment": 1}]', []),
        ('["a / dev"]', []),
    ],
    ids=["names", "empty-list", "empty", "bad-json", "object", "no-env", "int-env", "string"],
)
def test_missing_names_reads_names_or_nothing_and_never_raises(raw, names):
    """Mutations: let bad JSON raise (drop the `except`) -- `empty` and `bad-json` red; drop the
    `environment` type check -- `no-env` and `int-env` red."""
    assert missing_names(raw) == names


_SHA = "0123456789abcdef0123456789abcdef01234567"


def test_is_head_sha_accepts_40_lowercase_hex_only():
    """Mutation: `match` for `fullmatch` -- the 41-character value reds."""
    table = {
        "valid": _SHA,
        "upper": _SHA.upper(),
        "non-hex": "g" * 40,
        "short": _SHA[:39],
        "long": _SHA + "0",
        "none": None,
    }
    assert {k: is_head_sha(v) for k, v in table.items()} == {
        "valid": True,
        "upper": False,
        "non-hex": False,
        "short": False,
        "long": False,
        "none": False,
    }


def test_validate_head_sha_names_its_source_in_the_refusal():
    """Mutation: change the message text in `validate_head_sha`."""
    with pytest.raises(SystemExit) as exc:
        validate_head_sha("../x", "the head")
    assert str(exc.value) == "::error::the head must be a 40-char lowercase hex SHA (got: '../x')"


def test_validate_head_sha_names_shipmate_head_sha_by_default():
    """The default `source` is the variable both apply-detect forms and unlock-detect read.

    Mutation: change `source`'s default -- the whole message differs."""
    with pytest.raises(SystemExit) as exc:
        validate_head_sha("abc123")
    assert str(exc.value) == (
        "::error::SHIPMATE_HEAD_SHA must be a 40-char lowercase hex SHA (got: 'abc123')"
    )


def _recording():
    calls = []

    def run(args):
        calls.append(args)
        return "listing"

    return calls, run


@pytest.mark.parametrize(
    ("jq", "expected"),
    [((), ".check_runs[]"), ((".check_runs[] | {name}",), ".check_runs[] | {name}")],
    ids=["default-jq", "passed-jq"],
)
def test_check_runs_sends_this_whole_argv(jq, expected):
    """Mutations, each run: drop `filter=all`; drop `--paginate`; `per_page=100` ->
    `per_page=30`."""
    calls, run = _recording()
    assert check_runs("o/r", _SHA, *jq, run=run) == "listing"
    assert calls == [
        [
            "gh",
            "api",
            "--paginate",
            f"repos/o/r/commits/{_SHA}/check-runs?filter=all&per_page=100",
            "--jq",
            expected,
        ]
    ]


@pytest.mark.parametrize("sha", ["A" * 40, "a" * 39, "../x"])
def test_check_runs_refuses_a_bad_sha_before_any_call(sha):
    """Mutation: move `validate_head_sha` after the `run` call -- the double records a call."""
    calls, run = _recording()
    with pytest.raises(SystemExit) as exc:
        check_runs("o/r", sha, run=run)
    assert (str(exc.value), calls) == (
        f"::error::head SHA must be a 40-char lowercase hex SHA (got: {sha!r})",
        [],
    )


def test_command_data_escapes_percent_cr_and_lf():
    """Mutations: drop any one `replace`; or replace `%` last, which double-escapes `%0D`."""
    assert command_data("a%0Ab\r\nc%") == "a%250Ab%0D%0Ac%25"


def _raising(code):
    def main():
        raise SystemExit(code)

    return main


def _annotated(main, capsys):
    """(exit code, stderr) of `run_annotated(main)`; exit code None when it returns."""
    try:
        run_annotated(main)
        code = None
    except SystemExit as exc:
        code = exc.code
    return code, capsys.readouterr().err


def test_run_annotated_prints_a_refusal_as_one_escaped_command(capsys):
    """A literal `%0A` or `%25` in a stack path stays verbatim in the annotation, and every line
    of a multi-line refusal reaches it. Mutation: print `code` unescaped -- red."""
    main = _raising("::error::needs a%0Ab\n- 100%25 x")
    assert _annotated(main, capsys) == (1, "::error::needs a%250Ab%0A- 100%2525 x\n")


def test_run_annotated_ends_a_line_at_cr_lf_and_crlf(capsys):
    """`build-matrix`'s `refusal` output takes the first line with `splitlines`, so the
    annotation splits the same way: its first line is the gate description's. Mutation: split
    on `"\\n"` only -- `%0D` appears."""
    main = _raising("::error::first\r\nsecond\rthird")
    assert _annotated(main, capsys) == (1, "::error::first%0Asecond%0Athird\n")


def test_run_annotated_drops_trailing_empty_lines(capsys):
    """A refusal ending in newlines, alone or before another error, prints no trailing `%0A`.
    Mutation: drop the `rstrip` -- red."""
    main = _raising("::error::first\n\n::error::second\n\n")
    assert _annotated(main, capsys) == (1, "::error::first\n::error::second\n")


def test_run_annotated_prints_each_joined_error_as_its_own_command(capsys):
    """The `"\\n".join(errors)` shape of `build-matrix` and `env-config`: each error stays its
    own annotation, carrying the lines under it. Mutation: one chunk for the whole code -- red."""
    main = _raising("::error::first\nmore\n::error::second")
    assert _annotated(main, capsys) == (1, "::error::first%0Amore\n::error::second\n")


def test_run_annotated_puts_a_failed_commands_whole_stderr_in_one_annotation(capsys):
    """A `_run` failure carries the tool's stderr on the lines under its first. Deliberate: the
    annotation is where a reader looks, and doctor's harvest bounds the row it renders.
    Mutation: print `code` unescaped -- red."""
    main = _raising("::error::command failed (1): gh api x\nHTTP 404\nnot found")
    assert _annotated(main, capsys) == (
        1,
        "::error::command failed (1): gh api x%0AHTTP 404%0Anot found\n",
    )


@pytest.mark.parametrize("code", [0, 3, "plain"])
def test_run_annotated_passes_any_other_exit_through(capsys, code):
    """Mutation: treat every `str` code as an annotation -- the `plain` case red."""
    assert _annotated(_raising(code), capsys) == (code, "")


def test_run_annotated_returns_when_main_returns(capsys):
    """Mutation: `raise SystemExit(1)` after `main()` returns -- red."""
    assert _annotated(lambda: None, capsys) == (None, "")


def test_run_annotated_passes_its_arguments_to_main():
    """The arguments after `main` reach `main` unchanged. Mutation: call `main()` without
    `*args` -- red."""
    calls = []
    assert run_annotated(lambda *a: calls.append(a), ["refresh", "x"], "y") is None
    assert calls == [(["refresh", "x"], "y")]
