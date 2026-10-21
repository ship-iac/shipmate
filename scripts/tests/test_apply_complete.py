"""Unit tests for scripts/apply-complete."""

import json
import os
import subprocess
import sys

import pytest
from _loader import ENGINE, SCRIPTS, action_steps, bash_only, jsonl, load_script, run_step

apply_complete = load_script("apply-complete")


SNAP = {
    "apply / stacks/dns / dev-eu": [1],
    "apply / stacks/app / dev-eu": [2, 3],
}


def job(name, conclusion):
    return {"name": name, "conclusion": conclusion, "status": "completed"}


def test_completes_only_cells_whose_wave_job_succeeded():
    jobs = [
        job("L0 / dev-eu / stacks/dns", "success"),
        job("L0 / dev-eu / stacks/app", "failure"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [], [], ["stacks/app / dev-eu"])


@pytest.mark.parametrize(
    "prefix", ["apply / L0 / dev-eu / stacks/app", "deploy / L2 / dev-eu / stacks/app"]
)
def test_matches_the_nested_reusable_workflow_job_name_suffix(prefix):
    """A called workflow's jobs display as `<caller job> / <called job> / <job>`, and the cell
    job is named `<env> / <stack>`; the same stack in another env completes nothing.
    Mutation: `apply-gate.cell_job_name` builds `<stack> / <env>`."""
    jobs = [job(prefix, "success")]
    assert apply_complete.to_complete(SNAP, jobs) == ([2, 3], [], ["stacks/dns / dev-eu"], [])
    jobs = [job(prefix.replace("dev-eu", "dev-us"), "success")]
    assert apply_complete.to_complete(SNAP, jobs)[0] == []


@pytest.mark.parametrize(
    ("snap", "name"),
    [
        ({"apply / detect / apply": [5]}, "apply / detect"),
        (SNAP, "apply / verify / dev-eu / stacks/app"),
        (SNAP, "apply / L0 / ${{ matrix.environment }} / ${{ matrix.stack }}"),
    ],
    ids=["orchestration-job", "non-wave-cell-job", "skipped-wave-row"],
)
def test_only_a_wave_job_completes_a_cell(snap, name):
    """A job outside an `L<n>` env-level can end in ` / <env> / <stack>`: the orchestration
    job `apply / detect` does for env `apply` and root stack `detect`, and it succeeds when
    that cell's wave failed. Mutation: `apply-gate.is_cell_job` drops the `L<n>` segment."""
    assert apply_complete.to_complete(snap, [job(name, "success")])[0] == []


def test_a_cancelled_or_missing_job_leaves_the_check_pending():
    jobs = [
        job("L0 / dev-eu / stacks/dns", "cancelled"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == (
        [],
        [],
        ["stacks/app / dev-eu"],
        ["stacks/dns / dev-eu"],
    )


def test_a_rerun_success_after_a_failure_completes_the_cell():
    jobs = [
        job("L0 / dev-eu / stacks/dns", "failure"),
        job("L0 / dev-eu / stacks/dns", "success"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [], ["stacks/app / dev-eu"], [])


def test_never_completes_a_cell_that_was_not_snapshotted():
    jobs = [job("L0 / dev-eu / stacks/rogue", "success")]
    assert apply_complete.to_complete(SNAP, jobs)[0] == []


def test_a_job_of_a_longer_env_name_cannot_complete_the_cell_it_shadows():
    """A "dev-eu / stacks/dns" job shares its trailing characters with env `eu`'s cell job
    "eu / stacks/dns" but is another env, so only a ` / `-boundary-respecting suffix may
    count. Same idiom as scripts/apply-comment's _job_url, pinned in
    test_apply_comment.py::test_job_url_does_not_false_match_on_bare_endswith.
    Mutation: `apply-gate.is_cell_job` becomes a bare `endswith` of `<env> / <stack>`."""
    jobs = [job("L0 / dev-eu / stacks/dns", "success")]
    assert apply_complete.to_complete({"apply / stacks/dns / eu": [1]}, jobs)[0] == []


def test_suffix_collisions_on_similar_stack_and_env_names_never_complete():
    """Two real cells, "stacks/app" and a bare "app", plus two decoy jobs sharing a trailing
    substring with each without naming them: a different stack ("stacks/my-app") and a
    different env ("prod-dev-eu"). None of it may complete anything.
    Mutation: `apply-gate.is_cell_job` becomes a bare `endswith` of `<env> / <stack>`."""
    snap = {
        "apply / stacks/app / dev-eu": [2, 3],
        "apply / app / dev-eu": [99],
    }
    jobs = [
        job("L0 / dev-eu / stacks/my-app", "success"),
        job("L0 / prod-dev-eu / stacks/app", "success"),
    ]
    assert apply_complete.to_complete(snap, jobs)[0] == []


def test_a_job_present_without_a_conclusion_is_unresolved_not_dropped():
    # The listing lagging a finished job is the whole reason for the retry loop: a null
    # conclusion must be reported, never read as "did not run".
    jobs = [job("L0 / dev-eu / stacks/dns", None)]
    ids, unresolved, _, _ = apply_complete.to_complete(SNAP, jobs)
    assert (ids, unresolved) == ([], ["stacks/dns / dev-eu"])


def test_a_cell_with_no_matching_job_at_all_is_unmatched_not_unresolved():
    # Skip-propagation: an earlier wave failing skips wave1..wave7, and GitHub never expands a
    # skipped job's matrix, so those cells get no job row at all. Retrying cannot help, so they
    # are named rather than retried. The skipped wave lists one row under its unexpanded name.
    # Mutation: `to_complete` also counts every skipped job as the cell's.
    jobs = [
        job("L0 / dev-eu / stacks/dns", "success"),
        job("deploy / L1 / ${{ matrix.environment }} / ${{ matrix.stack }}", "skipped"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [], ["stacks/app / dev-eu"], [])


def test_skipped_and_failure_are_terminal_and_neither_completes_nor_blocks():
    jobs = [
        job("L0 / dev-eu / stacks/dns", "skipped"),
        job("L0 / dev-eu / stacks/app", "failure"),
    ]
    ids, unresolved, unmatched, unsuccessful = apply_complete.to_complete(SNAP, jobs)
    assert (ids, unresolved, unmatched) == ([], [], [])
    assert unsuccessful == ["stacks/app / dev-eu", "stacks/dns / dev-eu"]


LIVE_SNAP = {
    f"apply / tenant-{n} / dev-{r}": [n * 10 + i]
    for i, r in enumerate(("eu", "us"))
    for n, _ in enumerate("abcd", 1)
}


def _live_jobs(stale):
    return [
        job(
            f"L0 / dev-{r} / tenant-{n}",
            None if f"apply / tenant-{n} / dev-{r}" == stale else "success",
        )
        for r in ("eu", "us")
        for n, _ in enumerate("abcd", 1)
    ]


def test_the_live_shape_reports_exactly_the_unpropagated_cell():
    ids, unresolved, unmatched, unsuccessful = apply_complete.to_complete(
        LIVE_SNAP, _live_jobs("apply / tenant-2 / dev-us")
    )
    assert (unmatched, unsuccessful) == ([], [])
    assert unresolved == ["tenant-2 / dev-us"]
    assert ids == sorted(
        i for k, v in LIVE_SNAP.items() if k != "apply / tenant-2 / dev-us" for i in v
    )


def _run(snapshot, jobs):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete")],
        input="\n".join(json.dumps(j) for j in jobs),
        env={**os.environ, "SHIPMATE_SNAPSHOT_JSON": json.dumps(snapshot)},
        capture_output=True,
        text=True,
    )


def test_main_exits_with_the_retry_code_and_names_the_unresolved_cells():
    done = _run(LIVE_SNAP, _live_jobs("apply / tenant-2 / dev-us"))
    assert done.returncode == apply_complete.UNRESOLVED_EXIT
    assert apply_complete.RETRY_PREFIX in done.stderr
    assert "tenant-2 / dev-us" in done.stderr


def test_main_prints_the_ids_and_exits_zero_when_every_cell_resolved():
    done = _run(
        SNAP,
        [job("L0 / dev-eu / stacks/dns", "success"), job("L0 / dev-eu / stacks/app", "success")],
    )
    assert done.returncode == 0
    assert done.stdout.split() == ["1", "2", "3"]


def test_main_names_the_unmatched_cells_instead_of_dropping_them_silently():
    done = _run(SNAP, [job("L0 / dev-eu / stacks/dns", "success")])
    assert done.returncode == 0
    assert done.stdout.split() == ["1"]
    assert "::warning::" in done.stderr
    assert "stacks/app / dev-eu" in done.stderr


def test_main_names_the_cells_whose_apply_job_did_not_succeed():
    # A failed, cancelled or skipped cell legitimately stays pending and no retry changes
    # that, but exiting 0 while saying nothing about it is the silent drop this step exists to
    # eliminate.
    done = _run(
        SNAP,
        [
            job("L0 / dev-eu / stacks/dns", "success"),
            job("L0 / dev-eu / stacks/app", "failure"),
        ],
    )
    assert done.returncode == 0
    assert done.stdout.split() == ["1"]
    assert "::warning::" in done.stderr
    assert "stacks/app / dev-eu" in done.stderr


def test_the_zero_match_floor_is_retryable_not_an_immediate_hard_failure():
    """A run always lists its own wave jobs, so zero matches is never a real run state -- it is
    a lost, truncated or lagging listing. Completing nothing off it strands every check, so it
    must never exit 0, and the code it exits is the retryable one because the loop exists to
    outlast exactly this lag."""
    done = _run(SNAP, [job("L0 / dev-eu / stacks/rogue", "success")])
    assert done.returncode == apply_complete.UNRESOLVED_EXIT
    assert apply_complete.RETRY_PREFIX in done.stderr
    assert "refusing to complete nothing" in done.stderr
    assert done.stdout.strip() == ""


def test_the_app_token_is_minted_after_the_listing_and_selection_step():
    # The mint step runs after the step that can fail or abort, so the token's lifetime never
    # spans work that may not need it.
    steps = action_steps("apply-complete")
    listing = next(i for i, s in enumerate(steps) if "actions/runs" in (s.get("run") or ""))
    mint = next(
        i for i, s in enumerate(steps) if "create-github-app-token" in (s.get("uses") or "")
    )
    assert mint > listing


def test_the_stranded_failure_is_reported_after_the_completion_patches():
    # The point of deferring it: failing at selection time discards the completions this run
    # did earn.
    steps = action_steps("apply-complete")
    patch = next(i for i, s in enumerate(steps) if "check-runs/$id" in (s.get("run") or ""))
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

HARNESS_SNAP = {
    "apply / dns / dev-eu": [11],
    "apply / app / dev-eu": [22],
    "apply / web / dev-eu": [33],
}


def _select(tmp_path, listings, snapshot=None):
    """Execute the selection step with `listings[n]` served on attempt n.

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
        "SHIPMATE_SNAPSHOT_JSON": json.dumps(snapshot if snapshot is not None else HARNESS_SNAP),
        "FIXTURES": str(fixtures).replace("\\", "/"),
        "LAST_FIXTURE": str(len(listings)),
        "ATTEMPT": str(tmp_path / "attempt").replace("\\", "/"),
        "SLEEPS": str(tmp_path / "sleeps").replace("\\", "/"),
        "PYEXE": sys.executable.replace("\\", "/"),
    }
    proc = run_step(tmp_path, GH_STUB + _loop_body(), env, timeout=120)
    ids = (runner_temp / "complete-ids.txt").read_text(encoding="utf-8").split()
    attempts = int((tmp_path / "attempt").read_text(encoding="utf-8"))
    return proc, ids, attempts, env


def _all_success(*cells):
    return jsonl(*(job(f"L0 / dev-eu / {c}", "success") for c in cells))


@bash_only
def test_the_listing_is_refetched_on_every_attempt(tmp_path):
    # The property that makes this a retry at all: hoisting the fetch above the `for` re-reads
    # one frozen snapshot on every attempt and sleeps the whole budget over it.
    lagging = jsonl(
        job("L0 / dev-eu / dns", "success"),
        job("L0 / dev-eu / app", "success"),
        job("L0 / dev-eu / web", None),
    )
    proc, ids, attempts, _ = _select(
        tmp_path, [lagging, lagging, _all_success("dns", "app", "web")]
    )
    assert proc.returncode == 0, proc.stderr
    assert attempts == 3
    assert sorted(ids) == ["11", "22", "33"]


@bash_only
def test_an_empty_listing_is_retried_rather_than_failed_on_the_first_attempt(tmp_path):
    proc, ids, attempts, _ = _select(tmp_path, ["", "", _all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert attempts == 3
    assert sorted(ids) == ["11", "22", "33"]


@bash_only
def test_a_failed_fetch_never_feeds_its_half_written_file_to_the_selection(tmp_path):
    # A truncated listing matches a subset of cells, so it slips past the all-cells-unmatched
    # floor and completes only the rows that survived.
    proc, ids, attempts, _ = _select(tmp_path, [None, _all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert attempts == 2
    assert sorted(ids) == ["11", "22", "33"]


@bash_only
def test_a_listing_matching_no_cell_at_all_is_retried_and_completes_nothing_meanwhile(tmp_path):
    # The zero-match floor routed through the loop rather than around it.
    proc, ids, attempts, _ = _select(
        tmp_path, [_all_success("rogue"), _all_success("dns", "app", "web")]
    )
    assert proc.returncode == 0, proc.stderr
    assert attempts == 2
    assert sorted(ids) == ["11", "22", "33"]


@bash_only
def test_an_id_earned_by_one_attempt_survives_a_staler_later_listing(tmp_path):
    # Replicas differ, so a later read can be missing a row an earlier one had. A later read
    # may add a completion; it may never revoke one.
    first = jsonl(
        job("L0 / dev-eu / dns", "success"),
        job("L0 / dev-eu / app", "success"),
        job("L0 / dev-eu / web", None),
    )
    staler = _all_success("web")  # dns and app have vanished from this replica.
    proc, ids, _, _ = _select(tmp_path, [first, staler])
    assert proc.returncode == 0, proc.stderr
    assert sorted(ids) == ["11", "22", "33"]


@bash_only
def test_exhaustion_keeps_the_earned_completions_and_then_fails_naming_only_the_stranded(tmp_path):
    """One cell resolves, one is unmatched by skip-propagation, one never resolves. The earned
    id must still reach the PATCH step, the unmatched cell must be a warning rather than the
    reason for the failure, and the stranded cell must be named in the `::error::`.

    The waits back off from 2 s to a 110 s total, and the last attempt does not sleep.
    Mutations: the first delay 2 -> 10; one later delay 10 -> 20; the last attempt sleeps
    (`set -u` aborts on the missing delay); `attempts` drops its `+ 1`."""
    lagging = jsonl(
        job("L0 / dev-eu / dns", "success"),
        job("L0 / dev-eu / web", None),
    )
    proc, ids, attempts, env = _select(tmp_path, [lagging])
    assert proc.returncode == 0, proc.stderr
    assert attempts == 14
    sleeps = (tmp_path / "sleeps").read_text(encoding="utf-8").splitlines()
    assert sleeps == ["2", "3", "5", "10", "10", "10", "10", "10", "10", "10", "10", "10", "10"]
    assert ids == ["11"]
    assert proc.stderr.count("::warning::") == 1
    assert "app / dev-eu" in proc.stderr

    fail = run_step(tmp_path, GH_STUB + _fail_body(), env, timeout=120)
    assert fail.returncode == 1
    errors = [ln for ln in fail.stdout.splitlines() if "::error::" in ln]
    assert len(errors) == 1
    assert "web / dev-eu" in errors[0]
    assert "app / dev-eu" not in errors[0]
    assert "::warning::" not in errors[0]


@bash_only
def test_a_fully_resolved_run_leaves_nothing_for_the_stranded_step_to_fail_on(tmp_path):
    proc, ids, _, env = _select(tmp_path, [_all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert sorted(ids) == ["11", "22", "33"]
    assert run_step(tmp_path, GH_STUB + _fail_body(), env, timeout=120).returncode == 0


#: The PATCH body gh receives, written by hand rather than read from the action. It ends in
#: the one newline the here-string appends.
COMPLETE_BODY = (
    '{"status": "completed", "conclusion": "success", "output": {"title": "applied", '
    '"summary": "Applied the reviewed plan for this stack x environment."}}\n'
)


@bash_only
def test_the_completion_body_reaches_gh_without_a_file(tmp_path):
    """The PATCH body reaches gh whole, and the step writes no file, neither in the job
    workspace (its working directory) nor in `$RUNNER_TEMP`.

    Mutations: write the body to `body.json` and pass `--input body.json`; drop `--input -`
    from the PATCH.
    """
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    (runner_temp / "complete-ids.txt").write_text("11\n", encoding="utf-8", newline="\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sent = (tmp_path / "sent").as_posix()
    # `sleep` is a no-op so a failing PATCH reaches red without the retry loop's real waits.
    stubs = (
        'gh() { while [ $# -gt 0 ] && [ "$1" != --input ]; do shift; done; '
        '[ $# -gt 0 ] || return 1; cat "$2" > "' + sent + '"; }\n'
        "sleep() { :; }\n"
    )
    body = next(
        s["run"]
        for s in action_steps("apply-complete")
        if s.get("name") == "Complete the apply checks"
    )
    env = {
        **os.environ,
        "GITHUB_REPOSITORY": "acme/demo",
        "RUNNER_TEMP": runner_temp.as_posix(),
    }
    proc = run_step(tmp_path, stubs + body, env, cwd=workspace)
    assert list(workspace.iterdir()) == [], "the step left a file in the workspace"
    assert sorted(p.name for p in runner_temp.iterdir()) == ["complete-ids.txt"], (
        "the step left a file in RUNNER_TEMP"
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "sent").read_text(encoding="utf-8") == COMPLETE_BODY
