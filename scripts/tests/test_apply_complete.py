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

BY_WAVE = {
    "wave0": {"apply / stacks/dns / dev-eu": [1]},
    "wave1": {"apply / stacks/app / dev-eu": [2, 3]},
    "wave2": {"apply / stacks/web / dev-eu": [4]},
}
#: Every wave ran.
RAN = {"wave0": "success", "wave1": "success", "wave2": "success"}
_IDS = iter(range(100, 10_000))


def job(name, conclusion, jid=None):
    jid = next(_IDS) if jid is None else jid
    return {
        "id": jid,
        "name": name,
        "conclusion": conclusion,
        "status": "completed",
        "html_url": f"https://github.com/acme/demo/actions/runs/7/job/{jid}",
    }


def row(check, ids, j=None):
    return {
        "check": check,
        "ids": ids,
        "conclusion": None if j is None else j["conclusion"],
        "job_id": None if j is None else j["id"],
        "url": None if j is None else j["html_url"],
        "persisted": None,
    }


def _by_check(rows):
    return {r["check"]: r for r in rows}


def _report(**classes):
    """A `state-read` report: every class empty unless given."""
    return {"state": [], "none": [], "not_applied": [], "not_run": [], "refused": {}, **classes}


def _none_reports(by_wave):
    """Each wave's report with every cell in `none`: no state to save, so no leg needed."""
    return {wave: _report(none=sorted(cells)) for wave, cells in by_wave.items()}


def _state_env(by_wave):
    """`SHIPMATE_WAVE_STATE_JSON` for `_none_reports`, each report a JSON string."""
    return json.dumps({w: json.dumps(r) for w, r in _none_reports(by_wave).items()})


#: The level most tests' cell jobs run at, and this completion job's check run id.
LEVEL = "L0"
SELF_ID = 900


def own(level=LEVEL):
    """This completion job as the listing names it under `level`, whose cells it matches."""
    return job(f"{level} / complete", None, jid=SELF_ID)


def _classify(jobs, by_wave=BY_WAVE, results=RAN, level=LEVEL):
    return apply_complete.classify(
        by_wave, results, [own(level), *jobs], str(SELF_ID), _none_reports(by_wave)
    )


DNS = "apply / stacks/dns / dev-eu"
APP = "apply / stacks/app / dev-eu"
WEB = "apply / stacks/web / dev-eu"
#: A skipped wave's one listing row: GitHub never expands a skipped job's matrix.
SKIPPED_ROW = "deploy / L1 / ${{ matrix.environment }} / ${{ matrix.stack }}"


def test_each_cell_takes_its_matched_jobs_row():
    """Mutation: drop the job id or URL from a row -> the rows differ."""
    jobs = [
        job("L0 / dev-eu / stacks/dns", "success"),
        job("L0 / dev-eu / stacks/app", "failure"),
        job("L0 / dev-eu / stacks/web", "cancelled"),
    ]
    rows, unresolved = _classify(jobs)
    assert unresolved == []
    assert _by_check(rows) == {
        DNS: row(DNS, [1], jobs[0]),
        APP: row(APP, [2, 3], jobs[1]),
        WEB: row(WEB, [4], jobs[2]),
    }


@pytest.mark.parametrize(
    "prefix", ["apply / L0 / dev-eu / stacks/app", "deploy / L2 / dev-eu / stacks/app"]
)
def test_matches_the_nested_reusable_workflow_job_name_suffix(prefix):
    """A called workflow's jobs display as `<caller job> / <called job> / <job>`, and the cell
    job is named `<env> / <stack>`; the same stack in another env matches nothing.
    Mutation: `apply-gate.cell_job_name` builds `<stack> / <env>`."""
    by_wave = {"wave1": BY_WAVE["wave1"]}
    level = prefix.removesuffix(" / dev-eu / stacks/app")
    j = job(prefix, "success")
    assert _classify([j], by_wave, level=level) == ([row(APP, [2, 3], j)], [])
    other = job(prefix.replace("dev-eu", "dev-us"), "success")
    assert _classify([other, j], by_wave, level=level) == ([row(APP, [2, 3], j)], [])
    assert _classify([other], by_wave, level=level) == ([], ["stacks/app / dev-eu"])


def test_a_cell_job_of_another_level_never_completes_the_cell():
    """`state-read` matches the same way (`apply-gate.cell_jobs`). Mutation: drop the level
    from the match (`is_cell_job` alone) -> APP takes the other level's `success`."""
    by_wave = {"wave1": BY_WAVE["wave1"]}
    other = job("deploy / L1 / dev-eu / stacks/app", "success")
    assert _classify([other], by_wave, level="deploy / L0") == ([], ["stacks/app / dev-eu"])


