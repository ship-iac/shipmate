import json

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
    cells = ad.workset_from_artifacts(names, "dev-eu", graph_paths, {})
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


def test_workset_ignores_slug_with_wrong_env_suffix():
    names = ["plan.dev-eu-apply.stacks-app"]  # not the plain env
    cells = ad.workset_from_artifacts(names, "dev-eu", ["stacks/app"], {})
    assert cells == []


def test_workset_env_suffix_no_cross_match():
    # env "eu" must NOT match "dev-eu" artifacts (forward-construct, no reverse split)
    names = ["plan.dev-eu.stacks-app"]
    assert ad.workset_from_artifacts(names, "eu", ["stacks/app"], {}) == []


def test_old_delimiter_collision_no_longer_forward_matches():
    # The L9 collision: (stacks/app, dev-eu) planned; apply-detect runs for env
    # "eu" with a graph path "stacks/app-dev". Under the old `plan-<slug>-<env>`
    # scheme both rendered `plan-stacks-app-dev-eu`, so env "eu" wrongly enrolled
    # stacks/app-dev. Under `plan.<env>.<slug>` the artifact is
    # `plan.dev-eu.stacks-app` and env "eu" constructs `plan.eu.stacks-app-dev`
    # -> no match.
    names = ["plan.dev-eu.stacks-app"]
    assert ad.workset_from_artifacts(names, "eu", ["stacks/app-dev"], {}) == []


def test_workset_slug_collision_fails_loud():
    # two distinct paths slug identically -> ambiguous artifact match -> fail loud
    names = ["plan.dev-eu.stacks-a-b"]
    with pytest.raises(SystemExit):
        ad.workset_from_artifacts(names, "dev-eu", ["stacks/a/b", "stacks-a/b"], {})


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


def test_dag_shape_notice_reports_a_flat_graph():
    # The migration shape: every stack independent, so the whole repository
    # would apply at once. Nothing can detect the missing edges — this line is
    # how a reader who knows the repository notices.
    deps = {"stacks/a": set(), "stacks/b": set(), "stacks/c": set()}
    assert ad.dag_shape_notice(deps) == (
        "::notice::3 stacks, 0 after edges, 1 wave levels; 3 stacks would apply concurrently"
    )


def test_dag_shape_notice_reports_a_layered_graph():
    # `stacks/d` carries two edges on purpose: with one edge per dependent stack
    # an edge count and a count of stacks-that-have-dependencies agree, and the
    # figure a reader is being asked to judge is the edge count.
    deps = {
        "stacks/a": set(),
        "stacks/b": {"stacks/a"},
        "stacks/c": {"stacks/a"},
        "stacks/d": {"stacks/b", "stacks/c"},
    }
    assert ad.dag_shape_notice(deps) == (
        "::notice::4 stacks, 4 after edges, 3 wave levels; 2 stacks would apply concurrently"
    )


def test_main_emits_the_dag_shape_notice(monkeypatch, tmp_path, capsys):
    # The line is worth nothing unprinted: this executes the real entry point.
    deps = {"stacks/a": set(), "stacks/b": {"stacks/a"}}
    monkeypatch.setattr(ad, "verify_plan_run", lambda repo, run_id, head: None)
    monkeypatch.setattr(ad, "run_graph_deps", lambda: deps)
    monkeypatch.setattr(ad, "_artifact_names", lambda repo, run_id: ["plan.dev-eu.stacks-a"])
    monkeypatch.setattr(ad, "completed_apply_names", lambda repo, head: set())
    # Every terramate call main() makes must be stubbed: CI installs uv alone,
    # so a real invocation passes on a developer machine and fails there.
    monkeypatch.setattr(ad.bm, "_tags", lambda stack: ["env/dev-eu"])
    for name, value in {
        "GITHUB_REPOSITORY": "acme/iac",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_PLAN_RUN_ID": "123456",
        "SHIPMATE_HEAD_SHA": "0123456789abcdef0123456789abcdef01234567",
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        # An absent decision refuses the run; this test is about the notice.
        "SHIPMATE_REVIEW_DECISION": "APPROVED",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SHIPMATE_UNGATED_ENVS", raising=False)
    ad.main()
    assert (
        "::notice::2 stacks, 1 after edges, 2 wave levels; 1 stacks would apply concurrently"
        in capsys.readouterr().out.splitlines()
    )


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


def test_validate_env_rejects_empty():
    # An empty env reads as a BARE apply inside _review_reason, which exempts it
    # whenever SHIPMATE_UNGATED_ENVS names anything -- a bypassed refusal on a
    # gate path. There is no bare form on this workflow.
    with pytest.raises(SystemExit):
        ad.validate_env("")


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


def test_main_wires_the_tag_map_into_the_cells(tmp_path, monkeypatch):
    # Two claims at once: the map reaches the cells (without it every cell is
    # role-less and the suite stays green), and it is derived for the workset
    # alone -- evaluating `stacks/unrelated` would let a stack this apply never
    # touches block an approved plan.
    out = tmp_path / "out"
    for k, v in {
        "GITHUB_REPOSITORY": "o/r",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_PLAN_RUN_ID": "42",
        "SHIPMATE_HEAD_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(out),
        "SHIPMATE_REVIEW_DECISION": "APPROVED",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SHIPMATE_UNGATED_ENVS", raising=False)
    monkeypatch.setattr(ad, "verify_plan_run", lambda *a: None)
    monkeypatch.setattr(
        ad, "run_graph_deps", lambda: {"stacks/app": set(), "stacks/unrelated": set()}
    )
    monkeypatch.setattr(ad, "_artifact_names", lambda *a: ["plan.dev-eu.stacks-app"])
    monkeypatch.setattr(ad, "completed_apply_names", lambda *a: set())
    evaluated = []
    monkeypatch.setattr(
        ad.bm,
        "_tags",
        lambda stack: evaluated.append(stack) or ["env/dev-eu", "workload/net-edge"],
    )
    ad.main()
    parsed = dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())
    assert json.loads(parsed["waves"])["wave0"] == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload_var": "NET_EDGE"}
    ]
    assert evaluated == ["stacks/app"]


