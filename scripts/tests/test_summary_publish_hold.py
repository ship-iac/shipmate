"""The summary job publishes no apply check another run owns, and holds the gate before it
publishes any.

`gate-refresh` greens an absent or `pending` gate once every published apply check is complete,
and `deploy-detect`'s queue holds only published checks. A summary job that dies between its first
apply-check POST and `Create/refresh gate` would therefore let an apply of the published cells
green the gate and merge the unpublished ones. `Hold the gate while apply checks publish` writes a
`failure` gate first, which `gate-refresh` refuses to green.

Before that hold it refuses, writing nothing, when a newer plan run of the head holds the gate
anywhere in its status history or published an apply check, so an older run (a first attempt or a
re-run) never replaces a newer run's gate or checks. That refusal ends the step green with a notice
and sets `superseded`, which every later step's `if:` reads, so a re-run of a cancelled autoplan
clears its checks. A failed read of either, or a failed
`pending-checks`, holds whether or not a cell summary was downloaded: a hold is always safe, and a
failed download leaves no cell summary over planned cells.

`Create apply checks` runs after the sticky comment, so a failed `post` or `nothing-changed`
comment write publishes no apply check. With nothing in `bodies.jsonl` it reads nothing;
otherwise it runs `gate-status refuse-newer` first and refuses, writing nothing, over a newer
run's hold: `gate-hold` writes outside the job's concurrency group, after the hold step's read.
`Create/refresh gate` runs `gate-status refuse-newer` again before its final write and refuses,
writing nothing, over a newer run's hold. It skips its self-refresh when this run published a queued
check, which cannot be done yet.

The steps run for real, in order, with `_loader.write_python3_shim`'s `python3` running the real
`gate-status` and `pending-checks`, and a bash `gh` keyed on the whole argv of every call it
accepts. A failed step ends the sequence, as it skips the later default-`success()` steps on a
runner.
"""

import json
import os

import pytest
from _loader import (
    ACTIONS,
    action_steps,
    bash_only,
    load_script,
    read_calls,
    run_lines,
    run_step,
    step_by,
    write_python3_shim,
)

ag = load_script("apply-gate")

HEAD_SHA = "a" * 40
RUN_ID = "42"
RUN_URL = f"https://github.com/acme/demo/actions/runs/{RUN_ID}"
APP_ID = "4242"
HOLD = "Hold the gate while apply checks publish"
CREATE = "Create apply checks (pending / no-changes)"
UPSERT = "Upsert sticky comment"
GATE = "Create/refresh gate"
HOLD_DESCRIPTION = (
    "re-plan this pull request (comment shipmate plan or push): "
    "its plan run ended before writing a final gate"
)
PUBLISH_HOLD = (
    "plan run 42 holds this gate while it publishes; "
    "re-plan (comment shipmate plan or push) if that run has ended"
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
LISTING_FAILED = (
    f"::error::could not list the check runs on {HEAD_SHA}; the step log names the cause"
)
PUBLISH_HOLD_POST = _status_post("failure", PUBLISH_HOLD)
#: `gate-status refuse-newer`'s whole refusal, the one message of both steps' newer-run check.
REFUSED = (
    f"::error::plan run 50 holds the gate of {HEAD_SHA} and is newer than this run ({RUN_ID}); "
    "this run stops without writing its final gate. Re-plan this pull request (comment shipmate "
    "plan or push) if that run did not finish.\n"
)

# Records every call's argv one line per argument, then the stdin of an `--input -` call, then a
# `--` line. FAKE_FAIL_CHECK fails that check-runs POST (1-based); pipeline members run in
# subshells, so its counter lives in a file. The pull request's comment listing fails.
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
elif [ "$1 $2 $5" = "api --paginate repos/acme/demo/issues/7/comments?per_page=100" ]; then
  exit 1
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


def _check_post(slug, cells):
    """The whole POST argv and body pending-checks emits for a changed cell `slug` of a run that
    planned `cells` cells."""
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
                "cells": cells,
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
    refresh_checks=None,
    head=HEAD_SHA,
    plan_result="success",
    planned_cells=None,
):
    """`planned_cells=None` reports as many planned cells as the test wrote cell summaries."""
    if planned_cells is None:
        planned_cells = str(len(list((tmp_path / "cells").rglob("cell.json"))))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    write_python3_shim(bin_dir)
    (bin_dir / "gh").write_text(GH_STUB, encoding="utf-8", newline="\n")
    # Without the exec bit, bash on Linux skips the stub on PATH and runs the real `gh`.
    (bin_dir / "gh").chmod(0o755)
    calls = tmp_path / "calls"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GH_TOKEN": "x",
        "SHIPMATE_HEAD_SHA": head,
        "SHIPMATE_PR_NUMBER": "7",
        "SHIPMATE_APP_ID": APP_ID,
        "SHIPMATE_PLAN_RESULT": plan_result,
        "SHIPMATE_PLANNED_CELLS": planned_cells,
        "HEAD_SHA": HEAD_SHA,
        "STATE": "pending",
        "DESCRIPTION": "waiting",
        "MODE": "post",
        "PR": "7",
        "DOCTOR_WARNED": "false",
        "UNMANAGED": "false",
        "LOST": "false",
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_OUTPUT": str(tmp_path / "github_output"),
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
        # By default a queued apply check: refresh leaves the gate as it is.
        "FAKE_REFRESH_CHECKS": refresh_checks or _lines(_record(1, "a", RUN_ID, status="queued")),
        "FAKE_STATUS_POST_RC": str(status_post_rc),
        "FAKE_FAIL_CHECK": str(fail_check),
    }
    codes, stderr, stdout = [], "", ""
    for name in steps:
        proc = run_step(tmp_path, step_by("summary", name=name)["run"], env)
        codes.append(proc.returncode)
        stderr += proc.stderr
        stdout += proc.stdout
        if proc.returncode != 0:
            break
    return codes, read_calls(calls), stderr, stdout


