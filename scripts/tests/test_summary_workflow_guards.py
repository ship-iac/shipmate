"""The trusted post-plan workflow must not be fireable by branch-authored runs."""

import pathlib
import re

WF = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows/summary.yml"


def text():
    return WF.read_text(encoding="utf-8")


def test_guards_on_the_triggering_workflow_file_path():
    # `workflow_run.workflows:` matches by workflow NAME, so a branch file named
    # "shipmate · plan" with `on: push` would otherwise fire this workflow.
    assert "workflow_run.path" in text()


def test_guards_on_the_triggering_event_being_pull_request():
    assert "workflow_run.event" in text()
    assert "'pull_request'" in text()


def test_guards_on_the_head_repository_being_this_repository():
    # Without this a fork's artifacts would author checks and a gate under the
    # App identity — the fork safe-harbour inverts once this job holds the key.
    assert "head_repository.full_name" in text()


def test_serialises_gate_writes_per_head_sha_at_job_level():
    # Workflow-level `concurrency:` is ignored in a called workflow, so pinning
    # it there would assert a string that does nothing at runtime.
    body = text().split("jobs:", 1)[1]
    assert re.search(r"concurrency:\s*\n\s*group:.*head_sha", body)
    assert "concurrency:" not in text().split("jobs:", 1)[0]


def test_skips_draft_pull_requests():
    assert ".draft" in text()


def test_privileged_job_names_the_engine_environment():
    assert "environment: shipmate-engine" in text()


def test_privileged_job_does_not_check_out_the_pull_request_head():
    # The job holding the App key must never execute repository code.
    assert "actions/checkout" not in text()


def test_artifact_count_input_falls_back_to_a_valid_integer():
    # `scripts/gate-state` does int(os.environ.get("SHIPMATE_ARTIFACT_COUNT",
    # "0")); an empty string reaches int("") and raises, which kills the
    # action and writes NO gate at all. The `with:` value must never be able
    # to resolve to an empty string.
    assert re.search(
        r"artifact-count:\s*\$\{\{\s*steps\.artifacts\.outputs\.count\s*\|\|\s*'0'\s*\}\}",
        text(),
    )


def test_artifact_count_step_cannot_abort_without_emitting_a_count():
    # A `gh api` failure inside the counting step must not raise the step (and
    # so the whole job -- every later step's plain `if:` is implicitly
    # `success() && ...`) to failure, which would skip the summary step
    # entirely and leave no gate written at all. The step must catch the
    # failure itself and still emit a `count=` output on every path.
    body = text().split("id: artifacts", 1)[1].split("- uses:", 1)[0]
    assert "set -euo pipefail" not in body
    assert body.count('echo "count=') >= 2