def test_a_listing_without_this_job_matches_no_cell():
    """Mutation: match every level when this job is not listed -> APP takes the job."""
    rows, unresolved = apply_complete.classify(
        {"wave1": BY_WAVE["wave1"]},
        RAN,
        [job("L0 / dev-eu / stacks/app", "success")],
        str(SELF_ID),
        _none_reports(BY_WAVE),
    )
    assert (rows, unresolved) == ([], ["stacks/app / dev-eu"])


@pytest.mark.parametrize(
    ("by_wave", "name", "level"),
    [
        ({"wave0": {"apply / detect / apply": [5]}}, "apply / detect", "apply"),
        (BY_WAVE, "apply / verify / dev-eu / stacks/app", "apply / verify"),
    ],
    ids=["orchestration-job", "non-wave-cell-job"],
)
def test_only_a_wave_job_completes_a_cell(by_wave, name, level):
    """A job outside an `L<n>` env-level can end in ` / <env> / <stack>`: the orchestration
    job `apply / detect` does for env `apply` and root stack `detect`, and it succeeds when
    that cell's wave failed. Mutation: `apply-gate.is_cell_job` drops the `L<n>` segment."""
    rows, unresolved = _classify([job(name, "success")], by_wave, level=level)
    assert (rows, len(unresolved)) == ([], len(by_wave))


def test_a_rerun_success_after_a_failure_completes_the_cell():
    """Mutation: take the last matched job whatever its conclusion -> the failure row."""
    ok = job("L0 / dev-eu / stacks/dns", "success")
    rows, _ = _classify([ok, job("L0 / dev-eu / stacks/dns", "failure")])
    assert _by_check(rows)[DNS] == row(DNS, [1], ok)


def test_never_completes_a_cell_that_was_not_snapshotted():
    rows, _ = _classify(
        [job("L0 / dev-eu / stacks/rogue", "success"), job("L0 / dev-eu / stacks/dns", "failure")]
    )
    assert all(r["conclusion"] != "success" for r in rows)


def test_a_job_of_a_longer_env_name_cannot_complete_the_cell_it_shadows():
    """A "dev-eu / stacks/dns" job shares its trailing characters with env `eu`'s cell job
    "eu / stacks/dns" but is another env, so only a ` / `-boundary-respecting suffix may
    count. Same idiom as scripts/apply-comment's _job_url, pinned in
    test_apply_comment.py::test_job_url_does_not_false_match_on_bare_endswith.
    Mutation: `apply-gate.is_cell_job` becomes a bare `endswith` of `<env> / <stack>`."""
    by_wave = {"wave0": {"apply / stacks/dns / eu": [1]}}
    assert _classify([job("L0 / dev-eu / stacks/dns", "success")], by_wave)[0] == []


def test_suffix_collisions_on_similar_stack_and_env_names_never_complete():
    """Two real cells, "stacks/app" and a bare "app", plus two decoy jobs sharing a trailing
    substring with each without naming them: a different stack ("stacks/my-app") and a
    different env ("prod-dev-eu"). None of it may complete anything.
    Mutation: `apply-gate.is_cell_job` becomes a bare `endswith` of `<env> / <stack>`."""
    by_wave = {"wave0": {"apply / stacks/app / dev-eu": [2, 3], "apply / app / dev-eu": [99]}}
    jobs = [
        job("L0 / dev-eu / stacks/my-app", "success"),
        job("L0 / prod-dev-eu / stacks/app", "success"),
    ]
    assert _classify(jobs, by_wave)[0] == []


def test_a_job_present_without_a_conclusion_is_unresolved_and_gets_an_unresolved_row():
    """The listing lagging a finished job is the whole reason for the retry loop: a null
    conclusion must be reported, never read as "did not run". Its `unresolved` row withholds
    an earlier attempt's outcome in `merge`. Mutations: give it a `Not run` row -> DNS's row
    differs; drop the `unresolved` row -> DNS has none."""
    rows, unresolved = _classify(
        [
            job("L0 / dev-eu / stacks/dns", None),
            job("L0 / dev-eu / stacks/app", "success"),
            job("L0 / dev-eu / stacks/web", "success"),
        ]
    )
    assert unresolved == ["stacks/dns / dev-eu"]
    assert _by_check(rows)[DNS] == {**row(DNS, [1]), "unresolved": True}
    assert sorted(_by_check(rows)) == [APP, DNS, WEB]


#: This completion job, `deploy / L1 / complete`, check run id 900.
OWN = job("deploy / L1 / complete", None, jid=900)