@bash_only
def test_the_hold_is_written_before_any_apply_check(tmp_path):
    """`APPLY_LIST` pins the hold's whole projection, and `PUBLISH_HOLD_POST` the publish hold's
    whole POST, `state=failure` included: a `pending` hold is what gate-refresh greens.
    Mutations: move the hold below the publish loop -> the hold follows the checks; drop the
    `refuse-newer` read -> the statuses listing is missing; drop `external_id` from the
    projection -> an unexpected listing call; pass the publish hold `"$ended"`, or drop
    `$GITHUB_RUN_ID` from its text -> every publish-hold test red; post `pending` from
    `gate-status hold` -> every hold test red; write `superseded=true` on every path -> an
    output file appears."""
    _write_cells(tmp_path, "a", "b")
    codes, calls, _, _ = _run(tmp_path)
    assert codes == [0, 0]
    assert not (tmp_path / "github_output").exists()
    assert calls == [
        STATUSES_LIST,
        APPLY_LIST,
        PUBLISH_HOLD_POST,
        STATUSES_LIST,
        _check_post("a", 2),
        _check_post("b", 2),
    ]


@bash_only
def test_a_failed_plan_job_holds_and_publishes_no_check(tmp_path):
    """Two cells summarized under a failed plan job: the hold is written and no apply check is
    posted, so a pre-merge apply has nothing to apply. Mutation: skip the gap check in
    `pending-checks` (`gap = None`) -> both check-runs POSTs follow the hold."""
    _write_cells(tmp_path, "a", "b")
    codes, calls, _, _ = _run(tmp_path, plan_result="failure")
    assert codes == [0, 0]
    assert calls == [STATUSES_LIST, APPLY_LIST, PUBLISH_HOLD_POST]


@bash_only
def test_a_failed_check_post_fails_the_step_and_stops_publishing(tmp_path):
    """Mutation: append `|| true` to the check-runs POST -> exit 0 and the third check posts."""
    _write_cells(tmp_path, "a", "b", "c")
    codes, calls, _, _ = _run(tmp_path, fail_check=2)
    assert codes == [0, 1]
    assert calls == [
        STATUSES_LIST,
        APPLY_LIST,
        PUBLISH_HOLD_POST,
        STATUSES_LIST,
        _check_post("a", 3),
        _check_post("b", 3),
    ]


@bash_only
def test_a_post_failing_mid_publish_leaves_a_run_the_apply_refuses(tmp_path):
    """Two cells, the second POST fails: the one published check, read back as a listing
    returns it, makes run 42 incomplete. The failed POST is recorded too, so it is dropped.
    Mutation: drop `cells` from `pending-checks`' `body` record -> `(1, ())`."""
    _write_cells(tmp_path, "a", "b")
    codes, calls, _, _ = _run(tmp_path, fail_check=2)
    assert codes == [0, 1]
    posts = [json.loads(c[-1]) for c in calls if c[:2] == ["api", "repos/acme/demo/check-runs"]]
    listing = [
        json.dumps(
            {
                "name": p["name"],
                "id": i,
                "app": {"id": int(APP_ID)},
                "external_id": p["external_id"],
            }
        )
        for i, p in enumerate(posts[:-1], 1)
    ]
    assert ag.incomplete_runs(listing, APP_ID) == {"42": (1, (2,), False)}


