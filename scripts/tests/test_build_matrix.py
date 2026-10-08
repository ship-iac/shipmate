import json

import pytest
from _detect_fixtures import spy_env_config
from _loader import action_yaml, load_script

bm = load_script("build-matrix")

#: What a detect reads when a test names no table. `layout` is required, and `folder`
#: derives no identity variables, so a row carries only the stamp and the tier.
_MINIMAL_TABLE = {"layout": "folder"}


def _no_read():
    """A supplied table must reach validation without a second read of the file."""
    raise AssertionError("env_config read the table although one was supplied")


def test_multi_env_stack_yields_one_cell_per_env():
    cells = bm.build_matrix(
        envs=["dev-eu", "dev-us"],
        stacks_by_env={"dev-eu": ["stacks/app"], "dev-us": ["stacks/app", "stacks/dns"]},
        tags_by_stack={
            "stacks/app": ["env/dev-eu", "env/dev-us"],
            "stacks/dns": ["env/dev-us", "workload/net-edge"],
        },
    )
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/app", "environment": "dev-us", "workload": ""},
        {
            "stack": "stacks/dns",
            "environment": "dev-us",
            "workload": "net-edge",
        },
    ]


def test_empty_when_no_changed_stacks():
    assert bm.build_matrix(["dev-eu"], {"dev-eu": []}, {}) == []


def test_raises_above_256_cells():
    # The remediation must match CONTRACT.md §Fan-out: splitting the change is the
    # general remedy, and `shipmate apply <env>` is not an escape hatch -- the
    # ceiling trips in plan detect, so no reviewed plan exists to apply.
    stacks = [f"stacks/s{i}" for i in range(257)]
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(["dev-eu"], {"dev-eu": stacks}, {s: ["env/dev-eu"] for s in stacks})
    assert str(exc_info.value) == (
        "::error::257 plan cells exceeds the GitHub Actions matrix limit of 256. "
        "Split the change across several pull requests -- the matrix is built over "
        "`terramate list --changed`. A one-line edit to a shared local module correctly "
        "marks every dependent stack changed and is one atomic change by nature; there the "
        "only lever is to reduce the number of environments in play."
    )


def test_whole_tree_sweep_above_256_cells_names_drift_files(monkeypatch):
    """A drift sweep is split across drift files, not pull requests.

    Reddens when the `all_stacks` branch is dropped, or `compute_cells` stops forwarding
    `all_stacks`, and the changed-set message returns.
    """
    stacks = [f"stacks/s{i}" for i in range(257)]
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: stacks)
    monkeypatch.setattr(bm, "_tags", lambda s: ["env/dev-eu"])
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True)
    assert str(exc_info.value) == (
        "::error::257 plan cells exceeds the GitHub Actions matrix limit of 256. "
        "A drift sweep is one matrix; split it across more drift files, each calling "
        "drift.yml with a narrower `tags` query (docs/drift.md)."
    )


def test_the_matrix_limit_counts_the_cells_a_query_keeps(monkeypatch):
    """257 tree cells and a query keeping 10 plan those 10.

    Mutations: cap the tree before the filter in `compute_cells` -- the 257-cell tree refuses;
    return the filtered cells as the tree -- the tree comparison reddens."""
    stacks = [f"stacks/s{i:03}" for i in range(257)]
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: stacks)
    monkeypatch.setattr(
        bm,
        "_tags",
        lambda s: ["env/dev-eu", "workload/keep" if s < "stacks/s010" else "workload/drop"],
    )
    _, cells, tree, _ = bm.compute_cells(all_stacks=True, tags="workload/keep")
    assert [c["stack"] for c in cells] == [f"stacks/s{i:03}" for i in range(10)]
    assert tree == [{"environment": "dev-eu", "stack": f"stacks/s{i:03}"} for i in range(257)]


def test_stack_at_engine_reserved_word_paths_plans():
    """Stacks at paths exactly `apply` and `shipmate` each build a cell row.

    Reddens on re-adding a refusal of either path (SystemExit).
    """
    cells = bm.build_matrix(
        ["dev"],
        {"dev": ["apply", "shipmate"]},
        {"apply": ["env/dev"], "shipmate": ["env/dev"]},
    )
    assert cells == [
        {"stack": "apply", "environment": "dev", "workload": ""},
        {"stack": "shipmate", "environment": "dev", "workload": ""},
    ]


_TWO_WORKLOADS_ERROR = (
    "::error::stack 'stacks/dns' carries 2 workload tags (workload/net, workload/network), "
    "and a stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` "
    "and remove the rest."
)


def test_two_workload_tags_refuse_naming_the_stack_and_every_tag():
    """Reddens on restoring the first-match `return` loop in `workload_of` (returns "net",
    no SystemExit), and on any edit to the message: it is compared whole."""
    with pytest.raises(SystemExit) as exc_info:
        bm.workload_of(["env/dev-eu", "workload/net", "workload/network"], "stacks/dns")
    assert exc_info.value.code == _TWO_WORKLOADS_ERROR


def test_one_or_zero_workload_tags_keep_their_values():
    """Reddens on `workload_of` returning "" unconditionally."""
    assert bm.workload_of(["env/dev-eu", "workload/net"], "stacks/dns") == "net"
    assert bm.workload_of(["env/dev-eu"], "stacks/dns") == ""


def test_build_matrix_refuses_a_stack_with_two_workload_tags():
    """Reddens on replacing the `workload_of` call in `build_matrix` with an inline first-match
    over the tags, and on dropping the sort of the named tags (they arrive unsorted here)."""
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(
            ["dev-eu"],
            {"dev-eu": ["stacks/dns"]},
            {"stacks/dns": ["workload/network", "env/dev-eu", "workload/net"]},
        )
    assert exc_info.value.code == _TWO_WORKLOADS_ERROR


def test_apply_detect_cells_refuse_a_stack_with_two_workload_tags():
    """Reddens on replacing the `workload_of` call in `apply-detect.cells_for_env` with an
    inline first-match over the tags."""
    ad = load_script("apply-detect")
    with pytest.raises(SystemExit) as exc_info:
        ad.cells_for_env(
            "dev-eu",
            ["stacks/dns"],
            {"stacks/dns": ["env/dev-eu", "workload/net", "workload/network"]},
        )
    assert exc_info.value.code == _TWO_WORKLOADS_ERROR


def test_list_stacks_changed_uses_changed_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(bm, "_run", lambda args: captured.update(args=args) or "stacks/a\n")
    assert bm._list_stacks(all_stacks=False, base="deadbeef") == ["stacks/a"]
    assert captured["args"] == ["terramate", "list", "--changed", "-B", "deadbeef"]


def test_list_stacks_all_omits_changed_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        bm, "_run", lambda args: captured.update(args=args) or "stacks/a\nstacks/b\n"
    )
    assert bm._list_stacks(all_stacks=True, base="") == ["stacks/a", "stacks/b"]
    assert captured["args"] == ["terramate", "list"]


def test_tags_evals_with_as_json(monkeypatch):
    captured = {}
    monkeypatch.setattr(bm, "_run", lambda args: captured.update(args=args) or '["env/dev-eu"]')
    assert bm._tags("stacks/app") == ["env/dev-eu"]
    assert captured["args"] == [
        "terramate",
        "-C",
        "stacks/app",
        "experimental",
        "eval",
        "--as-json",
        "terramate.stack.tags",
    ]


def test_compute_cells_fans_out_multi_env(monkeypatch):
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["env/dev-eu", "env/dev-us", "workload/app"])
    _, cells, _, _ = bm.compute_cells(all_stacks=True)
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
        {"stack": "stacks/app", "environment": "dev-us", "workload": "app"},
    ]


def test_env_membership_groups_stacks_by_env_tag(monkeypatch):
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/dns"])
    tags = {
        "stacks/app": ["env/dev-eu", "env/dev-us"],
        "stacks/dns": ["env/dev-eu", "workload/dns"],
    }
    monkeypatch.setattr(bm, "_tags", lambda s: tags[s])
    stacks_by_env, tags_by_stack = bm.env_membership(all_stacks=True)
    assert stacks_by_env == {"dev-eu": ["stacks/app", "stacks/dns"], "dev-us": ["stacks/app"]}
    assert tags_by_stack == tags


_UNMANAGED_TREE = {
    "stacks/app": ["env/dev-eu"],
    "stacks/zeta": ["workload/net"],
    "stacks/alpha": [],
}
_TWENTY_UNMANAGED = {"stacks/app": ["env/dev-eu"], **{f"stacks/u{i:02}": [] for i in range(20)}}


