"""Tests for `dispatch-body`: one `workflow_dispatch` POST, retried on a transient failure
unless a `shipmate.yml` run created since `t0` may be this dispatch's."""

import json
from datetime import UTC, datetime, timedelta

import _shipmate
import pytest
from _loader import load_script

db = load_script("dispatch-body")

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
#: `t0`, five seconds before `NOW`, as GitHub renders a run's `created_at`.
T0 = "2026-11-07T11:59:55Z"

#: Hand-written whole argv values; never derived from the script under test.
POST = ("gh", "api", "-X", "POST", "repos/acme/demo/actions/workflows/shipmate.yml/dispatches") + (
    "--input",
    "-",
)
LIST = (
    "gh",
    "api",
    "repos/acme/demo/actions/workflows/shipmate.yml/runs?event=workflow_dispatch&per_page=100",
    "--jq",
    ".workflow_runs[] | {created_at} | tojson",
)
BODY = json.dumps(
    {
        "ref": "main",
        "inputs": {"verb": "apply", "ref": "abc123", "pr_number": "42"},
        "return_run_details": True,
    }
)

BAD_GATEWAY = "command failed (1): gh api x\ngh: Bad Gateway (HTTP 502)"
NOT_FOUND = "command failed (1): gh api x\ngh: Not Found (HTTP 404)"
UNPROCESSABLE = "command failed (1): gh api x\ngh: Unexpected inputs provided (HTTP 422)"
FORBIDDEN = "command failed (1): gh api x\ngh: Resource not accessible (HTTP 403)"
ANSWER = '{"workflow_run_id":123}'


def row(created_at, title="shipmate · apply"):
    """One listed run as the fake listing answers it: `--jq` ignored, so the title rides along
    and a stop rule that reads it sees one."""
    return json.dumps({"created_at": created_at, "display_title": title})


