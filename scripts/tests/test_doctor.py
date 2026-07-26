import importlib.util
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
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._environment_warnings(_ctx(envs=set(), envs_available=False))
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE
    assert "no plan run" in out[0][1]


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


def test_healthy_repo_emits_nothing(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu", "dev-eu-apply"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.warnings(_ctx()) == []


def test_missing_environment_pair_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        # dev-eu-apply is missing
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu"),
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
    def fake_gh_json(path):
        if "rules/branches" in path:
            raise SystemExit("::error::command failed (1): gh api ...")
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
    def fake_gh_json(path):
        if "rules/branches" in path:
            raise RuntimeError("connection reset")
        return _environments("dev-eu", "dev-eu-apply")

    monkeypatch.setattr(doctor, "_gh_json", fake_gh_json)
    out = doctor.warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "could not verify" in text and "probe skipped" in text
