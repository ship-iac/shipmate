"""Tests for `dispatch-body`: one `workflow_dispatch` POST, retried on a transient failure
unless a listed `shipmate.yml` run may be this dispatch's: for a comment-less dispatch any
`workflow_dispatch` run created since `t0`, for a commented one the App's run of this command."""

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
        "inputs": {"verb": "auto", "ref": "abc123", "pr_number": "42"},
        "return_run_details": True,
    }
)

BAD_GATEWAY = "command failed (1): gh api x\ngh: Bad Gateway (HTTP 502)"
NOT_FOUND = "command failed (1): gh api x\ngh: Not Found (HTTP 404)"
UNPROCESSABLE = "command failed (1): gh api x\ngh: Unexpected inputs provided (HTTP 422)"
FORBIDDEN = "command failed (1): gh api x\ngh: Resource not accessible (HTTP 403)"
ANSWER = '{"workflow_run_id":123}'
HEAD = "0123456789abcdef0123456789abcdef01234567"
PLAN_TITLE = "shipmate · plan pr=42 ref= comment="


def row(created_at, title=f"shipmate · apply pr=42 ref={HEAD} comment="):
    """One listed run as the fake listing answers it: `--jq` ignored, so the title rides along
    and a stop rule that reads it sees one."""
    return json.dumps({"created_at": created_at, "display_title": title})


