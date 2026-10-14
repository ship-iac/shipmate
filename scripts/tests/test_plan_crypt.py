import subprocess

import pytest
from _loader import load_script, step_by

pcx = load_script("plan-crypt")

# A plaintext OpenTofu plan is a zip archive, so the payload carries the zip local-file header.
PLAIN = b"PK\x03\x04SECRET_PLAN_BODY\nresource null_resource.x {}\n"
# The top-level keys, in order, of a plan OpenTofu v1.12.4 writes under an
# `encryption { plan { ... } }` block (pbkdf2 key provider, aes_gcm method); values shortened.
ENVELOPE = (
    b'{"meta":{"key_provider.pbkdf2.k":"e30="},"encrypted_data":"AAAA","encryption_version":"v0"}'
)
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
    """Mutations: `decrypt=False` in decrypt's `_openssl` call, and the plan is encrypted twice;
    drop the zip branch from `_is_plan`."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    pcx.decrypt(str(f))
    assert f.read_bytes() == PLAIN


def _raw_openssl(src, dst, *flags):
    subprocess.run(
        ["openssl", "enc", *flags, "-aes-256-ctr", "-pbkdf2", "-md", "sha256", "-salt"]
        + ["-in", str(src), "-out", str(dst), "-pass", "env:PLAN_PASSPHRASE"],
        check=True,
    )


_NOT_A_PLAN = (
    "::error::plan artifact does not decrypt to an OpenTofu plan: it was encrypted with another "
    "key derivation or a rotated passphrase, the plan and apply sides read different "
    "SHIPMATE_PLAN_PASSPHRASE values, or it is corrupt. Nothing was applied. "
    "docs/troubleshooting.md section `plan artifact does not decrypt to an OpenTofu plan` gives "
    "each remedy."
)


_RUNNER = (
    "Re-run the job, which clears a transient hosted-runner disk failure, or fix a self-hosted "
    "runner's openssl or disk."
)
_LONG_ENOUGH = b"Salted__01234567" + b"x" * 8


def _failing_openssl(monkeypatch, error):
    """Replace openssl with one that creates its output file, as openssl does, then fails."""

    def fake_run(args, check):
        with open(args[args.index("-out") + 1], "wb") as fh:
            fh.write(b"partial")
        raise error

    monkeypatch.setattr(pcx.subprocess, "run", fake_run)


@pytest.mark.parametrize("data", [b"", b"Salte", b"Salted__0123456"], ids=["0", "5", "15"])
def test_decrypt_refuses_a_truncated_artifact_before_openssl(tmp_path, monkeypatch, data):
    """Below the 16-byte header the artifact is truncated whatever it holds.

    Mutations: drop decrypt's truncated branch, and openssl's own failure takes the runner
    message; classify the magic before the length in `_header`, and 0 and 5 bytes read as
    plaintext.
    """
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path, data)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(f))
    assert exc.value.code == (
        f"::error::plan artifact `{f}` is truncated: it is shorter than its 16-byte salted "
        "header. Nothing was applied. Re-plan: comment shipmate plan or push, or after the merge "
        "open a new pull request touching the stack."
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_decrypt_openssl_failure_names_the_runner_and_leaves_no_temp(tmp_path, monkeypatch):
    """Mutations: drop `_openssl`'s `finally` cleanup, and `.crypt.tmp` stays; drop the
    `CalledProcessError` handler, and a traceback replaces the `::error::` line; pass
    `failure=None` from decrypt."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    _failing_openssl(monkeypatch, subprocess.CalledProcessError(1, ["openssl"]))
    f = _write(tmp_path, _LONG_ENOUGH)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(f))
    assert exc.value.code == (
        f"::error::plan artifact `{f}` could not be decrypted: openssl or the disk failed on the "
        f"runner. Nothing was applied. {_RUNNER}"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]
    assert f.read_bytes() == _LONG_ENOUGH


def test_decrypt_without_openssl_prints_an_error_not_a_traceback(tmp_path, monkeypatch):
    """Mutation: delete `_openssl`'s `except OSError` clause, and FileNotFoundError escapes."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    _failing_openssl(monkeypatch, FileNotFoundError(2, "No such file or directory", "openssl"))
    f = _write(tmp_path, _LONG_ENOUGH)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(f))
    assert exc.value.code == (
        f"::error::plan artifact `{f}` could not be decrypted: openssl or the disk failed on the "
        f"runner. Nothing was applied. {_RUNNER} Error: [Errno 2] No such file or directory: "
        "'openssl'"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_encrypt_refuses_a_missing_file(tmp_path, monkeypatch):
    """Mutation: drop encrypt's `isfile` check, and openssl's failure on the missing input
    takes the runner message."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    missing = str(tmp_path / "absent.otplan")
    with pytest.raises(SystemExit) as exc:
        pcx.encrypt(missing)
    assert exc.value.code == (
        f"::error::`{missing}` cannot be encrypted: the file does not exist. The step that writes "
        "it did not; its error is earlier in the job log."
    )
    assert list(tmp_path.iterdir()) == []


