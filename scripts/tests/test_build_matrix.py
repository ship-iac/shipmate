import json

import pytest
from _loader import load_script

bm = load_script("build-matrix")


def test_multi_env_stack_yields_one_cell_per_env():
    cells = bm.build_matrix(
        envs=["dev-eu", "dev-us"],
        stacks_by_env={"dev-eu": ["stacks/app"], "dev-us": ["stacks/app", "stacks/dns"]},
        tags_by_stack={
            "stacks/app": ["env/dev-eu", "env/dev-us"],
            "stacks/dns": ["env/dev-us", "workload/net"],
        },
    )
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/app", "environment": "dev-us", "workload": ""},
        {"stack": "stacks/dns", "environment": "dev-us", "workload": "net"},
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
    assert cells == [{"stack": "infra/apply", "environment": "dev-eu", "workload": ""}]


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
    assert cells == [{"stack": "infra/shipmate", "environment": "dev-eu", "workload": ""}]


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
    cells = bm.compute_cells(all_stacks=True)
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
        {"stack": "stacks/app", "environment": "dev-us", "workload": "app"},
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


def _payload(head_repo):
    """A `pull_request` event payload whose head repo is `head_repo`; None
    models the shape GitHub sends once the fork has been deleted."""
    repo = {"full_name": head_repo} if head_repo is not None else None
    return {"pull_request": {"head": {"repo": repo}}}


def test_fork_pull_request_is_rejected():
    err = bm.fork_pr_error("pull_request", "acme/iac", _payload("outsider/iac"))
    assert err.startswith("::error::")
    assert "outsider/iac" in err


def test_fork_pull_request_target_is_rejected():
    # `pull_request_target` runs at the base ref *with* the repository's secrets
    # while acting on pull-request-author-controlled content, so it is the one
    # pull-request event where failing open is worst. Same payload shape.
    err = bm.fork_pr_error("pull_request_target", "acme/iac", _payload("outsider/iac"))
    assert err.startswith("::error::")
    assert "outsider/iac" in err


def test_same_repository_pull_request_is_planned():
    assert bm.fork_pr_error("pull_request", "acme/iac", _payload("acme/iac")) == ""


def test_non_pull_request_events_are_never_rejected():
    # The drift path calls build-matrix with all-stacks on `schedule` /
    # `workflow_dispatch` and no pull request context, so `head.repo` is absent
    # exactly as it is for a deleted fork. Keying the guard on that field
    # instead of on the event would kill nightly drift silently.
    for event in ("schedule", "workflow_dispatch", "push", ""):
        assert bm.fork_pr_error(event, "acme/iac", {}) == ""
        assert bm.fork_pr_error(event, "acme/iac", _payload(None)) == ""


def test_undeterminable_head_repository_is_rejected():
    # Within a pull request event: `head.repo` is null once the fork was
    # deleted, and the payload is {} when GITHUB_EVENT_PATH could not be read.
    # Neither is evidence of a same-repository pull request.
    assert bm.fork_pr_error("pull_request", "acme/iac", _payload(None)).startswith("::error::")
    assert bm.fork_pr_error("pull_request", "acme/iac", {}).startswith("::error::")


def test_event_payload_degrades_to_empty_dict(tmp_path):
    # Each of these reaches fork_pr_error as {}, which is rejected above.
    assert bm._event_payload("") == {}
    assert bm._event_payload(str(tmp_path / "absent.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert bm._event_payload(str(bad)) == {}
    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]", encoding="utf-8")
    assert bm._event_payload(str(listy)) == {}


def _run_main(
    monkeypatch, tmp_path, env, cells=(("stacks/app", "dev-eu"),), called=None, plan_workflow=True
):
    """main() with GITHUB_OUTPUT redirected, returning (parsed outputs, calls)
    where calls records compute_cells' arguments -- so a rejection is
    observable as the stack enumeration never having run. Pass `called` to keep
    that record readable when main() raises and there is no return value."""
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
    ):
        monkeypatch.delenv(k, raising=False)
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


def _event_file(tmp_path, head_repo):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(_payload(head_repo)), encoding="utf-8")
    return str(path)


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
                "GITHUB_EVENT_PATH": _event_file(tmp_path, "outsider/iac"),
            },
            called=called,
        )
    assert str(excinfo.value).startswith("::error::")
    assert "fork pull requests are not supported" in str(excinfo.value)
    assert called == []


