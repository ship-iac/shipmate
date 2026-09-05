import json

import pytest
from _detect_fixtures import APP_ID, _apply_check, _record, completed_names
from _detect_fixtures import check_run as _check
from _loader import load_script

ad = load_script("apply-detect")


def test_workset_is_the_graph_paths_whose_apply_check_is_present():
    # Membership is the head's apply checks, not one run's artifact names: a stack planned into
    # an artifact but carrying no check is not applied, and a check for another env is not this
    # env's work.
    names = {
        "apply / stacks/app / dev-eu",
        "apply / stacks/dns / dev-eu",
        "apply / stacks/platform / dev-us",
        "plan / stacks/platform / dev-eu",
    }
    graph_paths = ["stacks/app", "stacks/dns", "stacks/platform"]
    assert ad.paths_with_checks("dev-eu", graph_paths, names) == ["stacks/app", "stacks/dns"]


def test_workset_forward_constructs_the_name_and_never_parses_it():
    """Both paths must resolve. `components/app` contains the '/' that makes a split-on-'/'
    parse wrong, and `a / b` is ambiguous under any rsplit of `apply / a / b / dev-eu` -- is
    the stack "a" or "a / b"? Forward construction never has to decide, so neither path can be
    misresolved."""
    graph_paths = ["components/app", "a / b", "stacks/unplanned"]
    names = {"apply / components/app / dev-eu", "apply / a / b / dev-eu"}
    assert ad.paths_with_checks("dev-eu", graph_paths, names) == ["components/app", "a / b"]


def test_cells_take_workload_var_from_the_tags():
    """Never from the check name: the name carries no workload, and a cell that invented one
    from its path would assume the wrong environment role. A stack missing from the map carries
    "", because the map comes from a separate terramate query and must never be able to raise
    here."""
    cells = ad.cells_for_env(
        "dev-eu",
        ["stacks/app", "stacks/dns"],
        {"stacks/app": ["env/dev-eu", "workload/net-edge"], "stacks/dns": ["env/dev-eu"]},
    )
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload_var": "NET_EDGE"},
        {"stack": "stacks/dns", "environment": "dev-eu", "workload_var": ""},
    ]


_TWO_CELLS = [
    {"stack": "stacks/app", "environment": "dev-eu", "workload_var": ""},
    {"stack": "stacks/dns", "environment": "dev-eu", "workload_var": ""},
]


def test_each_cell_carries_the_plan_run_its_own_check_names():
    # The recovery shape: one cell re-planned by a later run while its sibling is still named
    # by the first. Each must apply from the run that planned it.
    out = ad.with_plan_runs(
        _TWO_CELLS,
        {"apply / stacks/app / dev-eu": "111", "apply / stacks/dns / dev-eu": "222"},
    )
    assert out == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload_var": "", "plan_run_id": "111"},
        {"stack": "stacks/dns", "environment": "dev-eu", "workload_var": "", "plan_run_id": "222"},
    ]


def test_a_cell_whose_check_names_no_plan_run_refuses():
    # Not skipped and not defaulted: falling back to a run lookup keyed on a plan run's
    # head_sha is the platform dependency this path exists to drop, and a silent default
    # applies a cell from nowhere.
    with pytest.raises(SystemExit) as exc_info:
        ad.with_plan_runs(_TWO_CELLS, {"apply / stacks/app / dev-eu": "111"})
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan run recorded for apply / stacks/dns / dev-eu — the "
        "apply check names no plan run to apply from — most likely a check written "
        "before this engine version, and post-merge possibly no apply check for that "
        "cell at all. Re-plan these stacks on their pull request, then apply again; if "
        "that pull request has already merged, a new pull request touching them plans "
        "and applies them afresh."
    )


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
    # "completed" status with a failing conclusion must not count as done: apply-detect shares
    # apply-gate's success/neutral predicate.
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    done = _completed(monkeypatch, [_check(conclusion="failure")])
    assert ad.filter_pending(cells, done) == cells


def test_foreign_app_completed_check_stays_pending(monkeypatch):
    # A completed+success check authored by another identity (github-actions, app id 15368)
    # must not count as done. The App id (999 here) reaches the predicate as an argument, not
    # by the query reading SHIPMATE_APP_ID itself.
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    done = _completed(monkeypatch, [_check(app={"id": 15368})])
    assert ad.filter_pending(cells, done) == cells