@bash_only
def test_no_planned_cell_writes_no_hold(tmp_path):
    """Mutation: replace the cells test with `true` -> the hold is written."""
    codes, calls, _, _ = _run(tmp_path)
    assert codes == [0, 0]
    assert calls == [STATUSES_LIST, APPLY_LIST]


@bash_only
def test_a_re_run_that_publishes_nothing_still_holds(tmp_path):
    """Every cell's check is this run's (one completed by an apply since, one queued and possibly
    mid-apply): nothing is republished, and the hold stands until the final gate write.
    Mutation: hold only when `bodies.jsonl` is non-empty (`[ -s bodies.jsonl ]`) -> no hold."""
    _write_cells(tmp_path, "a", "b")
    checks = _lines(_record(1, "a", RUN_ID), _record(2, "b", RUN_ID, status="queued"))
    codes, calls, _, _ = _run(tmp_path, apply_checks=checks)
    assert codes == [0, 0]
    assert calls == [STATUSES_LIST, APPLY_LIST, PUBLISH_HOLD_POST]


@bash_only
def test_a_failed_hold_write_publishes_no_check(tmp_path):
    """Mutation: append `|| true` to the publish hold -> the hold step passes and the checks post
    over no hold."""
    _write_cells(tmp_path, "a", "b")
    codes, calls, _, _ = _run(tmp_path, status_post_rc=1)
    assert codes == [1]
    assert calls == [STATUSES_LIST, APPLY_LIST, PUBLISH_HOLD_POST]


@bash_only
def test_the_final_gate_write_replaces_the_hold_and_skips_the_refresh(tmp_path):
    """This run published a queued check, so the refresh is skipped, even with a listing that
    lags the POST and shows only an older run's completed check of the same name.
    Mutations: run `Create/refresh gate`'s held-read for every mode (`[ "$MODE" = "draft" ]` ->
    `true`) -> it reads the hold, keeps it, and never writes the final gate; drop the queued-body
    skip -> the refresh lists the checks and greens the gate."""
    _write_cells(tmp_path, "a")
    codes, calls, _, _ = _run(
        tmp_path, steps=(HOLD, CREATE, GATE), refresh_checks=_lines(_record(1, "a", "41"))
    )
    assert codes == [0, 0, 0]
    assert calls == [
        STATUSES_LIST,
        APPLY_LIST,
        PUBLISH_HOLD_POST,
        STATUSES_LIST,
        _check_post("a", 1),
        STATUSES_LIST,
        _status_post("pending", "waiting"),
    ]


@bash_only
def test_a_newer_runs_hold_refuses_the_final_write(tmp_path):
    """A newer same-head run held the gate after this run's publish hold (its summary was
    evicted). Mutation: `refuse-newer ... || exit 1` -> `|| true` -> a `pending` POST buries the
    hold."""
    codes, calls, stderr, _ = _run(tmp_path, statuses=_lines(_gate("failure", 50)), steps=(GATE,))
    assert (codes, calls, stderr) == ([1], [STATUSES_LIST], REFUSED)


def _write_bodies(tmp_path, text):
    (tmp_path / "bodies.jsonl").write_text(text, encoding="utf-8")


@bash_only
def test_a_newer_runs_hold_refuses_the_apply_checks(tmp_path):
    """A newer same-head run held the gate while this run wrote its comment.
    Mutation: delete the `refuse-newer` line in `Create apply checks` -- the check POST follows."""
    _write_bodies(tmp_path, '{"name": "apply / stacks/a / dev"}\n')
    codes, calls, stderr, _ = _run(tmp_path, statuses=_lines(_gate("failure", 50)), steps=(CREATE,))
    assert (codes, calls, stderr) == ([1], [STATUSES_LIST], REFUSED)


@bash_only
def test_nothing_to_publish_reads_nothing(tmp_path):
    """A held, draft or nothing-changed run passes this step whatever the statuses API does.
    Mutation: drop the `[ -s bodies.jsonl ] || exit 0` guard -- the failed read fails the step."""
    _write_bodies(tmp_path, "")
    codes, calls, _, _ = _run(tmp_path, statuses_rc=1, steps=(CREATE,))
    assert (codes, calls) == ([0], [])


