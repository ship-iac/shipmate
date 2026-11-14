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
    jsonl,
    load_script,
    run_step,
    run_with_gh_recorder,
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
    }


def _by_check(rows):
    return {r["check"]: r for r in rows}


def _classify(jobs, by_wave=BY_WAVE, results=RAN):
    return apply_complete.classify(by_wave, results, jobs)


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
    j = job(prefix, "success")
    assert _classify([j], by_wave) == ([row(APP, [2, 3], j)], [])
    other = job(prefix.replace("dev-eu", "dev-us"), "success")
    assert _classify([other, j], by_wave) == ([row(APP, [2, 3], j)], [])
    assert _classify([other], by_wave) == ([], ["stacks/app / dev-eu"])


@pytest.mark.parametrize(
    ("by_wave", "name"),
    [
        ({"wave0": {"apply / detect / apply": [5]}}, "apply / detect"),
        (BY_WAVE, "apply / verify / dev-eu / stacks/app"),
    ],
    ids=["orchestration-job", "non-wave-cell-job"],
)
def test_only_a_wave_job_completes_a_cell(by_wave, name):
    """A job outside an `L<n>` env-level can end in ` / <env> / <stack>`: the orchestration
    job `apply / detect` does for env `apply` and root stack `detect`, and it succeeds when
    that cell's wave failed. Mutation: `apply-gate.is_cell_job` drops the `L<n>` segment."""
    rows, unresolved = _classify([job(name, "success")], by_wave)
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


def test_a_job_present_without_a_conclusion_is_unresolved_and_gets_no_row():
    """The listing lagging a finished job is the whole reason for the retry loop: a null
    conclusion must be reported, never read as "did not run". Mutation: give it a `Not run`
    row -> DNS has a row."""
    rows, unresolved = _classify(
        [
            job("L0 / dev-eu / stacks/dns", None),
            job("L0 / dev-eu / stacks/app", "success"),
            job("L0 / dev-eu / stacks/web", "success"),
        ]
    )
    assert (sorted(_by_check(rows)), unresolved) == ([APP, WEB], ["stacks/dns / dev-eu"])


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
        {"wave1": BY_WAVE["wave1"]}, {"wave1": "cancelled"}, [OWN, unexpanded], "900"
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
        {"wave1": BY_WAVE["wave1"]}, {"wave1": "cancelled"}, listed, "900"
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


def test_merge_keeps_a_success_any_attempt_saw_and_otherwise_the_last_attempt():
    """Replicas differ: a later listing can lose a success an earlier one saw. Mutations: the
    last attempt alone -> DNS reads `Not run`; every attempt, last row wins -> the same; take
    WEB's failure from the first attempt -> WEB has a row."""
    ok = job("L0 / dev-eu / stacks/dns", "success")
    failed = job("L0 / dev-eu / stacks/app", "failure")
    first = [row(DNS, [1], ok), row(APP, [2, 3], failed), row(WEB, [4], failed)]
    last = [row(DNS, [1]), row(APP, [2, 3])]
    assert apply_complete.merge(first + last, last) == [row(APP, [2, 3]), row(DNS, [1], ok)]


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
    assert done.stdout == ""


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
    """The last attempt's fetch failed, so it decided nothing; only the earlier success is
    written. Mutation: swap the two file arguments -> WEB's earlier `Cancelled` is written."""
    ok = job("L0 / dev-eu / stacks/dns", "success", jid=11)
    stopped = job("L0 / dev-eu / stacks/web", "cancelled", jid=12)
    every = tmp_path / "all.jsonl"
    last = tmp_path / "last.jsonl"
    every.write_text(jsonl(row(DNS, [1], ok), row(WEB, [4], stopped)), encoding="utf-8")
    last.write_text("", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete"), "plan", str(every), str(last)],
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
    ]


@pytest.mark.parametrize("argv", [["plan"], ["ids", "a", "b"], ["plan", "a", "b", "c"]])
def test_a_wrong_argument_list_refuses(argv):
    """Mutation: drop the length check -> `plan` with no files raises IndexError."""
    with pytest.raises(SystemExit) as exc:
        apply_complete.main(argv)
    assert str(exc.value) == "::error::usage: apply-complete [plan <all rows> <last rows>]"


def test_the_app_token_is_minted_after_the_listing_and_selection_step():
    # The mint step runs after the step that can fail or abort, so the token's lifetime never
    # spans work that may not need it.
    steps = action_steps("apply-complete")
    listing = next(i for i, s in enumerate(steps) if "actions/runs" in (s.get("run") or ""))
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


def _loop_body():
    return next(
        s["run"] for s in action_steps("apply-complete") if "actions/runs" in s.get("run", "")
    )


def _fail_body():
    return next(
        s["run"]
        for s in action_steps("apply-complete")
        if s.get("name", "").startswith("Fail over")
    )


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


# `gh`, `sleep` and `python3` are bash functions, which bash resolves before PATH, so nothing
# is installed and `sleep` only records its argument. `python3` forwards to the interpreter
# running the suite, so the real scripts/apply-complete decides every cell.

GH_STUB = r"""
gh() {
  n=$(( $(cat "$ATTEMPT") + 1 ))
  printf '%s' "$n" > "$ATTEMPT"
  f="$FIXTURES/$n.jsonl"
  [ -f "$f" ] || f="$FIXTURES/$LAST_FIXTURE.jsonl"
  if [ "$(head -n 1 "$f")" = FAIL ]; then tail -n +2 "$f"; return 1; fi
  cat "$f"
}
sleep() { echo "$1" >> "$SLEEPS"; }
python3() { "$PYEXE" "$@"; }
"""

HARNESS_BY_WAVE = {
    "wave0": {"apply / dns / dev-eu": [11], "apply / app / dev-eu": [22]},
    "wave1": {"apply / web / dev-eu": [33]},
}
HARNESS_RAN = {"wave0": "success", "wave1": "success"}


def _select(tmp_path, listings, results=None):
    """Execute the selection step with `listings[n]` served on attempt n; return the process,
    the `{id: entry}` write plan, the attempt count and the env.

    A listing is a jsonl string, ``""`` for an empty one, or ``None`` for a
    fetch that writes one row and then fails. The last entry is served for
    every further attempt.
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
    env = {
        **os.environ,
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
        "PYEXE": sys.executable.replace("\\", "/"),
    }
    proc = run_step(tmp_path, GH_STUB + _loop_body(), env, timeout=120)
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
    may add a completion; it may never revoke one. Mutation: feed `plan` the last attempt as
    both files -> dns and app get no write."""
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

    fail = run_step(tmp_path, GH_STUB + _fail_body(), env, timeout=120)
    assert fail.returncode == 1
    errors = [ln for ln in fail.stdout.splitlines() if "::error::" in ln]
    assert errors == [
        (
            "::error::the retries were exhausted and these apply checks are left pending: the jobs "
            "listing shows no conclusion for these cells of waves that ran: app / dev-eu, "
            "web / dev-eu "
        )
    ]


@bash_only
def test_a_fully_resolved_run_leaves_nothing_for_the_stranded_step_to_fail_on(tmp_path):
    proc, plan, _, env = _select(tmp_path, [_all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert _outcomes(plan) == ALL_DONE
    assert run_step(tmp_path, GH_STUB + _fail_body(), env, timeout=120).returncode == 0


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
