# scripts/tests/test_apply_comment.py
import io
import json
import re

import pytest
from _loader import ENGINE as _ENGINE
from _loader import load_script

ac = load_script("apply-comment")
eo = load_script("env-order")
wv = load_script("waves")

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


def test_link_only_distinguishes_missing_output_from_output_too_large():
    # Two different reasons reach _link_only and they must not share a
    # sentence. Output that exists but does not fit is "too large"; output that
    # never arrived (a promoted not_attempted row, or a cell.json with no
    # apply.txt) is unavailable, and calling that "too large" is simply false.
    missing = ac._link_only(_row(apply_text=None), RUN_URL)
    assert "Apply output unavailable for this cell" in missing
    assert "too large" not in missing

    too_large = ac._link_only(_row(apply_text="x" * 100_000), RUN_URL)
    assert "Output too large for this comment" in too_large
    assert "unavailable" not in too_large


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
        jobs.append(_job(f"apply / stacks/s{i:02} / dev-eu", f"https://gh/job/s{i:02}"))
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


def test_build_comment_reserves_blocked_one_liners_up_front():
    # A run with many blocked cells (expired plan artifacts) plus a few large
    # attempted cells: pre-fix, only applied/failed link-onlys were counted
    # in the up-front reserve, so an early giant applied cell's section was
    # sized as if the blocked one-liners cost nothing -- they were appended
    # afterward, uncounted, and could push the body over HARD_CAP, at which
    # point the table-only fallback silently discarded every blocked reason
    # (the only place a 🚫 row's cause appears). Every blocked reason must
    # survive intact.
    reason = "reviewed plan artifact missing or expired -- re-run plan"
    blocked_rows = [
        _row(
            status="blocked",
            stack_display=f"b{i:03}",
            stack_path=f"stacks/b{i:03}",
            reason=reason,
            apply_text=None,
        )
        for i in range(200)
    ]
    giant_text = "\n".join("X" * 80 for _ in range(3_000))
    applied_rows = [
        _row(stack_display=f"a{i}", stack_path=f"stacks/a{i}", apply_text=giant_text)
        for i in range(3)
    ]
    rows = applied_rows + blocked_rows
    body = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")
    assert len(body) <= ac.sc.HARD_CAP
    # No fallback wipe: the hard-cap fallback's marker text must be absent,
    # and every one of the 200 blocked rows must carry its own copy of the
    # reason (a fallback would drop all of them at once; a starved-but-not-
    # quite-fallback scenario could drop only some).
    assert "use each row's log link" not in body
    assert body.count(reason) == 200


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
    # The backtick run lives near the START of the text; tail-oriented
    # degradation keeps the END and cuts the front away, but the fence
    # surrounding the KEPT (backtick-free) slice must still be sized against
    # the WHOLE original body (computed once, up front) -- not recomputed
    # against the backtick-free slice, which would only need the minimum
    # 3-backtick fence and would reopen the fence-escape hole the moment a
    # later, less-truncated render includes the backtick run again.
    lines = [f"line {i}" for i in range(3_000)]
    evil = "`" * 60 + "z\nbefore the backticks\n" + "\n".join(lines)
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


def test_read_tail_drops_partial_leading_line(tmp_path):
    # Every line kept in the tail must be a genuine, complete line from the
    # source -- proof that a mid-line seek point is dropped rather than
    # emitted as a fragment.
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
    assert "A" * 500 not in tail  # the head is gone


def test_read_tail_small_file_is_returned_whole_no_leading_drop(tmp_path):
    # A file smaller than the budget needs no truncation at all -- its first
    # line must survive intact (nothing to "drop" when nothing was cut).
    p = tmp_path / "apply.txt"
    p.write_text("first line\nsecond line\n", encoding="utf-8", newline="\n")
    tail = ac._read_tail(p, 10_000)
    assert tail == "first line\nsecond line\n"


def test_read_tail_tolerates_non_utf8_byte(tmp_path):
    # A single non-UTF-8 byte anywhere in the file must not raise
    # UnicodeDecodeError -- it must decode to a replacement character.
    p = tmp_path / "apply.txt"
    p.write_bytes(b"before\n\xff\nafter\n")
    tail = ac._read_tail(p, 10_000)  # must not raise
    assert "before" in tail
    assert "after" in tail


