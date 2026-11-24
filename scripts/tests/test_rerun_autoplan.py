"""`scripts/rerun-autoplan`: which autoplan run a commanded plan re-runs, and the one POST.

Every gh call goes through a stub `run` keyed on the whole argv, which answers as `gh_json` and
`_pages` read: one page per call, `total_count` beside the listed items.
"""

import json

import _shipmate
import pytest
from _detect_fixtures import autoplans_read, dispatched_plan, listed_autoplan
from _loader import load_script

ra = load_script("rerun-autoplan")

REPO = "acme/demo"
HEAD = "a" * 40
ENGINE_SHA = "c" * 40
CELLS = [{"stack": "stacks/dns", "environment": "dev-us"}]
ENV = {
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_RUN_ID": "999",
    "SHIPMATE_PR_NUMBER": "7",
    "SHIPMATE_HEAD_SHA": HEAD,
    "SHIPMATE_PLANNED_CELLS_NAMES": json.dumps(CELLS),
    "SHIPMATE_ENGINE_REPO": "acme/engine",
    "SHIPMATE_ENGINE_SHA": ENGINE_SHA,
}

AUTOPLANS = autoplans_read(REPO, HEAD)
DISPATCH = [
    [
        "gh",
        "api",
        (
            f"repos/{REPO}/actions/workflows/shipmate.yml/runs"
            f"?event=workflow_dispatch&status={status}&per_page=100&page=1"
        ),
    ]
    for status in ("requested", "pending", "waiting", "queued", "in_progress")
]
POST = ["gh", "api", "-X", "POST", f"repos/{REPO}/actions/runs/9/rerun-failed-jobs"]
NOTICE = "::notice title=rerun::re-ran the failed jobs of autoplan run 9, superseded by this plan\n"
WARNING = (
    "::warning::could not re-run the failed jobs of autoplan run 9; its failed plan checks stay "
    "red until a push plans a new head\n"
)


def jobs_read(rid):
    return ["gh", "api", f"repos/{REPO}/actions/runs/{rid}/jobs?filter=latest&per_page=100&page=1"]


def artifacts_read(rid, page=1):
    return ["gh", "api", f"repos/{REPO}/actions/runs/{rid}/artifacts?per_page=100&page={page}"]


def run_read(rid):
    return ["gh", "api", f"repos/{REPO}/actions/runs/{rid}"]


def happy_calls(rid=9):
    return [AUTOPLANS, *DISPATCH, jobs_read(rid), artifacts_read(rid), run_read(rid)]


def job(name, conclusion, attempt=1):
    return {"name": name, "status": "completed", "conclusion": conclusion, "run_attempt": attempt}


#: The jobs of an autoplan whose one cell failed on a precondition, its summary failing after it.
JOBS = [
    job("plan / claim", "skipped"),
    job("plan / facts", "success"),
    job("plan / detect", "success"),
    job("plan / dev-us / stacks/dns", "failure"),
    job("plan / dev-us / stacks/app", "success"),
    job("plan / summary", "failure"),
    job("plan / outcome", "skipped"),
    job("plan / gate-hold", "skipped"),
]
ARTIFACTS = ["cell-error.dev-us.stacks-dns", "plan.dev-us.stacks-app"]
PLAN_YML = "acme/engine/.github/workflows/plan.yml@main"
REFS = [
    {"path": "acme/engine/.github/workflows/cell.yml@main", "sha": "d" * 40},
    {"path": PLAN_YML, "sha": ENGINE_SHA},
]


def with_job(name, **fields):
    return [j | fields if j["name"] == name else j for j in JOBS]


def autoplan(rid=9, status="completed", conclusion="failure", attempt=1):
    return listed_autoplan(rid, HEAD, status=status, conclusion=conclusion, run_attempt=attempt)


def artifact_pages(names):
    """Whole artifact pages: `names` itself when it holds pages, else one page of those names."""
    if names and isinstance(names[0], dict):
        return names
    return [{"total_count": len(names), "artifacts": [{"name": n} for n in names]}]


