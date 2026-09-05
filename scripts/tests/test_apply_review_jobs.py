"""Guards the `review` job both apply workflows carry, and its wiring into each
`detect`.

The job re-reads the pull request's `reviewDecision` server-side, on both apply paths, so the
apply decision rests on GitHub's answer rather than on a dispatch input. Threat model is
accidental regression -- a line reverted in a refactor, a flag dropped, an `if:` re-introduced --
not a hostile edit to a SHA-pinned engine file. Three of the regressions are silently fail-open,
which is why they are pinned whole:

- an `if:` re-appearing on either `review` job. A conditional review job can be skipped, a
  skipped job delivers an empty decision, and that is the state an `ungated-envs` action input
  wider than the repository variable used to exploit. Absence is the property, so it is asserted
  rather than assumed.
- either `detect`'s needs list losing `review`: the decision then never arrives at all.
- `review` missing from `summary`'s `needs`. That is pinned as one whole-list-per-job map in
  `test_apply_dispatch_actor_guard.py` rather than a second time here, because `results:` is
  `join(needs.*.result, ',')` over that same list.

Everything is asserted over `yaml.safe_load`ed structures, compared whole: a substring is
satisfied by a comment and by an inverted operator.
"""

import re

import pytest
import yaml
from _loader import ENGINE, WORKFLOWS, action_yaml

_MINT = "actions/create-github-app-token"
_CHECKOUT = "actions/checkout"

#: The whole `if:` expression `apply-all.yml`'s `detect` carries, hand-written.
#: A failed `review` must skip it; nothing else may.
_DETECT_IF = "${{ !failure() && !cancelled() }}"
_DETECT_NEEDS = ["guard", "review"]
_APPLY_PATHS = ("apply-all.yml", "apply.yml")

#: The whole `--jq` program, hand-written. It is the entire mapping from the GraphQL response to
#: the decision `detect` partitions on, and the fail-open form is one edit away:
#: `.data.repository.pullRequest.reviewDecision // "NONE"` -- comment-ops' expression, safe only
#: because that job proves the pull request exists first -- turns a pr_number matching no pull
#: request into the value that applies everything. Compared whole, because a check for
#: `MISSING_PR` alone passes an expression that also defaults a null decision to something
#: `review_held` lets through.
_REVIEW_JQ = (
    '--jq \'.data.repository.pullRequest | if type == "object" '
    'then (.reviewDecision // "NONE") else "MISSING_PR" end\''
)

#: The whole permission set the review mint may request: reading the decision needs
#: pull-requests, and nothing else.
_MINT_PERMISSIONS = {"permission-pull-requests": "read"}


def _jobs(workflow):
    return yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"]


def _review(workflow="apply-all.yml"):
    return _jobs(workflow)["review"]


def test_both_apply_paths_carry_an_identical_review_job():
    """The targeted path once consulted the review decision nowhere at all, so an
    `ungated-envs` input wider than the repository variable applied unreviewed there
    unconditionally. One job, one shape, both files, compared whole, so the two cannot drift
    apart. `_REVIEW_JQ` is the hand-written content anchor that keeps them from drifting
    together."""
    assert _review("apply.yml") == _review("apply-all.yml")


def test_the_review_job_checks_nothing_out():
    """It holds an App token, and a checkout would put branch-controlled content in the same
    job. Terramate over pull request head content belongs in `detect`, which holds no token."""
    for name in _APPLY_PATHS:
        offenders = [s for s in _review(name)["steps"] if _CHECKOUT in str(s.get("uses") or "")]
        assert not offenders, f"{name}: the review job checks out branch content: {offenders}"


def test_the_review_job_carries_no_if_and_so_always_runs():
    """The absence is the property, and an absence nothing asserts is fail-open by construction.
    A conditional review job can be skipped, a skipped job yields an empty decision, and the
    condition this replaced -- `vars.SHIPMATE_UNGATED_ENVS != ''` -- is what let a literal
    `ungated-envs` input apply every environment unreviewed."""
    for name in _APPLY_PATHS:
        assert "if" not in _review(name), (
            f"{name}: the review job grew an `if:` ({_review(name).get('if')!r}); a review "
            "job that can be skipped delivers an empty decision to detect"
        )


def test_the_review_mint_requests_only_pull_requests_read():
    for name in _APPLY_PATHS:
        mints = [s for s in _review(name)["steps"] if _MINT in str(s.get("uses") or "")]
        assert len(mints) == 1, (
            f"{name}: expected exactly one App-token mint in review, got {len(mints)}"
        )
        with_ = mints[0]["with"]
        got = {k: v for k, v in with_.items() if k.startswith("permission-")}
        assert got == _MINT_PERMISSIONS


def test_detect_needs_review_and_refuses_to_run_after_it_failed():
    """One property, two halves: without `review` in `needs` the decision never
    arrives, and the explicit `if:` is what keeps a FAILED `review` from being
    read as anything but a dead run."""
    detect = _jobs("apply-all.yml")["detect"]
    assert detect.get("needs") == _DETECT_NEEDS
    # `.get`, not `[...]`: a deleted `if:` is a fail-open mutation, and a KeyError would red
    # without naming the expression that went missing.
    assert detect.get("if") == _DETECT_IF


#: workflow -> the detect action it calls, which is also the script name. Both apply paths
#: thread the same two values, and neither may be supplied without the other, because a list with
#: no decision exempts envs from a check nothing ran.
_DETECTS = {"apply-all.yml": "apply-all-detect", "apply.yml": "apply-detect"}


@pytest.mark.parametrize(("workflow", "detect"), sorted(_DETECTS.items()))
def test_detect_sources_both_new_inputs_from_the_server_side_values(workflow, detect):
    step = next(
        s
        for s in _jobs(workflow)["detect"]["steps"]
        if f"actions/{detect}@" in str(s.get("uses") or "")
    )
    with_ = step["with"]
    assert with_["ungated-envs"] == "${{ vars.SHIPMATE_UNGATED_ENVS }}"
    # Raw, never `|| 'NONE'`: a decision that never arrived must arrive empty, which is the
    # hold-everything, refuse-the-run value.
    assert with_["review-decision"] == "${{ needs.review.outputs.decision }}"


@pytest.mark.parametrize("detect", sorted(_DETECTS.values()))
def test_the_action_feeds_every_shipmate_env_var_the_script_reads(detect):
    """Derived from the script's own source, not a second hand-written list: a renamed read on
    either side is the regression this catches."""
    src = (ENGINE / "scripts" / detect).read_text(encoding="utf-8")
    read = set(re.findall(r'os\.environ(?:\.get)?\(?\[?["\'](SHIPMATE_[A-Z0-9_]+)["\']', src))
    assert {"SHIPMATE_UNGATED_ENVS", "SHIPMATE_REVIEW_DECISION"} <= read, (
        f"{detect} no longer reads both review variables: {sorted(read)}"
    )
    step = action_yaml(detect)["runs"]["steps"][0]
    missing = read - set(step["env"])
    assert not missing, f"the {detect} action's env: block omits {sorted(missing)}"


def test_the_decision_query_distinguishes_a_missing_pull_request():
    """A null `pullRequest` is not "no review required": `gh` exits 0 with no
    errors array, so the jq default is the only thing standing between a bad
    pr_number and applying every environment unreviewed."""
    step = next(s for s in _review()["steps"] if s.get("id") == "rd")
    jq = [ln.strip() for ln in step["run"].splitlines() if ln.strip().startswith("--jq")]
    assert jq == [_REVIEW_JQ + ")"], f"the review job's jq program changed: {jq}"
