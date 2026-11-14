"""`_shipmate.apply_runs` and `_shipmate.run_jobs` against a recorded `gh api`, each result
compared whole."""

import json

import _shipmate

_SHA = "df148592137074dd47f043a225f9ff859175ed6b"
_OLD = "0" * 40
_RUNS = "repos/o/r/actions/workflows/shipmate.yml/runs"
_BOT = {"login": "shipmate-iac[bot]"}


def _run(id_, title, *, actor=_BOT, event="workflow_dispatch", status="pending"):
    return {"id": id_, "display_title": title, "event": event, "actor": actor, "status": status}


def _apply(id_, pr="7", ref=_SHA, **kw):
    return _run(id_, f"shipmate · apply pr={pr} ref={ref} comment=11", **kw)


def _gh(pages, fail=()):
    """A `run` serving `pages` ({path: body}); a path in `fail` exits non-transiently and a
    path not in `pages` lists nothing."""
    calls = []

    def run(argv):
        assert argv[:2] == ["gh", "api"]
        calls.append(argv[2])
        if argv[2] in fail:
            raise SystemExit(f"::error::command failed (1): gh api {argv[2]}\nHTTP 404")
        return json.dumps(pages.get(argv[2], {"workflow_runs": [], "jobs": []}))

    run.calls = calls
    return run


def _listing(status, page=1):
    return f"{_RUNS}?event=workflow_dispatch&status={status}&per_page=100&page={page}"


def _call(pages, fail=()):
    return _shipmate.apply_runs("o/r", "shipmate.yml", "7", "shipmate-iac", run=_gh(pages, fail))


def _row(id_, ref=_SHA, verb="apply", status="pending", trusted=True):
    return {"id": id_, "ref": ref, "verb": verb, "status": status, "trusted": trusted}


def test_every_page_of_every_live_status_is_read():
    """Mutations: stop after the first page -- red (run 1100 lost); drop `waiting` from
    `LIVE_RUN_STATUSES` -- red; drop `requested` -- red (the call list differs); drop the
    `event=workflow_dispatch` filter -- red."""
    first = [_apply(1000 + i, pr="8") for i in range(99)] + [_apply(5)]
    run = _gh(
        {
            _listing("pending"): {"workflow_runs": first},
            _listing("pending", 2): {"workflow_runs": [_apply(1100, ref=_OLD)]},
            _listing("waiting"): {"workflow_runs": [_apply(6, status="waiting")]},
        }
    )
    got = _shipmate.apply_runs("o/r", "shipmate.yml", "7", "shipmate-iac", run=run)
    assert got == ([_row(5), _row(6, status="waiting"), _row(1100, ref=_OLD)], True)
    assert run.calls == [
        _listing("queued"),
        _listing("in_progress"),
        _listing("waiting"),
        _listing("pending"),
        _listing("pending", 2),
        _listing("requested"),
    ]


def test_a_failed_second_page_makes_the_listing_incomplete():
    """Mutation: `continue` past a page that returns None without clearing `complete` -- red."""
    first = [_apply(1000 + i, pr="8") for i in range(100)]
    got = _call({_listing("pending"): {"workflow_runs": first}}, fail={_listing("pending", 2)})
    assert got == ([], False)


def test_a_malformed_page_makes_the_listing_incomplete():
    """Mutations: drop the `isinstance(got, list)` check -- red (TypeError); drop the
    `all(isinstance(i, dict) ...)` check -- red (AttributeError)."""
    assert _call({_listing("queued"): {"workflow_runs": 5}}) == ([], False)
    assert _call({_listing("queued"): {"workflow_runs": [_apply(5), 7]}}) == ([], False)


