import json

import pytest
from _detect_fixtures import spy_env_config
from _loader import action_yaml, load_script

bm = load_script("build-matrix")

#: What a detect reads when a test names no table. `layout` is required, and `folder`
#: derives no identity variables, so a row carries only the stamp and the tier.
_MINIMAL_TABLE = {"layout": "folder"}


def _no_read(run=None):
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


def test_rejects_stack_path_exactly_apply():
    """A stack path of exactly `apply` is refused, with the whole message pinned.

    Reddens on dropping `"apply"` from `RESERVED_STACK_PATHS` (no SystemExit), and on any
    edit to its message -- the comparison is against a hand-written constant, so a reason
    rewritten back into the retired collision claim fails here.
    """
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(["dev-eu"], {"dev-eu": ["apply"]}, {"apply": ["env/dev-eu"]})
    assert exc_info.value.code == (
        "::error::a stack path may not be exactly 'apply': 'apply' is the engine's own "
        "verb, and its checks are named 'apply / <stack> / <env>'. Rename or nest the stack."
    )


def test_nested_apply_stack_is_allowed():
    # Only an exact top-level `apply` is reserved; `infra/apply` reads as a stack, not as
    # the engine's verb.
    cells = bm.build_matrix(
        ["dev-eu"], {"dev-eu": ["infra/apply"]}, {"infra/apply": ["env/dev-eu"]}
    )
    assert cells == [{"stack": "infra/apply", "environment": "dev-eu", "workload": ""}]


def test_rejects_stack_path_exactly_shipmate():
    """A stack path of exactly `shipmate` is refused, with the whole message pinned.

    Reddens on dropping `"shipmate"` from `RESERVED_STACK_PATHS` (no SystemExit), and on any
    edit to its message -- the comparison is against a hand-written constant, so a reason
    rewritten back into the retired collision claim fails here.
    """
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(["dev-eu"], {"dev-eu": ["shipmate"]}, {"shipmate": ["env/dev-eu"]})
    assert exc_info.value.code == (
        "::error::a stack path may not be exactly 'shipmate': 'shipmate / ' is the engine's "
        "own check namespace (e.g. 'shipmate / gate', 'shipmate / summary'). Rename or nest "
        "the stack."
    )


def test_nested_shipmate_stack_is_allowed():
    cells = bm.build_matrix(
        ["dev-eu"], {"dev-eu": ["infra/shipmate"]}, {"infra/shipmate": ["env/dev-eu"]}
    )
    assert cells == [{"stack": "infra/shipmate", "environment": "dev-eu", "workload": ""}]


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
        "SHIPMATE_TAGS",
    ):
        monkeypatch.delenv(k, raising=False)
    if head_sha is not None:
        monkeypatch.setenv("SHIPMATE_HEAD_SHA", head_sha)
        monkeypatch.setattr(bm, "_run", lambda args: f"{head_sha}\n")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    called = [] if called is None else called

    def fake_compute(all_stacks=False, base="", require_env_tag=True, tags=""):
        called.append((all_stacks, base, tags))
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
    monkeypatch.setattr(bm.ec, "read_table", lambda run=None: dict(table or _MINIMAL_TABLE))
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
    """Case- and whitespace-insensitive, so a `no-pull-request: True` does not redden a nightly
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


def test_plan_workflow_at_the_contract_path_is_planned(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "shipmate.yml").write_text("", encoding="utf-8")
    assert bm.plan_workflow_error("pull_request_target", str(tmp_path)) == ""


def test_a_renamed_plan_workflow_is_refused(tmp_path):
    """This refusal makes the path load-bearing: no plan-run lookup matches it literally any
    more, so a rename would merge green while doctor's filename-keyed probes went quiet. The
    whole message is hand-written, and names only consequences still true now that the plan
    run id rides on each apply check and `actions/dispatch` aims every verb at this one file;
    a clause about plan-run discovery coming back here would be a falsehood."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "shipmate-plan.yml").write_text("", encoding="utf-8")
    assert bm.plan_workflow_error("pull_request", str(tmp_path)) == (
        "::error::this repository has no `.github/workflows/shipmate.yml` — the one path "
        "`CONTRACT.md` lets the consumer's workflow file live at, and this refusal is what "
        "enforces it. That exact filename is matched literally by `shipmate doctor`, which "
        "keys its calling-job-name, dispatch-wiring and routing probes on it, and by "
        "`actions/dispatch`, which sends every commented verb to it. A consumer workflow "
        "under any other name silently loses those probes, draws doctor's own "
        "`pull_request_target` warning instead, and is reached by no `shipmate` command at "
        "all. Move the consumer's workflow back to `.github/workflows/shipmate.yml`."
    )