def test_load_cells_preserves_trailing_error_in_huge_failed_apply(tmp_path):
    # Finding scenario: a long failed apply whose fatal diagnostic is the
    # very last line. Head-first reading/truncation used to drop it entirely.
    d = tmp_path / "apply-summary.dev-eu.stacks-app"
    d.mkdir()
    (d / "cell.json").write_text(json.dumps(_cell(result="failed")))
    body = "\n".join("Still creating..." for _ in range(6_000))
    text = body + "\nError: Provider produced inconsistent result after apply\n"
    assert len(text) > 100_000
    (d / "apply.txt").write_text(text, encoding="utf-8", newline="\n")
    cells = ac.load_cells(str(tmp_path))
    _, loaded_text = cells[0]
    assert "Error:" in loaded_text
    row = _row(status="failed", apply_text=loaded_text)
    section = ac.render_apply_section(row, loaded_text, RUN_URL, 4_000)
    assert "Error:" in section


def test_load_cells_reads_resources_line_from_large_successful_apply(tmp_path):
    d = tmp_path / "apply-summary.dev-eu.stacks-app"
    d.mkdir()
    (d / "cell.json").write_text(json.dumps(_cell(result="applied")))
    noise = "\n".join(f"aws_instance.node[{i}]: Creation complete" for i in range(3_000))
    text = noise + "\nApply complete! Resources: 1200 added, 0 changed, 0 destroyed.\n"
    assert len(text) > 60_000
    (d / "apply.txt").write_text(text, encoding="utf-8", newline="\n")
    cells = ac.load_cells(str(tmp_path))
    _, loaded_text = cells[0]
    assert ac._resources(loaded_text) == "+1200 ~0 -0"


def test_load_cells_tolerates_non_utf8_byte_in_apply_txt(tmp_path):
    d = tmp_path / "apply-summary.dev-eu.stacks-app"
    d.mkdir()
    (d / "cell.json").write_text(json.dumps(_cell(result="applied")))
    (d / "apply.txt").write_bytes(
        b"Apply complete! Resources: 1 added, 0 changed, 0 destroyed.\n"
        b"trailer with a bad byte: \xff\n"
    )
    cells = ac.load_cells(str(tmp_path))  # must not raise UnicodeDecodeError
    _, loaded_text = cells[0]
    assert loaded_text is not None
    assert "Apply complete!" in loaded_text


def test_load_cells_strips_ansi_from_realistic_apply_output(tmp_path):
    # Finding scenario, reproduced verbatim: `tofu init`/`apply` stdout+stderr
    # teed raw (no -no-color) carries SGR colour codes that must not survive
    # into the rendered comment as literal garbage inside the fence.
    d = tmp_path / "apply-summary.dev-eu.stacks-auth"
    d.mkdir()
    (d / "cell.json").write_text(json.dumps(_cell(result="applied")))
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
    section = ac.render_apply_section(row, loaded_text, RUN_URL, 10_000)
    assert "\x1b" not in section


def test_load_cells_ansi_strip_happens_before_fence_is_computed(tmp_path):
    # A backtick run split by a colour escape (backtick, ESC[0m, backtick) is
    # two separate 1-backtick runs in the raw bytes, but becomes a single
    # 2-backtick run once the escape between them is stripped. If the fence
    # were computed on the UNstripped text (only 1-backtick runs -> a
    # 3-backtick fence), the stripped body's now-2-backtick run would still
    # fit safely under a 3-backtick fence -- so this alone proves too little.
    # Push it further: pad the raw text with backtick runs of length 3 (fence
    # would need to be 4 backticks against the raw view) positioned so that,
    # once stripped, they multiply out. Simplest robust proof: use 50
    # backticks either side of the escape sequence, so unstripped the longest
    # run is 50 (fence = 51) while stripped it is exactly 100 (fence must be
    # 101) -- if the fence were computed pre-strip (51 backticks) it would be
    # STRICTLY SHORTER than the post-strip 100-backtick run, i.e. the fence
    # would fail to be a real delimiter (the body would contain a run at
    # least as long as the fence itself).
    d = tmp_path / "apply-summary.dev-eu.stacks-auth"
    d.mkdir()
    (d / "cell.json").write_text(json.dumps(_cell(result="applied")))
    # The trailing "x" keeps the merged run's line from being a PURE-backtick
    # line itself (which would otherwise masquerade as a third "delimiter"
    # line to _fence_delimiter_lines, same caveat as the existing fence-escape
    # tests below).
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
    assert longest_run == 100  # the merge actually happened
    row = _row(status="applied", apply_text=loaded_text)
    section = ac.render_apply_section(row, loaded_text, RUN_URL, 10_000)
    fence_lines = _fence_delimiter_lines(section)
    assert len(fence_lines) == 2
    assert fence_lines[0] == fence_lines[1]
    assert len(fence_lines[0]) > longest_run


