"""The non-`ship-iac` actions the engine uses must stay on the allowed-actions list.

Every repository that runs shipmate is set to `allowed_actions: selected` with a named pattern
list (`docs/hardening.md`, "Actions settings"). That list names the engine plus the third-party
actions in `THIRD_PARTY`. Adding a new third-party action here is therefore not a local change:
until every consumer's list names it too, their next run dies in `Set up job`, and the engine's
own suite stays green while it happens.

The set covers the files a consumer run reaches: every composite action, and every workflow
that declares `workflow_call`. The engine's own `push`-triggered workflows (`ci.yml`,
`manifest-load.yml`) run only in the engine repository, under its settings, so what they use
is never resolved against a consumer's list and does not belong on it.

A `$/` reference resolves in the engine repository and passes with the `<owner>/shipmate/*@*`
entry consumers already list (measured), so it needs no additional allowlist pattern. A `./`
reference resolves in the running workspace, never a third party's. Both are excluded here.

Version bumps are free, the patterns ending `@*`, so the set is compared by action path without
refs. Hand-written, never derived from the files it checks: a derived vector passes whatever the
tree says.
"""

import re

from _loader import ACTIONS, ENGINE, WORKFLOWS, action_yaml, load_script, workflow_yaml

onboard = load_script("onboard")

#: What `docs/hardening.md` and every consumer repository's allowed-actions list name. Changing
#: this constant alone does not make a new action work; the failure message says what does.
THIRD_PARTY = {
    "actions/cache/restore",
    "actions/cache/save",
    "actions/checkout",
    "actions/create-github-app-token",
    "actions/download-artifact",
    "actions/upload-artifact",
    "aws-actions/configure-aws-credentials",
    "opentofu/setup-opentofu",
}

#: The engine workflows a consumer calls, hand-written.
CALLABLE_WORKFLOWS = [
    "apply-env-level.yml",
    "apply.yml",
    "comment-ops.yml",
    "deploy.yml",
    "drift.yml",
    "plan.yml",
    "unlock.yml",
]

_ALLOWED_FENCE = re.compile(
    r"^- \*\*Allowed actions\.\*\*.*?^  ```\n(?P<body>.*?)^  ```$", re.M | re.S
)


def _uses(node):
    """Every `uses:` value anywhere in a parsed YAML document.

    Parsed, not grepped: `uses:` values carry trailing version comments that a line regex drops,
    and the literal text `uses: write` appears inside `permission-statuses: write`.
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
    """Mutations: select no workflow, or every workflow, or re-include `ci.yml` -> red."""
    workflows = [
        p for p in sorted(WORKFLOWS.glob("*.yml")) if "workflow_call" in workflow_yaml(p)["on"]
    ]
    assert [p.name for p in workflows] == CALLABLE_WORKFLOWS, (
        "the consumer-reachable workflow selection changed -- the collection is broken, "
        "or a workflow gained or lost `workflow_call`"
    )
    docs = [action_yaml(p) for p in sorted(ACTIONS.glob("*/action.yml"))]
    docs += [workflow_yaml(p) for p in workflows]
    assert len(docs) > 20, f"expected the whole engine tree, found {len(docs)} files"

    refs = {u.split("@")[0] for doc in docs for u in _uses(doc)}
    assert {r for r in refs if not r.startswith(("ship-iac/", "./", "$/"))} == THIRD_PARTY, (
        "the engine's third-party action set changed. Updating this constant is "
        "not the fix: add the action's `owner/repo@*` pattern to the allowed-actions "
        "list in docs/hardening.md AND in every consumer repository's Actions "
        "settings first, or their next run fails in `Set up job`."
    )


def test_onboard_allowed_patterns_cover_the_third_party_set():
    """GitHub-owned `actions/` entries are covered by `github_owned_allowed`, every other one
    needs a pattern. Mutation: add `"astral-sh/setup-uv@*"` to `ALLOWED_PATTERNS` -> red."""
    expected = {"ship-iac/shipmate/*@*"} | {
        f"{a}@*" for a in THIRD_PARTY if not a.startswith("actions/")
    }
    assert set(onboard.ALLOWED_PATTERNS) == expected


def test_hardening_fence_lists_onboard_allowed_patterns():
    """Mutations: leave `astral-sh/setup-uv@*` in the fence, or swap two fence lines -> red."""
    match = _ALLOWED_FENCE.search((ENGINE / "docs" / "hardening.md").read_text(encoding="utf-8"))
    assert match, "no code fence under the hardening.md 'Allowed actions' bullet"
    lines = [line.strip().replace("<owner>", "ship-iac") for line in match["body"].splitlines()]
    assert lines == list(onboard.ALLOWED_PATTERNS)
