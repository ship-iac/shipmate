"""Tests for scripts/upsert-comment: the sticky-comment lookup, write and one-word result."""

import json
import os
import shutil
import subprocess
import sys

import pytest
from _loader import (
    ACTIONS,
    SCRIPTS,
    bash_only,
    load_script,
    run_with_gh_recorder,
    step_by,
)

uc = load_script("upsert-comment")

SUMMARY = "<!-- shipmate:summary -->"
DOCTOR = "<!-- shipmate:doctor -->"
_FILTER = '.[] | select(.user.type == "Bot") | {id, head: ((.body // "")[0:64])}'
LIST_ARGV = [
    "gh",
    "api",
    "--paginate",
    "--jq",
    _FILTER,
    "repos/o/r/issues/7/comments?per_page=100",
]


def _head(cid, head):
    return json.dumps({"id": cid, "head": head})


def _upsert(
    monkeypatch,
    tmp_path,
    listing,
    *flags,
    first_line=SUMMARY,
    list_fails=False,
    write_fails=False,
    pr="7",
):
    """Run `main` in-process against a fake API; return (exit code, `run` calls)."""
    body = tmp_path / "comment.md"
    body.write_text(f"{first_line}\n### shipmate plan\n", encoding="utf-8")
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[2] == "--paginate":
            if list_fails:
                raise SystemExit("command failed (1): gh api\nHTTP 502")
            return "".join(line + "\n" for line in listing)
        if write_fails:
            raise SystemExit("command failed (1): gh api\nHTTP 403")
        return '{"id": 99, "body": "response"}'

    monkeypatch.setattr(uc, "run", fake_run)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(sys, "argv", ["upsert-comment", pr, str(body), *flags])
    try:
        uc.main()
    except SystemExit as exc:
        return exc.code, calls
    return 0, calls


def _patch(cid, tmp_path):
    return [
        "gh",
        "api",
        "-X",
        "PATCH",
        f"repos/o/r/issues/comments/{cid}",
        "-F",
        f"body=@{tmp_path / 'comment.md'}",
    ]


def _post(tmp_path):
    return ["gh", "api", "repos/o/r/issues/7/comments", "-F", f"body=@{tmp_path / 'comment.md'}"]


def test_a_match_is_patched_and_prints_updated(monkeypatch, tmp_path, capsys):
    """The whole listing and PATCH argv, hand-written; stdout is the one word, never gh's response.

    Mutations: drop `per_page=100`; swap the PATCH and POST branches; print the PATCH's return.
    """
    code, calls = _upsert(monkeypatch, tmp_path, [_head(5, SUMMARY + "\n### shipmate plan")])
    assert code == 0
    assert calls == [LIST_ARGV, _patch(5, tmp_path)]
    assert capsys.readouterr().out == "updated\n"


def test_no_match_is_posted_and_prints_created(monkeypatch, tmp_path, capsys):
    """Mutations: swap the PATCH and POST branches; print the POST's return."""
    code, calls = _upsert(monkeypatch, tmp_path, [])
    assert code == 0
    assert calls == [LIST_ARGV, _post(tmp_path)]
    assert capsys.readouterr().out == "created\n"


def test_a_comment_quoting_the_marker_mid_body_is_not_matched(monkeypatch, tmp_path, capsys):
    """Mutation: `startswith` -> `in`."""
    code, calls = _upsert(monkeypatch, tmp_path, [_head(5, f"### shipmate doctor\n`{SUMMARY}`")])
    assert code == 0
    assert calls == [LIST_ARGV, _post(tmp_path)]
    assert capsys.readouterr().out == "created\n"


def test_the_first_match_wins_across_pages(monkeypatch, tmp_path):
    """`--paginate` runs `--jq` per page and concatenates the lines, so pages arrive as one list.

    Mutation: iterate the listing reversed.
    """
    listing = [_head(1, "hello"), _head(5, SUMMARY), _head(6, SUMMARY + "x")]
    code, calls = _upsert(monkeypatch, tmp_path, listing)
    assert code == 0
    assert calls == [LIST_ARGV, _patch(5, tmp_path)]


