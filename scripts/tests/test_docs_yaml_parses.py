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
from _loader import ENGINE, ENGINE_CALL_SECRETS, WORKFLOWS

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


def _workflow_call_inputs(target):
    """{name: spec} for the engine callee's declared `workflow_call` inputs.

    Read from the callee itself: this guard's job is to compare two files that
    must agree, so one side has to come from the file it is checking. The
    hand-written side is the documented wrapper.
    """
    doc = yaml.safe_load((WORKFLOWS / target).read_text(encoding="utf-8"))
    on = doc.get("on", doc.get(True))
    return (on["workflow_call"].get("inputs") or {}) if isinstance(on, dict) else {}


@pytest.mark.parametrize(
    ("page", "line", "body"),
    _FENCES,
    ids=[f"{page.name}:{line}" for page, line, _ in _FENCES],
)
def test_documented_wrapper_passes_exactly_the_declared_engine_inputs(page, line, body):
    """Every `with:` key a documented wrapper passes to an engine reusable
    workflow is declared there, and every `required: true` input is passed.

    Not ordinary doc staleness. An undeclared input on a *reusable workflow* is
    a load-time rejection: the run dies at startup with no job, no check-run and
    no retrievable log, only a workflow-validation error on the run itself. (The
    same line on a composite action is merely ignored with a warning, which is
    why nothing here ever needed to notice.) So a consumer pasting the
    documented wrapper after an input is retired gets a dead pipeline, and until
    this guard existed no test in this repository would have said so.

    The requiredness half is the mirror image and equally fatal at call time.
    """
    for job_name, target, job in _engine_workflow_calls(yaml.safe_load(body)):
        where = f"{page.relative_to(ENGINE).as_posix()}:{line} job `{job_name}`"
        assert (WORKFLOWS / target).is_file(), (
            f"{where} calls `{target}`, which this engine does not ship -- the pasted "
            "wrapper cannot resolve it"
        )
        declared = _workflow_call_inputs(target)
        passed = job.get("with") or {}
        undeclared = sorted(set(passed) - set(declared))
        assert not undeclared, (
            f"{where} passes {undeclared} to `{target}`, which declares no such "
            "workflow_call input -- GitHub rejects the run as it LOADS, so a consumer "
            "pasting this wrapper gets no job and no log"
        )
        unpassed = sorted(
            n
            for n, spec in declared.items()
            if isinstance(spec, dict) and spec.get("required") and n not in passed
        )
        assert not unpassed, (
            f"{where} omits {unpassed}, which `{target}` declares required -- the call "
            "is rejected before any job starts"
        )


def _dispatch_inputs(doc):
    """(input name, spec) per `workflow_dispatch` input in a fence.

    `on:` is YAML 1.1's `y`/`yes`/`on` family, so `yaml.safe_load` gives the key
    back as `True`. Reading only the string spelling would silently find nothing.
    """
    on = doc.get("on", doc.get(True)) if isinstance(doc, dict) else None
    wd = on.get("workflow_dispatch") if isinstance(on, dict) else None
    for name, spec in ((wd.get("inputs") if isinstance(wd, dict) else None) or {}).items():
        yield name, spec if isinstance(spec, dict) else {}


def test_no_documented_wrapper_input_is_required():
    """Every documented `workflow_dispatch` input is optional with a default.

    The wrappers are dispatched only by `actions/dispatch` from a body the engine
    builds -- no human fills a form -- so `required: true` protects no caller.
    What it does do is turn a value the engine sent empty on purpose into an
    HTTP 422 before the workflow starts: GitHub reads an empty value for a
    required `workflow_dispatch` input as "not provided". Every `shipmate unlock`
    dispatch failed that way while the wrapper still declared the plan-run input
    the engine has since retired, since unlock applies no plan and so carried no
    run id. The engine validates instead, where the mode is known.

    Whole-vector comparison against a hand-written constant, for the reason
    `CLAUDE.md` gives: a "no input is required" predicate is also satisfied by a
    selector that finds nothing, and a per-input assertion cannot see an input
    that was deleted. Adding an input here is a deliberate edit that must state
    its shape, which is the point -- `required` is what a new input drifts to.

    Out of reach: a fence that shows an input block as a *fragment*, with no
    `on:` above it -- `docs/upgrading.md`'s migration snippet is one. Those are
    illustrative; the copyable wrapper in `getting-started.md` is what consumers
    paste, and it is what this pins.
    """
    found = sorted(
        (page.relative_to(ENGINE).as_posix(), name, spec.get("required"), spec.get("default"))
        for page, _, body in _FENCES
        for name, spec in _dispatch_inputs(yaml.safe_load(body))
    )
    assert found == [
        ("docs/getting-started.md", "environment", False, ""),
        ("docs/getting-started.md", "mode", False, "apply"),
        ("docs/getting-started.md", "pr_number", False, ""),
        ("docs/getting-started.md", "ref", False, ""),
    ], f"documented workflow_dispatch inputs changed: {found}"
