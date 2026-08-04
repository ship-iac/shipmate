import pathlib

import pytest
from _loader import action_steps, load_script

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
    stacks = [f"stacks/s{i}" for i in range(257)]
    with pytest.raises(bm.MatrixTooLarge):
        bm.build_matrix(["dev-eu"], {"dev-eu": stacks}, {s: ["env/dev-eu"] for s in stacks})


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


def _run_main(monkeypatch, tmp_path, env, cells=(("stacks/app", "dev-eu"),), called=None):
    """main() with GITHUB_OUTPUT redirected, returning (parsed outputs, calls)
    where calls records compute_cells' arguments -- so a rejection is
    observable as the stack enumeration never having run. Pass `called` to keep
    that record readable when main() raises and there is no return value."""
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    # main() reads the wiring off the CURRENT DIRECTORY, which under pytest is
    # the engine checkout -- a repository that legitimately has no plan.yml and
    # would fail every pull-request case below. tmp_path has no
    # `.github/workflows` at all, so the wiring degrades to UNKNOWN, which never
    # blocks; the wiring path itself is exercised by the cases further down.
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
    import json

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
    # Same reason as _run_main's: without this the wiring check reads the engine
    # checkout, raises `shipmate wiring` first, and this guard passes on somebody
    # else's SystemExit -- proving nothing about the fork rejection. The
    # assertion below names the rejection for the same reason.
    monkeypatch.chdir(tmp_path)
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


def test_build_matrix_action_declares_no_fork_input():
    # The refusal is a business rule, not a configurable: an input here would
    # be a way to turn it off.
    import yaml
    from _loader import ACTIONS

    doc = yaml.safe_load((ACTIONS / "build-matrix/action.yml").read_text(encoding="utf-8"))
    assert set(doc["inputs"]) == {"base-sha", "all-stacks"}


wi = load_script("wiring")

_BROKEN = [(wi.BROKEN, "plan.yml is named 'x'")]
_UNKNOWN = [(wi.UNKNOWN, "could not read .github/workflows")]


def test_a_broken_wiring_fails_a_pull_request():
    """build-matrix has no never-fails contract and already exits loud for fork
    pull requests. Two of the three wiring breaks MERGE GREEN otherwise: the
    workflow_run trigger resolves against the default branch, so the pull
    request that breaks the name or the summary wrapper still gets its own gate,
    and the fix pull request afterwards has none."""
    lines, error = bm.wiring_report(_BROKEN, "pull_request")
    assert error.startswith("::error")
    assert wi.WIRING_TITLE in error
    assert "plan.yml is named 'x'" in error


def test_a_broken_wiring_fails_a_pull_request_target_too():
    lines, error = bm.wiring_report(_BROKEN, "pull_request_target")
    assert error.startswith("::error")


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch", "push"])
def test_a_broken_wiring_only_warns_off_the_pull_request_path(event):
    """The drift path calls this script with all-stacks and no pull-request
    context. An already-merged break must not kill nightly drift -- and drift
    then becomes a second, post-merge detector of a break that landed."""
    lines, error = bm.wiring_report(_BROKEN, event)
    assert error == ""
    assert lines == [f"::warning title={wi.WIRING_TITLE}::plan.yml is named 'x'"]


@pytest.mark.parametrize("event", ["pull_request", "schedule"])
def test_an_unknown_wiring_never_blocks_and_never_warns(event):
    """A false positive here would block every pull request in every consumer
    until an engine fix and a re-pin, so anything short of certain is a notice."""
    lines, error = bm.wiring_report(_UNKNOWN, event)
    assert error == ""
    assert lines == [f"::notice title={wi.WIRING_TITLE}::could not read .github/workflows"]


def test_a_clean_wiring_emits_nothing():
    assert bm.wiring_report([], "pull_request") == ([], "")


def test_workflow_files_reads_the_local_checkout(tmp_path):
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / "plan.yml").write_bytes(b"name: x\n")
    (d / "notes.md").write_bytes(b"ignored")
    (d / "nested").mkdir()
    assert bm.workflow_files(tmp_path) == {"plan.yml": b"name: x\n"}


def test_workflow_files_returns_none_without_a_workflows_directory(tmp_path):
    assert bm.workflow_files(tmp_path) is None


