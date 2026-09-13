"""`scripts/env-inject`'s export policy for consumer variables and secrets.

Reddens on: accepting a key `$GITHUB_ENV` cannot hold, dropping any one rule from the
reserved set, refusing a table-derived name on a cell whose table does not derive it,
refusing an absent envelope, accepting `null` in an envelope, refusing `null` or `''`
from the enumeration, exporting a name two channels supply, lowercasing an envelope key,
not lowercasing an enumerated `TF_VAR_*` suffix, refusing an enumerated reserved name
instead of skipping it, and quoting the envelope's value in any refusal.
"""

import pytest
from _loader import load_script

env_inject = load_script("env-inject")

SECRETS = "SHIPMATE_SECRETS"
VARS = "SHIPMATE_VARS"
ENUM = "SHIPMATE_GITHUB_VARS"

#: Hand-written whole messages; the script builds the same text from the source name.
ENVELOPE_REFUSAL = (
    "::error::{source} must be a JSON object of strings, and its value {rule}. "
    "No part of the value is shown, because it may be a secret. An unset or "
    "empty envelope exports nothing; null is not the same thing."
)
NOT_JSON = ENVELOPE_REFUSAL.format(source=SECRETS, rule="is not JSON")
NOT_AN_OBJECT_OF_STRINGS = ENVELOPE_REFUSAL.format(
    source=SECRETS, rule="is not a JSON object of strings"
)
RESERVED = (
    "::error::{source} key {name} is reserved: shipmate owns this cell's "
    "identity variables, its credentials, and OpenTofu's execution controls."
)

#: The identity a `dry` cell derives; a `folder` cell derives none.
DRY_TABLE = {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}


def _refusal(raw, source=VARS, table=None):
    with pytest.raises(SystemExit) as excinfo:
        env_inject.envelope_exports(raw, source, DRY_TABLE if table is None else table)
    return str(excinfo.value)


def test_a_key_containing_a_newline_is_refused():
    """`write_env` emits `{name}<<{delim}` unchecked, so a two-line key writes further
    assignments into `$GITHUB_ENV`.

    Mutation: `_NAME.fullmatch(name)` -> `_NAME.match(name)`, which the first line
    satisfies.
    """
    assert _refusal('{"FOO\\nX=Y": "v"}') == (
        f"::error::{VARS} key 'FOO\\nX=Y' is not an environment variable name; "
        "keys must match [A-Za-z_][A-Za-z0-9_]*."
    )


def test_tf_workspace_from_an_envelope_is_refused():
    """`TF_WORKSPACE` is workspace identity and a fingerprint input: setting it
    authenticates a cell and runs it against another environment's state.

    Mutation: remove `"TF_WORKSPACE"` from `_RESERVED_NAMES`.
    """
    assert _refusal('{"TF_WORKSPACE": "prod"}', table={}) == RESERVED.format(
        source=VARS, name="TF_WORKSPACE"
    )


def test_a_folder_cell_accepts_a_name_its_table_does_not_derive():
    """The protected identity set is per-cell: a `folder` layout derives nothing, so
    `TF_VAR_env` is an ordinary consumer variable there.

    Mutation: `or name in table` -> `or name in {"TF_VAR_env", "TF_VAR_region"}`, which
    refuses it on the folder cell.
    """
    assert env_inject.envelope_exports('{"TF_VAR_env": "mine"}', VARS, {}) == {"TF_VAR_env": "mine"}
    assert _refusal('{"TF_VAR_env": "mine"}') == RESERVED.format(source=VARS, name="TF_VAR_env")


def test_aws_names_from_an_envelope_are_refused():
    """`AWS_*` is filled by the OIDC role assumption the engine established.

    Mutation: remove `"AWS_"` from `_RESERVED_PREFIXES`.
    """
    assert _refusal('{"AWS_ROLE_ARN": "arn:aws:iam::1:role/x"}') == RESERVED.format(
        source=VARS, name="AWS_ROLE_ARN"
    )


def test_tf_cli_args_names_from_an_envelope_are_refused():
    """The engine's plan and apply invocation is a contract the consumer does not
    rewrite.

    Mutation: remove `"TF_CLI_ARGS_"` from `_RESERVED_PREFIXES`, which leaves the bare
    `TF_CLI_ARGS` in `_RESERVED_NAMES` and accepts the per-command form.
    """
    assert _refusal('{"TF_CLI_ARGS_plan": "-refresh=false"}') == RESERVED.format(
        source=VARS, name="TF_CLI_ARGS_plan"
    )


@pytest.mark.parametrize("raw", ["", None], ids=["empty", "absent"])
def test_an_absent_envelope_exports_nothing(raw):
    """A consumer with no extras sets neither envelope, which is the normal case and not
    an omitted input. `resolve()`'s empty-value refusal is the pattern not copied here.

    Mutation: make the `if not raw: return {}` case raise instead.
    """
    assert env_inject.envelope_exports(raw, SECRETS, DRY_TABLE) == {}


def test_null_in_an_envelope_is_refused():
    """An envelope is consumer-written, so a `null` there is a mistake with no reading.

    Mutation: pass `null_is_empty=True` on `envelope_exports`'s `_parse_object` call.
    """
    assert _refusal("null", source=SECRETS) == NOT_AN_OBJECT_OF_STRINGS


