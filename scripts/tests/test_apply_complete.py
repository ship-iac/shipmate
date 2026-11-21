"""Unit tests for scripts/apply-complete and its action."""

import json
import os
import subprocess
import sys

import pytest
from _loader import (
    ENGINE,
    SCRIPTS,
    action_steps,
    bash_only,
    fail_script,
    jsonl,
    load_script,
    read_calls,
    run_step,
    run_with_gh_recorder,
    write_python3_shim,
    write_stub,
)

apply_complete = load_script("apply-complete")
deploy_detect = load_script("deploy-detect")

BY_WAVE = {
    "wave0": {"apply / dev-eu / stacks/dns": [1]},
    "wave1": {"apply / dev-eu / stacks/app": [2, 3]},
    "wave2": {"apply / dev-eu / stacks/web": [4]},
}
#: Every wave ran.
RAN = {"wave0": "success", "wave1": "success", "wave2": "success"}
_IDS = iter(range(100, 10_000))
#: The steps of an `apply` job whose apply step ran.
APPLIED = [
    {"name": "Pre-apply check", "conclusion": "success"},
    {"name": "Apply the cell", "conclusion": "success"},
]
#: The steps of an `apply` job whose pre-apply check refused: it never applied.
REFUSED = [
    {"name": "Pre-apply check", "conclusion": "failure"},
    {"name": "Apply the cell", "conclusion": "failure"},
]


def job(name, conclusion, jid=None, *, attempt=1, steps=APPLIED):
    jid = next(_IDS) if jid is None else jid
    return {
        "id": jid,
        "name": name,
        "conclusion": conclusion,
        "status": "in_progress" if conclusion is None else "completed",
        "run_attempt": attempt,
        "started_at": f"2026-11-20T10:{jid // 60 % 60:02d}:{jid % 60:02d}Z",
        "steps": steps,
        "html_url": f"https://github.com/acme/demo/actions/runs/7/job/{jid}",
    }


def cell(prefix, apply, save="success", *, attempt=1, steps=APPLIED):
    """The `apply` and `save` jobs of the cell `prefix`, `<level> / <env> / <stack>`."""
    return [
        job(f"{prefix} / apply", apply, attempt=attempt, steps=steps),
        job(f"{prefix} / save", save, attempt=attempt, steps=[]),
    ]


def row(check, ids, j=None):
    return {
        "check": check,
        "ids": ids,
        "conclusion": None if j is None else j["conclusion"],
        "job_id": None if j is None else j["id"],
        "url": None if j is None else j["html_url"],
    }


def held(check, ids):
    return {**row(check, ids), "held": True}


def _by_check(rows):
    return {r["check"]: r for r in rows}


#: This completion job, `L0 / complete`, check run id 900, in run attempt 1.
OWN = job("L0 / complete", None, jid=900)


def _classify(jobs, by_wave=BY_WAVE, results=RAN, own=OWN):
    return apply_complete.classify(by_wave, results, [own, *jobs], str(own["id"]))


DNS = "apply / dev-eu / stacks/dns"
APP = "apply / dev-eu / stacks/app"
WEB = "apply / dev-eu / stacks/web"
#: A skipped wave's one listing row: GitHub never expands a skipped job's matrix.
SKIPPED_ROW = "deploy / L1 / ${{ matrix.environment }} / ${{ matrix.stack }}"
SKIPPED_L0 = "L0 / ${{ matrix.environment }} / ${{ matrix.stack }}"


def test_each_cell_takes_its_apply_jobs_row():
    """Mutations: drop the job id or URL from a row -> the rows differ; take the `save` job's
    row -> every row differs."""
    dns, app, web = (
        cell("L0 / dev-eu / stacks/dns", "success"),
        cell("L0 / dev-eu / stacks/app", "failure"),
        cell("L0 / dev-eu / stacks/web", "cancelled"),
    )
    rows, unresolved = _classify([*dns, *app, *web])
    assert unresolved == []
    assert _by_check(rows) == {
        DNS: row(DNS, [1], dns[0]),
        APP: row(APP, [2, 3], app[0]),
        WEB: row(WEB, [4], web[0]),
    }


@pytest.mark.parametrize("level", ["apply / L0", "deploy / L2"])
def test_matches_the_cell_jobs_under_this_jobs_level(level):
    """A called workflow's jobs display as `<caller job> / <called job> / <job>`, and the cell
    jobs are `<env> / <stack> / apply|save`; the same stack in another env matches nothing.
    Mutation: `apply-gate.cell_job_name` builds `<stack> / <env>`."""
    own = job(f"{level} / complete", None, jid=901)
    by_wave = {"wave1": BY_WAVE["wave1"]}
    mine = cell(f"{level} / dev-eu / stacks/app", "success")
    other = cell(f"{level} / dev-us / stacks/app", "success")
    assert _classify(mine, by_wave, own=own) == ([row(APP, [2, 3], mine[0])], [])
    assert _classify([*other, *mine], by_wave, own=own) == ([row(APP, [2, 3], mine[0])], [])
    assert _classify(other, by_wave, own=own) == ([], ["dev-eu / stacks/app"])


def test_a_cell_of_another_level_is_not_this_jobs():
    """Mutation: match the cell jobs at any level (`apply-gate.cell_part` instead of
    `cell_rows`) -> APP takes the `L1` row."""
    by_wave = {"wave1": BY_WAVE["wave1"]}
    assert _classify(cell("L1 / dev-eu / stacks/app", "success"), by_wave) == (
        [],
        ["dev-eu / stacks/app"],
    )


@pytest.mark.parametrize(
    ("by_wave", "prefix"),
    [
        ({"wave0": {"apply / run / detect": [5]}}, "apply / run / detect"),
        (BY_WAVE, "apply / verify / dev-eu / stacks/app"),
    ],
    ids=["orchestration-job", "non-wave-cell-job"],
)
def test_only_a_wave_job_completes_or_holds_a_cell(by_wave, prefix):
    """A job outside an `L<n>` env-level can end in ` / <env> / <stack> / apply`: an
    orchestration job `apply / run / detect / apply` would for env `run` and root stack
    `detect`. Its unsaved apply holds nothing. Mutation: `apply-gate.cell_part` drops the
    `L<n>` segment -> the cell is held."""
    rows, unresolved = _classify(cell(prefix, "success", "failure"), by_wave)
    assert (rows, len(unresolved)) == ([], len(by_wave))


def test_the_write_reads_this_attempts_rows():
    """Attempt 2 re-ran the cell after attempt 1 failed. Mutation: read the rows of every attempt
    (the last listed is attempt 1's) -> DNS reads the failure."""
    own = job("L0 / complete", None, jid=902, attempt=2)
    again = cell("L0 / dev-eu / stacks/dns", "success", attempt=2)
    first = cell("L0 / dev-eu / stacks/dns", "failure")
    rows, _ = _classify([*again, *first], {"wave0": BY_WAVE["wave0"]}, own=own)
    assert rows == [row(DNS, [1], again[0])]


def test_never_completes_a_cell_that_was_not_snapshotted():
    rows, _ = _classify(
        [
            *cell("L0 / dev-eu / stacks/rogue", "success"),
            *cell("L0 / dev-eu / stacks/dns", "failure"),
        ]
    )
    assert all(r["conclusion"] != "success" for r in rows)


