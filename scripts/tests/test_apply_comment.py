# scripts/tests/test_apply_comment.py
import importlib.util
import io
import json
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "apply-comment"
_loader = SourceFileLoader("apply_comment", str(_p))
_spec = importlib.util.spec_from_loader("apply_comment", _loader)
ac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ac)

RUN_URL = "https://gh/run/1"


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


# --- build_rows / expected-set merge ----------------------------------------


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
    assert missing["stack_display"] == "stacks/missing"  # no display name available


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


def test_build_table_statuses_emoji_and_not_attempted_note_present():
    rows = [
        _row(status="applied"),
        _row(status="failed", stack_display="db", environment="dev-us"),
        _row(
            status="blocked",
            stack_display="auth",
            environment="dev-eu",
            reason="state restore failed",
        ),
        _row(
            status="not_attempted",
            stack_display="stacks/x",
            environment="dev-eu",
            apply_text=None,
        ),
    ]
    table = ac.build_table(rows, [], RUN_URL)
    assert "| ✅ | app | dev-eu |" in table
    assert "| ❌ | db | dev-us |" in table
    assert "| 🚫 | auth | dev-eu |" in table
    assert "| ⏭️ | stacks/x | dev-eu |" in table
    comment = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")
    assert ac.NOT_ATTEMPTED_NOTE in comment


# --- blocked-reason rendering ------------------------------------------------


def test_blocked_cell_renders_reason_with_no_fence():
    rows = [
        _row(
            status="blocked",
            reason="reviewed plan artifact missing or expired — re-run plan",
            apply_text=None,
        )
    ]
    comment = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")
    assert "reviewed plan artifact missing or expired" in comment
    assert "🚫 **app / dev-eu**" in comment
    assert "```" not in comment


def test_blocked_reason_is_md_escaped():
    row = _row(status="blocked", reason="x</summary><b>evil", apply_text=None)
    line = ac._blocked_line(row, RUN_URL)
    assert "</summary>" not in line
    assert "&lt;/summary&gt;" in line


# --- degradation --------------------------------------------------------------


def test_render_apply_section_full_in_plain_fence():
    row = _row()
    s = ac.render_apply_section(row, "hello world", RUN_URL, 10_000)
    assert s.startswith("<details><summary>✅ app / dev-eu — applied</summary>")
    assert "```\nhello world\n```" in s
    assert "```diff" not in s
    assert s.endswith("</details>")


def test_render_apply_section_truncates_at_line_boundary_with_log_link():
    text = "\n".join(f"line {i}" for i in range(5_000))
    row = _row()
    s = ac.render_apply_section(row, text, RUN_URL, 3_000)
    assert len(s) <= 3_000
    assert "Truncated" in s and RUN_URL in s
    assert s.rstrip().endswith("</details>")


def test_render_apply_section_link_only_when_first_line_exceeds_room():
    text = "x" * 5_000  # no newline to cut at
    row = _row()
    s = ac.render_apply_section(row, text, RUN_URL, 3_000)
    assert "```" not in s
    assert RUN_URL in s
    assert "too large" in s


def test_render_apply_section_link_only_when_apply_text_missing():
    row = _row(apply_text=None)
    s = ac.render_apply_section(row, None, RUN_URL, 10_000)
    assert "```" not in s
    assert RUN_URL in s


def test_build_comment_reserves_link_only_space_for_every_cell():
    # An early giant apply.txt must not starve a later cell of its link.
    rows = [
        _row(stack_display="giant", stack_path="stacks/giant", apply_text="X" * 200_000),
    ]
    for i in range(20):
        rows.append(
            _row(
                stack_display=f"s{i:02}",
                stack_path=f"stacks/s{i:02}",
                apply_text="\n".join(f"line {j}" for j in range(200)),
            )
        )
    body = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")
    assert len(body) <= ac.sc.SIZE_BUDGET
    for i in range(20):
        assert f"s{i:02}" in body