def test_resources_parses_colour_wrapped_apply_complete_line():
    # tofu commonly wraps the whole "Apply complete!" line in an SGR pair
    # (bold on ... bold off) -- an escape at either end of the line must not
    # defeat the line-anchored (^...$, re.MULTILINE) regex. _resources is
    # exercised directly on already-stripped text (as load_cells produces),
    # proving the anchor itself is robust once the colour codes are gone.
    text = ac._strip_ansi(
        "\x1b[1mApply complete! Resources: 3 added, 1 changed, 2 destroyed.\x1b[0m\n"
    )
    assert ac._resources(text) == "+3 ~1 -2"


def test_strip_ansi_covers_csi_two_char_and_osc_forms():
    # CSI (SGR), a bare two-character escape (ESC + byte in @-_), and an
    # OSC sequence terminated by BEL, then the same OSC form terminated by
    # ST (ESC \) instead -- all three forms named in the finding.
    csi = "before\x1b[36;1mcolour\x1b[0mafter"
    two_char = "before\x1bMreset-ish\x1bDafter"
    osc_bel = "before\x1b]0;window title\x07after"
    osc_st = "before\x1b]0;window title\x1b\\after"
    for sample in (csi, two_char, osc_bel, osc_st):
        stripped = ac._strip_ansi(sample)
        assert "\x1b" not in stripped
        assert "before" in stripped
        assert "after" in stripped


def test_strip_ansi_leaves_ordinary_text_and_newlines_and_carriage_returns_alone():
    text = "plain line one\nplain line two\r\nno escapes here at all"
    assert ac._strip_ansi(text) == text


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
    # Regression guard: today's live apply-all.yml one-liner appends these
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


def test_excluded_skipped_lines_escape_evil_env_names():
    # excluded/skipped env names are author-controlled (Terramate tags /
    # GitHub Environment names), same as every other display value in this
    # file -- these sentences must not be the one place that guarantee lapses.
    evil = "x</summary><b>evil"
    lines = ac._excluded_skipped_lines([evil], [evil])
    joined = " ".join(lines)
    assert "</summary><b>evil" not in joined
    assert "&lt;/summary&gt;&lt;b&gt;evil" in joined


def test_short_form_escapes_evil_excluded_and_skipped_env_names():
    evil = "x</summary><b>evil"
    body = ac._short_form("success,skipped", "", "complete", RUN_URL, [evil], [evil])
    assert "</summary><b>evil" not in body
    assert "&lt;/summary&gt;&lt;b&gt;evil" in body


def test_footer_escapes_evil_excluded_and_skipped_env_names():
    evil = "x</summary><b>evil"
    footer = ac._footer("pending", RUN_URL, [evil], [evil], "")
    assert "</summary><b>evil" not in footer
    assert "&lt;/summary&gt;&lt;b&gt;evil" in footer


def test_build_comment_uses_short_form_when_no_rows_and_no_expected(monkeypatch, tmp_path):
    monkeypatch.setenv("CELLS", str(tmp_path / "empty"))
    monkeypatch.setenv("SHIPMATE_ENVIRONMENT", "dev-eu")
    monkeypatch.setenv("SHIPMATE_WAVES_JSON", "")
    for i in range(ac.MAX_ENV_LEVELS):
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
    jobs = [_job("wave0 (matrix) / apply / stacks/app / dev-eu", "https://gh/job/1")]
    assert ac._job_url(row, jobs, RUN_URL) == "https://gh/job/1"


def test_job_url_falls_back_to_run_url_when_no_job_matches():
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("wave0 / apply / stacks/db / dev-us", "https://gh/job/1")]
    assert ac._job_url(row, jobs, RUN_URL) == RUN_URL


def test_job_url_does_not_false_match_on_bare_endswith():
    # A job named "...reapply / stacks/app / dev-eu" must NOT match the target
    # "apply / stacks/app / dev-eu" via a naive str.endswith — only a
    # `/`-boundary-respecting suffix counts.
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("reapply / stacks/app / dev-eu", "https://gh/job/should-not-match")]
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


# --- not-attempted note names the targeted env -------------------------------


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
    # Reproduced end to end: a targeted `shipmate apply prod` run with a
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


# --- job-level SHIPMATE_RESULTS failure surfaces on the table path too -------


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


