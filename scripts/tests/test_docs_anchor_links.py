"""Every `getting-started.md#<anchor>` link in the docs and in `scripts/onboard` names a
heading of `docs/getting-started.md`.

Threat model: accidental regression -- a heading renamed or reworded while a link to it
stays. A link to a missing anchor still opens the page, at its top, so nothing else fails.
"""

import re

from _loader import ENGINE

PAGE = ENGINE / "docs" / "getting-started.md"
SOURCES = [
    *sorted((ENGINE / "docs").glob("*.md")),
    ENGINE / "README.md",
    ENGINE / "CONTRACT.md",
    ENGINE / "scripts" / "onboard",
]
LINK = re.compile(r"getting-started\.md#([\w-]+)")
EVENT_POLICY = "allow-shipmates-events-on-a-public-repository"


def _anchors(text):
    """GitHub's heading anchors: lowercased, punctuation other than `-` and `_` dropped,
    each space a hyphen, `-1`, `-2` appended to repeats. Lines inside fenced code blocks
    are not headings."""
    anchors, seen, fenced = set(), {}, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        m = re.match(r"#{1,6}\s+(.*?)\s*$", line)
        if fenced or not m:
            continue
        slug = re.sub(r"[^\w\- ]", "", m.group(1).lower()).replace(" ", "-")
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        anchors.add(f"{slug}-{n}" if n else slug)
    return anchors


def _links():
    return {
        (path.relative_to(ENGINE).as_posix(), anchor)
        for path in SOURCES
        for anchor in LINK.findall(path.read_text(encoding="utf-8"))
    }


def test_every_getting_started_anchor_link_names_a_heading():
    """Mutation: rename `#### Allow shipmate's events on a public repository` in
    `getting-started.md`, which `troubleshooting.md` and `scripts/onboard` link."""
    anchors = _anchors(PAGE.read_text(encoding="utf-8"))
    assert {link for link in _links() if link[1] not in anchors} == set()


def test_the_event_policy_links_are_collected():
    """The guard above passes over no link at all; these are the links it exists for.

    Mutation: drop `scripts/onboard` from `SOURCES`.
    """
    assert {
        ("docs/troubleshooting.md", EVENT_POLICY),
        ("scripts/onboard", EVENT_POLICY),
    } <= _links()


def test_anchors_follow_githubs_slug_rule_outside_fences():
    """Mutation: collect headings inside fences, so the `# .github/shipmate-config.yml`
    comment of a YAML fence answers for a heading."""
    text = "\n".join(
        [
            "## Required — plan",
            "#### Allow shipmate's events on a `public` repository",
            "### Setup",
            "### Setup",
            "```yaml",
            "# .github/shipmate-config.yml",
            "```",
        ]
    )
    assert _anchors(text) == {
        "required--plan",
        EVENT_POLICY,
        "setup",
        "setup-1",
    }
