import json

import pytest
from _detect_fixtures import (
    APP_ID,
    MINIMAL_TABLE,
    PLAN_SHA,
    _apply_check,
    _record,
    check_run,
    spy_env_config,
    stub_read_table,
)
from _loader import github_outputs, load_script

aad = load_script("apply-detect")

HEAD = "a" * 40
CHECK_RUNS_URL = f"repos/o/r/commits/{HEAD}/check-runs?filter=all&per_page=100"


def test_cells_are_the_tree_candidates_whose_check_is_on_the_head_in_every_env():
    # Membership is the head's apply checks across all envs, not one plan run's artifact names:
    # `stacks/platform` is in the dev-eu tree but carries only a plan check, and `stacks/app` is
    # work in two envs.
    names = {
        "apply / stacks/app / dev-eu",
        "apply / stacks/dns / dev-eu",
        "apply / stacks/app / dev-us",
        "plan / stacks/platform / dev-eu",
    }
    stacks_by_env = {
        "dev-eu": ["stacks/app", "stacks/dns", "stacks/platform"],
        "dev-us": ["stacks/app"],
    }
    cells = aad.cells_from_checks(names, stacks_by_env, {})
    assert sorted((c["environment"], c["stack"]) for c in cells) == [
        ("dev-eu", "stacks/app"),
        ("dev-eu", "stacks/dns"),
        ("dev-us", "stacks/app"),
    ]


def test_cells_forward_construct_the_check_name_and_never_parse_it():
    # `components/app` contains the '/' that makes a split-on-'/' parse wrong, and `a / b` is
    # ambiguous under any rsplit of `apply / a / b / dev-eu` -- is the stack "a" or "a / b"?
    # Forward construction never has to decide.
    stacks_by_env = {"dev-eu": ["components/app", "a / b", "stacks/unplanned"]}
    names = {"apply / components/app / dev-eu", "apply / a / b / dev-eu"}
    cells = aad.cells_from_checks(names, stacks_by_env, {})
    assert [c["stack"] for c in cells] == ["components/app", "a / b"]


def test_cells_take_the_workload_from_the_tags():
    # Never from the check name, which carries no workload. A stack missing from the map carries
    # "" and applies with the environment's generic role.
    cells = aad.cells_from_checks(
        {
            "apply / stacks/app / dev-eu",
            "apply / stacks/dns / dev-eu",
            "apply / stacks/app / dev-us",
        },
        {"dev-eu": ["stacks/app", "stacks/dns"], "dev-us": ["stacks/app"]},
        {"stacks/app": ["workload/net-edge"]},  # stacks/dns is absent, so its workload is "".
    )
    assert cells == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "net-edge",
        },
        {"stack": "stacks/dns", "environment": "dev-eu", "workload": ""},
        {
            "stack": "stacks/app",
            "environment": "dev-us",
            "workload": "net-edge",
        },
    ]


def test_env_without_a_check_contributes_nothing():
    cells = aad.cells_from_checks(
        {"apply / stacks/app / dev-eu"}, {"dev-eu": ["stacks/app"], "prod": ["stacks/app"]}, {}
    )
    assert all(c["environment"] == "dev-eu" for c in cells)


def test_slug_alike_paths_never_enrol_each_other():
    """`a/b` and `a-b` slug identically, and both are in the tree. Only `a-b` carries an apply
    check, so only `a-b` is work. A pull request adding `a-b` beside an unchanged `a/b` never
    shows the pair to build-matrix's plan-time collision guard, so nothing resolving a name back
    to a path is the whole protection: there is no slug to collide over any more."""
    cells = aad.cells_from_checks({"apply / a-b / dev-eu"}, {"dev-eu": ["a/b", "a-b"]}, {})
    assert [c["stack"] for c in cells] == ["a-b"]


