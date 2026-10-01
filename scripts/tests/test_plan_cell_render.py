"""plan-cell renders the text and JSON plans concurrently, and a failure of either fails the step.

Each render is awaited by its own `wait "$pid" || rc=1` before the step exits. A bare `wait`
returns 0 whatever the renders did, and a `wait "$pid"` without `|| rc=1` exits under errexit at
the first failure while the other render is still writing into the workspace.

The step body runs unmodified, with `tofu` and `python3` replaced by bash functions: bash resolves
a function before PATH, and a function behind `&` runs in a subshell like the binary would.
"""

import os
import pathlib
import subprocess
import sys

from _loader import bash_only, load_script, run_step, step_by

_TEXT = "plan text\n"
_JSON = '{"format_version":"1.2"}\n'

#: The `-json` arm sleeps before writing, so a step that returns without awaiting it leaves
#: plan.json incomplete. It drops its stderr first: an inherited pipe would hold subprocess.run
#: open until the sleep ends and hide an early return. `FAIL=both` reverses the timing, so the
#: json error is written first.
_STUBS = r"""
tofu() {
  case "$*" in
    *-no-color*) [ "$FAIL" = both ] && { sleep 1 ; printf 'text error\n' >&2 ; return 3 ; } ;
      [ "$FAIL" = text ] && return 3 ; printf 'plan text\n' ;;
    *-json*) [ "$FAIL" = both ] && { printf 'json error\n' >&2 ; return 4 ; } ;
      exec 2>/dev/null ; sleep 1 ; [ "$FAIL" = json ] && return 4 ;
      printf '{"format_version":"1.2"}\n' ;;
  esac
}
python3() { touch "$RUNNER_TEMP/classified" ; }
"""


def _run(tmp_path, fail):
    body = _STUBS + step_by("plan-cell", id="plan")["run"]
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir()
    env = {
        **os.environ,
        "STACK": "stacks/app",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_ACTION_PATH": str(tmp_path),
        "FAIL": fail,
    }
    return run_step(tmp_path, body, env)


def _read(tmp_path, name):
    return (tmp_path / name).read_text(encoding="utf-8")


@bash_only
def test_both_renders_succeed_and_plan_txt_is_the_text_render_whole(tmp_path):
    """Mutation: swap `plan.txt` and `plan.json` in the two render lines."""
    r = _run(tmp_path, "")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _read(tmp_path, "plan.txt") == _TEXT
    assert _read(tmp_path, "plan.json") == _JSON
    assert (tmp_path / "rt" / "classified").exists()


@bash_only
def test_a_failing_json_render_fails_the_step(tmp_path):
    """Mutations: replace both `wait` lines with one bare `wait`; drop the second `wait`."""
    r = _run(tmp_path, "json")
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert not (tmp_path / "rt" / "classified").exists()


@bash_only
def test_a_failing_text_render_fails_the_step_after_the_json_render_finished(tmp_path):
    """Mutations: replace both `wait` lines with one bare `wait` (exit 0); drop `|| rc=1` from the
    first `wait` (the step exits at the text failure with plan.json still empty)."""
    r = _run(tmp_path, "text")
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _read(tmp_path, "plan.json") == _JSON
    assert not (tmp_path / "rt" / "classified").exists()


@bash_only
def test_two_failing_renders_print_their_errors_text_first(tmp_path):
    """Mutations: drop the `2> show-json.err` redirect -- the json error prints first; drop the
    `cat` -- neither error prints."""
    r = _run(tmp_path, "both")
    assert r.returncode != 0
    assert r.stderr == "text error\njson error\n"


pcs = load_script("plan-cell-summary")
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _step_line(tmp_path, monkeypatch, plan_text, changed):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "plan.txt").write_text(plan_text, encoding="utf-8")
    monkeypatch.setenv("STACK_NAME", "stacks/<app>")
    monkeypatch.setenv("ENV", "dev-eu")
    monkeypatch.setenv("CHANGED", changed)
    return pcs.step_line()


def test_step_line_names_an_import_only_plan_by_its_tally(tmp_path, monkeypatch):
    """Mutation: format the line locally (`f"{sc.emoji(cell)} {stack} ({env}): ..."`) instead of
    through `cell_line`, which leaves the stack name unescaped."""
    plan = (FIXTURES / "import-only.plan.txt").read_text(encoding="utf-8")
    line = _step_line(tmp_path, monkeypatch, plan, "true")
    assert line == "🟡 stacks/&lt;app&gt; (dev-eu): +0 ~0 -0, 1 import"


