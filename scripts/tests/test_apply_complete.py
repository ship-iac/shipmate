"""Unit tests for scripts/apply-complete."""

from _loader import load_script

apply_complete = load_script("apply-complete")

SNAP = {
    "stacks/dns\x00dev-eu": [1],
    "stacks/app\x00dev-eu": [2, 3],
}


def job(name, conclusion):
    return {"name": name, "conclusion": conclusion}


def test_completes_only_cells_whose_wave_job_succeeded():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "success"),
        job("waves / apply / stacks/app / dev-eu", "failure"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == [1]


def test_matches_the_nested_reusable_workflow_job_name_suffix():
    # A called workflow's jobs display as `<caller job> / <called job> / <job>`.
    jobs = [job("targeted / waves / apply / stacks/app / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == [2, 3]


def test_a_cancelled_or_missing_job_leaves_the_check_pending():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "cancelled"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == []


def test_a_rerun_success_after_a_failure_completes_the_cell():
    jobs = [
        job("waves / apply / stacks/dns / dev-eu", "failure"),
        job("waves / apply / stacks/dns / dev-eu", "success"),
    ]
    assert apply_complete.to_complete(SNAP, jobs) == [1]


def test_never_completes_a_cell_that_was_not_snapshotted():
    jobs = [job("waves / apply / stacks/rogue / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == []


def test_a_job_named_for_another_cell_cannot_complete_this_one():
    # Evidence is the run's own job conclusions, so one cell cannot vouch for
    # another the way a shared artifact would allow.
    jobs = [job("waves / apply / stacks/app / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == [2, 3]
