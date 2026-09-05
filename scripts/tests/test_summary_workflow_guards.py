"""The trusted summary job must refuse forks and unrequested drafts, and execute nothing.

It runs on `pull_request_target`, through the consumer's plan workflow, holding the App key. Two
things keep it safe: its `if:`, and the fact that it executes no repository content. The job now
lives in `plan.yml` alongside three jobs that check out and execute pull-request content, so the
job-id list is pinned here too. Every assertion below is on a parsed value -- `yaml.safe_load`,
then a whole `if:`/`environment:`/`with:` field -- rather than a substring of the raw file text.
The substring form was proven vacuous: four simultaneous mutations of the summary job (all three
trust guards inverted, `environment: shipmate-engine` commented out, the draft-skip deleted) left
the old suite's `1 failed, 762 passed` unchanged from baseline. A YAML comment or an inverted
operator can contain the same substring as the real guard; it cannot produce the same parsed
value.
"""

import yaml
from _loader import WORKFLOWS

WF = WORKFLOWS / "plan.yml"

#: Hand-written, never derived from the workflow. A constant lifted out of the file it checks
#: passes whatever the file says.
EXPECTED_IF = (
    "${{ !cancelled() && needs.facts.outputs.head-repo != '' && "
    "needs.facts.outputs.head-repo == github.repository && "
    "(needs.facts.outputs.is-draft == 'false' || needs.facts.outputs.on-demand == 'true') }}"
)
#: The whole job, as an ordered list of what each step runs. It subsumes "no checkout": a
#: checkout step, a `run:` step, or any extra step at all changes this list, where a substring
#: scan for "checkout" would miss every one of those.
EXPECTED_STEP_USES = [
    "actions/download-artifact",
    "ship-iac/shipmate/actions/summary",
]
EXPECTED_SUMMARY_WITH = {
    "pr-number": "${{ needs.facts.outputs.pr-number }}",
    "head-sha": "${{ needs.facts.outputs.head-sha }}",
    "detect-result": "${{ needs.detect.result }}",
    "plan-result": "${{ needs.plan.result }}",
    "planned-cells": "${{ needs.detect.outputs.count }}",
    "on-demand": "${{ needs.facts.outputs.on-demand }}",
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}
EXPECTED_JOB_IDS = ["facts", "detect", "plan", "summary"]


def _summary_job():
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert list(jobs) == EXPECTED_JOB_IDS, (
        f"plan.yml's jobs are {list(jobs)}; these guards cover only {EXPECTED_JOB_IDS}, and "
        "each job id is also a check-run name segment"
    )
    return jobs["summary"], doc


def test_the_trusted_job_refuses_forks_and_unrequested_drafts():
    """The whole `if:`, compared as one value.

    Every clause is load-bearing, the parentheses included: `&&` binds tighter than `||`, so
    losing them makes `on-demand` alone satisfy the guard. The decision cannot move to the
    consumer's file, because nothing inspects consumer YAML -- a consumer who dropped a clause
    would hand a fork an App-authored gate and nothing anywhere would notice. What the facts job
    supplies is a head repository, a draft flag, and whether a person named this run. The
    empty-string clause is what makes an omitted fact a refusal instead of a pass. Only the draft
    clause yields to `on-demand`; the fork clause guards the App key over fork-authored content
    and yields to no trigger.
    """
    job, _ = _summary_job()
    assert " ".join(job["if"].split()) == EXPECTED_IF


def test_the_trusted_job_binds_the_engine_environment():
    job, _ = _summary_job()
    assert job["environment"] == "shipmate-engine"


def test_the_trusted_job_runs_exactly_two_things_and_checks_nothing_out():
    """It runs at the base ref holding the App key. A checkout here would make it the canonical
    pull_request_target vulnerability, and so would any step that executes repository content by
    another route."""
    job, _ = _summary_job()
    assert [str(s["uses"]).split("@")[0] for s in job["steps"]] == EXPECTED_STEP_USES


def test_the_workflow_passes_exactly_these_values_to_the_summary_action():
    job, _ = _summary_job()
    call = [s for s in job["steps"] if "actions/summary" in str(s.get("uses", ""))]
    assert len(call) == 1
    assert call[0]["with"] == EXPECTED_SUMMARY_WITH
