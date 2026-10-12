"""The summary job publishes no apply check another run owns, and holds the gate before it
publishes any.

`gate-refresh` greens an absent or `pending` gate once every published apply check is complete,
and `deploy-detect`'s queue holds only published checks. A summary job that dies between its first
apply-check POST and `Create/refresh gate` would therefore let an apply of the published cells
green the gate and merge the unpublished ones. `Hold the gate while apply checks publish` writes a
`failure` gate first, which `gate-refresh` refuses to green.

Before that hold it refuses, writing nothing, when a newer plan run of the head holds the gate
anywhere in its status history or published an apply check, so an older run (a first attempt or a
re-run) never replaces a newer run's gate or checks. A failed read of either holds, since a hold is
always safe: the statuses read always, the check-runs listing when cells were planned.

The steps run for real, in order, with `_loader.write_python3_shim`'s `python3` running the real
`gate-status` and `pending-checks`, and a bash `gh` keyed on the whole argv of every call it
accepts. A failed step ends the sequence, as it skips the later default-`success()` steps on a
runner.
"""

import json
import os

import pytest
from _loader import ACTIONS, bash_only, read_calls, run_step, step_by, write_python3_shim

HEAD_SHA = "a" * 40
RUN_ID = "42"
RUN_URL = f"https://github.com/acme/demo/actions/runs/{RUN_ID}"
APP_ID = "4242"
HOLD = "Hold the gate while apply checks publish"
CREATE = "Create apply checks (pending / no-changes)"
GATE = "Create/refresh gate"
HOLD_DESCRIPTION = (
    "re-plan this pull request (comment shipmate plan or push): "
    "its plan run ended before writing a final gate"
)
STATUSES_JQ = ".[] | {context, state, target_url}"
APPLY_CHECKS_JQ = (
    ".check_runs[] | {name, status, conclusion, started_at, id, app: {id: .app.id}, external_id}"
)
REFRESH_CHECKS_JQ = ".check_runs[] | {name, status, conclusion, started_at, id, app: {id: .app.id}}"
STATUSES_LIST = [
    "api",
    "--paginate",
    f"repos/acme/demo/commits/{HEAD_SHA}/statuses?per_page=100",
    "--jq",
    STATUSES_JQ,
]
APPLY_LIST = [
    "api",
    "--paginate",
    f"repos/acme/demo/commits/{HEAD_SHA}/check-runs?filter=all&per_page=100",
    "--jq",
    APPLY_CHECKS_JQ,
]
REFRESH_LIST = [*APPLY_LIST[:4], REFRESH_CHECKS_JQ]


def _status_post(state, description):
    return [
        "api",
        f"repos/acme/demo/statuses/{HEAD_SHA}",
        "-f",
        f"state={state}",
        "-f",
        "context=shipmate / gate",
        "-f",
        f"description={description}",
        "-f",
        f"target_url={RUN_URL}",
    ]


HOLD_POST = _status_post("failure", HOLD_DESCRIPTION)

# Records every call's argv one line per argument, then the stdin of an `--input -` call, then a
# `--` line. FAKE_FAIL_CHECK fails that check-runs POST (1-based); pipeline members run in
# subshells, so its counter lives in a file.
GH_STUB = """\
#!/bin/bash
printf '%s\\n' "$@" >> "$CALLS"
if [ "${*: -1}" = "-" ]; then cat >> "$CALLS"; printf '\\n' >> "$CALLS"; fi
printf -- '--\\n' >> "$CALLS"
head="repos/acme/demo/commits/$FAKE_SHA"
if [ "$*" = "api --paginate $head/statuses?per_page=100 --jq $FAKE_STATUSES_JQ" ]; then
  printf '%s' "$FAKE_STATUSES"
  exit "$FAKE_STATUSES_RC"
elif [ "$*" = "api --paginate $head/check-runs?filter=all&per_page=100 --jq $FAKE_APPLY_JQ" ]; then
  printf '%s' "$FAKE_APPLY_CHECKS"
  exit "$FAKE_APPLY_RC"
elif [ "$*" = "api --paginate $head/check-runs?filter=all&per_page=100 --jq $FAKE_REFRESH_JQ" ]
then
  printf '%s' "$FAKE_REFRESH_CHECKS"
elif [ "$1 $2" = "api repos/acme/demo/statuses/$FAKE_SHA" ]; then
  echo '{}'
  exit "$FAKE_STATUS_POST_RC"
elif [ "$*" = "api repos/acme/demo/check-runs --input -" ]; then
  n=$(( $(cat "$COUNT" 2>/dev/null || echo 0) + 1 ))
  echo "$n" > "$COUNT"
  [ "$n" != "$FAKE_FAIL_CHECK" ]
else
  printf 'unexpected gh call: %s\\n' "$*" >&2
  exit 1
fi
"""


def _gate(state, run):
    return json.dumps(
        {
            "context": "shipmate / gate",
            "state": state,
            "target_url": f"https://github.com/acme/demo/actions/runs/{run}",
        }
    )


