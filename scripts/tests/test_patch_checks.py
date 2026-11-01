import io
import json

import _shipmate
import pytest
from _loader import load_script

pc = load_script("patch-checks")

_REPO = "acme/demo"
_BAD_GATEWAY = "gh: Bad Gateway (HTTP 502)"


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


def _main(monkeypatch, status, ids):
    monkeypatch.setattr(pc.sys, "stdin", io.StringIO("".join(f"{i}\n" for i in ids)))
    pc.main([status])


def test_bodies_are_exactly_the_three_status_writes():
    """None carries `output`, which would replace the plan text on the check. Mutation: add
    `"output"` to the `in_progress` body -> the dict differs."""
    assert pc.BODIES == {
        "in_progress": {"status": "in_progress"},
        "queued": {"status": "queued"},
        "completed": {"status": "completed", "conclusion": "success"},
    }


@pytest.mark.parametrize(
    ("status", "body"),
    [
        ("in_progress", {"status": "in_progress"}),
        ("queued", {"status": "queued"}),
        ("completed", {"status": "completed", "conclusion": "success"}),
    ],
)
def test_each_id_is_patched_with_its_body_on_stdin(monkeypatch, capsys, calls, status, body):
    """Blank lines are skipped. Mutation: write the body to a temp file and pass `--input
    <path>` -> the args no longer end `--input -` and `stdin` is None."""
    recorded, _ = calls
    _main(monkeypatch, status, ["7", "", "9"])
    assert [a for a, _ in recorded] == [_argv("7"), _argv("9")]
    assert [json.loads(s) for _, s in recorded] == [body, body]
    assert capsys.readouterr() == ("7\n9\n", "")


_RETRY = (
    "::warning::command failed (1): gh api -X PATCH repos/acme/demo/check-runs/1 --input -"
    "%0Agh: Bad Gateway (HTTP 502)\n"
)


@pytest.mark.parametrize(
    ("status", "warning"),
    [
        (
            "in_progress",
            (
                "::warning::failed to set in_progress on apply check 1; 2 left unpatched"
                "%0Acommand failed (1): gh api -X PATCH repos/acme/demo/check-runs/1 --input -"
                "%0Agh: Bad Gateway (HTTP 502)\n"
            ),
        ),
        (
            "queued",
            (
                "::warning::failed to set queued on apply check 1; 2 left unpatched"
                "%0Acommand failed (1): gh api -X PATCH repos/acme/demo/check-runs/1 --input -"
                "%0Agh: Bad Gateway (HTTP 502)\n"
            ),
        ),
    ],
)
def test_a_degrading_write_stops_at_the_first_exhausted_id_and_warns_once(
    monkeypatch, capsys, calls, status, warning
):
    """Mutation: raise `SystemExit(1)` on the exhausted id -> the call exits nonzero; second:
    `continue` to the next id -> the second id is PATCHed three times too; third: count
    `len(ids) - n + 10` left -> the warning says 12."""
    recorded, fail = calls
    fail.update({"1": _BAD_GATEWAY, "2": _BAD_GATEWAY})
    _main(monkeypatch, status, ["1", "2"])
    assert [a for a, _ in recorded] == [_argv("1")] * 3
    assert capsys.readouterr() == ("", _RETRY + _RETRY + warning)


def test_a_degrading_write_does_not_retry_a_permanent_failure(monkeypatch, capsys, calls):
    """Mutation: retry every failure -> three calls and two retry warnings."""
    recorded, fail = calls
    fail["1"] = "gh: Validation Failed (HTTP 422)"
    _main(monkeypatch, "queued", ["1", "2"])
    assert [a for a, _ in recorded] == [_argv("1")]
    assert capsys.readouterr() == (
        "",
        (
            "::warning::failed to set queued on apply check 1; 2 left unpatched"
            "%0Acommand failed (1): gh api -X PATCH repos/acme/demo/check-runs/1 --input -"
            "%0Agh: Validation Failed (HTTP 422)\n"
        ),
    )


def test_an_exhausted_completion_fails(monkeypatch, capsys, calls):
    """Mutation: degrade `completed` like the others -> `main` returns instead of raising."""
    _, fail = calls
    fail["1"] = _BAD_GATEWAY
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, "completed", ["1", "2"])
    assert str(exc.value).startswith("::error::failed to complete apply check 1 after retries")


def test_a_permanent_failure_is_not_retried(monkeypatch, calls):
    """Mutation: retry every failure (catch any `SystemExit` in the loop and call again) -> three
    calls."""
    recorded, fail = calls
    fail["1"] = "gh: Validation Failed (HTTP 422)"
    with pytest.raises(SystemExit):
        _main(monkeypatch, "completed", ["1"])
    assert len(recorded) == 1


def test_stdout_holds_only_the_ids_acknowledged_before_a_failure(monkeypatch, capsys, calls):
    """The revert reads stdout as the completed ids. Mutation: print every input id up front ->
    stdout holds ids 1, 2 and 3."""
    _, fail = calls
    fail["2"] = _BAD_GATEWAY
    with pytest.raises(SystemExit):
        _main(monkeypatch, "completed", ["1", "2", "3"])
    assert capsys.readouterr().out == "1\n"


def test_an_unknown_status_refuses(monkeypatch, calls):
    """Mutation: default an unknown status to a body -> no refusal and a PATCH is sent."""
    recorded, _ = calls
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, "success", ["1"])
    assert str(exc.value).startswith("::error::")
    assert recorded == []