def test_a_forged_completed_check_does_not_mark_a_cell_applied(tmp_path, monkeypatch):
    """A completed+success check of the same name from another identity (github-actions, app id
    15368) must not count the cell as applied. It is also the newer run of that name, so only
    the App filter keeps the cell in, and bare_main() is what feeds that filter its app id -- so
    this is the behavioural pin on the threading test_detect_app_scoping only sees
    structurally."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        checks=[
            _apply_check("stacks/app", "dev-eu"),
            check_run(name="apply / stacks/app / dev-eu", id=2, app={"id": 15368}),
        ],
    )
    assert [c["stack"] for c in _wave_cells(parsed)] == ["stacks/app"]


def test_membership_is_read_over_the_whole_tree_with_no_other_keyword(tmp_path, monkeypatch):
    """Mutations: leave `require_env_tag=False` at the `env_membership` call -- TypeError;
    pass `all_stacks=False`."""
    membership = []
    _run_main(tmp_path, monkeypatch, envs=["dev-eu"], decision="APPROVED", membership=membership)
    assert membership == [((), {"all_stacks": True})]


def test_reuses_single_sourced_helpers():
    """The `is not None` lines pin only that the bare form's helpers still exist under these
    names; the behavioural bare_main() tests above pin that it calls them. Env-level bucketing
    and the GITHUB_OUTPUT writer live in waves, shared with deploy-detect, so this module never
    loads deploy-detect.

    Mutation: add a module-level `dd = None` to apply-detect -- the `dd` line reddens."""
    assert aad.paths_with_checks is not None
    assert aad.cells_for_env is not None
    assert aad.with_plan_runs is not None
    assert aad.wv.env_level_waves is not None
    assert aad.wv.write_env_level_waves is not None
    assert not hasattr(aad, "dd")
    assert not hasattr(aad, "cells_from_artifacts")


ALL_PENDING = ["dev-eu", "dev-us", "prod-eu"]


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("NONE", []),
        ("APPROVED", []),
        ("REVIEW_REQUIRED", ["prod-eu"]),
        ("CHANGES_REQUESTED", ALL_PENDING),
        ("", ALL_PENDING),
        (None, ALL_PENDING),
        ("BANANA", ALL_PENDING),
        # The review job's sentinel for a pr_number matching no pull request. GraphQL returns
        # a null pullRequest with no errors, so the query succeeds and only this value keeps
        # the run from applying everything.
        ("MISSING_PR", ALL_PENDING),
    ],
)
def test_main_holds_per_the_review_decision(tmp_path, monkeypatch, decision, expected):
    """`None` is the unset variable. Mutations: drop `ungated` from the `_review_reason` call
    -- REVIEW_REQUIRED holds dev-eu and dev-us too; `if not az._review_reason(...)` --
    every row inverts; `sorted(..., reverse=True)` -- the multi-env rows reorder."""
    parsed = _run_main(
        tmp_path, monkeypatch, envs=ALL_PENDING, ungated="dev-eu,dev-us", decision=decision
    )
    assert json.loads(parsed["review_held_envs"]) == expected


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("NONE", []),
        ("APPROVED", []),
        # With nothing exempted, an unreviewed pull request holds every env. An empty list
        # short-circuiting to "hold nothing" is what lets an `ungated-envs` action input wider
        # than the repository variable apply every pending environment unreviewed.
        ("REVIEW_REQUIRED", ALL_PENDING),
        ("CHANGES_REQUESTED", ALL_PENDING),
        ("", ALL_PENDING),
        (None, ALL_PENDING),
        ("BANANA", ALL_PENDING),
        ("MISSING_PR", ALL_PENDING),
    ],
)
def test_main_holds_everything_unreviewed_when_no_env_is_ungated(
    tmp_path, monkeypatch, decision, expected
):
    """Mutations: `if not az._review_reason(...)` -- every row inverts;
    `sorted(..., reverse=True)` -- the multi-env rows reorder."""
    parsed = _run_main(tmp_path, monkeypatch, envs=ALL_PENDING, decision=decision)
    assert json.loads(parsed["review_held_envs"]) == expected


def _membership_double(tree, tags, calls):
    """`env_membership` returning `(tree, tags)`, appending each call's `(args, kwargs)` to
    `calls` when one is given. The real signature, so a keyword `env_membership` no longer
    takes raises here as it would on a runner."""

    def _membership(all_stacks=False, base="", check_names=True):
        return tree, tags

    def _record(*args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        return _membership(*args, **kwargs)

    return _record


def _run_main(
    tmp_path,
    monkeypatch,
    *,
    envs,
    order=None,
    explicit=(),
    ungated=None,
    decision=None,
    checks=None,
    tree=None,
    tags=None,
    urls=None,
    table=None,
    reads=None,
    membership=None,
    deps=None,
):
    """main() with an empty SHIPMATE_ENV, which is bare_main(), over the head's apply checks,
    with everything the script reaches from GitHub or Terramate stubbed. Defaults to one
    pending `stacks/app` check per env in `envs`. Returns parsed GITHUB_OUTPUT, and appends
    each `gh api` path requested to `urls` when one is given.

    `order`, `explicit` and `ungated` are folded into the stubbed table rather than stubbed
    on `wv`: all three are fields of the mapping this path loads, so a double on any reader
    would mask a caller that stopped passing the table. One entry is appended to `reads` per
    `read_table` call. Each `env_membership` call appends its `(args, kwargs)` to `membership`
    when one is given. `deps` replaces the run-graph, which defaults to the tree's stacks with
    no edges."""
    out = tmp_path / "out"
    for k, v in {
        "GITHUB_REPOSITORY": "o/r",
        "SHIPMATE_ENV": "",
        "SHIPMATE_HEAD_SHA": HEAD,
        "GITHUB_OUTPUT": str(out),
        "SHIPMATE_APP_ID": APP_ID,
    }.items():
        monkeypatch.setenv(k, v)
    if decision is None:
        monkeypatch.delenv("SHIPMATE_REVIEW_DECISION", raising=False)
    else:
        monkeypatch.setenv("SHIPMATE_REVIEW_DECISION", decision)
    if ungated is not None:
        gated = dict(table or MINIMAL_TABLE)
        entries = {e: dict(v) for e, v in dict(gated.get("environments", {})).items()}
        for env in ungated.split(","):
            entries.setdefault(env, {})["gated"] = False
        gated["environments"] = entries
        table = gated
    if checks is None:
        checks = [_apply_check("stacks/app", e) for e in envs]
    jsonl = "\n".join(json.dumps(c) for c in checks)

    def _run(args):
        # Every terramate call must be stubbed, because CI installs uv alone: a real
        # invocation passes on a developer machine and fails there.
        assert args[0] == "gh", args
        if urls is not None:
            urls.append(args[-1])
        return jsonl

    tree = tree or {e: ["stacks/app"] for e in envs}
    deps = deps or {p: set() for ps in tree.values() for p in ps}
    monkeypatch.setattr(aad, "run_graph_deps", lambda: deps)
    monkeypatch.setattr(aad, "_run", _run)
    monkeypatch.setattr(aad.bm, "_run", _run)
    monkeypatch.setattr(
        aad.bm, "env_membership", _membership_double(tree, tags or {"stacks/app": []}, membership)
    )
    stub_read_table(monkeypatch, (aad.bm.ec,), table, order, explicit, reads)
    aad.main()
    return github_outputs(out)


def _wave_cells(parsed):
    return [
        c
        for lvl in range(aad.wv.MAX_ENV_LEVELS)
        for w in [json.loads(parsed[f"envlevel{lvl}_waves"])]
        for i in range(aad.wv.MAX_WAVES)
        for c in w[f"wave{i}"]
    ]


def _wave_envs(parsed):
    return sorted(c["environment"] for c in _wave_cells(parsed))


def test_main_wires_the_tag_map_into_the_cells(tmp_path, monkeypatch):
    # Without the map every cell is role-less and the suite stays green.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        checks=[_apply_check("stacks/app", "dev-eu", plan_run="42")],
        tags={"stacks/app": ["env/dev-eu", "workload/net-edge"]},
    )
    assert json.loads(parsed["envlevel0_waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "net-edge",
            "role_arn": "",
            "cred_region": "",
            "tf_vars": {},
            "config_path": "apply",
            "env_binding": "dev-eu-apply",
            "plan_run_id": "42",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_main_passes_the_whole_tree_workload_map(tmp_path, monkeypatch):
    """Mutation: pass `set(stacks_by_env)` at `bare_main`'s `env_config` call -- the spy records
    the environment set."""
    seen = spy_env_config(monkeypatch, aad.bm)
    _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        tree={"dev-eu": ["stacks/app"], "dev-us": ["stacks/web"]},
        tags={"stacks/app": ["env/dev-eu", "workload/net-edge"], "stacks/web": ["env/dev-us"]},
    )
    assert seen == [{"dev-eu": frozenset({"net-edge"}), "dev-us": frozenset()}]


def test_main_reads_the_head_listing_and_makes_no_artifact_lookup(tmp_path, monkeypatch):
    """No site keys a bare apply on a plan run's artifacts any more. Whole-list comparison
    against a hand-written constant, so any added gh api call -- an artifact listing, a
    workflow-runs lookup, a second read of this same listing -- reddens here. One entry: the
    workset, the done predicate and the plan runs are all read off a single fetch, so nothing
    can disagree about a check that changed mid-run."""
    urls = []
    parsed = _run_main(tmp_path, monkeypatch, envs=["dev-eu"], decision="APPROVED", urls=urls)
    assert len(_wave_cells(parsed)) == 1  # Not vacuous: a cell exists.
    assert urls == [CHECK_RUNS_URL]


def test_main_never_enrols_a_slug_alike_stack(tmp_path, monkeypatch):
    # The slug property through the real entry point: `a/b` and `a-b` slug identically, both
    # are in the dev-eu tree, and only `a-b` has a check.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        tree={"dev-eu": ["a/b", "a-b"]},
        tags={"a/b": [], "a-b": []},
        checks=[_apply_check("a-b", "dev-eu")],
    )
    assert [c["stack"] for c in _wave_cells(parsed)] == ["a-b"]


def test_main_gives_each_cell_the_plan_run_its_own_check_names(tmp_path, monkeypatch):
    # The recovery shape, across envs: dev-us was re-planned by a later run while dev-eu is
    # still named by the first. Each must apply from the run that planned it, so one shared id
    # is not enough.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us"],
        decision="APPROVED",
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="111"),
            _apply_check("stacks/app", "dev-us", plan_run="222"),
        ],
    )
    assert sorted((c["environment"], c["plan_run_id"]) for c in _wave_cells(parsed)) == [
        ("dev-eu", "111"),
        ("dev-us", "222"),
    ]


def test_main_refuses_a_cell_whose_check_records_no_plan_text_digest(tmp_path, monkeypatch):
    """Through this detect's own entry point: the digest reaches `with_plan_runs` from this
    call site, so a call site that stopped passing it -- or passed the wrong mapping -- would
    apply dev-us against a plan text nobody bound. The cell HAS a usable plan run, so the
    missing-run refusal cannot absorb this one."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            envs=["dev-eu", "dev-us"],
            decision="APPROVED",
            checks=[
                _apply_check("stacks/app", "dev-eu", plan_run="42"),
                _apply_check("stacks/app", "dev-us", plan_run="43", plan_sha256=None),
            ],
        )
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan-text digest recorded for apply / stacks/app / dev-us: "
        "the reviewed plan text cannot be checked against the plan that would be applied, so "
        "this apply is refused rather than run unverified. Re-plan these stacks on their pull "
        "request, then apply again; if that pull request has already merged, a new pull "
        "request touching them plans and applies them afresh."
    )


