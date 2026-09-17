from _loader import load_script

dd = load_script("deploy-detect")
pf = load_script("pr-facts")
bm = load_script("build-matrix")


def test_gh_json_lives_in_env_config():
    # gh_json lives once, next to _run, in the module that `_load`s nothing; build-matrix
    # aliases it the same way, so its own callers cannot fork a second copy.
    # Mutation: give build-matrix its own `def gh_json`.
    assert bm.gh_json is bm.ec.gh_json


def test_deploy_detect_gh_json_is_the_single_source():
    # deploy-detect references build-matrix.gh_json, not a private copy. apply-detect holds no
    # alias because it makes no `gh_json` call at all; the one `gh api` call its apply path makes
    # is pinned whole-list by test_apply_detect.test_apply_path_makes_no_run_lookup_at_all.
    assert dd._gh_json is dd.bm.gh_json


def test_pr_facts_gh_json_is_the_single_source():
    # pr-facts references build-matrix.gh_json, not a private copy: one definition decides how a
    # nonzero `gh api` exit surfaces.
    assert pf._gh_json is pf.bm.gh_json
