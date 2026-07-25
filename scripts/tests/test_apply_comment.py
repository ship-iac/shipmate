# scripts/tests/test_apply_comment.py
import importlib.util
import io
import json
import pathlib
import re
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
    assert ac._not_attempted_note("dev-eu") in comment


def test_build_table_escapes_evil_stack_display_name():
    # stack_display is author-controlled (apply-cell's stack-name input); a
    # value like `x</summary><b>evil` must not survive as live HTML in the
    # table cell.
    rows = [_row(stack_display="x</summary><b>evil", environment="dev-eu")]
    table = ac.build_table(rows, [], RUN_URL)
    assert "</summary>" not in table
    assert "<b>" not in table
    assert "&lt;/summary&gt;&lt;b&gt;evil" in table


def test_summary_line_escapes_evil_stack_display_name():
    # Same author-controlled value, but in the <summary> a details section is
    # built from -- an unescaped `</summary>` here would close the tag early.
    row = _row(stack_display="x</summary><b>evil", environment="dev-eu")
    line = ac._summary_line(row)
    assert "</summary><b>evil" not in line
    assert "&lt;/summary&gt;&lt;b&gt;evil" in line
    assert line.endswith("</summary>")  # the tag itself is still the real one


def test_build_table_and_summary_escape_markdown_link_syntax_in_stack_name():
    rows = [_row(stack_display="[x](https://evil)", environment="dev-eu")]
    table = ac.build_table(rows, [], RUN_URL)
    assert "&#91;x&#93;(https://evil)" in table
    assert "[x](https://evil)" not in table
    line = ac._summary_line(rows[0])
    assert "&#91;x&#93;(https://evil)" in line
    assert "[x](https://evil)" not in line


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
    # An early giant apply.txt must not starve a later cell of its link. The
    # giant body MUST contain newlines: a single unbroken line (no "\n")
    # degrades straight to link-only (tiny, ~200 chars) regardless of
    # whether the reserve logic exists at all, so it can never actually
    # exercise -- or disprove -- the reserve (verified: with the reserve
    # deleted entirely, this exact fixture with a no-newline giant still
    # yields a body under SIZE_BUDGET with every section present -- see the
    # fix report's probe output).
    giant_text = "\n".join("X" * 80 for _ in range(3_000))
    rows = [
        _row(stack_display="giant", stack_path="stacks/giant", apply_text=giant_text),
    ]
    jobs = []
    for i in range(20):
        rows.append(
            _row(
                stack_display=f"s{i:02}",
                stack_path=f"stacks/s{i:02}",
                apply_text="\n".join(f"line {j}" for j in range(200)),
            )
        )
        # Distinct, zero-padded (no prefix collisions between e.g. job/s01 and
        # job/s10) per-cell job URLs, so "the URL appears" can only be
        # satisfied by that cell's OWN section, not a neighbor's.
        jobs.append(_job(f"apply / dev-eu / stacks/s{i:02}", f"https://gh/job/s{i:02}"))
    body = ac.build_comment(rows, jobs, RUN_URL, "pending", [], [], "dev-eu")
    assert len(body) <= ac.sc.SIZE_BUDGET
    for i in range(20):
        url = f"https://gh/job/s{i:02}"
        # Each later cell actually got its own <details> section (not merely
        # a table-row mention, which is present regardless of the reserve
        # logic) -- proves the up-front reserve left it room for at least a
        # section, rather than the giant early cell starving it.
        assert f"<summary>✅ s{i:02} / dev-eu — applied</summary>" in body
        # ...AND that section itself carries the cell's own log link (not
        # just the table row's mention of it) -- the whole point of the
        # reserve. Under this budget every one of these degrades to
        # link-only/truncated, both of which cite the job URL, so a genuine
        # per-cell section produces >=2 occurrences (table + section); a
        # cell that lost its section entirely would have only the table's 1.
        assert body.count(url) >= 2


# --- fence-escape attempt ------------------------------------------------------


def _fence_delimiter_lines(rendered):
    """All-backtick lines actually emitted in `rendered` -- the real fence
    delimiters, read from the output itself rather than re-derived from the
    helper the renderer is supposed to have called. A renderer that hardcodes
    a 3-backtick fence (ignoring the computed length entirely) must fail an
    assertion built from this, not just an assertion built from the helper."""
    return [ln for ln in rendered.splitlines() if ln and set(ln) == {"`"}]


def test_fence_escape_attempt_cannot_break_out_of_fence():
    # Trailing non-backtick chars ("x"/"y") keep every body LINE from being
    # pure backticks itself (which would otherwise masquerade as a third
    # "delimiter" line to _fence_delimiter_lines) while leaving the longest
    # contiguous backtick RUN at 50 either way.
    evil = "````` " + "`" * 50 + "x\nrm -rf /\n" + "`" * 50 + "y"
    row = _row(apply_text=evil)
    s = ac.render_apply_section(row, evil, RUN_URL, 10_000)
    longest_run_in_evil = max(len(m) for m in re.findall(r"`+", evil))
    fence_lines = _fence_delimiter_lines(s)
    assert len(fence_lines) == 2  # opening + closing delimiter
    assert fence_lines[0] == fence_lines[1]
    # The delimiter actually emitted must be strictly longer than the longest
    # backtick run in the body -- checked against the RENDERED fence line,
    # not merely against a length computed off to the side.
    assert len(fence_lines[0]) > longest_run_in_evil
    # The section still closes with </details> — no early close from the
    # injected backtick run.
    assert s.endswith("</details>")


