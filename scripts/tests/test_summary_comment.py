import io
import json
import os
import pathlib

import pytest
from _loader import ENGINE as _ENGINE
from _loader import action_steps, load_script, run_lines, step_by

sc = load_script("summary-comment")


@pytest.fixture(autouse=True)
def _run_context(monkeypatch):
    """The runner defaults `provenance` reads for the verdict's commit and run links."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://gh")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "7")


_COUNT_KEYS = ("add", "change", "destroy", "import", "forget")


def _bare_app(url):
    return f'🟡 stacks/app (dev-eu): +1 ~0 -0 <a href="{url}">plan</a>'


def _cell(**kw):
    base = {
        "stack": "stacks/app",
        "environment": "dev-eu",
        "add": 1,
        "change": 0,
        "destroy": 0,
        "import": 0,
        "forget": 0,
        "changed": True,
    }
    base.update(kw)
    return base


def test_diff_map_moves_signs_to_column_zero_preserving_indent():
    text = (
        '  + resource "null_resource" "a" {\n'
        "      + id = (known after apply)\n"
        "  - resource removed\n"
        "  ~ resource updated in-place"
    )
    assert sc.diff_map(text) == (
        '+   resource "null_resource" "a" {\n'
        "+       id = (known after apply)\n"
        "-   resource removed\n"
        "!   resource updated in-place"
    )


def test_diff_map_handles_replace_sign_and_leaves_plain_lines():
    text = "  -/+ resource replaced\n  # null_resource.a will be created\nplain"
    out = sc.diff_map(text).splitlines()
    assert out[0].startswith("-/+")
    assert out[1] == "  # null_resource.a will be created"
    assert out[2] == "plain"


def test_diff_map_does_not_touch_interior_tildes():
    assert sc.diff_map("value = ~/.config") == "value = ~/.config"


def test_diff_map_is_heredoc_aware_leaves_body_lines_untouched():
    # A heredoc's literal body, cloud-init YAML for instance, can itself start with `-` or
    # `~`. Those are content, not plan diff markers, and must survive byte-identical. The
    # opener line is still a real change line and maps.
    text = (
        "  + user_data = <<-EOT\n"
        "        - name: install\n"
        "        ~ literal tilde line\n"
        "    EOT\n"
        '  + resource "x" "y" {'
    )
    out = sc.diff_map(text).splitlines()
    assert out[0] == "+   user_data = <<-EOT"
    assert out[1] == "        - name: install"
    assert out[2] == "        ~ literal tilde line"
    assert out[3] == "    EOT"
    assert out[4] == '+   resource "x" "y" {'


def test_diff_map_resumes_sign_mapping_after_heredoc_terminator():
    text = "  + user_data = <<EOT\n    body\n    EOT\n  ~ real change"
    out = sc.diff_map(text).splitlines()
    assert out[-1] == "!   real change"


def test_fence_grows_past_backtick_runs_in_plan_text():
    text = "x = ```code```"
    fenced = sc.fence(text, sc._fence_of(text))
    assert fenced.startswith("````diff\n")
    assert fenced.endswith("\n````")


def test_fence_minimum_three_backticks():
    assert sc.fence("no ticks", sc._fence_of("no ticks")).startswith("```diff\n")


def test_fence_lang_param_plain_vs_default_diff():
    assert sc.fence("x", sc._fence_of("x")) == "```diff\nx\n```"
    assert sc.fence("x", sc._fence_of("x"), lang="") == "```\nx\n```"


def test_emoji_verdicts():
    assert sc.emoji(_cell(changed=False, add=0)) == "🟢"
    # A destroy count also covers replacements, so it must not render red.
    assert sc.emoji(_cell(destroy=2)) == "🟡"
    assert sc.emoji(_cell()) == "🟡"


def test_md_escape_keeps_pipes_and_neutralizes_newlines():
    """Mutation: escape `|` as `&#124;` -- red. Mutation: drop the `\\n` replace -- red."""
    assert sc._md_escape("a|b\nc") == "a|b c"


def test_md_escape_neutralizes_angle_brackets():
    assert sc._md_escape("x</summary><b>") == "x&lt;/summary&gt;&lt;b&gt;"


def test_md_escape_neutralizes_markdown_link_syntax():
    assert sc._md_escape("[x](https://e)") == "&#91;x&#93;(https://e)"


def test_md_escape_neutralizes_an_entity_a_name_spells():
    """`&` is escaped before the other replacements, and only where it starts an entity.

    Mutations: drop the `&` escape -- `&#91;` reaches the comment and renders `[`; move it last
    -- the `&#91;` the `[` escape wrote becomes `&amp;#91;`; escape every `&` -- `&&` is lost.
    """
    assert sc._md_escape("&#91;x&lt;&amp;[a && b]") == (
        "&amp;#91;x&amp;lt;&amp;amp;&#91;a && b&#93;"
    )


CHECKS = {
    "shipmate / stacks/app / dev-eu": {"html_url": "https://ck/app-eu"},
    "shipmate / stacks/db / dev-us": {"html_url": "https://ck/db-us"},
}
RUN_URL = "https://gh/run/1"


def test_plan_check_prefix_is_the_shim_job_name():
    """The shim's calling job name is a contract literal, restated here rather than imported.

    Reddens on any edit to `PLAN_CHECK_PREFIX` -- the whole value is compared against a
    hand-written constant.
    """
    assert sc.PLAN_CHECK_PREFIX == "shipmate / "


def test_check_url_resolves_by_env_and_stack_path_with_run_url_fallback():
    """Reddens on `PLAN_CHECK_PREFIX = ""` or `"shipmate/"`: the prefixed name misses."""
    assert sc.check_url(_cell(), CHECKS, RUN_URL) == "https://ck/app-eu"
    assert sc.check_url(_cell(environment="prod"), CHECKS, RUN_URL) == RUN_URL


def test_check_url_ignores_an_unprefixed_check_and_falls_back_to_the_run_url():
    """A shim job named anything but `shipmate` degrades every plan link to the run URL.

    The old two-segment name is not a match, so this reddens on `PLAN_CHECK_PREFIX = ""`
    for the reason the constant exists, not on a plain absent-name miss.
    """
    cell = _cell(stack="a", environment="dev")
    assert sc.check_url(cell, {"shipmate / a / dev": {"html_url": "https://ck/a"}}, RUN_URL) == (
        "https://ck/a"
    )
    assert sc.check_url(cell, {"a / dev": {"html_url": "https://ck/a"}}, RUN_URL) == RUN_URL


SHA = "0123456789abcdef0123456789abcdef01234567"
HINT = "Comment `shipmate help` for the available commands."
FOOT = f"[run](https://gh/run/1). {HINT}"
#: `provenance(SHA)` under `_run_context`, hand-written.
AT = (
    "at [0123456](https://gh/o/r/commit/0123456789abcdef0123456789abcdef01234567) "
    "in [run #7](https://gh/run/1)"
)
#: `provenance(SHA, current_run_url())` under `_run_context`, which is what `main` renders.
MAIN_AT = (
    "at [0123456](https://gh/o/r/commit/0123456789abcdef0123456789abcdef01234567) "
    "in [run #7](https://gh/o/r/actions/runs/1)"
)
HEAD = "<!-- shipmate:summary -->\n### shipmate plan\n\n"


def test_worst_orders_circles_worst_first():
    """Mutation: reversing `CIRCLES` reddens every case."""
    assert sc.CIRCLES == ("🔴", "🟠", "🟡", "⚪", "🟢")
    assert sc.worst(["🟢", "🟡", "⚪"]) == "🟡"
    assert sc.worst(["🟢", "🔴", "🟠"]) == "🔴"
    assert sc.worst([]) == "🟢"


def test_link_attribute_escapes_the_url():
    """Mutation: dropping `quote=True` leaves the `"` raw, closing the attribute."""
    assert sc.link('https://x/?a="b"&c=1', "plan") == (
        '<a href="https://x/?a=&quot;b&quot;&amp;c=1">plan</a>'
    )


def test_cell_line_without_a_url_ends_at_the_state():
    """Mutation: emitting `link(url or "", text)` unconditionally appends `<a href="">`."""
    assert sc.cell_line("🟢", "a", "dev", "no changes") == "🟢 a (dev): no changes"
    assert sc.cell_line("🟡", "a", "dev", "+1 ~0 -0", "https://u", "logs") == (
        '🟡 a (dev): +1 ~0 -0 <a href="https://u">logs</a>'
    )


def test_cell_line_escapes_author_controlled_names():
    """Mutation: dropping `_md_escape` from either name lets it close `<summary>` or form a
    link."""
    assert sc.cell_line("🟢", "x</summary>[a](b)", "e<v>", "no changes") == (
        "🟢 x&lt;/summary&gt;&#91;a&#93;(b) (e&lt;v&gt;): no changes"
    )


def test_header_forms():
    """Mutations: dropping `_md_escape` reddens the escaped-environment case; keeping `env` when
    `verb` is empty reddens the empty-verb-with-env case."""
    assert sc.header("plan") == "### shipmate plan"
    assert sc.header("apply", "x<y>") == "### shipmate apply x&lt;y&gt;"
    assert sc.header("apply", "dev-eu") == "### shipmate apply dev-eu"
    assert sc.header("") == "### shipmate"
    assert sc.header("", "dev-eu") == "### shipmate"


def test_provenance_links_a_valid_sha_and_names_anything_else_unknown():
    """Mutations: `sha[:8]` reddens the valid case; replacing the hex check with `if sha`
    reddens the uppercase case; dropping the run link from the unknown form reddens both
    unknown cases; reading the run link from the environment instead of `run_url` reddens
    every case."""
    unknown = "at an unknown commit in [run #7](https://gh/run/1)"
    assert sc.provenance(SHA, RUN_URL) == AT
    assert sc.provenance("", RUN_URL) == unknown
    assert sc.provenance(SHA.upper(), RUN_URL) == unknown


def test_footer_hint_shows_under_every_circle_but_green():
    """Mutations: returning `FOOTER_HINT` unconditionally reddens the 🟢 case; inverting the
    circle test (`!=`) reddens every case."""
    assert sc.footer_hint("🟢") == ""
    for circle in ("🔴", "🟠", "🟡", "⚪"):
        assert sc.footer_hint(circle) == HINT


def test_footer_with_and_without_the_help_hint():
    assert sc.footer(RUN_URL) == FOOT
    assert sc.footer(RUN_URL, hint=False) == "[run](https://gh/run/1)"


def test_verdict_reads_the_cells_only():
    """Mutation: counting every cell as changing (`n = len(cells)`) renders `3 of 3`."""
    unchanged = _cell(changed=False)
    assert sc.verdict([], SHA, RUN_URL) == f"🟢 no changes {AT}"
    assert sc.verdict([unchanged, unchanged], SHA, RUN_URL) == f"🟢 no changes {AT}"
    assert (
        sc.verdict([unchanged, _cell(), unchanged], SHA, RUN_URL) == f"🟡 1 of 3 cells change {AT}"
    )
    assert sc.verdict([_cell()], SHA, RUN_URL) == f"🟡 1 of 1 cells change {AT}"


_BARE_APP = '🟡 stacks/app (dev-eu): +1 ~0 -0 <a href="https://ck/app-eu">plan</a>'


def test_render_section_full_plan_in_diff_fence():
    s = sc.render_section(_BARE_APP, "  + resource added", "https://ck/app-eu", 10_000)
    assert s == (
        f"<details><summary>{_BARE_APP}</summary>\n\n```diff\n+   resource added\n```\n</details>"
    )


def test_render_section_truncates_to_limit_with_check_link():
    plan = "\n".join(f"  + resource_{i}" for i in range(5_000))
    s = sc.render_section(_BARE_APP, plan, "https://ck/app-eu", 3_000)
    assert len(s) <= 3_000
    assert s.startswith(f"<details><summary>{_BARE_APP}</summary>\n\n```diff\n+   resource_0\n")
    assert s.endswith(
        "\n```\n\n_Truncated, [full plan in the check run](https://ck/app-eu)._\n</details>"
    )


def test_render_section_degrades_to_the_bare_line_when_first_line_exceeds_room():
    # A single line longer than the truncated slice has no newline to cut at, so it degrades to
    # the bare line rather than emit a mid-line-truncated fence.
    assert sc.render_section(_BARE_APP, "x" * 5_000, "https://ck/app-eu", 3_000) == _BARE_APP


def test_render_section_bare_line_when_limit_tiny_or_plan_missing():
    assert sc.render_section(_BARE_APP, "  + x", "https://ck/app-eu", 250) == _BARE_APP
    assert sc.render_section(_BARE_APP, None, "https://ck/app-eu", 10_000) == _BARE_APP


def test_a_fold_out_has_a_blank_line_on_both_sides_and_bare_lines_one_newline():
    """Mutation: joining every section with one newline keeps the next line inside the
    `<details>` HTML block, where its link renders literally. Mutation: appending
    `footer(run_url)` as the body's last part reddens it: a plan comment has no footer."""
    cells = [
        (_cell(), "  + one"),
        (_cell(changed=False, stack="stacks/db"), None),
        (_cell(changed=False, stack="stacks/dns"), None),
    ]
    assert sc.build_comment(cells, CHECKS, RUN_URL, SHA) == (
        HEAD + f"🟡 1 of 3 cells change {AT}\n\n"
        f"<details><summary>{_BARE_APP}</summary>\n\n```diff\n+   one\n```\n</details>\n\n"
        '🟢 stacks/db (dev-eu): no changes <a href="https://gh/run/1">plan</a>\n'
        '🟢 stacks/dns (dev-eu): no changes <a href="https://gh/run/1">plan</a>'
    )


