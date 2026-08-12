"""Guards the plan-side `environment:` bindings the docs tell a consumer to copy
into `plan.yml` and `drift.yml`, in both shapes the pages document.

Four pages carry one: `CONTRACT.md` §Env model (normative) and
`docs/upgrading.md` §0.13.0 (the migration) hold the mixed-mode folded
expression, while `docs/getting-started.md`'s `plan.yml` and `docs/drift.md`'s
`drift.yml` hold the static `${{ matrix.environment }}-plan` a uniform-split
consumer actually copies. None is read by any consumer-facing code, so nothing
else reddens when they rot. The realistic failures, and the whole threat model
here:

- the engine's own expression evolves, `test_apply_env_binding_guard.py` goes
  red, its author updates that constant, and the documented copies keep telling
  consumers to bind the old shape;
- the two starter workflows lose the suffix, or regain the bare `<env>` the
  0.13.0 rename replaced, and a new consumer copies a plan job that binds the
  apply-side naming or an environment nobody created;
- the `CONTRACT.md` fence is nested in a list item, so an outdent of its
  continuation line turns the folded scalar into a two-line value that GitHub
  rejects -- the exact trap the fence's own prose warns about, and one
  `test_docs_yaml_parses.py` cannot see (it scans README.md + docs/*.md only).

Not a hostile edit: every one of these files is reviewed on every pull request.
Whole-value comparisons against hand-written constants cover it, plus a
one-match-per-page count so a moved or reworded binding fails instead of
dropping out of coverage.

`PLAN_ENV` and `STATIC_PLAN_ENV` are hand-written and the engine's value is
derived from `PLAN_ENV` by suffix swap, never the other way round -- a constant
read back out of the file under test passes whatever that file says.
"""

import re
import textwrap

import yaml
from _loader import ENGINE, WORKFLOWS

#: Single line: the folded scalar (`>-`) collapses the two source lines with a
#: space, which is what makes an outdent detectable here.
PLAN_ENV = (
    "${{ contains(format(',{0},', vars.SHIPMATE_SHARED_ENVS), "
    "format(',{0},', matrix.environment)) "
    "&& matrix.environment "
    "|| format('{0}-plan', matrix.environment) }}"
)

#: What a uniform-split consumer copies out of the two starter workflows.
STATIC_PLAN_ENV = "${{ matrix.environment }}-plan"

_FENCE = re.compile(r"```yaml\n(.*?)```", re.S)
PAGES = ("CONTRACT.md", "docs/upgrading.md")
#: page -> the job whose `environment:` a consumer copies. `shipmate-engine` is
#: the one fixed environment name and is excluded, so each page has exactly one.
STARTER_WORKFLOWS = {"docs/getting-started.md": "plan", "docs/drift.md": "drift"}
_ENGINE_ENV = "shipmate-engine"


def _fences(page):
    """Every ```yaml fence of `page`, parsed.

    An outdented continuation line does not merely change a folded value, it can
    stop parsing altogether -- reported against the page, since a bare
    ScannerError names only the YAML stream.
    """
    text = (ENGINE / page).read_text(encoding="utf-8")
    for body in _FENCE.findall(text):
        try:
            yield yaml.safe_load(textwrap.dedent(body))
        except yaml.YAMLError as e:
            raise AssertionError(f"{page}: a ```yaml fence no longer parses: {e}") from e


def _starter_binding(page):
    """The one env-bearing job of `page`'s starter workflow: (job name, binding).

    Exactly one, asserted, for the same reason as `_documented_binding` below.
    """
    found = [
        (name, job["environment"])
        for doc in _fences(page)
        if isinstance(doc, dict) and isinstance(doc.get("jobs"), dict)
        for name, job in doc["jobs"].items()
        if isinstance(job, dict) and job.get("environment", _ENGINE_ENV) != _ENGINE_ENV
    ]
    assert len(found) == 1, (
        f"{page}: expected exactly one job binding an env-derived `environment:`, "
        f"found {found} -- a moved or reworded binding is unguarded"
    )
    return found[0]


def _documented_binding(page):
    """The one documented `environment:`-only fence of `page`, parsed.

    Exactly one, asserted: a reworded or relocated fence that stops matching
    would otherwise drop out of coverage and leave this guard green over nothing.
    """
    found = []
    for doc in _fences(page):
        if isinstance(doc, dict) and set(doc) == {"environment"}:
            found.append(doc["environment"])
    assert len(found) == 1, (
        f"{page}: expected exactly one ```yaml fence binding only `environment:`, "
        f"found {len(found)} -- a moved or reworded fence is unguarded"
    )
    return found[0]


def test_both_pages_document_the_plan_side_expression():
    for page in PAGES:
        assert _documented_binding(page) == PLAN_ENV, (
            f"{page}: the documented plan-side `environment:` must be the engine's "
            "mode expression with a `-plan` fallback, on one folded line"
        )


def test_the_starter_workflows_bind_the_split_plan_environment():
    """The fences a new consumer copies wholesale. Both are uniform-split, so the
    binding is the static suffixed name -- not the bare `${{ matrix.environment }}`
    the 0.13.0 rename retired, and not the apply-side suffix."""
    for page, job in STARTER_WORKFLOWS.items():
        assert _starter_binding(page) == (job, STATIC_PLAN_ENV), (
            f"{page}: the `{job}` job must bind the split plan environment `{STATIC_PLAN_ENV}`"
        )


def test_the_engine_binds_the_same_expression_with_the_apply_suffix():
    spec = yaml.safe_load((WORKFLOWS / "apply-env-level.yml").read_text(encoding="utf-8"))
    assert spec["jobs"]["wave0"]["environment"] == PLAN_ENV.replace("-plan", "-apply"), (
        "apply-env-level.yml's wave binding and the documented plan-side "
        "expression have diverged -- one of the two was updated alone"
    )