def test_check_runs_jsonl_parsing_reuses_apply_gates_parse_jsonl(monkeypatch):
    # No private json.loads-per-line loop: a malformed line raises SystemExit naming the
    # offending line, through the single shared implementation.
    with pytest.raises(SystemExit) as exc_info:
        _completed(monkeypatch, ['{"a": 1}', "not-json-garbage-{{{"])
    assert "not-json-garbage" in str(exc_info.value)


def test_dag_shape_notice_reports_a_flat_graph():
    # The migration shape: every stack independent, so the whole repository would apply at
    # once. Nothing can detect the missing edges, so this line is how a reader who knows the
    # repository notices.
    deps = {"stacks/a": set(), "stacks/b": set(), "stacks/c": set()}
    assert ad.dag_shape_notice(deps) == (
        "::notice::3 stacks, 0 after edges, 1 wave levels; 3 stacks would apply concurrently"
    )


def test_dag_shape_notice_reports_a_layered_graph():
    # `stacks/d` carries two edges on purpose: with one edge per dependent stack, an edge count
    # and a count of stacks-that-have-dependencies agree, and the figure a reader is asked to
    # judge is the edge count.
    deps = {
        "stacks/a": set(),
        "stacks/b": {"stacks/a"},
        "stacks/c": {"stacks/a"},
        "stacks/d": {"stacks/b", "stacks/c"},
    }
    assert ad.dag_shape_notice(deps) == (
        "::notice::4 stacks, 4 after edges, 3 wave levels; 2 stacks would apply concurrently"
    )