@pytest.fixture
def calls(monkeypatch):
    """The ordered log of POSTs (`(argv, stdin)`), listings (`argv`) and sleeps (`("sleep", s)`)
    of one comment-less `auto` `post`, its env set and its clock fixed at `NOW`."""
    for name, value in {
        "GITHUB_REPOSITORY": "acme/demo",
        "VERB": "auto",
        "REF": "abc123",
        "PR_NUMBER": "42",
        "ENVIRONMENT": "",
        "DISPATCH_REF": "main",
        "SHIPMATE_APP_SLUG": "shipmate-app",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("COMMENT_ID", raising=False)
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
    answering(monkeypatch, calls, [SystemExit(FORBIDDEN), ANSWER], [""])
    code, out, err = post(capsys)
    assert (code, out, calls) == (1, "", [SENT])
    assert err == FORBIDDEN + "\n"


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
    only on a `display_title` starting `f"shipmate · {verb} "` -> the run is missed, retried."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(T0, title)])
    assert (post(capsys)[0], calls) == (db.MAY_HAVE_STARTED, [SENT, SLEPT_2, LIST])


def test_a_run_titled_shipmate_stops_the_retry(calls, monkeypatch, capsys):
    """The literal case beside the parametrization. Mutation: as above."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(T0, "shipmate")])
    assert post(capsys)[0] == db.MAY_HAVE_STARTED


@pytest.mark.parametrize(
    "title",
    [
        PLAN_TITLE,
        f"shipmate · unlock pr= ref={HEAD} comment=",
        f"shipmate · auto pr=42 ref={HEAD} comment=",
    ],
)
def test_a_landed_run_with_another_verbs_dispatch_title_stops_the_retry(
    calls, monkeypatch, capsys, title
):
    """The title is consumer-authored, so another verb's title cannot prove a run is not this
    dispatch's. Mutation: skip a run whose title is another verb's dispatched-run title ->
    retried."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(T0, title)])
    assert (post(capsys)[0], calls) == (db.MAY_HAVE_STARTED, [SENT, SLEPT_2, LIST])


def test_a_landed_plan_titled_run_stops_an_apply_retry(calls, monkeypatch, capsys):
    """The literal case beside the parametrization. Mutation: as above."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [row(T0, PLAN_TITLE)])
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


def test_a_skew_answer_after_a_transient_failure_prints_no_hint(calls, monkeypatch, capsys):
    """A later 422 does not show that no run started, so exit 4 carries no remedy. Mutation:
    print `skew_hint` before the `UNDETERMINED` exit -> stdout carries it."""
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), SystemExit(UNPROCESSABLE)], [""])
    assert post(capsys)[:2] == (db.UNDETERMINED, "")


# The skew remedy (exit 1): stdout carries it for the reply, stderr annotates it.

#: Hand-written whole remedies.
INPUTS_HINT = (
    "This repository's .github/workflows/shipmate.yml does not declare every input this "
    "engine's dispatch sends, so it is older than the engine pin. Write the published file over "
    "it with `python <engine>/scripts/repin <consumer> <tag> --write-fence`."
)
LAYOUT_HINT = (
    "This repository has no .github/workflows/shipmate.yml, or that file declares no "
    "workflow_dispatch trigger. docs/getting-started.md publishes it; re-pinning cannot create it."
)
NO_TRIGGER = (
    "command failed (1): gh api x\n"
    "gh: Workflow does not have 'workflow_dispatch' trigger (HTTP 422)"
)


def test_an_auto_dispatch_to_a_fence_without_its_inputs_names_repin(calls, monkeypatch, capsys):
    """The auto dispatch prints the remedy too, since the classifier lives here. Mutations: return
    the layout hint for `Unexpected inputs provided`; drop the `::error::` print -> stderr ends at
    gh's message; drop the stdout write -> the reply cannot carry it."""
    answering(monkeypatch, calls, [SystemExit(UNPROCESSABLE)])
    assert (post(capsys), calls) == (
        (1, INPUTS_HINT, f"{UNPROCESSABLE}\n::error::{INPUTS_HINT}\n"),
        [SENT],
    )


@pytest.mark.parametrize("answer", [NOT_FOUND, NO_TRIGGER])
def test_a_missing_file_or_trigger_prints_the_layout_hint(calls, monkeypatch, capsys, answer):
    """Mutations: drop either `_LAYOUT_ANSWERS` entry -> its shape prints no hint."""
    answering(monkeypatch, calls, [SystemExit(answer)])
    assert post(capsys) == (1, LAYOUT_HINT, f"{answer}\n::error::{LAYOUT_HINT}\n")


@pytest.mark.parametrize(
    "message",
    [
        "command failed (1): gh api x\ngh: Required input 'plan_run_id' not provided (HTTP 422)",
        "command failed (1): gh api Unexpected inputs provided HTTP 404\ngh: Forbidden (HTTP 403)",
    ],
)
def test_another_failure_has_no_skew_hint(message):
    """A 422 about another input is not skew, and the argv line is not gh's answer. Mutations:
    match any `HTTP 422`; read the whole message, argv line included."""
    assert db.skew_hint(message) == ""


# A commented dispatch: the retry stops on this command's run, dispatched by the App.

SLUG = "shipmate-app"
#: Hand-written whole listing argv values.
JQ = ".workflow_runs[] | {created_at, event, display_title, actor: .actor.login} | tojson"
PAGE_1 = ("gh", "api", "repos/acme/demo/actions/workflows/shipmate.yml/runs?per_page=100&page=1")
PAGE_1 += ("--jq", JQ)
PAGE_2 = ("gh", "api", "repos/acme/demo/actions/workflows/shipmate.yml/runs?per_page=100&page=2")
PAGE_2 += ("--jq", JQ)
SLEPT_1 = ("sleep", 1)
#: Per verb: the env the dispatch action sets, the body it sends and its run's title.
COMMANDS = {
    "plan": (
        # `REF` set, as a caller may pass one: the plan body and title still carry none.
        {"VERB": "plan", "PR_NUMBER": "42", "REF": HEAD, "ENVIRONMENT": ""},
        {"verb": "plan", "pr_number": "42", "comment_id": "99"},
        "shipmate · plan pr=42 ref= comment=99",
    ),
    "apply": (
        {"VERB": "apply", "PR_NUMBER": "42", "REF": HEAD, "ENVIRONMENT": "dev-eu"},
        {
            "verb": "apply",
            "ref": HEAD,
            "pr_number": "42",
            "environment": "dev-eu",
            "comment_id": "99",
        },
        f"shipmate · apply pr=42 ref={HEAD} comment=99",
    ),
    "unlock": (
        {"VERB": "unlock", "PR_NUMBER": "42", "REF": HEAD, "ENVIRONMENT": "dev-eu"},
        {"verb": "unlock", "ref": HEAD, "environment": "dev-eu", "comment_id": "99"},
        f"shipmate · unlock pr= ref={HEAD} comment=99",
    ),
}
UNLOCK_TITLE = COMMANDS["unlock"][2]


def command(monkeypatch, log, verb, slug=SLUG):
    """Set the env of a commented `verb` dispatch and a clock that each sleep advances; return
    the POST as `answering` logs it."""
    env, inputs, _ = COMMANDS[verb]
    for name, value in {**env, "COMMENT_ID": "99", "SHIPMATE_APP_SLUG": slug}.items():
        monkeypatch.setenv(name, value)
    clock = [NOW]

    def sleep(s):
        log.append(("sleep", s))
        clock[0] += timedelta(seconds=s)

    monkeypatch.setattr(db, "_now", lambda: clock[0])
    monkeypatch.setattr(_shipmate, "_sleep", sleep)
    return (POST, json.dumps({"ref": "main", "inputs": inputs, "return_run_details": True}))


def run_row(title, actor=f"{SLUG}[bot]", event="workflow_dispatch", created_at=T0):
    return json.dumps(
        {"created_at": created_at, "event": event, "display_title": title, "actor": actor}
    )


#: The listings and sleeps of a window in which nothing matches: the first read 2 s after the
#: failed attempt, then one each second until 10 s after it.
WINDOW = [SLEPT_2, PAGE_1] + [SLEPT_1, PAGE_1] * 8


def test_a_lost_unlock_response_followed_by_its_listed_run_posts_once(calls, monkeypatch, capsys):
    """An unlock's title carries no pull request and its ref. Mutation: `_command_title` puts
    `PR_NUMBER` in the pr slot for unlock too -> its run is missed and the dispatch is sent
    again."""
    sent = command(monkeypatch, calls, "unlock")
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [run_row(UNLOCK_TITLE)])
    assert (post(capsys)[0], calls) == (db.MAY_HAVE_STARTED, [sent, SLEPT_2, PAGE_1])