def test_plan_workflow_check_is_skipped_off_a_pull_request(tmp_path):
    for event in ("schedule", "workflow_dispatch", "push", ""):
        assert bm.plan_workflow_error(event, str(tmp_path)) == ""


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
    refuses (plan wrapper) or reddens the nightly (drift), never plans a fork.

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
    # `count` is what the trusted summary job measures its evidence against, so
    # a rename or a rewire here is a silent hole in the gate. Hand-written,
    # name -> wiring; descriptions are prose and deliberately not pinned.
    doc = action_yaml("build-matrix")
    assert {name: spec["value"] for name, spec in doc["outputs"].items()} == {
        "matrix": "${{ steps.build.outputs.matrix }}",
        "empty": "${{ steps.build.outputs.empty }}",
        "count": "${{ steps.build.outputs.count }}",
    }


def test_rejects_stack_paths_that_slug_to_one_artifact_name():
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


_UNKNOWN_TERM_ERROR = (
    "::error::the `tags` filter names tag(s) no stack carries: {terms}. "
    "Tags are matched in their ON-DISK form -- 'env/dev-eu', not 'env:dev-eu', because "
    "Terramate forbids ':' in a tag value and ':' in this filter means AND. A typo that "
    "quietly matched nothing would disable that slice's drift for good."
)

_EMPTY_TERM_ERROR = (
    "::error::the `tags` filter has an empty term: '{query}'. Terms are separated "
    "by ',' (OR) and ':' (AND), and each must be a tag in its on-disk form -- "
    "'env/dev-eu,env/dev-us' or 'env/dev-eu:workload/app'."
)


def test_tag_filter_matches_a_cell_against_its_own_env_only():
    """A stack in two envs, filtered by one of them, yields that env's cell alone.

    Fails when `_cell_tags` returns the raw `tags_by_stack` entry: the prod-eu
    cell then carries `env/dev-eu` too and both cells survive.
    """
    cells = bm.build_matrix(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/app"], "prod-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu", "env/prod-eu"]},
        tags="env/dev-eu",
    )
    assert cells == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]


