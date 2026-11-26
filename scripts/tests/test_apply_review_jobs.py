"""Guards `actions/apply-pr-read` and its two call sites, `apply-run.yml`'s `review` and `detect`.

The action re-reads the pull request's `reviewDecision` server-side, on every apply form, so the
apply decision rests on GitHub's answer rather than on a dispatch input, and it refuses unless
the dispatch's `ref` is the head of an open, non-draft, same-repository pull request: a writer's
hand-made dispatch pairing an approved number with another head never reaches decide(). `review`
reads with its App token and `detect` with its job token, beside each other; `lock` refuses
unless both read the same decision (`test_apply_lock_wiring.py`). The action's shell body is
executed against a `gh` stub for each refusal. Threat model is accidental regression -- a line
reverted in a refactor, a flag dropped, an `if:` re-introduced, a step moved -- not a hostile
edit to a SHA-pinned engine file. Pinned whole here, and in `test_apply_dispatch_actor_guard.py`:

- an `if:` re-appearing on the `review` job. A conditional review job can be skipped, and a
  skipped job delivers an empty decision, which every apply form fails on
  (`refuse_undetermined`). Absence is the property, so the job mapping is compared whole
  rather than the absence assumed.
- `detect` gaining `needs` or an `if:`: it would wait for `review` again.
- either call site reading with another token, pull request or ref, or `detect` checking out
  before its read accepted the pull request.
- `review` missing from `summary`'s `needs`. That is pinned as one whole-list-per-job map in
  `test_apply_dispatch_actor_guard.py` rather than a second time here, because `results:` is
  `join(needs.*.result, ',')` over that same list.

Everything is asserted over parsed structures, compared whole: a substring is
satisfied by a comment and by an inverted operator.
"""

import json
import os
import re
import shutil

import pytest
from _loader import (
    ENGINE,
    ENGINE_ENVIRONMENT,
    action_yaml,
    bash_only,
    run_step,
    step_by,
    workflow_jobs,
)

_MINT = "actions/create-github-app-token"
_CHECKOUT = "actions/checkout"

#: The whole `review` job minus its steps, hand-written. No `if:` is part of the value, and the
#: App key reaches the job only through `shipmate-engine`, whose branch policy admits the default
#: branch alone. An unmapped output arrives empty, on which detect fails every apply form.
_REVIEW_JOB = {
    "runs-on": "ubuntu-slim",
    "environment": ENGINE_ENVIRONMENT,
    "permissions": {},
    "outputs": {
        "decision": "${{ steps.rd.outputs.decision }}",
        "unbound": "${{ steps.rd.outputs.unbound }}",
        "admitted": "${{ steps.admit.outputs.admitted }}",
    },
}