@bash_only
def test_a_failed_comment_write_stops_the_job_before_any_apply_check(tmp_path):
    """The hold, apply-check, comment and gate steps in the order the action file gives them:
    a `post` run whose comment listing fails publishes no apply check and writes no final gate,
    because a failed step skips the default-`success()` steps after it.
    Mutation: move `Create apply checks` back above the mirror step -- its POST follows the hold."""
    _write_cells(tmp_path, "a")
    (tmp_path / "comment.md").write_text("<!-- shipmate:summary -->\n", encoding="utf-8")
    wanted = {HOLD, CREATE, UPSERT, GATE}
    steps = tuple(s["name"] for s in action_steps("summary") if s.get("name") in wanted)
    codes, calls, _, _ = _run(tmp_path, steps=steps)
    assert (codes, calls) == (
        [0, 1],
        [
            STATUSES_LIST,
            APPLY_LIST,
            PUBLISH_HOLD_POST,
            [
                "api",
                "--paginate",
                "--jq",
                '.[] | select(.user.type == "Bot") | {id, head: ((.body // "")[0:64])}',
                "repos/acme/demo/issues/7/comments?per_page=100",
            ],
        ],
    )


@bash_only
def test_a_failed_read_before_the_final_write_writes_nothing(tmp_path):
    """Mutation: `refuse-newer ... || exit 1` -> `|| true` -> a `pending` POST."""
    codes, calls, _, _ = _run(tmp_path, statuses_rc=1, steps=(GATE,))
    assert (codes, calls) == ([1], [STATUSES_LIST])


NEWER_CHECK = (
    "::error::plan run 50, newer than this run (42), published apply checks on this head "
    "(apply / stacks/a / dev); this run publishes nothing over them. That run's summary "
    "writes the gate; if it did not finish, re-plan this pull request (comment shipmate "
    "plan or push).\n"
)


@bash_only
@pytest.mark.parametrize("newest", ["pending", "success"])
def test_a_newer_runs_apply_check_refuses_before_any_write(tmp_path, newest):
    """A newer run published a check but its gate reads `pending` or `success`, no hold. The step
    ends green with the refusal as a notice and sets `superseded`.
    Mutations: write the publish hold before `pending-checks` -> a hold POST; exit pending-checks'
    refusal 1 (`NEWER_RUN = 1`) -> a hold POST; `superseded pending.err` -> `exit 1` -> `[1]`."""
    _write_cells(tmp_path, "a")
    codes, calls, stderr, _ = _run(
        tmp_path,
        statuses=_lines(_gate(newest, 50)),
        apply_checks=_lines(_record(1, "a", "50")),
        steps=(HOLD,),
    )
    assert (codes, calls, stderr) == ([0], [STATUSES_LIST, APPLY_LIST], _notice(NEWER_CHECK))
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == "superseded=true\n"


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
    """The step ends green with the refusal as a notice and sets `superseded`.
    Mutations: read only the newest gate in `newer_hold` -> `behind-own-hold` publishes;
    `[ "$rc" = 4 ]` -> `[ "$rc" = 3 ]` -> both hold (a status POST); `superseded refuse.err` ->
    `exit 1` -> `[1]`."""
    _write_cells(tmp_path, "a")
    codes, calls, stderr, _ = _run(tmp_path, statuses=statuses, steps=(HOLD,))
    assert (codes, calls, stderr) == ([0], [STATUSES_LIST], _notice(REFUSED))
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == "superseded=true\n"


def _notice(error):
    assert error.startswith("::error::")
    return "::notice::" + error.removeprefix("::error::")


NOT_SUPERSEDED = "${{ steps.hold.outputs.superseded != 'true' }}"
#: Every step after the hold, by name (or `uses` for the unnamed one), to its whole `if:`.
LATER_STEP_IFS = {
    "Mirror this run's per-cell plan checks onto the head": (
        "${{ inputs.on-demand == 'true' && steps.hold.outputs.superseded != 'true' }}"
    ),
    "$/actions/python-yaml": NOT_SUPERSEDED,
    "Doctor: settings-drift warnings (annotations only, never blocks)": NOT_SUPERSEDED,
    "Build comment + gate state": NOT_SUPERSEDED,
    "Decide gate state and comment mode": NOT_SUPERSEDED,
    UPSERT: NOT_SUPERSEDED,
    CREATE: NOT_SUPERSEDED,
    GATE: NOT_SUPERSEDED,
}