def _apply_env(monkeypatch, tmp_path, **overrides):
    """Env for an apply-mode main() run; returns the GITHUB_OUTPUT path."""
    out = tmp_path / "out.txt"
    env = {
        "GITHUB_REPOSITORY": "acme/iac",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_HEAD_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(out),
        "SHIPMATE_APP_ID": APP_ID,
        "SHIPMATE_REVIEW_DECISION": "APPROVED",
    }
    env.update(overrides)
    for name in ("SHIPMATE_UNGATED_ENVS", "SHIPMATE_MODE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return out


def _stub_apply(monkeypatch, deps, checks):
    """Stub the apply path's IO: the run-graph, the check-run listing and the per-stack tag
    query. Every terramate call must be stubbed, because CI installs uv alone -- a real
    invocation passes on a developer machine and fails there.

    Returns the list of `gh api` paths main() requested, so a re-added run lookup shows up as
    an extra entry rather than as a silent success."""
    urls = []

    def _run(args):
        assert args[0] == "gh", args
        urls.append(args[-1])
        return "\n".join(json.dumps(c) for c in checks)

    monkeypatch.setattr(ad, "run_graph_deps", lambda: deps)
    monkeypatch.setattr(ad.bm, "_run", _run)
    monkeypatch.setattr(ad.bm, "_tags", lambda stack: ["env/dev-eu", "workload/app"])
    return urls


def test_workset_never_resolves_a_slug_back_to_a_stack_path():
    """`a/b` and `a-b` slug identically, and only `a-b` carries an apply check, so only `a-b`
    is in the workset. A pull request adding `a-b` beside an unchanged `a/b` never shows the
    pair to build-matrix's plan-time collision guard, since `a/b` is not in `terramate list
    --changed`, so this is the whole protection: nothing here resolves a name back to a path."""
    assert ad.paths_with_checks("dev-eu", ["a/b", "a-b"], {"apply / a-b / dev-eu"}) == ["a-b"]


def test_apply_path_never_enrols_a_slug_alike_stack(monkeypatch, tmp_path):
    # The same property through the real entry point: a second construction that slugged its
    # way from a check name back to a path would enrol `a/b` here.
    out = _apply_env(monkeypatch, tmp_path)
    _stub_apply(monkeypatch, {"a/b": set(), "a-b": set()}, [_apply_check("a-b")])
    ad.main()
    assert [c["stack"] for c in json.loads(_parsed(out)["waves"])["wave0"]] == ["a-b"]


def test_apply_path_makes_no_run_lookup_at_all(monkeypatch, tmp_path):
    # No site keys an apply on a plan run's head_sha any more. Whole-list comparison against a
    # hand-written constant, so any added gh api call -- a run lookup, an artifact listing --
    # reddens here.
    out = _apply_env(monkeypatch, tmp_path)
    urls = _stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app")])
    ad.main()
    assert len(json.loads(_parsed(out)["waves"])["wave0"]) == 1  # Not vacuous: a cell exists.
    assert urls == [f"repos/acme/iac/commits/{'a' * 40}/check-runs?filter=all&per_page=100"]


def test_a_forged_completed_check_does_not_mark_a_cell_applied(monkeypatch, tmp_path):
    # A completed+success check of the same name from another identity (github-actions, app id
    # 15368) must not count the cell as applied. It is also the newer run of that name, so only
    # the App filter keeps the cell in.
    out = _apply_env(monkeypatch, tmp_path)
    _stub_apply(
        monkeypatch,
        {"stacks/app": set()},
        [
            _apply_check("stacks/app"),
            _check(name="apply / stacks/app / dev-eu", id=2, app={"id": 15368}),
        ],
    )
    ad.main()
    assert [c["stack"] for c in json.loads(_parsed(out)["waves"])["wave0"]] == ["stacks/app"]


def test_a_record_less_completed_check_does_not_block_the_rest(monkeypatch, tmp_path):
    """The upgrade shape: `stacks/dns` was applied by an older engine version, so its completed
    check carries a legacy bare-hex record naming no plan run. Only cells still to be applied
    need one -- refusing over an already-applied cell would strand every pull request open
    across the upgrade."""
    out = _apply_env(monkeypatch, tmp_path)
    _stub_apply(
        monkeypatch,
        {"stacks/app": set(), "stacks/dns": set()},
        [
            _apply_check("stacks/app", plan_run="42"),
            _check(name="apply / stacks/dns / dev-eu", external_id="b" * 64),
        ],
    )
    ad.main()
    assert [c["stack"] for c in json.loads(_parsed(out)["waves"])["wave0"]] == ["stacks/app"]


def test_a_failed_apply_check_stays_re_appliable(monkeypatch, tmp_path):
    """completed with a failing conclusion: done being run, but not applied. Such a cell is not
    "pending" and must still be in the workset -- membership by pending-ness alone would
    silently drop the one cell an operator is retrying."""
    out = _apply_env(monkeypatch, tmp_path)
    _stub_apply(
        monkeypatch,
        {"stacks/app": set()},
        [
            _check(
                name="apply / stacks/app / dev-eu", conclusion="failure", external_id=_record("42")
            )
        ],
    )
    ad.main()
    assert [c["stack"] for c in json.loads(_parsed(out)["waves"])["wave0"]] == ["stacks/app"]


def test_main_emits_the_dag_shape_notice(monkeypatch, tmp_path, capsys):
    # The line is worth nothing unprinted, so this executes the real entry point. An absent
    # decision refuses the run, and this test is about the notice.
    _apply_env(monkeypatch, tmp_path)
    _stub_apply(
        monkeypatch, {"stacks/a": set(), "stacks/b": {"stacks/a"}}, [_apply_check("stacks/a")]
    )
    ad.main()
    assert (
        "::notice::2 stacks, 1 after edges, 2 wave levels; 1 stacks would apply concurrently"
        in capsys.readouterr().out.splitlines()
    )


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
    ad.validate_head_sha("0123456789abcdef0123456789abcdef01234567")  # This must not raise.


def test_validate_env_rejects_dot():
    # A '.' in env would break plan.<env>.<slug> disambiguation, which works env-first only
    # because env has no '.'. Fail loud at the trust boundary.
    with pytest.raises(SystemExit):
        ad.validate_env("dev.eu")


def test_validate_env_rejects_empty():
    # An empty env reads as a bare apply inside _review_reason, which exempts it whenever
    # SHIPMATE_UNGATED_ENVS names anything -- a bypassed refusal on a gate path. This workflow
    # has no bare form.
    with pytest.raises(SystemExit):
        ad.validate_env("")


def test_validate_env_accepts_normal():
    ad.validate_env("dev-eu")  # A hyphenated env is fine.
    ad.validate_env("eu")  # This must not raise.


def test_duplicate_run_newer_queued_stays_pending():
    # An old completed+success run must not mask a newer queued run of the same check name:
    # the latest run per name governs.
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
    """Two claims at once: the map reaches the cells, and it is derived for the workset alone.
    Fails when the map never reaches the cells: every cell is then role-less and the suite stays
    green. Evaluating `stacks/unrelated` would let a stack this apply never touches block an
    approved plan."""
    out = tmp_path / "out"
    out = _apply_env(monkeypatch, tmp_path)
    _stub_apply(
        monkeypatch,
        {"stacks/app": set(), "stacks/unrelated": set()},
        [_apply_check("stacks/app", plan_run="42")],
    )
    evaluated = []
    monkeypatch.setattr(
        ad.bm,
        "_tags",
        lambda stack: evaluated.append(stack) or ["env/dev-eu", "workload/net-edge"],
    )
    ad.main()
    parsed = dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())
    assert json.loads(parsed["waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload_var": "NET_EDGE",
            "plan_run_id": "42",
        }
    ]
    assert evaluated == ["stacks/app"]