@pytest.fixture
def calls(monkeypatch):
    """The ordered log of POSTs (`(argv, stdin)`), listings (`argv`) and sleeps (`("sleep", s)`)
    of one `post`, its env set and its clock fixed at `NOW`."""
    for name, value in {
        "GITHUB_REPOSITORY": "acme/demo",
        "VERB": "apply",
        "REF": "abc123",
        "PR_NUMBER": "42",
        "ENVIRONMENT": "",
        "DISPATCH_REF": "main",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(db, "_now", lambda: NOW)
    log = []
    monkeypatch.setattr(_shipmate, "_sleep", lambda s: log.append(("sleep", s)))
    return log


def answering(monkeypatch, log, posts, listings=()):
    """Patch `db.run`: each POST takes the next of `posts`, each listing the next of `listings`;
    an `str` answer is returned, an exception raised."""
    posts, listings = list(posts), list(listings)

    def run(args, stdin=None):
        if tuple(args) == POST:
            log.append((tuple(args), stdin))
            answer = posts.pop(0)
        else:
            log.append(tuple(args))
            answer = listings.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(db, "run", run)


def post(capsys):
    """`(exit code, stdout, stderr)` of `main()`."""
    try:
        db.main()
        code = 0
    except SystemExit as e:
        code = e.code
    out, err = capsys.readouterr()
    return code, out, err


SENT = (POST, BODY)
SLEPT_2 = ("sleep", 2)
SLEPT_5 = ("sleep", 5)


def test_a_2xx_posts_once_and_prints_the_answer(calls, monkeypatch, capsys):
    """Mutation: call `landed_since` before the first attempt -> a listing precedes the POST."""
    answering(monkeypatch, calls, [ANSWER], [""])
    assert (post(capsys), calls) == ((0, ANSWER, ""), [SENT])


def test_a_4xx_on_the_first_attempt_posts_once_and_exits_1(calls, monkeypatch, capsys):
    """A 4xx cannot have landed, so it is neither listed for nor retried. Mutation: add
    `HTTP 4\\d\\d` to `_shipmate.TRANSIENT` -> a listing and a second POST follow."""
    answering(monkeypatch, calls, [SystemExit(NOT_FOUND), ANSWER], [""])
    code, out, err = post(capsys)
    assert (code, out, calls) == (1, "", [SENT])
    assert err == NOT_FOUND + "\n"


def test_a_transient_failure_lists_then_retries(calls, monkeypatch, capsys):
    """The listing is read after the sleep: a run lists seconds after its POST, so a listing
    before the sleep sees less. Mutations: pass no `before_retry` -> no listing between the
    POSTs; list inside the attempt before raising -> the listing precedes the sleep."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [""])
    assert post(capsys)[:2] == (0, ANSWER)
    assert calls == [SENT, SLEPT_2, LIST, SENT]


def test_a_listed_run_of_this_verb_stops_the_retry(calls, monkeypatch, capsys):
    """Mutation: `>` for `>=` on `created_at` -> the run created at `t0` is missed, retried."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(T0)])
    assert (post(capsys)[0], calls) == (db.MAY_HAVE_STARTED, [SENT, SLEPT_2, LIST])


def test_a_run_created_before_t0_does_not_stop(calls, monkeypatch, capsys):
    """Mutation: drop the time filter -> the older run stops the retry."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row("2026-11-07T11:59:54Z")])
    assert (post(capsys)[0], calls) == (0, [SENT, SLEPT_2, LIST, SENT])


@pytest.mark.parametrize("title", ["shipmate", "custom"])
def test_a_run_without_the_fence_title_stops_the_retry(calls, monkeypatch, capsys, title):
    """A consumer that dropped or edited the fence's `run-name` still lands runs. Mutation: stop
    only on `display_title == f"shipmate · {verb}"` -> the run is missed, retried."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(T0, title)])
    assert (post(capsys)[0], calls) == (db.MAY_HAVE_STARTED, [SENT, SLEPT_2, LIST])


def test_a_run_titled_shipmate_stops_the_retry(calls, monkeypatch, capsys):
    """The literal case beside the parametrization. Mutation: as above."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(T0, "shipmate")])
    assert post(capsys)[0] == db.MAY_HAVE_STARTED


@pytest.mark.parametrize("title", ["shipmate · plan", "shipmate · unlock", "shipmate · auto"])
def test_a_landed_run_with_another_verbs_fence_title_stops_the_retry(
    calls, monkeypatch, capsys, title
):
    """The title is consumer-authored, so another verb's title cannot prove a run is not this
    dispatch's. Mutation: skip a run titled exactly another verb's fence title -> retried."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(T0, title)])
    assert (post(capsys)[0], calls) == (db.MAY_HAVE_STARTED, [SENT, SLEPT_2, LIST])


def test_a_landed_plan_titled_run_stops_an_apply_retry(calls, monkeypatch, capsys):
    """The literal case beside the parametrization. Mutation: as above."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(T0, "shipmate · plan")])
    assert post(capsys)[0] == db.MAY_HAVE_STARTED


def test_the_listing_argv_is_exact(monkeypatch):
    """Mutations: add `--paginate`; drop `event=workflow_dispatch`; add `display_title` back to the
    projection -> the argv differs."""
    seen = []
    monkeypatch.setattr(_shipmate, "_sleep", lambda s: None)
    landed = db.landed_since("acme/demo", NOW, run=lambda args: seen.append(args) or "")
    assert landed is False
    assert seen == [list(LIST)]


@pytest.mark.parametrize(
    "listing", [SystemExit(FORBIDDEN), '{"created_at": "x"}', "[]", '{"created_at": null}']
)
def test_a_failed_or_malformed_listing_is_undetermined(calls, monkeypatch, capsys, listing):
    """A listing that cannot show no run started stops the retry. Mutation: treat a failed listing
    as "no run" and retry -> a second POST, exit 0."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [listing])
    assert (post(capsys)[0], calls) == (db.UNDETERMINED, [SENT, SLEPT_2, LIST])


def test_a_created_at_without_a_zone_is_undetermined(calls, monkeypatch, capsys):
    """A naive time cannot be compared with `t0`, and the log says why. Mutation: drop the
    `tzinfo` check -> the comparison's `TypeError` prints in place of the row."""
    answering(
        monkeypatch,
        calls,
        [SystemExit(BAD_GATEWAY), ANSWER],
        ['{"created_at": "2026-11-07T11:59:55"}'],
    )
    code, _, err = post(capsys)
    assert (code, calls) == (db.UNDETERMINED, [SENT, SLEPT_2, LIST])
    assert err.endswith(
        "\nlisting shipmate.yml runs failed: ValueError: listed run created_at without a zone: "
        '{"created_at": "2026-11-07T11:59:55"}\n'
    )


def test_a_listing_that_raises_anything_is_undetermined(calls, monkeypatch, capsys):
    """Any failure inside the listing leaves the question open. Mutation: catch only
    `(SystemExit, ValueError)` -> the `OSError` escapes `post`."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [OSError("disk gone")])
    code, _, err = post(capsys)
    assert (code, calls) == (db.UNDETERMINED, [SENT, SLEPT_2, LIST])
    assert err.endswith("\nlisting shipmate.yml runs failed: OSError: disk gone\n")


def test_a_failed_listing_prints_its_failure(calls, monkeypatch, capsys):
    """gh's failure prints alone; another exception keeps its type name
    (`test_a_listing_that_raises_anything_is_undetermined`). Mutations:
    drop the listing failure's print -> stderr ends at the retry notice; print the type name for
    a `SystemExit` too -> `SystemExit: ` before gh's text."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [SystemExit(FORBIDDEN)])
    assert post(capsys)[2].endswith("\nlisting shipmate.yml runs failed: " + FORBIDDEN + "\n")


def test_three_transient_failures_are_undetermined(calls, monkeypatch, capsys):
    """The third attempt may have landed and nothing lists after it. Mutation: exit 1 on the
    re-raised transient failure -> exit 1."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY)] * 3, ["", ""])
    code, _, err = post(capsys)
    assert (code, calls) == (
        db.UNDETERMINED,
        [SENT, SLEPT_2, LIST, SENT, SLEPT_5, LIST, SENT],
    )
    assert err.endswith(BAD_GATEWAY + "\n")


def test_a_4xx_after_a_transient_failure_is_undetermined(calls, monkeypatch, capsys):
    """A later 4xx proves nothing about the earlier 502. Mutation: classify only the final
    exception -> exit 1."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), SystemExit(UNPROCESSABLE)], [""])
    code, _, err = post(capsys)
    assert (code, calls) == (db.UNDETERMINED, [SENT, SLEPT_2, LIST, SENT])
    assert err.endswith(UNPROCESSABLE + "\n")


@pytest.mark.parametrize(("ago", "code"), [(4, 3), (6, 0)])
def test_t0_is_five_seconds_before_the_first_attempt(calls, monkeypatch, capsys, ago, code):
    """Mutation: drop the 5 s -> the run created 4 s before the first attempt is missed."""
    created = (NOW - timedelta(seconds=ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(created)])
    assert post(capsys)[0] == code


@pytest.mark.parametrize("argv", [["post"], ["--help"]])
def test_an_argument_refuses_before_any_call(calls, monkeypatch, argv):
    """Mutation: drop the `if argv:` refusal -> `--help` sends the dispatch."""
    answering(monkeypatch, calls, [ANSWER])
    with pytest.raises(SystemExit) as exc:
        db.main(argv)
    assert exc.value.code == "::error::usage: dispatch-body"
    assert calls == []
