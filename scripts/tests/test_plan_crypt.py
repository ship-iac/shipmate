import hashlib
import hmac
import os
import subprocess

import pytest
from _loader import load_script, step_by

pcx = load_script("plan-crypt")

PLAIN = b"PK\x03\x04SECRET_PLAN_BODY\nresource null_resource.x {}\n"
# The top-level keys, in order, of a plan OpenTofu v1.12.4 writes under an
# `encryption { plan { ... } }` block (pbkdf2 key provider, aes_gcm method); values shortened.
ENVELOPE = (
    b'{"meta":{"key_provider.pbkdf2.k":"e30="},"encrypted_data":"AAAA","encryption_version":"v0"}'
)
STATE = b'{"version":4,"serial":1}'
KEY = "state/app-net/dev-eu/123-1"
NON_ASCII = "korrekt hæst ✓"
UNSET = (
    "::error::SHIPMATE_PLAN_PASSPHRASE is not set; it is required to encrypt and decrypt plan "
    "artifacts and local state. Set the secret and forward it to the shipmate workflows."
)
_RUNNER = (
    "Re-run the job, which clears a transient hosted-runner disk failure, or fix a self-hosted "
    "runner's openssl or disk."
)
_REPLAN = (
    "comment shipmate plan or push, or after the merge open a new pull request touching the stack."
)
#: Hand-written, never read from the module.
_HEADER = b"shipmate-crypt-v1\n"
_OPENSSL_ARGV = [
    "openssl",
    "enc",
    "-aes-256-ctr",
    "-pbkdf2",
    "-iter",
    "1",
    "-md",
    "sha256",
    "-nosalt",
    "-pass",
    "env:SHIPMATE_CRYPT_KEY",
]


def _write(tmp_path, data=PLAIN):
    f = tmp_path / "stack.otplan"
    f.write_bytes(data)
    return f


def _encrypted(tmp_path, monkeypatch, data, kind="plan", passphrase=NON_ASCII):
    monkeypatch.setenv("PLAN_PASSPHRASE", passphrase)
    f = _write(tmp_path, data)
    if kind == "plan":
        pcx.encrypt(str(f))
    else:
        pcx.encrypt_state(str(f), KEY)
    return f


def _unauthenticated(kind, path):
    if kind == "plan":
        return (
            f"::error::plan artifact `{path}` failed authentication: it was encrypted with another "
            "passphrase (a rotated one, or different values on <env>-plan and <env>-apply), or it "
            "is corrupt, truncated or altered. Nothing was applied. docs/troubleshooting.md "
            "section `plan artifact ... failed authentication` gives each remedy."
        )
    return (
        "::error::state cache entry `state/app-net/dev-eu/123-1` failed authentication: it was "
        "saved with another passphrase (a rotated one, or different values on <env>-plan and "
        "<env>-apply), or it is corrupt, truncated, altered or copied under another stack's or "
        "environment's key. tofu was not run. docs/troubleshooting.md section `state cache entry "
        "... failed authentication` tells the causes apart. Fix a passphrase with the secret: "
        "deleting an intact entry loses the state it holds."
    )


def _no_header(kind, path, repo=" --repo ship-iac/sample"):
    if kind == "plan":
        return (
            f"::error::plan artifact `{path}` is not a shipmate-crypt-v1 artifact: it was uploaded "
            "unencrypted or encrypted by an earlier engine release. Nothing was applied. "
            f"Re-plan: {_REPLAN}"
        )
    return (
        "::error::state cache entry `state/app-net/dev-eu/123-1` is not a shipmate-crypt-v1 "
        "entry: it was saved unencrypted or by an earlier engine release. Delete every entry "
        f"under its prefix, then re-run: `gh cache list{repo} --key state/app-net/dev-eu/ --limit "
        f"1000 --json id --jq '.[].id' | xargs -n1 gh cache delete{repo}` (the restore matches by "
        "prefix, so deleting only `state/app-net/dev-eu/123-1` restores the next-older entry, "
        "which is no shipmate-crypt-v1 entry either). Deleting discards that state: OpenTofu no "
        "longer tracks the resources it recorded. A sample stack's null and random resources "
        "re-create; a real resource can collide with or be duplicated by its re-creation."
    )


