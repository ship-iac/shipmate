"""`env-config.parse_table` resolves `{ vars = "NAME" }` references against GitHub variables.

Every expectation is a hand-written constant compared whole: a partial check leaves the rest
of the table free to be rewritten. Each docstring names the mutation its test reddens on.
"""

import base64
import json
import tomllib
import types

import pytest
from _loader import load_script

ec = load_script("env-config")

_TABLE = """\
layout = "tf_vars"

[identities.prod]
aws.plan = { vars = "PROD_PLAN_ROLE" }
aws.apply = { app = "arn:aws:iam::1:role/apply", net-edge = { vars = "NET_EDGE_ROLE" } }

[environments.prod]
region = "eu-west-1"
needs = [{ vars = "FIRST_ENV" }, "stage"]
"""

_VARIABLES = {
    "FIRST_ENV": "dev",
    "PROD_PLAN_ROLE": "arn:aws:iam::1:role/plan",
    "NET_EDGE_ROLE": "arn:aws:iam::1:role/net-edge",
    "UNUSED": "never-read",
}

_RESOLVED = {
    "layout": "tf_vars",
    "identities": {
        "prod": {
            "aws": {
                "plan": "arn:aws:iam::1:role/plan",
                "apply": {
                    "app": "arn:aws:iam::1:role/apply",
                    "net-edge": "arn:aws:iam::1:role/net-edge",
                },
            }
        }
    },
    "environments": {"prod": {"region": "eu-west-1", "needs": ["dev", "stage"]}},
}

_ROLE_REF = '[identities.prod]\naws.apply = { vars = "PROD_APPLY_ROLE" }\n'


def test_every_reference_is_replaced_by_its_value():
    """Covers an identity field, a list item and a workload map value.
    Reddens on returning the raw table, on recursing into dicts only (the `needs` item stays
    a mapping), and on stopping at depth 4 (the workload map value stays a mapping)."""
    assert ec.parse_table(_TABLE, _VARIABLES) == _RESOLVED


def test_a_reference_inside_needs_orders_by_its_value():
    """The resolved list is what `env_order` hands the sorter. Reddens on `_replace` no
    longer recursing into lists: the first predecessor stays a mapping."""
    assert ec.env_order(ec.parse_table(_TABLE, _VARIABLES)) == {"prod": ["dev", "stage"]}


_NEEDS_REF = 'layout = "folder"\n[environments.prod]\nneeds = [{ vars = "FIRST_ENV" }]\n'


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            "Dev",
            "::error::environments.prod.needs entry 'Dev' is not an environment name; entries "
            "are bare logical env names (lowercase letters, digits, '-' and '_'), with no "
            "quotes, spaces or path separators.",
        ),
        (
            "dev-eu-plan",
            "::error::environments.prod.needs entry 'dev-eu-plan' carries the environment "
            "suffix '-plan'; environments.prod.needs is matched against the bare logical env "
            "name — write 'dev-eu' instead.",
        ),
    ],
    ids=["uppercase", "suffix"],
)
def test_a_resolved_needs_value_is_name_checked(value, message):
    """A variable's value meets the rule a written one does, because resolution runs before
    validation. Reddens on validating the unresolved table: the mapping then refuses as a
    non-string element instead."""
    with pytest.raises(SystemExit) as exc:
        ec.validate_structure(ec.parse_table(_NEEDS_REF, {"FIRST_ENV": value}))
    assert str(exc.value) == message


def test_a_mapping_that_merely_contains_vars_is_data():
    """An environment named `vars`, and a two-key map holding `vars`, are ordinary data.
    Reddens on detecting a reference as "a dict containing `vars`"."""
    text = (
        '[environments.vars]\nregion = "eu-west-1"\n'
        '[identities.prod]\naws.apply = { vars = "r", core = "s" }\n'
    )
    assert ec.parse_table(text, {}) == {
        "environments": {"vars": {"region": "eu-west-1"}},
        "identities": {"prod": {"aws": {"apply": {"vars": "r", "core": "s"}}}},
    }