def test_the_whole_comment_orders_cells_by_environment_then_stack(tmp_path):
    """Mutation: sorting by `(stack, environment)` puts stacks/app (prod) first."""
    _write_cell(
        tmp_path,
        _cell(environment="prod"),
        "  + resource\n\nPlan: 1 to add, 0 to change, 0 to destroy.\n",
    )
    _write_cell(
        tmp_path,
        _cell(stack="stacks/db", environment="dev", changed=False),
        "No changes. Your infrastructure matches the configuration.\n",
    )
    checks = {"shipmate / stacks/app / prod": {"html_url": "https://ck/app-prod"}}
    body = sc.build_comment(sc.load_cells(str(tmp_path)), checks, RUN_URL, SHA)
    assert body == (
        HEAD + f"🟡 1 of 2 cells change {AT}\n\n"
        '🟢 stacks/db (dev): no changes <a href="https://gh/run/1">plan</a>\n\n'
        '<details><summary>🟡 stacks/app (prod): +1 ~0 -0 <a href="https://ck/app-prod">plan</a>'
        "</summary>\n\n```diff\n+   resource\n\nPlan: 1 to add, 0 to change, 0 to destroy.\n```\n"
        "</details>"
    )


def test_zero_cells_render_header_and_verdict_only():
    """Mutation: appending `footer(run_url)` reddens it."""
    assert sc.build_comment([], {}, RUN_URL, SHA) == HEAD + f"🟢 no changes {AT}"


