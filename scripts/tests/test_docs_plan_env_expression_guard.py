"""Guards the cell-job `environment:` expression the normative pages document.

`CONTRACT.md` §Env model holds the expression, normatively and as the only copy. It is read by
no consumer-facing code, so nothing else reddens when it rots. The realistic failures, and the
whole threat model here:

- the engine's own expression evolves, `test_apply_env_binding_guard.py` goes
  red, its author updates that constant, and the documented copy keeps telling
  readers the old shape;
- the `CONTRACT.md` fence is nested in a list item, so a mis-indented edit can
  stop it parsing -- which `test_docs_yaml_parses.py` cannot see (it scans
  README.md + docs/*.md only).

Not a hostile edit: every one of these files is reviewed on every pull request.
Whole-value comparisons against hand-written constants cover it, plus a
one-match-per-page count so a moved or reworded binding fails instead of
dropping out of coverage.

`CELL_ENV` is hand-written, never read back out of a file under test: a constant derived from
the file it checks passes whatever that file says.
"""

import re
import textwrap

import yaml
from _loader import ENGINE, WORKFLOWS

CELL_ENV = "${{ matrix.env_binding }}"

_FENCE = re.compile(r"```yaml\n(.*?)```", re.S)
PAGES = ("CONTRACT.md",)


def _fences(page):
    """Every ```yaml fence of `page`, parsed.

    An outdented continuation line does not merely change a folded value, it can stop parsing
    altogether. Reported against the page, because a bare ScannerError names only the YAML
    stream.
    """
    text = (ENGINE / page).read_text(encoding="utf-8")
    for body in _FENCE.findall(text):
        try:
            yield yaml.safe_load(textwrap.dedent(body))
        except yaml.YAMLError as e:
            raise AssertionError(f"{page}: a ```yaml fence no longer parses: {e}") from e


def _documented_binding(page):
    """The one documented `environment:`-only fence of `page`, parsed.

    Exactly one, asserted: a reworded or relocated fence that stops matching would otherwise
    drop out of coverage and leave this guard green over nothing.
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


def test_every_page_documents_the_cell_job_expression():
    for page in PAGES:
        assert _documented_binding(page) == CELL_ENV, (
            f"{page}: the documented `environment:` must be the engine's cell-job binding"
        )


def test_the_engine_binds_the_documented_expression():
    spec = yaml.safe_load((WORKFLOWS / "apply-env-level.yml").read_text(encoding="utf-8"))
    assert spec["jobs"]["wave0"]["environment"] == CELL_ENV, (
        "apply-env-level.yml's wave binding and the documented expression have "
        "diverged -- one of the two was updated alone"
    )