def test_a_one_key_vars_mapping_holding_no_string_is_data():
    """The existing validation refuses it later. Reddens on treating any one-key `vars`
    mapping as a reference: the lookup then matches the integer against the name charset
    and raises `TypeError`."""
    text = "[environments.prod]\nregion = { vars = 3 }\n"
    assert ec.parse_table(text, {}) == {"environments": {"prod": {"region": {"vars": 3}}}}


def _refusal(text, variables):
    with pytest.raises(SystemExit) as exc:
        ec.parse_table(text, variables)
    return str(exc.value)


_UNSET_REFUSAL = (
    "::error::.github/shipmate.toml identities.prod.aws.apply references GitHub "
    "variable PROD_APPLY_ROLE, which is not set. A reference reads repository and "
    "organization variables; the variables of a cell's <env>-plan, <env>-apply or shared "
    "<env> Environment are never read."
)


def test_an_unset_variable_refuses_naming_the_path_and_name():
    """Reddens on swallowing the `KeyError` and substituting `""`."""
    assert _refusal(_ROLE_REF, {"OTHER": "secret-looking-value"}) == _UNSET_REFUSAL


def test_an_empty_variable_refuses():
    """Reddens on dropping the empty check."""
    assert _refusal(_ROLE_REF, {"PROD_APPLY_ROLE": ""}) == (
        "::error::.github/shipmate.toml identities.prod.aws.apply references GitHub "
        "variable PROD_APPLY_ROLE, which is set to an empty value."
    )


def test_a_lowercase_name_refuses_and_names_the_uppercase_spelling():
    """Reddens on uppercasing the name before the lookup, which resolves it silently."""
    text = '[identities.prod]\naws.apply = { vars = "prod_apply_role" }\n'
    assert _refusal(text, {"PROD_APPLY_ROLE": "arn:aws:iam::1:role/apply"}) == (
        "::error::.github/shipmate.toml identities.prod.aws.apply references GitHub "
        'variable "prod_apply_role"; GitHub variable names are uppercase. Write '
        '{ vars = "PROD_APPLY_ROLE" }.'
    )


@pytest.mark.parametrize(
    ("toml_name", "name", "rendered"),
    [
        ("", "", "''"),
        ("A-B", "A-B", "'A-B'"),
        ("a-b", "a-b", "'a-b'"),
        ("1ROLE", "1ROLE", "'1ROLE'"),
        ("A\\nB", "A\nB", "'A\\nB'"),
    ],
)
def test_a_name_outside_the_charset_refuses_naming_the_rule(toml_name, name, rendered):
    """`a-b` is lowercase too, but uppercasing it cannot help. The name renders escaped, so a
    TOML `\\n` in it cannot split the `::error::` line. Reddens on dropping the charset check:
    `""`, `A-B` and `1ROLE` then resolve to the value set for them and `a-b` refuses as
    lowercase, suggesting `A-B`. Reddens on rendering the name raw instead of with `!r`."""
    text = f'[identities.prod]\naws.apply = {{ vars = "{toml_name}" }}\n'
    assert _refusal(text, {name: "v", name.upper(): "v"}) == (
        "::error::.github/shipmate.toml identities.prod.aws.apply references GitHub "
        f"variable {rendered}, which is not a GitHub variable name ([A-Z_][A-Z0-9_]*)."
    )


def test_no_reference_never_reads_the_environment(monkeypatch):
    """Reddens on reading `SHIPMATE_GITHUB_VARS` unconditionally: absent, it refuses."""
    monkeypatch.delenv("SHIPMATE_GITHUB_VARS", raising=False)
    text = 'layout = "tf_vars"\n[environments.prod]\nneeds = ["dev"]\n'
    assert ec.parse_table(text) == {
        "layout": "tf_vars",
        "environments": {"prod": {"needs": ["dev"]}},
    }