def test_env_membership_skips_untagged_stacks_with_a_whole_tree_notice(monkeypatch, capsys):
    """Two stacks with no `env/*` tag are in the tag map, in no environment, and named in one
    sorted notice. Mutation: restore the `SystemExit` refusal."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_UNMANAGED_TREE))
    monkeypatch.setattr(bm, "_tags", lambda s: _UNMANAGED_TREE[s])
    stacks_by_env, tags_by_stack = bm.env_membership(all_stacks=True)
    assert stacks_by_env == {"dev-eu": ["stacks/app"]}
    assert tags_by_stack == _UNMANAGED_TREE
    assert capsys.readouterr().out == (
        "::notice::2 stack(s) carry no env/* tag and are not managed by shipmate: "
        "stacks/alpha, stacks/zeta\n"
    )


def test_the_changed_set_scan_names_its_scope_in_the_notice(monkeypatch, capsys):
    """Mutation: print the whole-tree wording on both scans."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_UNMANAGED_TREE))
    monkeypatch.setattr(bm, "_tags", lambda s: _UNMANAGED_TREE[s])
    bm.env_membership(all_stacks=False, base="deadbeef")
    assert capsys.readouterr().out == (
        "::notice::2 changed stack(s) carry no env/* tag and are not managed by shipmate: "
        "stacks/alpha, stacks/zeta\n"
    )


def test_the_notice_names_ten_paths_and_counts_the_rest(monkeypatch, capsys):
    """Mutation: name every path -- the notice lists all twenty."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_TWENTY_UNMANAGED))
    monkeypatch.setattr(bm, "_tags", lambda s: _TWENTY_UNMANAGED[s])
    bm.env_membership(all_stacks=True)
    assert capsys.readouterr().out == (
        "::notice::20 stack(s) carry no env/* tag and are not managed by shipmate: "
        + ", ".join(f"stacks/u{i:02}" for i in range(10))
        + ", and 10 more\n"
    )


def test_no_unmanaged_stack_prints_nothing(monkeypatch, capsys):
    """Mutation: print the notice with N = 0."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["env/dev-eu"])
    bm.env_membership(all_stacks=True)
    assert capsys.readouterr().out == ""


def test_compute_cells_leaves_an_unmanaged_stack_out_of_cells_and_tree(monkeypatch):
    """The fourth element lists it, and no cell or tree entry names it, so drift closes its
    Issues. Mutation: add unmanaged stacks to `tree`."""
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(_UNMANAGED_TREE))
    monkeypatch.setattr(bm, "_tags", lambda s: _UNMANAGED_TREE[s])
    _, cells, tree, unmanaged = bm.compute_cells(all_stacks=True)
    assert cells == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    assert tree == [{"environment": "dev-eu", "stack": "stacks/app"}]
    assert unmanaged == ["stacks/alpha", "stacks/zeta"]


def test_two_workload_tags_on_an_unmanaged_stack_draw_no_refusal(monkeypatch):
    """Mutation: call `workload_of` over every stack in `tags_by_stack` in `full_tree`."""
    tree = {"stacks/app": ["env/dev-eu"], "stacks/odd": ["workload/a", "workload/b"]}
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "_tags", lambda s: tree[s])
    _, cells, _, unmanaged = bm.compute_cells(all_stacks=True)
    assert cells == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    assert unmanaged == ["stacks/odd"]


def test_a_stated_head_repository_equal_to_this_repository_is_planned():
    assert bm.fork_pr_error("acme/iac", "acme/iac", False) == ""


def test_a_stated_foreign_head_repository_is_refused_naming_both():
    err = bm.fork_pr_error("acme/iac", "outsider/iac", False)
    assert err.startswith("::error::")
    assert "outsider/iac" in err
    assert "acme/iac" in err


def test_an_unstated_head_repository_is_refused_naming_the_input():
    # Pinning the message, not the refusal: letting an empty value fall through to the
    # equality check refuses too, with the fork wording, which tells a consumer who forgot
    # the input to push their branch to where it already is.
    err = bm.fork_pr_error("acme/iac", "   ", False)
    assert err.startswith("::error::")
    assert "head-repo" in err
    assert "no-pull-request" in err
    assert "docs/getting-started.md" in err
    assert "fork pull requests are not supported" not in err


#: Values that are not the bool True, so every refusal helper must still refuse on them.
_NOT_OPTED_OUT = [
    pytest.param(False, id="False"),
    pytest.param("false", id="str-false"),
    pytest.param("true", id="str-true"),
    pytest.param(1, id="one"),
    pytest.param("", id="empty"),
    pytest.param(None, id="None"),
]


@pytest.mark.parametrize("value", _NOT_OPTED_OUT)
def test_only_the_bool_true_skips_the_three_refusals(monkeypatch, value):
    """main() parses the input to a bool, so a raw string here is a caller that forgot to parse.
    A truthy check would skip the fork refusal for "false".

    Mutation: `if no_pull_request is True:` -> `if no_pull_request:` in any of the three
    helpers -- its str-false, str-true and one rows redden."""
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    assert bm.fork_pr_error("acme/iac", "outsider/iac", value).startswith("::error::")
    assert bm.head_checkout_error("cafe1234", value).startswith("::error::")
    assert bm.tag_filter_error("env/dev-eu", value).startswith("::error::")


def test_the_opt_out_plans_whatever_the_head_repository():
    for head in ("", "   ", "acme/iac", "outsider/iac"):
        assert bm.fork_pr_error("acme/iac", head, True) == ""


def test_an_unknown_this_repository_refuses_a_stated_head_repository():
    # GITHUB_REPOSITORY unset is not reachable on a runner; if it ever is, the
    # empty string must not compare equal to whatever was stated.
    assert "fork pull requests are not supported" in bm.fork_pr_error("", "acme/iac", False)


def _run_main(
    monkeypatch,
    tmp_path,
    env,
    cells=(("stacks/app", "dev-eu"),),
    called=None,
    plan_workflow=True,
    head_sha=None,
    table=None,
    stacks=None,
    tree=None,
):
    """main() with GITHUB_OUTPUT redirected, returning (parsed outputs, calls) where calls
    records compute_cells' arguments, so a rejection is observable as the stack enumeration
    never having run. Pass `called` to keep that record readable when main() raises.

    The double returns `tree` as the scanned tree's cell names, or the names of `cells` when
    it is None. `stacks`, a `{stack: [tags]}` map, runs the real `compute_cells` over that
    tree instead of the double, and leaves `called`, `cells` and `tree` unused.

    `head_sha` states that commit AND makes `git rev-parse HEAD` answer it, which is what a
    run past the head-checkout refusal looks like; without it the run states no head and is
    refused, so every test that wants to reach the matrix passes one."""
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    # main() reads the checkout it runs in: the consumer's workflow file has to be
    # where plan_workflow_error requires it, and the engine repo (pytest's cwd) has
    # no shipmate.yml of its own.
    (tmp_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    if plan_workflow:
        (tmp_path / ".github" / "workflows" / "shipmate.yml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for k in (
        "SHIPMATE_ALL_STACKS",
        "GITHUB_EVENT_NAME",
        "GITHUB_REPOSITORY",
        "GITHUB_EVENT_PATH",
        "SHIPMATE_HEAD_REPO",
        "SHIPMATE_HEAD_SHA",
        "SHIPMATE_NO_PULL_REQUEST",
        "SHIPMATE_TAGS",
    ):
        monkeypatch.delenv(k, raising=False)
    if head_sha is not None:
        monkeypatch.setenv("SHIPMATE_HEAD_SHA", head_sha)
        monkeypatch.setattr(bm, "_run", lambda args: f"{head_sha}\n")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    called = [] if called is None else called

    def fake_compute(all_stacks=False, base="", tags="", errors=None):
        called.append((all_stacks, base, tags))
        # The whole row `build_matrix` emits, `workload` included: a double that omits a
        # key the real builder always adds cannot fail on a guard that pins the row shape.
        rows = [{"stack": s, "environment": e, "workload": ""} for s, e in cells]
        names = [{"environment": e, "stack": s} for s, e in cells] if tree is None else tree
        # The real `compute_cells` returns the env->workloads map beside the rows, and `main`
        # forwards it as `tagged` only under `all_stacks`. The rows tag no workload.
        return {e: frozenset() for _, e in cells}, rows, names, []

    if stacks is None:
        monkeypatch.setattr(bm, "compute_cells", fake_compute)
    else:
        monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(stacks))
        monkeypatch.setattr(bm, "_tags", lambda s: stacks[s])
    monkeypatch.setattr(bm.ec, "read_table", lambda: dict(table or _MINIMAL_TABLE))
    bm.main()
    parsed = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    return parsed, called


