import importlib.util
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
        "harvest_failed": False,
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


def test_quoted_tag_pin_warned(monkeypatch):
    # Some YAML formatters quote the `uses:` value -- the anchor must not
    # make those pins invisible to the probe.
    responses = {
        f"repos/{_REPO}/contents/.github/workflows": _wf_listing("plan.yml"),
        f"repos/{_REPO}/contents/.github/workflows/plan.yml": _wf_file(
            'uses: "acme/engine/actions/setup@v2"\n'
        ),
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
        f"repos/{_REPO}/contents/.github/workflows": [
            {"name": "sub.yml", "type": "dir"},
            {"name": "plan.yml", "type": "file"},
        ],
        f"repos/{_REPO}/contents/.github/workflows/plan.yml": _wf_file(
            "uses: acme/engine/actions/setup@v2\n"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "acme/engine@v2" in out[0][1]


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


def test_report_states_when_the_harvest_failed():
    # An empty harvest from a failed check-runs listing must not read the
    # same as an empty harvest from a genuinely clean commit -- the report
    # must say the harvest itself could not be read, not claim all-clear.
    body = doctor.render_report([], [], _ctx(harvest_failed=True))
    assert "could not read this commit's check runs" in body
    assert "no warnings on this commit's workflow runs" not in body


def test_report_all_clear_when_harvest_did_not_fail():
    # The other branch of the same conditional: harvest_failed False (the
    # default) with an empty harvest still renders the ordinary all-clear
    # line, not the failure line.
    body = doctor.render_report([], [], _ctx(harvest_failed=False))
    assert "no warnings on this commit's workflow runs" in body
    assert "could not read this commit's check runs" not in body


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


def test_report_states_when_environment_probes_were_skipped():
    body = doctor.render_report([], [], _ctx(envs_available=False, plan_run_id=""))
    assert "no plan run" in body


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
    # so the header-only check the old code used would pass for zzz-toolong,
    # while the new combined header+first-row check correctly refuses it.
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