def test_build_comment_surfaces_results_failure_with_no_cell_reports():
    # Reproduces an apply run that dies before any cell reports:
    # SHIPMATE_ENVIRONMENT set, a non-empty expected cell set, no artifacts
    # downloaded (the apply job died before any cell reported), SHIPMATE_RESULTS
    # carries a failure token. Pre-fix this rendered with no ❌ and no "failed"
    # anywhere.
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
    # Anti-divergence guard: the table path's failure line
    # and the short form must use ONE shared tokenizer, so a given
    # SHIPMATE_RESULTS string can never read as "failed" on one path and
    # "not failed" on the other.
    failed = ac._results_failed(results_csv)
    short = ac._short_form(results_csv, "", "pending", RUN_URL, [], [])
    assert short.startswith(":x:") == failed
    fail_line = ac._failure_line(results_csv, [], "")
    assert bool(fail_line) == failed


def test_results_failed_blank_token_counts_as_failure():
    # Restores the semantics of the shell tokenizer this replaced
    # (`tr ',' '\n' | grep -qvE '^(success|skipped)$'`): an empty line never
    # matches that alternation, so a blank segment reads as failure.
    # Filtering blank tokens out let an empty/blank result read as clean.
    assert ac._results_failed("") is True
    assert ac._results_failed("success,,skipped") is True  # embedded blank segment
    assert ac._results_failed("success,") is True  # trailing blank segment
    assert ac._results_failed("success,skipped") is False  # sanity: no blanks, no failure


def test_short_form_and_failure_line_agree_blank_token_is_failure():
    # Same anti-divergence property as the parametrized test above, exercised
    # specifically on the blank-token case the parametrization didn't cover.
    assert ac._short_form("", "", "pending", RUN_URL, [], []).startswith(":x:")
    assert ac._failure_line("", [], "") != ""


# --- coupling guards ----------------------------------------------------------------


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


# --- apply-check state: three-way lookup, absence is unknown -----------------

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


def test_check_state_maps_empty_app_id_warns_and_returns_no_data(capsys):
    # Must NOT fail loud the way from_app does: a missing SHIPMATE_APP_ID is
    # only allowed to cost this one display axis, never the whole comment.
    lines = _jsonl(_check("apply / stacks/app / dev-eu"))
    present, done = ac.check_state_maps(lines, "")
    assert (present, done) == (set(), set())
    assert "::warning::" in capsys.readouterr().out


def test_check_state_three_way_lookup():
    present = {"apply / stacks/app / dev-eu", "apply / stacks/db / dev-eu"}
    done = {"apply / stacks/app / dev-eu"}
    assert ac._check_state(_row(stack_path="stacks/app"), present, done) == ac.CHECK_DONE
    assert ac._check_state(_row(stack_path="stacks/db"), present, done) == ac.CHECK_PENDING
    assert ac._check_state(_row(stack_path="stacks/gone"), present, done) == ac.CHECK_UNKNOWN


def test_apply_check_state_applied_with_pending_check_becomes_unrecorded():
    # The finding: tofu apply succeeded, but Save state / the completion token
    # mint / Complete the apply check failed (or the job was cancelled) after
    # the cell summary was already composed and uploaded.
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
    assert rows[0]["apply_text"] is None  # no output to show; renders link-only


def test_apply_check_state_leaves_rows_alone_when_check_state_is_unknown():
    # Degradation contract: no data (scan failed, empty file, pin skew) must
    # render byte-identically to the artifact-only behaviour.
    rows = [
        _row(status="applied", stack_path="stacks/app"),
        _row(status="not_attempted", stack_path="stacks/db", apply_text=None),
    ]
    ac.apply_check_state(rows, set(), set())
    assert [r["status"] for r in rows] == ["applied", "not_attempted"]


def test_apply_check_state_never_downgrades_failed_or_blocked():
    # A red row against a green check means another run applied that cell:
    # over-reporting, nothing stranded, and the gate remains the truth.
    # Downgrading here would let an unrelated run's green check hide a real
    # failure in this one.
    done = {"apply / stacks/app / dev-eu", "apply / stacks/db / dev-eu"}
    rows = [
        _row(status="failed", stack_path="stacks/app"),
        _row(status="blocked", stack_path="stacks/db", reason="state restore failed"),
    ]
    ac.apply_check_state(rows, done, done)
    assert [r["status"] for r in rows] == ["failed", "blocked"]


def test_load_check_maps_missing_file_is_no_data(tmp_path):
    # The pinned-action skew window: an older apply-summary never writes
    # checks.jsonl. Silent no-data, not a crash.
    present, done = ac.load_check_maps(str(tmp_path / "nope.jsonl"), APP_ID)
    assert (present, done) == (set(), set())


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
    # a warning, exactly like the missing-file case, rather than propagate.
    p = tmp_path / "checks.jsonl"
    p.write_text("not json\n", encoding="utf-8")
    assert ac.load_check_maps(str(p), APP_ID) == (set(), set())
    assert "::warning::" in capsys.readouterr().out


