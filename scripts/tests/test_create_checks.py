"""`scripts/create-checks`: the summary's apply-check POSTs, retried through `rate_limited` with
`gate-status refuse-newer` before each retry. In process, because a child process would sleep
the real 60 s."""

import io
import json

import _shipmate
import pytest
from _loader import GATE_USER, load_script, run_lines, step_by

cc = load_script("create-checks")

SHA = "a" * 40
_POST = ["gh", "api", "repos/acme/demo/check-runs", "--input", "-"]
_USER = ["gh", "api", "users/shipmate-test%5Bbot%5D"]
_READ = [
    "gh",
    "api",
    "--paginate",
    f"repos/acme/demo/commits/{SHA}/statuses?per_page=100",
    "--jq",
    ".[] | {context, state, target_url, creator: .creator.id}",
]
#: Not captured: gh's `gh: <message> (HTTP <code>)` form around the probed API message.
_SECONDARY = "gh: You have exceeded a secondary rate limit. (HTTP 403)"
_NEWER_HOLD = json.dumps(
    {
        "context": "shipmate / gate",
        "state": "failure",
        "target_url": "https://example.invalid/acme/demo/actions/runs/50",
        "creator": 306306466,
    }
)


@pytest.fixture
def gh(monkeypatch):
    """Every gh call recorded as `(argv, stdin)` in one list, POSTs through `cc.run` and the
    `refuse-newer` reads through gate-status's `_run`. `fail` holds the stderr of each POST to
    fail, in order; `statuses` is what a statuses read answers; `slept` the sleeps."""
    for name, value in {
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_RUN_ID": "42",
        "SHIPMATE_APP_SLUG": "shipmate-test",
    }.items():
        monkeypatch.setenv(name, value)
    cc.gs.app_user.cache_clear()
    state = {"calls": [], "fail": [], "statuses": "", "slept": []}

    def post(args, secrets=(), stdin=None):
        state["calls"].append((args, stdin))
        if state["fail"]:
            raise SystemExit(f"command failed (1): {' '.join(args)}\n{state['fail'].pop(0)}")
        return "{}"

    def read(args):
        state["calls"].append((args, None))
        return GATE_USER if args == _USER else state["statuses"]

    monkeypatch.setattr(cc, "run", post)
    monkeypatch.setattr(cc.gs, "_run", read)
    monkeypatch.setattr(_shipmate, "_sleep", state["slept"].append)
    return state


def _main(monkeypatch, bodies):
    monkeypatch.setattr(cc.sys, "stdin", io.StringIO(bodies))
    cc.main([SHA])


def test_each_body_is_posted_in_order_with_blank_lines_skipped(monkeypatch, gh):
    """Mutation: drop `stdin=body` -> `stdin` is None."""
    _main(monkeypatch, '{"name": "a"}\n\n{"name": "b"}\n')
    assert gh["calls"] == [(_POST, '{"name": "a"}'), (_POST, '{"name": "b"}')]


def test_a_secondary_rate_limit_runs_refuse_newer_then_posts_again(monkeypatch, capsys, gh):
    """Mutations, each run: POST through `run` alone (no `rate_limited`) -> the 403 raises; drop
    `before_retry=` -> no statuses read before the second POST."""
    gh["fail"].append(_SECONDARY)
    _main(monkeypatch, '{"name": "a"}\n')
    assert (gh["calls"], gh["slept"]) == (
        [(_POST, '{"name": "a"}'), (_USER, None), (_READ, None), (_POST, '{"name": "a"}')],
        [60],
    )
    assert capsys.readouterr().err == (
        "::notice::waiting 60 s for GitHub's secondary rate limit, then retrying: "
        "command failed (1): gh api repos/acme/demo/check-runs --input -\n"
    )


def test_a_newer_hold_after_the_wait_posts_nothing_more(monkeypatch, gh):
    """Mutation: drop `before_retry=` -> the second POST lands over the newer run's hold."""
    gh["fail"].append(_SECONDARY)
    gh["statuses"] = _NEWER_HOLD + "\n"
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, '{"name": "a"}\n{"name": "b"}\n')
    assert (exc.value.code, gh["calls"]) == (
        cc.gs.NEWER,
        [(_POST, '{"name": "a"}'), (_USER, None), (_READ, None)],
    )


def test_a_failed_post_stops_the_loop(monkeypatch, gh):
    """Fail closed: the gate's work queue is the pending checks. Mutation: catch the failure and
    continue -> the second body is posted."""
    gh["fail"].append("gh: Validation Failed (HTTP 422)")
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, '{"name": "a"}\n{"name": "b"}\n')
    assert exc.value.code == (
        "command failed (1): gh api repos/acme/demo/check-runs --input -\n"
        "gh: Validation Failed (HTTP 422)"
    )
    assert gh["calls"] == [(_POST, '{"name": "a"}')]


def test_a_malformed_sha_is_refused_before_any_call(monkeypatch, gh):
    """Mutation: drop `validate_head_sha` -> the body is posted."""
    monkeypatch.setattr(cc.sys, "stdin", io.StringIO('{"name": "a"}\n'))
    with pytest.raises(SystemExit) as exc:
        cc.main(["../x"])
    assert (exc.value.code, gh["calls"]) == (
        "::error::head SHA must be a 40-char lowercase hex SHA (got: '../x')",
        [],
    )


def test_the_summary_publishes_apply_checks_through_create_checks():
    """Mutation: restore the bash `gh api` POST loop -> the lines differ."""
    step = step_by("summary", name="Create apply checks (pending / no-changes)")
    script = '"$GITHUB_ACTION_PATH/../../scripts'
    assert run_lines(step) == [
        "set -euo pipefail",
        "[ -s bodies.jsonl ] || exit 0",
        f'python3 {script}/gate-status" refuse-newer "$SHIPMATE_HEAD_SHA" || exit 1',
        f'python3 {script}/create-checks" "$SHIPMATE_HEAD_SHA" < bodies.jsonl',
    ]
