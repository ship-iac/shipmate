"""Check-run fixtures for the three detect scripts.

All three ask `apply-detect.completed_apply_names` the same question over the same `gh` output,
so the stub contract -- which module attribute to patch, the check-run shape -- lives here rather
than once per detect. Three copies drift one at a time, and the one left stubbing the old shape
keeps passing against output `gh` no longer produces.
"""

import json

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
    digest, which is what a check written before the engine recorded one looks like."""
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


def completed_names(apply_detect, monkeypatch, checks, app_id=APP_ID):
    """The "already applied" set `apply_detect` reports for `checks`, with only the `gh` call
    stubbed.

    `apply_detect` is the caller's own loaded `apply-detect` module, each detect holding its own
    instance, so the query runs exactly as that detect's `main()` runs it: fetch the listing,
    then ask it. A raw string in `checks` is passed through unparsed, for the malformed-line
    case.
    """
    jsonl = "\n".join(json.dumps(c) if isinstance(c, dict) else c for c in checks)
    monkeypatch.setattr(apply_detect.bm, "_run", lambda args: jsonl)
    return apply_detect.completed_apply_names(
        apply_detect._check_run_lines("acme/repo", HEAD), app_id
    )