def test_update_only_with_no_match_writes_nothing(monkeypatch, tmp_path, capsys):
    """Mutation: ignore `--update-only`."""
    code, calls = _upsert(monkeypatch, tmp_path, [], "--update-only")
    assert code == 0
    assert calls == [LIST_ARGV]
    assert capsys.readouterr().out == "skipped\n"


def test_update_only_with_a_match_still_patches(monkeypatch, tmp_path, capsys):
    """Mutation: treat `--update-only` as "write nothing"."""
    code, calls = _upsert(monkeypatch, tmp_path, [_head(5, SUMMARY)], "--update-only")
    assert code == 0
    assert calls == [LIST_ARGV, _patch(5, tmp_path)]
    assert capsys.readouterr().out == "updated\n"


def test_a_listing_failure_prints_list_failed_and_writes_nothing(monkeypatch, tmp_path, capsys):
    """`_shipmate.run` raises SystemExit on a failed `gh`; the caller decides on `list-failed`.

    Mutation: drop the `except SystemExit` around the listing.
    """
    code, calls = _upsert(monkeypatch, tmp_path, [], list_fails=True)
    assert code == 0
    assert calls == [LIST_ARGV]
    out, err = capsys.readouterr()
    assert out == "list-failed\n"
    assert err == "command failed (1): gh api\nHTTP 502\n"


def test_a_write_failure_exits_non_zero_with_an_error(monkeypatch, tmp_path, capsys):
    """Mutation: catch the write failure and print `created`."""
    code, calls = _upsert(monkeypatch, tmp_path, [], write_fails=True)
    assert (
        code == "::error::could not write the sticky comment: command failed (1): gh api\nHTTP 403"
    )
    assert calls == [LIST_ARGV, _post(tmp_path)]
    assert capsys.readouterr().out == ""


def test_the_marker_is_the_body_files_first_line(monkeypatch, tmp_path, capsys):
    """A doctor body does not match the summary sticky.

    Mutation: hard-code the summary marker.
    """
    code, calls = _upsert(monkeypatch, tmp_path, [_head(5, SUMMARY)], first_line=DOCTOR)
    assert code == 0
    assert calls == [LIST_ARGV, _post(tmp_path)]
    assert capsys.readouterr().out == "created\n"


@pytest.mark.parametrize(
    "first_line",
    [
        "### shipmate plan",
        f"{SUMMARY} trailing",
        "<!-- shipmate:Summary -->",
        f"<!-- shipmate:{'a' * 41} -->",
        "",
    ],
)
def test_a_first_line_that_is_not_a_whole_marker_is_refused_before_any_call(
    monkeypatch, tmp_path, first_line
):
    """Mutations: `fullmatch` -> `match`; drop the `{1,40}` bound."""
    code, calls = _upsert(monkeypatch, tmp_path, [], first_line=first_line)
    path = tmp_path / "comment.md"
    assert code == f"::error::{path}: the first line must be a shipmate marker (got: {first_line})"
    assert calls == []


@pytest.mark.parametrize("pr", ["7a", "", "../1", "0", "007"])
def test_a_value_that_is_not_a_pull_request_number_is_refused_before_any_call(
    monkeypatch, tmp_path, pr
):
    """`PR` is bound into the API path. Mutations: delete the check; check with `isdigit()`
    (`0` and `007` red)."""
    code, calls = _upsert(monkeypatch, tmp_path, [], pr=pr)
    assert code == f"::error::PR must be a pull request number (got: {pr})"
    assert calls == []


def test_the_pull_request_number_is_checked_before_the_body_file_is_read(monkeypatch, tmp_path):
    """Mutation: open BODYFILE above the PR check (FileNotFoundError instead of the refusal)."""
    missing = tmp_path / "missing.md"
    monkeypatch.setattr(uc, "run", lambda args: pytest.fail(f"unexpected gh call: {args}"))
    monkeypatch.setattr(sys, "argv", ["upsert-comment", "0", str(missing)])
    with pytest.raises(SystemExit) as exc:
        uc.main()
    assert exc.value.code == "::error::PR must be a pull request number (got: 0)"