@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
def test_a_lost_response_followed_by_its_listed_run_posts_once(calls, monkeypatch, capsys, verb):
    """Mutations: `_command_title` puts `REF` in the ref slot for plan too -> the plan run is
    missed; drop the `comment` key from `_command_title` -> no title matches."""
    sent = command(monkeypatch, calls, verb)
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [run_row(COMMANDS[verb][2])])
    assert (post(capsys)[0], calls) == (db.MAY_HAVE_STARTED, [sent, SLEPT_2, PAGE_1])


def test_a_run_listed_on_a_later_read_stops_the_retry(calls, monkeypatch, capsys):
    """The listing lags the POST, so it is read again each second. Mutation: read it once ->
    the run listed on the third read is missed and the dispatch is sent again."""
    sent = command(monkeypatch, calls, "apply")
    answering(
        monkeypatch,
        calls,
        [SystemExit(BAD_GATEWAY), ANSWER],
        ["", "", run_row(COMMANDS["apply"][2])],
    )
    assert (post(capsys)[0], calls) == (
        db.MAY_HAVE_STARTED,
        [sent, SLEPT_2, PAGE_1, SLEPT_1, PAGE_1, SLEPT_1, PAGE_1],
    )


def test_a_foreign_actors_run_with_this_title_does_not_stop_the_retry(calls, monkeypatch, capsys):
    """The title is consumer-authored; only the App's run counts. The whole call log also pins
    the window. Mutations: drop the actor check -> the retry stops; `_LIST_WINDOW` of 5 s ->
    fewer reads."""
    sent = command(monkeypatch, calls, "apply")
    title = COMMANDS["apply"][2]
    answering(
        monkeypatch,
        calls,
        [SystemExit(BAD_GATEWAY), ANSWER],
        [run_row(title, actor="someone[bot]")] * 9,
    )
    assert (post(capsys)[0], calls) == (0, [sent, *WINDOW, sent])


@pytest.mark.parametrize(
    "listed",
    [
        run_row(f"shipmate · apply pr=42 ref={HEAD} comment=98"),
        run_row(f"shipmate · apply pr=43 ref={HEAD} comment=99"),
        run_row("shipmate · plan pr=42 ref= comment=99"),
        run_row("shipmate · apply pr=42 ref=" + "1" * 40 + " comment=99"),
        run_row(f"shipmate · apply pr=42 ref={HEAD} comment=99", event="repository_dispatch"),
        run_row(f"shipmate · apply pr=42 ref={HEAD} comment=99", actor=None),
    ],
)
def test_a_run_of_another_command_does_not_stop_the_retry(calls, monkeypatch, capsys, listed):
    """Each row differs from this apply's run in one field: comment, pull request, verb, ref,
    event, actor. Mutations: compare only the verb and comment of the title; drop the event
    check."""
    sent = command(monkeypatch, calls, "apply")
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [listed] * 9)
    assert (post(capsys)[0], calls) == (0, [sent, *WINDOW, sent])