def test_main_fails_the_step_for_a_fork_and_does_not_enumerate(monkeypatch, tmp_path):
    # SystemExit, not an empty matrix: no gate is ever written for a fork head,
    # so a green "nothing to plan" would leave the contributor waiting on a
    # required check that structurally cannot arrive.
    called = []
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "outsider/iac",
            },
            called=called,
        )
    assert "fork pull requests are not supported" in str(excinfo.value)
    assert called == []


def test_main_passes_the_three_environment_values_to_the_guard(monkeypatch, tmp_path):
    # Three distinct values, so a reordered call site is red rather than merely
    # differently spelled.
    seen = []
    monkeypatch.setattr(bm, "fork_pr_error", lambda *args: seen.append(args) or "")
    _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "outsider/iac",
            "SHIPMATE_NO_PULL_REQUEST": "true",
        },
    )
    assert seen == [("acme/iac", "outsider/iac", True)]


def test_main_refuses_the_fork_before_the_consumer_wiring_checks(monkeypatch, tmp_path):
    # Both guards would fire on this run -- a fork head, and a checkout that is not the
    # stated one -- so the assertion pins the order: a run this script cannot vouch for is
    # turned away before it shells out to git on the fork's tree, so `_run` fails.
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("git ran on a fork's tree"))
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "outsider/iac",
                "SHIPMATE_HEAD_SHA": "cafe1234",
            },
            called=called,
        )
    assert "fork pull requests are not supported" in str(excinfo.value)
    assert called == []


def test_main_refuses_a_pull_request_when_the_repository_is_unknown(monkeypatch, tmp_path):
    # `if repository and head_repo == repository` fails closed on purpose: with
    # GITHUB_REPOSITORY empty, dropping that clause makes every stated head
    # repository compare equal to the empty string and the run gets planned.
    called = []
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "",
                "SHIPMATE_HEAD_REPO": "acme/iac",
            },
            called=called,
        )
    assert "fork pull requests are not supported" in str(excinfo.value)
    assert called == []


def test_main_does_not_enumerate_stacks_for_a_fork(monkeypatch, tmp_path):
    # The rejection must precede the terramate calls, not merely discard them.
    def boom(*a, **k):
        pytest.fail("compute_cells ran for a fork pull request")

    monkeypatch.setattr(bm, "compute_cells", boom)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/iac")
    monkeypatch.setenv("SHIPMATE_HEAD_REPO", "outsider/iac")
    monkeypatch.delenv("SHIPMATE_NO_PULL_REQUEST", raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    # The assertion names the rejection so this guard cannot pass on somebody
    # else's SystemExit.
    with pytest.raises(SystemExit) as excinfo:
        bm.main()
    assert "fork pull requests are not supported" in str(excinfo.value)


def test_main_plans_a_same_repository_pull_request(monkeypatch, tmp_path):
    outputs, called = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "acme/iac",
        },
        head_sha="cafe1234",
    )
    assert outputs["empty"] == "false"
    assert called == [(False, "", "")]


def test_main_drift_run_is_unaffected(monkeypatch, tmp_path):
    # all-stacks, no pull request context: every stack must still be enumerated
    # off the opt-out alone, with no head repository stated.
    outputs, called = _run_main(
        monkeypatch,
        tmp_path,
        {
            "SHIPMATE_ALL_STACKS": "true",
            "SHIPMATE_NO_PULL_REQUEST": "true",
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
        },
    )
    assert outputs["empty"] == "false"
    assert called == [(True, "", "")]


def test_a_stated_head_equal_to_the_checkout_is_planned(monkeypatch):
    monkeypatch.setattr(bm, "_run", lambda args: "cafe1234\n")
    assert bm.head_checkout_error("cafe1234", False) == ""


def test_a_checkout_that_is_not_the_stated_head_is_refused_naming_both(monkeypatch):
    """What both plan triggers leave checked out by default: the base branch under
    `pull_request_target`, the dispatch ref under `workflow_dispatch`. Either way terramate
    diffs the wrong tree against the base and reports nothing changed. Both SHAs are asserted
    because the message is the only place naming the tree planned and the one that should be."""
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    err = bm.head_checkout_error("cafe1234", False)
    assert err.startswith("::error::")
    assert "cafe1234" in err and "basebase" in err
    assert "nothing queued to apply" in err


def test_an_unstated_head_is_refused_naming_the_input(monkeypatch):
    """Refused, not skipped, and without probing git: the stated head is the only thing this
    check compares against, so an omitted `head-sha` leaves it unmade -- the direction that
    plans the base, greens the gate and queues no applies. The message is asserted, not the
    refusal alone: it names the input and the drift opt-out, never the mismatch wording."""
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("probed with no stated head"))
    for head in ("", "   "):
        err = bm.head_checkout_error(head, False)
        assert err.startswith("::error::")
        assert "head-sha" in err
        assert "no-pull-request" in err
        assert "checked out" not in err


def test_the_opt_out_skips_the_head_checkout_check(monkeypatch):
    """The drift path has no pull-request context and no reason to be at any particular commit, so
    `git rev-parse` must not even run."""
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("head checkout was probed"))
    for head in ("", "cafe1234"):
        assert bm.head_checkout_error(head, True) == ""


@pytest.mark.parametrize(
    ("value", "opted_out"),
    [
        pytest.param("true", True, id="true"),
        pytest.param("True", True, id="capitalised"),
        pytest.param(" TRUE ", True, id="padded-upper"),
        pytest.param("false", False, id="manifest-default"),
        pytest.param("False", False, id="capitalised-false"),
        pytest.param(" false ", False, id="padded-false"),
        pytest.param("yes", False, id="yes"),
        pytest.param("1", False, id="one"),
        pytest.param("", False, id="empty"),
        pytest.param("no-pull-request", False, id="input-name"),
    ],
)
def test_main_parses_the_opt_out_once_for_all_three_guards(monkeypatch, tmp_path, value, opted_out):
    """Case- and whitespace-insensitive, so a `no-pull-request: True` does not redden a sweep
    over YAML capitalisation. Only "true" opts out: the manifest default is the non-empty string
    "false", so anything that treats a non-empty value as the opt-out would plan every unstated
    run unchecked.

    Mutations: drop `.strip()` or `.lower()` from main's parse -- a padded or capitalised row
    reddens; parse with `bool(...)` -- every non-empty `False` row reddens; pass the raw variable
    to one guard -- that guard records a string."""
    seen = []
    for name in ("fork_pr_error", "head_checkout_error", "tag_filter_error"):
        monkeypatch.setattr(bm, name, lambda *args, _n=name: seen.append((_n, args[-1])) or "")
    _run_main(monkeypatch, tmp_path, {"SHIPMATE_NO_PULL_REQUEST": value})
    assert seen == [
        ("fork_pr_error", opted_out),
        ("head_checkout_error", opted_out),
        ("tag_filter_error", opted_out),
    ]


def test_main_refuses_a_run_that_states_no_head(monkeypatch, tmp_path):
    # The wiring this backstop exists for: a correct `head-repo` gets past the
    # fork refusal, and the wrapper never wired `head-sha`.
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("probed with no stated head"))
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
            },
            called=called,
        )
    assert "did not state the commit it is planning" in str(excinfo.value)
    assert called == []


def test_main_refuses_a_dispatched_run_whose_checkout_is_not_the_stated_head(monkeypatch, tmp_path):
    """The leg the event-keyed version made no check on: `actions/checkout` takes the dispatch
    ref here, so a wrapper that adds the trigger and forgets `ref:` plans the default branch
    against the base, comes out empty, skips the plan job and greens the gate with nothing
    queued. Held at main(): re-keying the guard cannot help if the call site keys on events."""
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: "defaultbranch\n")
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
                "SHIPMATE_HEAD_SHA": "cafe1234",
            },
            called=called,
        )
    assert "which is not the commit it is planning (cafe1234)" in str(excinfo.value)
    assert called == []


def test_main_refuses_a_dispatched_run_that_states_no_head(monkeypatch, tmp_path):
    # The same leg, unstated rather than mismatching: a wrapper that adds the
    # trigger and never wires `head-sha` is refused too, without probing git.
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("probed with no stated head"))
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
            },
            called=called,
        )
    assert "did not state the commit it is planning" in str(excinfo.value)
    assert called == []