#: The action's one step, its `run` with comment lines stripped, hand-written. The `--jq` program
#: and the refusals are the entire mapping from the GraphQL response to the decision `detect`
#: partitions on, and to whether `ref` is the open, same-repository pull request's own head.
_RD_STEP = {
    "id": "rd",
    "shell": "bash",
    "env": {
        "GH_TOKEN": "${{ inputs.token }}",
        "OWNER": "${{ github.repository_owner }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        "REF": "${{ inputs.ref }}",
    },
    "run": "".join(
        line + "\n"
        for line in (
            "set -euo pipefail",
            "refuse() {",
            "  local msg=\"${1//[$'\\r\\n']/ }\"",
            '  echo "refusal=$msg" >> "$GITHUB_OUTPUT"',
            '  echo "::error::$msg"',
            "  exit 1",
            "}",
            "rc=0",
            (
                "pr=$(gh api graphql -f query='query($owner:String!,$repo:String!,$pr:Int!)"
                "{repository(owner:$owner,name:$repo){pullRequest(number:$pr)"
                "{reviewDecision state isDraft isCrossRepository headRefOid}}}' \\"
            ),
            '  -F owner="$OWNER" -F repo="${GITHUB_REPOSITORY#*/}" -F pr="$PR_NUMBER" \\',
            (
                "  --jq '.data.repository.pullRequest | select(. != null) | [.state, .isDraft, "
                '.isCrossRepository, .headRefOid, (.reviewDecision // "NONE")] | @tsv\') || rc=$?'
            ),
            'if [[ "$rc" -ne 0 && "$pr" == *\'"type":"NOT_FOUND"\'* ]]; then',
            '  echo "unbound=true" >> "$GITHUB_OUTPUT"',
            (
                '  refuse "Pull request #$PR_NUMBER does not exist in this repository; '
                'nothing applies."'
            ),
            "fi",
            'if [[ "$rc" -ne 0 ]]; then',
            (
                '  refuse "Could not read pull request #$PR_NUMBER (gh exited $rc); '
                'nothing applies. Comment \\`shipmate apply\\` on the pull request again."'
            ),
            "fi",
            'if [[ -z "$pr" ]]; then',
            '  echo "unbound=true" >> "$GITHUB_OUTPUT"',
            (
                '  refuse "Pull request #$PR_NUMBER does not exist in this repository; '
                'nothing applies."'
            ),
            "fi",
            "IFS=$'\\t' read -r state draft cross head rd <<< \"$pr\"",
            'if [[ "$state" != OPEN ]]; then',
            '  echo "unbound=true" >> "$GITHUB_OUTPUT"',
            (
                '  refuse "Pull request #$PR_NUMBER is $state, not OPEN; nothing applies. '
                "A merged pull request's plans apply through the merge deploy.\""
            ),
            "fi",
            'if [[ "$draft" != false ]]; then',
            (
                '  refuse "Pull request #$PR_NUMBER is a draft; nothing applies. '
                'Mark it ready for review first."'
            ),
            "fi",
            'if [[ "$cross" != false ]]; then',
            '  refuse "Pull request #$PR_NUMBER comes from a fork; nothing applies."',
            "fi",
            'if [[ "$head" != "$REF" ]]; then',
            (
                '  refuse "Pull request #$PR_NUMBER head is $head, but this dispatch names '
                '$REF; nothing applies. Comment \\`shipmate apply\\` on the pull request."'
            ),
            "fi",
            'echo "decision=$rd" >> "$GITHUB_OUTPUT"',
        )
    ),
}

#: The whole permission set the review mint may request: reading the decision needs
#: pull-requests, and nothing else.
_MINT_PERMISSIONS = {"permission-pull-requests": "read"}


def _review():
    return workflow_jobs("apply-run.yml")["review"]


def _rd():
    (step,) = action_yaml("apply-pr-read")["runs"]["steps"]
    return step


#: The whole `outputs:` of `actions/apply-pr-read`, hand-written. Each call site reads them by
#: name; one the action stops mapping reads '' there.
_PR_READ_OUTPUTS = {
    "decision": {
        "description": (
            "The pull request's `reviewDecision`, `NONE` when it has none; empty when the step "
            "refused."
        ),
        "value": "${{ steps.rd.outputs.decision }}",
    },
    "unbound": {
        "description": "`true` when no open pull request matched; empty otherwise.",
        "value": "${{ steps.rd.outputs.unbound }}",
    },
    "refusal": {
        "description": (
            "The refusal message without the `::error::` prefix, every CR and LF replaced by a "
            "space; empty when the step did not refuse. Set although the step fails. Carries the "
            "dispatcher-supplied `ref`, so a reader passes it through `env:`."
        ),
        "value": "${{ steps.rd.outputs.refusal }}",
    },
}


def test_the_pr_read_action_declares_exactly_these_outputs():
    """Mutation: read `unbound` from `steps.read.outputs.unbound`."""
    assert action_yaml("apply-pr-read")["outputs"] == _PR_READ_OUTPUTS