def test_step_line_names_an_unchanged_plan(tmp_path, monkeypatch):
    """Mutation: format the line locally instead of through `cell_line`."""
    plan = "No changes. Your infrastructure matches the configuration.\n"
    line = _step_line(tmp_path, monkeypatch, plan, "false")
    assert line == "🟢 stacks/&lt;app&gt; (dev-eu): no changes"


def test_step_line_of_a_changed_plan_without_a_tally_is_underivable(tmp_path, monkeypatch):
    """Mutation: fall back to `(0,) * 5` instead of `("?",) * 5` (renders `+0 ~0 -0`)."""
    line = _step_line(tmp_path, monkeypatch, "Terraform will perform actions\n", "true")
    assert line == "🟡 stacks/&lt;app&gt; (dev-eu): +? ~? -?"


def test_any_other_argument_is_refused_before_cell_json_is_written(tmp_path):
    """A mistyped flag must not fall through to writing cell.json. Mutation: drop the `elif args`
    refusal, so any argument runs `main()`."""
    (tmp_path / "fingerprint.txt").write_text("fp\n", encoding="utf-8")
    env = {
        **os.environ,
        "STACK_NAME": "app",
        "STACK": "stacks/app",
        "ENV": "dev",
        "CHANGED": "true",
    }
    script = pathlib.Path(__file__).resolve().parents[1] / "plan-cell-summary"
    r = subprocess.run(
        [sys.executable, str(script), "--steps-line"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert r.returncode == 1
    assert r.stderr == (
        "::error::plan-cell-summary takes no argument or --step-line, got ['--steps-line']\n"
    )
    assert not (tmp_path / "cell.json").exists()


def test_step_summary_env_is_exactly_the_cell_names_and_changed():
    """Mutation: bind `CHANGED: ${{ steps.plan.outputs.change }}` (empty, so every changed cell
    reads 🟢 no changes)."""
    step = step_by("plan-cell", name="Step summary (plan text, 64KiB cap)")
    assert step["env"] == {
        "ENV": "${{ inputs.env }}",
        "STACK_NAME": "${{ inputs.stack }}",
        "CHANGED": "${{ steps.plan.outputs.changed }}",
    }


def _run_summary(tmp_path, python3_body, plan_text):
    """The `Step summary` body under GitHub's `bash -eo pipefail`, `python3` stubbed. The stub
    records its arguments in `argv.txt` before running `python3_body`."""
    step = step_by("plan-cell", name="Step summary (plan text, 64KiB cap)")
    record = "printf '%s\\n' \"$*\" > argv.txt"
    body = f"set -eo pipefail\npython3() {{ {record} ; {python3_body} ; }}\n" + step["run"]
    (tmp_path / "plan.txt").write_text(plan_text, encoding="utf-8", newline="\n")
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "STACK_NAME": "stacks/app",
        "ENV": "dev-eu",
        "GITHUB_ACTION_PATH": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    r = run_step(tmp_path, body, env)
    return r, summary.read_text(encoding="utf-8")


@bash_only
def test_step_summary_heads_the_plan_with_the_step_line_whole(tmp_path):
    """Mutations: write `## $STACK_NAME / $ENV` in place of `## $line`; drop `--step-line`; call
    `scripts/plan-cell-summaries` (a wrong path would fall back silently forever)."""
    r, summary = _run_summary(tmp_path, "echo '🟡 stacks/app (dev-eu): +1 ~0 -0'", "plan text\n")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert summary == "## 🟡 stacks/app (dev-eu): +1 ~0 -0\n```\nplan text\n\n```\n"
    argv = (tmp_path / "argv.txt").read_text(encoding="utf-8")
    assert argv == f"{tmp_path}/../../scripts/plan-cell-summary --step-line\n"


@bash_only
def test_a_failing_step_line_falls_back_and_a_long_plan_is_truncated_whole(tmp_path):
    """The heading is cosmetic, so a failure to compute it must not fail the plan. Mutations:
    drop `|| line="$STACK_NAME ($ENV)"` (the step fails); put the dash back in the truncation
    line."""
    plan = "x" * 65537
    r, summary = _run_summary(tmp_path, "return 1", plan)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert summary == (
        "## stacks/app (dev-eu)\n```\n"
        + "x" * 65536
        + "\n```\n_Plan truncated at 64 KiB (65537 bytes total); the full plan is in this job's"
        " raw log._\n"
    )
