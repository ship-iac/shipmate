"""Shared test-side helpers: load a ``scripts/`` helper, or read an action.yml.

Two jobs: ``load_script`` for the extension-less helpers, and
``ENGINE``/``ACTIONS``/``WORKFLOWS`` + ``action_steps`` for the YAML-shape
guards. The step parser is load-bearing -- a guard that silently parses to
``[]`` asserts nothing -- so it has one definition.

Loading a helper script
-----------------------

``scripts/*`` run as GHA steps rather than as an importable package, so they
carry no ``.py`` suffix and cannot be imported by name. ``spec_from_file_location``
is no help either: it infers the loader from the suffix and returns None for
these on every platform, not just Windows. Passing ``SourceFileLoader``
explicitly sidesteps the suffix guess.

Importable from every test module because ``scripts/tests`` is on the pytest
``pythonpath`` (``pyproject.toml``). Prepend import mode would put this directory
on ``sys.path`` anyway, since there is no ``__init__.py`` here -- but that is
pytest's default behaviour rather than a declared invariant, and 20 modules
failing collection is a poor way to discover someone changed the import mode.

The ``-`` to ``_`` name mapping is load-bearing rather than cosmetic --
``test_env_order.py`` asserts ``eo.bm._run.__module__ == "build_matrix"``.

Deliberately NOT cached in ``sys.modules``: every call returns a fresh module
object, matching what the ``_load`` helpers inside the production scripts do.
Several tests depend on that isolation -- ``test_env_order.py`` monkeypatches
``eo.bm._run``, and a shared ``build_matrix`` instance would leak that patch
into every other sibling holding a reference to it, for the rest of the session.

Tests of the ``dev/`` tooling do not use this. Those are real ``.py`` modules on
the pytest ``pythonpath`` (``pyproject.toml``), imported by name.

The production scripts keep their own local ``_load`` copies on purpose: the
internal-pin guard derives each pinned action's transitive script set by
matching the literal ``_load("<name>")`` call pattern (``dev/pinrefs.py``), and
a shared module imported rather than named in an ``action.yml`` would be a
dependency no part of that derivation can see.
"""

import copy
import functools
import importlib.util
import pathlib
import shutil
import subprocess
from importlib.machinery import SourceFileLoader

import yaml

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]

#: The engine repo root, and the trees the source-derived guards read.
ENGINE = _SCRIPTS.parent
SCRIPTS = _SCRIPTS
ACTIONS = ENGINE / "actions"
WORKFLOWS = ENGINE / ".github" / "workflows"


def load_script(fname):
    """Load ``scripts/<fname>``, named with ``-`` mapped to ``_``."""
    loader = SourceFileLoader(fname.replace("-", "_"), str(_SCRIPTS / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@functools.cache
def _parse_action(path):
    """Parsed ``action.yml``, cached: nothing in the suite rewrites these files."""
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Never fall back to ``{}``: a file that parses to None (emptied by a bad
    # merge, fully commented out) would hand every guard zero steps, and a guard
    # over zero steps passes while asserting nothing.
    assert isinstance(spec, dict), (
        f"{path} did not parse to a mapping ({spec!r}) -- guards derived from it "
        "would assert nothing"
    )
    return spec


def action_yaml(action):
    """Parsed ``action.yml`` for ``action`` -- a composite action's name under
    ``actions/``, or a path to the file itself (what a parametrized guard
    iterating ``ACTIONS.glob("*/action.yml")`` already holds).

    A deep copy per call: the parse is cached, so handing out the cached object
    would let the first guard that normalizes what it was given (dedenting a
    `run:`, sorting steps) silently rewrite what every later module asserts
    against -- a false green that only reproduces in a full-suite run.
    """
    path = action if isinstance(action, pathlib.Path) else ACTIONS / action / "action.yml"
    return copy.deepcopy(_parse_action(path))


def action_steps(action):
    """``runs.steps`` for ``action``, or ``[]`` for one that declares none
    (a non-composite action is legal, and has no bash for a guard to read)."""
    return (action_yaml(action).get("runs") or {}).get("steps") or []


@functools.cache
def usable_bash():
    """Path to a bash that actually runs, or None -- what the tests that execute
    an action's shell body skip on.

    ``which("bash")`` alone is not enough on Windows: the Store/WSL ``bash.exe``
    execution alias sits on PATH by default and answers every invocation with a
    UTF-16 error on stdout and a non-zero exit. A test trusting it reads that as
    the shell body misbehaving, so each candidate is probed before it is used.
    Linux CI takes the first candidate and never reaches the fallback.
    """
    for cand in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if not cand or not pathlib.Path(cand).exists():
            continue
        try:
            probe = subprocess.run(
                [cand, "-c", "printf ok"], capture_output=True, text=True, timeout=30
            )
        except OSError:
            continue
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return cand
    return None
