"""`scripts/env-inject`: the single writer of a cell's identity variables.

Reddens on: uppercasing an injected name (`TF_VAR_ENV` for `TF_VAR_env`), defaulting an absent
legacy binding to the empty string, dropping a set-but-empty one, writing `NAME=value` instead of
a heredoc, falling through on an unknown mode, selecting the source on emptiness rather than on
the mode, accepting anything but a JSON object of strings as `SHIPMATE_TF_VARS`, accepting a
`SHIPMATE_LEGACY_*` name the engine does not define, refusing a job that binds none of them,
returning a fixed heredoc delimiter that a value's own text can collide with, and dropping or
narrowing the table-mode warning that names the repository variables the table supersedes.
"""

import json

import pytest
from _loader import load_script

env_inject = load_script("env-inject")

#: Hand-written message templates; the script builds the same text from `_TABLE` and the value.
NOT_JSON = (
    "::error::SHIPMATE_TF_VARS must be the JSON object the cell step passes as "
    "toJSON(matrix.tf_vars), and {} is not JSON. An empty value means the step omitted "
    "the input."
)
NOT_AN_OBJECT_OF_STRINGS = (
    "::error::SHIPMATE_TF_VARS must be a JSON object of strings, got {}. "
    "The empty table is {{}}; null is what toJSON renders for a matrix field nothing "
    "stamped, and is not the same thing."
)

#: All three legacy bindings, set to values no table-mode assertion expects. Every cell job binds
#: them on both paths, so a table-mode cell sees them and must ignore them: two sources for one
#: name would make precedence load-bearing, and a mode inferred from `tf_vars` being empty would
#: inject these instead.
LEGACY_PRESENT = {
    "SHIPMATE_LEGACY_TF_VAR_ENV": "from-vars",
    "SHIPMATE_LEGACY_TF_VAR_REGION": "eu-central-1",
    "SHIPMATE_LEGACY_TF_WORKSPACE": "from-vars",
}
UNKNOWN_REFUSAL = (
    "::error::SHIPMATE_CONFIG_MODE must be 'legacy' or 'table', got 'Legacy'. "
    "The detect job sets it; an unexpected value means the cell and the detect "
    "that produced its row disagree."
)

UNRECOGNISED_REFUSAL = (
    "::error::unrecognised legacy binding(s): SHIPMATE_LEGACY_TF_VAR_REGIONS, "
    "SHIPMATE_LEGACY_TF_VAR_env. Accepted: SHIPMATE_LEGACY_AWS_REGION, "
    "SHIPMATE_LEGACY_AWS_ROLE_ARN, SHIPMATE_LEGACY_AWS_ROLE_ARN_WORKLOAD, "
    "SHIPMATE_LEGACY_TF_VAR_ENV, SHIPMATE_LEGACY_TF_VAR_REGION, SHIPMATE_LEGACY_TF_WORKSPACE. "
    "A misspelled or wrong-case key is injected under no name at all, and a cell carrying no "
    "identity shares one backend key with every other environment."
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


def test_table_mode_injects_the_row_and_never_the_legacy_bindings():
    """One source per mode. The legacy bindings are set here to values the table does not carry,
    because every cell job binds all six on both paths -- a table-mode cell that merged them, or
    preferred them, would inject an environment the detect never resolved.

    Mutation: merge `SHIPMATE_LEGACY_*` under the table's values, or over them.
    """
    assert env_inject.resolve(
        {
            "SHIPMATE_CONFIG_MODE": "table",
            "SHIPMATE_TF_VARS": '{"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}',
            **LEGACY_PRESENT,
        }
    ) == {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}


def test_an_empty_table_injects_nothing_even_with_every_legacy_binding_set():
    """The discriminating fixture: `layout = "folder"` resolves no `tf_vars` at all, so a
    table-mode cell legitimately carries `{}` while the job still binds all six legacy names.
    The mode comes from the row and nothing else.

    Mutation: select the source on `SHIPMATE_TF_VARS` being empty rather than on the mode.
    """
    assert (
        env_inject.resolve(
            {"SHIPMATE_CONFIG_MODE": "table", "SHIPMATE_TF_VARS": "{}", **LEGACY_PRESENT}
        )
        == {}
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
    assert (
        env_inject.resolve(
            {"SHIPMATE_CONFIG_MODE": "table", "SHIPMATE_TF_VARS": json.dumps(tf_vars)}
        )
        == tf_vars
    )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", NOT_JSON.format("''")),
        ("{oops}", NOT_JSON.format("'{oops}'")),
        ("null", NOT_AN_OBJECT_OF_STRINGS.format("'null'")),
        ("[]", NOT_AN_OBJECT_OF_STRINGS.format("'[]'")),
        ("3", NOT_AN_OBJECT_OF_STRINGS.format("'3'")),
        ('{"TF_VAR_env": 3}', NOT_AN_OBJECT_OF_STRINGS.format("'{\"TF_VAR_env\": 3}'")),
    ],
    ids=["absent", "malformed", "null", "list", "number", "non-string value"],
)
def test_a_table_that_is_not_an_object_of_strings_refuses(raw, message):
    """`null` is what `toJSON` renders for a matrix field nothing stamped, and an omitted `with:`
    input arrives as `''` -- neither is the empty table, which is `{}`. Injecting nothing for
    them authenticates the cell and then runs it against another environment's workspace, which
    no later step can tell from a folder layout.

    Mutation: drop the shape check and return whatever parsed, which turns the last four cases
    into an empty injection.
    """
    with pytest.raises(SystemExit) as excinfo:
        env_inject.resolve({"SHIPMATE_CONFIG_MODE": "table", "SHIPMATE_TF_VARS": raw})
    assert str(excinfo.value) == message


