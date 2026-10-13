"""`scripts/gate-status`: the one reader and writer of the `shipmate / gate` commit status.

The gate is a commit status, not a check-run: a check-run binds to a check-suite, and an
imperatively created one lands in an arbitrary suite when a commit carries two plan runs (draft
to ready, or a rapid re-push), where the merge evaluator never finds it and blocks the pull
request forever. Every write goes through `post`, and `test_post_and_hold_send_this_whole_argv`
and the refresh tests pin its whole argv, the statuses endpoint included.

The CLI runs for real through `_loader.write_python3_shim` and a bash `gh` keyed on the whole
argv of every call it accepts.
"""

import json
import os

import pytest
from _loader import SCRIPTS, bash_only, load_script, run_with_gate_gh

gs = load_script("gate-status")

SHA = "a" * 40
APP_ID = "4242"
RUN_URL = "https://example.invalid/acme/demo/actions/runs/999"
ENV = {
    **os.environ,
    "GH_TOKEN": "x",
    "GITHUB_REPOSITORY": "acme/demo",
    "GITHUB_SERVER_URL": "https://example.invalid",
    "GITHUB_RUN_ID": "999",
    # A different SHA, so posting to the merge commit instead of the head reddens.
    "GITHUB_SHA": "b" * 40,
    "SHIPMATE_APP_ID": APP_ID,
}
HELD = (
    f"shipmate / gate is held for {SHA}; its status description on the pull request "
    "names the cause. A hold clears only when a new plan run rewrites the gate: comment "
    "shipmate plan, or push.\n"
)


def _status_argv(state, description):
    """The whole POST argv, hand-written with the context literal: a value read back from the
    script passes whatever the script says."""
    return [
        "api",
        f"repos/acme/demo/statuses/{SHA}",
        "-f",
        f"state={state}",
        "-f",
        "context=shipmate / gate",
        "-f",
        f"description={description}",
        "-f",
        f"target_url={RUN_URL}",
    ]


CHECKS_ARGV = [
    "api",
    "--paginate",
    f"repos/acme/demo/commits/{SHA}/check-runs?filter=all&per_page=100",
    "--jq",
    ".check_runs[] | {name, status, conclusion, started_at, id, app: {id: .app.id}}",
]
STATUSES_ARGV = [
    "api",
    "--paginate",
    f"repos/acme/demo/commits/{SHA}/statuses?per_page=100",
    "--jq",
    ".[] | {context, state, target_url}",
]
GREEN = _status_argv("success", "all applies complete, nothing left to apply")


def _check(cid, status, conclusion=None, name="apply / a / dev"):
    return json.dumps(
        {"name": name, "status": status, "conclusion": conclusion, "id": cid, "app": {"id": 4242}}
    )


def _status(context, state, url=RUN_URL):
    return json.dumps({"context": context, "state": state, "target_url": url})


def _lines(*items):
    return "".join(f"{i}\n" for i in items)


DONE = _lines(_check(1, "completed", "success"), _check(2, "completed", "neutral", "apply / b / x"))


def _run(tmp_path, *args, **fakes):
    cmd = " ".join(f"'{a}'" for a in args)
    return run_with_gate_gh(
        tmp_path, f'python3 "{(SCRIPTS / "gate-status").as_posix()}" {cmd}', ENV, sha=SHA, **fakes
    )


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ("success", "success"),
        ("success,skipped", "success"),
        ("skipped,skipped,skipped,skipped", "success"),
        ("success,skipped,skipped,skipped,skipped", "success"),
        ("success,failure", "failure"),
        ("failure,success", "failure"),
        ("success,failure,skipped,skipped,skipped", "failure"),
        ("success,cancelled", "failure"),
        ("cancelled", "failure"),
        ("skipped,timed_out", "failure"),
        ("success,skipped,neutral", "failure"),
        # No results at all: nothing was applied, so the gate must not green.
        ("", "failure"),
    ],
)
def test_deploy_verdict(results, expected):
    """Mutation: drop empty tokens (`if t`) from the split -> `""` reads as success."""
    assert (
        gs.deploy_verdict("success", results)
        == {
            "success": ("success", "all env-levels applied (deploy env-level applies completed)"),
            "failure": ("failure", "deploy incomplete (deploy env-level applies completed)"),
        }[expected]
    )