def _record(cid, slug, plan_run, status="completed"):
    return json.dumps(
        {
            "name": f"apply / stacks/{slug} / dev",
            "status": status,
            "id": cid,
            "app": {"id": int(APP_ID)},
            "external_id": json.dumps({"plan_run": plan_run}),
        }
    )


def _lines(*items):
    return "".join(f"{i}\n" for i in items)


def _write_cells(tmp_path, *slugs):
    for slug in slugs:
        d = tmp_path / "cells" / f"cell-summary.dev.stacks-{slug}"
        d.mkdir(parents=True)
        cell = {"stack": f"stacks/{slug}", "environment": "dev", "changed": True}
        (d / "cell.json").write_text(
            json.dumps({**cell, "fingerprint": "f" * 64}), encoding="utf-8"
        )
        (d / "plan.txt").write_bytes(b"plan\n")


def _check_post(slug):
    """The whole POST argv and body pending-checks emits for a changed cell `slug`."""
    body = {
        "name": f"apply / stacks/{slug} / dev",
        "head_sha": HEAD_SHA,
        "status": "queued",
        "external_id": json.dumps(
            {
                "fingerprint": "f" * 64,
                "plan_run": RUN_ID,
                # sha256 of b"plan\n", computed once off the tree.
                "plan_sha256": "1b4025dc7b8d27cf38df85e77b20ed44a00851a2c28b338560560d85deded8e3",
                "pr": "7",
            }
        ),
        "output": {
            "title": "apply pending",
            "summary": "Waiting to be applied. Merge after apply completes "
            "for this stack x environment.",
        },
    }
    return ["api", "repos/acme/demo/check-runs", "--input", "-", json.dumps(body)]


def _run(
    tmp_path,
    *,
    statuses="",
    statuses_rc=0,
    apply_checks="",
    apply_rc=0,
    status_post_rc=0,
    fail_check=0,
    steps=(HOLD, CREATE),
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    write_python3_shim(bin_dir)
    (bin_dir / "gh").write_text(GH_STUB, encoding="utf-8", newline="\n")
    calls = tmp_path / "calls"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GH_TOKEN": "x",
        "SHIPMATE_HEAD_SHA": HEAD_SHA,
        "SHIPMATE_PR_NUMBER": "7",
        "SHIPMATE_APP_ID": APP_ID,
        "HEAD_SHA": HEAD_SHA,
        "STATE": "pending",
        "DESCRIPTION": "waiting",
        "MODE": "post",
        "PLAN_RESULT": "success",
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_ACTION_PATH": str(ACTIONS / "summary"),
        "CALLS": str(calls),
        "COUNT": str(tmp_path / "count"),
        "FAKE_SHA": HEAD_SHA,
        "FAKE_STATUSES_JQ": STATUSES_JQ,
        "FAKE_APPLY_JQ": APPLY_CHECKS_JQ,
        "FAKE_REFRESH_JQ": REFRESH_CHECKS_JQ,
        "FAKE_STATUSES": statuses,
        "FAKE_STATUSES_RC": str(statuses_rc),
        "FAKE_APPLY_CHECKS": apply_checks,
        "FAKE_APPLY_RC": str(apply_rc),
        # A queued apply check: refresh leaves the gate as it is.
        "FAKE_REFRESH_CHECKS": _lines(_record(1, "a", RUN_ID, status="queued")),
        "FAKE_STATUS_POST_RC": str(status_post_rc),
        "FAKE_FAIL_CHECK": str(fail_check),
    }
    codes, stderr = [], ""
    for name in steps:
        proc = run_step(tmp_path, step_by("summary", name=name)["run"], env)
        codes.append(proc.returncode)
        stderr += proc.stderr
        if proc.returncode != 0:
            break
    return codes, read_calls(calls), stderr


@bash_only
def test_the_hold_is_written_before_any_apply_check(tmp_path):
    """Mutations: move the hold below the publish loop -> the hold follows the checks; drop the
    `newer-hold` read -> the statuses listing is missing."""
    _write_cells(tmp_path, "a", "b")
    codes, calls, _ = _run(tmp_path)
    assert codes == [0, 0]
    assert calls == [STATUSES_LIST, APPLY_LIST, HOLD_POST, _check_post("a"), _check_post("b")]


@bash_only
def test_a_failed_check_post_fails_the_step_and_stops_publishing(tmp_path):
    """Mutation: append `|| true` to the check-runs POST -> exit 0 and the third check posts."""
    _write_cells(tmp_path, "a", "b", "c")
    codes, calls, _ = _run(tmp_path, fail_check=2)
    assert codes == [0, 1]
    assert calls == [STATUSES_LIST, APPLY_LIST, HOLD_POST, _check_post("a"), _check_post("b")]


@bash_only
def test_no_planned_cell_writes_no_hold(tmp_path):
    """Mutation: replace the cells test with `true` -> the hold is written."""
    codes, calls, _ = _run(tmp_path)
    assert codes == [0, 0]
    assert calls == [STATUSES_LIST, APPLY_LIST]


