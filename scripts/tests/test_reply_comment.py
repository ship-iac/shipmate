"""Tests for scripts/reply-comment: the refusal, failure and notice bodies the bash sites post."""

import os
import subprocess
import sys

import pytest
from _loader import SCRIPTS, load_script
from _shipmate import run_annotated

rc = load_script("reply-comment")

_RUN_URL = "https://github.com/org/repo/actions/runs/7777"
_FOOTER = f"[comment run]({_RUN_URL}). Comment `shipmate help` for the available commands."


def _main(**reply):
    """Run the script as a subprocess. It installs no gh stub, so only refusals that exit before
    any `gh` call may use it."""
    env = {
        **os.environ,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_RUN_ID": "7777",
        "PR_NUMBER": "",
        **{f"SHIPMATE_REPLY_{k.upper()}": v for k, v in reply.items()},
    }
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "reply-comment")],
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def test_a_refusal_is_header_verdict_and_footer():
    """Mutations: drop the blank line between the verdict and the footer; pass `hint=False`
    for every outcome."""
    assert rc.body("unlock", "dev-eu", "refused", "no", _RUN_URL) == (
        f"### shipmate unlock dev-eu\n\n🔴 refused: no\n\n{_FOOTER}"
    )


def test_a_failure_says_failed_not_refused():
    """Mutation: map `failed` to the `refused` verdict."""
    assert rc.body("doctor", "", "failed", "no token", _RUN_URL) == (
        f"### shipmate doctor\n\n🔴 failed: no token\n\n{_FOOTER}"
    )


def test_a_notice_carries_the_white_circle_and_no_hint():
    """Mutations: give `notice` the 🔴 circle; pass `hint=True` for every outcome."""
    assert rc.body("apply", "dev-eu", "notice", "dev-eu: ungated", _RUN_URL) == (
        f"### shipmate apply dev-eu\n\n⚪ dev-eu: ungated\n\n[comment run]({_RUN_URL})"
    )


def test_an_empty_verb_drops_the_env_from_the_header():
    """Mutation: keep the env in `header` when the verb is empty."""
    assert rc.body("", "dev-eu", "refused", "x", _RUN_URL) == (
        f"### shipmate\n\n🔴 refused: x\n\n{_FOOTER}"
    )


def test_the_header_env_is_escaped_and_the_text_is_not():
    """Mutation: drop `_md_escape` from the header's env."""
    assert rc.body("apply", "a<b", "refused", "`x` <y>", _RUN_URL) == (
        f"### shipmate apply a&lt;b\n\n🔴 refused: `x` <y>\n\n{_FOOTER}"
    )


def test_an_unknown_outcome_exits_non_zero_and_prints_nothing():
    """The refusal is the whole stderr, and stdout stays empty.

    Mutation: default an unknown outcome to `refused`.
    """
    result = _main(verb="apply", outcome="refuse", text="x")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "::error::SHIPMATE_REPLY_OUTCOME must be refused, failed, notice or started (got: refuse)\n"
    )