#: Each call site of the action, whole, by job: `review` reads with its App token, `detect` with
#: its job token, both the dispatch's own pull request and ref.
_READ_SITES = {
    "review": {
        "id": "rd",
        "if": "${{ steps.admit.outputs.admitted == 'true' }}",
        "uses": "$/actions/apply-pr-read",
        "with": {
            "token": "${{ steps.token.outputs.token }}",
            "pr-number": "${{ inputs.pr_number }}",
            "ref": "${{ inputs.ref }}",
        },
    },
    "detect": {
        "id": "pr",
        "uses": "$/actions/apply-pr-read",
        "with": {
            "token": "${{ github.token }}",
            "pr-number": "${{ inputs.pr_number }}",
            "ref": "${{ inputs.ref }}",
        },
    },
}


def test_the_review_job_is_this_whole_mapping():
    """The targeted path once consulted the review decision nowhere at all, so an
    exempt-list action input wider than the repository variable applied unreviewed there
    unconditionally. Compared whole, so the job cannot gain an `if:` that skips it, lose its
    environment binding, or widen its token.

    Mutations: add `if: always()`; `environment: shipmate-engine-x`;
    `permissions: {pull-requests: read}`.
    """
    assert {k: v for k, v in _review().items() if k != "steps"} == _REVIEW_JOB


def test_the_review_job_checks_nothing_out():
    """It holds an App token, and a checkout would put branch-controlled content in the same
    job. Terramate over pull request head content belongs in `detect`, which holds no token."""
    offenders = [s for s in _review()["steps"] if _CHECKOUT in str(s.get("uses") or "")]
    assert not offenders, f"the review job checks out branch content: {offenders}"


def test_the_review_mint_requests_only_pull_requests_read():
    """`review`'s own `token` step, which `rd` reads with; the `waiters` job's reply mints inside
    `actions/apply-waiters`. Mutation: widen it to `pull-requests: write` -- red."""
    mints = [s for s in _review()["steps"] if _MINT in str(s.get("uses") or "")]
    assert len(mints) == 1, f"expected exactly one App-token mint in review, got {len(mints)}"
    with_ = mints[0]["with"]
    got = {k: v for k, v in with_.items() if k.startswith("permission-")}
    assert got == _MINT_PERMISSIONS


def test_detect_runs_beside_review():
    """`detect` reads the decision itself, so it waits on no job; `lock` needs both and compares
    their reads.

    Mutations: add `needs: [review]` to `detect`; add `if: ${{ always() }}` to it."""
    detect = workflow_jobs("apply-run.yml")["detect"]
    assert (detect.get("needs"), detect.get("if")) == (None, None)


@pytest.mark.parametrize("job", sorted(_READ_SITES))
def test_each_job_reads_the_pull_request_through_the_one_action(job):
    """Mutation: `detect` passes `token: ${{ steps.token.outputs.token }}`."""
    steps = [s for s in workflow_jobs("apply-run.yml")[job]["steps"] if "apply-pr-read" in str(s)]
    assert steps == [_READ_SITES[job]]


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


def test_the_rd_step_is_this_whole_step():
    """Compared whole, so the query cannot read another pull request and the refusals cannot be
    reordered or reworded unseen; the tests below execute the same text.

    Mutations: `-F pr="$PR_NUMBER"` to `-F pr=1` in the action; delete `REF` from `env`."""
    step = _rd()
    run = "".join(ln + "\n" for ln in step["run"].splitlines() if not ln.lstrip().startswith("#"))
    assert {**step, "run": run} == _RD_STEP


#: A `gh` that runs the call's own `--jq` program over `$GH_RESPONSE`, as `gh` does. `tr` drops
#: the CR a Windows jq ends lines with.
_JQ_GH = """#!/bin/bash
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--jq" ]; then printf '%s' "$GH_RESPONSE" | jq -r "$2" | tr -d '\\r'; exit; fi
  shift
done
exit 99
"""

_HEAD = "0123456789abcdef0123456789abcdef01234567"
_OPEN = {
    "state": "OPEN",
    "isDraft": False,
    "isCrossRepository": False,
    "headRefOid": _HEAD,
    "reviewDecision": "APPROVED",
}


