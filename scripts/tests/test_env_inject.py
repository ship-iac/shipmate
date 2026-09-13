"""`scripts/env-inject`: the single writer of a cell's identity variables.

Reddens on: uppercasing an injected name (`TF_VAR_ENV` for `TF_VAR_env`), defaulting an absent
legacy binding to the empty string, dropping a set-but-empty one, writing `NAME=value` instead of
a heredoc, falling through on an unknown or a `table` mode, and returning a fixed heredoc
delimiter that a value's own text can collide with.
"""

import pytest
from _loader import load_script

env_inject = load_script("env-inject")

TABLE_REFUSAL = (
    "::error::SHIPMATE_CONFIG_MODE=table, but this engine version resolves "
    "identity from the legacy variables only. Re-pin to a version that "
    "implements the environment table, or unset the repository's `layout`."
)
UNKNOWN_REFUSAL = (
    "::error::SHIPMATE_CONFIG_MODE must be 'legacy' or 'table', got 'Legacy'. "
    "The detect job sets it; an unexpected value means the cell and the detect "
    "that produced its row disagree."
)


def _read_github_env(text):
    """Recover the assignments of a `$GITHUB_ENV` file, the way the runner reads them.

    A value ends at the FIRST line equal to its delimiter, because that is where the runner ends
    it. A reader scanning to the last matching line recovers the original value even from a writer
    whose delimiter the value collides with, and the test would then be asserting its own leniency
    rather than the writer's correctness.
    """
    pairs = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        name, sep, delim = lines[i].partition("<<")
        assert sep, f"line {i} is not a heredoc assignment: {lines[i]!r}"
        i += 1
        value = []
        while i < len(lines) and lines[i] != delim:
            value.append(lines[i])
            i += 1
        assert i < len(lines), f"unterminated heredoc for {name!r}"
        pairs[name] = "\n".join(value)
        i += 1
    return pairs


def test_legacy_bindings_map_to_the_names_tofu_reads():
    assert env_inject.resolve(
        {
            "SHIPMATE_CONFIG_MODE": "legacy",
            "SHIPMATE_LEGACY_TF_VAR_ENV": "dev-eu",
            "SHIPMATE_LEGACY_TF_VAR_REGION": "eu-west-1",
            "SHIPMATE_LEGACY_TF_WORKSPACE": "dev-eu",
            "SHIPMATE_LEGACY_AWS_REGION": "eu-west-1",
        }
    ) == {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1", "TF_WORKSPACE": "dev-eu"}


def test_absent_binding_produces_no_entry():
    assert env_inject.resolve(
        {"SHIPMATE_CONFIG_MODE": "legacy", "SHIPMATE_LEGACY_TF_VAR_ENV": "dev-eu"}
    ) == {"TF_VAR_env": "dev-eu"}


def test_set_but_empty_binding_is_injected_empty():
    assert env_inject.resolve(
        {
            "SHIPMATE_CONFIG_MODE": "legacy",
            "SHIPMATE_LEGACY_TF_VAR_ENV": "dev-eu",
            "SHIPMATE_LEGACY_TF_VAR_REGION": "eu-west-1",
            "SHIPMATE_LEGACY_TF_WORKSPACE": "",
        }
    ) == {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1", "TF_WORKSPACE": ""}


def test_write_env_emits_heredoc_lines(tmp_path):
    path = tmp_path / "github_env"
    env_inject.write_env({"TF_VAR_tags": '{\n"a": "b"\n}', "TF_VAR_env": "dev-eu"}, path)
    assert path.read_text(encoding="utf-8") == (
        "TF_VAR_env<<SHIPMATE_EOF\n"
        "dev-eu\n"
        "SHIPMATE_EOF\n"
        "TF_VAR_tags<<SHIPMATE_EOF\n"
        '{\n"a": "b"\n}\n'
        "SHIPMATE_EOF\n"
    )


def test_unknown_mode_refuses_by_name():
    with pytest.raises(SystemExit) as excinfo:
        env_inject.resolve({"SHIPMATE_CONFIG_MODE": "Legacy"})
    assert str(excinfo.value) == UNKNOWN_REFUSAL


def test_table_mode_refuses_as_unimplemented():
    with pytest.raises(SystemExit) as excinfo:
        env_inject.resolve(
            {"SHIPMATE_CONFIG_MODE": "table", "SHIPMATE_LEGACY_TF_VAR_ENV": "dev-eu"}
        )
    assert str(excinfo.value) == TABLE_REFUSAL


def test_value_containing_the_delimiter_round_trips_whole(tmp_path):
    value = "first\nSHIPMATE_EOF\nlast"
    path = tmp_path / "github_env"
    env_inject.write_env({"TF_VAR_tags": value}, path)
    assert _read_github_env(path.read_text(encoding="utf-8")) == {"TF_VAR_tags": value}
