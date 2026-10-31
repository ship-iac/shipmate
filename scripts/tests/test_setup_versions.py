"""actions/setup resolves its tool versions with `scripts/tool-versions`.

Each version is the consumer table's `tools` entry, read from the default branch, else the
release's own `VERSIONS` pin. No action input selects a version: its one input, `tofu`, only
decides whether OpenTofu is installed at all.

The structural guards pin the shape that makes the script the source -- it runs before both
installers, and both installers read its outputs -- and the behavioural ones run the script
in-process against a hand-written VERSIONS fixture and a mocked contents read. One case runs the
repository's own file, because that is the file every consumer of this release reads.

The fail-closed property is what the behavioural cases exist for: `opentofu/setup-opentofu`
resolves an empty `tofu_version` as "latest", and a table read that fails must not install the
default in place of the consumer's version, so every refusal writes no output at all.
"""

import base64
import re

import pytest
from _loader import ENGINE, action_steps, action_yaml, load_script, step_by
from _shipmate import run_annotated

_ACTION = "setup"
_PYTHON_YAML = "$/actions/python-yaml"

#: Every step, in order, by name or, for the unnamed install, by `uses:`. Hand-written: PyYAML
#: must be present before the resolve step parses the table, and the resolve step must precede
#: both installers; one whole-list comparison pins that ordering.
_EXPECTED_STEPS = [
    _PYTHON_YAML,
    "Resolve versions",
    "Install OpenTofu",
    "Install Terramate",
    "Provider plugin cache",
]

#: The resolve step, whole. The token reads the table through the contents API; every job
#: calling `setup` holds `contents: read`.
_EXPECTED_RESOLVE = {
    "name": "Resolve versions",
    "id": "versions",
    "shell": "bash",
    "env": {"GH_TOKEN": "${{ github.token }}"},
    "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/tool-versions"',
}

#: The OpenTofu installer's whole `with:` block. `uses:` is deliberately absent -- it is a pin,
#: bumped on its own schedule, and pinning it here would red this guard on every pin bump.
_EXPECTED_TOFU_WITH = {
    "tofu_version": "${{ steps.versions.outputs.tofu }}",
    "tofu_wrapper": False,
    "checksums": "${{ steps.versions.outputs.tofu_sha256 }}",
}

#: The Terramate installer is a `run:` step; its whole `env:` block carries the version and the
#: pinned `sha256sum` lines.
_EXPECTED_TERRAMATE_ENV = {
    "SHIPMATE_TERRAMATE_VERSION": "${{ steps.versions.outputs.terramate }}",
    "SHIPMATE_TERRAMATE_SHA256": "${{ steps.versions.outputs.terramate_sha256 }}",
}

_A, _B, _C, _D, _E, _F = (c * 64 for c in "abcdef")

#: Two versions with their floors, the four default assets, and the two assets of the older tofu
#: the table may choose, plus one older terramate line that no lookup may return.
_FIXTURE_VERSIONS = (
    "terramate=9.9.9\nterramate_min=9.9.0\ntofu=8.8.8\ntofu_min=8.0.0\n"
    f"{_A}  terramate_9.9.9_linux_x86_64.tar.gz\n"
    f"{_B}  terramate_9.9.9_linux_arm64.tar.gz\n"
    f"{_E}  terramate_9.9.8_linux_x86_64.tar.gz\n"
    f"{_C}  tofu_8.8.8_linux_amd64.zip\n"
    f"{_D}  tofu_8.8.8_linux_arm64.zip\n"
    f"{_E}  tofu_8.8.7_linux_amd64.zip\n"
    f"{_F}  tofu_8.8.7_linux_arm64.zip\n"
)

#: A table naming no tools. Its `{vars: NAME}` reference is outside `tools`: setup receives no
#: variables, so resolving it would refuse in every job.
_TABLE = "layout: tf_vars\nenvironments:\n  dev:\n    region: {vars: DEV_REGION}\n"

#: The whole $GITHUB_OUTPUT `_TABLE` resolves to, hand-written: the Terramate lines whole for
#: the install step's lookup, the tofu digests hash-only for setup-opentofu's `checksums`.
_FIXTURE_OUTPUT = (
    "terramate=9.9.9\ntofu=8.8.8\n"
    "terramate_sha256<<SHIPMATE_EOF\n"
    f"{_A}  terramate_9.9.9_linux_x86_64.tar.gz\n"
    f"{_B}  terramate_9.9.9_linux_arm64.tar.gz\n"
    "SHIPMATE_EOF\n"
    f"tofu_sha256<<SHIPMATE_EOF\n{_C}\n{_D}\nSHIPMATE_EOF\n"
)

