"""plan-cell renders the text and JSON plans concurrently, and a failure of either fails the step.

Each render is awaited by its own `wait "$pid" || rc=1` before the step exits. A bare `wait`
returns 0 whatever the renders did, and a `wait "$pid"` without `|| rc=1` exits under errexit at
the first failure while the other render is still writing into the workspace.

The step body runs unmodified, with `tofu` and `python3` replaced by bash functions: bash resolves
a function before PATH, and a function behind `&` runs in a subshell like the binary would.
"""

import os

from _loader import bash_only, run_step, step_by

_TEXT = "plan text\n"
_JSON = '{"format_version":"1.2"}\n'

#: The `-json` arm sleeps before writing, so a step that returns without awaiting it leaves
#: plan.json incomplete. It drops its stderr first: an inherited pipe would hold subprocess.run
#: open until the sleep ends and hide an early return.
_STUBS = r"""
tofu() {
  case "$*" in
    *-no-color*) [ "$FAIL" = text ] && return 3 ; printf 'plan text\n' ;;
    *-json*) exec 2>/dev/null ; sleep 1 ; [ "$FAIL" = json ] && return 4 ;
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