def test_a_job_of_a_longer_env_name_cannot_complete_or_hold_the_cell_it_shadows():
    """A "dev-eu / stacks/dns" job shares its trailing characters with env `eu`'s cell job
    "eu / stacks/dns" but is another env, so only a ` / `-boundary-respecting suffix may
    count. Same idiom as scripts/apply-comment's _job_url, pinned in
    test_apply_comment.py::test_job_url_does_not_false_match_on_bare_endswith.
    Mutation: `apply-gate.cell_part` drops its ` / ` boundary -> the cell is held."""
    by_wave = {"wave0": {"apply / eu / stacks/dns": [1]}}
    assert _classify(cell("L0 / dev-eu / stacks/dns", "success", "failure"), by_wave)[0] == []


def test_suffix_collisions_on_similar_stack_and_env_names_never_complete():
    """Two real cells, "stacks/app" and a bare "app", plus two decoy cells sharing a trailing
    substring with each without naming them: a different stack ("stacks/my-app") and a
    different env ("prod-dev-eu"). None of it may complete or hold anything.
    Mutation: `apply-gate.cell_part` drops its ` / ` boundary -> both cells are held."""
    by_wave = {"wave0": {"apply / dev-eu / stacks/app": [2, 3], "apply / dev-eu / app": [99]}}
    jobs = [
        *cell("L0 / dev-eu / stacks/my-app", "success", "failure"),
        *cell("L0 / prod-dev-eu / stacks/app", "success", "failure"),
    ]
    assert _classify(jobs, by_wave)[0] == []


def test_a_job_present_without_a_conclusion_is_unresolved_and_gets_an_unresolved_row():
    """The listing lagging a finished job is the whole reason for the retry loop: a null
    conclusion must be reported, never read as "did not run". Its `unresolved` row withholds
    an earlier attempt's outcome in `merge`. Mutations: give it a `Not run` row -> DNS's row
    differs; drop the `unresolved` row -> DNS has none."""
    rows, unresolved = _classify(
        [
            *cell("L0 / dev-eu / stacks/dns", None, None),
            *cell("L0 / dev-eu / stacks/app", "success"),
            *cell("L0 / dev-eu / stacks/web", "success"),
        ]
    )
    assert unresolved == ["dev-eu / stacks/dns"]
    assert _by_check(rows)[DNS] == {**row(DNS, [1]), "unresolved": True}
    assert sorted(_by_check(rows)) == [APP, DNS, WEB]


@pytest.mark.parametrize(
    "listed",
    [
        pytest.param(cell("L0 / dev-eu / stacks/app", "success", None), id="save-lagging"),
        pytest.param(cell("L0 / dev-eu / stacks/app", "success")[:1], id="save-not-listed"),
        pytest.param(cell("L0 / dev-eu / stacks/app", "success")[1:], id="apply-not-listed"),
    ],
)
def test_a_lagging_save_is_unresolved_not_held(listed):
    """A `save` row the listing has not concluded or not shown yet reads as an unsaved apply.
    Mutation: decide the hold before the lag -> APP is held (save-lagging, save-not-listed)."""
    rows, unresolved = _classify(listed, {"wave1": BY_WAVE["wave1"]})
    assert (rows, unresolved) == (
        [{**row(APP, [2, 3]), "unresolved": True}],
        ["dev-eu / stacks/app"],
    )


def test_an_applied_cell_whose_save_failed_is_held():
    """Mutation: write `queued` for apply `success` and save `failure` (drop the hold) -> APP
    takes the apply row."""
    rows, unresolved = _classify(
        cell("L0 / dev-eu / stacks/app", "success", "failure"), {"wave1": BY_WAVE["wave1"]}
    )
    assert (rows, unresolved) == ([held(APP, [2, 3])], [])


def test_a_rerun_after_an_unsaved_attempt_is_held():
    """ "Re-run all jobs" after attempt 1 applied and did not save: attempt 2's pre-apply refuses
    and its `save` fails on the lookup. Mutation: read the hold from this attempt's rows only
    (`unsaved(..., before=None)` over attempt 2) -> APP reads `Failed` of the `save` job."""
    own = job("L0 / complete", None, jid=903, attempt=2)
    jobs = [
        *cell("L0 / dev-eu / stacks/app", "success", "failure"),
        *cell("L0 / dev-eu / stacks/app", "failure", "failure", attempt=2, steps=REFUSED),
    ]
    rows, _ = _classify(jobs, {"wave1": BY_WAVE["wave1"]}, own=own)
    assert rows == [held(APP, [2, 3])]


def test_a_skipped_wave_after_an_unsaved_attempt_is_held():
    """Mutation: write `Not run` for a cell of a skipped wave before the hold -> APP reads
    `Not run`."""
    own = job("L0 / complete", None, jid=904, attempt=2)
    jobs = [
        *cell("L0 / dev-eu / stacks/app", "success", "failure"),
        job(SKIPPED_L0, "skipped", attempt=2),
    ]
    rows, _ = _classify(jobs, {"wave1": BY_WAVE["wave1"]}, {"wave1": "skipped"}, own=own)
    assert rows == [held(APP, [2, 3])]


@pytest.mark.parametrize(
    ("apply", "title"),
    [("failure", "failure"), ("cancelled", "cancelled"), ("timed_out", "timed_out")],
)
def test_a_saved_cell_writes_its_apply_jobs_outcome(apply, title):
    """Mutation: take the `save` job's row when it succeeded -> the conclusion is `success`."""
    jobs = cell("L0 / dev-eu / stacks/app", apply)
    rows, _ = _classify(jobs, {"wave1": BY_WAVE["wave1"]})
    assert rows == [row(APP, [2, 3], jobs[0])]
    assert rows[0]["conclusion"] == title


def test_a_failed_save_of_a_cell_that_never_applied_fails_with_the_save_job():
    """The pre-apply check refused, so nothing applied, and the save failed. Mutation: take the
    `apply` job's row -> APP's job id is the `apply` job's."""
    apply, save = cell("L0 / dev-eu / stacks/app", "failure", "failure", steps=REFUSED)
    rows, _ = _classify([apply, save], {"wave1": BY_WAVE["wave1"]})
    assert rows == [row(APP, [2, 3], save) | {"conclusion": "failure"}]


def test_a_cell_cancelled_while_pending_is_cancelled():
    """Cancelled while waiting on its group: the `apply` row ended with no steps and no `save`
    row is listed. Mutation: retry it (unresolved) -> APP has an unresolved row."""
    pending = job("L0 / dev-eu / stacks/app / apply", "cancelled", steps=[])
    rows, unresolved = _classify([pending], {"wave1": BY_WAVE["wave1"]})
    assert (rows, unresolved) == ([row(APP, [2, 3], pending)], [])


@pytest.mark.parametrize(
    "steps", [pytest.param(APPLIED, id="with-steps"), pytest.param([], id="no-steps")]
)
def test_an_ended_apply_with_no_save_listed_is_unresolved(steps):
    """Mutation: write `Cancelled` for any `apply` row with no `save` row -> APP has a row."""
    ended = job("L0 / dev-eu / stacks/app / apply", "failure", steps=steps)
    rows, unresolved = _classify([ended], {"wave1": BY_WAVE["wave1"]})
    assert (rows, unresolved) == (
        [{**row(APP, [2, 3]), "unresolved": True}],
        ["dev-eu / stacks/app"],
    )