def test_tag_filter_on_a_non_env_tag_keeps_every_env_of_that_stack():
    """`workload/app` is not narrowed by the cell's env, so both cells survive.

    Fails when `_cell_tags` keeps only the cell's own `env/*` tag and drops
    every other tag: nothing then carries `workload/app` and the filter aborts.
    """
    cells = bm.build_matrix(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/app"], "prod-eu": ["stacks/app"]},
        {"stacks/app": ["env/dev-eu", "env/prod-eu", "workload/app"]},
        tags="workload/app",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [
        ("dev-eu", "stacks/app"),
        ("prod-eu", "stacks/app"),
    ]


def test_colon_binds_tighter_than_comma():
    """`a:b,c` is `(a AND b) OR c`, not `a OR (b AND c)`.

    Fails when the query is split on ':' outermost and ',' within: the clauses
    become `[{a}, {b, c}]`, which keeps the a-only stack and drops the c-only one.
    """
    cells = bm.build_matrix(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/bare", "stacks/app"], "prod-eu": ["stacks/other"]},
        {
            "stacks/bare": ["env/dev-eu"],
            "stacks/app": ["env/dev-eu", "workload/app"],
            "stacks/other": ["env/prod-eu"],
        },
        tags="env/dev-eu:workload/app,env/prod-eu",
    )
    assert [(c["environment"], c["stack"]) for c in cells] == [
        ("dev-eu", "stacks/app"),
        ("prod-eu", "stacks/other"),
    ]


def test_comma_is_or_across_clauses():
    """A cell matching either clause is kept.

    Fails when `all` replaces `any` over the clauses: no stack carries both
    workloads, so the filter matches nothing and aborts.
    """
    cells = bm.build_matrix(
        ["dev-eu"],
        {"dev-eu": ["stacks/a", "stacks/b"]},
        {
            "stacks/a": ["env/dev-eu", "workload/a"],
            "stacks/b": ["env/dev-eu", "workload/b"],
        },
        tags="workload/a,workload/b",
    )
    assert [c["stack"] for c in cells] == ["stacks/a", "stacks/b"]


def test_colon_is_and_within_a_clause():
    """A two-term clause keeps only a stack carrying both terms.

    Fails when the clause is tested by intersection rather than subset: the
    stack carrying `workload/app` alone then matches too.
    """
    cells = bm.build_matrix(
        ["dev-eu"],
        {"dev-eu": ["stacks/both", "stacks/one"]},
        {
            "stacks/both": ["env/dev-eu", "workload/app", "team/core"],
            "stacks/one": ["env/dev-eu", "workload/app"],
        },
        tags="workload/app:team/core",
    )
    assert [c["stack"] for c in cells] == ["stacks/both"]


def test_unknown_tag_is_refused_by_name():
    """A term no stack carries aborts, naming the term.

    Fails when the unknown term only warns and the run continues: the known
    term still matches, so cells come back instead of SystemExit.
    """
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(
            ["dev-eu"],
            {"dev-eu": ["stacks/app"]},
            {"stacks/app": ["env/dev-eu", "workload/app"]},
            tags="env/dev-eu,workload/nope",
        )
    assert str(exc_info.value) == _UNKNOWN_TERM_ERROR.format(terms="workload/nope")


def test_conceptual_tag_form_is_refused_as_two_unknown_terms():
    """`env:dev-eu` is the documentation form; on disk the tag is `env/dev-eu`.

    Fails when ':' is translated to '/' before validation: the typo then
    silently works instead of naming both halves as unknown.
    """
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(
            ["dev-eu"],
            {"dev-eu": ["stacks/app"]},
            {"stacks/app": ["env/dev-eu"]},
            tags="env:dev-eu",
        )
    assert str(exc_info.value) == _UNKNOWN_TERM_ERROR.format(terms="dev-eu, env")


@pytest.mark.parametrize("query", ["env/dev-eu,", "env/dev-eu::workload/app", "env/dev-eu,   "])
def test_empty_term_is_refused(query):
    """A trailing comma, a doubled separator and a whitespace-only clause abort.

    Fails when empty terms are dropped before matching, silently either way: dropping one
    from `env/dev-eu,` leaves an empty clause, a subset of every cell's tags that matches
    everything, while dropping one from `env/dev-eu::workload/app` repairs the clause into
    the valid, narrower `{env/dev-eu, workload/app}`."""
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(
            ["dev-eu"],
            {"dev-eu": ["stacks/app"]},
            {"stacks/app": ["env/dev-eu", "workload/app"]},
            tags=query,
        )
    assert str(exc_info.value) == _EMPTY_TERM_ERROR.format(query=query)


def test_known_terms_that_co_occur_nowhere_are_refused():
    """Every term exists, but no cell carries the conjunction -- abort rather
    than hand back an empty matrix that reads as a quiet, healthy night.

    Fails when the zero-cell result returns `[]` instead of raising.
    """
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(
            ["dev-eu", "prod-eu"],
            {"dev-eu": ["stacks/db"], "prod-eu": ["stacks/app"]},
            {
                "stacks/db": ["env/dev-eu", "workload/db"],
                "stacks/app": ["env/prod-eu", "workload/app"],
            },
            tags="env/dev-eu:workload/app",
        )
    assert str(exc_info.value) == (
        "::error::the `tags` filter 'env/dev-eu:workload/app' matched no stack x environment "
        "cell. Every term is a tag some stack carries, so this is a conjunction that "
        "co-occurs nowhere (':' means AND, ',' means OR)."
    )


@pytest.mark.parametrize("query", ["", "   "])
def test_an_empty_query_is_no_filter_at_all(query):
    """An absent `tags` input is not an empty term.

    Fails when an empty query falls through to the parser: it would abort with
    the empty-term error instead of returning the unfiltered cells.
    """
    args = (
        ["dev-eu"],
        {"dev-eu": ["stacks/app", "stacks/db"]},
        {"stacks/app": ["env/dev-eu"], "stacks/db": ["env/dev-eu"]},
    )
    assert bm.build_matrix(*args, tags=query) == bm.build_matrix(*args)


def test_matrix_limit_measures_the_filtered_set(monkeypatch):
    """The ceiling counts the cells this run will actually produce.

    Fails when the filter runs after the guards: the unfiltered two cells then
    trip the patched limit of one.
    """
    monkeypatch.setattr(bm, "MATRIX_LIMIT", 1)
    cells = bm.build_matrix(
        ["dev-eu"],
        {"dev-eu": ["stacks/a", "stacks/b"]},
        {
            "stacks/a": ["env/dev-eu", "workload/a"],
            "stacks/b": ["env/dev-eu", "workload/b"],
        },
        tags="workload/a",
    )
    assert [c["stack"] for c in cells] == ["stacks/a"]


def test_slug_collision_among_filtered_out_cells_does_not_abort():
    """`a/b` and `a-b` share one artifact name only if both are in the run.

    Fails when the filter runs after the guards: the collision aborts a run
    that never plans the second stack.
    """
    cells = bm.build_matrix(
        ["dev-eu"],
        {"dev-eu": ["a/b", "a-b"]},
        {"a/b": ["env/dev-eu", "workload/keep"], "a-b": ["env/dev-eu", "workload/drop"]},
        tags="workload/keep",
    )
    assert [c["stack"] for c in cells] == ["a/b"]


def test_reserved_stack_path_among_filtered_out_cells_does_not_abort():
    """A stack path of exactly `apply` aborts only if it is in the run.

    Fails when the filter runs after the reserved loop: a scoped sweep that
    never covers that stack aborts on it.
    """
    cells = bm.build_matrix(
        ["dev-eu"],
        {"dev-eu": ["apply", "stacks/a"]},
        {
            "apply": ["env/dev-eu", "workload/drop"],
            "stacks/a": ["env/dev-eu", "workload/keep"],
        },
        tags="workload/keep",
    )
    assert [c["stack"] for c in cells] == ["stacks/a"]


def test_existing_build_matrix_callers_pass_no_tags():
    """`apply-detect` calls `build_matrix` with three positional arguments.

    Fails when `tags` is made positional-required on `build_matrix`.
    """
    assert bm.build_matrix(
        ["dev-eu"], {"dev-eu": ["stacks/app"]}, {"stacks/app": ["env/dev-eu"]}
    ) == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]


