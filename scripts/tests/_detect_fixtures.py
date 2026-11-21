"""Check-run fixtures shared by the detect-script tests."""

import json
import pathlib
from typing import Any

import _shipmate

HEAD = "0" * 40
APP_ID = "999"
PLAN_SHA = "d" * 64
#: The pull request every fixture record and every apply-detect run names.
PR_NUMBER = "8"


#: The argv `build-matrix.run_graph_deps` runs.
RUN_GRAPH_ARGV = ["terramate", "experimental", "run-graph", "--label", "stack.dir"]


def run_graph_capture(shape):
    """`fixtures/run-graph-<shape>.dot`. Each `cycle-*` capture is `terramate experimental
    run-graph --label stack.dir` written raw to a file (not a TTY, exit 0, empty stderr) by
    Terramate 0.17.1, the `VERSIONS` pin; `cycle-two` holds stacks `a` and `b`, each `after`
    the other."""
    path = pathlib.Path(__file__).parent / "fixtures" / f"run-graph-{shape}.dot"
    return path.read_text(encoding="utf-8")


def answering_run_graph(run, dot):
    """`run`, except that `RUN_GRAPH_ARGV` answers `dot`."""
    return lambda args: dot if args == RUN_GRAPH_ARGV else run(args)


def forbid_shared_runner(monkeypatch):
    """Make `_shipmate._run` raise, so a site that stops passing its own module's runner to
    `_shipmate.check_runs` fails offline here instead of reaching the real `gh`."""

    def _bypassed(args):
        raise AssertionError("listing bypassed the module double")

    monkeypatch.setattr(_shipmate, "_run", _bypassed)


#: The `created_at` `listed_autoplan` gives a run.
AUTOPLAN_CREATED = "2026-11-15T22:47:48Z"


def listed_autoplan(run_id, head, **keys):
    """One run object of the autoplan listing as `apply-gate.autoplan_runs`' `--jq`
    projects it: a `pull_request_target` run of `head` pushed by `k3np`, created at
    `AUTOPLAN_CREATED`, `keys` over it."""
    login = {"login": "k3np"}
    run = {
        "id": run_id,
        "event": "pull_request_target",
        "head_sha": head,
        "created_at": AUTOPLAN_CREATED,
    }
    return run | {"actor": login, "triggering_actor": login} | keys


def autoplans_read(repo, head):
    """The argv `apply-gate.autoplan_runs` lists `head`'s autoplans in `repo` with,
    hand-written."""
    return [
        "gh",
        "api",
        "--paginate",
        (
            f"repos/{repo}/actions/workflows/shipmate.yml/runs?event=pull_request_target"
            f"&head_sha={head}&per_page=100"
        ),
        "--jq",
        (
            ".workflow_runs[] | {id, event, head_sha, status, conclusion, run_attempt, created_at, "
            "actor: {login: .actor.login}, triggering_actor: {login: .triggering_actor.login}} "
            "| tojson"
        ),
    ]


#: The base tip `base_answers` gives a commanded detect.
BASE_TIP = "b" * 40


def base_answers(repo, base="main", tip=BASE_TIP):
    """`(path, body)` pairs answering a commanded detect's pull request read and base branch ref
    read in `repo`: pull request `PR_NUMBER` on base branch `base`, whose tip is `tip`."""
    ref = {"ref": f"refs/heads/{base}", "object": {"sha": tip, "type": "commit"}}
    return [
        (f"repos/{repo}/pulls/{PR_NUMBER}", json.dumps({"base": {"ref": base}})),
        (f"repos/{repo}/git/ref/heads/{base}", json.dumps(ref)),
    ]


def routed(answers, default):
    """A `gh api` body source: the body `answers` maps a request's `repos/` path to (raised when
    it is an exception), else `default()`."""

    def body(url):
        if url not in answers:
            return default()
        if isinstance(answers[url], BaseException):
            raise answers[url]
        return answers[url]

    return body


def stub_base_changed(monkeypatch, bm, changed=(), calls=None):
    """Stub `build-matrix` module `bm`'s `base_changed_stacks` to answer `changed` (raised when
    it is an exception), appending each `(base tip, sorted candidate stacks)` to `calls`."""

    def base_changed(base_ref, candidates):
        if calls is not None:
            calls.append((base_ref, sorted(candidates)))
        if isinstance(changed, BaseException):
            raise changed
        return set(changed)

    monkeypatch.setattr(bm, "base_changed_stacks", base_changed)