def test_a_cell_of_a_skipped_wave_gets_a_not_run_row():
    """A failed release or a failed earlier wave skips the wave, which never starts. Mutations:
    count every skipped job as the cell's -> APP takes the skipped row; read `skipped` like a
    wave that ran -> APP is unresolved."""
    ok = job("L0 / dev-eu / stacks/dns", "success")
    rows, unresolved = _classify(
        [ok, job(SKIPPED_ROW, "skipped")], results={**RAN, "wave1": "skipped", "wave2": "skipped"}
    )
    assert unresolved == []
    assert rows == [row(DNS, [1], ok), row(APP, [2, 3]), row(WEB, [4])]


def test_a_cancelled_wave_that_never_expanded_is_not_run():
    """A cancel between the release and the wave lists the wave's literal row under this job's
    level, `cancelled`. Mutation: read a `cancelled` wave like one that ran -> APP is
    unresolved."""
    unexpanded = job(SKIPPED_ROW, "cancelled")
    rows, unresolved = apply_complete.classify(
        {"wave1": BY_WAVE["wave1"]},
        {"wave1": "cancelled"},
        [OWN, unexpanded],
        "900",
        _none_reports(BY_WAVE),
    )
    assert (rows, unresolved) == ([row(APP, [2, 3])], [])


@pytest.mark.parametrize(
    "listed",
    [
        pytest.param([OWN], id="no-literal-row"),
        pytest.param(
            [
                OWN,
                job("deploy / L0 / ${{ matrix.environment }} / ${{ matrix.stack }}", "cancelled"),
            ],
            id="another-level",
        ),
        pytest.param([OWN, job(SKIPPED_ROW, "skipped")], id="skipped-row"),
        pytest.param([job(SKIPPED_ROW, "cancelled")], id="own-job-not-listed"),
    ],
)
def test_a_missing_cell_of_a_cancelled_wave_that_may_have_started_is_unresolved(listed):
    """A wave cancelled after its matrix expanded may have applied the cell. Mutations: treat
    every `cancelled` wave as not run -> APP gets a `Not run` row; match the literal row under
    any level -> another-level gets a row; drop the `cancelled` conclusion check -> skipped-row
    gets a row."""
    rows, unresolved = apply_complete.classify(
        {"wave1": BY_WAVE["wave1"]}, {"wave1": "cancelled"}, listed, "900", _none_reports(BY_WAVE)
    )
    assert (rows, unresolved) == ([], ["stacks/app / dev-eu"])


@pytest.mark.parametrize("result", ["success", "failure", "", "weird"])
def test_a_missing_cell_of_a_wave_that_ran_is_unresolved_and_never_not_run(result):
    """A wave that ran lists every cell job, so a missing one is a lagging or lost listing; a
    `Not run` write would turn a completed `success` back to `queued`. Mutation: write `Not
    run` for any missing cell -> APP gets a row."""
    ok = job("L0 / dev-eu / stacks/dns", "success")
    rows, unresolved = _classify([ok], results={"wave0": "success", "wave1": result})
    assert (rows, unresolved) == ([row(DNS, [1], ok)], ["stacks/app / dev-eu"])


def test_a_cancelled_wave_keeps_its_listed_cells_rows():
    """Only cells with no job read the wave's result. Mutation: write `Not run` for every cell
    of a cancelled wave -> APP loses its `cancelled` row."""
    stopped = job("L0 / dev-eu / stacks/app", "cancelled")
    rows, _ = _classify([stopped], {"wave1": BY_WAVE["wave1"]}, {"wave1": "cancelled"})
    assert rows == [row(APP, [2, 3], stopped)]


def _write(conclusion, *, url="https://u/job/9", job_id=9, ids=(5,), repo="acme/demo"):
    r = {
        "check": APP,
        "ids": list(ids),
        "conclusion": conclusion,
        "job_id": job_id,
        "url": url,
        "persisted": None,
    }
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


def test_merge_keeps_a_success_any_attempt_saw_and_otherwise_the_last_row_seen():
    """Replicas differ: a later listing can lose a row an earlier one saw. Mutations: the last
    row wins whatever its conclusion -> DNS reads `Not run`; the first row wins -> APP keeps
    its failure; successes only (`out = {}`) -> APP and WEB have no row."""
    ok = job("L0 / dev-eu / stacks/dns", "success")
    failed = job("L0 / dev-eu / stacks/app", "failure")
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
    ok = job("L0 / dev-eu / stacks/dns", "success")
    later = [{**row(APP, [2, 3]), "unresolved": True}, {**row(DNS, [1]), "unresolved": True}]
    assert apply_complete.merge([row(APP, [2, 3]), row(DNS, [1], ok), *later]) == [
        row(DNS, [1], ok)
    ]


