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


def _record(plan_run, plan_sha256=PLAN_SHA, pr=PR_NUMBER):
    """An `external_id` record as pending-checks writes it. `plan_sha256=None` omits the
    digest, `pr=None` the pull request."""
    record = {"fingerprint": "a" * 64, "plan_run": plan_run}
    if plan_sha256 is not None:
        record["plan_sha256"] = plan_sha256
    if pr is not None:
        record["pr"] = pr
    return json.dumps(record)


def _apply_check(stack, env="dev-eu", plan_run="123456", plan_sha256=PLAN_SHA, pr=PR_NUMBER, **kw):
    """A pending App-authored apply check carrying its plan-run record."""
    return check_run(
        name=f"apply / {stack} / {env}",
        status="queued",
        conclusion=None,
        external_id=_record(plan_run, plan_sha256, pr),
        **kw,
    )


def foreign_message(listed):
    """The refusal a detect raises when `listed` check runs record a plan made on a pull
    request other than `PR_NUMBER`."""
    return (
        f"::error::apply aborted: the plan recorded for {listed} was not made on pull request "
        "#8, so this apply is refused. Re-plan on this pull request (push or comment `shipmate "
        "plan`), then apply again; if another open pull request shares this head commit, the "
        "newest plan decides which one may apply."
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


def stub_read_table(monkeypatch, configs, base=None, order=None, explicit=(), reads=None):
    """Stub `read_table` on every `env-config` instance in `configs`, carrying `order`
    as each entry's `needs` and `explicit` as each named entry's `explicit: true`.

    Shared by the two ordering detects so neither can drift back to stubbing an ordering
    reader in `waves`: a double there answers whatever the test asked for even when the caller
    has stopped passing the mapping at all. One entry is appended to `reads` per call, which is
    how the one-parse-per-operation count is taken -- and the count needs every instance,
    because `_load` re-executes a module per caller, so a stub on one `env-config` object
    cannot see a read through another.
    """
    cfg = dict(base or MINIMAL_TABLE)
    if order is not None or explicit:
        entries = {env: dict(entry) for env, entry in dict(cfg.get("environments", {})).items()}
        for env, preds in (order or {}).items():
            entries.setdefault(env, {})["needs"] = list(preds)
        for env in explicit:
            entries.setdefault(env, {})["explicit"] = True
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
