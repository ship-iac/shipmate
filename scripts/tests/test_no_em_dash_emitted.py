"""No shipped string carries an em dash, an en dash or a middle dot.

Scope: every Python string constant under `scripts/` that is not a module, class or
function docstring (f-string parts included), and every YAML string in
`actions/*/action.yml` and `.github/workflows/*.yml` outside lines that are shell
comments. That covers what the engine prints, posts and writes as a status, and also
action `description:` fields and step `name:`s: one rule over every shipped string is
smaller than a classification of which strings reach a reader. Docstrings and comments
are out of scope.

Threat model: accidental regression, a new message written with a dash. The helpers
under `scripts/` carry no extension, so Python is recognised by a `python3` shebang or
a `.py` suffix, and any other file there fails the scan rather than being skipped.
"""

import ast

import pytest
import yaml
from _loader import ACTIONS, ENGINE, SCRIPTS, WORKFLOWS

#: U+2014 em dash, U+2013 en dash, U+00B7 middle dot.
BANNED = ("—", "–", "·")

_DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _has(text):
    return any(c in text for c in BANNED)


def _is_python(path, src):
    first = src.splitlines()[0] if src else ""
    return path.suffix == ".py" or (first.startswith("#!") and "python3" in first)


def _python_sources(scripts):
    """Every Python file under `scripts` outside `tests/` and `__pycache__`, read."""
    out = {}
    for p in sorted(scripts.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or p.is_relative_to(scripts / "tests"):
            continue
        src = p.read_text(encoding="utf-8")
        if not _is_python(p, src):
            raise AssertionError(
                f"{p} is not Python (no python3 shebang, no .py suffix), so this guard "
                "cannot read its strings. Teach the guard its format or move it."
            )
        out[p] = src
    return out


def _docstrings(tree):
    return {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, _DOC_OWNERS)
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
        and isinstance(n.body[0].value.value, str)
    }


def _python_hits(path, src):
    tree = ast.parse(src)
    docs = _docstrings(tree)
    lines = src.splitlines()
    hits = []
    for n in ast.walk(tree):
        is_str = isinstance(n, ast.Constant) and isinstance(n.value, str)
        if is_str and id(n) not in docs and _has(n.value):
            span = range(n.lineno, n.end_lineno + 1)
            hits += [ln for ln in span if _has(lines[ln - 1])] or [n.lineno]
    return [f"{path.relative_to(ENGINE).as_posix()}:{ln}" for ln in hits]


def _yaml_files():
    return sorted(ACTIONS.glob("*/action.yml")) + sorted(WORKFLOWS.glob("*.yml"))


def _yaml_hits(path):
    """Composed, not loaded, so each scalar keeps the line it starts on."""
    root = yaml.compose(path.read_text(encoding="utf-8"), Loader=yaml.SafeLoader)
    hits, stack = [], [root]
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.MappingNode):
            stack += [part for pair in node.value for part in pair]
        elif isinstance(node, yaml.SequenceNode):
            stack += node.value
        elif isinstance(node, yaml.ScalarNode):
            block = 1 if node.style in ("|", ">") else 0
            for i, line in enumerate(node.value.splitlines()):
                if _has(line) and not line.strip().startswith("#"):
                    hits.append(node.start_mark.line + 1 + block + i)
    return [f"{path.relative_to(ENGINE).as_posix()}:{ln}" for ln in sorted(hits)]


def test_the_scan_reaches_every_tree():
    """Without this the dash test passes on an empty scan.

    Mutation: filter `_python_sources` to `p.suffix == ".py"`.
    """
    scripts = {p.relative_to(ENGINE).as_posix() for p in _python_sources(SCRIPTS)}
    yamls = {p.relative_to(ENGINE).as_posix() for p in _yaml_files()}
    assert {"scripts/build-matrix", "scripts/_shipmate.py"} <= scripts, sorted(scripts)[:10]
    assert {"actions/apply-cell/action.yml", ".github/workflows/deploy.yml"} <= yamls


def test_a_non_python_file_under_scripts_fails_the_scan(tmp_path):
    """Mutation: replace the `raise AssertionError` in `_python_sources` with `continue`."""
    (tmp_path / "helper").write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="is not Python"):
        _python_sources(tmp_path)


def test_no_shipped_string_carries_a_banned_dash():
    """Mutations, each red on its own: an em dash in an f-string in `scripts/apply-detect`;
    in an `::error::` echo of an `actions/apply-cell` `run:` body; in a step `name:`. An em
    dash in a docstring or on a shell-comment line of a `run:` body stays green.
    """
    hits = [h for p, src in _python_sources(SCRIPTS).items() for h in _python_hits(p, src)]
    hits += [h for p in _yaml_files() for h in _yaml_hits(p)]
    assert hits == [], f"use a colon, comma, parentheses or a new sentence: {hits}"
