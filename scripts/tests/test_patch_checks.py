import io
import json
import os
import subprocess
import sys

import _shipmate
import pytest
from _loader import SCRIPTS, load_script

pc = load_script("patch-checks")

_REPO = "acme/demo"
_BAD_GATEWAY = "gh: Bad Gateway (HTTP 502)"
_USAGE = "::error::usage: patch-checks (write plan on stdin, no arguments)"
_SUCCESS = {"status": "completed", "conclusion": "success"}
_PLAN_TITLE = "+1 ~0 -0"
_SUMMARY = "```diff\n+ a\n```"


def _argv(cid):
    return ["gh", "api", "-X", "PATCH", f"repos/{_REPO}/check-runs/{cid}", "--input", "-"]


def _check(record_title=_PLAN_TITLE, title=_PLAN_TITLE):
    record = {"plan_run": "1"} if record_title is None else {"plan_run": "1", "title": record_title}
    return {
        "status": "queued",
        "external_id": json.dumps(record),
        "output": {"title": title, "summary": _SUMMARY},
    }


@pytest.fixture
def calls(monkeypatch):
    """`patch-checks` in this process with every retry sleep skipped, on attempt 1 of a
    pre-merge run with no pull request read. Returns `(recorded, fail, reads)`: `recorded` holds
    each PATCH's `(args, stdin)`, `fail` maps an id to the stderr its PATCH fails with, and
    `reads` maps a GET path to its JSON (a string is the stderr the GET fails with), each GET
    appended to `reads["calls"]`. A PATCH updates the check stored at its path, so a later GET
    sees it."""
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.delenv("SHIPMATE_DEPLOY", raising=False)
    monkeypatch.delenv("SHIPMATE_PR_NUMBER", raising=False)
    monkeypatch.setattr(_shipmate, "_sleep", lambda _s: None)
    recorded = []
    fail = {}
    reads = {"calls": []}

    def run(args, secrets=(), stdin=None):
        if args[:2] == ["gh", "api"] and len(args) == 3:
            reads["calls"].append(args[2])
            value = reads[args[2]]
            if isinstance(value, str):
                raise SystemExit(f"command failed (1): {' '.join(args)}\n{value}")
            return json.dumps(value)
        recorded.append((args, stdin))
        cid = args[4].rsplit("/", 1)[1]
        if cid in fail:
            raise SystemExit(f"command failed (1): {' '.join(args)}\n{fail[cid]}")
        stored = reads.get(args[4])
        if isinstance(stored, dict):
            body = json.loads(stdin)
            reads[args[4]] = {**stored, **body}
        return "{}"

    monkeypatch.setattr(pc, "run", run)
    return recorded, fail, reads


def _main(monkeypatch, entries, argv=()):
    lines = "".join(json.dumps(e) + "\n" if isinstance(e, dict) else f"{e}\n" for e in entries)
    monkeypatch.setattr(pc.sys, "stdin", io.StringIO(lines))
    pc.main(list(argv))


def _entry(cid, body=None, **extra):
    return {"id": cid, "body": body or _SUCCESS, **extra}


def test_each_entry_is_patched_with_its_body_on_stdin(monkeypatch, capsys, calls):
    """Blank lines are skipped, and an entry with no `title` reads nothing. Mutations: write the
    body to a temp file and pass `--input <path>` -> the args no longer end `--input -` and
    `stdin` is None; GET every check -> `reads["calls"]` is not empty."""
    recorded, _, reads = calls
    flip = {"status": "in_progress", "details_url": "https://example.test/run"}
    _main(monkeypatch, [_entry(7, flip), "", _entry(9)])
    assert [a for a, _ in recorded] == [_argv("7"), _argv("9")]
    assert [json.loads(s) for _, s in recorded] == [flip, _SUCCESS]
    assert reads["calls"] == []
    assert capsys.readouterr() == ("7\n9\n", "")


def test_a_title_writes_it_with_the_current_summary(monkeypatch, calls):
    """Mutations: send `output` without reading the check -> no GET and no summary; take the
    title from the check as read instead of the entry -> the plan title."""
    recorded, _, reads = calls
    reads[f"repos/{_REPO}/check-runs/4"] = _check()
    _main(monkeypatch, [_entry(4, {"status": "queued"}, title="Failed: boom")])
    assert reads["calls"] == [f"repos/{_REPO}/check-runs/4"]
    assert [json.loads(s) for _, s in recorded] == [
        {"status": "queued", "output": {"title": "Failed: boom", "summary": _SUMMARY}}
    ]


def test_fail_then_success_ends_with_the_record_title_and_the_same_summary(monkeypatch, calls):
    """A re-applied cell loses its `Failed:` title. Mutations: a null `title` keeps the check's
    current title -> it ends `Failed: boom`; drop `output` from success -> the same."""
    recorded, _, reads = calls
    path = f"repos/{_REPO}/check-runs/4"
    reads[path] = _check()
    _main(monkeypatch, [_entry(4, {"status": "queued"}, title="Failed: boom")])
    assert reads[path]["output"]["title"] == "Failed: boom"
    _main(monkeypatch, [_entry(4, title=None)])
    assert reads[path]["status"] == "completed"
    assert reads[path]["conclusion"] == "success"
    assert reads[path]["output"] == {"title": _PLAN_TITLE, "summary": _SUMMARY}


def test_a_missing_record_title_sends_no_output(monkeypatch, calls):
    """A check created before the record carried a title keeps its own. Mutation: fall back to
    the check's current title -> `output` is sent."""
    recorded, _, reads = calls
    reads[f"repos/{_REPO}/check-runs/4"] = _check(record_title=None, title="Failed: boom")
    _main(monkeypatch, [_entry(4, title=None)])
    assert [json.loads(s) for _, s in recorded] == [_SUCCESS]


def test_a_failed_check_read_writes_nothing(monkeypatch, calls):
    """Mutation: treat a failed GET as a missing summary -> the PATCH is sent."""
    recorded, _, reads = calls
    reads[f"repos/{_REPO}/check-runs/4"] = "gh: Not Found (HTTP 404)"
    with pytest.raises(SystemExit):
        _main(monkeypatch, [_entry(4, title="Not run")])
    assert recorded == []


@pytest.mark.parametrize("deploy", [None, "false"])
def test_a_pre_merge_re_run_writes_only_its_successes(monkeypatch, calls, capsys, deploy):
    """A re-run of a completion job can complete a cell an earlier listing missed, but never
    reopens a check, and reads no pull request. Mutations: drop the attempt check -> 2 is
    written; return before any write on a re-run -> 1 is not written."""
    recorded, _, reads = calls
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "31")
    if deploy is not None:
        monkeypatch.setenv("SHIPMATE_DEPLOY", deploy)
    reads[f"repos/{_REPO}/check-runs/2"] = _check()
    _main(monkeypatch, [_entry(1), _entry(2, {"status": "queued"}, title="Not run")])
    assert ([a for a, _ in recorded], reads["calls"]) == ([_argv("1")], [])
    assert capsys.readouterr() == (
        "1\n",
        "::notice::run attempt 2 of a pre-merge apply writes only its successes\n",
    )


def test_a_completed_check_is_never_reopened(monkeypatch, calls, capsys):
    """Another writer, such as a merge deploy after a gate-bypassing merge, completed it.
    Mutation: drop the completed check -> 4 is written `queued`."""
    recorded, _, reads = calls
    reads[f"repos/{_REPO}/check-runs/4"] = {**_check(), "status": "completed"}
    reads[f"repos/{_REPO}/check-runs/5"] = _check()
    _main(
        monkeypatch,
        [
            _entry(4, {"status": "queued"}, title="Not run"),
            _entry(5, {"status": "queued"}, title="Not run"),
        ],
    )
    assert [a for a, _ in recorded] == [_argv("5")]
    assert capsys.readouterr() == (
        "5\n",
        "::notice::apply check 4 is already completed; left as it is\n",
    )


def test_a_success_rewrites_a_completed_check(monkeypatch, calls):
    """`complete` repeats a completion job's success write. Mutation: skip every write over a
    completed check -> nothing is written."""
    recorded, _, reads = calls
    reads[f"repos/{_REPO}/check-runs/4"] = {**_check(), "status": "completed"}
    _main(monkeypatch, [_entry(4, title=None)])
    assert [a for a, _ in recorded] == [_argv("4")]


@pytest.mark.parametrize("value", ["", "True", "1"])
def test_a_deploy_flag_that_is_not_true_or_false_refuses_before_a_read(monkeypatch, calls, value):
    """Mutation: compare `SHIPMATE_DEPLOY.lower() != "true"` -> each value runs as pre-merge."""
    recorded, _, reads = calls
    monkeypatch.setenv("SHIPMATE_DEPLOY", value)
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, [_entry(1)])
    assert exc.value.code == f"::error::SHIPMATE_DEPLOY must be true or false (got: {value!r})"
    assert (recorded, reads["calls"]) == ([], [])


def test_a_merge_deploy_re_run_still_writes(monkeypatch, calls):
    """Mutation: apply the attempt check to the deploy too -> nothing is written."""
    recorded, _, _ = calls
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("SHIPMATE_DEPLOY", "true")
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "31")
    _main(monkeypatch, [_entry(1)])
    assert [a for a, _ in recorded] == [_argv("1")]


def _pr(merged, state="closed"):
    return {"number": 31, "state": state, "merged": merged}


def _pr_case(monkeypatch, calls, pr):
    recorded, _, reads = calls
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "31")
    reads[f"repos/{_REPO}/pulls/31"] = pr
    reads[f"repos/{_REPO}/check-runs/2"] = _check()
    _main(monkeypatch, [_entry(1), _entry(2, {"status": "queued"}, title="Not run")])
    return [a for a, _ in recorded], reads["calls"]


def test_a_merged_pull_request_gets_only_its_success_writes(monkeypatch, calls):
    """The merge deploy owns the rest. Mutation: skip the pull request read -> 2 is written."""
    patched, gets = _pr_case(monkeypatch, calls, _pr(True))
    assert (patched, gets) == ([_argv("1")], [f"repos/{_REPO}/pulls/31"])


@pytest.mark.parametrize("pr", [_pr(False), _pr(False, "open")], ids=["closed", "open"])
def test_an_unmerged_pull_request_gets_every_write(monkeypatch, calls, pr):
    """Mutation: drop success writes when the pull request is not open -> 2 is missing."""
    patched, gets = _pr_case(monkeypatch, calls, pr)
    assert patched == [_argv("1"), _argv("2")]
    assert gets == [f"repos/{_REPO}/pulls/31", f"repos/{_REPO}/check-runs/2"]


def test_a_failed_pull_request_read_writes_nothing_and_fails(monkeypatch, calls):
    """Mutation: treat a failed read as not merged -> both are written."""
    recorded, _, reads = calls
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "31")
    reads[f"repos/{_REPO}/pulls/31"] = "gh: Not Found (HTTP 404)"
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, [_entry(1)])
    assert exc.value.code == (
        "::error::command failed (1): gh api repos/acme/demo/pulls/31\ngh: Not Found (HTTP 404)"
    )
    assert recorded == []


def test_a_deploy_never_reads_the_pull_request(monkeypatch, calls):
    """Mutation: read the pull request whatever `SHIPMATE_DEPLOY` says -> a GET is made."""
    recorded, _, reads = calls
    monkeypatch.setenv("SHIPMATE_DEPLOY", "true")
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "31")
    _main(monkeypatch, [_entry(1)])
    assert reads["calls"] == []
    assert [a for a, _ in recorded] == [_argv("1")]


@pytest.mark.parametrize("argv", [["completed"], ["queued"]])
def test_any_argument_refuses_before_a_write(monkeypatch, calls, argv):
    """Mutation: accept `queued` -> no refusal and a PATCH is sent."""
    recorded, _, _ = calls
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, [_entry(1)], argv)
    assert str(exc.value) == _USAGE
    assert recorded == []


def test_the_command_line_reaches_main():
    """The env carries no `PATH`, `GH_TOKEN` or `GITHUB_REPOSITORY`, so a mutated script can
    never write to a live repository; a write-first mutation dies on the missing variable with
    a different stderr. Mutation: `__main__` calls `run_annotated(lambda: main([]))` -> argv
    never reaches `main`, which reads stdin and fails on the missing `GITHUB_REPOSITORY`."""
    env = {k: os.environ[k] for k in ("SYSTEMROOT",) if k in os.environ}
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "patch-checks"), "completed"],
        input=json.dumps(_entry(1)) + "\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (1, "", _USAGE + "\n")


def test_an_exhausted_write_fails(monkeypatch, capsys, calls):
    """Mutations: degrade like a warning -> `main` returns instead of raising; restore "after
    retries" to the message -> it differs."""
    _, fail, _ = calls
    fail["1"] = _BAD_GATEWAY
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, [_entry(1), _entry(2)])
    assert exc.value.code == (
        "::error::failed to write apply check 1\n"
        "command failed (1): gh api -X PATCH repos/acme/demo/check-runs/1 --input -\n"
        "gh: Bad Gateway (HTTP 502)"
    )


def test_a_permanent_failure_is_not_retried(monkeypatch, calls):
    """Mutation: retry every failure (catch any `SystemExit` in the loop and call again) -> three
    calls."""
    recorded, fail, _ = calls
    fail["1"] = "gh: Validation Failed (HTTP 422)"
    with pytest.raises(SystemExit):
        _main(monkeypatch, [_entry(1)])
    assert len(recorded) == 1


def test_stdout_holds_only_the_ids_acknowledged_before_a_failure(monkeypatch, capsys, calls):
    """Mutation: print every input id up front -> stdout holds ids 1, 2 and 3."""
    _, fail, _ = calls
    fail["2"] = _BAD_GATEWAY
    with pytest.raises(SystemExit):
        _main(monkeypatch, [_entry(1), _entry(2), _entry(3)])
    assert capsys.readouterr().out == "1\n"


#: Not captured: gh's `gh: <message> (HTTP <code>)` form around the probed API message.
_SECONDARY = "gh: You have exceeded a secondary rate limit. (HTTP 403)"


def test_a_secondary_rate_limit_waits_60_s_and_completes(monkeypatch, capsys, calls):
    """Mutation: PATCH through `retried` instead of `write_retried` -> the 403 fails the loop."""
    recorded, fail, _ = calls
    slept = []
    fail["1"] = _SECONDARY

    def clear(_delay):
        slept.append(_delay)
        fail.pop("1")

    monkeypatch.setattr(_shipmate, "_sleep", clear)
    _main(monkeypatch, [_entry(1), _entry(2)])
    assert ([a for a, _ in recorded], slept) == ([_argv("1"), _argv("1"), _argv("2")], [60])
    assert capsys.readouterr().out == "1\n2\n"


def test_a_rate_limited_read_waits_60_s_and_writes(monkeypatch, capsys, calls):
    """The summary read sits between writes, so it draws the same limit. Mutation: read through
    `gh_json` (transient retries only) instead of `write_retried` -> the 403 fails the loop."""
    recorded, _, reads = calls
    path = f"repos/{_REPO}/check-runs/4"
    reads[path] = _SECONDARY
    slept = []

    def clear(_delay):
        slept.append(_delay)
        reads[path] = _check()

    monkeypatch.setattr(_shipmate, "_sleep", clear)
    _main(monkeypatch, [_entry(4, {"status": "queued"}, title="Not run")])
    assert (reads["calls"], slept) == ([path, path], [60])
    assert [json.loads(s) for _, s in recorded] == [
        {"status": "queued", "output": {"title": "Not run", "summary": _SUMMARY}}
    ]