def test_load_check_maps_missing_file_stays_silent(tmp_path, capsys):
    # Pins the deliberate asymmetry: the pinned-action skew window (an older
    # apply-summary that never writes checks.jsonl) is expected, not an error,
    # so it must NOT warn -- unlike a malformed or otherwise unreadable file.
    ac.load_check_maps(str(tmp_path / "nope.jsonl"), APP_ID)
    assert "::warning::" not in capsys.readouterr().out


def test_load_check_maps_non_numeric_app_id_degrades_with_a_warning(tmp_path, capsys):
    # A non-numeric SHIPMATE_APP_ID (e.g. the App's client id pasted in place
    # of its numeric app id) makes ag.from_app's int(app_id) raise ValueError.
    # That must cost only the check-state display axis, never the whole
    # render step -- same degradation as a malformed checks.jsonl.
    p = tmp_path / "checks.jsonl"
    p.write_text("\n".join(_jsonl(_check("apply / stacks/app / dev-eu"))), encoding="utf-8")
    assert ac.load_check_maps(str(p), "Iv1.notanumericid") == (set(), set())
    assert "::warning::" in capsys.readouterr().out


def test_unrecorded_has_its_own_emoji():
    assert ac._EMOJI["unrecorded"] == "⚠️"


def test_cell_json_result_enum_is_unchanged():
    # Display statuses are a superset of the artifact enum. The normative
    # cell.json grammar in CONTRACT.md must not drift because the comment grew
    # a display state.
    assert ac._RESULTS == frozenset({"applied", "failed", "blocked"})


# --- unrecorded rendering ----------------------------------------------------


def test_unrecorded_note_names_the_cell_and_the_recovery():
    rows = [_row(status="unrecorded", stack_display="db", environment="prod")]
    note = ac._unrecorded_note(rows)
    assert "**db / prod**" in note
    assert "applied but not recorded" in note
    # The cause is not "could not be completed" (implying only one possible
    # cause) -- a duplicate apply check re-created by a later plan run on the
    # same head SHA reads pending even though its OWN run completed, so the
    # note must name all three reachable causes.
    assert (
        "not recorded as complete (it failed, was cancelled, or a newer plan re-created it)" in note
    )
    assert "`shipmate / gate` stays pending" in note
    assert "Re-plan and re-apply" in note


def test_unrecorded_note_empty_when_no_unrecorded_row():
    assert ac._unrecorded_note([_row(status="applied"), _row(status="failed")]) == ""


def test_unrecorded_note_lists_every_affected_cell():
    rows = [
        _row(status="unrecorded", stack_display="db", environment="prod"),
        _row(status="unrecorded", stack_display="auth", environment="prod"),
    ]
    note = ac._unrecorded_note(rows)
    assert "**db / prod**" in note and "**auth / prod**" in note


def test_unrecorded_note_escapes_evil_stack_and_env_names():
    # stack_display and environment are author-controlled (a Terramate tag /
    # GitHub Environment name / apply-cell's stack-name input). Bold, not a
    # backtick code span: _md_escape does not escape a backtick, so a code
    # span would be the one place the escape could be broken out of.
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


def test_build_table_renders_unrecorded_with_the_warning_emoji():
    rows = [_row(status="unrecorded", stack_display="db", environment="prod")]
    table = ac.build_table(rows, [], RUN_URL)
    assert "| ⚠️ | db | prod |" in table


def test_build_table_unrecorded_still_shows_its_resources_count():
    # The apply genuinely ran and its output is real -- the resources column
    # must not go blank just because the check was never completed.
    rows = [_row(status="unrecorded", stack_display="db", environment="prod")]
    table = ac.build_table(rows, [], RUN_URL)
    assert "+1 ~0 -0" in table


def test_build_comment_unrecorded_keeps_its_details_section_and_carries_the_note():
    rows = [_row(status="unrecorded", stack_display="db", environment="prod")]
    comment = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "prod")
    # The section header carries the human phrase, never the internal enum
    # token -- "unrecorded" appears nowhere else in the comment, so leaking it
    # here would make the reader guess what it means.
    assert "<details><summary>⚠️ db / prod — applied but not recorded</summary>" in comment
    assert "— unrecorded</summary>" not in comment
    assert "```\nApply complete! Resources: 1 added, 0 changed, 0 destroyed.\n```" in comment
    assert ac._unrecorded_note(rows) in comment


def test_build_comment_no_unrecorded_note_when_nothing_is_unrecorded():
    rows = [_row(status="applied")]
    comment = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")
    assert "applied but not recorded" not in comment