@pytest.mark.parametrize(
    "line",
    ["not json", '{"id": 5}', '{"head": "x"}', '{"id": 5, "head": null}', "[1]", "5"],
)
def test_an_unparseable_listing_line_prints_list_failed_and_writes_nothing(
    monkeypatch, tmp_path, capsys, line
):
    """The doctor step degrades on `list-failed`; a traceback would fail it instead.

    Mutation: drop the `try`/`except` around the line's parse.
    """
    code, calls = _upsert(monkeypatch, tmp_path, [line])
    assert code == 0
    assert calls == [LIST_ARGV]
    assert capsys.readouterr() == (
        "list-failed\n",
        f"unparseable comment listing line: {line!r}\n",
    )


@pytest.mark.parametrize("flags", [["--update"], ["--update-only", "x"]])
def test_unknown_arguments_are_refused_before_any_call(monkeypatch, tmp_path, flags):
    """Mutation: accept any third argument as `--update-only`."""
    code, calls = _upsert(monkeypatch, tmp_path, [], *flags)
    assert code == "::error::usage: upsert-comment PR BODYFILE [--update-only]"
    assert calls == []


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_the_listing_filter_keeps_bot_heads_only(monkeypatch, tmp_path):
    """The `--jq` string from the listing argv the script sends, run by jq over a crafted page.

    Mutations: `"Bot"` -> `"bot"`; drop `// ""`.
    """
    _, calls = _upsert(monkeypatch, tmp_path, [])
    jq_filter = calls[0][calls[0].index("--jq") + 1]
    page = [
        {"id": 1, "user": {"type": "Bot"}, "body": SUMMARY + "\nplan"},
        {"id": 2, "user": {"type": "User"}, "body": SUMMARY + "\nhuman"},
        {"id": 3, "user": {"type": "Bot"}, "body": None},
        {"id": 4, "user": {"type": "Bot"}, "body": "x" * 60 + "TAIL-CUT-OFF"},
    ]
    jq = subprocess.run(
        ["jq", "-c", jq_filter],
        input=json.dumps(page),
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert jq.returncode == 0, jq.stderr
    assert jq.stdout.splitlines() == [
        '{"id":1,"head":"<!-- shipmate:summary -->\\nplan"}',
        '{"id":3,"head":""}',
        '{"id":4,"head":"' + "x" * 60 + 'TAIL"}',
    ]


@bash_only
def test_a_listing_that_fails_after_printing_a_match_writes_nothing(tmp_path):
    """A later page failing after an earlier one printed the sticky: no ids, so no POST, which
    would create a second sticky.

    Mutation: read the listing with a streaming reader that keeps partial output.
    """
    (tmp_path / "comment.md").write_text(SUMMARY + "\n", encoding="utf-8")
    script = (SCRIPTS / "upsert-comment").as_posix()
    proc, calls = run_with_gh_recorder(
        tmp_path,
        f'python3 "{script}" 7 comment.md\n',
        {**os.environ, "GITHUB_REPOSITORY": "o/r"},
        listing=_head(5, SUMMARY) + "\n",
        list_rc=1,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "list-failed\n"
    assert calls == [_LIST]


#: The listing each caller step makes, as the argv after `gh`.
_LIST = LIST_ARGV[1:]


def _post_of(body):
    return ["api", "repos/o/r/issues/7/comments", "-F", f"body=@{body}"]


def _patch_of(body):
    return ["api", "-X", "PATCH", "repos/o/r/issues/comments/5", "-F", f"body=@{body}"]


_SUMMARY_LIST_FAILED = (
    "::error::could not list this pull request's comments, so the sticky plan comment was not "
    "updated; re-plan this pull request (comment shipmate plan or push)\n"
)
_SUMMARY_SKIPPED = (
    "nothing changed, no existing plan comment, no doctor findings; nothing to post\n"
)
_SHA = "a" * 40
#: The pull request head read every writing mode makes first.
_PULLS = ["api", "repos/o/r/pulls/7", "--jq", ".head.sha"]
_DEGRADED = (
    "::warning::the sticky plan comment was not updated with why the gate is held; the gate "
    "description says it\n"
)
#: The held body for `_run_summary`'s defaults, hand-written.
_HELD_BODY = (
    f"{SUMMARY}\n### shipmate plan\n\n"
    f"🔴 the gate is held at [aaaaaaa](https://gh/o/r/commit/{_SHA}) in "
    "[run #7](https://gh/o/r/actions/runs/1)\n\n"
    "```\nthe reason\n```\n\n"
    "Comment `shipmate help` for the available commands."
)


def _run_summary(
    tmp_path, mode, *, warned="false", unmanaged="false", lost="false", head=_SHA, env=None, **gh
):
    """The summary action's upsert step under its own env names; return (process, gh calls).
    `env` adds to or, with None values, removes from the step's environment."""
    (tmp_path / "comment.md").write_text(SUMMARY + "\n### shipmate plan\n", encoding="utf-8")
    (tmp_path / "check-runs.jsonl").write_text("", encoding="utf-8")
    full = {
        **os.environ,
        "GH_TOKEN": "x",
        "PR": "7",
        "HEAD_SHA": _SHA,
        "MODE": mode,
        "DOCTOR_WARNED": warned,
        "UNMANAGED": unmanaged,
        "LOST": lost,
        "SHIPMATE_HOLD_REASON": "the reason",
        "SHIPMATE_MISSING": "[]",
        "SHIPMATE_PLAN_RESULT": "failure",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_SERVER_URL": "https://gh",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_NUMBER": "7",
        "GITHUB_ACTION_PATH": str(ACTIONS / "summary"),
    }
    for k, v in (env or {}).items():
        if v is None:
            full.pop(k, None)
        else:
            full[k] = v
    run = step_by("summary", name="Upsert sticky comment")["run"]
    return run_with_gh_recorder(tmp_path, run, full, head=head, **gh)


@bash_only
@pytest.mark.parametrize(
    ("mode", "stdout"),
    [
        (
            "draft",
            (
                "the pull request was a draft when this run started and was not planned; the "
                "sticky "
                "plan comment was left as it is\n"
            ),
        ),
        (
            "hold",
            (
                "::warning::the plan job was cancelled, so this run writes no gate; the sticky "
                "plan comment was left as it is\n"
            ),
        ),
    ],
)
def test_summary_draft_and_hold_make_no_comment_call(tmp_path, mode, stdout):
    """Mutations: move the `hold` branch below the `upsert-comment` call; route `hold` into the
    held render (drop its branch) -- the head read and the held write follow."""
    proc, calls = _run_summary(tmp_path, mode, listing=_head(5, SUMMARY) + "\n")
    assert (proc.returncode, proc.stdout, calls) == (0, stdout, [])


@bash_only
@pytest.mark.parametrize(
    ("listing", "write"),
    [("", _post_of("held.md")), (_head(5, SUMMARY), _patch_of("held.md"))],
    ids=["no-comment", "existing"],
)
def test_summary_held_writes_the_held_body(tmp_path, listing, write):
    """A held gate creates the comment when there is none and replaces the existing one.
    Mutations: pass `--update-only` on the held write -- `no-comment` writes nothing; render
    without `SHIPMATE_COMMENT_MODE=held` -- no held body, and no write."""
    proc, calls = _run_summary(tmp_path, "held", listing=listing + "\n")
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        0,
        "",
        "",
        [_PULLS, _LIST, write],
    )
    assert (tmp_path / "held.md").read_text(encoding="utf-8") == _HELD_BODY


@bash_only
def test_summary_held_write_failure_degrades(tmp_path):
    """The step passes, so `Create/refresh gate` still writes the `failure` gate with its
    description; the write's error reaches the log as a warning. Mutations: `exit 1` after the
    degraded warning -- red; drop the `sed` -- an `::error::` line."""
    proc, calls = _run_summary(tmp_path, "held", write_rc=1, write_stderr="HTTP 502\n")
    assert (proc.returncode, proc.stdout, calls) == (
        0,
        _DEGRADED,
        [_PULLS, _LIST, _post_of("held.md")],
    )
    assert proc.stderr == (
        "::warning::could not write the sticky comment: command failed (1): gh api "
        "repos/o/r/issues/7/comments -F body=@held.md%0AHTTP 502\n"
    )


@bash_only
def test_summary_held_list_failure_degrades(tmp_path):
    """Mutation: count only a failed run as unwritten (`[ "$result" = failed ]`) -- no
    warning."""
    proc, calls = _run_summary(tmp_path, "held", list_rc=1)
    assert (proc.returncode, proc.stdout, calls) == (0, _DEGRADED, [_PULLS, _LIST])


@bash_only
def test_summary_held_render_failure_writes_no_comment(tmp_path):
    """A render that raises (no `GITHUB_RUN_NUMBER` for `provenance`) never reaches the write.
    Mutation: run `upsert-comment` with comment.md after a failed render too (`|| true` on the
    render) -- the listing and a POST follow."""
    proc, calls = _run_summary(tmp_path, "held", env={"GITHUB_RUN_NUMBER": None})
    assert (proc.returncode, proc.stdout, calls) == (0, _DEGRADED, [_PULLS])
    assert not (tmp_path / "held.md").exists()


@bash_only
@pytest.mark.parametrize("mode", ["held", "post", "nothing-changed"])
def test_summary_writes_nothing_over_a_newer_head(tmp_path, mode):
    """A re-run of an old run never replaces the comment of the head the pull request has moved
    to. Mutation: drop the head comparison -- the listing and a write follow."""
    proc, calls = _run_summary(tmp_path, mode, head="b" * 40, warned="true")
    assert (proc.returncode, proc.stdout, calls) == (
        0,
        (
            f"::notice::pull request #7 has moved past {_SHA}; its sticky plan comment belongs "
            "to the newer head's run and was not written\n"
        ),
        [_PULLS],
    )


@bash_only
def test_summary_held_head_read_failure_degrades(tmp_path):
    """Mutation: fail the step on a failed head read whatever the mode -- exit 1."""
    proc, calls = _run_summary(tmp_path, "held", head_rc=1)
    assert (proc.returncode, proc.stdout, calls) == (0, _DEGRADED, [_PULLS])


@bash_only
@pytest.mark.parametrize("mode", ["post", "nothing-changed"])
def test_summary_post_head_read_failure_fails_the_step(tmp_path, mode):
    """A comment write cannot be judged current, so the job stops before the gate, as on a failed
    listing. Mutation: degrade the failed read for every mode (`exit 0`) -- red."""
    proc, calls = _run_summary(tmp_path, mode, head_rc=1)
    assert (proc.returncode, proc.stdout, calls) == (
        1,
        (
            "::error::could not read the head of pull request #7, so the sticky plan comment was "
            "not updated; re-plan this pull request (comment shipmate plan or push)\n"
        ),
        [_PULLS],
    )


@bash_only
@pytest.mark.parametrize(
    ("mode", "warned", "unmanaged", "lost", "listing", "expected", "stdout"),
    [
        ("nothing-changed", "false", "false", "false", "", [_LIST], _SUMMARY_SKIPPED),
        ("post", "false", "false", "false", "", [_LIST, _post_of("comment.md")], ""),
        ("nothing-changed", "true", "false", "false", "", [_LIST, _post_of("comment.md")], ""),
        ("nothing-changed", "false", "true", "false", "", [_LIST, _post_of("comment.md")], ""),
        ("nothing-changed", "false", "false", "true", "", [_LIST, _post_of("comment.md")], ""),
        (
            "nothing-changed",
            "false",
            "false",
            "false",
            _head(5, SUMMARY),
            [_LIST, _patch_of("comment.md")],
            "",
        ),
    ],
    ids=["quiet", "ordinary", "doctor-warned", "unmanaged", "lost", "quiet-existing"],
)
def test_summary_posts_unless_quiet_and_keeps_an_existing_comment_current(
    tmp_path, mode, warned, unmanaged, lost, listing, expected, stdout
):
    """Nothing changed, no doctor warning, no unmanaged line and no lost pairs line: update only.

    Mutations: delete the `DOCTOR_WARNED` clause (doctor-warned red); delete the `UNMANAGED`
    clause (unmanaged red); delete the `LOST` clause (lost red); delete the `nothing-changed`
    clause (ordinary red).
    """
    proc, calls = _run_summary(
        tmp_path, mode, warned=warned, unmanaged=unmanaged, lost=lost, listing=listing + "\n"
    )
    assert (proc.returncode, proc.stdout, calls) == (0, stdout, [_PULLS, *expected]), proc.stderr


@bash_only
def test_summary_list_failure_fails_the_step_and_writes_nothing(tmp_path):
    """The step fails, so `Create/refresh gate` does not write a gate over a comment that still
    shows the previous head's plan.

    Mutations: misspell `list-failed` in the step; restore the `::warning::` and continue.
    """
    proc, calls = _run_summary(tmp_path, "post", list_rc=1)
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        1,
        _SUMMARY_LIST_FAILED,
        f"command failed (1): {' '.join(LIST_ARGV)}\n",
        [_PULLS, _LIST],
    )


