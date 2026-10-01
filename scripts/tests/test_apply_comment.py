import io
import json
import pathlib
import re

import pytest
from _loader import ENGINE as _ENGINE
from _loader import load_script, workflow_yaml

ac = load_script("apply-comment")
eo = load_script("env-order")
wv = load_script("waves")

RUN_URL = "https://gh/run/1"


@pytest.fixture(autouse=True)
def _run_context(monkeypatch):
    """The runner defaults `provenance` reads for the verdict's commit and run links."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://gh")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "7")


def _cell(**kw):
    base = {
        "stack": "app",
        "stack_path": "stacks/app",
        "environment": "dev-eu",
        "result": "applied",
        "reason": "",
    }
    base.update(kw)
    return base


def _write_cell(cells_dir, env, slug, cell):
    d = cells_dir / f"apply-summary.{env}.{slug}"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    return d


def _row(**kw):
    base = {
        "environment": "dev-eu",
        "stack_path": "stacks/app",
        "stack_display": "app",
        "status": "applied",
        "reason": "",
        "apply_text": "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.",
    }
    base.update(kw)
    return base


def _job(name, url):
    return {"name": name, "html_url": url}


def _fixture_text(name):
    return (pathlib.Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_build_rows_statuses_and_not_attempted_for_missing_artifact():
    expected = {("dev-eu", "stacks/app"), ("dev-eu", "stacks/missing")}
    downloaded = [
        (_cell(result="applied"), "Apply complete! Resources: 1 added, 0 changed, 0 destroyed."),
        (
            _cell(stack="db", stack_path="stacks/db", environment="dev-us", result="failed"),
            "Error: boom",
        ),
    ]
    rows = ac.build_rows(expected, downloaded)
    by_key = {(r["environment"], r["stack_path"]): r for r in rows}
    assert by_key[("dev-eu", "stacks/app")]["status"] == "applied"
    assert by_key[("dev-us", "stacks/db")]["status"] == "failed"
    missing = by_key[("dev-eu", "stacks/missing")]
    assert missing["status"] == "not_attempted"
    assert missing["stack_display"] == "stacks/missing"  # No artifact arrived, so no display name.


def test_build_rows_downloaded_cell_outside_expected_set_still_rendered():
    # Never silently drop evidence that an apply ran, even if it wasn't in the
    # expected wave set (e.g. a stale expected-set computation).
    downloaded = [(_cell(), "text")]
    rows = ac.build_rows(set(), downloaded)
    assert len(rows) == 1
    assert rows[0]["status"] == "applied"


def test_build_rows_sorted_by_environment_then_stack():
    downloaded = [
        (_cell(stack="z", stack_path="stacks/z", environment="dev-eu"), "t"),
        (_cell(stack="a", stack_path="stacks/a", environment="dev-eu"), "t"),
        (_cell(stack="a", stack_path="stacks/a", environment="dev-us"), "t"),
    ]
    rows = ac.build_rows(set(), downloaded)
    assert [(r["environment"], r["stack_display"]) for r in rows] == [
        ("dev-eu", "a"),
        ("dev-eu", "z"),
        ("dev-us", "a"),
    ]


SHA = "0123456789abcdef0123456789abcdef01234567"
HINT = "Comment `shipmate help` for the available commands."
#: `provenance(SHA)` under `_run_context`, hand-written.
AT = (
    "at [0123456](https://gh/o/r/commit/0123456789abcdef0123456789abcdef01234567) "
    "in [run #7](https://gh/o/r/actions/runs/1)"
)
FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_APPLIED = "Apply complete! Resources: 1 added, 0 changed, 0 destroyed."


def _comment(rows, env="dev-eu", results="success", **kw):
    return ac.build_comment(rows, [], RUN_URL, [], [], env, results, head_sha=SHA, **kw)


def _capture(tmp_path, name):
    """`name` copied byte-for-byte into an `app (dev-eu)` applied cell, loaded as in production."""
    d = _write_cell(tmp_path, "dev-eu", "stacks-app", _cell())
    (d / "apply.txt").write_bytes((FIXTURES / name).read_bytes())
    return ac.build_rows(set(), ac.load_cells(str(tmp_path)))


def test_an_import_and_forget_capture_renders_its_whole_fold_out(tmp_path):
    """Raw OpenTofu 1.12.4 capture (`tofu apply -input=false stack.otplan 2>&1 | tee apply.txt`,
    piped so not a TTY, ANSI colour intact), stripped by `load_cells` as in production. The colour
    codes sit on the lines around the tally, not on it, so only the fenced body shows a dropped
    strip.

    Mutation: delete the `imported` group from `_RESOURCES_RE` -- the line no longer matches and
    the state reads `applied`, red.
    Mutation: delete the `li.ANSI_RE.sub` in `load_cells` -- the fence holds ESC bytes, red.
    Mutation: return `FOOTER_HINT` from `footer_hint` under every verdict -- a 🟢 comment gains
    the hint, red."""
    rows = _capture(tmp_path, "import-forget.apply.txt")
    assert ac.build_comment(rows, [], RUN_URL, [], [], "dev-eu", "success", head_sha=SHA) == (
        f"### shipmate apply dev-eu\n\n🟢 1 applied {AT}\n\n"
        '<details><summary>🟢 app (dev-eu): +1 ~0 -0, 1 import, 1 forget <a href="https://gh/run/1">'
        "logs</a></summary>\n\n```\n"
        "random_id.c: Importing... [id=p-9hUg]\n"
        "random_id.c: Import complete [id=p-9hUg]\n"
        "random_id.d: Creating...\n"
        "random_id.d: Creation complete after 0s [id=SOaBow]\n"
        "\n"
        "Apply complete! Resources: 1 imported, 1 added, 0 changed, 0 destroyed, 1 forgotten.\n"
        "\n```\n</details>"
    )


@pytest.mark.parametrize(
    ("name", "line"),
    [
        (
            "import-only.apply.txt",
            '🟢 app (dev-eu): +0 ~0 -0, 1 import <a href="https://gh/run/1">logs</a>',
        ),
        (
            "forget-only.apply.txt",
            '🟢 app (dev-eu): +0 ~0 -0, 1 forget <a href="https://gh/run/1">logs</a>',
        ),
    ],
)
def test_an_import_only_or_forget_only_capture_renders_its_counts(tmp_path, name, line):
    """Raw OpenTofu 1.12.4 captures, as in the import-and-forget test; tofu omits the zero count.

    Mutation: delete the `imported` group -- import-only reads `applied`, red.
    Mutation: delete the `forgotten` group -- forget-only reads `applied`, red."""
    (row,) = _capture(tmp_path, name)
    assert ac._cell_line(row, RUN_URL) == line


def test_a_blocked_reason_renders_escaped_on_its_bare_line():
    """cell.json is untrusted, so the reason is `_md_escape`d before it reaches the line.

    Mutation: drop `_md_escape` on the reason in `_state` -- the raw `<a href>` renders, red."""
    row = _row(status="blocked", reason='<a href="https://evil">x</a>', apply_text=None)
    assert _comment([row]) == (
        f"### shipmate apply dev-eu\n\n⚪ 1 blocked {AT}\n\n"
        '⚪ app (dev-eu): blocked: &lt;a href="https://evil"&gt;x&lt;/a&gt; '
        '<a href="https://gh/run/1">logs</a>\n\n' + HINT
    )


def test_a_mixed_run_renders_every_status_in_one_comment():
    """Every status across two environments, in `(environment, stack)` order: fold-outs for the
    rows with output, bare lines for the rest, a blank line on both sides of every fold-out, the
    notes and the footer hint a 🔴 verdict carries.

    Mutation: render the unrecorded row's state without `, not recorded` -- red.
    Mutation: give `blocked` the 🟡 circle in `_STATUS` -- red.
    Mutation: join the notes with a blank line -- red.
    Mutation: append a `gate: pending until every environment is applied` line to
    `_footer_parts` -- red."""
    jobs = [
        _job("wave0 / apply / stacks/app / dev-eu", "https://gh/job/app-eu"),
        _job("wave0 / apply / stacks/db / dev-eu", "https://gh/job/db-eu"),
    ]
    rows = [
        _row(),
        _row(
            stack_display="auth",
            stack_path="stacks/auth",
            status="blocked",
            reason="state restore failed",
            apply_text=None,
        ),
        _row(stack_display="db", stack_path="stacks/db", status="failed", apply_text="Error: boom"),
        _row(
            environment="prod",
            status="unrecorded",
            apply_text="Apply complete! Resources: 0 added, 2 changed, 0 destroyed.",
        ),
        _row(
            environment="prod",
            stack_display="stacks/dns",
            stack_path="stacks/dns",
            status="not_attempted",
            apply_text=None,
        ),
    ]
    body = ac.build_comment(rows, jobs, RUN_URL, [], [], "", "success,failure", head_sha=SHA)
    assert body == (
        "### shipmate apply\n\n"
        f"🔴 1 failed, 1 not recorded, 1 not attempted, 1 blocked, 1 applied {AT}\n\n"
        '<details><summary>🟢 app (dev-eu): +1 ~0 -0 <a href="https://gh/job/app-eu">logs</a>'
        "</summary>\n\n```\nApply complete! Resources: 1 added, 0 changed, 0 destroyed.\n```\n"
        "</details>\n\n"
        '⚪ auth (dev-eu): blocked: state restore failed <a href="https://gh/run/1">logs</a>\n\n'
        '<details><summary>🔴 db (dev-eu): failed <a href="https://gh/job/db-eu">logs</a>'
        "</summary>\n\n```\nError: boom\n```\n</details>\n\n"
        '<details><summary>🟠 app (prod): +0 ~2 -0, not recorded <a href="https://gh/run/1">logs</a>'
        "</summary>\n\n```\nApply complete! Resources: 0 added, 2 changed, 0 destroyed.\n```\n"
        "</details>\n\n"
        '🟡 stacks/dns (prod): not attempted <a href="https://gh/run/1">logs</a>\n\n'
        "not recorded: **app (prod)**. The apply succeeded but its apply check is not recorded "
        "as complete (it failed, was cancelled, or a newer plan re-created it), so "
        "`shipmate / gate` stays pending. Re-plan and re-apply.\n"
        "not attempted: the apply checks stay pending; retry with `shipmate apply`.\n\n" + HINT
    )


def test_no_line_of_the_apply_comment_is_itself_a_shipmate_command():
    """A line matching the command grammar would make the comment a shipmate command; every
    command it names sits inside backticks.

    Mutation: start the explicit footer line with `shipmate apply <env>` outside backticks --
    red."""
    cp = load_script("comment-parse")
    rows = [_row(status="not_attempted", apply_text=None), _row(environment="prod")]
    bodies = [
        ac.build_comment(
            rows, [], RUN_URL, ["dev", "sbx"], ["stg"], "", "success", ["prod", "sbx"]
        ),
        _comment([], results="failure"),
    ]
    for line in "\n".join(bodies).splitlines():
        assert not cp._SHIPMATE_LINE.match(line.strip()), line


@pytest.mark.parametrize(
    ("statuses", "results", "verdict"),
    [
        (["not_attempted", "not_attempted"], "success,failure", "🔴 failed, 2 not attempted"),
        (
            [("unrecorded", "prod"), "not_attempted"],
            "success,failure",
            "🔴 failed, 1 not recorded, 1 not attempted",
        ),
        (["unrecorded"], "success,failure", "🔴 failed, 1 not recorded"),
        (["failed", "applied", "applied"], "success,failure", "🔴 1 failed, 2 applied"),
        (["blocked"], "success,failure", "🔴 1 blocked"),
        (["applied"], "", "🔴 failed, 1 applied"),
        (["not_attempted"], "success,skipped", "🟡 1 not attempted"),
        (["applied", "blocked"], "success", "⚪ 1 blocked, 1 applied"),
        (["applied", "unrecorded"], "success", "🟠 1 not recorded, 1 applied"),
        (["applied"], "success", "🟢 1 applied"),
    ],
)
def test_the_verdict_counts_rows_and_carries_a_failed_run(statuses, results, verdict):
    """A run that dies before any cell reports (a denied apply environment, a job-level cancel)
    leaves only `not attempted` rows, so the job results add a leading `failed` and the 🔴. A
    `not recorded` row in one environment says nothing about another, so it never suppresses
    that token. A `failed` or `blocked` row already names a cell that did not apply, so it
    does; without that, a blocked-only run would carry a redundant `failed` token, while the
    🔴 still marks the failed run.

    Mutation: suppress the token on an `unrecorded` row too -- red.
    Mutation: suppress the token on a `failed` row only -- the blocked case, red.
    Mutation: take the circle from the rows alone -- red.
    Mutation: insert `failed` even when a row failed -- red."""
    rows = [
        _row(status=s) if isinstance(s, str) else _row(status=s[0], environment=s[1])
        for s in statuses
    ]
    assert ac._verdict(rows, ac._results_failed(results), (), SHA) == f"{verdict} {AT}"


def test_a_run_that_died_before_any_cell_reported_renders_failed():
    """The whole comment for the case `_verdict`'s leading token exists for: an environment set,
    expected cells, no artifacts, a failure token in the job results.

    Mutation: suppress the leading `failed` whenever every row is `not_attempted` -- red."""
    rows = ac.build_rows({("prod", "stacks/app"), ("prod", "stacks/db")}, [])
    assert _comment(rows, env="prod", results="success,failure") == (
        f"### shipmate apply prod\n\n🔴 failed, 2 not attempted {AT}\n\n"
        '🟡 stacks/app (prod): not attempted <a href="https://gh/run/1">logs</a>\n'
        '🟡 stacks/db (prod): not attempted <a href="https://gh/run/1">logs</a>\n\n'
        "not attempted: the apply checks stay pending; retry with `shipmate apply prod`.\n\n" + HINT
    )


def test_the_short_form_names_a_failed_run():
    """Mutation: test `held` before the job results in `_verdict` -- red with a held env."""
    assert _comment([], results="success,failure", held=["prod"]) == (
        f"### shipmate apply dev-eu\n\n🔴 failed {AT}\n\n" + HINT
    )


def test_the_short_form_of_an_all_held_run_says_nothing_applied():
    """Work WAS pending and the engine refused it, so the verdict may claim neither success nor an
    empty queue; the held lines still render, and the ⚪ verdict carries the hint.

    Mutation: delete the `elif held` branch in `_verdict` -- 🟢 no pending applies, red."""
    body = ac.build_comment([], [], RUN_URL, [], [], "", "success,skipped", ["prod"], SHA)
    assert body == (
        f"### shipmate apply\n\n⚪ nothing applied, environments are held for review {AT}\n\n"
        "⚪ prod: held, the review state does not permit applying\n"
        "held: get an approving review, or resolve or dismiss a requested-changes review. "
        "The run log's apply-all-detect notice names the decision seen.\n\n" + HINT
    )


def test_the_short_form_of_an_empty_queue_keeps_the_explicit_and_skipped_lines():
    """apply-all-detect drops explicit envs from `runnable`, so an explicit-only-pending
    repository is all-levels-empty and carries `excluded_envs` at once: the lines are the only
    sign of it.

    Mutation: render the footer lines only when there are rows -- red."""
    body = ac.build_comment(
        [], [], RUN_URL, ["prod"], ["staging"], "", "success,skipped", head_sha=SHA
    )
    assert body == (
        f"### shipmate apply\n\n🟢 no pending applies {AT}\n\n"
        "🟡 prod: left pending (explicit), comment `shipmate apply prod`\n"
        "⚪ staging: skipped, ordered after an environment not applying this run"
    )


def test_the_footer_lines_of_the_all_environments_form():
    """One line per environment, in order: explicit, skipped, held, then the `held:` remedy. A
    held explicit env is listed once, on its held line, with the targeted command a bare apply
    never replaces; a held env that is not explicit names none. The held line names no single
    cause (three decisions hold). No gate line: the `shipmate / gate` status shows that.

    Mutation: `needs an approving review` in the held line -- red.
    Mutation: list every `excluded` env on an explicit line -- `sbx` appears twice, red.
    Mutation: append a `gate: pending until every environment is applied` line -- red."""
    assert ac._footer_parts(["prod", "sbx"], ["stg"], "", ["dev", "sbx"]) == [
        "🟡 prod: left pending (explicit), comment `shipmate apply prod`",
        "⚪ stg: skipped, ordered after an environment not applying this run",
        "⚪ dev: held, the review state does not permit applying",
        "⚪ sbx: held, the review state does not permit applying; once it clears, comment "
        "`shipmate apply sbx`",
        "held: get an approving review, or resolve or dismiss a requested-changes review. "
        "The run log's apply-all-detect notice names the decision seen.",
    ]


def test_the_targeted_form_renders_no_footer_lines():
    """Explicit, skipped and held are apply-all concepts.

    Mutation: drop the `if env_name` reset in `_dispositions` -- red."""
    assert ac._footer_parts(["prod"], ["stg"], "dev-eu", ["dev"]) == []


def test_the_footer_lines_escape_every_env_name():
    """Env names are author-controlled. Mutation: `_dispositions` escapes `excluded` but not
    `held` -- red (both the held line and its command)."""
    assert ac._footer_parts(["e<1"], ["s<2"], "", ["h<3", "e<1"]) == [
        "⚪ s&lt;2: skipped, ordered after an environment not applying this run",
        "⚪ h&lt;3: held, the review state does not permit applying",
        "⚪ e&lt;1: held, the review state does not permit applying; once it clears, comment "
        "`shipmate apply e&lt;1`",
        "held: get an approving review, or resolve or dismiss a requested-changes review. "
        "The run log's apply-all-detect notice names the decision seen.",
    ]


_UNGATED_NOTICE = (
    ": ungated, permitted to apply without an approving review "
    "(gated = false in .github/shipmate.toml)"
)
_NO_REVIEW_NOTICE = ": no approving review was required, so gated had nothing to enforce"


def test_the_notices_name_ungated_then_no_review_envs():
    """The ungated text carries no completion verb: detect derives the set before any wave runs.
    The no-review text states the authorization fact and never that nobody reviewed.

    Mutation: `applied without an approving review` in `_UNGATED` -- red.
    Mutation: list the no-review envs first -- red."""
    rows = [_row(environment="qa")]
    assert ac.notices(rows, "", ["dev-eu"], ["qa"]) == [
        "::notice::dev-eu" + _UNGATED_NOTICE,
        "::notice::qa" + _NO_REVIEW_NOTICE,
    ]


def test_the_targeted_form_has_no_ungated_notice():
    """Ungated is an apply-all concept; a targeted apply of a gated env under a null decision is
    the case the no-review notice exists for.

    Mutation: drop the `if env_name` reset in `notices` -- red."""
    assert ac.notices([_row()], "dev-eu", ["dev-eu"], ["dev-eu"]) == [
        "::notice::dev-eu" + _NO_REVIEW_NOTICE
    ]


def test_no_review_notice_skips_an_env_whose_apply_never_ran():
    """sbx's only row is blocked, so nothing in it applied and there is nothing to disclose.

    Mutation: skip the row filter in `notices` -- the notice prints, red."""
    rows = [_row(environment="sbx", status="blocked", reason="upstream failed", apply_text=None)]
    assert ac.notices(rows, "sbx", [], ["sbx"]) == []


@pytest.mark.parametrize("status", ["applied", "failed", "unrecorded"])
def test_no_review_notice_names_an_env_whose_apply_ran(status):
    """sbx's only row is `status`: apply ran there, so infrastructure may have changed.

    Mutation: narrow the row filter in `notices` to `status == "applied"` -- red."""
    rows = [_row(environment="sbx", status=status)]
    assert ac.notices(rows, "sbx", [], ["sbx"]) == ["::notice::sbx" + _NO_REVIEW_NOTICE]


def test_the_not_attempted_note_names_the_targeted_env_escaped():
    """A bare `shipmate apply` cannot retry an explicit env, so a targeted run names its env.

    Mutation: build the command without `_md_escape` -- red."""
    rows = [_row(status="not_attempted", apply_text=None)]
    assert ac._not_attempted_note(rows, "x</summary>") == (
        "not attempted: the apply checks stay pending; retry with "
        "`shipmate apply x&lt;/summary&gt;`."
    )
    assert ac._not_attempted_note([_row()], "prod") == ""


def test_the_unrecorded_note_is_capped_and_summarizes_the_rest():
    """Notes belong to the part of the comment that never sheds, so an uncapped note could only
    push the render into the fail-loud SystemExit, on the run that needed it: the usual cause of
    `unrecorded` rows (an expired App key, a checks-API outage) strands a wide matrix.

    Mutation: name every cell in `_named` -- red.
    Mutation: name a cell `**<stack> / <env>**` in `_unrecorded_note` -- red."""
    rows = [
        _row(status="unrecorded", stack_display=f"s{i}", stack_path=f"stacks/s{i}")
        for i in range(7)
    ]
    assert ac._unrecorded_note(rows) == (
        "not recorded: **s0 (dev-eu)**, **s1 (dev-eu)**, **s2 (dev-eu)**, **s3 (dev-eu)**, "
        "**s4 (dev-eu)**, and 2 more. The apply succeeded but its apply check is not recorded "
        "as complete (it failed, was cancelled, or a newer plan re-created it), so "
        "`shipmate / gate` stays pending. Re-plan and re-apply."
    )


def test_the_lock_note_names_the_cell_the_lock_and_the_release_command_whole():
    """Mutation: drop `, then re-apply` from the note -- red.
    Mutation: name the cell `**<stack> / <env>**` in `_lock_cell` -- red."""
    rows = [_row(status="failed", apply_text=_fixture_text("lock_error_s3.txt"))]
    assert ac._lock_note(rows, "dev-eu") == (
        "state lock held: **app (dev-eu)** (lock **0f866bdc-d621-7230-876f-fa7398eff1f8**, held "
        "since **2026-08-20 19:53:19.7388258 +0000 UTC**). An earlier apply was cancelled or "
        "killed before releasing the lock, so nothing in these cells was applied. Per-cell "
        "concurrency admits one apply at a time, so the holder was that cell's most recent apply "
        "run. Release it with `shipmate unlock dev-eu`, then re-apply. Unlocking is not "
        "recovery: if the reviewed plan is now stale, re-plan."
    )


def test_the_notes_read_lock_then_unrecorded_then_not_attempted():
    """A held lock is why the run failed; a stranded applied cell needs a re-plan, a
    not-attempted one only a retry. The more urgent statement reads first.

    Mutation: swap the lock and unrecorded notes in `build_comment` -- red."""
    rows = [
        _row(status="failed", apply_text=_fixture_text("lock_error_s3.txt")),
        _row(environment="prod", stack_display="db", stack_path="stacks/db", status="unrecorded"),
        _row(
            environment="prod",
            stack_display="stacks/dns",
            stack_path="stacks/dns",
            status="not_attempted",
            apply_text=None,
        ),
    ]
    sections = _comment(rows, env="").split("\n\n")
    assert sections[-2] == (
        "state lock held: **app (dev-eu)** (lock **0f866bdc-d621-7230-876f-fa7398eff1f8**, held "
        "since **2026-08-20 19:53:19.7388258 +0000 UTC**). An earlier apply was cancelled or "
        "killed before releasing the lock, so nothing in these cells was applied. Per-cell "
        "concurrency admits one apply at a time, so the holder was that cell's most recent apply "
        "run. Release it with `shipmate unlock <env>`, then re-apply. Unlocking is not "
        "recovery: if the reviewed plan is now stale, re-plan.\n"
        "not recorded: **db (prod)**. The apply succeeded but its apply check is not recorded "
        "as complete (it failed, was cancelled, or a newer plan re-created it), so "
        "`shipmate / gate` stays pending. Re-plan and re-apply.\n"
        "not attempted: the apply checks stay pending; retry with `shipmate apply`."
    )
    assert sections[-1] == HINT


def test_a_fold_out_holds_the_whole_output_in_a_plain_fence_under_an_escaped_summary():
    """A stack name is author-controlled; an unescaped `</summary>` would close the tag early.

    Mutation: fence with the default `diff` language -- red."""
    row = _row(stack_display="x</summary><b>evil")
    assert ac.render_apply_section(ac._cell_line(row, RUN_URL), "hello", RUN_URL, 10_000) == (
        "<details><summary>🟢 x&lt;/summary&gt;&lt;b&gt;evil (dev-eu): +1 ~0 -0 "
        '<a href="https://gh/run/1">logs</a></summary>\n\n```\nhello\n```\n</details>'
    )


def test_a_truncated_fold_out_keeps_the_tail_at_a_line_boundary():
    """Mutation: keep the head (`text[:room]`) -- the kept lines are not a suffix, red."""
    lines = [f"line {i}" for i in range(5_000)]
    s = ac.render_apply_section(ac._cell_line(_row(), RUN_URL), "\n".join(lines), RUN_URL, 3_000)
    head = (
        '<details><summary>🟢 app (dev-eu): +1 ~0 -0 <a href="https://gh/run/1">logs</a>'
        "</summary>\n\n```\n"
    )
    trailer = (
        "\n```\n\n_Truncated, earlier output elided; [full output in the job log]"
        "(https://gh/run/1)._\n</details>"
    )
    assert len(s) <= 3_000
    assert s.startswith(head) and s.endswith(trailer)
    kept = s[len(head) : -len(trailer)].split("\n")
    assert kept == lines[-len(kept) :]
    assert len(kept) > 100


def test_a_row_degrades_to_its_bare_line():
    """No output, no line boundary to cut at, or a spent budget: the bare line, which the caller
    reserved.

    Mutation: drop the `limit < sc.MIN_PLAN_CHARS` guard -- the spent-budget case renders a
    fold-out, red."""
    bare = '🟢 app (dev-eu): +1 ~0 -0 <a href="https://gh/run/1">logs</a>'
    unread = ac._cell_line(_row(apply_text=None), RUN_URL)
    assert ac.render_apply_section(unread, None, RUN_URL, 10_000) == (
        '🟢 app (dev-eu): applied <a href="https://gh/run/1">logs</a>'
    )
    line = ac._cell_line(_row(), RUN_URL)
    assert ac.render_apply_section(line, "x" * 5_000, RUN_URL, 3_000) == bare
    assert ac.render_apply_section(line, "short", RUN_URL, ac.sc.MIN_PLAN_CHARS - 1) == bare


def _bare(i):
    return f'🟢 s{i:03} (dev-eu): +1 ~0 -0 <a href="https://gh/run/1">logs</a>'


_GIANT = "r\n" * (ac.sc.SIZE_BUDGET // 2 + 1) + _APPLIED


def _first_fold_out(body, verdict):
    """The kept lines of the body's first fold-out (row 0's, truncated), and what follows it."""
    opening = (
        f"### shipmate apply dev-eu\n\n{verdict}\n\n<details><summary>{_bare(0)}</summary>\n\n```\n"
    )
    trailer = (
        "\n```\n\n_Truncated, earlier output elided; [full output in the job log]"
        "(https://gh/run/1)._\n</details>"
    )
    assert body.startswith(opening)
    kept, rest = body[len(opening) :].split(trailer, 1)
    return kept.split("\n"), rest


def test_a_256_cell_fan_out_of_oversized_applies_keeps_every_cell_line():
    """Every remaining row's bare line is reserved before a fold-out is sized.

    Mutation: drop `reserve` from `render_apply_section`'s limit -- row 0 takes the whole budget
    and the 255 bare lines after it push the body past the hard cap, red."""
    rows = [
        _row(stack_display=f"s{i:03}", stack_path=f"stacks/s{i:03}", apply_text=_GIANT)
        for i in range(256)
    ]
    body = _comment(rows)
    assert len(body) <= ac.sc.SIZE_BUDGET
    kept, rest = _first_fold_out(body, f"🟢 256 applied {AT}")
    assert set(kept[:-1]) == {"r"} and kept[-1] == _APPLIED
    assert rest == "\n\n" + "\n".join(_bare(i) for i in range(1, 256))


def test_an_early_giant_apply_cannot_drop_a_later_cells_line():
    """Mutation: drop `reserve` from `render_apply_section`'s limit -- row 0 is sized to the whole
    budget and the later lines push the body past SIZE_BUDGET, red."""
    rows = [
        _row(stack_display="s000", stack_path="stacks/s000", apply_text=_GIANT),
        _row(stack_display="s001", stack_path="stacks/s001"),
        _row(
            stack_display="s002",
            stack_path="stacks/s002",
            status="blocked",
            reason="upstream failed",
            apply_text=None,
        ),
    ]
    body = _comment(rows)
    assert len(body) <= ac.sc.SIZE_BUDGET
    kept, rest = _first_fold_out(body, f"⚪ 1 blocked, 2 applied {AT}")
    assert set(kept[:-1]) == {"r"} and kept[-1] == _APPLIED
    assert rest == (
        "\n\n" + _bare(1) + "\n"
        '⚪ s002 (dev-eu): blocked: upstream failed <a href="https://gh/run/1">logs</a>\n\n' + HINT
    )


_OVER_CAP = (
    "::error::apply comment exceeds the 65,536-character comment cap even in its compact form; "
    "shorten stack or environment names"
)


def test_build_comment_fails_loud_when_even_the_cell_lines_overflow():
    """Mutation: delete the HARD_CAP check -- no SystemExit, red."""
    long_name = "s" * 400
    rows = [
        _row(stack_display=f"{long_name}{i:03}", stack_path=f"stacks/{long_name}{i:03}")
        for i in range(300)
    ]
    with pytest.raises(SystemExit) as exc:
        _comment(rows)
    assert exc.value.code == _OVER_CAP


def test_build_comment_fails_loud_when_even_the_compact_form_overflows():
    """256 blocked rows whose names alone pass HARD_CAP: the compact form is tried and is still
    too large.

    Mutation: `return _compact(...)` in `build_comment` -- the oversized body is returned, no
    SystemExit, red."""
    long_name = "s" * 300
    rows = [
        _row(
            stack_display=f"{long_name}{i:03}",
            stack_path=f"stacks/{long_name}{i:03}",
            status="blocked",
            reason="upstream failed",
            apply_text=None,
        )
        for i in range(256)
    ]
    with pytest.raises(SystemExit) as exc:
        _comment(rows)
    assert exc.value.code == _OVER_CAP


_PLANNED_HEAD = "reviewed plan records no commit or was produced from a different one; re-plan"
_JOB_URL = "https://github.com/ship-iac/repo-example-stacks/actions/runs/12345678901/job/{:011}"


def test_a_256_cell_all_blocked_run_falls_back_to_the_compact_form():
    """The reasons push 256 blocked lines past HARD_CAP; the compact form drops them, keeps every
    line and its link, and points at the logs.

    Mutation: delete the `_compact` branch in `build_comment` -- SystemExit, red."""
    env = "production-eu-west-1"
    rows, jobs = [], []
    for i in range(256):
        path = f"stacks/platform/services/service-{i:03}"
        rows.append(
            _row(
                environment=env,
                stack_path=path,
                stack_display=path,
                status="blocked",
                reason=_PLANNED_HEAD,
                apply_text=None,
            )
        )
        jobs.append(_job(f"wave0 / apply / {path} / {env}", _JOB_URL.format(23456789000 + i)))
    body = ac.build_comment(rows, jobs, RUN_URL, [], [], env, "failure", head_sha=SHA)
    assert len(body) <= ac.sc.HARD_CAP
    lines = body.split("\n")
    assert lines[:4] == [
        "### shipmate apply production-eu-west-1",
        "",
        f"🔴 256 blocked {AT}",
        "",
    ]
    assert lines[4] == (
        "⚪ stacks/platform/services/service-000 (production-eu-west-1): blocked "
        '<a href="https://github.com/ship-iac/repo-example-stacks/actions/runs/12345678901'
        '/job/23456789000">logs</a>'
    )
    assert lines[259] == (
        "⚪ stacks/platform/services/service-255 (production-eu-west-1): blocked "
        '<a href="https://github.com/ship-iac/repo-example-stacks/actions/runs/12345678901'
        '/job/23456789255">logs</a>'
    )
    assert lines[4:260] == [
        f"⚪ stacks/platform/services/service-{i:03} (production-eu-west-1): blocked "
        f'<a href="{_JOB_URL.format(23456789000 + i)}">logs</a>'
        for i in range(256)
    ]
    assert lines[260:] == [
        "",
        "blocked: each blocked cell's reason is in its logs.",
        "",
        HINT,
    ]


def test_a_256_environment_run_groups_its_footer_lines_in_the_compact_form():
    """256 applied cells, one per environment, and 256 explicit environments left pending: one
    explicit line per environment pushes the body past HARD_CAP with no blocked row, and the
    compact form names them all on one line. The verdict is 🟢, so no hint follows.

    Mutation: restore `and any(r["status"] == "blocked" for r in rows)` on the `_compact` branch
    in `build_comment` -- SystemExit, red.
    Mutation: append a `gate: complete` line to `_footer_parts` -- red."""
    envs = [f"env-{i}" for i in range(256)]
    explicit = [f"explicit-environment-{i:03}-eu-west-1-prod" for i in range(256)]
    rows = [
        _row(environment=e, stack_path="stacks/app", stack_display="stacks/app", apply_text=None)
        for e in envs
    ]
    jobs = [
        _job(f"wave0 / apply / stacks/app / {e}", _JOB_URL.format(23456789000 + i))
        for i, e in enumerate(envs)
    ]
    body = ac.build_comment(rows, jobs, RUN_URL, explicit, [], "", "success", head_sha=SHA)
    assert len(body) <= ac.sc.HARD_CAP
    lines = body.split("\n")
    assert lines[:4] == ["### shipmate apply", "", f"🟢 256 applied {AT}", ""]
    assert lines[4:260] == [
        f'🟢 stacks/app (env-{i}): applied <a href="{_JOB_URL.format(23456789000 + i)}">logs</a>'
        for i in range(256)
    ]
    assert lines[260:] == [
        "",
        "🟡 left pending (explicit): "
        + ", ".join(explicit)
        + "; comment `shipmate apply <env>` for each",
    ]


def test_the_compact_footer_lines_group_every_disposition():
    """One line per disposition, environments comma-separated, in the per-environment order; the
    held line names no single cause and lists the explicit held envs for their command.

    Mutation: delete the `held_explicit` clause in `_grouped_lines` -- red.
    Mutation: append a `gate: pending until every environment is applied` line -- red."""
    assert ac._footer_parts(
        ["prod", "prod-us", "sbx"], ["stg", "uat"], "", ["dev", "sbx"], compact=True
    ) == [
        "🟡 left pending (explicit): prod, prod-us; comment `shipmate apply <env>` for each",
        "⚪ skipped, ordered after an environment not applying this run: stg, uat",
        "⚪ held, the review state does not permit applying: dev, sbx; once the hold clears, "
        "comment `shipmate apply <env>` for sbx",
        "held: get an approving review, or resolve or dismiss a requested-changes review. "
        "The run log's apply-all-detect notice names the decision seen.",
    ]


def _fence_delimiter_lines(rendered):
    """All-backtick lines emitted in `rendered` -- the real fence delimiters, read from the output
    rather than re-derived from the helper the renderer is supposed to have called. A renderer that
    hardcodes a 3-backtick fence, ignoring the computed length, must fail an assertion built from
    this rather than one built from the helper."""
    return [ln for ln in rendered.splitlines() if ln and set(ln) == {"`"}]


def test_fence_escape_attempt_cannot_break_out_of_fence():
    # Trailing non-backtick characters keep every body line from being pure backticks --
    # which would masquerade as a third delimiter line to `_fence_delimiter_lines` --
    # while leaving the longest contiguous backtick run at 50 either way.
    evil = "````` " + "`" * 50 + "x\nrm -rf /\n" + "`" * 50 + "y"
    row = _row(apply_text=evil)
    s = ac.render_apply_section(ac._cell_line(row, RUN_URL), evil, RUN_URL, 10_000)
    longest_run_in_evil = max(len(m) for m in re.findall(r"`+", evil))
    fence_lines = _fence_delimiter_lines(s)
    assert len(fence_lines) == 2
    assert fence_lines[0] == fence_lines[1]
    # The delimiter emitted must be strictly longer than the longest backtick run in the
    # body, checked against the rendered fence line rather than a length computed off to
    # the side.
    assert len(fence_lines[0]) > longest_run_in_evil
    # The section still closes with </details>: no early close from the injected run.
    assert s.endswith("</details>")


def test_fence_escape_attempt_truncated_path_reuses_full_bodys_fence():
    """The backtick run sits near the start, and tail-oriented degradation keeps the end, so the
    fence around the kept backtick-free slice must still be sized against the whole original body,
    computed once up front. Recomputed against the slice it would need only the minimum 3-backtick
    fence, reopening the hole for a less-truncated render."""
    lines = [f"line {i}" for i in range(3_000)]
    evil = "`" * 60 + "z\nbefore the backticks\n" + "\n".join(lines)
    longest_run_in_evil = max(len(m) for m in re.findall(r"`+", evil))
    row = _row(apply_text=evil)
    s = ac.render_apply_section(ac._cell_line(row, RUN_URL), evil, RUN_URL, 2_000)
    assert "Truncated" in s
    fence_lines = _fence_delimiter_lines(s)
    assert len(fence_lines) == 2
    assert fence_lines[0] == fence_lines[1]
    assert len(fence_lines[0]) > longest_run_in_evil
    # The 60-backtick run was truncated away: the kept body never reaches it, so this is
    # a real cut rather than a coincidence.
    assert "`" * 60 not in s.replace(fence_lines[0], "")


def test_resources_present():
    text = "some noise\nApply complete! Resources: 3 added, 1 changed, 2 destroyed.\ntrailer"
    assert ac._resources(text) == "+3 ~1 -2"


def test_resources_absent():
    assert ac._resources("Error: something failed") == ""
    assert ac._resources(None) == ""


def test_resources_malformed_line_ignored():
    text = "Apply complete! Resources: many added, 1 changed, 2 destroyed."
    assert ac._resources(text) == ""


def test_resources_takes_last_matching_line():
    text = (
        "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.\n"
        "some retry output\n"
        "Apply complete! Resources: 2 added, 0 changed, 0 destroyed."
    )
    assert ac._resources(text) == "+2 ~0 -0"


def test_resources_regex_ignores_lookalike_author_text_digits_only_capture():
    # Author-controlled text cannot inject non-digit content into the captured groups: a
    # line missing real digits in those positions fails to match rather than smuggling
    # arbitrary text into the cell line.
    text = "Apply complete! Resources: <script>alert(1)</script> added, 0 changed, 0 destroyed."
    assert ac._resources(text) == ""


def test_resources_ignores_embedded_lookalike_in_later_output_line():
    """OpenTofu prints the `Outputs:` block after the "Apply complete!" line, so an output value
    containing that literal string mid-line may not be mistaken for the real line nor, being
    textually last, override it. Line anchoring (`^...$`, `re.M`) defeats it: an unanchored regex
    matches the embedded copy and, being last, wins."""
    text = (
        "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.\n"
        "\n"
        "Outputs:\n"
        'fake = "Apply complete! Resources: 99 added, 99 changed, 99 destroyed."\n'
    )
    assert ac._resources(text) == "+1 ~0 -0"


def test_load_cells_fails_loud_on_missing_schema_key(tmp_path):
    bad = _cell()
    del bad["reason"]
    _write_cell(tmp_path, "dev-eu", "stacks-app", bad)
    with pytest.raises(SystemExit, match="reason"):
        ac.load_cells(str(tmp_path))


def test_load_cells_fails_loud_on_wrong_type(tmp_path):
    bad = _cell(reason=123)
    _write_cell(tmp_path, "dev-eu", "stacks-app", bad)
    with pytest.raises(SystemExit, match="reason"):
        ac.load_cells(str(tmp_path))


def test_load_cells_fails_loud_on_out_of_enum_result(tmp_path):
    bad = _cell(result="cancelled")
    _write_cell(tmp_path, "dev-eu", "stacks-app", bad)
    with pytest.raises(SystemExit, match="result"):
        ac.load_cells(str(tmp_path))


def test_read_tail_drops_partial_leading_line(tmp_path):
    # Every line kept in the tail must be a complete line from the source: proof that a
    # mid-line seek point is dropped rather than emitted as a fragment.
    p = tmp_path / "apply.txt"
    lines = [f"line {i}" for i in range(10_000)]
    p.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    tail = ac._read_tail(p, 100)
    assert 0 < len(tail) <= 100
    for ln in tail.splitlines():
        assert ln in lines


def test_read_tail_keeps_the_end_not_the_start(tmp_path):
    p = tmp_path / "apply.txt"
    p.write_text("A" * 200_000 + "\nTAILMARKER\n", encoding="utf-8", newline="\n")
    tail = ac._read_tail(p, 1_000)
    assert "TAILMARKER" in tail
    assert "A" * 500 not in tail


def test_read_tail_small_file_is_returned_whole_no_leading_drop(tmp_path):
    # A file smaller than the budget needs no truncation, so its first line survives
    # intact: nothing to drop when nothing was cut.
    p = tmp_path / "apply.txt"
    p.write_text("first line\nsecond line\n", encoding="utf-8", newline="\n")
    tail = ac._read_tail(p, 10_000)
    assert tail == "first line\nsecond line\n"


def test_read_tail_tolerates_non_utf8_byte(tmp_path):
    # A single non-UTF-8 byte anywhere in the file must not raise
    # UnicodeDecodeError -- it must decode to a replacement character.
    p = tmp_path / "apply.txt"
    p.write_bytes(b"before\n\xff\nafter\n")
    tail = ac._read_tail(p, 10_000)
    assert "before" in tail
    assert "after" in tail


def test_load_cells_preserves_trailing_error_in_huge_failed_apply(tmp_path):
    # A long failed apply whose fatal diagnostic is the very last line: head-first
    # reading or truncation drops it entirely.
    d = _write_cell(tmp_path, "dev-eu", "stacks-app", _cell(result="failed"))
    body = "\n".join("Still creating..." for _ in range(6_000))
    text = body + "\nError: Provider produced inconsistent result after apply\n"
    assert len(text) > 100_000
    (d / "apply.txt").write_text(text, encoding="utf-8", newline="\n")
    cells = ac.load_cells(str(tmp_path))
    _, loaded_text = cells[0]
    assert "Error:" in loaded_text
    row = _row(status="failed", apply_text=loaded_text)
    section = ac.render_apply_section(ac._cell_line(row, RUN_URL), loaded_text, RUN_URL, 4_000)
    assert "Error:" in section


def test_load_cells_reads_resources_line_from_large_successful_apply(tmp_path):
    d = _write_cell(tmp_path, "dev-eu", "stacks-app", _cell(result="applied"))
    noise = "\n".join(f"aws_instance.node[{i}]: Creation complete" for i in range(3_000))
    text = noise + "\nApply complete! Resources: 1200 added, 0 changed, 0 destroyed.\n"
    assert len(text) > 60_000
    (d / "apply.txt").write_text(text, encoding="utf-8", newline="\n")
    cells = ac.load_cells(str(tmp_path))
    _, loaded_text = cells[0]
    assert ac._resources(loaded_text) == "+1200 ~0 -0"


def test_load_cells_tolerates_non_utf8_byte_in_apply_txt(tmp_path):
    d = _write_cell(tmp_path, "dev-eu", "stacks-app", _cell(result="applied"))
    (d / "apply.txt").write_bytes(
        b"Apply complete! Resources: 1 added, 0 changed, 0 destroyed.\n"
        b"trailer with a bad byte: \xff\n"
    )
    cells = ac.load_cells(str(tmp_path))  # A UnicodeDecodeError here is the failure.
    _, loaded_text = cells[0]
    assert loaded_text is not None
    assert "Apply complete!" in loaded_text


def test_load_cells_strips_ansi_from_realistic_apply_output(tmp_path):
    # `tofu init`/`apply` stdout+stderr teed raw (no -no-color) carries SGR colour codes,
    # which must not survive into the rendered comment as literal garbage in the fence.
    d = _write_cell(tmp_path, "dev-eu", "stacks-auth", _cell(result="applied"))
    text = (
        "/stacks/auth (script:1 job:0.0)> tofu init -input=false\n"
        "\x1b[0m\x1b[1m\n"
        "\x1b[36;1mInitializing the backend...\x1b[0m\n"
        "\x1b[31mError: something\x1b[0m\n"
        "\x1b[1mApply complete! Resources: 1 added, 0 changed, 0 destroyed.\x1b[0m\n"
    )
    (d / "apply.txt").write_text(text, encoding="utf-8", newline="\n")
    cells = ac.load_cells(str(tmp_path))
    _, loaded_text = cells[0]
    assert "\x1b" not in loaded_text
    assert "Initializing the backend..." in loaded_text
    row = _row(status="applied", apply_text=loaded_text)
    section = ac.render_apply_section(ac._cell_line(row, RUN_URL), loaded_text, RUN_URL, 10_000)
    assert "\x1b" not in section


def test_load_cells_ansi_strip_happens_before_fence_is_computed(tmp_path):
    """The fence is computed after the ANSI strip. 50 backticks either side of an escape are two
    runs of 50 raw and one run of 100 stripped, so a pre-strip fence of 51 would be strictly shorter
    than the run it must delimit. A 2-backtick merge alone proves too little: it still fits under
    the minimum 3-backtick fence."""
    d = _write_cell(tmp_path, "dev-eu", "stacks-auth", _cell(result="applied"))
    # The trailing "x" keeps the merged run's line from being a pure-backtick line, which
    # would masquerade as a third delimiter line to `_fence_delimiter_lines` -- the same
    # caveat as test_fence_escape_attempt_cannot_break_out_of_fence.
    text = (
        "`" * 50
        + "\x1b[0m"
        + "`" * 50
        + "x\nApply complete! Resources: 1 added, 0 changed, 0 destroyed.\n"
    )
    (d / "apply.txt").write_text(text, encoding="utf-8", newline="\n")
    cells = ac.load_cells(str(tmp_path))
    _, loaded_text = cells[0]
    assert "\x1b" not in loaded_text
    longest_run = max(len(m) for m in re.findall(r"`+", loaded_text))
    assert longest_run == 100  # The merge actually happened.
    row = _row(status="applied", apply_text=loaded_text)
    section = ac.render_apply_section(ac._cell_line(row, RUN_URL), loaded_text, RUN_URL, 10_000)
    fence_lines = _fence_delimiter_lines(section)
    assert len(fence_lines) == 2
    assert fence_lines[0] == fence_lines[1]
    assert len(fence_lines[0]) > longest_run


def test_resources_parses_colour_wrapped_apply_complete_line():
    """tofu wraps the whole "Apply complete!" line in an SGR pair, so an escape at either end may
    not defeat the line-anchored (`^...$`, `re.MULTILINE`) regex. `_resources` runs on
    already-stripped text, as `load_cells` produces, which pins the anchor itself once the colour
    codes are gone."""
    text = ac.li.ANSI_RE.sub(
        "", "\x1b[1mApply complete! Resources: 3 added, 1 changed, 2 destroyed.\x1b[0m\n"
    )
    assert ac._resources(text) == "+3 ~1 -2"


def test_load_cells_reads_apply_text_only_when_present(tmp_path):
    attempted = _write_cell(tmp_path, "dev-eu", "stacks-app", _cell(result="applied"))
    (attempted / "apply.txt").write_text("output here")
    _write_cell(
        tmp_path,
        "dev-us",
        "stacks-db",
        _cell(
            stack="db", stack_path="stacks/db", environment="dev-us", result="blocked", reason="x"
        ),
    )
    cells = ac.load_cells(str(tmp_path))
    texts = {c["stack_path"]: t for c, t in cells}
    assert texts["stacks/app"] == "output here"
    assert texts["stacks/db"] is None


def test_job_url_suffix_match_against_caller_prefixed_job_name():
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("wave0 (matrix) / apply / stacks/app / dev-eu", "https://gh/job/1")]
    assert ac._job_url(row, jobs, RUN_URL) == "https://gh/job/1"


def test_job_url_falls_back_to_run_url_when_no_job_matches():
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("wave0 / apply / stacks/db / dev-us", "https://gh/job/1")]
    assert ac._job_url(row, jobs, RUN_URL) == RUN_URL


def test_job_url_does_not_false_match_on_bare_endswith():
    # A job named "...reapply / stacks/app / dev-eu" must not match the target
    # "apply / stacks/app / dev-eu" through a naive str.endswith; only a `/`-boundary
    # suffix counts.
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("reapply / stacks/app / dev-eu", "https://gh/job/should-not-match")]
    assert ac._job_url(row, jobs, RUN_URL) == RUN_URL


def test_results_failed_blank_token_counts_as_failure():
    # Restores the semantics of the shell tokenizer this replaced (`tr ',' '\n' |
    # grep -qvE '^(success|skipped)$'`): an empty line never matches that alternation, so a
    # blank segment reads as failure. Filtering blank tokens out reads a blank as clean.
    assert ac._results_failed("") is True
    assert ac._results_failed("success,,skipped") is True
    assert ac._results_failed("success,") is True
    assert ac._results_failed("success,skipped") is False


def test_cell_schema_guard_apply_cell_writes_every_required_key():
    # Coupling: apply-cell (writer of cell.json) <-> apply-comment (reader).
    # Assert every key the reader requires appears as a JSON key literal in
    # the writer's source.
    src = (_ENGINE / "scripts" / "apply-cell-summary").read_text(encoding="utf-8")
    missing = [k for k in ac.CELL_KEYS if f'"{k}"' not in src]
    assert missing == [], f"apply-cell-summary no longer writes cell.json keys: {missing}"


def test_apply_summary_artifact_name_matches_contract():
    # apply-comment's load_cells globs `apply-summary.*` trees; drift here
    # would silently stop cells from being found (an empty comment, not a
    # loud failure), so pin the exact artifact-name grammar.
    src = (_ENGINE / "actions" / "apply-cell" / "action.yml").read_text(encoding="utf-8")
    assert "apply-summary.${{ inputs.env }}.${{ steps.ids.outputs.slug }}" in src


APP_ID = "12345"


def _check(name, *, status="completed", conclusion="success", run_id=1, app_id=int(APP_ID)):
    # `run_id`, not `id`: shadowing the builtin in a parameter name is a lint
    # finding, and the JSON key stays `id` either way.
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "id": run_id,
        "app": {"id": app_id},
    }


def _jsonl(*checks):
    return [json.dumps(c) for c in checks]


def test_check_state_maps_splits_present_and_done():
    lines = _jsonl(
        _check("apply / stacks/app / dev-eu", run_id=1),
        _check("apply / stacks/db / dev-eu", status="in_progress", conclusion=None, run_id=2),
    )
    present, done = ac.check_state_maps(lines, APP_ID)
    assert present == {"apply / stacks/app / dev-eu", "apply / stacks/db / dev-eu"}
    assert done == {"apply / stacks/app / dev-eu"}


def test_check_state_maps_ignores_checks_from_another_app_identity():
    # Security-relevant: a same-name check created by any other identity must
    # not be able to paint an applied cell as stranded (nor green a pending
    # one). Same posture as the gate's own from_app filter.
    lines = _jsonl(
        _check("apply / stacks/app / dev-eu", status="in_progress", conclusion=None, app_id=15368)
    )
    present, done = ac.check_state_maps(lines, APP_ID)
    assert present == set()
    assert done == set()


def test_check_state_maps_judges_the_newest_run_per_name():
    # A duplicate apply check created mid-apply is deliberately left pending by
    # apply-cell; the newest run per name (highest id) must win, so the cell
    # reads pending even though an older completed run exists.
    lines = _jsonl(
        _check("apply / stacks/app / dev-eu", run_id=1),
        _check("apply / stacks/app / dev-eu", status="queued", conclusion=None, run_id=2),
    )
    present, done = ac.check_state_maps(lines, APP_ID)
    assert present == {"apply / stacks/app / dev-eu"}
    assert done == set()


def test_load_check_maps_empty_app_id_warns_and_returns_no_data(tmp_path, capsys):
    """Must NOT fail loud the way from_app does: a missing SHIPMATE_APP_ID is only allowed to cost
    this one display axis, never the whole comment, and the warning names the variable rather
    than blaming the file. Mutation: delete load_check_maps' `if not app_id` early return (the
    catch-all then warns about checks.jsonl instead)."""
    p = tmp_path / "checks.jsonl"
    p.write_text("\n".join(_jsonl(_check("apply / stacks/app / dev-eu"))), encoding="utf-8")
    assert ac.load_check_maps(str(p), "") == (set(), set())
    assert capsys.readouterr().out == (
        "::warning::SHIPMATE_APP_ID is empty, so the apply result comment falls back "
        "to artifact-only status (see docs/github-app.md).\n"
    )


def test_apply_check_state_applied_with_done_check_stays_applied():
    """A done check is not a pending one, though its name is in both sets. Mutation: drop
    `and name not in done` from apply_check_state."""
    rows = [_row(status="applied", stack_path="stacks/app")]
    ac.apply_check_state(rows, {"apply / stacks/app / dev-eu"}, {"apply / stacks/app / dev-eu"})
    assert rows[0]["status"] == "applied"


def test_apply_check_state_applied_with_pending_check_becomes_unrecorded():
    # tofu apply succeeded, but Save state, the completion token mint or Complete the
    # apply check failed (or the job was cancelled) after the cell summary was composed
    # and uploaded.
    rows = [_row(status="applied", stack_path="stacks/app")]
    ac.apply_check_state(rows, {"apply / stacks/app / dev-eu"}, set())
    assert rows[0]["status"] == "unrecorded"


def test_apply_check_state_not_attempted_with_done_check_becomes_applied():
    # The mirror image: the apply landed and completed its check, but the
    # cosmetic (continue-on-error) artifact upload dropped, so no cell.json
    # arrived and the row would otherwise claim the check stays pending.
    rows = [_row(status="not_attempted", stack_path="stacks/app", apply_text=None)]
    ac.apply_check_state(rows, {"apply / stacks/app / dev-eu"}, {"apply / stacks/app / dev-eu"})
    assert rows[0]["status"] == "applied"
    assert rows[0]["apply_text"] is None  # No output to show, so it renders its bare line.


def test_apply_check_state_leaves_rows_alone_when_check_state_is_unknown():
    # Degradation contract: no data (scan failed, empty file) must
    # render byte-identically to the artifact-only behaviour.
    rows = [
        _row(status="applied", stack_path="stacks/app"),
        _row(status="not_attempted", stack_path="stacks/db", apply_text=None),
    ]
    ac.apply_check_state(rows, set(), set())
    assert [r["status"] for r in rows] == ["applied", "not_attempted"]


def test_apply_check_state_never_downgrades_failed_or_blocked():
    # A red row against a green check means another run applied that cell: over-reporting,
    # nothing stranded, and the gate remains the truth. Downgrading here lets an unrelated
    # run's green check hide a real failure in this one.
    done = {"apply / stacks/app / dev-eu", "apply / stacks/db / dev-eu"}
    rows = [
        _row(status="failed", stack_path="stacks/app"),
        _row(status="blocked", stack_path="stacks/db", reason="state restore failed"),
    ]
    ac.apply_check_state(rows, done, done)
    assert [r["status"] for r in rows] == ["failed", "blocked"]


def test_load_check_maps_missing_file_warns_and_is_no_data(tmp_path, capsys):
    """The scan step always writes checks.jsonl, so absence is a failure: no data and a warning,
    not a crash and not silence. Mutation: restore `except FileNotFoundError: return set(), set()`
    ahead of the catch-all."""
    present, done = ac.load_check_maps(str(tmp_path / "nope.jsonl"), APP_ID)
    assert (present, done) == (set(), set())
    assert "::warning::" in capsys.readouterr().out


def test_load_check_maps_empty_file_is_no_data(tmp_path):
    p = tmp_path / "checks.jsonl"
    p.write_text("", encoding="utf-8")
    assert ac.load_check_maps(str(p), APP_ID) == (set(), set())


def test_load_check_maps_reads_jsonl(tmp_path):
    p = tmp_path / "checks.jsonl"
    p.write_text("\n".join(_jsonl(_check("apply / stacks/app / dev-eu"))), encoding="utf-8")
    present, done = ac.load_check_maps(str(p), APP_ID)
    assert present == done == {"apply / stacks/app / dev-eu"}


def test_load_check_maps_malformed_line_degrades_with_a_warning(tmp_path, capsys):
    # A malformed checks.jsonl (parse_jsonl's SystemExit) must cost only the
    # check-state axis, never the whole render step -- degrade to no data with
    # a warning rather than propagate.
    p = tmp_path / "checks.jsonl"
    p.write_text("not json\n", encoding="utf-8")
    assert ac.load_check_maps(str(p), APP_ID) == (set(), set())
    assert "::warning::" in capsys.readouterr().out


def test_load_check_maps_non_numeric_app_id_degrades_with_a_warning(tmp_path, capsys):
    # A non-numeric SHIPMATE_APP_ID (the App's client id pasted in place of its numeric
    # app id) makes ag.from_app's int(app_id) raise ValueError. That must cost only the
    # check-state display axis, the same degradation as a malformed checks.jsonl.
    p = tmp_path / "checks.jsonl"
    p.write_text("\n".join(_jsonl(_check("apply / stacks/app / dev-eu"))), encoding="utf-8")
    assert ac.load_check_maps(str(p), "Iv1.notanumericid") == (set(), set())
    assert "::warning::" in capsys.readouterr().out


def test_cell_json_result_enum_is_unchanged():
    # Display statuses are a superset of the artifact enum. The normative
    # cell.json grammar in CONTRACT.md must not drift because the comment grew
    # a display state.
    assert frozenset({"applied", "failed", "blocked"}) == ac._RESULTS


def test_unrecorded_note_empty_when_no_unrecorded_row():
    assert ac._unrecorded_note([_row(status="applied"), _row(status="failed")]) == ""


def test_unrecorded_note_lists_every_affected_cell():
    rows = [
        _row(status="unrecorded", stack_display="db", environment="prod"),
        _row(status="unrecorded", stack_display="auth", environment="prod"),
    ]
    note = ac._unrecorded_note(rows)
    assert "**db (prod)**" in note and "**auth (prod)**" in note


def test_unrecorded_note_escapes_evil_stack_and_env_names():
    # stack_display and environment are author-controlled (a Terramate tag, a GitHub
    # Environment name, apply-cell's stack input). Bold, not a backtick code span:
    # _md_escape does not escape a backtick, so a span could be broken out of.
    rows = [
        _row(
            status="unrecorded",
            stack_display="x</summary><b>evil",
            environment="e</summary>vil",
        )
    ]
    note = ac._unrecorded_note(rows)
    assert "</summary>" not in note
    assert "<b>" not in note
    assert "&lt;/summary&gt;&lt;b&gt;evil" in note
    assert "`" not in note.split("`shipmate / gate`")[0]


def test_unrecorded_note_names_every_cell_when_under_the_cap():
    rows = [
        _row(status="unrecorded", stack_display=f"s{i}", stack_path=f"stacks/s{i}")
        for i in range(ac._UNRECORDED_NAMED)
    ]
    note = ac._unrecorded_note(rows)
    assert "more" not in note
    for i in range(ac._UNRECORDED_NAMED):
        assert f"**s{i} (dev-eu)**" in note


def test_build_comment_wide_unrecorded_run_still_produces_a_comment():
    # A whole matrix stranded at once, with long author-controlled display names, must
    # still render rather than raise.
    long_name = "s" * 180
    rows = [
        _row(
            status="unrecorded",
            stack_display=f"{long_name}{i:03}",
            stack_path=f"stacks/{long_name}{i:03}",
            apply_text="x" * 400,
        )
        for i in range(200)
    ]
    body = ac.build_comment(rows, [], RUN_URL, [], [], "prod")
    assert len(body) <= ac.sc.HARD_CAP
    assert ac._unrecorded_note(rows) in body.split("\n\n")


def test_load_check_maps_malformed_shape_degrades_with_a_warning(tmp_path, capsys):
    """Valid JSON per line, but not the check-run shape the helpers expect: a scalar element inside
    `.check_runs` raises inside from_app's own `(r.get("app") or {})`, and a dict that survives
    from_app with no usable `name` reaches latest_by_name's `run["name"].startswith(...)`. Either
    way it costs the check-state axis only."""
    reaching = [
        '"just a string"',
        "42",
        "null",
        json.dumps({"app": {"id": int(APP_ID)}, "status": "completed"}),
        json.dumps({"app": {"id": int(APP_ID)}, "name": None, "status": "completed"}),
    ]
    for payload in reaching:
        p = tmp_path / "checks.jsonl"
        p.write_text(payload, encoding="utf-8")
        assert ac.load_check_maps(str(p), APP_ID) == (set(), set()), payload
        assert "::warning::" in capsys.readouterr().out, payload


def test_load_check_maps_drops_records_with_no_app_silently(tmp_path, capsys):
    # A well-formed dict with no (or a foreign) `app` is not corruption but from_app's
    # fail-closed filter working, so it degrades to no data for that name without a
    # warning. Pinned so a widened except clause cannot start shouting about it.
    for payload in ({"status": "completed"}, {"name": "apply / stacks/app / dev-eu"}):
        p = tmp_path / "checks.jsonl"
        p.write_text(json.dumps(payload), encoding="utf-8")
        assert ac.load_check_maps(str(p), APP_ID) == (set(), set()), payload
        assert "::warning::" not in capsys.readouterr().out, payload


def test_check_name_grammar_matches_apply_cells_construction():
    """Coupling: apply-snapshot builds the apply check's name, to look up the pre-existing check
    ids before any wave runs -- apply-cell holds no App key and builds none -- and apply-comment
    forward-builds the same string to look it up. A divergence is silent: every lookup misses,
    `apply_check_state` reads every check as unknown, and the comment reverts to the
    artifact-only rendering this corrects. Same posture as
    test_cell_schema_guard_apply_cell_writes_every_required_key."""
    src = (_ENGINE / "scripts" / "apply-snapshot").read_text(encoding="utf-8")
    expected = 'f"apply / {stack} / {env}"'
    assert expected in src, (
        "apply-snapshot no longer builds the apply check name as "
        "'apply / <stack path> / <env>' -- scripts/apply-comment's _check_name "
        "and _job_url forward-build that exact grammar to look the check up, "
        "and a mismatch makes every lookup miss silently"
    )
    # And the reader's half, exercised rather than restated: the name `_check_name`
    # builds for a known row must be that same string.
    row = _row(environment="dev-eu", stack_path="stacks/app")
    assert ac._check_name(row) == "apply / stacks/app / dev-eu"


def test_env_level_count_matches_env_orders():
    # apply-comment keeps its own copy of the constant, so the equality is pinned here
    # rather than by construction.
    assert ac.MAX_ENV_LEVELS == eo.MAX_ENV_LEVELS


def test_wave_job_name_matches_the_apply_check_grammar():
    """Coupling: `_job_url` resolves a row's log link by matching the apply check name as a `/
    `-boundary suffix of the run's job names, which works only because every wave job's `name:` is
    byte-identical to that check name. Nothing else enforces it, and a rename downgrades every link
    to the run URL -- the documented degradation, so no test fails on it.

    Mutation: change the `apply / ...` literal on wave0's `name: &wave-name`, or replace one wave's
    `name: *wave-name` or `steps: *wave-steps` with a variant, or feed apply-cell's `stack:` from
    another matrix key."""
    jobs = workflow_yaml("apply-env-level.yml")["jobs"]
    expected = "apply / ${{ matrix.stack }} / ${{ matrix.environment }}"
    # Width from waves.MAX_WAVES, so a bump cannot leave this asserting the old count.
    max_waves = wv.MAX_WAVES
    waves = [f"wave{i}" for i in range(max_waves)]
    names = [jobs[w].get("name") for w in waves]
    assert names == [expected] * max_waves, (
        f"all {max_waves} wave job display names must stay byte-identical to the "
        "'apply / <stack path> / <env>' check-name grammar -- scripts/apply-comment's "
        f"_job_url matches it as a job-name suffix (got: {sorted(set(names))})"
    )
    # The two literals agree only if the wave job hands apply-cell the same matrix keys it
    # renders from.
    wired = [
        (step["with"]["stack"], step["with"]["env"])
        for w in waves
        for step in jobs[w]["steps"]
        if str(step.get("uses", "")).endswith("/actions/apply-cell")
    ]
    assert wired == [("${{ matrix.stack }}", "${{ matrix.environment }}")] * max_waves, (
        "every wave job must pass apply-cell the same matrix keys its display name "
        "renders (`stack: ${{ matrix.stack }}`, `env: ${{ matrix.environment }}`) -- "
        "apply-cell builds the check name from those two inputs, so a different "
        f"source for either silently breaks the name/job-name equality (got: {sorted(set(wired))})"
    )
    # The reader's half, exercised: a nested-display job name built from that grammar
    # must resolve, for the same row `_check_name` builds.
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("post-merge / L0 / apply / stacks/app / dev-eu", "https://gh/job/1")]
    assert ac._job_url(row, jobs, RUN_URL) == "https://gh/job/1"


def _main_env(monkeypatch, tmp_path, cells_dir, waves_json, checks_path):
    monkeypatch.setenv("CELLS", str(cells_dir))
    monkeypatch.setenv("SHIPMATE_ENVIRONMENT", "dev-eu")
    monkeypatch.setenv("SHIPMATE_WAVES_JSON", waves_json)
    for i in range(ac.MAX_ENV_LEVELS):
        monkeypatch.setenv(f"SHIPMATE_ENVLEVEL{i}_WAVES", "")
    monkeypatch.setenv("SHIPMATE_RESULTS", "success")
    monkeypatch.setenv("SHIPMATE_CHECKS", checks_path)
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)
    monkeypatch.setenv("SHIPMATE_HEAD_SHA", SHA)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))


def test_lock_note_names_the_cell_the_lock_and_the_release_command():
    rows = [
        _row(
            status="failed",
            stack_display="app",
            environment="dev-eu",
            apply_text=_fixture_text("lock_error_s3.txt"),
        )
    ]
    note = ac._lock_note(rows, "dev-eu")
    assert "**app (dev-eu)**" in note
    assert "0f866bdc-d621-7230-876f-fa7398eff1f8" in note
    assert "2026-08-20 19:53:19" in note
    assert "shipmate unlock dev-eu" in note


def test_lock_note_is_empty_without_a_lock():
    rows = [
        _row(
            status="failed",
            stack_display="app",
            environment="dev-eu",
            apply_text="Error: AccessDenied\n",
        )
    ]
    assert ac._lock_note(rows, "dev-eu") == ""


def test_lock_note_ignores_a_lock_in_a_cell_that_did_not_fail():
    # A lock report in an applied cell's output is a retry that then succeeded, or
    # author-controlled text imitating one; nothing is stranded.
    rows = [
        _row(
            status="applied",
            stack_display="app",
            environment="dev-eu",
            apply_text=_fixture_text("lock_error_local.txt"),
        )
    ]
    assert ac._lock_note(rows, "dev-eu") == ""


def test_lock_note_promises_no_run_attribution():
    rows = [
        _row(
            status="failed",
            stack_display="app",
            environment="dev-eu",
            apply_text=_fixture_text("lock_error_s3.txt"),
        )
    ]
    note = ac._lock_note(rows, "dev-eu")
    # `Who` is useless (runner@fv-az… on a hosted runner), so lock-info never returns it
    # and the note may not claim to name a run.
    assert "LAPTOP" not in note and "run #" not in note


def test_lock_note_caps_the_named_cells():
    rows = [
        _row(
            status="failed",
            stack_display=f"s{i}",
            stack_path=f"stacks/s{i}",
            environment="dev-eu",
            apply_text=_fixture_text("lock_error_s3.txt"),
        )
        for i in range(8)
    ]
    note = ac._lock_note(rows, "dev-eu")
    assert "and 3 more" in note
    assert "s7" not in note


def test_lock_note_escapes_author_controlled_names():
    # Same reasoning as the unrecorded note: stack_display and environment are
    # author-controlled, so bold plus _md_escape, never a backtick code span, because
    # _md_escape does not escape a backtick.
    rows = [
        _row(
            status="failed",
            stack_display="x</summary><b>evil",
            environment="e</summary>vil",
            apply_text=_fixture_text("lock_error_s3.txt"),
        )
    ]
    note = ac._lock_note(rows, "dev-eu")
    assert "</summary>" not in note
    assert "<b>" not in note
    assert "&lt;/summary&gt;&lt;b&gt;evil" in note


def _lock_text(created):
    """The s3 lock capture with a chosen `Created:` value."""
    return _fixture_text("lock_error_s3.txt").replace(
        "2026-08-20 19:53:19.7388258 +0000 UTC", created
    )


def test_lock_note_omits_held_since_when_the_created_value_was_refused():
    # `Created` is unbounded runtime output, so lock-info blanks a value that fails its
    # guard rather than losing the whole lock, the id being what the engine acts on. The
    # note must then read grammatically without it.
    rows = [
        _row(
            status="failed",
            stack_display="app",
            environment="dev-eu",
            apply_text=_lock_text("x</summary><b>evil **and** bold"),
        )
    ]
    note = ac._lock_note(rows, "dev-eu")
    assert "0f866bdc-d621-7230-876f-fa7398eff1f8" in note
    assert "held since" not in note
    assert "evil" not in note and "</summary>" not in note
    assert "(lock **0f866bdc-d621-7230-876f-fa7398eff1f8**). An earlier apply" in note


def test_lock_note_cannot_blow_the_comment_cap():
    """apply.txt is capped at SIZE_BUDGET, not per field: a crafted `Created:` line of ~59,900 chars
    parses fine, and `_LOCK_NAMED` bounds the cell count, not the rendered length. Notes belong to
    the part of the comment that never sheds, so an unguarded value costs the comment at 2-3
    cells."""
    rows = [
        _row(
            status="failed",
            stack_display=f"s{i}",
            stack_path=f"stacks/s{i}",
            environment="dev-eu",
            apply_text=_lock_text("2" * 59_900),
        )
        for i in range(3)
    ]
    body = ac.build_comment(rows, [], RUN_URL, [], [], "dev-eu")
    assert len(body) <= ac.sc.HARD_CAP
    assert "state lock held" in body


def test_lock_note_bare_form_stays_bare():
    rows = [
        _row(
            status="failed",
            stack_display="app",
            environment="dev-eu",
            apply_text=_fixture_text("lock_error_s3.txt"),
        )
    ]
    assert "shipmate unlock <env>" in ac._lock_note(rows, "")


_MAIN_RUN = "https://github.com/acme/repo/actions/runs/42"
#: `provenance(SHA)` under `_main_env`, hand-written.
_MAIN_AT = (
    "at [0123456](https://github.com/acme/repo/commit/0123456789abcdef0123456789abcdef01234567) "
    f"in [run #7]({_MAIN_RUN})"
)


def _main_body(tmp_path):
    ac.main()
    return (tmp_path / "comment.md").read_text(encoding="utf-8")


def test_main_folds_checks_jsonl_into_the_rendered_comment(monkeypatch, tmp_path):
    """The seam the action depends on, end to end, since GitHub Actions cannot run locally: the
    file the scan step writes is read, filtered to the App, folded into comment.md, under the head
    SHA the render step passes.

    Mutation: drop `apply_check_state`'s result in main -- the row reads applied, red.
    Mutation: read the head SHA from `HEAD_SHA` in main -- `at an unknown commit`, red."""
    cells = tmp_path / "cells"
    _write_cell(cells, "dev-eu", "stacks-app", _cell(stack="app", stack_path="stacks/app"))
    checks = tmp_path / "checks.jsonl"
    checks.write_text(
        "\n".join(
            _jsonl(_check("apply / stacks/app / dev-eu", status="in_progress", conclusion=None))
        ),
        encoding="utf-8",
    )
    waves = json.dumps({"wave0": [{"stack": "stacks/app", "environment": "dev-eu"}]})
    _main_env(monkeypatch, tmp_path, cells, waves, str(checks))
    assert _main_body(tmp_path) == (
        f"### shipmate apply dev-eu\n\n🟠 1 not recorded {_MAIN_AT}\n\n"
        f'🟠 app (dev-eu): applied, not recorded <a href="{_MAIN_RUN}">logs</a>\n\n'
        "not recorded: **app (dev-eu)**. The apply succeeded but its apply check is not recorded "
        "as complete (it failed, was cancelled, or a newer plan re-created it), so "
        "`shipmate / gate` stays pending. Re-plan and re-apply.\n\n" + HINT
    )


def test_main_promotes_a_missing_artifact_whose_check_is_done(monkeypatch, tmp_path):
    """The mirror direction through main(): no artifact at all, check done. Nothing was captured,
    so the row is its bare line with no counts, and the not-attempted note is gone.

    Mutation: drop the `not_attempted` promotion in `apply_check_state` -- red."""
    cells = tmp_path / "cells"
    cells.mkdir()
    checks = tmp_path / "checks.jsonl"
    checks.write_text("\n".join(_jsonl(_check("apply / stacks/app / dev-eu"))), encoding="utf-8")
    waves = json.dumps({"wave0": [{"stack": "stacks/app", "environment": "dev-eu"}]})
    _main_env(monkeypatch, tmp_path, cells, waves, str(checks))
    assert _main_body(tmp_path) == (
        f"### shipmate apply dev-eu\n\n🟢 1 applied {_MAIN_AT}\n\n"
        f'🟢 stacks/app (dev-eu): applied <a href="{_MAIN_RUN}">logs</a>'
    )


def test_main_without_checks_file_renders_the_artifact_only_comment(monkeypatch, tmp_path):
    """Every scan failure lands here: no checks.jsonl means no data, which means unknown, so the
    comment reads as the artifact-only one.

    Mutation: treat an absent check name as pending in `apply_check_state` -- red."""
    cells = tmp_path / "cells"
    _write_cell(cells, "dev-eu", "stacks-app", _cell(stack="app", stack_path="stacks/app"))
    waves = json.dumps({"wave0": [{"stack": "stacks/app", "environment": "dev-eu"}]})
    _main_env(monkeypatch, tmp_path, cells, waves, str(tmp_path / "absent.jsonl"))
    assert _main_body(tmp_path) == (
        f"### shipmate apply dev-eu\n\n🟢 1 applied {_MAIN_AT}\n\n"
        f'🟢 app (dev-eu): applied <a href="{_MAIN_RUN}">logs</a>'
    )


def _all_envs_main_env(monkeypatch, tmp_path, row_env, ungated, no_review):
    """`_main_env` for the all-environments form with one applied `app` cell in `row_env`, an
    empty (readable) checks file so stdout holds only what `main` prints, and the five env sets:
    `sbx` explicit, `stg` skipped, `prod` held, then `ungated` and `no_review`."""
    cells = tmp_path / "cells"
    _write_cell(cells, "x", "stacks-app", _cell(environment=row_env))
    checks = tmp_path / "checks.jsonl"
    checks.write_text("", encoding="utf-8")
    _main_env(monkeypatch, tmp_path, cells, "", str(checks))
    monkeypatch.setenv("SHIPMATE_ENVIRONMENT", "")
    monkeypatch.setenv("SHIPMATE_EXCLUDED_ENVS", json.dumps(["sbx"]))
    monkeypatch.setenv("SHIPMATE_SKIPPED_ENVS", json.dumps(["stg"]))
    monkeypatch.setenv("SHIPMATE_REVIEW_HELD_ENVS", json.dumps(["prod"]))
    monkeypatch.setenv("SHIPMATE_APPLIED_UNGATED_ENVS", json.dumps(ungated))
    monkeypatch.setenv("SHIPMATE_REVIEW_NOT_REQUIRED_ENVS", json.dumps(no_review))


def _all_envs_body(row_env):
    return (
        f"### shipmate apply\n\n🟢 1 applied {_MAIN_AT}\n\n"
        f'🟢 app ({row_env}): applied <a href="{_MAIN_RUN}">logs</a>\n\n'
        "🟡 sbx: left pending (explicit), comment `shipmate apply sbx`\n"
        "⚪ stg: skipped, ordered after an environment not applying this run\n"
        "⚪ prod: held, the review state does not permit applying\n"
        "held: get an approving review, or resolve or dismiss a requested-changes review. "
        "The run log's apply-all-detect notice names the decision seen."
    )


def test_main_reads_every_env_set_under_its_own_name(monkeypatch, tmp_path, capsys):
    """Five inputs are JSON arrays of env names with one shape, so a crossed read in main renders
    a plausible comment, or notice, naming the wrong environments for the wrong reason.

    Mutation: swap the `SHIPMATE_REVIEW_HELD_ENVS` and `SHIPMATE_APPLIED_UNGATED_ENVS` reads in
    main -- red.
    Mutation: read `SHIPMATE_REVIEW_NOT_REQUIRED_ENVS` under another name -- the qa notice is
    gone, red."""
    _all_envs_main_env(monkeypatch, tmp_path, "qa", ["dev-eu"], ["qa"])
    assert _main_body(tmp_path) == _all_envs_body("qa")
    assert capsys.readouterr().out == (
        f"::notice::dev-eu{_UNGATED_NOTICE}\n::notice::qa{_NO_REVIEW_NOTICE}\n"
    )


def test_main_prints_the_audit_notices_escaped_and_the_comment_carries_neither(
    monkeypatch, tmp_path, capsys
):
    """The ungated and no-review environments go to the step log as `::notice::` workflow
    commands, not into the comment, and the comment carries no gate line either. Env names are
    author-controlled, so a `%`, CR or LF in one is escaped as workflow-command data: raw, a
    newline would start a second workflow command.

    Mutation: drop `command_data` from the ungated notice in `notices` -- red.
    Mutation: drop `command_data` from the no-review notice in `notices` -- red.
    Mutation: append a `gate: complete` line to `_footer_parts` -- red."""
    _all_envs_main_env(monkeypatch, tmp_path, "q%2\r", ["u%1\n::error::x"], ["q%2\r"])
    assert _main_body(tmp_path) == _all_envs_body("q%2 ")
    assert capsys.readouterr().out == (
        f"::notice::u%251%0A::error::x{_UNGATED_NOTICE}\n::notice::q%252%0D{_NO_REVIEW_NOTICE}\n"
    )
