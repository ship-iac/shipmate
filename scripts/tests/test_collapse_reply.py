"""Tests for scripts/collapse-reply: minimizing the started reply that links this run."""

import json
import sys

import pytest
from _loader import load_script

cr = load_script("collapse-reply")
rc = load_script("reply-comment")

#: This run, and the comment-ops run whose footer the reply carries.
_ENV = {
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "org/repo",
    "GITHUB_RUN_ID": "7777",
}
_COMMENT_RUN = "https://github.com/org/repo/actions/runs/5555"

#: The listing and minimize argv, hand-written.
_LIST = [
    "gh",
    "api",
    "--paginate",
    "repos/org/repo/issues/42/comments?per_page=100",
    "--jq",
    '.[] | select(.user.login == "github-actions[bot]") | {node_id, body}',
]


def _minimize(node):
    return [
        "gh",
        "api",
        "graphql",
        "-f",
        (
            "query=mutation($id: ID!) { minimizeComment(input: {subjectId: $id, classifier: "
            "OUTDATED}) { minimizedComment { isMinimized } } }"
        ),
        "-f",
        f"id={node}",
    ]


@pytest.fixture
def env(monkeypatch):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)


def _reply(node, verb, run_id):
    """A listing line for a real started reply from `reply-comment`, linking run `run_id`."""
    body = rc.body(verb, "", "started", rc.started_text(verb, run_id), _COMMENT_RUN)
    return json.dumps({"node_id": node, "body": body})


def test_only_a_reply_linking_this_run_for_this_verb_matches(env):
    """Mutations: compare `expected in body` instead of line equality (`quoted` matches); drop
    the run id from the expected line (`started_text(verb, "")`: the link-less reply matches and
    neither linking reply does)."""
    quoted = "see `" + rc.VERDICTS["started"] + rc.started_text("plan", "7777") + "`"
    lines = [
        _reply("this", "plan", "7777"),
        _reply("other-run", "plan", "8888"),
        _reply("other-verb", "apply", "7777"),
        _reply("no-link", "plan", ""),
        json.dumps({"node_id": "quoted", "body": quoted}),
        "",
        _reply("again", "plan", "7777"),
    ]
    assert cr.matches(lines, "plan", "7777") == ["this", "again"]


def test_a_run_id_that_is_not_one_matches_no_link_less_reply(env):
    """Mutation: drop the `is_run_id` check (the link-less reply matches)."""
    assert cr.matches([_reply("no-link", "plan", "")], "plan", "") == []


def _main(monkeypatch, *argv, pr="42", listing="", fail=()):
    """Run `main` in-process; `run` answers the listing with `listing` and raises for an argv in
    `fail`. Return (exit code, argv of each `run` call)."""
    calls = []

    def fake_run(args):
        calls.append(args)
        if args in fail:
            raise SystemExit("command failed (1): gh api\nHTTP 502 (50%)")
        return listing if args == _LIST else "{}"

    monkeypatch.setattr(cr, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["collapse-reply", *argv])
    for key, value in {**_ENV, "PR_NUMBER": pr, "SHIPMATE_REPLY_VERB": "plan"}.items():
        monkeypatch.setenv(key, value)
    try:
        cr.main()
    except SystemExit as exc:
        return exc.code, calls
    return 0, calls


def test_main_minimizes_each_match_as_outdated(monkeypatch, capsys, env):
    """The whole argv of both calls, hand-written.

    Mutations: classifier `RESOLVED`; drop `--paginate`; select on `.user.type == "Bot"`.
    """
    listing = "\n".join([_reply("this", "plan", "7777"), _reply("other", "plan", "8888")]) + "\n"
    assert _main(monkeypatch, listing=listing) == (0, [_LIST, _minimize("this")])
    assert capsys.readouterr().out == "collapsed 1 started reply(ies)\n"


def test_a_failed_listing_warns_and_minimizes_nothing(monkeypatch, capsys, env):
    """Mutations: let the listing's SystemExit escape `main`; drop the `command_data` escape
    (`50%` unescaped); drop the one-line join (a second raw line)."""
    assert _main(monkeypatch, fail=(_LIST,)) == (0, [_LIST])
    assert capsys.readouterr().out == (
        "::warning::could not list the comments of pull request #42; the reply stays: "
        "command failed (1): gh api | HTTP 502 (50%25)\n"
    )


def test_an_unparsable_listing_line_warns_with_the_line_cut_and_minimizes_nothing(
    monkeypatch, capsys, env
):
    """Mutations: skip an unparsable line instead of raising (`this` is minimized); report it
    with the listing-failed text; `LINE_CUT = 300` (the whole line is quoted)."""
    listing = _reply("this", "plan", "7777") + "\nnot json " + "x" * 300 + "\n"
    assert _main(monkeypatch, listing=listing) == (0, [_LIST])
    assert capsys.readouterr().out == (
        "::warning::could not parse a comment of pull request #42; the started reply was not "
        "collapsed: 'not json " + "x" * 191 + "'\n"
    )


def test_a_failed_minimize_warns_and_the_next_match_is_still_minimized(monkeypatch, capsys, env):
    """Mutations: `break` or `return` instead of `continue` (the second minimize is missing)."""
    listing = "\n".join([_reply("first", "plan", "7777"), _reply("second", "plan", "7777")])
    code, calls = _main(monkeypatch, listing=listing, fail=(_minimize("first"),))
    assert (code, calls) == (0, [_LIST, _minimize("first"), _minimize("second")])
    assert capsys.readouterr().out == (
        "::warning::could not collapse a started reply on pull request #42: command failed (1): "
        "gh api | HTTP 502 (50%25)\ncollapsed 1 started reply(ies)\n"
    )


@pytest.mark.parametrize(
    ("pr", "shown"),
    [("abc", "abc"), ("", ""), ("0", "0"), ("1/x", "1/x"), ("4\n::error::x", "4 | ::error::x")],
)
def test_a_value_that_is_not_a_pull_request_number_warns_and_calls_nothing(
    monkeypatch, capsys, env, pr, shown
):
    """`PR_NUMBER` is bound into the API path; a failed collapse is a warning, never a failed
    step. Mutations: drop the check (a listing call); `sys.exit` with `::error::` instead of the
    warning (a nonzero exit); print the value raw (the newline case starts a second command)."""
    assert _main(monkeypatch, pr=pr) == (0, [])
    assert capsys.readouterr().out == (
        f"::warning::PR_NUMBER must be a pull request number (got: {shown}); the started reply "
        "was not collapsed\n"
    )


def test_any_argument_warns_before_any_call(monkeypatch, capsys, env):
    """Mutations: delete the argv check (a listing call); `sys.exit` with `::error::` instead of
    the warning."""
    assert _main(monkeypatch, "x") == (0, [])
    assert capsys.readouterr().out == (
        "::warning::usage: collapse-reply; the started reply was not collapsed\n"
    )