@pytest.mark.parametrize("raw", ["null", "", None], ids=["null", "empty", "absent"])
def test_the_enumeration_treats_null_and_empty_as_no_variables(raw):
    """`toJSON(vars)` is engine-built, so an empty rendering is a platform fact: a
    repository reaching no variable is a real consumer.

    Mutation: pass `null_is_empty=False` in `parse_enumeration` (the `null` case); delete
    `_parse_object`'s `if not raw: return {}` (the other two, shared with the envelope
    no-op guard above).
    """
    assert env_inject.parse_enumeration(raw, ENUM) == {}


def test_malformed_enumeration_json_refuses_and_may_quote_the_value():
    """Malformed JSON on an engine-built input is an engine bug, and the enumeration
    carries no secret.

    Mutation: return `{}` instead of raising on `ValueError`.
    """
    with pytest.raises(SystemExit) as excinfo:
        env_inject.parse_enumeration("{oops}", ENUM)
    assert str(excinfo.value) == (
        f"::error::{ENUM} must be the object toJSON(vars) renders, and '{{oops}}' is not JSON."
    )


def test_a_name_two_channels_supply_is_refused():
    """Precedence between a plain variable and an envelope entry is undefined, and the
    losing value is what the consumer then debugs.

    Mutation: delete the `raise` in `merge_exports`, so the last channel wins silently.
    """
    with pytest.raises(SystemExit) as excinfo:
        env_inject.merge_exports([(ENUM, {"TF_VAR_size": "1"}), (VARS, {"TF_VAR_size": "2"})])
    assert str(excinfo.value) == (
        f"::error::TF_VAR_size is supplied by both {ENUM} and {VARS}. "
        "Remove it from one of them; shipmate will not choose."
    )


def test_an_enumerated_tf_var_suffix_is_lowercased():
    """GitHub uppercases variable names on storage and OpenTofu matches `TF_VAR_<name>`
    case-sensitively, so the enumerated name reaches no conventional variable unchanged.

    Mutation: drop the `_lowercase_tf_var` call in `filter_enumeration`.
    """
    assert env_inject.filter_enumeration({"TF_VAR_MY_THING": "v", "OTHER": "w"}, DRY_TABLE) == {
        "TF_VAR_my_thing": "v",
        "OTHER": "w",
    }


def test_an_envelope_key_is_exported_verbatim():
    """Case preservation is the whole point of the envelope: it is the only route to
    `variable "myThing"` or `variable "ENDPOINT"`.

    Mutation: apply `_lowercase_tf_var` to envelope keys too, which makes this
    `TF_VAR_mything`.
    """
    assert env_inject.envelope_exports('{"TF_VAR_myThing": "v"}', VARS, DRY_TABLE) == {
        "TF_VAR_myThing": "v"
    }


def test_the_two_channels_collide_after_lowercasing():
    """An enumerated `TF_VAR_ENDPOINT` and an envelope `TF_VAR_endpoint` are one
    OpenTofu variable, so comparing the raw names exports both and one wins silently.

    Mutation: drop the `_lowercase_tf_var` call in `filter_enumeration`, so the two
    names differ and both export (shared with the lowercasing guard above).
    """
    enumerated = env_inject.filter_enumeration({"TF_VAR_ENDPOINT": "a"}, DRY_TABLE)
    envelope = env_inject.envelope_exports('{"TF_VAR_endpoint": "b"}', VARS, DRY_TABLE)
    with pytest.raises(SystemExit) as excinfo:
        env_inject.merge_exports([(ENUM, enumerated), (VARS, envelope)])
    assert str(excinfo.value) == (
        f"::error::TF_VAR_endpoint is supplied by both {ENUM} and {VARS}. "
        "Remove it from one of them; shipmate will not choose."
    )


def test_a_reserved_name_from_the_enumeration_is_skipped_not_refused():
    """`toJSON(vars)` sweeps up the consumer's own engine configuration, which they never
    aimed at a cell; refusing it fails every run on correct configuration.

    Mutation: raise instead of dropping the name in `filter_enumeration`, which makes a
    cell on a repository holding `GITHUB_REF`, `SHIPMATE_APP_ID` or `TF_VAR_ENV` refuse.
    """
    enumerated = {
        "GITHUB_REF": "refs/heads/main",
        "SHIPMATE_APP_ID": "4326562",
        "TF_VAR_ENV": "prod",
        "TF_VAR_SIZE": "small",
    }
    assert env_inject.filter_enumeration(enumerated, DRY_TABLE) == {"TF_VAR_size": "small"}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("{s3cr3t", NOT_JSON),
        ('["s3cr3t"]', NOT_AN_OBJECT_OF_STRINGS),
        ('{"KEY": ["s3cr3t"]}', NOT_AN_OBJECT_OF_STRINGS),
        ('"s3cr3t"', NOT_AN_OBJECT_OF_STRINGS),
    ],
    ids=["malformed", "list", "non-string value", "string"],
)
def test_no_envelope_refusal_carries_a_byte_of_the_value(raw, message):
    """GitHub masks the envelope's whole value, not a fragment of it, so a refusal
    echoing the raw value publishes the secret it was handed.

    Mutation: append `{raw!r}` to either `message(...)` branch in `envelope_exports`.
    """
    refusal = _refusal(raw, source=SECRETS)
    assert refusal == message
    assert "s3cr3t" not in refusal