def test_plan_workflow_at_the_contract_path_is_planned(monkeypatch, tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "shipmate.yml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert bm.plan_workflow_error("pull_request_target") == ""


def test_a_renamed_plan_workflow_is_refused(monkeypatch, tmp_path):
    """This refusal makes the path load-bearing: no plan-run lookup matches it literally any
    more, so a rename would merge green with doctor's `shipmate.yml` probe reduced to a
    notice. The whole message is hand-written, and names only consequences still true now
    that the plan run id rides on each apply check and `actions/dispatch` aims every verb at
    this one file; a clause about plan-run discovery coming back here would be a falsehood."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "shipmate-plan.yml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert bm.plan_workflow_error("pull_request") == (
        "::error::this repository has no `.github/workflows/shipmate.yml`, the one path "
        "`CONTRACT.md` lets the consumer's workflow file live at, and this refusal is what "
        "enforces it. That exact filename is matched literally by `shipmate doctor`, whose "
        "`shipmate.yml` probe checks its job name, dispatch wiring, event routing and leftover "
        "drift call, and by "
        "`actions/dispatch`, which sends every commented verb to it. A consumer workflow "
        "under any other name draws that probe's could-not-read notice and doctor's own "
        "`pull_request_target` warning, and is reached by no `shipmate` command at "
        "all. Move the consumer's workflow back to `.github/workflows/shipmate.yml`."
    )


def test_plan_workflow_check_is_skipped_off_a_pull_request(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for event in ("schedule", "workflow_dispatch", "push", ""):
        assert bm.plan_workflow_error(event) == ""


def test_main_refuses_a_base_checkout(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
                "SHIPMATE_HEAD_SHA": "cafe1234",
            },
            called=called,
        )
    assert "which is not the commit it is planning (cafe1234)" in str(excinfo.value)
    assert called == []


def test_main_refuses_a_missing_plan_workflow(monkeypatch, tmp_path):
    called = []
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
            },
            called=called,
            plan_workflow=False,
            head_sha="cafe1234",
        )
    assert ".github/workflows/shipmate.yml" in str(excinfo.value)
    assert called == []


def test_build_matrix_action_declares_its_inputs():
    """No input here can turn a refusal off: `head-repo` and `head-sha` are what the two
    refusals key on and an empty value refuses either, while `no-pull-request` only states
    that there is no pull request at all, and `tags` is refused unless the run states that
    too. All are settable only by this repository's own default-branch workflow, which a
    pull-request author cannot edit, and the direction is chosen so a forgotten input
    refuses (plan wrapper) or reddens the sweep (drift), never plans a fork.

    Hand-written, name -> default; descriptions are prose and not pinned."""
    from _loader import action_yaml

    doc = action_yaml("build-matrix")
    assert {name: spec.get("default") for name, spec in doc["inputs"].items()} == {
        "base-sha": None,
        "all-stacks": "false",
        "head-repo": "",
        "head-sha": "",
        "no-pull-request": "false",
        "tags": "",
        "github-vars": "",
    }


def test_build_matrix_action_hands_the_script_the_names_it_reads():
    """The whole `env:` block against a hand-written constant: the script reads
    SHIPMATE_HEAD_REPO, SHIPMATE_HEAD_SHA and SHIPMATE_NO_PULL_REQUEST by name, so a renamed
    key here leaves every plan run unstated -- refused, but only in production."""
    from _loader import action_steps

    (step,) = [s for s in action_steps("build-matrix") if s.get("id") == "build"]
    assert step["env"] == {
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_ALL_STACKS": "${{ inputs.all-stacks }}",
        "SHIPMATE_HEAD_REPO": "${{ inputs.head-repo }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_NO_PULL_REQUEST": "${{ inputs.no-pull-request }}",
        "SHIPMATE_TAGS": "${{ inputs.tags }}",
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
        "GH_TOKEN": "${{ github.token }}",
    }


def test_build_matrix_action_declares_the_outputs_the_gate_reads():
    """Mutation: delete the `refusal` output -- the gate never names a detect error."""
    # `count` is what the trusted summary job measures its evidence against, so
    # a rename or a rewire here is a silent hole in the gate. Hand-written,
    # name -> wiring; descriptions are prose and deliberately not pinned.
    doc = action_yaml("build-matrix")
    assert {name: spec["value"] for name, spec in doc["outputs"].items()} == {
        "matrix": "${{ steps.build.outputs.matrix }}",
        "empty": "${{ steps.build.outputs.empty }}",
        "count": "${{ steps.build.outputs.count }}",
        "cells": "${{ steps.build.outputs.cells }}",
        "unmanaged": "${{ steps.build.outputs.unmanaged }}",
        "refusal": "${{ steps.build.outputs.refusal }}",
    }


def test_the_cells_output_is_the_scanned_tree_not_the_matrix(monkeypatch, tmp_path):
    """`cells` is the tree `compute_cells` returns, names alone, in its order: drift-issues closes
    the Issue of every cell missing from it, so the filtered matrix there would close the Issues
    of every cell outside the query. Mutations: write `cells` from the matrix rows' names;
    emit the tree reversed."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_NO_PULL_REQUEST": "true",
        },
        cells=(("stacks/app", "dev-eu"),),
        tree=[
            {"environment": "dev-eu", "stack": "stacks/app"},
            {"environment": "prod-eu", "stack": "stacks/db"},
        ],
    )
    assert json.loads(outputs["cells"]) == [
        {"environment": "dev-eu", "stack": "stacks/app"},
        {"environment": "prod-eu", "stack": "stacks/db"},
    ]


def test_rejects_stacks_that_slug_to_one_artifact_name():
    # `a/b` and `a-b` both slug to `a-b`, so both cells' plan artifact is
    # `plan.dev-eu.a-b` and an apply downloads whichever landed last.
    stacks = ["a/b", "a-b"]
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(["dev-eu"], {"dev-eu": stacks}, {s: ["env/dev-eu"] for s in stacks})
    assert str(exc_info.value) == (
        "::error::a-b, a/b all map to the plan artifact 'plan.dev-eu.a-b': distinct "
        "stack paths sharing one artifact name would make an apply download another "
        "stack's plan. Rename one so the path->'-' slug is unique."
    )


def test_same_slug_in_different_envs_is_allowed():
    # The env is part of the artifact name: `plan.dev-eu.a-b` and
    # `plan.prod-eu.a-b` are distinct, so there is nothing to collide.
    cells = bm.build_matrix(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["a/b"], "prod-eu": ["a-b"]},
        {"a/b": ["env/dev-eu"], "a-b": ["env/prod-eu"]},
    )
    assert [c["stack"] for c in cells] == ["a/b", "a-b"]


