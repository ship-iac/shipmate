import subprocess

import pytest
from _loader import load_script

bm = load_script("build-matrix")


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
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "", "workload_var": ""},
        {"stack": "stacks/app", "environment": "dev-us", "workload": "", "workload_var": ""},
        {
            "stack": "stacks/dns",
            "environment": "dev-us",
            "workload": "net-edge",
            "workload_var": "NET_EDGE",
        },
    ]


def test_two_workload_tags_collapsing_to_one_variable_fail_loud():
    # Terramate accepts '_' in a tag value, so `net-edge` and `net_edge` are two
    # workloads that name one AWS_ROLE_ARN_NET_EDGE -- one of them would apply
    # real infrastructure under the other's IAM identity.
    with pytest.raises(SystemExit) as exc_info:
        bm.build_matrix(
            envs=["dev-eu"],
            stacks_by_env={"dev-eu": ["stacks/a", "stacks/b"]},
            tags_by_stack={
                "stacks/a": ["env/dev-eu", "workload/net-edge"],
                "stacks/b": ["env/dev-eu", "workload/net_edge"],
            },
        )
    assert str(exc_info.value) == (
        "::error::workload/net-edge, workload/net_edge all map to AWS_ROLE_ARN_NET_EDGE: "
        "the variable name upper-cases the tag and replaces '-' with '_', so one Environment "
        "variable would have to hold every one of those workloads' role ARNs and some cells "
        "would apply under an IAM identity that is not theirs. Rename one workload tag."
    )


def test_workloads_with_distinct_variables_build_normally():
    assert bm.build_matrix(
        envs=["dev-eu"],
        stacks_by_env={"dev-eu": ["stacks/a", "stacks/b"]},
        tags_by_stack={
            "stacks/a": ["env/dev-eu", "workload/net-edge"],
            "stacks/b": ["env/dev-eu", "workload/net-core"],
        },
    ) == [
        {
            "stack": "stacks/a",
            "environment": "dev-eu",
            "workload": "net-edge",
            "workload_var": "NET_EDGE",
        },
        {
            "stack": "stacks/b",
            "environment": "dev-eu",
            "workload": "net-core",
            "workload_var": "NET_CORE",
        },
    ]


def test_empty_when_no_changed_stacks():
    assert bm.build_matrix(["dev-eu"], {"dev-eu": []}, {}) == []


def test_raises_above_256_cells():
    # The remediation must match CONTRACT.md §Fan-out: splitting the change is the
    # general remedy, and `shipmate apply <env>` is not an escape hatch -- the
    # ceiling trips in plan detect, so no reviewed plan exists to apply.
    stacks = [f"stacks/s{i}" for i in range(257)]
    with pytest.raises(bm.MatrixTooLarge) as exc_info:
        bm.build_matrix(["dev-eu"], {"dev-eu": stacks}, {s: ["env/dev-eu"] for s in stacks})
    assert str(exc_info.value) == (
        "257 plan cells exceeds the GitHub Actions matrix limit of 256. "
        "Split the change across several pull requests -- the matrix is built over "
        "`terramate list --changed`. A one-line edit to a shared local module correctly "
        "marks every dependent stack changed and is one atomic change by nature; there the "
        "only lever is to reduce the number of environments in play."
    )


def test_rejects_stack_path_exactly_apply():
    # A stack literally named `apply` renders a plan check `apply / <env>`, which
    # collides with the apply-check namespace apply-gate selects the queue by.
    with pytest.raises(SystemExit, match="may not be exactly 'apply'"):
        bm.build_matrix(["dev-eu"], {"dev-eu": ["apply"]}, {"apply": ["env/dev-eu"]})


def test_nested_apply_stack_is_allowed():
    # Only an exact top-level `apply` collides; `infra/apply` renders
    # `infra/apply / <env>`, outside the `apply / ` namespace.
    cells = bm.build_matrix(
        ["dev-eu"], {"dev-eu": ["infra/apply"]}, {"infra/apply": ["env/dev-eu"]}
    )
    assert cells == [
        {"stack": "infra/apply", "environment": "dev-eu", "workload": "", "workload_var": ""}
    ]