#: The contents read, whole: no `ref`, so GitHub answers from the default branch.
_CONTENTS_PATH = "repos/o/r/contents/.github/shipmate-config.yml"

_UNREADABLE = (
    "::error::.github/shipmate-config.yml could not be read from the default branch. The "
    "engine reads the environment table from the default branch, never from this branch, so "
    "the file must be merged there before the first plan.\n"
)


def _outputs(text):
    """Parse $GITHUB_OUTPUT text, `key=value` lines and `key<<DELIM` blocks alike."""
    got, lines = {}, iter(text.splitlines())
    for line in lines:
        if "<<" in line:
            key, delim = line.split("<<", 1)
            got[key] = "\n".join(iter(lines.__next__, delim))
        else:
            key, _, value = line.partition("=")
            got[key] = value
    return got


def test_the_steps_run_in_this_order():
    """Mutations: move the resolve step after the installers; delete the python-yaml step."""
    assert [s.get("name") or s.get("uses") for s in action_steps(_ACTION)] == _EXPECTED_STEPS


def test_the_resolve_step_runs_the_script_with_the_token():
    """Mutations: drop `GH_TOKEN`; point `run:` at another script; change the step `id`."""
    assert step_by(_ACTION, name="Resolve versions") == _EXPECTED_RESOLVE
    assert action_steps(_ACTION)[0] == {"uses": _PYTHON_YAML}


def test_both_installers_read_the_resolve_steps_outputs():
    """Reds when an installer is pointed at anything but the resolve step's outputs."""
    got = (
        step_by(_ACTION, name="Install OpenTofu").get("with"),
        step_by(_ACTION, name="Install Terramate").get("env"),
    )
    assert got == (_EXPECTED_TOFU_WITH, _EXPECTED_TERRAMATE_ENV)


#: The action's whole `inputs:` mapping. A version override input would read as a second source
#: beside VERSIONS, so any input beyond `tofu` reds this.
_EXPECTED_INPUTS = {
    "tofu": {
        "description": 'Whether to install OpenTofu. "false" for jobs that never run it.',
        "required": False,
        "default": "true",
    },
}


def test_the_only_input_is_the_tofu_switch():
    """Mutations: `default: "true"` -> `"false"`; add a `tofu-version` input."""
    assert action_yaml(_ACTION).get("inputs") == _EXPECTED_INPUTS


def test_only_the_opentofu_install_is_gated_on_the_tofu_input():
    """Composite inputs are strings, and expression string comparison ignores case, so
    anything but "false" in any case installs OpenTofu.

    Mutations: `!=` -> `==` in the `Install OpenTofu` step's `if:`; add
    `if: ${{ inputs.tofu != 'false' }}` to `Install Terramate`; add one to the python-yaml step.
    """
    got = {s.get("name") or s.get("uses"): s.get("if") for s in action_steps(_ACTION)}
    assert got == {
        _PYTHON_YAML: None,
        "Resolve versions": None,
        "Install OpenTofu": "${{ inputs.tofu != 'false' }}",
        "Install Terramate": None,
        "Provider plugin cache": None,
    }


def _blob(text):
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def _resolve(
    monkeypatch, capsys, tmp_path, *, versions=_FIXTURE_VERSIONS, table=_TABLE, fetch=None
):
    """Run `tool-versions` in-process against a fixture VERSIONS and a mocked contents read.

    Returns (exit code, the raw $GITHUB_OUTPUT text, stderr, the paths read, the VERSIONS path).
    `versions=None` writes no file; `versions` is written as bytes, so a CRLF reaches the script
    untranslated. `fetch` replaces the whole contents read; by default it answers `table`.
    """
    tv = load_script("tool-versions")
    path = tmp_path / "VERSIONS"
    if versions is not None:
        path.write_bytes(versions.encode("utf-8"))
    monkeypatch.setattr(tv.ec, "VERSIONS_PATH", path)
    calls = []

    def gh_json(p):
        calls.append(p)
        return fetch(p) if fetch else _blob(table)

    monkeypatch.setattr(tv.ec, "gh_json", gh_json)
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.delenv("SHIPMATE_GITHUB_VARS", raising=False)
    try:
        run_annotated(tv.main)
        code = 0
    except SystemExit as exc:
        code = exc.code
    # Bytes, because read_text's newline translation would hide a carriage return.
    return code, out.read_bytes().decode("utf-8"), capsys.readouterr().err, calls, path