def test_fence_escape_attempt_truncated_path_reuses_full_bodys_fence():
    # The backtick run lives near the END of the text; degradation must cut
    # it away, but the fence surrounding the KEPT (backtick-free) slice must
    # still be sized against the WHOLE original body (computed once, up
    # front) -- not recomputed against the backtick-free slice, which would
    # only need the minimum 3-backtick fence and would reopen the
    # fence-escape hole the moment a later, less-truncated render includes
    # the backtick run again.
    lines = [f"line {i}" for i in range(3_000)]
    evil = "\n".join(lines) + "\n" + "`" * 60 + "z\nafter the backticks"
    longest_run_in_evil = max(len(m) for m in re.findall(r"`+", evil))
    row = _row(apply_text=evil)
    s = ac.render_apply_section(row, evil, RUN_URL, 2_000)
    assert "Truncated" in s
    fence_lines = _fence_delimiter_lines(s)
    assert len(fence_lines) == 2
    assert fence_lines[0] == fence_lines[1]
    assert len(fence_lines[0]) > longest_run_in_evil
    # The 60-backtick run itself was truncated away -- the kept body never
    # reaches it (proving this is a real cut, not a coincidence).
    assert "`" * 60 not in s.replace(fence_lines[0], "")


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


def test_resources_ignores_embedded_lookalike_in_later_output_line():
    # OpenTofu prints the `Outputs:` block AFTER the "Apply complete!" line;
    # an output value that happens to CONTAIN the literal string (embedded
    # mid-line, not starting the line) must not be mistaken for -- and, being
    # textually last, must not override -- the real line. Anchoring
    # (^...$, re.M) is what defeats this: an unanchored regex would match the
    # embedded copy too and, being last, would incorrectly win.
    text = (
        "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.\n"
        "\n"
        "Outputs:\n"
        'fake = "Apply complete! Resources: 99 added, 99 changed, 99 destroyed."\n'
    )
    assert ac._resources(text) == "+1 ~0 -0"


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
    body = ac._short_form("success,failure", "dev-eu", "pending", RUN_URL, [], [])
    assert body.startswith(":x: shipmate: `shipmate apply dev-eu` failed.")
    assert "gate` stays pending" in body
    assert RUN_URL in body


def test_short_form_nothing_pending_all_environments():
    body = ac._short_form("success,skipped", "", "complete", RUN_URL, [], [])
    assert body.startswith(
        ":white_check_mark: shipmate: `shipmate apply` (all environments) "
        "found no pending applies to run."
    )
    assert "gate` is complete" in body
    assert RUN_URL in body


def test_short_form_includes_excluded_and_skipped_lines_all_environments():
    # I3 regression: today's live apply-all.yml one-liner appends these
    # sentences unconditionally, including in the nothing-pending branch --
    # this is the actionable case where the ONLY reason nothing is pending is
    # an excluded explicit env (apply-all-detect derives `excluded` from
    # pending cells' envs and removes them from `runnable`, so an
    # explicit-only-pending repo is simultaneously all-levels-empty AND
    # carries a non-empty excluded_envs). Dropping the sentence here would be
    # actively false: it would claim nothing is pending when work is.
    body = ac._short_form("success,skipped", "", "complete", RUN_URL, ["prod"], ["staging"])
    assert (
        "Explicit environment(s) left pending: `prod` — run `shipmate apply prod` to apply them."
        in body
    )
    assert "Skipped (ordered after an unapplied explicit environment): `staging`." in body
    assert "gate` is complete" in body
    assert RUN_URL in body


def test_short_form_omits_excluded_skipped_for_targeted_env():
    # The targeted (single-env) form never carries excluded/skipped envs --
    # that's an apply-all-only concept.
    body = ac._short_form("success,skipped", "dev-eu", "complete", RUN_URL, ["prod"], ["staging"])
    assert "Explicit environment" not in body
    assert "Skipped" not in body


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
    # 400 short-named rows: enough that even every cell degraded to link-only
    # still overflows HARD_CAP (verified: 300 rows stays just under the cap
    # without needing the fallback at all -- 400 is the margin that actually
    # exercises it), while the table alone (short 4-char stack names) stays
    # comfortably within it.
    rows = [
        _row(stack_display=f"s{i:03}", stack_path=f"stacks/s{i:03}", apply_text="x" * 500)
        for i in range(400)
    ]
    body = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")
    assert len(body) <= ac.sc.HARD_CAP
    assert "s399" in body  # table row always present
    # The table-only fallback's specific wording, not merely "too large" --
    # _link_only's per-cell fallback text ("Output too large for this
    # comment") ALSO contains "too large", so that check alone can't tell the
    # two paths apart. No <details> section may survive this fallback.
    assert "use each row's log link" in body
    assert "<details>" not in body


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


