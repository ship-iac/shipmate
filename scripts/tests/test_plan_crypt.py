import pytest
from _loader import load_script

pcx = load_script("plan-crypt")

PLAIN = b"SECRET_PLAN_BODY\nresource null_resource.x {}\n"
STATE = b'{"version":4,"serial":1}'
KEY = "state/app-net/dev-eu/123-1"


def _write(tmp_path, data=PLAIN):
    f = tmp_path / "stack.otplan"
    f.write_bytes(data)
    return f


def _encrypted(tmp_path, monkeypatch, data):
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path, data)
    pcx.encrypt(str(f))
    return f


def test_encrypt_without_passphrase_refuses(tmp_path, monkeypatch):
    """Mutation: make encrypt's empty-passphrase branch `return` (the old no-op)."""
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    f = _write(tmp_path)
    with pytest.raises(SystemExit, match=r"^::error::SHIPMATE_PLAN_PASSPHRASE is not set"):
        pcx.encrypt(str(f))
    assert f.read_bytes() == PLAIN


def test_encrypt_with_passphrase_writes_salted_header(tmp_path, monkeypatch):
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    data = f.read_bytes()
    assert data[:8] == pcx.MAGIC and data != PLAIN


def test_round_trip_with_correct_passphrase(tmp_path, monkeypatch):
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    pcx.decrypt(str(f))
    assert f.read_bytes() == PLAIN


def test_wrong_passphrase_yields_garbage_not_an_error(tmp_path, monkeypatch):
    # Documents the CTR fact: wrong pass decrypts to garbage WITHOUT raising.
    # The real fail-safe is tofu rejecting the garbage plan at apply, not here.
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    monkeypatch.setenv("PLAN_PASSPHRASE", "wrong donkey")
    pcx.decrypt(str(f))
    assert f.read_bytes() != PLAIN


def test_decrypt_plaintext_plan_refuses(tmp_path, monkeypatch):
    """Mutation: delete decrypt's unsalted branch."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path)
    with pytest.raises(SystemExit, match=r"^::error::plan artifact is plaintext"):
        pcx.decrypt(str(f))


def test_decrypt_plan_without_passphrase_refuses(tmp_path, monkeypatch):
    """Mutation: delete decrypt's empty-passphrase branch."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit, match=r"^::error::SHIPMATE_PLAN_PASSPHRASE is not set"):
        pcx.decrypt(str(f))


def test_decrypt_state_without_passphrase_refuses(tmp_path, monkeypatch):
    """Mutation: delete decrypt_state's empty-passphrase branch."""
    f = _encrypted(tmp_path, monkeypatch, STATE)
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit, match=r"^::error::SHIPMATE_PLAN_PASSPHRASE is not set"):
        pcx.decrypt_state(str(f), KEY)


def test_decrypt_state_plaintext_refuses_naming_the_key(tmp_path, monkeypatch):
    """Mutation: drop decrypt_state's salt check."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path, STATE)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), KEY)
    assert exc.value.code == (
        "::error::state cache entry `state/app-net/dev-eu/123-1` is plaintext. Delete every "
        "entry under its prefix, then re-run: `gh cache list --key state/app-net/dev-eu/ "
        "--limit 1000 --json id --jq '.[].id' | xargs -n1 gh cache delete` (the restore "
        "matches by prefix, so deleting only `state/app-net/dev-eu/123-1` restores the "
        "next-older plaintext entry). Deleting discards that state: the next plan starts from "
        "empty state and plans to create the stack's resources again."
    )
    assert f.read_bytes() == STATE


def test_decrypt_state_wrong_passphrase_refuses(tmp_path, monkeypatch):
    """Mutation: delete decrypt_state's shape check."""
    f = _encrypted(tmp_path, monkeypatch, STATE)
    monkeypatch.setenv("PLAN_PASSPHRASE", "wrong donkey")
    with pytest.raises(SystemExit, match=r"^::error::state cache entry .* does not decrypt"):
        pcx.decrypt_state(str(f), KEY)


def test_decrypt_state_of_encrypted_empty_file_refuses(tmp_path, monkeypatch):
    """tofu reads an empty state file as no state.

    Mutation: delete decrypt_state's shape check.
    """
    f = _encrypted(tmp_path, monkeypatch, b"")
    with pytest.raises(SystemExit, match=r"^::error::state cache entry .* does not decrypt"):
        pcx.decrypt_state(str(f), KEY)


def test_decrypt_state_of_json_without_version_refuses(tmp_path, monkeypatch):
    """Mutation: drop the `version` condition from decrypt_state's shape check."""
    f = _encrypted(tmp_path, monkeypatch, b'{"a":1}')
    with pytest.raises(SystemExit, match=r"^::error::state cache entry .* does not decrypt"):
        pcx.decrypt_state(str(f), KEY)


def test_decrypt_state_round_trip_restores_exact_bytes(tmp_path, monkeypatch):
    f = _encrypted(tmp_path, monkeypatch, STATE)
    pcx.decrypt_state(str(f), KEY)
    assert f.read_bytes() == STATE


def test_cli_decrypt_state_without_cache_key_prints_usage(tmp_path):
    """Mutation: let main accept `decrypt-state <file>` with 3 args."""
    with pytest.raises(SystemExit, match=r"^usage: plan-crypt"):
        pcx.main(["plan-crypt", "decrypt-state", str(_write(tmp_path))])
