"""Check-run fixtures for the three detect scripts.

All three ask `apply-detect.completed_apply_names` the same question over the
same `gh` output, so the stub contract (which module attribute to patch, the
check-run shape) lives here rather than once per detect -- three copies drift one
at a time, and the one left stubbing the old shape keeps passing against output
`gh` no longer produces.
"""

import json

HEAD = "0" * 40
APP_ID = "999"


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


def completed_names(apply_detect, monkeypatch, checks, app_id=APP_ID):
    """The "already applied" set `apply_detect` reports for `checks`, with only
    the `gh` call stubbed.

    `apply_detect` is the caller's own loaded `apply-detect` module (each detect
    holds its own instance), so the query runs exactly as that detect's `main()`
    runs it -- fetch the listing, then ask it. A raw string in `checks` is passed
    through unparsed, for the malformed-line case.
    """
    jsonl = "\n".join(json.dumps(c) if isinstance(c, dict) else c for c in checks)
    monkeypatch.setattr(apply_detect.bm, "_run", lambda args: jsonl)
    return apply_detect.completed_apply_names(
        apply_detect._check_run_lines("acme/repo", HEAD), app_id
    )
