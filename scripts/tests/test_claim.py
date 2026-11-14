"""`scripts/claim`: which answer of the rocket POST makes a run `owned`, `taken` or failed, and
which runs never POST. Each outcome is compared whole: the step outputs, the summary, the POSTs
sent and the reply posted."""

import pytest
from _loader import load_script

cl = load_script("claim")

_POST = [
    "gh",
    "api",
    "--include",
    "-X",
    "POST",
    "repos/o/r/issues/comments/123/reactions",
    "-f",
    "content=rocket",
]
_REPLY = (
    "### shipmate apply dev-eu\n\n🔴 failed: could not claim this command for its run, so the "
    "command was not started. Comment it again.\n\n[comment run](https://github.com/o/r/actions/"
    "runs/50). Comment `shipmate help` for the available commands."
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Run `main` against a recorded POST answer; returns the recorder. Set `answer` (gh's stdout)
    or `fail` (the `SystemExit` text of a failed POST) before calling `go`."""
    out, summary = tmp_path / "out", tmp_path / "summary"
    out.write_text("")
    summary.write_text("")
    for k, v in {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": "50",
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_ACTOR": "shipmate-iac[bot]",
        "COMMENT_ID": "123",
        "PR_NUMBER": "7",
        "SHIPMATE_APP_SLUG": "shipmate-iac",
        "SHIPMATE_REPLY_VERB": "apply",
        "SHIPMATE_REPLY_ENV": "dev-eu",
    }.items():
        monkeypatch.setenv(k, v)

    class Rec:
        answer, fail = "HTTP/2.0 201 Created\nContent-Type: x\r\n\r\n{}", None
        posts, replies = [], []

        def outputs(self):
            return out.read_text()

        def summary(self):
            return summary.read_text()

        def go(self, *argv):
            try:
                cl.run_annotated(cl.main, argv)
            except SystemExit as e:
                return e.code
            return 0

    rec = Rec()

    def run(args, secrets=(), stdin=None, *, env=None):
        rec.posts.append(args)
        if rec.fail:
            raise SystemExit(rec.fail)
        return rec.answer

    monkeypatch.setattr(cl, "run", run)
    monkeypatch.setattr(cl.rc, "post", lambda pr, text: rec.replies.append((pr, text)))
    return rec


def test_a_201_owns_the_comment(env):
    """The first rocket is this run's: it does the work. Mutations: compare against `"200"` in the
    owned branch; write `state=taken` there; drop the `write_outputs` call."""
    assert env.go() == 0
    assert env.posts == [_POST]
    assert env.outputs() == "state=owned\n"
    assert env.summary() == ""
    assert env.replies == []


def test_a_200_is_taken_and_writes_nothing_on_the_pull_request(env):
    """Another run of the comment created the rocket first. Mutations: treat `"200"` as owned;
    post the not-started reply on it; drop the summary line; exit nonzero."""
    env.answer = "HTTP/2.0 200 OK\nContent-Type: x\r\n\r\n{}"
    assert env.go() == 0
    assert env.posts == [_POST]
    assert env.outputs() == "state=taken\n"
    assert env.summary() == "Another run of this comment owns it, so this run does no work.\n"
    assert env.replies == []


@pytest.mark.parametrize(
    ("answer", "fail", "why"),
    [
        ("HTTP/2.0 202 Accepted\n\r\n{}", None, "HTTP 202"),
        ("not a status line\n", None, "an unreadable answer"),
        ("", None, "an unreadable answer"),
        (None, "command failed (1): gh api\nHTTP 502", "command failed (1): gh api | HTTP 502"),
    ],
    ids=["202", "no-status-line", "empty", "post-failed"],
)
def test_any_other_answer_replies_and_fails(env, capsys, answer, fail, why):
    """Anything but 201 or 200 starts nothing and says to comment again, sent once: a retried POST
    that had landed reads 200 and would leave the comment claimed by a run doing nothing.
    Mutations: return on an unknown status; drop the reply; wrap the POST in `retried`."""
    env.answer, env.fail = answer, fail
    assert env.go() == 1
    assert env.posts == [_POST]
    assert env.outputs() == ""
    assert env.replies == [("7", _REPLY)]
    assert capsys.readouterr().err == f"::error::claiming comment 123 failed: {why}\n"


def test_a_status_line_below_the_first_is_not_the_status(env):
    """Only gh's first line is the status; a header or the body can carry the same text.
    Mutation: take the first line anywhere in the answer that matches the pattern."""
    env.answer = "Content-Type: x\nHTTP/2.0 201 Created\r\n\r\n{}"
    assert env.go() == 1
    assert env.outputs() == ""
    assert env.replies == [("7", _REPLY)]


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("GITHUB_ACTOR", "octocat"),
        ("GITHUB_ACTOR", "other-app[bot]"),
        ("SHIPMATE_APP_SLUG", ""),
        ("COMMENT_ID", ""),
        ("COMMENT_ID", "0123"),
        ("COMMENT_ID", "12 3"),
        ("PR_NUMBER", "x"),
    ],
)
def test_a_run_not_from_the_app_or_malformed_fails_without_a_post(env, monkeypatch, var, value):
    """Only the App dispatches a commented run, and a malformed id is bound into a path. Mutations:
    drop the actor check; compare the actor with `endswith('[bot]')`; replace `not slug or` with
    `slug and` (the empty-slug row passes the actor check); drop the `_COMMENT_ID` check; drop
    the pull request number check."""
    monkeypatch.setenv(var, value)
    assert env.go() == 1
    assert env.posts == []
    assert env.replies == []
    assert env.outputs() == ""


def test_an_empty_slug_admits_no_actor(env, monkeypatch):
    """A failed mint leaves the slug empty, and `f"{slug}[bot]"` is then `[bot]`. Mutation: delete
    `not slug or` from the actor check."""
    monkeypatch.setenv("SHIPMATE_APP_SLUG", "")
    monkeypatch.setenv("GITHUB_ACTOR", "[bot]")
    assert env.go() == 1
    assert env.posts == []
    assert env.outputs() == ""


def test_display_reacts_with_a_valid_id(env):
    """Unlock's rocket gates nothing: no output, exit 0. Mutation: skip the POST in display."""
    env.answer = "HTTP/2.0 201 Created\n\r\n{}"
    assert env.go("--display") == 0
    assert env.posts == [_POST]
    assert env.outputs() == ""


@pytest.mark.parametrize("value", ["", "0", "abc", "1" * 21])
def test_display_writes_nothing_for_a_malformed_id(env, monkeypatch, value):
    """Mutation: drop the `_COMMENT_ID` check in display."""
    monkeypatch.setenv("COMMENT_ID", value)
    assert env.go("--display") == 0
    assert env.posts == []


def test_display_only_warns_when_the_post_fails(env, capsys):
    """A red unlock `guard` would skip the recovery verb. Mutation: let the POST failure raise."""
    env.fail = "command failed (1): gh api\nHTTP 403"
    assert env.go("--display") == 0
    assert capsys.readouterr().out == (
        "::warning::could not mark the command started: command failed (1): gh api | HTTP 403\n"
    )


def test_usage(env):
    """Mutation: accept any argument as display."""
    assert env.go("--other") == 1
    assert env.posts == []
