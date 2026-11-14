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
_FOOT = f"[comment run]({_URL}50)"


def _r(id_, ref=_OLD, by_app=True, status="pending"):
    return {"id": id_, "ref": ref, "verb": "apply", "status": status, "by_app": by_app}


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


@pytest.fixture
def gh(monkeypatch):
    """Run `main` against recorded reads; returns the recorder. Set `runs`, `complete`, `jobs`
    ({id: (jobs, complete)}) and `heads` (one per read) before calling `go`."""
    for k, v in {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": "50",
        "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/shipmate.yml@refs/heads/main",
        "PR_NUMBER": "7",
        "SHIPMATE_ENV": "",
        "SHIPMATE_AUTO": "false",
        "SHIPMATE_APP_SLUG": "shipmate-iac",
        "SHIPMATE_ACTOR": "shipmate-iac[bot]",
        "SHIPMATE_TRIGGERING_ACTOR": "shipmate-iac[bot]",
    }.items():
        monkeypatch.setenv(k, v)

    class Rec:
        runs, complete, jobs, heads, own_ref, own_row = [], True, {}, [_HEAD, _HEAD], _HEAD, True
        cancels, posts, job_reads = [], [], []

    rec = Rec()

    def apply_runs(repo, wf, pr, slug):
        assert (repo, wf, pr, slug) == ("o/r", "shipmate.yml", "7", "shipmate-iac")
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

    monkeypatch.setattr(aq, "apply_runs", apply_runs)
    monkeypatch.setattr(aq, "run_jobs", run_jobs)
    monkeypatch.setattr(aq, "pr_head", pr_head)
    monkeypatch.setattr(aq, "_run", run)
    monkeypatch.setattr(aq.rc, "post", lambda pr, body: rec.posts.append((pr, body)))
    rec.go = aq.main
    return rec


def _reply(*lines):
    return [("7", f"### shipmate apply\n\n⚪ {' '.join(lines)}\n\n{_FOOT}")]


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
    assert gh.posts == _reply(_WAITS.format(40))


def test_a_run_at_the_live_head_is_kept_and_named(gh):
    """Mutation: drop `r["ref"] != head` -- red (cancelled)."""
    gh.runs = [_r(40, ref=_HEAD)]
    gh.jobs = {40: (_j("pending"), True)}
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _reply(_WAITS.format(40))


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
    assert gh.posts == _reply(_WAITS.format(40))


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
    assert gh.posts == _reply(_WAITS.format(40))


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
    assert gh.posts == _reply(_WAITS.format(45), _CANCELLED.format(40))


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
    assert gh.posts == _reply(_WAITS.format(40))


@pytest.mark.parametrize(
    "ref",
    [
        "o/r/.github/workflows/../x.yml@refs/heads/main",
        "o/other/.github/workflows/shipmate.yml@refs/heads/main",
        "o/r/.github/workflows/sub/shipmate.yml@refs/heads/main",
        "o/r/.github/workflows/shipmate.yml?x=1@refs/heads/main",
        "shipmate.yml@refs/heads/main",
        "",
    ],
)
def test_a_workflow_ref_outside_this_repository_is_refused(ref):
    """Mutations: `_WORKFLOW_FILE.search` for `fullmatch` -- red; drop `name == path` -- red
    (a bare file name with no repository)."""
    with pytest.raises(SystemExit):
        aq.workflow_file("o/r", ref)


def test_the_workflow_file_is_read_from_the_ref():
    assert aq.workflow_file("o/r", "o/r/.github/workflows/ship.yaml@refs/pull/1/merge") == (
        "ship.yaml"
    )


def test_contract_states_each_reply_sentence_as_the_script_renders_it(monkeypatch):
    """Mutations: reword the cancelled sentence in `sentences` -- red; drop the CONTRACT bullet
    for the untrusted run -- red."""
    from _loader import ENGINE

    monkeypatch.setattr(aq, "_link", lambda run_id: "<url>")
    run = {"id": 1, "ref": "0123456" + "a" * 33}
    said = aq.sentences(9, run, run, [run], True) + aq.sentences(9, None, None, [], False)
    contract = (ENGINE / "CONTRACT.md").read_text(encoding="utf-8")
    missing = [s for s in said if f"``{s.replace('0123456', '<short sha>')}``" not in contract]
    assert len(said) == 4
    assert missing == []


def test_this_run_is_neither_named_nor_cancelled(gh):
    """This run is listed with a job `in_progress`; run 40, waiting for the group at the live
    head, is named. Mutation: drop `r["id"] != own` -- red (run 50 named as the holder)."""
    gh.runs = [_r(40, ref=_HEAD)]
    gh.jobs = {40: ([_rv("pending")], True)}
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _reply(_WAITS.format(40))


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
    assert gh.posts == _reply(_WAITS.format(40))


def test_a_wave_pending_on_its_cell_group_after_an_earlier_wave_applied_is_kept(gh):
    """Mutation: `idle` admits `pending` past `review` -- red (a level cut half-applied)."""
    gh.runs, gh.jobs = _stale(
        [_rv("completed"), _job("L0 / dev / dns", "completed"), _job("L1 / dev / app", "pending")]
    )
    gh.go()
    assert gh.cancels == []
    assert gh.posts == _reply(_WAITS.format(40))


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
    assert gh.posts == _reply(_WAITS.format(40))


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
