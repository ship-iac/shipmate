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


def test_a_reapply_job_cannot_complete_the_cell_it_shadows():
    # "reapply / stacks/dns / dev-eu" shares its trailing characters with the
    # target "apply / stacks/dns / dev-eu" but is a different verb. Only a
    # `/`-boundary-respecting suffix may count -- the same idiom
    # scripts/apply-comment's _job_url pins in
    # test_apply_comment.py::test_job_url_does_not_false_match_on_bare_endswith.
    jobs = [job("reapply / stacks/dns / dev-eu", "success")]
    assert apply_complete.to_complete(SNAP, jobs) == []


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
    assert apply_complete.to_complete(snap, jobs) == []