# The engine reads vars.SHIPMATE_UNGATED_ENVS itself and refuses on it, because an
# `ungated-envs` input wider than the variable would otherwise apply unreviewed on the
# targeted path unconditionally.
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
        # The hole: an unreviewed pull request with the variable unset. The input the
        # consumer wired into comment-ops cannot widen this.
        ("REVIEW_REQUIRED", ""),
        # Listed, but not this env.
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
    # One function decides review policy on all three call sites, and a second implementation
    # here would eventually disagree with comment-ops about the same pull request. The reason
    # text is compared whole, not paraphrased.
    reason = ad.az._review_reason("REVIEW_REQUIRED", "dev-eu", frozenset({"prod-eu"}))
    with pytest.raises(SystemExit) as exc_info:
        ad.refuse_unreviewed("dev-eu", "prod-eu", "REVIEW_REQUIRED")
    assert str(exc_info.value) == f"::error::{reason}"


def test_refuse_unreviewed_rejects_a_malformed_variable_entry():
    # parse_ungated_envs fails closed on an entry that could never match an env name: a
    # silently inert entry leaves an operator believing an environment is exempt when it is
    # not.
    with pytest.raises(SystemExit):
        ad.refuse_unreviewed("dev-eu", "dev-eu-apply", "REVIEW_REQUIRED")


def _boom_on_the_workset(monkeypatch):
    """Every call the workset needs fails loudly: the run must die before any API call and
    before any terramate call."""

    def _boom(*a, **kw):
        raise AssertionError("main() built its workset on an unauthorized apply")

    for name in ("run_graph_deps", "_check_run_lines"):
        monkeypatch.setattr(ad, name, _boom)


def test_main_refuses_before_it_reads_any_check(monkeypatch, tmp_path):
    # The refusal is the first thing after input validation: the run dies before any API call
    # and before any wave, so the apply checks -- and with them the gate -- stay pending.
    _apply_env(monkeypatch, tmp_path, SHIPMATE_REVIEW_DECISION="REVIEW_REQUIRED")
    _boom_on_the_workset(monkeypatch)
    with pytest.raises(SystemExit) as exc_info:
        ad.main()
    assert str(exc_info.value).startswith("::error::not authorized")


def test_main_refuses_when_the_decision_variable_is_absent(monkeypatch, tmp_path):
    """The default in `os.environ.get("SHIPMATE_REVIEW_DECISION", "")` is the whole fail-closed
    behaviour when the review job's output never reaches the action. Every other main() test
    sets the variable, so without this case the default could be flipped to "APPROVED"
    unnoticed."""
    _apply_env(monkeypatch, tmp_path)
    monkeypatch.delenv("SHIPMATE_REVIEW_DECISION")
    _boom_on_the_workset(monkeypatch)
    with pytest.raises(SystemExit) as exc_info:
        ad.main()
    assert str(exc_info.value).startswith("::error::not authorized")


