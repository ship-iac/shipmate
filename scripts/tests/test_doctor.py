import io
import json
import os
import re
import textwrap

import pytest
from _loader import ACTIONS, ENGINE, SCRIPTS, load_script

doctor = load_script("doctor")

# The ```yaml fences of a docs page, dedented by their own indent -- the same
# selector test_docs_summary_call_wiring.py uses, for the one test below that
# runs a probe over the wrapper the page tells consumers to paste.
_YAML_FENCE = re.compile(r"^(?P<indent>[ \t]*)```yaml[ \t]*$\n(?P<body>.*?)^\1```", re.M | re.S)

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
        "plan_run_ids": ["1281"],
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


@pytest.fixture(autouse=True)
def _plan_env_secret_token(monkeypatch):
    """The plan-env secret probe reads its own env-scoped token and reports the
    check as not performed without one -- which would otherwise show up as an
    extra WARNING in every test that goes through `warnings()`. The tests for
    the absent-token behaviour delete it explicitly."""
    monkeypatch.setenv("SHIPMATE_ENV_TOKEN", "envtok")


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


def test_envs_unavailable_skips_the_probes_that_need_the_declared_set(monkeypatch):
    """Without the declared environment set, the only findings a listing alone
    supports are the ambiguous-naming ones -- a missing half of a split pair is
    unknowable, since nothing says `prod` is an environment this repository
    declares."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu", "dev-eu-plan", "prod-apply"))
    out = doctor._environment_warnings(_ctx(envs=set(), envs_available=False))
    assert [lvl for lvl, _ in out] == [doctor.WARNING, doctor.NOTICE]
    assert "`dev-eu` and `dev-eu-plan` exist side by side" in out[0][1]
    assert all("prod" not in text for _, text in out)
    assert "no plan run" in out[1][1]


def test_envs_unavailable_reports_the_ambiguity_the_declared_set_would_have(monkeypatch):
    """The finding is the same one a green plan run produces, so a mid-migration
    doctor is not worth less than a post-migration one. One flaked cell out of a
    13-cell fan-out withholds the declared set, and the ambiguity warning is the
    reason to run doctor between the merge and the delete."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu", "dev-eu-plan", "dev-eu-apply"))
    dark = doctor._environment_warnings(_ctx(envs=set(), envs_available=False))
    lit = doctor._environment_warnings(_ctx(envs={"dev-eu"}))
    assert [t for lvl, t in dark if lvl == doctor.WARNING] == [t for _, t in lit]


def test_split_naming_alone_is_never_read_as_ambiguous(monkeypatch):
    """The name scan must not turn every split repository's report into a
    warning: `<env>-plan`/`<env>-apply` with no bare `<env>` is the default
    naming, and only the bare name existing beside a suffixed one is ambiguous."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu-plan", "dev-eu-apply", "prod-plan"))
    out = doctor._environment_warnings(_ctx(envs=set(), envs_available=False))
    assert [lvl for lvl, _ in out] == [doctor.NOTICE]


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


def _pull_request_rule(code_owner=True, count=1):
    return {
        "type": "pull_request",
        "parameters": {
            "required_approving_review_count": count,
            "require_code_owner_review": code_owner,
        },
    }


def _gate_rule(integration_id=999, strict=True):
    """The `rules/branches` payload both rule probes read. Carries a healthy
    `pull_request` rule so tests aimed at the gate probe don't pick up the
    review probe's finding as incidental noise -- the same reason
    `_quiet_new_probes` exists."""
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
        _pull_request_rule(),
    ]


def _environments(*names, total=None):
    """The environments listing. `total` defaults to the number of names given;
    pass a larger value to model the truncated read `per_page=100` without
    pagination can produce."""
    return {
        "total_count": len(names) if total is None else total,
        "environments": [{"name": n} for n in names],
    }


def _secrets(*names, total=None):
    """An environment secrets listing. `total` defaults to the number of names
    given; pass a larger value to model the truncated read `per_page=100`
    without pagination can produce."""
    return {
        "total_count": len(names) if total is None else total,
        "secrets": [{"name": n} for n in names],
    }


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
    """Healthy responses for the env-protection, engine-environment,
    plan-env-secret, pin-freshness, fork-trigger, summary-wiring and
    dispatch-wiring probes, so tests exercising the older gate/environment probes
    via the top-level `warnings()` don't pick up incidental noise from these
    seven. The retired-input probe needs no response here: the listing below names
    no `apply.yml`, which is the only file it judges.

    The last four read the same workflow listing. `_QUIET_PLAN`'s `uses:` lines
    are engine pins, so the pin probe has something to read and needs the
    release endpoints to agree with them -- the pinned SHA and the SHA the
    release lookup returns are the same `_SHA`, or it reports staleness. That
    same file is on `pull_request_target` and named `plan.yml`, which is what
    keeps the fork-trigger probe quiet: it is the exemption, not the absence of
    the trigger. Its summary call carries both normalized inputs, which keeps
    the summary-wiring probe quiet, and its dispatch leg -- the trigger, the
    `pr_number` input, the `pr-facts` step -- keeps the dispatch-wiring probe
    quiet. The plan-env
    secret probe reads one listing per plan env; an empty one keeps the healthy
    path quiet."""
    return {
        f"repos/{_REPO}/environments/dev-eu-plan": _env("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(),
        f"repos/{_REPO}/environments/dev-eu-apply": _env(
            "dev-eu-apply", rules=("required_reviewers",)
        ),
        f"repos/{_REPO}/environments/shipmate-engine": {
            "name": "shipmate-engine",
            "deployment_branch_policy": {"custom_branch_policies": True},
        },
        f"repos/{_REPO}/environments/shipmate-engine/deployment-branch-policies": {
            "branch_policies": [{"name": _BRANCH}]
        },
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(_QUIET_PLAN),
        f"repos/{_ENGINE_REPO}/releases/latest": {"tag_name": "v9.9.9"},
        f"repos/{_ENGINE_REPO}/commits/v9.9.9": {"sha": _SHA},
    }


def test_healthy_repo_emits_nothing(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
        **_quiet_new_probes(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.warnings(_ctx()) == []


def test_missing_environment_of_the_split_pair_warned(monkeypatch):
    """Split mode with one half absent names the absent half SPECIFICALLY, not
    the pair: naming both would tell a consumer to create an environment they
    already have."""
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        # dev-eu-apply is missing; shipmate-engine present so only the pair
        # probe's own finding surfaces
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "shipmate-engine"),
        **_quiet_new_probes(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "`dev-eu-apply`" in text
    assert "`dev-eu-plan`" not in text


def _existence(*names):
    """`_environment_warnings`' only read: the environments listing."""
    return lambda path: _environments(*names)


def test_env_mode_names_each_of_the_four_listings():
    """The whole inference table, pinned on the returned string. The mode comes
    from environment NAMES and nothing else -- reading the repository variable
    that actually selects it would need an App permission `app/manifest.json`
    does not declare."""
    assert doctor._env_mode("dev-eu", {"dev-eu", "dev-eu-apply"}) == "ambiguous"
    assert doctor._env_mode("dev-eu", {"dev-eu"}) == "shared"
    assert doctor._env_mode("dev-eu", {"dev-eu-plan", "dev-eu-apply"}) == "split"
    assert doctor._env_mode("dev-eu", {"prod-eu-plan"}) == "missing"
    # Either half of the suffixed pair alone still means split -- the missing
    # half is a finding, not a different mode.
    assert doctor._env_mode("dev-eu", {"dev-eu-plan"}) == "split"
    assert doctor._env_mode("dev-eu", {"dev-eu-apply"}) == "split"
    assert doctor._env_mode("dev-eu", {"dev-eu", "dev-eu-plan"}) == "ambiguous"


def test_no_environment_at_all_names_both_modes(monkeypatch):
    """A consumer with nothing created must be able to reach either mode from
    the finding alone: both split names, the shared name, and the variable that
    opts into shared."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("shipmate-engine"))
    out = doctor._environment_warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "`dev-eu-plan`" in text
    assert "`dev-eu-apply`" in text
    assert "`dev-eu`" in text
    assert "SHIPMATE_SHARED_ENVS" in text


def test_ambiguous_environment_naming_is_the_phantom_control_warning(monkeypatch):
    """Both namings present is the one silent failure: whichever environment
    nothing binds is protected by rules in no code path, and it reads as a control
    that is there. So the finding names both, says the binding is decided by the
    variable and by the consumer's own plan.yml, and hedges *which* naming is the
    unbound one -- with a static plan-side `-plan` binding and the env shared,
    both namings are live, so this may not assert that one is inert nor advise
    deleting it."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu", "dev-eu-apply"))
    out = doctor._environment_warnings(_ctx())
    # Two findings and no more: the ambiguity WARNING plus the missing `dev-eu-plan`
    # (its own test below). Without a count, `out[0]` is an unpinned position.
    assert len(out) == 2, out
    level, text = out[0]
    assert level == doctor.WARNING
    assert "`dev-eu`" in text
    assert "`dev-eu-apply`" in text
    assert "SHIPMATE_SHARED_ENVS" in text
    assert "plan.yml" in text
    # The binding half...
    assert "Either naming may therefore be bound by nothing" in text
    # ...and the phantom-control half this test is named for. Both halves, or the
    # name and docstring claim more than the body checks.
    assert "reading as a control that is in no code path" in text
    assert "the other is bound by nothing" not in text
    assert "Delete the environments" not in text


def test_ambiguous_naming_still_reports_the_missing_half(monkeypatch):
    """A consumer holding `dev-eu` + `dev-eu-plan` and no `dev-eu-apply` must
    still be told `dev-eu-apply` does not exist. The ambiguity WARNING says
    nothing about which environments exist, so dropping this loop hides a
    genuinely half-created split pair behind it -- fail-closed."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu", "dev-eu-plan"))
    out = doctor._environment_warnings(_ctx())
    missing = [t for lvl, t in out if lvl == doctor.WARNING and "does not exist" in t]
    assert len(missing) == 1, out
    assert "`dev-eu-apply` does not exist" in missing[0]
    # Not "cannot apply": while `dev-eu` exists it may be what the apply waves
    # bind, so the consequence is conditional.
    assert "cannot apply" not in missing[0]


def test_split_missing_half_does_not_claim_the_jobs_cannot_run(monkeypatch):
    """Same correction as the MISSING-mode finding, in the other branch of the
    same helper: with `dev-eu-plan` present and `dev-eu-apply` absent the apply
    binds a name GitHub auto-creates empty and, on a layout injecting nothing,
    proceeds. "cannot apply" sends the reader looking for a failed run."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu-plan"))
    out = doctor._environment_warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "`dev-eu-apply` does not exist" in text
    assert "cannot apply" not in text
    assert "auto-creates empty" in text


def test_missing_mode_says_the_binding_auto_creates_an_empty_environment(monkeypatch):
    """`env:<env>` stacks with neither suffixed environment do NOT stop planning:
    the binding resolves to a name GitHub auto-creates empty, so the plan runs and
    describes whatever the layout defaults to. Claiming they "cannot plan or
    apply" sends the reader looking for a failed run there is none of."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("shipmate-engine"))
    out = doctor._environment_warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "cannot plan or apply" not in text
    assert "auto-creates empty" in text


def test_shared_environment_produces_no_existence_finding(monkeypatch):
    """The bare name alone IS shared mode, a supported configuration -- not a
    half-created split pair."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu"))
    assert doctor._environment_warnings(_ctx()) == []


def test_gate_rule_wrong_integration_id_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(integration_id=15368),
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
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
            {"type": "deletion", "parameters": {}},
            _pull_request_rule(),
        ],
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
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
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
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
        return _environments(
            "dev-eu-plan", "shipmate-engine"
        )  # dev-eu-apply missing -> its own warning

    monkeypatch.setattr(doctor, "_gh_json", fake_gh_json)
    out = doctor.warnings(_ctx())
    # Two probes read rules/branches and degrade independently -- that is the
    # point of the per-probe read; one endpoint failing must not let either
    # finding be silently attributed to the other.
    assert len(out) == 3
    texts = [t for _, t in out]
    degraded = [t for t in texts if "could not verify" in t and "probe skipped" in t]
    assert len(degraded) == 2
    assert any("gate rule" in t for t in degraded)
    assert any("review rule" in t for t in degraded)
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
        return _environments("dev-eu-plan", "dev-eu-apply", "shipmate-engine")

    monkeypatch.setattr(doctor, "_gh_json", fake_gh_json)
    out = doctor.warnings(_ctx())
    assert len(out) == 2
    assert all(level == doctor.WARNING for level, _ in out)
    assert all("could not verify" in t and "probe skipped" in t for _, t in out)


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
        return _environments("dev-eu-plan", "dev-eu-apply", "shipmate-engine")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor.warnings(_ctx())
    assert len(out) == 2
    level, text = next((lv, t) for lv, t in out if "gate rule" in t)
    assert level == doctor.WARNING
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
        _env("dev-eu-plan", rules=("required_reviewers",)),
        _env("dev-eu-apply", rules=("required_reviewers",)),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    # Not "will hang waiting for approval": `approval` is every rule that isn't
    # a branch policy, so a wait timer lands here too and is not an approval.
    assert "`dev-eu-plan`" in out[0][1] and "will not start immediately" in out[0][1]
    assert "approval" not in out[0][1]