def test_rejects_stack_path_exactly_shipmate():
    # `shipmate` renders a plan check `shipmate / <env>`, inside the reserved
    # `shipmate / ` namespace (`shipmate / gate`, and a consumer's own
    # non-fan-out job names). summary-comment resolves plan links by an exact
    # `<stack> / <env>` lookup over every check run on the head SHA, so for an
    # env named after one of those the row's link resolves to the wrong check.
    with pytest.raises(SystemExit, match="may not be exactly 'shipmate'"):
        bm.build_matrix(["dev-eu"], {"dev-eu": ["shipmate"]}, {"shipmate": ["env/dev-eu"]})


def test_nested_shipmate_stack_is_allowed():
    cells = bm.build_matrix(
        ["dev-eu"], {"dev-eu": ["infra/shipmate"]}, {"infra/shipmate": ["env/dev-eu"]}
    )
    assert cells == [
        {"stack": "infra/shipmate", "environment": "dev-eu", "workload": "", "workload_var": ""}
    ]


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
    monkeypatch.setattr(bm, "assert_run_env_roundtrip", lambda stack_dir: None)
    cells = bm.compute_cells(all_stacks=True)
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app", "workload_var": "APP"},
        {"stack": "stacks/app", "environment": "dev-us", "workload": "app", "workload_var": "APP"},
    ]


def test_compute_cells_raises_on_untagged_stack(monkeypatch):
    # A stack with no env/* tag would silently vanish from plan/apply/drift
    # -- compute_cells must fail loud instead (scripts/build-matrix lines ~76-84).
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
    # The artifact-sourced bare-apply path passes require_env_tag=False: an
    # untagged stack anywhere in the repo must NOT abort membership — it simply
    # produces no plan.<env>.<slug> artifact and contributes no cell. The tagged
    # stacks still bucket normally; the untagged one just vanishes from the map.
    stacks = ["stacks/app", "stacks/orphan"]
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: stacks)
    tags = {"stacks/app": ["env/dev-eu"], "stacks/orphan": ["workload/util"]}
    monkeypatch.setattr(bm, "_tags", lambda s: tags[s])
    stacks_by_env, tags_by_stack = bm.env_membership(all_stacks=True, require_env_tag=False)
    assert stacks_by_env == {"dev-eu": ["stacks/app"]}
    assert tags_by_stack == tags  # orphan still reported in tags, just not bucketed