@pytest.mark.parametrize("cells", [(("stacks/app", "dev-eu"), ("stacks/db", "dev-eu")), ()])
def test_the_three_outputs_agree_on_one_cell_list(monkeypatch, tmp_path, cells):
    """`count`, `matrix` and `empty` all describe the same cells.

    Fails when `count` is computed from anything but the list written to `matrix`: the
    trusted summary job checks its evidence against `count`, so a count above the cells
    planned would hold the gate on a cell no plan run ever produced."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_NO_PULL_REQUEST": "true",
        },
        cells=cells,
    )
    assert outputs["count"] == str(len(cells))
    assert len(json.loads(outputs["matrix"])["include"]) == len(cells)
    assert outputs["empty"] == ("true" if not cells else "false")


def test_the_plan_workflow_path_is_the_one_consumer_file():
    """The whole path, hand-written. Three surfaces match this literally — `actions/dispatch`
    aims every verb at it, `shipmate doctor` keys its fork-trigger exemption and its five
    filename-keyed probes on it, and this refusal is what makes
    a repository carry it at all. A rename here silently retires all of them.

    Mutation: set it back to `.github/workflows/plan.yml`.
    """
    assert bm.PLAN_WORKFLOW == ".github/workflows/shipmate.yml"


#: The plan and drift legs of one call site. Hand-written; `all_stacks` is the only difference
#: between them, and it is what decides whether an environment absent from the scan is evidence.
_TWO_ENV_TABLE = {
    "layout": "folder",
    "environments": {
        "dev-eu": {"region": "eu-west-1"},
        "dev-us": {"region": "us-east-1"},
    },
}
_UNUSED_DEV_US = (
    "::warning::the environment table declares dev-us, which no stack tags. Remove the "
    "entry, or tag the stacks that belong to it. This is a warning rather than a refusal "
    "because the table is read from the default branch and the tags from this branch, so "
    "an environment arrives and leaves over two pull requests."
)


def test_a_plan_run_says_nothing_about_an_environment_outside_the_changed_set(
    monkeypatch, tmp_path, capsys
):
    """`all_stacks=False` scans the CHANGED stacks only, so `dev-us` having no stack in that
    set says nothing about whether any stack tags it.

    Mutation: pass `tagged` unconditionally at the call site. Every plan run then
    warns about every environment with no changed stack. The drift sibling below is what keeps
    this test from passing with the diagnostic deleted."""
    _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "acme/iac",
        },
        head_sha="a" * 40,
        table=_TWO_ENV_TABLE,
    )
    assert "dev-us" not in capsys.readouterr().out


def test_a_whole_tree_run_does_report_the_unused_entry(monkeypatch, tmp_path, capsys):
    """The same call site with `all_stacks=true` -- drift -- has scanned the whole tree, so the
    absence is evidence. Mutation: pass `None` unconditionally there; drift then never reports
    an unused entry and the diagnostic reaches no production path at all."""
    _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_ALL_STACKS": "true",
            "SHIPMATE_NO_PULL_REQUEST": "true",
        },
        table=_TWO_ENV_TABLE,
    )
    assert _UNUSED_DEV_US in capsys.readouterr().out.splitlines()


_DRIFT_ENV = {
    "GITHUB_EVENT_NAME": "schedule",
    "GITHUB_REPOSITORY": "acme/iac",
    "SHIPMATE_ALL_STACKS": "true",
    "SHIPMATE_NO_PULL_REQUEST": "true",
}
#: `stacks/net` alone tags `workload/net`, and `stacks/web` alone sits in dev-us.
_WORKLOAD_TREE = {
    "stacks/app": ["env/dev-eu", "workload/app"],
    "stacks/net": ["env/dev-eu", "workload/net"],
    "stacks/web": ["env/dev-us"],
}
_LISTING_TABLE = {
    "layout": "folder",
    "identities": {"dev": {"aws": {"apply": "arn:aws:iam::111111111111:role/apply"}}},
    "environments": {
        "dev-eu": {"region": "eu-west-1", "identity": "dev", "workloads": ["app", "net"]},
        "dev-us": {"region": "us-east-1"},
    },
}


def test_drift_passes_the_whole_tree_workload_map(monkeypatch, tmp_path):
    """Mutations: pass `set(tagged)` at `main`'s drift call -- the real `env_config` raises
    `AttributeError: 'set' object has no attribute 'get'`; or build `tagged` from the cells in
    `compute_cells` -- dev-us records `frozenset({''})`."""
    seen = spy_env_config(monkeypatch, bm)
    _run_main(monkeypatch, tmp_path, _DRIFT_ENV, table=_LISTING_TABLE, stacks=_WORKLOAD_TREE)
    assert seen == [{"dev-eu": frozenset({"app", "net"}), "dev-us": frozenset()}]


def test_a_plan_passes_no_workload_map(monkeypatch, tmp_path):
    """Mutation: pass `tagged` unconditionally at `main`'s `env_config` call -- the spy records
    the workload map."""
    seen = spy_env_config(monkeypatch, bm)
    _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "acme/iac",
        },
        head_sha="a" * 40,
        table=_LISTING_TABLE,
        stacks=_WORKLOAD_TREE,
    )
    assert seen == [None]


def test_a_supplied_table_is_validated_exactly_as_a_read_one_is(monkeypatch):
    """`table=` exists to spare a second read, not to skip the checks. `validate_structure`
    alone does not supply `_check_dry_coverage`, so a supplied `tf_vars`
    table with no entry for a cell's environment would derive neither identity variable and
    the cell would plan undistinguished from every other environment's.

    Mutation: on the supplied-table branch return `ec.validate_structure(table)` instead of
    routing through `ec.validate`.
    """
    monkeypatch.setattr(bm.ec, "read_table", _no_read)
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    with pytest.raises(SystemExit) as exc:
        bm.env_config(cells, table={"layout": "tf_vars", "environments": {}})
    assert "dev-eu" in str(exc.value) and "no entry in it" in str(exc.value)


_MISSING = object()


def _resolving(binding):
    """A `resolve` double, matching its signature, whose row carries `binding` as its
    `env_binding`, or no such key for `_MISSING`."""

    def resolve(table, env, path, workload):
        row = {"role_arn": "", "cred_region": "", "tf_vars": {}, "config_path": path}
        if binding is not _MISSING:
            row["env_binding"] = binding
        return row

    return resolve


@pytest.mark.parametrize(
    ("binding", "shown"),
    [(_MISSING, "None"), (42, "42"), ("", "''")],
    ids=["missing", "int", "empty"],
)
def test_a_row_without_a_usable_binding_refuses(monkeypatch, binding, shown):
    """Every cell job binds `environment:` from the row, and GitHub binds no environment for an
    empty one: the cell would run outside every environment control.

    Mutation: delete the `env_binding` check from `stamp_rows` -- all three cases red.
    """
    monkeypatch.setattr(bm.ec, "resolve", _resolving(binding))
    cells = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    with pytest.raises(SystemExit) as exc:
        bm.stamp_rows(cells, _MINIMAL_TABLE, "plan")
    assert str(exc.value) == (
        f"::error::stacks/app in dev-eu resolved env_binding {shown}, which names no GitHub "
        "Environment. A job bound to it would run outside every environment control, so no "
        "matrix is written."
    )


def test_main_writes_no_matrix_when_a_binding_refuses(monkeypatch, tmp_path):
    """The refusal lands before the matrix is written, so no half-written matrix reaches a job
    that would bind it; only the refusal line is.

    Mutation: delete the `env_binding` check from `stamp_rows` -- main writes the matrix.
    """
    monkeypatch.setattr(bm.ec, "resolve", _resolving(""))
    env = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REPOSITORY": "acme/iac",
        "SHIPMATE_HEAD_REPO": "acme/iac",
    }
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, tmp_path, env, head_sha="cafe1234")
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == (
        "refusal=stacks/app in dev-eu resolved env_binding '', which names no GitHub "
        "Environment. A job bound to it would run outside every environment control, so no "
        "matrix is written.\n"
    )


def test_a_refusal_reaches_the_refusal_output_and_still_fails_the_step(monkeypatch, tmp_path):
    """Mutation: drop the `raise` in `main`'s handler -- no `SystemExit` reaches the test."""
    tree = {"stacks/a": ["env/dev", "workload/net", "workload/web"]}
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch, tmp_path, _PLAN_ENV, head_sha="a" * 40, table=_CORE_ONLY, stacks=tree
        )
    assert exc.value.code == _TWO_TAG_A
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == (
        "refusal=stack 'stacks/a' carries 2 workload tags (workload/net, workload/web), and a "
        "stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` and "
        "remove the rest.\n"
    )


def test_a_multi_line_refusal_writes_its_first_line_only(monkeypatch, tmp_path):
    """A second line would end the output's value and start a malformed one.

    Mutation: write the whole message as the `refusal` line -- the gap line follows it."""
    tree = {
        "stacks/a": ["env/dev", "workload/net", "workload/web"],
        "stacks/c": ["env/dev", "workload/app"],
    }
    with pytest.raises(SystemExit):
        _run_main(
            monkeypatch, tmp_path, _PLAN_ENV, head_sha="a" * 40, table=_CORE_ONLY, stacks=tree
        )
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == (
        "refusal=stack 'stacks/a' carries 2 workload tags (workload/net, workload/web), and a "
        "stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` and "
        "remove the rest.\n"
    )


def test_an_exit_that_is_not_a_refusal_writes_no_refusal(monkeypatch, tmp_path):
    """Mutation: drop the `isinstance` check -- `int.startswith` raises `AttributeError`."""

    def exit_one(repository, head_repo, no_pull_request):
        raise SystemExit(1)

    monkeypatch.setattr(bm, "fork_pr_error", exit_one)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, tmp_path, _PLAN_ENV, head_sha="a" * 40)
    assert exc.value.code == 1
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == ""


#: `dev-eu` lists `core` and `net`; `prod` names no identity, so a tag there is inert.
_LISTED = {
    "layout": "folder",
    "identities": {"dev": {"aws": {"account": "111111111111", "apply": "deploy-{workload}"}}},
    "environments": {
        "dev-eu": {"region": "eu-west-1", "identity": "dev", "workloads": ["net", "core"]},
        "prod": {"region": "eu-west-1"},
    },
}


