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


def test_whole_tree_sweep_above_256_cells_names_no_split(monkeypatch):
    """A whole-tree run (drift) cannot be split across pull requests.

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
        "A whole-tree sweep covers every stack and environment cell and cannot be split; "
        "the only lever is fewer environments or fewer env-tagged stacks."
    )


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
    # Happy path only -- does NOT exercise the untagged-stack guard.
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["env/dev-eu", "env/dev-us", "workload/app"])
    _, cells = bm.compute_cells(all_stacks=True)
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
        {"stack": "stacks/app", "environment": "dev-us", "workload": "app"},
    ]


def test_compute_cells_raises_on_untagged_stack(monkeypatch):
    # A stack with no env/* tag would silently vanish from plan, apply and drift, so
    # compute_cells fails loud instead.
    monkeypatch.setattr(
        bm, "_list_stacks", lambda all_stacks, base: ["stacks/app", "stacks/orphan"]
    )
    monkeypatch.setattr(
        bm,
        "_tags",
        lambda s: ["env/dev-eu"] if s == "stacks/app" else ["workload/net"],
    )
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True)
    assert "stacks/orphan" in str(exc_info.value)
    assert "stacks/app" not in str(exc_info.value)


def test_untagged_failure_names_the_count_and_every_stack(monkeypatch):
    # So a migration can be re-run and watched shrink.
    stacks = ["stacks/zeta", "stacks/alpha", "stacks/mid"]
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: stacks)
    monkeypatch.setattr(bm, "_tags", lambda s: ["workload/util"])
    with pytest.raises(SystemExit) as exc_info:
        bm.env_membership(all_stacks=True)
    assert str(exc_info.value) == (
        "::error::3 stack(s) have no env/* tag and cannot fan out to any "
        "environment (they would silently skip): stacks/alpha, stacks/mid, stacks/zeta"
    )


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


def test_env_membership_fails_loud_on_untagged_stack(monkeypatch):
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/orphan"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["workload/app"])
    with pytest.raises(SystemExit):
        bm.env_membership(all_stacks=True)


def test_env_membership_require_env_tag_false_ignores_untagged(monkeypatch):
    # The artifact-sourced bare-apply path passes require_env_tag=False: an untagged stack
    # anywhere in the repo must not abort membership. It produces no plan.<env>.<slug>
    # artifact and no cell, so it vanishes from the map while tagged stacks still bucket.
    stacks = ["stacks/app", "stacks/orphan"]
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: stacks)
    tags = {"stacks/app": ["env/dev-eu"], "stacks/orphan": ["workload/util"]}
    monkeypatch.setattr(bm, "_tags", lambda s: tags[s])
    stacks_by_env, tags_by_stack = bm.env_membership(all_stacks=True, require_env_tag=False)
    assert stacks_by_env == {"dev-eu": ["stacks/app"]}
    assert tags_by_stack == tags  # The orphan is still reported in tags, not bucketed.


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
def test_only_the_bool_true_skips_the_two_refusals(monkeypatch, value):
    """main() parses the input to a bool, so a raw string here is a caller that forgot to parse.
    A truthy check would skip the fork refusal for "false".

    Mutation: `if no_pull_request is True:` -> `if no_pull_request:` in either helper -- its
    str-false, str-true and one rows redden."""
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    assert bm.fork_pr_error("acme/iac", "outsider/iac", value).startswith("::error::")
    assert bm.head_checkout_error("cafe1234", value).startswith("::error::")


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
):
    """main() with GITHUB_OUTPUT redirected, returning (parsed outputs, calls) where calls
    records compute_cells' arguments, so a rejection is observable as the stack enumeration
    never having run. Pass `called` to keep that record readable when main() raises.

    `stacks`, a `{stack: [tags]}` map, runs the real `compute_cells` over that tree instead of
    the double, and leaves `called` and `cells` unused.

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
    ):
        monkeypatch.delenv(k, raising=False)
    if head_sha is not None:
        monkeypatch.setenv("SHIPMATE_HEAD_SHA", head_sha)
        monkeypatch.setattr(bm, "_run", lambda args: f"{head_sha}\n")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    called = [] if called is None else called

    def fake_compute(all_stacks=False, base=""):
        called.append((all_stacks, base))
        # The whole row `build_matrix` emits, `workload` included: a double that omits a
        # key the real builder always adds cannot fail on a guard that pins the row shape.
        rows = [{"stack": s, "environment": e, "workload": ""} for s, e in cells]
        # The real `compute_cells` returns the env->workloads map beside the rows, and `main`
        # forwards it as `tagged` only under `all_stacks`. The rows tag no workload.
        return {e: frozenset() for _, e in cells}, rows

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
    assert called == [(False, "")]


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
    assert called == [(True, "")]


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
def test_main_parses_the_opt_out_once_for_both_guards(monkeypatch, tmp_path, value, opted_out):
    """Case- and whitespace-insensitive, so a `no-pull-request: True` does not redden a nightly
    over YAML capitalisation. Only "true" opts out: the manifest default is the non-empty string
    "false", so anything that treats a non-empty value as the opt-out would plan every unstated
    run unchecked.

    Mutations: drop `.strip()` or `.lower()` from main's parse -- a padded or capitalised row
    reddens; parse with `bool(...)` -- every non-empty `False` row reddens; pass the raw variable
    to one guard -- that guard records a string."""
    seen = []
    for name in ("fork_pr_error", "head_checkout_error"):
        monkeypatch.setattr(bm, name, lambda *args, _n=name: seen.append((_n, args[-1])) or "")
    _run_main(monkeypatch, tmp_path, {"SHIPMATE_NO_PULL_REQUEST": value})
    assert seen == [
        ("fork_pr_error", opted_out),
        ("head_checkout_error", opted_out),
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
        "`shipmate.yml` probe checks its job name, dispatch wiring and event routing, and by "
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
    that there is no pull request at all. All are settable only by this repository's own
    default-branch workflow, which a pull-request author cannot edit, and the direction is
    chosen so a forgotten input refuses (plan wrapper) or reddens the nightly (drift), never
    plans a fork.

    Hand-written, name -> default; descriptions are prose and not pinned."""
    from _loader import action_yaml

    doc = action_yaml("build-matrix")
    assert {name: spec.get("default") for name, spec in doc["inputs"].items()} == {
        "base-sha": None,
        "all-stacks": "false",
        "head-repo": "",
        "head-sha": "",
        "no-pull-request": "false",
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
        "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
        "GH_TOKEN": "${{ github.token }}",
    }


def test_build_matrix_action_declares_the_outputs_the_gate_reads():
    # `count` is what the trusted summary job measures its evidence against, so
    # a rename or a rewire here is a silent hole in the gate. Hand-written,
    # name -> wiring; descriptions are prose and deliberately not pinned.
    doc = action_yaml("build-matrix")
    assert {name: spec["value"] for name, spec in doc["outputs"].items()} == {
        "matrix": "${{ steps.build.outputs.matrix }}",
        "empty": "${{ steps.build.outputs.empty }}",
        "count": "${{ steps.build.outputs.count }}",
        "cells": "${{ steps.build.outputs.cells }}",
    }


def test_the_cells_output_is_the_cell_names_in_matrix_order(monkeypatch, tmp_path):
    """The names alone: a stamped row carries `tf_vars` and roles, and 256 of them in one env
    var can pass Linux's single-argument limit. Mutation: emit the stamped rows as `cells`."""
    outputs, _ = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_NO_PULL_REQUEST": "true",
        },
        cells=(("stacks/db", "prod-eu"), ("stacks/app", "dev-eu")),
    )
    assert outputs["cells"] == (
        '[{"environment": "prod-eu", "stack": "stacks/db"}, '
        '{"environment": "dev-eu", "stack": "stacks/app"}]'
    )


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
    """The refusal lands before `GITHUB_OUTPUT` is opened, so no half-written matrix reaches a
    job that would bind it.

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