def _stub_terramate(monkeypatch, stacks, env_lines, calls=None):
    """Stub the ONE subprocess entry point, so these cases reach the probe
    through the real `compute_cells` path rather than calling it directly."""
    for name in ("TF_VAR_env", "TF_VAR_region", "TF_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)

    def fake_run(args, env=None, check=True):
        if args[:2] == ["terramate", "list"]:
            return "".join(f"{s}\n" for s in stacks)
        if "eval" in args:
            return '["env/dev-eu"]'
        if args[-1] == "env":
            if calls is not None:
                calls.append((args, env))
            return subprocess.CompletedProcess(args, 0, env_lines, "")
        # `raise` rather than a bare `pytest.fail(...)` call so the function has no
        # implicit fall-through beside its three value returns. Same exception the
        # call raises, and `Failed` is a BaseException, so a broad `except Exception`
        # in the code under test still cannot swallow it.
        raise pytest.fail.Exception(f"unexpected command: {args}")

    monkeypatch.setattr(bm, "_run", fake_run)


_SURVIVED = (
    "PATH=/usr/bin\n"
    "TF_VAR_env=SHIPMATE_RT_PROBE\n"
    "TF_VAR_region=SHIPMATE_RT_PROBE\n"
    "TF_WORKSPACE=SHIPMATE_RT_PROBE\n"
)

_TF_VAR_ENV_REWRITTEN = (
    "::error::this repository's terramate.config.run.env rewrites TF_VAR_env: detect "
    "injected 'SHIPMATE_RT_PROBE' and `terramate run` reported 'dev'. Every cell "
    "would plan and apply under a value CI did not choose, and the plan/apply "
    "fingerprint is computed outside `terramate run`, so both sides agree and "
    "nothing else reports it. Do not assign TF_VAR_env, TF_VAR_region, TF_WORKSPACE "
    "there; to keep a local default, put the injected name first in the chain — "
    'tm_try(env.TF_VAR_env, env.env, "dev"). See CONTRACT.md §Env model.'
)

_TF_WORKSPACE_REWRITTEN = (
    "::error::this repository's terramate.config.run.env rewrites TF_WORKSPACE: detect "
    "injected 'SHIPMATE_RT_PROBE' and `terramate run` reported 'default'. Every cell "
    "would plan and apply under a value CI did not choose, and the plan/apply "
    "fingerprint is computed outside `terramate run`, so both sides agree and "
    "nothing else reports it. Do not assign TF_VAR_env, TF_VAR_region, TF_WORKSPACE "
    "there; to keep a local default, put the injected name first in the chain — "
    'tm_try(env.TF_VAR_env, env.env, "dev"). See CONTRACT.md §Env model.'
)


_TF_VAR_REGION_UNREPORTED = (
    "::error::this repository's terramate.config.run.env rewrites TF_VAR_region: detect "
    "injected 'SHIPMATE_RT_PROBE' and `terramate run` reported (unset). Every cell "
    "would plan and apply under a value CI did not choose, and the plan/apply "
    "fingerprint is computed outside `terramate run`, so both sides agree and "
    "nothing else reports it. Do not assign TF_VAR_env, TF_VAR_region, TF_WORKSPACE "
    "there; to keep a local default, put the injected name first in the chain — "
    'tm_try(env.TF_VAR_env, env.env, "dev"). See CONTRACT.md §Env model.'
)


def test_compute_cells_probes_that_the_injected_environment_survives(monkeypatch):
    calls = []
    _stub_terramate(monkeypatch, ["stacks/app"], _SURVIVED, calls)
    assert bm.compute_cells(all_stacks=True) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "", "workload_var": ""}
    ]
    args, env = calls[0]
    assert args == [
        "terramate",
        "run",
        "--disable-safeguards=git-out-of-sync",
        "--no-recursive",
        "-C",
        "stacks/app",
        "--",
        "env",
    ]
    assert {k: v for k, v in env.items() if k in bm.RT_VARS} == {
        "TF_VAR_env": "SHIPMATE_RT_PROBE",
        "TF_VAR_region": "SHIPMATE_RT_PROBE",
        "TF_WORKSPACE": "SHIPMATE_RT_PROBE",
    }


def test_compute_cells_refuses_a_rewritten_tf_var_env(monkeypatch):
    # The sentinel, not the ambient value: detect binds no environment, so an
    # ambient comparison would pass here whatever run.env did.
    _stub_terramate(
        monkeypatch,
        ["stacks/app"],
        _SURVIVED.replace("TF_VAR_env=SHIPMATE_RT_PROBE", "TF_VAR_env=dev"),
    )
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True)
    assert str(exc_info.value) == _TF_VAR_ENV_REWRITTEN


def test_compute_cells_refuses_a_rewritten_tf_workspace(monkeypatch):
    _stub_terramate(
        monkeypatch,
        ["stacks/app"],
        _SURVIVED.replace("TF_WORKSPACE=SHIPMATE_RT_PROBE", "TF_WORKSPACE=default"),
    )
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True)
    assert str(exc_info.value) == _TF_WORKSPACE_REWRITTEN


def test_compute_cells_refuses_a_variable_terramate_run_never_reported(monkeypatch):
    # Dropped, not rewritten: `run.env` can also unset. An absent variable is
    # the same defect as a changed one — the cell would run under whatever the
    # tool decided rather than what CI injected — so "not reported" must not
    # read as "fine".
    _stub_terramate(
        monkeypatch,
        ["stacks/app"],
        _SURVIVED.replace("TF_VAR_region=SHIPMATE_RT_PROBE\n", ""),
    )
    with pytest.raises(SystemExit) as exc_info:
        bm.compute_cells(all_stacks=True)
    assert str(exc_info.value) == _TF_VAR_REGION_UNREPORTED


