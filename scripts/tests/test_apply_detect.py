import contextlib
import json

import pytest
from _detect_fixtures import (
    APP_ID,
    PLAN_SHA,
    _apply_check,
    _record,
    completed_names,
)
from _detect_fixtures import check_run as _check
from _loader import github_outputs, load_script

ad = load_script("apply-detect")

#: What a detect reads when a test names no table. `layout` is required, and `folder`
#: derives no identity variables, so a row carries only the stamp and the tier.
_MINIMAL_TABLE = {"layout": "folder"}


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


def test_cells_take_the_workload_from_the_tags():
    """Never from the check name: the name carries no workload, and a cell that invented one
    from its path would assume the wrong environment role. A stack missing from the map carries
    "", because the map comes from a separate terramate query and a missing entry must never be
    able to raise here."""
    cells = ad.cells_for_env(
        "dev-eu",
        ["stacks/app", "stacks/dns"],
        {"stacks/app": ["env/dev-eu", "workload/net-edge"], "stacks/dns": ["env/dev-eu"]},
    )
    assert cells == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "net-edge",
        },
        {"stack": "stacks/dns", "environment": "dev-eu", "workload": ""},
    ]


_TWO_CELLS = [
    {"stack": "stacks/app", "environment": "dev-eu"},
    {"stack": "stacks/dns", "environment": "dev-eu"},
]


def _lines(*checks):
    return [json.dumps(c) for c in checks]


_APP_CHECK = _apply_check("stacks/app", plan_run="111", plan_sha256="a" * 64)


def test_each_cell_carries_the_plan_run_and_digest_its_own_check_names():
    # The recovery shape: one cell re-planned by a later run while its sibling is still named
    # by the first. Each must apply from the run that planned it, and each must be bound to
    # the plan text reviewed for IT -- one shared digest would let a sibling's text vouch for
    # this cell's plan. Mutation: write `"plan_sha256": run` in `with_plan_runs` -- the
    # digests become run ids, red.
    lines = _lines(_APP_CHECK, _apply_check("stacks/dns", plan_run="222", plan_sha256="b" * 64))
    out = ad.with_plan_runs(_TWO_CELLS, lines, APP_ID)
    assert out == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "plan_run_id": "111",
            "plan_sha256": "a" * 64,
        },
        {
            "stack": "stacks/dns",
            "environment": "dev-eu",
            "plan_run_id": "222",
            "plan_sha256": "b" * 64,
        },
    ]


def test_a_cell_with_a_plan_run_but_no_digest_refuses_with_its_own_message():
    """The cell's record carries a run, so the missing-plan-run arm cannot absorb this: the two
    refusals name different causes and different remedies, and a reader told "no plan run"
    would go looking for a check that exists."""
    with pytest.raises(SystemExit) as exc_info:
        ad.with_plan_runs(
            _TWO_CELLS,
            _lines(_APP_CHECK, _apply_check("stacks/dns", plan_run="222", plan_sha256=None)),
            APP_ID,
        )
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan-text digest recorded for apply / stacks/dns / dev-eu: "
        "the reviewed plan text cannot be checked against the plan that would be applied, so "
        "this apply is refused rather than run unverified. Re-plan these stacks on their pull "
        "request, then apply again; if that pull request has already merged, a new pull "
        "request touching them plans and applies them afresh."
    )


def test_a_cell_whose_check_names_no_plan_run_refuses():
    # Not skipped and not defaulted: falling back to a run lookup keyed on a plan run's
    # head_sha is the platform dependency this path exists to drop, and a silent default
    # applies a cell from nowhere. `stacks/dns` carries no check, and the message is the
    # missing-run one: a cell with no check at all is not a cell whose digest went missing.
    # Mutation: raise the missing-digest refusal before the missing-run one -- red.
    with pytest.raises(SystemExit) as exc_info:
        ad.with_plan_runs(_TWO_CELLS, _lines(_APP_CHECK), APP_ID)
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan run recorded for apply / stacks/dns / dev-eu: the "
        "apply check names no plan run to apply from (post-merge, the cell may have no apply "
        "check at all), so the apply is refused. Re-plan these stacks on their pull request, "
        "then apply again; if that pull request has already merged, a new pull request "
        "touching them plans and applies them afresh."
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


