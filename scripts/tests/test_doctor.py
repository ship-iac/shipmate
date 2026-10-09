import io
import json
import os
import sys

import pytest
from _loader import ACTIONS, ENGINE, SCRIPTS, load_script
from _shipmate import load_workflow_text
from test_env_config_yaml_fixtures import CANONICAL

doctor = load_script("doctor")


def _documented_workflow_file():
    """The workflow file `docs/getting-started.md` tells consumers to paste, read through the
    same `scripts/onboard` selector that renders it into a repository -- so a page edit that
    moves it out of that selector's reach fails here instead of passing vacuously."""
    return load_script("onboard")._fence(
        (ENGINE / "docs" / "getting-started.md").read_text(encoding="utf-8"), "shipmate"
    )


_REPO = "o/r"
_APP_ID = "999"
_BRANCH = "main"
_ENVS = {"dev-eu"}
_HEAD = "f" * 40
_ENGINE_REPO = "acme/engine"
#: The engine repository `docs/getting-started.md`'s fence calls.
_PUBLISHED_ENGINE = "ship-iac/shipmate"
_WF_DIR = f"repos/{_REPO}/contents/.github/workflows"
# The pin probe reads the workflow files at the commit under examination, so
# every contents path it asks for carries _ctx()'s head_sha as ?ref=.
_REF = f"?ref={_HEAD}"


def _ctx(**over):
    ctx = {
        "repo": _REPO,
        "app_id": _APP_ID,
        "default_branch": _BRANCH,
        "envs": set(_ENVS),
        "app_permission_error": "",
        "head_sha": _HEAD,
        "plan_run_ids": ["1281"],
        "annotations_dir": "ann",
        "check_ids_path": "check-ids.tsv",
        "harvest_failed": False,
        "harvest_pending": False,
        # The engine's own owner/repo, passed in by the calling step from
        # `job.workflow_repository` -- never hardcoded, so the probe stays
        # org-agnostic while only ever reporting on shipmate's own pins.
        "engine_repo": _ENGINE_REPO,
    }
    ctx.update(over)
    return ctx


@pytest.fixture(autouse=True)
def _run_context(monkeypatch):
    """The runner defaults `provenance` reads for the verdict's commit and run links."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "12")


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
    """Without the declared environment set there is nothing to check a listing
    against, so the probe reads nothing and says the probes were skipped.

    Mutation: move the listing read above the empty-`envs` check."""
    monkeypatch.setattr(doctor, "_gh_json", lambda path: pytest.fail(f"read {path}"))
    assert doctor._environment_warnings(_ctx(envs=set())) == [
        (
            doctor.NOTICE,
            "no plan run with cell summaries for this commit: the declared "
            "environment set is unknown, so the environment probes were skipped.",
        )
    ]


def test_ctx_from_env_missing_cells_dir_yields_empty_envs(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.setenv("SHIPMATE_APP_ID", _APP_ID)
    monkeypatch.setenv("SHIPMATE_DEFAULT_BRANCH", _BRANCH)
    monkeypatch.setenv("SHIPMATE_CELLS_DIR", str(tmp_path / "missing"))
    ctx = doctor.ctx_from_env()
    assert ctx["envs"] == set()


def test_declared_envs_skips_malformed_cell_json(tmp_path):
    # Plausible after a partial `gh run download` (one of gatherdoc's degrade
    # paths) -- must be skipped, not raise and red the render step.
    bad = tmp_path / "cell-summary.dev-eu.app"
    bad.mkdir()
    (bad / "cell.json").write_text("{not valid json", encoding="utf-8")
    assert doctor._declared_envs(tmp_path) == set()


def test_declared_envs_skips_cell_json_without_a_usable_environment(tmp_path):
    """Well-formed JSON of the wrong shape degrades like unparsable JSON: KeyError, not
    only JSONDecodeError, is a guarded exception. A null, non-string or empty
    `environment` is dropped too, or `envs` goes non-empty and every environment
    probe runs against a name that cannot exist."""
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
    """Healthy responses for the env-protection, engine-environment, plan-env-secret,
    pin-freshness, fork-trigger, `shipmate.yml` and drift-file probes, so tests exercising the
    older gate/environment probes through `warnings()` collect no incidental noise from these
    seven.
    The config probe's read is here too, serving the design's canonical file: a sound table
    is silent in `warnings()`, and its status lines are rendered from `config_status` instead.

    The pin and fork-trigger probes read the workflow listing, and the `shipmate.yml` probe
    one file of it. `_SHIPMATE_WF`'s `uses:` lines are engine pins -- `_PIN` matches a
    `.github/workflows/` path as well as an `actions/` one -- so the pin probe has something
    to read and needs the release endpoints to agree with it: the pinned SHA and the SHA the
    release lookup returns are the same `_SHA`, or it reports staleness. That file is on
    `pull_request_target` and named `shipmate.yml`, which keeps the fork-trigger probe quiet:
    it is the exemption, not the absence of the trigger. Its plan-calling job is named
    `shipmate`, its dispatch leg -- the trigger, the four inputs, the verb options -- is
    whole, and its five jobs carry the five documented `if:` expressions, keeping the
    `shipmate.yml` probe quiet. `shipmate-drift.yml` calls the engine's `drift.yml`, keeping
    the drift-file probe quiet. The plan-env secret probe reads one listing per plan env; an
    empty one keeps the healthy path quiet."""
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
        f"{_WF_DIR}{_REF}": _wf_listing("shipmate.yml", "shipmate-drift.yml"),
        f"{_WF_DIR}/shipmate.yml{_REF}": _wf_file(_SHIPMATE_WF),
        f"{_WF_DIR}/shipmate-drift.yml{_REF}": _wf_file(_SHIPMATE_DRIFT_WF),
        f"repos/{_ENGINE_REPO}/releases/latest": {"tag_name": "v9.9.9"},
        f"repos/{_ENGINE_REPO}/commits/v9.9.9": {"sha": _SHA},
        _CONFIG_READ: _wf_file(CANONICAL),
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
    }


#: The config probe's read. `_quiet_new_probes` says why a sound table is silent here.
_CONFIG_READ = f"repos/{_REPO}/contents/{doctor.CONFIG_PATH}{_REF}"
#: The environment probes' read: no ref, the request `read_table` makes, which GitHub
#: answers from the default branch. Hand-written so a re-added `?ref=` reddens.
_CONFIG_ON_DEFAULT = "repos/o/r/contents/.github/shipmate-config.yml"


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


def test_one_run_reads_the_default_branch_table_once(monkeypatch):
    """Every probe that needs the default branch's table judges one read of it: the review
    count-0 path and the three environment probes behind `_bound_names`.

    Mutation: have `_default_branch_table` read the file without the `ctx` memo -- the file
    is read four times.
    """
    rules = [_gate_rule()[0], _pull_request_rule(count=0)]
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": rules,
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
        **_quiet_new_probes(),
    }
    asked = []

    def gh(path):
        asked.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    ctx = _ctx()
    doctor.warnings(ctx)
    doctor.config_status(ctx)
    assert asked.count(_CONFIG_ON_DEFAULT) == 1


def test_missing_environment_of_the_split_pair_warned(monkeypatch):
    """Split mode with one half absent names the absent half SPECIFICALLY, not
    the pair: naming both would tell a consumer to create an environment they
    already have."""
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        # `dev-eu-apply` is missing, and `shipmate-engine` is present so that only the
        # pair probe's own finding surfaces.
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


#: Hand-written tables for the mode tests. `CANONICAL` declares `dev-eu` without the key.
_SHARED_TABLE = """\
layout: tf_vars

environments:
  dev-eu:
    region: eu-west-1
    shared: true
"""
_UNSHARED_TABLE = """\
layout: tf_vars

environments:
  dev-eu:
    region: eu-west-1
    shared: false
"""
#: Well-formed YAML that `validate_structure` refuses: an entry that is not a mapping.
_INVALID_TABLE = """\
layout: tf_vars

environments:
  dev-eu: [7]
"""


def _existence(*names, table=CANONICAL):
    """`_environment_warnings`' two reads: the environments listing and the table."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments(*names),
        _CONFIG_ON_DEFAULT: _wf_file(table),
    }
    return lambda path: responses[path]


#: Every finding the split and shared namings produce, hand-written.
_MISSING_PLAN = (
    doctor.WARNING,
    "GitHub Environment `dev-eu-plan` does not exist: the plan jobs for stacks tagged "
    "`env:dev-eu` bind a name GitHub auto-creates empty, with no secrets and none of its "
    "protection rules. Create it.",
)
_MISSING_APPLY = (
    doctor.WARNING,
    "GitHub Environment `dev-eu-apply` does not exist: the apply jobs for stacks tagged "
    "`env:dev-eu` bind a name GitHub auto-creates empty, with no secrets and none of its "
    "protection rules. Create it.",
)
_MISSING_SHARED = (
    doctor.WARNING,
    "GitHub Environment `dev-eu` does not exist: the plan and apply jobs for stacks "
    "tagged `env:dev-eu` bind a name GitHub auto-creates empty, with no secrets and none of "
    "its protection rules. Create it.",
)
#: The shared-mode NOTICE for a bare `dev-eu` with no approval rules.
_SHARED_UNREVIEWED = (
    doctor.NOTICE,
    "GitHub Environment `dev-eu` (shared between plan and apply by `shared: true` in its "
    "`environments.dev-eu` entry) has no approval rules (required reviewers or a wait "
    "timer), so pre-merge applies to it are unreviewed, and no reviewer gate is available "
    "while it is shared: a reviewer here would stall the plan cells and every drift sweep "
    "covering it. "
    "Split it into `dev-eu-plan` and `dev-eu-apply` if you need one.",
)


def _env_findings(monkeypatch, table, *envs):
    """Both existence and protection findings for `envs`, against `table`."""
    responses = _protection(*envs, table=table)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    return doctor._environment_warnings(_ctx()) + doctor._env_protection_warnings(_ctx())


def test_the_table_selects_the_mode(monkeypatch):
    """The mode comes from `shared: true` in the environment's table entry, never from
    which environments exist: the same bare `dev-eu` is a healthy shared environment under
    the key and two missing halves without it. `shared: false` reads as absent.

    Mutation: make env-config's `env_names` ignore `shared` and always return the split
    pair -- the shared case reddens; always return the bare shared name -- the other two
    redden."""
    assert _env_findings(monkeypatch, _SHARED_TABLE, _env("dev-eu")) == [_SHARED_UNREVIEWED]
    split = [_MISSING_PLAN, _MISSING_APPLY]
    assert _env_findings(monkeypatch, CANONICAL, _env("dev-eu")) == split
    assert _env_findings(monkeypatch, _UNSHARED_TABLE, _env("dev-eu")) == split


#: Hand-written: the environment probes' one finding when the default branch's table is unusable.
_DEFAULT_TABLE_SKIPPED = (
    doctor.NOTICE,
    "the environment probes were skipped: the default branch's `.github/shipmate-config.yml` "
    "could not be read, is invalid, or references a GitHub variable that is unset or empty, "
    "and it alone selects which GitHub Environments a run binds. While that holds, every run "
    "refuses at detect.",
)


def test_an_invalid_default_table_skips_the_environment_probes(monkeypatch):
    """`shared_envs` reads entries without a mapping guard, so it may only see a table
    `validate_structure` accepted. An invalid default-branch table selects no naming: the
    environment probes say they were skipped, once, beside the config probe's own finding
    about the examined commit's copy.

    Mutation: have `_default_branch_table` keep the unvalidated `parse_table` result, so
    `_bound_names` calls `shared_envs` on it -- the non-table entry raises inside every
    environment probe and `warnings()` degrades them."""
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu", "shipmate-engine"),
        **_quiet_new_probes(),
        _CONFIG_READ: _wf_file(_INVALID_TABLE),
        _CONFIG_ON_DEFAULT: _wf_file(_INVALID_TABLE),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.warnings(_ctx()) == [
        _DEFAULT_TABLE_SKIPPED,
        (
            doctor.WARNING,
            "`.github/shipmate-config.yml` at the commit under examination is not valid: "
            "environment dev-eu must be a mapping, got list. Merging it refuses every "
            "operation that reads the table. Execution still reads the default branch's "
            "copy, which this says nothing about.",
        ),
    ]


def test_an_unreadable_default_table_is_not_read_as_split(monkeypatch):
    """No naming is guessed when the default branch's table cannot be read: a split guess
    reports `dev-eu-plan`/`dev-eu-apply` missing on a repository whose runs bind `dev-eu`.

    Mutation: have `_bound_names` take `set()` as the shared envs when the read fails -- the
    split existence findings replace the NOTICE."""

    def gh(path):
        if path == _CONFIG_ON_DEFAULT:
            raise SystemExit("::error::command failed (1): gh api ...")
        return _environments("dev-eu")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    found = (
        doctor._environment_warnings(_ctx())
        + doctor._env_protection_warnings(_ctx())
        + doctor._plan_env_secret_warnings(_ctx())
    )
    assert found == [_DEFAULT_TABLE_SKIPPED]


def _environment_probes(monkeypatch, ctx):
    """(the three environment probes' findings, every path they asked for)."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu"),
        _CONFIG_ON_DEFAULT: _wf_file(_SHARED_TABLE),
    }
    asked = []

    def gh(path):
        asked.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    found = (
        doctor._environment_warnings(ctx)
        + doctor._env_protection_warnings(ctx)
        + doctor._plan_env_secret_warnings(ctx)
    )
    return found, asked


def test_an_interpreter_below_the_floor_skips_the_environment_probes(monkeypatch):
    """Below the Python floor no revision of the table is at fault, and the config probe's
    WARNING already says so: the skip NOTICE blames the runner, not the file, and nothing is
    read for probes that cannot select a naming.

    Mutation: drop the NOTICE (`return []`) -- an empty list reads as every environment
    existing; or move the refusal check after `_existing_env_names` in any probe -- the
    listing is read."""
    monkeypatch.setattr(sys, "version_info", (3, 11, 9, "final", 0))
    found, asked = _environment_probes(monkeypatch, _ctx())
    assert found == [
        (
            doctor.NOTICE,
            "the environment probes were skipped: this runner's Python refuses the "
            "environment table, so which GitHub Environments a run binds cannot be "
            "determined here.",
        )
    ]
    assert asked == []


def test_no_declared_env_reads_nothing_in_the_environment_probes(monkeypatch):
    """With no env to probe there is no binding to select, so neither the listing nor the
    default branch's table is read, and a failed read cannot report probes that had no work.
    The one finding is the skipped-probes NOTICE.

    Mutation: remove `_env_protection_warnings`' early return on an empty `ctx["envs"]` --
    it reads the listing and the table."""
    found, asked = _environment_probes(monkeypatch, _ctx(envs=set()))
    assert found == [
        (
            doctor.NOTICE,
            "no plan run with cell summaries for this commit: the declared "
            "environment set is unknown, so the environment probes were skipped.",
        )
    ]
    assert asked == []


@pytest.mark.parametrize(
    "at_head",
    [CANONICAL, _UNSHARED_TABLE],
    ids=["key-absent-at-head", "shared-false-at-head"],
)
def test_the_environment_probes_follow_the_default_branchs_table(monkeypatch, at_head):
    """Execution binds from the default branch's table, so on a pull request that removes
    `shared: true` every run until merge still binds the bare `dev-eu`: that is the
    environment the probes inspect. The table is read with no ref, as `read_table` reads it,
    whatever the event payload names as the default branch.

    Mutation: read the table at `_contents_ref(ctx)` instead of the default branch -- the
    probes inspect `dev-eu-plan`/`dev-eu-apply`; or re-add `?ref=<default branch>` to the
    read -- the whole path differs."""
    on_default = _CONFIG_ON_DEFAULT
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu"),
        _CONFIG_READ: _wf_file(at_head),
        on_default: _wf_file(_SHARED_TABLE),
        f"repos/{_REPO}/environments/dev-eu": _env("dev-eu"),
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100": _secrets(),
    }
    asked = []

    def gh(path):
        asked.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    checked = []
    real_existing = doctor._existing_env_names

    def existing(ctx):
        checked.append(1)
        return real_existing(ctx)

    monkeypatch.setattr(doctor, "_existing_env_names", existing)
    ctx = _ctx(default_branch="release/v1")
    found = (
        doctor._environment_warnings(ctx)
        + doctor._env_protection_warnings(ctx)
        + doctor._plan_env_secret_warnings(ctx)
    )
    assert found == [_SHARED_UNREVIEWED]
    listing = f"repos/{_REPO}/environments?per_page=100"
    assert asked == [
        listing,
        on_default,
        f"repos/{_REPO}/environments/dev-eu",
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100",
    ]
    # The listing is read once per run, so each probe's existence check is counted here.
    assert len(checked) == 3


def test_a_missing_environment_is_named_by_the_selected_naming(monkeypatch):
    """With nothing created, split mode names each half and shared mode the bare name,
    and neither names the naming the table does not select.

    Mutation: report the other mode's names from `_environment_warnings`."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("shipmate-engine"))
    assert doctor._environment_warnings(_ctx()) == [_MISSING_PLAN, _MISSING_APPLY]
    monkeypatch.setattr(doctor, "_gh_json", _existence("shipmate-engine", table=_SHARED_TABLE))
    assert doctor._environment_warnings(_ctx()) == [_MISSING_SHARED]


def test_both_namings_present_report_only_the_selected_namings_gaps(monkeypatch):
    """No finding names the naming the table does not select: with both present nothing
    is reported, and beside a half pair only the missing half is.

    Mutation: append a warning for each present environment of the other naming in
    `_environment_warnings`."""
    names = ("dev-eu", "dev-eu-plan", "dev-eu-apply")
    monkeypatch.setattr(doctor, "_gh_json", _existence(*names))
    assert doctor._environment_warnings(_ctx()) == []
    monkeypatch.setattr(doctor, "_gh_json", _existence(*names, table=_SHARED_TABLE))
    assert doctor._environment_warnings(_ctx()) == []
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu", "dev-eu-plan"))
    assert doctor._environment_warnings(_ctx()) == [_MISSING_APPLY]


def test_split_missing_half_does_not_claim_the_jobs_cannot_run(monkeypatch):
    """With `dev-eu-plan` present and `dev-eu-apply` absent the apply
    binds a name GitHub auto-creates empty and proceeds. "cannot apply" sends the
    reader looking for a failed run."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu-plan"))
    out = doctor._environment_warnings(_ctx())
    assert len(out) == 1
    level, text = out[0]
    assert level == doctor.WARNING
    assert "`dev-eu-apply` does not exist" in text
    assert "cannot apply" not in text
    assert "auto-creates empty" in text


def test_shared_environment_produces_no_existence_finding(monkeypatch):
    """The bare name alone under `shared: true` is a supported configuration --
    not a half-created split pair."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu", table=_SHARED_TABLE))
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
    """`ec.gh_json` hard-fails a nonzero `gh api` exit with `raise SystemExit`, which derives
    from BaseException, so a catch of only `except Exception` lets a 403 on `rules/branches`
    propagate past `warnings()`. The environments probe still succeeds and its own finding
    must surface beside the degrade note: one probe failing may not swallow the other."""
    quiet = _quiet_new_probes()

    def fake_gh_json(path):
        if "rules/branches" in path:
            raise SystemExit("::error::command failed (1): gh api ...")
        if path in quiet:
            return quiet[path]
        return _environments(
            "dev-eu-plan", "shipmate-engine"
        )  # `dev-eu-apply` is missing, so that probe adds its own warning.

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
    """The degrade text renders verbatim into the sticky comment and into `::warning ...::`
    annotation data, so echoing `ec.gh_json`'s `::error::command failed (N): gh api <path>`
    would put a literal workflow command in the comment body, nest one inside another in
    annotate mode, and leak the internal endpoint. Name the probe skipped; keep the reason."""
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
    assert "command failed (1)" in text
    assert "::error::" not in text
    assert "gh api" not in text and "rules/branches" not in text


def _protection(*envs, listed=None, table=CANONICAL):
    """Responses for `_env_protection_warnings`: the environments listing the
    probe reads first (by default naming exactly the fixtures given), the table
    that selects each environment's naming, plus each fixture's per-environment
    protection read."""
    out = {f"repos/{_REPO}/environments/{e['name']}": e for e in envs}
    names = [e["name"] for e in envs] if listed is None else listed
    out[f"repos/{_REPO}/environments?per_page=100"] = _environments(*names)
    out[_CONFIG_ON_DEFAULT] = _wf_file(table)
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
    assert out[0][0] == doctor.NOTICE
    assert "dev-eu-apply" in out[0][1]
    # "no approval rules", never "no protection rules": the finding keys on
    # `approval`, which excludes the branch_policy rule GitHub synthesizes, so
    # a branch policy may well be present on the environment it names.
    assert "no approval rules" in out[0][1]
    assert "required reviewers" in out[0][1] and "wait timer" in out[0][1]


