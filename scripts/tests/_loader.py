"""Load one of the extension-less helper scripts under ``scripts/`` as a module.

``scripts/*`` run as GHA steps rather than as an importable package, so they
carry no ``.py`` suffix and cannot be imported by name. ``spec_from_file_location``
is no help either: it infers the loader from the suffix and returns None for
these on every platform, not just Windows. Passing ``SourceFileLoader``
explicitly sidesteps the suffix guess.

Importable from every test module with no ``pythonpath`` entry: ``scripts/tests``
has no ``__init__.py``, so pytest's default prepend import mode puts this
directory on ``sys.path`` itself.

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

import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]


def load_script(fname):
    """Load ``scripts/<fname>``, named with ``-`` mapped to ``_``."""
    loader = SourceFileLoader(fname.replace("-", "_"), str(_SCRIPTS / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod
