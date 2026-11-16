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
import pathlib

import _shipmate
import pytest
from _detect_fixtures import autoplans_read, listed_autoplan
from _loader import GATE_USER, SCRIPTS, bash_only, load_script, run_with_gate_gh

gs = load_script("gate-status")

SHA = "a" * 40
APP_ID = "4242"
RUN_URL = "https://example.invalid/acme/demo/actions/runs/999"
#: The App's bot user id the gh fakes answer, unlike `APP_ID`, so an App-id comparison reddens.
BOT_ID = 306306466
ENV = {
    **os.environ,
    "GH_TOKEN": "x",
    "GITHUB_REPOSITORY": "acme/demo",
    "GITHUB_SERVER_URL": "https://example.invalid",
    "GITHUB_RUN_ID": "999",
    # A different SHA, so posting to the merge commit instead of the head reddens.
    "GITHUB_SHA": "b" * 40,
    "SHIPMATE_APP_ID": APP_ID,
    "SHIPMATE_APP_SLUG": "shipmate-test",
}
NO_SLUG = {k: v for k, v in ENV.items() if k != "SHIPMATE_APP_SLUG"}
HELD = (
    f"shipmate / gate is held for {SHA}; its status description on the pull request "
    "names the cause. A hold clears only when a new plan run rewrites the gate: comment "
    "shipmate plan, or push.\n"
)


@pytest.fixture(autouse=True)
def _fresh_app_user():
    """`app_user` caches for the process, which every in-process test here shares."""
    gs.app_user.cache_clear()


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
    ".[] | {context, state, target_url, creator: .creator.id}",
]
#: The App bot user lookup, hand-written: `[` and `]` percent-encoded in the path.
USER_ARGV = ["api", "users/shipmate-test%5Bbot%5D"]
GREEN = _status_argv("success", "all applies complete, nothing left to apply")


def _check(cid, status, conclusion=None, name="apply / a / dev"):
    return json.dumps(
        {"name": name, "status": status, "conclusion": conclusion, "id": cid, "app": {"id": 4242}}
    )


def _status(context, state, url=RUN_URL, creator=BOT_ID):
    return json.dumps({"context": context, "state": state, "target_url": url, "creator": creator})


def _lines(*items):
    return "".join(f"{i}\n" for i in items)


DONE = _lines(_check(1, "completed", "success"), _check(2, "completed", "neutral", "apply / b / x"))


def _run(tmp_path, *args, env=ENV, **fakes):
    cmd = " ".join(f"'{a}'" for a in args)
    return run_with_gate_gh(
        tmp_path, f'python3 "{(SCRIPTS / "gate-status").as_posix()}" {cmd}', env, sha=SHA, **fakes
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
    assert calls == [CHECKS_ARGV, USER_ARGV, STATUSES_ARGV, GREEN]


@bash_only
def test_refresh_refuses_to_green_a_held_gate(tmp_path):
    """The hold prints unannotated and exits `HELD`, so each caller decides whether it fails.
    Mutations: test `pending` instead of `failure` in `refresh` -> the hold is greened; prefix the
    text with `::error::` -> stdout differs; exit 1 -> the exit code differs."""
    proc, calls = _run(
        tmp_path, "refresh", SHA, checks=DONE, page1=_lines(_status("shipmate / gate", "failure"))
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (3, HELD, "")
    assert calls == [CHECKS_ARGV, USER_ARGV, STATUSES_ARGV]


@bash_only
def test_refresh_leaves_the_gate_while_a_check_is_open(tmp_path):
    """Mutations: ignore the verdict (`if False:`) -> the statuses are read and the gate greened;
    call `app_user()` at `refresh`'s entry -> a lookup precedes the listing."""
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
    assert calls == [CHECKS_ARGV, USER_ARGV, STATUSES_ARGV]


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
    assert (proc.stdout, calls) == (printed, [USER_ARGV, STATUSES_ARGV])


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
    """A write that succeeds first time reads no gate, so it makes no bot user lookup.
    Mutations: change `GATE_CONTEXT`; drop the `[:140]`; post `pending` in `hold`; call
    `app_user()` at the `post` command's entry -> a lookup precedes the POST."""
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
    assert calls == [USER_ARGV, STATUSES_ARGV]


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
        [USER_ARGV, STATUSES_ARGV],
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
    """Mutation: ignore the argument and send `.check_runs[]` -> the stub refuses the call."""
    jq = ".check_runs[] | {name, status}"
    proc, calls = _run(tmp_path, "checks", SHA, jq, checks="{}\n", checks_jq=jq)
    assert (proc.returncode, proc.stdout) == (0, "{}\n"), proc.stderr
    assert calls == [_checks_argv(jq)]


@bash_only
def test_checks_fails_unannotated_when_the_listing_fails(tmp_path):
    """Degrading sites warn and continue, so the listing's failure must not add a warning or error
    annotation to their green step. Mutations: catch the `SystemExit` and return -> exit 0; call
    `check_runs` without `run=run` -> the default `_run` prefixes `::error::`."""
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
        [USER_ARGV, STATUSES_ARGV],
    )


_BAD_GATEWAY = "command failed (1): gh api x\ngh: Bad Gateway (HTTP 502)"
_RETRY_NOTICE = "::notice::command failed (1): gh api x%0Agh: Bad Gateway (HTTP 502)\n"


@pytest.fixture
def in_process(monkeypatch):
    """`gate-status` called in this process: its env set and every retry sleep recorded."""
    for name in ("GITHUB_REPOSITORY", "GITHUB_SERVER_URL", "GITHUB_RUN_ID", "SHIPMATE_APP_SLUG"):
        monkeypatch.setenv(name, ENV[name])
    slept = []
    monkeypatch.setattr(_shipmate, "_sleep", slept.append)
    return slept


def test_checks_keeps_a_retry_notice_off_its_stdout(in_process, monkeypatch, capsys):
    """Stdout is the JSONL listing `apply-gate.parse_jsonl` reads, so a stray line fails it.
    Mutation: write `note_retry`'s line to stdout -> the notice leads the listing."""
    calls = []

    def run(args):
        calls.append(args)
        if len(calls) == 1:
            raise SystemExit(_BAD_GATEWAY)
        return "{}\n"

    monkeypatch.setattr(gs, "run", run)
    gs.main(["checks", SHA, ".check_runs[]"])
    assert capsys.readouterr() == ("{}\n", _RETRY_NOTICE)
    assert (len(calls), in_process) == (2, [2])


_POST = ["gh", *_status_argv("pending", "the reason")]
_READ = ["gh", *STATUSES_ARGV]
_USER = ["gh", *USER_ARGV]


def _flaky_run(monkeypatch, statuses):
    """`gs._run` answering the bot user lookup, failing the first call of each other argv with a
    502 and answering a statuses read with the next of `statuses`, each a gate `(state, run id)`.
    Returns the calls."""
    calls = []

    def run(args):
        calls.append(args)
        if args == _USER:
            return GATE_USER
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
    assert (calls, in_process, capsys.readouterr()) == ([_POST, _POST], [2], ("", _RETRY_NOTICE))


def test_gates_retries_a_transient_failure(in_process, monkeypatch):
    """Mutation: `_run(args)` without `retried` in `_gates` -> the 502 raises."""
    calls = _flaky_run(monkeypatch, [("failure", "42")])
    assert (gs.newest_gate(SHA), calls, in_process) == (
        ("failure", "42"),
        [_USER, _READ, _READ],
        [2],
    )


def test_refresh_stops_when_a_hold_lands_while_its_write_retries(in_process, monkeypatch, capsys):
    """Read pending, the green POST 502s, the re-read finds a hold. Mutation: drop `refresh`'s
    `before_retry` -> the second POST greens over the hold."""
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)
    monkeypatch.setattr(gs, "check_runs", lambda *_: DONE)
    calls = []

    def run(args):
        calls.append(args)
        if args == _USER:
            return GATE_USER
        if args == _READ:
            state = "pending" if calls.count(_READ) == 1 else "failure"
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
        [_USER, _READ, green, _READ],
        (HELD, _RETRY_NOTICE),
    )


def test_post_flattens_a_multi_line_description(in_process, monkeypatch):
    """Every line after the argv reads as gh's stderr to `is_transient`, so a description line
    reading `HTTP 503` would retry a 4xx. Mutation: post `description[:140]` -> the argv keeps
    the newline."""
    calls = []
    monkeypatch.setattr(gs, "_run", lambda args: calls.append(args) or "{}")
    gs.post(SHA, "failure", "held:\nHTTP 503  twice")
    assert calls == [["gh", *_status_argv("failure", "held: HTTP 503 twice")]]


def test_the_post_command_stops_when_a_newer_run_holds_before_its_retry(in_process, monkeypatch):
    """The summary's `post` runs after `refuse-newer`; a newer run's hold can land while the write
    retries. Mutation: map `post` to `post` with no `before_retry` -> the second POST lands."""
    calls = _flaky_run(monkeypatch, [("failure", "1000")])
    with pytest.raises(SystemExit) as exc:
        gs.main(["post", SHA, "pending", "the reason"])
    assert (exc.value.code, calls) == (gs.NEWER, [_POST, _USER, _READ, _READ])


def test_the_post_command_writes_again_when_no_newer_run_holds(in_process, monkeypatch):
    """An older run's hold is no newer hold. Mutation: map `post`'s `before_retry` to one that
    exits `NEWER` on any `failure` gate -> no second POST."""
    calls = _flaky_run(monkeypatch, [("failure", "1")])
    gs.main(["post", SHA, "pending", "the reason"])
    assert calls == [_POST, _USER, _READ, _READ, _POST]


#: The pull request's live head, and the whole argv of its one read.
_LIVE = "b" * 40
_PULL_READ = ["gh", "api", "repos/acme/demo/pulls/7"]
_RUNS_READ = autoplans_read("acme/demo", _LIVE)
_PULL_FAILED = "command failed (1): gh api repos/acme/demo/pulls/7 | HTTP 502 | ::add-mask::x"
_DRAFT_LOG = f"pull request #7 moved to {_LIVE}, a draft; nothing dispatched\n"


def _pull(sha=_LIVE, draft=False, state="open"):
    return json.dumps({"head": {"sha": sha}, "draft": draft, "state": state})


#: The live `workflow_dispatch` listing's one page per live status, hand-written.
_DISPATCH_READS = [
    [
        "gh",
        "api",
        (
            "repos/acme/demo/actions/workflows/shipmate.yml/runs"
            f"?event=workflow_dispatch&status={status}&per_page=100&page=1"
        ),
    ]
    for status in ("queued", "in_progress", "waiting", "pending", "requested")
]


def _jobs_read(run_id):
    return [
        "gh",
        "api",
        f"repos/acme/demo/actions/runs/{run_id}/jobs?filter=latest&per_page=100&page=1",
    ]


def _captured_jobs(kind):
    """`fixtures/jobs-<kind>.json`: the bytes of `gh api
    'repos/ship-iac/repo-example-stacks/actions/runs/<id>/jobs?filter=latest&per_page=100'`
    saved to a file on 2026-11-16, for a draft-skipped autoplan (run 43782205187), a zero-cell
    one (43782335507) and a `closed`-event run (43772184166)."""
    return (pathlib.Path(__file__).parent / "fixtures" / f"jobs-{kind}.json").read_text(
        encoding="utf-8"
    )


def _dispatch(run_id, title, status="queued", actor="shipmate-test[bot]"):
    """One live `workflow_dispatch` run as the runs listing returns it."""
    return {
        "id": run_id,
        "display_title": title,
        "event": "workflow_dispatch",
        "status": status,
        "actor": {"login": actor},
    }


def _live_head(
    monkeypatch,
    pull=None,
    runs="",
    pull_fails=False,
    runs_fail=False,
    jobs=None,
    dispatched=(),
    dispatched_later=(),
    dispatched_fail=False,
):
    """`gs.run` answering the pulls read with the text `pull` (default: `_LIVE`, not a draft) and
    the live head's autoplan listing with `runs`; a list of either answers each item in turn,
    the last repeating, raising one that is a `SystemExit`. Each read fails with a multi-line gh
    error on request (`runs_fail` is the HTTP status). The live dispatch listing answers each
    status with the runs of `dispatched` listed in it (a run's `listed_as`, else its `status`),
    plus those of `dispatched_later` from the second listing on, and fails on request; a jobs
    read of run `i` answers `_jobs_answer(jobs[i])`. Returns the calls."""
    calls = []

    pull_failure = SystemExit(
        "command failed (1): gh api repos/acme/demo/pulls/7\nHTTP 502\r::add-mask::x"
    )

    def pull_read():
        return _raised(pull_failure if pull_fails else _pull() if pull is None else _next(pull))

    def runs_read():
        if runs_fail:
            raise SystemExit(f"command failed (1): gh api --paginate x\nHTTP {runs_fail}")
        return _raised(_next(runs))

    def dispatch_read(args):
        if dispatched_fail:
            raise SystemExit("command failed (1): gh api x\nHTTP 404")
        status = args[2].split("status=")[1].split("&")[0]
        later = dispatched_later if calls.count(_DISPATCH_READS[0]) > 1 else ()
        listed = [r for r in (*dispatched, *later) if r.get("listed_as", r["status"]) == status]
        return json.dumps({"workflow_runs": listed})

    answers = {
        json.dumps(_PULL_READ): lambda _: pull_read(),
        json.dumps(_RUNS_READ): lambda _: runs_read(),
        **{json.dumps(read): dispatch_read for read in _DISPATCH_READS},
        **{
            json.dumps(_jobs_read(i)): lambda _, b=body: _jobs_answer(b)
            for i, body in (jobs or {}).items()
        },
    }

    def run(args):
        calls.append(args)
        answer = answers.get(json.dumps(args))
        if answer is None:
            raise AssertionError(f"unexpected gh call: {args}")
        return answer(args)

    monkeypatch.setattr(gs, "run", run)
    return calls


def _raised(item):
    """`item`, or raised when it is a `SystemExit`."""
    if isinstance(item, SystemExit):
        raise item
    return item


def _next(answers):
    """`answers` itself, or, for a list, its next item, the last repeating."""
    if not isinstance(answers, list):
        return answers
    return answers.pop(0) if len(answers) > 1 else answers[0]


def _jobs_answer(body):
    """A jobs read's answer: the capture of run kind `body` (or of the next of a list of them),
    else `body` itself, raised when it is a `SystemExit`."""
    body = _next(body)
    kinds = (_DRAFT_SKIPPED, _ZERO_CELL, _CLOSED)
    return _captured_jobs(body) if body in kinds else _raised(body)


def _cli(*argv):
    """`gate-status argv` as its `__main__` block runs it: 0, or the `SystemExit` code, which for
    a non-refusal is the message the interpreter prints as the one stderr line before exit 1."""
    try:
        _shipmate.run_annotated(gs.main, list(argv))
    except SystemExit as exc:
        return exc.code
    return 0


def _row(run_id, status, conclusion, attempt=1):
    """One listing line of an autoplan run of the live head, at run attempt `attempt`."""
    keys = {"status": status, "conclusion": conclusion, "run_attempt": attempt}
    return json.dumps(listed_autoplan(run_id, _LIVE, **keys)) + "\n"


def _rows(*runs):
    """Listing lines for `(status, conclusion)` pairs; a None status omits both keys."""
    return "".join(
        json.dumps(
            listed_autoplan(9, _LIVE, run_attempt=1)
            | ({} if status is None else {"status": status, "conclusion": conclusion})
        )
        + "\n"
        for status, conclusion in runs
    )


@pytest.mark.parametrize(
    ("sha", "stdout"), [(SHA, f"{_LIVE}\n"), (_LIVE, "")], ids=["moved", "same"]
)
def test_moved_prints_the_live_head_only_when_it_is_not_the_sha(
    in_process, monkeypatch, capsys, sha, stdout
):
    """Mutation: compare `==` for `!=` in `_moved` -- both cases red."""
    calls = _live_head(monkeypatch)
    assert _cli("moved", sha, "7") == 0
    assert (capsys.readouterr(), calls) == ((stdout, ""), [_PULL_READ])


@pytest.mark.parametrize("cmd", ["moved", "replan"])
def test_a_failed_head_read_exits_1_on_one_unannotated_line(in_process, monkeypatch, capsys, cmd):
    """gh's CR-led `::add-mask::` stays inside the one line, after the retries' notices.
    Mutation: re-raise `run`'s `SystemExit` raw in `pr_head` -- two lines."""
    calls = _live_head(monkeypatch, pull_fails=True)
    notice = "::notice::command failed (1): gh api repos/acme/demo/pulls/7%0AHTTP 502\n"
    assert (_cli(cmd, SHA, "7"), capsys.readouterr(), calls, in_process) == (
        _PULL_FAILED,
        ("", 2 * notice),
        [_PULL_READ] * 3,
        [2, 5],
    )


@pytest.mark.parametrize(
    ("pull", "message"),
    [
        pytest.param(
            _pull(None), "pull request #7 has head SHA None and draft False", id="null-sha"
        ),
        pytest.param(
            _pull(draft="false"),
            f"pull request #7 has head SHA '{_LIVE}' and draft 'false'",
            id="str-draft",
        ),
        pytest.param(
            '{"draft": false}', "unreadable pull request #7: KeyError('head')", id="no-head"
        ),
        pytest.param(
            json.dumps({"head": {"sha": _LIVE}, "draft": False}),
            "unreadable pull request #7: KeyError('state')",
            id="no-state",
        ),
        pytest.param(_pull(state=None), "pull request #7 has state None", id="null-state"),
        pytest.param(
            "null",
            "unreadable pull request #7: TypeError(\"'NoneType' object is not subscriptable\")",
            id="null-pull",
        ),
        pytest.param(
            "<html>",
            "unreadable pull request #7: JSONDecodeError('Expecting value: line 1 column 1 "
            "(char 0)')",
            id="not-json",
        ),
    ],
)
@pytest.mark.parametrize("cmd", ["moved", "replan"])
def test_a_head_read_without_a_sha_and_draft_flag_exits_1(
    in_process, monkeypatch, capsys, cmd, pull, message
):
    """Mutations: return `pr_head`'s answer unchecked (drop the `is_head_sha` or the `isinstance`
    clause) -- null-sha or str-draft prints or lists; drop the state `isinstance` check --
    null-state prints or lists; read `pull.get("state")` -- no-state prints or lists; drop
    `TypeError` from the caught classes -- null-pull is a traceback."""
    calls = _live_head(monkeypatch, pull=pull)
    assert (_cli(cmd, SHA, "7"), capsys.readouterr(), calls) == (message, ("", ""), [_PULL_READ])


@pytest.mark.parametrize("cmd", ["moved", "replan"])
def test_a_pr_that_is_not_a_number_is_refused_annotated_before_any_call(
    in_process, monkeypatch, capsys, cmd
):
    """It is bound into the pulls path. Mutation: drop the `is_pr_number` check -- a pulls call."""
    calls = _live_head(monkeypatch)
    assert (_cli(cmd, SHA, "1/../x"), capsys.readouterr(), calls) == (
        1,
        ("", "::error::<pr> must be a pull request number (got: '1/../x')\n"),
        [],
    )


@pytest.mark.parametrize("cmd", ["moved", "replan"])
def test_a_malformed_sha_is_refused_unannotated_before_any_call(
    in_process, monkeypatch, capsys, cmd
):
    """The step that runs it degrades with its own warning. Mutation: leave
    `annotate=cmd != "checks"` -- an `::error::` line."""
    calls = _live_head(monkeypatch)
    assert (_cli(cmd, "A" * 40, "7"), capsys.readouterr(), calls) == (
        f"head SHA must be a 40-char lowercase hex SHA (got: {'A' * 40!r})",
        ("", ""),
        [],
    )


_DRAFT_SKIPPED, _ZERO_CELL, _CLOSED = "draft-skipped", "zero-cell", "closed"


def _relisted(first, *later):
    """The calls of a replan that found nothing blocking in `first` (the calls after the pull
    read) and re-listed the autoplans three times (`later`, each the calls of one re-list),
    then re-read the pull request and the live plan runs."""
    calls = [_PULL_READ, *first]
    for one in later or ([_RUNS_READ],) * 3:
        calls += one
    return [*calls, _PULL_READ, *_DISPATCH_READS]


@pytest.mark.parametrize(
    ("runs", "jobs", "first"),
    [
        pytest.param("", {}, [_RUNS_READ, *_DISPATCH_READS], id="no-run"),
        pytest.param(
            _row(9, "completed", "cancelled"), {}, [_RUNS_READ, *_DISPATCH_READS], id="cancelled"
        ),
        pytest.param(
            _row(9, "completed", "failure"), {}, [_RUNS_READ, *_DISPATCH_READS], id="failure"
        ),
        pytest.param(
            _row(9, "completed", "success"),
            {9: _DRAFT_SKIPPED},
            [_RUNS_READ, *_DISPATCH_READS, _jobs_read(9)],
            id="draft-skipped",
        ),
        pytest.param(
            _row(9, "completed", "success"),
            {9: _CLOSED},
            [_RUNS_READ, *_DISPATCH_READS, _jobs_read(9)],
            id="closed",
        ),
    ],
)
def test_replan_prints_a_moved_head_no_run_planned_after_waiting_for_the_push(
    in_process, monkeypatch, capsys, runs, jobs, first
):
    """A run that did not plan the live head blocks nothing: a cancelled or failed one, a
    draft-skipped one (its `plan / detect` listed `skipped`) and a `closed`-event run (no
    `plan / detect`), each read from jobs listings captured raw. The autoplans are re-listed
    three times, 5 s apart, then the live plan runs once more. A cancelled or failed run's jobs
    are not read.

    Mutations: treat any listed run as blocking -- cancelled and failure print nothing; block a
    `success` run without reading its jobs, as the replan did before -- draft-skipped and closed
    print nothing; count a run with no detect job as planned -- closed prints nothing; read jobs
    for every completed run -- cancelled and failure read jobs; no re-list -- the calls and
    sleeps differ; list the live plan runs only after the wait -- the calls differ; drop the
    post-wait re-read -- the calls differ; re-list only on an empty listing -- the
    draft-skipped and closed calls differ."""
    calls = _live_head(monkeypatch, runs=runs, jobs=jobs)
    assert _cli("replan", SHA, "7") == 0
    assert (capsys.readouterr(), calls, in_process) == (
        (f"{_LIVE}\n", ""),
        _relisted(first),
        [5, 5, 5],
    )


@pytest.mark.parametrize("status", ["queued", "in_progress", "requested", "waiting", "pending"])
def test_replan_an_unfinished_autoplan_blocks_unread(in_process, monkeypatch, capsys, status):
    """Any status but `completed` is unfinished: it blocks before any other read, its jobs
    unread, and the log names the lowest unfinished id. Mutations: read jobs for it -- a jobs
    call; block only `queued` and `in_progress` -- waiting red; name the highest id."""
    runs = _row(12, status, None) + _row(9, status, None) + _row(5, "completed", "failure")
    calls = _live_head(monkeypatch, runs=runs)
    assert _cli("replan", SHA, "7") == 0
    assert (capsys.readouterr(), calls, in_process) == (
        (
            "",
            (
                f"pull request #7 moved to {_LIVE}, which has an autoplan run 9 in progress; "
                "nothing dispatched\n"
            ),
        ),
        [_PULL_READ, _RUNS_READ],
        [],
    )


def test_replan_a_succeeded_autoplan_that_planned_blocks(in_process, monkeypatch, capsys):
    """A zero-cell autoplan planned: its `plan / detect` succeeded (raw capture). A duplicate plan
    would write newer plan records under a waiting auto apply. The log names the lowest such
    id, and jobs reads stop at it. Mutations: require a plan cell job -- the zero-cell run
    dispatches; drop the jobs read and never block a completed run -- prints the head; change a
    word of the text."""
    runs = _row(11, "completed", "success") + _row(9, "completed", "success")
    calls = _live_head(monkeypatch, runs=runs, jobs={9: _ZERO_CELL, 11: _ZERO_CELL})
    assert _cli("replan", SHA, "7") == 0
    assert (capsys.readouterr(), calls, in_process) == (
        (
            "",
            (
                f"pull request #7 moved to {_LIVE}, whose autoplan run 9 succeeded; "
                "nothing dispatched\n"
            ),
        ),
        [_PULL_READ, _RUNS_READ, *_DISPATCH_READS, _jobs_read(9)],
        [],
    )


def _with_detect(drop=(), attempt=1, **detect):
    """The zero-cell capture with its `plan / detect` job's fields replaced by `detect` and the
    keys in `drop` removed, every job at run attempt `attempt`."""
    body = json.loads(_captured_jobs(_ZERO_CELL))
    for job in body["jobs"]:
        job["run_attempt"] = attempt
        if job["name"] == "plan / detect":
            job.update(detect)
            for key in drop:
                del job[key]
    return json.dumps(body)


def _two_detects():
    body = json.loads(_captured_jobs(_ZERO_CELL))
    body["jobs"] += [j for j in body["jobs"] if j["name"] == "plan / detect"]
    return json.dumps(body)


_JOBS_CAUSE = "autoplan run 9: job plan / detect has status 'completed' and conclusion "


@pytest.mark.parametrize(
    ("runs", "jobs", "message", "reads"),
    [
        pytest.param(
            _row(9, None, None),
            {},
            "autoplan run 9 has status None and conclusion None",
            [],
            id="null-status",
        ),
        pytest.param(
            _row(9, "completed", None),
            {},
            "autoplan run 9 has status 'completed' and conclusion None",
            [],
            id="null-conclusion",
        ),
        pytest.param(
            _row(9, "completed", "failure", attempt=None),
            {},
            "autoplan run 9 has run attempt None",
            [],
            id="null-attempt",
        ),
        pytest.param(
            _row(9, "completed", "success"),
            {9: SystemExit("command failed (1): gh api x\nHTTP 404")},
            "the jobs of autoplan run 9 could not be listed",
            [*_DISPATCH_READS, _jobs_read(9)],
            id="jobs-failed",
        ),
        pytest.param(
            _row(9, "completed", "success"),
            {9: '{"jobs": []}'},
            "the jobs of autoplan run 9 could not be listed",
            [*_DISPATCH_READS, _jobs_read(9)],
            id="no-jobs",
        ),
        pytest.param(
            _row(9, "completed", "success"),
            {9: _with_detect(conclusion=None)},
            _JOBS_CAUSE + "None",
            [*_DISPATCH_READS, _jobs_read(9)],
            id="detect-null-conclusion",
        ),
        pytest.param(
            _row(9, "completed", "success"),
            {9: _with_detect(drop=("conclusion",))},
            _JOBS_CAUSE + "None",
            [*_DISPATCH_READS, _jobs_read(9)],
            id="detect-missing-conclusion",
        ),
        pytest.param(
            _row(9, "completed", "success"),
            {9: _with_detect(conclusion=0)},
            _JOBS_CAUSE + "0",
            [*_DISPATCH_READS, _jobs_read(9)],
            id="detect-int-conclusion",
        ),
        pytest.param(
            _row(9, "completed", "success"),
            {9: _with_detect(status="in_progress", conclusion=None)},
            "autoplan run 9: job plan / detect is 'in_progress' in a run that has ended",
            [*_DISPATCH_READS, _jobs_read(9)],
            id="detect-unfinished",
        ),
        pytest.param(
            _row(9, "completed", "success"),
            {9: _two_detects()},
            "autoplan run 9: 2 jobs are named plan / detect",
            [*_DISPATCH_READS, _jobs_read(9)],
            id="two-detects",
        ),
    ],
)
def test_replan_an_unreadable_run_or_job_exits_1_and_prints_no_head(
    in_process, monkeypatch, capsys, runs, jobs, message, reads
):
    """Each unreadable answer dispatches nothing: the caller warns. Mutations: treat each as not
    planned or not unfinished (the head prints after the wait)."""
    calls = _live_head(monkeypatch, runs=runs, jobs=jobs)
    code = _cli("replan", SHA, "7")
    out, err = capsys.readouterr()
    assert (code, out, calls) == (message, "", [_PULL_READ, _RUNS_READ, *reads])
    failed = isinstance(jobs.get(9), SystemExit)
    assert err == ("::warning::command failed (1): gh api x | HTTP 404\n" if failed else "")


_PLAN_7 = "shipmate \u00b7 plan pr=7 ref= comment="


@pytest.mark.parametrize(
    ("dispatched", "logged"),
    [
        pytest.param([_dispatch(30, _PLAN_7)], (30, "queued"), id="app"),
        pytest.param([_dispatch(30, _PLAN_7, actor="dev")], (30, "queued"), id="human"),
        pytest.param(
            [_dispatch(30, _PLAN_7, status="in_progress")], (30, "in progress"), id="in-progress"
        ),
        pytest.param(
            [_dispatch(31, _PLAN_7, status="waiting"), _dispatch(30, _PLAN_7)],
            (30, "queued"),
            id="lowest-id",
        ),
        pytest.param(
            [_dispatch(30, "shipmate \u00b7 plan pr=7 ref=x comment=")],
            (30, "queued"),
            id="unparsed-plan-of-this-pr",
        ),
    ],
)
def test_replan_a_live_dispatched_plan_blocks_before_any_wait(
    in_process, monkeypatch, capsys, dispatched, logged
):
    """A live `shipmate plan` run of this pull request, by the App or a human, has not yet read
    its head (it queues behind this run in `plan-<pr>`), so it plans the live head. It blocks
    before any jobs read or sleep. A title that starts `shipmate · plan pr=7 ` and does not
    parse counts too. Mutations: filter by actor -- human prints the head; hard-code
    `in progress` -- app reddens; ignore prefix-matching unparsed titles -- the unparsed row
    prints the head; list the live plan runs only after the wait -- sleeps are counted."""
    run_id, status = logged
    calls = _live_head(
        monkeypatch,
        runs=_row(9, "completed", "success"),
        jobs={9: _ZERO_CELL},
        dispatched=dispatched,
    )
    assert _cli("replan", SHA, "7") == 0
    assert (capsys.readouterr(), calls, in_process) == (
        (
            "",
            (
                f"pull request #7 moved to {_LIVE}; its shipmate plan run {run_id} is {status} and "
                "plans the live head; nothing dispatched\n"
            ),
        ),
        [_PULL_READ, _RUNS_READ, *_DISPATCH_READS],
        [],
    )


@pytest.mark.parametrize(
    "dispatched",
    [
        pytest.param([_dispatch(30, "shipmate \u00b7 plan pr=8 ref= comment=")], id="other-pr"),
        pytest.param([_dispatch(999, _PLAN_7)], id="own-run"),
        pytest.param([_dispatch(30, "shipmate \u00b7 plan pr=70 ref=x comment=")], id="pr-70"),
        pytest.param(
            [_dispatch(30, "shipmate \u00b7 plan pr=8 ref=x comment=")], id="other-unparsed"
        ),
        pytest.param(
            [_dispatch(30, "shipmate \u00b7 apply pr=7 ref=x comment=")], id="unparsed-apply"
        ),
        pytest.param([_dispatch(30, "hand-run")], id="foreign-title"),
    ],
)
def test_replan_a_dispatched_run_that_does_not_plan_this_pull_request_blocks_nothing(
    in_process, monkeypatch, capsys, dispatched
):
    """Another pull request's plan, this run itself (`GITHUB_RUN_ID` 999, a dispatched plan's
    own summary) and an unparsed title of another verb or pull request neither block nor raise.
    Mutations: drop the pr filter -- other-pr blocks; drop the own-run exclusion -- own-run
    blocks; match the prefix without its trailing space -- pr-70 blocks; raise on every unparsed
    title -- the unparsed rows exit 1."""
    calls = _live_head(monkeypatch, dispatched=dispatched)
    assert _cli("replan", SHA, "7") == 0
    assert (capsys.readouterr(), calls) == (
        (f"{_LIVE}\n", ""),
        _relisted([_RUNS_READ, *_DISPATCH_READS]),
    )


def test_replan_a_dispatched_plan_listed_completed_blocks_nothing(in_process, monkeypatch, capsys):
    """A run that ended between the listing query and its answer is listed `completed` in a live
    page; it has ended. Mutation: drop the `completed` filter in `live_plan_runs`."""
    calls = _live_head(
        monkeypatch,
        dispatched=[_dispatch(30, _PLAN_7, status="completed") | {"listed_as": "queued"}],
    )
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == (f"{_LIVE}\n", "")
    assert calls == _relisted([_RUNS_READ, *_DISPATCH_READS])


def test_replan_an_incomplete_live_plan_listing_exits_1(in_process, monkeypatch, capsys):
    """Mutation: ignore `complete` -- the head prints."""
    calls = _live_head(monkeypatch, dispatched_fail=True)
    assert (
        _cli("replan", SHA, "7")
        == "the live shipmate plan runs of pull request #7 could not be listed"
    )
    assert capsys.readouterr().out == ""
    assert calls == [_PULL_READ, _RUNS_READ, *_DISPATCH_READS]


def test_replan_a_dispatched_plan_listed_only_after_the_wait_blocks(
    in_process, monkeypatch, capsys
):
    """Mutation: drop the post-wait re-read -- the head prints."""
    calls = _live_head(monkeypatch, dispatched_later=[_dispatch(30, _PLAN_7)])
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == (
        "",
        (
            f"pull request #7 moved to {_LIVE}; its shipmate plan run 30 is queued and plans the "
            "live head; nothing dispatched\n"
        ),
    )
    assert (calls, in_process) == (_relisted([_RUNS_READ, *_DISPATCH_READS]), [5, 5, 5])


@pytest.mark.parametrize(
    ("first", "second", "log", "reads"),
    [
        pytest.param(
            "",
            _row(12, "in_progress", None),
            "which has an autoplan run 12 in progress",
            [],
            id="empty-then-unfinished",
        ),
        pytest.param(
            _row(9, "completed", "success"),
            _row(9, "completed", "success") + _row(12, "completed", "success"),
            "whose autoplan run 12 succeeded",
            [_jobs_read(12)],
            id="draft-skipped-then-planned",
        ),
    ],
)
def test_replan_an_autoplan_listed_on_a_re_list_blocks(
    in_process, monkeypatch, capsys, first, second, log, reads
):
    """The push that moved the head can take seconds to list its autoplan run; a run first
    listed on a re-list is judged as on the first listing, and a run judged already is not
    read again. Mutations: no re-list; re-list only on an empty listing -- the draft-skipped row
    dispatches."""
    calls = _live_head(monkeypatch, runs=[first, second], jobs={9: _DRAFT_SKIPPED, 12: _ZERO_CELL})
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == (
        "",
        f"pull request #7 moved to {_LIVE}, {log}; nothing dispatched\n",
    )
    first_reads = [_RUNS_READ, *_DISPATCH_READS] + ([_jobs_read(9)] if first else [])
    assert (calls, in_process) == ([_PULL_READ, *first_reads, _RUNS_READ, *reads], [5])


def test_replan_a_re_run_listed_again_unfinished_blocks(in_process, monkeypatch, capsys):
    """A re-run keeps its run id: a run listed failed, then queued on a re-list, plans the live
    head. Each re-list judges every listed run. Mutation: judge only ids not listed before --
    the head prints."""
    runs = [_row(9, "completed", "failure"), _row(9, "queued", None, attempt=2)]
    calls = _live_head(monkeypatch, runs=runs)
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == (
        "",
        (
            f"pull request #7 moved to {_LIVE}, which has an autoplan run 9 in progress; "
            "nothing dispatched\n"
        ),
    )
    assert (calls, in_process) == ([_PULL_READ, _RUNS_READ, *_DISPATCH_READS, _RUNS_READ], [5])


def test_replan_reads_a_run_attempt_s_jobs_once(in_process, monkeypatch, capsys):
    """A run's jobs evidence is kept per run attempt: the same attempt listed again is not read
    again, and a re-run's new attempt is. Here attempt 1 skipped as a draft and attempt 2
    planned. Mutations: key the evidence by run id alone -- the head prints; keep no evidence
    -- attempt 1's jobs are read on every re-list."""
    first = _row(9, "completed", "success")
    runs = [first, first, _row(9, "completed", "success", attempt=2)]
    jobs = {9: [_DRAFT_SKIPPED, _with_detect(attempt=2)]}
    calls = _live_head(monkeypatch, runs=runs, jobs=jobs)
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == (
        "",
        f"pull request #7 moved to {_LIVE}, whose autoplan run 9 succeeded; nothing dispatched\n",
    )
    assert (calls, in_process) == (
        [
            _PULL_READ,
            _RUNS_READ,
            *_DISPATCH_READS,
            _jobs_read(9),
            _RUNS_READ,
            _RUNS_READ,
            _jobs_read(9),
        ],
        [5, 5],
    )


def test_replan_jobs_of_another_attempt_are_never_kept(in_process, monkeypatch, capsys):
    """Jobs listed at another attempt than the run (a re-run started between the two reads) say
    nothing about the listed attempt: they are read again on each re-list, and the last re-list
    exits 1. Mutations: keep the evidence whatever the jobs' attempt -- one jobs read, and the
    head prints; never refuse on the last re-list -- the head prints."""
    calls = _live_head(
        monkeypatch, runs=_row(9, "completed", "success", attempt=2), jobs={9: _DRAFT_SKIPPED}
    )
    assert _cli("replan", SHA, "7") == (
        "autoplan run 9 is listed at run attempt 2 and its jobs at [1]"
    )
    assert capsys.readouterr().out == ""
    assert (calls, in_process) == (
        [
            _PULL_READ,
            _RUNS_READ,
            *_DISPATCH_READS,
            _jobs_read(9),
            *[_RUNS_READ, _jobs_read(9)] * 3,
        ],
        [5, 5, 5],
    )


def test_replan_judges_no_job_of_another_attempt(in_process, monkeypatch, capsys):
    """Run 9 listed at attempt 1 while its jobs answer at attempt 2, with `plan / detect` still
    running: the attempt check comes first, so the jobs are read again on the next listing (at
    attempt 1, draft-skipped) instead of failing on the unfinished detect. Mutation: call
    `ag.planned` before the attempt check -- exit 1 on the first listing."""
    unfinished = _with_detect(attempt=2, status="in_progress", conclusion=None)
    calls = _live_head(
        monkeypatch,
        runs=_row(9, "completed", "success"),
        jobs={9: [unfinished, _DRAFT_SKIPPED]},
    )
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == (f"{_LIVE}\n", "")
    assert calls.count(_jobs_read(9)) == 2


def test_replan_with_no_re_list_judges_its_one_listing_as_the_last(in_process, monkeypatch, capsys):
    """With no re-list, the first listing is the last: an attempt mismatch exits 1 there.
    Mutation: pass `last=False` to the first judgement -- the head prints."""
    monkeypatch.setattr(gs, "REPLAN_WAIT", 0)
    _live_head(
        monkeypatch, runs=_row(9, "completed", "success", attempt=2), jobs={9: _DRAFT_SKIPPED}
    )
    assert _cli("replan", SHA, "7") == (
        "autoplan run 9 is listed at run attempt 2 and its jobs at [1]"
    )
    assert capsys.readouterr().out == ""