#: This completion job at level `deploy / L1`, check run id 900.
OWN_L1 = job("deploy / L1 / complete", None, jid=900)


def test_a_cell_of_a_skipped_wave_gets_a_not_run_row():
    """A failed release or a failed earlier wave skips the wave, which never starts. Mutations:
    count every skipped job as the cell's -> APP takes the skipped row; read `skipped` like a
    wave that ran -> APP is unresolved."""
    ok = cell("L0 / dev-eu / stacks/dns", "success")
    rows, unresolved = _classify(
        [*ok, job(SKIPPED_L0, "skipped")], results={**RAN, "wave1": "skipped", "wave2": "skipped"}
    )
    assert unresolved == []
    assert rows == [row(DNS, [1], ok[0]), row(APP, [2, 3]), row(WEB, [4])]


def test_a_cancelled_wave_that_never_expanded_is_not_run():
    """A cancel between the release and the wave lists the wave's literal row under this job's
    level, `cancelled`. Mutation: read a `cancelled` wave like one that ran -> APP is
    unresolved."""
    unexpanded = job(SKIPPED_ROW, "cancelled")
    rows, unresolved = apply_complete.classify(
        {"wave1": BY_WAVE["wave1"]}, {"wave1": "cancelled"}, [OWN_L1, unexpanded], "900"
    )
    assert (rows, unresolved) == ([row(APP, [2, 3])], [])


@pytest.mark.parametrize(
    "listed",
    [
        pytest.param([OWN_L1], id="no-literal-row"),
        pytest.param(
            [
                OWN_L1,
                job("deploy / L0 / ${{ matrix.environment }} / ${{ matrix.stack }}", "cancelled"),
            ],
            id="another-level",
        ),
        pytest.param([OWN_L1, job(SKIPPED_ROW, "skipped")], id="skipped-row"),
        pytest.param([job(SKIPPED_ROW, "cancelled")], id="own-job-not-listed"),
        pytest.param(
            [{**OWN_L1, "run_attempt": 2}, job(SKIPPED_ROW, "cancelled")], id="another-attempt"
        ),
    ],
)
def test_a_missing_cell_of_a_cancelled_wave_that_may_have_started_is_unresolved(listed):
    """A wave cancelled after its matrix expanded may have applied the cell. Mutations: treat
    every `cancelled` wave as not run -> APP gets a `Not run` row; match the literal row under
    any level -> another-level gets a row; drop the `cancelled` conclusion check -> skipped-row
    gets a row; match the literal row of any attempt -> another-attempt gets a row."""
    rows, unresolved = apply_complete.classify(
        {"wave1": BY_WAVE["wave1"]}, {"wave1": "cancelled"}, listed, "900"
    )
    assert (rows, unresolved) == ([], ["dev-eu / stacks/app"])


@pytest.mark.parametrize("result", ["success", "failure", "", "weird"])
def test_a_missing_cell_of_a_wave_that_ran_is_unresolved_and_never_not_run(result):
    """A wave that ran lists every cell job, so a missing one is a lagging or lost listing; a
    `Not run` write would turn a completed `success` back to `queued`. Mutation: write `Not
    run` for any missing cell -> APP gets a row."""
    ok = cell("L0 / dev-eu / stacks/dns", "success")
    rows, unresolved = _classify(ok, results={"wave0": "success", "wave1": result})
    assert (rows, unresolved) == ([row(DNS, [1], ok[0])], ["dev-eu / stacks/app"])


def test_a_cancelled_wave_keeps_its_listed_cells_rows():
    """Only cells with no job read the wave's result. Mutation: write `Not run` for every cell
    of a cancelled wave -> APP loses its `cancelled` row."""
    stopped = cell("L0 / dev-eu / stacks/app", "cancelled", steps=REFUSED)
    rows, _ = _classify(stopped, {"wave1": BY_WAVE["wave1"]}, {"wave1": "cancelled"})
    assert rows == [row(APP, [2, 3], stopped[0])]


def _write(conclusion, *, url="https://u/job/9", job_id=9, ids=(5,), repo="acme/demo"):
    r = {"check": APP, "ids": list(ids), "conclusion": conclusion, "job_id": job_id, "url": url}
    return apply_complete.writes(r, repo)


@pytest.fixture
def annotations(monkeypatch):
    """`gh_json` for the annotation read: returns `notes["value"]`, raising it when it is an
    exception; each path is appended to `notes["calls"]`."""
    notes = {"value": [], "calls": []}

    def gh_json(path, run=None, *, retry=True):
        notes["calls"].append((path, retry))
        if isinstance(notes["value"], BaseException):
            raise notes["value"]
        return notes["value"]

    monkeypatch.setattr(apply_complete, "gh_json", gh_json)
    return notes


def test_success_completes_with_the_job_link_and_the_record_title():
    """Mutations: drop `details_url`; send a title string -> `title` is not None."""
    assert _write("success", ids=(5, 6)) == [
        {
            "id": 5,
            "body": {
                "status": "completed",
                "conclusion": "success",
                "details_url": "https://u/job/9",
            },
            "title": None,
        },
        {
            "id": 6,
            "body": {
                "status": "completed",
                "conclusion": "success",
                "details_url": "https://u/job/9",
            },
            "title": None,
        },
    ]


def test_failure_returns_to_queued_with_the_error_and_the_job_link(annotations):
    """Mutations: drop `details_url` from the failure body; read with retries -> `retry` True."""
    annotations["value"] = [
        {"annotation_level": "warning", "message": "deprecated"},
        {"annotation_level": "failure", "message": "Error: boom\nsecond line"},
    ]
    assert _write("failure") == [
        {
            "id": 5,
            "body": {"status": "queued", "details_url": "https://u/job/9"},
            "title": "Failed: Error: boom",
        }
    ]
    assert annotations["calls"] == [
        ("repos/acme/demo/check-runs/9/annotations?per_page=100", False)
    ]


@pytest.mark.parametrize(
    ("conclusion", "title"),
    [
        ("cancelled", "Cancelled"),
        ("skipped", "Not run"),
        (None, "Not run"),
        ("timed_out", "Not applied (timed_out)"),
        ("action_required", "Not applied (action_required)"),
        ("neutral", "Not applied (neutral)"),
        ("stale", "Not applied (stale)"),
        ("something-new", "Not applied (something-new)"),
    ],
)
def test_every_other_outcome_returns_to_queued_with_its_title(conclusion, title):
    """Never `success` and no job link. Mutations: map `cancelled` to `Not run`; map an unknown
    conclusion to success; map `timed_out` to `Not run`; add the job link to every write."""
    assert _write(conclusion) == [{"id": 5, "body": {"status": "queued"}, "title": title}]


@pytest.mark.parametrize(
    ("message", "title"),
    [
        ("Error: one\ntwo", "Failed: Error: one"),
        ("a\x1b[31mred\x00\x07b", "Failed: a[31mredb"),
        ("x" * 121, "Failed: " + "x" * 120),
        ("x" * 120, "Failed: " + "x" * 120),
        ("\n", "Failed"),
    ],
    ids=["newline", "control", "cap-121", "cap-120", "blank"],
)
def test_the_error_line_is_sanitised(annotations, message, title):
    """Mutations: keep control characters; cap at 121; take the whole message."""
    annotations["value"] = [{"annotation_level": "failure", "message": message}]
    assert apply_complete.error_line(9, "acme/demo") == title


