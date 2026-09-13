"""Every matrix row every detect emits carries the five fields the cell actions resolve a cell's
identity and credentials from: `config_mode`, `role_arn`, `cred_region`, `tf_vars` and
`config_path`.

`scripts/env-inject` refuses a mode it does not recognise and there is no default anywhere on
the route, so a row that reaches a cell without `config_mode` fails that cell -- and one call site
left unstamped fails one workflow while the other five stay green. The stamp is applied at each
call site rather than inside `build_matrix`, which stays pure, so the sites are an enumeration
and each one needs its own assertion.

Each assertion runs the detect's own `main()` through the stub scaffolding its module already
owns, and compares the whole first row against a hand-written constant: the stamp wraps whatever
the builder returned, so the constant is that module's fixture shape plus the new keys.

The tier is the second property here. One call site serves both the plan and the drift workflow,
so nothing may infer `plan` or `apply` from the code that runs; each caller states it, and a
caller that states the wrong one hands a plan cell the apply role.
"""

import json

import test_apply_all_detect as taad
import test_apply_detect as tad
import test_build_matrix as tbm
import test_deploy_detect as tdd
from _detect_fixtures import PLAN_SHA, _apply_check
from _loader import SCRIPTS, load_script

bm = load_script("build-matrix")

#: The scripts that emit matrix rows, and the call site in each. Hand-written, and derived from
#: the tree by `test_the_table_names_every_script_that_can_emit_rows` -- a seventh detect script
#: reddens that test rather than going unstamped and unnoticed.
_PRODUCERS = {
    "build-matrix": ("main",),
    "deploy-detect": ("main",),
    "apply-detect": ("run_unlock", "main"),
    "apply-all-detect": ("main",),
}

_PLAN_ENV = {
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_REPOSITORY": "acme/iac",
    "SHIPMATE_HEAD_REPO": "acme/iac",
}
_DRIFT_ENV = {
    "SHIPMATE_ALL_STACKS": "true",
    "SHIPMATE_NO_PULL_REQUEST": "true",
    "GITHUB_EVENT_NAME": "schedule",
    "GITHUB_REPOSITORY": "acme/iac",
}

#: The five fields a row carries when the repository has no table. `resolve()` is never called
#: there: an environment an empty table has never heard of would need an invented default for
#: every field, and the cell actions read none of the five in legacy mode.
_LEGACY = {
    "config_mode": "legacy",
    "role_arn": "",
    "cred_region": "",
    "tf_vars": {},
    "config_path": "",
}

_PLAN_ROLE = "arn:aws:iam::1:role/plan"
_APPLY_ROLE = "arn:aws:iam::1:role/apply"

#: A table whose two tiers hold distinct roles, so a detect resolving the wrong tier is a
#: different value rather than the same one. `layout = "folder"` derives no `tf_vars`, which is
#: exactly the legitimate case a mode inferred from an empty `tf_vars` would send down the
#: legacy path.
_TABLE = {
    "layout": "folder",
    "environments": {
        "dev-eu": {
            "region": "eu-west-1",
            "aws": {"plan": {"role": _PLAN_ROLE}, "apply": {"role": _APPLY_ROLE}},
        }
    },
}


def _table(tier):
    """The five fields `_TABLE` resolves for a dev-eu cell on `tier`."""
    return {
        "config_mode": "table",
        "role_arn": _PLAN_ROLE if tier == "plan" else _APPLY_ROLE,
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": tier,
    }


def test_the_table_names_every_script_that_can_emit_rows():
    """Both sides derived: the glob against the table above. Every other test here names its
    call site by hand, and a hand-written list does not notice a seventh detect script.

    Mutation: add a `scripts/x-detect` file and watch this red with no edit to the guard.
    """
    found = {p.name for p in SCRIPTS.glob("*-detect")} | {"build-matrix"}
    assert found == set(_PRODUCERS)


def test_the_plan_matrix_rows_carry_the_stamp(monkeypatch, tmp_path):
    """Call site 1: `build-matrix` main(). Without it every plan cell of every pull request
    refuses.

    Mutation: drop the `stamp_rows` wrapper from that call.
    """
    outputs, _ = tbm._run_main(monkeypatch, tmp_path, _PLAN_ENV, head_sha="cafe1234")
    assert json.loads(outputs["matrix"])["include"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            "workload_var": "",
            **_LEGACY,
        }
    ]