def test_compute_cells_warns_and_continues_when_the_probe_cannot_run(monkeypatch, capsys):
    # detect binds no GitHub Environment, so a `run.env` that merely READS a
    # variable the plan/apply Environment supplies cannot evaluate here while
    # every plan cell evaluates it fine. Raising would fail every pull request
    # in such a repository at `detect`, with no plan cells and no gate.
    # Stubbed at `subprocess.run`, not at `_run`: the fact under test is that
    # the probe asks _run NOT to raise, and a stubbed _run cannot show that.
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["env/dev-eu"])
    monkeypatch.setattr(
        bm.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0],
            1,
            "",
            "Error: evaluating terramate.config.run.env\n> undefined variable env.TF_VAR_env\n",
        ),
    )
    cells = bm.compute_cells(all_stacks=True)
    # The cell dict's own shape is pinned by test_multi_env_stack_yields_one_cell_per_env;
    # what this case adds is that the fan-out happened at all after the probe failed.
    assert [(c["stack"], c["environment"]) for c in cells] == [("stacks/app", "dev-eu")]
    expected_warning = (
        "::warning::could not verify that terramate.config.run.env leaves TF_VAR_env, "
        "TF_VAR_region, TF_WORKSPACE alone: `terramate run` exited 1. terramate said: "
        "Error: evaluating terramate.config.run.env > undefined variable env.TF_VAR_env"
    )
    assert capsys.readouterr().out.splitlines() == [expected_warning]


def test_compute_cells_skips_the_probe_with_no_stacks(monkeypatch):
    # Nothing to run the probe in: `terramate run -C` needs a stack directory.
    calls = []
    _stub_terramate(monkeypatch, [], "", calls)
    assert bm.compute_cells(all_stacks=True) == []
    assert calls == []


def test_a_stated_head_repository_equal_to_this_repository_is_planned():
    assert bm.fork_pr_error("acme/iac", "acme/iac", "false") == ""


def test_a_stated_foreign_head_repository_is_refused_naming_both():
    err = bm.fork_pr_error("acme/iac", "outsider/iac", "false")
    assert err.startswith("::error::")
    assert "outsider/iac" in err
    assert "acme/iac" in err


def test_an_unstated_head_repository_is_refused_naming_the_input():
    # Pinning the MESSAGE, not merely the refusal: letting an empty value fall
    # through to the equality check refuses too -- with the fork wording, which
    # tells a consumer who forgot the input to push their branch to where it
    # already is.
    err = bm.fork_pr_error("acme/iac", "   ", "false")
    assert err.startswith("::error::")
    assert "head-repo" in err
    assert "no-pull-request" in err
    assert "docs/getting-started.md" in err
    assert "fork pull requests are not supported" not in err


def test_the_opt_out_plans_whatever_the_head_repository():
    for head in ("", "   ", "acme/iac", "outsider/iac"):
        assert bm.fork_pr_error("acme/iac", head, "true") == ""
    # A consumer's `no-pull-request: True` must not redden a nightly over a YAML
    # capitalisation; only this repository's default-branch workflow can set it.
    assert bm.fork_pr_error("acme/iac", "", " True ") == ""


def test_only_the_opt_out_skips_the_head_repository_check():
    # The manifest default is the non-empty string "false", so anything that
    # treats a non-empty value as the opt-out would plan every unstated run.
    for value in ("false", "False", " false ", "yes", "1", "", "no-pull-request"):
        assert bm.fork_pr_error("acme/iac", "", value).startswith("::error::")


def test_an_unknown_this_repository_refuses_a_stated_head_repository():
    # GITHUB_REPOSITORY unset is not reachable on a runner; if it ever is, the
    # empty string must not compare equal to whatever was stated.
    assert "fork pull requests are not supported" in bm.fork_pr_error("", "acme/iac", "false")


