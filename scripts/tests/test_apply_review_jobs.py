"""Guards the `review` job `apply.yml` calls from `apply-review.yml`, and its wiring into
`detect`.

The job re-reads the pull request's `reviewDecision` server-side, on both apply forms, so the
apply decision rests on GitHub's answer rather than on a dispatch input. Threat model is
accidental regression -- a line reverted in a refactor, a flag dropped, an `if:` re-introduced --
not a hostile edit to a SHA-pinned engine file. Three of the regressions are silently fail-open,
which is why they are pinned whole:

- an `if:` re-appearing on the calling `review` job or on the called one. A conditional
  review job can be skipped, a
  skipped job delivers an empty decision, and that is the state an `ungated-envs` action input
  wider than the repository variable used to exploit. Absence is the property, so it is asserted
  rather than assumed.
- `detect`'s needs list losing `review`: the decision then never arrives at all.
- `review` missing from `summary`'s `needs`. That is pinned as one whole-list-per-job map in
  `test_apply_dispatch_actor_guard.py` rather than a second time here, because `results:` is
  `join(needs.*.result, ',')` over that same list.

Everything is asserted over parsed structures, compared whole: a substring is
satisfied by a comment and by an inverted operator.
"""

import re

from _loader import ENGINE, step_by, workflow_yaml

_MINT = "actions/create-github-app-token"
_CHECKOUT = "actions/checkout"

#: The whole `if:` expression `apply.yml`'s `detect` carries, hand-written.
#: A failed `review` must skip it; nothing else may.
_DETECT_IF = "${{ !failure() && !cancelled() }}"
_DETECT_NEEDS = ["guard", "review"]
_REVIEW_WORKFLOW = "apply-review.yml"

#: The whole calling job, hand-written. No `if:` is part of the
#: value; the `secrets:` mapping is what lets the callee's `shipmate-engine` binding supply the key.
_CALLER = {
    "needs": ["guard"],
    "uses": f"./.github/workflows/{_REVIEW_WORKFLOW}",
    "permissions": {},
    "with": {"pr_number": "${{ inputs.pr_number }}"},
    "secrets": {"SHIPMATE_APP_PRIVATE_KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}"},
}

#: The whole `--jq` program, hand-written. It is the entire mapping from the GraphQL response to
#: the decision `detect` partitions on, and the fail-open form is one edit away:
#: `.data.repository.pullRequest.reviewDecision // "NONE"` -- comment-ops' expression, safe only
#: because that job proves the pull request exists first -- turns a pr_number matching no pull
#: request into the value that applies everything. Compared whole, because a check for
#: `MISSING_PR` alone passes an expression that also defaults a null decision to something
#: `_review_reason` lets through.
_REVIEW_JQ = (
    '--jq \'.data.repository.pullRequest | if type == "object" '
    'then (.reviewDecision // "NONE") else "MISSING_PR" end\''
)

#: The whole permission set the review mint may request: reading the decision needs
#: pull-requests, and nothing else.
_MINT_PERMISSIONS = {"permission-pull-requests": "read"}


def _jobs(workflow):
    return workflow_yaml(workflow)["jobs"]


def _review():
    return _jobs(_REVIEW_WORKFLOW)["review"]


def test_apply_calls_the_review_workflow_with_this_whole_job():
    """The targeted path once consulted the review decision nowhere at all, so an
    `ungated-envs` input wider than the repository variable applied unreviewed there
    unconditionally. The caller compared whole, so it cannot drop the call, gain an `if:` that
    skips it, or stop mapping the key.

    Mutations: add `if: ${{ inputs.pr_number != '' }}` to the caller; delete its `secrets:`.
    """
    assert _jobs("apply.yml")["review"] == _CALLER


def test_the_decision_output_reaches_the_callers():
    """An unmapped output arrives empty, which detect holds everything on: fail-closed, but every
    apply would then refuse. Mutation: point the workflow output at a job output that does not
    exist."""
    spec = workflow_yaml(_REVIEW_WORKFLOW)
    assert spec["on"]["workflow_call"]["outputs"]["decision"]["value"] == (
        "${{ jobs.review.outputs.decision }}"
    )
    assert _review()["outputs"] == {"decision": "${{ steps.rd.outputs.decision }}"}