def test_an_untrusted_dispatcher_is_listed_untrusted_and_other_titles_are_ignored():
    """A human or `GITHUB_TOKEN` dispatch, a push run carrying the App's actor, another pull
    request, and a `plan` and an `unlock` of this one.

    Mutations: compare only `endswith('[bot]')` -- red (github-actions[bot] trusted); drop the
    event check -- red; keep `plan` titles -- red."""
    runs = [
        _apply(1, actor={"login": "kp"}),
        _apply(2, actor={"login": "github-actions[bot]"}),
        _apply(3, event="push"),
        _apply(4, pr="70"),
        _run(5, "shipmate · plan pr=7 ref= comment=11"),
        _run(6, f"shipmate · unlock pr= ref={_SHA} comment=11"),
        _run(8, "shipmate · deploy", event="push"),
    ]
    got = _call({_listing("queued"): {"workflow_runs": runs}})
    assert got == ([_row(1, trusted=False), _row(2, trusted=False), _row(3, trusted=False)], True)


def test_an_unparseable_title_on_an_app_dispatched_run_makes_the_listing_incomplete():
    """Its run is unknown, never absent. Mutation: `continue` on a None parse without touching
    `complete` -- red. An untrusted run with the same title leaves the listing complete."""
    bad = _run(9, f"shipmate · apply pr=7 ref={_SHA}")
    assert _call({_listing("queued"): {"workflow_runs": [bad]}}) == ([], False)
    human = _run(9, f"shipmate · apply pr=7 ref={_SHA}", actor={"login": "kp"})
    assert _call({_listing("queued"): {"workflow_runs": [human]}}) == ([], True)


def test_an_empty_slug_trusts_nothing():
    """Mutation: build the bot login without the empty-slug check -- red (`[bot]` trusted)."""
    run = _gh({_listing("queued"): {"workflow_runs": [_apply(1, actor={"login": "[bot]"})]}})
    got = _shipmate.apply_runs("o/r", "shipmate.yml", "7", "", run=run)
    assert got == ([_row(1, status="pending", trusted=False)], True)


def test_the_current_run_is_listed_for_the_caller_to_exclude():
    """`apply_runs` does not read `GITHUB_RUN_ID`; `apply-queue` drops its own run
    (`test_this_run_is_neither_named_nor_cancelled`). Pins the row shape for a live run."""
    got = _call({_listing("in_progress"): {"workflow_runs": [_apply(42, status="in_progress")]}})
    assert got == ([_row(42, status="in_progress")], True)


_JOBS = "repos/o/r/actions/runs/5/jobs?filter=latest"


def _jobs(page):
    return f"{_JOBS}&per_page=100&page={page}"


def test_run_jobs_reads_every_page():
    """Mutation: stop after the first page -- red (the `in_progress` job lost)."""
    first = [{"name": f"j{i}", "status": "completed"} for i in range(100)]
    second = [{"name": "apply / run / L0 / dev / app", "status": "in_progress"}]
    run = _gh({_jobs(1): {"jobs": first}, _jobs(2): {"jobs": second}})
    assert _shipmate.run_jobs("o/r", 5, run=run) == (first + second, True)


def test_run_jobs_with_a_failed_second_page_is_incomplete():
    """Mutation: return the first page's jobs as complete on a failed page -- red."""
    first = [{"name": f"j{i}", "status": "completed"} for i in range(100)]
    run = _gh({_jobs(1): {"jobs": first}}, fail={_jobs(2)})
    assert _shipmate.run_jobs("o/r", 5, run=run) == ([], False)


def test_run_jobs_with_no_job_is_incomplete():
    """A live run that lists no job is not idle. Mutation: drop `not jobs` -- red."""
    assert _shipmate.run_jobs("o/r", 5, run=_gh({_jobs(1): {"jobs": []}})) == ([], False)


def test_run_jobs_with_a_job_without_status_is_incomplete():
    """Mutations: drop the status check -- red; return the partial list -- red."""
    run = _gh({_jobs(1): {"jobs": [{"name": "x", "status": "pending"}, {"name": "y"}]}})
    assert _shipmate.run_jobs("o/r", 5, run=run) == ([], False)


def test_run_jobs_with_a_job_without_name_is_incomplete():
    """`apply-queue` keys on the name. Mutation: drop the name check -- red."""
    run = _gh({_jobs(1): {"jobs": [{"status": "pending"}]}})
    assert _shipmate.run_jobs("o/r", 5, run=run) == ([], False)