def test_event_payload_degrades_to_empty_dict(tmp_path):
    # `scripts/pr-facts` is the reader: each of these reaches it as {}, which has
    # neither a pull request nor a `pr_number` and so refuses rather than
    # emitting empty facts. Neither guard in this module reads the event.
    assert bm._event_payload("") == {}
    assert bm._event_payload(str(tmp_path / "absent.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert bm._event_payload(str(bad)) == {}
    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]", encoding="utf-8")
    assert bm._event_payload(str(listy)) == {}


def _run_main(
    monkeypatch,
    tmp_path,
    env,
    cells=(("stacks/app", "dev-eu"),),
    called=None,
    plan_workflow=True,
    head_sha=None,
):
    """main() with GITHUB_OUTPUT redirected, returning (parsed outputs, calls)
    where calls records compute_cells' arguments -- so a rejection is
    observable as the stack enumeration never having run. Pass `called` to keep
    that record readable when main() raises and there is no return value.

    `head_sha` states that commit AND makes `git rev-parse HEAD` answer it, which
    is what a run past the head-checkout refusal looks like; without it the run
    states no head and is refused, so every test that wants to reach the matrix
    passes one."""
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    # main() reads the checkout it runs in: the plan workflow has to be where
    # plan_workflow_error requires it, and the engine repo (pytest's cwd) has no
    # plan.yml of its own.
    (tmp_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    if plan_workflow:
        (tmp_path / ".github" / "workflows" / "plan.yml").write_text("", encoding="utf-8")
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
        return [{"stack": s, "environment": e, "workload": ""} for s, e in cells]

    monkeypatch.setattr(bm, "compute_cells", fake_compute)
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
    assert seen == [("acme/iac", "outsider/iac", "true")]


def test_main_refuses_the_fork_before_the_consumer_wiring_checks(monkeypatch, tmp_path):
    # Both guards would fire on this run -- a fork head, and a checkout that is
    # not the stated one -- so the assertion pins the ORDER: a run this script
    # cannot vouch for is turned away before it shells out to git on the fork's
    # tree, which is why `_run` fails rather than answering.
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
    assert bm.head_checkout_error("cafe1234", "false") == ""


def test_a_checkout_that_is_not_the_stated_head_is_refused_naming_both(monkeypatch):
    # What both plan triggers leave checked out by default: the base branch under
    # `pull_request_target`, the dispatch ref under `workflow_dispatch`. Either
    # way terramate diffs the wrong tree against the base and reports nothing
    # changed. Both SHAs are asserted because the message is the only place a
    # consumer sees which tree was planned and which one should have been.
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    err = bm.head_checkout_error("cafe1234", "false")
    assert err.startswith("::error::")
    assert "cafe1234" in err and "basebase" in err
    assert "nothing queued to apply" in err


def test_an_unstated_head_is_refused_naming_the_input(monkeypatch):
    # REFUSED, not skipped, and without probing git: the stated head is the only
    # thing this check has to compare against, so an omitted `head-sha` leaves it
    # unmade -- the direction that plans the base, greens the gate and queues no
    # applies. The message is asserted, not just the refusal: it must name the
    # input and the drift opt-out rather than reading as the mismatch above.
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("probed with no stated head"))
    for head in ("", "   "):
        err = bm.head_checkout_error(head, "false")
        assert err.startswith("::error::")
        assert "head-sha" in err
        assert "no-pull-request" in err
        assert "checked out" not in err


def test_the_opt_out_skips_the_head_checkout_check(monkeypatch):
    # The drift path has no pull-request context and no reason to be at any
    # particular commit; `git rev-parse` must not even run. Case- and
    # whitespace-insensitive for the same reason as the fork refusal's opt-out: a
    # `no-pull-request: True` must not redden a nightly over YAML capitalisation.
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("head checkout was probed"))
    for value in ("true", "True", " TRUE "):
        for head in ("", "cafe1234"):
            assert bm.head_checkout_error(head, value) == ""