def test_no_failure_annotation_titles_failed(annotations):
    """Mutation: take the first annotation of any level -> `Failed: deprecated`."""
    annotations["value"] = [{"annotation_level": "warning", "message": "deprecated"}]
    assert apply_complete.error_line(9, "acme/demo") == "Failed"


def test_a_failed_annotation_read_titles_failed(annotations, capsys):
    """The title only names the error, so the read degrades. Mutation: let the read's exit
    propagate -> SystemExit."""
    annotations["value"] = SystemExit("::error::command failed (1): gh api x\nHTTP 403")
    assert apply_complete.error_line(9, "acme/demo") == "Failed"
    assert capsys.readouterr().err == (
        "::warning::could not read the annotations of job 9: "
        "command failed (1): gh api x%0AHTTP 403\n"
    )


def test_a_job_id_that_is_not_an_integer_reads_nothing(annotations):
    """Mutation: drop the `isinstance` check -> a read with `None` in the path."""
    assert apply_complete.error_line(None, "acme/demo") == "Failed"
    assert annotations["calls"] == []


#: The run page of run 999 of acme/demo.
HELD_URL = "https://github.com/acme/demo/actions/runs/999"


@pytest.fixture
def run_env(monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")


def test_a_held_row_holds_each_check_in_progress_linked_to_the_run_page(run_env):
    """Mutations: write `queued` -> the status differs; link the `save` job
    (`<run page>/job/<id>`) -> the link differs; drop the `held` key -> `patch-checks` drops
    the entry on a re-run."""
    held_body = {"status": "in_progress", "details_url": HELD_URL}
    assert apply_complete.writes(held(APP, [5, 6]), "acme/demo") == [
        {"id": 5, "body": held_body, "title": "State not saved", "held": True},
        {"id": 6, "body": held_body, "title": "State not saved", "held": True},
    ]


@pytest.mark.parametrize(
    ("link", "cause"),
    [(HELD_URL, ("live", "999")), (HELD_URL + "/job/12", ("unknown", "5"))],
    ids=["run-page", "job-page"],
)
def test_the_holder_reader_names_the_run_from_the_held_link(run_env, link, cause):
    """The deploy reads the holding run from the link: it strips the run-page prefix and needs a
    run id, so a job link reads `unknown`. Pins the reader's parse of both links; the held write's
    link is pinned whole above. Mutation: link the `save` job -> the held write reads
    `unknown`."""
    entry = apply_complete.writes(held(APP, [5]), "acme/demo")[0]
    assert entry["body"]["details_url"] == HELD_URL
    check = {"id": 5, "details_url": link}
    assert deploy_detect.ad.holder_state("acme/demo", {}, check, {"999": ("live", [])}) == cause


def test_merge_ranks_a_hold_below_a_success_and_above_any_other_row():
    """A success from any attempt wins; else a hold from any attempt, over an earlier or later
    failure and a later unresolved listing. Mutations: drop the held overlay -> APP takes the
    unresolved row (no write); overlay holds after successes -> DNS is held."""
    ok = job("L0 / dev-eu / stacks/dns / apply", "success")
    failed = job("L0 / dev-eu / stacks/app / apply", "failure")
    every = [
        row(APP, [2, 3], failed),
        held(APP, [2, 3]),
        row(DNS, [1], ok),
        row(APP, [2, 3], failed),
        held(DNS, [1]),
        {**row(APP, [2, 3]), "unresolved": True},
    ]
    assert apply_complete.merge(every) == [held(APP, [2, 3]), row(DNS, [1], ok)]


def test_merge_keeps_a_success_any_attempt_saw_and_otherwise_the_last_row_seen():
    """Replicas differ: a later listing can lose a row an earlier one saw. Mutations: the last
    row wins whatever its conclusion -> DNS reads `Not run`; the first row wins -> APP keeps
    its failure; successes only (`out = {}`) -> APP and WEB have no row."""
    ok = job("L0 / dev-eu / stacks/dns / apply", "success")
    failed = job("L0 / dev-eu / stacks/app / apply", "failure")
    first = [row(DNS, [1], ok), row(APP, [2, 3], failed), row(WEB, [4], failed)]
    last = [row(DNS, [1]), row(APP, [2, 3])]
    assert apply_complete.merge(first + last) == [
        row(APP, [2, 3]),
        row(DNS, [1], ok),
        row(WEB, [4], failed),
    ]


def test_merge_withholds_an_earlier_outcome_a_later_attempt_lists_unresolved():
    """Attempt 1 wrote APP `Not run`; a later attempt lists APP's job without a conclusion, so
    APP gets no write. A success still wins over a later unresolved row. Mutation: drop the
    `unresolved` filter -> APP's unresolved row is returned (a `Not run` write)."""
    ok = job("L0 / dev-eu / stacks/dns / apply", "success")
    later = [{**row(APP, [2, 3]), "unresolved": True}, {**row(DNS, [1]), "unresolved": True}]
    assert apply_complete.merge([row(APP, [2, 3]), row(DNS, [1], ok), *later]) == [
        row(DNS, [1], ok)
    ]


def _run(by_wave, results, jobs):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete")],
        input="\n".join(json.dumps(j) for j in jobs),
        env={
            **os.environ,
            "SHIPMATE_BY_WAVE_JSON": json.dumps(by_wave),
            "SHIPMATE_WAVE_RESULTS_JSON": json.dumps(results),
            "SHIPMATE_JOB_ID": "900",
        },
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("results", ["", "[]", "{}"])
def test_main_refuses_wave_results_that_are_no_non_empty_object(results):
    """An empty `wave-results` would trace back, and `{}` would decide no cell and exit 0.
    Mutation: drop the check (a traceback for `""`, exit 0 for `[]` and `{}`)."""
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete")],
        input=jsonl(OWN, *cell("L0 / dev-eu / stacks/dns", "success")),
        env={
            **os.environ,
            "SHIPMATE_BY_WAVE_JSON": json.dumps(BY_WAVE),
            "SHIPMATE_WAVE_RESULTS_JSON": results,
            "SHIPMATE_JOB_ID": "900",
        },
        capture_output=True,
        text=True,
    )
    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr == "::error::wave-results must be a non-empty JSON object of wave results\n"


def test_main_exits_with_the_retry_code_and_names_the_unresolved_cells():
    """A missing cell of a wave that ran retries like a lagging one. Mutation: exit 0 when a
    cell is unresolved -> returncode 0."""
    done = _run(BY_WAVE, RAN, [OWN, *cell("L0 / dev-eu / stacks/web", None, None)])
    assert done.returncode == apply_complete.UNRESOLVED_EXIT
    assert done.stderr == (
        apply_complete.RETRY_PREFIX
        + "the jobs listing shows no conclusion for these cells of waves that ran: "
        + "dev-eu / stacks/app, dev-eu / stacks/dns, dev-eu / stacks/web\n"
    )
    assert [json.loads(line) for line in done.stdout.splitlines()] == [
        {**row(WEB, [4]), "unresolved": True}
    ]


def test_main_prints_the_rows_and_exits_zero_when_every_cell_resolved():
    dns, app = (
        cell("L0 / dev-eu / stacks/dns", "success"),
        cell("L0 / dev-eu / stacks/app", "failure"),
    )
    done = _run(BY_WAVE, {**RAN, "wave2": "skipped"}, [OWN, *dns, *app])
    assert (done.returncode, done.stderr) == (0, "")
    assert [json.loads(line) for line in done.stdout.splitlines()] == [
        row(DNS, [1], dns[0]),
        row(APP, [2, 3], app[0]),
        row(WEB, [4]),
    ]


