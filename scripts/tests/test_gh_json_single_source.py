import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parents[1]


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ad = _load("apply-detect")
dd = _load("deploy-detect")
bm = _load("build-matrix")


def test_build_matrix_has_gh_json():
    # gh_json lives once, next to _run, in build-matrix.
    assert callable(bm.gh_json)


def test_detects_gh_json_is_the_single_source():
    # Both detects reference build-matrix.gh_json, not a private copy.
    assert ad._gh_json is ad.bm.gh_json
    assert dd._gh_json is dd.bm.gh_json
