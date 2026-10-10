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
    monkeypatch, tmp_path, listing, *flags, first_line=SUMMARY, list_fails=False, write_fails=False
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
    monkeypatch.setattr(sys, "argv", ["upsert-comment", "7", str(body), *flags])
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
    """`_shipmate.run` raises SystemExit on a failed `gh`; the caller degrades on `list-failed`.

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
    "::warning::could not list this pull request's comments; the sticky plan comment was not "
    "updated this run\n"
)
_SUMMARY_SKIPPED = (
    "nothing changed, no existing plan comment, no doctor findings; nothing to post\n"
)


def _run_summary(tmp_path, mode, *, warned="false", unmanaged="false", **gh):
    """The summary action's upsert step under its own env names; return (process, gh calls)."""
    (tmp_path / "comment.md").write_text(SUMMARY + "\n### shipmate plan\n", encoding="utf-8")
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "PR": "7",
        "MODE": mode,
        "PLAN_RESULT": "failure",
        "DOCTOR_WARNED": warned,
        "UNMANAGED": unmanaged,
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_ACTION_PATH": str(ACTIONS / "summary"),
    }
    run = step_by("summary", name="Upsert sticky comment")["run"]
    return run_with_gh_recorder(tmp_path, run, env, **gh)


@bash_only
@pytest.mark.parametrize(
    ("mode", "stdout"),
    [
        (
            "draft",
            "the pull request was a draft when this run started and was not planned; the sticky "
            "plan comment was left as it is\n",
        ),
        (
            "hold",
            "::warning::the plan job (failure) did not produce a trustworthy set of cell "
            "summaries; the sticky plan comment was left as it is\n",
        ),
    ],
)
def test_summary_draft_and_hold_make_no_comment_call(tmp_path, mode, stdout):
    """Mutation: move the `hold` branch below the `upsert-comment` call."""
    proc, calls = _run_summary(tmp_path, mode, listing=_head(5, SUMMARY) + "\n")
    assert (proc.returncode, proc.stdout, calls) == (0, stdout, [])


@bash_only
@pytest.mark.parametrize(
    ("mode", "warned", "unmanaged", "listing", "expected", "stdout"),
    [
        ("nothing-changed", "false", "false", "", [_LIST], _SUMMARY_SKIPPED),
        ("post", "false", "false", "", [_LIST, _post_of("comment.md")], ""),
        ("nothing-changed", "true", "false", "", [_LIST, _post_of("comment.md")], ""),
        ("nothing-changed", "false", "true", "", [_LIST, _post_of("comment.md")], ""),
        (
            "nothing-changed",
            "false",
            "false",
            _head(5, SUMMARY),
            [_LIST, _patch_of("comment.md")],
            "",
        ),
    ],
    ids=["quiet", "ordinary", "doctor-warned", "unmanaged", "quiet-existing"],
)
def test_summary_posts_unless_quiet_and_keeps_an_existing_comment_current(
    tmp_path, mode, warned, unmanaged, listing, expected, stdout
):
    """Nothing changed, no doctor warning and no unmanaged line: update only.

    Mutations: delete the `DOCTOR_WARNED` clause (doctor-warned red); delete the `UNMANAGED`
    clause (unmanaged red); delete the `nothing-changed` clause (ordinary red).
    """
    proc, calls = _run_summary(
        tmp_path, mode, warned=warned, unmanaged=unmanaged, listing=listing + "\n"
    )
    assert (proc.returncode, proc.stdout, calls) == (0, stdout, expected), proc.stderr


@bash_only
def test_summary_list_failure_warns_and_writes_nothing(tmp_path):
    """The gate is written by a later step, so the degrade keeps this step green.

    Mutation: misspell `list-failed` in the step.
    """
    proc, calls = _run_summary(tmp_path, "post", list_rc=1)
    assert (proc.returncode, proc.stdout, calls) == (0, _SUMMARY_LIST_FAILED, [_LIST])


@bash_only
def test_summary_write_failure_fails_the_step(tmp_path):
    """Mutation: add `|| true` to the `upsert-comment` call."""
    proc, calls = _run_summary(tmp_path, "post", write_rc=1)
    assert proc.returncode != 0
    assert calls == [_LIST, _post_of("comment.md")]


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
        "::warning::could not list this pull request's comments; the doctor report was not "
        "posted to the pull request this run: read it in this job's summary, or comment "
        "shipmate doctor again to retry\n",
        [_LIST],
    )


@bash_only
def test_doctor_write_failure_fails_the_step(tmp_path):
    """Mutation: add `|| true` to the `upsert-comment` call."""
    proc, calls = _run_doctor(tmp_path, write_rc=1)
    assert proc.returncode != 0
    assert calls == [_LIST, _post_of("doctor.md")]
