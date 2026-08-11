"""Guards every ```yaml fence in README.md and docs/*.md: it must load via
yaml.safe_load, it must load to a mapping -- the fences are workflow files,
and one that loads to a bare string or None is a mangled paste rather than a
workflow -- and discovery must lose none of them, so a fence the pairing
logic drops fails the guard rather than going unchecked.

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
from _loader import ENGINE, ENGINE_CALL_SECRETS

# ponytail: catches syntax rot from a bad paste, not semantic drift away from the
# sample repos -- proving a documented workflow still runs is the sample repos' CI.

DOCS = ENGINE / "docs"

_PAGES = [ENGINE / "README.md", *sorted(DOCS.glob("*.md"))]

_FENCE = re.compile(r"^(?P<indent>[ \t]*)```yaml[ \t]*$\n(?P<body>.*?)^\1```", re.M | re.S)

# Openers as a reader sees them, not as _FENCE pairs them: `yml`, and an info
# string after the language, both count here and neither pairs above.
_OPENER = re.compile(r"^[ \t]*```ya?ml\b", re.M)


def _fences():
    """Every fence as (page, 1-based line of its opening ```yaml, dedented body).

    Discovery is by glob so a page added later is covered without editing this
    file, and indented fences (inside a list item) are dedented rather than
    skipped -- a fence this misses is a fence nothing checks.
    """
    for page in _PAGES:
        text = page.read_text(encoding="utf-8")
        for m in _FENCE.finditer(text):
            yield page, text[: m.start()].count("\n") + 1, textwrap.dedent(m.group("body"))


_FENCES = list(_fences())


def test_every_fence_was_discovered():
    """No fence is silently dropped: _FENCE pairs as many as the pages open.

    _FENCE skips whatever it cannot pair, so a relabelled opener, an info
    string, or a mangled closing delimiter would drop a fence out of the
    parametrization and leave the suite green over an unchecked workflow.
    """
    openers = sum(len(_OPENER.findall(p.read_text(encoding="utf-8"))) for p in _PAGES)
    # A discovery bug that finds nothing parametrizes zero cases and checks
    # nothing, which is green either way without this.
    assert openers > 0, "found no ```yaml fence openers in README.md + docs/*.md"
    assert len(_FENCES) == openers, (
        f"{openers} ```yaml fence openers in README.md + docs/*.md but only "
        f"{len(_FENCES)} paired into checkable fences -- the unpaired ones are "
        "not parsed by anything (relabelled opener, info string, or a broken "
        "closing delimiter)"
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


# Owner-agnostic: the pages publish `<owner>/shipmate/...` as well as the
# engine's own org, and a selector that recognizes only one spelling silently
# stops checking the other.
_PATH = "/shipmate/.github/workflows/"


def _engine_workflow_calls(doc):
    """(job name, callee file name, job) per fence job that `uses:` an engine
    reusable workflow."""
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    for name, job in (jobs or {}).items():
        uses = job.get("uses") or "" if isinstance(job, dict) else ""
        if _PATH in uses:
            yield name, uses.split(_PATH, 1)[1].split("@", 1)[0], job


@pytest.mark.parametrize(
    ("page", "line", "body"),
    _FENCES,
    ids=[f"{page.name}:{line}" for page, line, _ in _FENCES],
)
def test_documented_wrapper_passes_engine_secrets_by_name(page, line, body):
    """A documented wrapper names the secrets it forwards; `secrets: inherit`
    is never one of the shapes we publish.

    Measured in a cross-organization consumer: `inherit` delivers nothing there
    AND suppresses the key the callee's `environment: shipmate-engine` would
    have supplied, so a consumer outside `ship-iac` following these snippets
    gets no App-authored surface at all -- no gate, no apply checks, no comment.
    Same-organization it also hands the engine every secret the repository holds.

    Whole-block comparison against `_loader.ENGINE_CALL_SECRETS`: a key-set test
    would pass an entry whose value expression was mistyped, and reading the
    expected set out of the callee would pass whatever that file says.
    """
    for job_name, target, job in _engine_workflow_calls(yaml.safe_load(body)):
        where = f"{page.relative_to(ENGINE).as_posix()}:{line} job `{job_name}`"
        assert target in ENGINE_CALL_SECRETS, (
            f"{where} calls `{target}`, which _loader.ENGINE_CALL_SECRETS does not "
            "know -- add its whole expected secrets block there"
        )
        assert job.get("secrets") == ENGINE_CALL_SECRETS[target], (
            f"{where} must pass exactly {ENGINE_CALL_SECRETS[target]} to `{target}`; "
            f"got {job.get('secrets')!r}"
        )


def test_the_wrapper_snippets_are_still_being_found():
    """A floor under the guard above, which asserts nothing when its selector
    matches nothing. Zero matches is indistinguishable from all-clear per fence,
    so the count of documented engine calls is pinned here instead: rewording a
    `uses:` out of the selector's reach fails rather than going quiet."""
    found = sorted(
        (page.relative_to(ENGINE).as_posix(), target)
        for page, _, body in _FENCES
        for _, target, _ in _engine_workflow_calls(yaml.safe_load(body))
    )
    assert found == [
        ("docs/getting-started.md", "apply-all.yml"),
        ("docs/getting-started.md", "apply.yml"),
        ("docs/getting-started.md", "deploy.yml"),
        ("docs/getting-started.md", "summary.yml"),
    ], f"documented engine reusable-workflow calls changed: {found}"