def _unlock_env(monkeypatch, tmp_path, **overrides):
    """Env for a main() run, unlock unless `SHIPMATE_MODE` is overridden. Returns the
    GITHUB_OUTPUT path.

    `shipmate unlock <env>` applies no plan and consumes no plan artifact: a lock outlives the
    run that stranded it, so those artifacts may be long expired."""
    out = tmp_path / "out"
    env = {
        "GITHUB_REPOSITORY": "acme/iac",
        "SHIPMATE_ENV": "dev-eu",
        "SHIPMATE_HEAD_SHA": "a" * 40,
        "SHIPMATE_MODE": "unlock",
        "GITHUB_OUTPUT": str(out),
    }
    env.update(overrides)
    for name in ("SHIPMATE_UNGATED_ENVS", "SHIPMATE_REVIEW_DECISION"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return out


def _boom_on_plan_path(monkeypatch):
    """Every apply-workset call fails loudly: unlock must reach none of them.

    `_check_run_lines` is deliberately not boomed, because the unlock queue reads the same
    listing through `pending_apply_names`. What unlock must never reach is the workset built
    from it, and the plan run each cell would apply from."""

    def _boom(*a, **kw):
        raise AssertionError("unlock mode reached the apply workset")

    for name in ("run_graph_deps", "paths_with_checks", "cells_for_env", "with_plan_runs"):
        monkeypatch.setattr(ad, name, _boom)


_DEV_EU_PENDING_CHECKS = [
    _check(name=f"apply / {stack} / dev-eu", status="in_progress", conclusion=None)
    for stack in ("stacks/app", "stacks/dns", "stacks/db")
]


def _stub_unlock_tree(monkeypatch, cells, checks=None):
    """Stub the tag walk and the check-run listing; returns the kwargs `env_membership` was
    called with.

    Only the walk is stubbed. The real `build_matrix` turns its output into cells, so the
    matrix-limit and reserved-path guards it carries stay on the unlock path instead of being
    stubbed out of it.

    `checks` are stubbed as the raw JSONL `gh` emits, not as a set of names, so the queue's
    membership rule itself is under test rather than assumed: a construction that asks the
    wrong question of the same listing reddens here. Default: every dev-eu cell has a pending
    check."""
    seen = {}

    def _membership(all_stacks=False, base="", require_env_tag=True):
        seen.update(all_stacks=all_stacks, base=base, require_env_tag=require_env_tag)
        stacks_by_env, tags_by_stack = {}, {}
        for c in cells:
            stacks_by_env.setdefault(c["environment"], []).append(c["stack"])
            tags = tags_by_stack.setdefault(c["stack"], [])
            for tag in (f"env/{c['environment']}", f"workload/{c['workload']}"):
                if tag not in tags:
                    tags.append(tag)
        return stacks_by_env, tags_by_stack

    runs = _DEV_EU_PENDING_CHECKS if checks is None else checks
    monkeypatch.setattr(ad.bm, "_run", lambda args: "\n".join(json.dumps(r) for r in runs))
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)
    monkeypatch.setattr(ad.bm, "env_membership", _membership)
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
    """stacks/app has a pending check and is queued; stacks/dns has a completed check and is
    not; stacks/db has no check at all and is not queued either. Every queued cell takes a real
    state lock and may force-break one, so a stack this pull request never planned must not be
    in range. The foreign-App pending check on stacks/db must not enrol it."""
    out = _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    seen = _stub_unlock_tree(
        monkeypatch,
        _DEV_EU_CELLS,
        [
            _check(name="apply / stacks/app / dev-eu", status="in_progress", conclusion=None),
            _check(name="apply / stacks/dns / dev-eu"),
            _check(
                name="apply / stacks/db / dev-eu",
                status="in_progress",
                conclusion=None,
                app={"id": 15368},
            ),
        ],
    )
    ad.main()
    # all_stacks=True is the point: a cell whose plan artifacts expired long ago is exactly the
    # cell that can hold a stranded lock.
    assert seen == {"all_stacks": True, "base": "", "require_env_tag": False}
    assert json.loads(_parsed(out)["cells"]) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app", "workload_var": "APP"},
    ]
    assert _parsed(out)["empty"] == "false"


def test_unlock_empty_queue_warns_that_nothing_was_probed(monkeypatch, tmp_path, capsys):
    # An empty queue is legitimate, and since the queue narrowed to the cells that have a
    # pending check it is the normal outcome for the case the runbook names. It must not be a
    # silent green run.
    out = _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS, [_check(name="apply / stacks/app / dev-eu")])
    ad.main()
    assert _parsed(out)["empty"] == "true"
    assert json.loads(_parsed(out)["cells"]) == []
    assert (
        "::warning::no cell in dev-eu has a pending apply check, so no lock was "
        "probed; a lock on a cell whose check already completed, or on a stack "
        "applied out of band, is released out of band — see the state-lock "
        "section of docs/troubleshooting.md." in capsys.readouterr().out.splitlines()
    )


def test_unlock_non_empty_queue_does_not_warn(monkeypatch, tmp_path, capsys):
    # The other half: the warning is about an empty queue, not decoration on every unlock run.
    _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS)
    ad.main()
    out = capsys.readouterr().out
    assert "cells=3 pending=3" in out  # Not vacuous: there is a queue.
    assert "::warning::" not in out


