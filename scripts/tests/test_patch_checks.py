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
_USAGE = "::error::usage: patch-checks (ids on stdin, no arguments)"


def _argv(cid):
    return ["gh", "api", "-X", "PATCH", f"repos/{_REPO}/check-runs/{cid}", "--input", "-"]


@pytest.fixture
def calls(monkeypatch):
    """`patch-checks` in this process with every retry sleep skipped. Returns `(recorded,
    fail)`: `recorded` holds each `run`'s `(args, stdin)`, `fail` maps an id to the stderr its
    PATCH fails with."""
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPO)
    monkeypatch.setattr(_shipmate, "_sleep", lambda _s: None)
    recorded = []
    fail = {}

    def run(args, secrets=(), stdin=None):
        recorded.append((args, stdin))
        cid = args[4].rsplit("/", 1)[1]
        if cid in fail:
            raise SystemExit(f"command failed (1): {' '.join(args)}\n{fail[cid]}")
        return "{}"

    monkeypatch.setattr(pc, "run", run)
    return recorded, fail


def _main(monkeypatch, ids, argv=()):
    monkeypatch.setattr(pc.sys, "stdin", io.StringIO("".join(f"{i}\n" for i in ids)))
    pc.main(list(argv))


def test_the_body_is_exactly_the_completion():
    """`output` would replace the plan text on the check. Mutation: add `"output"` -> the dict
    differs."""
    assert pc.BODY == {"status": "completed", "conclusion": "success"}


def test_each_id_is_patched_with_the_body_on_stdin(monkeypatch, capsys, calls):
    """Blank lines are skipped. Mutation: write the body to a temp file and pass `--input
    <path>` -> the args no longer end `--input -` and `stdin` is None."""
    recorded, _ = calls
    _main(monkeypatch, ["7", "", "9"])
    assert [a for a, _ in recorded] == [_argv("7"), _argv("9")]
    assert [json.loads(s) for _, s in recorded] == [pc.BODY, pc.BODY]
    assert capsys.readouterr() == ("7\n9\n", "")


@pytest.mark.parametrize("argv", [["completed"], ["queued"]])
def test_any_argument_refuses_before_a_write(monkeypatch, calls, argv):
    """Mutation: accept `queued` -> no refusal and a PATCH is sent."""
    recorded, _ = calls
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, ["1"], argv)
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
        input="1\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (1, "", _USAGE + "\n")


def test_an_exhausted_completion_fails(monkeypatch, capsys, calls):
    """Mutations: degrade like a warning -> `main` returns instead of raising; restore "after
    retries" to the message -> it differs."""
    _, fail = calls
    fail["1"] = _BAD_GATEWAY
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, ["1", "2"])
    assert exc.value.code == (
        "::error::failed to complete apply check 1\n"
        "command failed (1): gh api -X PATCH repos/acme/demo/check-runs/1 --input -\n"
        "gh: Bad Gateway (HTTP 502)"
    )


def test_a_permanent_failure_is_not_retried(monkeypatch, calls):
    """Mutation: retry every failure (catch any `SystemExit` in the loop and call again) -> three
    calls."""
    recorded, fail = calls
    fail["1"] = "gh: Validation Failed (HTTP 422)"
    with pytest.raises(SystemExit):
        _main(monkeypatch, ["1"])
    assert len(recorded) == 1


def test_stdout_holds_only_the_ids_acknowledged_before_a_failure(monkeypatch, capsys, calls):
    """Mutation: print every input id up front -> stdout holds ids 1, 2 and 3."""
    _, fail = calls
    fail["2"] = _BAD_GATEWAY
    with pytest.raises(SystemExit):
        _main(monkeypatch, ["1", "2", "3"])
    assert capsys.readouterr().out == "1\n"


#: Not captured: gh's `gh: <message> (HTTP <code>)` form around the probed API message.
_SECONDARY = "gh: You have exceeded a secondary rate limit. (HTTP 403)"


def test_a_secondary_rate_limit_waits_60_s_and_completes(monkeypatch, capsys, calls):
    """Mutation: PATCH through `retried` instead of `write_retried` -> the 403 fails the loop."""
    recorded, fail = calls
    slept = []
    fail["1"] = _SECONDARY

    def clear(_delay):
        slept.append(_delay)
        fail.pop("1")

    monkeypatch.setattr(_shipmate, "_sleep", clear)
    _main(monkeypatch, ["1", "2"])
    assert ([a for a, _ in recorded], slept) == ([_argv("1"), _argv("1"), _argv("2")], [60])
    assert capsys.readouterr().out == "1\n2\n"