def test_both_versions_and_their_digests_come_from_versions(monkeypatch, capsys, tmp_path):
    """A table naming no tools, holding a reference elsewhere, installs the release's pins.

    Mutations: read the table with `read_table` (the reference refuses without
    `SHIPMATE_GITHUB_VARS`); emit the Terramate digests into `tofu_sha256`; drop the version
    from the lookup prefix (the other-version lines reach the outputs).
    """
    code, out, err, _, _ = _resolve(monkeypatch, capsys, tmp_path)
    assert code == 0, err
    assert out == _FIXTURE_OUTPUT


def test_the_tables_tools_entry_wins_over_versions(monkeypatch, capsys, tmp_path):
    """Mutations: ignore `tools` (tofu resolves 8.8.8); look the digests up by the `VERSIONS`
    version instead of the resolved one."""
    table = _TABLE + "tools:\n  tofu: 8.8.7\n"
    code, out, err, _, _ = _resolve(monkeypatch, capsys, tmp_path, table=table)
    assert code == 0, err
    assert out == (
        "terramate=9.9.9\ntofu=8.8.7\n"
        "terramate_sha256<<SHIPMATE_EOF\n"
        f"{_A}  terramate_9.9.9_linux_x86_64.tar.gz\n"
        f"{_B}  terramate_9.9.9_linux_arm64.tar.gz\n"
        "SHIPMATE_EOF\n"
        f"tofu_sha256<<SHIPMATE_EOF\n{_E}\n{_F}\nSHIPMATE_EOF\n"
    )


def test_the_table_is_read_from_the_default_branch(monkeypatch, capsys, tmp_path):
    """Mutation: append `?ref=main` to the contents path."""
    code, _, err, calls, _ = _resolve(monkeypatch, capsys, tmp_path)
    assert code == 0, err
    assert calls == [_CONTENTS_PATH]


def test_an_invalid_tools_entry_refuses_and_writes_nothing(monkeypatch, capsys, tmp_path):
    """Mutation: skip the `tool_version_errors` refusal (the bad versions are written)."""
    table = _TABLE + "tools:\n  tofu: v8.8.8\n  terramate: 1.0.0\n"
    code, out, err, _, _ = _resolve(monkeypatch, capsys, tmp_path, table=table)
    assert (code, out) == (1, "")
    assert err == (
        "::error::tools.tofu is 'v8.8.8'; a version is written X.Y.Z in ASCII digits, such as "
        "1.13.2, with no v, pre-release or range.\n"
        "::error::tools.terramate is '1.0.0', below 9.9.0, the oldest terramate this engine "
        "supports.\n"
    )


def _gh_fails(path):
    raise SystemExit("::error::gh: Not Found (HTTP 404)")


@pytest.mark.parametrize(
    ("fetch", "logged"),
    [
        (_gh_fails, "gh: Not Found (HTTP 404)\n"),
        (lambda path: {"encoding": "none", "content": ""}, ""),
    ],
    ids=["gh-fails", "too-large"],
)
def test_an_unreadable_table_refuses_and_writes_nothing(
    monkeypatch, capsys, tmp_path, fetch, logged
):
    """A transient read failure must not install the default in place of the consumer's
    version. Mutation: fall back to the `VERSIONS` defaults when the read returns `None`."""
    code, out, err, _, _ = _resolve(monkeypatch, capsys, tmp_path, fetch=fetch)
    assert (code, out) == (1, "")
    assert err == logged + _UNREADABLE


def test_a_table_that_is_not_a_mapping_refuses_and_writes_nothing(monkeypatch, capsys, tmp_path):
    """Mutation: read the table with `load_config_text` alone (a list reaches
    `tool_version_errors`, and `"tools" in` a list is False)."""
    code, out, err, _, _ = _resolve(monkeypatch, capsys, tmp_path, table="- tools\n")
    assert (code, out) == (1, "")
    assert err == (
        "::error::.github/shipmate-config.yml must hold a mapping at the top level, got list\n"
    )


