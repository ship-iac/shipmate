import io
import os
import subprocess
import sys
import types

import _shipmate
import pytest
from _loader import ENGINE
from _shipmate import (
    _run,
    approval_rules,
    cell_names,
    check_runs,
    command_data,
    command_property,
    event_action,
    event_payload,
    gate_check,
    is_head_sha,
    is_pr_number,
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
        (
            "repin",
            ["c", "é"],
            "'é' is not a vX.Y.Z release tag.\n",
        ),
    ],
)
def test_a_script_run_as_a_file_writes_its_refusal_as_utf8(tmp_path, script, argv, refusal):
    """Each refusal runs before any `gh` or `git` call. Mutation: delete `utf8_output()` from
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
        ("", None),
        ("not json", None),
        ('{"stack": "a", "environment": "dev"}', None),
        ('[{"stack": "a"}]', None),
        ('[{"stack": "a", "environment": 1}]', None),
        ('["a / dev"]', None),
    ],
    ids=["names", "empty-list", "empty", "bad-json", "object", "no-env", "int-env", "string"],
)
def test_cell_names_reads_names_or_none_and_never_raises(raw, names):
    """Mutations: let bad JSON raise (drop the `except`) -- `empty` and `bad-json` red; drop the
    `environment` type check -- `no-env` and `int-env` red."""
    assert cell_names(raw) == names


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


def test_check_runs_narrows_to_one_name_url_encoded():
    """A name holds spaces and slashes. Mutations, each run: drop the `name` query; quote the
    name with the default `safe="/"`."""
    calls, run = _recording()
    check_runs("o/r", _SHA, run=run, name="apply / stacks/a / dev")
    assert calls == [
        [
            "gh",
            "api",
            "--paginate",
            (
                f"repos/o/r/commits/{_SHA}/check-runs?filter=all&per_page=100"
                "&check_name=apply%20%2F%20stacks%2Fa%20%2F%20dev"
            ),
            "--jq",
            ".check_runs[]",
        ]
    ]


_BAD_GATEWAY = "::error::command failed (1): gh api x\ngh: Bad Gateway (HTTP 502)\nretry 50%"
#: PROBE: gh's message for a refused connection, captured raw from a run behind a dead proxy.
_REFUSED = (
    "::error::command failed (1): gh api x\n"
    'Get "https://api.github.com/repos/o/r/commits/x/check-runs?filter=all&per_page=100": '
    "proxyconnect tcp: dial tcp 127.0.0.1:9: connect: connection refused"
)


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(_shipmate, "_sleep", slept.append)
    return slept


def _failing(*messages):
    """A call raising each of `messages` in turn, then returning `"x"`; and its call count."""
    calls = []

    def call(*_):
        calls.append(None)
        if len(calls) <= len(messages):
            raise SystemExit(messages[len(calls) - 1])
        return "x"

    return calls, call


@pytest.mark.parametrize("message", [_BAD_GATEWAY, _REFUSED], ids=["502", "connection-refused"])
def test_retried_calls_again_after_a_transient_failure(message, sleeps, capsys):
    """Mutations, each run: drop the `HTTP 5xx` alternative from `TRANSIENT` -> the 502
    re-raises; drop `connection refused` -> the probe's message re-raises; write the warning to
    stdout -> the streams swap."""
    calls, call = _failing(message)
    assert _shipmate.retried(call) == "x"
    head = "%0A".join(message.removeprefix("::error::").splitlines()[:2])
    assert (len(calls), sleeps, capsys.readouterr()) == (2, [2], ("", f"::warning::{head}\n"))


def test_retried_warns_the_command_and_first_stderr_line_whole(sleeps, capsys):
    """Mutations, each run: keep `::error::` -> a nested prefix; print every line -> `retry 50%25`
    trails; skip `command_data` -> a raw newline."""
    _shipmate.retried(_failing(_BAD_GATEWAY)[1])
    assert capsys.readouterr().err == (
        "::warning::command failed (1): gh api x%0Agh: Bad Gateway (HTTP 502)\n"
    )


def test_retried_re_raises_a_4xx_at_once(sleeps, capsys):
    """Mutation: add an `HTTP 4xx` alternative to `TRANSIENT` -> a second call and a sleep."""
    message = "::error::command failed (1): gh api x\ngh: Not Found (HTTP 404)"
    calls, call = _failing(message)
    with pytest.raises(SystemExit) as exc:
        _shipmate.retried(call)
    assert (exc.value.code, len(calls), sleeps, capsys.readouterr()) == (message, 1, [], ("", ""))


def test_retried_gives_up_after_three_attempts_with_the_last_failure(sleeps, capsys):
    """Mutations, each run: `RETRY_DELAYS = ()` (one attempt) -> the first message, one call;
    `RETRY_DELAYS = (2, 5, 9)` -> a fourth call returns `x`."""
    messages = [
        f"::error::command failed (1): gh api x\ngh: Server Error (HTTP 50{i})" for i in (0, 2, 3)
    ]
    calls, call = _failing(*messages)
    with pytest.raises(SystemExit) as exc:
        _shipmate.retried(call)
    assert (exc.value.code, len(calls), sleeps) == (messages[2], 3, [2, 5])
    assert capsys.readouterr().err == (
        "::warning::command failed (1): gh api x%0Agh: Server Error (HTTP 500)\n"
        "::warning::command failed (1): gh api x%0Agh: Server Error (HTTP 502)\n"
    )


def test_check_runs_retries_a_transient_failure_through_the_injected_run(sleeps):
    """The double takes `(args)` alone, as every injected `run` does. Mutation: call
    `(run or _run)(args)` without `retried` -> the 502 raises."""
    calls = []

    def run(args):
        calls.append(args)
        if len(calls) == 1:
            raise SystemExit(_BAD_GATEWAY)
        return "listing"

    assert check_runs("o/r", _SHA, run=run) == "listing"
    argv = [
        "gh",
        "api",
        "--paginate",
        f"repos/o/r/commits/{_SHA}/check-runs?filter=all&per_page=100",
        "--jq",
        ".check_runs[]",
    ]
    assert (calls, sleeps) == ([argv, argv], [2])


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


def test_command_property_also_escapes_colon_and_comma():
    """Mutation: drop the `,` replacement -- red."""
    assert command_property("a%b\r\nc:d,e") == "a%25b%0D%0Ac%3Ad%2Ce"


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
    """The `refusal` output splits lines with the same `splitlines`, so the two surfaces agree
    on where a line ends. Mutation: split on `"\\n"` only -- `%0D` appears."""
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


def _refusal_output(monkeypatch, tmp_path, main, **switch):
    """(exit code, `GITHUB_OUTPUT` bytes) of `run_annotated(main, **switch)`. Bytes, because a
    text read would turn a CR into a line end and hide one written into the value; only the
    platform's own line end (`write_outputs` writes in text mode) is read back as LF."""
    out = tmp_path / "out.txt"
    out.write_bytes(b"")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    with pytest.raises(SystemExit) as exc:
        run_annotated(main, **switch)
    return exc.value.code, out.read_bytes().replace(os.linesep.encode(), b"\n")