def check_run(**kw):
    """One check-run object as the detects' `--jq` projection emits it."""
    base = {
        "name": "apply / stacks/app / dev-eu",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-07-18T10:00:00Z",
        "id": 1,
        "app": {"id": int(APP_ID)},
    }
    base.update(kw)
    return base


def _record(plan_run, plan_sha256=PLAN_SHA, pr=PR_NUMBER, *, cells, action="synchronize"):
    """An `external_id` record as pending-checks writes it for a push's autoplan.
    `plan_sha256=None` omits the digest, `pr=None` the pull request, `cells=None` the run's
    planned cell count, `action=None` the event action."""
    record = {"plan_run": plan_run}
    if plan_sha256 is not None:
        record["plan_sha256"] = plan_sha256
    if pr is not None:
        record["pr"] = pr
    if cells is not None:
        record["cells"] = cells
    if action is not None:
        record["action"] = action
    return json.dumps(record)


def _apply_check(
    stack,
    env="dev-eu",
    plan_run="123456",
    plan_sha256=PLAN_SHA,
    pr=PR_NUMBER,
    *,
    cells,
    action="synchronize",
    **kw,
):
    """A pending App-authored apply check carrying its plan-run record."""
    return check_run(
        name=f"apply / {stack} / {env}",
        status="queued",
        conclusion=None,
        external_id=_record(plan_run, plan_sha256, pr, cells=cells, action=action),
        **kw,
    )


def behind_check(stack, env="dev-eu", plan_run="123457", *, cells, **kw):
    """A queued App-authored apply check carrying a behind record, as pending-checks writes it
    for a cell whose stack the base changed after the branch left it: no digest, `behind`
    true."""
    record = {"plan_run": plan_run, "pr": PR_NUMBER, "cells": cells, "action": "synchronize"}
    return check_run(
        name=f"apply / {stack} / {env}",
        status="queued",
        conclusion=None,
        external_id=json.dumps({**record, "behind": True}),
        **kw,
    )


def behind_message(cells, base="main"):
    """The refusal a bare or targeted detect raises when `cells`, `<stack> / <env>` strings,
    are behind base branch `base`, hand-written."""
    return (
        f"::error::apply refused: {len(cells)} cell(s) behind {base} ({', '.join(cells)}); "
        f"update the branch from {base} and push."
    )


def foreign_message(listed):
    """The refusal a detect raises when `listed` check runs, each named with its plan run,
    record a plan made on a pull request other than `PR_NUMBER`."""
    return (
        f"::error::apply aborted: the plan recorded for {listed} was not made on pull request "
        "#8, so this apply is refused. Re-plan on this pull request (push or comment `shipmate "
        "plan`), then apply again; if another open pull request shares this head commit, the "
        "newest plan decides which one may apply."
    )


def partial_message(listed, *, rerun, several=False, merged=False):
    """The refusal a detect raises when the runs and held-back cells `listed` name published
    only part of a run's apply checks; `rerun` when every run published fewer checks than its
    one planned count, `several` when `listed` names more than one run, `merged` for the
    post-merge deploy, whose remedy is a new pull request."""
    it, runs = ("they", "runs") if several else ("it", "run")
    head = (
        f"::error::apply aborted: {listed}: the published apply checks do not match the cells "
        f"{it} planned, so an ordering among them can be lost. "
    )
    if merged:
        return head + (
            "The pull request has merged, so a new pull request touching these stacks plans and "
            "applies them."
        )
    remedy = (
        f"Re-run the failed jobs of the plan {runs} (not all jobs), then apply again; if the "
        "refusal remains, push"
        if rerun
        else "Push"
    )
    return head + (
        f"{remedy} a commit to the pull request, which plans and publishes every cell afresh, "
        "then apply again."
    )


def wave_cells(wv, parsed):
    """Every cell in the `envlevel<n>_waves` outputs `parsed`, flattened, sized by `waves`
    module `wv`."""
    return [
        c
        for lvl in range(wv.MAX_ENV_LEVELS)
        for w in [json.loads(parsed[f"envlevel{lvl}_waves"])]
        for i in range(wv.MAX_WAVES)
        for c in w[f"wave{i}"]
    ]


#: What a detect reads when a test names no table. `layout` is required, and `folder` derives
#: no identity variables, so a row carries only the stamp and the tier.
MINIMAL_TABLE: dict[str, Any] = {"layout": "folder"}