def test_main_refuses_a_pull_request_when_the_repository_is_unknown(monkeypatch, tmp_path):
    # `if repository and full_name == repository` fails closed on purpose: with
    # GITHUB_REPOSITORY empty, dropping that clause makes an undeterminable head
    # repository compare equal to the empty string and the run gets planned.
    called = []
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REPOSITORY": ""},
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
    monkeypatch.setenv("GITHUB_EVENT_PATH", _event_file(tmp_path, "outsider/iac"))
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
            "GITHUB_EVENT_PATH": _event_file(tmp_path, "acme/iac"),
        },
    )
    assert outputs["empty"] == "false"
    assert called == [(False, "")]


def test_main_drift_run_is_unaffected(monkeypatch, tmp_path):
    # all-stacks, no pull request context: every stack must still be enumerated.
    outputs, called = _run_main(
        monkeypatch,
        tmp_path,
        {
            "SHIPMATE_ALL_STACKS": "true",
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REPOSITORY": "acme/iac",
        },
    )
    assert outputs["empty"] == "false"
    assert called == [(True, "")]


def _head_payload(sha):
    return {"pull_request": {"head": {"sha": sha}}}


def test_head_checkout_matching_the_pull_request_head_is_planned(monkeypatch):
    monkeypatch.setattr(bm, "_run", lambda args: "cafe1234\n")
    assert bm.head_checkout_error("pull_request_target", _head_payload("cafe1234")) == ""


def test_head_checkout_of_the_base_is_refused(monkeypatch):
    # The pull_request_target default: actions/checkout took the base branch, so
    # terramate would diff the base against itself and report nothing changed.
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    err = bm.head_checkout_error("pull_request_target", _head_payload("cafe1234"))
    assert err.startswith("::error::")
    assert "ref: ${{ github.event.pull_request.head.sha }}" in err
    assert "cafe1234" in err and "basebase" in err


def test_head_checkout_check_is_skipped_off_pull_request_target(monkeypatch):
    # The drift path has no pull-request context and no reason to be at any
    # particular commit; `git rev-parse` must not even run. `pull_request` is in
    # the same list on purpose: its default checkout is `refs/pull/<n>/merge`, a
    # merge commit that equals neither SHA, so comparing it would refuse every
    # correctly wired consumer still on that trigger.
    monkeypatch.setattr(
        bm, "_run", lambda args: pytest.fail("head checkout was probed off pull_request_target")
    )
    for event in ("pull_request", "schedule", "workflow_dispatch", "push", ""):
        assert bm.head_checkout_error(event, _head_payload("cafe1234")) == ""


def test_head_checkout_check_is_skipped_when_the_payload_has_no_head_sha(monkeypatch):
    monkeypatch.setattr(bm, "_run", lambda args: pytest.fail("probed with no head sha"))
    assert bm.head_checkout_error("pull_request_target", {}) == ""


def test_plan_workflow_at_the_contract_path_is_planned(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "plan.yml").write_text("", encoding="utf-8")
    assert bm.plan_workflow_error("pull_request_target", str(tmp_path)) == ""


def test_a_renamed_plan_workflow_is_refused(tmp_path):
    # Four lookups match `.github/workflows/plan.yml` byte-for-byte; a rename
    # merges green and wedges every apply from that commit on.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "shipmate-plan.yml").write_text("", encoding="utf-8")
    err = bm.plan_workflow_error("pull_request", str(tmp_path))
    assert err.startswith("::error::")
    assert ".github/workflows/plan.yml" in err


def test_plan_workflow_check_is_skipped_off_a_pull_request(tmp_path):
    for event in ("schedule", "workflow_dispatch", "push", ""):
        assert bm.plan_workflow_error(event, str(tmp_path)) == ""


def test_main_refuses_a_base_checkout(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(bm, "_run", lambda args: "basebase\n")
    event = tmp_path / "head-event.json"
    event.write_text(
        json.dumps(
            {"pull_request": {"head": {"sha": "cafe1234", "repo": {"full_name": "acme/iac"}}}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_REPOSITORY": "acme/iac",
                "GITHUB_EVENT_PATH": str(event),
            },
            called=called,
        )
    assert "ref: ${{ github.event.pull_request.head.sha }}" in str(excinfo.value)
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
                "GITHUB_EVENT_PATH": _event_file(tmp_path, "acme/iac"),
            },
            called=called,
            plan_workflow=False,
        )
    assert ".github/workflows/plan.yml" in str(excinfo.value)
    assert called == []


def test_build_matrix_action_declares_no_fork_input():
    # The refusal is a business rule, not a configurable: an input here would
    # be a way to turn it off.
    import yaml
    from _loader import ACTIONS

    doc = yaml.safe_load((ACTIONS / "build-matrix/action.yml").read_text(encoding="utf-8"))
    assert set(doc["inputs"]) == {"base-sha", "all-stacks"}


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