def test_the_review_job_binds_the_engine_environment_and_displays_as_decision():
    """The App key reaches this job only through `shipmate-engine`, whose branch policy admits the
    default branch alone. `name` is pinned beside it because the job-name table in CONTRACT.md
    lists `review / decision`.

    Mutations: delete the `environment:` line; rename the job's `name:`.
    """
    assert _review().get("environment") == "shipmate-engine"
    assert _review().get("name") == "decision"


def test_the_review_job_checks_nothing_out():
    """It holds an App token, and a checkout would put branch-controlled content in the same
    job. Terramate over pull request head content belongs in `detect`, which holds no token."""
    offenders = [s for s in _review()["steps"] if _CHECKOUT in str(s.get("uses") or "")]
    assert not offenders, f"the review job checks out branch content: {offenders}"


def test_the_review_job_carries_no_if_and_so_always_runs():
    """The absence is the property, and an absence nothing asserts is fail-open by construction.
    A conditional review job can be skipped, and a skipped job yields an empty decision --
    which detect must read as hold-everything rather than as no-review-required."""
    assert "if" not in _review(), (
        f"the review job grew an `if:` ({_review().get('if')!r}); a review "
        "job that can be skipped delivers an empty decision to detect"
    )


def test_the_review_mint_requests_only_pull_requests_read():
    mints = [s for s in _review()["steps"] if _MINT in str(s.get("uses") or "")]
    assert len(mints) == 1, f"expected exactly one App-token mint in review, got {len(mints)}"
    with_ = mints[0]["with"]
    got = {k: v for k, v in with_.items() if k.startswith("permission-")}
    assert got == _MINT_PERMISSIONS


def test_detect_needs_review_and_refuses_to_run_after_it_failed():
    """One property, two halves: without `review` in `needs` the decision never
    arrives, and the explicit `if:` is what keeps a FAILED `review` from being
    read as anything but a dead run.

    Mutation: set detect's `if:` to `${{ always() }}`."""
    detect = _jobs("apply.yml")["detect"]
    assert detect.get("needs") == _DETECT_NEEDS
    # `.get`, not `[...]`: a deleted `if:` is a fail-open mutation, and a KeyError would red
    # without naming the expression that went missing.
    assert detect.get("if") == _DETECT_IF


def test_the_apply_detect_action_feeds_every_shipmate_env_var_the_script_reads():
    """Derived from the script's own source, both forms' reads, not a second hand-written list:
    a renamed read on either side is the regression this catches.

    Mutations: delete `SHIPMATE_HEAD_SHA` from `apply-detect`'s script step; add a read of
    `os.environ["SHIPMATE_X"]` to `bare_main`."""
    read = _shipmate_reads("apply-detect")
    assert "SHIPMATE_REVIEW_DECISION" in read, f"no review decision read: {sorted(read)}"
    missing = sorted(read - set(step_by("apply-detect", id="d")["env"]))
    assert not missing, f"the apply-detect action's env: block omits {missing}"


def test_the_unlock_action_feeds_every_shipmate_env_var_its_script_reads():
    """The same derivation for the one detect that reads no review decision.

    Mutation: add a read of `os.environ["SHIPMATE_X"]` to `unlock-detect`."""
    read = _shipmate_reads("unlock-detect")
    assert "SHIPMATE_ENV" in read, f"the derivation found no reads: {sorted(read)}"
    missing = sorted(read - set(step_by("unlock-detect", id="d")["env"]))
    assert not missing, f"the unlock-detect action's env: block omits {missing}"


def _shipmate_reads(detect):
    src = (ENGINE / "scripts" / detect).read_text(encoding="utf-8")
    return set(re.findall(r'os\.environ(?:\.get)?\(?\[?["\'](SHIPMATE_[A-Z0-9_]+)["\']', src))


def test_the_decision_query_distinguishes_a_missing_pull_request():
    """A null `pullRequest` is not "no review required": `gh` exits 0 with no
    errors array, so the jq default is the only thing standing between a bad
    pr_number and applying every environment unreviewed."""
    step = next(s for s in _review()["steps"] if s.get("id") == "rd")
    jq = [ln.strip() for ln in step["run"].splitlines() if ln.strip().startswith("--jq")]
    assert jq == [_REVIEW_JQ + ")"], f"the review job's jq program changed: {jq}"