# --- the targeted path re-verifies the review decision ----------------------
# `apply.yml` used to consult the review decision nowhere at all, so an
# `ungated-envs` input wider than vars.SHIPMATE_UNGATED_ENVS applied unreviewed
# there unconditionally. The engine now reads the variable itself and refuses.


@pytest.mark.parametrize(
    ("decision", "ungated"),
    [
        ("NONE", ""),
        ("APPROVED", ""),
        ("APPROVED", "prod-eu"),
        ("REVIEW_REQUIRED", "dev-eu"),
        ("REVIEW_REQUIRED", "other,DEV-EU"),
    ],
)
def test_refuse_unreviewed_lets_an_authorized_apply_through(decision, ungated):
    ad.refuse_unreviewed("dev-eu", ungated, decision)  # must not raise


@pytest.mark.parametrize(
    ("decision", "ungated"),
    [
        # The hole: an unreviewed PR with the variable unset. The input the
        # consumer wired into comment-ops cannot widen this.
        ("REVIEW_REQUIRED", ""),
        # Listed, but not THIS env.
        ("REVIEW_REQUIRED", "prod-eu"),
        ("CHANGES_REQUESTED", "dev-eu"),
        # The review job's sentinel for a pr_number matching no pull request.
        ("MISSING_PR", "dev-eu"),
        ("BANANA", ""),
        # Wiring drift: the decision never arrived at all.
        ("", "dev-eu"),
        ("", ""),
    ],
)
def test_refuse_unreviewed_refuses_everything_else(decision, ungated):
    with pytest.raises(SystemExit) as exc_info:
        ad.refuse_unreviewed("dev-eu", ungated, decision)
    assert str(exc_info.value).startswith("::error::not authorized")


def test_refuse_unreviewed_reuses_authorizes_selector_verbatim():
    # One function decides review policy on all three call sites; a second
    # implementation here would eventually disagree with comment-ops about the
    # same pull request. Reason text compared whole, not paraphrased.
    reason = ad.az._review_reason("REVIEW_REQUIRED", "dev-eu", frozenset({"prod-eu"}))
    with pytest.raises(SystemExit) as exc_info:
        ad.refuse_unreviewed("dev-eu", "prod-eu", "REVIEW_REQUIRED")
    assert str(exc_info.value) == f"::error::{reason}"


def test_refuse_unreviewed_rejects_a_malformed_variable_entry():
    # parse_ungated_envs fails closed on an entry that could never match an env
    # name -- a silently inert entry leaves an operator believing an
    # environment is exempt when it is not.
    with pytest.raises(SystemExit):
        ad.refuse_unreviewed("dev-eu", "dev-eu-apply", "REVIEW_REQUIRED")


