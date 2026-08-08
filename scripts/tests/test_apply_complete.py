"""Unit tests for scripts/apply-complete."""

import json
import os
import subprocess
import sys

from _loader import SCRIPTS, load_script

apply_complete = load_script("apply-complete")

SNAP = {
    "stacks/dns\x00dev-eu": [1],
    "stacks/app\x00dev-eu": [2, 3],
}


def job(name, conclusion, status="completed"):
    return {"name": name, "conclusion": conclusion, "status": status}


def test_completes_only_cells_whose_wave_job_succeeded():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "success"),
        job("waves / apply / stacks/app / dev-eu", "failure"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [])


def test_matches_the_nested_reusable_workflow_job_name_suffix():
    # A called workflow's jobs display as `<caller job> / <called job> / <job>`.
    jobs = [job("targeted / waves / apply / stacks/app / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == ([2, 3], [])


def test_a_cancelled_or_missing_job_leaves_the_check_pending():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "cancelled"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([], [])


def test_a_rerun_success_after_a_failure_completes_the_cell():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "failure"),
        job("waves / apply / stacks/dns / dev-eu", "success"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [])


def test_never_completes_a_cell_that_was_not_snapshotted():
    jobs = [job("waves / apply / stacks/rogue / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == ([], [])


def test_a_reapply_job_cannot_complete_the_cell_it_shadows():
    # "reapply / stacks/dns / dev-eu" shares its trailing characters with the
    # target "apply / stacks/dns / dev-eu" but is a different verb. Only a
    # `/`-boundary-respecting suffix may count -- the same idiom
    # scripts/apply-comment's _job_url pins in
    # test_apply_comment.py::test_job_url_does_not_false_match_on_bare_endswith.
    jobs = [job("reapply / stacks/dns / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == ([], [])


def test_suffix_collisions_on_similar_stack_and_env_names_never_complete():
    # Two real cells, "stacks/app" and a bare "app", plus two decoy jobs that
    # share a trailing substring with each without actually naming them: a
    # different stack ("stacks/my-app") and a different env ("prod-dev-eu").
    # None of it may complete anything.
    snap = {
        "stacks/app\x00dev-eu": [2, 3],
        "app\x00dev-eu": [99],
    }
    jobs = [
        job("waves / apply / stacks/my-app / dev-eu", "success"),
        job("waves / apply / stacks/app / prod-dev-eu", "success"),
    ]
    assert apply_complete.to_complete(snap, jobs) == ([], [])


def test_a_job_present_without_a_conclusion_is_unresolved_not_dropped():
    # The listing lagging a finished job is the whole reason for the retry
    # loop: a null conclusion must be reported, never read as "did not run".
    jobs = [job("waves / apply / stacks/dns / dev-eu", None)]
    assert apply_complete.to_complete(SNAP, jobs) == ([], ["stacks/dns / dev-eu"])


def test_an_in_progress_job_is_unresolved():
    jobs = [job("waves / apply / stacks/dns / dev-eu", None, status="in_progress")]
    assert apply_complete.to_complete(SNAP, jobs) == ([], ["stacks/dns / dev-eu"])


def test_a_cell_with_no_matching_job_at_all_is_skipped_silently():
    # A targeted apply of one environment lists only that environment's jobs;
    # every other snapshot cell simply is not part of this run.
    jobs = [job("waves / apply / stacks/dns / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [])


def test_skipped_and_failure_are_terminal_and_neither_completes_nor_blocks():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "skipped"),
        job("waves / apply / stacks/app / dev-eu", "failure"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([], [])


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
            status="queued" if key == stale else "completed",
        )
        for key in LIVE_SNAP
    ]


def test_the_live_shape_reports_exactly_the_unpropagated_cell():
    ids, unresolved = apply_complete.to_complete(LIVE_SNAP, _live_jobs("tenant-2\x00dev-us"))
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