def test_apply_env_with_only_a_branch_policy_is_still_noted(monkeypatch):
    """GitHub synthesizes a `branch_policy` protection rule whenever
    `deployment_branch_policy` is set, so an apply environment carrying nothing but a
    branch policy has a truthy `protection_rules` list while being entirely unreviewed.
    Keying the note on the raw rule list reports it as protected, inverting the finding."""
    responses = _protection(
        _env("dev-eu-plan"),
        _env("dev-eu-apply", branch_policy={"protected_branches": True}),
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE
    assert "dev-eu-apply" in out[0][1] and "no approval rules" in out[0][1]


def test_apply_env_with_only_a_typeless_rule_is_still_noted(monkeypatch):
    """A typeless rule confirms no reviewer or wait timer, so the apply notice stands.
    Mutation: key the notice on `not approval` -- the `?` rule silences it."""
    responses = _protection(_env("dev-eu-plan"), _env("dev-eu-apply", rules=("",)))
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._env_protection_warnings(_ctx()) == [
        (
            doctor.NOTICE,
            "GitHub Environment `dev-eu-apply` has no approval rules (required reviewers "
            "or a wait timer), so pre-merge applies to it are unreviewed.",
        )
    ]


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
    AND every drift sweep covering it -- both have to be named, or a consumer reads the
    finding as being only about applies."""
    responses = _protection(_env("dev-eu", rules=("required_reviewers",)), table=_SHARED_TABLE)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "`dev-eu`" in out[0][1]
    assert "required_reviewers" in out[0][1]
    assert "plan cells" in out[0][1]
    assert "drift" in out[0][1]


def test_shared_env_with_a_branch_policy_is_a_notice_naming_the_trade_both_ways(monkeypatch):
    """NOTICE, not the WARNING a split plan environment gets: on a shared environment the
    policy is a real control over which branches may claim its secrets, and simultaneously
    what refuses plan cells whose base ref it does not name. A consumer whose pull requests
    all target the default branch is correct to set it, so a WARNING would be unclearable."""
    responses = _protection(
        _env("dev-eu", branch_policy={"protected_branches": True}), table=_SHARED_TABLE
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._env_protection_warnings(_ctx())
    assert [lvl for lvl, _ in out] == [doctor.NOTICE, doctor.NOTICE]
    policy = next(t for _, t in out if "branch policy" in t)
    assert "secrets" in policy
    assert "base ref" in policy


def test_shared_env_without_approval_rules_says_no_gate_is_available(monkeypatch):
    """The split apply-env note says applies are unreviewed; on a shared
    environment it must also say that adding a reviewer is not an option while
    the environment is shared -- otherwise the fix it implies stalls every plan
    cell."""
    responses = _protection(_env("dev-eu"), table=_SHARED_TABLE)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._env_protection_warnings(_ctx()) == [_SHARED_UNREVIEWED]


def test_the_protection_probe_reads_only_the_selected_naming(monkeypatch):
    """No job binds the naming the table does not select, so its protection shape is no
    rule on any path and it is not read.

    Mutation: have `_env_protection_warnings` read both namings."""
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
    assert doctor._env_protection_warnings(_ctx()) == []
    assert seen == [
        f"repos/{_REPO}/environments?per_page=100",
        _CONFIG_ON_DEFAULT,
        f"repos/{_REPO}/environments/dev-eu-plan",
        f"repos/{_REPO}/environments/dev-eu-apply",
    ]


def test_a_shared_env_with_reviewers_names_the_key_that_shares_it(monkeypatch):
    """The whole WARNING: the table's key is what shares the environment, so both the
    opening clause and the remedy name it.

    Mutation: restore the remedy "remove `dev-eu` from `SHIPMATE_SHARED_ENVS`"."""
    responses = _protection(_env("dev-eu", rules=("required_reviewers",)), table=_SHARED_TABLE)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._env_protection_warnings(_ctx()) == [
        (
            doctor.WARNING,
            "GitHub Environment `dev-eu` (shared between plan and apply by `shared: true` "
            "in its `environments.dev-eu` entry) has protection rules "
            "(required_reviewers). A protection rule gates every job that binds the "
            "environment and GitHub offers no per-job filter, so the plan cells and "
            "every drift sweep covering it will not start immediately either. To gate applies "
            "only, split "
            "it into `dev-eu-plan` and `dev-eu-apply` and remove `shared: true` from its "
            "`environments.dev-eu` entry.",
        )
    ]


def test_env_protection_missing_env_is_not_this_probes_problem(monkeypatch):
    """An environment that does not exist is `_environment_warnings`' finding, not this
    probe's, so a name absent from the environments listing is skipped without a
    per-environment read and without a finding. Catching every per-environment exception
    and continuing instead silences 403s and 5xx on environments that DO exist."""
    responses = _protection(_env("dev-eu-plan"), listed=["dev-eu-plan"])
    seen = []

    def gh(path):
        seen.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._env_protection_warnings(_ctx()) == []
    assert f"repos/{_REPO}/environments/dev-eu-apply" not in seen


def test_env_protection_reads_nothing_when_no_environment_was_declared(monkeypatch):
    """With no declared environment set there is nothing to probe, so the listing must not
    be read either: a repository whose token cannot list environments would otherwise
    collect a "could not verify the env protection settings" degrade for a probe with no
    work to do, beside `_environment_warnings`' (correct) skipped statement."""

    def gh(path):
        pytest.fail(f"the env protection probe hit the API with no envs: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._env_protection_warnings(_ctx(envs=set())) == []


def test_env_protection_unreadable_existing_env_is_a_notice_naming_it(monkeypatch):
    """`ec.gh_json`'s exception carries `gh`'s stderr as free text doctor never parses, so a
    403 or 5xx on an environment that IS in the listing is indistinguishable from a 404;
    swallowing it lets the report say the settings probes found no problems. Listing first
    separates the two: present-but-unreadable is a note that names the environment."""
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
    `shipmate-engine` appears in the environments listing -- the only thing existence is
    decided from -- independently of `env`/`policies`, which answer the per-environment
    reads that run only once existence is established."""

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
    """The headline case: `shipmate-engine` absent from the environments listing itself,
    never a per-environment-read failure standing in for absence -- `ec.gh_json`'s
    exception carries `gh`'s stderr as free text doctor never parses, so doctor cannot tell
    that apart from a 403 or a 5xx on an environment that does exist."""
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
    # not necessarily only the default branch -- so it can't be confirmed as
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
    # The probe must confirm the default branch is the ONLY policy entry: a policy naming
    # `main` plus a leftover branch -- what a one-off allow-list forgotten in place
    # produces -- still lets a workflow on that leftover branch read the App private key.
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
        # `shipmate-engine` is deliberately absent from the listing.
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


#: The head of a workflow file whose one job runs the steps a test appends, one
#: `      - uses: <value>` line each.
_STEPS = "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"


def _doc(text):
    """The parsed workflow `text`, as the `shipmate.yml` probe hands it to each finder."""
    doc, finding = doctor._workflow_doc(text, "shipmate.yml")
    assert finding is None, finding
    return doc


_SHA = "a" * 40
_OTHER_SHA = "b" * 40

#: The consumer workflow file `_quiet_new_probes()` serves, in the shipped shape, and the
#: canonical fixture wherever a correct consumer `shipmate.yml` is needed: five jobs, one per
#: engine reusable workflow, each with the `if:` that routes its event. Hand-written rather
#: than read from the page, so a drifting page reddens the fence guard and not every test
#: here. Defined here rather than beside that fixture because interpolating `_SHA` happens at
#: import time, while the fixture's own body is evaluated only when a test calls it.
_SHIPMATE_WF = (
    "name: shipmate\n"
    "on:\n"
    "  pull_request_target:\n"
    "    types: [opened, synchronize, reopened, ready_for_review]\n"
    "  issue_comment:\n"
    "    types: [created]\n"
    "  push:\n"
    "    branches: [main]\n"
    "  workflow_dispatch:\n"
    "    inputs:\n"
    "      verb:\n"
    "        description: What to run (plan, apply or unlock)\n"
    "        type: choice\n"
    "        options: [plan, apply, unlock]\n"
    "        required: true\n"
    "      environment:\n"
    "        description: Target environment\n"
    "        required: false\n"
    "        default: ''\n"
    "      ref:\n"
    "        description: PR head SHA\n"
    "        required: false\n"
    "        default: ''\n"
    "      pr_number:\n"
    "        description: Pull request number\n"
    "        required: false\n"
    "        default: ''\n"
    "permissions: {}\n"
    "jobs:\n"
    "  plan:\n"
    "    name: shipmate\n"
    "    if: github.event_name == 'pull_request_target' || (github.event_name == "
    "'workflow_dispatch' && github.event.inputs.verb == 'plan')\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/plan.yml@{_SHA}\n"
    "    permissions: { contents: read, pull-requests: read, id-token: write }\n"
    "    secrets:\n"
    "      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}\n"
    "      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}\n"
    "  comment-ops:\n"
    "    name: shipmate\n"
    "    if: github.event_name == 'issue_comment'\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/comment-ops.yml@{_SHA}\n"
    "  deploy:\n"
    "    name: post-merge\n"
    "    if: github.event_name == 'push'\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/deploy.yml@{_SHA}\n"
    "  apply:\n"
    "    if: github.event_name == 'workflow_dispatch' && inputs.verb == 'apply'\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/apply.yml@{_SHA}\n"
    "  unlock:\n"
    "    if: github.event_name == 'workflow_dispatch' && inputs.verb == 'unlock'\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/unlock.yml@{_SHA}\n"
)
#: The drift workflow file `_quiet_new_probes()` serves beside `_SHIPMATE_WF`, in the shipped
#: shape: pinned at `_SHA` like the rest, so the pin probe stays quiet over it too.
_SHIPMATE_DRIFT_WF = (
    "name: shipmate drift\n"
    "on:\n"
    "  schedule:\n"
    '    - cron: "17 3 * * *"\n'
    "  workflow_dispatch:\n"
    "permissions: {}\n"
    "jobs:\n"
    "  drift:\n"
    "    name: shipmate\n"
    f"    uses: {_ENGINE_REPO}/.github/workflows/drift.yml@{_SHA}\n"
)


def test_tag_pin_warned(monkeypatch):
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            _STEPS + "      - uses: acme/engine/actions/setup@v2\n"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "tag or branch" in out[0][1] and "acme/engine@v2" in out[0][1]


def test_a_pin_counts_under_a_job_and_a_step_and_not_in_a_comment(monkeypatch):
    """A job's `uses:` (a reusable workflow) and a step's (an action) each pin the engine; a
    commented-out line pins nothing.

    Mutations: have `_call_sites` yield only the jobs -- the `v2` step pin is missed; only
    the steps -- the `v1` job pin is missed."""
    text = (
        "on: push\njobs:\n"
        "  call:\n    uses: acme/engine/.github/workflows/plan.yml@v1\n"
        "  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      # - uses: acme/engine/actions/setup@v3\n"
        "      - uses: acme/engine/actions/setup@v2\n"
    )
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(text),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert [t.split("`")[3] for _, t in doctor._pin_warnings(_ctx())] == [
        "acme/engine@v1",
        "acme/engine@v2",
    ]


def test_quoted_tag_pin_warned(monkeypatch):
    # Some YAML formatters quote the `uses:` value -- the anchor must not
    # make those pins invisible to the probe.
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            _STEPS + '      - uses: "acme/engine/actions/setup@v2"\n'
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "tag or branch" in out[0][1] and "acme/engine@v2" in out[0][1]


def test_non_file_workflow_entry_skipped(monkeypatch):
    """A directory (or symlink/submodule) whose name ends in `.yml` is not a workflow
    file: its "contents" read returns a list, not a blob. The probe SKIPS it and keeps
    scanning, because it stops at the first unreadable FILE -- treating a non-file entry
    as unreadable aborts the whole directory and drops every pin finding after it."""
    responses = {
        f"{_WF_DIR}{_REF}": [
            {"name": "sub.yml", "type": "dir"},
            {"name": "plan.yml", "type": "file"},
        ],
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            _STEPS + "      - uses: acme/engine/actions/setup@v2\n"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "tag or branch" in out[0][1] and "acme/engine@v2" in out[0][1]


def test_stale_sha_pin_warned(monkeypatch):
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            _STEPS + f"      - uses: acme/engine/actions/setup@{_SHA}\n"
        ),
        "repos/acme/engine/releases/latest": {"tag_name": "v1.4.0"},
        "repos/acme/engine/commits/v1.4.0": {"sha": _OTHER_SHA},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    assert "v1.4.0" in out[0][1] and _SHA[:7] in out[0][1]


def test_a_stale_engine_workflow_pin_is_warned(monkeypatch):
    r"""A consumer's only pin is the engine reusable workflow each job calls -- a
    `.github/workflows/` path, not an `actions/` one -- so every consumer's engine pin
    travels that arm of `_PIN`. Every
    other fixture that produces a pin finding pins an `actions/` path.

    Mutation: drop `|\.github` from `_PIN`. The suite stays green while doctor stops reporting a
    stale engine pin for every consumer there is.
    """
    responses = {
        **_fork_responses({"shipmate.yml": _SHIPMATE_WF}),
        f"repos/{_ENGINE_REPO}/releases/latest": {"tag_name": "v1.4.0"},
        f"repos/{_ENGINE_REPO}/commits/v1.4.0": {"sha": _OTHER_SHA},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1, out
    assert out[0][0] == doctor.WARNING
    assert "v1.4.0" in out[0][1] and _SHA[:7] in out[0][1]


def test_current_sha_pin_silent(monkeypatch):
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml", "notes.md"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            _STEPS + f"      - uses: acme/engine/actions/setup@{_SHA}\n"
            f"      - uses: acme/engine/actions/summary@{_SHA}\n"
        ),
        "repos/acme/engine/releases/latest": {"tag_name": "v1.4.0"},
        "repos/acme/engine/commits/v1.4.0": {"sha": _SHA},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._pin_warnings(_ctx()) == []


def test_pin_probe_ignores_another_orgs_shared_action(monkeypatch):
    """Findings worded about the engine ("a moving ref lets the engine change under your deploy
    credentials", "re-pin to pick up fixes") are true only of shipmate's own repository. Another
    org's shared action reported as a stale engine pin, once per workflow file, also exhausts
    GitHub's 10-warning-per-step annotation budget, hiding the real findings."""
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            _STEPS + "      - uses: other/shared/actions/lint@v1\n"
            "      - uses: other/shared/.github/actions/scan@main\n"
            f"      - uses: {_ENGINE_REPO}/actions/setup@{_SHA}\n"
        ),
        f"repos/{_ENGINE_REPO}/releases/latest": {"tag_name": "v1.4.0"},
        f"repos/{_ENGINE_REPO}/commits/v1.4.0": {"sha": _OTHER_SHA},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._pin_warnings(_ctx())
    # Expect only the engine's own stale pin, not the two tag-pinned third-party
    # references, and no release lookup against `other/shared`, whose absence from
    # `responses` would surface as an extra "could not read" note.
    assert len(out) == 1, out
    assert _ENGINE_REPO in out[0][1]
    assert "other/shared" not in out[0][1]


def test_pin_probe_without_the_engine_repo_degrades_to_a_note(monkeypatch):
    """The slug is empty when the calling step supplied no `SHIPMATE_ENGINE_REPO`. Without it
    the probe cannot tell shipmate's pins from anyone else's, so it
    says pin freshness was not verified instead of falling back to warning about every
    cross-repo pin it can see."""

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
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            _STEPS + f"      - uses: acme/engine/actions/setup@{_SHA}\n"
        ),
    }

    def gh(path):
        if path.startswith("repos/acme/engine/"):
            raise SystemExit("404")
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._pin_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.NOTICE
    assert "not verified" in out[0][1]


def test_missing_workflows_directory_degrades_to_a_note(monkeypatch):
    """A brand-new consumer's first shipmate pull request is the one that ADDS
    `.github/workflows`, so this listing legitimately fails -- as it also does on a 403 or a
    transient 5xx. Being the default outcome of the first-ever `shipmate doctor`, it degrades
    at NOTICE; `warnings()`' generic WARNING renders `::error::` and `gh api` in the body."""

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
    # A per-file read can fail after the listing succeeded: a file deleted between the two
    # calls, a 403, a transient 5xx. The findings already collected must survive with the
    # note appended, not be discarded, and not escalate to the generic degrade.
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("a.yml", "b.yml"),
        f"{_WF_DIR}/a.yml{_REF}": _wf_file(_STEPS + "      - uses: acme/engine/actions/setup@v2\n"),
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
    """A value that is not a 40-char hex SHA must never reach the request path -- the same
    guard `apply-detect` puts on `SHIPMATE_HEAD_SHA`. With no usable commit there is
    nothing to compare a pin against, so the probe declines rather than retargeting the
    `gh api` URL or silently reading the default branch."""

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
            return _wf_file(_STEPS + f"      - uses: acme/engine/actions/setup@{_SHA}\n")
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
    """`head_sha` is empty on the comment path's degrade branch, where the pull request head
    could not be read; the annotate path always supplies a validated 40-hex SHA. A
    default-branch read reports the pin stale on the very change that fixes it, since the bumping
    pull request carries the new SHA only on its own head. Say freshness was not verified."""

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


def test_an_empty_public_token_leaves_gh_token_alone(monkeypatch):
    """A set-but-empty SHIPMATE_PUBLIC_TOKEN is no token: swapping it in would make both
    cross-repo calls authenticate as nobody instead of with the ambient App token.

    Mutation: have `_gh_token` skip only `None` -- this reddens."""
    seen = []

    def gh(path):
        seen.append(os.environ.get("GH_TOKEN"))
        if path == "repos/acme/engine/releases/latest":
            return {"tag_name": "v1.4.0"}
        return {"sha": _SHA}

    monkeypatch.setattr(doctor, "_gh_json", gh)
    monkeypatch.setenv("GH_TOKEN", "app-token")
    monkeypatch.setenv("SHIPMATE_PUBLIC_TOKEN", "")
    assert doctor._latest_release_sha("acme/engine") == ("v1.4.0", _SHA)
    assert seen == ["app-token", "app-token"]


def test_one_line_flattens_and_pins_the_truncation_boundary():
    assert doctor._one_line(" a\nb\tc ") == "a b c"
    assert doctor._one_line("x" * 10, limit=10) == "x" * 10  # At the limit, untouched.
    out = doctor._one_line("x" * 11, limit=10)
    assert len(out) == 10 and out.endswith("…")  # Never longer than `limit`.
    assert len(doctor._one_line("é" * 300, limit=120)) == 120  # Code points, not bytes.


def test_app_permission_ok_silent():
    ctx = _ctx(app_permission_error="")
    assert doctor._app_permission_warnings(ctx) == []