def _apply_env(monkeypatch, tmp_path, table=None, reads=None, **overrides):
    """Env for a main() run; returns the GITHUB_OUTPUT path."""
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
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    _stub_read_table(monkeypatch, table, reads)
    return out


def _stub_read_table(monkeypatch, table, reads):
    """Every detect reads the environment table from the default branch before it stamps; the
    real read shells out to gh, git and terramate, none of which CI has. One entry is appended
    to `reads` per call, which is how the one-parse-per-operation count is taken."""

    def read_table():
        if reads is not None:
            reads.append(1)
        return dict(table or _MINIMAL_TABLE)

    monkeypatch.setattr(ad.bm.ec, "read_table", read_table)


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


class _Stop(Exception):
    pass


@pytest.mark.parametrize(
    ("environment", "expected"), [("", ["bare_main"]), ("dev-eu", ["refuse_unreviewed"])]
)
def test_main_routes_an_empty_env_to_the_bare_form_and_never_to_the_review_refusal(
    monkeypatch, tmp_path, environment, expected
):
    """`_review_reason` reads an empty env as a bare apply, exempt when any entry is ungated, so
    an empty env reaching `refuse_unreviewed` is a bypassed refusal.

    Mutation: route on `env is None` instead of `not env` -- the empty case reaches
    `validate_env`'s SystemExit. Mutation: `if env:` -- dev-eu records `bare_main`, and the
    empty case reaches that SystemExit."""
    _apply_env(monkeypatch, tmp_path, SHIPMATE_ENV=environment)
    calls = []
    monkeypatch.setattr(ad, "bare_main", lambda: calls.append("bare_main"))

    def _refuse(*a):
        calls.append("refuse_unreviewed")
        raise _Stop

    monkeypatch.setattr(ad, "refuse_unreviewed", _refuse)
    with contextlib.suppress(_Stop):
        ad.main()
    assert calls == expected


def test_workset_never_resolves_a_slug_back_to_a_stack():
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
    assert [c["stack"] for c in json.loads(github_outputs(out)["envlevel0_waves"])["wave0"]] == [
        "a-b"
    ]


def test_apply_path_makes_no_run_lookup_at_all(monkeypatch, tmp_path):
    # No site keys an apply on a plan run's head_sha any more. Whole-list comparison against a
    # hand-written constant, so any added gh api call -- a run lookup, an artifact listing --
    # reddens here.
    out = _apply_env(monkeypatch, tmp_path)
    urls = _stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app")])
    ad.main()
    assert (
        len(json.loads(github_outputs(out)["envlevel0_waves"])["wave0"]) == 1
    )  # Not vacuous: a cell exists.
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
    assert [c["stack"] for c in json.loads(github_outputs(out)["envlevel0_waves"])["wave0"]] == [
        "stacks/app"
    ]


def test_a_record_less_completed_check_does_not_block_the_rest(monkeypatch, tmp_path):
    """`stacks/dns` is applied and its completed check carries a bare-hex record naming no plan
    run. Only cells still to be applied need one, so it does not block the rest."""
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
    assert [c["stack"] for c in json.loads(github_outputs(out)["envlevel0_waves"])["wave0"]] == [
        "stacks/app"
    ]


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
    assert [c["stack"] for c in json.loads(github_outputs(out)["envlevel0_waves"])["wave0"]] == [
        "stacks/app"
    ]


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


def test_main_refuses_a_cyclic_run_graph_naming_the_cycle(monkeypatch, tmp_path):
    """Mutation: `levels` for `stack_levels` in both `dag_shape_notice` and
    `waves.env_level_waves` -- a raw `CycleError` escapes instead of this `SystemExit`."""
    _apply_env(monkeypatch, tmp_path)
    _stub_apply(
        monkeypatch,
        {"stacks/a": {"stacks/b"}, "stacks/b": {"stacks/a"}},
        [_apply_check("stacks/a"), _apply_check("stacks/b")],
    )
    with pytest.raises(SystemExit) as exc:
        ad.main()
    assert str(exc.value) == (
        "::error::dependency cycle in the Terramate stack run-graph: ('nodes are in a cycle', "
        "['stacks/a', 'stacks/b', 'stacks/a']). Two or more stacks order each other through "
        "`after`/`before`; break the loop in their stack configuration."
    )


