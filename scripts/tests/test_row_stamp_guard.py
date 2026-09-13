"""Every matrix row every detect emits carries `config_mode`, the field the cell actions'
`config-mode` input reads.

`scripts/env-inject` refuses a mode it does not recognise and there is no default anywhere on
the route, so a row that reaches a cell without this field fails that cell -- and one call site
left unstamped fails one workflow while the other five stay green. The stamp is applied at each
call site rather than inside `build_matrix`, which stays pure, so the sites are an enumeration
and each one needs its own assertion.

Each assertion runs the detect's own `main()` through the stub scaffolding its module already
owns, and compares the whole first row against a hand-written constant: the stamp wraps whatever
the builder returned, so the constant is that module's fixture shape plus the new key.
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


def test_the_table_names_every_script_that_can_emit_rows():
    """Both sides derived: the glob against the table above. Every other test here names its
    call site by hand, and a hand-written list does not notice a seventh detect script.

    Mutation: add a `scripts/x-detect` file and watch this red with no edit to the guard.
    """
    found = {p.name for p in SCRIPTS.glob("*-detect")} | {"build-matrix"}
    assert found == set(_PRODUCERS)


def test_the_plan_matrix_rows_carry_the_mode(monkeypatch, tmp_path):
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
            "config_mode": "legacy",
        }
    ]


def test_the_drift_matrix_rows_carry_the_mode(monkeypatch, tmp_path):
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
            "config_mode": "legacy",
        }
    ]


def test_the_deploy_rows_carry_the_mode(monkeypatch, tmp_path):
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
            "workload_var": "",
            "config_mode": "legacy",
            "plan_run_id": "123456",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_unlock_rows_carry_the_mode(monkeypatch, tmp_path):
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
        "config_mode": "legacy",
    }


def test_the_targeted_apply_rows_carry_the_mode(monkeypatch, tmp_path):
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
            "workload_var": "APP",
            "config_mode": "legacy",
            "plan_run_id": "42",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_bare_apply_all_rows_carry_the_mode(monkeypatch, tmp_path):
    """Call site 6: `apply-all-detect` main(), which reaches `cells_for_env` through its own
    `cells_from_checks`.

    Mutation: drop the `stamp_rows` wrapper from `apply-all-detect`.
    """
    parsed = taad._run_main(tmp_path, monkeypatch, envs=["dev-eu"], decision="APPROVED")
    assert json.loads(parsed["envlevel0_waves"])["wave0"] == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload_var": "",
            "config_mode": "legacy",
            "plan_run_id": "123456",
            "plan_sha256": PLAN_SHA,
        }
    ]


def test_the_mode_is_the_literal_and_never_derived_from_a_row():
    """One mode ships today, so this reads as a tautology. It is not: it is what stops a later
    release inferring the mode from whether some other field is empty, which would send half a
    repository's cells down the legacy path and half down the table path. Both rows carry the
    same mode and they differ in `workload_var`, so a truthiness test on a row field reddens.

    Mutation: `"legacy" if not cell["workload_var"] else "table"` inside `stamp_rows`.
    """
    rows = [
        {"stack": "stacks/app", "environment": "dev-eu", "workload_var": "APP"},
        {"stack": "stacks/dns", "environment": "dev-eu", "workload_var": ""},
    ]
    assert bm.stamp_rows(rows, "legacy") == [
        {
            "stack": "stacks/app",
            "environment": "dev-eu",
            "workload_var": "APP",
            "config_mode": "legacy",
        },
        {
            "stack": "stacks/dns",
            "environment": "dev-eu",
            "workload_var": "",
            "config_mode": "legacy",
        },
    ]