def test_run_annotated_writes_the_whole_first_error_as_the_refusal_output(monkeypatch, tmp_path):
    """Joined errors share one output: the first error, prefix removed, its lines stripped and
    joined by a space. Mutations: drop the `write_outputs` call -- empty; keep only the first
    line -- `first`; write the last error -- `second`."""
    main = _raising("::error::first\n  more \n\n::error::second")
    assert _refusal_output(monkeypatch, tmp_path, main, refusal_output=True) == (
        1,
        b"refusal=first more\n",
    )


def test_run_annotated_keeps_a_failed_commands_stderr_in_the_refusal_output(monkeypatch, tmp_path):
    """A `_run` failure's cause is on the lines under its first. Mutation: keep only the first
    line -- the `HTTP 404` cause is lost."""
    main = _raising("::error::command failed (1): gh api x\nHTTP 404\nnot found")
    assert _refusal_output(monkeypatch, tmp_path, main, refusal_output=True) == (
        1,
        b"refusal=command failed (1): gh api x HTTP 404 not found\n",
    )


def test_run_annotated_ends_a_refusal_output_line_at_a_lone_cr(monkeypatch, tmp_path):
    """The output splits lines as the annotation does, so no CR reaches the value. A CRLF and an
    LF end a line too, but their split cannot differ after the strip; the lone CR is what pins
    `splitlines`. Mutation: split on `"\\n"` only -- `second\\rthird` stays inside the value."""
    main = _raising("::error::first\r\nsecond\rthird\n::error::x")
    assert _refusal_output(monkeypatch, tmp_path, main, refusal_output=True) == (
        1,
        b"refusal=first second third\n",
    )


