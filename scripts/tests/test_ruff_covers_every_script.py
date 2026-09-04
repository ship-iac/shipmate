"""Every Python script under ``scripts/`` is linted.

``scripts/*`` carry no ``.py`` suffix, so ruff does not discover them: each has to be named in
``[tool.ruff] extend-include``. A script left out of that list is never reported as unlinted --
``ruff check .`` says "All checks passed!" without opening it, which is how ``scripts/lock-info``
shipped a C901 violation unseen, and how an unused import in it would have gone unseen too.

Both sides are derived: the scripts from the filesystem, the list from ``pyproject.toml``. A
hand-written expected list would only pass until the next script is added. An extension-less file
whose first line is the python3 shebang is what counts as a python script, and that is what
excludes ``.gitkeep``.
"""

import tomllib

from _loader import ENGINE, SCRIPTS

_SHEBANG = "#!/usr/bin/env python3"


def _python_scripts():
    for p in sorted(SCRIPTS.iterdir()):
        if not p.is_file() or p.suffix:
            continue
        head = p.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0].strip()
        if head == _SHEBANG:
            yield f"scripts/{p.name}"


def test_extend_include_names_every_python_script():
    scripts = set(_python_scripts())
    # A guard over an empty set passes while asserting nothing, and this one derives both sides,
    # so a broken shebang read would otherwise pass.
    assert len(scripts) > 15, f"derived too few scripts to be reading the tree: {scripts}"
    config = tomllib.loads((ENGINE / "pyproject.toml").read_text(encoding="utf-8"))
    missing = sorted(scripts - set(config["tool"]["ruff"]["extend-include"]))
    assert not missing, (
        f"not in pyproject.toml [tool.ruff] extend-include, so ruff never reads them: {missing}"
    )
