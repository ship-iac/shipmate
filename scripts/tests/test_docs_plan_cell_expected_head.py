"""Guards the documented `plan-cell` step's whole `with:` mapping.

plan-cell refuses an empty `expected-head` and points the reader at
`docs/getting-started.md`, so a snippet that does not carry the input sends a
consumer to a page without the fix. The realistic failure is an input added to
the action and forgotten in the snippet consumers copy.

Whole-mapping comparison against a hand-written literal, for the reason `CLAUDE.md` gives: a
"contains expected-head" check relocates the hole to whichever key it does not name. The fences
are parsed as YAML rather than regexed, because an indentation change would silently stop a text
match and leave this green over nothing. That is also why the call count is pinned.
"""

import re
import textwrap

import yaml
from _loader import ENGINE

_PAGE = ENGINE / "docs" / "getting-started.md"

_FENCE = re.compile(r"^(?P<indent>[ \t]*)```yaml[ \t]*$\n(?P<body>.*?)^\1```", re.M | re.S)

# Owner-agnostic, like test_docs_yaml_parses.py's selector: the pages publish
# `<owner>/shipmate/...` as well as the engine's own organization.
_ACTION = "/shipmate/actions/plan-cell@"

_EXPECTED_WITH = {
    "stack": "${{ matrix.stack }}",
    "stack-name": "${{ matrix.stack }}",
    "env": "${{ matrix.environment }}",
    "expected-head": "${{ needs.facts.outputs.head-sha }}",
    "plan-passphrase": "${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}",
}


def _plan_cell_steps(page):
    """(job name, step) per `plan-cell` step in the page's ```yaml fences."""
    text = page.read_text(encoding="utf-8")
    for m in _FENCE.finditer(text):
        doc = yaml.safe_load(textwrap.dedent(m.group("body")))
        jobs = doc.get("jobs") if isinstance(doc, dict) else None
        for name, job in (jobs or {}).items():
            for step in (job.get("steps") or []) if isinstance(job, dict) else []:
                if _ACTION in (step.get("uses") or ""):
                    yield name, step


def test_documented_plan_cell_calls_are_found():
    """A floor under test_documented_plan_cell_passes_expected_head, which asserts nothing when
    its selector matches nothing: rewording the step out of the selector's reach fails here
    rather than going quiet."""
    found = [job for job, _ in _plan_cell_steps(_PAGE)]
    assert found == ["plan"], f"documented plan-cell call sites changed: {found}"


def test_documented_plan_cell_passes_expected_head():
    for job, step in _plan_cell_steps(_PAGE):
        assert step.get("with") == _EXPECTED_WITH, (
            f"docs/getting-started.md job `{job}` must pass exactly "
            f"{_EXPECTED_WITH} to plan-cell; got {step.get('with')!r}"
        )