_NO_VARIABLES_REFUSAL = (
    "::error::.github/shipmate.toml identities.prod.aws.apply references GitHub "
    "variable PROD_APPLY_ROLE, but this step received no GitHub variables: the engine did not "
    "pass github-vars to this step, or the repository reaches no variables at all."
)


def test_a_reference_without_wiring_refuses_naming_the_wiring(monkeypatch):
    """Reddens on treating an absent `SHIPMATE_GITHUB_VARS` as `{}`, which blames the
    consumer's variable for the engine's missing wiring."""
    monkeypatch.delenv("SHIPMATE_GITHUB_VARS", raising=False)
    assert _refusal(_ROLE_REF, None) == _NO_VARIABLES_REFUSAL


def test_an_empty_variables_input_refuses_naming_the_wiring(monkeypatch):
    """Every action defaults `github-vars` to "", so an unpassed input arrives empty. Reddens
    on refusing only an absent `SHIPMATE_GITHUB_VARS` (`raw is None`), which parses "" as
    `{}` and blames the variable as unset."""
    monkeypatch.setenv("SHIPMATE_GITHUB_VARS", "")
    assert _refusal(_ROLE_REF, None) == _NO_VARIABLES_REFUSAL


def test_a_null_enumeration_refuses_as_unset(monkeypatch):
    """`toJSON(vars)` renders `null` for a repository reaching no variable. Reddens on
    parsing `null` as malformed, which reports an engine bug instead of the unset name."""
    monkeypatch.setenv("SHIPMATE_GITHUB_VARS", "null")
    assert _refusal(_ROLE_REF, None) == _UNSET_REFUSAL


def test_a_root_level_vars_key_is_a_setting_not_a_reference():
    """Reddens on `references` walking from the root table, which reports it under an empty
    path; with `parse_table` also replacing from the root, the whole file resolves to the
    variable's value."""
    assert ec.parse_table('vars = "X"\n', {"X": "v"}) == {"vars": "X"}
    assert ec.references({"vars": "X"}) == []


def test_references_lists_every_reference_sorted_by_path():
    """Reddens on dropping list recursion (the `environments.prod.needs[0]` row
    disappears)."""
    assert ec.references(tomllib.loads(_TABLE)) == [
        ("environments.prod.needs[0]", "FIRST_ENV"),
        ("identities.prod.aws.apply.net-edge", "NET_EDGE_ROLE"),
        ("identities.prod.aws.plan", "PROD_PLAN_ROLE"),
    ]


_ENUMERATION = '{"PROD_APPLY_ROLE": "arn:aws:iam::1:role/apply"}'
_ROLE_RESOLVED = {"identities": {"prod": {"aws": {"apply": "arn:aws:iam::1:role/apply"}}}}


def test_read_table_resolves_references(monkeypatch):
    """Reddens on `read_table` calling `load_toml` instead of `parse_table`."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "an-org/a-repo")
    monkeypatch.setenv("SHIPMATE_GITHUB_VARS", _ENUMERATION)
    git = types.SimpleNamespace(returncode=0, stdout=_ROLE_REF, stderr="")

    def run(args, check=True):
        return "trunk\n" if args[0] == "gh" else git

    assert ec.read_table(run=run) == _ROLE_RESOLVED


def test_read_table_at_default_branch_resolves_references(monkeypatch):
    """Reddens on `read_table_at_default_branch` calling `load_toml` instead of
    `parse_table`."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "an-org/a-repo")
    monkeypatch.setenv("SHIPMATE_GITHUB_VARS", _ENUMERATION)
    blob = {"encoding": "base64", "content": base64.b64encode(_ROLE_REF.encode()).decode()}

    def run(args, check=True):
        return "trunk\n" if "--jq" in args else json.dumps(blob)

    assert ec.read_table_at_default_branch(run=run) == _ROLE_RESOLVED