def dispatch_page(dispatched, i):
    """The page answering `DISPATCH[i]`: `dispatched[i]` for a dict of whole pages, else the runs
    of `dispatched` listed in that read's status."""
    if isinstance(dispatched, dict):
        return dispatched.get(i, {"total_count": 0, "workflow_runs": []})
    status = DISPATCH[i][2].split("status=")[1].split("&")[0]
    listed = [r for r in dispatched if r["status"] == status]
    return {"total_count": len(listed), "workflow_runs": listed}


@pytest.fixture
def slept(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    slept = []
    monkeypatch.setattr(_shipmate, "_sleep", slept.append)
    return slept


def stub(
    monkeypatch, autoplans=None, dispatched=(), jobs=None, artifacts=None, refs=None, fail=None
):
    """`ra.run` over the head's autoplan runs (`autoplans`: listing rows, default run 9), the
    live dispatches (`dispatched`: runs, or a dict of `DISPATCH` index to a whole page), the
    `jobs` of runs 9 and 12 (one listing for both, or a dict by run id), their `artifacts`
    (names, or a list of whole pages), and each run's `referenced_workflows` (`refs`, by run
    id). `fail` pairs an argv with a `SystemExit` raised on its first call only. Returns the
    calls."""
    calls = []
    autoplans = [autoplan()] if autoplans is None else autoplans
    jobs = JOBS if jobs is None else jobs
    jobs = jobs if isinstance(jobs, dict) else {9: jobs, 12: jobs}
    refs = {9: REFS, 12: REFS} if refs is None else refs
    pages = artifact_pages(ARTIFACTS if artifacts is None else artifacts)
    answers = {json.dumps(AUTOPLANS): "".join(json.dumps(r) + "\n" for r in autoplans)}
    for i, read in enumerate(DISPATCH):
        answers[json.dumps(read)] = json.dumps(dispatch_page(dispatched, i))
    for rid in (9, 12):
        listed = jobs.get(rid, JOBS)
        page = {"total_count": len(listed), "jobs": listed}
        answers[json.dumps(jobs_read(rid))] = json.dumps(page)
        for n, page in enumerate(pages, 1):
            answers[json.dumps(artifacts_read(rid, n))] = json.dumps(page)
    for rid, listed in refs.items():
        answers[json.dumps(run_read(rid))] = json.dumps({"id": rid, "referenced_workflows": listed})
    answers[json.dumps(POST)] = "{}"
    fail = {json.dumps(k): v for k, v in fail or []}

    def run(args):
        calls.append(args)
        key = json.dumps(args)
        if key in fail:
            raise fail.pop(key)
        if key not in answers:
            raise AssertionError(f"unexpected gh call: {args}")
        return answers[key]

    monkeypatch.setattr(ra, "run", run)
    return calls


def cli(*argv):
    """`rerun-autoplan argv` as its `__main__` block runs it: 0, or the `SystemExit` code."""
    try:
        _shipmate.run_annotated(ra.main, list(argv))
    except SystemExit as exc:
        return exc.code
    return 0


def notice(text):
    return f"::notice::no autoplan re-run: {text}\n"


def test_every_row_holding_prints_the_run_id(slept, monkeypatch, capsys):
    """Mutation: print the newest listed id before row 2 -- the calls stop at the listing."""
    calls = stub(monkeypatch)
    assert (cli("decide"), capsys.readouterr(), calls, slept) == (
        0,
        ("9\n", ""),
        happy_calls(),
        [],
    )


def test_the_newest_of_two_autoplan_runs_is_the_one_judged(slept, monkeypatch, capsys):
    """Mutation: `min` for `max` in `newest_autoplan` -- run 9 prints."""
    calls = stub(monkeypatch, autoplans=[autoplan(9), autoplan(12)])
    assert (cli("decide"), capsys.readouterr(), calls) == (0, ("12\n", ""), happy_calls(12))


#: A ready run that deferred to an earlier autoplan of its head, shaped as `deferred` reads it.
DEFERRED = [
    job("plan / claim", "skipped"),
    job("plan / facts", "success"),
    job("plan / defer", "success"),
    job("plan / detect", "skipped"),
    job("plan / summary", "skipped"),
    job("plan / outcome", "skipped"),
    job("plan / gate-hold", "skipped"),
]


def test_a_deferred_newest_autoplan_yields_to_the_older_run(slept, monkeypatch, capsys):
    """Run 12 deferred and planned nothing, so run 9 holds the head's plan. Mutation: return the
    newest listed run in `newest_autoplan` -- run 12 is judged and `did not plan`."""
    calls = stub(
        monkeypatch,
        autoplans=[autoplan(9), autoplan(12, conclusion="success")],
        jobs={9: JOBS, 12: DEFERRED},
    )
    assert (cli("decide"), capsys.readouterr(), calls, slept) == (
        0,
        ("9\n", ""),
        [AUTOPLANS, jobs_read(12), *DISPATCH, jobs_read(9), artifacts_read(9), run_read(9)],
        [],
    )


def test_the_deferred_run_read_is_not_retried(slept, monkeypatch, capsys):
    """Mutation: drop `with no_retries()` from `main` -- run 12's jobs are read twice after a
    sleep and run 9 prints."""
    calls = stub(
        monkeypatch,
        autoplans=[autoplan(9), autoplan(12, conclusion="success")],
        jobs={9: JOBS, 12: DEFERRED},
        fail=[(jobs_read(12), SystemExit(_TRANSIENT))],
    )
    assert (cli("decide"), capsys.readouterr(), calls, slept) == (
        0,
        (
            "",
            _TRANSIENT_WARNING
            + notice("a read failed: the jobs of autoplan run 12 could not be listed"),
        ),
        [AUTOPLANS, jobs_read(12)],
        [],
    )


def test_an_unfinished_newest_autoplan_is_not_re_run(slept, monkeypatch, capsys):
    """Its jobs listing would be partial. Mutation: delete the status check -- the jobs are read
    and the run id prints."""
    calls = stub(monkeypatch, autoplans=[autoplan(status="in_progress", conclusion=None)])
    assert (cli("decide"), capsys.readouterr(), calls) == (
        0,
        ("", notice("autoplan run 9 is in progress")),
        [AUTOPLANS],
    )


def test_another_live_plan_run_of_the_pull_request_blocks(slept, monkeypatch, capsys):
    """The re-run would replace it while it waits in `plan-<pr>`. Mutation: drop row 3 -- the run
    id prints."""
    calls = stub(monkeypatch, dispatched=[dispatched_plan(30, "7", "queued")])
    assert (cli("decide"), capsys.readouterr(), calls) == (
        0,
        (
            "",
            notice(
                "shipmate plan run 30 of pull request #7 is queued, and a re-run would replace it"
            ),
        ),
        [AUTOPLANS, *DISPATCH],
    )


def test_this_run_listed_alone_blocks_nothing(slept, monkeypatch, capsys):
    """This run is itself a live plan run of the pull request. Mutation: drop the self-exclusion
    -- nothing prints."""
    calls = stub(monkeypatch, dispatched=[dispatched_plan(999, "7")])
    assert (cli("decide"), capsys.readouterr(), calls) == (0, ("9\n", ""), happy_calls())


_INCOMPLETE = notice(
    "a read failed: the live shipmate plan runs of pull request #7 could not be listed"
)
_REQUESTED = (
    "repos/acme/demo/actions/workflows/shipmate.yml/runs?event=workflow_dispatch&status=requested"
)


@pytest.mark.parametrize(
    ("dispatched", "fail", "warning"),
    [
        pytest.param(
            {0: {"total_count": 1, "workflow_runs": []}},
            [],
            f"::warning::{_REQUESTED} advertised [1] entries and returned 0\n",
            id="short-count",
        ),
        pytest.param(
            (),
            [(DISPATCH[0], SystemExit("command failed (1): gh api x\nHTTP 404"))],
            "::warning::command failed (1): gh api x | HTTP 404\n",
            id="failed-page",
        ),
    ],
)
def test_an_incomplete_live_plan_listing_blocks(
    slept, monkeypatch, capsys, dispatched, fail, warning
):
    """A run the listing hides may be waiting. Mutations: ignore `complete` in
    `other_live_plan_run` -- both print the run id; list without `counted` -- short-count
    prints the run id."""
    calls = stub(monkeypatch, dispatched=dispatched, fail=fail)
    assert (cli("decide"), capsys.readouterr(), calls) == (
        0,
        ("", warning + _INCOMPLETE),
        [AUTOPLANS, *DISPATCH],
    )


_UP_TO_JOBS = [AUTOPLANS, *DISPATCH, jobs_read(9)]


@pytest.mark.parametrize(
    ("jobs", "reason"),
    [
        pytest.param(
            with_job("plan / facts", run_attempt=2),
            "autoplan run 9 is listed at attempt 1 and its jobs at [1, 2]",
            id="attempt",
        ),
        pytest.param(
            with_job("plan / detect", conclusion="failure"),
            "autoplan run 9 did not plan",
            id="detect-failed",
        ),
        pytest.param(
            with_job("plan / outcome", conclusion="cancelled"),
            "autoplan run 9: its job plan / outcome ended cancelled",
            id="cancelled",
        ),
        pytest.param(
            with_job("plan / gate-hold", conclusion="timed_out"),
            "autoplan run 9: its job plan / gate-hold ended timed_out",
            id="timed-out",
        ),
        pytest.param(
            with_job("plan / gate-hold", conclusion="failure"),
            "autoplan run 9: its failed job plan / gate-hold is no plan cell this run planned",
            id="gate-hold",
        ),
        pytest.param(
            with_job("plan / dev-us / stacks/app", conclusion="failure"),
            "autoplan run 9: its failed job plan / dev-us / stacks/app is no plan cell this run "
            "planned",
            id="unplanned-cell",
        ),
        pytest.param(
            with_job("plan / dev-us / stacks/dns", conclusion="success"),
            "autoplan run 9: no plan cell this run planned failed in it",
            id="summary-only",
        ),
    ],
)
def test_a_job_listing_outside_the_re_run_bound_is_not_re_run(
    slept, monkeypatch, capsys, jobs, reason
):
    """Re-run failed jobs re-runs failed, cancelled and timed-out jobs and their dependents; the
    bound is failed cells this run planned plus `plan / summary`. Mutations, each reddening its
    case: drop the `run_attempt` comparison -- attempt; skip `planned` -- detect-failed; drop
    the conclusion rule -- cancelled and timed-out; refuse only `cancelled` -- timed-out; allow
    any `plan / ` job -- gate-hold; build the allowed set from the job listing --
    unplanned-cell; require any failed job instead of a failed cell -- summary-only. Each
    prints the run id."""
    calls = stub(monkeypatch, jobs=jobs)
    assert (cli("decide"), capsys.readouterr(), calls) == (0, ("", notice(reason)), _UP_TO_JOBS)


_UP_TO_ARTIFACTS = [*_UP_TO_JOBS, artifacts_read(9)]


@pytest.mark.parametrize(
    "name", ["plan.dev-us.stacks-dns", "cell-summary.dev-us.stacks-dns"], ids=["plan", "summary"]
)
def test_an_upload_already_made_for_a_failed_cell_blocks(slept, monkeypatch, capsys, name):
    """A second upload of either name fails. Mutation: drop the slug rule (name the stack path)
    -- both print the run id."""
    calls = stub(monkeypatch, artifacts=["cell-error.dev-us.stacks-dns", name])
    assert (cli("decide"), capsys.readouterr(), calls) == (
        0,
        ("", notice(f"autoplan run 9 already uploaded {name}")),
        _UP_TO_ARTIFACTS,
    )


@pytest.mark.parametrize(
    "artifacts",
    [["cell-error.dev-us.stacks-dns"], ["plan.dev-us.stacks-dns-x"]],
    ids=["cell-error-only", "longer-slug"],
)
def test_other_uploads_do_not_block(slept, monkeypatch, capsys, artifacts):
    """`cell-error.*` overwrites, and another cell's name only shares a prefix. Mutations: match
    names by suffix -- cell-error-only prints nothing; by `startswith` -- longer-slug prints
    nothing."""
    calls = stub(monkeypatch, artifacts=artifacts)
    assert (cli("decide"), capsys.readouterr(), calls) == (0, ("9\n", ""), happy_calls())


def test_an_upload_listed_on_the_second_page_blocks(slept, monkeypatch, capsys):
    """Mutation: read the first page alone (`gh_json(...?per_page=100&page=1)["artifacts"]`) --
    the run id prints."""
    filler = [{"name": f"plan.dev-us.filler-{i}"} for i in range(100)]
    pages = [
        {"total_count": 101, "artifacts": filler},
        {"total_count": 101, "artifacts": [{"name": "plan.dev-us.stacks-dns"}]},
    ]
    calls = stub(monkeypatch, artifacts=pages)
    assert (cli("decide"), capsys.readouterr(), calls) == (
        0,
        ("", notice("autoplan run 9 already uploaded plan.dev-us.stacks-dns")),
        [*_UP_TO_JOBS, artifacts_read(9, 1), artifacts_read(9, 2)],
    )


@pytest.mark.parametrize(
    ("artifacts", "fail", "warning"),
    [
        pytest.param(
            [{"total_count": 2, "artifacts": [{"name": "cell-error.dev-us.stacks-dns"}]}],
            [],
            "::warning::repos/acme/demo/actions/runs/9/artifacts advertised [2] entries and "
            "returned 1\n",
            id="short-count",
        ),
        pytest.param(
            None,
            [(artifacts_read(9), SystemExit("command failed (1): gh api x\nHTTP 404"))],
            "::warning::command failed (1): gh api x | HTTP 404\n",
            id="failed",
        ),
    ],
)
def test_an_incomplete_artifact_listing_blocks(
    slept, monkeypatch, capsys, artifacts, fail, warning
):
    """A hidden upload may be one that fails a second time. Mutations: list without `counted` --
    short-count prints the run id; read an unread listing as empty -- both print the run id."""
    calls = stub(monkeypatch, artifacts=artifacts, fail=fail)
    assert (cli("decide"), capsys.readouterr(), calls) == (
        0,
        ("", warning + notice("the artifacts of autoplan run 9 could not be listed")),
        _UP_TO_ARTIFACTS,
    )


_OLD = {"path": "acme/engine/.github/workflows/plan.yml-old@main", "sha": ENGINE_SHA}
_CELL_ONLY = [REFS[0]]


def plan_yml_at(sha):
    return [REFS[0], {"path": PLAN_YML, "sha": sha}]


@pytest.mark.parametrize(
    ("refs", "theirs"),
    [
        pytest.param({9: plan_yml_at("e" * 40)}, "e" * 40, id="shas-differ"),
        pytest.param({9: plan_yml_at("c" * 39 + "e")}, "c" * 39 + "e", id="last-char"),
        pytest.param({9: plan_yml_at("c" * 7)}, "c" * 7, id="prefix"),
        pytest.param({9: [REFS[0], _OLD]}, None, id="other-path"),
        pytest.param({9: _CELL_ONLY}, None, id="no-entry"),
    ],
)
def test_an_engine_plan_yml_pin_that_differs_blocks(slept, monkeypatch, capsys, refs, theirs):
    """The re-run must plan with the engine this run planned with, `SHIPMATE_ENGINE_SHA`; only
    the autoplan run's `referenced_workflows` is read. Mutations: match the path by
    `startswith` -- other-path; compare only when an entry exists -- other-path and no-entry;
    compare the first 12 characters -- last-char; `ours.startswith(theirs)` -- prefix; read this
    run's `referenced_workflows` for `ours` again -- every case calls run 999. Each but the last
    prints the run id."""
    calls = stub(monkeypatch, refs=refs)
    assert (cli("decide"), capsys.readouterr(), calls) == (
        0,
        (
            "",
            notice(f"autoplan run 9 ran engine plan.yml at {theirs} and this run at {ENGINE_SHA}"),
        ),
        happy_calls(),
    )


@pytest.mark.parametrize(
    ("engine", "path"),
    [
        pytest.param("Acme/Engine", PLAN_YML, id="engine-cased"),
        pytest.param("acme/engine", "ACME/Engine/.github/workflows/plan.yml@main", id="path-cased"),
    ],
)
def test_the_engine_slug_matches_in_any_case(slept, monkeypatch, capsys, engine, path):
    """`job.workflow_repository` may spell the slug otherwise than the caller's `uses:`.
    Mutations: drop `.lower()` from `engine` -- engine-cased; from the referenced path's
    `<owner>/<repo>` -- path-cased. Each reads the pin as missing."""
    monkeypatch.setenv("SHIPMATE_ENGINE_REPO", engine)
    calls = stub(monkeypatch, refs={9: [REFS[0], {"path": path, "sha": ENGINE_SHA}]})
    assert (cli("decide"), capsys.readouterr(), calls) == (0, ("9\n", ""), happy_calls())


@pytest.mark.parametrize("sha", ["", "C" * 40, "c" * 39], ids=["empty", "upper", "short"])
def test_a_malformed_engine_sha_refuses_before_any_call(slept, monkeypatch, capsys, sha):
    """The autoplan's pin equals the malformed value, so only the format check refuses.
    Mutation: drop the `is_head_sha` check -- every case reads and prints the run id."""
    monkeypatch.setenv("SHIPMATE_ENGINE_SHA", sha)
    calls = stub(monkeypatch, refs={9: plan_yml_at(sha)})
    assert (cli("decide"), capsys.readouterr(), calls) == (
        0,
        ("", notice(f"SHIPMATE_ENGINE_SHA is not a 40-char hex SHA (got: {sha!r})")),
        [],
    )


def test_a_failed_read_prints_one_notice_and_exits_0(slept, monkeypatch, capsys):
    """Mutation: catch only `ValueError` -- the gh `SystemExit` exits non-zero."""
    calls = stub(
        monkeypatch, fail=[(run_read(9), SystemExit("command failed (1): gh api x\nHTTP 404"))]
    )
    assert (cli("decide"), capsys.readouterr(), calls) == (
        0,
        ("", notice("a read failed: command failed (1): gh api x | HTTP 404")),
        happy_calls(),
    )


_TRANSIENT = "command failed (1): gh api x\nHTTP 502"
_TRANSIENT_WARNING = "::warning::command failed (1): gh api x | HTTP 502\n"


@pytest.mark.parametrize(
    ("site", "stderr", "calls"),
    [
        pytest.param(
            AUTOPLANS,
            notice("a read failed: command failed (1): gh api x | HTTP 502"),
            [AUTOPLANS],
            id="autoplans",
        ),
        pytest.param(
            DISPATCH[0], _TRANSIENT_WARNING + _INCOMPLETE, [AUTOPLANS, *DISPATCH], id="live"
        ),
        pytest.param(
            jobs_read(9),
            _TRANSIENT_WARNING
            + notice("a read failed: the jobs of autoplan run 9 could not be listed"),
            _UP_TO_JOBS,
            id="jobs",
        ),
        pytest.param(
            artifacts_read(9),
            _TRANSIENT_WARNING + notice("the artifacts of autoplan run 9 could not be listed"),
            _UP_TO_ARTIFACTS,
            id="artifacts",
        ),
        pytest.param(
            run_read(9),
            notice("a read failed: command failed (1): gh api x | HTTP 502"),
            [*_UP_TO_ARTIFACTS, run_read(9)],
            id="autoplan-run",
        ),
    ],
)
def test_decide_retries_no_read(slept, monkeypatch, capsys, site, stderr, calls):
    """The summary holds the head's gate-writer group, so a read fails once into a notice.
    Mutation: drop `with no_retries()` from `main` -- every case reads its site twice after a
    sleep and prints the run id."""
    got = stub(monkeypatch, fail=[(site, SystemExit(_TRANSIENT))])
    assert (cli("decide"), capsys.readouterr(), got, slept) == (0, ("", stderr), calls, [])


def test_an_empty_head_sha_exits_1_before_any_call(slept, monkeypatch, capsys):
    """Mutation: read `HEAD_SHA` instead -- the decision runs and prints the run id."""
    monkeypatch.setenv("SHIPMATE_HEAD_SHA", "")
    monkeypatch.setenv("HEAD_SHA", HEAD)
    calls = stub(monkeypatch)
    assert (cli("decide"), capsys.readouterr(), calls) == (
        1,
        ("", "::error::SHIPMATE_HEAD_SHA must be a 40-char lowercase hex SHA (got: '')\n"),
        [],
    )


def test_post_sends_one_post_over_a_clean_listing(slept, monkeypatch, capsys):
    """Mutations: skip the re-read -- the calls differ; send the POST twice -- two POSTs."""
    calls = stub(monkeypatch)
    assert (cli("post", "9"), capsys.readouterr(), calls, slept) == (
        0,
        ("", NOTICE),
        [*DISPATCH, POST],
        [],
    )


def test_post_sends_nothing_when_a_run_appeared_since_decide(slept, monkeypatch, capsys):
    """Mutation: skip the re-read in `post` -- the POST is sent."""
    calls = stub(monkeypatch, dispatched=[dispatched_plan(30, "7", "queued")])
    assert (cli("post", "9"), capsys.readouterr(), calls) == (
        0,
        (
            "",
            (
                "::notice::no re-run of autoplan run 9: shipmate plan run 30 of pull request #7 "
                "is queued\n"
            ),
        ),
        DISPATCH,
    )


@pytest.mark.parametrize(
    ("dispatched", "fail", "warning"),
    [
        pytest.param(
            {0: {"total_count": 1, "workflow_runs": []}},
            [],
            f"::warning::{_REQUESTED} advertised [1] entries and returned 0\n",
            id="short-count",
        ),
        pytest.param(
            (), [(DISPATCH[0], SystemExit(_TRANSIENT))], _TRANSIENT_WARNING, id="transient"
        ),
    ],
)
def test_post_sends_nothing_over_an_incomplete_listing(
    slept, monkeypatch, capsys, dispatched, fail, warning
):
    """Mutations: re-read without `counted` -- short-count sends the POST; drop `with no_retries()`
    from `main` -- transient re-reads and sends the POST."""
    calls = stub(monkeypatch, dispatched=dispatched, fail=fail)
    assert (cli("post", "9"), capsys.readouterr(), calls, slept) == (
        0,
        (
            "",
            warning
            + "::notice::no re-run of autoplan run 9: a read failed: the live shipmate plan runs "
            "of pull request #7 could not be listed\n",
        ),
        DISPATCH,
        [],
    )


def test_a_failed_post_warns_once_and_exits_0(slept, monkeypatch, capsys):
    """A retried POST cannot tell whether the first landed. Mutation: send it through
    `retried` -- a second POST after a sleep."""
    failure = SystemExit("command failed (1): gh api -X POST x\nHTTP 502")
    calls = stub(monkeypatch, fail=[(POST, failure)])
    assert (cli("post", "9"), capsys.readouterr(), calls, slept) == (
        0,
        ("", WARNING + "command failed (1): gh api -X POST x | HTTP 502\n"),
        [*DISPATCH, POST],
        [],
    )


def test_post_refuses_an_id_that_is_not_a_run_id(slept, monkeypatch, capsys):
    """It is bound into the POST path. Mutation: drop the `is_run_id` check -- reads and a
    POST."""
    calls = stub(monkeypatch)
    assert (cli("post", "9/x"), capsys.readouterr(), calls) == (
        1,
        ("", "::error::<id> must be a decimal run id (got: '9/x')\n"),
        [],
    )


def test_main_restores_the_retry_delays_when_it_raises(slept):
    """Other code in the process retries again. Mutation: restore `RETRY_DELAYS` after the
    `yield` instead of in `finally` -- the usage error leaves it ()."""
    before = _shipmate.RETRY_DELAYS
    with pytest.raises(SystemExit):
        ra.main(["nope"])
    assert _shipmate.RETRY_DELAYS is before