def test_main_refuses_before_it_touches_the_plan_run(monkeypatch, tmp_path):
    # The refusal is the first thing after input validation: the run dies
    # before any API call, before any wave, and the apply checks -- and so the
    # gate -- stay pending.
    def _boom(*a, **kw):
        raise AssertionError("main() reached the plan-run lookup on an unreviewed apply")

    monkeypatch.setattr(ad, "verify_plan_run", _boom)
    monkeypatch.setattr(ad, "run_graph_deps", _boom)
    monkeypatch.setattr(ad, "_artifact_names", _boom)
    monkeypatch.setattr(ad, "completed_apply_names", _boom)
    for name, value in {
        "GITHUB_REPOSITORY": "acme/iac",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_PLAN_RUN_ID": "123456",
        "SHIPMATE_HEAD_SHA": "0123456789abcdef0123456789abcdef01234567",
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        "SHIPMATE_REVIEW_DECISION": "REVIEW_REQUIRED",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SHIPMATE_UNGATED_ENVS", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        ad.main()
    assert str(exc_info.value).startswith("::error::not authorized")


def test_main_refuses_when_the_decision_variable_is_absent(monkeypatch, tmp_path):
    # The default in `os.environ.get("SHIPMATE_REVIEW_DECISION", "")` is the
    # whole fail-closed behaviour when the review job's output never reaches
    # the action, and every other main() test sets the variable -- so without
    # this case the default could be flipped to "APPROVED" unnoticed.
    def _boom(*a, **kw):
        raise AssertionError("main() proceeded with no review decision at all")

    monkeypatch.setattr(ad, "verify_plan_run", _boom)
    monkeypatch.setattr(ad, "run_graph_deps", _boom)
    for name, value in {
        "GITHUB_REPOSITORY": "acme/iac",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_PLAN_RUN_ID": "123456",
        "SHIPMATE_HEAD_SHA": "0123456789abcdef0123456789abcdef01234567",
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("SHIPMATE_REVIEW_DECISION", "SHIPMATE_UNGATED_ENVS"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as exc_info:
        ad.main()
    assert str(exc_info.value).startswith("::error::not authorized")


# --- unlock mode: the queue is the pending apply checks ----------------------
# `shipmate unlock <env>` applies no plan and consumes no plan artifact: a lock
# outlives the run that stranded it, so those artifacts may be long expired.


def _unlock_env(monkeypatch, tmp_path, **overrides):
    """Env for a main() run, unlock unless `SHIPMATE_MODE` is overridden.

    Returns the GITHUB_OUTPUT path. `SHIPMATE_PLAN_RUN_ID` is absent by design —
    the unlock path must never read it."""
    out = tmp_path / "out"
    env = {
        "GITHUB_REPOSITORY": "acme/iac",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_HEAD_SHA": "a" * 40,
        "SHIPMATE_MODE": "unlock",
        "GITHUB_OUTPUT": str(out),
    }
    env.update(overrides)
    for name in ("SHIPMATE_PLAN_RUN_ID", "SHIPMATE_UNGATED_ENVS", "SHIPMATE_REVIEW_DECISION"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return out


def _boom_on_plan_path(monkeypatch):
    """Every plan-artifact-dependent call fails loudly: unlock must reach none."""

    def _boom(*a, **kw):
        raise AssertionError("unlock mode reached the plan-artifact path")

    for name in ("verify_plan_run", "_artifact_names", "workset_from_artifacts"):
        monkeypatch.setattr(ad, name, _boom)


def _stub_unlock_tree(monkeypatch, cells, completed=()):
    """Stub the tree walk; returns the kwargs `compute_cells` was called with."""
    seen = {}

    def _compute(all_stacks=False, base=""):
        seen.update(all_stacks=all_stacks, base=base)
        return cells

    monkeypatch.setattr(ad.bm, "compute_cells", _compute)
    monkeypatch.setattr(ad, "completed_apply_names", lambda repo, head: set(completed))
    return seen


_DEV_EU_CELLS = [
    {"stack": "stacks/app", "environment": "dev-eu", "workload": "app", "workload_var": "APP"},
    {"stack": "stacks/dns", "environment": "dev-eu", "workload": "net", "workload_var": "NET"},
    {"stack": "stacks/db", "environment": "dev-eu", "workload": "app", "workload_var": "APP"},
    {"stack": "stacks/app", "environment": "prod-eu", "workload": "app", "workload_var": "APP"},
]


def _parsed(out):
    return dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())


def test_unlock_queue_is_the_pending_cells_of_the_target_env(monkeypatch, tmp_path):
    # Two pending in dev-eu, one completed there, one cell in another env.
    out = _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    seen = _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS, ["apply / stacks/dns / dev-eu"])
    ad.main()
    # all_stacks=True is the point: a cell whose plan artifacts expired long ago
    # is exactly the cell that can hold a stranded lock.
    assert seen == {"all_stacks": True, "base": ""}
    assert json.loads(_parsed(out)["cells"]) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app", "workload_var": "APP"},
        {"stack": "stacks/db", "environment": "dev-eu", "workload": "app", "workload_var": "APP"},
    ]
    assert _parsed(out)["empty"] == "false"


def test_unlock_emits_no_wave_array_with_any_member(monkeypatch, tmp_path):
    # The guard against a fall-through into the apply matrix: a mode confusion
    # that reaches the wave assignment turns an unlock into an apply.
    out = _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS)
    ad.main()
    parsed = _parsed(out)
    assert len(json.loads(parsed["cells"])) == 3  # not vacuous: there IS a queue
    wave_keys = [k for k in parsed if k == "waves" or k.startswith("wave")]
    assert wave_keys == []


