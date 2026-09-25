"""Every matrix row every detect emits carries all five resolved fields: `role_arn`,
`cred_region`, `tf_vars`, `config_path` and `env_binding`. Only `tf_vars` reaches a cell action,
as its `tf-vars` input; `role_arn` and `cred_region` are read by the job's credentials step,
`env_binding` names the GitHub Environment the job binds, and `config_path` is diagnostic and
read by nothing (`CONTRACT.md` §Resolution).

`scripts/env-inject` refuses anything but a JSON object of strings and there is no default
anywhere on the route, so a row that reaches a cell without `tf_vars` fails that cell -- and one
call site left unstamped fails one workflow while the other five stay green. The stamp is applied
at each call site rather than inside `build_matrix`, which stays pure, so the sites are an
enumeration and each one needs its own assertion.

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
from _loader import SCRIPTS

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

_PLAN_ROLE = "arn:aws:iam::1:role/plan"
_APPLY_ROLE = "arn:aws:iam::1:role/apply"

#: A table whose two tiers hold distinct roles, so a detect resolving the wrong tier is a
#: different value rather than the same one. `layout = "folder"` derives no `tf_vars`, so an
#: empty `tf_vars` on a stamped row is legitimate and is not evidence of a missing stamp.
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
    """The five fields `_TABLE` resolves for a dev-eu cell on `tier`. Every site's assertion
    compares whole rows against this, so dropping `env_binding` from `resolve` reddens every site.
    """
    return {
        "role_arn": _PLAN_ROLE if tier == "plan" else _APPLY_ROLE,
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": tier,
        "env_binding": "dev-eu-plan" if tier == "plan" else "dev-eu-apply",
    }


def test_the_table_names_every_script_that_can_emit_rows():
    """Both sides derived: the glob against the table above. Every other test here names its
    call site by hand, and a hand-written list does not notice a seventh detect script.

    Mutation: add a `scripts/x-detect` file and watch this red with no edit to the guard.
    """
    found = {p.name for p in SCRIPTS.glob("*-detect")} | {"build-matrix"}
    assert found == set(_PRODUCERS)


def test_the_plan_matrix_resolves_the_plan_tier(monkeypatch, tmp_path):
    """Call site 1: `build-matrix` main(). The plan workflow reads the plan credential, and a
    cell that resolved `apply.role` here would hand a pull request's plan the role that
    mutates infrastructure.

    Mutations: pass `"apply"` at `build-matrix`'s call site; drop the `stamp_rows` wrapper
    from it, which refuses every plan cell of every pull request.
    """
    outputs, _ = tbm._run_main(monkeypatch, tmp_path, _PLAN_ENV, head_sha="cafe1234", table=_TABLE)
    assert json.loads(outputs["matrix"])["include"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            **_table("plan"),
        }
    ]


def test_the_drift_matrix_resolves_the_plan_tier(monkeypatch, tmp_path):
    """Call site 2: the same `build-matrix` line, reached with `all-stacks: true`. Drift plans
    and never applies, and the nightly run is the one nobody watches.

    Mutations: as above -- one argument and one wrapper, so both cases red together.
    """
    outputs, _ = tbm._run_main(monkeypatch, tmp_path, _DRIFT_ENV, table=_TABLE)
    assert json.loads(outputs["matrix"])["include"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            **_table("plan"),
        }
    ]


def test_the_deploy_rows_resolve_the_apply_tier(monkeypatch, tmp_path):
    """Call site 3: `deploy-detect` main(), which reaches the builder through `compute_cells`
    and so is invisible to a search for `build_matrix`.

    Mutations: pass `"plan"` at that call site, and every post-merge apply runs with the
    read-only role; drop the `stamp_rows` wrapper from it.
    """
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
            **_table("apply"),
            "plan_run_id": "123456",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_unlock_rows_resolve_the_apply_tier(monkeypatch, tmp_path):
    """Call site 4: `run_unlock`, which returns from main() early -- a stamp in any main() tail
    skips the unlock path entirely. Unlock releases a state lock, which the plan credential
    cannot do.

    Mutations: pass `"plan"` in `run_unlock`; drop its `stamp_rows` wrapper.
    """
    out = tad._unlock_env(monkeypatch, tmp_path, table=_TABLE)
    tad._boom_on_plan_path(monkeypatch)
    tad._stub_unlock_tree(monkeypatch, tad._DEV_EU_CELLS)
    tad.ad.main()
    assert json.loads(tad._parsed(out)["cells"])[0] == {
        "stack": "stacks/app",
        "environment": "dev-eu",
        "workload": "app",
        **_table("apply"),
    }


def test_the_targeted_apply_rows_resolve_the_apply_tier(monkeypatch, tmp_path):
    """Call site 5: `apply-detect` main(), the targeted `shipmate apply <env>` path.

    Mutations: pass `"plan"` at that call site; drop its `stamp_rows` wrapper.
    """
    out = tad._apply_env(monkeypatch, tmp_path, table=_TABLE)
    tad._stub_apply(monkeypatch, {"stacks/app": set()}, [_apply_check("stacks/app", plan_run="42")])
    tad.ad.main()
    assert json.loads(tad._parsed(out)["waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "app",
            **_table("apply"),
            "plan_run_id": "42",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_bare_apply_all_rows_resolve_the_apply_tier(monkeypatch, tmp_path):
    """Call site 6: `apply-all-detect` main(), which reaches `cells_for_env` through its own
    `cells_from_checks`.

    Mutations: pass `"plan"` at that call site; drop its `stamp_rows` wrapper.
    """
    parsed = taad._run_main(
        tmp_path, monkeypatch, envs=["dev-eu"], decision="APPROVED", table=_TABLE
    )
    assert json.loads(parsed["envlevel0_waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            **_table("apply"),
            "plan_run_id": "123456",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_a_shared_environment_resolves_the_apply_tier_on_the_plan_path(monkeypatch, tmp_path):
    """The plan detect passes `plan`, and an entry holding `shared = true` still resolves
    `aws.apply` and binds the bare environment: one GitHub Environment on both paths means one
    credential on both paths.

    Mutation: pass `"plan"` as `config_path` for a shared entry in `resolve`, or bind
    `f"{env}-{path}"` unconditionally.
    """
    shared = {
        "layout": "folder",
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "shared": True,
                "aws": {"apply": {"role": _APPLY_ROLE}},
            }
        },
    }
    outputs, _ = tbm._run_main(monkeypatch, tmp_path, _PLAN_ENV, head_sha="cafe1234", table=shared)
    assert json.loads(outputs["matrix"])["include"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload": "",
            **_table("apply"),
            "env_binding": "dev-eu",
        }
    ]
