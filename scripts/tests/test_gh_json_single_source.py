from _loader import load_script

dd = load_script("deploy-detect")
bm = load_script("build-matrix")


def test_build_matrix_has_gh_json():
    # gh_json lives once, next to _run, in build-matrix.
    assert callable(bm.gh_json)


def test_deploy_detect_gh_json_is_the_single_source():
    # deploy-detect references build-matrix.gh_json, not a private copy.
    # apply-detect holds no alias: it makes no `gh_json` call at all. The one
    # `gh api` call its apply path does make is pinned, whole-list, by
    # test_apply_detect.test_apply_path_makes_no_run_lookup_at_all.
    assert dd._gh_json is dd.bm.gh_json