def test_a_multi_line_pr_number_refusal_prints_one_escaped_annotation():
    """`dispatch` hands `PR_NUMBER` through unvalidated, so its refusal goes through
    `run_annotated`. Mutation: print the refusal to stderr and `sys.exit(1)` -- two raw lines."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "reply-comment")],
        env={**os.environ, "SHIPMATE_REPLY_OUTCOME": "refused", "PR_NUMBER": "4\n::warning::x"},
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        1,
        "",
        "::error::PR_NUMBER must be a pull request number (got: 4%0A::warning::x)\n",
    )


def _post(monkeypatch, *argv, pr="42", outcome="refused"):
    """Run `main` in-process with `argv`; return (exit code, captured `(argv, stdin)` calls)."""
    calls = []
    monkeypatch.setattr(rc, "run", lambda args, stdin=None: calls.append((args, stdin)) or "{}")
    monkeypatch.setattr(sys, "argv", ["reply-comment", *argv])
    for key, value in {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_RUN_ID": "7777",
        "PR_NUMBER": pr,
        "SHIPMATE_REPLY_VERB": "apply",
        "SHIPMATE_REPLY_ENV": "dev-eu",
        "SHIPMATE_REPLY_OUTCOME": outcome,
        "SHIPMATE_REPLY_TEXT": "no",
    }.items():
        monkeypatch.setenv(key, value)
    try:
        rc.main()
    except SystemExit as exc:
        return exc.code, calls
    return 0, calls


def test_main_posts_the_body_to_the_pull_request_and_prints_nothing(monkeypatch, capsys):
    """The whole argv and stdin, hand-written.

    Mutations: take the issue number from `GITHUB_RUN_ID`; drop `-X POST`; `print(text); return`
    before the post.
    """
    code, calls = _post(monkeypatch)
    assert code == 0
    assert calls == [
        (
            ["gh", "api", "-X", "POST", "repos/org/repo/issues/42/comments", "-F", "body=@-"],
            f"### shipmate apply dev-eu\n\n🔴 refused: no\n\n{_FOOTER}",
        )
    ]
    assert capsys.readouterr().out == ""


def test_an_unknown_outcome_posts_nothing(monkeypatch):
    """Mutation: post before validating the outcome."""
    assert _post(monkeypatch, outcome="refuse") == (
        "::error::SHIPMATE_REPLY_OUTCOME must be refused, failed, notice or started (got: refuse)",
        [],
    )


@pytest.mark.parametrize("pr", ["1; x", "", "0", "007"])
def test_a_value_that_is_not_a_pull_request_number_posts_nothing(monkeypatch, pr):
    """`PR_NUMBER` is bound into the API path, and dispatch passes its input unvalidated.

    Mutations: drop the check; check with `isdigit()` (`0` and `007` red); move the check after
    the `run`.
    """
    code, calls = _post(monkeypatch, pr=pr)
    assert code not in (0, None)
    assert calls == []


@pytest.mark.parametrize("argv", [["--post"], ["--psot"], ["--post", "x"], ["x"]])
def test_any_argument_is_refused_before_any_work(monkeypatch, capsys, argv):
    """Mutations: accept `--post` as a no-op (`["--post"]` red); delete the argv check (every
    case then posts, exit 0)."""
    assert _post(monkeypatch, *argv) == ("::error::usage: reply-comment", [])
    assert capsys.readouterr().out == ""


_STARTED_RUN = "https://github.com/org/repo/actions/runs/38481239914"
_NOT_LISTED = "started the apply run; it is listed in this repository's Actions tab"


def _run_env(monkeypatch):
    for key, value in {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_RUN_ID": "7777",
    }.items():
        monkeypatch.setenv(key, value)


def test_a_started_run_is_linked_by_a_url_built_from_its_id(monkeypatch):
    """The link is built from the id, on this repository.

    Mutations: link `current_run_url()` (this run) instead of the id's run; drop the link.
    """
    _run_env(monkeypatch)
    assert rc.started_text("apply", "38481239914") == (f"started [the apply run]({_STARTED_RUN})")


@pytest.mark.parametrize(
    "run_id",
    ["", "abc", "123456789012345678901", "12\n", "-1", "1) [x](https://evil.example"],
)
def test_a_started_run_without_a_run_id_names_no_link(monkeypatch, run_id):
    """Only 1 to 20 digits link: an id is bound into a markdown link.

    Mutations: widen `_RUN_ID` to `[0-9]+` (the 21-digit case links); match with `match`
    instead of `fullmatch` (the trailing-newline, 21-digit and breakout cases link); drop the
    `is_run_id` check (every case links).
    """
    _run_env(monkeypatch)
    assert rc.started_text("apply", run_id) == _NOT_LISTED


def test_a_started_run_carries_the_white_circle_and_no_hint():
    """Mutation: give `started` the help hint (`hint=outcome != "notice"`)."""
    assert rc.body("plan", "", "started", "started x", _RUN_URL) == (
        f"### shipmate plan\n\n⚪ started x\n\n[comment run]({_RUN_URL})"
    )


def test_main_posts_the_started_text_and_ignores_the_reply_text(monkeypatch):
    """`started` reads the run id, not `SHIPMATE_REPLY_TEXT`.

    Mutations: read `SHIPMATE_REPLY_TEXT` for `started` too; read the id from another variable.
    """
    monkeypatch.setenv("SHIPMATE_REPLY_RUN_ID", "38481239914")
    code, calls = _post(monkeypatch, outcome="started")
    body = (
        f"### shipmate apply dev-eu\n\n⚪ started [the apply run]({_STARTED_RUN})"
        f"\n\n[comment run]({_RUN_URL})"
    )
    assert code == 0
    assert calls == [
        (["gh", "api", "-X", "POST", "repos/org/repo/issues/42/comments", "-F", "body=@-"], body)
    ]


def test_a_failed_post_names_the_pull_request_and_not_the_body(monkeypatch, capsys):
    """`run`'s failure message quotes the argv, so the body travels on stdin. The exit is
    unannotated, so `run_annotated` passes it through, and on one line, so no raw stderr line
    can start a workflow command.

    Mutations: post with `-f body={text}` and no stdin -- the body appears in the message and
    stdin is empty; prefix the message with `::error::` -- an annotation on stderr; drop the
    `" | ".join` -- gh's stderr on its own lines.
    """
    calls = []
    failed = subprocess.CompletedProcess([], 1, b"", b"HTTP 403: forbidden\nsecond line")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(k.get("input")) or failed)
    monkeypatch.setattr(sys, "argv", ["reply-comment"])
    for key, value in {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_RUN_ID": "7777",
        "PR_NUMBER": "42",
        "SHIPMATE_REPLY_OUTCOME": "refused",
        "SHIPMATE_REPLY_TEXT": "body-only-text",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit) as exc:
        run_annotated(rc.main)
    assert (exc.value.code, capsys.readouterr().err) == (
        (
            "could not post the reply on pull request #42: command failed (1): gh api -X POST "
            "repos/org/repo/issues/42/comments -F body=@- | HTTP 403: forbidden | second line"
        ),
        "",
    )
    assert "body-only-text" not in str(exc.value.code)
    assert len(calls) == 1 and b"body-only-text" in calls[0]
