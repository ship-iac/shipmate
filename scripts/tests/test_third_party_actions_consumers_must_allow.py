"""The non-`ship-iac` actions the engine uses must stay on the allowed-actions list.

Every repository that runs shipmate is set to `allowed_actions: selected` with a
named pattern list (`docs/hardening.md`, "Actions settings"). That list names the
engine plus the third-party actions below. Adding a *new* third-party action here
is therefore not a local change: until every consumer's list names it too, their
next run dies in `Set up job`, and the engine's own suite stays green while it
happens.

Version bumps are free -- the patterns end `@*` -- so the set is compared by
action path, without refs. Hand-written, never derived from the files it checks:
a derived vector passes whatever the tree says.
"""

import yaml
from _loader import ACTIONS, WORKFLOWS

#: What `docs/hardening.md` and every consumer repository's allowed-actions list
#: name. Changing this constant alone does not make a new action work -- see the
#: failure message.
THIRD_PARTY = {
    "actions/cache",
    "actions/cache/restore",
    "actions/cache/save",
    "actions/checkout",
    "actions/create-github-app-token",
    "actions/download-artifact",
    "actions/upload-artifact",
    "astral-sh/setup-uv",
    "aws-actions/configure-aws-credentials",
    "opentofu/setup-opentofu",
    "terramate-io/terramate-action",
}


def _uses(node):
    """Every `uses:` value anywhere in a parsed YAML document.

    Parsed, not grepped: `uses:` values carry trailing version comments that a
    line regex drops, and the literal text `uses: write` appears inside
    `permission-statuses: write`.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                yield value
            else:
                yield from _uses(value)
    elif isinstance(node, list):
        for item in node:
            yield from _uses(item)


def test_engine_uses_no_third_party_action_consumers_do_not_allow():
    files = sorted(ACTIONS.glob("*/action.yml")) + sorted(WORKFLOWS.glob("*.yml"))
    assert len(files) > 20, f"expected the whole engine tree, found {files}"

    refs = {
        u.split("@")[0] for f in files for u in _uses(yaml.safe_load(f.read_text(encoding="utf-8")))
    }
    assert any(r.startswith("ship-iac/") for r in refs), (
        f"no engine action found among {sorted(refs)} -- the collection is broken, "
        "and an empty set would compare green against an empty expectation"
    )

    assert {r for r in refs if not r.startswith("ship-iac/")} == THIRD_PARTY, (
        "the engine's third-party action set changed. Updating this constant is "
        "not the fix: add the action's `owner/repo@*` pattern to the allowed-actions "
        "list in docs/hardening.md AND in every consumer repository's Actions "
        "settings first, or their next run fails in `Set up job`."
    )
