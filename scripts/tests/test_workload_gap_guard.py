"""The unlisted-workload refusal (`build-matrix`'s `refuse_workload_gaps`) sees only the cells a
path will run: a completed, excluded or held cell is not refused, because a table-only change can
make an applied cell look misconfigured and refusing over it strands the pull request that
planned it.

The refusal is called at each site rather than inside `stamp_rows`, so the sites are an
enumeration and each needs its own assertion. Each site test runs that detect's own `main()`
with a recording double in place of the refusal on that script's own `build-matrix` instance
(`_load` returns a fresh module per caller) and compares every recorded call whole against a
hand-written constant. Each fixture holds a cell the path does not run, so passing the
unfiltered set records a different value.

The behavioural tests run the real refusal on each apply path: a pending cell with an unlisted
tag refuses, and the same cell completed or excluded does not.
"""

import json

import pytest
import test_apply_all_detect as taad
import test_apply_detect as tad
import test_build_matrix as tbm
import test_deploy_detect as tdd
import test_row_stamp_guard as trs
from _detect_fixtures import PLAN_SHA, _apply_check, check_run

_ROLE = "arn:aws:iam::1:role/net"

#: The apply tier lists only `net` and resolves no role of its own, so a `workload/app` cell
#: would run with no cloud credentials.
_GAP_ENTRY = {"region": "eu-west-1", "aws": {"apply": {"workloads": {"net": {"role": _ROLE}}}}}
_GAP = {"layout": "folder", "environments": {"dev-eu": _GAP_ENTRY}}

_GAP_ERROR = (
    "::error::stacks/app in dev-eu carries workload/app, which aws.apply.workloads does not "
    "list (it lists: net), and aws.apply sets no role to fall back to. The cell would run with "
    "no cloud credentials. Retag the stack, or add the workload to .github/shipmate.toml on the "
    "default branch, which is where this table is read from: merge the workload entry there on "
    "its own pull request first."
)

#: The apply-tier fields `trs._TABLE` resolves for a dev-eu cell.
_APPLY = {
    "role_arn": "arn:aws:iam::1:role/apply",
    "cred_region": "eu-west-1",
    "tf_vars": {},
    "config_path": "apply",
    "env_binding": "dev-eu-apply",
}
_PLAN = {
    "role_arn": "arn:aws:iam::1:role/plan",
    "cred_region": "eu-west-1",
    "tf_vars": {},
    "config_path": "plan",
    "env_binding": "dev-eu-plan",
}


def _spy(monkeypatch, bm):
    calls = []
    monkeypatch.setattr(
        bm, "refuse_workload_gaps", lambda rows, table, tier: calls.append((rows, tier))
    )
    return calls


def _done(stack, env="dev-eu"):
    return check_run(name=f"apply / {stack} / {env}")


def test_the_plan_matrix_refuses_over_the_stamped_cells(monkeypatch, tmp_path):
    """Call site 1: `build-matrix` main(), plan tier. Every stamped cell is planned.

    Mutations: call the refusal before `stamp_rows` (the rows lack the resolved fields); pass
    `"apply"` (the plan path's apply-tier check is keyed on `"plan"`)."""
    calls = _spy(monkeypatch, tbm.bm)
    tbm._run_main(monkeypatch, tmp_path, trs._PLAN_ENV, head_sha="cafe1234", table=trs._TABLE)
    assert calls == [
        ([{"stack": "stacks/app", "environment": "dev-eu", "workload": "", **_PLAN}], "plan")
    ]


def test_the_drift_matrix_refuses_over_the_stamped_cells(monkeypatch, tmp_path):
    """Call site 2: the same line, reached with `all-stacks: true`.

    Mutations: as above."""
    calls = _spy(monkeypatch, tbm.bm)
    tbm._run_main(monkeypatch, tmp_path, trs._DRIFT_ENV, table=trs._TABLE)
    assert calls == [
        ([{"stack": "stacks/app", "environment": "dev-eu", "workload": "", **_PLAN}], "plan")
    ]


def test_the_deploy_refuses_over_the_pending_cells_only(monkeypatch, tmp_path):
    """Call site 3: `deploy-detect` main(). stacks/dns was applied pre-merge.

    Mutation: pass `cells` instead of `pending` -- stacks/dns is recorded too."""
    calls = _spy(monkeypatch, tdd.dd.bm)
    tdd._run_main(
        tmp_path,
        monkeypatch,
        cells=[tdd._cell("stacks/app"), tdd._cell("stacks/dns")],
        checks=[_apply_check("stacks/app"), _done("stacks/dns")],
        table=trs._TABLE,
    )
    assert calls == [
        (
            [
                {
                    "stack": "stacks/app",
                    "environment": "dev-eu",
                    "workload": "",
                    **_APPLY,
                    "plan_run_id": "123456",
                    "plan_sha256": PLAN_SHA,
                }
            ],
            "apply",
        )
    ]


def test_the_targeted_apply_refuses_over_the_pending_cells_only(monkeypatch, tmp_path):
    """Call site 4: `apply-detect` main(). stacks/dns's apply check already completed.

    Mutation: pass `cells` instead of `pending` -- stacks/dns is recorded too."""
    calls = _spy(monkeypatch, tad.ad.bm)
    tad._apply_env(monkeypatch, tmp_path, table=trs._TABLE)
    tad._stub_apply(
        monkeypatch,
        {"stacks/app": set(), "stacks/dns": set()},
        [_apply_check("stacks/app", plan_run="42"), _done("stacks/dns")],
    )
    tad.ad.main()
    assert calls == [
        (
            [
                {
                    "stack": "stacks/app",
                    "environment": "dev-eu",
                    "workload": "app",
                    **_APPLY,
                    "plan_run_id": "42",
                    "plan_sha256": PLAN_SHA,
                }
            ],
            "apply",
        )
    ]


