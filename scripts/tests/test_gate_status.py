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

import _shipmate
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


@pytest.mark.parametrize("level", ["skipped", "success", "failure"])
@pytest.mark.parametrize("detect", ["failure", "cancelled"])
def test_a_deploy_stopped_at_detect_says_so(detect, level):
    """Any detect result but `success` names itself in the description, whatever the env-level
    results.

    Mutations: drop the `detect != "success"` branch -> "deploy incomplete"; add
    `and "success" not in results.split(",")[1:]` to it -> the `success` rows say "deploy
    incomplete"."""
    assert gs.deploy_verdict(detect, f"{detect},{level},{level},{level},{level}") == (
        "failure",
        f"deploy stopped at detect ({detect}), nothing applied; see the run",
    )


@pytest.mark.parametrize("results", ["", "success,success", "failure,skipped"])
def test_an_empty_detect_result_names_the_missing_input(results):
    """Mutation: drop the `not detect` branch -> "deploy stopped at detect ()"."""
    assert gs.deploy_verdict("", results) == (
        "failure",
        "deploy gate got no detect result; see the run",
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
            f"::error::head SHA must be a 40-char lowercase hex SHA (got: {'A' * 40!r})\n",
        ),
        (
            ("refresh", SHA[:39]),
            f"::error::head SHA must be a 40-char lowercase hex SHA (got: {SHA[:39]!r})\n",
        ),
    ],
)
def test_a_bad_sha_is_refused_before_any_call(tmp_path, args, error):
    """Mutation: drop the `validate_head_sha` call in `main` -> the stub records a call."""
    proc, calls = _run(tmp_path, *args)
    assert (proc.returncode, proc.stderr, calls) == (1, error, [])


def _run_url(run):
    return f"https://example.invalid/acme/demo/actions/runs/{run}"


def _newer(run):
    return (
        f"::error::plan run {run} holds the gate of {SHA} and is newer than this run (999); this "
        "run stops without writing its final gate. Re-plan this pull request (comment shipmate "
        "plan or push) if that run did not finish.\n"
    )


@bash_only
@pytest.mark.parametrize(
    ("page1", "page2", "newer"),
    [
        # This run's own hold (999, the retry after a failed read) on top of a newer run's earlier
        # hold on page 2.
        (
            _lines(_status("shipmate / gate", "failure")),
            _lines(_status("shipmate / gate", "failure", _run_url(1000))),
            1000,
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
            1200,
        ),
        # Run ids of different lengths: 1000 is newer than 999 only as an integer.
        (
            _lines(
                _status("shipmate / gate", "failure", _run_url(1000)),
                _status("shipmate / gate", "failure", _run_url(99)),
            ),
            "",
            1000,
        ),
        (
            _lines(
                _status("shipmate / gate", "failure", _run_url(998)),
                _status("shipmate / gate", "failure", "https://host1.invalid/a/b/pull/7000"),
                _status("shipmate / gate", "pending", _run_url(1000)),
            ),
            "",
            None,
        ),
        ("", "", None),
    ],
    ids=["behind-own-hold", "highest-hold", "integer-compare", "none-newer", "no-statuses"],
)
def test_refuse_newer_names_the_newest_hold_written_after_this_run(tmp_path, page1, page2, newer):
    """A newer hold exits `NEWER` with the whole refusal on stderr; none exits 0 silently.

    Mutations: read only the newest gate (`_gates(sha)[:1]`) -> the first case exits 0; compare
    run ids as strings -> the third case exits 0 (`"1000" > "999"` is false); drop the
    `state == "failure"` filter -> the second case names 5000; `max` -> `min` -> 1001; exit 1
    instead of `NEWER` -> the exit code differs; print the refusal to stdout -> stdout and stderr
    differ."""
    proc, calls = _run(tmp_path, "refuse-newer", SHA, page1=page1, page2=page2)
    expected = (4, "", _newer(newer)) if newer else (0, "", "")
    assert (proc.returncode, proc.stdout, proc.stderr) == expected
    assert calls == [STATUSES_ARGV]


