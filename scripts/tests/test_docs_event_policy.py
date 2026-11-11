"""The event policy read-back `docs/getting-started.md` publishes is the policy shipmate
needs.

Threat model: accidental regression -- an edited read-back, or a new `on:` event in the
`shipmate` fence the policy does not allow, which GitHub would then block in a public
repository.
"""

import json
import re

from _loader import ENGINE, load_script
from _shipmate import load_workflow_text

PAGE = (ENGINE / "docs" / "getting-started.md").read_text(encoding="utf-8")
HEADING = "#### Allow shipmate's events on a public repository\n"
#: The samples' policy as `gh api .../actions/policies/<id> --jq '{enforcement, conditions,
#: rules}'` printed it, hand-written.
READ_BACK = {
    "conditions": {"workflow_path": {"exclude": [], "include": [".github/workflows/shipmate.yml"]}},
    "enforcement": "active",
    "rules": [
        {
            "parameters": {
                "allowed_events": [
                    "pull_request_target",
                    "issue_comment",
                    "push",
                    "workflow_dispatch",
                ]
            },
            "type": "restrict_action_events",
        }
    ],
}


def _read_back():
    section = PAGE.split(HEADING, 1)[1].split("\n## ", 1)[0]
    fences = re.findall(r"^```json\n(.*?)^```$", section, re.M | re.S)
    assert len(fences) == 1
    return json.loads(fences[0])


def test_the_read_back_is_the_policy_shipmate_needs():
    """Mutations, each proven: `enforcement` set to `disabled`;
    `.github/workflows/shipmate.yml` added to `exclude`; `issue_comment` dropped from
    `allowed_events`; `pull_request` added to it."""
    assert _read_back() == READ_BACK


def test_the_read_back_allows_exactly_the_fences_on_events():
    """Mutations, each proven: `issue_comment` dropped from the read-back's
    `allowed_events`; `pull_request` added to it."""
    fence = load_workflow_text(load_script("onboard")._fence(PAGE, "shipmate"))
    allowed = _read_back()["rules"][0]["parameters"]["allowed_events"]
    assert sorted(allowed) == sorted(fence["on"])
