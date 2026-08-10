import http.client
import http.server
import stat
import sys
import threading

import pytest
from _loader import load_script

ra = load_script("register-app")


def _stub_run(monkeypatch):
    """Record every `gh` argv; answer the conversion call with a fixed App."""
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if "conversions" in args[-1]:
            return '{"id": 42, "pem": "PRIVATE_KEY", "slug": "shipmate-acme"}'
        return ""

    monkeypatch.setattr(ra, "_run", fake_run)
    return calls


def test_main_writes_the_key_to_a_file_and_creates_no_repository_secret(monkeypatch, tmp_path):
    # The whole command list, not a scan: a `gh secret set` restored anywhere in
    # the file puts the App key in a repository secret, readable by any workflow
    # on any branch -- the exact placement docs/github-app.md §5 exists to avoid.
    out = tmp_path / "key.pem"
    calls = _stub_run(monkeypatch)

    ra.main(["--name", "shipmate-acme", "--repo", "org/repo", "--out", str(out), "--code", "c123"])

    assert calls == [
        ["gh", "api", "-X", "POST", "app-manifests/c123/conversions"],
        ["gh", "variable", "set", "SHIPMATE_APP_ID", "--repo", "org/repo", "--body", "42"],
    ]
    assert out.read_text(encoding="utf-8") == "PRIVATE_KEY"
    if sys.platform != "win32":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_out_is_required(monkeypatch):
    # An optional --out defaults to writing the key nowhere, which is the same
    # failure as writing it to a secret: the key is minted and then unreachable.
    _stub_run(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        ra.main(["--name", "shipmate-acme", "--repo", "org/repo", "--code", "c123"])

    assert exc.value.code == 2


def _get(server, path):
    handled = threading.Thread(target=server.handle_request, daemon=True)
    handled.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    conn.request("GET", path)
    status = conn.getresponse().status
    conn.close()
    handled.join(5)
    return status


def test_the_listener_captures_the_code_and_refuses_a_request_without_one():
    # The capture is what keeps the single-use code off the clipboard, and the
    # browser asks for /favicon.ico on the same port -- answering that as a
    # capture would end the wait with no code at all.
    ra._Handler.code = None
    server = http.server.HTTPServer(("127.0.0.1", 0), ra._Handler)
    try:
        assert _get(server, "/favicon.ico") == 404
        assert ra._Handler.code is None
        assert _get(server, "/callback?code=abc123") == 200
        assert ra._Handler.code == "abc123"
    finally:
        server.server_close()


def _failing_gh(monkeypatch, stderr):
    class _P:
        returncode = 1
        stdout = ""

    _P.stderr = stderr
    monkeypatch.setattr(ra.subprocess, "run", lambda *a, **kw: _P())


def test_run_error_never_echoes_the_argv(monkeypatch, capsys):
    # The argv carries the App private key and the one-time manifest code, so a
    # failure must name the subcommand and nothing else. Enforced only here --
    # the real failure needs a broken gh.
    _failing_gh(monkeypatch, "gh: HTTP 403\n")

    try:
        ra._run(
            ["gh", "secret", "set", "SHIPMATE_APP_PRIVATE_KEY", "--body", "SECRET_PEM"],
            secrets=("SECRET_PEM",),
        )
    except SystemExit as exc:
        message = str(exc)
    else:
        raise AssertionError("a nonzero gh exit must raise")

    assert "SECRET_PEM" not in message
    assert "SECRET_PEM" not in capsys.readouterr().err
    assert "gh secret" in message


def test_run_error_scrubs_secrets_out_of_ghs_own_stderr(monkeypatch, capsys):
    # gh quotes the URL it called, and the one-time manifest code is IN that URL
    # -- so suppressing our own argv is not enough. Anything the caller declares
    # as a secret must be scrubbed from gh's message too.
    _failing_gh(monkeypatch, "gh: HTTP 404 (POST app-manifests/CODE123/conversions)\n")

    with pytest.raises(SystemExit):
        ra._run(
            ["gh", "api", "-X", "POST", "app-manifests/CODE123/conversions"], secrets=("CODE123",)
        )

    err = capsys.readouterr().err
    assert "CODE123" not in err
    assert ra.REDACTED in err
    assert "HTTP 404" in err  # the diagnosis itself survives
