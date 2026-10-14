import subprocess

import pytest
from _loader import load_script, step_by

pcx = load_script("plan-crypt")

PLAIN = b"SECRET_PLAN_BODY\nresource null_resource.x {}\n"
STATE = b'{"version":4,"serial":1}'
KEY = "state/app-net/dev-eu/123-1"
UNSET = (
    "::error::SHIPMATE_PLAN_PASSPHRASE is not set; it is required to encrypt and decrypt plan "
    "artifacts and local state. Set the secret and forward it to the shipmate workflows."
)


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
    """Mutations: make encrypt's empty-passphrase branch `return` (the old no-op); drop `and
    local state` from the refusal."""
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    f = _write(tmp_path)
    with pytest.raises(SystemExit) as exc:
        pcx.encrypt(str(f))
    assert exc.value.code == UNSET
    assert f.read_bytes() == PLAIN


def test_encrypt_with_passphrase_writes_salted_header(tmp_path, monkeypatch):
    """Mutation: `return` before encrypt's `_openssl` call, and the file stays plaintext."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    data = f.read_bytes()
    assert data[:8] == pcx.MAGIC and data != PLAIN


def test_round_trip_with_correct_passphrase(tmp_path, monkeypatch):
    """Mutation: `decrypt=False` in decrypt's `_openssl` call, and the plan is encrypted twice."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    pcx.decrypt(str(f))
    assert f.read_bytes() == PLAIN


def _raw_openssl(src, dst, *flags):
    subprocess.run(
        ["openssl", "enc", *flags, "-aes-256-ctr", "-pbkdf2", "-salt"]
        + ["-in", str(src), "-out", str(dst), "-pass", "env:PLAN_PASSPHRASE"],
        check=True,
    )


def test_decrypt_does_not_accept_a_default_iteration_ciphertext(tmp_path, monkeypatch):
    """CTR decrypts any key with exit 0, so only the bytes show which key decrypt derived.

    Mutation: drop `-iter`/`600000` from `_openssl`, and OpenSSL's default 10,000 matches.
    """
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    plain, f = _write(tmp_path), tmp_path / "old.otplan"
    _raw_openssl(plain, f, "-iter", "10000")
    pcx.decrypt(str(f))
    assert f.read_bytes() != PLAIN


def test_encrypt_derives_the_key_with_600000_iterations(tmp_path, monkeypatch):
    """Mutation: `-iter 600001` in `_openssl`, and the bytes no longer decrypt to the plan."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    out = tmp_path / "plain.otplan"
    _raw_openssl(f, out, "-d", "-iter", "600000")
    assert out.read_bytes() == PLAIN


def test_wrong_passphrase_yields_garbage_not_an_error(tmp_path, monkeypatch):
    # Documents the CTR fact: wrong pass decrypts to garbage WITHOUT raising.
    # The fail-safe is apply-cell's `tofu show` render step refusing the garbage plan file.
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


_PLAINTEXT = (
    "::error::state cache entry `state/app-net/dev-eu/123-1` is plaintext. Delete every entry "
    "under its prefix, then re-run: `gh cache list{repo} --key state/app-net/dev-eu/ --limit 1000 "
    "--json id --jq '.[].id' | xargs -n1 gh cache delete{repo}` (the restore matches by prefix, "
    "so deleting only `state/app-net/dev-eu/123-1` restores the next-older plaintext entry). "
    "Deleting discards that state: OpenTofu no longer tracks the resources it recorded. A sample "
    "stack's null and random resources re-create; a real resource can collide with or be "
    "duplicated by its re-creation."
)


@pytest.mark.parametrize(
    ("repository", "flag"), [("ship-iac/sample", " --repo ship-iac/sample"), (None, "")]
)
def test_decrypt_state_plaintext_refuses_naming_the_key(tmp_path, monkeypatch, repository, flag):
    """Mutations: drop decrypt_state's salt check; always emit `--repo` (reddens the unset
    case); drop `--repo` from the `gh cache delete` half of the command."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    if repository is None:
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    else:
        monkeypatch.setenv("GITHUB_REPOSITORY", repository)
    f = _write(tmp_path, STATE)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), KEY)
    assert exc.value.code == _PLAINTEXT.format(repo=flag)
    assert f.read_bytes() == STATE