def test_main_refuses_a_cell_whose_check_records_no_plan_run(tmp_path, monkeypatch):
    # Not skipped and not defaulted: a legacy bare-hex record names no run, and a silent
    # default would apply that cell from nowhere. The refusal names the cell so an operator
    # knows what to re-plan.
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            envs=["dev-eu", "dev-us"],
            decision="APPROVED",
            checks=[
                _apply_check("stacks/app", "dev-eu", plan_run="42"),
                check_run(
                    name="apply / stacks/app / dev-us",
                    status="queued",
                    conclusion=None,
                    external_id="b" * 64,
                ),
            ],
        )
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan run recorded for apply / stacks/app / dev-us: the "
        "apply check names no plan run to apply from (post-merge, the cell may have no apply "
        "check at all), so the apply is refused. Re-plan these stacks on their pull request, "
        "then apply again; if that pull request has already merged, a new pull request "
        "touching them plans and applies them afresh."
    )


def test_main_lets_a_record_less_completed_check_through(tmp_path, monkeypatch):
    """dev-us is applied and its completed check carries a bare-hex record. Only cells still to
    be applied need a plan run, so the attachment runs after the pending filter."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us"],
        decision="APPROVED",
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="42"),
            check_run(name="apply / stacks/app / dev-us", external_id="b" * 64),
        ],
    )
    assert _wave_envs(parsed) == ["dev-eu"]


def test_main_lets_a_record_less_check_in_an_excluded_env_through(tmp_path, monkeypatch):
    # prod-eu is explicit, so its check deliberately stays pending for a later targeted
    # `shipmate apply prod-eu`, which refuses there if the record is still missing. Refusing
    # here would strand every env that can apply.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        explicit=["prod-eu"],
        decision="APPROVED",
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="42"),
            check_run(
                name="apply / stacks/app / prod-eu",
                status="queued",
                conclusion=None,
                external_id="b" * 64,
            ),
        ],
    )
    assert _wave_envs(parsed) == ["dev-eu"]
    assert json.loads(parsed["excluded_envs"]) == ["prod-eu"]


def test_main_keeps_a_failed_apply_check_re_appliable(tmp_path, monkeypatch):
    # completed with a failing conclusion: done being run, but not applied. Such a cell is not
    # pending, so membership by pending-ness alone would silently drop the one cell an operator
    # is retrying.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        checks=[
            check_run(
                name="apply / stacks/app / dev-eu",
                conclusion="failure",
                external_id=_record("42"),
            )
        ],
    )
    assert [c["stack"] for c in _wave_cells(parsed)] == ["stacks/app"]


def test_main_holds_unlisted_envs_and_skips_their_successors(tmp_path, monkeypatch):
    """dev-us is ordered after prod-eu, which is held for review rather than explicit, so
    dev-us is skipped: a held env stops its successors exactly like an explicit one.

    Mutation: `stopped = pending_envs & set(explicit)` -- dev-us leaves `skipped_envs` and
    applies, red."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us", "prod-eu"],
        order={"dev-us": ["prod-eu"]},
        ungated="dev-eu,dev-us",
        decision="REVIEW_REQUIRED",
    )
    assert _wave_envs(parsed) == ["dev-eu"]
    assert json.loads(parsed["review_held_envs"]) == ["prod-eu"]
    assert json.loads(parsed["applied_ungated_envs"]) == ["dev-eu"]
    assert json.loads(parsed["excluded_envs"]) == []
    assert json.loads(parsed["skipped_envs"]) == ["dev-us"]


