"""`env-config` reads the consumer's `tools` versions and the engine's `VERSIONS` floors.

Every expectation is a hand-written constant compared whole. Each docstring names the mutation
its test reddens on.
"""

import pytest
from _loader import load_script

ec = load_script("env-config")

_ENGINE = {
    "terramate": "0.17.1",
    "terramate_min": "0.17.1",
    "tofu": "1.13.1",
    "tofu_min": "1.13.1",
}

_SHAPE = (
    "::error::tools.tofu is {!r}; a version is written X.Y.Z in ASCII digits, such as 1.13.2, "
    "with no v, pre-release or range."
)


def test_absent_tools_reads_both_from_versions():
    """Mutation: return `tools` values without the `VERSIONS` fallback -- a `KeyError`."""
    assert ec.tool_version_errors({"layout": "folder"}, _ENGINE) == []
    assert ec.tool_versions({"layout": "folder"}, _ENGINE) == {
        "terramate": ("0.17.1", "VERSIONS"),
        "tofu": ("1.13.1", "VERSIONS"),
    }


def test_one_tool_set_leaves_the_other_on_versions():
    """Mutation: ignore `tools` in `tool_versions` -- tofu reads `("1.13.1", "VERSIONS")`."""
    table = {"layout": "folder", "tools": {"tofu": "1.13.2"}}
    assert ec.tool_version_errors(table, _ENGINE) == []
    assert ec.tool_versions(table, _ENGINE) == {
        "terramate": ("0.17.1", "VERSIONS"),
        "tofu": ("1.13.2", "tools"),
    }


@pytest.mark.parametrize(
    "value",
    ["1.13", "v1.13.2", "1.13.2-rc1", " 1.13.2", "１.13.2"],
    ids=["two-part", "v-prefix", "pre-release", "leading-space", "full-width-digit"],
)
def test_a_malformed_version_refuses_naming_key_and_value(value):
    """Mutations: `_VERSION` written with `\\d` -- the full-width digit passes; `match` for
    `fullmatch` -- the pre-release crashes in the floor comparison; drop the shape check --
    every case passes, crashes or gets the floor message."""
    table = {"tools": {"tofu": value}}
    assert ec.tool_version_errors(table, _ENGINE) == [_SHAPE.format(value)]


def test_an_unknown_tool_refuses_naming_key_and_value():
    """Mutation: drop the `TOOLS` membership check -- terraform raises a `KeyError` reading
    its floor."""
    assert ec.tool_version_errors({"tools": {"terraform": "1.5.0"}}, _ENGINE) == [
        (
            "::error::tools.terraform is '1.5.0'; terraform is not a tool this engine installs. "
            "tools holds terramate, tofu."
        )
    ]


@pytest.mark.parametrize(
    ("value", "kind"), [("", "str"), (["1.13.2"], "list")], ids=["empty", "list"]
)
def test_a_non_mapping_tools_refuses(value, kind):
    """`tools:` with no value parses to `""` and must not read as absent.

    Mutation: return `[]` for a falsy `tools` -- the empty case passes; drop the `isinstance`
    check -- the list case raises `AttributeError`."""
    assert ec.tool_version_errors({"tools": value}, _ENGINE) == [
        (
            f"::error::tools is {value!r}; it must be a mapping of terramate and tofu to versions, "
            f"got {kind}."
        )
    ]


def test_the_floor_compares_numerically():
    """`1.13.10` is above `1.13.9`, `1.9.0` below it; a string compare gets both wrong.

    Mutations: compare the strings -- `1.13.10` refuses and `1.9.0` passes; skip the floor
    check -- `1.9.0` passes."""
    engine = {**_ENGINE, "tofu_min": "1.13.9"}
    assert ec.tool_version_errors({"tools": {"tofu": "1.13.10"}}, engine) == []
    assert ec.tool_version_errors({"tools": {"tofu": "1.9.0"}}, engine) == [
        "::error::tools.tofu is '1.9.0', below 1.13.9, the oldest tofu this engine supports."
    ]


def test_engine_versions_reads_the_real_file():
    """Mutation: drop the `terramate_min=` line from `VERSIONS` -- `SystemExit`."""
    assert ec.engine_versions() == _ENGINE


def test_engine_versions_accepts_crlf(tmp_path):
    """Mutation: `path.read_bytes().decode().split("\\n")` for the text-mode read -- every
    value keeps a `\\r` and refuses."""
    path = tmp_path / "VERSIONS"
    path.write_bytes(
        b"terramate=0.17.1\r\nterramate_min=0.17.1\r\ntofu=1.13.1\r\ntofu_min=1.13.1\r\n"
    )
    assert ec.engine_versions(path) == _ENGINE


def test_engine_versions_names_a_missing_line(tmp_path):
    """Mutation: default a missing key to its tool's version -- no `SystemExit`."""
    path = tmp_path / "VERSIONS"
    path.write_text("terramate=0.17.1\nterramate_min=0.17.1\ntofu=1.13.1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        ec.engine_versions(path)
    assert str(exc.value) == f"::error::{path} holds no tofu_min= line; restore it."


def test_engine_versions_names_a_malformed_line(tmp_path):
    """Mutation: drop the shape check in `engine_versions` -- `1.13` returns."""
    path = tmp_path / "VERSIONS"
    path.write_text(
        "terramate=0.17.1\nterramate_min=0.17.1\ntofu=1.13.1\ntofu_min=1.13\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit) as exc:
        ec.engine_versions(path)
    assert str(exc.value) == (
        f"::error::{path} line tofu_min=1.13 is malformed; a version is written X.Y.Z."
    )


def test_validate_structure_reports_a_bad_tools_value():
    """`doctor` and `gate-config` validate with `validate_structure` alone.

    Mutation: drop the `tool_version_errors` call in `validate_structure` -- the table
    validates."""
    with pytest.raises(SystemExit) as exc:
        ec.validate_structure({"layout": "folder", "tools": {"tofu": "1.13"}})
    assert str(exc.value) == _SHAPE.format("1.13")


def test_tools_is_an_allowed_top_level_key():
    """Mutation: drop `"tools"` from `_TOP_KEYS` -- the table refuses."""
    table = {"layout": "folder", "tools": {"terramate": "0.17.2"}}
    assert ec.validate_structure(table) == table


def test_a_reference_under_tools_refuses():
    """A version names a download, and the setup reader resolves no references.

    Mutation: drop the `tools` `_refuse_reference` in `_normalise_settings` -- the table
    loads."""
    with pytest.raises(SystemExit) as exc:
        ec.load_config("layout: folder\ntools:\n  tofu: {vars: TOFU}\n")
    assert str(exc.value) == (
        "::error::tools holds a variable reference, and it accepts none. Write the version "
        "into the file."
    )