def test_decrypt_state_wrong_passphrase_names_both_recoveries(tmp_path, monkeypatch):
    """Mutations: drop the corrupt-entry `gh cache delete` remedy; drop `--repo` from it; restore
    the two-cause sentence `SHIPMATE_PLAN_PASSPHRASE does not match the one that saved the entry,
    or the entry is corrupt`."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "ship-iac/sample")
    f = _encrypted(tmp_path, monkeypatch, STATE)
    monkeypatch.setenv("PLAN_PASSPHRASE", "wrong donkey")
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), KEY)
    assert exc.value.code == (
        "::error::state cache entry `state/app-net/dev-eu/123-1` does not decrypt to OpenTofu "
        "state: it was saved with another SHIPMATE_PLAN_PASSPHRASE or another key derivation, or "
        "it is corrupt. tofu was not run. If the entry is corrupt, `gh cache delete "
        "state/app-net/dev-eu/123-1 --repo ship-iac/sample` deletes it and the next restore uses "
        "the previous one. Otherwise every entry under the prefix was saved the same way: set the "
        "old secret back, which helps only after a passphrase rotation, or delete them all, which "
        "discards the state: `gh cache list --repo ship-iac/sample --key state/app-net/dev-eu/ "
        "--limit 1000 --json id --jq '.[].id' | xargs -n1 gh cache delete --repo ship-iac/sample`."
    )


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
    """Mutation: `decrypt=False` in decrypt_state's `_openssl` call; the result is ciphertext,
    which the shape check refuses."""
    f = _encrypted(tmp_path, monkeypatch, STATE)
    pcx.decrypt_state(str(f), KEY)
    assert f.read_bytes() == STATE


def test_decrypt_state_accepts_opentofu_encrypted_state(tmp_path, monkeypatch):
    """The top-level keys OpenTofu v1.12.4 writes with an `encryption { state { ... } }` block.

    Mutation: drop the `encryption_version` alternative from decrypt_state's shape check.
    """
    envelope = (
        b'{"serial":1,"lineage":"x","meta":{},"encrypted_data":"AAAA","encryption_version":"v0"}'
    )
    f = _encrypted(tmp_path, monkeypatch, envelope)
    pcx.decrypt_state(str(f), KEY)
    assert f.read_bytes() == envelope


def test_cli_require_without_passphrase_refuses(monkeypatch):
    """Mutation: delete main's `require` branch, and it prints the usage error instead."""
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit) as exc:
        pcx.main(["plan-crypt", "require"])
    assert exc.value.code == UNSET


def test_cli_require_with_passphrase_passes(monkeypatch):
    """Mutation: `if _passphrase():` in `_require_passphrase`, which refuses every caller."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    assert pcx.main(["plan-crypt", "require"]) is None


def test_plan_cell_requires_the_passphrase_before_init():
    """Its position is pinned in test_cell_step_order.py. Mutations: delete the step; drop its
    `PLAN_PASSPHRASE` env line, and every plan refuses."""
    assert step_by("plan-cell", name="Require the plan passphrase") == {
        "name": "Require the plan passphrase",
        "shell": "bash",
        "env": {"PLAN_PASSPHRASE": "${{ inputs.plan-passphrase }}"},
        "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/plan-crypt" require',
    }


def test_cli_decrypt_state_without_cache_key_prints_usage(tmp_path):
    """Mutation: let main accept `decrypt-state <file>` with 3 args."""
    with pytest.raises(SystemExit, match=r"^usage: plan-crypt"):
        pcx.main(["plan-crypt", "decrypt-state", str(_write(tmp_path))])