def test_run_annotated_prints_the_annotation_before_it_writes_the_refusal_output(
    monkeypatch, capsys
):
    """A write that fails (`GITHUB_OUTPUT` unset, an `OSError`) cannot cost the annotation.
    Mutation: write the output before printing -- the annotation is missing."""

    def fail(**pairs):
        raise OSError("GITHUB_OUTPUT not writable")

    monkeypatch.setattr(_shipmate, "write_outputs", fail)
    with pytest.raises(OSError):
        run_annotated(_raising("::error::first\nmore"), refusal_output=True)
    assert capsys.readouterr().err == "::error::first%0Amore\n"


def test_run_annotated_cuts_the_refusal_output_at_the_reason_cap(monkeypatch, tmp_path):
    """An env var holding the value must stay under Linux's 128 KiB per-string limit, or the
    step reading it fails at exec. Mutation: drop the `REASON_CAP` cut -- red."""
    main = _raising("::error::" + "x" * 3998 + "\nyz tail")
    assert _refusal_output(monkeypatch, tmp_path, main, refusal_output=True) == (
        1,
        b"refusal=" + b"x" * 3998 + b" y\n",
    )


@pytest.mark.parametrize("code", [1, "plain"])
def test_run_annotated_writes_no_refusal_output_for_another_exit(monkeypatch, tmp_path, code):
    """A crash or a non-refusal exit leaves the comment as it is without a refusal. Mutation:
    write the output before the `is_refusal` check -- red."""
    main = _raising(code)
    assert _refusal_output(monkeypatch, tmp_path, main, refusal_output=True) == (code, b"")


def test_run_annotated_writes_no_refusal_output_without_the_switch(monkeypatch, tmp_path):
    """Mutation: write the output whatever `refusal_output` says -- red."""
    main = _raising("::error::first")
    assert _refusal_output(monkeypatch, tmp_path, main) == (1, b"")


def test_event_payload_degrades_to_empty_dict(tmp_path):
    """Each of these reaches pr-facts' main() as {}, which has neither a pull request nor a
    `pr_number` and so refuses rather than emitting empty facts.

    Mutation: return `payload` without the `isinstance(payload, dict)` check -- the list case
    returns `[1, 2]`."""
    assert event_payload("") == {}
    assert event_payload(str(tmp_path / "absent.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert event_payload(str(bad)) == {}
    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]", encoding="utf-8")
    assert event_payload(str(listy)) == {}


@pytest.mark.parametrize(
    ("body", "action"),
    [
        pytest.param(None, "", id="unset"),
        pytest.param('{"action": "synchronize"}', "synchronize", id="push"),
        pytest.param('{"action": 1}', "", id="not-a-string"),
        pytest.param('{"inputs": {}}', "", id="dispatch"),
    ],
)
def test_event_action_reads_the_payloads_action_or_empty(tmp_path, monkeypatch, body, action):
    """Mutation: read `os.environ["GITHUB_EVENT_PATH"]` (the unset row raises KeyError).
    Mutation: drop the `isinstance(action, str)` check (the not-a-string row returns 1)."""
    if body is None:
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    else:
        path = tmp_path / "event.json"
        path.write_text(body, encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    assert event_action() == action
