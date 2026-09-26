"""`env-config.parse_table` resolves `{ var = "NAME" }` references against GitHub variables.

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
layout = "dry"
explicit_envs = [{ var = "PROD_ENV" }, "stage"]

[gate]
approvers_team = { var = "APPROVERS" }

[environments.prod]
region = "eu-west-1"
aws.plan.role = "arn:aws:iam::1:role/plan"
aws.apply.role = { var = "PROD_APPLY_ROLE" }
aws.apply.workloads.net-edge.role = { var = "NET_EDGE_ROLE" }
"""

_VARIABLES = {
    "PROD_ENV": "prod",
    "APPROVERS": "platform",
    "PROD_APPLY_ROLE": "arn:aws:iam::1:role/apply",
    "NET_EDGE_ROLE": "arn:aws:iam::1:role/net-edge",
    "UNUSED": "never-read",
}

_RESOLVED = {
    "layout": "dry",
    "explicit_envs": ["prod", "stage"],
    "gate": {"approvers_team": "platform"},
    "environments": {
        "prod": {
            "region": "eu-west-1",
            "aws": {
                "plan": {"role": "arn:aws:iam::1:role/plan"},
                "apply": {
                    "role": "arn:aws:iam::1:role/apply",
                    "workloads": {"net-edge": {"role": "arn:aws:iam::1:role/net-edge"}},
                },
            },
        }
    },
}

_ROLE_REF = '[environments.prod]\naws.apply.role = { var = "PROD_APPLY_ROLE" }\n'


def test_every_reference_is_replaced_by_its_value():
    """Covers an environment tier, a list item, a workload tier and `gate.approvers_team`.
    Reddens on returning the raw table, on recursing into dicts only (the `explicit_envs`
    item stays a mapping), and on stopping at depth 3 (the workload and apply roles stay
    mappings)."""
    assert ec.parse_table(_TABLE, _VARIABLES) == _RESOLVED


def test_a_mapping_that_merely_contains_var_is_data():
    """An environment and a workload named `var` are ordinary data. Reddens on detecting a
    reference as "a dict containing `var`"."""
    text = (
        '[environments.var]\nregion = "eu-west-1"\n'
        '[environments.prod.aws.apply.workloads.var]\nrole = "r"\n'
    )
    assert ec.parse_table(text, {}) == {
        "environments": {
            "var": {"region": "eu-west-1"},
            "prod": {"aws": {"apply": {"workloads": {"var": {"role": "r"}}}}},
        }
    }


def test_a_one_key_var_mapping_holding_no_string_is_data():
    """The existing validation refuses it later. Reddens on treating any one-key `var`
    mapping as a reference: the lookup of a non-string name refuses here instead."""
    text = "[environments.prod]\nregion = { var = 3 }\n"
    assert ec.parse_table(text, {}) == {"environments": {"prod": {"region": {"var": 3}}}}


def _refusal(text, variables):
    with pytest.raises(SystemExit) as exc:
        ec.parse_table(text, variables)
    return str(exc.value)


def test_an_unset_variable_refuses_naming_the_path_and_name():
    """Reddens on swallowing the `KeyError` and substituting `""`."""
    assert _refusal(_ROLE_REF, {"OTHER": "secret-looking-value"}) == (
        "::error::.github/shipmate.toml environments.prod.aws.apply.role references GitHub "
        "variable PROD_APPLY_ROLE, which is not set. A reference reads repository and "
        "organization variables; the variables of a cell's <env>-plan, <env>-apply or shared "
        "<env> Environment are never read."
    )


def test_an_empty_variable_refuses():
    """Reddens on dropping the empty check."""
    message = _refusal(_ROLE_REF, {"PROD_APPLY_ROLE": ""})
    assert message.startswith("::error::")
    assert "environments.prod.aws.apply.role" in message
    assert "PROD_APPLY_ROLE" in message
    assert "empty" in message


def test_a_lowercase_name_refuses_and_names_the_uppercase_spelling():
    """Reddens on uppercasing the name before the lookup, which resolves it silently."""
    text = '[environments.prod]\naws.apply.role = { var = "prod_apply_role" }\n'
    message = _refusal(text, {"PROD_APPLY_ROLE": "arn:aws:iam::1:role/apply"})
    assert message.startswith("::error::")
    assert "environments.prod.aws.apply.role" in message
    assert '"prod_apply_role"' in message
    assert '"PROD_APPLY_ROLE"' in message
    assert "arn:aws:iam::1:role/apply" not in message


def test_no_reference_never_reads_the_environment(monkeypatch):
    """Reddens on reading `SHIPMATE_GITHUB_VARS` unconditionally: absent, it refuses."""
    monkeypatch.delenv("SHIPMATE_GITHUB_VARS", raising=False)
    text = 'layout = "dry"\nexplicit_envs = ["prod"]\n'
    assert ec.parse_table(text) == {"layout": "dry", "explicit_envs": ["prod"]}


def test_a_reference_without_wiring_refuses_naming_the_wiring(monkeypatch):
    """Reddens on treating an absent `SHIPMATE_GITHUB_VARS` as `{}`, which blames the
    consumer's variable for the engine's missing wiring."""
    monkeypatch.delenv("SHIPMATE_GITHUB_VARS", raising=False)
    with pytest.raises(SystemExit) as exc:
        ec.parse_table(_ROLE_REF)
    message = str(exc.value)
    assert message.startswith("::error::")
    assert "environments.prod.aws.apply.role" in message
    assert "received no GitHub variables" in message
    assert "not set" not in message


def test_a_null_enumeration_refuses_as_unset(monkeypatch):
    """`toJSON(vars)` renders `null` for a repository reaching no variable. Reddens on
    parsing `null` as malformed, which reports an engine bug instead of the unset name."""
    monkeypatch.setenv("SHIPMATE_GITHUB_VARS", "null")
    with pytest.raises(SystemExit) as exc:
        ec.parse_table(_ROLE_REF)
    message = str(exc.value)
    assert "PROD_APPLY_ROLE" in message
    assert "not set" in message


def test_references_lists_every_reference_sorted_by_path():
    """Reddens on dropping list recursion (the `explicit_envs[0]` row disappears)."""
    assert ec.references(tomllib.loads(_TABLE)) == [
        ("environments.prod.aws.apply.role", "PROD_APPLY_ROLE"),
        ("environments.prod.aws.apply.workloads.net-edge.role", "NET_EDGE_ROLE"),
        ("explicit_envs[0]", "PROD_ENV"),
        ("gate.approvers_team", "APPROVERS"),
    ]


_ENUMERATION = '{"PROD_APPLY_ROLE": "arn:aws:iam::1:role/apply"}'
_ROLE_RESOLVED = {
    "environments": {"prod": {"aws": {"apply": {"role": "arn:aws:iam::1:role/apply"}}}}
}


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
