# scripts/tests/test_plan_crypt.py
import pytest
from _loader import load_script

pcx = load_script("plan-crypt")

PLAIN = b"SECRET_PLAN_BODY\nresource null_resource.x {}\n"


def _write(tmp_path, data=PLAIN):
    f = tmp_path / "stack.otplan"
    f.write_bytes(data)
    return f


def test_encrypt_without_passphrase_is_byte_identical_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    f = _write(tmp_path)
    pcx.encrypt(str(f))
    assert f.read_bytes() == PLAIN  # untouched


def test_encrypt_with_passphrase_writes_salted_header(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path)
    pcx.encrypt(str(f))
    data = f.read_bytes()
    assert data[:8] == pcx.MAGIC and data != PLAIN


def test_round_trip_with_correct_passphrase(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path)
    pcx.encrypt(str(f))
    pcx.decrypt(str(f))
    assert f.read_bytes() == PLAIN


def test_wrong_passphrase_yields_garbage_not_an_error(tmp_path, monkeypatch):
    # Documents the CTR fact: wrong pass decrypts to garbage WITHOUT raising.
    # The real fail-safe is tofu rejecting the garbage plan at apply, not here.
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path)
    pcx.encrypt(str(f))
    monkeypatch.setenv("PLAN_PASSPHRASE", "wrong donkey")
    pcx.decrypt(str(f))  # does not raise
    assert f.read_bytes() != PLAIN  # garbage, not the original


def test_decrypt_plaintext_with_passphrase_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path)  # plaintext, no Salted__
    with pytest.raises(SystemExit, match="plaintext"):
        pcx.decrypt(str(f))


def test_decrypt_encrypted_without_passphrase_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path)
    pcx.encrypt(str(f))
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit, match="no plan-passphrase|not configured"):
        pcx.decrypt(str(f))


def test_decrypt_plaintext_without_passphrase_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    f = _write(tmp_path)
    pcx.decrypt(str(f))
    assert f.read_bytes() == PLAIN
