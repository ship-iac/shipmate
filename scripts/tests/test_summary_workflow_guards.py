"""The trusted summary job must refuse forks and drafts, and execute nothing.

It runs on `pull_request_target` (via the consumer's plan workflow) holding the
App key, so the two things that keep it safe are its `if:` and the fact that it
executes no repository content. Every assertion below is on a *parsed* value
(`yaml.safe_load`, then a whole `if:`/`environment:`/`with:` field) rather than
a substring of the raw file text. The substring form this replaces was proven
vacuous: four simultaneous mutations of `summary.yml` -- all three trust guards
inverted, `environment: shipmate-engine` commented out, and the draft-skip
deleted -- left the old suite's `1 failed, 762 passed` unchanged from baseline.
A YAML comment or an inverted operator can contain the same substring as the
real guard; it cannot produce the same *parsed value*.
"""

import yaml
from _loader import WORKFLOWS

WF = WORKFLOWS / "summary.yml"

# Hand-written, NOT derived from the workflow. A constant lifted out of the file
# it checks passes whatever the file says.
EXPECTED_IF = (
    "github.event.pull_request.head.repo.full_name == github.repository && "
    "github.event.pull_request.draft == false"
)
# The whole declaration per input, not just the names. A `default:` added here
# is what makes a caller that stops passing the input silent: `planned-cells`
# would arrive as '0', `cell_count` as 0, and the gate would green over a run
# that planned cells. `required: false` does the same by another route.
EXPECTED_INPUTS = {
    "pr-number": {"required": True, "type": "string"},
    "head-sha": {"required": True, "type": "string"},
    "detect-result": {"required": True, "type": "string"},
    "plan-result": {"required": True, "type": "string"},
    "planned-cells": {"required": True, "type": "string"},
}
# The whole job, as an ordered list of what each step runs. Subsumes "no
# checkout": a checkout step, a `run:` step, or any extra step at all changes
# this list. A substring scan for "checkout" would miss every one of those.
EXPECTED_STEP_USES = [
    "actions/download-artifact",
    "ship-iac/shipmate/actions/summary",
]
EXPECTED_SUMMARY_WITH = {
    "pr-number": "${{ inputs.pr-number }}",
    "head-sha": "${{ inputs.head-sha }}",
    "detect-result": "${{ inputs.detect-result }}",
    "plan-result": "${{ inputs.plan-result }}",
    "planned-cells": "${{ inputs.planned-cells }}",
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}


def _summary_job():
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert len(jobs) == 1, "a second job here would not be covered by these guards"
    return next(iter(jobs.values())), doc


def test_the_trusted_job_refuses_forks_and_drafts():
    """The whole `if:`, compared as one value.

    Both clauses are load-bearing and neither can live in the consumer's file:
    with the wiring checks gone, nothing inspects consumer YAML, so a consumer
    who dropped either clause would hand a fork an App-authored gate and nothing
    anywhere would notice.
    """
    job, _ = _summary_job()
    assert " ".join(job["if"].split()) == EXPECTED_IF


def test_the_trusted_job_binds_the_engine_environment():
    job, _ = _summary_job()
    assert job["environment"] == "shipmate-engine"


def test_the_trusted_job_runs_exactly_two_things_and_checks_nothing_out():
    """It runs at the base ref holding the App key. A checkout here would make it
    the canonical pull_request_target vulnerability, and so would any step that
    executes repository content by another route."""
    job, _ = _summary_job()
    assert [str(s["uses"]).split("@")[0] for s in job["steps"]] == EXPECTED_STEP_USES


def test_the_workflow_passes_exactly_these_values_to_the_summary_action():
    """Successor to test_the_workflow_passes_the_marker_count_to_the_summary_action."""
    job, _ = _summary_job()
    call = [s for s in job["steps"] if "actions/summary" in str(s.get("uses", ""))]
    assert len(call) == 1
    assert call[0]["with"] == EXPECTED_SUMMARY_WITH


def test_the_workflow_call_inputs_are_exactly_these():
    # `doc[True]` is not a typo: PyYAML parses the bare key `on:` as the boolean
    # True.
    _, doc = _summary_job()
    assert doc[True]["workflow_call"]["inputs"] == EXPECTED_INPUTS