def test_existing_compute_cells_callers_pass_no_tags(monkeypatch):
    """`deploy-detect` calls `compute_cells` without a `tags` argument.

    Fails when `tags` is made positional-required on `compute_cells`.
    """
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["env/dev-eu"])
    assert bm.compute_cells(all_stacks=False, base="abc123")[1] == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""}
    ]


def test_main_refuses_a_tag_filter_on_a_plan_run_and_does_not_enumerate(monkeypatch, tmp_path):
    """A `tags` value with no `no-pull-request` aborts before the stacks are listed.

    Fails when the refusal returns "" for a non-empty query: the run plans a
    narrowed matrix instead, and `called` records the enumeration.
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
    assert "the `tags` filter is only for a workflow with no pull request" in str(exc_info.value)
    assert called == []


def test_all_stacks_does_not_exempt_the_tag_filter_refusal(monkeypatch, tmp_path):
    """`all-stacks: true` plus `tags` on a plan run is still refused.

    Fails when `all_stacks` is added to the exemption: a plan wrapper setting
    both would drop changed stacks from the matrix, so they get no plan cell and
    no apply check, `shipmate / gate` greens, and the change merges unapplied.
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
    assert "the `tags` filter is only for a workflow with no pull request" in str(exc_info.value)
    assert called == []