def test_plan_mode_writes_the_merged_rows(tmp_path):
    """The last attempt's fetch failed, so it decided nothing; the earlier success and the
    earlier `Cancelled` are both written. Mutation: `merge` keeps successes only (`out = {}`)
    -> WEB gets no write."""
    ok = job("L0 / dev-eu / stacks/dns / apply", "success", jid=11)
    stopped = job("L0 / dev-eu / stacks/web / apply", "cancelled", jid=12)
    every = tmp_path / "all.jsonl"
    every.write_text(jsonl(row(DNS, [1], ok), row(WEB, [4], stopped)), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete"), "plan", str(every)],
        env={**os.environ, "GITHUB_REPOSITORY": "acme/demo"},
        capture_output=True,
        text=True,
    )
    assert (done.returncode, done.stderr) == (0, "")
    assert [json.loads(line) for line in done.stdout.splitlines()] == [
        {
            "id": 1,
            "body": {"status": "completed", "conclusion": "success", "details_url": ok["html_url"]},
            "title": None,
        },
        {"id": 4, "body": {"status": "queued"}, "title": "Cancelled"},
    ]


def test_plan_mode_writes_a_hold_over_an_earlier_failure(tmp_path):
    """Mutation: drop the held overlay in `merge` -> APP is written `Failed`."""
    failed = {**job("L0 / dev-eu / stacks/app / apply", "failure"), "id": "app"}
    every = tmp_path / "all.jsonl"
    every.write_text(jsonl(held(APP, [2]), row(APP, [2], failed)), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete"), "plan", str(every)],
        env={
            **os.environ,
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "999",
        },
        capture_output=True,
        text=True,
    )
    assert (done.returncode, done.stderr) == (0, "")
    assert [json.loads(line) for line in done.stdout.splitlines()] == [
        {
            "id": 2,
            "body": {"status": "in_progress", "details_url": HELD_URL},
            "title": "State not saved",
            "held": True,
        }
    ]


@pytest.mark.parametrize(
    "argv", [["plan"], ["ids", "a"], ["plan", "a", "b"], ["list", "a"], ["stranded"]]
)
def test_a_wrong_argument_list_refuses(argv):
    """Mutation: drop the length check -> `plan` with no files raises IndexError."""
    with pytest.raises(SystemExit) as exc:
        apply_complete.main(argv)
    assert str(exc.value) == (
        "::error::usage: apply-complete [list | plan <rows> | stranded <reasons>]"
    )


#: `apply-complete list`'s one gh call, whole.
LIST_ARGV = [
    "api",
    "--paginate",
    "--jq",
    ".jobs[]",
    "repos/acme/demo/actions/runs/999/jobs?filter=all&per_page=100",
]
LIST_CMD = (
    "command failed (1): gh api --paginate --jq .jobs[] "
    "repos/acme/demo/actions/runs/999/jobs?filter=all&per_page=100"
)


def _list(tmp_path, **gh):
    env = {**os.environ, "GITHUB_REPOSITORY": "acme/demo", "GITHUB_RUN_ID": "999"}
    body = f'python3 "{(SCRIPTS / "apply-complete").as_posix()}" list'
    return run_with_gh_recorder(tmp_path, body, env, **gh)


@bash_only
def test_list_prints_the_listing_unchanged_from_one_gh_call(tmp_path):
    """Mutations: add `filter=latest` to the path; call through `gh_json`."""
    listing = jsonl(
        job("L0 / dev-eu / dns / apply", "success", jid=1),
        job("L0 / dev-eu / app / apply", None, jid=2),
    )
    proc, calls = _list(tmp_path, listing=listing)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, listing, "")
    assert calls == [LIST_ARGV]


@bash_only
def test_list_does_not_validate_the_jobs(tmp_path):
    """The classifier reads what the listing holds, as the `jq` call did. Mutation: validate
    the jobs as `listed_jobs` does (exit 75 on a job with no `name` or `status`)."""
    listing = '{"id": 1}\n{"name": "L0 / dev-eu / dns / apply"}\n'
    proc, _ = _list(tmp_path, listing=listing)
    assert (proc.returncode, proc.stdout) == (0, listing)


@bash_only
def test_a_failed_list_prints_its_cause_once_escaped_and_retries(tmp_path):
    """gh's stderr can hold lines that would start workflow commands, broken by any line end
    `splitlines` knows; the cause prints as one escaped `::error::` and one `retry: ` line on
    one line, raw lines nowhere. Mutations: print `e.code` raw; exit 1; drop the `retry: ` line;
    pass the cause to `print_annotated` unjoined (two annotations); join `one_line` on CR and LF
    only (two annotations)."""
    stderr = "::error::boom\r\n::warning::careful 100%0A\nHTTP 502\u2028::error::sep"
    proc, calls = _list(tmp_path, listing="", list_rc=1, list_stderr=stderr)
    cause = f"{LIST_CMD} | ::error::boom | ::warning::careful 100%0A | HTTP 502 | ::error::sep"
    escaped = cause.replace("%", "%25")
    assert (proc.returncode, proc.stdout) == (apply_complete.UNRESOLVED_EXIT, "")
    assert proc.stderr == f"::error::{escaped}\n{apply_complete.RETRY_PREFIX}{cause}\n"
    assert calls == [LIST_ARGV]


@bash_only
def test_a_permanent_list_failure_refuses_at_once(tmp_path):
    """A 403 or a 404 is no lag: asking again only spends the retry budget. Mutation: exit 75
    for every failure -> exit 75 and a `retry: ` line."""
    proc, calls = _list(tmp_path, listing="", list_rc=1, list_stderr="gh: Not Found (HTTP 404)")
    assert (proc.returncode, proc.stdout) == (1, "")
    assert proc.stderr == f"::error::{LIST_CMD} | gh: Not Found (HTTP 404)\n"
    assert calls == [LIST_ARGV]


@bash_only
def test_an_empty_list_retries(tmp_path):
    """Mutation: exit 0 on an empty listing."""
    proc, _ = _list(tmp_path, listing="")
    assert (proc.returncode, proc.stdout) == (apply_complete.UNRESOLVED_EXIT, "")
    assert proc.stderr == (
        "retry: the run jobs listing came back empty; cannot tell which wave cells succeeded\n"
    )


def test_the_app_token_is_minted_after_the_listing_and_selection_step():
    # The mint step runs after the step that can fail or abort, so the token's lifetime never
    # spans work that may not need it.
    steps = action_steps("apply-complete")
    listing = next(i for i, s in enumerate(steps) if s.get("name") == LISTING_STEP)
    mint = next(
        i for i, s in enumerate(steps) if "create-github-app-token" in (s.get("uses") or "")
    )
    assert mint > listing


def test_the_stranded_failure_is_reported_after_the_writes():
    """The point of deferring it: failing at selection time discards the writes this run did
    decide. Mutation: move the fail-over before `Write the apply checks`."""
    steps = action_steps("apply-complete")
    patch = next(i for i, s in enumerate(steps) if s.get("name") == "Write the apply checks")
    fail = next(i for i, s in enumerate(steps) if s.get("name", "").startswith("Fail over"))
    assert fail > patch


