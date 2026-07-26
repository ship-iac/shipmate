import importlib.util
import io
import json
import os
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "doctor"
_loader = SourceFileLoader("doctor", str(_p))
_spec = importlib.util.spec_from_loader("doctor", _loader)
doctor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doctor)

_REPO = "o/r"
_APP_ID = "999"
_BRANCH = "main"
_ENVS = {"dev-eu"}
_HEAD = "f" * 40
_ENGINE_REPO = "acme/engine"
_WF_DIR = f"repos/{_REPO}/contents/.github/workflows"
# The pin probe reads the workflow files at the commit under examination, so
# every contents path it asks for carries _ctx()'s head_sha as ?ref=.
_REF = f"?ref={_HEAD}"


def _ctx(**over):
    ctx = {
        "repo": _REPO,
        "owner": "o",
        "app_id": _APP_ID,
        "default_branch": _BRANCH,
        "envs": set(_ENVS),
        "envs_available": True,
        "team": None,
        "app_permissions_checked": False,
        "app_permission_error": "",
        "head_sha": _HEAD,
        "plan_run_id": "1281",
        "annotations_dir": "ann",
        "check_ids_path": "check-ids.tsv",
        "harvest_failed": False,
        "harvest_pending": False,
        # The engine's own owner/repo, discovered at runtime from
        # github.action_repository -- never hardcoded, so the probe stays
        # org-agnostic while only ever reporting on shipmate's own pins.
        "engine_repo": _ENGINE_REPO,
    }
    ctx.update(over)
    return ctx


def test_mode_must_be_set(monkeypatch):
    monkeypatch.setenv("SHIPMATE_DOCTOR_MODE", "")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.setenv("SHIPMATE_APP_ID", _APP_ID)
    monkeypatch.setenv("SHIPMATE_DEFAULT_BRANCH", _BRANCH)
    with pytest.raises(SystemExit):
        doctor.main()


def test_render_annotations_one_line_per_level_and_escapes():
    out = doctor.render_annotations([(doctor.WARNING, "a %s b"), (doctor.NOTICE, "two\nlines")])
    assert out[0] == "::warning title=shipmate doctor::a %25s b"
    assert out[1] == "::notice title=shipmate doctor::two%0Alines"
    assert all("\n" not in line for line in out)


def test_envs_unavailable_skips_environment_probes(monkeypatch):
    monkeypatch.setattr(
        doctor, "_gh_json", lambda path: pytest.fail(f"env probe hit the API: {path}")
    )
    out = doctor._environment_warnings(_ctx(envs=set(), envs_available=False))
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE
    assert "no plan run" in out[0][1]


def test_ctx_from_env_missing_cells_dir_yields_empty_envs(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.setenv("SHIPMATE_APP_ID", _APP_ID)
    monkeypatch.setenv("SHIPMATE_DEFAULT_BRANCH", _BRANCH)
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path / "missing"))
    ctx = doctor.ctx_from_env()
    assert ctx["envs"] == set()
    assert ctx["envs_available"] is False


def test_declared_envs_skips_malformed_cell_json(tmp_path):
    # Plausible after a partial `gh run download` (one of gatherdoc's degrade
    # paths) -- must be skipped, not raise and red the render step.
    bad = tmp_path / "cell-summary.dev-eu.app"
    bad.mkdir()
    (bad / "cell.json").write_text("{not valid json", encoding="utf-8")
    assert doctor._declared_envs(tmp_path) == set()


def test_declared_envs_skips_cell_json_without_a_usable_environment(tmp_path):
    # Well-formed JSON that isn't the expected shape (e.g. a truncated
    # download that still parses) must degrade the same way -- KeyError, not
    # just JSONDecodeError, is one of the guarded exceptions. A present but
    # unusable value (null, non-string, empty) must be dropped too: it would
    # otherwise make envs_available true and run every environment probe
    # against a name that cannot exist.
    for i, payload in enumerate(
        [{"stack": "app"}, {"environment": None}, {"environment": 7}, {"environment": ""}]
    ):
        cell = tmp_path / f"cell-summary.c{i}.app"
        cell.mkdir()
        (cell / "cell.json").write_text(json.dumps(payload), encoding="utf-8")
    assert doctor._declared_envs(tmp_path) == set()


def test_declared_envs_keeps_well_formed_entries_alongside_malformed_ones(tmp_path):
    # One bad cell.json must not take down the whole declared-env set --
    # every other well-formed cell still contributes its environment.
    good = tmp_path / "cell-summary.dev-eu.app"
    good.mkdir()
    (good / "cell.json").write_text(json.dumps({"environment": "dev-eu"}), encoding="utf-8")
    bad = tmp_path / "cell-summary.dev-us.app"
    bad.mkdir()
    (bad / "cell.json").write_text("not json", encoding="utf-8")
    null_env = tmp_path / "cell-summary.dev-ap.app"
    null_env.mkdir()
    (null_env / "cell.json").write_text(json.dumps({"environment": None}), encoding="utf-8")
    assert doctor._declared_envs(tmp_path) == {"dev-eu"}


def _gate_rule(integration_id=999, strict=True):
    return [
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": strict,
                "required_status_checks": [
                    {"context": "shipmate / gate", "integration_id": integration_id},
                ],
            },
        },
    ]


def _environments(*names):
    return {"environments": [{"name": n} for n in names]}


def _env(name, rules=(), branch_policy=None):
    # The real "Get an environment" response includes a {"type":
    # "branch_policy"} protection_rules entry whenever deployment_branch_policy
    # is non-null -- mirror that so fixtures match the shape the API returns.
    protection_rules = [{"type": t} for t in rules]
    if branch_policy is not None:
        protection_rules.append({"type": "branch_policy"})
    return {
        "name": name,
        "protection_rules": protection_rules,
        "deployment_branch_policy": branch_policy,
    }


def _quiet_new_probes():
    """Healthy responses for the env-protection and pin-freshness probes, so
    tests exercising the older gate/environment probes via the top-level
    `warnings()` don't pick up incidental noise from these two."""
    return {
        f"repos/{_REPO}/environments/dev-eu": _env("dev-eu"),
        f"repos/{_REPO}/environments/dev-eu-apply": _env(
            "dev-eu-apply", rules=("required_reviewers",)
        ),
        f"{_WF_DIR}{_REF}": [],
    }


