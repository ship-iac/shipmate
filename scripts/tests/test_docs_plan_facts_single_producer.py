"""The documented plan wrapper reads its pull-request facts from one producer.

`plan.yml` answers to two triggers and only one of them carries a pull request:
a `workflow_dispatch` run's payload has no `github.event.pull_request` at all, so
every expression reading it renders empty. An empty checkout `ref` plans the
dispatch ref, an empty `head-repo` refuses the summary job, and both fail in the
direction that greens a gate over a pull request nobody planned. The `facts` job
is the single producer, and a fact read from the payload beside it is a second
producer of the same fact.

The whole set of `github.event.pull_request` occurrences inside the fence is
compared against a hand-written constant, for the reason `CLAUDE.md` gives: an
"is the checkout wired to `facts`" check leaves the hole wherever it does not
look, and the surviving occurrence is a real one -- the concurrency group, which
must read payload data because `needs` is not in scope at that level.

That set is read as fence *text* rather than parsed values: a payload expression
inside a comment teaches the reader to write it back, and the concurrency key is
a scalar whose sibling `github.event.inputs.pr_number` must not be confused with
it. The checkout guard below is parsed, as the sibling docs guards are.
"""

import re
import textwrap

import yaml
from _loader import ENGINE

_PAGE = ENGINE / "docs" / "getting-started.md"

_FENCE = re.compile(r"^(?P<indent>[ \t]*)```yaml[ \t]*$\n(?P<body>.*?)^\1```", re.M | re.S)

# Owner-agnostic, like the sibling docs guards: the pages publish
# `<owner>/shipmate/...` as well as the engine's own org.
_SUMMARY_CALL = "/shipmate/.github/workflows/summary.yml@"

#: Greedy on the trailing dotted path, so `.head.sha` cannot truncate to the one
#: allowed value.
_PAYLOAD_READ = re.compile(r"github\.event\.pull_request[\w.]*")

#: `needs` is not in scope in `concurrency:`, so the group has to read payload
#: data -- and `github.event.inputs.pr_number` beside it is a different
#: expression, not another read of this one.
_ALLOWED = ["github.event.pull_request.number"]

_CHECKOUT = "actions/checkout@"
_HEAD_SHA = "${{ needs.facts.outputs.head-sha }}"


def _plan_fence():
    """The documented `plan.yml` fence, as text.

    Exactly one, asserted: a reworded fence that stops matching would leave this
    guard green over nothing.
    """
    found = [
        m.group("body")
        for m in _FENCE.finditer(_PAGE.read_text(encoding="utf-8"))
        if _SUMMARY_CALL in m.group("body")
    ]
    assert len(found) == 1, f"documented plan-wrapper fences: {len(found)}"
    return found[0]


def test_the_documented_wrapper_reads_the_payload_nowhere_but_the_concurrency_group():
    found = _PAYLOAD_READ.findall(_plan_fence())
    assert found == _ALLOWED, (
        "docs/getting-started.md's plan.yml must read every pull-request fact from the "
        f"`facts` job; these read the event payload instead: {found}"
    )


def test_the_documented_checkouts_name_the_head_from_the_facts_job():
    """The other half: the read that replaced them exists. Without this, deleting
    a checkout `ref:` outright satisfies the guard above -- and a checkout with no
    `ref:` is exactly what both triggers do by default, which is the base branch
    or the dispatch ref."""
    doc = yaml.safe_load(textwrap.dedent(_plan_fence()))
    found = [
        (name, (step.get("with") or {}).get("ref"))
        for name, job in doc["jobs"].items()
        for step in (job.get("steps") or [])
        if _CHECKOUT in (step.get("uses") or "")
    ]
    assert found == [("detect", _HEAD_SHA), ("plan", _HEAD_SHA)], (
        f"docs/getting-started.md's documented checkouts must name {_HEAD_SHA}; got {found}"
    )