def test_the_unlock_refuses_over_the_queue_only(monkeypatch, tmp_path):
    """Call site 5: `run_unlock`. Only stacks/app has a pending check; stacks/dns and stacks/db
    are in dev-eu and outside the queue.

    Mutation: build the cells from the whole of `stacks_by_env` -- stacks/db and stacks/dns are
    recorded too."""
    calls = _spy(monkeypatch, tad.ad.bm)
    tad._unlock_env(monkeypatch, tmp_path, table=trs._TABLE)
    tad._boom_on_plan_path(monkeypatch)
    tad._stub_unlock_tree(
        monkeypatch,
        tad._DEV_EU_CELLS,
        [check_run(name="apply / stacks/app / dev-eu", status="in_progress", conclusion=None)],
    )
    tad.ad.main()
    assert calls == [
        ([{"stack": "stacks/app", "environment": "dev-eu", "workload": "app", **_APPLY}], "apply")
    ]


def test_the_bare_apply_refuses_over_the_runnable_cells_only(monkeypatch, tmp_path):
    """Call site 6: `apply-all-detect` main(). prod-eu is explicit, so its cell stays pending
    for a targeted apply and does not run here.

    Mutation: pass `pending` instead of `runnable` -- the prod-eu cell is recorded too."""
    calls = _spy(monkeypatch, taad.aad.bm)
    taad._run_main(
        tmp_path,
        monkeypatch,
        envs=["dev-eu", "prod-eu"],
        explicit=["prod-eu"],
        decision="APPROVED",
        table=trs._TABLE,
    )
    assert calls == [
        (
            [
                {
                    "stack": "stacks/app",
                    "environment": "dev-eu",
                    "workload": "",
                    **_APPLY,
                    "plan_run_id": "123456",
                    "plan_sha256": PLAN_SHA,
                }
            ],
            "apply",
        )
    ]


@pytest.mark.parametrize("pending", [True, False], ids=["pending", "completed"])
def test_the_deploy_refuses_an_unlisted_workload_only_on_a_pending_cell(
    monkeypatch, tmp_path, pending
):
    """Mutation: pass `cells` instead of `pending` -- the completed case refuses."""
    check = _apply_check("stacks/app") if pending else _done("stacks/app")

    def run():
        return tdd._run_main(
            tmp_path,
            monkeypatch,
            cells=[{"stack": "stacks/app", "environment": "dev-eu", "workload": "app"}],
            checks=[check],
            table=_GAP,
        )

    if pending:
        with pytest.raises(SystemExit) as exc:
            run()
        assert exc.value.code == _GAP_ERROR
    else:
        assert json.loads(run()["envlevel0_waves"])["wave0"] == []


@pytest.mark.parametrize("pending", [True, False], ids=["pending", "completed"])
def test_the_targeted_apply_refuses_an_unlisted_workload_only_on_a_pending_cell(
    monkeypatch, tmp_path, pending
):
    """Mutation: pass `cells` instead of `pending` -- the completed case refuses."""
    out = tad._apply_env(monkeypatch, tmp_path, table=_GAP)
    check = _apply_check("stacks/app", plan_run="42") if pending else _done("stacks/app")
    tad._stub_apply(monkeypatch, {"stacks/app": set()}, [check])
    if pending:
        with pytest.raises(SystemExit) as exc:
            tad.ad.main()
        assert exc.value.code == _GAP_ERROR
    else:
        tad.ad.main()
        assert json.loads(tad._parsed(out)["waves"])["wave0"] == []


@pytest.mark.parametrize("pending", [True, False], ids=["pending", "completed"])
def test_the_unlock_refuses_an_unlisted_workload_only_on_a_queued_cell(
    monkeypatch, tmp_path, pending
):
    """Mutation: build the cells from the whole of `stacks_by_env` -- the completed case
    refuses."""
    out = tad._unlock_env(monkeypatch, tmp_path, table=_GAP)
    tad._boom_on_plan_path(monkeypatch)
    status = {"status": "in_progress", "conclusion": None} if pending else {}
    tad._stub_unlock_tree(
        monkeypatch,
        [{"stack": "stacks/app", "environment": "dev-eu", "workload": "app"}],
        [check_run(name="apply / stacks/app / dev-eu", **status)],
    )
    if pending:
        with pytest.raises(SystemExit) as exc:
            tad.ad.main()
        assert exc.value.code == _GAP_ERROR
    else:
        tad.ad.main()
        assert json.loads(tad._parsed(out)["cells"]) == []


@pytest.mark.parametrize("explicit", [False, True], ids=["runnable", "excluded"])
def test_the_bare_apply_refuses_an_unlisted_workload_only_on_a_runnable_cell(
    monkeypatch, tmp_path, explicit
):
    """dev-eu carries the gap; dev-us applies either way.

    Mutation: pass `pending` instead of `runnable` -- the excluded case refuses."""

    def run():
        return taad._run_main(
            tmp_path,
            monkeypatch,
            envs=["dev-eu", "dev-us"],
            explicit=["dev-eu"] if explicit else [],
            decision="APPROVED",
            tags={"stacks/app": ["env/dev-eu", "env/dev-us", "workload/app"]},
            table=_GAP,
        )

    if explicit:
        assert taad._wave_envs(run()) == ["dev-us"]
    else:
        with pytest.raises(SystemExit) as exc:
            run()
        assert exc.value.code == _GAP_ERROR
