"""The consumer fences choose the runner and the engine secrets once, through YAML anchors.

Each fence is read through `scripts/onboard`'s own selector, the text it writes into a
consumer repository, and parsed by the YAML 1.2 loader, which resolves aliases as GitHub does.
"""

import pytest
from _loader import ENGINE, load_script
from _shipmate import load_workflow_text

RUNNER = "ubuntu-26.04-arm"
ENGINE_SECRETS = {
    "SHIPMATE_APP_PRIVATE_KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
    "SHIPMATE_ENCRYPTION_KEY": "${{ secrets.SHIPMATE_ENCRYPTION_KEY }}",
    "SHIPMATE_SECRETS": "${{ secrets.SHIPMATE_SECRETS }}",
}


def _fence_text(name):
    return load_script("onboard")._fence(
        (ENGINE / "docs" / "getting-started.md").read_text(encoding="utf-8"), name
    )


def _jobs(name):
    return load_workflow_text(_fence_text(name))["jobs"]


@pytest.mark.parametrize(
    "job", ["plan", "release", "comment-ops", "deploy", "apply", "auto", "unlock"]
)
def test_every_engine_job_runs_on_the_default_runner(job):
    """Mutations: `deploy`'s `*runner` replaced by `ubuntu-latest`; `comment-ops`'s `with:`
    deleted."""
    assert (_jobs("shipmate")[job].get("with") or {}).get("runs_on") == RUNNER


def test_the_runner_is_chosen_once_through_an_anchor():
    """Mutation: `apply`'s `*runner` inlined as `ubuntu-26.04-arm`, which parses the same."""
    text = _fence_text("shipmate")
    assert text.count(f"runs_on: &runner {RUNNER}\n") == 1
    assert text.count("runs_on: *runner\n") == 6


def test_engine_secrets_are_chosen_once_and_unlock_and_release_keep_their_own():
    """Mutations: `apply`'s block written out in full, which parses the same; `apply`'s block
    written out without `SHIPMATE_SECRETS`; `auto`'s block written out without
    `SHIPMATE_SECRETS`; `unlock` aliased to `*engine-secrets`; `release` aliased to
    `*engine-secrets`."""
    text = _fence_text("shipmate")
    assert text.count("secrets: &engine-secrets\n") == 1
    assert text.count("secrets: *engine-secrets\n") == 3
    jobs = _jobs("shipmate")
    assert jobs["plan"]["secrets"] == ENGINE_SECRETS
    assert jobs["deploy"]["secrets"] == ENGINE_SECRETS
    assert jobs["apply"]["secrets"] == ENGINE_SECRETS
    assert jobs["auto"]["secrets"] == ENGINE_SECRETS
    assert jobs["unlock"]["secrets"] == {"SHIPMATE_SECRETS": "${{ secrets.SHIPMATE_SECRETS }}"}
    assert jobs["release"]["secrets"] == {
        "SHIPMATE_APP_PRIVATE_KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}"
    }


def test_a_ready_or_dispatched_plan_run_queues_behind_the_run_in_flight():
    """Mutations: `cancel-in-progress` restored to `github.event.action != 'ready_for_review'`;
    `==` turned to `!=` in the `event_name` clause."""
    assert _jobs("shipmate")["plan"]["concurrency"] == {
        "group": "plan-${{ github.event.pull_request.number || github.event.inputs.pr_number }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request_target'"
        " && github.event.action != 'ready_for_review' }}",
    }


def test_a_release_run_is_never_cancelled():
    """The plan group cancels the run in flight, so a release sharing it could stop mid-delete.

    Mutations: `group` set to the plan group; `cancel-in-progress` set to `true`."""
    assert _jobs("shipmate")["release"]["concurrency"] == {
        "group": "release-${{ github.event.pull_request.number }}",
        "cancel-in-progress": False,
    }


def test_drift_runs_on_the_default_runner():
    """Mutation: the drift fence's `runs_on:` line deleted."""
    assert (_jobs("shipmate drift")["drift"].get("with") or {}).get("runs_on") == RUNNER


_APPLY_PERMISSIONS = {
    "contents": "read",
    "checks": "read",
    "pull-requests": "read",
    "actions": "read",
    "id-token": "write",
}


def test_the_auto_job_is_the_apply_job_with_auto_set():
    """The autoplan's dispatch selects `auto`, which calls `apply.yml` with `auto: true` and no
    `environment`; both jobs grant the pull-request read that detect and the wave re-check need.

    Mutations: `auto: true` dropped from `auto`'s `with:`; `pull-requests: read` dropped from
    `apply`'s permissions; `auto`'s `if:` reading `'apply'`."""
    jobs = _jobs("shipmate")
    assert jobs["apply"]["permissions"] == _APPLY_PERMISSIONS
    auto = dict(jobs["auto"])
    assert auto.pop("uses").split("@", 1)[0] == "ship-iac/shipmate/.github/workflows/apply.yml"
    assert auto == {
        "if": "github.event_name == 'workflow_dispatch' && inputs.verb == 'auto'",
        "permissions": _APPLY_PERMISSIONS,
        "secrets": ENGINE_SECRETS,
        "with": {
            "auto": True,
            "ref": "${{ inputs.ref }}",
            "pr_number": "${{ inputs.pr_number }}",
            "runs_on": RUNNER,
        },
    }