def test_main_refuses_a_change_deeper_than_max_waves(monkeypatch, tmp_path, capsys):
    """`main` reaches `env_level_waves`' padding, so a chain too deep for the pre-declared
    wave jobs refuses before any output instead of emitting wave0..wave7 with the deepest cells
    dropped. The DAG-shape notice still prints first: it is the line that explains the depth.

    Mutation: bucket the cells in `main` without `env_level_waves` (one unpadded
    `{waveN: cells}` dict from `wv.assign_waves` into `write_env_level_waves`) -- the run
    writes the waves and exits 0.
    Mutation: print the DAG-shape notice after `env_level_waves` -- the notice is missing.
    """
    depth = ad.wv.MAX_WAVES + 1
    stacks = [f"stacks/s{i}" for i in range(depth)]
    deps = {s: ({stacks[i - 1]} if i else set()) for i, s in enumerate(stacks)}
    out = _apply_env(monkeypatch, tmp_path)
    out.touch()  # The runner creates GITHUB_OUTPUT before the step runs.
    _stub_apply(monkeypatch, deps, [_apply_check(s) for s in stacks])

    with pytest.raises(SystemExit, match="dependency levels"):
        ad.main()

    assert "envlevel0_waves=" not in out.read_text(encoding="utf-8")
    assert capsys.readouterr().out.splitlines() == [
        "::notice::9 stacks, 8 after edges, 9 wave levels; 1 stacks would apply concurrently"
    ]


def test_validate_head_sha_rejects_short():
    """Without `source` the refusal names SHIPMATE_HEAD_SHA, the variable both apply-detect
    forms and unlock-detect read.

    Mutation: change `source`'s default -- the whole message differs."""
    with pytest.raises(SystemExit) as exc_info:
        ad.validate_head_sha("abc123")
    assert str(exc_info.value) == (
        "::error::SHIPMATE_HEAD_SHA must be a 40-char lowercase hex SHA (got: 'abc123')"
    )


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


@pytest.mark.parametrize("value", ["Dev-EU", "dev/eu"])
def test_validate_env_refuses_a_name_no_environment_can_take(value):
    """Mutation: refuse only a `.` in `value` -- `Dev-EU` passes."""
    with pytest.raises(SystemExit) as e:
        ad.validate_env(value)
    assert str(e.value) == (
        f"::error::SHIPMATE_ENV {value!r} is not an environment name: "
        "lowercase letters, digits, '-' and '_', with no quotes, spaces or path separators."
    )


def test_validate_env_rejects_empty():
    """Each caller acts on one environment: `main` routes an empty env to `bare_main` before
    this, and unlock-detect has no all-environments form.

    Mutation: run the name check before the empty check -- `""` gets the charset message."""
    with pytest.raises(SystemExit) as e:
        ad.validate_env("")
    assert str(e.value) == "::error::SHIPMATE_ENV is empty; this path is single-env."


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
    parsed = github_outputs(out)
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
    assert evaluated == ["stacks/app"]


def _gate(ungated):
    """A structure-valid table whose entries named in `ungated`, a comma-separated string,
    hold `gated: false`."""
    return {
        "layout": "folder",
        "environments": {e: {"gated": False} for e in ungated.split(",") if e},
    }


# The engine resolves the exemption list itself and refuses on it, because an `ungated-envs`
# input wider than the repository's own configuration would otherwise apply unreviewed on the
# targeted path unconditionally.
@pytest.mark.parametrize(
    ("decision", "ungated"),
    [
        ("NONE", ""),
        ("APPROVED", ""),
        ("APPROVED", "prod-eu"),
        ("REVIEW_REQUIRED", "dev-eu"),
        ("REVIEW_REQUIRED", "other,dev-eu"),
    ],
)
def test_refuse_unreviewed_lets_an_authorized_apply_through(decision, ungated):
    ad.refuse_unreviewed("dev-eu", _gate(ungated), decision)  # must not raise