def test_only_the_opt_out_skips_the_head_checkout_check():
    # The manifest default is the non-empty string "false", so anything that
    # treats a non-empty value as the opt-out would leave every run unchecked.
    for value in ("false", "False", " false ", "yes", "1", "", "no-pull-request"):
        assert bm.head_checkout_error("", value).startswith("::error::")


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
    # The leg the event-keyed version made NO check on: `actions/checkout` takes
    # the dispatch ref here, so a wrapper that adds the trigger and forgets `ref:`
    # planned the default branch against the base, came out empty, skipped the
    # plan job and greened the gate with nothing queued to apply. Held at main(),
    # not only in the guard: re-keying the guard is worthless if the call site
    # keeps deciding by event name.
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
    (tmp_path / ".github" / "workflows" / "plan.yml").write_text("", encoding="utf-8")
    assert bm.plan_workflow_error("pull_request_target", str(tmp_path)) == ""


def test_a_renamed_plan_workflow_is_refused(tmp_path):
    # This refusal is what makes the path load-bearing: no plan-run lookup
    # matches it literally any more, so a rename would otherwise merge green
    # while doctor's filename-keyed probes went quiet. Whole message, written by
    # hand: the consequences it names are the ones still true after the plan run
    # id moved onto each apply check, and a clause about plan-run discovery
    # coming back here would be a user-facing falsehood.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "shipmate-plan.yml").write_text("", encoding="utf-8")
    assert bm.plan_workflow_error("pull_request", str(tmp_path)) == (
        "::error::this repository has no `.github/workflows/plan.yml` — that exact path is "
        "matched literally by `shipmate doctor`, which keys its plan-wrapper probes on the "
        "filename: a plan workflow under any other name silently loses the head-repository "
        "and draft wiring checks, and draws doctor's own `pull_request_target` warning "
        "instead. Planning is refused here rather than degrading those diagnostics quietly. "
        "Move the plan workflow back to `.github/workflows/plan.yml`."
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
    assert ".github/workflows/plan.yml" in str(excinfo.value)
    assert called == []


def test_build_matrix_action_declares_its_inputs():
    # No input here is a way to turn a refusal off: `head-repo` and `head-sha`
    # are what the two refusals are keyed on and an empty value refuses either,
    # while `no-pull-request` only states
    # that there is no pull request at all. All are settable only by this
    # repository's own default-branch workflow, which a pull-request author
    # cannot edit -- and the direction is chosen so a forgotten input refuses
    # (plan wrapper) or reddens the nightly (drift), never plans a fork.
    # Hand-written, name -> default; descriptions are prose and not pinned.
    from _loader import action_yaml

    doc = action_yaml("build-matrix")
    assert {name: spec.get("default") for name, spec in doc["inputs"].items()} == {
        "base-sha": None,
        "all-stacks": "false",
        "head-repo": "",
        "head-sha": "",
        "no-pull-request": "false",
    }


def test_build_matrix_action_hands_the_script_the_names_it_reads():
    # The whole `env:` block against a hand-written constant: the script reads
    # SHIPMATE_HEAD_REPO / SHIPMATE_HEAD_SHA / SHIPMATE_NO_PULL_REQUEST by name,
    # so a renamed key here leaves every plan run unstated -- refused, but only
    # in production.
    from _loader import action_steps

    (step,) = [s for s in action_steps("build-matrix") if s.get("id") == "build"]
    assert step["env"] == {
        "SHIPMATE_BASE_SHA": "${{ inputs.base-sha }}",
        "SHIPMATE_ALL_STACKS": "${{ inputs.all-stacks }}",
        "SHIPMATE_HEAD_REPO": "${{ inputs.head-repo }}",
        "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_NO_PULL_REQUEST": "${{ inputs.no-pull-request }}",
    }


def test_build_matrix_action_declares_the_outputs_the_gate_reads():
    # `count` is what the trusted summary job measures its evidence against, so
    # a rename or a rewire here is a silent hole in the gate. Hand-written,
    # name -> wiring; descriptions are prose and deliberately not pinned.
    import yaml
    from _loader import ACTIONS

    doc = yaml.safe_load((ACTIONS / "build-matrix/action.yml").read_text(encoding="utf-8"))
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