#: What `gh api graphql` printed for this query with pr=999999 on 2026-10-12: the raw body on
#: stdout, `--jq` not applied, the message on stderr, exit 1.
_NOT_FOUND_GH = """#!/bin/bash
echo '{"data":{"repository":{"pullRequest":null}},"errors":[{"type":"NOT_FOUND",\
"path":["repository","pullRequest"],"locations":[{"line":1,"column":82}],\
"message":"Could not resolve to a PullRequest with the number of 7."}]}'
echo 'gh: Could not resolve to a PullRequest with the number of 7.' >&2
exit 1
"""


def _run_rd(tmp_path, pull_request, gh=_JQ_GH, ref=_HEAD):
    """Run the action's body for PR 7 at `ref`; return (exit code, stdout, outputs)."""
    (tmp_path / "gh").write_text(gh, encoding="utf-8", newline="\n")
    (tmp_path / "gh").chmod(0o755)
    out_file = tmp_path / "github_output"
    out_file.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
        "GH_RESPONSE": json.dumps({"data": {"repository": {"pullRequest": pull_request}}}),
        "GH_TOKEN": "t",
        "GITHUB_OUTPUT": str(out_file),
        "GITHUB_REPOSITORY": "org/repo",
        "OWNER": "org",
        "PR_NUMBER": "7",
        "REF": ref,
    }
    proc = run_step(tmp_path, _rd()["run"], env)
    return proc.returncode, proc.stdout, out_file.read_text(encoding="utf-8")


_needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


@bash_only
@_needs_jq
def test_a_pull_request_github_cannot_find_refuses_unbound(tmp_path):
    """A hand-made dispatch naming no pull request is the threat shape. GitHub answers it with
    NOT_FOUND and gh exits 1; `unbound` keeps `outcome` from replying on a number that names no
    pull request.

    Mutations: drop the NOT_FOUND branch -> the failed-read refusal, no `unbound`; drop its
    `unbound=true` write; its `refuse` call echoes the `::error::` line and exits without
    writing `refusal=`."""
    assert _run_rd(tmp_path, None, gh=_NOT_FOUND_GH) == (1, f"::error::{_MISSING}\n", _UNBOUND)


_MISSING = "Pull request #7 does not exist in this repository; nothing applies."
#: The outputs of a refusal naming no pull request: `unbound`, then the refusal.
_UNBOUND = f"unbound=true\nrefusal={_MISSING}\n"


#: A failed read that is not NOT_FOUND: gh's text for a 502, exit 1.
_BAD_GATEWAY_GH = """#!/bin/bash
echo 'gh: HTTP 502: Bad Gateway (https://api.github.com/graphql)' >&2
exit 1
"""


@bash_only
@_needs_jq
def test_a_failed_read_refuses_without_unbound(tmp_path):
    """A transient failure names no missing pull request, so `outcome` still replies.

    Mutations: write `unbound=true` on every failed read; drop the failed-read branch -> the
    empty output reaches the null-pull-request refusal, which writes `unbound`; its `refuse`
    call echoes the `::error::` line and exits without writing `refusal=`."""
    said = (
        "Could not read pull request #7 (gh exited 1); nothing applies. Comment "
        "`shipmate apply` on the pull request again."
    )
    assert _run_rd(tmp_path, None, gh=_BAD_GATEWAY_GH) == (
        1,
        f"::error::{said}\n",
        f"refusal={said}\n",
    )


@bash_only
@_needs_jq
def test_a_bare_null_pull_request_refuses(tmp_path):
    """A null `pullRequest` with exit 0 is not what GitHub sends for an unknown number (that is
    NOT_FOUND, above); select() still refuses it rather than reading empty fields.

    Mutation: replace the `-z "$pr"` branch's `echo ...; exit 1` with
    `echo "decision=MISSING_PR" >> "$GITHUB_OUTPUT"; exit 0`; drop its `unbound=true` write
    (`outcome` would reply on a number naming no pull request)."""
    assert _run_rd(tmp_path, None) == (1, f"::error::{_MISSING}\n", _UNBOUND)