def _runner_failure(kind, path):
    if kind == "plan":
        return (
            f"::error::plan artifact `{path}` could not be decrypted: openssl or the disk failed "
            f"on the runner. Nothing was applied. {_RUNNER}"
        )
    return (
        "::error::state cache entry `state/app-net/dev-eu/123-1` could not be decrypted: openssl "
        f"or the disk failed on the runner. tofu was not run. {_RUNNER}"
    )


def _decrypt(kind, path):
    if kind == "plan":
        pcx.decrypt(str(path))
    else:
        pcx.decrypt_state(str(path), KEY)


def _record_openssl(monkeypatch):
    """Replace openssl with a recorder that fails the test's expectation by being called."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        raise AssertionError("openssl ran")

    monkeypatch.setattr(pcx.subprocess, "run", fake_run)
    return calls


def _failing_openssl(monkeypatch, error):
    def fake_run(args, **kwargs):
        raise error

    monkeypatch.setattr(pcx.subprocess, "run", fake_run)


KINDS = ["plan", "state"]


@pytest.fixture(autouse=True)
def _repository(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "ship-iac/sample")


def test_encrypt_without_passphrase_refuses(tmp_path, monkeypatch):
    """Mutations: make encrypt's empty-passphrase branch `return` (the old no-op); drop `and
    local state` from the refusal."""
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    f = _write(tmp_path)
    with pytest.raises(SystemExit) as exc:
        pcx.encrypt(str(f))
    assert exc.value.code == UNSET
    assert f.read_bytes() == PLAIN


@pytest.mark.parametrize(
    ("kind", "data"), [("plan", PLAIN), ("state", STATE), ("plan", b""), ("state", b"")]
)
def test_round_trip_with_a_non_ascii_passphrase(tmp_path, monkeypatch, kind, data):
    """Mutations: `decrypt=False` in `_decrypt`'s openssl call, and the body is encrypted twice;
    `return` before `_encrypt`'s `_replace`, and the file stays plaintext; `MIN_LEN` one higher,
    and an empty plaintext's file refuses."""
    f = _encrypted(tmp_path, monkeypatch, data, kind)
    assert f.read_bytes() != data
    _decrypt(kind, f)
    assert f.read_bytes() == data
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_encrypt_writes_the_v1_header(tmp_path, monkeypatch):
    """Mutation: change one byte of `MAGIC`."""
    assert _encrypted(tmp_path, monkeypatch, PLAIN).read_bytes()[:18] == _HEADER


def test_two_encrypts_draw_different_salts(tmp_path, monkeypatch):
    """Mutation: `salt = bytes(SALT_LEN)` in encrypt."""
    first = _encrypted(tmp_path, monkeypatch, PLAIN).read_bytes()
    second = _encrypted(tmp_path, monkeypatch, PLAIN).read_bytes()
    assert first[18:34] != second[18:34]


@pytest.mark.parametrize(("kind", "scope"), [("plan", b""), ("state", b"state/app-net/dev-eu/")])
def test_decrypt_accepts_a_hand_built_v1_file(tmp_path, monkeypatch, kind, scope):
    """The derivation is PBKDF2-HMAC-SHA256, 600,000 iterations, 32 bytes, over the file's salt,
    then the two labelled HMACs per kind; the tag covers the length-prefixed scope (a state
    entry's cache prefix) and the file. Every constant here is hand-written.

    Mutations: `ITERATIONS = 600001`; `"sha512"` in `_keys`' pbkdf2_hmac; `+ b"enx"` as the enc
    label; `+ b"max"` as the mac label; `len(s).to_bytes(8, "big")` in `_tag`.
    """
    salt = bytes(range(16))
    master = hashlib.pbkdf2_hmac("sha256", NON_ASCII.encode("utf-8"), salt, 600000)
    label = b"shipmate-crypt-v1 " + kind.encode() + b" "
    enc_key = hmac.new(master, label + b"enc", "sha256").digest()
    mac_key = hmac.new(master, label + b"mac", "sha256").digest()
    body = subprocess.run(
        [*_OPENSSL_ARGV[:-1], "env:HAND_BUILT_KEY"],
        input=PLAIN,
        stdout=subprocess.PIPE,
        env={**os.environ, "HAND_BUILT_KEY": enc_key.hex()},
        check=True,
    ).stdout
    head = b"shipmate-crypt-v1\n" + salt + body
    tagged = len(scope).to_bytes(4, "big") + scope + head
    f = _write(tmp_path, head + hmac.new(mac_key, tagged, "sha256").digest())
    monkeypatch.setenv("PLAN_PASSPHRASE", NON_ASCII)
    _decrypt(kind, f)
    assert f.read_bytes() == PLAIN


@pytest.mark.parametrize(("written", "read"), [("plan", "state"), ("state", "plan")])
def test_a_file_of_one_kind_fails_authentication_as_the_other(tmp_path, monkeypatch, written, read):
    """A state entry placed as a plan artifact, or the reverse, never reaches tofu.

    The kind is bound twice, by the labels and by the state scope, so it takes both mutations
    together: one label for both kinds (`label = b"shipmate-crypt-v1 "` in `_keys`) and an empty
    scope (`s = b""` in `_tag`). The hand-built test pins each label alone.
    """
    f = _encrypted(tmp_path, monkeypatch, STATE, written)
    data = f.read_bytes()
    with pytest.raises(SystemExit) as exc:
        _decrypt(read, f)
    assert exc.value.code == _unauthenticated(read, f)
    assert f.read_bytes() == data


@pytest.mark.parametrize(
    "other", ["state/app-net/prod-eu/123-1", "state/app-dns/dev-eu/123-1", "state/app-net/123-1"]
)
def test_a_state_entry_under_another_prefix_fails_authentication(tmp_path, monkeypatch, other):
    """An entry copied under another stack's or environment's key refuses.

    Mutation: `s = b""` in `_tag`, and the prefix leaves the tagged input.
    """
    f = _encrypted(tmp_path, monkeypatch, STATE, "state")
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), other)
    assert exc.value.code == _unauthenticated("state", f).replace(KEY, other)


def test_a_state_entry_restored_under_a_newer_key_of_its_prefix_decrypts(tmp_path, monkeypatch):
    """The restore matches by prefix, so an older entry arrives under its own, older run id.

    Mutation: bind the whole cache key (`_state_scope` returns `cache_key`).
    """
    f = _encrypted(tmp_path, monkeypatch, STATE, "state")
    pcx.decrypt_state(str(f), "state/app-net/dev-eu/999-2")
    assert f.read_bytes() == STATE


def test_cli_encrypt_state_round_trips_through_decrypt_state(tmp_path, monkeypatch):
    """Mutation: route main's `encrypt-state` to `encrypt`, and the entry carries the plan
    label."""
    monkeypatch.setenv("PLAN_PASSPHRASE", NON_ASCII)
    f = _write(tmp_path, STATE)
    pcx.main(["plan-crypt", "encrypt-state", str(f), KEY])
    pcx.main(["plan-crypt", "decrypt-state", str(f), KEY])
    assert f.read_bytes() == STATE


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("offset", [21, 54, -1], ids=["salt", "body", "tag"])
def test_a_flipped_byte_fails_authentication_before_openssl(tmp_path, monkeypatch, kind, offset):
    """Mutation: call `_openssl(body, enc_key, decrypt=True)` before the tag compare in
    `_decrypt`."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN, kind)
    data = bytearray(f.read_bytes())
    data[offset] ^= 0x01
    f.write_bytes(data)
    calls = _record_openssl(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _decrypt(kind, f)
    assert (exc.value.code, calls) == (_unauthenticated(kind, f), [])
    assert f.read_bytes() == data


@pytest.mark.parametrize("kind", KINDS)
def test_a_wrong_passphrase_fails_authentication_and_keeps_the_ciphertext(
    tmp_path, monkeypatch, kind
):
    """Mutation: drop the tag compare in `_decrypt`, and openssl's garbage replaces the
    ciphertext."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN, kind)
    ciphertext = f.read_bytes()
    monkeypatch.setenv("PLAN_PASSPHRASE", "wrong donkey")
    with pytest.raises(SystemExit) as exc:
        _decrypt(kind, f)
    assert exc.value.code == _unauthenticated(kind, f)
    assert f.read_bytes() == ciphertext
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_the_openssl_key_reaches_only_the_child_environment(tmp_path, monkeypatch):
    """Mutations: `os.environ[KEY_VAR] = enc_key.hex()` in `_openssl`; `-pass pass:<key>` in
    place of `env:`."""
    monkeypatch.delenv("SHIPMATE_CRYPT_KEY", raising=False)
    seen = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        seen.append((list(args), dict(kwargs.get("env") or os.environ)))
        return real_run(args, **kwargs)

    monkeypatch.setattr(pcx.subprocess, "run", spy)
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    pcx.decrypt(str(f))
    assert "SHIPMATE_CRYPT_KEY" not in os.environ
    assert len(seen) == 2
    for args, env in seen:
        key = env["SHIPMATE_CRYPT_KEY"]
        assert len(key) == 64
        assert not any(key in a for a in args)


def _pre_v1_ciphertext(tmp_path):
    """An earlier release's artifact: openssl's own output, with no shipmate header."""
    src = tmp_path / "plain.bin"
    src.write_bytes(PLAIN)
    return subprocess.run(
        ["openssl", "enc", "-aes-256-ctr", "-pbkdf2", "-iter", "600000", "-md", "sha256", "-salt"]
        + ["-in", str(src), "-pass", "env:PLAN_PASSPHRASE"],
        stdout=subprocess.PIPE,
        env={**os.environ, "PLAN_PASSPHRASE": NON_ASCII},
        check=True,
    ).stdout


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("case", ["zip", "state", "envelope", "salted", "earlier-release"])
def test_a_file_without_the_v1_header_refuses_as_pre_v1(tmp_path, monkeypatch, kind, case):
    """Mutations: drop the `Salted__` prefix from `_is_pre_v1`; drop its zip prefix; make its
    JSON branch `return False`."""
    data = {
        "zip": PLAIN,
        "state": STATE,
        "envelope": ENVELOPE,
        "salted": b"Salted__\x01\x02",
        "earlier-release": _pre_v1_ciphertext(tmp_path),
    }[case]
    f = _write(tmp_path, data)
    monkeypatch.setenv("PLAN_PASSPHRASE", NON_ASCII)
    calls = _record_openssl(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _decrypt(kind, f)
    assert (exc.value.code, calls) == (_no_header(kind, f), [])
    assert f.read_bytes() == data


@pytest.mark.parametrize(("repository", "flag"), [("ship-iac/sample", None), (None, "")])
def test_the_state_purge_command_names_the_repository_when_known(
    tmp_path, monkeypatch, repository, flag
):
    """Mutations: always emit `--repo` (reddens the unset case); drop `--repo` from the `gh cache
    delete` half of the command."""
    if repository is None:
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    f = _write(tmp_path, STATE)
    monkeypatch.setenv("PLAN_PASSPHRASE", NON_ASCII)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), KEY)
    expected = _no_header("state", f) if flag is None else _no_header("state", f, repo=flag)
    assert exc.value.code == expected


def _damaged(kind, whole):
    first = bytearray(whole)
    first[0] ^= 0x08  # `s` becomes `{`
    inner = bytearray(whole)
    inner[5] ^= 0x01
    return {
        "empty": b"",
        "cut-in-magic": whole[:11],
        "magic-byte": bytes(inner),
        "magic-brace": bytes(first),
        "cut-at-salt": whole[:18],
        "cut-in-salt": whole[:30],
        "cut-at-body": whole[:34],
        "cut-in-body": whole[:60],
        "one-below-minimum": whole[:65],
        "cut-tag-byte": whole[:-1],
    }[kind]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "damage",
    [
        "empty",
        "cut-in-magic",
        "magic-byte",
        "magic-brace",
        "cut-at-salt",
        "cut-in-salt",
        "cut-at-body",
        "cut-in-body",
        "one-below-minimum",
        "cut-tag-byte",
    ],
)
def test_a_cut_or_damaged_v1_file_fails_authentication_not_the_purge(
    tmp_path, monkeypatch, kind, damage
):
    """Anything that is not a whole, verified v1 file is an authentication failure, never the
    pre-v1 purge.

    A file shorter than a v1 file with an empty body, or one without the v1 header, refuses
    before the key derivation. Only cut-tag-byte, whose header is intact and whose length is at
    least the minimum, derives a key; magic-byte and magic-brace keep the whole length and derive
    none.

    Mutations: `if not data.startswith(MAGIC): raise SystemExit(no_header)` in place of
    `_is_pre_v1` in `_decrypt` (reddens empty, cut-in-magic, magic-byte and magic-brace); drop
    `len(data) < MIN_LEN or` (reddens cut-at-salt, cut-in-salt, cut-at-body, cut-in-body and
    one-below-minimum); drop `or not data.startswith(MAGIC)` (reddens magic-byte and
    magic-brace).
    """
    f = _encrypted(tmp_path, monkeypatch, PLAIN, kind)
    data = _damaged(damage, f.read_bytes())
    f.write_bytes(data)
    calls = _record_openssl(monkeypatch)
    derivations = 0
    real_pbkdf2 = hashlib.pbkdf2_hmac

    def pbkdf2(*args):
        nonlocal derivations
        derivations += 1
        return real_pbkdf2(*args)

    monkeypatch.setattr(pcx.hashlib, "pbkdf2_hmac", pbkdf2)
    with pytest.raises(SystemExit) as exc:
        _decrypt(kind, f)
    assert (exc.value.code, calls) == (_unauthenticated(kind, f), [])
    assert derivations == (1 if damage == "cut-tag-byte" else 0)
    assert f.read_bytes() == data


@pytest.mark.parametrize("kind", KINDS)
def test_an_openssl_failure_after_a_good_tag_names_the_runner(tmp_path, monkeypatch, kind):
    """Mutation: drop `_replace`'s `CalledProcessError` handler, and a traceback replaces the
    `::error::` line."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN, kind)
    ciphertext = f.read_bytes()
    _failing_openssl(monkeypatch, subprocess.CalledProcessError(1, ["openssl"]))
    with pytest.raises(SystemExit) as exc:
        _decrypt(kind, f)
    assert exc.value.code == _runner_failure(kind, f)
    assert f.read_bytes() == ciphertext
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_missing_openssl_prints_an_error_not_a_traceback(tmp_path, monkeypatch, kind):
    """Mutation: delete `_replace`'s `except OSError` clause, and FileNotFoundError escapes."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN, kind)
    _failing_openssl(monkeypatch, FileNotFoundError(2, "No such file or directory", "openssl"))
    with pytest.raises(SystemExit) as exc:
        _decrypt(kind, f)
    assert exc.value.code == (
        f"{_runner_failure(kind, f)} Error: [Errno 2] No such file or directory: 'openssl'"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_openssl_argv_is_exactly_this(tmp_path, monkeypatch):
    """OpenSSL's defaults equal some of these flags, so only the recorded argv pins them.

    Mutations: drop `-md sha256` from `_openssl`; `-iter 2`; move decrypt's `-d`.
    """
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        out = PLAIN if "-d" in args else bytes(b ^ 0x5A for b in kwargs["input"])
        return subprocess.CompletedProcess(args, 0, stdout=out)

    monkeypatch.setattr(pcx.subprocess, "run", fake_run)
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    pcx.decrypt(str(f))
    assert calls == [_OPENSSL_ARGV, [*_OPENSSL_ARGV[:2], "-d", *_OPENSSL_ARGV[2:]]]
    assert f.read_bytes() == PLAIN


def test_encrypt_openssl_failure_names_no_re_run(tmp_path, monkeypatch):
    """An apply cell's state save can call encrypt after its apply ran, where a re-run would
    apply the stored plan again; the message names the cause and points at troubleshooting only.

    Mutation: append `_RUNNER` to encrypt's message.
    """
    monkeypatch.setenv("PLAN_PASSPHRASE", NON_ASCII)
    _failing_openssl(monkeypatch, subprocess.CalledProcessError(1, ["openssl"]))
    f = _write(tmp_path)
    with pytest.raises(SystemExit) as exc:
        pcx.encrypt(str(f))
    assert exc.value.code == (
        f"::error::`{f}` could not be encrypted: openssl or the disk failed on the runner, so it "
        "was not uploaded or saved. docs/troubleshooting.md section `... could not be encrypted: "
        "openssl or the disk failed on the runner` says what to do for a plan cell and for an "
        "apply cell's state save."
    )
    assert f.read_bytes() == PLAIN
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stack.otplan"]