#: A failed write's two-line gh stderr; the `%` shows `command_data` escaping it.
_WRITE_STDERR = "unexpected end of JSON input\nHTTP 502 (50% of retries)\n"


def _write_annotation(body):
    """The whole stderr of a step whose write of `body` failed with `_WRITE_STDERR`."""
    return (
        "::error::could not write the sticky comment: command failed (1): gh api "
        f"repos/o/r/issues/7/comments -F body=@{body}"
        "%0Aunexpected end of JSON input%0AHTTP 502 (50%25 of retries)\n"
    )


@bash_only
def test_summary_write_failure_fails_the_step(tmp_path):
    """The step fails, so `Create/refresh gate` does not run, and its one annotation carries gh's
    whole stderr.

    Mutations: add `|| true` to the `upsert-comment` call; revert `upsert-comment`'s `__main__`
    block to `main()` (the raw multi-line text reaches stderr)."""
    proc, calls = _run_summary(tmp_path, "post", write_rc=1, write_stderr=_WRITE_STDERR)
    assert proc.returncode != 0
    assert calls == [_PULLS, _LIST, _post_of("comment.md")]
    assert proc.stderr == _write_annotation("comment.md")


_DOCTOR_LINE = 'python3 "$GITHUB_ACTION_PATH/../../scripts/doctor" > doctor.md\n'


