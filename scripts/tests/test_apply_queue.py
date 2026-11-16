"""`scripts/apply-queue`: which stale apply runs it cancels and the reply it posts, each compared
whole against hand-written text."""

import pytest
from _loader import load_script

aq = load_script("apply-queue")

_HEAD = "a" * 40
_OLD = "b" * 40
_NEW = "c" * 40
_URL = "https://github.com/o/r/actions/runs/"
_ME = f"[the apply run]({_URL}50)"
_WAITS = f"{_ME} waits for [an earlier apply run]({_URL}{{}}) of this pull request to finish."
_CANCELLED = (
    f"{_ME} cancelled [an apply run]({_URL}{{}}) of this pull request waiting at an older head "
    "(bbbbbbb)."
)
_FOREIGN = (
    f"{_ME} may wait for [an apply run]({_URL}{{}}) of this pull request that shipmate did not "
    "dispatch."
)
_INCOMPLETE = (
    f"{_ME} may wait for an earlier apply run of this pull request; its apply runs could not all "
    "be listed."
)
_FOOT = f"[apply run]({_URL}50)"


def _r(id_, ref=_OLD, by_app=True, status="pending", comment="11"):
    return {
        "id": id_,
        "ref": ref,
        "verb": "apply" if comment else "auto",
        "status": status,
        "by_app": by_app,
        "comment": comment,
    }


def _j(*statuses):
    """Wave cells of the `run` call, named as a jobs listing names them."""
    return [{"name": f"apply / run / L0 / dev / s{i}", "status": s} for i, s in enumerate(statuses)]


def _rv(status):
    """The call's `review` job: `pending` while the call waits for the group (run 43538585572
    listed exactly `apply / run / review` `pending`)."""
    return {"name": "apply / run / review", "status": status}


def _job(name, status):
    return {"name": f"apply / run / {name}", "status": status}


_GUARD = {"name": "apply / guard", "status": "in_progress"}
_QUEUE = {"name": "apply / queue", "status": "completed"}


def _marked(comments, marker):
    """`comments`, `(login, body)` pairs all by a Bot, as `upsert-comment.marker_comments` lists
    them: each whose first 64 characters start with `marker`."""
    return [
        {"id": i, "node_id": f"IC_{i}", "login": login, "head": body[:64]}
        for i, (login, body) in enumerate(comments)
        if body[:64].startswith(marker)
    ]