def test_encrypt_openssl_failure_names_the_runner(tmp_path, monkeypatch):
    """Mutation: drop `_RUNNER` from encrypt's message."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    _failing_openssl(monkeypatch, subprocess.CalledProcessError(1, ["openssl"]))
    f = _write(tmp_path)
    with pytest.raises(SystemExit) as exc:
        pcx.encrypt(str(f))
    assert exc.value.code == (
        f"::error::`{f}` could not be encrypted: openssl or the disk failed on the runner. "
        + _RUNNER
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_decrypt_refuses_a_default_iteration_ciphertext_and_keeps_it(tmp_path, monkeypatch):
    """Mutations: drop `-iter`/`600000` from `_openssl`, and OpenSSL's default 10,000 recovers
    the plan; pass no `accept` from decrypt; `os.replace` before the `accept` check, and the
    garbage replaces the ciphertext."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    plain, f = _write(tmp_path), tmp_path / "old.otplan"
    _raw_openssl(plain, f, "-iter", "10000")
    ciphertext = f.read_bytes()
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(f))
    assert exc.value.code == _NOT_A_PLAN
    assert f.read_bytes() == ciphertext
    assert sorted(p.name for p in tmp_path.iterdir()) == ["old.otplan", "stack.otplan"]


def test_decrypt_accepts_a_600000_iteration_sha256_ciphertext(tmp_path, monkeypatch):
    """Mutations: `-iter 600001` or `-md sha512` in `_openssl`, and the plan no longer decrypts."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    plain, f = _write(tmp_path), tmp_path / "new.otplan"
    _raw_openssl(plain, f, "-iter", "600000")
    pcx.decrypt(str(f))
    assert f.read_bytes() == PLAIN


def test_decrypt_accepts_an_opentofu_encrypted_plan(tmp_path, monkeypatch):
    """Mutation: drop the `encryption_version` branch from `_is_plan`."""
    f = _encrypted(tmp_path, monkeypatch, ENVELOPE)
    pcx.decrypt(str(f))
    assert f.read_bytes() == ENVELOPE


_ARGV = [
    "openssl",
    "enc",
    "-aes-256-ctr",
    "-pbkdf2",
    "-iter",
    "600000",
    "-md",
    "sha256",
    "-salt",
    "-in",
    "{path}",
    "-out",
    "{path}.crypt.tmp",
    "-pass",
    "env:PLAN_PASSPHRASE",
]


def test_openssl_argv_is_exactly_this(tmp_path, monkeypatch):
    """OpenSSL's defaults equal some of these flags, so only the recorded argv pins them.

    Mutations: drop `-md sha256` from `_openssl`; drop `-iter 600000`; move decrypt's `-d`.
    """
    calls = []

    def fake_run(args, check):
        calls.append(args)
        with open(args[args.index("-out") + 1], "wb") as fh:
            fh.write(b"Salted__01234567" if "-d" not in args else PLAIN)

    monkeypatch.setattr(pcx.subprocess, "run", fake_run)
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    path = str(_write(tmp_path))
    pcx.encrypt(path)
    pcx.decrypt(path)
    argv = [a.replace("{path}", path) for a in _ARGV]
    assert calls == [argv, argv[:2] + ["-d"] + argv[2:]]


def test_encrypt_derives_the_key_with_600000_iterations(tmp_path, monkeypatch):
    """Mutations: `-iter 600001` or `-md sha512` in `_openssl`, and the bytes no longer decrypt
    to the plan."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    out = tmp_path / "plain.otplan"
    _raw_openssl(f, out, "-d", "-iter", "600000")
    assert out.read_bytes() == PLAIN


def test_decrypt_wrong_passphrase_refuses(tmp_path, monkeypatch):
    """CTR decrypts a wrong key to garbage with exit 0; `_is_plan` refuses it.

    Mutation: pass no `accept` from decrypt.
    """
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    monkeypatch.setenv("PLAN_PASSPHRASE", "wrong donkey")
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(f))
    assert exc.value.code == _NOT_A_PLAN
    assert f.read_bytes()[:8] == pcx.MAGIC