def test_app_permission_failure_warned():
    ctx = _ctx(
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


def _ann(level="warning", title="t", message="m", check="shipmate · plan / shipmate / detect"):
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
        # The shipmate App's own check runs (the apply checks) are kept via `app_id`.
        '{"id": 4, "name": "apply / app / dev-eu", "started_at": "2026-07-26T10:00:00Z", '
        + '"app_slug": "shipmate", "app_id": 999}',
        # Third-party apps are dropped: they are outside the harvest's scope.
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
    assert "- 🟠 gate ruleset is missing" in body
    assert "stale codegen" in body
    assert "shipmate · plan / shipmate / detect" in body
    assert _ctx()["head_sha"][:7] in body
    assert "1281" in body


def test_report_renders_notes_as_info_not_warning():
    body = doctor.render_report([(doctor.NOTICE, "apply env has no protection")], [], _ctx())
    assert "- ⚪ apply env has no protection" in body
    assert "- 🟠 apply env has no protection" not in body


def test_report_all_clear():
    body = doctor.render_report([], [], _ctx())
    assert "no problems found" in body
    assert "no warnings" in body


_HINT = "Comment `shipmate help` for the available commands."
_RUN = "in [run #12](https://github.com/o/r/actions/runs/77)"
#: `provenance(_HEAD)` under `_run_context`, hand-written.
_AT = f"at [fffffff](https://github.com/o/r/commit/{'f' * 40}) {_RUN}"
_COVERED = (
    "- 🟢 no problems found by the settings probes. The environment probes covered only the "
    "environments of the stacks changed in this pull request: `dev-eu`."
)


def test_report_with_no_findings_is_the_whole_all_clear_body():
    """Mutation: render the verdict without `provenance` -- `🟢 no problems found` alone.
    Mutation: always show the footer hint in `render_report`'s tail."""
    assert doctor.render_report([], [], _ctx()) == (
        "<!-- shipmate:doctor -->\n"
        "### shipmate doctor\n"
        "\n"
        f"🟢 no problems found {_AT}\n"
        "\n"
        "Cell summaries from plan run ID 1281.\n"
        "\n"
        "#### repository settings\n"
        "\n"
        f"{_COVERED}\n"
        "\n"
        "#### warnings from this commit's workflow runs\n"
        "\n"
        "🟢 no warnings on this commit's workflow runs."
    )


def test_report_with_a_warning_a_notice_and_a_harvested_failure_is_the_whole_body():
    """Mutation: the verdict ignores harvested failures (`harvested.count("failure")` -> 0): it
    reads `🟠 2 warnings, 1 notice`. Mutation: `_LEVEL_EMOJI[WARNING]` -> ⚪. Mutation: `🟠` -> `⚪`
    in `_render_annotation_row`'s warning branch."""
    findings = [
        (doctor.WARNING, "gate ruleset is missing"),
        (doctor.NOTICE, "apply env has no protection"),
    ]
    anns = [
        _ann(level="failure", title="plan failed", message="exit 1", check="app / dev-eu"),
        _ann(level="warning", title="provider", message="deprecated", check="app / dev-eu"),
    ]
    assert doctor.render_report(findings, anns, _ctx()) == (
        "<!-- shipmate:doctor -->\n"
        "### shipmate doctor\n"
        "\n"
        f"🔴 1 error, 2 warnings, 1 notice {_AT}\n"
        "\n"
        "Cell summaries from plan run ID 1281.\n"
        "\n"
        "#### repository settings\n"
        "\n"
        "- 🟠 gate ruleset is missing\n"
        "- ⚪ apply env has no protection\n"
        "\n"
        "#### warnings from this commit's workflow runs\n"
        "\n"
        "- **app / dev-eu**\n"
        "  - 🔴 plan failed: exit 1\n"
        "  - 🟠 provider: deprecated\n"
        "\n"
        f"{_HINT}"
    )


def test_report_with_a_pending_harvest_and_no_findings_is_the_whole_body():
    """Mutation: drop the `harvest incomplete` token: the verdict reads `🟢 no problems found`."""
    assert doctor.render_report([], [], _ctx(harvest_pending=True)) == (
        "<!-- shipmate:doctor -->\n"
        "### shipmate doctor\n"
        "\n"
        f"⚪ harvest incomplete {_AT}\n"
        "\n"
        "Cell summaries from plan run ID 1281.\n"
        "\n"
        "#### repository settings\n"
        "\n"
        f"{_COVERED}\n"
        "\n"
        "#### warnings from this commit's workflow runs\n"
        "\n"
        "- ⚪ some of this commit's workflow runs had not finished when this report was "
        "rendered: warnings they record later are not in this list. Comment `shipmate doctor` "
        "again once they have finished.\n"
        "\n"
        f"{_HINT}"
    )


def test_report_with_one_warning_is_the_whole_body_with_the_hint():
    """Mutation: show the footer hint only under 🔴: this 🟠 report loses its last line."""
    assert doctor.render_report([(doctor.WARNING, "gate ruleset is missing")], [], _ctx()) == (
        "<!-- shipmate:doctor -->\n"
        "### shipmate doctor\n"
        "\n"
        f"🟠 1 warning {_AT}\n"
        "\n"
        "Cell summaries from plan run ID 1281.\n"
        "\n"
        "#### repository settings\n"
        "\n"
        "- 🟠 gate ruleset is missing\n"
        "\n"
        "#### warnings from this commit's workflow runs\n"
        "\n"
        "🟢 no warnings on this commit's workflow runs.\n"
        "\n"
        f"{_HINT}"
    )


@pytest.mark.parametrize(
    ("findings", "levels", "over", "line"),
    [
        ([(doctor.WARNING, "w")], [], {}, f"🟠 1 warning {_AT}"),
        ([(doctor.NOTICE, "n")], ["warning", "warning"], {}, f"🟠 2 warnings, 1 notice {_AT}"),
        (
            [(doctor.NOTICE, "n")],
            [],
            {"harvest_failed": True},
            f"⚪ 1 notice, harvest incomplete {_AT}",
        ),
        (
            [],
            ["failure", "failure"],
            {"head_sha": "unknown"},
            f"🔴 2 errors at an unknown commit {_RUN}",
        ),
    ],
)
def test_the_verdict_counts_and_takes_the_worst_circle(findings, levels, over, line):
    """Mutation: the verdict's warning circle 🟠 -> ⚪ (first two rows). Mutation: drop the
    `harvest_failed` half of the `harvest incomplete` condition (third row). Mutation: `_plural`
    always singular (second and fourth rows)."""
    anns = [_ann(level=lvl, title=f"t{i}") for i, lvl in enumerate(levels)]
    body = doctor.render_report(findings, anns, _ctx(**over))
    assert body.splitlines()[3] == line


def test_the_environment_table_status_counts_toward_nothing_in_the_verdict():
    """Mutation: hand `_verdict` `findings + list(status)`: it reads `⚪ 1 notice`."""
    body = doctor.render_report([], [], _ctx(), [(doctor.NOTICE, doctor.CONFIG_VALID)])
    assert body.splitlines()[3] == f"🟢 no problems found {_AT}"


def test_the_findings_only_fallback_keeps_the_verdict_and_the_footer():
    """Mutation: give `_findings_only_report` an empty verdict. Mutation: drop its footer
    hint."""
    findings = [(doctor.WARNING, "x" * 200) for _ in range(400)]
    lines = doctor.render_report(findings, [], _ctx()).splitlines()
    assert "#### warnings from this commit's workflow runs" not in lines  # The fallback fired.
    assert lines[3] == f"🟠 400 warnings {_AT}"
    assert lines[-2:] == ["", _HINT]


def test_the_harvest_budget_leaves_room_for_the_footer():
    """Everything but the omitted-count note fits `sc.SIZE_BUDGET`. Short rows keep the slack a
    dropped row leaves below the footer's length. Mutation: drop `tail` from the harvest budget's
    subtraction."""
    anns = [_ann(title="t", message="m", check=f"c{i}") for i in range(5000)]
    lines = doctor.render_report([], anns, _ctx()).splitlines()
    kept = [ln for ln in lines if not ln.startswith("- _") or "omitted" not in ln]
    assert len(kept) == len(lines) - 1
    assert lines[-1] == _HINT
    assert len("\n".join(kept)) <= doctor.sc.SIZE_BUDGET


def test_all_clear_names_the_environments_the_probes_actually_covered():
    """All three environment probes -- existence, protection shape and plan-environment
    secrets -- see only the environments of the stacks this pull request changed, since the
    declared set comes from the plan matrix's cell summaries. A categorical "no problems
    found" overclaims. Name the set instead of implying the repository is sound."""
    body = doctor.render_report([], [], _ctx(envs={"dev-eu", "dev-us"}))
    assert "no problems found by the settings probes" in body
    assert "`dev-eu`" in body and "`dev-us`" in body
    assert "changed in this pull request" in body


def test_all_clear_says_when_no_environments_were_probed():
    body = doctor.render_report([], [], _ctx(envs=set()))
    assert "No environments were probed" in body


def test_all_clear_escapes_and_bounds_the_environment_names():
    # `environment` is read from cell.json, so it is repository data: a name carrying the
    # plan comment's marker must not hijack this comment's identity, and a large fan-out's
    # env list must not blow the size budget in a line no truncation path covers.
    body = doctor.render_report([], [], _ctx(envs={"<!-- shipmate:summary -->"}))
    assert "<!-- shipmate:summary -->" not in body
    assert body.count(doctor.DOCTOR_MARKER) == 1
    wide = doctor.render_report([], [], _ctx(envs={f"env-{i:04}" for i in range(500)}))
    assert len(wide) <= doctor.sc.HARD_CAP
    line = next(ln for ln in wide.splitlines() if "no problems found" in ln)
    assert len(line) < 600


def test_all_clear_names_ten_environments_then_a_count():
    """Bounded by name count, so no code span is cut.

    Mutation: drop the slice in `_code_spans` -- all twelve are named. Mutation: restore the
    `_one_line(..., 400)` cut in `_all_clear_line` -- the count suffix is missing."""
    shown = ", ".join(f"`env-{i:02}`" for i in range(10)) + " and 2 more"
    assert doctor._all_clear_line(_ctx(envs={f"env-{i:02}" for i in range(12)})) == (
        "- 🟢 no problems found by the settings probes. The environment "
        "probes covered only the environments of the stacks changed in this pull request: "
        f"{shown}."
    )


def test_findings_only_fallback_uses_the_same_all_clear_line():
    # Two renderers emit the all-clear; the scope statement must not live in
    # only one of them.
    body = doctor._findings_only_report([], _ctx(envs={"dev-eu"}), "🟢", "🟢 no problems found")
    assert "`dev-eu`" in body
    assert "changed in this pull request" in body


def test_harvest_incomplete_note_says_the_harvest_is_incomplete():
    """The other harvest tests assert `HARVEST_INCOMPLETE in body`, pinning the flag-to-note
    wiring but not the note's meaning: swap the constant for an all-clear and they stay green.
    Pinned here on a stable stem -- the warning level, and a phrase unreadable as "everything
    is fine" -- not the whole sentence, so ordinary rewording stays cheap."""
    assert doctor.HARVEST_INCOMPLETE.startswith("- ⚪")
    assert "could not read all" in doctor.HARVEST_INCOMPLETE
    assert "🟢" not in doctor.HARVEST_INCOMPLETE


def test_report_states_when_the_whole_harvest_failed():
    # An empty harvest from a failed check-runs listing must not read the
    # same as an empty harvest from a genuinely clean commit -- the report
    # must say the harvest itself could not be read, not claim all-clear.
    body = doctor.render_report([], [], _ctx(harvest_failed=True))
    assert doctor.HARVEST_INCOMPLETE in body
    assert "no warnings on this commit's workflow runs" not in body


def test_report_states_when_a_partial_harvest_still_found_warnings():
    # The incompleteness note must be additive, not an alternative to the rows: one check
    # run's annotations fetch can fail while the others return warnings, and a note
    # emitted only for an empty harvest lets the rows read as the commit's complete set.
    body = doctor.render_report([], [_ann(title="real warning")], _ctx(harvest_failed=True))
    assert doctor.HARVEST_INCOMPLETE in body
    assert "real warning" in body
    assert "shipmate · plan / shipmate / detect" in body


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
    """An empty harvest cannot tell "annotations not recorded yet" from "no warnings", so
    `shipmate doctor` on a queued or in-flight plan run printed an all-clear nothing
    refreshed. Keyed on every shipmate-relevant check run, not the subset
    `latest_check_ids` ranks: a queued run has no `started_at`, so it sorts below a
    completed run of the same name and the still-running case goes unreported."""
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


_QUEUED_APP_APPLY = (
    '{"id": 3, "name": "apply / app / dev-eu", "app_slug": "shipmate", "app_id": 999, '
    '"status": "queued"}'
)


def test_harvest_pending_ignores_the_queued_app_apply_checks():
    """The App creates each changed cell's `apply / ` check queued until it is applied, so
    counting it reported every pull request with an unapplied cell as unfinished.

    Mutation: drop the `apply / ` skip -- True."""
    lines = ['{"id": 1, "name": "app / dev-eu", "started_at": "t", ' + _COMPLETED]
    assert doctor.harvest_pending([*lines, _QUEUED_APP_APPLY], app_id=_APP_ID) is False


def test_harvest_pending_counts_a_queued_github_actions_apply_job():
    """A `github-actions` apply job is queued only while an apply runs.

    Mutation: skip every run with `apply / ` anywhere in its name, whoever authored it --
    False."""
    lines = [
        '{"id": 1, "name": "app / dev-eu", "started_at": "t", ' + _COMPLETED,
        _QUEUED_APP_APPLY,
        '{"id": 4, "name": "wave0 / apply / app / dev-eu", "app_slug": "github-actions", '
        '"status": "queued"}',
    ]
    assert doctor.harvest_pending(lines, app_id=_APP_ID) is True


def test_harvest_pending_counts_a_queued_app_run_outside_the_apply_checks():
    """Mutation: skip every App-authored run -- False."""
    lines = [
        '{"id": 1, "name": "app / dev-eu", "started_at": "t", ' + _COMPLETED,
        '{"id": 6, "name": "db / dev-eu", "app_slug": "shipmate", "app_id": 999, '
        '"status": "queued"}',
    ]
    assert doctor.harvest_pending(lines, app_id=_APP_ID) is True


def test_harvest_pending_counts_a_queued_run_ranked_below_a_completed_one():
    """A queued run has no `started_at`, so newest-per-name ranks it below a completed run
    of the same name.

    Mutation: key the flag on the newest run per name, as `latest_check_ids` ranks -- False."""
    lines = [
        '{"id": 1, "name": "app / dev-eu", "started_at": "t", ' + _COMPLETED,
        '{"id": 2, "name": "app / dev-eu", "app_slug": "github-actions", "status": "queued"}',
    ]
    assert doctor.harvest_pending(lines, app_id=_APP_ID) is True


def test_check_ids_mode_writes_the_harvest_pending_step_output(monkeypatch, tmp_path, capsys):
    """The reduction already reads every check run on the commit, so it also decides the
    pending flag, which reaches the render step as the gather step's output. The TSV on
    stdout must stay exactly (id, name) pairs: `load_annotations` splits each line on the
    first tab and would otherwise fold the flag into a check name."""
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
    assert "shipmate doctor" in doctor.HARVEST_PENDING  # It tells the reader what to do.
    assert "🟢" not in doctor.HARVEST_PENDING
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
    keyed on `envs` -- so the preamble and the finding can neither
    repeat it nor disagree about it."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu-plan", "dev-eu-apply"))
    findings = doctor._environment_warnings(_ctx(envs=set()))
    body = doctor.render_report(findings, [], _ctx(plan_run_ids=[]))
    assert "environment probes were skipped" in body
    assert body.count("environment probes were skipped") == 1


def test_plan_runs_line_and_probe_coverage_can_disagree_without_contradicting(monkeypatch):
    """The id set is written from the plan records on the head's apply checks whether or
    not those runs' cell summaries could be downloaded, so a non-empty set with no
    declared environments is a live state. The preamble still names the runs read, and its
    coverage claim still comes from `envs` alone, or it contradicts the next line."""
    monkeypatch.setattr(doctor, "_gh_json", _existence("dev-eu-plan", "dev-eu-apply"))
    findings = doctor._environment_warnings(_ctx(envs=set()))
    body = doctor.render_report(findings, [], _ctx(plan_run_ids=["1281"]))
    assert "Cell summaries from plan run ID 1281." in body
    assert "environment probes were skipped" in body


def test_report_escapes_a_hostile_settings_finding():
    """Settings findings interpolate repository data: a workflow file name reaches the pin
    warning verbatim. A file named `<!-- shipmate:summary -->.yml` would put the PLAN comment's
    upsert marker inside the doctor comment, and `actions/summary`'s marker+Bot upsert would
    then PATCH this comment with the plan body: report destroyed, plan comment orphaned."""
    finding = (doctor.WARNING, "`<!-- shipmate:summary -->.yml` pins `acme/engine@v2` by tag")
    body = doctor.render_report([finding], [], _ctx())
    assert "<!-- shipmate:summary -->" not in body
    assert "&lt;!-- shipmate:summary --&gt;" in body
    assert body.count(doctor.DOCTOR_MARKER) == 1


def test_the_explicit_row_escapes_the_names_and_keeps_the_engine_placeholder():
    """The env names are repository data; `<env>` is the engine's own placeholder, inside a
    code span where an entity shows as written.

    Mutation: escape `_Rendered` text in `_finding_row` too -- `&lt;env&gt;` shows.
    Mutation: drop the `_md_escape` around the env names -- `a<b` renders raw."""
    [_, (level, text)] = doctor._config_defaults({"environments": {"a<b": {"explicit": True}}})
    assert doctor._finding_row(level, text) == (
        f"- {doctor._LEVEL_EMOJI[doctor.NOTICE]} `explicit: true` on a&lt;b: a bare "
        "`shipmate apply` skips those, and each needs its own `shipmate apply <env>`."
    )


def test_the_shim_job_name_row_keeps_the_engine_placeholders():
    """Mutation: escape `_Rendered` text in `_finding_row` too -- `&lt;stack&gt;` shows."""
    text = _SHIPMATE_WF.replace("    name: shipmate\n", "    name: terraform\n", 1)
    [(level, finding)] = doctor._shim_job_name_finding(_doc(text), "shipmate.yml", _ENGINE_REPO)
    assert doctor._finding_row(level, finding) == (
        f"- {doctor._LEVEL_EMOJI[doctor.WARNING]} `shipmate.yml`'s calling job is not named "
        "`shipmate`. GitHub names a called workflow's check runs `<caller job> / <callee job>`, "
        "so this repository's plan cell checks are not `shipmate / <stack> / <env>`. The plan "
        "runs and the gate is unaffected; what is lost is every `plan` link in the plan "
        "comment, which falls back to the workflow-run page instead of the cell's own check. "
        "Rename the job `shipmate` (docs/getting-started.md)."
    )


def test_findings_only_fallback_escapes_a_hostile_settings_finding():
    # Same escaping on the HARD_CAP fallback path: it renders the findings
    # through _findings_lines, a second renderer that must not bypass it.
    findings = [(doctor.WARNING, "<!-- shipmate:summary -->" + "x" * 200) for _ in range(400)]
    body = doctor.render_report(findings, [], _ctx())
    assert len(body) <= doctor.sc.HARD_CAP
    assert "warnings from this commit's workflow runs" not in body  # The fallback fired.
    assert "<!-- shipmate:summary -->" not in body


def test_plan_runs_line_names_every_run_the_head_recorded():
    """One head's cells can be planned across several runs -- a cell replanned
    after a push is recorded by its own newest apply check -- so the preamble
    names the whole set. Naming only the first would attribute the report to a
    plan run half of it did not come from."""
    text = doctor._plan_runs_line(_ctx(plan_run_ids=["1281", "1290"]))
    assert text == "Cell summaries from plan run IDs 1281, 1290."


def test_plan_runs_line_states_the_run_without_a_coverage_claim():
    """The run branch must name what was read and claim nothing about what the probes did
    with it: that claim belongs to `_environment_warnings`' NOTICE, keyed on
    `envs`. Wording that implies the declared environment set came from these
    runs, or that mentions the probes at all, fails here."""
    text = doctor._plan_runs_line(_ctx(plan_run_ids=["1281"]))
    assert text == "Cell summaries from plan run ID 1281."


def test_plan_runs_line_says_so_when_the_head_recorded_no_plan_run():
    """No apply check on this head carries a plan record -- nothing was planned
    yet, or no record is readable. Naming the absence is
    the whole degrade path: doctor still reports its settings probes."""
    text = doctor._plan_runs_line(_ctx(plan_run_ids=[]))
    assert text == "No plan records on this commit's apply checks."


def test_plan_runs_line_one_lines_an_overlong_plan_run_id():
    # The ids are interpolated verbatim otherwise -- an unbounded value there
    # would make the preamble itself unbounded, defeating the whole report's
    # size budget regardless of the harvest/findings truncation.
    text = doctor._plan_runs_line(_ctx(plan_run_ids=["9" * 200]))
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
    # A payload that parses to a list of non-dict values -- a check run whose annotations
    # endpoint returned something unexpected -- must be skipped row by row and never
    # raise: `load_annotations`'s contract is "never fatal".
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
    # 10 raw warnings on one check, 2 of them doctor's own, which `harvest_sections` drops
    # to leave 8 rows shown. GitHub's cap is already hit on the raw listing, so the note
    # must fire even though the rendered row count is under `ANNOTATION_CAP`.
    anns = [_ann(level="warning", title=doctor.DOCTOR_TITLE) for _ in range(2)]
    anns += [_ann(level="warning", title=f"w{i}") for i in range(8)]
    body = doctor.render_report([], anns, _ctx())
    assert "may be truncated" in body


def test_report_harvest_truncates_to_stay_under_hard_cap_and_notes_dropped_count():
    # The harvest keeps every github-actions check run on the commit, not shipmate's
    # alone: a repository with broad lint/test annotations, or a large fan-out plan with
    # one failure per cell, produces far more content than a single PR comment can hold.
    anns = [_ann(title=f"warning {i}", message="m" * 300, check=f"check-{i}") for i in range(400)]
    body = doctor.render_report([], anns, _ctx())
    assert len(body) <= doctor.sc.HARD_CAP
    assert "omitted" in body


def test_report_findings_alone_stay_under_hard_cap_when_harvest_is_empty():
    # `sc.SIZE_BUDGET` budgets the harvest and nothing budgets the settings-probe
    # findings, so hundreds of long findings -- one per stale pin across many workflow
    # files -- can alone exceed `sc.HARD_CAP`, the 422 the size budget exists to prevent.
    findings = [(doctor.WARNING, "x" * 200) for _ in range(400)]
    body = doctor.render_report(findings, [], _ctx())
    assert len(body) <= doctor.sc.HARD_CAP
    assert "omitted" in body
    # The fallback drops the harvest section entirely.
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
    assert len(header) < 130  # `_one_line(name, 120)` plus the wrapping, never 5000.


def test_harvest_never_emits_a_dangling_section_header():
    # A section whose header fits but whose only row doesn't must not leave
    # a bullet with no children -- both the header and the row are dropped
    # together.
    fits = _ann(check="aaa-fits", title="t", message="m")
    toolong = _ann(check="zzz-toolong", title="t", message="m")
    header_fits = f"- **{doctor._md_escape('aaa-fits')}**"
    row_fits = doctor._render_annotation_row(fits)
    # The budget holds "aaa-fits"'s header and row plus "zzz-toolong"'s header alone, so
    # a budget check measuring only the header would admit "zzz-toolong" while the
    # combined header-plus-first-row check refuses it.
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


def test_doctor_marker_matches_action_upsert():
    # Coupling: the marker doctor embeds must equal the marker the comment-ops action's
    # doctor upsert step greps for. Drift means a new comment every run instead of an
    # edit in place, so the action site must carry the marker and invoke the script.
    src = (ACTIONS / "comment-ops" / "action.yml").read_text(encoding="utf-8")
    assert src.count(doctor.DOCTOR_MARKER) >= 1, "upsert step no longer greps the script's marker"
    assert "scripts/doctor" in src, "comment-ops action no longer calls scripts/doctor"


def _fork_responses(files):
    """Listing + contents for the fork-trigger probe: {filename: text}."""
    responses = {f"{_WF_DIR}{_REF}": _wf_listing(*files)}
    for name, text in files.items():
        responses[f"{_WF_DIR}/{name}{_REF}"] = _wf_file(text)
    return responses


# Each row is one workflow directory, {filename: text}, and the substrings the one WARNING must
# carry: the level and filename matter because the unreadable-directory degrade also returns
# exactly one item, so a length-only assertion passes on a probe that recognised nothing.
_FORK_WARNED = [
    pytest.param(
        {"label.yml": "on:\n  pull_request_target:\n    types: [opened]\n"},
        ["label.yml", "pull_request_target"],
        id="pull_request_target_trigger_warned",
    ),
    # `on: [push, pull_request_target]` is the same trigger written inline; a
    # probe that only recognised the block form would miss it entirely.
    pytest.param(
        {"label.yml": "on: [push, pull_request_target]\n"},
        ["label.yml"],
        id="pull_request_target_in_a_flow_sequence_warned",
    ),
    # `on: pull_request_target` is the legal one-event scalar form -- no block,
    # no sequence, no brackets. The shortest way to declare the trigger must not
    # be the one shape the probe misses.
    pytest.param(
        {"label.yml": "on: pull_request_target\njobs: {}\n"},
        ["label.yml"],
        id="pull_request_target_as_a_single_event_scalar_warned",
    ),
    pytest.param(
        {"label.yml": "on: {pull_request_target: {types: [opened]}}\n"},
        ["label.yml"],
        id="pull_request_target_as_a_flow_mapping_key_warned",
    ),
    pytest.param(
        {"label.yml": "on:\n  - push\n  - pull_request_target\n"},
        ["label.yml"],
        id="pull_request_target_as_a_sequence_item_warned",
    ),
    # A flow sequence is one value however it is wrapped across lines.
    pytest.param(
        {"label.yml": "on: [push,\n     pull_request_target]\n"},
        ["label.yml"],
        id="pull_request_target_in_a_wrapped_flow_sequence_warned",
    ),
    pytest.param(
        {"label.yml": "on: [\n  push,\n  pull_request_target,\n]\njobs: {}\n"},
        ["label.yml"],
        id="pull_request_target_in_a_flow_sequence_with_brackets_on_own_lines_warned",
    ),
    # The `shipmate.yml` exemption is by exact filename and nothing else.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\n    types: [opened]\n",
            "labeler.yml": "on:\n  pull_request_target:\n    types: [opened]\n",
        },
        ["labeler.yml"],
        id="another_workflow_is_still_warned_about_alongside_shipmate_yml",
    ),
    # A key ending in `on` is not the top-level `on:` -- `python-version: 3.12` belongs to
    # `env` -- and reading it as the trigger would leave the real one unreported.
    pytest.param(
        {"label.yml": "env:\n  python-version: 3.12\non:\n  pull_request_target:\n"},
        ["label.yml"],
        id="an_indented_on_is_not_the_top_level_one",
    ),
    # Workflows written by hand put a column-0 comment block right above their events, and
    # it must not read as an empty trigger.
    pytest.param(
        {"label.yml": "on:\n\n# runs at the base ref\n  pull_request_target:\n"},
        ["label.yml"],
        id="a_blank_line_or_a_column_zero_comment_does_not_end_the_on_block",
    ),
    # `shipmate.yml` is exempt from the trigger finding, but it is exactly the file where a
    # fork checkout would be turned on -- so this check must run BEFORE that exemption.
    # Below it, the one workflow that matters reports nothing.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\njobs:\n  detect:\n"
            "    steps:\n      - uses: actions/checkout@v7\n"
            "        with:\n          allow-unsafe-pr-checkout: true\n"
        },
        ["allow-unsafe-pr-checkout", "shipmate.yml"],
        id="unsafe_pr_checkout_in_shipmate_yml_is_warned",
    ),
    # No `pull_request_target` here, so the trigger finding cannot account for the
    # warning: it is this check or nothing. The detection is deliberately trigger-blind,
    # so the message must be true of a `pull_request`-only file, handed no secrets.
    pytest.param(
        {
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - uses: actions/checkout@v7\n"
            '        with:\n          allow-unsafe-pr-checkout: "true"\n'
        },
        ["allow-unsafe-pr-checkout", "`pull_request`"],
        id="unsafe_pr_checkout_in_another_workflow_is_warned",
    ),
    # Unknown, not false: it may evaluate true, and doctor cannot evaluate it.
    pytest.param(
        {
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: ${{ vars.UNSAFE }}\n"
        },
        ["allow-unsafe-pr-checkout"],
        id="unsafe_pr_checkout_as_an_expression_is_warned",
    ),
    # Flow style is not exotic authoring: the engine's own `.github/workflows/apply.yml`
    # writes `with: { fetch-depth: 0, ref: ... }`. Missing it is fail-open on the outermost
    # guard of the plan path.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - uses: actions/checkout@v7\n"
            "        with: { fetch-depth: 0, allow-unsafe-pr-checkout: true }\n",
        },
        ["allow-unsafe-pr-checkout"],
        id="flow_style_unsafe_pr_checkout_is_warned",
    ),
    # A consumer file checks out in more than one job, and this probe's own finding asks for
    # an explicit `false`, so both occurrences coexist in one file routinely. Examining only
    # the first reads the `false` and returns nothing. Both are in ONE file here;
    # `unsafe_pr_checkout_set_to_false_is_silent` uses two files with one each.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\njobs:\n"
            "  detect:\n    steps:\n      - with:\n"
            "          allow-unsafe-pr-checkout: false\n"
            "  plan:\n    steps:\n      - with:\n"
            "          allow-unsafe-pr-checkout: true\n",
        },
        ["allow-unsafe-pr-checkout"],
        id="a_false_in_one_job_does_not_silence_a_true_in_another",
    ),
    # A job calling a reusable workflow hands it inputs through the job's own `with:`, so
    # the input is read there as well as under a step.
    pytest.param(
        {
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n"
            "    uses: acme/tools/.github/workflows/build.yml@v1\n"
            "    with:\n      allow-unsafe-pr-checkout: true\n"
        },
        ["allow-unsafe-pr-checkout", "label.yml"],
        id="unsafe_pr_checkout_in_a_jobs_own_with_is_warned",
    ),
]


@pytest.mark.parametrize(("files", "needles"), _FORK_WARNED)
def test_fork_trigger_warned(monkeypatch, files, needles):
    responses = _fork_responses(files)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING
    for needle in needles:
        assert needle in out[0][1]


