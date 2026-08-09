"""Guards every ```yaml fence in README.md and docs/*.md: it must load via
yaml.safe_load, and it must load to a mapping -- the fences are workflow files,
and one that loads to a bare string or None is a mangled paste rather than a
workflow.

Threat model: the realistic failure is an accidental bad paste, or a later edit
that outdents a line and breaks a block's indentation. Reviewers reading prose
do not reliably see either. A fence that parses but is semantically wrong is
out of scope -- the docs are reviewed prose, and the sample repos remain the
executed copies of record.
"""

import re
import textwrap

import pytest
import yaml
from _loader import ENGINE

# ponytail: catches syntax rot from a bad paste, not semantic drift away from the
# sample repos -- proving a documented workflow still runs is the sample repos' CI.

DOCS = ENGINE / "docs"

_FENCE = re.compile(r"^(?P<indent>[ \t]*)```yaml[ \t]*$\n(?P<body>.*?)^\1```", re.M | re.S)


def _fences():
    """Every fence as (page, 1-based line of its opening ```yaml, dedented body).

    Discovery is by glob so a page added later is covered without editing this
    file, and indented fences (inside a list item) are dedented rather than
    skipped -- a fence this misses is a fence nothing checks.
    """
    for page in [ENGINE / "README.md", *sorted(DOCS.glob("*.md"))]:
        text = page.read_text(encoding="utf-8")
        for m in _FENCE.finditer(text):
            yield page, text[: m.start()].count("\n") + 1, textwrap.dedent(m.group("body"))


_FENCES = list(_fences())


def test_fences_were_discovered():
    # Without this, a discovery bug parametrizes zero cases and the suite is
    # green while checking nothing. Four is the floor the docs already carry.
    assert len(_FENCES) >= 4, (
        f"found only {len(_FENCES)} ```yaml fences in README.md + docs/*.md -- "
        "the documented consumer workflows account for at least four, so "
        "discovery is broken"
    )


@pytest.mark.parametrize(
    ("page", "line", "body"),
    _FENCES,
    ids=[f"{page.name}:{line}" for page, line, _ in _FENCES],
)
def test_yaml_fence_loads_to_a_mapping(page, line, body):
    where = f"{page.relative_to(ENGINE).as_posix()}:{line}"
    try:
        doc = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        pytest.fail(f"{where} ```yaml fence does not parse: {exc}")
    assert isinstance(doc, dict), (
        f"{where} ```yaml fence loaded to {type(doc).__name__}, not a mapping -- "
        "the fences are workflow files, so this one is a mangled paste"
    )