def test_every_cell_tagged_outside_its_list_is_named_in_one_refusal():
    """Mutation: raise inside the loop -- only stacks/app is named."""
    cells = [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
        {"stack": "stacks/net", "environment": "dev-eu", "workload": "net"},
        {"stack": "stacks/free", "environment": "prod", "workload": "app"},
        {"stack": "stacks/db", "environment": "dev-eu", "workload": "db"},
        {"stack": "stacks/bare", "environment": "dev-eu", "workload": ""},
    ]
    with pytest.raises(SystemExit) as exc:
        bm.refuse_workload_gaps(cells, _LISTED)
    assert str(exc.value) == (
        "::error::2 cell(s) carry a workload tag their environment's workloads list does not "
        "name: stacks/app in dev-eu (workload/app; dev-eu lists core, net); stacks/db in dev-eu "
        "(workload/db; dev-eu lists core, net). A listed workload is the only one the default "
        "branch grants a role to. Retag the stack, or add the workload to "
        "environments.<env>.workloads in .github/shipmate.toml on the default branch, which is "
        "where this table is read from: merge it there on its own pull request first."
    )


_PLAN_ENV = {
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_REPOSITORY": "acme/iac",
    "SHIPMATE_HEAD_REPO": "acme/iac",
}
#: Both environments name an identity and list only `core`, so every other tag is outside.
_CORE_ONLY = {
    "layout": "folder",
    "identities": {"dev": {"aws": {"account": "111111111111", "apply": "deploy-{workload}"}}},
    "environments": {
        "dev": {"region": "eu-west-1", "identity": "dev", "workloads": ["core"]},
        "prod": {"region": "eu-west-1", "identity": "dev", "workloads": ["core"]},
    },
}
_TWO_TAG_A = (
    "::error::stack 'stacks/a' carries 2 workload tags (workload/net, workload/web), and a "
    "stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` and remove "
    "the rest."
)
_TWO_TAG_B = (
    "::error::stack 'stacks/b' carries 2 workload tags (workload/db, workload/dns), and a "
    "stack carries at most one `workload/<name>` tag. Keep one in the stack's `tags` and remove "
    "the rest."
)


def test_plan_detect_names_every_two_tag_stack_and_every_gap_in_one_refusal(monkeypatch, tmp_path):
    """stacks/a sits in two environments and is named once; stacks/c's gap follows.

    Mutations: raise the two-tag messages before the table is read in `main` -- the gap line
    is missing; return the first workload instead of `None` from `workload_of`'s list branch --
    the gap line also names stacks/a and stacks/b; raise inside `workload_of` when given a list
    -- only stacks/a is named; call `workload_of` per cell in `full_tree` -- stacks/a is named
    twice."""
    tree = {
        "stacks/a": ["env/dev", "env/prod", "workload/net", "workload/web"],
        "stacks/b": ["env/dev", "workload/db", "workload/dns"],
        "stacks/c": ["env/dev", "workload/app"],
        "stacks/d": ["env/prod", "workload/core"],
    }
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch, tmp_path, _PLAN_ENV, head_sha="a" * 40, table=_CORE_ONLY, stacks=tree
        )
    assert exc.value.code == "\n".join(
        [
            _TWO_TAG_A,
            _TWO_TAG_B,
            "::error::1 cell(s) carry a workload tag their environment's workloads list does "
            "not name: stacks/c in dev (workload/app; dev lists core). A listed workload is the "
            "only one the default branch grants a role to. Retag the stack, or add the workload "
            "to environments.<env>.workloads in .github/shipmate.toml on the default branch, "
            "which is where this table is read from: merge it there on its own pull request "
            "first.",
        ]
    )


def test_plan_detect_names_a_two_tag_stack_and_a_table_refusal_together(monkeypatch, tmp_path):
    """Mutation: raise the two-tag messages before the table is read in `main` -- the table
    error is missing."""
    tree = {"stacks/a": ["env/dev", "workload/net", "workload/web"]}
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _PLAN_ENV,
            head_sha="a" * 40,
            table={"layout": "bogus"},
            stacks=tree,
        )
    assert exc.value.code == (
        _TWO_TAG_A + "\n::error::layout is 'bogus'; it must be one of tf_vars, workspace, folder."
    )


def test_a_cell_the_table_no_longer_lists_stamps_with_no_role():
    """`stamp_rows` resolves every cell, completed ones included, before the caller filters
    and refuses; a table-only change that drops a workload must strand no applied cell.

    Mutation: make `resolve` raise for a tag outside the list -- this raises.
    """
    cells = [{"stack": "stacks/db", "environment": "dev-eu", "workload": "db"}]
    assert bm.stamp_rows(cells, _LISTED, "apply") == [
        {
            "stack": "stacks/db",
            "environment": "dev-eu",
            "workload": "db",
            "role_arn": "",
            "cred_region": "eu-west-1",
            "tf_vars": {},
            "config_path": "apply",
            "env_binding": "dev-eu-apply",
        }
    ]


_EMPTY_TERM_ERROR = (
    "::error::the `tags` filter has an empty term: '{query}'. Terms are separated "
    "by ',' (OR) and ':' (AND), and each must be a tag in its on-disk form -- "
    "'env/dev-eu,env/dev-us' or 'env/dev-eu:workload/app'."
)
_NO_MATCH_NOTICE = (
    "::notice::the drift tags query '{query}' matches no stack x environment cell, so this "
    "sweep is empty."
)
_ON_DISK = " Tags match in their on-disk form, such as 'env/dev-eu'."


def _filtered(envs, stacks_by_env, tags_by_stack, query):
    return bm.filter_cells(bm.full_tree(envs, stacks_by_env, tags_by_stack), tags_by_stack, query)


def test_tag_filter_matches_a_cell_against_its_own_env_only():
    """A stack in two envs, filtered by one of them, yields that env's cell alone.

    Mutation: `_cell_tags` returns the raw `tags_by_stack` entry -- the prod-eu cell then
    carries `env/dev-eu` too and both cells survive.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/app"], "prod-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu", "env/prod-eu"]},
        "env/dev-eu",
    )
    assert cells == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]


def test_tag_filter_on_a_non_env_tag_keeps_every_env_of_that_stack():
    """`workload/app` is not narrowed by the cell's env, so both cells survive.

    Mutation: `_cell_tags` keeps only the cell's own `env/*` tag -- nothing then carries
    `workload/app` and the sweep is empty.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/app"], "prod-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu", "env/prod-eu", "workload/app"]},
        "workload/app",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [
        ("dev-eu", "stacks/app"),
        ("prod-eu", "stacks/app"),
    ]