def test_replan_a_blocking_run_wins_over_an_attempt_mismatch(in_process, monkeypatch, capsys):
    """On the last listing an attempt mismatch refuses only once every run is judged: run 11,
    listed there and planned, blocks. Mutation: refuse at the mismatch -- exit 1."""
    nine = _row(9, "completed", "success", attempt=2)
    runs = [nine, nine, nine, nine + _row(11, "completed", "success")]
    _live_head(monkeypatch, runs=runs, jobs={9: _DRAFT_SKIPPED, 11: _ZERO_CELL})
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == (
        "",
        f"pull request #7 moved to {_LIVE}, whose autoplan run 11 succeeded; nothing dispatched\n",
    )


def test_replan_a_job_without_an_int_run_attempt_exits_1(in_process, monkeypatch, capsys):
    """Mutation: drop the jobs' `run_attempt` type check -- the mismatch path answers instead."""
    body = json.loads(_captured_jobs(_ZERO_CELL))
    body["jobs"][0]["run_attempt"] = "1"
    _live_head(monkeypatch, runs=_row(9, "completed", "success"), jobs={9: json.dumps(body)})
    assert _cli("replan", SHA, "7") == "the jobs of autoplan run 9 carry run attempts [1, '1']"
    assert capsys.readouterr().out == ""


def test_replan_jobs_of_another_attempt_are_read_again(in_process, monkeypatch, capsys):
    """Attempt 2 listed, its jobs first read at attempt 1, then at 2, where it planned.
    Mutation: keep the first read's evidence -- the head prints."""
    calls = _live_head(
        monkeypatch,
        runs=_row(9, "completed", "success", attempt=2),
        jobs={9: [_DRAFT_SKIPPED, _with_detect(attempt=2)]},
    )
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == (
        "",
        f"pull request #7 moved to {_LIVE}, whose autoplan run 9 succeeded; nothing dispatched\n",
    )
    assert calls[-2:] == [_RUNS_READ, _jobs_read(9)]


@pytest.mark.parametrize("code", [None, 1], ids=["none", "int"])
def test_replan_a_listing_exit_without_text_names_a_fixed_cause(
    in_process, monkeypatch, capsys, code
):
    """A `SystemExit` with no text prints `gh failed`, not `None` or `1`. Mutation: print
    `str(exc.code)`."""
    _live_head(monkeypatch, runs=[SystemExit(code)])
    assert _cli("replan", SHA, "7") == "gh failed"


_OTHER = "c" * 40


@pytest.mark.parametrize(
    ("after", "log"),
    [
        pytest.param(
            _pull(_OTHER),
            f"pull request #7 moved from {_LIVE} to {_OTHER} while this run waited; "
            "nothing dispatched\n",
            id="moved-again",
        ),
        pytest.param(
            _pull(state="closed"), "pull request #7 is closed; nothing dispatched\n", id="closed"
        ),
        pytest.param(
            _pull(draft=True),
            f"pull request #7 moved to {_LIVE}, a draft; nothing dispatched\n",
            id="draft",
        ),
    ],
)
def test_replan_re_reads_the_pull_request_after_the_wait(
    in_process, monkeypatch, capsys, after, log
):
    """The pull request can move again, close or turn draft while the run waits; the head the
    wait judged is then not the one to plan. Mutation: drop the re-read -- each row prints the
    head."""
    calls = _live_head(monkeypatch, pull=[_pull(), after])
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == ("", log)
    assert calls == _relisted([_RUNS_READ, *_DISPATCH_READS])[: -len(_DISPATCH_READS)]


def test_replan_the_re_list_is_bounded(in_process, monkeypatch, capsys):
    """Three re-lists, then a dispatch. Mutation: re-list forever (the test runs under a
    timeout; the stub's listing never blocks)."""
    calls = _live_head(monkeypatch)
    assert _cli("replan", SHA, "7") == 0
    assert capsys.readouterr() == (f"{_LIVE}\n", "")
    assert (calls.count(_RUNS_READ), in_process) == (4, [5, 5, 5])


@pytest.mark.parametrize(
    ("name", "planned"),
    [
        ("plan / detect", True),
        ("x / detect", False),
        ("plan / detect-extra", False),
        ("plan / x / detect", False),
        ("detect", False),
    ],
)
def test_planned_counts_only_the_job_named_plan_detect(name, planned):
    """A caller job named other than `plan` makes no run count. Mutations: match on a suffix
    (`endswith("/ detect")`) -- x / detect counts; match on a substring -- detect-extra
    counts."""
    jobs = json.loads(_with_detect(name=name))["jobs"]
    assert gs.ag.planned(jobs) is planned


def test_replan_retries_a_transient_listing_failure(in_process, monkeypatch, capsys):
    """Mutation: list without `retried` in `autoplan_runs` (`listing = run(args)`) -- the 502
    exits 1, red."""
    transient = SystemExit("command failed (1): gh api --paginate x\nHTTP 502")
    calls = _live_head(monkeypatch, runs=[transient, _rows()])
    assert _cli("replan", SHA, "7") == 0
    assert (capsys.readouterr(), calls, in_process) == (
        (f"{_LIVE}\n", "::notice::command failed (1): gh api --paginate x%0AHTTP 502\n"),
        _relisted([_RUNS_READ, _RUNS_READ, *_DISPATCH_READS]),
        [2, 5, 5, 5],
    )


def test_replan_spends_three_listing_reads_on_a_persistent_5xx(in_process, monkeypatch, capsys):
    """The listing retries inside `autoplan_runs` only. Mutation: call it through
    `retried(lambda: ag.autoplan_runs(...))` again -- nine listing calls."""
    calls = _live_head(monkeypatch, runs_fail=502)
    notice = "::notice::command failed (1): gh api --paginate x%0AHTTP 502\n"
    assert (_cli("replan", SHA, "7"), capsys.readouterr(), calls, in_process) == (
        "command failed (1): gh api --paginate x | HTTP 502",
        ("", notice * 2),
        [_PULL_READ] + [_RUNS_READ] * 3,
        [2, 5],
    )


def test_replan_prints_nothing_for_a_draft_and_lists_no_run(in_process, monkeypatch, capsys):
    """Mutation: skip the draft check -- the listing is read and the head printed."""
    calls = _live_head(monkeypatch, pull=_pull(draft=True))
    assert _cli("replan", SHA, "7") == 0
    assert (capsys.readouterr(), calls) == (("", _DRAFT_LOG), [_PULL_READ])


def test_replan_prints_nothing_for_a_closed_pull_request_and_lists_no_run(
    in_process, monkeypatch, capsys
):
    """A merged or closed pull request needs no plan. Mutation: drop the state check -- the
    listing is read and the head printed."""
    calls = _live_head(monkeypatch, pull=_pull(state="closed"))
    assert _cli("replan", SHA, "7") == 0
    assert (capsys.readouterr(), calls) == (
        ("", "pull request #7 is closed; nothing dispatched\n"),
        [_PULL_READ],
    )


def test_replan_prints_nothing_when_the_head_did_not_move(in_process, monkeypatch, capsys):
    """Mutation: drop the unmoved return -- the listing is read and the head printed."""
    calls = _live_head(monkeypatch)
    assert _cli("replan", _LIVE, "7") == 0
    assert (capsys.readouterr(), calls) == (("", ""), [_PULL_READ])


@pytest.mark.parametrize(
    ("fakes", "message"),
    [
        pytest.param(
            {"runs": json.dumps(listed_autoplan("9", _LIVE, status="queued")) + "\n"},
            "run id is '9'",
            id="str-id",
        ),
        pytest.param(
            {"runs_fail": 404}, "command failed (1): gh api --paginate x | HTTP 404", id="failed"
        ),
    ],
)
def test_replan_exits_1_on_one_unannotated_line_when_the_listing_fails(
    in_process, monkeypatch, capsys, fakes, message
):
    """Mutations: catch only `SystemExit` -- str-id escapes as a `ValueError` traceback; re-raise
    the listing's `SystemExit` raw -- failed prints two lines."""
    calls = _live_head(monkeypatch, **fakes)
    assert (_cli("replan", SHA, "7"), capsys.readouterr(), calls) == (
        message,
        ("", ""),
        [_PULL_READ, _RUNS_READ],
    )