def test_main_skips_only_the_successors_of_a_pending_explicit_env(tmp_path, monkeypatch):
    """stage-eu is explicit with pending work, so prod-eu, ordered after it, is skipped.
    sbx-eu is explicit with no pending cells, so it is already applied and after-sbx, ordered
    after it, applies.

    Mutation: `stopped = pending_envs & set(held)` -- prod-eu applies, red.
    Mutation: `stopped = set(explicit + held)` -- after-sbx is skipped, red."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["after-sbx", "dev-eu", "prod-eu", "stage-eu"],
        order={"prod-eu": ["stage-eu"], "after-sbx": ["sbx-eu"]},
        explicit=["sbx-eu", "stage-eu"],
        decision="APPROVED",
    )
    assert json.loads(parsed["excluded_envs"]) == ["stage-eu"]
    assert json.loads(parsed["skipped_envs"]) == ["prod-eu"]
    assert _wave_envs(parsed) == ["after-sbx", "dev-eu"]


def test_main_reports_a_held_explicit_env_as_excluded_too(tmp_path, monkeypatch):
    """prod-eu is explicit and held, so it is in both outputs and the comment can name its
    targeted command under the hold; dev-eu is held and not explicit, so it is held only.

    Mutation: subtract `held` from `excluded` -- prod-eu leaves `excluded_envs`, red.
    Mutation: take `excluded` from `sorted(stopped)`, the explicit and held envs -- dev-eu
    enters `excluded_envs`, red."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        explicit=["prod-eu"],
        decision="REVIEW_REQUIRED",
    )
    assert json.loads(parsed["excluded_envs"]) == ["prod-eu"]
    assert json.loads(parsed["review_held_envs"]) == ["dev-eu", "prod-eu"]