@pytest.mark.parametrize(
    ("decision", "ungated"),
    [
        # The hole: an unreviewed pull request with the variable unset. The input the
        # consumer wired into comment-ops cannot widen this.
        ("REVIEW_REQUIRED", ""),
        # Ungated, but not this env.
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
        ad.refuse_unreviewed("dev-eu", _gate(ungated), decision)
    assert str(exc_info.value).startswith("::error::not authorized")


def test_refuse_unreviewed_reuses_authorizes_selector_verbatim():
    # One function decides review policy on all three call sites, and a second implementation
    # here would eventually disagree with comment-ops about the same pull request. The reason
    # text is compared whole, not paraphrased.
    reason = ad.az._review_reason("REVIEW_REQUIRED", "dev-eu", frozenset({"prod-eu"}))
    with pytest.raises(SystemExit) as exc_info:
        ad.refuse_unreviewed("dev-eu", _gate("prod-eu"), "REVIEW_REQUIRED")
    assert str(exc_info.value) == f"::error::{reason}"


def test_refuse_unreviewed_refuses_an_env_whose_entry_is_gated():
    """`gated: false` on the default branch's entry is the only source, so an environment
    without it is refused whatever else is set.

    Mutation: resolve the exemption from the process environment -- nothing sets it, so this
    stays green while the refusal it pins stops depending on the table at all."""
    with pytest.raises(SystemExit) as exc_info:
        ad.refuse_unreviewed("dev-eu", _gate("prod-eu"), "REVIEW_REQUIRED")
    assert str(exc_info.value).startswith("::error::not authorized")
    # A table flagging no environment exempts none.
    with pytest.raises(SystemExit):
        ad.refuse_unreviewed("dev-eu", _gate(""), "REVIEW_REQUIRED")


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


def test_main_refuses_an_env_the_gate_table_does_not_exempt(monkeypatch, tmp_path):
    """The table reaches the refusal. `main` resolves the env under test from the table the
    run loaded, so an env the table exempts elsewhere is still refused here.

    Mutation: exempt unconditionally once any entry is ungated -- dev-eu is then exempted
    by prod-eu's entry and main() runs to completion."""
    _apply_env(
        monkeypatch,
        tmp_path,
        table=_gate("prod-eu"),
        SHIPMATE_REVIEW_DECISION="REVIEW_REQUIRED",
    )
    _boom_on_the_workset(monkeypatch)
    with pytest.raises(SystemExit) as exc_info:
        ad.main()
    assert str(exc_info.value).startswith("::error::not authorized")


def test_main_exempts_an_env_whose_entry_is_ungated(monkeypatch, tmp_path):
    """The other half: an unreviewed apply of a listed env runs.

    Mutation: drop the gate read -- dev-eu is no longer exempt and main() refuses."""
    out = _apply_env(
        monkeypatch,
        tmp_path,
        table=_gate("dev-eu"),
        SHIPMATE_REVIEW_DECISION="REVIEW_REQUIRED",
    )
    _stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app", plan_run="42")])
    ad.main()
    assert json.loads(github_outputs(out)["envlevel0_waves"])["wave0"][0]["stack"] == "stacks/app"


def test_main_validates_the_table_before_it_reads_the_gate(monkeypatch, tmp_path):
    """A `gated: no` reads as ungated to a person and resolves as gated. The run must die
    naming the malformed setting, not refuse the apply as unreviewed.

    Mutation: drop `bm.ec.validate_structure(table)` from main -- the refusal passes the
    unvalidated table on and raises "not authorized" rather than naming the setting."""
    _apply_env(
        monkeypatch,
        tmp_path,
        table={"layout": "folder", "environments": {"dev-eu": {"gated": "no"}}},
        SHIPMATE_REVIEW_DECISION="REVIEW_REQUIRED",
    )
    _boom_on_the_workset(monkeypatch)
    with pytest.raises(SystemExit) as exc_info:
        ad.main()
    assert str(exc_info.value) == (
        "::error::environments.dev-eu.gated must be true or false, got 'no'."
    )


def test_apply_path_loads_the_environment_table_exactly_once(monkeypatch, tmp_path):
    """One parse per operation. The refusal needs the table before the cells exist and
    `env_config` needs it after, and two reads of the default branch can disagree if the
    branch moves mid-run.

    Mutation: drop `table=table` from main's `bm.env_config` call, so `env_config` re-reads
    -- the count becomes 2."""
    reads = []
    _apply_env(monkeypatch, tmp_path, reads=reads)
    _stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app", plan_run="42")])
    ad.main()
    assert len(reads) == 1


@pytest.mark.parametrize(
    ("decision", "table", "expected"),
    [
        ("NONE", None, ["dev-eu"]),
        ("NONE", _gate("dev-eu"), []),
        ("APPROVED", None, []),
    ],
)
def test_main_names_a_gated_env_applied_with_no_review_required(
    monkeypatch, tmp_path, decision, table, expected
):
    """A null decision authorizes a gated env without a review (no rule requires one, or
    the code-owner rule owns none of the changed files); an ungated env's `gated: false`
    entry already declares it applies unreviewed, and an approval needs no disclosure.

    Mutation: compare `review_decision != "NONE"` in `authorize._review_not_required` -- the
    NONE/gated and APPROVED cases swap and go red.
    Mutation: drop its ungated check -- the NONE/ungated case names dev-eu and goes red."""
    out = _apply_env(monkeypatch, tmp_path, table=table, SHIPMATE_REVIEW_DECISION=decision)
    _stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app", plan_run="42")])
    ad.main()
    assert json.loads(github_outputs(out)["review_not_required_envs"]) == expected


def test_main_writes_the_whole_output_file_verbatim(monkeypatch, tmp_path):
    """Whole-file comparison against a hand-written constant, so an added, dropped or reordered
    key on the apply path is caught. apply.yml reads the four `envlevelN_waves`, the four
    `envlevelN_empty`, `head_sha` and `review_not_required_envs`; a targeted apply is env-level
    0 alone, so levels 1-3 must say `true` or apply.yml runs them into `fromJSON('')`.

    Mutation: pass `{env: ["_"]}` as the order to `env_level_waves` -- the cells land in
    `envlevel1`."""
    out = _apply_env(monkeypatch, tmp_path)
    _stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app", plan_run="42")])
    ad.main()
    empty = (
        '{"wave0": [], "wave1": [], "wave2": [], "wave3": [], "wave4": [], "wave5": [], '
        '"wave6": [], "wave7": []}'
    )
    assert out.read_text(encoding="utf-8") == (
        'envlevel0_waves={"wave0": [{"stack": "stacks/app", "environment": "dev-eu", '
        '"workload": "app", '
        '"role_arn": "", "cred_region": "", "tf_vars": {}, "config_path": "apply", '
        '"env_binding": "dev-eu-apply", '
        '"plan_run_id": "42", '
        '"plan_sha256": "dddddddddddddddd'
        'dddddddddddddddddddddddddddddddddddddddddddddddd"}], "wave1": [], "wave2": [], '
        '"wave3": [], "wave4": [], "wave5": [], "wave6": [], "wave7": []}\n'
        "envlevel0_empty=false\n"
        f"envlevel1_waves={empty}\n"
        "envlevel1_empty=true\n"
        f"envlevel2_waves={empty}\n"
        "envlevel2_empty=true\n"
        f"envlevel3_waves={empty}\n"
        "envlevel3_empty=true\n"
        "head_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "review_not_required_envs=[]\n"
    )


def test_main_notice_counts_every_padded_wave(monkeypatch, tmp_path, capsys):
    """Mutation: iterate `w0` instead of `w0.values()` in the notice -- it prints the key
    lengths `[5, 5, ...]` and `empty=False`."""
    _apply_env(monkeypatch, tmp_path)
    _stub_apply(
        monkeypatch,
        {"stacks/a": set(), "stacks/b": {"stacks/a"}},
        [_apply_check("stacks/a"), _apply_check("stacks/b")],
    )
    ad.main()
    assert (
        f"::notice title=apply-detect::env=dev-eu head={'a' * 40} "
        "cells=2 completed=0 pending=2 waves=[1, 1, 0, 0, 0, 0, 0, 0] empty=False"
        in capsys.readouterr().out.splitlines()
    )