LISTING_STEP = "List run jobs and decide each apply check's write"


def _loop_body():
    return _step(LISTING_STEP)["run"]


def _fail_body():
    return _step("Fail over any cell the jobs listing never resolved")["run"]


def _case_arms(body):
    """The `case "$rc"` arms of the retry loop, as pattern -> body text."""
    arms = {}
    for line in body.splitlines():
        stripped = line.strip()
        pat, sep, rest = stripped.partition(")")
        if sep and rest.rstrip().endswith(";;"):
            arms[pat] = rest.rstrip()[:-2].strip()
    return arms


def test_the_retry_arm_sleeps_and_only_the_fallthrough_arm_leaves_the_loop():
    # Pinning the digits alone is satisfiable by swapping the two arms' bodies, which turns
    # "ask me again" into an instant deploy-halting failure.
    arms = _case_arms(_loop_body())
    retry = arms[f"{apply_complete.UNRESOLVED_EXIT}"]
    assert "sleep" in retry
    assert "exit" not in retry and "break" not in retry
    assert "exit" in arms["*"]
    assert "break" in arms["0"] and "sleep" not in arms["0"]


def test_diagnostics_are_echoed_after_the_retry_loop():
    # Inside the loop it would repeat the same warning once per attempt, and the file is
    # rewritten every attempt, so only the last one describes the outcome.
    lines = [ln.strip() for ln in _loop_body().splitlines()]
    done = max(i for i, ln in enumerate(lines) if ln == "done")
    echo = max(i for i, ln in enumerate(lines) if 'diagnostics.txt" >&2' in ln)
    assert echo > done


# `sleep` is a bash function, which bash resolves before PATH, so it only records its argument.
# `python3` is `write_python3_shim`'s, wrapped to log each script and subcommand it runs, so the
# real scripts decide every cell and their `gh` calls reach `GH_STUB`.

SLEEP_STUB = 'sleep() { echo "$1" >> "$SLEEPS"; }\n'

#: A bash `gh`: a `--paginate` listing serves `$FIXTURES/<n>.jsonl` on its nth call, the last
#: fixture after that; one starting `FAIL` prints the rest and `$FAKE_LIST_STDERR` and exits 1.
#: Any other call is recorded in `$CALLS` and answers an `in_progress` check.
GH_STUB = r"""
if [ "$2" = --paginate ]; then
  n=$(( $(cat "$ATTEMPT") + 1 ))
  printf '%s' "$n" > "$ATTEMPT"
  f="$FIXTURES/$n.jsonl"
  [ -f "$f" ] || f="$FIXTURES/$LAST_FIXTURE.jsonl"
  if [ "$(head -n 1 "$f")" = FAIL ]; then
    tail -n +2 "$f"
    printf '%s' "${FAKE_LIST_STDERR:-}" >&2
    exit 1
  fi
  cat "$f"
  exit 0
fi
printf '%s\n' "$@" -- >> "$CALLS"
echo '{"status": "in_progress"}'
"""

HARNESS_BY_WAVE = {
    "wave0": {"apply / dev-eu / dns": [11], "apply / dev-eu / app": [22]},
    "wave1": {"apply / dev-eu / web": [33]},
}
HARNESS_RAN = {"wave0": "success", "wave1": "success"}


def _bin(tmp_path, list_rc=None):
    """`bin/` with the python3 shim, `GH_STUB` and a `python3` that logs `<script> <subcommand>`
    (`stdin` for none) to `script-calls`; with `list_rc`, `apply-complete list` exits it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_python3_shim(bin_dir)
    write_stub(bin_dir, "gh", GH_STUB)
    if list_rc is not None:
        fail_script(bin_dir, "apply-complete", list_rc, "listing broke", subcommand="list")
    (bin_dir / "python3").rename(bin_dir / "python3-logged")
    log = (tmp_path / "script-calls").as_posix()
    write_stub(
        bin_dir,
        "python3",
        f'printf \'%s %s\\n\' "${{1##*/}}" "${{2:-stdin}}" >> "{log}"\n'
        f'exec "{(bin_dir / "python3-logged").as_posix()}" "$@"',
    )
    return bin_dir


def _script_calls(tmp_path):
    path = tmp_path / "script-calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _select(tmp_path, listings, results=None, *, list_stderr="HTTP 502", list_rc=None):
    """Execute the selection step with `listings[n]` served on attempt n; return the process,
    the `{id: entry}` write plan, the attempt count and the env.

    A listing is a jsonl string, ``""`` for an empty one, or ``None`` for a fetch that writes
    one row and then fails with `list_stderr`. The last entry is served for every further
    attempt. With `list_rc`, `apply-complete list` exits it instead.
    """
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for i, listing in enumerate(listings, 1):
        (fixtures / f"{i}.jsonl").write_text(
            "FAIL\n" + _all_success("dns") if listing is None else listing,
            encoding="utf-8",
            newline="\n",
        )
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    (tmp_path / "attempt").write_text("0", encoding="utf-8")
    bin_dir = _bin(tmp_path, list_rc)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GH_TOKEN": "x",
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_RUN_ID": "999",
        "GITHUB_ACTION_PATH": str(ENGINE / "actions" / "apply-complete").replace("\\", "/"),
        "RUNNER_TEMP": str(runner_temp).replace("\\", "/"),
        "SHIPMATE_BY_WAVE_JSON": json.dumps(HARNESS_BY_WAVE),
        "SHIPMATE_WAVE_RESULTS_JSON": json.dumps(results or HARNESS_RAN),
        "SHIPMATE_JOB_ID": "900",
        "FIXTURES": str(fixtures).replace("\\", "/"),
        "LAST_FIXTURE": str(len(listings)),
        "ATTEMPT": str(tmp_path / "attempt").replace("\\", "/"),
        "SLEEPS": str(tmp_path / "sleeps").replace("\\", "/"),
        "CALLS": (tmp_path / "gh-calls").as_posix(),
        "FAKE_LIST_STDERR": list_stderr,
    }
    proc = run_step(tmp_path, SLEEP_STUB + _loop_body(), env, timeout=120)
    writes_path = runner_temp / "writes.jsonl"
    lines = writes_path.read_text(encoding="utf-8").splitlines() if writes_path.exists() else []
    plan = {e["id"]: e for e in map(json.loads, lines)}
    attempts = int((tmp_path / "attempt").read_text(encoding="utf-8"))
    return proc, plan, attempts, env


def _listing(*jobs):
    """A listing of this completion job, `OWN`, and `jobs`."""
    return jsonl(OWN, *jobs)


def _all_success(*cells):
    return _listing(*(j for c in cells for j in cell(f"L0 / dev-eu / {c}", "success")))


def _outcomes(plan):
    return {
        cid: e["body"]["status"] if e["title"] is None else e["title"] for cid, e in plan.items()
    }


ALL_DONE = {11: "completed", 22: "completed", 33: "completed"}


@bash_only
def test_the_listing_is_refetched_on_every_attempt(tmp_path):
    # The property that makes this a retry at all: hoisting the fetch above the `for` re-reads
    # one frozen snapshot on every attempt and sleeps the whole budget over it.
    lagging = _listing(
        *cell("L0 / dev-eu / dns", "success"),
        *cell("L0 / dev-eu / app", "success"),
        *cell("L0 / dev-eu / web", None, None),
    )
    proc, plan, attempts, _ = _select(
        tmp_path, [lagging, lagging, _all_success("dns", "app", "web")]
    )
    assert proc.returncode == 0, proc.stderr
    assert attempts == 3
    assert _outcomes(plan) == ALL_DONE


@bash_only
def test_an_empty_listing_is_retried_rather_than_failed_on_the_first_attempt(tmp_path):
    proc, plan, attempts, _ = _select(tmp_path, ["", "", _all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert attempts == 3
    assert _outcomes(plan) == ALL_DONE


@bash_only
def test_a_failed_fetch_never_feeds_its_half_written_file_to_the_selection(tmp_path):
    # A truncated listing is never read: the cells it holds would be written from it.
    proc, plan, attempts, _ = _select(tmp_path, [None, _all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert attempts == 2
    assert _outcomes(plan) == ALL_DONE


@bash_only
def test_a_listing_matching_no_cell_at_all_is_retried_and_writes_nothing_meanwhile(tmp_path):
    # Cells of waves that ran with no job retry through the loop rather than around it.
    proc, plan, attempts, _ = _select(
        tmp_path, [_all_success("rogue"), _all_success("dns", "app", "web")]
    )
    assert proc.returncode == 0, proc.stderr
    assert attempts == 2
    assert _outcomes(plan) == ALL_DONE


@bash_only
def test_a_success_seen_by_one_attempt_survives_a_staler_later_listing(tmp_path):
    """Replicas differ, so a later read can be missing a row an earlier one had. A later read
    may add a completion; it may never revoke one. Mutation: feed `plan` the last attempt's
    `attempt-rows.jsonl` -> dns and app get no write."""
    first = _listing(
        *cell("L0 / dev-eu / dns", "success"),
        *cell("L0 / dev-eu / app", "success"),
        *cell("L0 / dev-eu / web", None, None),
    )
    staler = _all_success("web")  # dns and app have vanished from this replica.
    proc, plan, _, _ = _select(tmp_path, [first, staler])
    assert proc.returncode == 0, proc.stderr
    assert _outcomes(plan) == ALL_DONE


@bash_only
@pytest.mark.parametrize("result", ["skipped", "cancelled"])
def test_a_wave_that_did_not_run_writes_not_run_at_once(tmp_path, result):
    """A failed release, or a cancel between the release and the wave, lists no cell job, only
    the wave's literal row under this job's level. Mutation: treat a `skipped` or `cancelled`
    wave as one that ran -> 14 attempts and no write."""
    skipped = _listing(job(SKIPPED_L0, result))
    proc, plan, attempts, _ = _select(
        tmp_path, [skipped], results={"wave0": result, "wave1": "skipped"}
    )
    assert (proc.returncode, attempts) == (0, 1), proc.stderr
    assert _outcomes(plan) == {11: "Not run", 22: "Not run", 33: "Not run"}


@bash_only
def test_a_failure_one_attempt_saw_survives_later_empty_listings(tmp_path):
    """Attempt 1 lists app `failure` and web lagging; every later attempt's listing comes back
    empty, so the retries run out. App still gets its `Failed` write. (A job id that is not an
    integer keeps the annotation read out of the step.) Mutation: the step feeds `plan` only
    the last attempt's `attempt-rows.jsonl` -> app gets no write."""
    app = cell("L0 / dev-eu / app", "failure")
    first = _listing(
        *cell("L0 / dev-eu / dns", "success"),
        {**app[0], "id": "app"},
        app[1],
        *cell("L0 / dev-eu / web", None, None),
    )
    proc, plan, attempts, _ = _select(tmp_path, [first, ""])
    assert (proc.returncode, attempts) == (0, 14), proc.stderr
    assert _outcomes(plan) == {11: "completed", 22: "Failed"}