def test_colon_binds_tighter_than_comma():
    """`a:b,c` is `(a AND b) OR c`, not `a OR (b AND c)`.

    Mutation: split on ':' outermost and ',' within -- the clauses become `[{a}, {b, c}]`,
    which keeps the a-only stack and drops the c-only one.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/bare", "stacks/app"], "prod-eu": ["stacks/other"]},
        {
            "stacks/bare": ["env/dev-eu"],
            "stacks/app": ["env/dev-eu", "workload/app"],
            "stacks/other": ["env/prod-eu"],
        },
        "env/dev-eu:workload/app,env/prod-eu",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [
        ("dev-eu", "stacks/app"),
        ("prod-eu", "stacks/other"),
    ]


def test_comma_is_or_across_clauses():
    """A cell matching either clause is kept.

    Mutation: `all` replaces `any` over the clauses -- no stack carries both workloads, so
    the sweep is empty.
    """
    cells = _filtered(
        ["dev-eu"],
        {"dev-eu": ["stacks/a", "stacks/b"]},
        {
            "stacks/a": ["env/dev-eu", "workload/a"],
            "stacks/b": ["env/dev-eu", "workload/b"],
        },
        "workload/a,workload/b",
    )
    assert [c["stack"] for c in cells] == ["stacks/a", "stacks/b"]


def test_colon_is_and_within_a_clause():
    """A two-term clause keeps only a stack carrying both terms.

    Mutation: test the clause by intersection rather than subset -- the stack carrying
    `workload/app` alone then matches too.
    """
    cells = _filtered(
        ["dev-eu"],
        {"dev-eu": ["stacks/both", "stacks/one"]},
        {
            "stacks/both": ["env/dev-eu", "workload/app", "team/core"],
            "stacks/one": ["env/dev-eu", "workload/app"],
        },
        "workload/app:team/core",
    )
    assert [c["stack"] for c in cells] == ["stacks/both"]


def test_spaces_around_terms_match_as_without_them():
    """Terms are stripped, so a query written with spaces after its commas sweeps the same cells.

    Mutation: drop `.strip()` in `_parse_tag_query` -- `env/dev-eu ` is carried by no stack and
    the spaced query sweeps nothing.
    """
    args = (
        ["dev-eu", "dev-us", "prod-eu"],
        {"dev-eu": ["stacks/app"], "dev-us": ["stacks/app"], "prod-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu", "env/dev-us", "env/prod-eu"]},
    )
    expected = [("dev-eu", "stacks/app"), ("dev-us", "stacks/app")]
    for query in ("env/dev-eu , env/dev-us", "env/dev-eu,env/dev-us"):
        assert [(c["environment"], c["stack"]) for c in _filtered(*args, query)] == expected


def test_a_term_no_stack_carries_drops_only_its_own_clause(monkeypatch, tmp_path, capsys):
    """`env/dev-eu,env/nope` sweeps the dev-eu cells, and one notice names `env/nope`: a query
    written before an environment's first stack still sweeps the environments that exist.

    Mutations: return `[]` whenever a term is unknown -- the matrix is empty; drop the notice
    on the matching path -- stdout lacks it.
    """
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu,env/nope"},
        stacks=_MULTI_ENV_TREE,
    )
    include = json.loads(outputs["matrix"])["include"]
    assert [(c["environment"], c["stack"]) for c in include] == [
        ("dev-eu", "stacks/app"),
        ("dev-eu", "stacks/db"),
    ]
    assert capsys.readouterr().out.splitlines() == [
        "::notice::the drift tags query 'env/dev-eu,env/nope' has clause(s) matching no cell: "
        "env/nope. No stack carries: env/nope." + _ON_DISK,
        "2 cell(s): dev-eu/stacks/app, dev-eu/stacks/db",
    ]


def test_conceptual_tag_form_names_both_halves_as_carried_by_no_stack(capsys):
    """`env:dev-eu` is the documentation form; on disk the tag is `env/dev-eu`.

    Mutation: translate ':' to '/' before matching -- the typo then silently sweeps.
    """
    cells = _filtered(
        ["dev-eu"], {"dev-eu": ["stacks/app"]}, {"stacks/app": ["env/dev-eu"]}, "env:dev-eu"
    )
    assert cells == []
    assert capsys.readouterr().out.splitlines() == [
        _NO_MATCH_NOTICE.format(query="env:dev-eu") + " No stack carries: dev-eu, env." + _ON_DISK
    ]


@pytest.mark.parametrize("query", ["env/dev-eu,", "env/dev-eu::workload/app", "env/dev-eu,   "])
def test_empty_term_is_refused(query):
    """A trailing comma, a doubled separator and a whitespace-only clause abort.

    Mutation: drop the `"" in terms` check -- `env/dev-eu,` reaches the matcher with the term
    `""` and returns an empty sweep instead of refusing.
    """
    with pytest.raises(SystemExit) as exc_info:
        _filtered(
            ["dev-eu"],
            {"dev-eu": ["stacks/app"]},
            {"stacks/app": ["env/dev-eu", "workload/app"]},
            query,
        )
    assert str(exc_info.value) == _EMPTY_TERM_ERROR.format(query=query)


def test_known_terms_that_co_occur_nowhere_empty_the_sweep(capsys):
    """Every term exists, but no cell carries the conjunction: an empty sweep and a notice
    naming the query, with no `No stack carries` clause.

    Mutation: append the clause unconditionally -- the notice gains ` No stack carries: .`.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/db"], "prod-eu": ["stacks/app"]},
        {
            "stacks/db": ["env/dev-eu", "workload/db"],
            "stacks/app": ["env/prod-eu", "workload/app"],
        },
        "env/dev-eu:workload/app",
    )
    assert cells == []
    assert capsys.readouterr().out.splitlines() == [
        _NO_MATCH_NOTICE.format(query="env/dev-eu:workload/app")
    ]


def test_a_clause_of_known_terms_matching_no_cell_is_named_beside_a_live_one(capsys):
    """`env/prod-eu` and `workload/app` both exist but never share a cell, so the first clause
    sweeps nothing while `env/dev-eu` sweeps its cells. The dead clause is named, with no `No
    stack carries` sentence, because every term is carried.

    Mutation: notice only when a term is unknown -- stdout is empty.
    """
    cells = _filtered(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/app", "stacks/db"], "prod-eu": ["stacks/net"]},
        {
            "stacks/app": ["env/dev-eu", "workload/app"],
            "stacks/db": ["env/dev-eu"],
            "stacks/net": ["env/prod-eu"],
        },
        "env/prod-eu:workload/app,env/dev-eu",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [
        ("dev-eu", "stacks/app"),
        ("dev-eu", "stacks/db"),
    ]
    assert capsys.readouterr().out.splitlines() == [
        "::notice::the drift tags query 'env/prod-eu:workload/app,env/dev-eu' has clause(s) "
        "matching no cell: env/prod-eu:workload/app."
    ]


def test_a_repeated_dead_clause_is_named_once(capsys):
    """Mutation: drop the `dict.fromkeys` dedupe -- the notice names `env/nope` twice."""
    cells = _filtered(
        ["dev-eu"],
        {"dev-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu"]},
        "env/nope,env/nope,env/dev-eu",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [("dev-eu", "stacks/app")]
    assert capsys.readouterr().out.splitlines() == [
        "::notice::the drift tags query 'env/nope,env/nope,env/dev-eu' has clause(s) matching "
        "no cell: env/nope. No stack carries: env/nope." + _ON_DISK
    ]


@pytest.mark.parametrize("query", ["", "   "])
def test_an_empty_query_is_no_filter_at_all(query):
    """An absent `tags` input is not an empty term.

    Mutation: let an empty query fall through to the parser -- it refuses with the empty-term
    error instead of returning the unfiltered cells.
    """
    args = (
        ["dev-eu"],
        {"dev-eu": ["stacks/app", "stacks/db"]},
        {"stacks/app": ["env/dev-eu"], "stacks/db": ["env/dev-eu"]},
    )
    assert _filtered(*args, query) == bm.full_tree(*args)


#: `stacks/app` sits in both environments; `stacks/db` only in dev-eu, `stacks/web` only in
#: dev-us.
_MULTI_ENV_TREE = {
    "stacks/app": ["env/dev-eu", "env/dev-us"],
    "stacks/db": ["env/dev-eu"],
    "stacks/web": ["env/dev-us"],
}
#: The full tree of `_MULTI_ENV_TREE`, hand-written in matrix order.
_MULTI_ENV_CELLS = [
    {"environment": "dev-eu", "stack": "stacks/app"},
    {"environment": "dev-eu", "stack": "stacks/db"},
    {"environment": "dev-us", "stack": "stacks/app"},
    {"environment": "dev-us", "stack": "stacks/web"},
]


def test_a_one_env_query_plans_that_env_and_outputs_the_whole_tree(monkeypatch, tmp_path):
    """`env/dev-eu` plans the dev-eu cells alone, and `cells` still names the dev-us ones, so
    drift-issues does not close their Issues.

    Mutations: write `cells` from the filtered list in `main`; build the tree from the filtered
    cells in `compute_cells`; `_cell_tags` returns the raw stack tags (the dev-us `stacks/app`
    cell enters the matrix).
    """
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu"},
        stacks=_MULTI_ENV_TREE,
    )
    include = json.loads(outputs["matrix"])["include"]
    assert [(c["environment"], c["stack"]) for c in include] == [
        ("dev-eu", "stacks/app"),
        ("dev-eu", "stacks/db"),
    ]
    assert json.loads(outputs["cells"]) == _MULTI_ENV_CELLS


def test_a_query_matching_no_cell_is_an_empty_sweep_with_a_notice(monkeypatch, tmp_path, capsys):
    """Exit 0 with an empty matrix: a drift file may precede its first tagged stack. `cells`
    stays the whole tree, so no Issue is closed for a cell the query never planned.

    Mutations: refuse a no-match query with `SystemExit` as before; drop the ` No stack carries`
    clause. The whole stdout is compared, so a second notice or a lost sentence reddens too.
    """
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu:workload/nope"},
        stacks=_MULTI_ENV_TREE,
    )
    assert outputs["empty"] == "true"
    assert outputs["count"] == "0"
    assert json.loads(outputs["cells"]) == _MULTI_ENV_CELLS
    assert capsys.readouterr().out.splitlines() == [
        _NO_MATCH_NOTICE.format(query="env/dev-eu:workload/nope")
        + " No stack carries: workload/nope."
        + _ON_DISK,
        "0 cell(s): (none)",
    ]