def test_the_drift_matrix_rows_carry_the_stamp(monkeypatch, tmp_path):
    """Call site 2: the same `build-matrix` line, reached with `all-stacks: true`. Same code,
    a second workflow, and the nightly run is the one nobody watches.

    Mutation: as above -- both this and the plan case red together.
    """
    outputs, _ = tbm._run_main(monkeypatch, tmp_path, _DRIFT_ENV)
    assert json.loads(outputs["matrix"])["include"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            "workload_var": "",
            **_LEGACY,
        }
    ]


def test_the_deploy_rows_carry_the_stamp(monkeypatch, tmp_path):
    """Call site 3: `deploy-detect` main(), which reaches the builder through `compute_cells`
    and so is invisible to a search for `build_matrix`.

    Mutation: drop the `stamp_rows` wrapper from `deploy-detect`.
    """
    parsed = tdd._run_main(
        tmp_path,
        monkeypatch,
        cells=[tdd._cell("stacks/app")],
        checks=[_apply_check("stacks/app")],
    )
    assert json.loads(parsed["envlevel0_waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            "workload_var": "",
            **_LEGACY,
            "plan_run_id": "123456",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_unlock_rows_carry_the_stamp(monkeypatch, tmp_path):
    """Call site 4: `run_unlock`, which returns from main() early -- a stamp in any main() tail
    skips the unlock path entirely and unlock is the verb an operator reaches for when the
    pipeline is already degraded.

    Mutation: drop the `stamp_rows` wrapper from `run_unlock`.
    """
    out = tad._unlock_env(monkeypatch, tmp_path)
    tad._boom_on_plan_path(monkeypatch)
    tad._stub_unlock_tree(monkeypatch, tad._DEV_EU_CELLS)
    tad.ad.main()
    assert json.loads(tad._parsed(out)["cells"])[0] == {
        "stack": "stacks/app",
        "environment": "dev-eu",
        "workload": "app",
        "workload_var": "APP",
        **_LEGACY,
    }


def test_the_targeted_apply_rows_carry_the_stamp(monkeypatch, tmp_path):
    """Call site 5: `apply-detect` main(), the targeted `shipmate apply <env>` path.

    Mutation: drop the `stamp_rows` wrapper from that call.
    """
    out = tad._apply_env(monkeypatch, tmp_path)
    tad._stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app", plan_run="42")])
    tad.ad.main()
    assert json.loads(tad._parsed(out)["waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "app",
            "workload_var": "APP",
            **_LEGACY,
            "plan_run_id": "42",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_bare_apply_all_rows_carry_the_stamp(monkeypatch, tmp_path):
    """Call site 6: `apply-all-detect` main(), which reaches `cells_for_env` through its own
    `cells_from_checks`.

    Mutation: drop the `stamp_rows` wrapper from `apply-all-detect`.
    """
    parsed = taad._run_main(tmp_path, monkeypatch, envs=["dev-eu"], decision="APPROVED")
    assert json.loads(parsed["envlevel0_waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            "workload_var": "",
            **_LEGACY,
            "plan_run_id": "123456",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_plan_matrix_resolves_the_plan_tier(monkeypatch, tmp_path):
    """The plan workflow reads the plan credential. A cell that resolved `apply.role` here would
    hand a pull request's plan the role that mutates infrastructure.

    Mutation: pass `"apply"` at `build-matrix`'s call site.
    """
    outputs, _ = tbm._run_main(monkeypatch, tmp_path, _PLAN_ENV, head_sha="cafe1234", table=_TABLE)
    assert json.loads(outputs["matrix"])["include"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            "workload_var": "",
            **_table("plan"),
        }
    ]


def test_the_drift_matrix_resolves_the_plan_tier(monkeypatch, tmp_path):
    """The same line, the other workflow: drift plans and never applies.

    Mutation: as above -- the tier is one argument, so both red together.
    """
    outputs, _ = tbm._run_main(monkeypatch, tmp_path, _DRIFT_ENV, table=_TABLE)
    assert json.loads(outputs["matrix"])["include"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            "workload_var": "",
            **_table("plan"),
        }
    ]


def test_the_deploy_rows_resolve_the_apply_tier(monkeypatch, tmp_path):
    """Mutation: pass `"plan"` at `deploy-detect`'s call site, and every post-merge apply runs
    with the read-only role."""
    parsed = tdd._run_main(
        tmp_path,
        monkeypatch,
        cells=[tdd._cell("stacks/app")],
        checks=[_apply_check("stacks/app")],
        table=_TABLE,
    )
    assert json.loads(parsed["envlevel0_waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            "workload_var": "",
            **_table("apply"),
            "plan_run_id": "123456",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_unlock_rows_resolve_the_apply_tier(monkeypatch, tmp_path):
    """Unlock releases a state lock, which the plan credential cannot do.

    Mutation: pass `"plan"` in `run_unlock`.
    """
    out = tad._unlock_env(monkeypatch, tmp_path, table=_TABLE)
    tad._boom_on_plan_path(monkeypatch)
    tad._stub_unlock_tree(monkeypatch, tad._DEV_EU_CELLS)
    tad.ad.main()
    assert json.loads(tad._parsed(out)["cells"])[0] == {
        "stack": "stacks/app",
        "environment": "dev-eu",
        "workload": "app",
        "workload_var": "APP",
        **_table("apply"),
    }


def test_the_targeted_apply_rows_resolve_the_apply_tier(monkeypatch, tmp_path):
    """Mutation: pass `"plan"` at `apply-detect`'s main() call site."""
    out = tad._apply_env(monkeypatch, tmp_path, table=_TABLE)
    tad._stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app", plan_run="42")])
    tad.ad.main()
    assert json.loads(tad._parsed(out)["waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "app",
            "workload_var": "APP",
            **_table("apply"),
            "plan_run_id": "42",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_bare_apply_all_rows_resolve_the_apply_tier(monkeypatch, tmp_path):
    """Mutation: pass `"plan"` at `apply-all-detect`'s call site."""
    parsed = taad._run_main(
        tmp_path, monkeypatch, envs=["dev-eu"], decision="APPROVED", table=_TABLE
    )
    assert json.loads(parsed["envlevel0_waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            "workload_var": "",
            **_table("apply"),
            "plan_run_id": "123456",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_mode_is_the_layout_and_never_derived_from_a_resolved_field():
    """`layout` decides, and nothing else may. `_TABLE` declares `folder`, which derives no
    identity variables at all, so a mode inferred from `tf_vars` being empty would send an
    adopted repository's every cell down the legacy path -- the same fail-open as
    `matrix.role_arn || vars.AWS_ROLE_ARN`, one layer down. The untabled call carries no
    `layout` and must stay `legacy`.

    Mutation: `"legacy" if not resolved["tf_vars"] else "table"` inside `stamp_rows`.
    """
    rows = [{"stack": "stacks/app", "environment": "dev-eu", "workload": ""}]
    assert bm.stamp_rows(rows, _TABLE, "apply", set()) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "", **_table("apply")}
    ]
    assert bm.stamp_rows(rows, {}, "apply", set()) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "", **_LEGACY}
    ]


def test_a_shared_environment_resolves_the_apply_tier_on_the_plan_path(monkeypatch, tmp_path):
    """The plan detect passes `plan`, and a shared environment still resolves `aws.apply`: one
    GitHub Environment on both paths means one credential on both paths. This is the property
    the `shared-envs` wiring exists for, and `test_shared_envs_wiring_guard.py` pins the two
    hops that carry the value.

    Mutation: pass an empty set as `stamp_rows`' `shared_envs` at `build-matrix`'s call site.
    """
    shared = {
        "layout": "folder",
        "environments": {
            "dev-eu": {"region": "eu-west-1", "aws": {"apply": {"role": _APPLY_ROLE}}}
        },
    }
    outputs, _ = tbm._run_main(
        monkeypatch,
        tmp_path,
        {**_PLAN_ENV, "SHIPMATE_SHARED_ENVS": "dev-eu"},
        head_sha="cafe1234",
        table=shared,
    )
    assert json.loads(outputs["matrix"])["include"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            "workload_var": "",
            **_table("apply"),
        }
    ]