_FORK_SILENT = [
    # The same comparison as `quoted_event_name_comparison_is_silent`, folded across lines by
    # a block scalar -- outside the `on:` block either way.
    pytest.param(
        {
            "plan.yml": "on:\n  pull_request:\njobs:\n  a:\n"
            "    if: >-\n      github.event_name ==\n      'pull_request_target'\n"
        },
        id="folded_event_name_comparison_is_silent",
    ),
    pytest.param(
        {
            "plan.yml": "on:\n  pull_request:\njobs:\n  a:\n    steps:\n"
            "      - run: |\n          echo this repo has no pull_request_target trigger\n"
        },
        id="the_word_in_a_run_body_is_silent",
    ),
    # Inside the `on:` block, but nested below the event-name level: it is one
    # input's allowed value, not a trigger.
    pytest.param(
        {
            "ops.yml": "on:\n  workflow_dispatch:\n    inputs:\n      event:\n"
            "        type: choice\n        options:\n          - pull_request_target\n"
        },
        id="a_workflow_dispatch_choice_option_is_silent",
    ),
    pytest.param(
        {"label.yml": "on: pull_request_target_foo\n"},
        id="a_longer_trigger_name_is_not_the_token",
    ),
    # The prefix must not match: `pull_request:` is the ordinary plan trigger
    # and every consumer has one. A probe that fired on it would fire always.
    pytest.param(
        {"shipmate.yml": "on:\n  pull_request:\n    branches: [main]\n"},
        id="plain_pull_request_trigger_is_silent",
    ),
    # These are the lines a careful repository writes *because* it has no such trigger, and
    # reporting them trains readers to ignore the finding. Both shapes the pattern would match
    # are here: a trailing comment, and a commented-out key at the head of a line.
    pytest.param(
        {
            "plan.yml": "on: [pull_request]  # never [pull_request_target]\n",
            "drift.yml": "on:\n  # pull_request_target:  <- deliberately absent\n  schedule:\n",
        },
        id="commented_out_pull_request_target_is_silent",
    ),
    # `github.event_name == 'pull_request_target'` compares against the
    # trigger; it does not declare one.
    pytest.param(
        {
            "plan.yml": "on:\n  pull_request:\njobs:\n  a:\n"
            "    if: github.event_name == 'pull_request_target'\n"
        },
        id="quoted_event_name_comparison_is_silent",
    ),
    # `shipmate.yml` declaring `pull_request_target` IS the shape the engine ships: the job
    # holding the App key is the engine plan workflow's `summary` job, which checks out no
    # consumer content. Warning about it trains readers to ignore the dangerous labeler workflow.
    pytest.param(
        {"shipmate.yml": "on:\n  pull_request_target:\n    types: [opened]\n"},
        id="the_consumer_workflow_file_is_not_warned_about",
    ),
    # The input written out and explicitly disabled is the safe default made
    # visible. Reporting the key regardless of its value would fire on it.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: false\n",
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: 'false'\n",
        },
        id="unsafe_pr_checkout_set_to_false_is_silent",
    ),
    # `False` and `FALSE` are the same legal YAML boolean as `false`, so both are the safe
    # configuration and a case-sensitive comparison reports them. `build-matrix`'s fork
    # refusal normalizes with `.strip().lower()`; two guards in one release may not disagree.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: False\n",
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          allow-unsafe-pr-checkout: FALSE\n",
        },
        id="a_capitalised_false_is_silent",
    ),
    # The other half of recognising flow style: a flow-style `false` is the safest shape a
    # consumer can write.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - uses: actions/checkout@v7\n"
            "        with: { allow-unsafe-pr-checkout: false, fetch-depth: 0 }\n",
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n    steps:\n"
            "      - with: { fetch-depth: 0, allow-unsafe-pr-checkout: false }\n",
        },
        id="a_flow_style_false_is_silent",
    ),
    # The line a careful repository writes *because* it does not have one.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      # never set allow-unsafe-pr-checkout: true\n"
            "      - uses: actions/checkout@v7\n",
        },
        id="commented_out_unsafe_pr_checkout_is_silent",
    ),
    # The value is `false`, not `false  # deliberate`: a finding here would be a false
    # positive on the safest shape a consumer can write.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - with:\n"
            "          allow-unsafe-pr-checkout: false  # deliberate, never true\n",
        },
        id="a_trailing_comment_after_a_false_value_is_silent",
    ),
    # A longer key that happens to end in the same characters is a different input
    # entirely, and reporting it names a line the reader cannot find.
    pytest.param(
        {
            "shipmate.yml": "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
            "      - with:\n          no-allow-unsafe-pr-checkout: true\n",
        },
        id="a_key_merely_ending_in_the_input_name_is_not_reported",
    ),
    pytest.param(
        {
            "label.yml": "on:\n  pull_request:\njobs:\n  x:\n"
            "    uses: acme/tools/.github/workflows/build.yml@v1\n"
            "    with:\n      allow-unsafe-pr-checkout: false\n"
        },
        id="a_false_in_a_jobs_own_with_is_silent",
    ),
]


@pytest.mark.parametrize("files", _FORK_SILENT)
def test_fork_trigger_silent(monkeypatch, files):
    responses = _fork_responses(files)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._fork_trigger_warnings(_ctx()) == []


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


def test_a_byte_order_mark_does_not_hide_the_on_key(monkeypatch):
    """A BOM'd workflow carries U+FEFF in front of its `on:`, which the parser skips: the
    key must not read as U+FEFF + `on`, silence over a real `pull_request_target`.

    Documents the parser's behaviour, no mutation: doctor strips nothing."""
    responses = _fork_responses({"label.yml": ""})
    responses[f"{_WF_DIR}/label.yml{_REF}"] = _wf_bytes(
        b"\xef\xbb\xbfon:\n  pull_request_target:\n"
    )
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING


def test_an_invalid_utf8_byte_does_not_abort_the_scan(monkeypatch):
    """`errors="replace"`, both halves. A lossy-encoded workflow is not valid UTF-8: a
    strict decode raises out of the probe instead of reporting the trigger in the same
    file, and an `ignore` decode drops the bad byte, splicing `pull_request_targ<0xe9>et`
    back into this probe's own token and manufacturing a warning. U+FFFD keeps it broken."""
    responses = _fork_responses({"label.yml": ""})
    responses[f"{_WF_DIR}/label.yml{_REF}"] = _wf_bytes(b"# caf\xe9\non:\n  pull_request_target:\n")
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor._fork_trigger_warnings(_ctx())
    assert len(out) == 1
    assert out[0][0] == doctor.WARNING

    responses[f"{_WF_DIR}/label.yml{_REF}"] = _wf_bytes(b"on: [pull_request_targ\xe9et]\n")
    assert doctor._fork_trigger_warnings(_ctx()) == []


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


# The finding's whole text, hand-written and never derived from `doctor`: `shipmate` is
# spelled out here, so renaming `doctor._SHIM_JOB_NAME` reddens this rather than following it.
_WRONG_JOB_NAME_TEXT = (
    "`shipmate.yml`'s calling job is not named `shipmate`. GitHub names a called workflow's check "
    "runs `<caller job> / <callee job>`, so this repository's plan cell checks are not "
    "`shipmate / <stack> / <env>`. The plan runs and the gate is unaffected; what is lost is "
    "every `plan` link in the plan comment, which falls back to the workflow-run page instead "
    "of the cell's own check. Rename the job `shipmate` (docs/getting-started.md)."
)


def test_a_shim_whose_job_carries_the_contract_name_is_silent():
    assert doctor._shim_job_name_finding(_doc(_SHIPMATE_WF), "shipmate.yml", _ENGINE_REPO) == []


def test_a_shim_whose_job_is_named_something_else_is_reported():
    """Mutation: `_shim_job_name_finding` returning [] unconditionally. This test passes
    vacuously if the probe reports nothing for everything, so it is paired with the silent
    case above."""
    text = _SHIPMATE_WF.replace("    name: shipmate\n", "    name: terraform\n", 1)
    assert doctor._shim_job_name_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == [
        (doctor.WARNING, _WRONG_JOB_NAME_TEXT)
    ]


def test_a_shim_with_no_job_name_takes_the_job_id_and_is_silent():
    """GitHub uses the job id as the display name when `name:` is absent, so
    `jobs: { shipmate: { uses: ... } }` produces the same check names. Mutation: read the
    job id as unnamed and this documented-equivalent shape is reported."""
    text = _SHIPMATE_WF.replace("  plan:\n", "  shipmate:\n", 1).replace(
        "    name: shipmate\n", "", 1
    )
    assert doctor._shim_job_name_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == []


def test_a_job_name_beats_a_job_id_that_is_the_contract_name():
    """GitHub displays `name:` when there is one, so the id is read only in its absence. The
    uncovered shape is a consumer who copies the documented file and edits the display name
    alone: job id `shipmate`, `name: terraform`, and the checks become
    `terraform / <stack> / <env>`.

    Mutation: `job_name = <id> if <id> == _SHIM_JOB_NAME else (named[0] if named else <id>)`,
    which every other shape test in this file passes.
    """
    text = _SHIPMATE_WF.replace("  plan:\n", "  shipmate:\n", 1).replace(
        "    name: shipmate\n", "    name: terraform\n", 1
    )
    assert doctor._shim_job_name_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == [
        (doctor.WARNING, _WRONG_JOB_NAME_TEXT)
    ]


def test_a_job_id_that_is_not_the_contract_name_is_reported():
    """The other half of the job-id fallback: with no `name:` the id IS the display name, so
    an id that is not `shipmate` produces the wrong check names."""
    text = _SHIPMATE_WF.replace("    name: shipmate\n", "", 1)
    assert doctor._shim_job_name_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == [
        (doctor.WARNING, _WRONG_JOB_NAME_TEXT)
    ]


def test_a_name_deeper_in_the_block_is_not_the_jobs_name():
    """Only the job's own `name:` is its name. A `name:` under `with:` is an input, and
    taking it would silence the finding for a job called something else entirely.

    Mutation: `job.get("name") or _mapping(job.get("with")).get("name") or job_id` in
    `_shim_job_name_finding`."""
    anchor = "      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}\n"
    text = _SHIPMATE_WF.replace("    name: shipmate\n", "", 1).replace(
        anchor, f"{anchor}    with:\n      name: shipmate\n", 1
    )
    assert doctor._shim_job_name_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == [
        (doctor.WARNING, _WRONG_JOB_NAME_TEXT)
    ]


def test_a_workflow_file_that_calls_no_engine_plan_workflow_is_silent():
    """Nothing to name: the routing finding reports the missing call, and two findings for one
    hole ask the reader's question twice."""
    assert (
        doctor._shim_job_name_finding(
            _doc("on:\n  pull_request_target:\njobs:\n  x:\n"), "shipmate.yml", _ENGINE_REPO
        )
        == []
    )


#: `_SHIPMATE_WF` with a flow sequence that never closes, and the parse NOTICE it earns, whole.
_UNPARSEABLE_WF = _SHIPMATE_WF.replace("  plan:\n", "  plan:\n    on: [ unbalanced\n", 1)
_UNPARSEABLE_TEXT = (
    "`shipmate.yml` could not be parsed as YAML (expected ',' or ']', but got ':' "
    "(line 32, column 9)): doctor skipped its checks."
)


def _healthy_responses(**files):
    """A healthy repository's responses, with each `{name}: text` workflow file replacing or
    joining `_quiet_new_probes()`' two."""
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
        **_quiet_new_probes(),
    }
    names = {"shipmate.yml": None, "shipmate-drift.yml": None, **files}
    responses[f"{_WF_DIR}{_REF}"] = _wf_listing(*names)
    for name, text in files.items():
        responses[f"{_WF_DIR}/{name}{_REF}"] = _wf_file(text)
    return responses


def test_an_unparseable_shipmate_yml_is_one_notice_from_the_parse_probe(monkeypatch):
    """Every probe reading `shipmate.yml` skips it, and the `workflow parse` probe reports it
    once.

    Mutations: have `_scan_workflow_docs` and `_shipmate_yml_warnings` return the parse
    finding of a file that does not parse (four copies); return `[]` from
    `_workflow_parse_warnings` (none)."""
    responses = _healthy_responses(**{"shipmate.yml": _UNPARSEABLE_WF})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.warnings(_ctx()) == [(doctor.NOTICE, _UNPARSEABLE_TEXT)]


def test_an_unparseable_file_does_not_stop_the_scan_of_the_others(monkeypatch):
    """`a.yml` does not parse and is listed first; `b.yml` behind it still draws its pin and
    fork-trigger findings.

    Mutation: `break` at the first file whose doc is None in `_scan_workflow_docs`."""
    b = (
        "on: [pull_request_target]\njobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: acme/engine/actions/setup@v2\n"
    )
    responses = _fork_responses({"a.yml": "on: [ unbalanced\n", "b.yml": b})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    ctx = _ctx()
    [(pin_level, pin)] = doctor._pin_warnings(ctx)
    assert pin_level == doctor.WARNING and pin.startswith("`b.yml` pins `acme/engine@v2`")
    [(fork_level, fork)] = doctor._fork_trigger_warnings(ctx)
    assert fork_level == doctor.WARNING and fork.startswith("`b.yml` triggers on")


def test_a_file_nested_too_deeply_is_one_notice_and_the_others_are_still_checked(monkeypatch):
    """`deep.yml` nests past the recursion limit and is listed first: it gets its one parse
    NOTICE, and `b.yml` behind it still draws its pin finding. The mark depends on the stack
    depth, so the column is matched as a number.

    Mutation: drop the `except RecursionError` in `_shipmate._parse` -- `RecursionError`
    escapes `_workflow_file` and both probes raise."""
    import re

    deep = "on: push\nx: " + "[" * 5000 + "]" * 5000 + "\n"
    b = _STEPS + "      - uses: acme/engine/actions/setup@v2\n"
    responses = {
        **_fork_responses({"deep.yml": deep, "b.yml": b, "shipmate.yml": _SHIPMATE_WF}),
        f"repos/{_ENGINE_REPO}/releases/latest": {"tag_name": "v9.9.9"},
        f"repos/{_ENGINE_REPO}/commits/v9.9.9": {"sha": _SHA},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    ctx = _ctx()
    [(level, notice)] = doctor._workflow_parse_warnings(ctx)
    assert level == doctor.NOTICE
    assert re.fullmatch(
        r"`deep\.yml` could not be parsed as YAML \(the document nests too deeply "
        r"\(line 2, column \d+\)\): doctor skipped its checks\.",
        notice,
    ), notice
    [(pin_level, pin)] = doctor._pin_warnings(ctx)
    assert pin_level == doctor.WARNING and pin.startswith("`b.yml` pins `acme/engine@v2`")


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        ("on: [ unbalanced\n", []),
        ("on: push\njobs:\n  x:\n    runs-on: ubuntu-latest\n", [doctor.DRIFT_FILE_MISSING]),
    ],
    ids=["one_file_does_not_parse", "every_file_parses"],
)
def test_the_drift_file_is_missing_only_when_every_file_parsed(monkeypatch, other, expected):
    """A file that does not parse may be the drift file, so its presence silences the notice.

    Mutation: `return [] if callers else [DRIFT_FILE_MISSING]` in `_drift_file_warnings` --
    the first row reports the file missing."""
    responses = _fork_responses({"shipmate.yml": _SHIPMATE_WF, "other.yml": other})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._drift_file_warnings(_ctx()) == expected


#: Nine nested aliases of nine items, 9**9 leaves when expanded; `*a8` is the whole graph.
_ALIAS_GRAPH = "x-graph:\n  a0: &a0 [x, x, x, x, x, x, x, x, x]\n" + "".join(
    f"  a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]\n" for i in range(1, 9)
)
_GRAPH_JOB = "  graph:\n    runs-on: ubuntu-latest\n    steps:\n"


@pytest.mark.parametrize(
    "place",
    [
        pytest.param(lambda wf: wf, id="unused-key"),
        pytest.param(lambda wf: wf + "  graph:\n    uses: *a8\n", id="job-uses"),
        pytest.param(lambda wf: wf + _GRAPH_JOB + "      - uses: *a8\n", id="step-uses"),
        pytest.param(
            lambda wf: wf.replace("    if: github.event_name == 'push'\n", "    if: *a8\n", 1),
            id="job-if",
        ),
        pytest.param(
            lambda wf: wf.replace("    name: shipmate\n", "    name: *a8\n", 1), id="job-name"
        ),
        pytest.param(
            lambda wf: wf + _GRAPH_JOB + "      - with:\n          allow-unsafe-pr-checkout: *a8\n",
            id="unsafe-checkout",
        ),
    ],
)
def test_an_alias_graph_costs_bounded_time_wherever_it_sits(monkeypatch, request, place):
    """A pull request author controls `shipmate.yml`. A graph under an unused key is never
    read; one at a fixed path a probe reads is never turned into text (`_scalar`). Either
    way every probe finishes in bounded time without degrading, and the unused key changes
    no finding.

    Mutations: walk the whole document in `_workflow_doc` (a recursive leaf count before the
    return) -- `unused-key` takes about a minute; `str(value)` in place of `str(_scalar(value))`
    at one site -- its row runs past the bound: `_engine_calls` (`job-uses`), `_pin_findings`
    (`job-uses`, `step-uses`), `_routing_finding` (`job-if`), `_shim_job_name_finding`
    (`job-name`), `_unsafe_checkout_warnings` (`unsafe-checkout`). Run them under
    `timeout 120`."""
    import time

    unused = request.node.callspec.id == "unused-key"
    text = place(_ALIAS_GRAPH + _SHIPMATE_WF)
    assert ("*a8" in text) != unused, "the row must place the graph at its path"
    responses = _healthy_responses(**{"shipmate.yml": text})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    start = time.monotonic()
    out = doctor.warnings(_ctx())
    assert time.monotonic() - start < 2
    assert not [t for _, t in out if "could not verify" in t], out
    if unused:
        assert out == []


def test_a_runner_without_pyyaml_gets_one_notice_for_every_probe_that_parses(monkeypatch):
    """The runner refusal once, as a NOTICE, in place of the workflow, config and environment
    probes; nothing claims the Python version is at fault, the report's config section
    included.

    Mutations: delete the up-front check in `warnings` -- the four workflow probes and the
    parse probe degrade and the config probe blames the runner's Python; drop
    `_environment_warnings.parses_yaml` -- the environment probes' notice says this runner's
    Python refuses the table."""
    responses = _healthy_responses()
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    monkeypatch.setitem(sys.modules, "yaml", None)
    ctx = _ctx()
    assert doctor.warnings(ctx) == [
        (
            doctor.NOTICE,
            "the engine needs PyYAML for python3 and this runner has none. Install "
            "python3-yaml (Debian or Ubuntu) or PyYAML >= 6 for this runner's python3; "
            "CONTRACT.md section Runner prerequisites lists it.",
        )
    ]
    assert doctor.config_status(ctx) == []


def test_the_shipmate_yml_probe_is_registered_and_runs_its_three_finders_in_order(monkeypatch):
    """An unregistered probe runs nowhere while its finders' unit tests stay green -- assert it
    executes as part of `warnings()`, and that one file read feeds all three finders.

    Mutation: drop any one finder from `_shipmate_yml_warnings` (its finding leaves the list),
    or reorder them."""
    text = _WF_NO_TRIGGER.replace("    name: shipmate\n", "    name: terraform\n", 1).replace(
        " && inputs.verb == 'apply'\n", "\n", 1
    )
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
        **_quiet_new_probes(),
        f"{_WF_DIR}/shipmate.yml{_REF}": _wf_file(text),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.warnings(_ctx()) == [
        (doctor.WARNING, _WRONG_JOB_NAME_TEXT),
        (doctor.WARNING, _NO_TRIGGER_TEXT),
        (doctor.WARNING, _EDITED_IF_TEXT),
    ]


# The consumer `shipmate.yml` shapes the dispatch-wiring finding judges. Each bad one
# isolates ONE finding.
_WF_NO_TRIGGER = (
    _SHIPMATE_WF[: _SHIPMATE_WF.index("  workflow_dispatch:\n")]
    + _SHIPMATE_WF[_SHIPMATE_WF.index("permissions: {}\n") :]
)
# A `with:` line forwarding the number is a `pr_number:` outside the `on:` block, so a
# whole-file search for the key is satisfied by a file that declares no such input.
_WF_NO_PR_NUMBER = _SHIPMATE_WF.replace(
    "      pr_number:\n"
    "        description: Pull request number\n"
    "        required: false\n"
    "        default: ''\n",
    "",
).replace(
    "      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}\n",
    "      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}\n"
    "    with:\n"
    "      pr_number: ${{ github.event.inputs.pr_number }}\n",
    1,
)
_WF_REQUIRED_REF = _SHIPMATE_WF.replace(
    "      ref:\n        description: PR head SHA\n        required: false\n",
    "      ref:\n        description: PR head SHA\n        required: true\n",
    1,
)
# A consumer input of its own, above `verb` and required -- which refuses every commented verb
# with HTTP 422, since no dispatch body carries a value for it.
_WF_REQUIRED_TAGS = _SHIPMATE_WF.replace(
    "      verb:\n",
    "      tags:\n"
    "        description: An input of the consumer's own\n"
    "        required: true\n"
    "      verb:\n",
    1,
)
_WF_SHORT_OPTIONS = _SHIPMATE_WF.replace(
    "        options: [plan, apply, unlock]\n", "        options: [plan, apply]\n", 1
)
# Hand-written, whole, and never derived from `scripts/doctor`: each finding is compared
# in full rather than by substring, so a reworded message is a deliberate edit here and
# the three cannot collapse into one another.
_NO_TRIGGER_TEXT = (
    "`shipmate.yml` declares no `workflow_dispatch` trigger, so every commented verb is "
    "authorized, reacted to with a rocket, and then dispatches nothing: GitHub answers the "
    "dispatch with `Workflow does not have 'workflow_dispatch' trigger`, no run is created, "
    "and the pull request gets a comment saying the dispatch failed and linking the "
    "comment-handling run that holds the error. Add the trigger, with its `verb`, "
    "`environment`, `ref` and `pr_number` inputs (docs/getting-started.md)."
)
_NO_PR_NUMBER_TEXT = (
    "`shipmate.yml`'s `workflow_dispatch` trigger declares no `pr_number` input. "
    "`actions/dispatch` sends one body per verb, and GitHub refuses a body naming an input "
    "the workflow does not declare: `Unexpected inputs provided`, no run created, and the "
    "pull request pointed at the comment-handling run by a comment saying the dispatch "
    "failed. Declare `verb`, `environment`, `ref` and `pr_number` under the trigger's "
    "`inputs:` (docs/getting-started.md)."
)
_REQUIRED_REF_TEXT = (
    "`shipmate.yml` declares the `ref` input `required: true`, but at least one dispatch body "
    "leaves it empty, and GitHub reads an omitted or empty value for a required input as not "
    "provided: `Required input not provided`, HTTP 422 and no run, so one required input "
    "refuses a whole verb. `verb` is the only required one; give the rest `required: false` "
    "and `default: ''` (docs/getting-started.md)."
)
_REQUIRED_TAGS_TEXT = (
    "`shipmate.yml` declares the `tags` input `required: true`, but at least one dispatch body "
    "leaves it empty, and GitHub reads an omitted or empty value for a required input as not "
    "provided: `Required input not provided`, HTTP 422 and no run, so one required input "
    "refuses a whole verb. `verb` is the only required one; give the rest `required: false` "
    "and `default: ''` (docs/getting-started.md)."
)
_SHORT_OPTIONS_TEXT = (
    "`shipmate.yml`'s `verb` input does not offer `[plan, apply, unlock]`, which is "
    "the whole set of verbs this file routes. A missing option is refused at the dispatch "
    "form and at the API, so that verb reaches nothing; an extra one offers a verb no job's "
    "`if:` selects, and its run completes with every job skipped, which reads as success "
    "everywhere. Write `options: [plan, apply, unlock]` (docs/getting-started.md)."
)