@bash_only
def test_a_re_run_that_publishes_nothing_still_holds(tmp_path):
    """Every cell's check is this run's (one completed by an apply since, one queued and possibly
    mid-apply): nothing is republished, and the hold stands until the final gate write.
    Mutation: hold only when `bodies.jsonl` is non-empty (`[ -s bodies.jsonl ]`) -> no hold."""
    _write_cells(tmp_path, "a", "b")
    checks = _lines(_record(1, "a", RUN_ID), _record(2, "b", RUN_ID, status="queued"))
    codes, calls, _ = _run(tmp_path, apply_checks=checks)
    assert codes == [0, 0]
    assert calls == [STATUSES_LIST, APPLY_LIST, HOLD_POST]


@bash_only
def test_a_failed_hold_write_publishes_no_check(tmp_path):
    """Mutation: append `|| true` to the publish hold -> the hold step passes and the checks post
    over no hold."""
    _write_cells(tmp_path, "a", "b")
    codes, calls, _ = _run(tmp_path, status_post_rc=1)
    assert codes == [1]
    assert calls == [STATUSES_LIST, APPLY_LIST, HOLD_POST]


@bash_only
def test_the_final_gate_write_replaces_the_hold(tmp_path):
    """Mutation: run `Create/refresh gate`'s held-read for every mode (`[ "$MODE" = "draft" ]` ->
    `true`) -> it reads the hold, keeps it, and never writes the final gate."""
    _write_cells(tmp_path, "a")
    codes, calls, _ = _run(tmp_path, steps=(HOLD, CREATE, GATE))
    assert codes == [0, 0, 0]
    assert calls == [
        STATUSES_LIST,
        APPLY_LIST,
        HOLD_POST,
        _check_post("a"),
        _status_post("pending", "waiting"),
        REFRESH_LIST,
    ]


NEWER_CHECK = (
    "::error::plan run 50, newer than this run (42), published apply checks on this head "
    "(apply / stacks/a / dev); this run publishes nothing over them. That run's summary "
    "writes the gate; if it did not finish, re-plan this pull request (comment shipmate "
    "plan or push).\n"
)


@bash_only
@pytest.mark.parametrize("newest", ["pending", "success"])
def test_a_newer_runs_apply_check_refuses_before_any_write(tmp_path, newest):
    """A newer run published a check but its gate reads `pending` or `success`, no hold.
    Mutation: write the publish hold before `pending-checks` -> a hold POST."""
    _write_cells(tmp_path, "a")
    codes, calls, stderr = _run(
        tmp_path,
        statuses=_lines(_gate(newest, 50)),
        apply_checks=_lines(_record(1, "a", "50")),
    )
    assert (codes, calls, stderr) == ([1], [STATUSES_LIST, APPLY_LIST], NEWER_CHECK)


REFUSED = (
    f"::error::plan run 50 holds the gate of {HEAD_SHA} and is newer than this run ({RUN_ID}); "
    "this run publishes nothing. Re-plan this pull request (comment shipmate plan or push) if "
    "that run did not finish.\n"
)


@bash_only
@pytest.mark.parametrize(
    "statuses",
    [
        _lines(_gate("failure", 50)),
        # This run's own hold, written after a failed read, on top of the newer run's.
        _lines(_gate("failure", RUN_ID), _gate("failure", 50), _gate("pending", 41)),
    ],
    ids=["newer-hold", "behind-own-hold"],
)
def test_a_newer_runs_hold_refuses_before_any_write(tmp_path, statuses):
    """Mutations: read only the newest gate in `newer-hold` -> `behind-own-hold` publishes;
    drop the `newer-hold` refusal -> both publish."""
    _write_cells(tmp_path, "a")
    codes, calls, stderr = _run(tmp_path, statuses=statuses)
    assert (codes, calls, stderr) == ([1], [STATUSES_LIST], REFUSED)


@bash_only
def test_a_failed_statuses_read_holds(tmp_path):
    """Mutation: drop the hold on a failed `newer-hold` -> no POST."""
    _write_cells(tmp_path, "a")
    codes, calls, _ = _run(tmp_path, statuses_rc=1)
    assert (codes, calls) == ([1], [STATUSES_LIST, HOLD_POST])


@bash_only
def test_a_failed_check_listing_with_cells_holds(tmp_path):
    """Mutation: drop the hold on a failed listing -> no POST, and an earlier green stands over
    the unpublished cells."""
    _write_cells(tmp_path, "a")
    codes, calls, _ = _run(tmp_path, apply_rc=1)
    assert (codes, calls) == ([1], [STATUSES_LIST, APPLY_LIST, HOLD_POST])


@bash_only
def test_a_failed_check_listing_without_cells_fails_without_a_write(tmp_path):
    """Mutation: swallow the listing's failure -> exit 0."""
    codes, calls, _ = _run(tmp_path, apply_rc=1)
    assert (codes, calls) == ([1], [STATUSES_LIST, APPLY_LIST])
