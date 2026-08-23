"""Guards the documented wiring of the two guards that decide on stated facts.

The fork refusal and the draft skip read inputs the wrapper passes, so the
snippets consumers paste are what makes those guards real. Omitting `head-repo`
on `build-matrix` fails `detect` loudly; omitting either input on the `summary`
call SKIPS that job, which writes no gate at all and says nothing on the run
page -- a snippet missing them documents a wrapper that cannot merge anything.
`drift.md`'s `no-pull-request` is the same class: without it the documented
nightly is refused.

Whole-mapping comparisons against hand-written literals, for the reason
`CLAUDE.md` gives: a "contains head-repo" check relocates the hole to whichever
key it does not name, and a constant read out of the page would agree with
whatever the page says. `test_docs_yaml_parses.py` proves these fences parse; it
cannot see a dropped input.
"""

import re
import textwrap

import yaml
from _loader import ENGINE

_FENCE = re.compile(r"^(?P<indent>[ \t]*)```yaml[ \t]*$\n(?P<body>.*?)^\1```", re.M | re.S)

# Owner-agnostic, like test_docs_yaml_parses.py's selectors: the pages publish
# `<owner>/shipmate/...` as well as the engine's own org.
_SUMMARY_CALL = "/shipmate/.github/workflows/summary.yml@"
_BUILD_MATRIX = "/shipmate/actions/build-matrix@"

_HEAD_REPO = "${{ github.event.pull_request.head.repo.full_name }}"

_EXPECTED_SUMMARY_WITH = {
    "pr-number": "${{ github.event.pull_request.number }}",
    "head-sha": "${{ github.event.pull_request.head.sha }}",
    "detect-result": "${{ needs.detect.result }}",
    "plan-result": "${{ needs.plan.result }}",
    "planned-cells": "${{ needs.detect.outputs.count }}",
    "head-repo": _HEAD_REPO,
    "is-draft": "${{ github.event.pull_request.draft }}",
}

_EXPECTED_PLAN_MATRIX_WITH = {
    "base-sha": "${{ github.event.pull_request.base.sha }}",
    "head-repo": _HEAD_REPO,
}

_EXPECTED_DRIFT_MATRIX_WITH = {
    "base-sha": "",
    "all-stacks": "true",
    "no-pull-request": "true",
}

_PLAN_PAGE = ENGINE / "docs" / "getting-started.md"
_DRIFT_PAGE = ENGINE / "docs" / "drift.md"


def _fence_docs(page):
    for m in _FENCE.finditer(page.read_text(encoding="utf-8")):
        doc = yaml.safe_load(textwrap.dedent(m.group("body")))
        if isinstance(doc, dict):
            yield doc


def _jobs(page):
    for doc in _fence_docs(page):
        for name, job in (doc.get("jobs") or {}).items():
            if isinstance(job, dict):
                yield name, job


def _summary_calls(page):
    """(job name, job) per job whose `uses:` is the engine's summary workflow."""
    for name, job in _jobs(page):
        if _SUMMARY_CALL in (job.get("uses") or ""):
            yield name, job


def _build_matrix_steps(page):
    """(job name, step) per `build-matrix` step in the page's ```yaml fences."""
    for name, job in _jobs(page):
        for step in job.get("steps") or []:
            if _BUILD_MATRIX in (step.get("uses") or ""):
                yield name, step


def test_the_documented_call_sites_are_found():
    """A floor under the three guards below, which assert nothing when their
    selectors match nothing: renaming a documented job out of a selector's reach
    fails here rather than going quiet."""
    found = (
        sorted(job for job, _ in _summary_calls(_PLAN_PAGE)),
        sorted(job for job, _ in _build_matrix_steps(_PLAN_PAGE)),
        sorted(job for job, _ in _build_matrix_steps(_DRIFT_PAGE)),
    )
    assert found == (["summary"], ["detect"], ["detect"]), (
        f"documented summary / build-matrix call sites changed: {found}"
    )


def test_documented_summary_call_states_the_facts_it_decides_on():
    for job, call in _summary_calls(_PLAN_PAGE):
        assert call.get("with") == _EXPECTED_SUMMARY_WITH, (
            f"docs/getting-started.md job `{job}` must pass exactly "
            f"{_EXPECTED_SUMMARY_WITH} to the engine's summary.yml; got {call.get('with')!r}"
        )


def test_documented_plan_build_matrix_states_the_head_repository():
    for job, step in _build_matrix_steps(_PLAN_PAGE):
        assert step.get("with") == _EXPECTED_PLAN_MATRIX_WITH, (
            f"docs/getting-started.md job `{job}` must pass exactly "
            f"{_EXPECTED_PLAN_MATRIX_WITH} to build-matrix; got {step.get('with')!r}"
        )


def test_documented_drift_build_matrix_opts_out_of_the_pull_request_check():
    for job, step in _build_matrix_steps(_DRIFT_PAGE):
        assert step.get("with") == _EXPECTED_DRIFT_MATRIX_WITH, (
            f"docs/drift.md job `{job}` must pass exactly "
            f"{_EXPECTED_DRIFT_MATRIX_WITH} to build-matrix; got {step.get('with')!r}"
        )
