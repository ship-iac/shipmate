"""Guards every ```toml fence in CONTRACT.md, README.md and docs/*.md. Each must parse with
`tomllib` and pass `env-config.validate_structure`, so a documented example is one a consumer
can copy into `.github/shipmate.toml` and merge. Discovery must lose none of them, so a fence
the pairing logic drops fails the guard rather than going unchecked.

Threat model: the realistic failure is an example that is wrong in the way the format newly
makes easy — two `[table]` headers for one environment, a top-level setting written below a
header, a duplicate key — none of which a reviewer reading prose reliably sees, and all of
which refuse at `detect` for the consumer who copies them. One such example shipped in
`docs/hardening.md` through a full docs sweep and a green suite: a Yes/No pair in one fence
declaring `[environments.prod]` twice.

**Every fence must be a complete file, not a fragment.** `validate_structure` judges a whole
table — it requires `layout` — so a fragment would refuse for a reason that says nothing about
the example. The rule is not to loosen the assertion for fragments but to write no fragments:
a snippet worth publishing for this file is a snippet worth being able to merge. A fence that
genuinely cannot be complete does not belong in ```toml.

Sibling of `test_docs_yaml_parses.py`, whose fence-discovery shape this copies.
"""

import re
import textwrap

import pytest
from _loader import ENGINE, load_script

DOCS = ENGINE / "docs"
ec = load_script("env-config")

# CONTRACT.md as well as the pages `test_docs_yaml_parses.py` walks: it holds the schema of
# record, and its example is the one a consumer is likeliest to copy.
_PAGES = [ENGINE / "CONTRACT.md", ENGINE / "README.md", *sorted(DOCS.glob("*.md"))]

_FENCE = re.compile(r"^(?P<indent>[ \t]*)```toml[ \t]*$\n(?P<body>.*?)^\1```", re.M | re.S)

# Openers as a reader sees them, not as _FENCE pairs them: an info string after the language
# counts here and does not pair in _FENCE.
_OPENER = re.compile(r"^[ \t]*```toml\b", re.M)


def _fences():
    """Every fence as (page, 1-based line of its opening ```toml, dedented body).

    Discovery is by glob, so a page added later is covered without editing this file, and
    indented fences, those inside a list item, are dedented rather than skipped: a fence this
    misses is a fence nothing checks.
    """
    for page in _PAGES:
        text = page.read_text(encoding="utf-8")
        for m in _FENCE.finditer(text):
            yield page, text[: m.start()].count("\n") + 1, textwrap.dedent(m.group("body"))


_FENCES = list(_fences())


def test_every_fence_was_discovered():
    """No fence is silently dropped: _FENCE pairs as many as the pages open.

    _FENCE skips whatever it cannot pair, so a relabelled opener, an info string, or a mangled
    closing delimiter would drop a fence out of the parametrization and leave the suite green
    over an unchecked example.

    Mutation: relabel one ```toml opener to ```ini.
    """
    openers = sum(len(_OPENER.findall(p.read_text(encoding="utf-8"))) for p in _PAGES)
    # A discovery bug that finds nothing parametrizes zero cases and checks nothing, which is
    # green either way without this.
    assert openers > 0, "found no ```toml fence openers in CONTRACT.md + README.md + docs/*.md"
    assert len(_FENCES) == openers, (
        f"{openers} ```toml fence openers in CONTRACT.md + README.md + docs/*.md but only "
        f"{len(_FENCES)} paired into checkable fences -- the unpaired ones are "
        "not parsed by anything (relabelled opener, info string, or a broken "
        "closing delimiter)"
    )


@pytest.mark.parametrize(
    ("page", "line", "body"),
    _FENCES,
    ids=[f"{page.name}:{line}" for page, line, _ in _FENCES],
)
def test_toml_fence_is_a_configuration_a_consumer_could_merge(page, line, body):
    """Parse and validate, in the order the engine does.

    Mutation: give one fence a second `[environments.<name>]` header for a name it already
    declares, the Yes/No shape that shipped — `tomllib` refuses it as `Cannot declare ... twice`.
    """
    where = f"{page.relative_to(ENGINE).as_posix()}:{line}"
    try:
        table = ec.parse_table(body)
    except SystemExit as exc:
        pytest.fail(f"{where} ```toml fence does not parse: {str(exc).removeprefix('::error::')}")
    try:
        ec.validate_structure(table)
    except SystemExit as exc:
        pytest.fail(
            f"{where} ```toml fence parses but the engine refuses it: "
            f"{str(exc).removeprefix('::error::')} Publish complete tables only — a fragment "
            "is not something a consumer can merge."
        )