def _run_doctor(tmp_path, **gh):
    """comment-ops' doctor step with the doctor run replaced by a fixed marker-led report."""
    run = step_by("comment-ops", name="Doctor: render and upsert the sticky comment")["run"]
    # A replace that misses leaves the real doctor running, passing for the wrong reason.
    assert run.count(_DOCTOR_LINE) == 1
    report = f"printf '%s\\n' '{DOCTOR}' '### shipmate doctor' > doctor.md\n"
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "PR_NUMBER": "7",
        "FULLMINT_OUTCOME": "success",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step-summary.md"),
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_ACTION_PATH": str(ACTIONS / "comment-ops"),
    }
    return run_with_gh_recorder(tmp_path, run.replace(_DOCTOR_LINE, report), env, **gh)


@bash_only
def test_doctor_posts_its_report_and_writes_the_job_summary(tmp_path):
    """Mutation: change `_DOCTOR_LINE`'s text (the occurs-once assertion goes red)."""
    proc, calls = _run_doctor(tmp_path, listing=_head(5, SUMMARY) + "\n")
    assert (proc.returncode, proc.stdout, calls) == (0, "", [_LIST, _post_of("doctor.md")])
    summary = (tmp_path / "step-summary.md").read_text(encoding="utf-8")
    assert summary == f"{DOCTOR}\n### shipmate doctor\n"


@bash_only
def test_doctor_list_failure_warns_with_the_retry_hint_and_writes_nothing(tmp_path):
    """Mutation: misspell `list-failed` in the step."""
    proc, calls = _run_doctor(tmp_path, list_rc=1)
    assert (proc.returncode, proc.stdout, calls) == (
        0,
        (
            "::warning::could not list this pull request's comments; the doctor report was not "
            "posted to the pull request this run: read it in this job's summary, or comment "
            "shipmate doctor again to retry\n"
        ),
        [_LIST],
    )


@bash_only
def test_doctor_write_failure_fails_the_step(tmp_path):
    """Mutations: add `|| true` to the `upsert-comment` call; revert `upsert-comment`'s
    `__main__` block to `main()` (the raw multi-line text reaches stderr)."""
    proc, calls = _run_doctor(tmp_path, write_rc=1, write_stderr=_WRITE_STDERR)
    assert proc.returncode != 0
    assert calls == [_LIST, _post_of("doctor.md")]
    assert proc.stderr == _write_annotation("doctor.md")
