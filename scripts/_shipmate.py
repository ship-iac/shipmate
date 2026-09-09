"""Load extension-less sibling scripts as fresh modules; do not share mutable state."""

import importlib.util
import pathlib
from importlib.machinery import ModuleSpec, SourceFileLoader
from typing import cast


def _load(fname):
    loader = SourceFileLoader(
        fname.replace("-", "_"), str(pathlib.Path(__file__).resolve().parent / fname)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    loader.exec_module(mod)
    return mod