@pytest.fixture
def gh(monkeypatch):
    """Run `main` against recorded reads; returns the recorder. Set `runs`, `complete`, `jobs`
    ({id: (jobs, complete)}) and `heads` (one per read) before calling `go`."""
    for k, v in {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": "50",
        "PR_NUMBER": "7",
        "SHIPMATE_ENV": "",
        "SHIPMATE_AUTO": "false",
        "SHIPMATE_APP_SLUG": "shipmate-iac",
        "SHIPMATE_ACTOR": "shipmate-iac[bot]",
        "SHIPMATE_TRIGGERING_ACTOR": "shipmate-iac[bot]",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(aq.sys, "argv", ["apply-queue"])

    class Rec:
        runs, complete, jobs, heads, own_ref, own_row = [], True, {}, [_HEAD, _HEAD], _HEAD, True
        cancels, posts, job_reads = [], [], []
        #: `(login, body)` of the pull request's comments; each post adds the App's.
        comments, marker_reads = [], []
        waiters = None

    rec = Rec()

    def apply_runs(repo, pr, slug):
        assert (repo, pr, slug) == ("o/r", "7", "shipmate-iac")
        own = [_r(50, ref=rec.own_ref, status="in_progress")] if rec.own_row else []
        return [*rec.runs, *own], rec.complete

    def run_jobs(repo, run_id):
        rec.job_reads.append(run_id)
        own = ([_QUEUE, *_j("in_progress")], True)
        return rec.jobs.get(run_id, own if run_id == 50 else ([], False))

    def pr_head(repo, pr):
        return rec.heads.pop(0), False, "open"

    def run(argv):
        assert argv[:4] == ["gh", "api", "-X", "POST"]
        rec.cancels.append(argv[4])
        return ""

    def post(pr, body):
        rec.posts.append((pr, body))
        rec.comments.append(("shipmate-iac[bot]", body))

    def marker_comments(repo, pr, marker):
        rec.marker_reads.append((repo, pr, marker))
        return _marked(rec.comments, marker)

    monkeypatch.setattr(aq, "apply_runs", apply_runs)
    monkeypatch.setattr(aq, "run_jobs", run_jobs)
    monkeypatch.setattr(aq, "pr_head", pr_head)
    monkeypatch.setattr(aq, "_run", run)
    monkeypatch.setattr(aq.rc, "post", post)
    monkeypatch.setattr(aq.uc, "marker_comments", marker_comments)
    rec.go = aq.main
    rec.waiters = lambda: _main_waiters(monkeypatch)
    return rec


def _main_waiters(monkeypatch):
    monkeypatch.setattr(aq.sys, "argv", ["apply-queue", "waiters"])
    aq.main()


def _reply(*lines):
    return [("7", f"### shipmate apply\n\n⚪ {' '.join(lines)}\n\n{_FOOT}")]


def _held(*lines):
    """`_reply` under this run's waits marker: the reply names the run holding the group."""
    return [("7", f"<!-- shipmate:waits-50 -->\n{body}") for _, body in _reply(*lines)]


def _cancel(id_):
    return f"repos/o/r/actions/runs/{id_}/cancel"


def test_a_stale_run_whose_unfinished_jobs_all_wait_is_cancelled(gh):
    """Every unfinished job of the call waits on an approval. Mutation: cancel nothing
    (`cancelled = []`) -- red."""
    gh.runs = [_r(40)]
    gh.jobs = {40: (_j("completed", "waiting", "waiting"), True)}
    gh.go()
    assert gh.cancels == [_cancel(40)]
    assert gh.posts == _reply(_CANCELLED.format(40))


def test_a_stale_run_with_a_queued_job_is_kept_and_named(gh):
    """Mutation: `idle` admits `queued` -- red (cancelled)."""
    gh.runs = [_r(40)]
    gh.jobs = {40: (_j("waiting", "queued"), True)}
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _held(_WAITS.format(40))


def test_a_run_at_the_live_head_is_kept_and_named(gh):
    """Mutations: drop `r["ref"] != head` -- red (cancelled); drop the `apply run` footer label."""
    gh.runs = [_r(40, ref=_HEAD)]
    gh.jobs = {40: (_j("pending"), True)}
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _held(_WAITS.format(40))


def test_an_incomplete_runs_listing_cancels_nothing_and_says_the_run_may_wait(gh):
    """Mutation: drop the `not complete` return -- red (40 cancelled)."""
    gh.runs, gh.complete = [_r(40)], False
    gh.jobs = {40: (_j("waiting"), True)}
    gh.go()
    assert gh.cancels == []
    assert gh.job_reads == []
    assert gh.posts == _reply(_INCOMPLETE)


def test_a_run_shipmate_did_not_dispatch_is_never_cancelled_and_is_named_so(gh):
    """An untrusted run at an older head whose jobs only wait: no jobs read, no cancel.

    Mutations: treat every listed run as trusted (`app_runs = others`) -- red (jobs read and
    cancelled); drop the untrusted sentence -- red."""
    gh.runs = [_r(30, by_app=False), _r(40, by_app=False)]
    gh.jobs = {30: (_j("waiting"), True), 40: (_j("waiting"), True)}
    gh.go()
    assert gh.cancels == []
    assert gh.job_reads == []
    assert gh.posts == _reply(_FOREIGN.format(30))


def test_an_incomplete_jobs_listing_keeps_its_run(gh):
    """Mutation: drop `jobs[...][1]` from the stale test -- red (cancelled)."""
    gh.runs = [_r(40)]
    gh.jobs = {40: (_j("waiting"), False)}
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _held(_WAITS.format(40))


def test_a_run_with_no_unfinished_job_is_kept(gh):
    """Mutation: `idle` returns `all(...)` without `bool(unfinished)` -- red (cancelled)."""
    gh.runs = [_r(40)]
    gh.jobs = {40: (_j("completed", "completed"), True)}
    gh.go()
    assert gh.cancels == []
    assert gh.posts == []


def test_a_head_that_moved_between_reads_cancels_nothing(gh):
    """Mutation: drop the second head read -- red (cancelled)."""
    gh.runs, gh.heads = [_r(40)], [_HEAD, _NEW]
    gh.jobs = {40: (_j("waiting"), True)}
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _held(_WAITS.format(40))


def test_a_failed_head_read_cancels_nothing(gh, monkeypatch):
    """No jobs are read and no run is named. Mutations: return "" from `_head` on a failure --
    red (cancelled); drop the `head is None` return -- red (jobs read, cancelled)."""

    def pr_head(repo, pr):
        raise SystemExit("HTTP 502")

    monkeypatch.setattr(aq, "pr_head", pr_head)
    gh.runs = [_r(40)]
    gh.jobs = {40: (_j("waiting"), True)}
    gh.go()
    assert gh.cancels == []
    assert gh.job_reads == []
    assert gh.posts == []


def test_an_own_run_the_app_did_not_dispatch_writes_nothing(gh, monkeypatch, capsys):
    """A human re-run keeps `actor` and changes `triggering_actor`. Mutations: read `actor`
    only -- red (cancelled and posted); trust any actor ending `[bot]` -- red (the second
    case)."""
    monkeypatch.setenv("SHIPMATE_TRIGGERING_ACTOR", "kp")
    gh.runs = [_r(40)]
    gh.jobs = {40: (_j("waiting"), True)}
    gh.go()
    assert (gh.cancels, gh.posts) == ([], [])
    monkeypatch.setenv("SHIPMATE_TRIGGERING_ACTOR", "shipmate-iac[bot]")
    monkeypatch.setenv("SHIPMATE_ACTOR", "github-actions[bot]")
    gh.heads = [_HEAD, _HEAD]
    gh.go()
    assert (gh.cancels, gh.posts) == ([], [])
    assert _WAITS.format(40) in capsys.readouterr().out


def test_the_holder_is_named_over_an_older_waiting_run_and_cancels_share_one_reply(gh):
    """Run 45 holds the group (past `review`, a job `in_progress`), 40 is stale and waits on an
    approval, 30 and 60 wait for the group at the live head (`review` `pending`).

    Mutations: name the oldest earlier kept run instead of the holder -- red (30 named); `holds`
    ignores a `pending` `review` -- red (30 named); post only the first sentence -- red."""
    gh.runs = [_r(30, ref=_HEAD), _r(40), _r(45, ref=_HEAD), _r(60, ref=_HEAD)]
    gh.jobs = {
        30: ([_rv("pending")], True),
        40: ([_rv("completed"), _job("L0 / dev / app", "waiting")], True),
        45: ([_rv("completed"), _job("L0 / dev / app", "in_progress")], True),
        60: ([_rv("pending")], True),
    }
    gh.go()
    assert gh.cancels == [_cancel(40)]
    assert gh.posts == _held(_WAITS.format(45), _CANCELLED.format(40))


def test_a_newer_run_that_holds_nothing_is_not_named(gh):
    """Without a holder, only a kept run older than this one is named. Mutation: drop
    `r["id"] < own` -- red (60 named)."""
    gh.runs = [_r(60, ref=_HEAD)]
    gh.jobs = {60: ([_rv("pending")], True)}
    gh.go()
    assert gh.posts == []


def test_an_auto_run_cancels_but_posts_no_reply(gh, monkeypatch):
    """Mutation: drop the `SHIPMATE_AUTO` check -- red (posted)."""
    gh.runs = [_r(40)]
    gh.jobs = {40: (_j("waiting"), True)}
    monkeypatch.setenv("SHIPMATE_AUTO", "true")
    gh.go()
    assert gh.cancels == [_cancel(40)]
    assert gh.posts == []


def test_a_failed_cancel_keeps_the_run(gh, monkeypatch):
    """Mutation: count the run cancelled whatever `cancel` returns -- red."""

    def run(argv):
        raise SystemExit("::error::command failed (1): gh api\nHTTP 409")

    monkeypatch.setattr(aq, "_run", run)
    gh.runs = [_r(40)]
    gh.jobs = {40: (_j("waiting"), True)}
    gh.go()
    assert gh.posts == _held(_WAITS.format(40))


def test_contract_states_each_reply_sentence_as_the_script_renders_it(monkeypatch):
    """The queue's sentences, the holder's reply sentence and the waits marker line.

    Mutations: reword the cancelled sentence in `sentences` -- red; drop the CONTRACT bullet
    for the untrusted run -- red; reword `waits_sentence` -- red; drop ` -->` from
    `waits_marker` -- red."""
    from _loader import ENGINE

    monkeypatch.setattr(aq, "_link", lambda run_id: "<url>")
    monkeypatch.setattr(aq, "_comment_link", lambda repo, pr, comment_id: "<url>")
    run = {"id": 1, "ref": "0123456" + "a" * 33, "comment": "2"}
    said = aq.sentences(9, run, run, [run], True) + aq.sentences(9, None, None, [], False)
    said += [aq.waits_sentence(run, "o/r", "7", 9), aq.waits_marker("<run id>")]
    contract = (ENGINE / "CONTRACT.md").read_text(encoding="utf-8")
    missing = [s for s in said if f"``{s.replace('0123456', '<short sha>')}``" not in contract]
    assert len(said) == 6
    assert missing == []


def test_this_run_is_neither_named_nor_cancelled(gh):
    """This run is listed with a job `in_progress`; run 40, waiting for the group at the live
    head, is named. Mutation: drop `r["id"] != own` -- red (run 50 named as the holder)."""
    gh.runs = [_r(40, ref=_HEAD)]
    gh.jobs = {40: ([_rv("pending")], True)}
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _held(_WAITS.format(40))


def test_a_job_outside_the_run_call_neither_holds_the_group_nor_keeps_a_stale_run(gh):
    """A newer run still in its `guard` holds nothing, so it is not named; a stale run whose
    `queue` is listed `completed` beside its waiting call is cancelled.

    Mutations: `in_call` returns every job -- red (60 named as the holder); `_IN_CALL` as
    `" / guard"` -- red (40 kept)."""
    gh.runs = [_r(40), _r(60, ref=_HEAD)]
    gh.jobs = {40: ([_QUEUE, *_j("waiting")], True), 60: ([_GUARD], True)}
    gh.go()
    assert gh.cancels == [_cancel(40)]
    assert gh.posts == _reply(_CANCELLED.format(40))


def test_a_malformed_jobs_listing_keeps_its_run_and_the_queue_still_replies(gh, monkeypatch):
    """Through the real `run_jobs`: run 45's listing holds a job with no `name`, so it is
    incomplete and kept, and the holder pick after the cancel of 40 reads no job of it; 45 is
    not named, since nothing shows it in the queue.

    Mutation: `run_jobs` returns the partial list on an incomplete listing -- red (KeyError)."""
    import json

    import _shipmate

    listings = {
        40: [{"name": "apply / run / L0 / prod / app", "status": "waiting"}],
        45: [{"name": "apply / run / review", "status": "in_progress"}, {"status": "pending"}],
    }

    def gh_run(argv):
        run_id = int(argv[2].split("/")[5])
        return json.dumps({"jobs": listings[run_id]})

    monkeypatch.setattr(aq, "run_jobs", lambda repo, rid: _shipmate.run_jobs(repo, rid, gh_run))
    gh.runs = [_r(40), _r(45)]
    gh.go()
    assert gh.cancels == [_cancel(40)]
    assert gh.posts == _reply(_CANCELLED.format(40))


def _stale(jobs):
    """One stale run 40 whose complete jobs listing is `jobs`."""
    return [_r(40)], {40: (jobs, True)}


def test_a_run_whose_summary_waits_after_its_cells_applied_is_kept(gh):
    """`summary` `pending` on its gate-writer group: cancelling would drop the apply comment
    and the gate refresh. Mutation: a non-cell unfinished job no longer keeps the run
    (`not _is_cell(j) or j["status"] == "waiting"` in `idle`) -- red."""
    gh.runs, gh.jobs = _stale(
        [_rv("completed"), _job("L0 / dev / app", "completed"), _job("summary", "pending")]
    )
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _held(_WAITS.format(40))


def test_a_wave_pending_on_its_cell_group_after_an_earlier_wave_applied_is_kept(gh):
    """Mutation: `idle` admits `pending` past `review` -- red (a level cut half-applied)."""
    gh.runs, gh.jobs = _stale(
        [_rv("completed"), _job("L0 / dev / dns", "completed"), _job("L1 / dev / app", "pending")]
    )
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _held(_WAITS.format(40))


def test_a_run_whose_call_waits_for_the_group_is_cancelled(gh):
    """Mutation: drop the `_in_group_wait` branch of `idle` -- red (kept)."""
    gh.runs, gh.jobs = _stale([_QUEUE, _rv("pending")])
    gh.go()
    assert gh.cancels == [_cancel(40)]
    assert gh.posts == _reply(_CANCELLED.format(40))


def test_a_run_whose_cell_waits_on_an_approval_is_cancelled(gh):
    """Mutation: compare `== "pending"` for `== "waiting"` in `idle` -- red (kept)."""
    gh.runs, gh.jobs = _stale(
        [_rv("completed"), _job("detect", "completed"), _job("L0 / prod / app", "waiting")]
    )
    gh.go()
    assert gh.cancels == [_cancel(40)]


def test_a_waiting_release_job_is_not_a_cell_and_keeps_the_run(gh):
    """A wave's release job is named by its id, four segments under the caller. Mutation: count
    any `<caller> / run / L<n> / ...` job as a cell (`len(segments) >= 4`) -- red (cancelled)."""
    gh.runs, gh.jobs = _stale(
        [
            _rv("completed"),
            _job("L0 / prod / app", "waiting"),
            _job("L0 / wave1-release", "waiting"),
        ]
    )
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _held(_WAITS.format(40))


def test_a_run_with_one_cell_waiting_and_one_pending_is_kept(gh):
    """Mutation: `any` for `all` in `idle` -- red (cancelled)."""
    gh.runs, gh.jobs = _stale(
        [_rv("completed"), _job("L0 / prod / a", "waiting"), _job("L0 / prod / b", "pending")]
    )
    gh.go()
    assert gh.cancels == []


def test_a_run_left_only_in_outcome_is_not_named(gh):
    """Every job of its call finished; its `outcome` runs outside the group. Mutation: drop the
    unfinished-job check from the fallback -- red (40 named)."""
    outcome = {"name": "apply / outcome", "status": "in_progress"}
    gh.runs = [_r(40, ref=_HEAD)]
    gh.jobs = {40: ([_rv("completed"), _job("summary", "completed"), outcome], True)}
    gh.go()
    assert gh.posts == []


def test_a_pending_cell_of_env_run_and_stack_review_is_not_the_group_wait(gh):
    """Env `run` and root stack `review` give a cell named `apply / run / L1 / run / review`.
    Mutation: match the review job by `endswith(" / run / review")` -- red (cancelled)."""
    gh.runs, gh.jobs = _stale(
        [
            _rv("completed"),
            _job("L0 / dev / dns", "completed"),
            _job("L1 / run / review", "pending"),
        ]
    )
    gh.go()
    assert gh.cancels == []


def test_a_waiting_job_that_is_not_a_cell_keeps_the_run(gh):
    """`summary` `waiting` as the only unfinished job is no approval of a cell. Mutation: drop
    the `_is_cell` filter from `idle` -- red (cancelled)."""
    gh.runs, gh.jobs = _stale(
        [_rv("completed"), _job("L0 / dev / app", "completed"), _job("summary", "waiting")]
    )
    gh.go()
    assert gh.cancels == []


def test_a_run_not_at_the_live_head_cancels_nothing(gh):
    """This run's own listed title names the older head too: nothing is cancelled and no cancel
    sentence is posted. Mutation: drop `own_ref != head` -- red (40 cancelled)."""
    gh.own_ref = _OLD
    gh.runs, gh.jobs = _stale([_rv("pending")])
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _held(_WAITS.format(40))


def test_a_run_missing_from_its_own_listing_cancels_nothing(gh):
    """Its own ref is unknown, so a stale peer waiting for the group stays. Mutation: default
    `own_ref` to `head` (`(own_ref or head) != head`) -- red (40 cancelled)."""
    gh.own_row = False
    gh.runs, gh.jobs = _stale([_rv("pending")])
    gh.go()
    assert gh.cancels == []


def test_a_run_at_an_older_head_cancels_no_peer_at_a_third_head(gh):
    """This run at `_OLD`, its peer at `_NEW`, the live head `_HEAD`: the peer is not at this
    run's ref, yet this run is not at the live head either. Mutation: replace the own-ref guard
    with a filter dropping peers whose `ref` equals `own_ref` -- red (40 cancelled)."""
    gh.own_ref = _OLD
    gh.runs, gh.jobs = [_r(40, ref=_NEW)], {40: ([_rv("pending")], True)}
    gh.go()
    assert gh.cancels == []


_PULL = "https://github.com/o/r/pull/7#issuecomment-"


def _said(waiter, comment="11"):
    """The sentence telling run `waiter`, commanded by comment `comment`, that run 50 holds the
    group."""
    return (
        f"[the apply run]({_URL}{waiter}) for [this comment]({_PULL}{comment}) waits for "
        f"[the apply run]({_URL}50) of this pull request to finish."
    )


def _told(waiter, comment="11"):
    """The whole reply carrying `_said`, under the waiter's marker."""
    body = f"### shipmate apply\n\n⚪ {_said(waiter, comment)}\n\n{_FOOT}"
    return ("7", f"<!-- shipmate:waits-{waiter} -->\n{body}")


def _waiting(*ids):
    """Runs `ids`, each App-dispatched at the live head with its call waiting for the group."""
    return [_r(i, ref=_HEAD) for i in ids], {i: ([_QUEUE, _rv("pending")], True) for i in ids}


def _warnings(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("::warning")]


def test_a_waiting_run_is_told_once_which_run_it_waits_behind(gh, monkeypatch):
    """The whole reply: the waiter's marker, then the sentence citing the waiter's own comment,
    under a header with no environment, and this run's footer.

    Mutations: drop the marker line -- red; link this run's comment instead of the waiter's --
    red; this run's id in the marker -- red; `SHIPMATE_ENV` in the header -- red."""
    monkeypatch.setenv("SHIPMATE_ENV", "dev-eu")
    gh.runs = [_r(40, ref=_HEAD, comment="6902658567")]
    gh.jobs = {40: ([_QUEUE, _rv("pending")], True)}
    gh.waiters()
    assert gh.posts == [_told(40, "6902658567")]
    assert gh.marker_reads == [("o/r", "7", "<!-- shipmate:waits-")]


def test_a_waiting_run_already_told_by_the_app_is_not_told_again(gh):
    """Mutation: skip the marker read (answer every waiter) -- red."""
    gh.runs, gh.jobs = _waiting(40)
    gh.comments = [("shipmate-iac[bot]", "<!-- shipmate:waits-40 -->\n### shipmate apply")]
    gh.waiters()
    assert gh.posts == []


def test_a_marker_by_another_author_does_not_count(gh):
    """Only the App's own comments are its replies. Mutation: keep every Bot comment (drop the
    login filter) -- red."""
    gh.runs, gh.jobs = _waiting(40)
    gh.comments = [
        ("other[bot]", "<!-- shipmate:waits-40 -->"),
        ("github-actions[bot]", "<!-- shipmate:waits-40 -->"),
    ]
    gh.waiters()
    assert gh.posts == [_told(40)]


def test_a_marker_for_a_longer_run_id_does_not_answer_its_prefix(gh):
    """Mutation: build the marker without ` -->` -- red (`waits-12` matches `waits-123`)."""
    gh.runs, gh.jobs = _waiting(12)
    gh.comments = [("shipmate-iac[bot]", "<!-- shipmate:waits-123 -->\n### shipmate apply")]
    gh.waiters()
    assert gh.posts == [_told(12)]


def test_review_then_summary_tell_a_waiting_run_once(gh):
    """Two invocations against comments that keep what was posted. Mutation: skip the marker
    read -- red (posted twice)."""
    gh.runs, gh.jobs = _waiting(40)
    gh.waiters()
    gh.waiters()
    assert gh.posts == [_told(40)]


def test_a_waiting_run_first_listed_by_summary_is_told_then(gh):
    """Mutation: answer only on the first invocation (a module-level flag) -- red."""
    gh.waiters()
    gh.runs, gh.jobs = _waiting(60)
    gh.waiters()
    assert gh.posts == [_told(60)]


def test_only_an_app_commanded_run_waiting_for_the_group_is_told(gh):
    """This run (its `review` listed `pending` too), a run the App did not dispatch, an `auto`
    run, a run past `review` and a run whose jobs are not all listed: none is told, and jobs are
    read only for App runs with a comment other than this one.

    Mutations: drop each of the `by_app`, own-id and `comment` filters -- red; read jobs for
    every row -- red; filter on `comment` after `run_jobs` -- red; drop the listing check --
    red (`_in_group_wait` over 44's partial listing)."""
    gh.runs = [
        _r(41, ref=_HEAD, by_app=False),
        _r(42, ref=_HEAD, comment=""),
        _r(43, ref=_HEAD),
        _r(44, ref=_HEAD),
    ]
    gh.jobs = {
        41: ([_rv("pending")], True),
        42: ([_rv("pending")], True),
        43: ([_rv("in_progress")], True),
        44: ([_rv("pending")], False),
        50: ([_rv("pending")], True),
    }
    gh.waiters()
    assert gh.posts == []
    assert gh.job_reads == [43, 44]
    assert gh.marker_reads == []


def test_no_other_live_run_reads_no_jobs_and_no_comments(gh):
    """Mutation: list the comments before finding a waiter -- red."""
    gh.waiters()
    assert (gh.job_reads, gh.marker_reads, gh.posts) == ([], [], [])


def test_an_incomplete_runs_listing_tells_no_one(gh, capsys):
    """Mutation: treat an incomplete listing as complete -- red (40 told)."""
    gh.runs, gh.jobs = _waiting(40)
    gh.complete = False
    gh.waiters()
    assert (gh.job_reads, gh.posts) == ([], [])
    assert _warnings(capsys) == [
        ("::warning::the apply runs of #7 could not all be listed; no waiting run is told")
    ]


def test_two_waiting_runs_share_one_comments_listing(gh):
    """Mutation: list the comments per waiter -- red (two reads)."""
    gh.runs, gh.jobs = _waiting(40, 45)
    gh.waiters()
    assert gh.posts == [_told(40), _told(45)]
    assert len(gh.marker_reads) == 1


def test_a_failed_comments_listing_tells_no_one(gh, monkeypatch, capsys):
    """Mutation: post when the listing fails -- red."""

    def marker_comments(repo, pr, marker):
        raise SystemExit("::error::command failed (1): gh api\nHTTP 502")

    monkeypatch.setattr(aq.uc, "marker_comments", marker_comments)
    gh.runs, gh.jobs = _waiting(40)
    gh.waiters()
    assert gh.posts == []
    assert _warnings(capsys) == [
        (
            "::warning::could not list the comments of #7; no waiting run is told: "
            "::error::command failed (1): gh api | HTTP 502"
        )
    ]


def test_a_failed_post_still_tells_the_next_waiting_run(gh, monkeypatch, capsys):
    """Mutations: `return` in the POST handler -- red (45 not told); let the `SystemExit`
    escape -- red."""
    posts = []

    def post(pr, body):
        if "waits-40" in body:
            raise SystemExit("::error::command failed (1): gh api\nHTTP 502")
        posts.append((pr, body))

    monkeypatch.setattr(aq.rc, "post", post)
    gh.runs, gh.jobs = _waiting(40, 45)
    gh.waiters()
    assert posts == [_told(45)]
    assert _warnings(capsys) == [
        (
            "::warning::could not tell run 40 on #7 what it waits behind: "
            "::error::command failed (1): gh api | HTTP 502"
        )
    ]


def test_an_auto_run_holding_the_group_tells_a_commanded_waiting_run(gh, monkeypatch):
    """Mutation: honour `SHIPMATE_AUTO` in `waiters` -- red."""
    monkeypatch.setenv("SHIPMATE_AUTO", "true")
    gh.runs, gh.jobs = _waiting(40)
    gh.waiters()
    assert gh.posts == [_told(40)]


def test_a_holder_the_app_did_not_dispatch_prints_and_posts_nothing(gh, monkeypatch, capsys):
    """Mutation: drop the `self_by_app` check in `waiters` -- red (posted)."""
    monkeypatch.setenv("SHIPMATE_TRIGGERING_ACTOR", "kp")
    gh.runs, gh.jobs = _waiting(40)
    gh.waiters()
    assert gh.posts == []
    assert capsys.readouterr().out.splitlines() == [
        "::notice::this run was not dispatched by the shipmate App; it writes nothing",
        _said(40),
    ]


@pytest.mark.parametrize("argv", [["waiters", "extra"], ["other"]])
def test_an_unknown_argument_is_refused(gh, monkeypatch, argv):
    """Mutation: ignore arguments after the first -- red (`waiters extra` runs)."""
    monkeypatch.setattr(aq.sys, "argv", ["apply-queue", *argv])
    with pytest.raises(SystemExit) as exc:
        aq.main()
    assert exc.value.code == "::error::usage: apply-queue [waiters]"


def test_only_a_queue_reply_naming_the_holder_carries_the_waits_marker(gh):
    """The marker records the holder sentence; the may-wait, untrusted-only and cancel-only
    replies name no holder (other tests compare each whole).

    Mutations: add the marker to every reply -- red; add it to none -- red."""
    gh.runs, gh.jobs = [_r(40, ref=_HEAD)], {40: ([_rv("in_progress")], True)}
    gh.go()
    gh.runs, gh.jobs, gh.heads = [_r(30, by_app=False)], {}, [_HEAD, _HEAD]
    gh.go()
    assert [body.split("\n")[0] for _, body in gh.posts] == [
        "<!-- shipmate:waits-50 -->",
        "### shipmate apply",
    ]


def test_the_comment_link_names_the_repository_main_validated(monkeypatch):
    """Mutation: read `GITHUB_REPOSITORY` again in `_comment_link` -- red."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/env")
    monkeypatch.setattr(aq, "_link", lambda run_id: "<url>")
    said = aq.waits_sentence({"id": 40, "comment": "11"}, "o/r", "7", 50)
    assert said == (
        "[the apply run](<url>) for [this comment](https://github.com/o/r/pull/7#issuecomment-11)"
        " waits for [the apply run](<url>) of this pull request to finish."
    )