# --- I2: not-attempted note names the targeted env -------------------------------


def test_not_attempted_note_targeted_form_names_the_env():
    note = ac._not_attempted_note("prod")
    assert note == (
        "_⏭️ not attempted — the apply check stays pending; retry with `shipmate apply prod`._"
    )


def test_not_attempted_note_bare_form_stays_bare():
    note = ac._not_attempted_note("")
    assert note == (
        "_⏭️ not attempted — the apply check stays pending; retry with `shipmate apply`._"
    )


def test_not_attempted_note_escapes_evil_env_name():
    note = ac._not_attempted_note("x</summary><b>evil")
    assert "x</summary><b>evil" not in note
    assert "shipmate apply x&lt;/summary&gt;&lt;b&gt;evil" in note


def test_build_comment_not_attempted_note_in_targeted_run_names_the_env():
    # I2 reproduced end to end: a targeted `shipmate apply prod` run with a
    # not-attempted cell must not tell the reader to retry with the bare
    # form, which cannot retry an explicit env.
    rows = [_row(status="not_attempted", environment="prod", apply_text=None)]
    comment = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "prod")
    assert "retry with `shipmate apply prod`" in comment
    assert "retry with `shipmate apply`._" not in comment


def test_build_comment_not_attempted_note_in_bare_run_stays_bare():
    rows = [_row(status="not_attempted", environment="dev-eu", apply_text=None)]
    comment = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "")
    assert "retry with `shipmate apply`._" in comment


# --- I1: job-level SHIPMATE_RESULTS failure surfaces on the table path too -------


def test_failure_line_present_when_results_failed_and_no_row_shows_it():
    rows = [
        _row(status="not_attempted", stack_display="app", apply_text=None),
        _row(
            status="not_attempted",
            stack_display="db",
            stack_path="stacks/db",
            apply_text=None,
        ),
    ]
    line = ac._failure_line("success,failure", rows, "prod")
    assert line == ":x: shipmate: `shipmate apply prod` failed."


def test_failure_line_absent_when_results_clean():
    rows = [_row(status="not_attempted", apply_text=None)]
    assert ac._failure_line("success,skipped", rows, "prod") == ""


def test_failure_line_absent_when_a_row_already_shows_failed_or_blocked():
    # No double-signaling: a run with a real ❌/🚫 row already carries the
    # failure in the table itself.
    failed_rows = [_row(status="failed")]
    assert ac._failure_line("success,failure", failed_rows, "prod") == ""
    blocked_rows = [_row(status="blocked", reason="x", apply_text=None)]
    assert ac._failure_line("success,failure", blocked_rows, "prod") == ""


def test_build_comment_reproduces_i1_red_run_with_no_cell_reports():
    # Reproduces the review's failure scenario verbatim: SHIPMATE_ENVIRONMENT
    # set, a non-empty expected cell set, no artifacts downloaded (the apply
    # job died before any cell reported), SHIPMATE_RESULTS carries a failure
    # token. Pre-fix this rendered with no ❌ and no "failed" anywhere.
    rows = ac.build_rows({("prod", "stacks/app"), ("prod", "stacks/db")}, [])
    body = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "prod", "success,failure")
    assert ":x: shipmate: `shipmate apply prod` failed." in body
    assert all(r["status"] == "not_attempted" for r in rows)  # the scenario's own precondition


def test_build_comment_no_failure_line_when_results_clean_and_nothing_attempted():
    rows = ac.build_rows({("prod", "stacks/app")}, [])
    body = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "prod", "success,skipped")
    assert ":x: shipmate:" not in body


def test_build_comment_failure_line_sits_between_header_and_table():
    rows = [_row(status="not_attempted", apply_text=None)]
    body = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "prod", "success,failure")
    header_idx = body.index("### shipmate apply prod")
    fail_idx = body.index(":x: shipmate: `shipmate apply prod` failed.")
    table_idx = body.index("| | stack | env | resources | logs |")
    assert header_idx < fail_idx < table_idx


@pytest.mark.parametrize(
    "results_csv", ["", "success", "success,skipped", "success,failure", "failure", "cancelled"]
)
def test_results_failed_tokenizer_shared_by_short_form_and_failure_line(results_csv):
    # Anti-divergence guard (ledger item T3d): the table path's failure line
    # and the short form must use ONE shared tokenizer, so a given
    # SHIPMATE_RESULTS string can never read as "failed" on one path and
    # "not failed" on the other.
    failed = ac._results_failed(results_csv)
    short = ac._short_form(results_csv, "", "pending", RUN_URL, [], [])
    assert short.startswith(":x:") == failed
    fail_line = ac._failure_line(results_csv, [], "")
    assert bool(fail_line) == failed


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
