import importlib.util
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
        "head_sha": "f" * 40,
        "plan_run_id": "1281",
        "annotations_dir": "ann",
        "check_ids_path": "check-ids.tsv",
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
    return {
        "name": name,
        "protection_rules": [{"type": t} for t in rules],
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
        f"repos/{_REPO}/contents/.github/workflows": [],
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


def test_plan_env_with_reviewers_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/environments/dev-eu": _env("dev-eu", rules=("required_reviewers",)),
        f"repos/{_REPO}/environments/dev-eu-apply": _env(
            "dev-eu-apply", rules=("required_reviewers",)
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "dev-eu" in out[0][1] and "hang" in out[0][1]


def test_apply_env_without_protection_noted(monkeypatch):
    responses = {
        f"repos/{_REPO}/environments/dev-eu": _env("dev-eu"),
        f"repos/{_REPO}/environments/dev-eu-apply": _env("dev-eu-apply"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE  # note, not warning
    assert "dev-eu-apply" in out[0][1] and "no protection" in out[0][1]


def test_plan_env_branch_policy_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/environments/dev-eu": _env(
            "dev-eu", branch_policy={"protected_branches": True}
        ),
        f"repos/{_REPO}/environments/dev-eu-apply": _env(
            "dev-eu-apply", rules=("required_reviewers",)
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "branch policy" in out[0][1]


def test_env_protection_missing_env_is_not_this_probes_problem(monkeypatch):
    def boom(path):
        raise SystemExit("404")

    monkeypatch.setattr(doctor, "_gh_json", boom)
    assert doctor._env_protection_warnings(_ctx()) == []


def _wf_listing(*names):
    return [{"name": n, "type": "file"} for n in names]


def _wf_file(text):
    import base64

    return {"content": base64.b64encode(text.encode()).decode()}


_SHA = "a" * 40
_OTHER_SHA = "b" * 40


def test_tag_pin_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/contents/.github/workflows": _wf_listing("plan.yml"),
        f"repos/{_REPO}/contents/.github/workflows/plan.yml": _wf_file(
            "uses: acme/engine/actions/setup@v2\n"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "tag or branch" in out[0][1] and "acme/engine@v2" in out[0][1]


def test_stale_sha_pin_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/contents/.github/workflows": _wf_listing("plan.yml"),
        f"repos/{_REPO}/contents/.github/workflows/plan.yml": _wf_file(
            f"uses: acme/engine/actions/setup@{_SHA}\n"
        ),
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
        f"repos/{_REPO}/contents/.github/workflows": _wf_listing("plan.yml", "notes.md"),
        f"repos/{_REPO}/contents/.github/workflows/plan.yml": _wf_file(
            f"uses: acme/engine/actions/setup@{_SHA}\nuses: acme/engine/actions/summary@{_SHA}\n"
        ),
        "repos/acme/engine/releases/latest": {"tag_name": "v1.4.0"},
        "repos/acme/engine/commits/v1.4.0": {"sha": _SHA},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._pin_warnings(_ctx()) == []


def test_self_referencing_pin_ignored(monkeypatch):
    responses = {
        f"repos/{_REPO}/contents/.github/workflows": _wf_listing("plan.yml"),
        f"repos/{_REPO}/contents/.github/workflows/plan.yml": _wf_file(
            f"uses: {_REPO}/actions/setup@{_SHA}\n"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._pin_warnings(_ctx()) == []


def test_unreadable_release_degrades_to_note(monkeypatch):
    responses = {
        f"repos/{_REPO}/contents/.github/workflows": _wf_listing("plan.yml"),
        f"repos/{_REPO}/contents/.github/workflows/plan.yml": _wf_file(
            f"uses: acme/engine/actions/setup@{_SHA}\n"
        ),
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