def stub_read_table(monkeypatch, configs, base=None, order=None, targeted=(), reads=None):
    """Stub `read_table` on every `env-config` instance in `configs`, carrying `order`
    as each entry's `needs` and `targeted` as each named entry's `apply_mode: targeted`.

    Shared by the two ordering detects so neither can drift back to stubbing an ordering
    reader in `waves`: a double there answers whatever the test asked for even when the caller
    has stopped passing the mapping at all. One entry is appended to `reads` per call, which is
    how the one-parse-per-operation count is taken -- and the count needs every instance,
    because `_load` re-executes a module per caller, so a stub on one `env-config` object
    cannot see a read through another.
    """
    cfg = dict(base or MINIMAL_TABLE)
    if order is not None or targeted:
        entries = {env: dict(entry) for env, entry in dict(cfg.get("environments", {})).items()}
        for env, preds in (order or {}).items():
            entries.setdefault(env, {})["needs"] = list(preds)
        for env in targeted:
            entries.setdefault(env, {})["apply_mode"] = "targeted"
        cfg["environments"] = entries

    def read_table():
        if reads is not None:
            reads.append(cfg)
        return dict(cfg)

    for config in configs:
        monkeypatch.setattr(config, "read_table", read_table)


def spy_env_config(monkeypatch, build_matrix):
    """Record the `tagged` each `build_matrix.env_config` call receives, then run the real one.

    The double keeps the real signature, so a caller passing an argument the real function
    does not take still fails."""
    seen, real = [], build_matrix.env_config

    def spy(cells, tagged=None, table=None):
        seen.append(tagged)
        return real(cells, tagged, table=table)

    monkeypatch.setattr(build_matrix, "env_config", spy)
    return seen


def held(cl, stack, env, pr):
    """`{ref: Lock}` for one lock pull request `pr` holds on `stack` in `env`."""
    ref = cl.ref_name(env, stack)
    lock = cl.Lock(ref, "c" * 40, "2026-10-24T00:00:00Z", pr, env, stack, "1.1")
    return {ref: lock}


def stub_read_locks(monkeypatch, cl, locks=None, reads=None):
    """Answer `cl.read_locks` with `locks` (none by default), or raise `locks` when it is an
    exception, appending `(repo, envs)` to `reads` per call. Its `run` default is bound to the
    real runner at import, so an unstubbed read would reach `gh`."""

    def read_locks(repo, envs, *, run=None):
        if reads is not None:
            reads.append((repo, set(envs)))
        if isinstance(locks, BaseException):
            raise locks
        return dict(locks or {})

    monkeypatch.setattr(cl, "read_locks", read_locks)


def dispatched_plan(run_id, pr, status="in_progress", **keys):
    """One run of the `workflow_dispatch` listing of `shipmate.yml`, shaped like `gh api
    'repos/ship-iac/repo-example-stacks/actions/workflows/shipmate.yml/runs?event=
    workflow_dispatch&created=%3E%3D2026-11-15T22%3A47%3A48Z&per_page=100'` (2026-11-16) answers
    one, cut to the keys the engine reads: a `shipmate plan` of pull request `pr` by the App,
    created at `AUTOPLAN_CREATED`, `keys` over it."""
    run = {
        "id": run_id,
        "display_title": f"shipmate · plan pr={pr} ref= comment=",
        "event": "workflow_dispatch",
        "status": status,
        "conclusion": None if status != "completed" else "success",
        "run_attempt": 1,
        "created_at": AUTOPLAN_CREATED,
        "actor": {"login": "shipmate-iac[bot]"},
        "triggering_actor": {"login": "shipmate-iac[bot]"},
    }
    return run | keys


def plan_jobs(run_id, detect="success", attempt=1, names=None, total=None):
    """The jobs listing body of dispatched plan run `run_id`, shaped like `gh api
    'repos/ship-iac/repo-example-stacks/actions/runs/43638853580/jobs?filter=latest&per_page=100'`
    (2026-11-16) answers it, cut to the keys the engine reads: every job `completed` at run
    attempt `attempt`, `plan / detect` concluding `detect`, or absent when `detect` is None.
    `names` replaces the job names, `total` the advertised `total_count`."""
    rows = [
        ("plan / claim", "success"),
        ("release", "skipped"),
        ("plan / facts", "success"),
        ("plan / detect", detect),
        ("plan / dev-eu / stacks/app", "success"),
        ("plan / summary", "success"),
    ]
    rows = [(n, c) for n, c in rows if c is not None]
    if names is not None:
        rows = [(n, "success") for n in names]
    jobs = [
        {
            "id": 132581633286 + i,
            "run_id": run_id,
            "run_attempt": attempt,
            "name": name,
            "status": "completed",
            "conclusion": conclusion,
        }
        for i, (name, conclusion) in enumerate(rows)
    ]
    return json.dumps({"total_count": len(jobs) if total is None else total, "jobs": jobs})


