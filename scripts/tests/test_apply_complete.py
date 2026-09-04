"""Unit tests for scripts/apply-complete."""

import json
import os
import subprocess
import sys

import pytest
from _loader import ENGINE, SCRIPTS, action_steps, load_script, usable_bash

apply_complete = load_script("apply-complete")

_BASH = usable_bash()

SNAP = {
    "stacks/dns\x00dev-eu": [1],
    "stacks/app\x00dev-eu": [2, 3],
}


def job(name, conclusion):
    return {"name": name, "conclusion": conclusion, "status": "completed"}


def test_completes_only_cells_whose_wave_job_succeeded():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "success"),
        job("waves / apply / stacks/app / dev-eu", "failure"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [], [], ["stacks/app / dev-eu"])


def test_matches_the_nested_reusable_workflow_job_name_suffix():
    # A called workflow's jobs display as `<caller job> / <called job> / <job>`.
    jobs = [job("targeted / waves / apply / stacks/app / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == ([2, 3], [], ["stacks/dns / dev-eu"], [])


def test_a_cancelled_or_missing_job_leaves_the_check_pending():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "cancelled"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == (
        [],
        [],
        ["stacks/app / dev-eu"],
        ["stacks/dns / dev-eu"],
    )


def test_a_rerun_success_after_a_failure_completes_the_cell():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "failure"),
        job("waves / apply / stacks/dns / dev-eu", "success"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [], ["stacks/app / dev-eu"], [])


def test_never_completes_a_cell_that_was_not_snapshotted():
    jobs = [job("waves / apply / stacks/rogue / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs)[0] == []


def test_a_reapply_job_cannot_complete_the_cell_it_shadows():
    """A "reapply / stacks/dns / dev-eu" job shares its trailing characters with the target
    "apply / stacks/dns / dev-eu" but is a different verb, so only a `/`-boundary-respecting
    suffix may count. Same idiom as scripts/apply-comment's _job_url, pinned in
    test_apply_comment.py::test_job_url_does_not_false_match_on_bare_endswith."""
    jobs = [job("reapply / stacks/dns / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs)[0] == []


def test_suffix_collisions_on_similar_stack_and_env_names_never_complete():
    """Two real cells, "stacks/app" and a bare "app", plus two decoy jobs sharing a trailing
    substring with each without naming them: a different stack ("stacks/my-app") and a
    different env ("prod-dev-eu"). None of it may complete anything."""
    snap = {
        "stacks/app\x00dev-eu": [2, 3],
        "app\x00dev-eu": [99],
    }
    jobs = [
        job("waves / apply / stacks/my-app / dev-eu", "success"),
        job("waves / apply / stacks/app / prod-dev-eu", "success"),
    ]
    assert apply_complete.to_complete(snap, jobs)[0] == []


def test_a_job_present_without_a_conclusion_is_unresolved_not_dropped():
    # The listing lagging a finished job is the whole reason for the retry loop: a null
    # conclusion must be reported, never read as "did not run".
    jobs = [job("waves / apply / stacks/dns / dev-eu", None)]
    ids, unresolved, _, _ = apply_complete.to_complete(SNAP, jobs)
    assert (ids, unresolved) == ([], ["stacks/dns / dev-eu"])


def test_a_cell_with_no_matching_job_at_all_is_unmatched_not_unresolved():
    # Skip-propagation: an earlier wave failing skips wave1..wave7, and GitHub never expands a
    # skipped job's matrix, so those cells get no job row at all. Retrying cannot help, so they
    # are named rather than retried.
    jobs = [job("waves / apply / stacks/dns / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [], ["stacks/app / dev-eu"], [])


def test_skipped_and_failure_are_terminal_and_neither_completes_nor_blocks():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "skipped"),
        job("waves / apply / stacks/app / dev-eu", "failure"),
    ]
    ids, unresolved, unmatched, unsuccessful = apply_complete.to_complete(SNAP, jobs)
    assert (ids, unresolved, unmatched) == ([], [], [])
    assert unsuccessful == ["stacks/app / dev-eu", "stacks/dns / dev-eu"]


LIVE_SNAP = {
    f"tenant-{n}\x00dev-{r}": [n * 10 + i]
    for i, r in enumerate(("eu", "us"))
    for n, _ in enumerate("abcd", 1)
}


def _live_jobs(stale):
    return [
        job(
            f"waves / apply / {key.split(chr(0))[0]} / {key.split(chr(0))[1]}",
            None if key == stale else "success",
        )
        for key in LIVE_SNAP
    ]


def test_the_live_shape_reports_exactly_the_unpropagated_cell():
    ids, unresolved, unmatched, unsuccessful = apply_complete.to_complete(
        LIVE_SNAP, _live_jobs("tenant-2\x00dev-us")
    )
    assert (unmatched, unsuccessful) == ([], [])
    assert unresolved == ["tenant-2 / dev-us"]
    assert ids == sorted(i for k, v in LIVE_SNAP.items() if k != "tenant-2\x00dev-us" for i in v)


def _run(snapshot, jobs):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "apply-complete")],
        input="\n".join(json.dumps(j) for j in jobs),
        env={**os.environ, "SHIPMATE_SNAPSHOT_JSON": json.dumps(snapshot)},
        capture_output=True,
        text=True,
    )


def test_main_exits_with_the_retry_code_and_names_the_unresolved_cells():
    done = _run(LIVE_SNAP, _live_jobs("tenant-2\x00dev-us"))
    assert done.returncode == apply_complete.UNRESOLVED_EXIT
    assert apply_complete.RETRY_PREFIX in done.stderr
    assert "tenant-2 / dev-us" in done.stderr


def test_main_prints_the_ids_and_exits_zero_when_every_cell_resolved():
    done = _run(
        SNAP,
        [
            job(f"waves / apply / {k.split(chr(0))[0]} / {k.split(chr(0))[1]}", "success")
            for k in SNAP
        ],
    )
    assert done.returncode == 0
    assert done.stdout.split() == ["1", "2", "3"]


def test_main_names_the_unmatched_cells_instead_of_dropping_them_silently():
    done = _run(SNAP, [job("waves / apply / stacks/dns / dev-eu", "success")])
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
            job("waves / apply / stacks/dns / dev-eu", "success"),
            job("waves / apply / stacks/app / dev-eu", "failure"),
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
    done = _run(SNAP, [job("waves / apply / stacks/rogue / dev-eu", "success")])
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
    # Inside the loop it would repeat the same warning up to 12 times, and the file is
    # rewritten every attempt, so only the last one describes the outcome.
    lines = [ln.strip() for ln in _loop_body().splitlines()]
    done = max(i for i, ln in enumerate(lines) if ln == "done")
    echo = max(i for i, ln in enumerate(lines) if 'diagnostics.txt" >&2' in ln)
    assert echo > done


# `gh`, `sleep` and `python3` are bash functions, which bash resolves before PATH, so nothing
# is installed. `python3` forwards to the interpreter running the suite, so the real
# scripts/apply-complete decides every cell.

GH_STUB = r"""
gh() {
  n=$(( $(cat "$ATTEMPT") + 1 ))
  printf '%s' "$n" > "$ATTEMPT"
  f="$FIXTURES/$n.jsonl"
  [ -f "$f" ] || f="$FIXTURES/$LAST_FIXTURE.jsonl"
  if [ "$(head -n 1 "$f")" = FAIL ]; then tail -n +2 "$f"; return 1; fi
  cat "$f"
}
sleep() { :; }
python3() { "$PYEXE" "$@"; }
"""

HARNESS_SNAP = {
    "dns\x00dev-eu": [11],
    "app\x00dev-eu": [22],
    "web\x00dev-eu": [33],
}


def _jsonl(jobs):
    return "".join(json.dumps(j) + "\n" for j in jobs)


def _run_body(tmp_path, body, env):
    assert _BASH is not None  # Callers are skipif-gated on this, and it narrows the type.
    script = tmp_path / f"step-{abs(hash(body)) % 10**8}.sh"
    script.write_text(GH_STUB + body, encoding="utf-8", newline="\n")
    return subprocess.run(
        [_BASH, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )


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
        "PYEXE": sys.executable.replace("\\", "/"),
    }
    proc = _run_body(tmp_path, _loop_body(), env)
    ids = (runner_temp / "complete-ids.txt").read_text(encoding="utf-8").split()
    attempts = int((tmp_path / "attempt").read_text(encoding="utf-8"))
    return proc, ids, attempts, env


def _all_success(*cells):
    return _jsonl([job(f"L0 / apply / {c} / dev-eu", "success") for c in cells])


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_the_listing_is_refetched_on_every_attempt(tmp_path):
    # The property that makes this a retry at all: hoisting the fetch above the `for` re-reads
    # one frozen snapshot twelve times and sleeps 110s over it.
    lagging = _jsonl(
        [
            job("L0 / apply / dns / dev-eu", "success"),
            job("L0 / apply / app / dev-eu", "success"),
            job("L0 / apply / web / dev-eu", None),
        ]
    )
    proc, ids, attempts, _ = _select(
        tmp_path, [lagging, lagging, _all_success("dns", "app", "web")]
    )
    assert proc.returncode == 0, proc.stderr
    assert attempts == 3
    assert sorted(ids) == ["11", "22", "33"]


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_an_empty_listing_is_retried_rather_than_failed_on_the_first_attempt(tmp_path):
    proc, ids, attempts, _ = _select(tmp_path, ["", "", _all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert attempts == 3
    assert sorted(ids) == ["11", "22", "33"]


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_failed_fetch_never_feeds_its_half_written_file_to_the_selection(tmp_path):
    # A truncated listing matches a subset of cells, so it slips past the all-cells-unmatched
    # floor and completes only the rows that survived.
    proc, ids, attempts, _ = _select(tmp_path, [None, _all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert attempts == 2
    assert sorted(ids) == ["11", "22", "33"]


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_listing_matching_no_cell_at_all_is_retried_and_completes_nothing_meanwhile(tmp_path):
    # The zero-match floor routed through the loop rather than around it.
    proc, ids, attempts, _ = _select(
        tmp_path, [_all_success("rogue"), _all_success("dns", "app", "web")]
    )
    assert proc.returncode == 0, proc.stderr
    assert attempts == 2
    assert sorted(ids) == ["11", "22", "33"]


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_an_id_earned_by_one_attempt_survives_a_staler_later_listing(tmp_path):
    # Replicas differ, so a later read can be missing a row an earlier one had. A later read
    # may add a completion; it may never revoke one.
    first = _jsonl(
        [
            job("L0 / apply / dns / dev-eu", "success"),
            job("L0 / apply / app / dev-eu", "success"),
            job("L0 / apply / web / dev-eu", None),
        ]
    )
    staler = _all_success("web")  # dns and app have vanished from this replica.
    proc, ids, _, _ = _select(tmp_path, [first, staler])
    assert proc.returncode == 0, proc.stderr
    assert sorted(ids) == ["11", "22", "33"]


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_exhaustion_keeps_the_earned_completions_and_then_fails_naming_only_the_stranded(tmp_path):
    """One cell resolves, one is unmatched by skip-propagation, one never resolves. The earned
    id must still reach the PATCH step, the unmatched cell must be a warning rather than the
    reason for the failure, and the stranded cell must be named in the `::error::`."""
    lagging = _jsonl(
        [
            job("L0 / apply / dns / dev-eu", "success"),
            job("L0 / apply / web / dev-eu", None),
        ]
    )
    proc, ids, attempts, env = _select(tmp_path, [lagging])
    assert proc.returncode == 0, proc.stderr
    assert attempts == 12
    assert ids == ["11"]
    assert proc.stderr.count("::warning::") == 1
    assert "app / dev-eu" in proc.stderr

    fail = _run_body(tmp_path, _fail_body(), env)
    assert fail.returncode == 1
    errors = [ln for ln in fail.stdout.splitlines() if "::error::" in ln]
    assert len(errors) == 1
    assert "web / dev-eu" in errors[0]
    assert "app / dev-eu" not in errors[0]
    assert "::warning::" not in errors[0]


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_fully_resolved_run_leaves_nothing_for_the_stranded_step_to_fail_on(tmp_path):
    proc, ids, _, env = _select(tmp_path, [_all_success("dns", "app", "web")])
    assert proc.returncode == 0, proc.stderr
    assert sorted(ids) == ["11", "22", "33"]
    assert _run_body(tmp_path, _fail_body(), env).returncode == 0