def test_plan_env_wait_timer_is_not_diagnosed_as_an_approval_hang(monkeypatch):
    # docs/troubleshooting.md deliberately lumps wait timers in with
    # reviewers (both stop a plan job from starting when it should), so the
    # finding must fire -- but its wording must fit the rule it names.
    responses = _protection(
        _env("dev-eu-plan", rules=("wait_timer",)),
        _env("dev-eu-apply", rules=("required_reviewers",)),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert "wait_timer" in out[0][1]
    assert "will not start immediately" in out[0][1]
    assert "approval" not in out[0][1]


def test_apply_env_without_approval_rules_noted(monkeypatch):
    responses = _protection(_env("dev-eu-plan"), _env("dev-eu-apply"))
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
        _env("dev-eu-plan"),
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
        _env("dev-eu-plan"),
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
        _env("dev-eu-plan", branch_policy={"protected_branches": True}),
        _env("dev-eu-apply", rules=("required_reviewers",)),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "branch policy" in out[0][1]


def test_shared_env_with_reviewers_warns_about_plan_cells_and_drift(monkeypatch):
    """Protection rules gate every job binding the environment and GitHub offers
    no per-job filter, so on a shared environment reviewers stall the plan cells
    AND the nightly drift run -- both have to be named, or a consumer reads the
    finding as being only about applies."""
    responses = _protection(_env("dev-eu", rules=("required_reviewers",)))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "`dev-eu`" in out[0][1]
    assert "required_reviewers" in out[0][1]
    assert "plan cells" in out[0][1]
    assert "drift" in out[0][1]


def test_shared_env_with_a_branch_policy_is_a_notice_naming_the_trade_both_ways(monkeypatch):
    """NOTICE, not the WARNING a split plan environment gets: on a shared
    environment the policy is a real control over which branches may claim its
    secrets, and it is simultaneously what refuses plan cells whose base ref it
    does not name. A consumer whose pull requests all target the default branch
    is correct to set it, so a WARNING would be one nobody can clear."""
    responses = _protection(_env("dev-eu", branch_policy={"protected_branches": True}))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert [lvl for lvl, _ in out] == [doctor.NOTICE, doctor.NOTICE]
    policy = next(t for _, t in out if "branch policy" in t)
    assert "secrets" in policy  # the control it provides
    assert "base ref" in policy  # the plan cells it can refuse


def test_ambiguous_bare_env_with_a_branch_policy_keeps_the_plan_stall_warning(monkeypatch):
    """The NOTICE above is shared mode's accepted trade and only shared mode's.
    While both namings exist the bare environment is what an unmigrated `plan.yml`
    still binds, so a policy on it is the plan-stall misconfiguration the plan
    environment's WARNING exists to diagnose -- not a legitimate secret-release
    control. Downgrading it here made an unfinished migration a note."""
    responses = _protection(
        _env("dev-eu", branch_policy={"protected_branches": True}),
        _env("dev-eu-apply"),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    policy = [(lvl, t) for lvl, t in out if "branch policy" in t]
    assert len(policy) == 1, out
    assert policy[0][0] == doctor.WARNING
    assert "base ref" in policy[0][1]
    # The shared-mode NOTICE's own clause: it may not be reused here, because
    # while the naming is ambiguous the policy is not "correct".
    assert "Correct while every pull request targets a branch it names" not in policy[0][1]


def test_shared_env_without_approval_rules_says_no_gate_is_available(monkeypatch):
    """The split apply-env note says applies are unreviewed; on a shared
    environment it must also say that adding a reviewer is not an option while
    the environment is shared -- otherwise the fix it implies stalls every plan
    cell."""
    responses = _protection(_env("dev-eu"))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE
    assert "`dev-eu`" in out[0][1]
    assert "unreviewed" in out[0][1]
    assert "no reviewer gate is available" in out[0][1]


def test_ambiguous_naming_reads_the_protection_shape_of_both_namings(monkeypatch):
    """Ambiguous mode reads every environment that exists, not just one naming's
    -- pinned on the requested paths, which is all this asserts. The
    report-level statement that one naming may be bound by nothing is
    `_environment_warnings`' ambiguity WARNING, not a per-rule finding here."""
    responses = _protection(
        _env("dev-eu"),
        _env("dev-eu-plan"),
        _env("dev-eu-apply", rules=("required_reviewers",)),
    )
    seen = []

    def gh(path):
        seen.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    doctor._env_protection_warnings(_ctx())
    assert f"repos/{_REPO}/environments/dev-eu" in seen
    assert f"repos/{_REPO}/environments/dev-eu-plan" in seen
    assert f"repos/{_REPO}/environments/dev-eu-apply" in seen


# The three forms of the shared-role opening clause, hand-written rather than
# imported from `doctor`: `_SHARED_ASSERTED` is the flat assertion no mode may
# produce, the other two are the whole opening clause each mode must produce.
_SHARED_ASSERTED = "GitHub Environment `dev-eu`, shared between plan and apply,"
_SHARED_INTRO = (
    "GitHub Environment `dev-eu` — shared between plan and apply while `dev-eu` is "
    "listed in the `SHIPMATE_SHARED_ENVS` repository variable, which doctor cannot "
    "read —"
)
#: Hedged both ways: the ambiguous clause may not assert that the bare
#: environment IS shared, and may not assert that nothing binds it either -- a
#: static `<env>-plan` plan-side binding with the env shared leaves both namings
#: live, and doctor reads neither the variable nor the consumer's `plan.yml`.
_AMBIGUOUS_INTRO = (
    "GitHub Environment `dev-eu` — shared between plan and apply only if `dev-eu` is "
    "listed in the `SHIPMATE_SHARED_ENVS` repository variable, which doctor cannot "
    "read, and the suffixed naming exists beside it, so which naming each path binds "
    "is undetermined —"
)
#: The flat "nothing binds it" assertion the ambiguous clause used to make.
_AMBIGUOUS_UNBOUND = "bound by nothing at all otherwise"


def test_shared_role_findings_hedge_the_mode_when_the_naming_is_ambiguous(monkeypatch):
    """With both namings present the sibling ambiguity WARNING says the binding is
    undetermined, so a finding here may not assert that the bare environment IS
    shared -- nor that nothing binds it, which the ambiguous branch-policy WARNING
    in the same report would contradict, and which a reader could act on by
    deleting an environment an unmigrated `plan.yml` still binds. Two findings in
    one report may not contradict each other; the plan-env secret notice has the
    same rule for the same reason."""
    responses = _protection(
        _env("dev-eu", rules=("required_reviewers",)),
        _env("dev-eu-apply", rules=("required_reviewers",)),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert _SHARED_ASSERTED not in text
    assert _AMBIGUOUS_UNBOUND not in text
    assert text.startswith(_AMBIGUOUS_INTRO)


def test_a_shared_env_names_the_variable_its_mode_depends_on(monkeypatch):
    """Shared mode is selected by `SHIPMATE_SHARED_ENVS`, which doctor cannot
    read, so this finding may not assert the mode either: a migration that
    renamed the environments and forgot the variable has applies binding
    `dev-eu-apply` while the bare `dev-eu` looks shared. Its clause differs from
    the ambiguous one -- here the plan side does bind this environment -- so both
    whole clauses are pinned."""
    responses = _protection(_env("dev-eu", rules=("required_reviewers",)))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert _SHARED_ASSERTED not in out[0][1]
    assert out[0][1].startswith(_SHARED_INTRO)


def test_env_protection_missing_env_is_not_this_probes_problem(monkeypatch):
    """An environment that does not exist is `_environment_warnings`' finding,
    not this probe's — so a name absent from the environments listing is skipped
    without a per-environment read and without a finding. That used to be
    achieved by catching every per-environment exception and continuing, which
    silenced 403s and 5xx on environments that DO exist."""
    responses = _protection(_env("dev-eu-plan"), listed=["dev-eu-plan"])
    seen = []

    def gh(path):
        seen.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._env_protection_warnings(_ctx()) == []
    assert f"repos/{_REPO}/environments/dev-eu-apply" not in seen


def test_env_protection_reads_nothing_when_no_environment_was_declared(monkeypatch):
    """With no declared environment set there is nothing to probe, so the
    listing must not be read either — otherwise a repository whose token cannot
    list environments collects a "could not verify the env protection settings"
    degrade for a probe that had no work to do, alongside
    `_environment_warnings`' (correct) skipped statement."""

    def gh(path):
        pytest.fail(f"the env protection probe hit the API with no envs: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._env_protection_warnings(_ctx(envs=set(), envs_available=False)) == []


def test_env_protection_unreadable_existing_env_is_a_notice_naming_it(monkeypatch):
    """A 403 or 5xx on an environment that IS in the listing was
    indistinguishable from a 404 (`bm.gh_json`'s exception carries no status
    code) and got swallowed, so the report went on to say the settings probes
    found no problems. Listing first makes the two distinguishable:
    present-but-unreadable is a note that names the environment."""
    responses = _protection(_env("dev-eu-plan"), listed=["dev-eu-plan", "dev-eu-apply"])

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


def _engine_env_responses(env=None, policies=None, listed=True):
    """Responses for `_engine_environment_warnings`. `listed` controls whether
    `shipmate-engine` appears in the environments **listing** -- the only
    thing existence is decided from -- independently of `env`/`policies`,
    which answer the per-environment reads that only ever run once existence
    is already established."""

    def fake(path):
        if path == f"repos/{_REPO}/environments?per_page=100":
            return _environments("shipmate-engine") if listed else _environments()
        if path.endswith(f"repos/{_REPO}/environments/shipmate-engine"):
            return env
        if path.endswith(f"repos/{_REPO}/environments/shipmate-engine/deployment-branch-policies"):
            return policies
        raise AssertionError(f"unexpected path: {path}")

    return fake


def test_missing_engine_environment_warns(monkeypatch):
    """The headline case: `shipmate-engine` absent from the environments
    **listing** -- never a per-environment-read failure standing in for
    absence, which `bm.gh_json` cannot tell apart from a 403 or a 5xx on an
    environment that does exist (see `_engine_environment_warnings`'
    docstring)."""
    monkeypatch.setattr(doctor, "_gh_json", _engine_env_responses(listed=False))
    out = doctor._engine_environment_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "shipmate-engine" in out[0][1]
    assert "does not exist" in out[0][1]
    # The probe never checks where the key actually lives -- listing repository
    # secrets needs an App permission app/manifest.json does not declare -- so
    # the repository-secret consequence must stay conditional, not asserted.
    assert "If the key is still a repository secret" in out[0][1]
    assert "is still a repository secret and" not in out[0][1]


def test_engine_environment_without_a_branch_policy_warns(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_gh_json",
        _engine_env_responses(
            env={"name": "shipmate-engine", "deployment_branch_policy": None},
            policies={"branch_policies": []},
        ),
    )
    out = doctor._engine_environment_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "branch policy" in out[0][1]


def test_engine_environment_with_a_non_custom_policy_warns(monkeypatch):
    # protected_branches-only restricts to whatever branch protection covers --
    # not necessarily just the default branch -- so it can't be confirmed as
    # the specific guarantee this probe exists to check.
    monkeypatch.setattr(
        doctor,
        "_gh_json",
        _engine_env_responses(
            env={
                "name": "shipmate-engine",
                "deployment_branch_policy": {"protected_branches": True},
            },
        ),
    )
    out = doctor._engine_environment_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "custom policy" in out[0][1]


def test_engine_environment_branch_policy_missing_default_branch_warns(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_gh_json",
        _engine_env_responses(
            env={
                "name": "shipmate-engine",
                "deployment_branch_policy": {"custom_branch_policies": True},
            },
            policies={"branch_policies": [{"name": "release"}]},
        ),
    )
    out = doctor._engine_environment_warnings(_ctx(default_branch="main"))
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "main" in out[0][1]


def test_engine_environment_branch_policy_with_an_extra_entry_warns(monkeypatch):
    # The probe must confirm the default branch is the ONLY policy entry, not
    # merely one of them -- a policy naming `main` plus a leftover branch (the
    # exact shape a one-off allow-list forgotten in place produces) still lets
    # a workflow on that leftover branch read the App private key.
    monkeypatch.setattr(
        doctor,
        "_gh_json",
        _engine_env_responses(
            env={
                "name": "shipmate-engine",
                "deployment_branch_policy": {"custom_branch_policies": True},
            },
            policies={"branch_policies": [{"name": "main"}, {"name": "probe/env-branch-policy"}]},
        ),
    )
    out = doctor._engine_environment_warnings(_ctx(default_branch="main"))
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "probe/env-branch-policy" in out[0][1]


def test_correctly_scoped_engine_environment_is_silent(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_gh_json",
        _engine_env_responses(
            env={
                "name": "shipmate-engine",
                "deployment_branch_policy": {"custom_branch_policies": True},
            },
            policies={"branch_policies": [{"name": "main"}]},
        ),
    )
    assert doctor._engine_environment_warnings(_ctx(default_branch="main")) == []


def test_engine_environment_listing_failure_propagates_to_the_degrade_note(monkeypatch):
    """The listing is this probe's precondition, exactly like
    `_env_protection_warnings`': without it no existence verdict can be
    reached, so the failure must reach `warnings()`'s outer handler and
    become the "could not verify" degrade instead of a silent empty result."""

    def boom(path):
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", boom)
    with pytest.raises(SystemExit):
        doctor._engine_environment_warnings(_ctx())
    out = doctor.warnings(_ctx())
    assert any("engine environment" in t and "probe skipped" in t for _, t in out)


def test_engine_environment_unreadable_degrades_to_a_note(monkeypatch):
    """The environment exists (per the listing), but the per-environment
    settings read itself fails -- a NOTICE naming it, not a propagated
    exception and not the "does not exist" warning, mirroring
    `_env_protection_warnings`'s unreadable-existing-environment note."""

    def gh(path):
        if path == f"repos/{_REPO}/environments?per_page=100":
            return _environments("shipmate-engine")
        if path.endswith("environments/shipmate-engine"):
            raise SystemExit(f"::error::command failed (1): gh api {path}")
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._engine_environment_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE
    assert "shipmate-engine" in out[0][1]
    assert "::error::" not in out[0][1] and "gh api" not in out[0][1]


def test_engine_environment_policies_unreadable_degrades_to_a_note(monkeypatch):
    def gh(path):
        if path == f"repos/{_REPO}/environments?per_page=100":
            return _environments("shipmate-engine")
        if path.endswith("deployment-branch-policies"):
            raise SystemExit(f"::error::command failed (1): gh api {path}")
        if path.endswith("environments/shipmate-engine"):
            return {
                "name": "shipmate-engine",
                "deployment_branch_policy": {"custom_branch_policies": True},
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._engine_environment_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE
    assert "::error::" not in out[0][1] and "gh api" not in out[0][1]


def test_engine_environment_probe_is_registered(monkeypatch):
    """An unregistered probe function runs nowhere while its own unit tests
    stay green -- assert it actually executes as part of `warnings()`."""
    assert doctor._engine_environment_warnings in doctor.PROBES
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        # shipmate-engine deliberately absent from the listing
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "dev-eu-apply"),
        **_quiet_new_probes(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_ctx())
    assert any("shipmate-engine" in t for _, t in out)


def _wf_listing(*names):
    return [{"name": n, "type": "file"} for n in names]


def _wf_file(text):
    import base64

    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


_SHA = "a" * 40
_OTHER_SHA = "b" * 40

# The consumer workflow `_quiet_new_probes()` serves: the shipped shape, one
# `pull_request_target` file exempt from the fork-trigger probe by exact name,
# carrying a fresh engine pin for the pin probe. Defined here rather
# than next to that fixture because interpolating `_SHA` happens at import time,
# while the fixture's own body is only evaluated when a test calls it.
#
# It carries the dispatch leg too -- the `workflow_dispatch` trigger, its
# `pr_number` input and a `pr-facts` step -- which is what keeps the
# dispatch-wiring probe quiet.
#
# `head-repo` appears TWICE, as it does in a correctly wired wrapper: once on
# the `build-matrix` step, once on the summary call. Without the first
# occurrence a wiring probe that searches the whole file instead of the summary
# call's own region passes this fixture while missing the finding it exists for.
# `head-sha` is the step's second expectation and appears only there.
_QUIET_PLAN = (
    "name: shipmate · plan\n"
    "on:\n"
    "  pull_request_target:\n"
    "  workflow_dispatch:\n"
    "    inputs:\n"
    "      pr_number: { description: PR to plan, required: true }\n"
    "jobs:\n"
    "  facts:\n"
    "    steps:\n"
    f"      - uses: {_ENGINE_REPO}/actions/pr-facts@{_SHA}\n"
    "  detect:\n"
    "    steps:\n"
    f"      - uses: {_ENGINE_REPO}/actions/build-matrix@{_SHA}\n"
    "        with:\n"
    "          head-repo: ${{ needs.facts.outputs.head-repo }}\n"
    "          head-sha: ${{ needs.facts.outputs.head-sha }}\n"
    "  summary:\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/summary.yml@{_SHA}\n"
    "    with:\n"
    "      head-repo: ${{ needs.facts.outputs.head-repo }}\n"
    "      is-draft: ${{ needs.facts.outputs.is-draft }}\n"
    "      on-demand: ${{ needs.facts.outputs.on-demand }}\n"
)


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
    """A directory (or symlink/submodule) whose name ends in `.yml` is not a
    workflow file -- its "contents" read returns a list, not a blob. This probe
    SKIPS it and keeps scanning: it stops at the first unreadable FILE, so
    treating a non-file entry as unreadable would abort the whole directory and
    silently drop every pin finding after it. The entry sorts FIRST here on
    purpose, which is the arrangement that hid the tag pin below."""
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
    assert "tag or branch" in out[0][1] and "acme/engine@v2" in out[0][1]


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
        '{"id": 4, "name": "apply / app / dev-eu", "started_at": "2026-07-26T10:00:00Z", '
        + '"app_slug": "shipmate", "app_id": 999}',
        # third-party apps are dropped (out of the harvest's scope)
        '{"id": 5, "name": "codecov/project", "started_at": "2026-07-26T10:00:00Z", '
        + '"app_slug": "codecov", "app_id": 254}',
    ]
    assert doctor.latest_check_ids(lines, app_id="999") == [
        (2, "app / dev-eu"),
        (4, "apply / app / dev-eu"),
        (3, "dns / dev-eu"),
    ]


def test_mirrored_app_check_does_not_displace_the_annotation_bearing_one():
    """An on-demand plan's App-authored mirror of `<stack> / <env>` carries no
    annotations, so it must not win the newest-per-name ranking over the
    autoplan's `github-actions` check -- while the App's own `apply / ` rows
    stay harvested."""
    autoplan = (
        '{"id": 1, "name": "app / dev-eu", "started_at": "2026-07-26T10:00:00Z", '
        '"app_slug": "github-actions", "app_id": 15368}'
    )
    # Later than the autoplan's: the mirror is created after it, and this keeps
    # the guard independent of whether GitHub populates started_at on a create.
    mirror = (
        '{"id": 2, "name": "app / dev-eu", "started_at": "2026-07-26T11:00:00Z", '
        '"app_slug": "shipmate", "app_id": 999}'
    )
    apply_check = (
        '{"id": 3, "name": "apply / app / dev-eu", "started_at": "2026-07-26T11:30:00Z", '
        '"app_slug": "shipmate", "app_id": 999}'
    )
    assert doctor.latest_check_ids([autoplan, mirror, apply_check], app_id="999") == [
        (1, "app / dev-eu"),
        (3, "apply / app / dev-eu"),
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
    """All three environment probes — environment existence, protection shape and
    plan-environment secrets — see only the environments of the stacks this
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
        + '"status": "in_progress"}',
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


def test_skipped_environment_probes_are_stated_exactly_once(monkeypatch):
    """The "skipped" wording comes from one place -- `_environment_warnings`,
    keyed on `envs_available` -- so the preamble and the finding can neither
    repeat it nor disagree about it."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu-plan", "dev-eu-apply"))
    findings = doctor._environment_warnings(_ctx(envs=set(), envs_available=False))
    body = doctor.render_report(findings, [], _ctx(envs_available=False, plan_run_ids=[]))
    assert "environment probes were skipped" in body
    assert body.count("environment probes were skipped") == 1


def test_provenance_and_probe_coverage_can_disagree_without_contradicting(monkeypatch):
    """The id set is written from the plan records on the head's apply checks
    whether or not those runs' cell summaries could be downloaded, so a
    non-empty set with no declared environments is a live state. The preamble
    must then still name the runs that were read, and the coverage claim must
    still come from `envs_available` alone -- a preamble that inferred coverage
    from the id set would contradict the very next line."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu-plan", "dev-eu-apply"))
    findings = doctor._environment_warnings(_ctx(envs=set(), envs_available=False))
    body = doctor.render_report(findings, [], _ctx(envs_available=False, plan_run_ids=["1281"]))
    assert "cell summaries from plan run 1281" in body
    assert "environment probes were skipped" in body


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


def test_provenance_names_every_run_the_head_recorded():
    """One head's cells can be planned across several runs -- a cell replanned
    after a push is recorded by its own newest apply check -- so the preamble
    names the whole set. Naming only the first would attribute the report to a
    plan run half of it did not come from."""
    text = doctor._provenance(_ctx(plan_run_ids=["1281", "1290"]))
    assert text == f"_Commit `{_HEAD[:7]}`; cell summaries from plan runs 1281, 1290._"


def test_provenance_states_the_run_without_a_coverage_claim():
    """The run branch must name what was read and claim nothing about what the
    probes did with it: that claim belongs only to `_environment_warnings`'
    NOTICE, keyed on `envs_available`. Pinned on the current stem, so restoring
    wording that implies the declared environment set came from these runs, or
    that mentions the probes at all, fails here."""
    text = doctor._provenance(_ctx(plan_run_ids=["1281"]))
    assert text == f"_Commit `{_HEAD[:7]}`; cell summaries from plan run 1281._"


def test_provenance_says_so_when_the_head_recorded_no_plan_run():
    """No apply check on this head carries a plan record -- nothing was planned
    yet, or every record is from an older engine version. Naming the absence is
    the whole degrade path: doctor still reports its settings probes."""
    text = doctor._provenance(_ctx(plan_run_ids=[]))
    assert text == f"_Commit `{_HEAD[:7]}`; no plan records on this commit's apply checks._"


def test_provenance_one_lines_an_overlong_plan_run_id():
    # The ids are interpolated verbatim otherwise -- an unbounded value there
    # would make the preamble itself unbounded, defeating the whole report's
    # size budget regardless of the harvest/findings truncation.
    text = doctor._provenance(_ctx(plan_run_ids=["9" * 200]))
    assert ("9" * 119 + "…") in text
    assert ("9" * 120) not in text


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
        + '"app_slug": "github-actions"}',
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
    src = (ACTIONS / "comment-ops" / "action.yml").read_text(encoding="utf-8")
    assert src.count(doctor.DOCTOR_MARKER) >= 1, "upsert step no longer greps the script's marker"
    assert "scripts/doctor" in src, "comment-ops action no longer calls scripts/doctor"


def _fork_responses(files):
    """Listing + contents for the fork-trigger probe: {filename: text}."""
    responses = {f"{_WF_DIR}{_REF}": _wf_listing(*files)}
    for name, text in files.items():
        responses[f"{_WF_DIR}/{name}{_REF}"] = _wf_file(text)
    return responses


def test_pull_request_target_trigger_warned(monkeypatch):
    responses = _fork_responses({"label.yml": "on:\n  pull_request_target:\n    types: [opened]\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "label.yml" in out[0][1]
    assert "pull_request_target" in out[0][1]


def test_pull_request_target_in_a_flow_sequence_warned(monkeypatch):
    # `on: [push, pull_request_target]` is the same trigger written inline; a
    # probe that only recognised the block form would miss it entirely.
    responses = _fork_responses({"label.yml": "on: [push, pull_request_target]\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert "label.yml" in out[0][1]


def test_pull_request_target_as_a_single_event_scalar_warned(monkeypatch):
    # `on: pull_request_target` is the legal one-event scalar form -- no block,
    # no sequence, no brackets. The shortest way to declare the trigger must not
    # be the one shape the probe misses.
    responses = _fork_responses({"label.yml": "on: pull_request_target\njobs: {}\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "label.yml" in out[0][1]


def test_pull_request_target_as_a_flow_mapping_key_warned(monkeypatch):
    responses = _fork_responses({"label.yml": "on: {pull_request_target: {types: [opened]}}\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    # Level and filename too: the unreadable-directory degrade also returns
    # exactly one item, so a length-only assertion passes on a probe that
    # recognised nothing at all.
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "label.yml" in out[0][1]


def test_pull_request_target_as_a_sequence_item_warned(monkeypatch):
    responses = _fork_responses({"label.yml": "on:\n  - push\n  - pull_request_target\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "label.yml" in out[0][1]


def test_pull_request_target_in_a_wrapped_flow_sequence_warned(monkeypatch):
    # A flow sequence is one value however it is wrapped across lines.
    responses = _fork_responses({"label.yml": "on: [push,\n     pull_request_target]\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "label.yml" in out[0][1]


def test_pull_request_target_in_a_flow_sequence_with_brackets_on_own_lines_warned(monkeypatch):
    responses = _fork_responses(
        {"label.yml": "on: [\n  push,\n  pull_request_target,\n]\njobs: {}\n"}
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "label.yml" in out[0][1]


def test_folded_event_name_comparison_is_silent(monkeypatch):
    # The same comparison as the single-line case below, folded across lines by
    # a block scalar -- outside the `on:` block either way.
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request:\njobs:\n  a:\n"
            "    if: >-\n      github.event_name ==\n      'pull_request_target'\n"
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_the_word_in_a_run_body_is_silent(monkeypatch):
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request:\njobs:\n  a:\n    steps:\n"
            "      - run: |\n          echo this repo has no pull_request_target trigger\n"
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_a_workflow_dispatch_choice_option_is_silent(monkeypatch):
    # Inside the `on:` block, but nested below the event-name level: it is one
    # input's allowed value, not a trigger.
    responses = _fork_responses(
        {
            "ops.yml": "on:\n  workflow_dispatch:\n    inputs:\n      event:\n"
            "        type: choice\n        options:\n          - pull_request_target\n"
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_a_longer_trigger_name_is_not_the_token(monkeypatch):
    responses = _fork_responses({"label.yml": "on: pull_request_target_foo\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_plain_pull_request_trigger_is_silent(monkeypatch):
    # The prefix must not match: `pull_request:` is the ordinary plan trigger
    # and every consumer has one. A probe that fired on it would fire always.
    responses = _fork_responses({"plan.yml": "on:\n  pull_request:\n    branches: [main]\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_commented_out_pull_request_target_is_silent(monkeypatch):
    # The lines a careful repository writes *because* it has no such trigger.
    # Reporting them would train readers to ignore the finding. Both shapes the
    # pattern would otherwise match are here: a trailing comment carrying the
    # flow-sequence form, and a commented-out key at the head of a line.
    responses = _fork_responses(
        {
            "plan.yml": "on: [pull_request]  # never [pull_request_target]\n",
            "drift.yml": "on:\n  # pull_request_target:  <- deliberately absent\n  schedule:\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_quoted_event_name_comparison_is_silent(monkeypatch):
    # `github.event_name == 'pull_request_target'` compares against the
    # trigger; it does not declare one.
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request:\njobs:\n  a:\n"
            "    if: github.event_name == 'pull_request_target'\n"
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_the_shipmate_plan_workflow_is_not_warned_about(monkeypatch):
    # plan.yml declaring pull_request_target IS the shape the engine ships: the
    # job that holds the App key is the engine's reusable summary workflow,
    # which checks out nothing. Warning about it would train readers to ignore
    # the finding on the labeler workflow that actually is dangerous.
    responses = _fork_responses({"plan.yml": "on:\n  pull_request_target:\n    types: [opened]\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_another_workflow_is_still_warned_about_alongside_plan_yml(monkeypatch):
    # The exemption is by exact filename and nothing else.
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\n    types: [opened]\n",
            "labeler.yml": "on:\n  pull_request_target:\n    types: [opened]\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert "labeler.yml" in out[0][1]


def test_every_offending_workflow_is_named(monkeypatch):
    responses = _fork_responses(
        {
            "label.yml": "on:\n  pull_request_target:\n",
            "plan.yml": "on:\n  pull_request:\n",
            "triage.yml": "on: [pull_request_target]\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert sorted(t.split("`")[1] for _, t in out) == ["label.yml", "triage.yml"]


def _wf_bytes(data):
    import base64

    return {"encoding": "base64", "content": base64.b64encode(data).decode()}


def test_an_indented_on_is_not_the_top_level_one(monkeypatch):
    """`_on_block`'s column-0 anchoring. Any key ending in `on` is an unanchored
    match -- `python-version: 3.12` is `versi` + `on:` -- and matching it
    retargets the block this probe reads, onto a line that can never name an
    event, so the trigger one line below goes unreported."""
    responses = _fork_responses(
        {"label.yml": "env:\n  python-version: 3.12\non:\n  pull_request_target:\n"}
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "label.yml" in out[0][1]


def test_a_blank_line_or_a_column_zero_comment_does_not_end_the_on_block(monkeypatch):
    """`_on_block` promises neither ends the block, and workflows written by
    hand put a column-0 comment block right above their events -- the same
    author style one line lower would otherwise read as an empty trigger."""
    responses = _fork_responses(
        {"label.yml": "on:\n\n# runs at the base ref\n  pull_request_target:\n"}
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING


def test_a_byte_order_mark_does_not_hide_the_on_block(monkeypatch):
    """A BOM'd workflow carries U+FEFF in front of its column-0 `on:`, which
    reads as no top-level trigger block at all -- silence over a real
    `pull_request_target`."""
    responses = _fork_responses({"label.yml": ""})
    responses[f"{_WF_DIR}/label.yml{_REF}"] = _wf_bytes(
        b"\xef\xbb\xbfon:\n  pull_request_target:\n"
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING


def test_an_invalid_utf8_byte_does_not_abort_the_scan(monkeypatch):
    """`errors="replace"`, both halves. A workflow written through a lossy
    encoding is not valid UTF-8: a strict decode raises out of the probe rather
    than reporting the trigger sitting in the same file, and an `ignore` decode
    DROPS the bad byte, splicing `pull_request_targ<0xe9>et` back into the very
    token this probe matches on -- a warning manufactured for a repository that
    declares no such trigger. U+FFFD keeps the token broken."""
    responses = _fork_responses({"label.yml": ""})
    responses[f"{_WF_DIR}/label.yml{_REF}"] = _wf_bytes(b"# caf\xe9\non:\n  pull_request_target:\n")
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING

    responses[f"{_WF_DIR}/label.yml{_REF}"] = _wf_bytes(b"on: [pull_request_targ\xe9et]\n")
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_strip_comment_ends_a_line_at_a_comment():
    assert (
        doctor._strip_comment('workflows: ["x"]  # was pull_request_target') == 'workflows: ["x"]  '
    )
    assert doctor._strip_comment("# whole line") == ""


def test_strip_comment_keeps_a_hash_inside_a_token():
    """The docstring promises a `#` inside a token is not a comment marker.
    Nothing else in the suite exercises that half, and a matcher that stripped
    at every `#` would still pass every other test here."""
    assert doctor._strip_comment("branches: [release#1]") == "branches: [release#1]"


def test_fork_trigger_without_a_commit_is_a_note_not_a_read(monkeypatch):
    # Same reasoning as the pin probe: reading the default branch instead would
    # report the trigger on the very pull request that removes it.
    def gh(path):
        pytest.fail(f"the fork-trigger probe read the API with no commit: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._fork_trigger_warnings(_ctx(head_sha=""))
    assert out == [doctor.FORK_TRIGGER_NO_COMMIT]
    assert out[0][0] == doctor.NOTICE


def test_fork_trigger_unreadable_directory_degrades_to_a_note(monkeypatch):
    def gh(path):
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._fork_trigger_warnings(_ctx())
    assert out == [doctor.FORK_TRIGGER_UNREADABLE]
    assert out[0][0] == doctor.NOTICE
    assert "::error::" not in out[0][1] and "gh api" not in out[0][1]


def test_fork_trigger_unreadable_file_degrades_to_a_note(monkeypatch):
    listing = {f"{_WF_DIR}{_REF}": _wf_listing("label.yml")}

    def gh(path):
        if path in listing:
            return listing[path]
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._fork_trigger_warnings(_ctx()) == [doctor.FORK_TRIGGER_UNREADABLE]


def test_fork_trigger_probe_is_registered(monkeypatch):
    """An unregistered probe runs nowhere while its own unit tests stay green --
    assert it actually executes as part of `warnings()`."""
    assert doctor._fork_trigger_warnings in doctor.PROBES
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "dev-eu-apply"),
        **_quiet_new_probes(),
        f"{_WF_DIR}{_REF}": _wf_listing("label.yml"),
        f"{_WF_DIR}/label.yml{_REF}": _wf_file("on:\n  pull_request_target:\n"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_ctx())
    assert any("pull_request_target" in t for _, t in out)


def test_unsafe_pr_checkout_in_plan_yml_is_warned(monkeypatch):
    """`plan.yml` is exempt from the trigger finding, but it is exactly the file
    where a fork checkout would be turned on -- so this check must run BEFORE
    that exemption. Below it, the one workflow that matters reports nothing."""
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\njobs:\n  detect:\n"
            "    steps:\n      - uses: actions/checkout@v7\n"
            "        with:\n          allow-unsafe-pr-checkout: true\n"
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "allow-unsafe-pr-checkout" in out[0][1]
    assert "plan.yml" in out[0][1]


def test_unsafe_pr_checkout_in_another_workflow_is_warned(monkeypatch):
    # No `pull_request_target` here, so the trigger finding cannot account for
    # the warning: it is this check or nothing. The detection is deliberately
    # trigger-blind, so the message has to be true of a `pull_request`-only
    # file too -- under that trigger a fork's run is handed no secrets, and a
    # message asserting a secret-holding job is simply wrong about it.
    responses = _fork_responses(
        {
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - uses: actions/checkout@v7\n"
            '        with:\n          allow-unsafe-pr-checkout: "true"\n'
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "allow-unsafe-pr-checkout" in out[0][1]
    assert "`pull_request`" in out[0][1]


def test_unsafe_pr_checkout_as_an_expression_is_warned(monkeypatch):
    # Unknown, not false: it may evaluate true, and doctor cannot evaluate it.
    responses = _fork_responses(
        {
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: ${{ vars.UNSAFE }}\n"
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert "allow-unsafe-pr-checkout" in out[0][1]


def test_unsafe_pr_checkout_set_to_false_is_silent(monkeypatch):
    # The input written out and explicitly disabled is the safe default made
    # visible. Reporting the key regardless of its value would fire on it.
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: false\n",
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: 'false'\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_a_capitalised_false_is_silent(monkeypatch):
    """`False` and `FALSE` are the same legal YAML boolean as `false`, so both are
    the safe configuration -- and a case-sensitive comparison reports them. A
    probe that fires on a correct repository trains readers to ignore the suite.
    `build-matrix`'s fork refusal normalizes with `.strip().lower()` for exactly
    this reason; one release must not hold two guards that disagree about it."""
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: False\n",
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: FALSE\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_flow_style_unsafe_pr_checkout_is_warned(monkeypatch):
    """A line-anchored key misses this, and it is not exotic authoring: the
    engine's own `docs/drift.md` fence and all four sample repositories write
    `with: { fetch-depth: 0 }`, so a flow-style checkout step is what a consumer
    copying those pages produces. Missing it is fail-open on the outermost guard
    of the whole plan path."""
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - uses: actions/checkout@v7\n"
            "        with: { fetch-depth: 0, allow-unsafe-pr-checkout: true }\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "allow-unsafe-pr-checkout" in out[0][1]


def test_a_flow_style_false_is_silent(monkeypatch):
    """The other half of recognising flow style: the value must stop at `,`/`}`,
    or a flow-style `false` reads as `false }` and the safest shape a consumer
    can write is reported."""
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - uses: actions/checkout@v7\n"
            "        with: { allow-unsafe-pr-checkout: false, fetch-depth: 0 }\n",
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - with: { fetch-depth: 0, allow-unsafe-pr-checkout: false }\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_commented_out_unsafe_pr_checkout_is_silent(monkeypatch):
    """The line a careful repository writes *because* it does not have one.
    Reporting it would train readers to ignore the finding.

    Two independent things keep it quiet -- the comment strip empties the line,
    and the pattern's key anchor rejects a key with `set ` in front of it -- so
    only removing BOTH turns this red. Each is pinned on its own by
    `test_a_trailing_comment_after_a_false_value_is_silent` (the strip) and
    `test_a_key_merely_ending_in_the_input_name_is_not_reported` (the anchor)."""
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      # never set allow-unsafe-pr-checkout: true\n"
            "      - uses: actions/checkout@v7\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_a_trailing_comment_after_a_false_value_is_silent(monkeypatch):
    """The comment strip is what makes the value comparison read `false` rather
    than `false  # deliberate`, which is not the literal and would be reported
    -- a false positive on the safest shape a consumer can write."""
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - with:\n"
            "          allow-unsafe-pr-checkout: false  # deliberate, never true\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


def test_a_false_in_one_job_does_not_silence_a_true_in_another(monkeypatch):
    """A plan wrapper checks out in more than one job, and this probe's own
    finding asks for an explicit `false` -- so the two occurrences coexist in
    one file routinely. Examining only the first read the `false` and returned
    nothing, which is fail-open on the outermost guard of the whole plan path.

    One FILE with both occurrences: `test_unsafe_pr_checkout_set_to_false_is_
    silent` uses two files with one occurrence each, which is why it could not
    see this."""
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\njobs:\n"
            "  detect:\n    steps:\n      - with:\n"
            "          allow-unsafe-pr-checkout: false\n"
            "  plan:\n    steps:\n      - with:\n"
            "          allow-unsafe-pr-checkout: true\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "allow-unsafe-pr-checkout" in out[0][1]


def test_a_key_merely_ending_in_the_input_name_is_not_reported(monkeypatch):
    """The key anchor -- a line start, `{` or `,` -- is what makes this the input
    and not a longer key that happens to end in the same characters: a different
    input entirely, and reporting it names a line the reader cannot find."""
    responses = _fork_responses(
        {
            "plan.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          no-allow-unsafe-pr-checkout: true\n",
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


# Hand-written, not derived from `doctor` or from `_QUIET_PLAN`: these are the
# expressions a correctly wired wrapper passes, and the finding must name them.
_HEAD_REPO_EXPR = "${{ needs.facts.outputs.head-repo }}"
_IS_DRAFT_EXPR = "${{ needs.facts.outputs.is-draft }}"
_HEAD_SHA_EXPR = "${{ needs.facts.outputs.head-sha }}"
_ON_DEMAND_EXPR = "${{ needs.facts.outputs.on-demand }}"


def _plan_calling_summary(
    *with_lines,
    detect_head_repo=False,
    with_first=False,
    matrix_with=None,
    on_demand=_ON_DEMAND_EXPR,
):
    """A consumer `plan.yml` whose summary job passes `with_lines`, plus a
    correctly wired `on-demand: on_demand` unless `on_demand` is None.

    `on-demand` is wired by default so a fixture that omits one of the other
    inputs produces that input's finding alone; `on_demand=None` (absent) and a
    constant value are what the on-demand finding's own tests pass.

    `detect_head_repo` adds the OTHER legitimate `head-repo`, on a correctly
    wired `build-matrix` step of an EARLIER job -- the occurrence a whole-file
    search is satisfied by. That step's own `head-sha` comes with it, so any
    finding these fixtures produce is the summary call's rather than the step's.
    `matrix_with` writes that step's `with:` lines verbatim
    instead, for the checks on the step's own wiring (an empty sequence writes
    the step with no `with:` block at all). `with_first` writes the summary
    job's `with:` block ABOVE its `uses:` line, which is legal YAML a
    forward-only scan cannot see."""
    matrix = (
        [f"head-repo: {_HEAD_REPO_EXPR}", f"head-sha: {_HEAD_SHA_EXPR}"]
        if detect_head_repo
        else matrix_with
    )
    detect = (
        "  detect:\n"
        "    steps:\n"
        f"      - uses: {_ENGINE_REPO}/actions/build-matrix@{_SHA}\n"
        + ("        with:\n" if matrix else "")
        + "".join(f"          {ln}\n" for ln in matrix)
        if matrix is not None
        else ""
    )
    uses = f"    uses: {_ENGINE_REPO}/.github/workflows/summary.yml@{_SHA}\n"
    lines = list(with_lines) + ([] if on_demand is None else [f"on-demand: {on_demand}"])
    block = "    with:\n" + "".join(f"      {ln}\n" for ln in lines)
    return f"on:\n  pull_request_target:\njobs:\n{detect}  summary:\n" + (
        block + uses if with_first else uses + block
    )


def test_summary_call_missing_head_repo_is_warned(monkeypatch):
    responses = _fork_responses({"plan.yml": _plan_calling_summary(f"is-draft: {_IS_DRAFT_EXPR}")})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert f"`head-repo: {_HEAD_REPO_EXPR}`" in out[0][1]
    assert "is-draft" not in out[0][1]


def test_summary_call_missing_is_draft_is_warned(monkeypatch):
    """The probe must check BOTH inputs. Checking only `head-repo` leaves an
    omitted `is-draft` -- equally a skipped job, equally silent -- unreported."""
    responses = _fork_responses(
        {"plan.yml": _plan_calling_summary(f"head-repo: {_HEAD_REPO_EXPR}")}
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert f"`is-draft: {_IS_DRAFT_EXPR}`" in out[0][1]
    assert "head-repo" not in out[0][1]


def test_a_correctly_wired_summary_call_is_silent(monkeypatch):
    # `_QUIET_PLAN` itself: the shipped shape, all three inputs, and the second
    # `head-repo` on the build-matrix step.
    responses = _fork_responses({"plan.yml": _QUIET_PLAN})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def test_a_head_repo_on_the_build_matrix_step_does_not_satisfy_the_summary_call(monkeypatch):
    """A correctly wired `plan.yml` carries `head-repo` twice. A probe that
    searches the whole file is satisfied by the `build-matrix` occurrence while
    the call that decides the gate carries nothing -- silence at exactly the
    finding this probe exists for."""
    responses = _fork_responses(
        {"plan.yml": _plan_calling_summary(f"is-draft: {_IS_DRAFT_EXPR}", detect_head_repo=True)}
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert f"`head-repo: {_HEAD_REPO_EXPR}`" in out[0][1]


def test_a_with_block_above_the_uses_line_is_silent(monkeypatch):
    """Key order in a YAML mapping carries no meaning, so a wrapper writing
    `with:` above `uses:` is correctly wired. Scanning forward from the `uses:`
    line reported it as passing neither input -- a probe firing on a healthy
    repository, which is what teaches readers to ignore the suite."""
    responses = _fork_responses(
        {
            "plan.yml": _plan_calling_summary(
                f"head-repo: {_HEAD_REPO_EXPR}",
                f"is-draft: {_IS_DRAFT_EXPR}",
                with_first=True,
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def test_the_build_matrix_head_repo_cannot_satisfy_the_call_in_either_ordering(monkeypatch):
    """The region is the summary job's own block however that job is written,
    so the `build-matrix` step's `head-repo` in an earlier job is outside it in
    both key orderings. Widening the region to the whole file greens both."""
    for with_first in (False, True):
        responses = _fork_responses(
            {
                "plan.yml": _plan_calling_summary(
                    f"is-draft: {_IS_DRAFT_EXPR}",
                    detect_head_repo=True,
                    with_first=with_first,
                )
            }
        )
        monkeypatch.setattr(doctor, "_gh_json", responses.__getitem__)
        out = doctor._summary_wiring_warnings(_ctx())
        assert len(out) == 1, with_first
        assert f"`head-repo: {_HEAD_REPO_EXPR}`" in out[0][1], with_first


def test_a_constant_head_repo_is_reported(monkeypatch):
    """The one remaining fail-open: a `head-repo` wired to the running
    repository equals it for EVERY pull request, fork ones included, so the
    engine's guard passes and nothing else in the system can see it. A
    presence-only check leaves it undetectable."""
    responses = _fork_responses(
        {
            "plan.yml": _plan_calling_summary(
                "head-repo: ${{ github.repository }}", f"is-draft: {_IS_DRAFT_EXPR}"
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert f"`head-repo: {_HEAD_REPO_EXPR}`" in out[0][1]


def test_the_wiring_finding_covers_the_wrong_value_case(monkeypatch):
    """A present-but-wrong `head-repo` IS passed, so a finding opening "does not
    pass `head-repo: …`" is literally false about the case the value check was
    added for -- and the opening clause is the one a reader skims. Compared
    against a hand-written constant, whole clause."""
    responses = _fork_responses(
        {
            "plan.yml": _plan_calling_summary(
                "head-repo: ${{ github.repository }}", f"is-draft: {_IS_DRAFT_EXPR}"
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert out[0][1].startswith(
        "`plan.yml`'s call of the engine's `summary.yml` must pass "
        "`head-repo: ${{ needs.facts.outputs.head-repo }}`, and does not: "
        "the input is absent, or carries a different value."
    )


def test_a_literal_is_draft_is_reported(monkeypatch):
    # `is-draft: false` states "never a draft" for every run, drafts included.
    responses = _fork_responses(
        {"plan.yml": _plan_calling_summary(f"head-repo: {_HEAD_REPO_EXPR}", "is-draft: false")}
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert f"`is-draft: {_IS_DRAFT_EXPR}`" in out[0][1]


def test_summary_call_missing_on_demand_is_warned(monkeypatch):
    """Its own finding, with its own consequence. Folding it into the skip
    finding would tell a reader whose pull requests all gate correctly that the
    job is skipped -- and the cost it does pay (a `shipmate plan` on a draft
    that plans everything and then gates nothing) is named nowhere."""
    responses = _fork_responses(
        {
            "plan.yml": _plan_calling_summary(
                f"head-repo: {_HEAD_REPO_EXPR}",
                f"is-draft: {_IS_DRAFT_EXPR}",
                on_demand=None,
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert f"`on-demand: {_ON_DEMAND_EXPR}`" in out[0][1]
    # The distinct sentence, not the skip finding's: this input costs an
    # ordinary pull request nothing, so borrowing "SKIPPED" here is a false
    # report about the repository the reader is looking at.
    assert "SKIPPED" not in out[0][1]
    assert "draft skip" in out[0][1]


def test_a_constant_on_demand_is_reported(monkeypatch):
    """`on-demand: true` claims a person named every run, so every draft gets a
    gate -- noise on every draft, and the fork clause is the only guard left. A
    presence-only check cannot see it."""
    responses = _fork_responses(
        {
            "plan.yml": _plan_calling_summary(
                f"head-repo: {_HEAD_REPO_EXPR}",
                f"is-draft: {_IS_DRAFT_EXPR}",
                on_demand="true",
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert f"`on-demand: {_ON_DEMAND_EXPR}`" in out[0][1]


def test_a_wrapper_missing_two_facts_gets_both_findings(monkeypatch):
    """The skip finding names its own missing inputs and the on-demand finding
    names its own. One finding covering all three would have to state one
    consequence for two different ones."""
    responses = _fork_responses(
        {"plan.yml": _plan_calling_summary(f"head-repo: {_HEAD_REPO_EXPR}", on_demand=None)}
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 2
    assert f"`is-draft: {_IS_DRAFT_EXPR}`" in out[0][1]
    assert "on-demand" not in out[0][1]
    assert f"`on-demand: {_ON_DEMAND_EXPR}`" in out[1][1]


def test_the_same_expression_written_without_inner_spaces_is_silent(monkeypatch):
    # The same expression, and formatters quote values -- the reason the pin
    # probe's anchor tolerates quotes too.
    responses = _fork_responses(
        {
            "plan.yml": _plan_calling_summary(
                'head-repo: "${{needs.facts.outputs.head-repo}}"',
                "is-draft: ${{needs.facts.outputs.is-draft}}",
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def _both_wired(matrix_with):
    """A `plan.yml` with a correctly wired summary call and `matrix_with` on its
    `build-matrix` step, so any finding is the step's."""
    return _plan_calling_summary(
        f"head-repo: {_HEAD_REPO_EXPR}",
        f"is-draft: {_IS_DRAFT_EXPR}",
        matrix_with=matrix_with,
    )


def test_a_constant_head_repo_on_the_build_matrix_step_is_reported(monkeypatch):
    """The consequence asymmetry: a constant on the summary call costs a gate
    job, while the same constant here equals the running repository for EVERY
    pull request, so `build-matrix`'s fork refusal -- the layer docs/hardening.md
    says has to hold -- passes a fork's own Terramate/OpenTofu onto the
    consumer's runners. "A missing input is loud" covers the absent case only."""
    responses = _fork_responses(
        {
            "plan.yml": _both_wired(
                ["head-repo: ${{ github.repository }}", f"head-sha: {_HEAD_SHA_EXPR}"]
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert out[0][1].startswith(
        "`plan.yml`'s `build-matrix` step must pass "
        "`head-repo: ${{ needs.facts.outputs.head-repo }}`, and does not: "
        "the input is absent, or carries a different value."
    )


def test_a_constant_head_sha_on_the_build_matrix_step_is_reported(monkeypatch):
    """The step's second expectation, and a different hole from the one above: a
    `head-sha` wired to `github.sha` is the base branch under
    `pull_request_target`, so the checkout check compares the wrong tree against
    itself, the matrix comes out empty and the gate greens with nothing queued to
    apply. Reported as its own finding, because the remedy is not the fork one."""
    responses = _fork_responses(
        {"plan.yml": _both_wired([f"head-repo: {_HEAD_REPO_EXPR}", "head-sha: ${{ github.sha }}"])}
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert out[0][1].startswith(
        "`plan.yml`'s `build-matrix` step must pass "
        "`head-sha: ${{ needs.facts.outputs.head-sha }}`, and does not: "
        "the input is absent, or carries a different value."
    )


def test_both_absent_inputs_on_the_build_matrix_step_are_reported_separately(monkeypatch):
    """A step with no `with:` block at all is missing both, and the two holes are
    not alike -- a fork planned on the consumer's runners, versus a plan of the
    wrong tree that greens the gate. One finding each, so the reader is told
    which remedy is theirs; a single "the step is miswired" finding names one."""
    responses = _fork_responses({"plan.yml": _both_wired([])})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 2
    assert all("`build-matrix` step" in text for _, text in out)
    assert f"`head-repo: {_HEAD_REPO_EXPR}`" in out[0][1]
    assert f"`head-sha: {_HEAD_SHA_EXPR}`" in out[1][1]
    assert "head-sha" not in out[0][1] and "head-repo" not in out[1][1]


def test_a_correctly_wired_build_matrix_step_is_silent(monkeypatch):
    responses = _fork_responses(
        {"plan.yml": _both_wired([f"head-repo: {_HEAD_REPO_EXPR}", f"head-sha: {_HEAD_SHA_EXPR}"])}
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def test_no_pull_request_in_the_plan_wrapper_is_reported(monkeypatch):
    """`no-pull-request: "true"` is the single opt-out from the fork refusal.
    docs/hardening.md calls it the one consumer edit that turns that refusal off
    for every pull request, and nothing in the system reported it."""
    responses = _fork_responses(
        {
            "plan.yml": _both_wired(
                [
                    f"head-repo: {_HEAD_REPO_EXPR}",
                    f"head-sha: {_HEAD_SHA_EXPR}",
                    'no-pull-request: "true"',
                ]
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._summary_wiring_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert out[0][1].startswith(
        "`plan.yml` sets `no-pull-request` to something other than `false` —"
    )


def test_no_pull_request_set_to_false_in_the_plan_wrapper_is_silent(monkeypatch):
    # `false` is what the action reads as no opt-out, so it is the shape this
    # finding asks for -- reporting it fires on a correct repository.
    responses = _fork_responses(
        {
            "plan.yml": _both_wired(
                [
                    f"head-repo: {_HEAD_REPO_EXPR}",
                    f"head-sha: {_HEAD_SHA_EXPR}",
                    "no-pull-request: false",
                ]
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def test_a_key_merely_ending_in_no_pull_request_is_not_reported(monkeypatch):
    """The twin of `test_a_key_merely_ending_in_the_input_name_is_not_reported`:
    the key anchor -- a line start, `{` or `,` -- is what makes this the input
    and not a longer key ending in the same characters, which is a different
    input entirely and names a line the reader cannot find."""
    responses = _fork_responses(
        {
            "plan.yml": _both_wired(
                [
                    f"head-repo: {_HEAD_REPO_EXPR}",
                    f"head-sha: {_HEAD_SHA_EXPR}",
                    'shipmate-no-pull-request: "true"',
                ]
            )
        }
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def test_drift_yml_may_carry_no_pull_request(monkeypatch):
    """The asymmetry this whole check has to preserve: docs/drift.md REQUIRES
    `no-pull-request: "true"` on the nightly's `build-matrix` step, and states no
    head repository at all. Selecting files by anything looser than the exact
    name `plan.yml` reports the documented nightly."""
    drift = (
        "on:\n  schedule:\n    - cron: 0 3 * * *\njobs:\n  detect:\n    steps:\n"
        f"      - uses: {_ENGINE_REPO}/actions/build-matrix@{_SHA}\n"
        '        with:\n          base-sha: ""\n          all-stacks: "true"\n'
        '          no-pull-request: "true"\n'
    )
    responses = _fork_responses({"drift.yml": drift})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def test_the_documented_plan_wrapper_produces_no_finding(monkeypatch):
    """The oracle for false positives: the wrapper consumers paste, verbatim
    from the page, through the whole probe. A probe that fires on the documented
    shape trains readers to ignore the advisory suite, which costs more than the
    gap it closes. The fence count is asserted first, so a page edit that moves
    the wrapper out of this selector's reach fails here instead of passing
    vacuously."""
    page = (ENGINE / "docs" / "getting-started.md").read_text(encoding="utf-8")
    fences = [
        textwrap.dedent(m.group("body"))
        for m in _YAML_FENCE.finditer(page)
        if "/.github/workflows/summary.yml@" in m.group("body")
    ]
    assert len(fences) == 1, f"documented summary-call fences: {len(fences)}"
    responses = _fork_responses({"plan.yml": fences[0]})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def test_summary_wiring_skips_workflows_other_than_plan_yml(monkeypatch):
    """`drift.yml` never calls the summary workflow, and a file that does under
    another name is not the shape this reports on. Without the name filter this
    fixture -- a summary call carrying neither input -- is reported."""
    responses = _fork_responses({"drift.yml": _plan_calling_summary("pr-number: 1")})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def test_a_plan_yml_with_no_summary_call_is_silent(monkeypatch):
    responses = _fork_responses({"plan.yml": "on:\n  pull_request_target:\njobs:\n  x:\n"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._summary_wiring_warnings(_ctx()) == []


def test_summary_wiring_without_a_commit_is_a_note_not_a_read(monkeypatch):
    # Same reasoning as the pin and fork-trigger probes: a default-branch read
    # would report the wiring broken on the very pull request that fixes it.
    def gh(path):
        pytest.fail(f"the summary-wiring probe read the API with no commit: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._summary_wiring_warnings(_ctx(head_sha=""))
    assert out == [doctor.SUMMARY_WIRING_NO_COMMIT]
    assert out[0][0] == doctor.NOTICE


def test_summary_wiring_unreadable_directory_degrades_to_a_note(monkeypatch):
    def gh(path):
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._summary_wiring_warnings(_ctx())
    assert out == [doctor.SUMMARY_WIRING_UNREADABLE]
    assert out[0][0] == doctor.NOTICE


def test_summary_wiring_probe_is_registered(monkeypatch):
    """An unregistered probe runs nowhere while its own unit tests stay green --
    assert it actually executes as part of `warnings()`."""
    assert doctor._summary_wiring_warnings in doctor.PROBES
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "dev-eu-apply"),
        **_quiet_new_probes(),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(_plan_calling_summary(f"is-draft: {_IS_DRAFT_EXPR}")),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_ctx())
    assert any(f"`head-repo: {_HEAD_REPO_EXPR}`" in t for _, t in out)


# The three apply-wrapper shapes the retired-input probe judges. The
# declaration is written flow-style, the shape three of the four sample repos
# carry, so the line-anchored key must match it there too.
_APPLY_DECLARING_IT = (
    "name: shipmate · apply\n"
    "on:\n"
    "  workflow_dispatch:\n"
    "    inputs:\n"
    "      ref: { description: PR head SHA to apply, required: false, default: '' }\n"
    "      plan_run_id: { description: Plan run id with the reviewed plans, required: true }\n"
    "jobs:\n"
    "  targeted:\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/apply.yml@{_SHA}\n"
    "    with:\n"
    "      ref: ${{ inputs.ref }}\n"
)
_APPLY_FORWARDING_IT = (
    "name: shipmate · apply\n"
    "on:\n"
    "  workflow_dispatch:\n"
    "jobs:\n"
    "  targeted:\n"
    "    steps:\n"
    f"      - uses: {_ENGINE_REPO}/actions/dispatch@{_SHA}\n"
    "        with:\n"
    "          plan-run-id: ${{ steps.authz.outputs.plan-run-id }}\n"
)
_APPLY_CLEAN = (
    "name: shipmate · apply\n"
    "on:\n"
    "  workflow_dispatch:\n"
    "    inputs:\n"
    "      ref: { description: PR head SHA to apply, required: false, default: '' }\n"
    "jobs:\n"
    "  targeted:\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/apply.yml@{_SHA}\n"
    "    with:\n"
    "      ref: ${{ inputs.ref }}\n"
)


# Hand-written, whole: the findings are compared in full rather than by
# substring, so a reworded message is a deliberate edit here and not a silent
# one. Never derived from `scripts/doctor`.
_DECLARED_TEXT = (
    "`apply.yml` still declares a `plan_run_id` input — the engine retired that input "
    "and dispatches no such value, so nothing ever fills it in. Remove the declaration, "
    "and any `with:` line forwarding it."
)
_FORWARDED_TEXT = (
    "`apply.yml` still passes `plan_run_id` on — the engine retired that input, so nothing "
    "it calls accepts one. Passed to the engine's reusable `apply.yml` or `apply-all.yml`, "
    "GitHub rejects the run when it LOADS the workflow: the run has no jobs and no logs, only "
    "a workflow-validation error on the run itself, which is the hardest failure here to "
    "diagnose from the outside. Passed to a composite action it is only a warning and the run "
    "continues. Remove the `with:` line — the plan run id now travels with each apply cell "
    "and needs no wiring."
)


def test_a_declared_plan_run_id_input_is_reported(monkeypatch):
    responses = _fork_responses({"apply.yml": _APPLY_DECLARING_IT})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._plan_run_id_warnings(_ctx())
    assert out == [(doctor.WARNING, _DECLARED_TEXT)]


def test_a_forwarded_plan_run_id_is_reported(monkeypatch):
    """The half that matters: an input a `workflow_call` does not declare is
    rejected as the run LOADS, so there is no job and no log to read. A probe
    reporting only the declaration leaves that failure undiagnosed."""
    responses = _fork_responses({"apply.yml": _APPLY_FORWARDING_IT})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._plan_run_id_warnings(_ctx())
    assert out == [(doctor.WARNING, _FORWARDED_TEXT)]


def test_a_clean_apply_wrapper_is_silent(monkeypatch):
    responses = _fork_responses({"apply.yml": _APPLY_CLEAN})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._plan_run_id_warnings(_ctx()) == []


def test_the_apply_yml_filter_lives_in_the_dispatcher(monkeypatch):
    """A direct call of the finding function reports whatever file it is handed:
    the caller bypassed the exemption, and silence there reads as a false
    positive that is not one. Only the dispatcher skips another file's name."""
    assert doctor._plan_run_id_finding(_APPLY_DECLARING_IT, "deploy.yml") == [
        (doctor.WARNING, _DECLARED_TEXT.replace("`apply.yml`", "`deploy.yml`"))
    ]
    responses = _fork_responses({"deploy.yml": _APPLY_DECLARING_IT})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._plan_run_id_warnings(_ctx()) == []


def test_the_documented_apply_wrapper_produces_no_finding(monkeypatch):
    """The oracle for false positives, and for the page: the wrapper consumers
    paste, verbatim, through the whole probe. The fence count is asserted first,
    so a page edit that moves the wrapper out of this selector's reach fails
    here instead of passing vacuously."""
    page = (ENGINE / "docs" / "getting-started.md").read_text(encoding="utf-8")
    fences = [
        textwrap.dedent(m.group("body"))
        for m in _YAML_FENCE.finditer(page)
        if "/.github/workflows/apply-all.yml@" in m.group("body")
    ]
    assert len(fences) == 1, f"documented apply-wrapper fences: {len(fences)}"
    responses = _fork_responses({"apply.yml": fences[0]})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._plan_run_id_warnings(_ctx()) == []


def test_plan_run_id_without_a_commit_is_a_note_not_a_read(monkeypatch):
    # Same reasoning as the pin, fork-trigger and summary-wiring probes: a
    # default-branch read would report the retired input on the very pull request
    # that removes it. The `gh` stub pins that no read happens at all, so a
    # weaker read cannot be silently substituted for the skip.
    def gh(path):
        pytest.fail(f"the retired-input probe read the API with no commit: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._plan_run_id_warnings(_ctx(head_sha=""))
    assert out == [doctor.PLAN_RUN_ID_NO_COMMIT]
    assert out[0][0] == doctor.NOTICE


def test_plan_run_id_unreadable_directory_degrades_to_a_note(monkeypatch):
    def gh(path):
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._plan_run_id_warnings(_ctx())
    assert out == [doctor.PLAN_RUN_ID_UNREADABLE]
    assert out[0][0] == doctor.NOTICE


# The four plan-wrapper shapes the dispatch-wiring probe judges. Each bad one
# isolates ONE finding: the two that carry the trigger also carry `pr-facts`,
# and the one missing `pr-facts` is otherwise correctly dispatchable.
_PLAN_NO_TRIGGER = (
    "name: shipmate · plan\n"
    "on:\n"
    "  pull_request_target:\n"
    "jobs:\n"
    "  facts:\n"
    "    steps:\n"
    f"      - uses: {_ENGINE_REPO}/actions/pr-facts@{_SHA}\n"
)
_PLAN_NO_PR_NUMBER = (
    "name: shipmate · plan\n"
    "on:\n"
    "  pull_request_target:\n"
    "  workflow_dispatch:\n"
    "jobs:\n"
    "  facts:\n"
    "    steps:\n"
    f"      - uses: {_ENGINE_REPO}/actions/pr-facts@{_SHA}\n"
    # A `with:` line forwarding the number is a line-anchored `pr_number:`
    # outside the `on:` block, so a whole-file search for the key is satisfied
    # by a wrapper that declares no such input.
    "  summary:\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/summary.yml@{_SHA}\n"
    "    with:\n"
    "      pr_number: ${{ github.event.inputs.pr_number }}\n"
)
_PLAN_NO_PR_FACTS = (
    "name: shipmate · plan\n"
    "on:\n"
    "  pull_request_target:\n"
    "  workflow_dispatch:\n"
    "    inputs:\n"
    "      pr_number: { description: PR to plan, required: true }\n"
    "jobs:\n"
    "  detect:\n"
    "    steps:\n"
    f"      - uses: {_ENGINE_REPO}/actions/build-matrix@{_SHA}\n"
)
_PLAN_DISPATCHABLE = (
    "name: shipmate · plan\n"
    "on:\n"
    "  pull_request_target:\n"
    "  workflow_dispatch:\n"
    "    inputs:\n"
    "      pr_number: { description: PR to plan, required: true }\n"
    "jobs:\n"
    "  facts:\n"
    "    steps:\n"
    f"      - uses: {_ENGINE_REPO}/actions/pr-facts@{_SHA}\n"
)


# Hand-written, whole: each finding is compared in full rather than by
# substring, so a reworded message is a deliberate edit here and not a silent
# one, and the three cannot collapse into one another. Never derived from
# `scripts/doctor`.
_NO_TRIGGER_TEXT = (
    "`plan.yml` declares no `workflow_dispatch` trigger — a commented `shipmate plan` is "
    "authorized, reacted to with a rocket, and then dispatches nothing: GitHub answers the "
    "dispatch with `Workflow does not have 'workflow_dispatch' trigger`, no run is created, "
    "and the error lands on the comment-handling run where nobody on the pull request sees "
    "it. Add the trigger, with a `pr_number` input (docs/getting-started.md)."
)
_NO_PR_NUMBER_TEXT = (
    "`plan.yml`'s `workflow_dispatch` trigger declares no `pr_number` input — that is the "
    "one input `shipmate plan` sends, and GitHub refuses a dispatch body naming an input the "
    "workflow does not declare: `Unexpected inputs provided`, no run created, the error on "
    "the comment-handling run rather than the pull request. Declare `pr_number` under the "
    "trigger's `inputs:` (docs/getting-started.md)."
)
_NO_PR_FACTS_TEXT = (
    "`plan.yml` has no `actions/pr-facts` step — a dispatched run carries no pull request in "
    "its event payload and checks out the dispatch ref, so that step is the only thing that "
    "resolves which pull request, head and repository the run is for. Without it there is no "
    "head SHA to hand `build-matrix`, so on a current engine pin that step refuses EVERY pull "
    "request and `detect` fails loudly — the autoplan too, not only the dispatched leg — "
    "rather than planning the wrong tree quietly. Add the step and feed the jobs below from "
    "it (docs/getting-started.md)."
)


def test_a_plan_wrapper_without_the_dispatch_trigger_is_reported(monkeypatch):
    responses = _fork_responses({"plan.yml": _PLAN_NO_TRIGGER})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._dispatch_wiring_warnings(_ctx())
    assert out == [(doctor.WARNING, _NO_TRIGGER_TEXT)]


def test_a_dispatch_trigger_without_pr_number_is_reported_on_its_own(monkeypatch):
    """Its own finding with its own text and its own remedy: the trigger is
    there, so a reader told only "the wrapper cannot be dispatched" would add
    what it already has. The key is looked for inside the `on:` block, which is
    why this fixture also forwards `pr_number` from a `with:` block below."""
    responses = _fork_responses({"plan.yml": _PLAN_NO_PR_NUMBER})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._dispatch_wiring_warnings(_ctx())
    assert out == [(doctor.WARNING, _NO_PR_NUMBER_TEXT)]
    assert _NO_PR_NUMBER_TEXT != _NO_TRIGGER_TEXT


def test_a_plan_wrapper_without_a_pr_facts_step_is_reported(monkeypatch):
    """The fail-open leg: this wrapper is dispatchable, so the other two
    findings are silent and the run starts — with nothing resolving the pull
    request its own event payload does not carry."""
    responses = _fork_responses({"plan.yml": _PLAN_NO_PR_FACTS})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._dispatch_wiring_warnings(_ctx())
    assert out == [(doctor.WARNING, _NO_PR_FACTS_TEXT)]


def test_a_dispatchable_plan_wrapper_is_silent(monkeypatch):
    responses = _fork_responses({"plan.yml": _PLAN_DISPATCHABLE})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._dispatch_wiring_warnings(_ctx()) == []


def test_a_flow_style_on_value_is_silent(monkeypatch):
    """The whole `on:` value as one flow mapping, with no line start in front of
    either key. The documented fence is block style and cannot cover this: with a
    line-anchored regex both keys go unseen, and because ABSENCE is this probe's
    finding, that reports a correctly wired wrapper as declaring no
    `workflow_dispatch` trigger — one stray warning, not two, since the
    `pr_number` finding is that one's `elif`. Both keys sit inside the flow
    mapping with whitespace in front of them, which is what the one-character
    lookbehind matches; index 0 is unreachable for a searched key here."""
    text = (
        "name: shipmate · plan\n"
        "on:{ pull_request_target: , workflow_dispatch: { inputs: { pr_number: "
        "{ required: true } } } }\n"
        "jobs:\n"
        "  facts:\n"
        "    steps:\n"
        f"      - uses: {_ENGINE_REPO}/actions/pr-facts@{_SHA}\n"
    )
    responses = _fork_responses({"plan.yml": text})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._dispatch_wiring_warnings(_ctx()) == []


def test_a_workflow_dispatch_line_under_jobs_does_not_satisfy_the_trigger(monkeypatch):
    """The reason the trigger is looked for inside the `on:` block: no such key
    exists under `jobs:`, but a whole-file regex is satisfied by any line that
    spells it, and the wrapper is then reported healthy while `shipmate plan`
    reaches nothing."""
    text = _PLAN_NO_TRIGGER.replace(
        "  facts:\n", "  facts:\n    env:\n      workflow_dispatch: yes\n", 1
    )
    responses = _fork_responses({"plan.yml": text})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._dispatch_wiring_warnings(_ctx())
    assert out == [(doctor.WARNING, _NO_TRIGGER_TEXT)]


def test_the_plan_yml_filter_lives_in_the_dispatcher(monkeypatch):
    """A direct call of the finding function reports whatever file it is handed:
    the caller bypassed the exemption, and silence there reads as a false
    positive that is not one. Only the dispatcher skips another file's name."""
    assert doctor._dispatch_wiring_finding(_PLAN_NO_TRIGGER, "drift.yml") == [
        (doctor.WARNING, _NO_TRIGGER_TEXT.replace("`plan.yml`", "`drift.yml`"))
    ]
    responses = _fork_responses({"drift.yml": _PLAN_NO_TRIGGER})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._dispatch_wiring_warnings(_ctx()) == []


def test_the_documented_plan_wrapper_is_dispatchable(monkeypatch):
    """The oracle for false positives, and for the page: the wrapper consumers
    paste, verbatim, through the whole probe. The fence count is asserted first,
    so a page edit that moves the wrapper out of this selector's reach fails
    here instead of passing vacuously. The selector names `plan-cell`, which
    this probe does not read, so the fence cannot be chosen by the very lines
    under test."""
    page = (ENGINE / "docs" / "getting-started.md").read_text(encoding="utf-8")
    fences = [
        textwrap.dedent(m.group("body"))
        for m in _YAML_FENCE.finditer(page)
        if "/actions/plan-cell@" in m.group("body")
    ]
    assert len(fences) == 1, f"documented plan-wrapper fences: {len(fences)}"
    responses = _fork_responses({"plan.yml": fences[0]})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._dispatch_wiring_warnings(_ctx()) == []


def test_dispatch_wiring_without_a_commit_is_a_note_not_a_read(monkeypatch):
    # Same reasoning as the pin, fork-trigger and summary-wiring probes: a
    # default-branch read would report the missing trigger on the very pull
    # request that adds it. The `gh` stub pins that no read happens at all, so a
    # weaker read cannot be silently substituted for the skip.
    def gh(path):
        pytest.fail(f"the dispatch-wiring probe read the API with no commit: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._dispatch_wiring_warnings(_ctx(head_sha=""))
    assert out == [doctor.DISPATCH_WIRING_NO_COMMIT]
    assert out[0][0] == doctor.NOTICE


def test_dispatch_wiring_unreadable_directory_degrades_to_a_note(monkeypatch):
    def gh(path):
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._dispatch_wiring_warnings(_ctx())
    assert out == [doctor.DISPATCH_WIRING_UNREADABLE]
    assert out[0][0] == doctor.NOTICE


def test_dispatch_wiring_probe_is_registered(monkeypatch):
    """An unregistered probe runs nowhere while its own unit tests stay green --
    assert it actually executes as part of `warnings()`."""
    assert doctor._dispatch_wiring_warnings in doctor.PROBES
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "dev-eu-apply"),
        **_quiet_new_probes(),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(_PLAN_NO_TRIGGER),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert (doctor.WARNING, _NO_TRIGGER_TEXT) in doctor.warnings(_ctx())


def test_the_probe_registry_is_exactly_this(monkeypatch):
    """The whole registry against a hand-written list, not its length: a length
    assertion cannot say WHICH entry changed, so a probe swapped for another
    passes it. Order is the order findings are reported in."""
    assert doctor.PROBES == (
        doctor._gate_rule_warnings,
        doctor._review_rule_warnings,
        doctor._environment_warnings,
        doctor._env_protection_warnings,
        doctor._engine_environment_warnings,
        doctor._plan_env_secret_warnings,
        doctor._pin_warnings,
        doctor._fork_trigger_warnings,
        doctor._summary_wiring_warnings,
        doctor._plan_run_id_warnings,
        doctor._dispatch_wiring_warnings,
        doctor._team_warnings,
        doctor._app_permission_warnings,
    )


def _rules_only(*rules):
    return {f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": list(rules)}


def test_review_rule_healthy_is_silent(monkeypatch):
    responses = _rules_only(_pull_request_rule(code_owner=True, count=1))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._review_rule_warnings(_ctx()) == []


def test_review_rule_without_code_owner_review_warned(monkeypatch):
    # An approval count is not a backstop: the App holds `pull-requests: write`
    # and can submit a counting APPROVED review (docs/hardening.md #3-5). Only a
    # CODEOWNERS review is App-proof.
    responses = _rules_only(_pull_request_rule(code_owner=False, count=2))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._review_rule_warnings(_ctx())
    assert out == [(doctor.WARNING, doctor._CODE_OWNER_REVIEW_OFF.format(branch=_BRANCH))]
    # Pin the text, not just the constant: comparing against the constant leaves
    # the two review findings' bodies interchangeable, so swapping them would
    # keep every review-rule test green while telling readers the wrong thing.
    assert "does not require code-owner review" in out[0][1]
    assert "Set `require_code_owner_review`" in out[0][1]


def test_review_rule_no_code_owner_review_at_a_high_count_still_warns(monkeypatch):
    # An approval count of any size is forgeable by the attacker in scope, so
    # the warning is keyed on the boolean, never softened by the count.
    responses = _rules_only(_pull_request_rule(code_owner=False, count=5))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._review_rule_warnings(_ctx())
    assert out == [(doctor.WARNING, doctor._CODE_OWNER_REVIEW_OFF.format(branch=_BRANCH))]


def test_review_rule_count_zero_without_code_owner_review_is_the_worst_case_warning(monkeypatch):
    # count 0 *and* code-owner review off is the one configuration with no
    # unforgeable merge-time control at all -- it must not be reported with the
    # same information notice as the supported sole-maintainer mode below.
    responses = _rules_only(_pull_request_rule(code_owner=False, count=0))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._review_rule_warnings(_ctx())
    assert out == [(doctor.WARNING, doctor._CODE_OWNER_REVIEW_OFF.format(branch=_BRANCH))]


def test_review_rule_count_zero_with_code_owner_review_is_still_a_notice(monkeypatch):
    # required_approving_review_count: 0 is a shipped, supported mode when
    # code-owner review is on: the merge-side control a leaked App key cannot
    # satisfy is still there. A warning on every run for a setting the sole
    # maintainer will not change trains readers to ignore doctor.
    responses = _rules_only(_pull_request_rule(code_owner=True, count=0))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._review_rule_warnings(_ctx())
    assert out == [(doctor.NOTICE, doctor._SOLE_MAINTAINER_REVIEW.format(branch=_BRANCH))]
    # Pin this finding's text too, so swapping the two constants' bodies fails
    # here as well as in the code-owner-off test above.
    assert "requires no approving review" in out[0][1]
    assert "`require_code_owner_review` is on" in out[0][1]


def test_review_rule_findings_are_unioned_across_layered_rulesets(monkeypatch):
    # GitHub enforces the union across layered rulesets. A repo-level count-only
    # rule listed first must not mask an org ruleset that does require code-owner
    # review -- reporting one there is a false "not required".
    responses = _rules_only(
        _pull_request_rule(code_owner=False, count=1),
        _pull_request_rule(code_owner=True, count=0),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._review_rule_warnings(_ctx()) == []


def test_review_rule_absent_warned(monkeypatch):
    responses = _rules_only({"type": "required_status_checks", "parameters": {}})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._review_rule_warnings(_ctx())
    assert out == [(doctor.WARNING, doctor._REVIEW_RULE_ABSENT.format(branch=_BRANCH))]


def test_review_rule_without_parameters_is_unverified_not_misconfigured(monkeypatch):
    # A token that can list the rule but not read its parameters gets the rule
    # with an empty body. Reporting that as "code-owner review is off" would be
    # a false warning about a correctly configured repository.
    responses = _rules_only({"type": "pull_request", "parameters": {}})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._review_rule_warnings(_ctx())
    assert out == [(doctor.NOTICE, doctor._REVIEW_RULE_UNREADABLE.format(branch=_BRANCH))]


def test_review_rule_missing_parameters_key_is_unverified(monkeypatch):
    responses = _rules_only({"type": "pull_request"})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._review_rule_warnings(_ctx())
    assert out == [(doctor.NOTICE, doctor._REVIEW_RULE_UNREADABLE.format(branch=_BRANCH))]


def test_review_rule_probe_is_registered():
    assert doctor._review_rule_warnings in doctor.PROBES


_COUNT_WORDS = {
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
}


def _count_word(n):
    assert n in _COUNT_WORDS, f"the probe count reached {n} -- extend _COUNT_WORDS"
    return _COUNT_WORDS[n]


def test_probe_count_is_stated_correctly_in_the_docs():
    """Adding or removing a probe means editing six pieces of prose that spell
    the count out — two in each of three files. Nothing else notices when they
    go stale, so this reads all three and pins each phrase against
    `len(PROBES)`:

    - `scripts/doctor`'s module docstring: (1) "the <n> live probes" and (2) the
      `Probes:` bullet list below it (one bullet per probe);
    - `CONTRACT.md`: (3) "<n> live settings probes" and (4) "<n-2> of the <n>";
    - `docs/troubleshooting.md`: (5) "combining <n>" and (6) "<n-2> of the <n>
      probes".

    The number words come from the count rather than being hardcoded, so this
    keeps biting when a further probe lands. `<n-2>` is the plan-path subset: the
    approvers-team and App-permission probes cannot report from `annotate` mode.
    """
    total = len(doctor.PROBES)
    word, plan_word = _count_word(total), _count_word(total - 2)

    src = (SCRIPTS / "doctor").read_text(encoding="utf-8")
    assert f"the {word} live probes" in src, f"scripts/doctor no longer says '{word} live probes'"
    bullets = [
        ln for ln in doctor.__doc__.split("Probes:\n", 1)[1].splitlines() if ln.startswith("- ")
    ]
    assert len(bullets) == total, f"the Probes: list names {len(bullets)} probes, not {total}"

    contract = (ENGINE / "CONTRACT.md").read_text(encoding="utf-8")
    assert f"{word} live settings probes" in contract
    assert f"{plan_word} of the {word}" in contract

    trouble = (ENGINE / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
    assert f"combining {word}" in trouble
    assert f"{plan_word} of the {word} probes" in trouble


def test_a_non_file_workflow_entry_does_not_blind_the_fork_trigger_probe(monkeypatch):
    """The pin probe's twin. This probe exists to surface the one
    misconfiguration on docs/hardening.md an outside contributor can reach, and
    it also stops at the first unreadable file -- so a non-file entry sorting
    ahead of the real workflows must be skipped, not treated as unreadable.
    Treating it as unreadable reported "could not read" and hid the
    `pull_request_target` trigger sitting in the very next file."""
    responses = {
        f"{_WF_DIR}{_REF}": [
            {"name": "sub.yml", "type": "dir"},
            {"name": "triage.yml", "type": "file"},
        ],
        f"{_WF_DIR}/triage.yml{_REF}": _wf_file("on:\n  pull_request_target:\n"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "pull_request_target" in out[0][1]


def test_an_unencoded_blob_is_unreadable_not_empty(monkeypatch):
    """A file over 1 MB comes back with `encoding: "none"` and an empty
    `content`. Decoded anyway, that is a readable EMPTY workflow file -- no
    `on:` block at all, which every content probe reads as clean."""
    responses = _fork_responses({"label.yml": ""})
    responses[f"{_WF_DIR}/label.yml{_REF}"] = {"encoding": "none", "content": ""}
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == [doctor.FORK_TRIGGER_UNREADABLE]


def test_harvest_keeps_an_annotation_that_is_not_doctors():
    """The harvest exists to surface what the live probes cannot restate, so
    only DOCTOR_TITLE is filtered -- a filter that dropped everything would
    render an all-clear over a red, gate-blocking run."""
    anns = [
        {
            "annotation_level": "warning",
            "title": "something else",
            "message": "y",
            "check_name": "shipmate / detect",
        },
        {
            "annotation_level": "failure",
            "title": "another thing",
            "message": "z",
            "check_name": "shipmate / detect",
        },
    ]
    sections = doctor.harvest_sections(anns)
    kept = [a["title"] for rows in sections.values() for a in rows]
    assert kept == ["something else", "another thing"]


def test_harvest_drops_doctors_own_annotation_at_every_level():
    """doctor's own annotations are only ever notices and warnings, and the live
    probes restate all of them against current settings."""
    anns = [
        {
            "annotation_level": level,
            "title": doctor.DOCTOR_TITLE,
            "message": "x",
            "check_name": "shipmate / detect",
        }
        for level in doctor.HARVEST_LEVELS
    ]
    assert doctor.harvest_sections(anns) == {}


def test_plan_env_secret_probe_is_registered():
    assert doctor._plan_env_secret_warnings in doctor.PROBES


def test_plan_env_holding_secrets_is_a_notice_naming_each(monkeypatch):
    """NOTICE, not WARNING: docs/hardening.md control 8 permits read-only,
    blast-radius-free credentials in a plan environment, and doctor's WARNING
    means misconfiguration. Same posture as the apply-env-without-reviewers
    note."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "dev-eu-apply"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE]
    assert "`AWS_ACCESS_KEY_ID`" in found[0][1]
    assert "`AWS_SECRET_ACCESS_KEY`" in found[0][1]
    assert "2 secret" in found[0][1]


def test_the_notice_states_the_rule_and_never_that_this_env_is_unprotected(monkeypatch):
    """The sibling `_env_protection_warnings` exists to report a plan
    environment that *does* carry protection rules, so one report can hold both
    findings: a NOTICE asserting that a plan environment cannot have approval
    rules would contradict the warning printed beside it. State control 8's
    requirement instead of a fact about this repository — and settle it without
    reading protection data, which is the sibling's read, so that neither
    probe's failure can silence the other."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(
            "AWS_ACCESS_KEY_ID"
        ),
    }
    asked = []

    def fake(path):
        asked.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", fake)
    text = doctor._plan_env_secret_warnings(_ctx())[0][1]
    assert "must have no approval rules and no deployment branch policy" in text
    assert "control 8" in text
    for claimed in ("cannot have approval", "has no approval", "is unprotected", "no protection"):
        assert claimed not in text, f"the notice asserts this environment {claimed!r}"
    assert not [p for p in asked if "deployment-branch-policies" in p], asked
    assert asked == [
        f"repos/{_REPO}/environments?per_page=100",
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100",
    ]


def test_shared_mode_reads_the_bare_env_and_says_it_is_the_apply_env_too(monkeypatch):
    """In shared mode the plan environment IS the apply environment, so its
    finding cannot be the split one: the credential a consumer put there for
    applying is reachable by plan-time code. Pinned on the requested paths as
    well as the wording -- a probe that read the right environment and worded it
    as a split plan environment understates the exposure."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu"),
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100": _secrets("AWS_ROLE_ARN"),
    }
    asked = []

    def fake(path):
        asked.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", fake)
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE]
    assert "`AWS_ROLE_ARN`" in found[0][1]
    assert "apply environment" in found[0][1]
    assert "plan-time code" in found[0][1]
    assert asked == [
        f"repos/{_REPO}/environments?per_page=100",
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100",
    ]


def test_ambiguous_mode_reads_both_plan_side_names_and_never_the_apply_one(monkeypatch):
    """With both namings present either could be the plan environment, so both
    are read -- and `<env>-apply` is read in neither mode, because control 7
    *requires* credentials there."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu", "dev-eu-plan", "dev-eu-apply"
        ),
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100": _secrets(),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(),
    }
    asked = []

    def fake(path):
        asked.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", fake)
    assert doctor._plan_env_secret_warnings(_ctx()) == []
    assert f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100" in asked
    assert f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100" in asked
    assert not [p for p in asked if "dev-eu-apply" in p], asked


def test_ambiguous_mode_secret_findings_do_not_assert_the_environments_role(monkeypatch):
    """Same rule as the protection findings: with both namings present the report
    already says the binding is undetermined, so neither the notice nor the
    App-key warning may call this environment shared or a plan environment --
    including the notice's later clause about the apply role, which used to
    assert what the opening clause had just hedged."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu", "dev-eu-plan", "dev-eu-apply"
        ),
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100": _secrets(
            "SHIPMATE_APP_PRIVATE_KEY"
        ),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE, doctor.WARNING]
    assert _SHARED_ASSERTED not in found[0][1]
    assert _AMBIGUOUS_UNBOUND not in found[0][1]
    assert found[0][1].startswith(_AMBIGUOUS_INTRO)
    assert "it is the apply environment too" not in found[0][1]
    assert "it may be the apply environment too" in found[0][1]
    assert "plan environment `dev-eu`" not in found[1][1]
    assert "environment `dev-eu` holds `SHIPMATE_APP_PRIVATE_KEY`" in found[1][1]


def test_no_probe_reads_a_repository_variable(monkeypatch):
    """The mode is inferred from environment names precisely so that doctor needs
    no `variables: read` permission -- `app/manifest.json` does not declare one,
    and adding it costs every installation a re-accept. A future "just read the
    variable" edit must break here rather than degrade silently on every
    consumer. Asserted over every path a full `warnings()` run requests."""
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
        **_quiet_new_probes(),
    }
    asked = []

    def fake(path):
        asked.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", fake)
    assert doctor.warnings(_ctx()) == []
    assert asked, "the probes read nothing, so this would pass vacuously"
    assert not [p for p in asked if "variables" in p], asked


def test_a_truncated_listing_cannot_clear_the_app_key(monkeypatch):
    """`_APP_KEY_NAME in names` is a membership test over the names actually
    read, and `bm.gh_json` does not multi-page — so on a truncated listing the
    key can sit outside the page and produce silence, which is the one
    configuration no document blesses reading as a routine note. Absence is
    reportable only when the read was complete."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(
            "A", "B", total=150
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE, doctor.WARNING]
    assert doctor._APP_KEY_NAME in found[1][1]
    assert "was not determined" in found[1][1]


def test_a_complete_listing_without_the_app_key_stays_a_single_notice(monkeypatch):
    """The counterpart of the truncation warning above: a read that saw every
    name really did clear the key, so the fail-closed branch must not fire on
    every environment that holds secrets."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets("A", "B"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert [lvl for lvl, _ in doctor._plan_env_secret_warnings(_ctx())] == [doctor.NOTICE]


def test_a_truncated_listing_that_did_show_the_app_key_reports_holding_it(monkeypatch):
    """Truncation must not downgrade a positive match to "could not determine":
    the key was read, so the finding is that the environment holds it."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(
            "SHIPMATE_APP_PRIVATE_KEY", "A", total=150
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE, doctor.WARNING]
    assert "forge" in found[1][1]
    assert "was not determined" not in found[1][1]


def test_apply_env_secrets_are_never_read(monkeypatch):
    """Control 7 *requires* credentials on `<env>-apply`, so a finding there
    would fire on correct configuration. Asserted on the requested paths, not
    on the absence of a finding: a probe that read the apply environment and
    then dropped the result would satisfy a findings-only assertion."""
    seen = []
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "dev-eu-apply"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(),
    }

    def fake(path):
        seen.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", fake)
    assert doctor._plan_env_secret_warnings(_ctx()) == []
    assert not [p for p in seen if "dev-eu-apply" in p]


def test_engine_environment_secrets_are_never_read(monkeypatch):
    """`shipmate-engine` holds the App key by design (control 16). The probe
    iterates the *declared* envs, never the environments listing, so an
    environment nobody tagged into is not its business."""
    seen = []
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "shipmate-engine"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(),
    }

    def fake(path):
        seen.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", fake)
    assert doctor._plan_env_secret_warnings(_ctx()) == []
    assert f"repos/{_REPO}/environments/shipmate-engine/secrets?per_page=100" not in seen


def test_app_private_key_in_a_plan_env_is_a_warning(monkeypatch):
    """The one configuration no document blesses: plan-time code execution
    could mint an App token with it. A name match, not a pattern guess."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(
            "SHIPMATE_APP_PRIVATE_KEY"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE, doctor.WARNING]
    assert "forge" in found[1][1]
    assert doctor.GATE in found[1][1]
    assert doctor._ENGINE_ENV in found[1][1]
    # Split mode is not ambiguous, so the definite noun is correct here.
    assert "plan environment `dev-eu-plan` holds" in found[1][1]


def test_shared_mode_app_key_warning_does_not_call_the_env_a_plan_environment(monkeypatch):
    """The noun follows the ROLE, not the mode. In shared mode the sibling NOTICE
    in the same report calls `dev-eu` shared between both paths, so this warning
    may not call the same environment the plan environment."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu"),
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100": _secrets(
            "SHIPMATE_APP_PRIVATE_KEY"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE, doctor.WARNING]
    assert "plan environment `dev-eu`" not in found[1][1]
    assert "environment `dev-eu` holds `SHIPMATE_APP_PRIVATE_KEY`" in found[1][1]


def test_a_truncated_environments_listing_refuses_to_infer_a_mode(monkeypatch):
    """A partial listing used to produce a false "does not exist"; it now also
    produces a wrong MODE, which asserts a naming the repository is not using and
    redirects the plan-secret probe to the wrong environment. So it raises, and
    `warnings()` degrades every environment probe loudly rather than reporting on
    half the names."""
    monkeypatch.setattr(doctor, "_gh_json", lambda path: _environments("dev-eu-plan", total=140))
    with pytest.raises(SystemExit, match="truncated"):
        doctor._existing_env_names(_ctx())


def test_an_environments_listing_without_a_total_count_is_not_taken_as_complete(monkeypatch):
    """Absence-means-complete would be fail-open: a listing that stops reporting
    `total_count` would silently pass as the whole set. The KeyError reaches
    `warnings()`' degrade instead."""
    monkeypatch.setattr(doctor, "_gh_json", lambda path: {"environments": [{"name": "dev-eu"}]})
    with pytest.raises(KeyError):
        doctor._existing_env_names(_ctx())


def test_no_env_token_is_a_warning_and_reads_nothing(monkeypatch):
    """An unaccepted permission request must never read as an all-clear.

    `pytest.fail`, not a raise: the probe catches `(Exception, SystemExit)` per
    environment, so a plain exception would degrade to a NOTICE and this test
    would pass against the very mutation it exists to catch."""
    monkeypatch.delenv("SHIPMATE_ENV_TOKEN", raising=False)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: pytest.fail(f"read {path}"))
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.WARNING]
    assert found == [doctor._ENV_TOKEN_UNCHECKED]
    assert "environments: read" in found[0][1]


def test_no_declared_env_reads_nothing_and_says_nothing(monkeypatch):
    """A docs-only or pin-bump pull request declares no environment, so there
    was nothing to read and no check went dark. The declared-env check must
    therefore come *before* the token check -- with neither, this returns the
    dark-check WARNING instead of nothing."""
    monkeypatch.delenv("SHIPMATE_ENV_TOKEN", raising=False)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: pytest.fail(f"read {path}"))
    assert doctor._plan_env_secret_warnings(_ctx(envs=set(), envs_available=False)) == []


def test_an_environment_that_does_not_exist_is_never_read(monkeypatch):
    """Existence comes from the environments *listing*, never from the
    per-environment read's exception. `bm.gh_json` raises without a status code,
    so a 404 for a declared-but-absent environment would be indistinguishable
    from the 403 that means this check saw nothing -- and the degrade note claims
    the environment exists but could not be read, which for an absent one is
    false and duplicates `_environment_warnings`' own finding.

    Asserted on the paths requested, not just on the absence of a finding: a
    probe that read the missing environment and swallowed the error would also
    return []."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(),
    }
    asked = []

    def fake(path):
        asked.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", fake)
    assert doctor._plan_env_secret_warnings(_ctx(envs={"dev-eu", "dev-nope"})) == []
    assert not [p for p in asked if "dev-nope" in p], f"read an absent environment: {asked}"


def test_a_long_secret_list_is_capped_so_it_cannot_eat_the_size_budget(monkeypatch):
    """The settings section is never truncated, so one environment with many
    secrets would otherwise spend the whole of `sc.SIZE_BUDGET` and push every
    other finding into the omitted-findings fallback -- and in annotate mode
    GitHub would cut the line off with no marker at all. Capped the same way the
    all-clear line's environment list is. The count is asserted alongside: a cap
    that also understated the total would hide secrets, not just their names."""
    names = [f"CONSUMER_CREDENTIAL_NUMBER_{i:03d}" for i in range(60)]
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(*names),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE]
    assert "…" in found[0][1], "a truncated name list must carry the ellipsis marker"
    assert len(found[0][1]) < 1000
    # The cap hides names, never the number of them.
    assert "60 secret(s)" in found[0][1]
    assert names[-1] not in found[0][1]


def test_one_env_listing_failure_is_a_notice_and_the_others_still_report(monkeypatch):
    """Per-environment degrade, like `_env_protection_warnings`: one 403 must
    not silence the environment that could be read."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "dev-us-plan"),
        f"repos/{_REPO}/environments/dev-us-plan/secrets?per_page=100": _secrets(
            "GOOGLE_CREDENTIALS"
        ),
    }

    def fake(path):
        if path.endswith("dev-eu-plan/secrets?per_page=100"):
            raise SystemExit(f"::error::command failed (1): gh api {path}")
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", fake)
    found = doctor._plan_env_secret_warnings(_ctx(envs={"dev-eu", "dev-us"}))
    assert [lvl for lvl, _ in found] == [doctor.NOTICE, doctor.NOTICE]
    assert "`dev-eu-plan`" in found[0][1]
    assert "could not be listed" in found[0][1]
    assert "`GOOGLE_CREDENTIALS`" in found[1][1]


def test_plan_env_secret_listing_failure_propagates_to_the_degrade_note(monkeypatch):
    """The environments listing is this probe's precondition -- without it no
    name can be classified present-or-absent -- so its failure must reach
    `warnings()`' outer handler and become a "could not verify" degrade rather
    than a silent empty result that reads as "no secrets anywhere"."""

    def boom(path):
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", boom)
    with pytest.raises(SystemExit):
        doctor._plan_env_secret_warnings(_ctx())
    out = doctor.warnings(_ctx())
    assert any("plan env secret" in t and "probe skipped" in t for _, t in out)


def test_truncated_secret_listing_reads_as_at_least(monkeypatch):
    """`bm.gh_json` does not multi-page, so a repository with more than 100
    secrets in one environment returns a partial list. Reporting `len(names)`
    would understate it as the whole set. The same partial read also warns that
    the App-key check could not be completed
    (`test_a_truncated_listing_cannot_clear_the_app_key`)."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(
            "A", "B", total=150
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE, doctor.WARNING]
    assert "at least 150" in found[0][1]


def test_secret_listing_uses_the_env_token_and_restores_gh_token(monkeypatch):
    """The ambient GH_TOKEN is the App token minted without `environments:
    read`; only the dedicated mint's token can list secrets. The five probes
    after this one must still see the token it started with."""
    monkeypatch.setenv("GH_TOKEN", "apptok")
    monkeypatch.setenv("SHIPMATE_ENV_TOKEN", "envtok")
    seen = {}

    def fake(path):
        if path.endswith("/secrets?per_page=100"):
            seen["secrets_call"] = os.environ.get("GH_TOKEN")
            return _secrets()
        seen["listing_call"] = os.environ.get("GH_TOKEN")
        return _environments("dev-eu-plan")

    monkeypatch.setattr(doctor, "_gh_json", fake)
    assert doctor._plan_env_secret_warnings(_ctx()) == []
    assert seen["secrets_call"] == "envtok"
    assert seen["listing_call"] == "apptok"
    assert os.environ["GH_TOKEN"] == "apptok"  # noqa: S105 - fixture value, not a real token


def test_gh_token_stays_unset_when_it_was_unset_before(monkeypatch):
    """The restore must reproduce absence, not write an empty string: a later
    `gh api` call with GH_TOKEN="" authenticates as nobody instead of falling
    back to the ambient credential."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("SHIPMATE_ENV_TOKEN", "envtok")
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._plan_env_secret_warnings(_ctx()) == []
    assert "GH_TOKEN" not in os.environ


def test_declared_envs_reads_a_flat_single_artifact_download(tmp_path):
    # Same layout split `pending-checks` has to survive: with exactly one
    # matching artifact, the download lands at `<cells>/cell.json` with no
    # per-artifact directory. Insisting on the nested layout silently empties
    # the declared-env set, which skips every environment probe on any
    # single-cell plan.
    (tmp_path / "cell.json").write_text(json.dumps({"environment": "dev-eu"}), encoding="utf-8")
    assert doctor._declared_envs(tmp_path) == {"dev-eu"}
