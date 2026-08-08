"""Unit tests for scripts/apply-complete."""

import json
import os
import subprocess
import sys

from _loader import SCRIPTS, action_steps, load_script

apply_complete = load_script("apply-complete")

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
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [], [])


def test_matches_the_nested_reusable_workflow_job_name_suffix():
    # A called workflow's jobs display as `<caller job> / <called job> / <job>`.
    jobs = [job("targeted / waves / apply / stacks/app / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == ([2, 3], [], ["stacks/dns / dev-eu"])


def test_a_cancelled_or_missing_job_leaves_the_check_pending():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "cancelled"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([], [], ["stacks/app / dev-eu"])


def test_a_rerun_success_after_a_failure_completes_the_cell():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "failure"),
        job("waves / apply / stacks/dns / dev-eu", "success"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [], ["stacks/app / dev-eu"])


def test_never_completes_a_cell_that_was_not_snapshotted():
    jobs = [job("waves / apply / stacks/rogue / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs)[0] == []


def test_a_reapply_job_cannot_complete_the_cell_it_shadows():
    # "reapply / stacks/dns / dev-eu" shares its trailing characters with the
    # target "apply / stacks/dns / dev-eu" but is a different verb. Only a
    # `/`-boundary-respecting suffix may count -- the same idiom
    # scripts/apply-comment's _job_url pins in
    # test_apply_comment.py::test_job_url_does_not_false_match_on_bare_endswith.
    jobs = [job("reapply / stacks/dns / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs)[0] == []


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
    assert apply_complete.to_complete(snap, jobs)[0] == []


def test_a_job_present_without_a_conclusion_is_unresolved_not_dropped():
    # The listing lagging a finished job is the whole reason for the retry
    # loop: a null conclusion must be reported, never read as "did not run".
    jobs = [job("waves / apply / stacks/dns / dev-eu", None)]
    ids, unresolved, _ = apply_complete.to_complete(SNAP, jobs)
    assert (ids, unresolved) == ([], ["stacks/dns / dev-eu"])


def test_a_cell_with_no_matching_job_at_all_is_unmatched_not_unresolved():
    # Skip-propagation: an earlier wave failing skips wave1..wave7, so those
    # cells never get a job. Retrying cannot help -- they are named, not
    # retried.
    jobs = [job("waves / apply / stacks/dns / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == ([1], [], ["stacks/app / dev-eu"])


def test_skipped_and_failure_are_terminal_and_neither_completes_nor_blocks():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "skipped"),
        job("waves / apply / stacks/app / dev-eu", "failure"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == ([], [], [])


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
    ids, unresolved, unmatched = apply_complete.to_complete(
        LIVE_SNAP, _live_jobs("tenant-2\x00dev-us")
    )
    assert unmatched == []
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


def test_main_names_the_unmatched_cells_instead_of_dropping_them_silently():
    done = _run(SNAP, [job("waves / apply / stacks/dns / dev-eu", "success")])
    assert done.returncode == 0
    assert done.stdout.split() == ["1"]
    assert "::warning::" in done.stderr
    assert "stacks/app / dev-eu" in done.stderr


def test_main_refuses_a_listing_that_matches_no_snapshot_cell_at_all():
    # A run always lists its own wave jobs, so zero matches is never a real
    # run state -- it is a lost or truncated listing, and completing nothing
    # off it strands every check while the step reports success.
    done = _run(SNAP, [job("waves / apply / stacks/rogue / dev-eu", "success")])
    assert done.returncode == 1
    assert "::error::" in done.stderr
    assert done.stdout.strip() == ""


def test_the_app_token_is_minted_after_the_listing_and_selection_step():
    # The mint step's own comment claims it runs after the step that can fail
    # or abort, so the token's lifetime never spans work that may not need it.
    steps = action_steps("apply-complete")
    listing = next(i for i, s in enumerate(steps) if "actions/runs" in (s.get("run") or ""))
    mint = next(
        i for i, s in enumerate(steps) if "create-github-app-token" in (s.get("uses") or "")
    )
    assert mint > listing


def test_the_retry_arm_names_the_scripts_temporary_failure_code():
    # The bash `case` arm hardcodes the number; a drift between the two turns
    # "ask me again" into an immediate hard failure, or vice versa.
    body = next(
        s["run"] for s in action_steps("apply-complete") if "actions/runs" in s.get("run", "")
    )
    assert f"{apply_complete.UNRESOLVED_EXIT})" in body


def _loop_body():
    return next(
        s["run"] for s in action_steps("apply-complete") if "actions/runs" in s.get("run", "")
    )


def test_the_exhaustion_annotation_names_the_unresolved_cells_and_nothing_else():
    # stderr also carries the unmatched-cells warning; annotating the whole
    # file emits `::error::::warning::…` and strands the real cells on a
    # second, un-annotated line.
    body = _loop_body()
    assert f"s/^{apply_complete.UNRESOLVED_PREFIX}//p" in body
    errors = [ln for ln in body.splitlines() if "::error::" in ln]
    assert errors
    assert not [ln for ln in errors if "diagnostics.txt" in ln]


def test_diagnostics_are_echoed_once_after_the_retry_loop():
    lines = [ln.strip() for ln in _loop_body().splitlines()]
    echo = 'cat "$RUNNER_TEMP/diagnostics.txt" >&2'
    assert lines.count(echo) == 1, "the unconditional echo must not sit inside the retry loop"
    assert lines.index(echo) > lines.index("done")