def test_main_takes_the_exemption_from_the_entry_flag(tmp_path, monkeypatch):
    """The hold and the applied-ungated report both resolve from `gated: false` on the
    default branch's entry, which is the only source: an environment without it is held.

    Mutation: resolve `ungated` from the process environment -- nothing sets it, so every
    environment is held and the applied report goes empty."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        table={"layout": "folder", "environments": {"dev-eu": {"gated": False}}},
        decision="REVIEW_REQUIRED",
    )
    assert _wave_envs(parsed) == ["dev-eu"]
    assert json.loads(parsed["review_held_envs"]) == ["prod-eu"]
    assert json.loads(parsed["applied_ungated_envs"]) == ["dev-eu"]


@pytest.mark.parametrize("written", ["true", '"true"'], ids=["bare", "quoted"])
def test_an_explicit_flag_holds_the_env_back_however_it_is_quoted(tmp_path, monkeypatch, written):
    """Every scalar in the file is a string, so a bare `true` and a quoted one parse alike and
    both must keep the environment off a bare apply. The table goes through the real parser.

    Mutation: delete the flag normalisation in `load_config` -- `explicit_envs` then sees the
    string `"true"`, and prod applies with dev-eu."""
    text = f"layout: folder\nenvironments:\n  prod:\n    explicit: {written}\n"
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod"],
        table=aad.bm.ec.parse_table(text, {}),
        decision="APPROVED",
    )
    assert _wave_envs(parsed) == ["dev-eu"]
    assert json.loads(parsed["excluded_envs"]) == ["prod"]


def test_main_reports_every_env_applied_when_all_of_them_are_listed(tmp_path, monkeypatch):
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        ungated="dev-eu,prod-eu",
        decision="REVIEW_REQUIRED",
    )
    assert _wave_envs(parsed) == ["dev-eu", "prod-eu"]
    assert json.loads(parsed["review_held_envs"]) == []
    assert json.loads(parsed["applied_ungated_envs"]) == ["dev-eu", "prod-eu"]


def test_main_omits_a_listed_explicit_env_from_the_applied_report(tmp_path, monkeypatch):
    # dev-us is ungated but explicit, so it never ran and the comment must not claim it
    # applied.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us"],
        explicit=["dev-us"],
        ungated="dev-eu,dev-us",
        decision="REVIEW_REQUIRED",
    )
    assert json.loads(parsed["excluded_envs"]) == ["dev-us"]
    assert json.loads(parsed["applied_ungated_envs"]) == ["dev-eu"]
    assert _wave_envs(parsed) == ["dev-eu"]


@pytest.mark.parametrize(("decision", "expected"), [("NONE", ["dev-eu"]), ("APPROVED", [])])
def test_main_names_the_gated_envs_applied_with_no_review_required(
    tmp_path, monkeypatch, decision, expected
):
    """dev-eu is gated and runnable; dev-us is ungated, and its `gated: false` entry already
    declares it applies unreviewed; prod-eu is gated but explicit, so it never runs and must
    not be named.

    Mutation: derive the set from `pending` instead of `runnable` -- prod-eu is named, red.
    Mutation: compare `review_decision != "NONE"` in `authorize._review_not_required` -- both
    cases go red.
    Mutation: drop its `not in ungated_envs` check -- dev-us is named, the NONE case goes red."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us", "prod-eu"],
        explicit=["prod-eu"],
        ungated="dev-us",
        decision=decision,
    )
    assert _wave_envs(parsed) == ["dev-eu", "dev-us"]
    assert json.loads(parsed["review_not_required_envs"]) == expected