@bash_only
def test_refuse_newer_fails_when_the_statuses_listing_fails(tmp_path):
    """Mutations: answer an empty listing on a failed read -> exit 0 and the caller writes; read
    the statuses through the unprefixed `run` -> stderr loses its `::error::`."""
    proc, calls = _run(
        tmp_path,
        "refuse-newer",
        SHA,
        page1=_lines(_status("shipmate / gate", "failure", _run_url(1000))),
        statuses_rc=1,
    )
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        1,
        "",
        f"::error::command failed (1): gh {' '.join(STATUSES_ARGV)}\n",
        [STATUSES_ARGV],
    )


def _checks_argv(jq):
    return [
        "api",
        "--paginate",
        f"repos/acme/demo/commits/{SHA}/check-runs?filter=all&per_page=100",
        "--jq",
        jq,
    ]


@bash_only
def test_checks_writes_the_listing_bytes_as_utf8_with_lf(tmp_path):
    """Bash sites redirect this stdout into a file they parse, and check names are
    consumer-controlled, so stdout must be UTF-8 with LF line ends and nothing appended.

    Mutations: write with `print(out, end="")` -> red on Windows only, where `\n` becomes `\r\n`
    and the code page re-encodes (on Linux the bytes match); append a `print()` -> a trailing
    newline."""
    listing = _lines(
        json.dumps({"name": "apply / é / dev — x"}, ensure_ascii=False), json.dumps({"name": "b"})
    )
    proc, calls = _run(
        tmp_path,
        "checks",
        SHA,
        ".check_runs[]",
        checks=listing,
        checks_jq=".check_runs[]",
        text=False,
    )
    assert (proc.returncode, proc.stdout) == (0, listing.encode("utf-8")), proc.stderr
    assert calls == [_checks_argv(".check_runs[]")]


@bash_only
def test_checks_sends_the_projection_it_is_given(tmp_path):
    """Mutation: ignore the argument and send `CHECK_RUNS_JQ` -> the stub refuses the call."""
    jq = ".check_runs[] | {name, status}"
    proc, calls = _run(tmp_path, "checks", SHA, jq, checks="{}\n", checks_jq=jq)
    assert (proc.returncode, proc.stdout) == (0, "{}\n"), proc.stderr
    assert calls == [_checks_argv(jq)]


@bash_only
def test_checks_fails_unannotated_when_the_listing_fails(tmp_path):
    """Degrading sites warn and continue, so the listing's failure must not annotate their green
    step. Mutations: catch the `SystemExit` and return -> exit 0; call `check_runs` without
    `run=run` -> the default `_run` prefixes `::error::`."""
    proc, calls = _run(
        tmp_path,
        "checks",
        SHA,
        ".check_runs[]",
        checks="{}\n",
        checks_rc=1,
        checks_jq=".check_runs[]",
    )
    assert (proc.returncode, proc.stdout) == (1, "")
    assert proc.stderr.startswith("command failed (1): gh api --paginate ")
    assert not [ln for ln in proc.stderr.splitlines() if ln.startswith("::error::")]
    assert calls == [_checks_argv(".check_runs[]")]


@bash_only
def test_checks_refuses_a_malformed_sha_unannotated(tmp_path):
    """A degrading site's warning must stay the only annotation on its passing step, a malformed
    head included. Mutation: drop the `checks` branch in `main` -> `validate_head_sha`'s
    `::error::` line."""
    proc, calls = _run(tmp_path, "checks", "A" * 40, ".check_runs[]", checks_jq=".check_runs[]")
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        1,
        "",
        f"head SHA must be a 40-char lowercase hex SHA (got: {'A' * 40!r})\n",
        [],
    )


@bash_only
def test_a_multi_line_failure_prints_as_one_escaped_annotation(tmp_path):
    """A failed `gh` call's stderr sits on the lines under the `::error::` line; printed raw, the
    runner annotates only the first. The stub answers another head, so the statuses read fails
    with its `unexpected gh call` line. Mutation: revert the `__main__` block to
    `main(sys.argv[1:])` -- two raw lines."""
    cmd = f'python3 "{(SCRIPTS / "gate-status").as_posix()}" refuse-newer {SHA}'
    proc, calls = run_with_gate_gh(tmp_path, cmd, ENV, sha="b" * 40)
    argv = " ".join(STATUSES_ARGV)
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        1,
        "",
        f"::error::command failed (1): gh {argv}%0Aunexpected gh call: {argv}\n",
        [STATUSES_ARGV],
    )


