"""Check-run fixtures for the three detect scripts."""

import json
from typing import Any

HEAD = "0" * 40
APP_ID = "999"
PLAN_SHA = "d" * 64


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


def _record(plan_run, plan_sha256=PLAN_SHA):
    """An `external_id` record as pending-checks writes it. `plan_sha256=None` omits the
    digest."""
    record = {"fingerprint": "a" * 64, "plan_run": plan_run}
    if plan_sha256 is not None:
        record["plan_sha256"] = plan_sha256
    return json.dumps(record)


def _apply_check(stack, env="dev-eu", plan_run="123456", plan_sha256=PLAN_SHA, **kw):
    """A pending App-authored apply check carrying its plan-run record."""
    return check_run(
        name=f"apply / {stack} / {env}",
        status="queued",
        conclusion=None,
        external_id=_record(plan_run, plan_sha256),
        **kw,
    )


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
