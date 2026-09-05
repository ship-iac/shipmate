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
    # The whole command list, not a scan: a `gh secret set` restored anywhere in the file puts
    # the App key in a repository secret, readable by any workflow on any branch, which is the
    # placement docs/github-app.md §5 exists to avoid.
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


def test_main_refuses_an_out_path_that_already_exists(monkeypatch, tmp_path):
    # POSIX applies the mode only on creation, so writing into a file left by an earlier
    # bootstrap would inherit its 0644. The refusal comes before the conversion call: refusing
    # after it costs an App key nothing can mint again.
    out = tmp_path / "key.pem"
    out.write_text("AN EARLIER KEY", encoding="utf-8")
    calls = _stub_run(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        ra.main(
            ["--name", "shipmate-acme", "--repo", "org/repo", "--out", str(out), "--code", "c123"]
        )

    assert str(out) in str(exc.value)
    assert out.read_text(encoding="utf-8") == "AN EARLIER KEY"
    assert calls == []


def test_main_refuses_an_out_path_whose_directory_does_not_exist(monkeypatch, tmp_path):
    # os.open raises here too, but only after the App exists, and then the PEM is in process
    # memory alone. Refuse while refusing is still free.
    out = tmp_path / "nope" / "key.pem"
    calls = _stub_run(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        ra.main(
            ["--name", "shipmate-acme", "--repo", "org/repo", "--out", str(out), "--code", "c123"]
        )

    assert str(out) in str(exc.value)
    assert calls == []


def test_the_app_id_is_printed_before_the_key_is_written(monkeypatch, tmp_path, capsys):
    # A refusal from _write_key leaves an App that exists on GitHub, and without the id the
    # operator cannot even find it to generate a replacement key.
    _stub_run(monkeypatch)
    monkeypatch.setattr(ra, "_write_key", lambda path, pem: (_ for _ in ()).throw(SystemExit("no")))

    with pytest.raises(SystemExit):
        ra.main(
            [
                "--name",
                "shipmate-acme",
                "--repo",
                "org/repo",
                "--out",
                str(tmp_path / "key.pem"),
                "--code",
                "c123",
            ]
        )

    assert "App created: id=42 slug=shipmate-acme" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("--repo", "org/../../evil"),
        ("--repo", "org"),
        ("--name", "shipmate' onload='alert(1)"),
    ],
)
def test_main_refuses_an_operator_value_before_it_reaches_gh(monkeypatch, tmp_path, field, value):
    # --repo's owner half is interpolated into the registration URL, and both halves into
    # `gh variable set --repo`. --name is embedded in the local HTML form, where a quote escapes
    # the attribute holding it.
    calls = _stub_run(monkeypatch)
    argv = {
        "--name": "shipmate-acme",
        "--repo": "org/repo",
        "--out": str(tmp_path / "key.pem"),
        "--code": "c123",
        field: value,
    }

    with pytest.raises(SystemExit) as exc:
        ra.main([token for pair in argv.items() for token in pair])

    assert repr(value) in str(exc.value)
    assert calls == []


def test_main_refuses_an_owner_that_could_be_read_as_a_flag(monkeypatch, tmp_path):
    # `--repo`'s value is its own argv element in `gh variable set --repo <value>`, so one
    # starting with '-' is the shape a CLI reads as a flag; GitHub logins cannot start with '-'
    # either. Written in the `--repo=` form deliberately: argparse refuses the space-separated
    # form, so that shape never reaches the regex and would prove nothing about it.
    calls = _stub_run(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        ra.main(
            [
                "--name",
                "shipmate-acme",
                "--repo=-evil/repo",
                "--out",
                str(tmp_path / "key.pem"),
                "--code",
                "c123",
            ]
        )

    assert str(exc.value) == "--repo must be <owner>/<repo>: '-evil/repo'"
    assert calls == []


def test_main_refuses_a_manifest_code_that_would_retarget_the_api_path(monkeypatch, tmp_path):
    # The code lands in `app-manifests/<code>/conversions` and arrives over a loopback socket, so
    # a '/' in it points the conversion at another endpoint. The refusal must not echo it,
    # because it converts into a private key.
    calls = _stub_run(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        ra.main(
            [
                "--name",
                "shipmate-acme",
                "--repo",
                "org/repo",
                "--out",
                str(tmp_path / "key.pem"),
                "--code",
                "../../user/repo/keys",
            ]
        )

    assert str(exc.value) == ("the manifest code must be letters, digits, '_' or '-'; refusing it.")
    assert calls == []


def test_out_is_required(monkeypatch):
    # An optional --out defaults to writing the key nowhere, which is the same failure as writing
    # it to a secret: the key is minted and then unreachable.
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
    """The capture is what keeps the single-use code off the clipboard. The browser asks for
    /favicon.ico on the same port, and answering that as a capture would end the wait with no
    code at all. A callback carrying a code but not this run's state is another App's
    registration, delivered by anything that can reach the port: converting it would write that
    App's key to --out and store its id as SHIPMATE_APP_ID."""
    ra._Handler.code = None
    ra._Handler.state = "this-runs-state"
    server = http.server.HTTPServer(("127.0.0.1", 0), ra._Handler)
    try:
        assert _get(server, "/favicon.ico") == 404
        assert ra._Handler.code is None
        assert _get(server, "/callback?code=forged&state=shipmate-setup") == 404
        assert ra._Handler.code is None
        assert _get(server, "/callback?code=abc123&state=this-runs-state") == 200
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
    # The argv carries the App private key and the one-time manifest code, so a failure must name
    # the subcommand and nothing else. Enforced only here, because the real failure needs a
    # broken gh.
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
    # gh quotes the URL it called, and the one-time manifest code is in that URL, so suppressing
    # the tool's own argv is not enough. Anything the caller declares as a secret must be
    # scrubbed from gh's message too.
    _failing_gh(monkeypatch, "gh: HTTP 404 (POST app-manifests/CODE123/conversions)\n")

    with pytest.raises(SystemExit):
        ra._run(
            ["gh", "api", "-X", "POST", "app-manifests/CODE123/conversions"], secrets=("CODE123",)
        )

    err = capsys.readouterr().err
    assert "CODE123" not in err
    assert ra.REDACTED in err
    assert "HTTP 404" in err  # the diagnosis itself survives