@pytest.mark.parametrize("detect", ["failure", "cancelled"])
def test_a_deploy_stopped_at_detect_says_so(detect):
    """Any detect result but `success` names itself in the description, whatever the env-level
    results.

    Mutation: drop the `detect != "success"` branch -> "deploy incomplete"."""
    assert gs.deploy_verdict(detect, f"{detect},skipped,skipped,skipped,skipped") == (
        "failure",
        f"deploy stopped at detect ({detect}), nothing applied; see the run",
    )


@bash_only
def test_refresh_greens_a_pending_gate_once_every_check_is_done(tmp_path):
    """Mutation: post to `GITHUB_SHA` instead of the head -> the POST argv differs."""
    proc, calls = _run(
        tmp_path, "refresh", SHA, checks=DONE, page1=_lines(_status("shipmate / gate", "pending"))
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [CHECKS_ARGV, STATUSES_ARGV, GREEN]


@bash_only
def test_refresh_refuses_to_green_a_held_gate(tmp_path):
    """The hold prints unannotated and exits `HELD`, so each caller decides whether it fails.
    Mutations: test `pending` instead of `failure` in `refresh` -> the hold is greened; prefix the
    text with `::error::` -> stdout differs; exit 1 -> the exit code differs."""
    proc, calls = _run(
        tmp_path, "refresh", SHA, checks=DONE, page1=_lines(_status("shipmate / gate", "failure"))
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (3, HELD, "")
    assert calls == [CHECKS_ARGV, STATUSES_ARGV]


@bash_only
def test_refresh_leaves_the_gate_while_a_check_is_open(tmp_path):
    """Mutation: ignore the verdict (`if False:`) -> the statuses are read and the gate greened."""
    checks = DONE + _lines(_check(3, "queued", name="apply / c / dev"))
    proc, calls = _run(tmp_path, "refresh", SHA, checks=checks)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [CHECKS_ARGV]
    assert (
        proc.stdout
        == f"apply checks on {SHA} are not all done (1 open); the gate is left as it is\n"
    )


@bash_only
def test_refresh_finds_a_hold_past_the_first_statuses_page(tmp_path):
    """Mutation: drop `--paginate` from the statuses listing -> the stub answers page 1 alone,
    which holds no gate, and the hold on page 2 is greened."""
    proc, calls = _run(
        tmp_path,
        "refresh",
        SHA,
        checks=DONE,
        page1=_lines(_status("ci / test", "success"), _status("ci / lint", "failure")),
        page2=_lines(_status("shipmate / gate", "failure")),
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (3, HELD, "")
    assert calls == [CHECKS_ARGV, STATUSES_ARGV]


@bash_only
def test_refresh_fails_when_the_check_listing_fails(tmp_path):
    """Mutation: swallow the check-runs listing's failure and exit 0 -> the run reads green over
    a gate nobody refreshed."""
    proc, calls = _run(tmp_path, "refresh", SHA, checks=DONE, checks_rc=1)
    assert proc.returncode == 1
    assert calls == [CHECKS_ARGV]


@bash_only
@pytest.mark.parametrize(
    ("page1", "printed"),
    [
        (_lines(_status("shipmate / gate", "failure")), "failure 999\n"),
        (
            _lines(
                _status("ci", "success", "https://example.invalid/acme/demo/actions/runs/7"),
                _status(
                    "shipmate / gate", "failure", "https://example.invalid/a/b/actions/runs/42"
                ),
                _status(
                    "shipmate / gate", "pending", "https://example.invalid/a/b/actions/runs/41"
                ),
            ),
            "failure 42\n",
        ),
        # A host holding digits and no run id: the run id is the URL's suffix or nothing.
        (
            _lines(_status("shipmate / gate", "pending", "https://host1.invalid/a/b/pull/7")),
            "pending -\n",
        ),
        (_lines(_status("ci", "success")), ""),
        ("", ""),
    ],
)
def test_newest_prints_the_newest_gate_state_and_run_id(tmp_path, page1, printed):
    """Mutation: take the first digits anywhere in `target_url` -> `pending 1`."""
    proc, calls = _run(tmp_path, "newest", SHA, page1=page1)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert (proc.stdout, calls) == (printed, [STATUSES_ARGV])


@bash_only
@pytest.mark.parametrize(
    ("args", "argv"),
    [
        (("post", SHA, "pending", "waiting"), _status_argv("pending", "waiting")),
        (("hold", SHA, "re-plan"), _status_argv("failure", "re-plan")),
        (("post", SHA, "success", "x" * 150), _status_argv("success", "x" * 140)),
        (
            ("deploy", SHA, "success", "success,skipped"),
            _status_argv("success", "all env-levels applied (deploy env-level applies completed)"),
        ),
        (
            ("deploy", SHA, "success", "success,cancelled"),
            _status_argv("failure", "deploy incomplete (deploy env-level applies completed)"),
        ),
    ],
)
def test_post_and_hold_send_this_whole_argv(tmp_path, args, argv):
    """Mutations: change `GATE_CONTEXT`; drop the `[:140]`; post `pending` in `hold`."""
    proc, calls = _run(tmp_path, *args)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert calls == [argv]


@bash_only
@pytest.mark.parametrize(
    ("args", "error"),
    [
        (
            ("deploy", "", "failure", "failure"),
            "::error::detect produced no head SHA; deploy cannot gate\n",
        ),
        (
            ("hold", "A" * 40, "x"),
            f"::error::head SHA must be 40 lowercase hex characters (got: {'A' * 40!r})\n",
        ),
        (
            ("refresh", SHA[:39]),
            f"::error::head SHA must be 40 lowercase hex characters (got: {SHA[:39]!r})\n",
        ),
    ],
)
def test_a_bad_sha_is_refused_before_any_call(tmp_path, args, error):
    """Mutation: drop the `_SHA.fullmatch` check -> the stub records a call."""
    proc, calls = _run(tmp_path, *args)
    assert (proc.returncode, proc.stderr, calls) == (1, error, [])


def _run_url(run):
    return f"https://example.invalid/acme/demo/actions/runs/{run}"


@bash_only
@pytest.mark.parametrize(
    ("page1", "page2", "printed"),
    [
        # This run's own hold (999, the retry after a failed read) on top of a newer run's earlier
        # hold on page 2.
        (
            _lines(_status("shipmate / gate", "failure")),
            _lines(_status("shipmate / gate", "failure", _run_url(1000))),
            "1000\n",
        ),
        # The highest newer hold wins, wherever it sits; a newer `pending` or `success` is no hold.
        (
            _lines(
                _status("shipmate / gate", "success", _run_url(5000)),
                _status("shipmate / gate", "failure", _run_url(1001)),
                _status("ci", "failure", _run_url(9000)),
                _status("shipmate / gate", "failure", _run_url(1200)),
            ),
            "",
            "1200\n",
        ),
        # Run ids of different lengths: 1000 is newer than 999 only as an integer.
        (
            _lines(
                _status("shipmate / gate", "failure", _run_url(1000)),
                _status("shipmate / gate", "failure", _run_url(99)),
            ),
            "",
            "1000\n",
        ),
        (
            _lines(
                _status("shipmate / gate", "failure", _run_url(998)),
                _status("shipmate / gate", "failure", "https://host1.invalid/a/b/pull/7000"),
                _status("shipmate / gate", "pending", _run_url(1000)),
            ),
            "",
            "",
        ),
        ("", "", ""),
    ],
    ids=["behind-own-hold", "highest-hold", "integer-compare", "none-newer", "no-statuses"],
)
def test_newer_hold_prints_the_newest_hold_written_after_this_run(tmp_path, page1, page2, printed):
    """Mutations: read only the newest gate (`_gates(sha)[:1]`) -> the first case prints nothing;
    compare run ids as strings -> the third case prints nothing (`"1000" > "999"` is false); drop
    the `state == "failure"` filter -> the second case prints 5000; `max` -> `min` -> 1001."""
    proc, calls = _run(tmp_path, "newer-hold", SHA, page1=page1, page2=page2)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert (proc.stdout, calls) == (printed, [STATUSES_ARGV])


@bash_only
def test_newer_hold_fails_when_the_statuses_listing_fails(tmp_path):
    """Mutation: answer an empty listing on a failed read -> exit 0 and the summary publishes."""
    proc, calls = _run(
        tmp_path,
        "newer-hold",
        SHA,
        page1=_lines(_status("shipmate / gate", "failure", _run_url(1000))),
        statuses_rc=1,
    )
    assert (proc.returncode, proc.stdout, calls) == (1, "", [STATUSES_ARGV])