def test_healthy_repo_emits_nothing(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu", "dev-eu-apply"),
        **_quiet_new_probes(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.warnings(_ctx()) == []


def test_missing_environment_pair_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        # dev-eu-apply is missing
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu"),
        **_quiet_new_probes(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "dev-eu-apply" in text


def test_gate_rule_wrong_integration_id_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(integration_id=15368),
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu", "dev-eu-apply"),
        **_quiet_new_probes(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "integration_id" in text
    assert "15368" in text


def test_gate_rule_absent_warned(monkeypatch):
    responses = {
        # no required_status_checks rule at all
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": [
            {"type": "deletion", "parameters": {}}
        ],
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu", "dev-eu-apply"),
        **_quiet_new_probes(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "ungated" in text or "not gated" in text


def test_strict_policy_off_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(strict=False),
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu", "dev-eu-apply"),
        **_quiet_new_probes(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "up to date" in text


def test_probe_403_degrades_to_note_not_failure(monkeypatch):
    # rules/branches probe raises the REAL failure type: bm.gh_json (via
    # build-matrix's _run) hard-fails a nonzero `gh api` exit with
    # `raise SystemExit(...)`, not a plain Exception -- SystemExit derives
    # from BaseException, so a catch of only `except Exception` would let
    # this propagate right past warnings(). This test simulates that exact
    # 403-on-rules/branches case. The environments probe still succeeds and
    # its own finding must still surface alongside the degrade note -- one
    # probe failing must not swallow the other, and no exception may escape
    # warnings() (no pytest.raises here -- a regression fails this test with
    # an uncaught SystemExit error, not a silent pass).
    quiet = _quiet_new_probes()

    def fake_gh_json(path):
        if "rules/branches" in path:
            raise SystemExit("::error::command failed (1): gh api ...")
        if path in quiet:
            return quiet[path]
        return _environments("dev-eu")  # dev-eu-apply missing -> its own warning

    monkeypatch.setattr(doctor, "_gh_json", fake_gh_json)
    out = doctor.warnings(_ctx())
    assert len(out) == 2
    texts = [t for _, t in out]
    assert any("could not verify" in t and "probe skipped" in t for t in texts)
    assert any("dev-eu-apply" in t for t in texts)


def test_probe_generic_exception_degrades_to_note(monkeypatch):
    # Non-SystemExit failures (e.g. a network error inside _run before it
    # even gets to check the return code) must degrade the same way.
    quiet = _quiet_new_probes()

    def fake_gh_json(path):
        if "rules/branches" in path:
            raise RuntimeError("connection reset")
        if path in quiet:
            return quiet[path]
        return _environments("dev-eu", "dev-eu-apply")

    monkeypatch.setattr(doctor, "_gh_json", fake_gh_json)
    out = doctor.warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "could not verify" in text and "probe skipped" in text


def test_degrade_note_names_the_probe_and_drops_the_workflow_command_prefix(monkeypatch):
    """The degrade text is rendered verbatim into the sticky comment and into
    `::warning ...::` annotation data. `bm.gh_json` raises
    `::error::command failed (N): gh api <path>`, so echoing the exception puts
    a literal workflow-command prefix in the comment body (and nests one
    workflow command inside another in annotate mode) and leaks the internal
    endpoint. Name the probe that was skipped; keep the reason."""
    quiet = _quiet_new_probes()

    def gh(path):
        if "rules/branches" in path:
            raise SystemExit(f"::error::command failed (1): gh api {path}")
        if path in quiet:
            return quiet[path]
        return _environments("dev-eu", "dev-eu-apply")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor.warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "gate rule" in text  # which probe was skipped
    assert "command failed (1)" in text  # the reason survives
    assert "::error::" not in text
    assert "gh api" not in text and "rules/branches" not in text


def _protection(*envs, listed=None):
    """Responses for `_env_protection_warnings`: the environments listing the
    probe reads first (by default naming exactly the fixtures given), plus each
    fixture's per-environment protection read."""
    out = {f"repos/{_REPO}/environments/{e['name']}": e for e in envs}
    names = [e["name"] for e in envs] if listed is None else listed
    out[f"repos/{_REPO}/environments?per_page=100"] = _environments(*names)
    return out


def test_plan_env_with_reviewers_warned(monkeypatch):
    responses = _protection(
        _env("dev-eu", rules=("required_reviewers",)),
        _env("dev-eu-apply", rules=("required_reviewers",)),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    # Not "will hang waiting for approval": `approval` is every rule that isn't
    # a branch policy, so a wait timer lands here too and is not an approval.
    assert "dev-eu" in out[0][1] and "will not start immediately" in out[0][1]
    assert "approval" not in out[0][1]


def test_plan_env_wait_timer_is_not_diagnosed_as_an_approval_hang(monkeypatch):
    # docs/branch-protection.md deliberately lumps wait timers in with
    # reviewers (both stop a plan job from starting when it should), so the
    # finding must fire -- but its wording must fit the rule it names.
    responses = _protection(
        _env("dev-eu", rules=("wait_timer",)),
        _env("dev-eu-apply", rules=("required_reviewers",)),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert "wait_timer" in out[0][1]
    assert "will not start immediately" in out[0][1]
    assert "approval" not in out[0][1]


def test_apply_env_without_approval_rules_noted(monkeypatch):
    responses = _protection(_env("dev-eu"), _env("dev-eu-apply"))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE  # note, not warning
    assert "dev-eu-apply" in out[0][1]
    # "no approval rules", never "no protection rules": the finding keys on
    # `approval`, which excludes the branch_policy rule GitHub synthesizes, so
    # a branch policy may well be present on the environment it names.
    assert "no approval rules" in out[0][1]
    assert "required reviewers" in out[0][1] and "wait timer" in out[0][1]


def test_apply_env_with_only_a_branch_policy_is_still_noted(monkeypatch):
    """GitHub synthesizes a `branch_policy` protection rule whenever
    `deployment_branch_policy` is set, so an apply environment carrying nothing
    but a branch policy has a truthy `protection_rules` list while being
    entirely unreviewed. Keying the note on the raw rule list therefore reported
    an unreviewed apply environment as protected — the exact inverse of the
    finding's purpose."""
    responses = _protection(
        _env("dev-eu"),
        _env("dev-eu-apply", branch_policy={"protected_branches": True}),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE
    assert "dev-eu-apply" in out[0][1] and "no approval rules" in out[0][1]


def test_apply_env_with_an_approval_rule_and_a_branch_policy_is_silent(monkeypatch):
    # The other shape of the same pair: a genuinely reviewed apply environment
    # that also restricts branches must produce nothing.
    responses = _protection(
        _env("dev-eu"),
        _env(
            "dev-eu-apply",
            rules=("required_reviewers",),
            branch_policy={"protected_branches": True},
        ),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._env_protection_warnings(_ctx()) == []


def test_plan_env_branch_policy_warned(monkeypatch):
    responses = _protection(
        _env("dev-eu", branch_policy={"protected_branches": True}),
        _env("dev-eu-apply", rules=("required_reviewers",)),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "branch policy" in out[0][1]


def test_env_protection_missing_env_is_not_this_probes_problem(monkeypatch):
    """An environment that does not exist is `_environment_warnings`' finding,
    not this probe's — so a name absent from the environments listing is skipped
    without a per-environment read and without a finding. That used to be
    achieved by catching every per-environment exception and continuing, which
    silenced 403s and 5xx on environments that DO exist."""
    responses = _protection(_env("dev-eu"), listed=["dev-eu"])
    seen = []

    def gh(path):
        seen.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._env_protection_warnings(_ctx()) == []
    assert f"repos/{_REPO}/environments/dev-eu-apply" not in seen


def test_env_protection_unreadable_existing_env_is_a_notice_naming_it(monkeypatch):
    """A 403 or 5xx on an environment that IS in the listing was
    indistinguishable from a 404 (`bm.gh_json`'s exception carries no status
    code) and got swallowed, so the report went on to say the settings probes
    found no problems. Listing first makes the two distinguishable:
    present-but-unreadable is a note that names the environment."""
    responses = _protection(_env("dev-eu"), listed=["dev-eu", "dev-eu-apply"])

    def gh(path):
        if path.endswith("dev-eu-apply"):
            raise SystemExit(f"::error::command failed (1): gh api {path}")
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE
    assert "dev-eu-apply" in out[0][1]
    assert "not checked" in out[0][1]
    # The text lands in a comment body and in annotation data.
    assert "::error::" not in out[0][1] and "gh api" not in out[0][1]


def test_env_protection_listing_failure_propagates_to_the_degrade_note(monkeypatch):
    """The listing is this probe's precondition: without it no name can be
    classified present-or-absent, so the failure must reach `warnings()`' outer
    handler and become the "could not verify the env protection settings"
    degrade instead of a silent empty result."""

    def boom(path):
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", boom)
    with pytest.raises(SystemExit):
        doctor._env_protection_warnings(_ctx())
    out = doctor.warnings(_ctx())
    assert any("env protection" in t and "probe skipped" in t for _, t in out)


def _wf_listing(*names):
    return [{"name": n, "type": "file"} for n in names]


def _wf_file(text):
    import base64

    return {"content": base64.b64encode(text.encode()).decode()}


_SHA = "a" * 40
_OTHER_SHA = "b" * 40


def test_tag_pin_warned(monkeypatch):
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file("uses: acme/engine/actions/setup@v2\n"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "tag or branch" in out[0][1] and "acme/engine@v2" in out[0][1]


def test_quoted_tag_pin_warned(monkeypatch):
    # Some YAML formatters quote the `uses:` value -- the anchor must not
    # make those pins invisible to the probe.
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file('uses: "acme/engine/actions/setup@v2"\n'),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "tag or branch" in out[0][1] and "acme/engine@v2" in out[0][1]


def test_non_file_workflow_entry_skipped(monkeypatch):
    # A directory (or symlink/submodule) entry whose name happens to end in
    # .yml must not be treated as a workflow file -- fetching its "contents"
    # would return a list, and .get("content") would raise AttributeError.
    # No response is registered for sub.yml's contents call, so a regression
    # of the type check fails this test with a KeyError, not a silent pass.
    responses = {
        f"{_WF_DIR}{_REF}": [
            {"name": "sub.yml", "type": "dir"},
            {"name": "plan.yml", "type": "file"},
        ],
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file("uses: acme/engine/actions/setup@v2\n"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "acme/engine@v2" in out[0][1]


def test_stale_sha_pin_warned(monkeypatch):
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(f"uses: acme/engine/actions/setup@{_SHA}\n"),
        "repos/acme/engine/releases/latest": {"tag_name": "v1.4.0"},
        "repos/acme/engine/commits/v1.4.0": {"sha": _OTHER_SHA},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "v1.4.0" in out[0][1] and _SHA[:7] in out[0][1]


def test_current_sha_pin_silent(monkeypatch):
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml", "notes.md"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            f"uses: acme/engine/actions/setup@{_SHA}\nuses: acme/engine/actions/summary@{_SHA}\n"
        ),
        "repos/acme/engine/releases/latest": {"tag_name": "v1.4.0"},
        "repos/acme/engine/commits/v1.4.0": {"sha": _SHA},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._pin_warnings(_ctx()) == []


def test_self_referencing_pin_ignored(monkeypatch):
    # The engine repository is also a consumer of its own actions (its E2E
    # workflows call them by local path or by slug). Exercised with
    # engine_repo == repo, the one arrangement in which the self-pin exclusion
    # is load-bearing rather than shadowed by the engine-slug filter.
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(f"uses: {_REPO}/actions/setup@{_SHA}\n"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._pin_warnings(_ctx(engine_repo=_REPO)) == []


def test_pin_probe_ignores_another_orgs_shared_action(monkeypatch):
    """The probe's findings are worded about the engine — "a moving ref lets the
    engine change under your deploy credentials", "re-pin to pick up fixes" —
    and are only true of shipmate's own repository. A consumer that also uses
    some other org's shared composite action would otherwise be told its
    unrelated action is a stale engine pin, once per workflow file, which can
    also exhaust GitHub's 10-warning-per-step annotation budget and push the
    real findings off the run page."""
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            "uses: other/shared/actions/lint@v1\n"
            "uses: other/shared/.github/actions/scan@main\n"
            f"uses: {_ENGINE_REPO}/actions/setup@{_SHA}\n"
        ),
        f"repos/{_ENGINE_REPO}/releases/latest": {"tag_name": "v1.4.0"},
        f"repos/{_ENGINE_REPO}/commits/v1.4.0": {"sha": _OTHER_SHA},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    # Only the engine's own stale pin — not the two tag-pinned third-party
    # references, and no release lookup against `other/shared` (whose absence
    # from `responses` would surface as an extra "could not read" note).
    assert len(out) == 1, out
    assert _ENGINE_REPO in out[0][1]
    assert "other/shared" not in out[0][1]


def test_pin_probe_without_the_engine_repo_degrades_to_a_note(monkeypatch):
    """`github.action_repository` is empty when the action runs from a local
    path rather than a pinned slug. Without it the probe cannot tell shipmate's
    pins from anyone else's, so it says pin freshness was not verified instead
    of falling back to warning about every cross-repo pin it can see."""

    def gh(path):
        pytest.fail(f"the pin probe hit the API with no engine repo: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._pin_warnings(_ctx(engine_repo="")) == [doctor.PIN_NO_ENGINE]
    assert doctor.PIN_NO_ENGINE[0] == doctor.NOTICE
    assert "not verified" in doctor.PIN_NO_ENGINE[1]


def test_ctx_from_env_reads_the_engine_repo_and_the_harvest_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.setenv("SHIPMATE_APP_ID", _APP_ID)
    monkeypatch.setenv("SHIPMATE_DEFAULT_BRANCH", _BRANCH)
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path))
    monkeypatch.setenv("SHIPMATE_ENGINE_REPO", _ENGINE_REPO)
    monkeypatch.setenv("SHIPMATE_HARVEST_PENDING", "true")
    ctx = doctor.ctx_from_env()
    assert ctx["engine_repo"] == _ENGINE_REPO
    assert ctx["harvest_pending"] is True
    monkeypatch.delenv("SHIPMATE_ENGINE_REPO")
    monkeypatch.delenv("SHIPMATE_HARVEST_PENDING")
    ctx = doctor.ctx_from_env()
    assert ctx["engine_repo"] == ""
    assert ctx["harvest_pending"] is False


def test_unreadable_release_degrades_to_note(monkeypatch):
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(f"uses: acme/engine/actions/setup@{_SHA}\n"),
    }

    def gh(path):
        if path.startswith("repos/acme/engine/"):
            raise SystemExit("404")
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE  # degrade to a note
    assert "not verified" in out[0][1]


def test_missing_workflows_directory_degrades_to_a_note(monkeypatch):
    """A brand-new consumer's first shipmate PR is the one that ADDS
    `.github/workflows`, so this listing legitimately fails (and it is also how
    a 403 or a transient 5xx arrives). That is the default outcome of the
    first-ever `shipmate doctor`, so it must degrade at NOTICE like the
    unreadable-release path above -- not fall through to warnings()' generic
    WARNING, which renders a literal `::error::` prefix and the internal
    `gh api` invocation into the comment body."""

    def gh(path):
        assert path == f"{_WF_DIR}{_REF}"
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._pin_warnings(_ctx())
    assert out == [doctor.PIN_UNREADABLE]
    assert out[0][0] == doctor.NOTICE
    assert "not verified" in out[0][1]
    assert "::error::" not in out[0][1] and "gh api" not in out[0][1]


def test_workflow_file_read_failure_keeps_the_pins_found_so_far(monkeypatch):
    # A per-file read can fail after the listing succeeded (a file deleted
    # between the two calls, a 403, a transient 5xx). The findings already
    # collected must survive, with the note appended -- not be discarded, and
    # not escalate to the generic degrade.
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("a.yml", "b.yml"),
        f"{_WF_DIR}/a.yml{_REF}": _wf_file("uses: acme/engine/actions/setup@v2\n"),
    }

    def gh(path):
        if path not in responses:
            raise SystemExit(f"::error::command failed (1): gh api {path}")
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 2
    assert out[0][0] == doctor.WARNING and "acme/engine@v2" in out[0][1]
    assert out[1] == doctor.PIN_UNREADABLE


def test_pin_probe_ignores_a_head_sha_that_is_not_a_sha(monkeypatch):
    # A value that isn't a 40-char hex SHA must never reach the request path --
    # the same guard apply-detect puts on SHIPMATE_HEAD_SHA. It now reaches no
    # request path at all: with no usable commit there is nothing to compare a
    # pin against (see the test below), so the probe declines rather than
    # retargeting the gh api URL or silently reading the default branch.
    def gh(path):
        pytest.fail(f"the pin probe hit the API with an unusable head SHA: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    ctx = _ctx(head_sha="main?per_page=1&x=/../")
    assert doctor._pin_warnings(ctx) == [doctor.PIN_NO_COMMIT]


def test_pin_probe_reads_the_commit_under_examination(monkeypatch):
    """The remediation loop the pin warning drives: doctor says "re-pin", the
    consumer opens a PR bumping the SHA, and this probe must read THAT commit's
    workflow files. Reading the default branch (the contents API default) would
    report the pin stale on the very PR that fixes it, once per workflow file."""
    seen = []

    def gh(path):
        seen.append(path)
        if path.endswith(f"plan.yml{_REF}"):
            return _wf_file(f"uses: acme/engine/actions/setup@{_SHA}\n")
        if path == "repos/acme/engine/releases/latest":
            return {"tag_name": "v1.4.0"}
        if path == "repos/acme/engine/commits/v1.4.0":
            return {"sha": _SHA}
        return _wf_listing("plan.yml")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    # Pin == latest release: silent only if the file was read at the head SHA.
    assert doctor._pin_warnings(_ctx()) == []
    contents = [p for p in seen if "/contents/" in p]
    assert contents, "the pin probe made no contents call"
    assert all(p.endswith(_REF) for p in contents), contents


def test_pin_probe_does_not_read_at_all_without_a_commit_under_examination(monkeypatch):
    """`head_sha` is empty on the comment path's degrade branch (the PR head
    could not be read). Reading the default branch there is worse than not
    reading: the pull request that bumps a stale pin carries the new SHA only on
    its own head, so a default-branch read reports the pin stale on the very
    change that fixes it — precisely the failure the `?ref=` was added to
    prevent. Say freshness was not verified instead.

    The annotate path always supplies a validated 40-hex head SHA, so only the
    degrade path reaches this."""

    def gh(path):
        pytest.fail(f"the pin probe hit the API with no commit to read: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._pin_warnings(_ctx(head_sha="")) == [doctor.PIN_NO_COMMIT]
    assert doctor.PIN_NO_COMMIT[0] == doctor.NOTICE
    assert "not verified" in doctor.PIN_NO_COMMIT[1]


def test_release_lookup_prefers_the_public_token(monkeypatch):
    """The App installation token is scoped to the consumer repo and may not
    read the engine repo — the two cross-repo calls must run under
    SHIPMATE_PUBLIC_TOKEN (the workflow token) when it is set, and GH_TOKEN
    must be restored afterwards."""
    seen = []

    def gh(path):
        seen.append((path, os.environ.get("GH_TOKEN")))
        if path == "repos/acme/engine/releases/latest":
            return {"tag_name": "v1.4.0"}
        return {"sha": _SHA}

    monkeypatch.setattr(doctor, "_gh_json", gh)
    monkeypatch.setenv("GH_TOKEN", "app-token")
    monkeypatch.setenv("SHIPMATE_PUBLIC_TOKEN", "workflow-token")
    assert doctor._latest_release_sha("acme/engine") == ("v1.4.0", _SHA)
    assert seen and all(tok == "workflow-token" for _, tok in seen)
    assert os.environ["GH_TOKEN"] == "app-token"  # noqa: S105 - fixture value, not a real token


def test_release_lookup_restores_gh_token_unset(monkeypatch):
    """The safety-critical restore path: when GH_TOKEN was never set, it must
    stay unset afterwards, not end up set to some stale value -- a leaked
    token left in GH_TOKEN would silently re-auth every later probe in the
    same process."""

    def gh(path):
        if path == "repos/acme/engine/releases/latest":
            return {"tag_name": "v1.4.0"}
        return {"sha": _SHA}

    monkeypatch.setattr(doctor, "_gh_json", gh)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("SHIPMATE_PUBLIC_TOKEN", "workflow-token")
    assert doctor._latest_release_sha("acme/engine") == ("v1.4.0", _SHA)
    assert "GH_TOKEN" not in os.environ


def test_team_probe_skipped_without_team(monkeypatch):
    monkeypatch.setattr(doctor, "_gh_json", lambda path: (_ for _ in ()).throw(AssertionError))
    assert doctor._team_warnings(_ctx(team=None)) == []


def test_unresolvable_team_warned(monkeypatch):
    def boom(path):
        assert path == "orgs/o/teams/ops-tem"
        raise SystemExit("404 Not Found")

    monkeypatch.setattr(doctor, "_gh_json", boom)
    out = doctor._team_warnings(_ctx(team="ops-tem"))
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "ops-tem" in out[0][1]
    assert "SHIPMATE_APPROVERS_TEAM" in out[0][1]


def test_resolvable_team_silent(monkeypatch):
    monkeypatch.setattr(doctor, "_gh_json", lambda path: {"slug": "ops"})
    assert doctor._team_warnings(_ctx(team="ops")) == []


def test_team_response_without_slug_warned(monkeypatch):
    """A 200 response that isn't actually the team resource (e.g. the team-slug
    input carrying a path segment that happens to hit some other list endpoint)
    must not be mistaken for a resolved team."""
    monkeypatch.setattr(doctor, "_gh_json", lambda path: {"id": 1})
    out = doctor._team_warnings(_ctx(team="ops"))
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING


def test_one_line_flattens_and_pins_the_truncation_boundary():
    assert doctor._one_line(" a\nb\tc ") == "a b c"
    assert doctor._one_line("x" * 10, limit=10) == "x" * 10  # at the limit: untouched
    out = doctor._one_line("x" * 11, limit=10)
    assert len(out) == 10 and out.endswith("…")  # never longer than `limit`
    assert len(doctor._one_line("é" * 300, limit=120)) == 120  # code points, not bytes


def test_app_permission_probe_skipped_when_not_attempted():
    assert doctor._app_permission_warnings(_ctx(app_permissions_checked=False)) == []


def test_app_permission_ok_silent():
    ctx = _ctx(app_permissions_checked=True, app_permission_error="")
    assert doctor._app_permission_warnings(ctx) == []


def test_app_permission_failure_warned():
    ctx = _ctx(
        app_permissions_checked=True,
        app_permission_error="422 permissions requested are not granted\nsecond line",
    )
    out = doctor._app_permission_warnings(ctx)
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    # Hedged wording: a failed mint usually but not definitively means a
    # missing permission (it also degrades identically on a transient error).
    assert "which usually means" in out[0][1]
    assert "missing a permission" in out[0][1]
    assert "422 permissions requested are not granted second line" in out[0][1]
    assert "\n" not in out[0][1]


def _ann(level="warning", title="t", message="m", check="shipmate · plan / detect"):
    return {
        "annotation_level": level,
        "title": title,
        "message": message,
        "check_name": check,
        "path": ".github/workflows/plan.yml",
        "start_line": 1,
    }


def test_latest_check_ids_keeps_newest_shipmate_run_per_name():
    ga = ', "app_slug": "github-actions", "app_id": 15368}'
    lines = [
        '{"id": 1, "name": "app / dev-eu", "started_at": "2026-07-26T10:00:00Z"' + ga,
        '{"id": 2, "name": "app / dev-eu", "started_at": "2026-07-26T11:00:00Z"' + ga,
        '{"id": 3, "name": "dns / dev-eu", "started_at": "2026-07-26T10:30:00Z"' + ga,
        # the shipmate App's own check runs (apply checks) are kept via app_id
        '{"id": 4, "name": "apply / dev-eu / app", "started_at": "2026-07-26T10:00:00Z", '
        '"app_slug": "shipmate", "app_id": 999}',
        # third-party apps are dropped (out of the harvest's scope)
        '{"id": 5, "name": "codecov/project", "started_at": "2026-07-26T10:00:00Z", '
        '"app_slug": "codecov", "app_id": 254}',
    ]
    assert doctor.latest_check_ids(lines, app_id="999") == [
        (2, "app / dev-eu"),
        (4, "apply / dev-eu / app"),
        (3, "dns / dev-eu"),
    ]


def test_harvest_drops_notices_and_doctors_own_annotations():
    anns = [
        _ann(level="notice"),
        _ann(title=doctor.DOCTOR_TITLE, message="gate ruleset missing"),
        _ann(title="stale codegen"),
        _ann(level="failure", title="plan failed"),
    ]
    sections = doctor.harvest_sections(anns)
    kept = [a["title"] for rows in sections.values() for a in rows]
    assert kept == ["stale codegen", "plan failed"]


def test_report_renders_probe_and_harvest_sections():
    body = doctor.render_report(
        [(doctor.WARNING, "gate ruleset is missing")], [_ann(title="stale codegen")], _ctx()
    )
    assert body.startswith(doctor.DOCTOR_MARKER)
    assert ":warning: gate ruleset is missing" in body
    assert "stale codegen" in body
    assert "shipmate · plan / detect" in body
    assert _ctx()["head_sha"][:7] in body
    assert "1281" in body


def test_report_renders_notes_as_info_not_warning():
    body = doctor.render_report([(doctor.NOTICE, "apply env has no protection")], [], _ctx())
    assert ":information_source: apply env has no protection" in body
    assert ":warning: apply env has no protection" not in body


def test_report_all_clear():
    body = doctor.render_report([], [], _ctx())
    assert "no problems found" in body
    assert "no warnings" in body


def test_all_clear_names_the_environments_the_probes_actually_covered():
    """The two environment probes see only the environments of the stacks this
    pull request changed — the declared set comes from the plan matrix's cell
    summaries — so a categorical "no problems found by the settings probes"
    overclaims: a broken `prod-eu` pair on a PR that touches only dev stacks was
    never looked at. Name the set instead of implying the repository is sound."""
    body = doctor.render_report([], [], _ctx(envs={"dev-eu", "dev-us"}))
    assert "no problems found by the settings probes" in body
    assert "`dev-eu`" in body and "`dev-us`" in body
    assert "changed in this pull request" in body


def test_all_clear_says_when_no_environments_were_probed():
    body = doctor.render_report([], [], _ctx(envs=set(), envs_available=False))
    assert "no environments were probed" in body


def test_all_clear_escapes_and_bounds_the_environment_names():
    # `environment` is read from cell.json, so it is repository data: a name
    # carrying the plan comment's marker must not hijack this comment's
    # identity, and a large fan-out's env list must not blow the size budget in
    # a line no truncation path covers.
    body = doctor.render_report([], [], _ctx(envs={"<!-- shipmate:summary -->"}))
    assert "<!-- shipmate:summary -->" not in body
    assert body.count(doctor.DOCTOR_MARKER) == 1
    wide = doctor.render_report([], [], _ctx(envs={f"env-{i:04}" for i in range(500)}))
    assert len(wide) <= doctor.sc.HARD_CAP
    line = next(ln for ln in wide.splitlines() if "no problems found" in ln)
    assert len(line) < 600


def test_findings_only_fallback_uses_the_same_all_clear_line():
    # Two renderers emit the all-clear; the scope statement must not live in
    # only one of them.
    body = doctor._findings_only_report([], _ctx(envs={"dev-eu"}))
    assert "`dev-eu`" in body
    assert "changed in this pull request" in body


def test_harvest_incomplete_note_says_the_harvest_is_incomplete():
    """The other harvest tests assert `HARVEST_INCOMPLETE in body`, which pins
    the flag -> note wiring but not the note's meaning: swap the constant for
    an all-clear and they all stay green, shipping the exact false all-clear
    the flag exists to prevent. So the text's meaning is pinned here,
    separately, on a stable stem -- a reword has to update this stem
    deliberately instead of silently inverting the claim.

    Stem, not the whole sentence, so ordinary rewording stays cheap: the
    warning level, and a phrase that cannot be read as "everything is fine"."""
    assert doctor.HARVEST_INCOMPLETE.startswith("- :warning:")
    assert "could not read all" in doctor.HARVEST_INCOMPLETE
    assert ":white_check_mark:" not in doctor.HARVEST_INCOMPLETE


def test_report_states_when_the_whole_harvest_failed():
    # An empty harvest from a failed check-runs listing must not read the
    # same as an empty harvest from a genuinely clean commit -- the report
    # must say the harvest itself could not be read, not claim all-clear.
    body = doctor.render_report([], [], _ctx(harvest_failed=True))
    assert doctor.HARVEST_INCOMPLETE in body
    assert "no warnings on this commit's workflow runs" not in body


def test_report_states_when_a_partial_harvest_still_found_warnings():
    # The incompleteness note must be additive, not an alternative to the
    # rows: one check run's annotations fetch can fail while the others
    # return warnings, and then a note emitted only for an empty harvest
    # would let the rows read as the complete set for the commit.
    body = doctor.render_report([], [_ann(title="real warning")], _ctx(harvest_failed=True))
    assert doctor.HARVEST_INCOMPLETE in body
    assert "real warning" in body
    assert "shipmate · plan / detect" in body


def test_report_all_clear_when_harvest_did_not_fail():
    # The other branch of the same conditional: harvest_failed False (the
    # default) with an empty harvest still renders the ordinary all-clear
    # line, not the failure line.
    body = doctor.render_report([], [], _ctx(harvest_failed=False))
    assert "no warnings on this commit's workflow runs" in body
    assert doctor.HARVEST_INCOMPLETE not in body


def test_report_omits_the_incompleteness_note_when_the_harvest_completed():
    # A complete harvest with rows must not carry the note either -- the
    # additive note is keyed on the flag, not on there being rows.
    body = doctor.render_report([], [_ann(title="real warning")], _ctx(harvest_failed=False))
    assert "real warning" in body
    assert doctor.HARVEST_INCOMPLETE not in body


_COMPLETED = '"app_slug": "github-actions", "status": "completed"}'


def test_harvest_pending_is_true_while_a_relevant_run_is_unfinished():
    """An empty harvest cannot distinguish "annotations not recorded yet" from
    "no warnings", so commenting `shipmate doctor` while the plan run is queued
    or in flight printed an all-clear that was never refreshed.

    Keyed on every shipmate-relevant check run, not on the subset
    `latest_check_ids` selects: a queued run has no `started_at` and therefore
    ranks *below* an already-completed run of the same name — the right ranking
    for choosing whose annotations to fetch, but it would hide exactly the
    still-running case this flag exists to report."""
    lines = [
        '{"id": 1, "name": "app / dev-eu", "started_at": "t", ' + _COMPLETED,
        '{"id": 2, "name": "db / dev-eu", "app_slug": "github-actions", "status": "queued"}',
    ]
    assert doctor.harvest_pending(lines) is True
    assert doctor.harvest_pending(lines[:1]) is False


def test_harvest_pending_ignores_third_party_check_runs():
    # Harvest scope is shipmate's own runs plus the consumer's other Actions
    # workflows; a third-party app's perpetually-queued check must not make
    # every report claim the commit's runs had not finished.
    lines = [
        '{"id": 1, "name": "app / dev-eu", "started_at": "t", ' + _COMPLETED,
        '{"id": 5, "name": "codecov/project", "app_slug": "codecov", "app_id": 254, '
        '"status": "in_progress"}',
    ]
    assert doctor.harvest_pending(lines, app_id=_APP_ID) is False


def test_check_ids_mode_writes_the_harvest_pending_step_output(monkeypatch, tmp_path, capsys):
    """The reduction already reads every check run on the commit, so it is also
    where the pending flag is decided; it reaches the render step as a step
    output of the gather step (the reader side is guarded in
    test_comment_ops_action.py). The TSV on stdout must stay exactly (id, name)
    pairs — `load_annotations` splits each line on the first tab and would
    otherwise fold the flag into a check name."""
    out_file = tmp_path / "gh-output"
    out_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("SHIPMATE_DOCTOR_MODE", "check-ids")
    monkeypatch.setenv("SHIPMATE_APP_ID", _APP_ID)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            '{"id": 1, "name": "app / dev-eu", "started_at": "t", ' + _COMPLETED + "\n"
            '{"id": 2, "name": "db / dev-eu", "app_slug": "github-actions", "status": "queued"}\n'
        ),
    )
    doctor.main()
    assert capsys.readouterr().out.splitlines() == ["1\tapp / dev-eu", "2\tdb / dev-eu"]
    assert "harvest_pending=true" in out_file.read_text(encoding="utf-8")


def test_check_ids_mode_runs_without_a_github_output(monkeypatch, capsys):
    # The modes stay runnable outside a runner: no GITHUB_OUTPUT, no crash.
    monkeypatch.setenv("SHIPMATE_DOCTOR_MODE", "check-ids")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    doctor.main()
    assert capsys.readouterr().out == ""


def test_harvest_pending_note_says_runs_had_not_finished():
    # Same posture as the HARVEST_INCOMPLETE stem test: the wiring tests below
    # would all stay green if the constant were swapped for an all-clear, which
    # is the exact false all-clear it exists to prevent.
    assert "had not finished" in doctor.HARVEST_PENDING
    assert "shipmate doctor" in doctor.HARVEST_PENDING  # tells the reader what to do
    assert ":white_check_mark:" not in doctor.HARVEST_PENDING
    assert doctor.HARVEST_PENDING != doctor.HARVEST_INCOMPLETE


def test_report_replaces_the_all_clear_when_runs_had_not_finished():
    body = doctor.render_report([], [], _ctx(harvest_pending=True))
    assert doctor.HARVEST_PENDING in body
    assert "no warnings on this commit's workflow runs" not in body
    # Distinct from harvest_failed, which means the harvest itself errored.
    assert doctor.HARVEST_INCOMPLETE not in body


def test_report_pending_note_is_additive_when_the_harvest_has_rows():
    body = doctor.render_report([], [_ann(title="real warning")], _ctx(harvest_pending=True))
    assert doctor.HARVEST_PENDING in body
    assert "real warning" in body


def test_report_states_pending_and_failed_separately():
    # Two different facts: one run has not finished yet, and some other run's
    # annotations could not be read at all. Neither may absorb the other.
    body = doctor.render_report([], [], _ctx(harvest_pending=True, harvest_failed=True))
    assert doctor.HARVEST_PENDING in body
    assert doctor.HARVEST_INCOMPLETE in body
    assert "no warnings on this commit's workflow runs" not in body


def test_report_notes_possible_annotation_truncation():
    anns = [_ann(title=f"w{i}") for i in range(doctor.ANNOTATION_CAP)]
    body = doctor.render_report([], anns, _ctx())
    assert "may be truncated" in body


def test_report_escapes_hostile_annotation_text():
    body = doctor.render_report(
        [], [_ann(title="x</summary><b>evil", message="[go](http://e)")], _ctx()
    )
    assert "</summary>" not in body
    assert "[go](http://e)" not in body


def test_skipped_environment_probes_are_stated_exactly_once():
    """The "skipped" wording comes from one place -- `_environment_warnings`,
    keyed on `envs_available` -- so the preamble and the finding can neither
    repeat it nor disagree about it."""
    findings = doctor._environment_warnings(_ctx(envs=set(), envs_available=False))
    body = doctor.render_report(findings, [], _ctx(envs_available=False, plan_run_id=""))
    assert "environment probes were skipped" in body
    assert body.count("environment probes were skipped") == 1


def test_provenance_claims_nothing_about_probes_when_the_run_id_was_cleared():
    """`gh run download` can extract the cell summaries and still exit non-zero;
    comment-ops then clears `run_id` while doctor-cells stays populated, so
    `envs_available` is True with an empty `plan_run_id`. A preamble keyed on the
    run id would claim the environment probes were skipped while they ran and
    produced the finding rendered right below it."""
    findings = [(doctor.WARNING, "GitHub Environment `dev-eu-apply` does not exist")]
    body = doctor.render_report(findings, [], _ctx(envs_available=True, plan_run_id=""))
    assert "probes were skipped" not in body
    assert "dev-eu-apply" in body
    assert _HEAD[:7] in body  # the preamble still says what was examined


def test_report_escapes_a_hostile_settings_finding():
    """Settings findings interpolate repository data (a workflow file name
    reaches the pin warning verbatim). A file named `<!-- shipmate:summary
    -->.yml` would otherwise put the PLAN comment's upsert marker inside the
    doctor comment, and actions/summary's marker+Bot upsert would then PATCH
    this comment with the plan body -- destroying the report and orphaning the
    plan comment."""
    finding = (doctor.WARNING, "`<!-- shipmate:summary -->.yml` pins `acme/engine@v2` by tag")
    body = doctor.render_report([finding], [], _ctx())
    assert "<!-- shipmate:summary -->" not in body
    assert "&lt;!-- shipmate:summary --&gt;" in body
    assert body.count(doctor.DOCTOR_MARKER) == 1


def test_findings_only_fallback_escapes_a_hostile_settings_finding():
    # Same escaping on the HARD_CAP fallback path: it renders the findings
    # through _findings_lines, a second renderer that must not bypass it.
    findings = [(doctor.WARNING, "<!-- shipmate:summary -->" + "x" * 200) for _ in range(400)]
    body = doctor.render_report(findings, [], _ctx())
    assert len(body) <= doctor.sc.HARD_CAP
    assert "warnings from this commit's workflow runs" not in body  # the fallback fired
    assert "<!-- shipmate:summary -->" not in body


def test_provenance_run_id_branch_states_the_run_without_a_coverage_claim():
    """The run-id branch must name the run that was read and claim nothing about
    what the probes did with it. `envs_available` and `plan_run_id` can disagree
    (a `gh run download` that extracts files and still exits non-zero clears the
    run id while the cells directory is populated), so any probe-coverage claim
    keyed on the run id can contradict the findings rendered below it -- that
    claim belongs only to `_environment_warnings`' NOTICE, keyed on
    `envs_available`. Pinned on the current stem, so restoring wording that
    implies the declared environment set came from this run fails here."""
    text = doctor._provenance(_ctx(plan_run_id="1281"))
    assert "1281" in text
    assert "cell summaries from plan run" in text
    assert "declared environments" not in text
    assert "probe" not in text


def test_provenance_one_lines_an_overlong_plan_run_id():
    # plan_run_id is interpolated verbatim otherwise -- an unbounded value
    # there would make the preamble itself unbounded, defeating the whole
    # report's size budget regardless of the harvest/findings truncation.
    ctx = _ctx(plan_run_id="9" * 100)
    text = doctor._provenance(ctx)
    assert ("9" * 39 + "…") in text
    assert ("9" * 40) not in text


def test_load_annotations_joins_names_and_tolerates_missing_files(tmp_path):
    (tmp_path / "ann").mkdir()
    (tmp_path / "ann" / "2.json").write_text(json.dumps([_ann(check=None)]), encoding="utf-8")
    (tmp_path / "check-ids.tsv").write_text("2\tapp / dev-eu\n7\tgone / dev-eu\n", encoding="utf-8")
    ctx = _ctx(
        annotations_dir=str(tmp_path / "ann"),
        check_ids_path=str(tmp_path / "check-ids.tsv"),
    )
    anns = doctor.load_annotations(ctx)
    assert [a["check_name"] for a in anns] == ["app / dev-eu"]


def test_load_annotations_skips_non_dict_rows(tmp_path):
    # A payload that parses to a list of non-dict values (e.g. a check run
    # whose annotations endpoint returned something unexpected) must be
    # skipped row-by-row, never raise -- load_annotations's contract is
    # "never fatal".
    (tmp_path / "ann").mkdir()
    (tmp_path / "ann" / "3.json").write_text(json.dumps(["oops", 5, None]), encoding="utf-8")
    (tmp_path / "check-ids.tsv").write_text("3\tapp / dev-eu\n", encoding="utf-8")
    ctx = _ctx(
        annotations_dir=str(tmp_path / "ann"),
        check_ids_path=str(tmp_path / "check-ids.tsv"),
    )
    assert doctor.load_annotations(ctx) == []


def test_latest_check_ids_tolerates_malformed_and_non_object_lines():
    lines = [
        "not json at all",
        "null",
        "5",
        '{"id": 9, "name": "ok / dev-eu", "started_at": "2026-07-26T10:00:00Z", '
        '"app_slug": "github-actions"}',
    ]
    assert doctor.latest_check_ids(lines) == [(9, "ok / dev-eu")]


def test_latest_check_ids_skips_runs_with_unusable_id():
    lines = [
        '{"name": "no-id / dev-eu", "started_at": "t", "app_slug": "github-actions"}',
        '{"id": "abc", "name": "bad-id / dev-eu", "started_at": "t", "app_slug": "github-actions"}',
        '{"id": 7, "name": "good / dev-eu", "started_at": "t", "app_slug": "github-actions"}',
    ]
    assert doctor.latest_check_ids(lines) == [(7, "good / dev-eu")]


def test_report_no_truncation_note_when_neither_level_hits_the_cap():
    # 6 warnings + 4 failures on one check = 10 rows shown, but GitHub's cap
    # is per level (10 warnings, 10 notices) -- neither level here is
    # anywhere near truncated, so the note must not fire on the combined count.
    anns = [_ann(level="warning", title=f"w{i}") for i in range(6)]
    anns += [_ann(level="failure", title=f"f{i}") for i in range(4)]
    body = doctor.render_report([], anns, _ctx())
    assert "may be truncated" not in body


def test_report_truncation_note_fires_on_raw_count_including_doctors_own():
    # 10 raw warnings on one check, 2 of them doctor's own (dropped by
    # harvest_sections, leaving only 8 rows shown) -- GitHub's cap already
    # hit on the raw listing, so the note must still fire even though the
    # rendered row count is under ANNOTATION_CAP.
    anns = [_ann(level="warning", title=doctor.DOCTOR_TITLE) for _ in range(2)]
    anns += [_ann(level="warning", title=f"w{i}") for i in range(8)]
    body = doctor.render_report([], anns, _ctx())
    assert "may be truncated" in body


def test_report_harvest_truncates_to_stay_under_hard_cap_and_notes_dropped_count():
    # The harvest keeps every github-actions check run on the commit, not
    # just shipmate's -- a repo with broad lint/test annotations (or a large
    # fan-out plan with one failure per cell) can produce far more content
    # than a single PR comment can hold.
    anns = [_ann(title=f"warning {i}", message="m" * 300, check=f"check-{i}") for i in range(400)]
    body = doctor.render_report([], anns, _ctx())
    assert len(body) <= doctor.sc.HARD_CAP
    assert "omitted" in body


def test_report_findings_alone_stay_under_hard_cap_when_harvest_is_empty():
    # The harvest is budgeted against sc.SIZE_BUDGET, but nothing budgets the
    # settings-probe findings themselves -- hundreds of long findings (e.g.
    # one warning per stale pin across many workflow files) can alone exceed
    # sc.HARD_CAP, which is exactly the 422 the size budget was meant to
    # prevent. render_report must fall back to a truncated findings list.
    findings = [(doctor.WARNING, "x" * 200) for _ in range(400)]
    body = doctor.render_report(findings, [], _ctx())
    assert len(body) <= doctor.sc.HARD_CAP
    assert "omitted" in body
    # the fallback drops the harvest section entirely
    assert "warnings from this commit's workflow runs" not in body


def test_harvest_header_one_lines_a_pathological_check_name():
    # An unbounded check name (author-controlled, forwarded verbatim from a
    # check run) must not be able to consume most of the harvest budget by
    # itself before per-row truncation even gets a chance to kick in.
    long_name = "x" * 5000
    anns = [_ann(check=long_name)]
    sections = doctor.harvest_sections(anns)
    lines, dropped = doctor._harvest_lines(sections, anns, budget=10_000)
    assert dropped == 0
    header = next(line for line in lines if line.startswith("- **"))
    assert len(header) < 130  # _one_line(name, 120) + '- **' + '**' wrapping, not 5000


def test_harvest_never_emits_a_dangling_section_header():
    # A section whose header fits but whose only row doesn't must not leave
    # a bullet with no children -- both the header and the row are dropped
    # together.
    fits = _ann(check="aaa-fits", title="t", message="m")
    toolong = _ann(check="zzz-toolong", title="t", message="m")
    header_fits = f"- **{doctor._md_escape('aaa-fits')}**"
    row_fits = doctor._render_annotation_row(fits)
    # Room for "aaa-fits"'s header + row, plus "zzz-toolong"'s header alone --
    # so a budget check that measured only the header would admit zzz-toolong,
    # while the combined header+first-row check refuses it.
    budget = (
        len(header_fits)
        + 1
        + len(row_fits)
        + 1
        + len(f"- **{doctor._md_escape('zzz-toolong')}**")
        + 1
    )
    sections = doctor.harvest_sections([fits, toolong])
    lines, dropped = doctor._harvest_lines(sections, [fits, toolong], budget)
    assert lines == [header_fits, row_fits]
    assert dropped == 1
    assert not any("zzz-toolong" in line for line in lines)


def test_emit_section_guards_against_an_empty_row_list():
    # harvest_sections never produces an empty group (setdefault+append), but
    # _emit_section indexes rendered[0] -- a latent IndexError for any future
    # caller that does pass one. Never raises; adds nothing, drops nothing.
    lines = []
    used, added, dropped = doctor._emit_section(lines, "empty-check", [], 0, 1000)
    assert (used, added, dropped) == (0, 0, 0)
    assert lines == []


def test_doctor_marker_matches_action_upsert():
    # Coupling: the marker doctor embeds <-> the marker the comment-ops
    # action's doctor upsert step greps for. Drift = a new comment every run
    # instead of an edit-in-place. Assert the action site carries the
    # script's marker and that its doctor step actually invokes the script.
    engine = pathlib.Path(__file__).resolve().parents[2]
    src = (engine / "actions" / "comment-ops" / "action.yml").read_text(encoding="utf-8")
    assert src.count(doctor.DOCTOR_MARKER) >= 1, "upsert step no longer greps the script's marker"
    assert "scripts/doctor" in src, "comment-ops action no longer calls scripts/doctor"
