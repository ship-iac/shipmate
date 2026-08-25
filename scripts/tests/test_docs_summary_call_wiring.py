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
from _loader import ENGINE, WORKFLOWS, load_script

doctor = load_script("doctor")

_FENCE = re.compile(r"^(?P<indent>[ \t]*)```yaml[ \t]*$\n(?P<body>.*?)^\1```", re.M | re.S)

# Owner-agnostic, like test_docs_yaml_parses.py's selectors: the pages publish
# `<owner>/shipmate/...` as well as the engine's own org.
_SUMMARY_CALL = "/shipmate/.github/workflows/summary.yml@"
_BUILD_MATRIX = "/shipmate/actions/build-matrix@"

_HEAD_REPO = "${{ needs.facts.outputs.head-repo }}"

_EXPECTED_SUMMARY_WITH = {
    "pr-number": "${{ needs.facts.outputs.pr-number }}",
    "head-sha": "${{ needs.facts.outputs.head-sha }}",
    "detect-result": "${{ needs.detect.result }}",
    "plan-result": "${{ needs.plan.result }}",
    "planned-cells": "${{ needs.detect.outputs.count }}",
    "head-repo": _HEAD_REPO,
    "is-draft": "${{ needs.facts.outputs.is-draft }}",
}

_EXPECTED_PLAN_MATRIX_WITH = {
    "base-sha": "${{ needs.facts.outputs.base-sha }}",
    "head-repo": _HEAD_REPO,
    "head-sha": "${{ needs.facts.outputs.head-sha }}",
}

_EXPECTED_DRIFT_MATRIX_WITH = {
    "base-sha": "",
    "all-stacks": "true",
    "no-pull-request": "true",
}

_SUMMARY_WF = WORKFLOWS / "summary.yml"
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


def test_the_documented_call_passes_exactly_the_inputs_the_workflow_declares():
    """Three files hand-write these input names: this page's snippet, the
    workflow's `workflow_call` declarations, and `scripts/doctor`'s wiring
    constant. A rename that updates one leaves the others stale with a green
    suite -- a documented wrapper whose summary job silently skips, or a probe
    looking for a key nobody passes.

    Both sides are derived here, which is not the `CLAUDE.md` hand-written-constant
    rule being broken: that rule pins a *value* against a constant, and the two
    expected mappings above do exactly that. This pins *agreement between two
    files*, neither of which is the constant for the other. Both sides are
    asserted non-empty, so a selector that matches nothing fails instead of
    passing vacuously."""
    declared = yaml.safe_load(_SUMMARY_WF.read_text(encoding="utf-8"))
    # `doc[True]`: PyYAML parses the bare key `on:` as the boolean True.
    names = sorted(declared[True]["workflow_call"]["inputs"])
    calls = dict(_summary_calls(_PLAN_PAGE))
    assert names and calls
    for job, call in calls.items():
        passed = sorted(call.get("with") or {})
        assert passed and passed == names, (
            f"docs/getting-started.md job `{job}` passes {passed} to the engine's "
            f"summary.yml, which declares {names}"
        )


def test_doctors_wiring_constant_expects_what_the_page_documents():
    """The third file this module's docstring names. `doctor.SUMMARY_WIRING` is a
    hand-written copy of these two expressions and is compared against a
    consumer's real `plan.yml`, so a change to the documented expression that
    leaves it behind makes the probe WARN on every correctly wired repository --
    a finding on a healthy repo, which is what teaches readers to ignore the
    suite. Nothing else pins the two together: `test_doctor.py`'s fixtures are
    written from `SUMMARY_WIRING`'s own side."""
    assert doctor.SUMMARY_WIRING == {
        key: _EXPECTED_SUMMARY_WITH[key] for key in ("head-repo", "is-draft")
    }, (
        "scripts/doctor's SUMMARY_WIRING drifted from the documented wrapper: "
        f"{doctor.SUMMARY_WIRING}"
    )


def test_doctors_build_matrix_constant_expects_what_the_page_documents():
    """Same failure mode as the summary constant above, on the step's own two
    inputs: `doctor.BUILD_MATRIX_WIRING` is a hand-written copy of what this page
    documents, and a documented expression that leaves it behind makes the probe
    WARN on every correctly wired repository."""
    assert doctor.BUILD_MATRIX_WIRING == {
        key: _EXPECTED_PLAN_MATRIX_WITH[key] for key in ("head-repo", "head-sha")
    }, (
        "scripts/doctor's BUILD_MATRIX_WIRING drifted from the documented wrapper: "
        f"{doctor.BUILD_MATRIX_WIRING}"
    )


def test_documented_plan_build_matrix_states_the_head_repository_and_commit():
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
