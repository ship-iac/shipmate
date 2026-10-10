"""`scripts/env-inject`: the single writer of a cell's identity variables.

Reddens on: writing `NAME=value` instead of a heredoc, accepting anything but a JSON object
of strings as `SHIPMATE_TF_VARS`, refusing the empty table, and returning a fixed heredoc
delimiter that a value's own text can collide with. Not on a name's casing: `resolve` passes
the mapping through verbatim, so the names are `env_config._derived`'s, pinned by
`test_env_config_resolve.py`.
"""

import json

import pytest
from _loader import load_script

env_inject = load_script("env-inject")

#: Hand-written message template; the script builds the same text from `_TABLE`, the value
#: and the rule it broke.
REFUSAL = (
    "::error::SHIPMATE_TF_VARS must be the JSON object of strings the cell step passes as "
    "toJSON(matrix.tf_vars), and {raw} {rule}. An empty value means the step omitted the "
    "input; null is what toJSON renders for a matrix field nothing stamped."
)
NOT_AN_OBJECT = "is not a JSON object of strings"


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


def test_the_row_is_injected_under_the_names_tofu_reads():
    assert env_inject.resolve(
        {"SHIPMATE_TF_VARS": '{"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}'}
    ) == {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}


def test_an_empty_table_injects_nothing():
    """`layout: folder` resolves no `tf_vars` at all, so `{}` is a legitimate row and not a
    row nothing stamped.

    Mutation: refuse an empty mapping, which hard-fails every folder-layout consumer.
    """
    assert env_inject.resolve({"SHIPMATE_TF_VARS": "{}"}) == {}


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


def test_the_table_round_trips_values_only_json_carries():
    """`with:` inputs are strings, so the mapping arrives serialised and the whole-value
    assertion is a round trip. A quote, a newline, a backslash and a non-ASCII character are
    what a value may hold and what a truncating or re-encoding writer loses; the heredoc form
    `write_env` uses exists for exactly the multi-line case.

    Mutations: truncate each value at its first newline; re-serialise each value with
    `json.dumps`.
    """
    tf_vars = {
        "TF_VAR_env": "dev-eu",
        "TF_VAR_tags": '{"owner": "a\\b", "note": "line1\nline2 å"}',
        "TF_WORKSPACE": "",
    }
    assert env_inject.resolve({"SHIPMATE_TF_VARS": json.dumps(tf_vars)}) == tf_vars


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", REFUSAL.format(raw="''", rule="is empty")),
        ("not json", REFUSAL.format(raw="'not json'", rule="is not JSON")),
        ("null", REFUSAL.format(raw="'null'", rule=NOT_AN_OBJECT)),
        ("[]", REFUSAL.format(raw="'[]'", rule=NOT_AN_OBJECT)),
        ("3", REFUSAL.format(raw="'3'", rule=NOT_AN_OBJECT)),
        ('{"a": 1}', REFUSAL.format(raw="'{\"a\": 1}'", rule=NOT_AN_OBJECT)),
    ],
    ids=["absent", "malformed", "null", "list", "number", "non-string value"],
)
def test_a_table_that_is_not_an_object_of_strings_refuses(raw, message):
    """`null` is what `toJSON` renders for a matrix field nothing stamped, and an omitted `with:`
    input arrives as `''` -- neither is the empty table, which is `{}`. Injecting nothing for
    them authenticates the cell and then runs it against another environment's workspace, which
    no later step can tell from a folder layout.

    Mutations: pass `null_is_empty=True` to `_parse_object`, which turns the `null` case into an
    empty injection; drop the empty-value refusal, which turns the absent case into one.
    """
    with pytest.raises(SystemExit) as excinfo:
        env_inject.resolve({"SHIPMATE_TF_VARS": raw})
    assert str(excinfo.value) == message


def test_value_containing_the_delimiter_round_trips_whole(tmp_path):
    value = "first\nSHIPMATE_EOF\nlast"
    path = tmp_path / "github_env"
    env_inject.write_env({"TF_VAR_tags": value}, path)
    assert _read_github_env(path.read_text(encoding="utf-8")) == {"TF_VAR_tags": value}