@bash_only
def test_exhaustion_keeps_the_decided_writes_and_then_fails_naming_only_the_stranded(tmp_path):
    """One cell resolves, one of a wave that ran is missing from the listing, one never
    resolves. The decided write must still reach the write step; the other two get no write,
    since a missing cell of a wave that ran may already be completed, and both are named in
    the `::error::`. Mutation: write `Not run` for a missing cell whatever its wave's result
    -> app gets a write.

    The waits back off from 2 s to a 110 s total, and the last attempt does not sleep.
    Mutations: the first delay 2 -> 10; one later delay 10 -> 20; the last attempt sleeps
    (`set -u` aborts on the missing delay); `attempts` drops its `+ 1`."""
    lagging = _listing(
        *cell("L0 / dev-eu / dns", "success"),
        *cell("L0 / dev-eu / web", None, None),
    )
    proc, plan, attempts, env = _select(tmp_path, [lagging])
    assert proc.returncode == 0, proc.stderr
    assert attempts == 14
    sleeps = (tmp_path / "sleeps").read_text(encoding="utf-8").splitlines()
    assert sleeps == ["2", "3", "5", "10", "10", "10", "10", "10", "10", "10", "10", "10", "10"]
    assert _outcomes(plan) == {11: "completed"}

    fail = run_step(tmp_path, _fail_body(), env, timeout=120)
    assert fail.returncode == 1
    assert fail.stderr == (
        "::error::the retries were exhausted and these apply checks are left pending: the jobs "
        "listing shows no conclusion for these cells of waves that ran: dev-eu / app, "
        "dev-eu / web\n"
    )


