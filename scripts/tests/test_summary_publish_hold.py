"""The summary job holds the gate before it publishes any apply check.

`gate-refresh` greens an absent or `pending` gate once every published apply check is complete,
and `deploy-detect`'s queue holds only published checks. A summary job that dies between its first
apply-check POST and `Create/refresh gate` would therefore let an apply of the published cells
green the gate and merge the unpublished ones. `Hold the gate while apply checks publish` writes a
`failure` gate first, which `gate-refresh` refuses to green.

Both steps run for real, in order, with `gh` and `python3` (standing in for `pending-checks`)
replaced by bash functions, as in `test_summary_draft_gate.py`. A failed step ends the sequence,
as it skips the later default-`success()` steps on a runner.
"""

import os

from _loader import bash_only, run_step, step_by

HEAD_SHA = "a" * 40
HOLD = "Hold the gate while apply checks publish"
CREATE = "Create apply checks (pending / no-changes)"
GATE = "Create/refresh gate"
HOLD_ARGV = [
    "api",
    f"repos/acme/demo/statuses/{HEAD_SHA}",
    "-f",
    "state=failure",
    "-f",
    "context=shipmate / gate",
    "-f",
    "description=re-plan this pull request (comment shipmate plan or push): "
    "its plan summary did not finish",
    "-f",
    "target_url=https://github.com/acme/demo/actions/runs/42",
]
BODIES = [
    '{"name": "apply / a / dev"}',
    '{"name": "apply / b / dev"}',
    '{"name": "apply / c / dev"}',
]

# Records every call's argv one line per argument, then the stdin of an `--input -` call, then a
# `--` line. FAKE_FAIL_CHECK fails that check-runs call (1-based); FAKE_STATUS_RC is the status
# write's exit code. A gate read answers `failure`, the hold. Pipeline members run in
# subshells, so the check counter lives in a file.
GH_STUB = r"""
python3() { printf '%s' "$FAKE_BODIES"; }
gh() {
  printf '%s\n' "$@" >> "$CALLS"
  if [ "${*: -1}" = "-" ]; then cat >> "$CALLS"; printf '\n' >> "$CALLS"; fi
  printf -- '--\n' >> "$CALLS"
  case "$*" in
    "api repos/acme/demo/statuses/$SHIPMATE_HEAD_SHA "*) return "$FAKE_STATUS_RC" ;;
    "api --paginate repos/acme/demo/commits/$SHIPMATE_HEAD_SHA/status?per_page=100 --jq "*)
      printf 'failure' ;;
    "api repos/acme/demo/check-runs --input -")
      n=$(( $(cat "$COUNT" 2>/dev/null || echo 0) + 1 ))
      echo "$n" > "$COUNT"
      [ "$n" != "$FAKE_FAIL_CHECK" ] ;;
    *) printf 'unexpected gh call: %s\n' "$*" >&2 ; return 1 ;;
  esac
}
"""


def _check(body):
    return ["api", "repos/acme/demo/check-runs", "--input", "-", body]


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


def _run(tmp_path, bodies, *, fail_check=0, status_rc=0, steps=(HOLD, CREATE)):
    calls = tmp_path / "calls"
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "SHIPMATE_HEAD_SHA": HEAD_SHA,
        "HEAD_SHA": HEAD_SHA,
        "STATE": "pending",
        "MODE": "post",
        "PLAN_RESULT": "success",
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "42",
        "GITHUB_ACTION_PATH": str(tmp_path),
        "FAKE_BODIES": "".join(f"{b}\n" for b in bodies),
        "FAKE_FAIL_CHECK": str(fail_check),
        "FAKE_STATUS_RC": str(status_rc),
        "CALLS": str(calls),
        "COUNT": str(tmp_path / "count"),
    }
    codes = []
    for name in steps:
        proc = run_step(tmp_path, GH_STUB + step_by("summary", name=name)["run"], env)
        codes.append(proc.returncode)
        if proc.returncode != 0:
            break
    return codes, _calls(calls)


@bash_only
def test_the_hold_is_written_before_any_apply_check(tmp_path):
    """Mutations: move the hold's `if` block to the end of `Create apply checks` -> the hold
    follows the checks; `state=failure` -> `state=pending` -> the argv differs."""
    codes, calls = _run(tmp_path, BODIES[:2])
    assert codes == [0, 0]
    assert calls == [HOLD_ARGV, _check(BODIES[0]), _check(BODIES[1])]


@bash_only
def test_a_failed_check_post_fails_the_step_and_stops_publishing(tmp_path):
    """Mutation: append `|| true` to the check-runs POST -> exit 0 and the third check posts."""
    codes, calls = _run(tmp_path, BODIES, fail_check=2)
    assert codes == [0, 1]
    assert calls == [HOLD_ARGV, _check(BODIES[0]), _check(BODIES[1])]


@bash_only
def test_no_body_writes_no_hold(tmp_path):
    """Mutation: replace `[ -s bodies.jsonl ]` with `true` -> the hold is written."""
    codes, calls = _run(tmp_path, [])
    assert codes == [0, 0]
    assert calls == []


@bash_only
def test_a_failed_hold_write_publishes_no_check(tmp_path):
    """Mutation: append `|| true` to the status write -> the hold step passes and the checks
    post over no hold."""
    codes, calls = _run(tmp_path, BODIES[:2], status_rc=1)
    assert codes == [1]
    assert calls == [HOLD_ARGV]


@bash_only
def test_the_final_gate_write_replaces_the_hold(tmp_path):
    """Mutation: run `Create/refresh gate`'s held-read for every mode (`[ "$MODE" = "draft" ]` ->
    `true`) -> it reads the hold, keeps it, and never writes the final gate."""
    (tmp_path / "gate.json").write_text("{}", encoding="utf-8")
    codes, calls = _run(tmp_path, BODIES[:1], steps=(HOLD, CREATE, GATE))
    assert codes == [0, 0, 0]
    assert calls == [
        HOLD_ARGV,
        _check(BODIES[0]),
        ["api", f"repos/acme/demo/statuses/{HEAD_SHA}", "--input", "gate.json"],
    ]