def test_main_labels_only_the_ungated_envs_applied_unreviewed(tmp_path, monkeypatch):
    """The review hold is bypassed, so the gated dev-eu stays runnable and only the label
    rule keeps it out of the report.

    Mutation: label every runnable env whenever any env is ungated under REVIEW_REQUIRED --
    dev-eu is named."""
    monkeypatch.setattr(aad.az, "_review_reason", lambda *a, **k: None)
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us"],
        ungated="dev-us",
        decision="REVIEW_REQUIRED",
    )
    assert _wave_envs(parsed) == ["dev-eu", "dev-us"]
    assert json.loads(parsed["applied_ungated_envs"]) == ["dev-us"]


def test_main_holds_every_env_unreviewed_when_the_list_is_unset(tmp_path, monkeypatch):
    # REVIEW_REQUIRED with no list exempts nothing, so nothing applies. The audit line stays
    # empty, because no env was permitted to apply without a review.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        decision="REVIEW_REQUIRED",
    )
    assert _wave_envs(parsed) == []
    assert json.loads(parsed["review_held_envs"]) == ["dev-eu", "prod-eu"]
    assert json.loads(parsed["applied_ungated_envs"]) == []


def test_main_holds_every_env_when_the_decision_variable_is_absent(tmp_path, monkeypatch):
    """`decision=None` deletes SHIPMATE_REVIEW_DECISION, so this is the only test that
    exercises the `os.environ.get(..., "")` default -- the whole fail-closed behaviour when the
    review job's output never reaches the action. Without it the default could be flipped to
    "APPROVED" unnoticed."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        decision=None,
    )
    assert _wave_envs(parsed) == []
    assert json.loads(parsed["review_held_envs"]) == ["dev-eu", "prod-eu"]


def test_main_applies_every_env_on_an_approved_pr_with_no_variable(tmp_path, monkeypatch):
    # The un-opted-in consumer's ordinary run: APPROVED applies every pending env regardless
    # of the list, so the unconditional review job costs them a deployment record and nothing
    # else.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        decision="APPROVED",
    )
    assert _wave_envs(parsed) == ["dev-eu", "prod-eu"]
    assert json.loads(parsed["review_held_envs"]) == []


def test_main_claims_nothing_applied_ungated_on_a_reviewed_pull_request(tmp_path, monkeypatch):
    # The envs are listed, but the pull request was APPROVED, so the exemption never fired:
    # "permitted to apply without an approving review" over a reviewed run would be a false
    # audit line.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        ungated="dev-eu,prod-eu",
        decision="APPROVED",
    )
    assert _wave_envs(parsed) == ["dev-eu", "prod-eu"]
    assert json.loads(parsed["review_held_envs"]) == []
    assert json.loads(parsed["applied_ungated_envs"]) == []


def test_main_without_the_variable_holds_every_env_on_changes_requested(tmp_path, monkeypatch):
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us", "prod-eu"],
        order={"dev-us": ["prod-eu"]},
        decision="CHANGES_REQUESTED",
    )
    assert _wave_envs(parsed) == []
    assert json.loads(parsed["review_held_envs"]) == ["dev-eu", "dev-us", "prod-eu"]
    assert json.loads(parsed["applied_ungated_envs"]) == []
    # Held, not excluded and not skipped: the remedy is "get a review", and the comment must
    # not tell the developer to run `shipmate apply <env>` instead.
    assert json.loads(parsed["excluded_envs"]) == []
    assert json.loads(parsed["skipped_envs"]) == []


def test_main_takes_ordering_and_exclusions_from_the_loaded_table(tmp_path, monkeypatch):
    """Both optional fields reach this path as fields of the mapping the operation loaded --
    the same mapping, from the default branch, that supplies every cell's identity.

    Every assertion is on a populated value, because the broken shape returns the empty
    default rather than raising. Mutation: replace `bare_main`'s `bm.ec.env_order(table)` and
    `bm.ec.explicit_envs(table)` with the bare `{}` and `[]` -- dev-us drops to
    env-level 0 and prod-eu applies instead of being excluded.
    """
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us", "prod-eu"],
        order={"dev-us": ["dev-eu"]},
        explicit=["prod-eu"],
        decision="APPROVED",
    )
    assert json.loads(parsed["excluded_envs"]) == ["prod-eu"]
    assert [c["environment"] for c in json.loads(parsed["envlevel0_waves"])["wave0"]] == ["dev-eu"]
    assert [c["environment"] for c in json.loads(parsed["envlevel1_waves"])["wave0"]] == ["dev-us"]


def test_main_loads_the_environment_table_exactly_once(tmp_path, monkeypatch):
    """One parse per operation. This path reads three fields off the mapping -- the
    environment entries, the ordering map and the exclusion list -- and a reader that fetched
    its own would make that three reads of the default branch, each able to disagree with the
    others if the branch moves mid-run.

    Mutation: add `bm.ec.read_table()` beside `bare_main`'s `bm.ec.env_order` call -- the count
    becomes 2.
    """
    reads = []
    _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "dev-us"],
        order={"dev-us": ["dev-eu"]},
        explicit=["dev-us"],
        decision="APPROVED",
        reads=reads,
    )
    assert len(reads) == 1


def test_main_emits_the_dag_shape_notice(tmp_path, monkeypatch, capsys):
    """A bare apply runs every non-explicit env at once, so it needs the line as much as a deploy.

    Mutation: delete `bare_main`'s DAG-shape notice print -- the line is missing.
    """
    _run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu"],
        decision="APPROVED",
        tree={"dev-eu": ["stacks/a", "stacks/b"]},
        tags={"stacks/a": [], "stacks/b": []},
        checks=[_apply_check("stacks/a", "dev-eu"), _apply_check("stacks/b", "dev-eu")],
        deps={"stacks/a": set(), "stacks/b": {"stacks/a"}},
    )
    assert (
        "::notice::2 stacks, 1 after edges, 2 wave levels; 1 stacks would apply concurrently"
        in capsys.readouterr().out.splitlines()
    )


def test_main_prints_the_dag_shape_notice_before_an_over_depth_refusal(
    tmp_path, monkeypatch, capsys
):
    """The notice is the line that explains a run too deep for the pre-declared wave jobs, so it
    prints before `env_level_waves` refuses.

    Mutation: print the DAG-shape notice after `env_level_waves` -- the notice is missing.
    """
    stacks = [f"stacks/s{i}" for i in range(aad.wv.MAX_WAVES + 1)]
    with pytest.raises(SystemExit, match="dependency levels"):
        _run_main(
            tmp_path,
            monkeypatch,
            envs=["dev-eu"],
            decision="APPROVED",
            tree={"dev-eu": stacks},
            tags={s: [] for s in stacks},
            checks=[_apply_check(s, "dev-eu") for s in stacks],
            deps={s: ({stacks[i - 1]} if i else set()) for i, s in enumerate(stacks)},
        )
    assert capsys.readouterr().out.splitlines() == [
        "::notice::9 stacks, 8 after edges, 9 wave levels; 1 stacks would apply concurrently"
    ]


def test_main_emits_the_apply_all_detect_notice_named_by_the_held_remedy(
    tmp_path, monkeypatch, capsys
):
    """apply-comment's held remedy tells reviewers to read the run log's apply-all-detect notice,
    so the title is pinned whole on both sides. Mutations: `title=apply-all-detect` ->
    `title=apply-detect` in apply-detect -- the line differs; `apply-all-detect notice` ->
    `apply-detect notice` in apply-comment's `_HELD_REMEDY` -- the title is absent from it."""
    _run_main(tmp_path, monkeypatch, envs=["dev-eu"], decision="APPROVED")
    notices = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("::notice title=")
    ]
    assert notices == [
        f"::notice title=apply-all-detect::head={HEAD} cells=1 pending=1 envlevels=[1, 0, 0, 0] "
        "excluded_explicit=[] skipped_after_explicit=[] review_held=[] applied_ungated=[] "
        "review_decision='APPROVED'"
    ]
    assert "apply-all-detect notice" in load_script("apply-comment")._HELD_REMEDY