def test_a_workflow_file_without_the_dispatch_trigger_is_reported():
    out = doctor._dispatch_wiring_finding(_doc(_WF_NO_TRIGGER), "shipmate.yml")
    assert out == [(doctor.WARNING, _NO_TRIGGER_TEXT)]


def test_a_dispatch_trigger_without_pr_number_is_reported_on_its_own():
    """Its own finding with its own text and its own remedy: the trigger is
    there, so a reader told only "the file cannot be dispatched" would add
    what it already has. Inputs are read from `on.workflow_dispatch.inputs`, and this
    fixture also forwards `pr_number` from a job's `with:`, which declares nothing."""
    out = doctor._dispatch_wiring_finding(_doc(_WF_NO_PR_NUMBER), "shipmate.yml")
    assert out == [(doctor.WARNING, _NO_PR_NUMBER_TEXT)]
    assert _NO_PR_NUMBER_TEXT != _NO_TRIGGER_TEXT


def test_the_dispatch_probe_reports_a_required_input_other_than_verb():
    """`verb` is the only required input the file may declare: every dispatch body omits at
    least one of the other three, and GitHub answers an omitted value for a required input
    with HTTP 422 and no run — which is how every `shipmate unlock` failed while the old
    apply file still required the retired plan-run input.

    Mutation: drop the requiredness finding from `_dispatch_input_findings`, and a file
    that refuses every unlock passes."""
    assert doctor._dispatch_wiring_finding(_doc(_WF_REQUIRED_REF), "shipmate.yml") == [
        (doctor.WARNING, _REQUIRED_REF_TEXT)
    ]


def test_the_dispatch_probe_reports_a_required_input_the_consumer_declared():
    """Requiredness is judged over every input the `on:` block declares, not the four
    `actions/dispatch` names: a consumer may declare inputs of its own, and no dispatch body
    carries a value for one, so a required one answers HTTP 422 to every commented verb.

    Mutation: iterate `_DISPATCH_INPUTS[1:]` instead of `inputs` for requiredness, and a
    consumer's own input is the one shape the probe cannot see."""
    assert doctor._dispatch_wiring_finding(_doc(_WF_REQUIRED_TAGS), "shipmate.yml") == [
        (doctor.WARNING, _REQUIRED_TAGS_TEXT)
    ]


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        ("true", [(doctor.WARNING, _REQUIRED_REF_TEXT)]),
        ("false", []),
        ('"false"', []),
    ],
    ids=["bool-true", "bool-false", "string-false"],
)
def test_only_a_boolean_true_makes_an_input_required(required, expected):
    """Mutation: read `required` by truthiness (`bool(...)` for `is True`) -- the string
    `"false"` is reported required."""
    ref = "      ref:\n        description: PR head SHA\n        required: false\n"
    assert ref in _SHIPMATE_WF
    text = _SHIPMATE_WF.replace(ref, ref.replace("false", required), 1)
    assert doctor._dispatch_wiring_finding(_doc(text), "shipmate.yml") == expected


def test_the_dispatch_probe_reports_a_changed_verb_option_list():
    """The whole option list is compared, not the presence of an `options:` key: a list
    missing `unlock` refuses that verb at the dispatch form and at the API.

    Mutation: check only that `options:` is present."""
    assert doctor._dispatch_wiring_finding(_doc(_WF_SHORT_OPTIONS), "shipmate.yml") == [
        (doctor.WARNING, _SHORT_OPTIONS_TEXT)
    ]


@pytest.mark.parametrize(
    "options",
    [
        "[plan,apply,unlock]",
        "[ plan, apply, unlock ]",
        "[plan, apply,  unlock]",
        "\n          - plan\n          - apply\n          - unlock",
    ],
    ids=["flow-tight", "flow-padded", "flow-uneven", "block"],
)
def test_the_dispatch_probe_accepts_any_spelling_of_the_option_list(options):
    """All four are the same YAML sequence as the fence's, the block-style list included, so
    all four route every verb. A probe that reported them would tell a healthy repository to
    write what its file already means, and the next finding from this probe would be read as
    noise too.

    Mutation: compare the parsed options against the flow string `_VERB_OPTIONS` instead of
    `_VERB_OPTION_LIST` -- every row reddens."""
    assert load_workflow_text(options) == ["plan", "apply", "unlock"], (
        "the fixture must be the same sequence the fence declares, or it proves nothing"
    )
    text = _SHIPMATE_WF.replace(
        "        options: [plan, apply, unlock]\n", f"        options: {options}\n", 1
    )
    assert doctor._dispatch_wiring_finding(_doc(text), "shipmate.yml") == []


def test_a_workflow_file_that_calls_no_engine_plan_workflow_is_reported_once(monkeypatch):
    """The fail-open leg: the published file without its `plan` job is dispatchable, so the
    run starts with nothing in it that plans. The routing finding's zero count is the one
    report; the job-name finder has no call to name.

    Mutation: restore the dispatch finding's own "does not call the engine's plan workflow"
    warning, and the list gains a second entry."""
    fence = _documented_workflow_file()
    text = fence[: fence.index("  plan:\n")] + fence[fence.index("  comment-ops:\n") :]
    responses = _fork_responses({"shipmate.yml": text})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._shipmate_yml_warnings(_ctx(engine_repo=_PUBLISHED_ENGINE)) == [
        (doctor.WARNING, _wrong_count_text("plan.yml", 0))
    ]


def test_a_dispatchable_workflow_file_is_silent():
    assert doctor._dispatch_wiring_finding(_doc(_SHIPMATE_WF), "shipmate.yml") == []


def test_an_input_of_the_consumers_own_is_not_read_as_the_verbs():
    """A consumer may declare inputs beside the four, and one written above `verb` carries
    its own `options:`. The verb list is read from `verb`'s own declaration, so this file
    is healthy.

    Mutation: read `options` from the first input declared (`next(iter(inputs.values()))`),
    and the consumer's list is what the probe pins."""
    text = _SHIPMATE_WF.replace(
        "      verb:\n",
        "      tags:\n"
        "        description: An input of the consumer's own\n"
        "        type: choice\n"
        "        options: [app, platform]\n"
        "        required: false\n"
        "      verb:\n",
        1,
    )
    assert doctor._dispatch_wiring_finding(_doc(text), "shipmate.yml") == []


def test_the_documented_workflow_file_is_dispatchable_and_correctly_named(monkeypatch):
    """The oracle for false positives and for the page: the file consumers paste, verbatim,
    through the probe that reads `shipmate.yml`.

    Mutations: rename the fence's job `name:` away from `shipmate`; delete its
    `workflow_dispatch:` trigger; edit one routing `if:`.
    """
    responses = _fork_responses({"shipmate.yml": _documented_workflow_file()})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._shipmate_yml_warnings(_ctx(engine_repo=_PUBLISHED_ENGINE)) == []


def test_a_flow_style_on_value_is_silent():
    """The whole `on:` value as one flow mapping, which the documented block-style fence
    cannot cover. Because ABSENCE is this probe's finding, an input it fails to read is a
    correctly wired file reported as declaring none.

    Every input is written flow-style too, `verb` first, so `verb`'s `required: true` must
    not read as the three optional ones'.

    Mutations: read every input's `required` from `verb`'s declaration -- the three optional
    inputs are reported required; read `inputs` as `{}` -- all four are reported missing."""
    text = (
        "name: shipmate\n"
        "on: { pull_request_target: {}, workflow_dispatch: { inputs: { verb: { type: choice, "
        "options: [plan, apply, unlock], required: true }, environment: "
        "{ required: false, default: '' }, ref: { required: false, default: '' }, "
        "pr_number: { required: false, default: '' } } } }\n"
        "jobs:\n"
        "  plan:\n"
        "    name: shipmate\n"
        f"    uses: {_ENGINE_REPO}/.github/workflows/plan.yml@{_SHA}\n"
    )
    assert doctor._dispatch_wiring_finding(_doc(text), "shipmate.yml") == []


def test_a_workflow_dispatch_line_under_jobs_does_not_satisfy_the_trigger():
    """The trigger is an event `on:` names: a `workflow_dispatch` key under a job declares
    nothing, and the file reported healthy would leave `shipmate plan` reaching nothing."""
    text = _WF_NO_TRIGGER.replace(
        "  plan:\n", "  plan:\n    env:\n      workflow_dispatch: yes\n", 1
    )
    out = doctor._dispatch_wiring_finding(_doc(text), "shipmate.yml")
    assert out == [(doctor.WARNING, _NO_TRIGGER_TEXT)]


def test_a_failing_shipmate_yml_probe_degrades_naming_the_file(monkeypatch):
    """Mutation: drop the probe's `label` -- the note names "the shipmate yml settings"."""

    def boom(text, name, engine):
        raise RuntimeError("boom")

    responses = _fork_responses({"shipmate.yml": _WF_NO_TRIGGER})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    monkeypatch.setattr(doctor, "_routing_finding", boom)
    monkeypatch.setattr(doctor, "PROBES", (doctor._shipmate_yml_warnings,))
    assert doctor.warnings(_ctx()) == [
        (
            doctor.WARNING,
            "doctor could not verify the `shipmate.yml` settings (boom): probe skipped.",
        )
    ]


def test_a_degraded_probe_logs_the_whole_failure_to_stderr(monkeypatch, capsys):
    """Mutation: drop the `ec.log_failure` call in `warnings` -- stderr is empty and the 403
    appears nowhere; pass it no label -- the probe goes unnamed. The note stays cut at
    ": gh api "."""

    def _pin_warnings(ctx):
        raise SystemExit("::error::command failed (1): gh api x\ngh: HTTP 403")

    monkeypatch.setattr(doctor, "PROBES", (_pin_warnings,))
    assert doctor.warnings(_ctx()) == [
        (
            doctor.WARNING,
            "doctor could not verify the pin settings (command failed (1)): probe skipped.",
        )
    ]
    captured = capsys.readouterr()
    assert captured.err == "pin probe: command failed (1): gh api x\ngh: HTTP 403\n"


# The routing findings, hand-written and whole, never derived from `scripts/doctor` or from
# `ROUTING_IFS`: the expressions are spelled out here, so an edited constant reddens these
# rather than being followed by them.
_EDITED_IF_TEXT = (
    "`shipmate.yml`'s job calling the engine's `apply.yml` is selected by "
    "`github.event_name == 'workflow_dispatch'`, not "
    "`github.event_name == 'workflow_dispatch' && inputs.verb == 'apply'`. A wrong expression "
    "routes a verb nowhere and its dispatched run completes with every job skipped, which "
    "reads as success everywhere (docs/getting-started.md)."
)


def _wrong_count_text(callee, count):
    return (
        f"`shipmate.yml` has {count} jobs calling the engine's `{callee}`: it needs exactly one, "
        "selected by the `if:` that routes its event. With none, the verb or event it serves "
        "reaches nothing and its run completes with every job skipped, which reads as success "
        "everywhere; with more than one, either the work runs twice or one of them is "
        "unreachable (docs/getting-started.md)."
    )


def test_the_routing_table_matches_the_documented_workflow_file():
    """`scripts/doctor`'s routing constants and the fence `docs/getting-started.md` publishes
    are two copies of one table — and `scripts/onboard` renders every consumer's file from
    that fence, so a doctor whose constants drift reports every correctly-onboarded repository
    as mis-routed, or blesses a fence that routes nothing.

    Derived from the fence, compared whole against the hand-written side in `doctor`: one
    entry per engine callee, and each entry's whole `if:`.

    Mutation: change one clause of one `if:` in the fence; change one entry of `ROUTING_IFS`.
    """
    jobs = load_workflow_text(_documented_workflow_file())["jobs"]
    documented = {
        job["uses"].split("/.github/workflows/", 1)[1].split("@", 1)[0]: doctor._normalize_if(
            job.get("if")
        )
        for job in jobs.values()
    }
    expected = {k: doctor._normalize_if(v) for k, v in doctor.ROUTING_IFS.items()}
    assert documented == expected, (
        f"the documented file routes {documented!r}; doctor expects {expected!r}"
    )


def test_the_routing_probe_is_silent_on_the_documented_file():
    """The floor under the three reporting cases below, and the oracle for false positives:
    the file consumers paste, verbatim, through the whole probe.

    Mutation: edit any one of the five `ROUTING_IFS` expressions."""
    text = _documented_workflow_file()
    assert doctor._routing_finding(_doc(text), "shipmate.yml", _PUBLISHED_ENGINE) == []


def test_the_routing_probe_reports_a_job_whose_if_was_edited():
    """One clause dropped from the apply job's `if:` and every dispatched verb, `plan` and
    `unlock` included, starts an apply as well.

    Mutation: compare the found expression as a prefix of the expected one instead of whole,
    and this edit passes."""
    text = _SHIPMATE_WF.replace(" && inputs.verb == 'apply'\n", "\n", 1)
    assert doctor._routing_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == [
        (doctor.WARNING, _EDITED_IF_TEXT)
    ]


def test_the_routing_finding_row_keeps_the_expected_if_intact():
    """The remedy names the whole expected `if:` inside a code span, where an entity shows as
    written, so plan.yml's `||` must reach the report row unescaped.

    Mutation: escape `|` as `&#124;` in `_md_escape` -- the row shows `&#124;&#124;`."""
    text = _SHIPMATE_WF.replace(
        "    if: github.event_name == 'pull_request_target' || (github.event_name == "
        "'workflow_dispatch' && github.event.inputs.verb == 'plan')\n",
        "",
        1,
    )
    [(level, finding)] = doctor._routing_finding(_doc(text), "shipmate.yml", _ENGINE_REPO)
    assert doctor._finding_row(level, finding) == (
        f"- {doctor._LEVEL_EMOJI[doctor.WARNING]} `shipmate.yml`'s job calling the engine's "
        "`plan.yml` declares no `if:`, so it then runs on every one of this file's triggers, so "
        "a `push` starts a plan and an `issue_comment` starts an apply. Add `if: "
        "github.event_name == 'pull_request_target' || (github.event_name == "
        "'workflow_dispatch' && github.event.inputs.verb == 'plan')` (docs/getting-started.md)."
    )


def test_a_folded_if_equal_to_the_expected_expression_is_silent():
    """A `>-` block scalar folds its lines into one expression, here wrapped in `${{ }}`;
    equal once normalised, so the file routes as published.

    Mutation: compare `str(found)` with `expected` without `_normalize_if` -- the `${{ }}`
    wrapper reads as a different expression."""
    text = _SHIPMATE_WF.replace(
        "    if: github.event_name == 'push'\n",
        "    if: >-\n      ${{ github.event_name ==\n      'push' }}\n",
        1,
    )
    assert text != _SHIPMATE_WF
    assert doctor._routing_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == []


def test_the_routing_probe_reports_a_missing_job():
    """A verb whose job is not in the file at all: the dispatch is accepted, every job is
    skipped, and the run is green.

    Mutation: skip a callee the file does not call (`if count == 0: continue`)."""
    text = _SHIPMATE_WF[: _SHIPMATE_WF.index("  unlock:\n")]
    assert doctor._routing_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == [
        (doctor.WARNING, _wrong_count_text("unlock.yml", 0))
    ]


def test_the_routing_probe_reports_two_jobs_calling_one_callee():
    """Two jobs on one callee means one of them is unreachable or the work runs twice, and the
    single-region reader would silently judge only the first.

    Mutation: report only a callee no job calls (`if count == 0`)."""
    text = _SHIPMATE_WF + (
        "  apply-eu:\n"
        "    if: github.event_name == 'workflow_dispatch' && inputs.verb == 'apply'\n"
        f"    uses: {_ENGINE_REPO}/.github/workflows/apply.yml@{_SHA}\n"
    )
    assert doctor._routing_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == [
        (doctor.WARNING, _wrong_count_text("apply.yml", 2))
    ]


def test_another_repositorys_plan_workflow_is_not_routed():
    """A third party's `plan.yml` is not a second job calling the engine's.

    Mutation: drop the repository from `_engine_calls`' pattern -- a count-2 finding."""
    text = _SHIPMATE_WF + "  lint:\n    uses: someorg/tf-tools/.github/workflows/plan.yml@v2\n"
    assert doctor._routing_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == []


def test_the_shipmate_yml_probe_without_the_engine_repo_still_checks_dispatch_wiring(
    monkeypatch,
):
    """With no engine repository the anchored selector matches no call, so the routing finder
    would report every job missing. Dispatch wiring reads no slug and still reports.

    Mutations: remove the empty-engine guard -- five count-0 routing findings; return the
    notice alone -- the dispatch finding leaves the list."""
    responses = _fork_responses({"shipmate.yml": _WF_NO_TRIGGER})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._shipmate_yml_warnings(_ctx(engine_repo="")) == [
        (doctor.WARNING, _NO_TRIGGER_TEXT),
        (
            "notice",
            "the workflow file's job name, event routing and leftover drift call not verified: "
            "the engine repository could not be determined.",
        ),
    ]


_MIXED_CASE_ENGINE = "Acme/Engine"


def test_a_mixed_case_engine_slug_is_routed():
    """GitHub resolves `owner/repo` in `uses:` case-insensitively.

    Mutation: drop `(?i:` from `_engine_slug` -- five count-0 findings."""
    text = _SHIPMATE_WF.replace(f"{_ENGINE_REPO}/", f"{_MIXED_CASE_ENGINE}/")
    assert _MIXED_CASE_ENGINE in text
    assert doctor._routing_finding(_doc(text), "shipmate.yml", _ENGINE_REPO) == []


def test_a_mixed_case_engine_slug_satisfies_the_drift_probe(monkeypatch):
    """Mutation: drop `(?i:` from `_engine_slug` -- the missing-file notice."""
    text = _SHIPMATE_DRIFT_WF.replace(f"{_ENGINE_REPO}/", f"{_MIXED_CASE_ENGINE}/")
    assert _MIXED_CASE_ENGINE in text
    assert _drift_probe(monkeypatch, {"sweeps.yml": text}) == []


