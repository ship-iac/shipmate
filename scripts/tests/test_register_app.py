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
