import pytest
from _loader import load_script

ra = load_script("register-app")


def test_main_stores_id_as_variable_and_pem_as_secret(monkeypatch):
    # Guards the id/pem parse+store: a swap (id<->pem) or wrong gh subcommand
    # would only surface during a real one-time registration otherwise.
    monkeypatch.setenv("MANIFEST_CODE", "code123")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if "conversions" in args[-1]:
            return '{"id": 42, "pem": "PRIVATE_KEY", "slug": "shipmate"}'
        return ""

    monkeypatch.setattr(ra, "_run", fake_run)
    ra.main()

    assert calls[0] == ["gh", "api", "-X", "POST", "app-manifests/code123/conversions"]
    assert [
        "gh",
        "variable",
        "set",
        "SHIPMATE_APP_ID",
        "--repo",
        "org/repo",
        "--body",
        "42",
    ] in calls
    assert [
        "gh",
        "secret",
        "set",
        "SHIPMATE_APP_PRIVATE_KEY",
        "--repo",
        "org/repo",
        "--body",
        "PRIVATE_KEY",
    ] in calls


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
