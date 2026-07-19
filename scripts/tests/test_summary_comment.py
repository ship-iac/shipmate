# scripts/tests/test_summary_comment.py
import importlib.util
import json
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "summary-comment"
_loader = SourceFileLoader("summary_comment", str(_p))
_spec = importlib.util.spec_from_loader("summary_comment", _loader)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _cell(**kw):
    base = {
        "stack": "stacks/app",
        "stack_path": "stacks/app",
        "environment": "dev-eu",
        "add": 1,
        "change": 0,
        "destroy": 0,
        "changed": True,
    }
    base.update(kw)
    return base


# --- diff_map -------------------------------------------------------------


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


# --- fence ----------------------------------------------------------------


def test_fence_grows_past_backtick_runs_in_plan_text():
    text = "x = ```code```"
    fenced = sc.fence(text)
    assert fenced.startswith("````diff\n")
    assert fenced.endswith("\n````")


def test_fence_minimum_three_backticks():
    assert sc.fence("no ticks").startswith("```diff\n")


# --- emoji / escape ---------------------------------------------------------


def test_emoji_verdicts():
    assert sc.emoji(_cell(changed=False, add=0)) == "🟢"
    assert sc.emoji(_cell(destroy=2)) == "🔴"
    assert sc.emoji(_cell()) == "🟡"


def test_md_escape_neutralizes_pipes_and_newlines():
    assert sc._md_escape("a|b\nc") == "a\\|b c"


def test_md_escape_neutralizes_angle_brackets():
    assert sc._md_escape("x</summary><b>") == "x&lt;/summary&gt;&lt;b&gt;"


# --- table / sections -------------------------------------------------------

CHECKS = {
    "plan / dev-eu / stacks/app": {"html_url": "https://ck/app-eu"},
    "plan / dev-us / stacks/db": {"html_url": "https://ck/db-us"},
}
RUN_URL = "https://gh/run/1"


def test_check_url_resolves_by_env_and_stack_path_with_run_url_fallback():
    assert sc.check_url(_cell(), CHECKS, RUN_URL) == "https://ck/app-eu"
    assert sc.check_url(_cell(environment="prod"), CHECKS, RUN_URL) == RUN_URL


def test_build_table_row_per_cell_with_emoji_counts_and_link():
    cells = [
        _cell(),
        _cell(stack="stacks/db", stack_path="stacks/db", environment="dev-us", add=0, destroy=2),
    ]
    table = sc.build_table(cells, CHECKS, RUN_URL)
    assert "| 🟡 | stacks/app | dev-eu | 1 | 0 | 0 | [plan](https://ck/app-eu) |" in table
    assert "| 🔴 | stacks/db | dev-us | 0 | 0 | 2 | [plan](https://ck/db-us) |" in table


def test_build_table_empty_case():
    assert "_(no stacks changed)_" in sc.build_table([], {}, RUN_URL)


def test_render_section_full_plan_in_diff_fence():
    s = sc.render_section(_cell(), "  + resource added", "https://ck/app-eu", 10_000)
    assert s.startswith("<details><summary>🟡 dev-eu / stacks/app — +1 ~0 -0</summary>")
    assert "```diff\n+   resource added\n```" in s
    assert s.endswith("</details>")


def test_render_section_truncates_to_limit_with_check_link():
    plan = "\n".join(f"  + resource_{i}" for i in range(5_000))
    s = sc.render_section(_cell(), plan, "https://ck/app-eu", 3_000)
    assert len(s) <= 3_000
    assert "Truncated" in s and "https://ck/app-eu" in s
    assert s.rstrip().endswith("</details>")


def test_render_section_link_only_when_limit_tiny_or_plan_missing():
    tiny = sc.render_section(_cell(), "  + x", "https://ck/app-eu", 250)
    assert "```" not in tiny and "https://ck/app-eu" in tiny
    missing = sc.render_section(_cell(), None, "https://ck/app-eu", 10_000)
    assert "```" not in missing and "https://ck/app-eu" in missing


# --- build_comment ----------------------------------------------------------


def test_build_comment_marker_first_no_change_cells_have_no_details():
    cells = [
        (_cell(changed=False, add=0), "no changes"),
        (_cell(stack="stacks/db", stack_path="stacks/db", environment="dev-us"), "  + one"),
    ]
    body = sc.build_comment(cells, CHECKS, RUN_URL)
    assert body.startswith(sc.MARKER)
    assert body.count("<details>") == 1
    assert "dev-us / stacks/db" in body


def test_build_comment_stays_under_budget_and_keeps_every_cells_link():
    cells = []
    for i in range(30):
        c = _cell(stack=f"stacks/s{i:02}", stack_path=f"stacks/s{i:02}")
        cells.append((c, "\n".join(f"  + resource_{j}" for j in range(500))))
    body = sc.build_comment(cells, {}, RUN_URL)
    assert len(body) <= sc.SIZE_BUDGET
    for i in range(30):
        assert f"stacks/s{i:02}" in body


def test_build_comment_hard_cap_fallback_drops_details_never_the_table():
    cells = [
        (_cell(stack=f"stacks/s{i:03}", stack_path=f"stacks/s{i:03}"), "  + r") for i in range(300)
    ]
    body = sc.build_comment(cells, {}, RUN_URL)
    assert len(body) <= sc.HARD_CAP
    assert "stacks/s299" in body  # table row always present


def test_build_comment_footer_links_run():
    body = sc.build_comment([], {}, RUN_URL)
    assert RUN_URL in body


def test_build_comment_fails_loud_when_even_the_table_overflows():
    long_name = "s" * 400
    cells = [
        (_cell(stack=f"stacks/{long_name}{i:03}", stack_path=f"stacks/{long_name}{i:03}"), "  + r")
        for i in range(300)
    ]
    with pytest.raises(SystemExit, match="comment cap"):
        sc.build_comment(cells, {}, RUN_URL)


# --- load_cells --------------------------------------------------------------


def test_load_cells_reads_json_and_plan_text_sorted(tmp_path):
    a = tmp_path / "cell-summary-stacks-db-dev-us"
    a.mkdir()
    (a / "cell.json").write_text(
        json.dumps(_cell(stack="stacks/db", stack_path="stacks/db", environment="dev-us"))
    )
    (a / "plan.txt").write_text("  + db")
    b = tmp_path / "cell-summary-stacks-app-dev-eu"
    b.mkdir()
    (b / "cell.json").write_text(json.dumps(_cell()))
    cells = sc.load_cells(str(tmp_path))
    assert [(c["environment"], c["stack"]) for c, _ in cells] == [
        ("dev-eu", "stacks/app"),
        ("dev-us", "stacks/db"),
    ]
    assert cells[0][1] is None and cells[1][1] == "  + db"


def test_load_cells_fails_loud_on_missing_schema_keys(tmp_path):
    d = tmp_path / "cell-summary-x-y"
    d.mkdir()
    legacy = _cell()
    del legacy["stack_path"]
    (d / "cell.json").write_text(json.dumps(legacy))
    with pytest.raises(SystemExit, match="stack_path"):
        sc.load_cells(str(tmp_path))


def test_load_cells_empty_dir_ok(tmp_path):
    assert sc.load_cells(str(tmp_path / "nope")) == []
