import pytest
from _detect_fixtures import check_run as _check
from _detect_fixtures import completed_names
from _loader import load_script

ad = load_script("apply-detect")


def test_workset_matches_plan_artifacts_for_env():
    names = [
        "plan.dev-eu.stacks-app",
        "plan.dev-eu.stacks-dns",
        "plan.dev-us.stacks-app",  # other env — excluded
        "cell-summary.dev-eu.stacks-app",
    ]  # not a plan artifact — excluded
    graph_paths = ["stacks/app", "stacks/dns", "stacks/platform"]
    cells = ad.workset_from_artifacts(names, "dev-eu", graph_paths)
    stacks = sorted(c["stack"] for c in cells)
    assert stacks == ["stacks/app", "stacks/dns"]
    assert all(c["environment"] == "dev-eu" for c in cells)


def test_workset_attaches_workload_var_from_the_tags():
    # A stack in the artifacts but absent from the tag map gets "" -- the map is
    # built from a separate terramate query and must never be able to raise here.
    names = ["plan.dev-eu.stacks-app", "plan.dev-eu.stacks-dns", "plan.dev-eu.stacks-platform"]
    cells = ad.workset_from_artifacts(
        names,
        "dev-eu",
        ["stacks/app", "stacks/dns", "stacks/platform"],
        {"stacks/app": ["env/dev-eu", "workload/net-edge"], "stacks/dns": ["env/dev-eu"]},
    )
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload_var": "NET_EDGE"},
        {"stack": "stacks/dns", "environment": "dev-eu", "workload_var": ""},
        {"stack": "stacks/platform", "environment": "dev-eu", "workload_var": ""},
    ]


def test_workset_without_a_tag_map_carries_an_empty_workload_var():
    assert ad.workset_from_artifacts(["plan.dev-eu.stacks-app"], "dev-eu", ["stacks/app"]) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload_var": ""}
    ]


def test_workset_ignores_slug_with_wrong_env_suffix():
    names = ["plan.dev-eu-apply.stacks-app"]  # not the plain env
    cells = ad.workset_from_artifacts(names, "dev-eu", ["stacks/app"])
    assert cells == []


def test_workset_env_suffix_no_cross_match():
    # env "eu" must NOT match "dev-eu" artifacts (forward-construct, no reverse split)
    names = ["plan.dev-eu.stacks-app"]
    assert ad.workset_from_artifacts(names, "eu", ["stacks/app"]) == []


def test_old_delimiter_collision_no_longer_forward_matches():
    # The L9 collision: (stacks/app, dev-eu) planned; apply-detect runs for env
    # "eu" with a graph path "stacks/app-dev". Under the old `plan-<slug>-<env>`
    # scheme both rendered `plan-stacks-app-dev-eu`, so env "eu" wrongly enrolled
    # stacks/app-dev. Under `plan.<env>.<slug>` the artifact is
    # `plan.dev-eu.stacks-app` and env "eu" constructs `plan.eu.stacks-app-dev`
    # -> no match.
    names = ["plan.dev-eu.stacks-app"]
    assert ad.workset_from_artifacts(names, "eu", ["stacks/app-dev"]) == []


def test_workset_slug_collision_fails_loud():
    # two distinct paths slug identically -> ambiguous artifact match -> fail loud
    names = ["plan.dev-eu.stacks-a-b"]
    with pytest.raises(SystemExit):
        ad.workset_from_artifacts(names, "dev-eu", ["stacks/a/b", "stacks-a/b"])


def test_filter_pending_drops_completed():
    cells = [
        {"stack": "stacks/app", "environment": "dev-eu"},
        {"stack": "stacks/dns", "environment": "dev-eu"},
    ]
    completed = {"apply / stacks/dns / dev-eu"}
    kept = ad.filter_pending(cells, completed)
    assert [c["stack"] for c in kept] == ["stacks/app"]


def _completed(monkeypatch, checks, **kw):
    """The "already applied" set every detect queries, with only `gh` stubbed."""
    return completed_names(ad, monkeypatch, checks, **kw)


def test_completed_failure_apply_stays_pending(monkeypatch):
    # "completed" status with a failing conclusion must not count as done —
    # apply-detect must share apply-gate's success/neutral predicate.
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    done = _completed(monkeypatch, [_check(conclusion="failure")])
    assert ad.filter_pending(cells, done) == cells


