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


@pytest.mark.parametrize("job", ["plan", "comment-ops", "deploy", "apply", "unlock"])
def test_every_engine_job_runs_on_the_default_runner(job):
    """Mutations: `deploy`'s `*runner` replaced by `ubuntu-latest`; `comment-ops`'s `with:`
    deleted."""
    assert (_jobs("shipmate")[job].get("with") or {}).get("runs_on") == RUNNER


def test_the_runner_is_chosen_once_through_an_anchor():
    """Mutation: `apply`'s `*runner` inlined as `ubuntu-26.04-arm`, which parses the same."""
    text = _fence_text("shipmate")
    assert text.count(f"runs_on: &runner {RUNNER}\n") == 1
    assert text.count("runs_on: *runner\n") == 4


def test_engine_secrets_are_chosen_once_and_unlock_keeps_its_own():
    """Mutations: `apply`'s block written out in full, which parses the same; `apply`'s block
    written out without `SHIPMATE_SECRETS`; `unlock` aliased to `*engine-secrets`."""
    text = _fence_text("shipmate")
    assert text.count("secrets: &engine-secrets\n") == 1
    assert text.count("secrets: *engine-secrets\n") == 2
    jobs = _jobs("shipmate")
    assert jobs["plan"]["secrets"] == ENGINE_SECRETS
    assert jobs["deploy"]["secrets"] == ENGINE_SECRETS
    assert jobs["apply"]["secrets"] == ENGINE_SECRETS
    assert jobs["unlock"]["secrets"] == {"SHIPMATE_SECRETS": "${{ secrets.SHIPMATE_SECRETS }}"}


def test_drift_runs_on_the_default_runner():
    """Mutation: the drift fence's `runs_on:` line deleted."""
    assert (_jobs("shipmate drift")["drift"].get("with") or {}).get("runs_on") == RUNNER