def test_value_containing_the_delimiter_round_trips_whole(tmp_path):
    value = "first\nSHIPMATE_EOF\nlast"
    path = tmp_path / "github_env"
    env_inject.write_env({"TF_VAR_tags": value}, path)
    assert _read_github_env(path.read_text(encoding="utf-8")) == {"TF_VAR_tags": value}


def test_unrecognised_legacy_binding_refuses_by_name():
    """A misspelled key injects nothing, and nothing downstream notices: `plan-classify`
    excludes an unset `TF_VAR_*` from the fingerprint, so plan, gate and apply all stay green
    while every environment writes the same backend key. Both spellings here are the realistic
    mistake -- a plural, and the workflow `env:` key GitHub does NOT uppercase.

    Mutation: delete the `unknown` check and watch this test red.
    """
    with pytest.raises(SystemExit) as excinfo:
        env_inject.resolve(
            {
                "SHIPMATE_CONFIG_MODE": "legacy",
                "SHIPMATE_LEGACY_TF_VAR_env": "dev-eu",
                "SHIPMATE_LEGACY_TF_VAR_REGIONS": "eu-west-1",
            }
        )
    assert str(excinfo.value) == UNRECOGNISED_REFUSAL


def test_binding_none_of_them_is_not_a_refusal():
    """Folder-per-environment binds no identity variables at all -- its leaves fix env and
    region by path -- and `docs/getting-started.md` says that flavor needs none. Refusing
    absence would hard-fail every such consumer, so the check above refuses an unrecognised
    name and never a missing one.

    Mutation: make the unknown check also refuse when no accepted name is bound.
    """
    assert env_inject.resolve({"SHIPMATE_CONFIG_MODE": "legacy"}) == {}


#: All six bindings a cell job carries, non-empty, plus the three reserved ones the table-mode
#: warning names alongside the injected three.
ALL_SIX = {
    "SHIPMATE_LEGACY_TF_VAR_ENV": "from-vars",
    "SHIPMATE_LEGACY_TF_VAR_REGION": "eu-central-1",
    "SHIPMATE_LEGACY_TF_WORKSPACE": "from-vars",
    "SHIPMATE_LEGACY_AWS_ROLE_ARN": "arn:aws:iam::1:role/legacy",
    "SHIPMATE_LEGACY_AWS_REGION": "eu-central-1",
    "SHIPMATE_LEGACY_AWS_ROLE_ARN_WORKLOAD": "arn:aws:iam::1:role/legacy-workload",
}

#: Hand-written and whole, in the order the script prints them. A per-name assertion passes a
#: script that warns on the role alone; `TF_VAR_env` and `TF_WORKSPACE` are leftovers as much as
#: `AWS_ROLE_ARN` is. The names are the `vars.` spellings the cell jobs read, so the workload
#: role -- keyed by the cell's own workload -- is named the way the docs name it.
_SUPERSEDED_TAIL = (
    " is superseded by the environment table and is no longer read. Delete it from the "
    "GitHub Environment, or the repository, that sets it."
)
SUPERSEDED_LINES = [
    "::warning::the variable AWS_REGION" + _SUPERSEDED_TAIL,
    "::warning::the variable AWS_ROLE_ARN" + _SUPERSEDED_TAIL,
    "::warning::the variable AWS_ROLE_ARN_<WORKLOAD>" + _SUPERSEDED_TAIL,
    "::warning::the variable TF_VAR_env" + _SUPERSEDED_TAIL,
    "::warning::the variable TF_VAR_region" + _SUPERSEDED_TAIL,
    "::warning::the variable TF_WORKSPACE" + _SUPERSEDED_TAIL,
]


def test_table_mode_names_every_leftover_binding(capsys):
    """Every non-empty legacy binding is reported, not the role alone, and the report is a
    warning: a repository mid-migration still runs.

    Mutations: warn on `AWS_ROLE_ARN` only -- a per-name assertion would pass that, the whole
    list is what catches it; and delete the print, which no other test in this feature reddens
    on because this is the only behaviour here that is neither a refusal nor silence."""
    resolved = env_inject.resolve(
        {"SHIPMATE_CONFIG_MODE": "table", "SHIPMATE_TF_VARS": '{"TF_VAR_env": "dev-eu"}', **ALL_SIX}
    )
    assert resolved == {"TF_VAR_env": "dev-eu"}
    # stdout alone: a combined-stream assertion here has been satisfied by a stub's own stderr.
    assert capsys.readouterr().out.splitlines() == SUPERSEDED_LINES


def test_bindings_present_but_empty_are_not_leftovers(capsys):
    """Every cell job binds all six from `vars.*`, so an unset variable arrives as the empty
    string. Mutation: test presence (`name in environ`) instead of a non-empty value -- a
    repository that never set them then gets six warnings on every table-mode cell forever."""
    env = {name: "" for name in ALL_SIX}
    env.update({"SHIPMATE_CONFIG_MODE": "table", "SHIPMATE_TF_VARS": "{}"})
    assert env_inject.resolve(env) == {}
    assert capsys.readouterr().out == ""


def test_legacy_mode_warns_about_nothing(capsys):
    """The bindings are the live source in legacy mode; nothing supersedes them there.

    Mutation: warn before the mode is read, or on both branches."""
    env = {"SHIPMATE_CONFIG_MODE": "legacy", **ALL_SIX}
    assert env_inject.resolve(env) == {
        "TF_VAR_env": "from-vars",
        "TF_VAR_region": "eu-central-1",
        "TF_WORKSPACE": "from-vars",
    }
    assert capsys.readouterr().out == ""