def test_failure_line_present_even_when_an_unrecorded_row_already_explains_it():
    # A ⚠️ row plus its note explains ONE cell in ONE environment; it says
    # nothing about a different environment whose job died before any cell
    # reported. Suppressing the generic ❌ on `unrecorded` would let that ⚠️
    # silently swallow the only failure signal a dead-before-any-cell
    # environment ever gets (see the mixed-environments test below), so the
    # line must still render even in this single-row, same-environment case:
    # the redundancy beside the ⚠️ never loses a signal.
    rows = [_row(status="unrecorded", stack_display="db", environment="prod")]
    assert ac._failure_line("success,failure", rows, "prod") == (
        ":x: shipmate: `shipmate apply prod` failed."
    )


def test_failure_line_present_for_mixed_unrecorded_and_not_attempted_across_envs():
    # The finding: an all-environments run where env A reports one
    # `unrecorded` cell (its apply ran but the check never completed) while
    # env B's job died before any cell reported at all (a denied
    # `<env>-apply` environment, a job-level cancel) -- env B has only
    # `not_attempted` rows, never `failed`. Pre-fix, the `unrecorded` row in
    # env A suppressed the job-level failure line, leaving the whole bare-form
    # comment with a ⚠️, an ⏭️ note, and no ❌ anywhere despite the run
    # genuinely having failed.
    rows = [
        _row(status="unrecorded", stack_display="db", environment="dev-eu"),
        _row(
            status="not_attempted",
            stack_display="app",
            stack_path="stacks/app",
            environment="dev-us",
            apply_text=None,
        ),
    ]
    line = ac._failure_line("success,failure", rows, "")
    assert line == ":x: shipmate: `shipmate apply` (all environments) failed."


def test_build_comment_promoted_row_carries_no_stays_pending_note():
    # not_attempted + done check -> applied: the comment must stop telling the
    # reader to retry a cell that actually applied.
    rows = [_row(status="not_attempted", stack_path="stacks/app", apply_text=None)]
    ac.apply_check_state(rows, {"apply / stacks/app / dev-eu"}, {"apply / stacks/app / dev-eu"})
    comment = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")
    assert "| ✅ |" in comment
    assert ac._not_attempted_note("dev-eu") not in comment
    # A link-only section, no fence -- and it must NOT claim the output was too
    # large: nothing was ever captured for this cell, and the linked run may not
    # even be the run that applied it (a different run may have completed the
    # check, in which case _job_url falls back to this run's URL).
    assert "Apply output unavailable for this cell" in comment
    assert "too large" not in comment
    assert "```" not in comment


def test_build_comment_genuinely_pending_row_keeps_the_stays_pending_note():
    rows = [_row(status="not_attempted", stack_path="stacks/app", apply_text=None)]
    ac.apply_check_state(rows, {"apply / stacks/app / dev-eu"}, set())
    comment = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "dev-eu")
    assert "| ⏭️ |" in comment
    assert ac._not_attempted_note("dev-eu") in comment


def test_build_comment_unrecorded_note_precedes_the_not_attempted_note():
    # A stranded applied cell needs a re-plan; a not-attempted one just needs a
    # retry. The more urgent statement reads first.
    rows = [
        _row(status="unrecorded", stack_display="db", environment="prod"),
        _row(status="not_attempted", stack_display="stacks/x", environment="prod", apply_text=None),
    ]
    comment = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "prod")
    assert comment.index("applied but not recorded") < comment.index("not attempted —")


# --- review findings: note cap, shape degradation, coupling, end-to-end ------


def test_unrecorded_note_is_capped_and_summarizes_the_rest():
    # The note rides in `top`, the one string the HARD_CAP table-only fallback
    # re-emits verbatim, so an uncapped note could only push the render into
    # the fail-loud SystemExit -- costing the whole comment on exactly the run
    # that needed it, since the usual cause of `unrecorded` rows (an expired
    # App key, a checks-API outage) strands a whole wide matrix at once.
    rows = [
        _row(status="unrecorded", stack_display=f"s{i:03}", stack_path=f"stacks/s{i:03}")
        for i in range(60)
    ]
    note = ac._unrecorded_note(rows)
    assert "**s000 / dev-eu**" in note
    assert f"and {60 - ac._UNRECORDED_NAMED} more" in note
    assert "**s059 / dev-eu**" not in note  # beyond the cap, summarized instead
    assert len(note) < 1_000