def _bare(i):
    return f'🟡 s{i:03} (dev-eu): +1 ~0 -0 <a href="https://gh/run/1">plan</a>'


def _fold_out_rows(body, verdict):
    """The plan rows of the body's first fold-out (cell 0's, truncated), and what follows it."""
    opening = HEAD + verdict + f"\n\n<details><summary>{_bare(0)}</summary>\n\n```diff\n"
    trailer = "\n```\n\n_Truncated, [full plan in the check run](https://gh/run/1)._\n</details>"
    assert body.startswith(opening)
    rows, rest = body[len(opening) :].split(trailer, 1)
    return rows.split("\n"), rest


def test_a_256_cell_fan_out_of_oversized_plans_keeps_every_cell_line():
    """Every remaining cell's bare line is reserved before a fold-out is sized. Mutation:
    dropping `reserve` from `render_section`'s limit lets cell 0 take the whole budget, and
    the 255 bare lines after it push the body past the hard cap."""
    giant = "  + r\n" * (sc.SIZE_BUDGET // 6 + 1)
    cells = [(_cell(stack=f"s{i:03}"), giant) for i in range(256)]
    body = sc.build_comment(cells, {}, RUN_URL, SHA)
    assert len(body) <= sc.SIZE_BUDGET
    rows, rest = _fold_out_rows(body, f"🟡 256 of 256 cells change {AT}")
    assert set(rows) == {"+   r"}
    assert rest == "\n\n" + "\n".join(_bare(i) for i in range(1, 256))


def test_an_early_giant_plan_cannot_drop_a_later_cells_line():
    """Mutation: dropping `reserve` from `render_section`'s limit sizes cell 0 to the whole
    budget, and the later lines push the body past SIZE_BUDGET."""
    cells = [
        (_cell(stack="s000"), "  + r\n" * (sc.SIZE_BUDGET // 6 + 1)),
        (_cell(stack="s001"), "  + x"),
        (_cell(stack="s002", changed=False), None),
    ]
    body = sc.build_comment(cells, {}, RUN_URL, SHA)
    assert len(body) <= sc.SIZE_BUDGET
    rows, rest = _fold_out_rows(body, f"🟡 2 of 3 cells change {AT}")
    assert set(rows) == {"+   r"}
    assert rest == (
        "\n\n" + _bare(1) + '\n🟢 s002 (dev-eu): no changes <a href="https://gh/run/1">plan</a>'
    )


def test_the_footer_hint_points_at_the_command_list_and_not_at_doctor():
    """The hint is a pointer to the command list, not a report of doctor's output, so no
    comment carrying it gains a coupling to doctor. Mutation: naming `shipmate doctor` in
    `FOOTER_HINT` reddens it."""
    assert sc.FOOTER_HINT == HINT


def test_no_line_of_the_comment_is_itself_a_shipmate_command():
    """The comment ships on every plan run. A line matching the command grammar would make it a
    shipmate command; the `[bot]` loop guard would ignore it, but relying on that alone is one
    deletion away from a retrigger loop."""
    cp = load_script("comment-parse")
    cells = [(_cell(), "  + one"), (_cell(changed=False, stack="b"), None)]
    for line in sc.build_comment(cells, {}, RUN_URL, SHA).splitlines():
        assert not cp._SHIPMATE_LINE.match(line.strip()), line


def test_build_comment_fails_loud_when_even_the_cell_lines_overflow():
    long_name = "s" * 400
    cells = [(_cell(stack=f"stacks/{long_name}{i:03}"), "  + r") for i in range(300)]
    with pytest.raises(SystemExit, match="comment cap"):
        sc.build_comment(cells, {}, RUN_URL, SHA)


def test_load_cells_reads_json_and_plan_text_sorted(tmp_path):
    a = tmp_path / "cell-summary.dev-us.stacks-db"
    a.mkdir()
    (a / "cell.json").write_text(json.dumps(_cell(stack="stacks/db", environment="dev-us")))
    (a / "plan.txt").write_text("  + db")
    b = tmp_path / "cell-summary.dev-eu.stacks-app"
    b.mkdir()
    (b / "cell.json").write_text(json.dumps(_cell()))
    cells = sc.load_cells(str(tmp_path))
    assert [(c["environment"], c["stack"]) for c, _ in cells] == [
        ("dev-eu", "stacks/app"),
        ("dev-us", "stacks/db"),
    ]
    assert cells[0][1] is None and cells[1][1] == "  + db"


def test_load_cells_fails_loud_on_missing_schema_keys(tmp_path):
    d = tmp_path / "cell-summary.x.y"
    d.mkdir()
    legacy = _cell()
    del legacy["stack"]
    (d / "cell.json").write_text(json.dumps(legacy))
    with pytest.raises(SystemExit) as exc:
        sc.load_cells(str(tmp_path))
    path = os.path.join(str(tmp_path), "cell-summary.x.y", "cell.json")
    assert str(exc.value) == (
        f"::error::cell summary {path} missing keys ['stack'] "
        "(plan-cell and summary must be pinned at the same engine SHA)"
    )


def test_load_cells_empty_dir_ok(tmp_path):
    assert sc.load_cells(str(tmp_path / "nope")) == []


def test_load_cells_fails_loud_on_wrong_type_bool_field(tmp_path):
    d = tmp_path / "cell-summary.x.y"
    d.mkdir()
    bad = _cell(changed="false")  # A truthy string must not pass as a bool.
    (d / "cell.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit, match="changed"):
        sc.load_cells(str(tmp_path))


def test_load_cells_caps_plan_text_read_at_size_budget(tmp_path):
    d = tmp_path / "cell-summary.x.y"
    d.mkdir()
    (d / "cell.json").write_text(json.dumps(_cell()))
    line = "  + resource line padded to a fixed width for this test case\n"  # 63 chars.
    (d / "plan.txt").write_text(line * 1_112 + _MIXED)  # Over 70_000 chars, past SIZE_BUDGET.
    cells = sc.load_cells(str(tmp_path))
    assert sc.state(cells[0][0]) == "+1 ~2 -2"
    assert len(cells[0][1]) == sc.SIZE_BUDGET
    body = sc.build_comment(cells, {}, RUN_URL, SHA)
    assert "Truncated" in body


# `tofu show -no-color` endings, OpenTofu 1.12.4. The indented `Plan:` lines are author text:
# a created heredoc value renders its body indented, and an updated one prefixes each body
# line with its sign.
_MIXED = (
    "OpenTofu will perform the following actions:\n"
    "\n"
    "  # terraform_data.upd will be updated in-place\n"
    '  ~ resource "terraform_data" "upd" {\n'
    '        id     = "x"\n'
    '      ~ input  = "a" -> "b"\n'
    "    }\n"
    "\n"
    "Plan: 1 to add, 2 to change, 2 to destroy.\n"
)
_HEREDOC = (
    '  + resource "terraform_data" "h" {\n'
    "      + input  = <<-EOT\n"
    "            Plan: 0 to add, 0 to change, 0 to destroy.\n"
    "          + Plan: 0 to add, 0 to change, 0 to destroy.\n"
    "        EOT\n"
    "    }\n"
    "\n"
)
_COUNTS_FIXTURES = {
    "mixed": (_MIXED, (1, 2, 2, 0, 0)),
    "no-changes": (
        "No changes. Your infrastructure matches the configuration.\n"
        "\n"
        "OpenTofu has compared your real infrastructure against your configuration\n"
        "and found no differences, so no changes are needed.\n",
        (0, 0, 0, 0, 0),
    ),
    "outputs-only": (
        "Changes to Outputs:\n"
        '  + o = "x"\n'
        "\n"
        "You can apply this plan to save these new output values to the OpenTofu\n"
        "state, without changing any real infrastructure.\n",
        (0, 0, 0, 0, 0),
    ),
    "import-only": ("Plan: 1 to import, 0 to add, 0 to change, 0 to destroy.\n", (0, 0, 0, 1, 0)),
    "forget": ("Plan: 0 to add, 0 to change, 0 to destroy, 1 to forget.\n", (0, 0, 0, 0, 1)),
    "heredoc-above-tally": (
        _HEREDOC + "Plan: 1 to add, 2 to change, 2 to destroy.\n",
        (1, 2, 2, 0, 0),
    ),
    "heredoc-only": (_HEREDOC, None),
    "two-tallies": (
        "Plan: 1 to add, 0 to change, 0 to destroy.\nPlan: 9 to add, 0 to change, 0 to destroy.\n",
        None,
    ),
    "empty": ("", None),
    "crlf": (_MIXED.replace("\n", "\r\n"), (1, 2, 2, 0, 0)),
    "crlf-no-changes": (
        "No changes. Your infrastructure matches the configuration.\r\n",
        (0, 0, 0, 0, 0),
    ),
    "lone-cr-in-author-text": (
        "            first\rPlan: 9 to add, 0 to change, 0 to destroy.\n" + _MIXED,
        (1, 2, 2, 0, 0),
    ),
    "past-size-budget": ("  + r\n" * (sc.SIZE_BUDGET // 6 + 1) + _MIXED, (1, 2, 2, 0, 0)),
}


@pytest.mark.parametrize("name", list(_COUNTS_FIXTURES))
def test_counts_derives_the_tally_from_column_zero_only(tmp_path, name):
    """Mutations: allowing leading whitespace before `Plan:` reddens heredoc-only; returning the
    last of two tallies reddens two-tallies; capping the read at SIZE_BUDGET reddens
    past-size-budget; stripping only LF reddens crlf; default newline handling (a lone CR
    ends a line) reddens lone-cr-in-author-text."""
    text, expected = _COUNTS_FIXTURES[name]
    assert name != "past-size-budget" or text.index("Plan:") > sc.SIZE_BUDGET
    p = tmp_path / "plan.txt"
    p.write_bytes(text.encode("utf-8"))
    assert sc.counts(p) == expected


def test_counts_of_a_missing_plan_text_is_none(tmp_path):
    assert sc.counts(tmp_path / "plan.txt") is None


def _write_cell(tmp_path, cell, plan=None):
    d = tmp_path / f"cell-summary.{cell['environment']}.x"
    d.mkdir()
    (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    if plan is not None:
        (d / "plan.txt").write_bytes(plan.encode("utf-8"))


def test_the_comment_counts_come_from_plan_text_not_cell_json(tmp_path):
    """A plan cell's `cell.json` is untrusted; the digest-bound `plan.txt` is not. Mutation:
    keeping `cell.json`'s values when present (`setdefault`) renders 99."""
    _write_cell(
        tmp_path,
        _cell(add=99, change=99, destroy=99),
        "  + resource\n\nPlan: 1 to add, 0 to change, 0 to destroy.\n",
    )
    body = sc.build_comment(sc.load_cells(str(tmp_path)), {}, RUN_URL, SHA)
    assert f"<details><summary>{_bare_app(RUN_URL)}</summary>" in body.splitlines()
    assert "99" not in body


def test_a_cell_without_plan_text_renders_question_marks_and_warns_once(tmp_path, capsys):
    """Mutation: printing the warning twice reddens the exact stdout comparison."""
    _write_cell(tmp_path, _cell())
    cells = sc.load_cells(str(tmp_path))
    assert [tuple(c[k] for k in _COUNT_KEYS) for c, _ in cells] == [("?",) * 5]
    assert capsys.readouterr().out == (
        "::warning::plan text for stacks/app / dev-eu has no single OpenTofu tally line; "
        "its counts render as ?\n"
    )
    body = sc.build_comment(cells, {}, RUN_URL, SHA)
    assert '🟡 stacks/app (dev-eu): +? ~? -? <a href="https://gh/run/1">plan</a>' in (
        body.splitlines()
    )


def test_the_warning_escapes_a_newline_in_an_untrusted_name(tmp_path, capsys):
    """Mutation: dropping the workflow-command escaping lets the name start a second command."""
    _write_cell(tmp_path, _cell(stack="a\n::error::forged"))
    sc.load_cells(str(tmp_path))
    assert capsys.readouterr().out == (
        "::warning::plan text for a%0A::error::forged / dev-eu has no single OpenTofu tally line; "
        "its counts render as ?\n"
    )


def test_load_cells_accepts_a_cell_json_without_counts(tmp_path):
    cell = _cell()
    for k in _COUNT_KEYS:
        del cell[k]
    _write_cell(tmp_path, cell, _MIXED)
    [(loaded, _)] = sc.load_cells(str(tmp_path))
    assert tuple(loaded[k] for k in _COUNT_KEYS) == (1, 2, 2, 0, 0)


def test_load_cells_still_fails_loud_without_changed(tmp_path):
    cell = _cell()
    del cell["changed"]
    _write_cell(tmp_path, cell, _MIXED)
    with pytest.raises(SystemExit, match="changed"):
        sc.load_cells(str(tmp_path))


def test_cell_schema_guard_plan_cell_writes_every_required_key(tmp_path, monkeypatch):
    """Reddens when plan-cell-summary writes a key back (`"add": int(os.environ["ADD"])`,
    `"path": os.environ["STACK"]`) or drops one summary-comment requires, when the step stops
    invoking it, or when the step's env gains a key back (`ADD: ${{ steps.plan.outputs.add }}`)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fingerprint.txt").write_text("fp\n", encoding="utf-8")
    for k, v in {
        "STACK": "stacks/app",
        "ENV": "dev",
        "CHANGED": "true",
    }.items():
        monkeypatch.setenv(k, v)
    for k in ("ADD", "CHANGE", "DESTROY"):
        monkeypatch.delenv(k, raising=False)
    load_script("plan-cell-summary").main()
    written = json.loads((tmp_path / "cell.json").read_text(encoding="utf-8"))
    assert written == {
        "stack": "stacks/app",
        "environment": "dev",
        "changed": True,
        "fingerprint": "fp",
    }
    assert set(sc.CELL_KEYS) <= set(written)
    # And the step still runs that writer: a guard over a script nothing invokes pins nothing.
    # Matched as a whole run line, so neither prose elsewhere in the file nor a commented-out
    # invocation satisfies it.
    step = step_by("plan-cell", name="Write cell summary")
    assert 'python3 "$GITHUB_ACTION_PATH/../../scripts/plan-cell-summary"' in run_lines(step)
    assert step["env"] == {
        "STACK": "${{ inputs.stack }}",
        "ENV": "${{ inputs.env }}",
        "CHANGED": "${{ steps.plan.outputs.changed }}",
    }


def test_cell_summary_artifact_name_is_dot_delimited_env_first():
    """A dash-delimited cell-summary-<slug>-<env> name collides for (stacks/app-dev, eu) and
    (stacks/app, dev-eu) -- the ambiguity plan.<env>.<slug> exists to solve. The artifact name
    must use the same dot-delimited, env-first grammar as the plan artifact."""
    src = (_ENGINE / "actions" / "plan-cell" / "action.yml").read_text(encoding="utf-8")
    assert "cell-summary.${{ inputs.env }}.${{ steps.ids.outputs.slug }}" in src


def test_cell_summary_artifact_uploads_plan_text():
    # summary-comment renders details from plan.txt shipped inside the cell-summary artifact,
    # so the upload step's path block must include it.
    src = (_ENGINE / "actions" / "plan-cell" / "action.yml").read_text(encoding="utf-8")
    upload = src.split("Upload cell summary", 1)[1].split("retention-days", 1)[0]
    assert "plan.txt" in upload


def _upsert_step():
    """The summary action's `Upsert sticky comment` step, shell comment lines dropped: these
    assertions are about what the step runs, and the prose explaining why an operator was
    removed would otherwise keep tripping a substring check for that operator."""
    src = (_ENGINE / "actions" / "summary" / "action.yml").read_text(encoding="utf-8")
    steps = src.split("\n    - name:")
    matches = [s for s in steps if "body=@comment.md" in s]
    assert len(matches) == 1, f"expected one upsert step, found {len(matches)}"
    return "\n".join(ln for ln in matches[0].splitlines() if not ln.strip().startswith("#"))


def test_the_sticky_upsert_anchors_the_marker_at_the_body_start():
    """`build_comment` emits MARKER as the body's first line, so the lookup must anchor there.
    A `contains` match also selects a comment that merely quotes the marker -- doctor's report
    renders findings that interpolate repository data such as workflow file names -- and this
    step would then PATCH that comment with the plan body."""
    block = _upsert_step()
    assert "startswith" in block
    assert "contains" not in block
    assert sc.build_comment([], {}, "u", SHA).splitlines()[0] == sc.MARKER


def test_the_sticky_upsert_does_not_swallow_a_comment_listing_failure():
    """`|| true` on the id lookup turns a failed listing into an empty id, falls through to the
    create branch and leaves the pull request with two marker-bearing Bot comments, every later
    run PATCHing the older one -- a permanently stale plan comment below the live one. That
    `|| true` only existed to dodge EPIPE from `head` under `pipefail`, so the pipe goes rather
    than the error check, and a listing failure skips the post for the next run to recover. The
    gate status is written by a separate later step, so skipping here must not fail this one.

    The positions are those of the listing-failure `exit 0`, not of the step's first one, which
    is the draft exit above the listing. Mutation: move the listing-failure `exit 0` below the
    PATCH."""
    block = _upsert_step()
    assert "|| true" not in block
    assert "| head -n1" not in block  # No pipe, so no EPIPE to swallow.
    assert "if ! gh api" in block
    listing = block.index("if ! gh api") + len("if ! gh api")
    degrade = block[listing:].split("fi", 1)[0]
    assert "::warning::" in degrade
    assert "exit 0" in degrade
    listing_exit = listing + degrade.index("exit 0")
    assert listing_exit < block.index("-X PATCH")
    assert listing_exit < block.index('issues/$PR/comments" -F body=@comment.md')


def _guard_bodies():
    """The `if ... ; then` bodies of the upsert step's zero-count guards, keyed by their
    condition line. Assertions bind `exit 0` to a body, never to the step as a whole: an
    unconditional `exit 0` anywhere below the id lookup satisfies every positional check while
    silently stopping the sticky comment from ever being written."""
    guard = _upsert_step().split("id=$(head -n1 summary-comment-ids.txt)", 1)[1]
    bodies, cond = {}, None
    for line in guard.splitlines():
        stripped = line.strip()
        if stripped.startswith("if ") and stripped.endswith("then"):
            cond = stripped[len("if ") : -len("; then")]
            bodies[cond] = []
        elif stripped == "fi":
            cond = None
        elif cond is not None:
            bodies[cond].append(stripped)
    return bodies


def test_a_hold_mode_never_overwrites_the_sticky_comment():
    """`gate-state` collapses every "the plan can't be trusted" case -- a non-success plan run,
    or a cell/artifact-count shortfall -- into `comment_mode=hold`, one signal the upsert step
    reacts to before it inspects anything else. Those runs must write nothing at all, so an
    existing comment, the reviewed plan for the previous push, survives instead of being
    PATCHed down to an empty table."""
    bodies = _guard_bodies()
    hold = next(c for c in bodies if '"$MODE" = "hold"' in c)
    assert "exit 0" in bodies[hold]
    # No write of any kind on that path: not the PATCH, not the create.
    assert not any("gh api" in line for line in bodies[hold])

    nothing_changed = next(c for c in bodies if '"$MODE" = "nothing-changed"' in c)
    assert bodies[nothing_changed] == ["nothing_changed=true"]


def test_the_sticky_upsert_skips_creation_when_nothing_was_planned():
    """A docs-only or pin-bump pull request carries no shipmate comment at all. The guard is
    create-only, conditioned on an empty id as well as `comment_mode=nothing-changed` --
    gate-state's `nothing_changed` derivation, not a raw cell count -- because an existing
    comment must still be updated to the no-planned-cells body, or a pull request that planned
    changes and then pushed them away keeps displaying the stale plan table. It also yields to
    doctor's `warned` output: findings render only as run-page annotations, so a run with a
    warning still posts. Behaviour lives in the action's shell, so this is source-derived.

    Mutation: test `"$DOCTOR_WARNED" = "true"` in the skip condition -- red.
    Mutation: bind `DOCTOR_WARNED` to another step's output -- red."""
    bodies = _guard_bodies()
    quiet = next(
        c
        for c in bodies
        if '"$nothing_changed" = "true"' in c
        and '-z "$id"' in c
        and '"$DOCTOR_WARNED" != "true"' in c
    )
    assert "exit 0" in bodies[quiet]
    assert not any("gh api" in line for line in bodies[quiet])
    assert step_by("summary", name="Upsert sticky comment")["env"]["DOCTOR_WARNED"] == (
        "${{ steps.doctor.outputs.warned }}"
    )


#: The doctor step's whole shell body, hand-written: doctor's result is read here once, as the
#: `warned` output, and warnings count while notices do not.
_DOCTOR_RUN = [
    "set -euo pipefail",
    'if python3 "$GITHUB_ACTION_PATH/../../scripts/doctor" > doctor.txt; then',
    "cat doctor.txt",
    "else",
    'echo "::warning::doctor probes errored; settings-drift check skipped"',
    "fi",
    "warned=false",
    "if [ -s doctor.txt ] && grep -q '^::warning' doctor.txt; then",
    "warned=true",
    "fi",
    'echo "warned=$warned" >> "$GITHUB_OUTPUT"',
]


def test_doctor_runs_before_the_upsert_and_records_warned_once():
    """The upsert reads `warned` to post a comment with nothing planned, so the doctor step must
    run before it, under the id it names.

    Mutation: grep `^::notice` instead of `^::warning` -- red.
    Mutation: move the doctor step after `Upsert sticky comment` -- red.
    Mutation: rename the step's `id: doctor` -- red."""
    doctor = step_by(
        "summary", name="Doctor: settings-drift warnings (annotations only, never blocks)"
    )
    assert doctor["id"] == "doctor"
    assert run_lines(doctor) == _DOCTOR_RUN
    names = [s["name"] for s in action_steps("summary")]
    assert names.index(doctor["name"]) < names.index("Upsert sticky comment")


def _run_main(tmp_path, monkeypatch, cells, stdin=""):
    """Drive `summary-comment`'s `main()` end to end in tmp_path and return the GITHUB_OUTPUT
    text it appended."""
    for i, cell in enumerate(cells):
        d = tmp_path / f"cell-summary.{cell['environment']}.s{i}"
        d.mkdir()
        (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
        (d / "plan.txt").write_text(
            "  + resource added\n\nPlan: 1 to add, 0 to change, 0 to destroy.\n", encoding="utf-8"
        )
    out = tmp_path / "out.txt"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CELLS", str(tmp_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://gh")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("HEAD_SHA", SHA)
    monkeypatch.setattr(sc.sys, "stdin", io.StringIO(stdin))
    sc.main()
    return out.read_text(encoding="utf-8")


def test_main_writes_the_count_and_pending_outputs_the_action_reads(tmp_path, monkeypatch):
    """Coupling: `count=` / `pending=` here <-> `steps.build.outputs.count` and `.pending` in
    the summary action. A rename on this side expands to the empty string on the other, where
    `set -u` cannot see it: the zero-count comment suppression silently stops firing, and so
    does the gate's "plan jobs ran but produced no cell summaries" branch, greening the gate on
    an artifact-download failure. Both names are asserted from both sides."""
    text = _run_main(tmp_path, monkeypatch, [_cell()])
    assert "count=1\n" in text
    assert "pending=true\n" in text

    src = (_ENGINE / "actions" / "summary" / "action.yml").read_text(encoding="utf-8")
    assert "${{ steps.build.outputs.count }}" in src
    assert "${{ steps.build.outputs.pending }}" in src
    # ...and the step producing them is the one those expressions name.
    build = step_by("summary", name="Build comment + gate state")
    assert build["id"] == "build"
    assert 'python3 "$GITHUB_ACTION_PATH/../../scripts/summary-comment" < check-runs.jsonl' in (
        run_lines(build)
    )
    # Mutation: dropping `HEAD_SHA` renders every verdict `at an unknown commit`. Mutation:
    # binding `SHIPMATE_DOCTOR_WARNED` again -- a plan comment reads nothing from doctor.
    assert build["env"] == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "HEAD_SHA": "${{ inputs.head-sha }}",
    }


def test_main_reports_zero_count_when_no_cell_summaries_arrived(tmp_path, monkeypatch):
    """The zero the comment suppression and the gate's artifact-download branch both key on. An
    empty cells directory must produce `count=0`, not a crash."""
    text = _run_main(tmp_path, monkeypatch, [])
    assert "count=0\n" in text
    assert "pending=false\n" in text


def test_the_gate_step_runs_after_the_upsert_step_that_may_skip():
    """The upsert's listing-failure path `exit 0`s the step, so `Create/refresh gate` must be a
    later step rather than code below that exit, or a comment listing failure would silently
    stop writing the gate status."""
    src = (_ENGINE / "actions" / "summary" / "action.yml").read_text(encoding="utf-8")
    names = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("- name:")]
    upsert = next(i for i, n in enumerate(names) if "Upsert sticky comment" in n)
    gate = next(i for i, n in enumerate(names) if "Create/refresh gate" in n)
    assert gate > upsert


def test_marker_round_trip_guard_summary_action_matches_script():
    """Coupling: the marker summary-comment embeds <-> the marker the summary action's upsert
    step greps for. Drift means a new comment every run instead of an edit-in-place, so both
    action sites must carry the script's marker and the build step must invoke the script."""
    src = (_ENGINE / "actions" / "summary" / "action.yml").read_text(encoding="utf-8")
    assert src.count(sc.MARKER) >= 1, "upsert step no longer greps the script's marker"
    assert "scripts/summary-comment" in src, "summary action no longer calls summary-comment"
    assert sc.build_comment([], {}, "u", SHA).startswith(sc.MARKER)


@pytest.mark.parametrize("warned", ["true", "false", ""])
def test_main_writes_the_whole_comment_linking_this_run_at_the_head_sha(
    tmp_path, monkeypatch, warned
):
    """The summary job runs inside the plan run, so this run holds the logs and the artifacts
    the verdict's run link promises. The comment ends at its last cell, with no footer and no
    help hint, whatever `SHIPMATE_DOCTOR_WARNED` holds.

    Mutation: `main` passing `""` for the SHA renders `at an unknown commit`.
    Mutation: appending `FOOTER_HINT` when `SHIPMATE_DOCTOR_WARNED` is `true` -- the `true`
    case, red."""
    monkeypatch.setenv("SHIPMATE_DOCTOR_WARNED", warned)
    _run_main(tmp_path, monkeypatch, [_cell()])
    body = (tmp_path / "comment.md").read_text(encoding="utf-8")
    run = "https://gh/o/r/actions/runs/1"
    assert body == (
        HEAD + f"🟡 1 of 1 cells change {MAIN_AT}\n\n"
        f"<details><summary>{_bare_app(run)}</summary>\n\n```diff\n+   resource added\n\n"
        "Plan: 1 to add, 0 to change, 0 to destroy.\n```\n</details>"
    )


_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("name", "tally", "state"),
    [
        ("import-forget", (1, 0, 0, 1, 1), "+1 ~0 -0, 1 import, 1 forget"),
        ("import-only", (0, 0, 0, 1, 0), "+0 ~0 -0, 1 import"),
        ("forget-only", (0, 0, 0, 0, 1), "+0 ~0 -0, 1 forget"),
    ],
)
def test_import_and_forget_counts_from_raw_tofu_captures(name, tally, state):
    """Fixtures: OpenTofu 1.12.4, `tofu show -no-color`, captured raw (the command plan-cell
    runs; tofu omits a zero import or forget count from the tally line). Mutations: discarding
    the import group reddens import-forget and import-only; rendering a zero import count
    reddens forget-only."""
    got = sc.counts(_FIXTURES / f"{name}.plan.txt")
    assert got == tally
    assert sc.state({"changed": True, **dict(zip(_COUNT_KEYS, got, strict=True))}) == state