def test_a_temp_file_that_cannot_be_removed_is_named(tmp_path, monkeypatch, capsys):
    """A decrypt whose replace fails leaves the plaintext in `.crypt.tmp`; a failed removal must
    not go quiet.

    Mutation: swallow the `os.remove` failure in `_replace`'s `finally` without printing.
    """
    f = _encrypted(tmp_path, monkeypatch, PLAIN)

    def deny(*args):
        raise PermissionError(13, "Permission denied", args[0])

    monkeypatch.setattr(pcx.os, "replace", deny)
    monkeypatch.setattr(pcx.os, "remove", deny)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(f))
    assert exc.value.code == (
        f"{_runner_failure('plan', f)} Error: "
        f"{PermissionError(13, 'Permission denied', f'{f}.crypt.tmp')}"
    )
    assert capsys.readouterr().out == (
        f"::error::could not remove the temporary file `{f}.crypt.tmp`; delete it, because after "
        "a decrypt it can hold the plaintext.\n"
    )


def test_decrypt_plan_without_passphrase_refuses(tmp_path, monkeypatch):
    """Mutation: delete `_decrypt`'s `_require_passphrase` call."""
    f = _encrypted(tmp_path, monkeypatch, PLAIN)
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(f))
    assert exc.value.code == UNSET