_GATE = "shipmate / gate"
#: Each gate-reading command's argv after the sha, its whole call list when it reads a head with
#: no App hold (`refresh` over done checks), and its stdout.
_READS_NO_HOLD = {
    "newest": ([USER_ARGV, STATUSES_ARGV], ""),
    "refuse-newer": ([USER_ARGV, STATUSES_ARGV], ""),
    "refresh": ([CHECKS_ARGV, USER_ARGV, STATUSES_ARGV, GREEN], ""),
}


@bash_only
@pytest.mark.parametrize("creator", [4242, None], ids=["app-id", "null"])
@pytest.mark.parametrize("cmd", list(_READS_NO_HOLD))
def test_a_gate_status_another_identity_created_is_no_hold(tmp_path, cmd, creator):
    """A `failure` gate by a newer run, created by an id that is not the App's bot user (the App
    id itself, or none): `newest` prints nothing, `refuse-newer` exits 0, `refresh` greens.
    Mutation: drop the creator filter in `_gates` -> `failure 1000`, exit `NEWER`, exit `HELD`."""
    page1 = _lines(_status(_GATE, "failure", _run_url(1000), creator=creator))
    proc, calls = _run(tmp_path, cmd, SHA, checks=DONE, page1=page1)
    expected_calls, stdout = _READS_NO_HOLD[cmd]
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (0, stdout, "", expected_calls)


@bash_only
def test_newest_ignores_a_gate_another_user_created(tmp_path):
    """The parametrized case above, written out. Mutation: drop the creator filter in `_gates` ->
    `failure 1000`."""
    page1 = (
        '{"context": "shipmate / gate", "state": "failure", "target_url": '
        '"https://example.invalid/acme/demo/actions/runs/1000", "creator": 7}\n'
    )
    proc, calls = _run(tmp_path, "newest", SHA, page1=page1)
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        0,
        "",
        "",
        [USER_ARGV, STATUSES_ARGV],
    )


_FOREIGN_GREEN_OVER_HOLD = _lines(
    _status(_GATE, "success", _run_url(1000), creator=7), _status(_GATE, "failure")
)


@bash_only
def test_a_foreign_green_does_not_hide_the_apps_hold_from_newest(tmp_path):
    """Mutation: drop the creator filter in `_gates` -> `success 1000`."""
    proc, calls = _run(tmp_path, "newest", SHA, page1=_FOREIGN_GREEN_OVER_HOLD)
    assert (proc.returncode, proc.stdout, calls) == (0, "failure 999\n", [USER_ARGV, STATUSES_ARGV])


@bash_only
def test_a_foreign_green_does_not_hide_the_apps_hold_from_refresh(tmp_path):
    """Mutation: drop the creator filter in `_gates` -> the hold is greened."""
    proc, calls = _run(tmp_path, "refresh", SHA, checks=DONE, page1=_FOREIGN_GREEN_OVER_HOLD)
    assert (proc.returncode, proc.stdout, proc.stderr) == (3, HELD, "")
    assert calls == [CHECKS_ARGV, USER_ARGV, STATUSES_ARGV]


@bash_only
def test_a_foreign_newer_hold_does_not_refuse(tmp_path):
    """A newer run id on a status another identity created, over the App's older hold.
    Mutation: drop the creator filter in `_gates` -> exit `NEWER` naming run 1000."""
    page1 = _lines(
        _status(_GATE, "failure", _run_url(1000), creator=7),
        _status(_GATE, "failure", _run_url(998)),
    )
    proc, calls = _run(tmp_path, "refuse-newer", SHA, page1=page1)
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        0,
        "",
        "",
        [USER_ARGV, STATUSES_ARGV],
    )


_SLUG_ERROR = (
    "::error::SHIPMATE_APP_SLUG is empty; gate-status reads only the gate statuses the shipmate "
    "App created and cannot tell which those are\n"
)
_OPEN = DONE + _lines(_check(3, "queued", name="apply / c / dev"))


@bash_only
@pytest.mark.parametrize("env", [NO_SLUG, {**ENV, "SHIPMATE_APP_SLUG": ""}], ids=["unset", "empty"])
@pytest.mark.parametrize(
    ("args", "checks"),
    [
        (("newest", SHA), ""),
        (("refuse-newer", SHA), ""),
        (("post", SHA, "pending", "waiting"), ""),
        (("refresh", SHA), DONE),
        (("refresh", SHA), _OPEN),
    ],
    ids=["newest", "refuse-newer", "post", "refresh-done", "refresh-open"],
)
def test_a_gate_reader_without_the_slug_refuses_before_any_call(tmp_path, env, args, checks):
    """`refresh` and `post` check the slug at entry, because an API call (the check-run listing,
    the POST) comes before their first gate read. `newest` and `refuse-newer` make no call before
    `app_user`, which checks it before its lookup.

    Mutations: drop `_require_slug()` from `refresh` -> `refresh-open` exits 0 after listing the
    check runs, `refresh-done` lists them; drop it from `_post` -> `post` writes; move
    `app_user()` below the listing in `_gates` -> `newest` and `refuse-newer` list the statuses."""
    proc, calls = _run(tmp_path, *args, env=env, checks=checks)
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (1, "", _SLUG_ERROR, [])


@bash_only
@pytest.mark.parametrize(
    ("args", "checks", "argv"),
    [
        (("hold", SHA, "re-plan"), "", _status_argv("failure", "re-plan")),
        (
            ("deploy", SHA, "success", "success"),
            "",
            _status_argv("success", "all env-levels applied (deploy env-level applies completed)"),
        ),
        (("checks", SHA, ".check_runs[]"), "{}\n", _checks_argv(".check_runs[]")),
    ],
    ids=["hold", "deploy", "checks"],
)
def test_a_command_that_reads_no_gate_needs_no_slug(tmp_path, args, checks, argv):
    """Mutation: call `_require_slug()` in `main` for every subcommand -> each exits 1."""
    proc, calls = _run(tmp_path, *args, env=NO_SLUG, checks=checks, checks_jq=".check_runs[]")
    assert (proc.returncode, proc.stderr, calls) == (0, "", [argv])


@pytest.mark.parametrize("cmd", ["moved", "replan"])
def test_a_pull_request_read_needs_no_slug(in_process, monkeypatch, capsys, cmd):
    """Mutation: call `_require_slug()` in `main` for every subcommand -> each exits 1."""
    monkeypatch.delenv("SHIPMATE_APP_SLUG")
    calls = _live_head(monkeypatch)
    assert _cli(cmd, SHA, "7") == 0
    assert (capsys.readouterr().out, calls) == (
        f"{_LIVE}\n",
        [_PULL_READ] if cmd == "moved" else _relisted([_RUNS_READ, *_DISPATCH_READS]),
    )


_LOOKUP_404 = (
    "::error::could not read the shipmate App's bot user shipmate-test[bot]: command failed (1): "
    "gh api users/shipmate-test%255Bbot%255D | gh: Not Found (HTTP 404)\n"
)


@bash_only
@pytest.mark.parametrize(
    ("cmd", "expected_calls"),
    [
        ("newest", [USER_ARGV]),
        ("refuse-newer", [USER_ARGV]),
        ("refresh", [CHECKS_ARGV, USER_ARGV]),
    ],
)
def test_a_failed_bot_user_lookup_refuses_before_the_statuses_read(tmp_path, cmd, expected_calls):
    """gh's cause stays on the one `::error::` line, `%` escaped as `run_annotated` prints it.
    Mutation: return None from `app_user` on a failed read -> exit 0 and a statuses read."""
    proc, calls = _run(
        tmp_path,
        cmd,
        SHA,
        checks=DONE,
        page1=_lines(_status(_GATE, "failure")),
        user='{"message": "Not Found"}',
        user_rc=1,
        user_stderr="gh: Not Found (HTTP 404)\n",
    )
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        1,
        "",
        _LOOKUP_404,
        expected_calls,
    )


