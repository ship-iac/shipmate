"""The trusted summary job must refuse forks, and execute no consumer code.

It runs on `pull_request_target`, through the consumer's plan workflow, holding the App key. Two
things keep it safe: its `if:`, and the fact that it executes no consumer repository content. The
job now lives in `plan.yml` alongside two jobs that check out and execute pull-request content --
`detect` and `plan` -- so the job-id list is pinned here too. The job checks out nothing; any
checkout step, which under `pull_request_target` would take the pull request head, changes the
ordered step list below pins.
Every assertion below is on a parsed value -- `yaml.safe_load`,
then a whole `if:`/`environment:`/`with:` field -- rather than a substring of the raw file text.
The substring form was proven vacuous: four simultaneous mutations of the summary job (all three
trust guards inverted, `environment: shipmate-engine` commented out, the draft-skip deleted) left
the old suite's `1 failed, 762 passed` unchanged from baseline. A YAML comment or an inverted
operator can contain the same substring as the real guard; it cannot produce the same parsed
value.

A draft is not refused here: it reaches the job, and gate-state writes its gate pending with the
reason.
"""

from _loader import WORKFLOWS, local_action, step_by, workflow_yaml

WF = WORKFLOWS / "plan.yml"

#: Hand-written, never derived from the workflow. A constant lifted out of the file it checks
#: passes whatever the file says.
EXPECTED_IF = (
    "${{ !cancelled() && needs.facts.outputs.head-repo != '' && "
    "needs.facts.outputs.head-repo == github.repository }}"
)
#: The whole job, as an ordered list of what each step runs. A checkout step, a `run:`
#: step, or any extra step at all changes this list, where a substring scan would miss every one
#: of those.
EXPECTED_STEP_USES = [local_action("summary")]
EXPECTED_SUMMARY_WITH = {
    "pr-number": "${{ needs.facts.outputs.pr-number }}",
    "head-sha": "${{ needs.facts.outputs.head-sha }}",
    "detect-result": "${{ needs.detect.result }}",
    "detect-refusal": "${{ needs.detect.outputs.refusal }}",
    "plan-result": "${{ needs.plan.result }}",
    "planned-cells": "${{ needs.detect.outputs.count }}",
    "on-demand": "${{ needs.facts.outputs.on-demand }}",
    "is-draft": "${{ needs.facts.outputs.is-draft }}",
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
    "github-vars": "${{ toJSON(vars) }}",
    "unmanaged-stacks": "${{ needs.detect.outputs.unmanaged }}",
}
#: `detect`'s whole outputs map: `unmanaged` and `refusal` are what the `with:` above passes on.
EXPECTED_DETECT_OUTPUTS = {
    "matrix": "${{ steps.matrix.outputs.matrix }}",
    "empty": "${{ steps.matrix.outputs.empty }}",
    "count": "${{ steps.matrix.outputs.count }}",
    "unmanaged": "${{ steps.matrix.outputs.unmanaged }}",
    "refusal": "${{ steps.matrix.outputs.refusal }}",
}
EXPECTED_JOB_IDS = ["facts", "detect", "plan", "summary"]


def _summary_job():
    doc = workflow_yaml(WF)
    jobs = doc["jobs"]
    assert list(jobs) == EXPECTED_JOB_IDS, (
        f"plan.yml's jobs are {list(jobs)}; these guards cover only {EXPECTED_JOB_IDS}, and "
        "each job id is also a check-run name segment"
    )
    return jobs["summary"], doc


def test_the_trusted_job_refuses_forks():
    """The whole `if:`, compared as one value.

    Every clause is load-bearing. The decision cannot move to the consumer's file, because
    nothing inspects consumer YAML -- a consumer who dropped a clause would hand a fork an
    App-authored gate and nothing anywhere would notice. The empty-string clause is what makes an
    omitted head repository a refusal instead of a pass. The fork clause guards the App key over
    fork-authored content and yields to no trigger. There is no draft clause: a draft reaches the
    job, and gate-state writes its gate pending with the reason.

    Mutation: restore `&& (needs.facts.outputs.is-draft == 'false' ||
    needs.facts.outputs.on-demand == 'true')`.
    """
    job, _ = _summary_job()
    assert " ".join(job["if"].split()) == EXPECTED_IF


def test_the_trusted_job_binds_the_engine_environment():
    job, _ = _summary_job()
    assert job["environment"] == "shipmate-engine"


def test_the_trusted_job_checks_out_nothing_and_runs_exactly_these_steps():
    """It runs at the base ref holding the App key. A checkout of the pull request head here
    would make it the canonical pull_request_target vulnerability, and so would any step that
    executes repository content by another route, which is why the step list is compared whole:
    any checkout step at all reddens it. Mutation: re-add the cell-summary download step ahead of
    the action."""
    job, _ = _summary_job()
    assert [str(s["uses"]).split("@")[0] for s in job["steps"]] == EXPECTED_STEP_USES


def test_the_workflow_passes_exactly_these_values_to_the_summary_action():
    """Mutation: drop the `is-draft:` line -> gate-state never sees a draft.
    Mutation: drop the `unmanaged-stacks:` line -> the comment never names an unmanaged stack.
    Mutation: drop `detect`'s `unmanaged` output -> the same, silently.
    Mutation: drop `detect`'s `refusal` output -> the gate never names a detect refusal."""
    job, _ = _summary_job()
    call = [s for s in job["steps"] if "actions/summary" in str(s.get("uses", ""))]
    assert len(call) == 1
    assert call[0]["with"] == EXPECTED_SUMMARY_WITH
    assert workflow_yaml(WF)["jobs"]["detect"]["outputs"] == EXPECTED_DETECT_OUTPUTS


#: The whole download step, as `yaml.safe_load` returns it. Any extra key reddens the comparison:
#: an `if:` or a `github-token`, which the job's `contents: read` grant would not cover.
EXPECTED_DOWNLOAD_STEP = {
    "name": "Download plan cell summaries",
    "uses": "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "continue-on-error": True,
    "with": {"pattern": "cell-summary.*", "path": "cells"},
}


def test_the_summary_action_always_downloads_and_tolerates_a_failed_download():
    """Unconditional, because skipping the download when zero cells were planned makes
    gate-state's "more cells than planned" branch unreachable. `continue-on-error`, because a
    failed download must reach gate-state as a shortfall that holds the gate, not end the job
    before any gate is written. `path` is the readers' default directory.

    Mutations: drop `continue-on-error`; change `path`; add `github-token`; add
    `if: ${{ inputs.planned-cells != '0' }}`.
    """
    assert step_by("summary", name="Download plan cell summaries") == EXPECTED_DOWNLOAD_STEP