def test_unrecorded_note_names_every_cell_when_under_the_cap():
    rows = [
        _row(status="unrecorded", stack_display=f"s{i}", stack_path=f"stacks/s{i}")
        for i in range(ac._UNRECORDED_NAMED)
    ]
    note = ac._unrecorded_note(rows)
    assert "more" not in note
    for i in range(ac._UNRECORDED_NAMED):
        assert f"**s{i} / dev-eu**" in note


def test_build_comment_wide_unrecorded_run_still_produces_a_comment():
    # End of the same finding: a whole matrix stranded at once, with long
    # author-controlled display names, must still render rather than raise.
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
    body = ac.build_comment(rows, [], RUN_URL, "pending", [], [], "prod")
    assert len(body) <= ac.sc.HARD_CAP
    assert "applied but not recorded" in body


def test_load_check_maps_malformed_shape_degrades_with_a_warning(tmp_path, capsys):
    # Valid JSON per line, but not the check-run shape the helpers expect. Two
    # sub-cases, and only the second one can raise:
    #
    #   - a scalar element inside .check_runs raises inside from_app's own
    #     `(r.get("app") or {})`;
    #   - a dict that survives from_app (its app id matches) but carries no
    #     usable `name` reaches latest_by_name's `run["name"].startswith(...)`.
    #
    # Either way it must cost the check-state axis only, never the comment.
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
    # A well-formed dict with no (or a foreign) `app` is not corruption: it is
    # from_app's deliberate fail-closed filter doing its job, so it degrades to
    # no data for that name WITHOUT a warning. Pinned so a future widening of
    # the except clause cannot start shouting about the normal filtered case.
    for payload in ({"status": "completed"}, {"name": "apply / stacks/app / dev-eu"}):
        p = tmp_path / "checks.jsonl"
        p.write_text(json.dumps(payload), encoding="utf-8")
        assert ac.load_check_maps(str(p), APP_ID) == (set(), set()), payload
        assert "::warning::" not in capsys.readouterr().out, payload


def test_check_name_grammar_matches_apply_cells_construction():
    # Coupling: apply-cell builds the apply check's NAME, apply-comment
    # forward-builds the same string to look that check up. A divergence is
    # silent by design -- every lookup would miss, _check_state would return
    # CHECK_UNKNOWN for every row, and the comment would quietly revert to the
    # artifact-only rendering this feature exists to correct. Same posture as
    # test_cell_schema_guard_apply_cell_writes_every_required_key above.
    src = (_ENGINE / "actions" / "apply-cell" / "action.yml").read_text(encoding="utf-8")
    expected = "f\"apply / {os.environ['STACK']} / {os.environ['ENV']}\""
    assert expected in src, (
        "apply-cell no longer builds the apply check name as "
        "'apply / <stack path> / <env>' -- scripts/apply-comment's _check_state "
        "and _job_url forward-build that exact grammar to look the check up, "
        "and a mismatch makes every lookup miss silently"
    )
    # And the reader's half, exercised rather than restated: the name
    # _check_state builds for a known row must be that same string.
    row = _row(environment="dev-eu", stack_path="stacks/app")
    assert ac._check_state(row, {"apply / stacks/app / dev-eu"}, set()) == ac.CHECK_PENDING


def test_env_level_count_matches_env_orders():
    # apply-comment keeps its own copy of the constant (see the comment there),
    # so the equality is pinned here rather than by construction.
    assert ac.MAX_ENV_LEVELS == eo.MAX_ENV_LEVELS


