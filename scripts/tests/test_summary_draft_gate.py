"""A draft autoplan's gate is written only over a head that carries none.

gate-state decides a draft autoplan `pending` with comment mode `draft`. `gate-refresh` treats
only `failure` as a hold and greens `pending` once every apply check is done, so a draft status
written over a held head -- a re-run of an old draft run, or a draft reopened at the same head --
would turn the hold into something `gate-refresh` can green. `Create/refresh gate` therefore reads
the head's gate first, with `gate-refresh`'s own read, and writes the draft status only when that
read succeeds and finds none. Every other mode writes as before, without the read.

The upsert step leaves the sticky comment as it is on a draft run and makes no comment API call.

Both steps run for real: `Create/refresh gate` with `gh` replaced by a bash function, as in
`test_gate_refresh_hold.py`; the upsert step with a stub `gh` on PATH.
"""

import os
import re

import pytest
from _loader import bash_only, run_step, step_by

HEAD_SHA = "a" * 40
READ_ARGV = [
    "api",
    "--paginate",
    f"repos/acme/demo/commits/{HEAD_SHA}/status?per_page=100",
    "--jq",
    '[.statuses[] | select(.context == "shipmate / gate")] | .[0].state // empty',
]
WRITE_ARGV = ["api", f"repos/acme/demo/statuses/{HEAD_SHA}", "--input", "gate.json"]

# Records every call's argv, one line per argument and a `--` line after each call, then answers
# the gate read with FAKE_GATE_STATE, or fails it when FAKE_READ_FAILS is set.
GH_STUB = """
gh() {
  printf '%s\\n' "$@" -- >> "$CALLS"
  case "$*" in
    "api --paginate repos/acme/demo/commits/$HEAD_SHA/status?per_page=100 --jq "*)
      [ -z "${FAKE_READ_FAILS:-}" ] || return 1
      printf '%s' "$FAKE_GATE_STATE" ;;
    *"/statuses/"*) ;;
    *) printf 'unexpected gh call: %s\\n' "$*" >&2 ; return 1 ;;
  esac
}
"""


def _calls(path):
    if not path.exists():
        return []
    calls, current = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "--":
            calls.append(current)
            current = []
        else:
            current.append(line)
    return calls


def _run_gate(tmp_path, mode, head_state, *, read_fails=False):
    calls = tmp_path / "calls"
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "HEAD_SHA": HEAD_SHA,
        "STATE": "pending",
        "MODE": mode,
        "PLAN_RESULT": "skipped",
        "GITHUB_REPOSITORY": "acme/demo",
        "FAKE_GATE_STATE": head_state,
        "CALLS": str(calls),
    }
    if read_fails:
        env["FAKE_READ_FAILS"] = "1"
    proc = run_step(tmp_path, GH_STUB + step_by("summary", name="Create/refresh gate")["run"], env)
    return proc, _calls(calls)


@bash_only
@pytest.mark.parametrize("head_state", ["failure", "pending", "success"])
def test_a_draft_run_keeps_an_existing_gate(tmp_path, head_state):
    """Mutation: delete the read -> the draft status overwrites the `failure` hold."""
    proc, calls = _run_gate(tmp_path, "draft", head_state)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [READ_ARGV]
    assert f"keeps its {head_state} gate status" in proc.stdout


@bash_only
def test_a_draft_run_writes_the_gate_on_a_head_with_none(tmp_path):
    """Mutation: invert the emptiness test (`-n` -> `-z`) -> no write."""
    proc, calls = _run_gate(tmp_path, "draft", "")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [READ_ARGV, WRITE_ARGV]


@bash_only
def test_a_failed_read_writes_nothing_and_warns(tmp_path):
    """Mutation: drop the `if !` around the read -> `set -e` fails the step."""
    proc, calls = _run_gate(tmp_path, "draft", "", read_fails=True)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [READ_ARGV]
    assert "::warning::" in proc.stdout


@bash_only
def test_a_post_run_writes_without_reading(tmp_path):
    """Mutation: key the read on `post` instead of `draft` -> a read, and no write."""
    proc, calls = _run_gate(tmp_path, "post", "failure")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [WRITE_ARGV]


_GATE_READ = re.compile(
    r"""gh api [^"\n]*"repos/\$GITHUB_REPOSITORY/commits/\$HEAD_SHA/status[^"]*" """
    r"""\\\n\s*--jq '[^']*'"""
)


def _gate_read(action, step):
    reads = _GATE_READ.findall(step_by(action, name=step)["run"])
    assert len(reads) == 1, f"{action} / {step}: {reads!r}"
    return re.sub(r"\\\n\s*", "", reads[0])


def test_the_draft_read_is_gate_refreshs_own():
    """Both sites decide "is this head held"; if they read differently, one greens or overwrites
    a hold the other would have seen. Mutation: change one site's `per_page`.
    """
    assert _gate_read("summary", "Create/refresh gate") == _gate_read(
        "gate-refresh", "Complete gate"
    )


def _run_upsert(tmp_path, mode):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    calls = tmp_path / "gh-calls"
    stub = stubs / "gh"
    stub.write_text(f'#!/bin/sh\necho "$*" >> "{calls.as_posix()}"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o755)
    proc = run_step(
        tmp_path,
        step_by("summary", name="Upsert sticky comment")["run"],
        {
            **os.environ,
            "PATH": f"{stubs.as_posix()}:/usr/bin:/bin",
            "GH_TOKEN": "x",
            "PR": "7",
            "MODE": mode,
            "PLAN_RESULT": "skipped",
            "GITHUB_REPOSITORY": "acme/demo",
        },
    )
    made = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return proc, made


@bash_only
def test_a_draft_run_makes_no_comment_call(tmp_path):
    """Mutation: delete the upsert step's `draft` branch -> the stub records the listing call."""
    proc, made = _run_upsert(tmp_path, "draft")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert made == []
    assert "::warning::" not in proc.stdout


@bash_only
def test_the_upsert_stub_records_a_post_run(tmp_path):
    """Control for the draft case: the same stub sees a `post` run's listing and create calls, so
    an empty record means the step made no call rather than that the stub was never reached."""
    proc, made = _run_upsert(tmp_path, "post")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert len(made) == 2
    assert made[0].startswith("api repos/acme/demo/issues/7/comments --paginate")
    assert made[1] == "api repos/acme/demo/issues/7/comments -F body=@comment.md"