def test_the_tag_filter_refusal_is_keyed_on_the_value():
    """A composite action's `required:`/`default:` is not enforced by GitHub Actions, so the
    refusal reads the value: any non-empty query outside a no-pull-request run is refused,
    and an absent input arrives as "" and is no filter at all.

    Fails when the manifest is relied on instead and the runtime check deleted:
    `tag_filter_error` is gone and every case below errors."""
    assert bm.tag_filter_error("env/dev-eu", False).startswith("::error::")
    assert bm.tag_filter_error("", False) == ""
    assert bm.tag_filter_error("   ", False) == ""
    assert bm.tag_filter_error("env/dev-eu", True) == ""


def test_main_with_no_tags_input_filters_nothing(monkeypatch, tmp_path):
    """An unset `SHIPMATE_TAGS` reaches `compute_cells` as the empty query.

    Fails when the absent variable is read as anything but "": this run states
    no `no-pull-request`, so `tag_filter_error` refuses the non-empty default
    and main() raises before `compute_cells` is reached at all.
    """
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


def test_a_drift_run_passes_the_query_through_verbatim(monkeypatch, tmp_path):
    """`compute_cells` is handed the query exactly as the input stated it.

    Fails when main() hard-codes the argument, and when it normalises the query
    on the way -- the surrounding and inner whitespace here is the parser's to
    strip, and a second reading of the grammar here is a second grammar.
    """
    _, called = _run_main(
        monkeypatch,
        tmp_path,
        {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
            "SHIPMATE_ALL_STACKS": "true",
            "SHIPMATE_NO_PULL_REQUEST": "true",
            "SHIPMATE_TAGS": " env/dev-eu ,workload/app",
        },
    )
    assert called == [(True, "", " env/dev-eu ,workload/app")]


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


def test_a_tag_filter_does_not_hide_a_workload_from_drift(monkeypatch, tmp_path, capsys):
    """The filter drops `stacks/net`, the only stack tagging `workload/net`, from the cells; the
    tag still exists, so drift must not report `net` as untagged every night.

    Mutation: derive `tagged` from the cells in `compute_cells` -- the warning names `net`."""
    _run_main(
        monkeypatch,
        tmp_path,
        {**_DRIFT_ENV, "SHIPMATE_TAGS": "workload/app"},
        table=_LISTING_TABLE,
        stacks=_WORKLOAD_TREE,
    )
    assert capsys.readouterr().out.splitlines() == ["1 cell(s): dev-eu/stacks/app"]


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


def test_a_tags_filter_drops_a_stack_before_its_workload_tags_are_read():
    """stacks/dns is outside the `env/dev-eu` sweep, so its two workload tags must not refuse it.

    Mutation: assign `workload` before `filter_cells` in `build_matrix` -- the two-workload-tag
    refusal fires."""
    cells = bm.build_matrix(
        ["dev-eu", "prod-eu"],
        {"dev-eu": ["stacks/app"], "prod-eu": ["stacks/dns"]},
        {
            "stacks/app": ["env/dev-eu"],
            "stacks/dns": ["env/prod-eu", "workload/net", "workload/network"],
        },
        "env/dev-eu",
    )
    assert cells == [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