def test_wave_job_name_matches_the_apply_check_grammar():
    # Coupling: _job_url resolves a row's per-cell log link by matching the
    # apply check name as a `/ `-boundary suffix of the run's JOB names, which
    # only works because every apply-env-level wave job's `name:` is byte-
    # identical to the check name apply-cell/pending-checks build. Nothing else
    # enforces that; a rename of either side would silently downgrade every
    # link in the apply result comment to the workflow-run URL, which is also
    # the documented degradation, so no test would fail on the observable.
    src = (_ENGINE / ".github" / "workflows" / "apply-env-level.yml").read_text(encoding="utf-8")
    names = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("name: apply / ")]
    expected = "name: apply / ${{ matrix.stack }} / ${{ matrix.environment }}"
    # Width from waves.MAX_WAVES so a bump cannot leave this asserting the old
    # count.
    max_waves = wv.MAX_WAVES
    assert names == [expected] * max_waves, (
        f"all {max_waves} wave job display names must stay byte-identical to the "
        "'apply / <stack path> / <env>' check-name grammar -- scripts/apply-comment's "
        f"_job_url matches it as a job-name suffix (got: {sorted(set(names))})"
    )
    # Pinning the two name literals is not enough: they only agree if the wave
    # job hands apply-cell the SAME matrix keys it renders itself from. The job
    # already feeds two inputs off one key (`stack:` and `stack-name:`), so a
    # later change that routed a display name through `matrix.stack` would keep
    # both literals intact while the rendered name stopped equalling the check
    # name apply-cell builds from these inputs.
    # `env:` alone is the job-level env mapping, not the apply-cell input.
    wired = [
        ln.strip()
        for ln in src.splitlines()
        if ln.strip().startswith(("stack: ", "env: ")) and ln.strip() != "env:"
    ]
    assert wired == ["stack: ${{ matrix.stack }}", "env: ${{ matrix.environment }}"] * max_waves, (
        "every wave job must pass apply-cell the same matrix keys its display name "
        "renders (`stack: ${{ matrix.stack }}`, `env: ${{ matrix.environment }}`) -- "
        "apply-cell builds the check name from those two inputs, so a different "
        f"source for either silently breaks the name/job-name equality (got: {sorted(set(wired))})"
    )
    # The reader's half, exercised: a nested-display job name built from that
    # same grammar must resolve, for the same row _check_state agrees on.
    row = _row(environment="dev-eu", stack_path="stacks/app")
    jobs = [_job("post-merge / L0 / apply / stacks/app / dev-eu", "https://gh/job/1")]
    assert ac._job_url(row, jobs, RUN_URL) == "https://gh/job/1"


def _write_cell(cells_dir, env, slug, cell):
    d = cells_dir / f"apply-summary.{env}.{slug}"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    return d


def _main_env(monkeypatch, tmp_path, cells_dir, waves_json, checks_path):
    monkeypatch.setenv("CELLS", str(cells_dir))
    monkeypatch.setenv("SHIPMATE_ENVIRONMENT", "dev-eu")
    monkeypatch.setenv("SHIPMATE_WAVES_JSON", waves_json)
    for i in range(ac.MAX_ENV_LEVELS):
        monkeypatch.setenv(f"SHIPMATE_ENVLEVEL{i}_WAVES", "")
    monkeypatch.setenv("SHIPMATE_RESULTS", "success")
    monkeypatch.setenv("SHIPMATE_GATE", "pending")
    monkeypatch.setenv("SHIPMATE_CHECKS", checks_path)
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))


def test_main_folds_checks_jsonl_into_the_rendered_comment(monkeypatch, tmp_path):
    # The seam the action actually depends on, end to end: the file the scan
    # step writes must be read, filtered to the App, and folded into
    # comment.md. Every other check-state test calls the helpers directly, so
    # without this a refactor that dropped apply_check_state's result (or moved
    # the call below the `if not rows` branch) would ship a comment with no
    # promotions and no unrecorded rows while the whole suite stayed green --
    # and GitHub Actions cannot be run locally to catch it.
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
    ac.main()
    body = (tmp_path / "comment.md").read_text(encoding="utf-8")
    assert "| ⚠️ | app | dev-eu |" in body
    assert "applied but not recorded" in body


def test_main_promotes_a_missing_artifact_whose_check_is_done(monkeypatch, tmp_path):
    # The mirror direction through main(): no artifact at all, check done.
    cells = tmp_path / "cells"
    cells.mkdir()
    checks = tmp_path / "checks.jsonl"
    checks.write_text("\n".join(_jsonl(_check("apply / stacks/app / dev-eu"))), encoding="utf-8")
    waves = json.dumps({"wave0": [{"stack": "stacks/app", "environment": "dev-eu"}]})
    _main_env(monkeypatch, tmp_path, cells, waves, str(checks))
    ac.main()
    body = (tmp_path / "comment.md").read_text(encoding="utf-8")
    assert "| ✅ | stacks/app | dev-eu |" in body
    assert ac._not_attempted_note("dev-eu") not in body


def test_main_without_checks_file_renders_the_artifact_only_comment(monkeypatch, tmp_path):
    # The pinned-action skew window and every scan failure land here: no
    # checks.jsonl means no data means unknown, and the comment must read
    # exactly as it did before this feature existed.
    cells = tmp_path / "cells"
    _write_cell(cells, "dev-eu", "stacks-app", _cell(stack="app", stack_path="stacks/app"))
    waves = json.dumps({"wave0": [{"stack": "stacks/app", "environment": "dev-eu"}]})
    _main_env(monkeypatch, tmp_path, cells, waves, str(tmp_path / "absent.jsonl"))
    ac.main()
    body = (tmp_path / "comment.md").read_text(encoding="utf-8")
    assert "| ✅ | app | dev-eu |" in body
    assert "applied but not recorded" not in body