def test_decrypt_refuses_a_zip_payload_that_is_not_a_plan(tmp_path, monkeypatch):
    """A zip end-of-archive record shares the `PK` prefix with the plan's local-file header.

    Mutation: compare decrypt's result against `b"PK"` alone.
    """
    f = _encrypted(tmp_path, monkeypatch, b"PK\x05\x06" + bytes(18))
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(f))
    assert exc.value.code == _NOT_A_PLAN


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
    """Mutations: drop the `gh cache delete` command; drop `--repo` from it; drop the plan/apply
    mismatch cause."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "ship-iac/sample")
    f = _encrypted(tmp_path, monkeypatch, STATE)
    monkeypatch.setenv("PLAN_PASSPHRASE", "wrong donkey")
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), KEY)
    assert exc.value.code == (
        "::error::state cache entry `state/app-net/dev-eu/123-1` does not decrypt to OpenTofu "
        "state: it was saved with another key derivation or a rotated passphrase, the plan and "
        "apply sides read different SHIPMATE_PLAN_PASSPHRASE values, or it is corrupt. tofu was "
        "not run. docs/troubleshooting.md section `state cache entry ... does not decrypt to "
        "OpenTofu state` says which command fits which cause. Delete this entry: `gh cache delete "
        "state/app-net/dev-eu/123-1 --repo ship-iac/sample`. Delete every entry under its prefix, "
        "which discards that state: `gh cache list --repo ship-iac/sample --key "
        "state/app-net/dev-eu/ --limit 1000 --json id --jq '.[].id' | xargs -n1 gh cache delete "
        "--repo ship-iac/sample`."
    )


@pytest.mark.parametrize("data", [b"", b"Salte", b"Salted__0123456"], ids=["0", "5", "15"])
def test_decrypt_state_refuses_a_truncated_entry_before_openssl(tmp_path, monkeypatch, data):
    """Below the 16-byte header the entry is truncated whatever it holds, so the remedy is to
    delete that one entry, not the purge-all a plaintext entry gets.

    Mutations: drop decrypt_state's truncated branch; classify the magic before the length in
    `_header`, and 0 and 5 bytes take the plaintext purge-all remedy.
    """
    monkeypatch.setenv("GITHUB_REPOSITORY", "ship-iac/sample")
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path, data)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), KEY)
    assert exc.value.code == (
        "::error::state cache entry `state/app-net/dev-eu/123-1` is truncated: it is shorter "
        "than its 16-byte salted header. tofu was not run. `gh cache delete "
        "state/app-net/dev-eu/123-1 --repo ship-iac/sample` deletes it, and the next restore uses "
        "the previous one."
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_decrypt_state_without_openssl_prints_an_error_not_a_traceback(tmp_path, monkeypatch):
    """Mutation: delete `_openssl`'s `except OSError` clause."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    _failing_openssl(monkeypatch, FileNotFoundError(2, "No such file or directory", "openssl"))
    f = _write(tmp_path, _LONG_ENOUGH)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), KEY)
    assert exc.value.code == (
        "::error::state cache entry `state/app-net/dev-eu/123-1` could not be decrypted: openssl "
        f"or the disk failed on the runner. tofu was not run. {_RUNNER} Error: [Errno 2] No such "
        "file or directory: 'openssl'"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_decrypt_state_openssl_failure_names_the_runner(tmp_path, monkeypatch):
    """Mutation: pass `failure=None` from decrypt_state."""
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    _failing_openssl(monkeypatch, subprocess.CalledProcessError(1, ["openssl"]))
    f = _write(tmp_path, _LONG_ENOUGH)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), KEY)
    assert exc.value.code == (
        "::error::state cache entry `state/app-net/dev-eu/123-1` could not be decrypted: openssl "
        f"or the disk failed on the runner. tofu was not run. {_RUNNER}"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_decrypt_state_wrong_passphrase_refuses(tmp_path, monkeypatch):
    """Mutations: delete decrypt_state's shape check; `os.replace` before the `accept` check in
    `_openssl`, and the garbage replaces the ciphertext."""
    f = _encrypted(tmp_path, monkeypatch, STATE)
    monkeypatch.setenv("PLAN_PASSPHRASE", "wrong donkey")
    with pytest.raises(SystemExit, match=r"^::error::state cache entry .* does not decrypt"):
        pcx.decrypt_state(str(f), KEY)
    assert f.read_bytes()[:8] == pcx.MAGIC


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


def test_decrypt_a_16_byte_header_only_artifact_reaches_the_plan_check(tmp_path, monkeypatch):
    """openssl decrypts a bare salted header to an empty file with exit 0.

    Mutation: let `_is_plan` accept an empty file.
    """
    monkeypatch.setenv("PLAN_PASSPHRASE", "correct horse")
    f = _write(tmp_path, b"Salted__01234567")
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(f))
    assert exc.value.code == _NOT_A_PLAN
