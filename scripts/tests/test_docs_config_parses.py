"""Guards every config fence in CONTRACT.md, README.md and docs/*.md: a ```yaml fence whose first
line is `# .github/shipmate-config.yml`. Each must parse with `env-config.load_config` and pass
`validate_structure`, so a documented example is one a consumer can copy into the file and merge.

Threat model: the realistic failure is an example that is wrong in a way a reviewer reading
prose does not reliably see -- a duplicate key, a mis-indented entry, a flag written `True` --
and that refuses at `detect` for the consumer who copies it. One such example shipped in
`docs/hardening.md` through a full docs sweep and a green suite: a Yes/No pair in one fence
declaring the same environment twice.

The marker line is the selector, so losing it would drop a fence silently. Two guards stand
behind it: a hand-written per-page count of config fences, and a check that every unmarked
```yaml fence on these pages parses, to anything but a mapping with a top-level config key.

Every fence must be a complete file, not a fragment. `validate_structure` judges a whole
table -- it requires `layout` -- so a fragment would refuse for a reason that says nothing about
the example. A snippet worth publishing for this file is a snippet worth being able to merge.

A fence holding `{vars: NAME}` references is validated with every referenced variable set to a
placeholder its position accepts, so the example is judged on its shape rather than on this
runner's variables: `_ROLE_PLACEHOLDER` at an `aws.plan` or `aws.apply` position, because a role
name there needs an `aws.account` and a fence must not add one for the placeholder's sake, and
`_PLACEHOLDER` everywhere else.

Sibling of `test_docs_yaml_parses.py`, which skips config fences; both discover fences through
`_loader.doc_fences` with `onboard._FENCE`.
"""

import re
from collections import Counter

import pytest
import yaml
from _loader import CONFIG_FENCE_MARKER, ENGINE, doc_fences, is_config_fence, load_script
from _shipmate import load_workflow_text, yaml_error_text

DOCS = ENGINE / "docs"
ec = load_script("env-config")

# CONTRACT.md as well as the pages `test_docs_yaml_parses.py` walks: it holds the schema of
# record, and its example is the one a consumer is likeliest to copy.
_PAGES = [ENGINE / "CONTRACT.md", ENGINE / "README.md", *sorted(DOCS.glob("*.md"))]

_FENCE = load_script("onboard")._FENCE

#: An environment name and a non-empty region at once.
_PLACEHOLDER = "dev"

#: A role a consumer's variable would hold: a full ARN, which needs no `aws.account`.
_ROLE_PLACEHOLDER = "arn:aws:iam::111111111111:role/dev"
_ROLE_POSITION = re.compile(r"\.aws\.(plan|apply)(\.|$)")


def _placeholders(body):
    """`{NAME: value}` for every reference in `body`, by the position it sits at."""
    return {
        name: _ROLE_PLACEHOLDER if _ROLE_POSITION.search(path) else _PLACEHOLDER
        for path, name in ec.references(ec.load_config(body))
    }


def _where(page, line):
    return f"{page.relative_to(ENGINE).as_posix()}:{line}"


_ALL = list(doc_fences(_PAGES, _FENCE))
_FENCES = [fence for fence in _ALL if is_config_fence(fence[2])]


def test_each_page_holds_its_config_fences():
    """A fence whose marker line is lost drops out of every check here, so the count per page
    is written by hand. Mutation: delete one fence's marker line -- its page's count drops."""
    found = Counter(page.relative_to(ENGINE).as_posix() for page, _, _ in _FENCES)
    assert found == {
        "CONTRACT.md": 3,
        "docs/aws.md": 2,
        "docs/getting-started.md": 2,
        "docs/hardening.md": 2,
    }


def test_no_unmarked_fence_is_a_config_file():
    """A config example without its marker would be published unchecked, and one that also
    does not parse shows no config key to recognise it by, so every unmarked fence must parse.
    `test_docs_yaml_parses.py` does not read CONTRACT.md, so for its fences this is the only
    parse check.

    Mutations, each with the count above edited to match: delete one fence's marker line --
    listed as unmarked; delete a CONTRACT.md fence's marker line and give it a second `prod:`
    under `environments` -- listed as not parsing."""
    unmarked, broken = [], []
    for page, line, body in _ALL:
        if is_config_fence(body):
            continue
        try:
            doc = load_workflow_text(body)
        except yaml.YAMLError as exc:
            broken.append(f"{_where(page, line)}: {yaml_error_text(exc)}")
            continue
        if isinstance(doc, dict) and {"layout", "identities", "environments"} & set(doc):
            unmarked.append(_where(page, line))
    assert (unmarked, broken) == ([], []), (
        f"config fences without the `{CONFIG_FENCE_MARKER}` line: {unmarked}; "
        f"```yaml fences that do not parse: {broken}"
    )


@pytest.mark.parametrize(
    ("page", "line", "body"),
    _FENCES,
    ids=[f"{page.name}:{line}" for page, line, _ in _FENCES],
)
def test_config_fence_is_a_configuration_a_consumer_could_merge(page, line, body):
    """Parse and validate, in the order the engine does.

    Mutations: give one fence a second `prod:` under `environments`, the Yes/No shape that
    shipped -- the loader refuses the duplicate key; give the role positions `_PLACEHOLDER`
    again -- the variable-reference fence in CONTRACT.md, whose `aws.apply` is a reference
    beside no account, refuses as a role name with no account.
    """
    try:
        table = ec.parse_table(body, _placeholders(body))
    except SystemExit as exc:
        pytest.fail(
            f"{_where(page, line)} config fence does not parse: "
            f"{str(exc).removeprefix('::error::')}"
        )
    try:
        ec.validate_structure(table)
    except SystemExit as exc:
        pytest.fail(
            f"{_where(page, line)} config fence parses but the engine refuses it: "
            f"{str(exc).removeprefix('::error::')} Publish complete files only -- a fragment "
            "is not something a consumer can merge."
        )
