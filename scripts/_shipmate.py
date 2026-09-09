"""Load extension-less sibling scripts as fresh modules.

``spec_from_file_location`` infers the loader from the suffix and returns None for a
suffix-less file, so the ``SourceFileLoader`` is passed explicitly. Nothing is cached in
``sys.modules``: every call returns a fresh module, so a test that monkeypatches one sibling's
``bm._run`` cannot leak the patch into every other holder of ``build_matrix``. The ``-`` to
``_`` module-name mapping is asserted by tests (``eo.bm._run.__module__ == "build_matrix"``).
"""

import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parent


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    # spec_from_loader is typed Optional; with a real loader it never returns None.
    mod = importlib.util.module_from_spec(spec)  # ty: ignore[invalid-argument-type]
    loader.exec_module(mod)
    return mod