def test_a_mixed_case_engine_pin_is_judged(monkeypatch):
    """Mutation: drop `(?i:` from `_engine_slug` -- the pin is skipped as a third party's."""
    responses = {
        f"{_WF_DIR}{_REF}": _wf_listing("plan.yml"),
        f"{_WF_DIR}/plan.yml{_REF}": _wf_file(
            _STEPS + f"      - uses: {_MIXED_CASE_ENGINE}/actions/setup@v2\n"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._pin_warnings(_ctx()) == [
        (
            doctor.WARNING,
            "`plan.yml` pins `Acme/Engine@v2` by tag or branch. Pin a commit SHA (a moving ref "
            "lets the engine change under your deploy credentials).",
        )
    ]


def test_the_shipmate_yml_probe_reads_that_file_alone(monkeypatch):
    """An unreadable workflow file listed before `shipmate.yml` cannot hide it: the probe
    reads the one file by path, never the directory listing or another file.

    Mutation: read it through `_scan_workflow_docs` again, and the result is the unreadable
    notice."""
    responses = {
        **_fork_responses({"aaa.yml": "", "shipmate.yml": _WF_NO_TRIGGER}),
        f"{_WF_DIR}/aaa.yml{_REF}": {"encoding": "none", "content": ""},
    }
    reads = []

    def gh(path):
        reads.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._shipmate_yml_warnings(_ctx()) == [(doctor.WARNING, _NO_TRIGGER_TEXT)]
    assert reads == [f"{_WF_DIR}/shipmate.yml{_REF}"]


def test_a_repository_without_shipmate_yml_draws_the_unreadable_notice(monkeypatch):
    """No file means no verb reaches anything, and the contents read cannot tell a 404 from
    any other failure, so the probe says it could not read the file rather than nothing.

    Mutation: return `[]` when `_workflow_file` reads no text."""
    responses = _fork_responses({"ci.yml": _SHIPMATE_WF})

    def gh(path):
        if path not in responses:
            raise SystemExit(f"::error::command failed (1): gh api {path}")
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._shipmate_yml_warnings(_ctx()) == [doctor.SHIPMATE_YML_UNREADABLE]


# The workflow probes whose two degrades share one shape. Without a commit, a
# default-branch read would report the old state on the very pull request that fixes it, so
# the `gh` stub pins that no read happens at all and a weaker read cannot stand in:
# fork-trigger reports the trigger on the pull request that removes it, and the `shipmate.yml`
# probe the old job name, missing trigger or old expression on the one that fixes it.
_WORKFLOW_DIR_DEGRADES = [
    pytest.param(
        doctor._fork_trigger_warnings,
        doctor.FORK_TRIGGER_NO_COMMIT,
        doctor.FORK_TRIGGER_UNREADABLE,
        id="fork_trigger",
    ),
    pytest.param(
        doctor._shipmate_yml_warnings,
        doctor.SHIPMATE_YML_NO_COMMIT,
        doctor.SHIPMATE_YML_UNREADABLE,
        id="shipmate_yml",
    ),
    pytest.param(
        doctor._drift_file_warnings,
        doctor.DRIFT_FILE_NO_COMMIT,
        doctor.DRIFT_FILE_UNREADABLE,
        id="drift_file",
    ),
]


def test_the_workflow_directory_degrades_read_as_written():
    """The table above compares each probe against doctor's own constants, which one template
    builds, so it cannot see the template itself go wrong. These are hand-written.

    Mutation: swap the two elements `_unverified` returns, or reword either sentence --
    this reddens."""
    assert (doctor.SHIPMATE_YML_UNREADABLE, doctor.SHIPMATE_YML_NO_COMMIT) == (
        (
            "notice",
            "could not read `.github/workflows/shipmate.yml`: the workflow file's job name, "
            "dispatch wiring, event routing and leftover drift call not verified.",
        ),
        (
            "notice",
            "the workflow file's job name, dispatch wiring, event routing and leftover drift "
            "call not verified: the commit under examination could not be determined.",
        ),
    )


@pytest.mark.parametrize(("probe", "no_commit", "_unreadable"), _WORKFLOW_DIR_DEGRADES)
def test_without_a_commit_is_a_note_not_a_read(monkeypatch, probe, no_commit, _unreadable):
    def gh(path):
        pytest.fail(f"{probe.__name__} read the API with no commit: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = probe(_ctx(head_sha=""))
    assert out == [no_commit]
    assert out[0][0] == doctor.NOTICE


@pytest.mark.parametrize(("probe", "_no_commit", "unreadable"), _WORKFLOW_DIR_DEGRADES)
def test_unreadable_directory_degrades_to_a_note(monkeypatch, probe, _no_commit, unreadable):
    def gh(path):
        raise SystemExit(f"::error::command failed (1): gh api {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = probe(_ctx())
    assert out == [unreadable]
    assert out[0][0] == doctor.NOTICE
    assert "::error::" not in out[0][1] and "gh api" not in out[0][1]


#: Hand-written, never derived from `scripts/doctor`.
_NO_DRIFT_FILE = (
    "notice",
    "no workflow file calls the engine's `drift.yml`, so no stack is ever checked for drift. "
    "Add a drift workflow file to check for drift (docs/drift.md).",
)
_DRIFT_FILE_UNREADABLE = (
    "notice",
    "could not read `.github/workflows`: whether a workflow file calls the engine's drift.yml "
    "not verified.",
)


def _drift_probe(monkeypatch, files):
    responses = _fork_responses(files)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    return doctor._drift_file_warnings(_ctx())


def test_a_drift_call_in_a_file_of_any_name_satisfies_the_drift_probe(monkeypatch):
    """The call is found by content: a sweep in `sweeps.yml` is a drift file, and a
    `shipmate-drift.yml` calling only `plan.yml` is not.

    Mutation: match by file name (`"drift" in name`) instead of the `uses:` content."""
    sweeps = _SHIPMATE_DRIFT_WF.replace("name: shipmate drift\n", "name: sweeps\n", 1)
    assert _drift_probe(monkeypatch, {"shipmate.yml": _SHIPMATE_WF, "sweeps.yml": sweeps}) == []
    plan_only = _SHIPMATE_DRIFT_WF.replace("/drift.yml@", "/plan.yml@", 1)
    assert _drift_probe(monkeypatch, {"shipmate-drift.yml": plan_only}) == [_NO_DRIFT_FILE]


def test_another_repositorys_drift_workflow_draws_the_missing_drift_notice(monkeypatch):
    """A third party's `drift.yml` sweeps nothing shipmate reports on.

    Mutation: drop the repository from `_engine_calls`' pattern -- the probe goes silent."""
    other = _SHIPMATE_DRIFT_WF.replace(
        f"{_ENGINE_REPO}/.github/workflows/drift.yml@{_SHA}",
        "someorg/tf-tools/.github/workflows/drift.yml@v2",
    )
    assert "someorg/tf-tools/.github/workflows/drift.yml@v2" in other
    assert _drift_probe(monkeypatch, {"sweeps.yml": other}) == [_NO_DRIFT_FILE]


def test_the_drift_probe_without_the_engine_repo_is_a_note(monkeypatch):
    """With no engine repository the anchored selector matches no call, so a repository that
    does sweep would read as having no drift file.

    Mutation: remove the empty-engine guard -- the false missing-file notice."""
    responses = _fork_responses({"shipmate-drift.yml": _SHIPMATE_DRIFT_WF})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._drift_file_warnings(_ctx(engine_repo="")) == [
        (
            "notice",
            "whether a workflow file calls the engine's drift.yml not verified: the engine "
            "repository could not be determined.",
        )
    ]


def test_a_commented_out_drift_call_draws_the_missing_drift_notice(monkeypatch):
    """A call only inside a `#` comment sweeps nothing. Documents the parser's behaviour, no
    mutation: a comment never reaches the parsed document."""
    commented = _SHIPMATE_DRIFT_WF.replace("    uses: ", "    # uses: ", 1)
    assert "# uses: acme/engine/.github/workflows/drift.yml@" in commented
    assert _drift_probe(monkeypatch, {"shipmate-drift.yml": commented}) == [_NO_DRIFT_FILE]


def test_annotate_mode_emits_the_missing_drift_file_as_a_notice_line(monkeypatch, capsys):
    """Through `main()`, so the plan path's `warned` grep (`^::warning`) never sees it.

    Mutation: restore `DRIFT_FILE_MISSING`'s level to WARNING."""
    responses = _fork_responses({"shipmate.yml": _SHIPMATE_WF})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    monkeypatch.setattr(doctor, "ctx_from_env", _ctx)
    monkeypatch.setattr(doctor, "PROBES", (doctor._drift_file_warnings,))
    monkeypatch.setenv("SHIPMATE_DOCTOR_MODE", "annotate")
    doctor.main()
    assert capsys.readouterr().out == (
        "::notice title=shipmate doctor::no workflow file calls the engine's `drift.yml`, so no "
        "stack is ever checked for drift. Add a drift workflow file to check for drift "
        "(docs/drift.md).\n"
    )


def test_a_failing_drift_probe_degrades_to_a_notice(monkeypatch):
    """The probe's worst finding is a NOTICE, so its degrade is one too.

    Mutation: drop `_drift_file_warnings.degrade_level`."""

    def boom(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "_contents_ref", boom)
    monkeypatch.setattr(doctor, "PROBES", (doctor._drift_file_warnings,))
    assert doctor.warnings(_ctx()) == [
        (
            doctor.NOTICE,
            "doctor could not verify the drift workflow files settings (boom): probe skipped.",
        )
    ]


def test_a_partial_drift_scan_reports_only_the_unreadable_notice(monkeypatch):
    """The scan stops at the unreadable `b.yml`, so the call in `shipmate-drift.yml` is never
    read: absence is unknown, and the missing-file notice would be a false report.

    Mutation: emit the missing-file notice whenever no call was found, beside the unreadable one."""
    responses = {
        **_fork_responses(
            {"a.yml": "name: a\n", "b.yml": "", "shipmate-drift.yml": _SHIPMATE_DRIFT_WF}
        ),
        f"{_WF_DIR}/b.yml{_REF}": {"encoding": "none", "content": ""},
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._drift_file_warnings(_ctx()) == [_DRIFT_FILE_UNREADABLE]


def test_drift_is_not_a_verb_shipmate_yml_offers():
    """Drift runs from its own workflow file, so `shipmate.yml` offers the three dispatched
    verbs and an option list still carrying `drift` offers one no job selects.

    Mutation: re-add `"drift"` to `_VERB_OPTION_LIST` -- both assertions redden."""
    assert doctor._dispatch_wiring_finding(_doc(_SHIPMATE_WF), "shipmate.yml") == []
    old = _SHIPMATE_WF.replace(
        "        options: [plan, apply, unlock]\n",
        "        options: [plan, apply, unlock, drift]\n",
    )
    assert doctor._dispatch_wiring_finding(_doc(old), "shipmate.yml") == [
        (doctor.WARNING, _SHORT_OPTIONS_TEXT)
    ]


def test_shipmate_yml_without_a_drift_job_draws_no_routing_finding():
    """Mutation: re-add the `drift.yml` entry to `ROUTING_IFS` -- a zero-count finding."""
    assert "drift.yml" not in _SHIPMATE_WF
    assert doctor._routing_finding(_doc(_SHIPMATE_WF), "shipmate.yml", _ENGINE_REPO) == []


_SHIPMATE_YML_CALLS_DRIFT_TEXT = (
    "`shipmate.yml` still calls the engine's `drift.yml`: move the drift job to its own "
    "workflow file and delete it, the `schedule` trigger and the `drift` verb option from "
    "`shipmate.yml` (docs/drift.md)."
)


def test_a_drift_job_left_in_shipmate_yml_is_warned(monkeypatch):
    """A `shipmate.yml` still carrying the drift job sweeps beside the drift file, so every
    sweep runs twice. One WARNING names the move; the published file draws none.

    Mutation: drop the drift-call check from `_shipmate_yml_warnings` -- the first assertion
    reddens."""
    old = _SHIPMATE_WF + _SHIPMATE_DRIFT_WF.split("jobs:\n", 1)[1]
    assert old.count("/drift.yml@") == 1
    responses = _fork_responses({"shipmate.yml": old})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._shipmate_yml_warnings(_ctx()) == [
        (doctor.WARNING, _SHIPMATE_YML_CALLS_DRIFT_TEXT)
    ]
    responses = _fork_responses({"shipmate.yml": _SHIPMATE_WF})
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._shipmate_yml_warnings(_ctx()) == []


def test_the_probe_registry_is_exactly_this(monkeypatch):
    """The whole registry against a hand-written list, not its length: a length
    assertion cannot say WHICH entry changed, so a probe swapped for another
    passes it. Order is the order findings are reported in."""
    expected = (
        doctor._gate_rule_warnings,
        doctor._review_rule_warnings,
        doctor._environment_warnings,
        doctor._env_protection_warnings,
        doctor._engine_environment_warnings,
        doctor._plan_env_secret_warnings,
        doctor._workflow_parse_warnings,
        doctor._pin_warnings,
        doctor._fork_trigger_warnings,
        doctor._shipmate_yml_warnings,
        doctor._config_warnings,
        doctor._app_permission_warnings,
        doctor._drift_file_warnings,
    )
    assert expected == doctor.PROBES


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
    # Pin the text, not the constant alone: comparing against the constant leaves
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
    # `required_approving_review_count: 0` is a shipped, supported mode when code-owner
    # review is on: the merge-side control a leaked App key cannot satisfy is still there.
    # Warning every run about a setting nobody will change teaches readers to ignore.
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


_GATED_AND_UNGATED_TABLE = """\
layout: tf_vars

environments:
  dev-eu:
    region: eu-west-1
  sandbox:
    region: eu-west-1
    gated: false
"""
_ALL_UNGATED_TABLE = """\
layout: tf_vars

environments:
  dev-eu:
    region: eu-west-1
    gated: false
"""


def _no_required_review(envs, level=doctor.NOTICE):
    """Hand-written: the count-0 finding naming `envs`, already rendered."""
    return (
        level,
        f"the `pull_request` rule on `{_BRANCH}` requires 0 approving reviews, so "
        f"these gated environments can apply without an approving review: {envs}. One is held "
        "only where a code-owner review is required for the changed files. `gated` can "
        "only relax a review requirement the ruleset sets (docs/hardening.md #3 to #5); set "
        "`required_approving_review_count` to 1 or more, or set `gated: false` on the "
        "environments meant to apply unreviewed.",
    )


def _review_probe(monkeypatch, rules, table, envs=_ENVS):
    responses = _rules_only(*rules)
    if table is not None:
        responses[_CONFIG_ON_DEFAULT] = _wf_file(table)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    return doctor._review_rule_warnings(_ctx(envs=set(envs)))


def test_review_rule_count_zero_names_only_the_gated_environments(monkeypatch):
    """Count 0 with code-owner review on is a notice naming the gated environments only: they
    are still held wherever owned files change.

    Mutation: drop the `ungated_envs` subtraction -- `sandbox` is named too.
    Mutation: emit the gated finding as WARNING unconditionally -- the level differs."""
    rules = [_pull_request_rule(code_owner=True, count=0)]
    out = _review_probe(monkeypatch, rules, _GATED_AND_UNGATED_TABLE)
    assert out == [_no_required_review("`dev-eu`")]


def test_review_rule_count_zero_without_code_owner_review_reports_both(monkeypatch):
    """Code-owner review off and count 0 are two warnings, in that order.

    Mutation: restore the early `return` on `not code_owner` -- the count finding is lost.
    Mutation: emit the gated finding as NOTICE unconditionally -- the level differs."""
    rules = [_pull_request_rule(code_owner=False, count=0)]
    out = _review_probe(monkeypatch, rules, _GATED_AND_UNGATED_TABLE)
    assert out == [
        (doctor.WARNING, doctor._CODE_OWNER_REVIEW_OFF.format(branch=_BRANCH)),
        _no_required_review("`dev-eu`", doctor.WARNING),
    ]


def test_review_rule_count_zero_with_every_environment_ungated_is_the_notice(monkeypatch):
    """No gated environment leaves count 0 the supported sole-maintainer mode.

    Mutation: emit the count finding for an empty gated set -- a warning naming nothing."""
    rules = [_pull_request_rule(code_owner=True, count=0)]
    out = _review_probe(monkeypatch, rules, _ALL_UNGATED_TABLE)
    assert out == [(doctor.NOTICE, doctor._SOLE_MAINTAINER_REVIEW.format(branch=_BRANCH))]


def test_review_rule_count_zero_with_an_unreadable_table_is_the_notice(monkeypatch):
    """An unreadable default-branch table names no environment: which are gated is unknown.

    Mutation: treat an unreadable table as an empty one, so every `ctx["envs"]` entry
    reads as gated -- `dev-eu` is warned about."""

    def gh(path):
        if path == _CONFIG_ON_DEFAULT:
            raise SystemExit("::error::command failed (1): gh api ...")
        return [_pull_request_rule(code_owner=True, count=0)]

    monkeypatch.setattr(doctor, "_gh_json", gh)
    out = doctor._review_rule_warnings(_ctx())
    assert out == [(doctor.NOTICE, doctor._SOLE_MAINTAINER_REVIEW.format(branch=_BRANCH))]


@pytest.mark.parametrize("table", [_INVALID_TABLE, "layout: ["], ids=["invalid", "not-yaml"])
def test_review_rule_count_zero_with_an_invalid_table_is_the_notice(monkeypatch, table):
    """An invalid or unparseable default-branch table degrades like an unreadable one, and
    never raises: `warnings()` would drop the code-owner finding with it.

    Mutation: remove the `except SystemExit` in `_default_branch_table` -- the probe raises."""
    rules = [_pull_request_rule(code_owner=True, count=0)]
    out = _review_probe(monkeypatch, rules, table)
    assert out == [(doctor.NOTICE, doctor._SOLE_MAINTAINER_REVIEW.format(branch=_BRANCH))]


def test_review_rule_count_zero_names_a_declared_environment_the_table_lacks(monkeypatch):
    """An environment only a cell declares is gated: no entry holds `gated: false` for it.

    Mutation: drop the union with `ctx["envs"]` -- `prod-us` is not named."""
    rules = [_pull_request_rule(code_owner=True, count=0)]
    out = _review_probe(monkeypatch, rules, _GATED_AND_UNGATED_TABLE, {"dev-eu", "prod-us"})
    assert out == [_no_required_review("`dev-eu`, `prod-us`")]


def test_review_rule_count_zero_names_at_most_ten_environments(monkeypatch):
    """Twelve gated environments: the first ten by name, then a count, so no code span is cut.

    Mutation: show every name (drop the `[:_ENV_NAME_CAP]` slice) -- all twelve are named.
    Mutation: drop the "and N more" suffix -- the count is missing."""
    envs = {f"env-{i:02}" for i in range(12)}
    rules = [_pull_request_rule(code_owner=True, count=0)]
    out = _review_probe(monkeypatch, rules, _ALL_UNGATED_TABLE, envs)
    shown = ", ".join(f"`env-{i:02}`" for i in range(10)) + " and 2 more"
    assert out == [_no_required_review(shown)]


def test_review_rule_count_zero_escapes_an_environment_name(monkeypatch):
    """The report row escapes the whole finding.

    Mutation: drop `_md_escape` from `_finding_row` -- the `<` renders raw.
    Mutation: escape `|` as `&#124;` in `_md_escape` -- red."""
    rules = [_pull_request_rule(code_owner=True, count=0)]
    [(level, text)] = _review_probe(monkeypatch, rules, _ALL_UNGATED_TABLE, {"a|b<c"})
    assert doctor._finding_row(level, text) == (
        f"- {doctor._LEVEL_EMOJI[doctor.NOTICE]} the `pull_request` rule on `{_BRANCH}` requires "
        "0 approving reviews, so these gated environments can apply without an approving "
        "review: `a|b&lt;c`. One is held only where a code-owner review is required for the "
        "changed files. `gated` can only relax a review requirement the ruleset sets "
        "(docs/hardening.md #3 to #5); set `required_approving_review_count` to 1 or more, or set "
        "`gated: false` on the environments meant to apply unreviewed."
    )


def test_review_rule_count_is_the_highest_across_layered_rulesets(monkeypatch):
    """GitHub enforces the union, so one ruleset requiring a review satisfies the count.

    Mutation: aggregate the count with `min` instead of `max` -- the count finding fires."""
    rules = [
        _pull_request_rule(code_owner=True, count=0),
        _pull_request_rule(code_owner=True, count=1),
    ]
    assert _review_probe(monkeypatch, rules, _GATED_AND_UNGATED_TABLE) == []


_LAST_PUSH_WARNING = (
    doctor.WARNING,
    f"the `pull_request` rule on `{_BRANCH}` requires 0 approving reviews but sets "
    "`require_last_push_approval`, so a sole maintainer cannot merge: the last push needs "
    "an approval from someone other than its pusher. Turn `require_last_push_approval` off "
    "(docs/branch-protection.md §Reproducible ruleset).",
)


def _last_push_rule(code_owner, count):
    rule = _pull_request_rule(code_owner=code_owner, count=count)
    rule["parameters"]["require_last_push_approval"] = True
    return rule


def test_review_rule_count_zero_with_last_push_approval_warns(monkeypatch):
    """Last-push approval at count 0 blocks a sole maintainer whether or not code-owner
    review is on.

    Mutation: warn only when code-owner review is also on -- the warning is missing."""
    out = _review_probe(monkeypatch, [_last_push_rule(False, 0)], _ALL_UNGATED_TABLE)
    assert out == [
        (doctor.WARNING, doctor._CODE_OWNER_REVIEW_OFF.format(branch=_BRANCH)),
        _LAST_PUSH_WARNING,
    ]


def test_review_rule_last_push_approval_replaces_the_sole_maintainer_note(monkeypatch):
    """The sole-maintainer note calls count 0 supported; with last-push on it is not.

    Mutation: keep the sole-maintainer note while last-push fires -- the note is listed."""
    out = _review_probe(monkeypatch, [_last_push_rule(True, 0)], _ALL_UNGATED_TABLE)
    assert out == [_LAST_PUSH_WARNING]
    rules = [_pull_request_rule(code_owner=True, count=0)]
    out = _review_probe(monkeypatch, rules, _ALL_UNGATED_TABLE)
    assert out == [(doctor.NOTICE, doctor._SOLE_MAINTAINER_REVIEW.format(branch=_BRANCH))]


def test_review_rule_last_push_approval_with_a_required_review_is_silent(monkeypatch):
    """At count 1 another person approves the last push anyway.

    Mutation: drop the count check -- the last-push warning fires."""
    assert _review_probe(monkeypatch, [_last_push_rule(True, 1)], _ALL_UNGATED_TABLE) == []


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
    16: "sixteen",
}


def _count_word(n):
    assert n in _COUNT_WORDS, f"the probe count reached {n} -- extend _COUNT_WORDS"
    return _COUNT_WORDS[n]


def test_probe_count_is_stated_correctly_in_the_docs():
    """Adding or removing a probe means editing seven pieces of prose that spell the
    count out. Nothing else notices when they go stale, so this reads all three files
    and pins each against `len(PROBES)`:

    - `scripts/doctor`'s module docstring: (1) "the <n> live probes" and (2) the
      `Probes:` bullet list below it (one bullet per probe);
    - `CONTRACT.md`: (3) "<n> live settings probes" and (4) "<n-1> of the <n>";
    - `docs/troubleshooting.md`: (5) "combining <n>", (6) "<n-1> of the <n> probes"
      and (7) the reader-facing probe list itself, one `- **` bullet per probe.

    The number words come from the count, so this keeps biting when a further probe lands.
    `<n-1>` is the plan-path subset: the App-permission probe cannot report from `annotate`
    mode, because only the comment path attempts the full-permission mint it reports on.

    Mutations, one per claim: change `len(PROBES)`; delete a bullet from the `Probes:`
    docstring list; edit either count phrase in `CONTRACT.md` or in
    `docs/troubleshooting.md`; delete a `- **` bullet from that page's probe list.
    """
    total = len(doctor.PROBES)
    word, plan_word = _count_word(total), _count_word(total - 1)

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
    # The list the two phrases above count, sliced between the sentence that states the
    # count and the paragraph that closes the list.
    listing = trouble.split("live probes.\n", 1)[1].split("\nThe report carries", 1)[0]
    page_bullets = [ln for ln in listing.splitlines() if ln.startswith("- **")]
    assert len(page_bullets) == total, (
        f"docs/troubleshooting.md's probe list names {len(page_bullets)} probes, not {total}"
    )


def test_a_non_file_workflow_entry_does_not_blind_the_fork_trigger_probe(monkeypatch):
    """The pin probe's twin. This probe surfaces the one misconfiguration on docs/hardening.md
    an outside contributor can reach, and it also stops at the first unreadable file, so a
    non-file entry sorting ahead of the real workflows must be skipped. Treated as unreadable
    it reports "could not read" and hides the `pull_request_target` trigger in the next file."""
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


def test_plan_env_holding_secrets_is_a_notice_naming_each(monkeypatch):
    """NOTICE, not WARNING: docs/hardening.md control 8 permits read-only,
    blast-radius-free credentials in a plan environment, and doctor's WARNING
    means misconfiguration. Same posture as the apply-env-without-reviewers
    note."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "dev-eu-apply"),
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
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
    """The sibling `_env_protection_warnings` reports a plan environment that *does* carry
    protection rules, so one report can hold both findings: a NOTICE asserting a plan
    environment cannot have approval rules would contradict it. State control 8's requirement,
    settled without the sibling's protection read, so one failure cannot silence the other."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
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
        _CONFIG_ON_DEFAULT,
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100",
    ]


def test_shared_mode_reads_the_bare_env_and_says_it_is_the_apply_env_too(monkeypatch):
    """In shared mode the plan environment IS the apply environment, so its finding cannot
    be the split one: the credential a consumer put there for applying is reachable by
    plan-time code. Pinned on the requested paths as well as the wording -- wording it as
    a split plan environment understates the exposure."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu", "dev-eu-plan"),
        _CONFIG_ON_DEFAULT: _wf_file(_SHARED_TABLE),
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100": _secrets("AWS_ROLE_ARN"),
    }
    asked = []

    def fake(path):
        asked.append(path)
        return responses[path]

    monkeypatch.setattr(doctor, "_gh_json", fake)
    assert doctor._plan_env_secret_warnings(_ctx()) == [
        (
            doctor.NOTICE,
            "GitHub Environment `dev-eu` (shared between plan and apply by `shared: true` "
            "in its `environments.dev-eu` entry) holds 1 secret(s) (`AWS_ROLE_ARN`). "
            "It is the apply environment too, so a plan cell runs the pull request "
            "branch's own code with everything it releases: any credential held there for "
            "applying is reachable by plan-time code. Protection rules on it would stall those "
            "plan cells, so it cannot be gated either. Treat anything it holds as readable "
            "by anyone who can push a branch, and keep them read-only and blast-radius-free, or "
            "move to OIDC (docs/hardening.md control 8).",
        )
    ]
    # `dev-eu-plan` exists and is not read: the table selects the bare name.
    assert asked == [
        f"repos/{_REPO}/environments?per_page=100",
        _CONFIG_ON_DEFAULT,
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100",
    ]


def test_no_probe_reads_a_repository_variable(monkeypatch):
    """doctor needs no `variables: read` permission -- `app/manifest.json` does not
    declare one, and adding it costs every installation a re-accept. A probe that starts
    reading a repository variable must break here rather than degrade silently. Asserted
    over every path `warnings()` reads."""
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
    """`_APP_KEY_NAME in names` is a membership test over the names actually read, and
    `ec.gh_json` does not multi-page, so on a truncated listing the key can sit outside
    the page and produce silence -- the one configuration no document blesses reading as a
    routine note. Absence is reportable only when the read was complete."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
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
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets("A", "B"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert [lvl for lvl, _ in doctor._plan_env_secret_warnings(_ctx())] == [doctor.NOTICE]


def test_a_truncated_listing_that_did_show_the_app_key_reports_holding_it(monkeypatch):
    """Truncation must not downgrade a positive match to "could not determine":
    the key was read, so the finding is that the environment holds it."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
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
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
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
    assert "plan environment `dev-eu-plan` holds" in found[1][1]


def test_shared_mode_app_key_warning_does_not_call_the_env_a_plan_environment(monkeypatch):
    """The noun follows the ROLE, not the mode. In shared mode the sibling NOTICE
    in the same report calls `dev-eu` shared between both paths, so this warning
    may not call the same environment the plan environment."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu"),
        _CONFIG_ON_DEFAULT: _wf_file(_SHARED_TABLE),
        f"repos/{_REPO}/environments/dev-eu/secrets?per_page=100": _secrets(
            "SHIPMATE_APP_PRIVATE_KEY"
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE, doctor.WARNING]
    assert "plan environment `dev-eu`" not in found[1][1]
    assert "environment `dev-eu` holds `SHIPMATE_APP_PRIVATE_KEY`" in found[1][1]


def test_a_truncated_environments_listing_refuses_to_report_existence(monkeypatch):
    """A partial listing produces a false "does not exist" for every environment past the
    page. So it raises, and `warnings()` degrades every environment probe loudly rather
    than reporting on half the names."""
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


def test_no_declared_env_reads_nothing_and_says_nothing(monkeypatch):
    """A docs-only or pin-bump pull request declares no environment, so there
    was nothing to read."""
    monkeypatch.setattr(doctor, "_gh_json", lambda path: pytest.fail(f"read {path}"))
    assert doctor._plan_env_secret_warnings(_ctx(envs=set())) == []


def test_an_environment_that_does_not_exist_is_never_read(monkeypatch):
    """Existence comes from the environments *listing*, never from the per-environment
    read's exception: it carries `gh`'s stderr as free text doctor never parses, so a 404
    for a declared-but-absent environment is indistinguishable from a 403, and the degrade
    note would claim it exists. Asserted on the paths requested, since a probe that read the
    absent environment and swallowed the error also returns []."""
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
    """The settings section is never truncated, so one environment with many secrets would
    spend the whole of `sc.SIZE_BUDGET` and push every other finding into the
    omitted-findings fallback -- and in annotate mode GitHub cuts the line off with no
    marker. Capped like the all-clear line's environment list. The count is asserted too:
    a cap that understated it would hide secrets, not only their names."""
    names = [f"CONSUMER_CREDENTIAL_NUMBER_{i:03d}" for i in range(60)]
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(*names),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE]
    assert "and 50 more)" in found[0][1], "a capped name list must carry the count of the rest"
    assert len(found[0][1]) < 1000
    # The cap hides names, never the number of them.
    assert "60 secret(s)" in found[0][1]
    assert names[-1] not in found[0][1]


def test_one_env_listing_failure_is_a_warning_and_the_others_still_report(monkeypatch):
    """Per-environment degrade, like `_env_protection_warnings`: one 403 must
    not silence the environment that could be read."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan", "dev-us-plan"),
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
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
    assert [lvl for lvl, _ in found] == [doctor.WARNING, doctor.NOTICE]
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
    """`ec.gh_json` does not multi-page, so an environment with more than 100 secrets
    returns a partial list, and reporting `len(names)` understates it as the whole set.
    The same partial read also warns that the App-key check could not be completed."""
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(
            "A", "B", total=150
        ),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    found = doctor._plan_env_secret_warnings(_ctx())
    assert [lvl for lvl, _ in found] == [doctor.NOTICE, doctor.WARNING]
    assert "at least 150" in found[0][1]


def test_a_long_secret_listing_names_ten_secrets_then_a_count(monkeypatch):
    """Bounded by name count, so no code span is cut; `count` still reports all twelve.

    Mutation: pass every name to the finding (drop the `_code_spans` slice) -- all twelve are
    named. Mutation: restore the `_one_line(..., 400)` cut -- the count suffix is missing."""
    names = [f"SECRET_{i:02}" for i in range(12)]
    responses = {
        f"repos/{_REPO}/environments?per_page=100": _environments("dev-eu-plan"),
        _CONFIG_ON_DEFAULT: _wf_file(CANONICAL),
        f"repos/{_REPO}/environments/dev-eu-plan/secrets?per_page=100": _secrets(*names),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    shown = ", ".join(f"`SECRET_{i:02}`" for i in range(10)) + " and 2 more"
    assert doctor._plan_env_secret_warnings(_ctx())[0] == (
        doctor.NOTICE,
        doctor._secret_finding("dev-eu-plan", "plan", "12", shown),
    )


def test_declared_envs_reads_a_flat_single_artifact_download(tmp_path):
    # Same layout split `pending-checks` has to survive: with exactly one matching artifact
    # the download lands at `<cells>/cell.json` with no per-artifact directory. Insisting on
    # the nested layout empties the declared-env set, skipping every environment probe.
    (tmp_path / "cell.json").write_text(json.dumps({"environment": "dev-eu"}), encoding="utf-8")
    assert doctor._declared_envs(tmp_path) == {"dev-eu"}


def test_declared_envs_reads_every_run_directory_of_comment_ops_download(tmp_path):
    """comment-ops downloads each plan run into its own directory, so a summary sits at
    `<run>/<artifact>/cell.json`, or at `<run>/cell.json` when the run had one artifact.
    Mutation: `glob("*/cell.json")` reads only the flat layout and drops `dev-us`."""
    for path, env in [
        ("1281/cell-summary.dev-eu.stacks-app/cell.json", "dev-eu"),
        ("1290/cell.json", "dev-eu"),
        ("1302/cell-summary.dev-us.stacks-db/cell.json", "dev-us"),
    ]:
        (tmp_path / path).parent.mkdir(parents=True)
        (tmp_path / path).write_text(json.dumps({"environment": env}), encoding="utf-8")
    assert doctor._declared_envs(tmp_path) == {"dev-eu", "dev-us"}


#: A top-level setting indented under an entry, where it parses as a key of that entry.
MISINDENTED_CONTROL = """\
environments:
  prod:
    region: eu-west-1
    layout: folder                 # intended as a top-level setting
"""

#: The refusal the misindented control earns, whole: the probe's own framing plus the
#: missing-layout and `_check_environment` messages with the `::error::` prefix stripped.
#: Hand-written, not read back from the module, so a probe that reported a different
#: refusal -- or reported this one as a skipped probe -- reddens here.
_MISINDENTED_FINDING = (
    doctor.WARNING,
    "`.github/shipmate-config.yml` at the commit under examination is not valid: "
    ".github/shipmate-config.yml declares no layout, so no cell can resolve its environment "
    "identity. Declare layout: tf_vars, layout: workspace or layout: folder on the default "
    "branch, which is where this table is read from. "
    "environment prod: layout is not a key this engine implements. An environment "
    "holds region, tf_vars, identity, workloads, shared, needs, explicit, gated. Merging it "
    "refuses every "
    "operation that reads the table. Execution still reads the default branch's copy, which "
    "this says nothing about.",
)


def _config_responses(at_head, on_default=CANONICAL):
    """The file the probe should read, and a healthy one on the default branch it must not.

    The two differ in verdict, so a probe reading the wrong ref does not merely read the
    wrong bytes -- it reports the opposite finding.
    """
    return {
        _CONFIG_READ: _wf_file(at_head),
        _CONFIG_ON_DEFAULT: _wf_file(on_default),
    }


def test_the_config_probe_reads_the_examined_commit_and_reports_a_misindented_control(
    monkeypatch,
):
    """Two claims, one fixture. A misindented control sits at the commit under examination
    and a valid file on the default branch, so:

    - reading `?ref=<head sha>` is what produces a finding at all (mutation: build the ref
      from `ctx["default_branch"]` -- the probe then reports the valid file's notes);
    - the placement mistake is reported, whole (mutation: return the parsed table without
      calling `validate_structure` -- the file is well-formed YAML and every finding here
      disappears).
    """
    responses = _config_responses(MISINDENTED_CONTROL)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._config_warnings(_ctx()) == [_MISINDENTED_FINDING]


def test_a_refusal_is_the_finding_not_a_skipped_probe(monkeypatch):
    """`validate_structure` raises SystemExit, and `warnings()` turns an escaped one into
    "could not verify ... probe skipped" with the reason cut at 120 characters -- a
    validation refusal misreported as a probe that never ran, and truncated mid-sentence.

    Mutation: drop the `except SystemExit` clause from `_config_table`.
    """
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
        **_quiet_new_probes(),
        **_config_responses(MISINDENTED_CONTROL),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.warnings(_ctx()) == [_MISINDENTED_FINDING]


def test_a_refusal_naming_three_errors_is_one_finding_naming_all_three(monkeypatch):
    """Each line of the refusal loses its own `::error::`, so none nests in the finding.

    Mutations, each proven: render `str(exc).removeprefix("::error::")` whole, as one line
    -- the second and third messages keep their prefix; join the lines with `"; "`, which
    reads `gated.; environments`.
    """
    text = (
        "layout: folder\n\nenvironments:\n  dev-eu:\n    regoin: eu-west-1\n    gated: False\n"
        "    needs: [dev-eu]\n"
    )
    responses = _config_responses(text)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._config_warnings(_ctx()) == [
        (
            doctor.WARNING,
            "`.github/shipmate-config.yml` at the commit under examination is not valid: "
            "environment dev-eu: regoin is not a key this engine implements. An environment "
            "holds region, tf_vars, identity, workloads, shared, needs, explicit, gated. "
            "environments.dev-eu.gated must be true or false, got 'False'. "
            "needs is cyclic: dev-eu -> dev-eu: each of those must fully apply before the "
            "next, so the ordering has no first environment and no apply path can sort it. "
            "Break the chain in .github/shipmate-config.yml. Merging it refuses every "
            "operation that reads the table. Execution still reads the default branch's copy, "
            "which this says nothing about.",
        )
    ]


def test_a_refusal_naming_twelve_errors_shows_ten_and_counts_the_rest(monkeypatch):
    """The finding lands in the PR comment and an annotation, so it is bounded however
    many errors the file holds.

    Mutation: join every line, dropping the `[:CONFIG_ERROR_LINES]` slice and the tail.
    """
    text = "layout: folder\n" + "".join(f"k{n:02}: 1\n" for n in range(1, 13))
    responses = _config_responses(text)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    unknown = (
        "{} is not a setting this engine implements. .github/shipmate-config.yml holds "
        "layout, identities, environments."
    )
    shown = " ".join(unknown.format(f"k{n:02}") for n in range(1, 11))
    assert doctor._config_warnings(_ctx()) == [
        (
            doctor.WARNING,
            "`.github/shipmate-config.yml` at the commit under examination is not valid: "
            f"{shown} … and 2 more. Merging it refuses every operation that reads the "
            "table. Execution still reads the default branch's copy, which this says nothing "
            "about.",
        )
    ]


def test_an_interpreter_below_the_floor_is_not_reported_as_an_invalid_file(monkeypatch):
    """The floor is a property of the runner, not of the file, so the file-validity wrapper
    would tell a consumer on an old `runs_on` image that their file is invalid, that merging
    it refuses every operation, and that the default branch is unaffected -- three false
    claims, sending them to read a file that is fine. The fixture holds a VALID file, so a
    wrapped refusal can only be the floor's.

    Mutation: drop the `runner_refusal()` check ahead of `_contents_ref` in
    `_config_table`, folding the refusal into the file-validity finding.
    """
    monkeypatch.setattr(sys, "version_info", (3, 11, 9, "final", 0))
    responses = _config_responses(CANONICAL)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._config_warnings(_ctx()) == [
        (
            doctor.WARNING,
            "this runner's Python refuses the environment table before any revision of it "
            "is read: the engine needs Python 3.12 or later; this runner has 3.11.9. "
            "CONTRACT.md section Runner prerequisites requires python3 >= 3.12 on every "
            "runner. Choose a newer runs_on image. "
            "Nothing is wrong with the file: merging it changes nothing, and every "
            "operation that reads the table meets the same refusal on the default branch "
            "too.",
        )
    ]


def test_an_unreadable_config_is_a_note_and_no_other_revision_is_read(monkeypatch):
    """Two claims, one fixture, and the default branch holds a VALID file so that a
    fallback would read as an all-clear rather than as an error:

    - an unreadable file is reported (mutation: `return []` when the read fails -- the
      probe then says nothing at all, which a reader takes for a clean table);
    - nothing else is read in its place (mutation: fall back to the default-branch ref --
      the probe reports the valid file's notes for a commit whose file it never read).
    """
    asked = []

    def gh(path):
        asked.append(path)
        if path == _CONFIG_READ:
            raise SystemExit("::error::command failed (1): gh api ...")
        return _wf_file(CANONICAL)

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._config_warnings(_ctx()) == [doctor.CONFIG_UNREADABLE]
    assert asked == [_CONFIG_READ]


def test_an_unencoded_config_blob_is_unreadable_not_empty(monkeypatch):
    """A file over 1 MB answers with `encoding: "none"` and empty content. Decoded anyway
    that is an empty table, which parses and would fail only on the absent `layout` -- a
    refusal about a file nobody wrote. Mutation: decode a blob whatever its encoding."""
    responses = {_CONFIG_READ: {"encoding": "none", "content": ""}}
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._config_warnings(_ctx()) == [doctor.CONFIG_UNREADABLE]


def test_the_config_probe_declines_without_a_usable_commit(monkeypatch):
    """No commit is a reason to skip, never to read the default branch instead, and the
    ref is not interpolated into a `gh api` path unless it is a 40-char hex SHA -- the same
    guard the pin probe carries. Mutation: drop the `ref is None` branch."""

    def gh(path):
        pytest.fail(f"the config probe hit the API with an unusable head SHA: {path}")

    monkeypatch.setattr(doctor, "_gh_json", gh)
    assert doctor._config_warnings(_ctx(head_sha="main?per_page=1&x=/../")) == [
        doctor.CONFIG_NO_COMMIT
    ]


def test_a_valid_verdict_names_the_checks_it_did_not_run(monkeypatch):
    """The whole status section against hand-written text, and no finding beside it.
    `doctor` runs `validate_structure` alone -- the remaining rules need a plan matrix or a
    whole-tree environment scan -- so a verdict that stopped naming them would report a
    clean bill over the rest of the checks, and a reader would take "valid" for
    "this will run". It also states that execution reads the default branch's copy, without
    which the report reads as a verdict on what the pull request will do.

    Mutation: shorten the verdict to "parses and is valid"; or return it from
    `_config_warnings` again, which reddens the `== []` line here and the end-to-end
    all-clear guard; or emit the variable-references notice with an empty list for a file
    holding no reference. Swapping `validate_structure` for `validate` is NOT a mutation that reds
    here -- the canonical file passes both -- so this guard pins the words, and
    `test_the_config_probe_feeds_nothing_a_run_reads` pins that the run-context reader is
    never reached.
    """
    # A file with no reference must not need the caller's variables at all.
    monkeypatch.delenv("SHIPMATE_GITHUB_VARS", raising=False)
    responses = _config_responses(CANONICAL, on_default=MISINDENTED_CONTROL)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._config_warnings(_ctx()) == []
    assert doctor.config_status(_ctx()) == [
        (
            doctor.NOTICE,
            "`.github/shipmate-config.yml` at the commit under examination parses, and passes "
            "every check a file can be judged on by itself: its top-level keys, "
            "`layout`, the identities and the environment entries. Not checked here, for want "
            "of a plan matrix and a whole-tree environment scan: `tf_vars`-layout coverage of "
            "the planned environments, entries "
            "that no stack tags, workload tags outside an environment's `workloads` and listed "
            "workloads no stack tags. `detect` checks each of those on the runs where it "
            "applies, and `scripts/onboard --app-id <id> --dry-run` checks them over a "
            "checkout. "
            "Execution reads the default branch's copy of this file, never this branch's.",
        ),
        (
            doctor.NOTICE,
            "`dev-eu` resolves these roles at the commit under examination: plan every cell: "
            "`arn:aws:iam::981781037707:role/shipmate-plan`; apply every cell: "
            "`arn:aws:iam::981781037707:role/shipmate-apply`.",
        ),
        (
            doctor.NOTICE,
            "`prod` resolves these roles at the commit under examination: plan app: "
            "`arn:aws:iam::981781037707:role/prod-plan`; plan net-edge: "
            "`arn:aws:iam::981781037707:role/prod-plan`; apply app: "
            "`arn:aws:iam::981781037707:role/prod-apply`; apply net-edge: "
            "`arn:aws:iam::981781037707:role/net-edge`.",
        ),
        (
            doctor.NOTICE,
            "`dev-eu-plan` reaches these AWS accounts at the commit under examination: "
            "981781037707.",
        ),
        (
            doctor.NOTICE,
            "`dev-eu-apply` reaches these AWS accounts at the commit under examination: "
            "981781037707.",
        ),
        (
            doctor.NOTICE,
            "`prod-plan` reaches these AWS accounts at the commit under examination: 981781037707.",
        ),
        (
            doctor.NOTICE,
            "`prod-apply` reaches these AWS accounts at the commit under examination: "
            "981781037707.",
        ),
        (
            doctor.NOTICE,
            "`needs` orders dev-us after dev-eu: a bare `shipmate apply` applies "
            "one env-level fully before it starts the next.",
        ),
        (
            doctor.NOTICE,
            "`explicit: true` on prod: a bare `shipmate apply` skips those, and each "
            "needs its own `shipmate apply <env>`.",
        ),
    ]


#: Two references, one of them a list item, hand-written.
_REFERENCED = """layout: folder

identities:
  dev:
    aws:
      plan: {vars: DEV_PLAN_ROLE}
      apply: arn:aws:iam::981781037707:role/shipmate-apply

environments:
  dev:
    region: eu-west-1
    identity: dev
  prod:
    explicit: true
    needs: [{vars: FIRST_ENV}]
"""


def test_a_valid_file_holding_references_lists_each_one(monkeypatch):
    """The report names the values the file does not hold, sorted by key path, beside the
    verdict and never as a finding: in `_config_warnings` the notice would annotate every
    plan run and displace the settings-probe all-clear. The `needs` line shows the resolved
    predecessor, and the explicit line reads the entry's flag.

    The roles line reads the plan role from the variable, where `CONFIG_REFERENCES` says a
    run reads it.

    Mutations: omit the references notice from `config_status`; stop `_replace` recursing
    into lists -- the `needs` line renders the mapping; or read the old top-level list in
    `_config_defaults` -- the explicit line reports none.
    """
    monkeypatch.setenv(
        "SHIPMATE_GITHUB_VARS",
        '{"DEV_PLAN_ROLE": "arn:aws:iam::981781037707:role/shipmate-plan", "FIRST_ENV": "dev"}',
    )
    responses = {_CONFIG_READ: _wf_file(_REFERENCED)}
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._config_warnings(_ctx()) == []
    assert doctor.config_status(_ctx()) == [
        (doctor.NOTICE, doctor.CONFIG_VALID),
        (
            doctor.NOTICE,
            "`.github/shipmate-config.yml` at the commit under examination takes these values from "
            "GitHub variables instead of holding them: `environments.prod.needs[0]` from "
            "variable `FIRST_ENV`; `identities.dev.aws.plan` from variable `DEV_PLAN_ROLE`. "
            "Every run "
            "resolves them again from repository and organization variables, never from a "
            "cell's Environment; in comment-ops and the plan summary a `shipmate-engine` "
            "Environment variable of the same name wins.",
        ),
        (
            doctor.NOTICE,
            "`dev` resolves these roles at the commit under examination: plan every cell: "
            "`arn:aws:iam::981781037707:role/shipmate-plan`; apply every cell: "
            "`arn:aws:iam::981781037707:role/shipmate-apply`.",
        ),
        (
            doctor.NOTICE,
            "`dev-plan` reaches these AWS accounts at the commit under examination: 981781037707.",
        ),
        (
            doctor.NOTICE,
            "`dev-apply` reaches these AWS accounts at the commit under examination: 981781037707.",
        ),
        (
            doctor.NOTICE,
            "`needs` orders prod after dev: a bare `shipmate apply` applies "
            "one env-level fully before it starts the next.",
        ),
        (
            doctor.NOTICE,
            "`explicit: true` on prod: a bare `shipmate apply` skips those, and each "
            "needs its own `shipmate apply <env>`.",
        ),
    ]


def test_a_long_needs_or_explicit_list_is_cut_between_env_names():
    """Each defaults line shows the whole env names that fit 400 characters with the count of
    the rest, never part of a name.

    Mutations: render `shown` or the explicit list through `_one_line(..., 400)` -- the last
    name shown is cut mid-text; drop the count from the tail -- the line ends ` …`.
    """
    name = "environment-with-a-long-name-{:02d}".format
    table = {
        "environments": {
            name(i): {"explicit": True, **({"needs": [name(i - 1)]} if i else {})}
            for i in range(20)
        }
    }
    assert doctor._config_defaults(table) == [
        (
            doctor.NOTICE,
            "`needs` orders "
            "environment-with-a-long-name-01 after environment-with-a-long-name-00; "
            "environment-with-a-long-name-02 after environment-with-a-long-name-01; "
            "environment-with-a-long-name-03 after environment-with-a-long-name-02; "
            "environment-with-a-long-name-04 after environment-with-a-long-name-03; "
            "environment-with-a-long-name-05 after environment-with-a-long-name-04"
            " … and 14 more: a bare `shipmate apply` applies one env-level fully before it "
            "starts the next.",
        ),
        (
            doctor.NOTICE,
            "`explicit: true` on "
            "environment-with-a-long-name-00, environment-with-a-long-name-01, "
            "environment-with-a-long-name-02, environment-with-a-long-name-03, "
            "environment-with-a-long-name-04, environment-with-a-long-name-05, "
            "environment-with-a-long-name-06, environment-with-a-long-name-07, "
            "environment-with-a-long-name-08, environment-with-a-long-name-09, "
            "environment-with-a-long-name-10"
            " … and 9 more: a bare `shipmate apply` skips those, and each needs its own "
            "`shipmate apply <env>`.",
        ),
    ]


class _SliceBudget(list):
    """A row list that refuses a 64th slice, so a trim loop that never ends fails fast."""

    slices = 0

    def __getitem__(self, key):
        if isinstance(key, slice):
            self.slices += 1
            assert self.slices < 64, "the trim loop did not stop"
        return super().__getitem__(key)


def test_a_first_item_over_the_limit_shows_only_the_count():
    """Mutation: stop trimming at one item (`while ... and shown > 1`) -- the first item
    shows, over the limit."""
    item = "a after " + ", ".join(f"predecessor-{i:02d}" for i in range(30))
    assert doctor._fit_items([item, "b after a"], "; ", 400) == "… and 2 more"


def test_a_defaults_notice_showing_only_the_count_has_one_space_before_it():
    """Mutation: keep the leading space of ` … and N more` when no item is shown -- the
    notice reads "orders  … and 1 more"."""
    preds = [f"predecessor-{i:02d}" for i in range(30)]
    table = {"environments": {"a": {"needs": preds}, **{p: {} for p in preds}}}
    assert doctor._config_defaults(table)[0] == (
        doctor.NOTICE,
        "`needs` orders … and 1 more: a bare `shipmate apply` applies one env-level fully "
        "before it starts the next.",
    )


def test_a_limit_shorter_than_the_count_shows_the_count_and_stops():
    """A roles heading over the budget leaves a negative limit: trimming went on past zero
    items, the count growing, and hung the report.

    Mutation: drop `and shown` -- the 64th slice fails inside `_SliceBudget` instead of
    hanging.
    """
    rows = _SliceBudget(["plan every cell: no role", "apply every cell: no role"])
    assert doctor._fit_items(rows, "; ", -1) == "… and 2 more"


def test_a_list_that_fits_the_limit_exactly_has_no_tail():
    """Mutation: trim while the text is `>= limit` -- the 400-character list loses an item to
    a ` … and 1 more` tail."""
    items = ["a" * 198, "b" * 200]
    assert doctor._fit_items(items, "; ", 400) == "a" * 198 + "; " + "b" * 200


def test_rows_that_fill_the_budget_exactly_all_fit():
    """Mutation: fit a row only while `used + len(row) + 1 < budget` -- the second row, which
    ends exactly at the budget, is dropped."""
    lines = []
    assert (doctor._fit(lines, ["ab", "cd"], 0, 6), lines) == ((6, 2, 0), ["ab", "cd"])


#: A varying identity with two workloads listed out of alphabetical order, an apply-only
#: identity named by a shared and by two unshared environments, one of them writing
#: `workloads`, and an entry naming none.
_ROLES_TABLE = """layout: folder

identities:
  app:
    aws:
      account: 111111111111
      plan: "{workload}-plan"
      apply: {core: core-apply, network: net-apply}
  ops:
    aws:
      apply: arn:aws:iam::333333333333:role/ops-apply

environments:
  dev:
    region: eu-west-1
    identity: app
    workloads: [network, core]
  ops:
    region: eu-west-1
    identity: ops
    shared: true
  plain:
    region: eu-west-1
  stage:
    region: eu-west-1
    identity: ops
  tools:
    region: eu-west-1
    identity: ops
    workloads: [ci]
"""


def test_each_environment_prints_the_role_every_path_and_workload_resolves(monkeypatch):
    """One notice per environment naming an identity, between the verdict and the defaults:
    `{workload}` filled and names expanded under the account, the list's written order, a
    shared environment's plan row showing the apply role it runs with, and an apply-only
    identity's plan row as `no role`. An entry naming no identity gets no line.

    Mutations: drop the roles notices from `config_status` (three lines vanish); consult the
    requested path rather than `apply` for a shared environment in `resolved_roles` (the
    `ops` plan row reads `no role`); skip rows whose `role_arn` is empty (the `stage` plan
    row vanishes); print `every cell` whatever the entry writes (the `tools` rows, whose list
    leaves a tag outside it with no role, read `every cell`); append the account notices
    after `_config_defaults` in `config_status`.
    """
    responses = {_CONFIG_READ: _wf_file(_ROLES_TABLE)}
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.config_status(_ctx()) == [
        (doctor.NOTICE, doctor.CONFIG_VALID),
        (
            doctor.NOTICE,
            "`dev` resolves these roles at the commit under examination: plan network: "
            "`arn:aws:iam::111111111111:role/network-plan`; plan core: "
            "`arn:aws:iam::111111111111:role/core-plan`; apply network: "
            "`arn:aws:iam::111111111111:role/net-apply`; apply core: "
            "`arn:aws:iam::111111111111:role/core-apply`.",
        ),
        (
            doctor.NOTICE,
            "`ops` resolves these roles at the commit under examination: plan every cell: "
            "`arn:aws:iam::333333333333:role/ops-apply`; apply every cell: "
            "`arn:aws:iam::333333333333:role/ops-apply`.",
        ),
        (
            doctor.NOTICE,
            "`stage` resolves these roles at the commit under examination: plan every cell: no "
            "role; apply every cell: `arn:aws:iam::333333333333:role/ops-apply`.",
        ),
        (
            doctor.NOTICE,
            "`tools` resolves these roles at the commit under examination: plan untagged and "
            "listed cells: no role; apply untagged and listed cells: "
            "`arn:aws:iam::333333333333:role/ops-apply`.",
        ),
        (
            doctor.NOTICE,
            "`dev-plan` reaches these AWS accounts at the commit under examination: 111111111111.",
        ),
        (
            doctor.NOTICE,
            "`dev-apply` reaches these AWS accounts at the commit under examination: 111111111111.",
        ),
        (
            doctor.NOTICE,
            "`ops` reaches these AWS accounts at the commit under examination: 333333333333.",
        ),
        (
            doctor.NOTICE,
            "`stage-apply` reaches these AWS accounts at the commit under examination: "
            "333333333333.",
        ),
        (
            doctor.NOTICE,
            "`tools-apply` reaches these AWS accounts at the commit under examination: "
            "333333333333.",
        ),
        (
            doctor.NOTICE,
            "`needs`: declared by no environment, so every environment sits at one level, "
            "and a bare `shipmate apply` applies them all together.",
        ),
        (
            doctor.NOTICE,
            "`explicit`: set on no environment, so every environment applies on a bare "
            "`shipmate apply`, production included.",
        ),
    ]


#: 62 characters, the role-name length that reproduces the oversized report.
_LONG_ROLE = "shipmate-" + "r" * 53


def _wide_roles_table(*envs):
    """`envs` each naming one identity whose apply role varies over 256 listed workloads."""
    workloads = [f"w{i:03}" for i in range(256)]
    return {
        "layout": "folder",
        "identities": {
            "wide": {
                "aws": {
                    "account": "111111111111",
                    "plan": _LONG_ROLE,
                    "apply": {w: _LONG_ROLE for w in workloads},
                }
            }
        },
        "environments": {
            env: {"region": "eu-west-1", "identity": "wide", "workloads": workloads} for env in envs
        },
    }


def test_the_roles_section_stays_within_its_budget_and_keeps_the_harvest():
    """A report whose roles notices ran past GitHub's comment limit fell back to the
    findings-only body, dropping the environment-table section and every harvested
    annotation. The first environment's items are cut and counted, the second is counted,
    and a harvested warning still renders.

    Mutations: remove the budget (every notice unabridged) -- the body falls back, and the
    harvested `stale codegen` warning and the section heading both vanish; or never cut an
    environment's items -- the first notice alone is over budget, so both environments are
    only counted.
    """
    table = doctor.ec.validate_structure(_wide_roles_table("dev", "prod"))
    roles = doctor._config_roles(table)
    body = doctor.render_report([], [_ann(title="stale codegen")], _ctx(), roles)
    assert (doctor.CONFIG_HEADING in body, "stale codegen" in body) == (True, True)
    arn = f"`arn:aws:iam::111111111111:role/{_LONG_ROLE}`"
    items = [f"plan w{i:03}: {arn}" for i in range(256)] + [
        f"apply w{i:03}: {arn}" for i in range(256)
    ]
    assert roles == [
        (
            doctor.NOTICE,
            "`dev` resolves these roles at the commit under examination: "
            + "; ".join(items[:73])
            + " … and 439 more.",
        ),
        (
            doctor.NOTICE,
            "roles for 1 more environment(s) not shown, to keep this report under GitHub's "
            "comment limit; `CONTRACT.md` §Resolution lists how each cell resolves.",
        ),
    ]


def test_a_heading_over_the_budget_shows_no_items_and_stops():
    """A lowercase environment name of 8,000 characters is valid, and its heading alone is
    over the budget: it and every environment after it are counted, not shown.

    Mutation: remove the `break` after the count notice -- `zz`'s notice follows it.
    """
    narrow = {"aws": {"account": "222222222222", "plan": "zz-plan"}}
    table = {
        "layout": "folder",
        "identities": {"narrow": narrow},
        "environments": {
            env: {"region": "eu-west-1", "identity": "narrow"} for env in ("e" * 8000, "zz")
        },
    }
    assert doctor._config_roles(doctor.ec.validate_structure(table)) == [
        (
            doctor.NOTICE,
            "roles for 2 more environment(s) not shown, to keep this report under GitHub's "
            "comment limit; `CONTRACT.md` §Resolution lists how each cell resolves.",
        )
    ]


def test_the_contract_states_the_roles_budget_doctor_applies():
    """Mutations: set `ROLE_LINES_BUDGET` to 9000; write `9,000-character` in `CONTRACT.md`."""
    contract = " ".join((ENGINE / "CONTRACT.md").read_text(encoding="utf-8").split())
    assert f"The notices share an {doctor.ROLE_LINES_BUDGET:,}-character budget," in contract


def test_a_later_environment_over_the_budget_is_cut_against_what_is_left():
    """Only the first environment could be cut: a later one over the budget on its own was
    counted not shown although budget remained.

    Mutation: cut every environment against the full budget -- `dev` is cut to fit 8,000,
    runs past what `aa` left, and is only counted (observed).
    """
    table = _wide_roles_table("dev")
    table["identities"]["narrow"] = {"aws": {"account": "222222222222", "plan": "aa-plan"}}
    table["environments"]["aa"] = {"region": "eu-west-1", "identity": "narrow"}
    roles = doctor._config_roles(doctor.ec.validate_structure(table))
    arn = f"`arn:aws:iam::111111111111:role/{_LONG_ROLE}`"
    items = [f"plan w{i:03}: {arn}" for i in range(256)] + [
        f"apply w{i:03}: {arn}" for i in range(256)
    ]
    assert roles == [
        (
            doctor.NOTICE,
            "`aa` resolves these roles at the commit under examination: plan every cell: "
            "`arn:aws:iam::222222222222:role/aa-plan`; apply every cell: no role.",
        ),
        (
            doctor.NOTICE,
            "`dev` resolves these roles at the commit under examination: "
            + "; ".join(items[:72])
            + " … and 440 more.",
        ),
    ]


def _accounts_line(subject, accounts):
    return (
        doctor.NOTICE,
        f"`{subject}` reaches these AWS accounts at the commit under examination: {accounts}.",
    )


def test_each_subject_names_the_accounts_its_roles_reach_once_and_sorted():
    """Four listed workloads, two of them in one account: the apply subject can assume every
    workload's role, so its line names the three accounts, each once, sorted.

    Mutations: drop the dedupe (`111111111111` twice); keep written order through
    `dict.fromkeys` instead of sorting (`333333333333` first); read only the first workload's
    role (one account).
    """
    arn = "arn:aws:iam::{}:role/apply"
    table = {
        "layout": "folder",
        "identities": {
            "app": {
                "aws": {
                    "plan": "arn:aws:iam::999999999999:role/plan",
                    "apply": {
                        "a": arn.format("333333333333"),
                        "b": arn.format("111111111111"),
                        "c": arn.format("222222222222"),
                        "d": arn.format("111111111111"),
                    },
                }
            }
        },
        "environments": {
            "dev": {"region": "eu-west-1", "identity": "app", "workloads": ["a", "b", "c", "d"]}
        },
    }
    assert doctor._subject_accounts(doctor.ec.validate_structure(table)) == [
        _accounts_line("dev-plan", "999999999999"),
        _accounts_line("dev-apply", "111111111111, 222222222222, 333333333333"),
    ]


def test_a_shared_environment_has_one_subject_reaching_the_apply_accounts():
    """A shared environment binds the bare name on both paths and runs the apply role on
    both. The table is hand-built and never validated: `validate_structure` refuses
    `aws.plan` on a shared identity, but a plan role written there must still not appear.

    Mutations: read `identity["aws"]["plan"]` for the plan path (`888888888888` appears);
    key the subject as `<env>-plan` / `<env>-apply` whatever the entry says (two lines).
    """
    table = {
        "layout": "folder",
        "identities": {
            "ops": {
                "aws": {
                    "plan": "arn:aws:iam::888888888888:role/plan",
                    "apply": "arn:aws:iam::777777777777:role/apply",
                }
            }
        },
        "environments": {"ops": {"region": "eu-west-1", "identity": "ops", "shared": True}},
    }
    assert doctor._subject_accounts(table) == [_accounts_line("ops", "777777777777")]


def test_a_subject_with_no_role_gets_no_accounts_line():
    """Mutation: keep subjects whose account set is empty (a `dev-plan` line with nothing
    after the colon)."""
    table = {
        "layout": "folder",
        "identities": {"app": {"aws": {"apply": "arn:aws:iam::111111111111:role/apply"}}},
        "environments": {"dev": {"region": "eu-west-1", "identity": "app"}},
    }
    assert doctor._subject_accounts(doctor.ec.validate_structure(table)) == [
        _accounts_line("dev-apply", "111111111111")
    ]


def test_an_arn_without_an_account_field_contributes_no_account():
    """`validate_structure` checks only the `arn:` prefix, and `config_status` must not raise.

    Mutations: drop the field-count check (IndexError); drop the non-empty check (an empty
    account leads the line).
    """
    table = {
        "layout": "folder",
        "identities": {
            "app": {
                "aws": {
                    "apply": {
                        "a": "arn:aws:iam",
                        "b": "arn:aws:iam:::role/x",
                        "c": "arn:aws:iam::111111111111:role/apply",
                    }
                }
            }
        },
        "environments": {
            "dev": {"region": "eu-west-1", "identity": "app", "workloads": ["a", "b", "c"]}
        },
    }
    assert doctor._subject_accounts(doctor.ec.validate_structure(table)) == [
        _accounts_line("dev-apply", "111111111111")
    ]


def test_the_account_lines_keep_their_own_budget():
    """Three environments whose apply subjects each reach 256 accounts: the first line fits
    whole, the second is cut against what is left, and the third is counted.

    Mutation: bound the account notices by `ROLE_LINES_BUDGET` (the second line is whole and
    the third cut).
    """
    workloads = [f"w{i:03}" for i in range(256)]
    accounts = [f"{100000000000 + i}" for i in range(256)]
    table = {
        "layout": "folder",
        "identities": {
            "wide": {
                "aws": {
                    "apply": {
                        w: f"arn:aws:iam::{a}:role/apply"
                        for w, a in zip(workloads, accounts, strict=True)
                    }
                }
            }
        },
        "environments": {
            env: {"region": "eu-west-1", "identity": "wide", "workloads": workloads}
            for env in ("dev", "prod", "stage")
        },
    }
    assert doctor._subject_accounts(doctor.ec.validate_structure(table)) == [
        _accounts_line("dev-apply", ", ".join(accounts)),
        _accounts_line("prod-apply", ", ".join(accounts[:18]) + " … and 238 more"),
        (
            doctor.NOTICE,
            "accounts for 1 more subject(s) not shown, to keep this report under GitHub's "
            "comment limit; the roles notices above list each environment's roles.",
        ),
    ]


def test_a_notice_that_fits_its_budget_exactly_is_whole_and_one_less_is_cut():
    """The full stop counts against the budget: one character short, the items are cut and
    the notice still shows, rather than running one past the budget and being counted.

    Mutations: drop the `- 1` reserved for the full stop (the shorter budget gives only the
    overflow notice); reserve two characters (the exact budget cuts the items).
    """
    groups = {"k": ["a" * 20, "b" * 20]}
    whole = "k: " + "a" * 20 + ", " + "b" * 20 + "."
    assert len(whole) == 46
    assert doctor._budgeted(groups, "{}: ", ", ", 46, "over {}") == [(doctor.NOTICE, whole)]
    assert doctor._budgeted(groups, "{}: ", ", ", 45, "over {}") == [
        (doctor.NOTICE, "k: " + "a" * 20 + " … and 1 more.")
    ]


def test_the_contract_states_the_account_budget_doctor_applies():
    """Mutations: set `ACCOUNT_LINES_BUDGET` to 5000; set it to 8000 (the roles sentence must
    not satisfy it)."""
    contract = " ".join((ENGINE / "CONTRACT.md").read_text(encoding="utf-8").split())
    budget = f"The account notices share their own {doctor.ACCOUNT_LINES_BUDGET:,}-character budget"
    assert budget in contract


def test_an_unset_reference_is_the_invalid_file_finding(monkeypatch):
    """A pull request adding a misspelled reference plans green, because plans read the
    default branch's file; `shipmate doctor` is where the refusal shows before the merge.

    Mutation: catch the refusal in `_config_table` and return the unresolved table.
    """
    monkeypatch.setenv("SHIPMATE_GITHUB_VARS", '{"FIRST_ENV": "dev"}')
    responses = {_CONFIG_READ: _wf_file(_REFERENCED)}
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor._config_warnings(_ctx()) == [
        (
            doctor.WARNING,
            "`.github/shipmate-config.yml` at the commit under examination is not valid: "
            ".github/shipmate-config.yml identities.dev.aws.plan references GitHub variable "
            "DEV_PLAN_ROLE, which is not set. A reference reads repository and organization "
            "variables; the variables of a cell's <env>-plan, <env>-apply or shared <env> "
            "Environment are never read. "
            "Merging it refuses every operation that reads the table. Execution still reads "
            "the default branch's copy, which this says nothing about.",
        )
    ]
    assert doctor.config_status(_ctx()) == []


def test_the_tolerant_defaults_are_read_back_when_absent(monkeypatch):
    """A table omitting `needs` and `explicit` is valid and takes the empty default for
    each: a bare `shipmate apply` applies every environment -- including the one a consumer
    meant to exclude. Nothing refuses and no validator can, so the report says it.

    Mutation: drop `_config_defaults` from `config_status`.
    """
    responses = {_CONFIG_READ: _wf_file("layout: folder\n")}
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.config_status(_ctx())[1:] == [
        (
            doctor.NOTICE,
            "`needs`: declared by no environment, so every environment sits at one level, "
            "and a bare `shipmate apply` applies them all together.",
        ),
        (
            doctor.NOTICE,
            "`explicit`: set on no environment, so every environment applies on a bare "
            "`shipmate apply`, production included.",
        ),
    ]


def test_the_config_probe_feeds_nothing_a_run_reads(monkeypatch):
    """Validation follows the branch in hand; authority stays with the default branch. The
    probe therefore touches neither the execution reader nor the resolver, and leaves the
    context it was handed alone.

    Mutation: resolve a cell from the parsed table, or call `read_table`.
    """

    def forbidden(*a, **kw):
        pytest.fail("the config probe reached the execution path")

    monkeypatch.setattr(doctor.ec, "read_table", forbidden)
    monkeypatch.setattr(doctor.ec, "resolve", forbidden)
    for text in (CANONICAL, MISINDENTED_CONTROL):
        responses = _config_responses(text)
        monkeypatch.setattr(doctor, "_gh_json", lambda path, r=responses: r[path])
        ctx = _ctx()
        before = dict(ctx)
        doctor._config_warnings(ctx)
        doctor.config_status(ctx)
        assert ctx == before


def test_the_all_clear_survives_a_sound_environment_table(monkeypatch):
    """End to end, the way `main` runs it: healthy fixture -> `warnings()` -> `render_report`.

    Every other all-clear guard hands `[]` straight to the renderer, so none of them can see
    a probe that reports on a healthy repository -- which is exactly how the status lines
    suppressed this line while the suite stayed green. The environment-coverage caveat lives
    only here, so losing the line loses the caveat.

    Mutation: return the status lines from `_config_warnings` again (e.g. append
    `*config_status(ctx)` to its return).
    """
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}?per_page=100": _gate_rule(),
        f"repos/{_REPO}/environments?per_page=100": _environments(
            "dev-eu-plan", "dev-eu-apply", "shipmate-engine"
        ),
        **_quiet_new_probes(),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    ctx = _ctx()
    body = doctor.render_report(doctor.warnings(ctx), [], ctx, doctor.config_status(ctx))
    assert "no problems found by the settings probes" in body
    assert "changed in this pull request" in body
    # The status section renders too, and below the all-clear rather than instead of it.
    assert doctor.CONFIG_HEADING in body
    assert body.index("no problems found") < body.index(doctor.CONFIG_HEADING)
    assert "`explicit: true` on prod" in body


def test_the_report_renders_the_table_status_beside_a_finding(monkeypatch):
    """A repository with a settings problem still gets the table's status: the two sections
    are independent, and a reader fixing a gate ruleset must not lose the `explicit`
    line because of it. Mutation: render the status only in the `else` branch."""
    responses = _config_responses(CANONICAL)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    ctx = _ctx()
    body = doctor.render_report(
        [(doctor.WARNING, "gate rule missing")], [], ctx, doctor.config_status(ctx)
    )
    assert "gate rule missing" in body
    assert "no problems found" not in body
    assert "`explicit: true` on prod" in body


def test_no_status_section_beside_a_refusal(monkeypatch):
    """A refused table has no status to state, and a section saying otherwise beside the
    refusal would read as a second opinion on the same file.

    Mutation: return `[(NOTICE, CONFIG_VALID)]` from `config_status` whatever the table.
    """
    responses = _config_responses(MISINDENTED_CONTROL)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    ctx = _ctx()
    assert doctor.config_status(ctx) == []
    body = doctor.render_report(doctor.warnings(ctx), [], ctx, doctor.config_status(ctx))
    assert doctor.CONFIG_HEADING not in body


#: A cycle is decidable from the file alone, so `doctor` must report it rather than certify
#: the file. Kept here rather than beside the design's two published files: that module's
#: subject is the bytes `docs/` publishes, and this is neither of them.
CYCLIC_ORDER = """layout: folder

environments:
  dev:
    needs: [prod]
  prod:
    needs: [dev]
"""
#: Hand-written whole, like `_MISINDENTED_FINDING`: the probe's framing plus
#: `_check_cycle`'s message, via `validate_structure`, with the `::error::` prefix stripped.
_CYCLIC_FINDING = (
    doctor.WARNING,
    "`.github/shipmate-config.yml` at the commit under examination is not valid: needs is "
    "cyclic: dev -> prod -> dev: each of those must fully apply before the next, so the "
    "ordering has no first environment and no apply path can sort it. Break the chain in "
    ".github/shipmate-config.yml. Merging it refuses every operation that reads the table. "
    "Execution still reads the default branch's copy, which this says nothing about.",
)


def test_a_cycle_across_needs_is_a_finding_and_gets_no_valid_verdict(monkeypatch):
    """Both halves of the same claim, because either one alone can pass while the file is
    still certified: the cycle is reported as a finding, and the CONFIG_VALID verdict -- which
    says the file "passes every check a file can be judged on by itself" -- is withheld. Before
    the cycle check, `doctor` issued that verdict over an ordering both apply paths refuse.

    Mutation: delete the `TopologicalSorter` block from `_check_cycle` -- the finding
    list empties and the verdict comes back.
    """
    responses = _config_responses(CYCLIC_ORDER)
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    ctx = _ctx()
    assert doctor._config_warnings(ctx) == [_CYCLIC_FINDING]
    assert doctor.config_status(ctx) == []


def test_status_never_fails_the_run(monkeypatch):
    """`report` mode renders the status outside `warnings()`' degrade handler, so an
    exception here would take down a `shipmate doctor` comment that the probe has already
    reported the same failure in. Mutation: drop the `except` clause in `config_status`."""

    def boom(path):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(doctor, "_contents_text", boom)
    assert doctor.config_status(_ctx()) == []


def test_workflow_scans_in_one_run_share_one_read_of_each_file(monkeypatch):
    """Two scans of the same commit, the `shipmate.yml` probe and the parse probe read the
    listing and each file once between them, parse each file once, and the second scan still
    sees every file.

    Mutations: drop the `ref not in cache` check in `_workflow_files` -- the listing is read
    twice; drop the `(name, ref) not in cache` check in `_workflow_file` -- every file is
    read and parsed more than once."""
    responses = _fork_responses({"a.yml": "on: push\n", "shipmate.yml": _SHIPMATE_WF})
    reads, parsed = [], []

    def gh(path):
        reads.append(path)
        return responses[path]

    def load(text):
        parsed.append(text)
        return doctor_load(text)

    doctor_load = doctor.load_workflow_text
    monkeypatch.setattr(doctor, "_gh_json", gh)
    monkeypatch.setattr(doctor, "load_workflow_text", load)
    ctx = _ctx()
    for _ in range(2):
        names = doctor._scan_workflow_docs(ctx, _REF, lambda doc, name: [name], None)
        assert names == ["a.yml", "shipmate.yml"]
    doctor._shipmate_yml_warnings(ctx)
    doctor._workflow_parse_warnings(ctx)
    assert sorted(reads) == sorted(responses)
    assert sorted(parsed) == sorted(["on: push\n", _SHIPMATE_WF])


def test_a_failed_shared_read_fails_every_probe_that_asks(monkeypatch):
    """A failed read is kept and re-raised to each later reader, so each probe degrades on its
    own and the endpoint is not asked again.

    Mutation: keep `(None, None)` for a failed read in `_read_once`; the second reader gets
    None back instead of the failure."""
    asked = []

    def gh(path):
        asked.append(path)
        raise SystemExit("::error::command failed (1): gh api " + path)

    monkeypatch.setattr(doctor, "_gh_json", gh)
    ctx = _ctx()
    for _ in range(2):
        with pytest.raises(SystemExit):
            doctor._existing_env_names(ctx)
    assert asked == [f"repos/{_REPO}/environments?per_page=100"]