@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
def test_a_commented_dispatch_without_the_slug_is_refused_before_any_post(
    calls, monkeypatch, capsys, verb
):
    """Without the App's slug the run a lost answer started could not be found among the
    listed runs, so the dispatch is refused at startup, as a wiring defect, and sends nothing.
    Mutation: drop the startup refusal in `main` -> the dispatch is sent."""
    command(monkeypatch, calls, verb, slug="")
    answering(monkeypatch, calls, [ANSWER])
    refusal = (
        "::error::SHIPMATE_APP_SLUG is empty on a commented dispatch, so a run it started could "
        "not be found after a lost answer; nothing was dispatched. This is an engine wiring "
        "defect: actions/dispatch passes the slug of the App token it minted."
    )
    assert post(capsys) == (refusal, "", "")
    assert calls == []


@pytest.mark.parametrize(
    "env",
    [
        {"VERB": "plan", "PR_NUMBER": "42"},
        {"VERB": "auto", "PR_NUMBER": "42", "REF": HEAD},
    ],
    ids=["replan", "auto"],
)
def test_a_comment_less_dispatch_needs_no_slug(calls, monkeypatch, capsys, env):
    """The replan and `auto` read no title. Mutation: refuse an empty slug on every dispatch;
    refuse it when `VERB` is `auto` (the auto row reddens)."""
    for name, value in {**env, "COMMENT_ID": "", "SHIPMATE_APP_SLUG": ""}.items():
        monkeypatch.setenv(name, value)
    answering(monkeypatch, calls, [ANSWER])
    assert post(capsys)[0] == 0


def test_every_page_back_to_t0_is_read(calls, monkeypatch, capsys):
    """Mutation: read page 1 alone -> the run on page 2 is missed."""
    sent = command(monkeypatch, calls, "apply")
    full = "\n".join([run_row("shipmate · comment")] * 100)
    answering(
        monkeypatch,
        calls,
        [SystemExit(BAD_GATEWAY), ANSWER],
        [full, run_row(COMMANDS["apply"][2])],
    )
    assert (post(capsys)[0], calls) == (db.MAY_HAVE_STARTED, [sent, SLEPT_2, PAGE_1, PAGE_2])


def test_a_page_reaching_before_t0_is_the_last(calls, monkeypatch, capsys):
    """Mutation: drop the `created_at < t0` stop -> page 2 is read."""
    sent = command(monkeypatch, calls, "apply")
    older = run_row("shipmate · comment", created_at="2026-11-07T11:59:54Z")
    full = "\n".join([run_row("shipmate · comment")] * 99 + [older])
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [full] * 9)
    assert (post(capsys)[0], calls) == (0, [sent, *WINDOW, sent])


def test_a_listing_past_ten_pages_is_undetermined(calls, monkeypatch, capsys):
    """Mutation: end the page loop with `return False` -> the dispatch is sent again."""
    sent = command(monkeypatch, calls, "apply")
    full = "\n".join([run_row("shipmate · comment")] * 100)
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [full] * 10)
    code, _, err = post(capsys)
    assert (code, calls[:3], len(calls)) == (db.UNDETERMINED, [sent, SLEPT_2, PAGE_1], 12)
    assert err.endswith(
        "\nlisting shipmate.yml runs failed: ValueError: shipmate.yml runs since "
        "2026-11-07T11:59:55+00:00 run past 10 pages\n"
    )


def test_a_failed_command_listing_is_undetermined(calls, monkeypatch, capsys):
    """Mutation: `return` in `before_retry`'s handler in place of the `UNDETERMINED` exit ->
    the dispatch is sent again."""
    sent = command(monkeypatch, calls, "unlock")
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [SystemExit(FORBIDDEN)])
    assert (post(capsys)[0], calls) == (db.UNDETERMINED, [sent, SLEPT_2, PAGE_1])


def test_the_replan_keeps_the_comment_less_rule(calls, monkeypatch, capsys):
    """A plan without a comment is the replan. Mutation: route every plan through the title
    rule (`if not want["comment"]` -> `if want["verb"] == "auto"`) -> the per-workflow pages
    are read."""
    monkeypatch.setenv("VERB", "plan")
    answering(monkeypatch, calls, [SystemExit(BAD_GATEWAY), ANSWER], [""])
    replan = json.dumps(
        {"ref": "main", "inputs": {"verb": "plan", "pr_number": "42"}, "return_run_details": True}
    )
    assert (post(capsys)[0], calls) == (0, [(POST, replan), SLEPT_2, LIST, (POST, replan)])


def test_an_empty_slug_raises_before_any_listing():
    """Mutation: drop the empty-slug `ValueError` in `command_run_listed` -> it lists."""
    read = []
    with pytest.raises(ValueError, match="SHIPMATE_APP_SLUG is empty"):
        db.command_run_listed("acme/demo", NOW, {}, "", run=lambda args: read.append(args))
    assert read == []