@pytest.mark.parametrize(
    ("user", "cause"),
    [
        ('{"id": null}', "its id is null"),
        ('{"id": "306306466"}', 'its id is "306306466"'),
        ('{"id": true}', "its id is true"),
        ("{}", "its id is null"),
        ("<html>", "Expecting value: line 1 column 1 (char 0)"),
        ("", "Expecting value: line 1 column 1 (char 0)"),
    ],
    ids=["null", "str", "bool", "missing", "html", "empty"],
)
@pytest.mark.parametrize("cmd", ["newest", "refuse-newer", "refresh"])
def test_a_bot_user_answer_without_an_integer_id_refuses(
    in_process, monkeypatch, capsys, cmd, user, cause
):
    """Mutations: accept any `id` (drop the `type(uid) is not int` refusal) -> a statuses read
    and exit 0 or `HELD`; test `isinstance(uid, int)` -> `bool` reads the statuses; drop the
    `ValueError` arm -> `html` and `empty` raise `JSONDecodeError`, not the refusal."""
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)
    monkeypatch.setattr(gs, "check_runs", lambda *_: DONE)
    calls = []

    def run(args):
        calls.append(args)
        if args == _USER:
            return user
        return _lines(_status(_GATE, "failure"))

    monkeypatch.setattr(gs, "_run", run)
    lead = "::error::could not read the shipmate App's bot user shipmate-test[bot]"
    assert (_cli(cmd, SHA), capsys.readouterr(), calls) == (
        1,
        ("", f"{lead}: {cause}\n"),
        [_USER],
    )


def test_the_post_command_looks_the_bot_user_up_once_across_retries(in_process, monkeypatch):
    """The write 502s twice; each retry re-reads the gates under `refuse-newer`, which finds only
    an older run's hold. Mutation: `functools.cache` -> `functools.lru_cache(maxsize=0)` (no
    caching) -> a second lookup before the second read."""
    calls = []

    def run(args):
        calls.append(args)
        if args == _USER:
            return GATE_USER
        if args == _READ:
            return _lines(_status(_GATE, "failure", _run_url(1)))
        if calls.count(args) <= 2:
            raise SystemExit(f"::error::{_BAD_GATEWAY}")
        return "{}"

    monkeypatch.setattr(gs, "_run", run)
    gs.main(["post", SHA, "pending", "the reason"])
    assert (calls, in_process) == ([_POST, _USER, _READ, _POST, _READ, _POST], [2, 5])


def test_the_post_command_stops_when_the_bot_user_lookup_fails_before_its_retry(
    in_process, monkeypatch
):
    """The write 502s once; the lookup under `refuse-newer` 502s on each of its own three
    attempts, and its refusal ends the command before a second POST. Mutations: `retry=False` on
    the lookup's `gh_json` -> one lookup and one sleep; return None from `app_user`'s `except` ->
    the statuses are read and the second POST lands."""
    calls = []

    def run(args):
        calls.append(args)
        if args == _USER or (args == _POST and calls.count(_POST) == 1):
            raise SystemExit(f"::error::{_BAD_GATEWAY}")
        if args == _READ:
            return _lines(_status(_GATE, "failure", _run_url(1)))
        return "{}"

    monkeypatch.setattr(gs, "_run", run)
    with pytest.raises(SystemExit) as exc:
        gs.main(["post", SHA, "pending", "the reason"])
    refusal = (
        "::error::could not read the shipmate App's bot user shipmate-test[bot]: command failed "
        "(1): gh api x | gh: Bad Gateway (HTTP 502)"
    )
    assert (exc.value.code, calls, in_process) == (
        refusal,
        [_POST, _USER, _USER, _USER],
        [2, 2, 5],
    )


#: Not captured: gh's `gh: <message> (HTTP <code>)` form around the probed API message.
_SECONDARY = (
    "command failed (1): gh api x\ngh: You have exceeded a secondary rate limit. (HTTP 403)"
)


_DEPLOYED = [
    "gh",
    *_status_argv("success", "all env-levels applied (deploy env-level applies completed)"),
]
_HOLD = ["gh", *_status_argv("failure", "the reason")]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["post", SHA, "pending", "the reason"], [_POST, _USER, _READ, _POST]),
        (["hold", SHA, "the reason"], [_HOLD, _HOLD]),
        (["deploy", SHA, "success", "success"], [_DEPLOYED, _DEPLOYED]),
    ],
    ids=["post", "hold", "deploy"],
)
def test_a_gate_write_waits_out_a_secondary_rate_limit(in_process, monkeypatch, command, expected):
    """The first write hits the secondary limit; `post` re-runs `refuse-newer` before its retry,
    `hold` and `deploy` re-check nothing. Mutations, each run: `post` writes through `retried`
    instead of `write_retried` -> the 403 raises; drop `before_retry=` from `post`'s
    `write_retried` -> the `post` case loses its reads."""
    calls = []

    def run(args):
        calls.append(args)
        if args == _USER:
            return GATE_USER
        if args == _READ:
            return ""
        if len(calls) == 1:
            raise SystemExit(f"::error::{_SECONDARY}")
        return "{}"

    monkeypatch.setattr(gs, "_run", run)
    gs.main(command)
    assert (calls, in_process) == (expected, [60])


def test_refresh_stops_when_a_hold_lands_while_it_waits_out_a_rate_limit(
    in_process, monkeypatch, capsys
):
    """Read pending, the green POST hits the secondary limit, the re-read after the wait finds a
    hold. Mutation: drop `before_retry=` from `post`'s `write_retried` -> the second POST greens
    over the hold."""
    monkeypatch.setenv("SHIPMATE_APP_ID", APP_ID)
    monkeypatch.setattr(gs, "check_runs", lambda *_: DONE)
    calls = []

    def run(args):
        calls.append(args)
        if args == _USER:
            return GATE_USER
        if args == _READ:
            state = "pending" if calls.count(_READ) == 1 else "failure"
            return _lines(_status("shipmate / gate", state, _run_url(42)))
        if calls.count(args) == 1:
            raise SystemExit(f"::error::{_SECONDARY}")
        return "{}"

    monkeypatch.setattr(gs, "_run", run)
    with pytest.raises(SystemExit) as exc:
        gs.refresh(SHA)
    assert (exc.value.code, calls, in_process) == (
        gs.HELD,
        [_USER, _READ, ["gh", *GREEN], _READ],
        [60],
    )
    assert capsys.readouterr().out == HELD


def test_a_rate_limited_refuse_newer_read_is_not_waited_out_as_the_write(in_process, monkeypatch):
    """The gate POST fails transiently; the `refuse-newer` statuses read before its retry hits
    the secondary limit. That read fails the write at once instead of a 60 s wait and a re-post.
    Mutation: `write_retried` wraps `retried` inside the rate-limit wait
    (`create_retried(lambda: retried(fn, before_retry), before_retry=before_retry)`) -> a 60 s
    sleep and a second POST."""
    calls = []
    limited = (
        f"::error::command failed (1): gh {' '.join(STATUSES_ARGV)}\n" + _SECONDARY.split("\n")[1]
    )

    def run(args):
        calls.append(args)
        if args == _USER:
            return GATE_USER
        if args == _READ and calls.count(_READ) == 1:
            raise SystemExit(limited)
        if calls.count(args) == 1:
            raise SystemExit(f"::error::{_BAD_GATEWAY}")
        return "{}"

    monkeypatch.setattr(gs, "_run", run)
    with pytest.raises(SystemExit) as exc:
        gs.main(["post", SHA, "pending", "the reason"])
    assert (exc.value.code, calls, in_process) == (limited, [_POST, _USER, _READ], [2])