def test_a_superseded_run_skips_every_later_step():
    """The hold step's `exit 0` does not end the composite, so each later step reads `superseded`
    itself and a superseded run writes nothing.
    Mutations: drop the guard from Mirror, Upsert, `Create apply checks` or `Create/refresh gate`
    -> red each."""
    steps = action_steps("summary")
    later = steps[steps.index(step_by("summary", id="hold")) + 1 :]
    assert {s.get("name") or s["uses"]: s.get("if") for s in later} == LATER_STEP_IFS


def test_the_hold_step_ends_a_superseded_run_green():
    """Mutations: `if [ "$rc" = 4 ]; then exit 1; fi` back -> red; drop `exit 0` from
    `superseded` -> red."""
    lines = run_lines(step_by("summary", name=HOLD))
    for line in [
        "sed 's/^::error::/::notice::/' \"$1\" >&2",
        'echo "superseded=true" >> "$GITHUB_OUTPUT"',
        "exit 0",
        'if [ "$rc" = 4 ]; then superseded refuse.err; fi',
        'if [ "$rc" = 3 ]; then superseded pending.err; fi',
    ]:
        assert line in lines


@bash_only
def test_a_pending_checks_failure_with_cells_holds(tmp_path):
    """Any pending-checks failure but the newer-run refusal holds: here a cell missing its
    `fingerprint`. Mutation: exit before the hold on every non-zero exit (`[ "$rc" = 3 ]` ->
    `[ "$rc" != 0 ]`) -> no POST, and an earlier green stands over the unpublished cells; pass
    that hold the publish text -> red."""
    d = tmp_path / "cells" / "cell-summary.dev.stacks-a"
    d.mkdir(parents=True)
    cell = {"stack": "stacks/a", "environment": "dev", "changed": True}
    (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    codes, calls, _, _ = _run(tmp_path)
    assert (codes, calls) == ([1], [STATUSES_LIST, APPLY_LIST, HOLD_POST])


@bash_only
def test_a_failed_statuses_read_holds(tmp_path):
    """Mutations: delete the `hold` call in the `rc != 0` branch after `refuse-newer` -> no
    POST; pass that hold the publish text -> red."""
    _write_cells(tmp_path, "a")
    codes, calls, _, _ = _run(tmp_path, statuses_rc=1)
    assert (codes, calls) == ([1], [STATUSES_LIST, HOLD_POST])


@bash_only
@pytest.mark.parametrize("status_post_rc", [0, 1], ids=["held", "hold-failed"])
@pytest.mark.parametrize("slugs", [("a",), ()], ids=["cells", "no-cells"])
def test_a_failed_check_listing_holds(tmp_path, slugs, status_post_rc):
    """No cell summary is no proof of no work: a failed download leaves `cells/` empty over
    planned cells. The annotation prints before the hold, so a failed hold write still leaves it.
    Mutations: replace the failed listing's `hold; exit 1` with `true` -> no POST in either case;
    restore the `[ -n "$cells" ]` condition on it -> `no-cells` posts nothing; pass that hold the
    publish text -> red; delete the `::error::` line, or move it after the `hold` -> its absence
    on stdout reddens, `hold-failed` for the move."""
    _write_cells(tmp_path, *slugs)
    codes, calls, _, stdout = _run(tmp_path, apply_rc=1, status_post_rc=status_post_rc)
    assert (codes, calls) == ([1], [STATUSES_LIST, APPLY_LIST, HOLD_POST])
    assert stdout == f"{LISTING_FAILED}\n"


@bash_only
def test_a_pending_checks_failure_without_cells_holds(tmp_path):
    """A malformed check-runs line fails `pending-checks` with no cell summary downloaded.
    Mutations: guard that hold with `[ -n "$cells" ]` -> no POST; pass that hold the publish
    text -> red."""
    codes, calls, _, _ = _run(tmp_path, apply_checks="not json\n")
    assert (codes, calls) == ([1], [STATUSES_LIST, APPLY_LIST, HOLD_POST])


@bash_only
def test_a_malformed_head_fails_the_hold_step_before_any_call(tmp_path):
    """The later steps run only after this one, so it is the head check for all of them. Both
    `refuse-newer` and the `hold` its failure calls refuse the head, so two `::error::` lines.
    Mutation: skip `validate_head_sha` in `gate-status`'s `main` -> the stub records a statuses
    call."""
    _write_cells(tmp_path, "a")
    codes, calls, stderr, _ = _run(tmp_path, head="A" * 40)
    refusal = f"::error::head SHA must be a 40-char lowercase hex SHA (got: {'A' * 40!r})\n"
    assert (codes, calls, stderr) == ([1], [], refusal * 2)