def _run(by_wave, results, jobs):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete")],
        input="\n".join(json.dumps(j) for j in [own(), *jobs]),
        env={
            **os.environ,
            "SHIPMATE_BY_WAVE_JSON": json.dumps(by_wave),
            "SHIPMATE_WAVE_RESULTS_JSON": json.dumps(results),
            "SHIPMATE_MODE": "complete",
            "SHIPMATE_WAVE_STATE_JSON": _state_env(by_wave),
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
        input=jsonl(job("L0 / dev-eu / stacks/dns", "success")),
        env={
            **os.environ,
            "SHIPMATE_BY_WAVE_JSON": json.dumps(BY_WAVE),
            "SHIPMATE_WAVE_RESULTS_JSON": results,
            "SHIPMATE_MODE": "complete",
            "SHIPMATE_WAVE_STATE_JSON": _state_env(BY_WAVE),
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
    done = _run(BY_WAVE, RAN, [job("L0 / dev-eu / stacks/web", None)])
    assert done.returncode == apply_complete.UNRESOLVED_EXIT
    assert done.stderr == (
        apply_complete.RETRY_PREFIX
        + "the jobs listing shows no conclusion for these cells of waves that ran: "
        + "stacks/app / dev-eu, stacks/dns / dev-eu, stacks/web / dev-eu\n"
    )
    assert [json.loads(line) for line in done.stdout.splitlines()] == [
        {**row(WEB, [4]), "unresolved": True}
    ]


def test_main_prints_the_rows_and_exits_zero_when_every_cell_resolved():
    jobs = [
        job("L0 / dev-eu / stacks/dns", "success"),
        job("L0 / dev-eu / stacks/app", "failure"),
    ]
    done = _run(BY_WAVE, {**RAN, "wave2": "skipped"}, jobs)
    assert (done.returncode, done.stderr) == (0, "")
    assert [json.loads(line) for line in done.stdout.splitlines()] == [
        row(DNS, [1], jobs[0]),
        row(APP, [2, 3], jobs[1]),
        row(WEB, [4]),
    ]


def test_plan_mode_writes_the_merged_rows(tmp_path):
    """The last attempt's fetch failed, so it decided nothing; the earlier success and the
    earlier `Cancelled` are both written. Mutation: `merge` keeps successes only (`out = {}`)
    -> WEB gets no write."""
    ok = job("L0 / dev-eu / stacks/dns", "success", jid=11)
    stopped = job("L0 / dev-eu / stacks/web", "cancelled", jid=12)
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


@pytest.mark.parametrize(
    "argv", [["plan"], ["ids", "a"], ["plan", "a", "b"], ["list", "a"], ["stranded"]]
)
def test_a_wrong_argument_list_refuses(argv):
    """Mutation: drop the length check -> `plan` with no files raises IndexError."""
    with pytest.raises(SystemExit) as exc:
        apply_complete.main(argv)
    assert str(exc.value) == (
        "::error::usage: apply-complete [list | plan <rows> | verdict <rows> | stranded <reasons>]"
    )


#: `apply-complete list`'s one gh call, whole.
LIST_ARGV = [
    "api",
    "--paginate",
    "--jq",
    ".jobs[]",
    "repos/acme/demo/actions/runs/999/jobs?per_page=100",
]
LIST_CMD = (
    "command failed (1): gh api --paginate --jq .jobs[] "
    "repos/acme/demo/actions/runs/999/jobs?per_page=100"
)


def _list(tmp_path, **gh):
    env = {**os.environ, "GITHUB_REPOSITORY": "acme/demo", "GITHUB_RUN_ID": "999"}
    body = f'python3 "{(SCRIPTS / "apply-complete").as_posix()}" list'
    return run_with_gh_recorder(tmp_path, body, env, **gh)


@bash_only
def test_list_prints_the_listing_unchanged_from_one_gh_call(tmp_path):
    """Mutations: add `filter=latest` to the path; call through `gh_json`."""
    listing = jsonl(
        job("L0 / dev-eu / dns", "success", jid=1), job("L0 / dev-eu / app", None, jid=2)
    )
    proc, calls = _list(tmp_path, listing=listing)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, listing, "")
    assert calls == [LIST_ARGV]


@bash_only
def test_list_does_not_validate_the_jobs(tmp_path):
    """The classifier reads what the listing holds, as the `jq` call did. Mutation: validate
    the jobs as `listed_jobs` does (exit 75 on a job with no `name` or `status`)."""
    listing = '{"id": 1}\n{"name": "L0 / dev-eu / dns"}\n'
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
    "wave0": {"apply / dns / dev-eu": [11], "apply / app / dev-eu": [22]},
    "wave1": {"apply / web / dev-eu": [33]},
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


def _select(tmp_path, listings, results=None, *, list_stderr="", list_rc=None):
    """Execute the selection step with `listings[n]` served on attempt n; return the process,
    the `{id: entry}` write plan, the attempt count and the env.

    A listing is a jsonl string, ``""`` for an empty one, or ``None`` for a fetch that writes
    one row and then fails with `list_stderr`. The last entry is served for every further
    attempt. With `list_rc`, `apply-complete list` exits it instead.
    """
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for i, listing in enumerate(listings, 1):
        # Every listing that comes back lists this job, which names the cells' level.
        served = listing and jsonl(own()) + listing
        (fixtures / f"{i}.jsonl").write_text(
            "FAIL\n" + jsonl(own()) + _all_success("dns") if listing is None else served,
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
        "SHIPMATE_WAVE_STATE_JSON": _state_env(HARNESS_BY_WAVE),
        "SHIPMATE_MODE": "complete",
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


def _all_success(*cells):
    return jsonl(*(job(f"L0 / dev-eu / {c}", "success") for c in cells))


def _outcomes(plan):
    return {
        cid: e["body"]["status"] if e["title"] is None else e["title"] for cid, e in plan.items()
    }


ALL_DONE = {11: "completed", 22: "completed", 33: "completed"}


@bash_only
def test_the_listing_is_refetched_on_every_attempt(tmp_path):
    # The property that makes this a retry at all: hoisting the fetch above the `for` re-reads
    # one frozen snapshot on every attempt and sleeps the whole budget over it.
    lagging = jsonl(
        job("L0 / dev-eu / dns", "success"),
        job("L0 / dev-eu / app", "success"),
        job("L0 / dev-eu / web", None),
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
    first = jsonl(
        job("L0 / dev-eu / dns", "success"),
        job("L0 / dev-eu / app", "success"),
        job("L0 / dev-eu / web", None),
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
    skipped = jsonl(
        job("L0 / complete", None, jid=900),
        job("L0 / ${{ matrix.environment }} / ${{ matrix.stack }}", result),
    )
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
    first = jsonl(
        job("L0 / dev-eu / dns", "success"),
        {**job("L0 / dev-eu / app", "failure"), "id": "app"},
        job("L0 / dev-eu / web", None),
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
    lagging = jsonl(
        job("L0 / dev-eu / dns", "success"),
        job("L0 / dev-eu / web", None),
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
        "listing shows no conclusion for these cells of waves that ran: app / dev-eu, "
        "web / dev-eu\n"
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
    first = jsonl(job("L0 / dev-eu / dns", "success"), job("L0 / dev-eu / web", None))
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
        "Fail unless every cell's state was saved",
        "Mint App installation token",
        "Write the apply checks",
        "Fail over any cell the jobs listing never resolved",
    ]


def test_a_verdict_checks_no_key_mints_nothing_and_writes_nothing():
    """A failed listing ends the job before the mint, and a missing key already fails at
    verify-app-key. Mutations: restore the mint's `always()`; drop the mode test from the
    mint's `if:`; run the verdict step in `complete` mode."""
    assert [(s.get("name"), s.get("if")) for s in action_steps("apply-complete")] == [
        ("Verify the App key arrived", "${{ inputs.mode != 'verdict' }}"),
        ("List run jobs and decide each apply check's write", None),
        ("Fail unless every cell's state was saved", "${{ inputs.mode == 'verdict' }}"),
        ("Mint App installation token", "${{ inputs.mode != 'verdict' }}"),
        ("Write the apply checks", "${{ inputs.mode != 'verdict' }}"),
        ("Fail over any cell the jobs listing never resolved", None),
    ]


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
        "SHIPMATE_WAVE_STATE_JSON": "${{ inputs.wave-state }}",
        "SHIPMATE_MODE": "${{ inputs.mode }}",
        "SHIPMATE_JOB_ID": "${{ inputs.job-id }}",
    }


def test_a_cancelled_wave_with_a_listed_cell_started_so_its_missing_sibling_is_unresolved():
    """A wave whose matrix expanded lists all its cells at once; the level's literal row is
    wave2's, which never expanded. Mutation: drop the expanded-sibling condition -> wave1's
    missing cell gets a `Not run` row."""
    by_wave = {
        "wave1": {APP: [2, 3], "apply / stacks/api / dev-eu": [5]},
        "wave2": BY_WAVE["wave2"],
    }
    listed = job("deploy / L1 / dev-eu / stacks/api", "cancelled")
    rows, unresolved = apply_complete.classify(
        by_wave,
        {"wave1": "cancelled", "wave2": "cancelled"},
        [OWN, listed, job(SKIPPED_ROW, "cancelled")],
        "900",
        _none_reports(by_wave),
    )
    assert (rows, unresolved) == (
        [row("apply / stacks/api / dev-eu", [5], listed), row(WEB, [4])],
        ["stacks/app / dev-eu"],
    )


#: The saver leg of the cell `APP`, as the jobs listing names it.
APP_LEG = "apply / run / L0 / save-state (dev-eu stacks/app)"
APP_JOB = "apply / run / L0 / dev-eu / stacks/app"
RUN_L0 = "apply / run / L0"


def _lost(reason, cancelled=False):
    word = "Cancelled" if cancelled else "Failed"
    return {"status": "queued"}, f"{word}: state not saved ({reason})"


#: (cell conclusion, wave report or None, leg conclusion or None for no leg, write).
_EVIDENCE = [
    ("success", _report(state=[APP]), "success", "success"),
    ("success", _report(none=[APP]), None, "success"),
    ("success", _report(not_applied=[APP]), None, _lost("the cell succeeded without applying")),
    (
        "success",
        _report(refused={APP: "no state line in the cell's log"}),
        None,
        _lost("no state line in the cell's log"),
    ),
    ("success", _report(state=[APP]), "failure", _lost("the saver job concluded failure")),
    ("success", None, None, _lost("the state reader did not finish")),
    ("success", _report(), None, _lost("the state reader did not classify the cell")),
    ("failure", _report(state=[APP]), "failure", _lost("the saver job concluded failure")),
    ("failure", _report(not_applied=[APP]), None, ({"status": "queued"}, "Failed: boom")),
    ("failure", _report(not_run=[APP]), None, ({"status": "queued"}, "Failed: boom")),
    ("cancelled", _report(not_run=[APP]), None, ({"status": "queued"}, "Cancelled")),
    ("cancelled", _report(state=[APP]), "success", ({"status": "queued"}, "Cancelled")),
    (
        "cancelled",
        _report(state=[APP]),
        "cancelled",
        _lost("the saver job concluded cancelled", cancelled=True),
    ),
]


def _evidence_jobs(conclusion, leg):
    jobs = [own(RUN_L0), job(APP_JOB, conclusion, jid=21)]
    return jobs + ([job(APP_LEG, leg, jid=22)] if leg else [])


@pytest.mark.parametrize(("conclusion", "found", "leg", "expected"), _EVIDENCE)
def test_a_check_completes_only_on_evidence_its_state_was_saved(
    annotations, conclusion, found, leg, expected
):
    """The completion's write per cell conclusion and persistence evidence. The cell's own
    annotation reads `boom`, its leg's none. Mutations: pass a `state` cell without a `success`
    leg; `success` for a `success` cell in `not_applied`; read an absent report as one with no
    reason; `Failed: <error>` winning over `state not saved`; read `not_run` as no
    class."""
    annotations["value"] = [] if leg else [{"annotation_level": "failure", "message": "boom"}]
    rows, unresolved = apply_complete.classify(
        {"wave1": {APP: [2]}},
        {"wave1": "success"},
        _evidence_jobs(conclusion, leg),
        "900",
        {"wave1": found},
    )
    assert unresolved == []
    (entry,) = apply_complete.writes(rows[0], "acme/demo")
    body = {k: v for k, v in entry["body"].items() if k != "details_url"}
    if expected == "success":
        assert (body, entry["title"]) == ({"status": "completed", "conclusion": "success"}, None)
    else:
        assert (body, entry["title"]) == expected


def test_a_failed_legs_error_line_names_the_reason(annotations):
    """Mutation: drop the leg's annotation read (`_lost_reason` returns the fixed reason)."""
    annotations["value"] = [{"annotation_level": "failure", "message": "no artifact matches"}]
    rows, _ = apply_complete.classify(
        {"wave1": {APP: [2]}},
        {"wave1": "success"},
        _evidence_jobs("success", "failure"),
        "900",
        {"wave1": _report(state=[APP])},
    )
    (entry,) = apply_complete.writes(rows[0], "acme/demo")
    assert entry["title"] == "Failed: state not saved (no artifact matches)"
    assert annotations["calls"] == [
        ("repos/acme/demo/check-runs/22/annotations?per_page=100", False)
    ]


@pytest.mark.parametrize("leg", [None, "pending"])
def test_a_state_cell_whose_leg_has_not_concluded_is_unresolved(leg):
    """The listing lags a finished leg as it lags a finished cell, so the caller asks again.
    Mutation: drop the `_leg_lagging` branch (a missing leg then raises on `legs[-1]`, a
    pending one writes `state not saved`)."""
    jobs = [own(RUN_L0), job(APP_JOB, "success")] + ([job(APP_LEG, None)] if leg else [])
    rows, unresolved = apply_complete.classify(
        {"wave1": {APP: [2]}}, {"wave1": "success"}, jobs, "900", {"wave1": _report(state=[APP])}
    )
    assert unresolved == ["stacks/app / dev-eu"]
    assert [r.get("unresolved") for r in rows] == [True]


def test_an_earlier_resolved_row_survives_a_later_lagging_leg():
    """Attempt 1 saw APP and its leg succeed; attempt 2 lists the leg without a conclusion.
    Mutation: give the lagging leg's unresolved row the cell job's `success` (`merge` then
    prefers it and drops it, so APP gets no write)."""
    found = {"wave1": _report(state=[APP])}
    cell = job(APP_JOB, "success")
    by_wave, ran = {"wave1": {APP: [2]}}, {"wave1": "success"}
    done, _ = apply_complete.classify(
        by_wave, ran, [own(RUN_L0), cell, job(APP_LEG, "success")], "900", found
    )
    lagging, _ = apply_complete.classify(
        by_wave, ran, [own(RUN_L0), cell, job(APP_LEG, None)], "900", found
    )
    assert apply_complete.merge(done + lagging) == [row(APP, [2], cell)]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{}",
        "[]",
        json.dumps({**_report(), "state": "x"}),
        json.dumps({**_report(), "refused": []}),
        json.dumps(_report(state=[APP], none=[APP])),
        json.dumps(_report(state=[APP], refused={APP: "r"})),
        json.dumps({**_report(), "extra": []}),
        json.dumps(_report(refused={APP: 1})),
    ],
    ids=["empty", "object", "list", "class", "refused", "twice", "refused-twice", "extra", "why"],
)
def test_a_malformed_report_is_absent(text):
    """Mutations: accept a report naming a cell twice; drop the key-set test."""
    assert apply_complete.report(text) is None


def test_a_well_formed_report_is_read_whole():
    found = _report(state=[APP], refused={DNS: "r"})
    assert apply_complete.report(json.dumps(found)) == found


@pytest.mark.parametrize(
    ("name", "stack", "env", "leg"),
    [
        (APP_LEG, "stacks/app", "dev-eu", True),
        ("L3 / save-state (dev-eu stacks/app)", "stacks/app", "dev-eu", True),
        ("apply / run / L0 / save-state (dev stacks/app)", "stacks/app", "dev-eu", False),
        ("apply / run / L0 / save-state (dev-eu stacks/app)x", "stacks/app", "dev-eu", False),
        ("apply / run / save-state (dev-eu stacks/app)", "stacks/app", "dev-eu", False),
        (APP_JOB, "stacks/app", "dev-eu", False),
        ("apply / run / L0 / save-state (dev-eu .)", ".", "dev-eu", True),
    ],
)
def test_a_leg_is_matched_by_its_whole_name(name, stack, env, leg):
    """Mutations: `search` for `fullmatch`; drop the `L<n>` segment."""
    assert apply_complete.ag.is_leg_job(name, stack, env) is leg


def _main(by_wave, results, jobs, states, mode="complete", level=LEVEL):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete")],
        input="\n".join(json.dumps(j) for j in [own(level), *jobs]),
        env={
            **os.environ,
            "SHIPMATE_BY_WAVE_JSON": json.dumps(by_wave),
            "SHIPMATE_WAVE_RESULTS_JSON": json.dumps(results),
            "SHIPMATE_WAVE_STATE_JSON": states if isinstance(states, str) else json.dumps(states),
            "SHIPMATE_MODE": mode,
            "SHIPMATE_JOB_ID": "900",
        },
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("waves", [1, 8])
def test_one_wave_and_every_wave_take_the_same_evidence(waves):
    """`waveN-complete` passes one wave, `complete` all eight. Mutation: read every wave's
    cells against the first wave's report."""
    by_wave = {f"wave{n}": {f"apply / s{n} / dev-eu": [n]} for n in range(waves)}
    lost = f"apply / s{waves - 1} / dev-eu"
    states = {w: json.dumps(_report(none=sorted(c))) for w, c in by_wave.items()}
    states[f"wave{waves - 1}"] = json.dumps(_report(refused={lost: "why"}))
    jobs = [job(f"L0 / dev-eu / s{n}", "success") for n in range(waves)]
    done = _main(by_wave, dict.fromkeys(by_wave, "success"), jobs, states)
    assert (done.returncode, done.stderr) == (0, "")
    persisted = {r["check"]: r["persisted"] for r in map(json.loads, done.stdout.splitlines())}
    assert persisted == {
        **{c: None for cells in by_wave.values() for c in cells},
        lost: {"reason": "why", "leg_id": None},
    }


@pytest.mark.parametrize("raw", ["", "[]", "x"])
def test_main_refuses_a_wave_state_that_is_no_object(raw):
    """Mutation: read an unparseable `wave-state` as no reports."""
    done = _main(BY_WAVE, RAN, [job("L0 / dev-eu / stacks/dns", "success")], raw)
    assert (done.returncode, done.stderr) == (
        1,
        "::error::wave-state must be a JSON object of state-read reports\n",
    )


@pytest.mark.parametrize("mode", ["", "verdct", "Complete"])
def test_main_refuses_an_unknown_mode(mode):
    """Mutation: drop the mode test."""
    done = _main(BY_WAVE, RAN, [], {}, mode=mode)
    assert (done.returncode, done.stderr) == (1, "::error::mode must be complete or verdict\n")


DB = "apply / stacks/db / dev-eu"
DB_JOB = "apply / run / L0 / dev-eu / stacks/db"


def _verdict_of(tmp_path, rows, by_wave):
    path = tmp_path / "rows.jsonl"
    path.write_text(rows, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete"), "verdict", str(path)],
        env={
            **os.environ,
            "SHIPMATE_BY_WAVE_JSON": json.dumps(by_wave),
            "SHIPMATE_WAVE_RESULTS_JSON": json.dumps(dict.fromkeys(by_wave, "success")),
        },
        capture_output=True,
        text=True,
    )


def _verdict(tmp_path, jobs, states):
    by_wave = {"wave1": {APP: [2], DB: [3]}}
    rows = _main(by_wave, {"wave1": "success"}, jobs, states, "verdict", RUN_L0).stdout
    return _verdict_of(tmp_path, rows, by_wave)


_VERDICT_TAIL = (
    "Never re-run these cells' jobs or their workflow run. A re-run, a shipmate apply comment, or "
    "a merge can apply their reviewed plans again against the state from before the apply. "
    "Before applying or merging any plan of a named stack in its environment, on any pull "
    "request, follow docs/troubleshooting.md section The apply step ran and its state was not "
    "saved.\n"
)


def test_a_verdict_names_each_cell_without_evidence(tmp_path):
    """Mutations: drop the never-re-run instruction; exit 0 on a refused cell."""
    jobs = [job(APP_JOB, "success"), job(APP_LEG, "success"), job(DB_JOB, "success")]
    states = {"wave1": json.dumps(_report(state=[APP], refused={DB: "no state line"}))}
    done = _verdict(tmp_path, jobs, states)
    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr == (
        "::error::no saved state was found for these cells: stacks/db / dev-eu (no state line). "
        + _VERDICT_TAIL
    )


@pytest.mark.parametrize("db", ["success", "failure"])
def test_a_verdict_passes_every_cell_with_evidence_whatever_its_outcome(tmp_path, db):
    """A failed cell whose state was saved passes: its failed wave stops the next release.
    Mutation: count a failed cell as a verdict failure."""
    jobs = [
        job(APP_JOB, "success"),
        job(APP_LEG, "success"),
        job(DB_JOB, db),
        job("apply / run / L0 / save-state (dev-eu stacks/db)", "success"),
    ]
    states = {"wave1": json.dumps(_report(state=[APP, DB]))}
    done = _verdict(tmp_path, jobs, states)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_a_verdict_without_a_report_blocks(tmp_path):
    """Mutation: fail only on refused cells (a missing report passes)."""
    jobs = [job(APP_JOB, "success"), job(DB_JOB, "success")]
    done = _verdict(tmp_path, jobs, {"wave1": ""})
    assert done.returncode == 1
    assert done.stderr == (
        "::error::no saved state was found for these cells: stacks/app / dev-eu (the state reader "
        "did not finish); stacks/db / dev-eu (the state reader did not finish). " + _VERDICT_TAIL
    )


def test_a_verdict_blocks_on_a_cell_no_attempt_resolved(tmp_path):
    """Mutation: skip cells without a merged row."""
    done = _verdict_of(tmp_path, "", {"wave1": {APP: [2]}})
    assert (done.returncode, done.stderr) == (
        1,
        "::error::no saved state was found for these cells: stacks/app / dev-eu (the jobs "
        "listing never resolved the cell). " + _VERDICT_TAIL,
    )
