"""Guards `.github/workflows/apply-all.yml`'s `review` job and its wiring into
`detect`.

The job re-reads the PR's `reviewDecision` server-side so the bare-apply
partition rests on GitHub's answer rather than on a dispatch input. Threat model
is accidental regression -- a line reverted in a refactor, a flag dropped, an
`if:` "simplified" -- not a hostile edit to a SHA-pinned engine file. Two of the
regressions are silently fail-OPEN, which is why they are pinned whole:

- `detect`'s `if:` reverting to GHA's default `success()`. `review` is *skipped*
  for an un-opted-in consumer, `success()` fails on a skipped `need`, and the
  obvious repair (dropping `review` from `needs`) applies every environment with
  no decision at all. The needs list and the `if:` are one property, so both are
  compared to hand-written constants.
- `review` missing from `summary`'s `needs`. `results:` is
  `join(needs.*.result, ',')` over `summary`'s own needs, so a failed `review`
  would render the non-failure comment over a dead run.

Everything is asserted over `yaml.safe_load`ed structures, compared whole: a
substring is satisfied by a comment and by an inverted operator.
"""

import re

import yaml
from _loader import ENGINE, WORKFLOWS, action_yaml

_MINT = "actions/create-github-app-token"
_CHECKOUT = "actions/checkout"

#: The whole `if:` expressions, hand-written. `review` runs only for a consumer
#: that set the variable; `detect` must survive `review` being skipped while
#: still refusing to run after it failed.
_REVIEW_IF = "${{ vars.SHIPMATE_UNGATED_ENVS != '' }}"
_DETECT_IF = "${{ !failure() && !cancelled() }}"
_DETECT_NEEDS = ["guard", "review"]
_SUMMARY_NEEDS = ["guard", "review", "detect", "envlevel0", "envlevel1", "envlevel2", "envlevel3"]

#: The whole `--jq` program, hand-written. It is the entire mapping from the
#: GraphQL response to the decision `detect` partitions on, and the fail-open
#: form is one edit away: `.data.repository.pullRequest.reviewDecision //
#: "NONE"` (comment-ops' expression, which is safe only because that job proves
#: the pull request exists first) turns a pr_number matching no pull request
#: into the value that applies everything. Compared whole -- a check for
#: `MISSING_PR` alone passes an expression that also defaults a null decision
#: to something `review_held` lets through.
_REVIEW_JQ = (
    '--jq \'.data.repository.pullRequest | if type == "object" '
    'then (.reviewDecision // "NONE") else "MISSING_PR" end\''
)

#: The whole permission set the review mint may request -- reading the decision
#: needs pull-requests, and nothing else.
_MINT_PERMISSIONS = {"permission-pull-requests": "read"}


def _jobs(workflow):
    return yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"]


def _review():
    return _jobs("apply-all.yml")["review"]


def test_the_review_job_checks_nothing_out():
    """It holds an App token; a checkout would put branch-controlled content in
    the same job. Terramate over PR head content belongs in `detect`, which
    holds no token."""
    offenders = [s for s in _review()["steps"] if _CHECKOUT in str(s.get("uses") or "")]
    assert not offenders, f"the review job checks out branch content: {offenders}"


def test_the_review_job_runs_only_for_an_opted_in_consumer():
    assert _review()["if"] == _REVIEW_IF


def test_the_review_mint_requests_only_pull_requests_read():
    mints = [s for s in _review()["steps"] if _MINT in str(s.get("uses") or "")]
    assert len(mints) == 1, f"expected exactly one App-token mint in review, got {len(mints)}"
    with_ = mints[0]["with"]
    got = {k: v for k, v in with_.items() if k.startswith("permission-")}
    assert got == _MINT_PERMISSIONS


def test_detect_needs_review_and_survives_it_being_skipped():
    """One property, two halves: without `review` in `needs` the decision never
    arrives, and without the explicit `if:` GHA's default `success()` skips
    `detect` behind a skipped `review` -- which is the whole un-opted-in path."""
    detect = _jobs("apply-all.yml")["detect"]
    assert detect.get("needs") == _DETECT_NEEDS
    # `.get`, not `[...]`: a DELETED `if:` is the fail-open mutation, and a
    # KeyError would red without naming the expression that went missing.
    assert detect.get("if") == _DETECT_IF


def test_detect_sources_both_new_inputs_from_the_server_side_values():
    step = next(
        s
        for s in _jobs("apply-all.yml")["detect"]["steps"]
        if "actions/apply-all-detect" in str(s.get("uses") or "")
    )
    with_ = step["with"]
    assert with_["ungated-envs"] == "${{ vars.SHIPMATE_UNGATED_ENVS }}"
    # Raw, never `|| 'NONE'`: a skipped review job must arrive empty, which is
    # the hold-everything value.
    assert with_["review-decision"] == "${{ needs.review.outputs.decision }}"


def test_the_action_feeds_every_shipmate_env_var_the_script_reads():
    """Derived from the script's own source, not a second hand-written list --
    a renamed read on either side is the regression this catches."""
    src = (ENGINE / "scripts" / "apply-all-detect").read_text(encoding="utf-8")
    read = set(re.findall(r'os\.environ(?:\.get)?\(?\[?["\'](SHIPMATE_[A-Z0-9_]+)["\']', src))
    assert {"SHIPMATE_UNGATED_ENVS", "SHIPMATE_REVIEW_DECISION"} <= read, (
        f"the script no longer reads both review-partition variables: {sorted(read)}"
    )
    step = action_yaml("apply-all-detect")["runs"]["steps"][0]
    missing = read - set(step["env"])
    assert not missing, f"the action's env: block omits {sorted(missing)}"


def test_the_decision_query_distinguishes_a_missing_pull_request():
    """A null `pullRequest` is not "no review required": `gh` exits 0 with no
    errors array, so the jq default is the only thing standing between a bad
    pr_number and applying every environment unreviewed."""
    step = next(s for s in _review()["steps"] if s.get("id") == "rd")
    jq = [ln.strip() for ln in step["run"].splitlines() if ln.strip().startswith("--jq")]
    assert jq == [_REVIEW_JQ + ")"], f"the review job's jq program changed: {jq}"


def test_summary_needs_review_so_a_failed_review_reddens_the_comment():
    assert _jobs("apply-all.yml")["summary"]["needs"] == _SUMMARY_NEEDS


def test_the_targeted_apply_workflow_grows_no_review_job():
    """`apply.yml` is `shipmate apply <env>`; comment-ops already decided that
    apply's authorization. A second decision point there would be a second
    answer to the same question."""
    assert "review" not in _jobs("apply.yml")