def test_a_filtered_sweep_still_checks_every_environment_against_a_tf_vars_table(
    monkeypatch, tmp_path
):
    """The table covers dev-eu alone and the query sweeps dev-eu alone, yet a dev-us stack is
    still refused: the coverage check runs over the whole tree.

    Mutation: `env_config(cells, ...)` in `main` -- the dev-eu sweep plans.
    """
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            {**_DRIFT_ENV, "SHIPMATE_TAGS": "env/dev-eu"},
            table={"layout": "tf_vars", "environments": {"dev-eu": {"region": "eu-west-1"}}},
            stacks=_MULTI_ENV_TREE,
        )
    assert str(exc.value) == (
        '::error::layout = "tf_vars" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and dev-us has no entry in it."
    )


def test_a_filtered_sweep_still_refuses_a_slug_collision_outside_it(monkeypatch):
    """`a/b` and `a-b` collide in dev-us only, and the query sweeps dev-eu: still refused.

    Mutation: run `guard_slug_collisions` over the filtered cells instead of the tree.
    """
    tree = {"stacks/app": ["env/dev-eu"], "a/b": ["env/dev-us"], "a-b": ["env/dev-us"]}
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "_tags", lambda s: tree[s])
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True, tags="env/dev-eu")
    assert str(exc_info.value) == (
        "::error::a-b, a/b all map to the plan artifact 'plan.dev-us.a-b': distinct "
        "stack paths sharing one artifact name would make an apply download another "
        "stack's plan. Rename one so the path->'-' slug is unique."
    )


def test_a_filtered_sweep_still_refuses_two_workload_tags_outside_it(monkeypatch):
    """stacks/dns is outside the `env/dev-eu` sweep, and its two workload tags still refuse it.

    Mutation: stamp `workload` on the filtered cells instead of the tree -- the sweep plans.
    """
    tree = {
        "stacks/app": ["env/dev-eu"],
        "stacks/dns": ["env/prod-eu", "workload/network", "workload/net"],
    }
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: list(tree))
    monkeypatch.setattr(bm, "_tags", lambda s: tree[s])
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True, tags="env/dev-eu")
    assert exc_info.value.code == _TWO_WORKLOADS_ERROR


def test_a_tag_filter_does_not_hide_a_workload_from_drift(monkeypatch, tmp_path, capsys):
    """The filter drops `stacks/net`, the only stack tagging `workload/net`, from the cells; the
    tag still exists, so drift must not report `net` as untagged on every sweep.

    Mutation: derive `tagged` from the filtered cells in `compute_cells` -- the warning names
    `net`."""
    _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "workload/app"},
        table=_LISTING_TABLE,
        stacks=_WORKLOAD_TREE,
    )
    assert capsys.readouterr().out.splitlines() == ["1 cell(s): dev-eu/stacks/app"]


_TAG_FILTER_ERROR = (
    "::error::the `tags` filter is only for a workflow with no pull request at all "
    "(a drift sweep), and this run did not pass `no-pull-request: true`. In a plan "
    "job it would drop changed stacks from the matrix: a dropped stack gets no plan "
    "cell and no apply check, `shipmate / gate` greens over it, and the change merges "
    "and never applies. Remove the input from the plan job."
)


def test_main_refuses_a_tag_filter_on_a_plan_run_and_does_not_enumerate(monkeypatch, tmp_path):
    """A `tags` value with no `no-pull-request` aborts before the stacks are listed.

    Mutation: drop `tag_filter_error` from `main`'s `or` chain -- the run plans a narrowed
    matrix and `called` records the enumeration.
    """
    called = []
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
                "SHIPMATE_TAGS": "env/dev-eu",
            },
            called=called,
            head_sha="cafe1234",
        )
    assert str(exc_info.value) == _TAG_FILTER_ERROR
    assert called == []


def test_all_stacks_does_not_exempt_the_tag_filter_refusal(monkeypatch, tmp_path):
    """`all-stacks: true` plus `tags` on a plan run is still refused.

    Mutation: add `all_stacks` to the exemption -- a plan wrapper setting both would drop
    changed stacks from the matrix, so they get no plan cell and no apply check,
    `shipmate / gate` greens, and the change merges unapplied.
    """
    called = []
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "acme/iac",
                "SHIPMATE_HEAD_REPO": "acme/iac",
                "SHIPMATE_ALL_STACKS": "true",
                "SHIPMATE_TAGS": "env/dev-eu",
            },
            called=called,
            head_sha="cafe1234",
        )
    assert str(exc_info.value) == _TAG_FILTER_ERROR
    assert called == []


def test_the_tag_filter_refusal_is_keyed_on_the_value():
    """A composite action's `required:`/`default:` is not enforced by GitHub Actions, so the
    refusal reads the value: any non-empty query outside a no-pull-request run is refused,
    and an absent input arrives as "" and is no filter at all.

    Mutation: `tag_filter_error` returns "" unconditionally -- the first case reddens."""
    assert bm.tag_filter_error("env/dev-eu", False) == _TAG_FILTER_ERROR
    assert bm.tag_filter_error("", False) == ""
    assert bm.tag_filter_error("   ", False) == ""
    assert bm.tag_filter_error("env/dev-eu", True) == ""


def test_a_drift_run_passes_the_query_through_verbatim(monkeypatch, tmp_path):
    """`compute_cells` is handed the query exactly as the input stated it.

    Mutation: hard-code `""` as main's `compute_cells` query, or strip the query there --
    the surrounding and inner whitespace here is the parser's to strip, and a second reading
    of the grammar is a second grammar.
    """
    _, called = _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": " env/dev-eu ,workload/app"},
    )
    assert called == [(True, "", " env/dev-eu ,workload/app")]


def test_an_env_tag_naming_no_table_entry_still_refuses_under_tf_vars(monkeypatch, tmp_path):
    """`env/nope` is a tag naming an environment the table lacks, not an unmanaged stack.

    Mutation: in `env_membership`, drop `env/*` tags naming no table entry -- the sweep plans.
    """
    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            tmp_path,
            _DRIFT_ENV,
            table={"layout": "tf_vars", "environments": {"dev-eu": {"region": "eu-west-1"}}},
            stacks={"stacks/app": ["env/dev-eu"], "stacks/typo": ["env/nope"]},
        )
    assert str(exc.value) == (
        '::error::layout = "tf_vars" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and nope has no entry in it."
    )


@pytest.mark.parametrize(
    ("stacks", "expected"),
    [
        (
            {"stacks/app": ["env/dev-eu"], "stacks/z": [], "stacks/a": ["workload/x"]},
            {"count": 2, "paths": ["stacks/a", "stacks/z"]},
        ),
        ({"stacks/app": ["env/dev-eu"]}, {"count": 0, "paths": []}),
        (
            _TWENTY_UNMANAGED,
            {"count": 20, "paths": [f"stacks/u{i:02}" for i in range(10)]},
        ),
    ],
    ids=["two", "none", "twenty"],
)
def test_main_writes_the_unmanaged_count_and_the_first_ten_paths(
    monkeypatch, tmp_path, stacks, expected
):
    """Written on every run, `{"count": 0, "paths": []}` when none; the paths are capped so the
    value stays small enough for one environment variable. Mutation: omit the line when there
    are none. Mutation: emit every path -- `twenty` gets twenty."""
    outputs, _ = _run_main(monkeypatch, tmp_path, _DRIFT_ENV, stacks=stacks)
    assert json.loads(outputs["unmanaged"]) == expected


def test_a_pull_request_plan_writes_its_changed_unmanaged_stacks(monkeypatch, tmp_path):
    """The plan path (`all_stacks=False`) is what the comment line reads. Mutation: write
    `unmanaged` only under `all_stacks` -- the key is missing."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_HEAD_REPO": "acme/iac",
        },
        head_sha="cafe1234",
        stacks={"stacks/app": ["env/dev-eu"], "stacks/new": []},
    )
    assert [c["stack"] for c in json.loads(outputs.pop("matrix"))["include"]] == ["stacks/app"]
    assert outputs == {
        "empty": "false",
        "count": "1",
        "cells": '[{"environment": "dev-eu", "stack": "stacks/app"}]',
        "unmanaged": '{"count": 1, "paths": ["stacks/new"]}',
    }
