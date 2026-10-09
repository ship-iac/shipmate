import sys

import pytest
import yaml
from _loader import ACTIONS, WORKFLOWS, load_script, workflow_yaml
from _shipmate import load_config_text, load_workflow_text, yaml_error_text


def _config_refusal(text):
    with pytest.raises(yaml.YAMLError) as exc:
        load_config_text(text)
    return yaml_error_text(exc.value)


def test_the_config_loader_reads_every_scalar_as_a_string():
    """Reddens on basing `ConfigLoader` on `yaml.SafeLoader`: `on` turns into a `True` key,
    `no` into `False`, and the account id, version, flag and null lose their text."""
    text = "on: x\nno: {a: 1}\nid: 012345670123\nv: 1.10\nb: false\nn: null\ne:\n"
    assert load_config_text(text) == {
        "on": "x",
        "no": {"a": "1"},
        "id": "012345670123",
        "v": "1.10",
        "b": "false",
        "n": "null",
        "e": "",
    }


def test_the_config_loader_refuses_a_duplicate_key_at_its_position():
    """Reddens on deleting the duplicate check in `_check_keys`: the second value wins
    silently."""
    assert _config_refusal("a: 1\na: 2\n") == "duplicate key 'a' (line 2, column 1)"


@pytest.mark.parametrize(
    "text, refusal",
    [
        ("y: *a\n", "an alias is not allowed (line 1, column 4)"),
        ("x: &a 1\n", "an anchor is not allowed (line 1, column 4)"),
        ("x: !!str 1\n", "a tag (tag:yaml.org,2002:str) is not allowed (line 1, column 4)"),
        (
            "x: !!python/name:os.system\n",
            "a tag (tag:yaml.org,2002:python/name:os.system) is not allowed (line 1, column 4)",
        ),
    ],
)
def test_the_config_loader_refuses_an_alias_an_anchor_and_a_tag_by_name(text, refusal):
    """Reddens on deleting the alias check (the alias is reported as an anchor), the anchor
    check (the anchored value parses) or the tag check (`BaseLoader` reads a tagged value as
    plain text, `!!python/name:os.system` as an empty string)."""
    assert _config_refusal(text) == refusal


def test_the_config_loader_refuses_a_non_scalar_key():
    """Reddens on deleting the `key.id != "scalar"` check in `_check_keys`: the list key then
    reaches the duplicate check and raises `TypeError`, which a caller catching `YAMLError`
    misses."""
    assert _config_refusal("? [a, b]\n: v\n") == "a key must be a plain value (line 1, column 3)"


def test_the_config_loader_reads_a_merge_key_as_an_ordinary_key():
    """Documents behaviour, no mutation: `BaseLoader` has no merge support, so `<<` is a plain
    key and the duplicate check sees every key a mapping holds."""
    assert load_config_text("<<: {a: 1}\n") == {"<<": {"a": "1"}}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("\ufefflayout: x\n", {"layout": "x"}),
        ("a: 1\r\nb: 2\r\n", {"a": "1", "b": "2"}),
        ("", {}),
    ],
)
def test_the_config_loader_accepts_a_bom_crlf_and_an_empty_document(text, expected):
    """Reddens on returning `yaml.load(...)` without the empty-document `{}`: an empty file
    then reads as `None`."""
    assert load_config_text(text) == expected


def test_the_workflow_loader_types_scalars_by_the_yaml_1_2_core_schema():
    """Reddens on skipping the clear of `yaml_implicit_resolvers`: YAML 1.1 then reads both
    `on` and `yes` as `True`, and the load refuses them as a duplicate key."""
    text = "on:\n  push:\nyes: no\nr: true\nn: 08\no: 0o17\nf: 1.10\nz: ~\n"
    assert load_workflow_text(text) == {
        "on": {"push": None},
        "yes": "no",
        "r": True,
        "n": 8,
        "o": 15,
        "f": 1.1,
        "z": None,
    }


def test_the_workflow_loader_refuses_a_duplicate_key():
    """Reddens on deleting the duplicate check in `_check_keys`: the second value wins."""
    with pytest.raises(yaml.YAMLError) as exc:
        load_workflow_text("a: 1\na: 2\n")
    assert yaml_error_text(exc.value) == "duplicate key 'a' (line 2, column 1)"


