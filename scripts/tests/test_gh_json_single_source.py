from _loader import load_script

ad = load_script("apply-detect")
dd = load_script("deploy-detect")
bm = load_script("build-matrix")


def test_build_matrix_has_gh_json():
    # gh_json lives once, next to _run, in build-matrix.
    assert callable(bm.gh_json)


def test_detects_gh_json_is_the_single_source():
    # Both detects reference build-matrix.gh_json, not a private copy.
    assert ad._gh_json is ad.bm.gh_json
    assert dd._gh_json is dd.bm.gh_json