def test_unlock_needs_no_plan_run_and_never_verifies_one(monkeypatch, tmp_path):
    # An empty plan_run_id is legitimate here, and `verify_plan_run` is not
    # merely tolerated — it must not be called at all (_boom_on_plan_path).
    out = _unlock_env(monkeypatch, tmp_path, SHIPMATE_PLAN_RUN_ID="")
    _boom_on_plan_path(monkeypatch)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS)
    ad.main()
    assert len(json.loads(_parsed(out)["cells"])) == 3


def test_unlock_does_not_refuse_an_unreviewed_pr(monkeypatch, tmp_path):
    # An approval reviews a diff and unlock applies none; `scripts/authorize`
    # made the same call at comment time. The apply-mode half of this
    # divergence is test_unlock_absent_mode_takes_the_stricter_apply_path.
    out = _unlock_env(monkeypatch, tmp_path, SHIPMATE_REVIEW_DECISION="")
    _boom_on_plan_path(monkeypatch)

    def _boom(*a):
        raise AssertionError("unlock mode consulted the review decision")

    monkeypatch.setattr(ad, "refuse_unreviewed", _boom)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS)
    ad.main()
    assert len(json.loads(_parsed(out)["cells"])) == 3


@pytest.mark.parametrize("mode", ["", "apply", "APPLY", "unlock-ish", "banana"])
def test_unlock_absent_mode_takes_the_stricter_apply_path(monkeypatch, tmp_path, mode):
    # Same review_decision="" the unlock test accepts; anything that is not
    # exactly "unlock" must refuse, so an absent or garbled mode fails CLOSED.
    _unlock_env(monkeypatch, tmp_path, SHIPMATE_MODE=mode, SHIPMATE_PLAN_RUN_ID="1")
    with pytest.raises(SystemExit) as exc_info:
        ad.main()
    assert str(exc_info.value).startswith("::error::not authorized")


def test_unlock_notice_names_the_mode(monkeypatch, tmp_path, capsys):
    _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS, ["apply / stacks/dns / dev-eu"])
    ad.main()
    assert (
        f"::notice title=apply-detect::mode=unlock env=dev-eu head={'a' * 40} "
        "cells=3 completed=1 pending=2" in capsys.readouterr().out.splitlines()
    )


def test_apply_mode_writes_the_whole_output_file_verbatim(monkeypatch, tmp_path):
    # `cells` is written in BOTH modes and is never an empty string: the unlock
    # job's strategy.matrix.include is fromJSON(cells), and fromJSON('') errors.
    # Whole-file comparison against a hand-written constant, so an added,
    # dropped or reordered key on the apply path is caught here too.
    out = _unlock_env(
        monkeypatch,
        tmp_path,
        SHIPMATE_MODE="apply",
        SHIPMATE_PLAN_RUN_ID="42",
        SHIPMATE_REVIEW_DECISION="APPROVED",
    )
    monkeypatch.setattr(ad, "verify_plan_run", lambda *a: None)
    monkeypatch.setattr(ad, "run_graph_deps", lambda: {"stacks/app": set()})
    monkeypatch.setattr(ad, "_artifact_names", lambda *a: ["plan.dev-eu.stacks-app"])
    monkeypatch.setattr(ad, "completed_apply_names", lambda *a: set())
    monkeypatch.setattr(ad.bm, "_tags", lambda stack: ["env/dev-eu", "workload/app"])
    ad.main()
    assert out.read_text(encoding="utf-8") == (
        'waves={"wave0": [{"stack": "stacks/app", "environment": "dev-eu", '
        '"workload_var": "APP"}], "wave1": [], "wave2": [], "wave3": [], "wave4": [], '
        '"wave5": [], "wave6": [], "wave7": []}\n'
        "empty=false\n"
        "cells=[]\n"
        "head_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "plan_run_id=42\n"
    )