def test_foreign_app_completed_check_stays_pending(monkeypatch):
    # A completed+success check authored by another identity (github-actions,
    # app id 15368) must not count as done once SHIPMATE_APP_ID scopes the
    # query to the shipmate App (999).
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    done = _completed(monkeypatch, [_check(app={"id": 15368})])
    assert ad.filter_pending(cells, done) == cells


def test_check_runs_jsonl_parsing_reuses_apply_gates_parse_jsonl(monkeypatch):
    # No private json.loads-per-line loop: a malformed line raises SystemExit
    # naming the offending line, via the single shared implementation.
    with pytest.raises(SystemExit) as exc_info:
        _completed(monkeypatch, ['{"a": 1}', "not-json-garbage-{{{"])
    assert "not-json-garbage" in str(exc_info.value)


def test_verify_plan_run_rejects_mismatched_head_sha(monkeypatch):
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "aaa",
            "conclusion": "success",
            "path": ".github/workflows/plan.yml",
        },
    )
    with pytest.raises(SystemExit):
        ad.verify_plan_run("o/r", "123", "bbb")


def test_verify_plan_run_rejects_non_success_conclusion(monkeypatch):
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "bbb",
            "conclusion": "failure",
            "path": ".github/workflows/plan.yml",
        },
    )
    with pytest.raises(SystemExit):
        ad.verify_plan_run("o/r", "123", "bbb")


def test_verify_plan_run_rejects_wrong_workflow_path(monkeypatch):
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "bbb",
            "conclusion": "success",
            "path": ".github/workflows/deploy.yml",
        },
    )
    with pytest.raises(SystemExit):
        ad.verify_plan_run("o/r", "123", "bbb")


def test_verify_plan_run_rejects_lookalike_workflow_name(monkeypatch):
    # "evil-plan.yml" / "not-plan.yml" end with the substring "plan.yml"
    # but are not THE plan.yml at the repo root of workflows -- endswith on
    # the raw string is bypassable by a same-named-suffix workflow.
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "bbb",
            "conclusion": "success",
            "path": ".github/workflows/evil-plan.yml",
        },
    )
    with pytest.raises(SystemExit):
        ad.verify_plan_run("o/r", "123", "bbb")


def test_validate_head_sha_rejects_short():
    with pytest.raises(SystemExit):
        ad.validate_head_sha("abc123")


def test_validate_head_sha_rejects_uppercase():
    with pytest.raises(SystemExit):
        ad.validate_head_sha("A" * 40)


def test_validate_head_sha_rejects_non_hex():
    with pytest.raises(SystemExit):
        ad.validate_head_sha("g" * 40)


def test_validate_head_sha_rejects_path_chars():
    with pytest.raises(SystemExit):
        ad.validate_head_sha("../../etc/passwd")


def test_validate_head_sha_accepts_valid():
    ad.validate_head_sha("0123456789abcdef0123456789abcdef01234567")  # must not raise


def test_validate_plan_run_id_rejects_non_numeric():
    with pytest.raises(SystemExit):
        ad.validate_plan_run_id("123/actions/runs/456")


def test_validate_plan_run_id_rejects_empty():
    with pytest.raises(SystemExit):
        ad.validate_plan_run_id("")


def test_validate_plan_run_id_accepts_valid():
    ad.validate_plan_run_id("123456")  # must not raise


def test_validate_env_rejects_dot():
    # A '.' in env would break plan.<env>.<slug> disambiguation (env-first only
    # works because env has no '.') — fail loud at the trust boundary.
    with pytest.raises(SystemExit):
        ad.validate_env("dev.eu")


def test_validate_env_accepts_normal():
    ad.validate_env("dev-eu")  # hyphenated env is fine
    ad.validate_env("eu")  # must not raise


def test_verify_plan_run_passes_when_all_match(monkeypatch):
    monkeypatch.setattr(
        ad,
        "_gh_json",
        lambda path: {
            "head_sha": "bbb",
            "conclusion": "success",
            "path": ".github/workflows/plan.yml",
        },
    )
    ad.verify_plan_run("o/r", "123", "bbb")  # must not raise


def test_duplicate_run_newer_queued_stays_pending():
    # An old completed+success run must not mask a newer queued run of the same
    # check name — the latest run per name governs.
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    checks = [
        {
            "name": "apply / stacks/app / dev-eu",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-18T10:00:00Z",
            "id": 1,
        },
        {
            "name": "apply / stacks/app / dev-eu",
            "status": "queued",
            "conclusion": None,
            "started_at": "2026-07-18T11:00:00Z",
            "id": 2,
        },
    ]
    done = ad.ag.done_names(checks)
    assert ad.filter_pending(cells, done) == cells