_BAD_GATEWAY = "command failed (1): gh api x\ngh: Bad Gateway (HTTP 502)"
_RETRY_WARNING = "::warning::command failed (1): gh api x%0Agh: Bad Gateway (HTTP 502)\n"


@pytest.fixture
def in_process(monkeypatch):
    """`gate-status` called in this process: its env set and every retry sleep recorded."""
    for name in ("GITHUB_REPOSITORY", "GITHUB_SERVER_URL", "GITHUB_RUN_ID"):
        monkeypatch.setenv(name, ENV[name])
    slept = []
    monkeypatch.setattr(_shipmate, "_sleep", slept.append)
    return slept


def test_checks_keeps_a_retry_warning_off_its_stdout(in_process, monkeypatch, capsys):
    """Stdout is the JSONL listing `apply-gate.parse_jsonl` reads, so a stray line fails it.
    Mutation: write `warn_retry`'s line to stdout -> the warning leads the listing."""
    calls = []

    def run(args):
        calls.append(args)
        if len(calls) == 1:
            raise SystemExit(_BAD_GATEWAY)
        return "{}\n"

    monkeypatch.setattr(gs, "run", run)
    gs.main(["checks", SHA, ".check_runs[]"])
    assert capsys.readouterr() == ("{}\n", _RETRY_WARNING)
    assert (len(calls), in_process) == (2, [2])


_POST = ["gh", *_status_argv("pending", "the reason")]
_READ = ["gh", *STATUSES_ARGV]


def _flaky_run(monkeypatch, statuses):
    """`gs._run` failing the first call of each argv with a 502 and answering a statuses read
    with the next of `statuses`, each a gate `(state, run id)`. Returns the calls."""
    calls = []

    def run(args):
        calls.append(args)
        if calls.count(args) == 1:
            raise SystemExit(f"::error::{_BAD_GATEWAY}")
        if args == _READ:
            state, run_id = statuses.pop(0)
            return _lines(_status("shipmate / gate", state, _run_url(run_id)))
        return "{}"

    monkeypatch.setattr(gs, "_run", run)
    return calls


def test_post_retries_a_transient_failure(in_process, monkeypatch, capsys):
    """Mutation: `_run(args)` without `retried` in `post` -> the 502 raises."""
    calls = _flaky_run(monkeypatch, [])
    gs.post(SHA, "pending", "the reason")
    assert (calls, in_process, capsys.readouterr()) == ([_POST, _POST], [2], ("", _RETRY_WARNING))


def test_gates_retries_a_transient_failure(in_process, monkeypatch):
    """Mutation: `_run(args)` without `retried` in `_gates` -> the 502 raises."""
    calls = _flaky_run(monkeypatch, [("failure", "42")])
    assert (gs.newest_gate(SHA), calls, in_process) == (("failure", "42"), [_READ, _READ], [2])


def test_refresh_stops_when_a_hold_lands_while_its_write_retries(in_process, monkeypatch, capsys):
    """Read pending, the green POST 502s, the re-read finds a hold. Mutation: drop `refresh`'s
    `before_retry` -> the second POST greens over the hold."""
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)
    monkeypatch.setattr(gs, "check_runs", lambda *_: DONE)
    calls = []

    def run(args):
        calls.append(args)
        if args == _READ:
            state = "pending" if len(calls) == 1 else "failure"
            return _lines(_status("shipmate / gate", state, _run_url(42)))
        if calls.count(args) == 1:
            raise SystemExit(f"::error::{_BAD_GATEWAY}")
        return "{}"

    monkeypatch.setattr(gs, "_run", run)
    green = ["gh", *GREEN]
    with pytest.raises(SystemExit) as exc:
        gs.refresh(SHA)
    assert (exc.value.code, calls, capsys.readouterr()) == (
        gs.HELD,
        [_READ, green, _READ],
        (HELD, _RETRY_WARNING),
    )