def test_unlock_is_not_capped_by_the_whole_tree_matrix_limit(monkeypatch, tmp_path):
    """build_matrix refuses a cell set above the GHA matrix limit, and over a whole-tree walk
    that ceiling counts every stack x every environment. Built for all envs and filtered
    afterwards, a repository past the limit could never unlock any environment however short
    its queue, and the refusal would tell the operator to split a pull request that does not
    exist. Only the target env's cells are built, so the ceiling bounds what the matrix holds."""
    out = _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    _stub_unlock_tree(
        monkeypatch,
        [
            {
                "stack": "stacks/app",
                "environment": f"env-{i}",
                "workload": "app",
                "workload_var": "APP",
            }
            for i in range(ad.bm.MATRIX_LIMIT + 10)
        ]
        + [_DEV_EU_CELLS[0]],
    )
    ad.main()
    assert json.loads(_parsed(out)["cells"]) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app", "workload_var": "APP"}
    ]


def test_unlock_emits_no_wave_array_with_any_member(monkeypatch, tmp_path):
    # The guard against a fall-through into the apply matrix: a mode confusion that reaches the
    # wave assignment turns an unlock into an apply.
    out = _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    _stub_unlock_tree(monkeypatch, _DEV_EU_CELLS)
    ad.main()
    parsed = _parsed(out)
    assert len(json.loads(parsed["cells"])) == 3  # Not vacuous: there is a queue.
    wave_keys = [k for k in parsed if k == "waves" or k.startswith("wave")]
    assert wave_keys == []


def test_unlock_does_not_refuse_an_unreviewed_pr(monkeypatch, tmp_path):
    # An approval reviews a diff and unlock applies none, and `scripts/authorize` makes the
    # same call at comment time. The apply-mode half of this divergence is
    # test_unlock_absent_mode_takes_the_stricter_apply_path.
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
    # Same review_decision="" the unlock test accepts. Anything that is not exactly "unlock"
    # must refuse, so an absent or garbled mode fails closed.
    _unlock_env(monkeypatch, tmp_path, SHIPMATE_MODE=mode)
    with pytest.raises(SystemExit) as exc_info:
        ad.main()
    assert str(exc_info.value).startswith("::error::not authorized")


def test_unlock_notice_names_the_mode(monkeypatch, tmp_path, capsys):
    _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    _stub_unlock_tree(
        monkeypatch,
        _DEV_EU_CELLS,
        [
            _check(name="apply / stacks/app / dev-eu", status="in_progress", conclusion=None),
            _check(name="apply / stacks/dns / dev-eu"),
            _check(name="apply / stacks/db / dev-eu", status="queued", conclusion=None),
        ],
    )
    ad.main()
    assert (
        f"::notice title=apply-detect::mode=unlock env=dev-eu head={'a' * 40} "
        "cells=3 pending=2" in capsys.readouterr().out.splitlines()
    )


def test_apply_mode_writes_the_whole_output_file_verbatim(monkeypatch, tmp_path):
    """`cells` is written in both modes and is never an empty string: the unlock job's
    strategy.matrix.include is fromJSON(cells), and fromJSON('') errors. Whole-file comparison
    against a hand-written constant, so an added, dropped or reordered key on the apply path is
    caught here too."""
    out = _apply_env(monkeypatch, tmp_path)
    _stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app", plan_run="42")])
    ad.main()
    assert out.read_text(encoding="utf-8") == (
        'waves={"wave0": [{"stack": "stacks/app", "environment": "dev-eu", '
        '"workload_var": "APP", "plan_run_id": "42"}], "wave1": [], "wave2": [], '
        '"wave3": [], "wave4": [], "wave5": [], "wave6": [], "wave7": []}\n'
        "empty=false\n"
        "cells=[]\n"
        "head_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
    )


def test_unlock_tolerates_an_untagged_stack_elsewhere_in_the_tree(monkeypatch, tmp_path):
    # Through the real env_membership: require_env_tag=True would abort on `stacks/orphan` and
    # make unlock unavailable for every environment, precisely when the pipeline is already
    # degraded enough to strand a lock.
    out = _unlock_env(monkeypatch, tmp_path)
    _boom_on_plan_path(monkeypatch)
    monkeypatch.setattr(
        ad, "pending_apply_names", lambda repo, head: {"apply / stacks/app / dev-eu"}
    )
    monkeypatch.setattr(
        ad.bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/orphan"]
    )
    monkeypatch.setattr(
        ad.bm, "_tags", lambda s: ["env/dev-eu", "workload/app"] if s == "stacks/app" else []
    )
    ad.main()
    assert json.loads(_parsed(out)["cells"]) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app", "workload_var": "APP"}
    ]