# --- fence-escape attempt ------------------------------------------------------


def test_fence_escape_attempt_cannot_break_out_of_fence():
    evil = "````` " + "`" * 50 + "\nrm -rf /\n" + "`" * 50
    row = _row(apply_text=evil)
    s = ac.render_apply_section(row, evil, RUN_URL, 10_000)
    # The fence used must be strictly longer than any backtick run in the text.
    fence_len = len(ac.sc._fence_of(evil))
    assert "`" * (fence_len + 1) not in s.replace(evil, "")
    # The section still closes with </details> — no early close from the
    # injected backtick run.
    assert s.endswith("</details>")


# --- resources-line parsing ----------------------------------------------------


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
    # Author-controlled text cannot inject non-digit content into the captured
    # groups — a line missing real digits in those positions simply fails to
    # match rather than smuggling arbitrary text into the table cell.
    text = "Apply complete! Resources: <script>alert(1)</script> added, 0 changed, 0 destroyed."
    assert ac._resources(text) == ""


# --- validation, loud ----------------------------------------------------------


def test_load_cells_fails_loud_on_missing_schema_key(tmp_path):
    d = tmp_path / "apply-summary.dev-eu.stacks-app"
    d.mkdir()
    bad = _cell()
    del bad["reason"]
    (d / "cell.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit, match="reason"):
        ac.load_cells(str(tmp_path))


def test_load_cells_fails_loud_on_wrong_type(tmp_path):
    d = tmp_path / "apply-summary.dev-eu.stacks-app"
    d.mkdir()
    bad = _cell(reason=123)
    (d / "cell.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit, match="reason"):
        ac.load_cells(str(tmp_path))


def test_load_cells_fails_loud_on_out_of_enum_result(tmp_path):
    d = tmp_path / "apply-summary.dev-eu.stacks-app"
    d.mkdir()
    bad = _cell(result="cancelled")
    (d / "cell.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit, match="result"):
        ac.load_cells(str(tmp_path))


def test_load_cells_reads_apply_text_only_when_present(tmp_path):
    attempted = tmp_path / "apply-summary.dev-eu.stacks-app"
    attempted.mkdir()
    (attempted / "cell.json").write_text(json.dumps(_cell(result="applied")))
    (attempted / "apply.txt").write_text("output here")
    blocked = tmp_path / "apply-summary.dev-us.stacks-db"
    blocked.mkdir()
    (blocked / "cell.json").write_text(
        json.dumps(
            _cell(
                stack="db",
                stack_path="stacks/db",
                environment="dev-us",
                result="blocked",
                reason="x",
            )
        )
    )
    cells = ac.load_cells(str(tmp_path))
    texts = {c["stack_path"]: t for c, t in cells}
    assert texts["stacks/app"] == "output here"
    assert texts["stacks/db"] is None


# --- short-forms ----------------------------------------------------------------


def test_short_form_detect_failed_targeted():
    body = ac._short_form("success,failure", "dev-eu", "pending", RUN_URL)
    assert body.startswith(":x: shipmate: `shipmate apply dev-eu` failed.")
    assert "gate` stays pending" in body
    assert RUN_URL in body


def test_short_form_nothing_pending_all_environments():
    body = ac._short_form("success,skipped", "", "complete", RUN_URL)
    assert body.startswith(
        ":white_check_mark: shipmate: `shipmate apply` (all environments) "
        "found no pending applies to run."
    )
    assert "gate` is complete" in body
    assert RUN_URL in body


def test_build_comment_uses_short_form_when_no_rows_and_no_expected(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLS", str(tmp_path / "empty"))
    monkeypatch.setenv("SHIPMATE_ENVIRONMENT", "dev-eu")
    monkeypatch.setenv("SHIPMATE_WAVES_JSON", "")
    for i in range(4):
        monkeypatch.setenv(f"SHIPMATE_ENVLEVEL{i}_WAVES", "")
    monkeypatch.setenv("SHIPMATE_RESULTS", "success,skipped")
    monkeypatch.setenv("SHIPMATE_GATE", "complete")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    ac.main()
    body = (tmp_path / "comment.md").read_text(encoding="utf-8")
    assert body.startswith(
        ":white_check_mark: shipmate: `shipmate apply dev-eu` found no pending applies to run."
    )


# --- HARD_CAP overflow -----------------------------------------------------------


def test_build_comment_hard_cap_fallback_keeps_table_drops_details():
    rows = [
        _row(stack_display=f"s{i:03}", stack_path=f"stacks/s{i:03}", apply_text="x" * 500)
        for i in range(300)
    ]
    body = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")
    assert len(body) <= ac.sc.HARD_CAP
    assert "s299" in body  # table row always present
    assert "too large" in body


def test_build_comment_fails_loud_when_even_table_overflows():
    long_name = "s" * 400
    rows = [
        _row(
            stack_display=f"{long_name}{i:03}",
            stack_path=f"stacks/{long_name}{i:03}",
            apply_text="x",
        )
        for i in range(300)
    ]
    with pytest.raises(SystemExit, match="65,536-char comment cap"):
        ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")


# --- per-cell log link ------------------------------------------------------------


def test_job_url_suffix_match_against_caller_prefixed_job_name():
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("wave0 (matrix) / apply / dev-eu / stacks/app", "https://gh/job/1")]
    assert ac._job_url(row, jobs, RUN_URL) == "https://gh/job/1"


def test_job_url_falls_back_to_run_url_when_no_job_matches():
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("wave0 / apply / dev-us / stacks/db", "https://gh/job/1")]
    assert ac._job_url(row, jobs, RUN_URL) == RUN_URL


def test_job_url_does_not_false_match_on_bare_endswith():
    # A job named "...reapply / dev-eu / stacks/app" must NOT match the target
    # "apply / dev-eu / stacks/app" via a naive str.endswith — only a
    # `/`-boundary-respecting suffix counts.
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("reapply / dev-eu / stacks/app", "https://gh/job/should-not-match")]
    assert ac._job_url(row, jobs, RUN_URL) == RUN_URL


# --- footer -----------------------------------------------------------------------


def test_footer_excluded_and_skipped_only_for_all_environments_form():
    footer_env = ac._footer("pending", RUN_URL, ["prod"], ["staging"], "dev-eu")
    assert "Explicit environment" not in footer_env
    footer_all = ac._footer("pending", RUN_URL, ["prod"], ["staging"], "")
    assert (
        "Explicit environment(s) left pending: `prod` — run `shipmate apply prod` to apply them."
        in footer_all
    )
    assert "Skipped (ordered after an unapplied explicit environment): `staging`." in footer_all


# --- coupling guards ----------------------------------------------------------------

_ENGINE = pathlib.Path(__file__).resolve().parents[2]


def test_cell_schema_guard_apply_cell_writes_every_required_key():
    # Coupling: apply-cell (writer of cell.json) <-> apply-comment (reader).
    # The writer is inline python in the action; assert every key the reader
    # requires appears as a JSON key literal in the writer's source.
    src = (_ENGINE / "actions" / "apply-cell" / "action.yml").read_text(encoding="utf-8")
    missing = [k for k in ac.CELL_KEYS if f'"{k}"' not in src]
    assert missing == [], f"apply-cell action.yml no longer writes cell.json keys: {missing}"


def test_apply_summary_artifact_name_matches_contract():
    # apply-comment's load_cells globs `apply-summary.*` trees; drift here
    # would silently stop cells from being found (an empty comment, not a
    # loud failure), so pin the exact artifact-name grammar.
    src = (_ENGINE / "actions" / "apply-cell" / "action.yml").read_text(encoding="utf-8")
    assert "apply-summary.${{ inputs.env }}.${{ steps.ids.outputs.slug }}" in src