def test_the_workflow_loader_refuses_a_non_scalar_key_as_a_yaml_error():
    """Reddens on deleting the `key.id != "scalar"` check in `_check_keys`: the constructed
    list key raises `TypeError` in the duplicate check instead of a `YAMLError`."""
    with pytest.raises(yaml.YAMLError) as exc:
        load_workflow_text("? [a, b]\n: v\n")
    assert yaml_error_text(exc.value) == "a key must be a plain value (line 1, column 3)"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("x: !!int abc\n", "'abc' is not a valid !!int (line 1, column 4)"),
        ("x: !!float abc\n", "'abc' is not a valid !!float (line 1, column 4)"),
        ("x: !!bool maybe\n", "'maybe' is not a valid !!bool (line 1, column 4)"),
        ("x: !!timestamp nope\n", "'nope' is not a valid !!timestamp (line 1, column 4)"),
        ("x:\n  y: !!float ''\n", "'' is not a valid !!float (line 2, column 6)"),
        ("x: !!set [1]\n", "expected a mapping node, but found sequence (line 1, column 4)"),
    ],
)
def test_the_workflow_loader_refuses_a_bad_explicit_tag_value_as_a_yaml_error(text, expected):
    """A caller isolates a file that does not parse by catching `YAMLError` alone.

    Reddens on deleting `WorkflowLoader.construct_object`: the scalar rows raise `ValueError`,
    `KeyError`, `AttributeError` or `IndexError`. Reddens on deleting the mapping-node check in
    `_check_keys`: `!!set [1]` raises `TypeError`."""
    with pytest.raises(yaml.YAMLError) as exc:
        load_workflow_text(text)
    assert yaml_error_text(exc.value) == expected


def test_a_bad_explicit_tag_value_is_echoed_cut_to_80_characters():
    """The refusal reaches doctor's pull request comment whole, so a 10,000-character value
    must not. Mutation: echo `node.value` uncut -- the refusal runs past 10,000 characters."""
    with pytest.raises(yaml.YAMLError) as exc:
        load_workflow_text("x: !!int " + "a" * 10_000 + "\n")
    assert yaml_error_text(exc.value) == (
        f"'{'a' * 80}...' is not a valid !!int (line 1, column 4)"
    )


@pytest.mark.parametrize("load", [load_workflow_text, load_config_text])
def test_a_document_nested_past_the_recursion_limit_is_a_yaml_error(load):
    """Composing recurses once per level, and a caller isolating a file that does not parse
    catches `YAMLError` alone. The mark is wherever the reader stopped, which depends on the
    stack depth, so only the problem is compared.

    Mutation: drop the `except RecursionError` in `_parse` -- `RecursionError` escapes."""
    with pytest.raises(yaml.YAMLError) as exc:
        load("x: " + "[" * 5000 + "]" * 5000 + "\n")
    assert isinstance(exc.value, yaml.MarkedYAMLError)
    assert exc.value.problem == "the document nests too deeply"


def test_the_workflow_loader_resolves_aliases_and_ignores_unknown_tags():
    """Reddens on dropping the `add_multi_constructor` line: `!foo` then has no constructor
    and raises."""
    assert load_workflow_text("s: &s {x: 1}\nt: *s\n") == {"s": {"x": 1}, "t": {"x": 1}}
    assert load_workflow_text("on: !foo bar\n") == {"on": "bar"}


def _safe_load_with_on(text):
    doc = yaml.safe_load(text)
    return {("on" if k is True else k): v for k, v in doc.items()}


@pytest.mark.parametrize(
    "path",
    sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml")),
    ids=lambda p: p.relative_to(ACTIONS.parent).as_posix(),
)
def test_the_workflow_loader_matches_safe_load_on_every_engine_file(path):
    """Reddens on narrowing the bool resolver regex to `^(?:true)$`: every `false` in the
    engine files then reads as a string."""
    text = path.read_text(encoding="utf-8")
    assert load_workflow_text(text) == _safe_load_with_on(text)


def test_the_guard_harness_parses_workflows_with_the_workflow_loader():
    """The guards read engine files the way doctor reads a consumer's, so `on` stays a string
    key. Reddens on parsing `_loader._parse_yaml` with `yaml.safe_load`: `on` becomes `True`."""
    assert set(workflow_yaml("plan.yml")) == {"name", "on", "permissions", "cache-mode", "jobs"}


def test_shipmate_imports_without_pyyaml_and_the_loader_raises(monkeypatch):
    """Reddens on adding `import yaml` at the top of `_shipmate.py`: every script that loads
    `_shipmate` then fails on a runner without PyYAML, including those that never parse."""
    monkeypatch.setitem(sys.modules, "yaml", None)
    mod = load_script("_shipmate.py")
    with pytest.raises(ModuleNotFoundError):
        mod.load_config_text("a: 1")


def test_a_yaml_error_reads_as_one_line():
    """Reddens on returning `str(exc)`: PyYAML's message spans lines, quoting the context and
    the source with carets."""
    with pytest.raises(yaml.YAMLError) as exc:
        load_config_text("a: [1, 2\n")
    assert yaml_error_text(exc.value) == (
        "expected ',' or ']', but got '<stream end>' (line 2, column 1)"
    )
    assert yaml_error_text(yaml.YAMLError("no\nmark")) == "no mark"