def test_decrypt_state_without_passphrase_refuses(tmp_path, monkeypatch):
    """Mutation: delete `_decrypt`'s `_require_passphrase` call."""
    f = _encrypted(tmp_path, monkeypatch, STATE, "state")
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(f), KEY)
    assert exc.value.code == UNSET


def test_cli_require_without_passphrase_refuses(monkeypatch):
    """Mutation: delete main's `require` branch, and it prints the usage error instead."""
    monkeypatch.delenv("PLAN_PASSPHRASE", raising=False)
    with pytest.raises(SystemExit) as exc:
        pcx.main(["plan-crypt", "require"])
    assert exc.value.code == UNSET


def test_cli_require_with_passphrase_passes(monkeypatch):
    """Mutation: `if _passphrase():` in `_require_passphrase`, which refuses every caller."""
    monkeypatch.setenv("PLAN_PASSPHRASE", NON_ASCII)
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


def _os_error(path):
    """The platform's own text for a missing file, which differs between Windows and Linux."""
    try:
        open(path, "rb").close()
    except OSError as exc:
        return str(exc)
    raise AssertionError(f"{path} exists")


def test_decrypt_a_missing_artifact_prints_an_error_not_a_traceback(tmp_path, monkeypatch):
    """Mutation: drop the `except OSError` around `_decrypt`'s read."""
    monkeypatch.setenv("PLAN_PASSPHRASE", NON_ASCII)
    missing = tmp_path / "absent.otplan"
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt(str(missing))
    assert exc.value.code == (
        f"::error::plan artifact `{missing}` cannot be read. Nothing was applied. An artifact "
        "downloaded without stack.otplan needs a re-plan: comment shipmate plan or push, or after "
        "the merge open a new pull request touching the stack. For a disk failure on the runner, "
        "the refusal comes before any apply step, so a re-run of the job is safe. Error: "
        f"{_os_error(missing)}"
    )


def test_decrypt_state_a_missing_entry_prints_an_error_not_a_traceback(tmp_path, monkeypatch):
    """Mutations: drop the `except OSError` around `_decrypt`'s read; drop `the restore may not
    have written the file` from decrypt_state's refusal."""
    monkeypatch.setenv("PLAN_PASSPHRASE", NON_ASCII)
    missing = tmp_path / "absent.tfstate"
    with pytest.raises(SystemExit) as exc:
        pcx.decrypt_state(str(missing), KEY)
    assert exc.value.code == (
        "::error::state cache entry `state/app-net/dev-eu/123-1` cannot be read: the restore may "
        "not have written the file, or the runner's disk failed. tofu was not run. The refusal "
        "comes before any apply step, so a re-run of the job is safe. Error: "
        f"{_os_error(missing)}"
    )