#: The run holding a cell's apply check in the held-check tests, and the detect's own run.
HOLDER = "77"
OWN_RUN = "999"
#: The runner variables `current_run_url` reads, for the detect's run `OWN_RUN`.
RUN_ENV = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": OWN_RUN}


def held_check(repo, link="", *, cells, **kw):
    """`stacks/app / dev-eu`'s App apply check, `in_progress` under run `HOLDER` of `repo`: its
    run page followed by `link`."""
    return {
        **_apply_check("stacks/app", cells=cells, **kw),
        "status": "in_progress",
        "details_url": f"https://github.com/{repo}/actions/runs/{HOLDER}{link}",
    }


def cell_job(part, steps=(("Apply the cell", "success"),), conclusion="success"):
    """The listed `apply` or `save` job (`part`) of `stacks/app / dev-eu` in an apply run,
    attempt 1, with `steps` as `(name, conclusion)` pairs."""
    return {
        "name": f"apply / run / L0 / dev-eu / stacks/app / {part}",
        "status": "completed",
        "conclusion": conclusion,
        "run_attempt": 1,
        "started_at": f"2026-11-20T10:00:0{int(part == 'save')}Z",
        "steps": [{"name": n, "status": "completed", "conclusion": c} for n, c in steps],
    }


def holder_answers(repo, run, jobs=None):
    """`(path, body)` pairs answering run `HOLDER` of `repo`: `run`, the run object's keys
    beside its id, on every run read, and `jobs`, when given, as its one-page jobs listing (a
    string is the listing's whole body)."""
    path = f"repos/{repo}/actions/runs/{HOLDER}"
    answers = [(path, json.dumps({"id": int(HOLDER), **run}))]
    if jobs is not None:
        listing = (
            jobs if isinstance(jobs, str) else json.dumps({"total_count": len(jobs), "jobs": jobs})
        )
        answers.append((f"{path}/jobs?filter=all&per_page=100&page=1", listing))
    return answers


_ENDED = {"status": "completed", "run_attempt": 1}
_BY_HAND = "docs/troubleshooting.md section Return an in-progress apply check by hand"
_UNREADABLE = (
    "::error::apply refused: stacks/app / dev-eu: run 77 could not be read completely; "
    f"re-run `shipmate apply`. If it fails the same way, follow {_BY_HAND}."
)
#: `(link, run, jobs, refusal)` per cause a holder of `held_check` reads as, hand-written: the
#: whole refusal of an apply, or None when the cell applies.
HELD_CASES = {
    "live": (
        "",
        {"status": "in_progress", "run_attempt": 1},
        None,
        (
            "::error::apply refused: stacks/app / dev-eu are held in progress by run 77, which is "
            "still running. Wait for it to end, then re-run `shipmate apply`."
        ),
    ),
    "ended unsaved": (
        "",
        _ENDED,
        [cell_job("apply")],
        (
            "::error::apply refused: stacks/app / dev-eu may have been applied by run 77 without "
            "saving their state; follow docs/troubleshooting.md section The apply step ran and its "
            "state was not saved, then re-plan."
        ),
    ),
    "ended saved": (
        "",
        _ENDED,
        [cell_job("apply"), cell_job("save", steps=())],
        (
            "::error::apply refused: stacks/app / dev-eu were left in progress by run 77, which "
            f"applied them and saved their state. Follow {_BY_HAND}, then re-run `shipmate apply`."
        ),
    ),
    "old job shape": (
        "",
        _ENDED,
        [{**cell_job("apply"), "name": "apply / run / L0 / dev-eu / stacks/app"}],
        (
            "::error::apply refused: stacks/app / dev-eu may have been applied by run 77 without "
            "saving their state; follow docs/troubleshooting.md section The apply step ran and its "
            "state was not saved, then re-plan."
        ),
    ),
    "ended never applied": (
        "",
        _ENDED,
        [cell_job("apply", steps=(("Apply the cell", "skipped"),))],
        None,
    ),
    "unreadable": (
        "",
        {"status": "completed"},
        None,
        _UNREADABLE,
    ),
    "empty listing": ("", _ENDED, [], _UNREADABLE),
    "short listing": (
        "",
        _ENDED,
        json.dumps({"total_count": 2, "jobs": [cell_job("apply")]}),
        _UNREADABLE,
    ),
    "no run named": (
        "/job/12",
        _ENDED,
        None,
        (
            "::error::apply refused: stacks/app / dev-eu: apply check 1 is in progress with no run "
            f"named; follow {_BY_HAND}, then re-run `shipmate apply`."
        ),
    ),
}