@bash_only
@_needs_jq
@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_a_pull_request_that_is_not_open_refuses(tmp_path, state):
    """A pull request merged between the comment and the dispatch is the deploy's to apply.

    Writes `unbound`, so `outcome` posts no reply for it.

    Mutations: delete the `"$state" != OPEN` check; drop its `unbound=true` write."""
    said = (
        f"Pull request #7 is {state}, not OPEN; nothing applies. A merged pull "
        "request's plans apply through the merge deploy."
    )
    assert _run_rd(tmp_path, {**_OPEN, "state": state}) == (
        1,
        f"::error::{said}\n",
        f"unbound=true\nrefusal={said}\n",
    )


@bash_only
@_needs_jq
def test_a_draft_pull_request_refuses(tmp_path):
    """decide() refuses a draft at comment time; a hand-made dispatch skips decide().

    Mutation: delete the `"$draft" != false` check."""
    said = "Pull request #7 is a draft; nothing applies. Mark it ready for review first."
    assert _run_rd(tmp_path, {**_OPEN, "isDraft": True}) == (
        1,
        f"::error::{said}\n",
        f"refusal={said}\n",
    )


@bash_only
@_needs_jq
def test_a_fork_pull_request_refuses(tmp_path):
    """Mutation: delete the `"$cross" != false` check."""
    said = "Pull request #7 comes from a fork; nothing applies."
    assert _run_rd(tmp_path, {**_OPEN, "isCrossRepository": True}) == (
        1,
        f"::error::{said}\n",
        f"refusal={said}\n",
    )


@bash_only
@_needs_jq
def test_a_head_differing_in_the_last_character_refuses(tmp_path):
    """The approved number paired with another head is the threat itself; the comparison is the
    whole SHA.

    Mutation: compare `"${head:0:39}" != "${REF:0:39}"` (a prefix on both sides, so the
    matching-head tests stay green)."""
    other = _HEAD[:-1] + "8"
    said = (
        f"Pull request #7 head is {other}, but this dispatch names {_HEAD}; "
        "nothing applies. Comment `shipmate apply` on the pull request."
    )
    assert _run_rd(tmp_path, {**_OPEN, "headRefOid": other}) == (
        1,
        f"::error::{said}\n",
        f"refusal={said}\n",
    )


@bash_only
@_needs_jq
def test_a_ref_carrying_newlines_writes_one_refusal_line(tmp_path):
    """The dispatcher supplies `ref`, and the head-mismatch refusal names it: a newline written
    through would forge a `decision` and an `unbound` line in the job that holds the App key.

    Mutation: write the raw message (`echo "refusal=$1"`)."""
    forged = f"{_HEAD}\ndecision=APPROVED\r\nunbound=true"
    said = (
        f"Pull request #7 head is {_HEAD}, but this dispatch names {_HEAD} decision=APPROVED  "
        "unbound=true; nothing applies. Comment `shipmate apply` on the pull request."
    )
    assert _run_rd(tmp_path, _OPEN, ref=forged) == (1, f"::error::{said}\n", f"refusal={said}\n")


@bash_only
@_needs_jq
@pytest.mark.parametrize(("review", "decision"), [("APPROVED", "APPROVED"), (None, "NONE")])
def test_the_open_pull_requests_own_head_writes_its_decision(tmp_path, review, decision):
    """A null `reviewDecision` (no review rule, or code-owner review owns no changed file) is
    NONE.

    Mutations: `// "NONE"` to `// "APPROVED"`; `decision=` to `decisions=`."""
    assert _run_rd(tmp_path, {**_OPEN, "reviewDecision": review}) == (
        0,
        "",
        f"decision={decision}\n",
    )