def test_workflow_files_maps_an_unreadable_file_to_none(tmp_path, monkeypatch):
    """The per-file None is the only thing between an unreadable plan.yml and a
    false BROKEN: `findings` degrades a None entry to UNKNOWN, but only if the
    name reaches it at all. Dropping it would read as "no plan.yml"."""
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / "plan.yml").write_bytes(b"name: x\n")
    (d / "summary.yml").write_bytes(b"on: workflow_run\n")

    real = pathlib.Path.read_bytes

    def refuse(self):
        # A chmod does not reliably deny a read on Windows, so the failure is
        # injected at the one call that would raise on a real permission denial.
        if self.name == "plan.yml":
            raise OSError("permission denied")
        return real(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", refuse)
    assert bm.workflow_files(tmp_path) == {
        "plan.yml": None,
        "summary.yml": b"on: workflow_run\n",
    }


def test_workflow_files_degrades_an_unstattable_entry_instead_of_dropping_it(tmp_path):
    """`Path.is_file()` swallows OSError and answers False, so filtering on it
    would DROP an entry whose stat failed -- and a dropped `plan.yml` is BROKEN,
    not unverified. A directory named `plan.yml` stands in for that entry: it is
    the portable way to make the listing hold a name whose bytes cannot be read.
    """
    d = tmp_path / ".github" / "workflows"
    (d / "plan.yml").mkdir(parents=True)
    assert bm.workflow_files(tmp_path) == {"plan.yml": None}
    # And the finding that reaches the run is the degraded one, never BAD_PATH.
    assert wi.findings(bm.workflow_files(tmp_path), "") == [
        wi.PLAN_UNREADABLE,
        wi.WIRING_UNREADABLE,
    ]


def test_the_action_supplies_the_engine_repo_to_the_matrix_step():
    """The wiring check pins the reusable-summary `uses:` to the engine's own
    slug, which is never hardcoded -- it arrives as github.action_repository."""
    steps = action_steps("build-matrix")
    compute = next(s for s in steps if s.get("id") == "build")
    assert compute["env"]["SHIPMATE_ENGINE_REPO"] == "${{ github.action_repository }}"


def _broken_checkout(tmp_path):
    """A checkout whose plan.yml carries the wrong workflow name."""
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / "plan.yml").write_bytes(b"name: plan\n")
    return tmp_path


def test_main_fails_a_pull_request_on_a_broken_wiring(monkeypatch, tmp_path):
    """The report is only a decision until main() acts on it: without the call,
    every test above still passes and no run ever fails."""
    _broken_checkout(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        _run_main(
            monkeypatch,
            tmp_path,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": "acme/iac",
                "GITHUB_EVENT_PATH": _event_file(tmp_path, "acme/iac"),
            },
        )
    assert str(excinfo.value).startswith(f"::error title={wi.WIRING_TITLE}::")


def test_main_still_plans_a_drift_run_over_a_broken_wiring(monkeypatch, tmp_path, capsys):
    _broken_checkout(tmp_path)
    outputs, called = _run_main(
        monkeypatch,
        tmp_path,
        {"SHIPMATE_ALL_STACKS": "true", "GITHUB_EVENT_NAME": "schedule"},
    )
    assert outputs["empty"] == "false"
    assert called == [(True, "")]
    # The warning is the whole of "drift becomes a second, post-merge detector":
    # main() computing the report and never printing it plans the run just the
    # same, and reports nothing to anybody.
    printed = capsys.readouterr().out
    assert f"::warning title={wi.WIRING_TITLE}::" in printed
    assert wi.PLAN_NAME in printed


def test_main_emits_an_unknown_wiring_notice(monkeypatch, tmp_path, capsys):
    """The notice path out of main() is not covered by the case above, which
    only has a BROKEN to print. tmp_path has no `.github/workflows` at all."""
    _run_main(
        monkeypatch,
        tmp_path,
        {"SHIPMATE_ALL_STACKS": "true", "GITHUB_EVENT_NAME": "schedule"},
    )
    assert f"::notice title={wi.WIRING_TITLE}::" in capsys.readouterr().out


def test_main_checks_the_wiring_before_enumerating_stacks(monkeypatch, tmp_path):
    """The fork guard's twin (`test_main_does_not_enumerate_stacks_for_a_fork`).
    Placement, not merely the raise: a wiring check run after `compute_cells`
    still fails the job, but only after `detect` has spent a terramate run on a
    repository whose plan can never gate."""
    _broken_checkout(tmp_path)

    def boom(*a, **k):
        pytest.fail("compute_cells ran over a broken wiring")

    monkeypatch.setattr(bm, "compute_cells", boom)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/iac")
    monkeypatch.setenv("GITHUB_EVENT_PATH", _event_file(tmp_path, "acme/iac"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        bm.main()
    assert str(excinfo.value).startswith(f"::error title={wi.WIRING_TITLE}::")