def test_unparseable_yaml_refuses_and_writes_nothing(monkeypatch, capsys, tmp_path):
    """Mutation: swallow the parse refusal and resolve an empty table (the defaults are
    written)."""
    code, out, err, _, _ = _resolve(monkeypatch, capsys, tmp_path, table="tools: [\n")
    assert (code, out) == (1, "")
    assert err.startswith("::error::.github/shipmate-config.yml is not valid YAML: "), err
    assert err.count("\n") == 1, err


def test_a_crlf_versions_file_yields_outputs_without_carriage_returns(
    monkeypatch, capsys, tmp_path
):
    """setup-opentofu compares each `checksums` line to a bare hex digest, and the install step
    compares asset names exactly, so a carriage return would fail every install.

    Two defences each keep the CR out, so the mutation breaks both: read the lines with
    `read_bytes().decode("utf-8").split("\\n")` and split each line's fields on `"  "`.
    """
    crlf = _FIXTURE_VERSIONS.replace("\n", "\r\n")
    code, out, err, _, _ = _resolve(monkeypatch, capsys, tmp_path, versions=crlf)
    assert code == 0, err
    assert out == _FIXTURE_OUTPUT


@pytest.mark.parametrize(("tool", "version"), [("terramate", "9.9.9"), ("tofu", "8.8.8")])
def test_a_version_without_digest_lines_refuses_and_writes_nothing(
    monkeypatch, capsys, tmp_path, tool, version
):
    """A version bump that left only the previous release's checksum lines.

    Mutation: drop the emptiness check on the digests.
    """
    fixture = _FIXTURE_VERSIONS.replace(f"{tool}_{version}_", f"{tool}_0.0.1_")
    code, out, err, _, path = _resolve(monkeypatch, capsys, tmp_path, versions=fixture)
    assert (code, out) == (1, "")
    assert err == (
        f"::error::{path} carries no sha256 line for {tool} {version}; "
        "paste the release's checksum lines.\n"
    )


def test_the_repositorys_own_versions_file_resolves(monkeypatch, capsys, tmp_path):
    """The cases above prove the logic; this one proves the file it runs against.

    The shape is asserted, never the numbers, or a tool-version bump reds this. Both Linux
    architectures of both tools must be pinned: a missing one resolves here and refuses only on
    that architecture's runners.

    Mutations: write `tofu = 1.12.4` into VERSIONS; delete the two tofu digest lines from it.
    """
    real = (ENGINE / "VERSIONS").read_bytes().decode("utf-8")
    code, out, err, _, _ = _resolve(monkeypatch, capsys, tmp_path, versions=real)
    assert code == 0, err
    got = _outputs(out)
    assert set(got) == {"terramate", "tofu", "terramate_sha256", "tofu_sha256"}, out
    v = got["terramate"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", v), out
    assert re.fullmatch(r"\d+\.\d+\.\d+", got["tofu"]), out
    terramate = {}
    for line in got["terramate_sha256"].split("\n"):
        digest, _, asset = line.partition("  ")
        terramate[asset] = digest
    assert set(terramate) == {
        f"terramate_{v}_linux_x86_64.tar.gz",
        f"terramate_{v}_linux_arm64.tar.gz",
    }, out
    tofu = got["tofu_sha256"].split("\n")
    digests = [*terramate.values(), *tofu]
    assert len(digests) == 4, out
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in digests), out
    assert len(set(digests)) == 4, out


def test_a_missing_versions_file_refuses_and_writes_nothing(monkeypatch, capsys, tmp_path):
    """Mutation: drop the `FileNotFoundError` handler in `engine_versions`."""
    code, out, err, calls, path = _resolve(monkeypatch, capsys, tmp_path, versions=None)
    assert (code, out, calls) == (1, "", [])
    assert err == (
        f"::error::{path} does not exist: it carries the terramate and tofu versions this "
        "release pins.\n"
    )


def test_a_missing_key_refuses_and_writes_nothing(monkeypatch, capsys, tmp_path):
    """A present file missing one key is the case an "install latest" fallback would hide.

    Mutation: drop the missing-key refusal in `engine_versions` (a `KeyError` escapes).
    """
    code, out, err, _, path = _resolve(monkeypatch, capsys, tmp_path, versions="terramate=9.9.9\n")
    assert (code, out) == (1, "")
    assert err == f"::error::{path} holds no terramate_min= line; restore it.\n"