@bash_only
def test_a_fully_resolved_run_leaves_nothing_for_the_stranded_step_to_fail_on(tmp_path):
    proc, plan, _, env = _select(tmp_path, [_all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert _outcomes(plan) == ALL_DONE
    assert run_step(tmp_path, _fail_body(), env, timeout=120).returncode == 0


def _write_body():
    return _step("Write the apply checks")["run"]


@bash_only
def test_a_listing_failing_after_one_resolved_cell_writes_it_and_fails_naming_the_cause(tmp_path):
    """Attempt 1 resolves dns and leaves the rest; every later listing fails. The decided write
    still lands, the diagnostics log line shows gh's text as gh wrote it, and the final refusal
    names the listing's cause escaped, so the runner decodes gh's literal `%0A` back to itself,
    not to a line break. Mutations: `|| true` on the `list` call (the classifier's retry line
    replaces the cause); drop the `cat` of `attempt-rows.jsonl` into `rows.jsonl` (no write); no
    escaping in the fail-over step (`%0A`); escape the cause in `list`'s `retry: ` line instead
    (the log line shows `%250A`)."""
    first = _listing(*cell("L0 / dev-eu / dns", "success"), *cell("L0 / dev-eu / web", None, None))
    proc, plan, attempts, env = _select(tmp_path, [first, None], list_stderr="HTTP 502 a%0Ab")
    assert (proc.returncode, attempts) == (0, 14), proc.stderr
    assert _outcomes(plan) == {11: "completed"}
    cause = f"{LIST_CMD} | HTTP 502 a%0Ab"
    assert f"{apply_complete.RETRY_PREFIX}{cause}" in proc.stderr.splitlines()
    stranded = tmp_path / "runner" / "stranded.txt"
    assert stranded.read_text(encoding="utf-8") == cause + "\n"

    env = {**env, "SHIPMATE_DEPLOY": "true", "SHIPMATE_PR_NUMBER": "", "GITHUB_RUN_ATTEMPT": "1"}
    write = run_step(tmp_path, _write_body(), env, timeout=120)
    assert write.returncode == 0, write.stderr
    patched = [c[3] for c in read_calls(tmp_path / "gh-calls") if "PATCH" in c]
    assert patched == ["repos/acme/demo/check-runs/11"]

    fail = run_step(tmp_path, _fail_body(), env, timeout=120)
    assert fail.returncode == 1
    assert fail.stderr == (
        "::error::the retries were exhausted and these apply checks are left pending: "
        f"{cause.replace('%', '%25')}\n"
    )


@bash_only
def test_the_fail_over_escapes_a_stranded_cell_name(tmp_path):
    """The classifier's `retry: ` line names cells raw; a stack path holding `%0A` stays itself
    in the refusal, and a CR or LF in the reasons becomes a ` | ` separator. Mutations: skip
    `one_line` (the line ends print as `%0D` and `%0A`); bypass `command_data` in
    `print_annotated` (`%0A`); print the refusal to stdout instead of raising it."""
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    (runner_temp / "stranded.txt").write_bytes(
        b"the jobs listing shows no conclusion for these cells of waves that ran: "
        b"stacks/a%0Ab\r / dev-eu\nthe other\n"
    )
    bin_dir = _bin(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "RUNNER_TEMP": runner_temp.as_posix(),
        "GITHUB_ACTION_PATH": (ENGINE / "actions" / "apply-complete").as_posix(),
    }
    fail = run_step(tmp_path, _fail_body(), env, timeout=120)
    assert (fail.returncode, fail.stdout) == (1, "")
    assert fail.stderr == (
        "::error::the retries were exhausted and these apply checks are left pending: the jobs "
        "listing shows no conclusion for these cells of waves that ran: stacks/a%250Ab |  / "
        "dev-eu | the other\n"
    )


@bash_only
def test_a_permanent_list_failure_ends_the_loop_without_sleeping(tmp_path):
    """The real `list` exits 1 on a 404, and the loop passes it on. Mutation: `list` exits 75
    for every failure -> 14 attempts."""
    proc, plan, attempts, _ = _select(tmp_path, [None], list_stderr="gh: Not Found (HTTP 404)")
    assert (proc.returncode, attempts, plan) == (1, 1, {})
    assert not (tmp_path / "sleeps").exists()


@bash_only
def test_any_other_list_exit_propagates_at_once(tmp_path):
    """Mutation: treat every non-zero `list` exit as 75 (14 attempts)."""
    proc, plan, _, _ = _select(tmp_path, [_all_success("dns", "app", "web")], list_rc=3)
    assert proc.returncode == 3
    assert plan == {}
    assert _script_calls(tmp_path) == ["apply-complete list"]


@bash_only
def test_the_classifier_runs_only_on_a_listing_that_came_back(tmp_path):
    """An empty and a failed listing are not classified. Mutation: run the classifier after
    every `list`."""
    proc, plan, attempts, _ = _select(tmp_path, ["", None, _all_success("dns", "app", "web")])
    assert (proc.returncode, attempts) == (0, 3), proc.stderr
    assert _outcomes(plan) == ALL_DONE
    assert _script_calls(tmp_path) == [
        "apply-complete list",
        "apply-complete list",
        "apply-complete list",
        "apply-complete stdin",
        "apply-complete plan",
    ]


def _step(name):
    return next(s for s in action_steps("apply-complete") if s.get("name") == name)


def test_the_steps_are_exactly_these():
    """Mutation: restore the revert step -> the list gains it."""
    assert [s.get("name") for s in action_steps("apply-complete")] == [
        "Verify the App key arrived",
        "List run jobs and decide each apply check's write",
        "Mint App installation token",
        "Write the apply checks",
        "Fail over any cell the jobs listing never resolved",
    ]


def test_no_step_carries_an_if():
    """A failed listing ends the job before the mint, and a missing key already fails at
    verify-app-key. Mutation: restore the mint's `always()`."""
    assert [s.get("if") for s in action_steps("apply-complete")] == [None] * 5


def test_the_write_token_can_read_the_pull_request_and_nothing_more():
    """Mutations: drop `permission-pull-requests: read`; grant it `write`."""
    assert _step("Mint App installation token")["with"] == {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-checks": "write",
        "permission-pull-requests": "read",
    }


def test_the_write_step_passes_the_deploy_flag_and_the_pull_request():
    """Mutations: drop `SHIPMATE_DEPLOY`; drop `SHIPMATE_PR_NUMBER`; write with the job token."""
    assert _step("Write the apply checks")["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_DEPLOY": "${{ inputs.deploy }}",
        "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
    }


def _run_action_step(tmp_path, name, *, plan, **gh):
    """Run step `name` with a recording gh; `plan` is the write plan in writes.jsonl."""
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    (runner_temp / "writes.jsonl").write_text(jsonl(*plan), encoding="utf-8")
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_RUN_ATTEMPT": "1",
        "SHIPMATE_DEPLOY": "true",
        "SHIPMATE_PR_NUMBER": "",
        "GITHUB_ACTION_PATH": (ENGINE / "actions" / "apply-complete").as_posix(),
        "RUNNER_TEMP": runner_temp.as_posix(),
    }
    proc, calls = run_with_gh_recorder(tmp_path, _step(name)["run"], env, **gh)
    return proc, [c[3] for c in calls], runner_temp


@bash_only
def test_the_write_step_writes_the_plan(tmp_path):
    """The bodies are pinned in-process by `test_patch_checks.py`; this pins the step's input
    to writes.jsonl. Mutation: feed `rows.jsonl` instead -> no write."""
    plan = [
        {"id": 1, "body": {"status": "completed", "conclusion": "success"}},
        {"id": 2, "body": {"status": "queued"}},
    ]
    proc, patched, _ = _run_action_step(tmp_path, "Write the apply checks", plan=plan)
    assert proc.returncode == 0, proc.stderr
    assert patched == ["repos/acme/demo/check-runs/1", "repos/acme/demo/check-runs/2"]


def test_the_listing_step_reads_this_job_and_the_wave_results():
    """Mutations: drop `SHIPMATE_JOB_ID`; map `SHIPMATE_WAVE_RESULTS_JSON` to `by-wave`."""
    assert _step("List run jobs and decide each apply check's write")["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_BY_WAVE_JSON": "${{ inputs.by-wave }}",
        "SHIPMATE_WAVE_RESULTS_JSON": "${{ inputs.wave-results }}",
        "SHIPMATE_JOB_ID": "${{ inputs.job-id }}",
    }


def test_a_cancelled_wave_with_a_listed_cell_started_so_its_missing_sibling_is_unresolved():
    """A wave whose matrix expanded lists all its cells at once; the level's literal row is
    wave2's, which never expanded. Mutation: drop the expanded-sibling condition -> wave1's
    missing cell gets a `Not run` row."""
    by_wave = {
        "wave1": {APP: [2, 3], "apply / dev-eu / stacks/api": [5]},
        "wave2": BY_WAVE["wave2"],
    }
    listed, saved = cell("deploy / L1 / dev-eu / stacks/api", "cancelled")
    rows, unresolved = apply_complete.classify(
        by_wave,
        {"wave1": "cancelled", "wave2": "cancelled"},
        [OWN_L1, listed, saved, job(SKIPPED_ROW, "cancelled")],
        "900",
    )
    assert (rows, unresolved) == (
        [row("apply / dev-eu / stacks/api", [5], listed), row(WEB, [4])],
        ["dev-eu / stacks/app"],
    )
