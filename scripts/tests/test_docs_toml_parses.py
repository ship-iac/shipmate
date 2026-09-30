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

Every fence must be a complete file, not a fragment. `validate_structure` judges a whole
table — it requires `layout` — so a fragment would refuse for a reason that says nothing about
the example. The rule is not to loosen the assertion for fragments but to write no fragments:
a snippet worth publishing for this file is a snippet worth being able to merge. A fence that
genuinely cannot be complete does not belong in ```toml.

A fence holding `{ vars = "NAME" }` references is validated with every referenced variable set
to a placeholder its position accepts, so the example is judged on its shape rather than on this
runner's variables: `_ROLE_PLACEHOLDER` at an `aws.plan` or `aws.apply` position, because a role
name there needs an `aws.account` and a fence must not add one for the placeholder's sake, and
`_PLACEHOLDER` everywhere else.

Sibling of `test_docs_yaml_parses.py`; both discover fences through `_loader.doc_fences`.
"""

import re

import pytest
from _loader import ENGINE, assert_every_fence_discovered, doc_fences, load_script

DOCS = ENGINE / "docs"
ec = load_script("env-config")

# CONTRACT.md as well as the pages `test_docs_yaml_parses.py` walks: it holds the schema of
# record, and its example is the one a consumer is likeliest to copy.
_PAGES = [ENGINE / "CONTRACT.md", ENGINE / "README.md", *sorted(DOCS.glob("*.md"))]

_FENCE = re.compile(r"^(?P<indent>[ \t]*)```toml[ \t]*$\n(?P<body>.*?)^\1```", re.M | re.S)

# Openers as a reader sees them, not as _FENCE pairs them: an info string after the language
# counts here and does not pair in _FENCE.
_OPENER = re.compile(r"^[ \t]*```toml\b", re.M)

#: An environment name and a non-empty region at once.
_PLACEHOLDER = "dev"

#: A role a consumer's variable would hold: a full ARN, which needs no `aws.account`.
_ROLE_PLACEHOLDER = "arn:aws:iam::111111111111:role/dev"
_ROLE_POSITION = re.compile(r"\.aws\.(plan|apply)(\.|$)")


def _placeholders(body):
    """`{NAME: value}` for every reference in `body`, by the position it sits at."""
    return {
        name: _ROLE_PLACEHOLDER if _ROLE_POSITION.search(path) else _PLACEHOLDER
        for path, name in ec.references(ec.load_toml(body))
    }


# Discovery is by glob, so a page added later is covered without editing this file.
_FENCES = list(doc_fences(_PAGES, _FENCE))


def test_every_fence_was_discovered():
    """No fence is silently dropped: _FENCE pairs as many as the pages open.

    Mutation: put an info string after one opener (```toml title=x) -- `_OPENER` still counts
    it and `_FENCE` no longer pairs it. Relabelling an opener outright does NOT red here: that
    removes the opener too, and the counts stay equal. This guard catches a fence that is
    *announced* and not parsed, not one that stops being announced.
    """
    assert_every_fence_discovered(_PAGES, _FENCES, _OPENER, "toml")


@pytest.mark.parametrize(
    ("page", "line", "body"),
    _FENCES,
    ids=[f"{page.name}:{line}" for page, line, _ in _FENCES],
)
def test_toml_fence_is_a_configuration_a_consumer_could_merge(page, line, body):
    """Parse and validate, in the order the engine does.

    Mutations: give one fence a second `[environments.<name>]` header for a name it already
    declares, the Yes/No shape that shipped — `tomllib` refuses it as `Cannot declare ... twice`;
    give the role positions `_PLACEHOLDER` again — the variable-reference fence in CONTRACT.md,
    whose `aws.apply` is a reference beside no account, refuses as a role name with no account.
    """
    where = f"{page.relative_to(ENGINE).as_posix()}:{line}"
    try:
        table = ec.parse_table(body, _placeholders(body))
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
