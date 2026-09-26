"""`scripts/env-inject`'s export policy for consumer variables and secrets.

Reddens on: accepting a key `$GITHUB_ENV` cannot hold, dropping `TF_WORKSPACE`, the
`AWS_` prefix, the `TF_CLI_ARGS_` prefix or the per-cell table comparison from the
reserved set, refusing a table-derived name on a cell whose table does not derive it,
refusing an absent envelope, accepting `null` in an envelope, refusing `null` or `''`
from the enumeration, exporting a name two channels supply, lowercasing an envelope key,
not lowercasing an enumerated `TF_VAR_*` suffix, refusing an enumerated reserved name
instead of skipping it, skipping an enumerated `SHIPMATE_SECRETS` instead of refusing it,
and quoting the envelope's value in any refusal.

Composition reddens on: writing `$GITHUB_ENV` before a secret value's mask command, masking
a multi-line value whole instead of per line, filtering the identity table, dropping the
enumeration channel, and skipping `SHIPMATE_VARS` as a reserved name instead of lifting it
out of the enumeration and parsing it as an envelope.
"""

import json
import subprocess

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
    "::error::{source} key '{name}' is reserved: shipmate owns this cell's "
    "identity variables, its credentials, and OpenTofu's execution controls."
)

#: The identity a `dry` cell derives; a `folder` cell derives none.
DRY_TABLE = {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}

#: The static half of the reserved set, hand-written. The behavioural guards below cover
#: four of these rules; the rest would drop out of the set with the suite still green.
RESERVED_NAMES = {
    "TF_WORKSPACE",
    "TF_CLI_ARGS",
    "TF_LOG",
    "TF_DATA_DIR",
    "TF_PLUGIN_CACHE_DIR",
    "PATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
}
RESERVED_PREFIXES = ("TF_CLI_ARGS_", "AWS_", "SHIPMATE_", "GITHUB_", "RUNNER_")


def test_the_reserved_set_is_the_whole_hand_written_set():
    """Whole-value, because the set is one fully known value and a per-rule guard for
    each entry would still leave the next addition unpinned. `PATH` and the loader
    variables execute arbitrary code; `SHIPMATE_`, `GITHUB_` and `RUNNER_` are engine and
    runner controls; `TF_LOG`, `TF_DATA_DIR` and `TF_PLUGIN_CACHE_DIR` redirect the tool.

    Mutation: remove `"PATH"` from `_RESERVED_NAMES`, or `"RUNNER_"` from
    `_RESERVED_PREFIXES`.
    """
    assert set(env_inject._RESERVED_NAMES) == RESERVED_NAMES
    assert env_inject._RESERVED_PREFIXES == RESERVED_PREFIXES


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

    Mutation: pass `null_is_empty=False` in `parse_enumeration` (the `null` case); make
    `_parse_object`'s `if not raw` case raise (the other two, shared with the envelope
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


def test_a_table_derived_pair_is_never_filtered():
    """The table is the engine's own identity, so the reserved set -- which exists to keep a
    consumer from writing those very names -- must not be applied to it. A `workspace` cell
    derives `TF_WORKSPACE`, which is reserved.

    Mutation: pass the table through `filter_enumeration` in `compose`, which drops the cell's
    own workspace and runs it against the default one.
    """
    assert env_inject.compose({"SHIPMATE_TF_VARS": '{"TF_WORKSPACE": "dev-eu"}'}) == (
        {"TF_WORKSPACE": "dev-eu"},
        {},
        {"TF_WORKSPACE": "dev-eu"},
    )


def test_shipmate_vars_is_lifted_out_of_the_enumeration():
    """`SHIPMATE_VARS` is itself a GitHub variable, so it arrives inside `toJSON(vars)`. Left
    there it matches the `SHIPMATE_` prefix and is skipped, and its keys never export.

    Mutation: drop the `enumerated.pop(_VARS, None)` lift, passing `None` to
    `envelope_exports` instead.
    """
    enumeration = {"SHIPMATE_VARS": '{"TF_VAR_myThing": "v"}', "TF_VAR_SIZE": "small"}
    assert env_inject.compose({"SHIPMATE_TF_VARS": "{}", ENUM: json.dumps(enumeration)}) == (
        {"TF_VAR_myThing": "v", "TF_VAR_size": "small"},
        {},
        {},
    )


def test_the_secret_envelope_set_as_a_variable_is_refused():
    """`SHIPMATE_SECRETS` is a secret and `SHIPMATE_VARS` a variable, same shape and adjacent
    names, so setting the secret one on the variable surface is the likely mistake. It arrives
    in the enumeration, matches the `SHIPMATE_` prefix and is skipped, so the cell exports
    nothing while the value sits world-readable in the repository UI.

    Mutation: delete the `_SECRETS in parsed` refusal in `filter_enumeration`, and `compose`
    returns `({}, {})` for this environment.
    """
    enumeration = {"SHIPMATE_SECRETS": '{"API_KEY": "leaked-value"}'}
    with pytest.raises(SystemExit) as exc:
        env_inject.compose({"SHIPMATE_TF_VARS": "{}", ENUM: json.dumps(enumeration)})
    assert str(exc.value) == (
        "::error::SHIPMATE_SECRETS is set as a GitHub variable, and it must be a secret. "
        "Nothing in it reaches the cell, and its value is readable by anyone who can "
        "see the repository. Delete the variable, rotate every credential it held, "
        "and set a secret of that name instead."
    )


def _no_run_env_check(table, pairs, environ, run=subprocess.run):
    """`main`'s `run.env` check, stubbed out; `test_env_inject_run_env.py` pins it."""


def test_a_plain_variable_supplies_a_tofu_variable_end_to_end(tmp_path, monkeypatch):
    """The setup promise: a consumer sets the repository variable `TF_VAR_ENDPOINT` and
    `variable "endpoint"` is populated, with no envelope and no per-cell wiring.

    Mutation: drop the `_ENUM` channel from `compose`'s `merge_exports` list, which leaves the
    transport wired and exports nothing through it.
    """
    path = tmp_path / "github_env"
    monkeypatch.setattr(env_inject, "check_run_env", _no_run_env_check)
    monkeypatch.setenv("GITHUB_ENV", str(path))
    monkeypatch.setenv("SHIPMATE_TF_VARS", '{"TF_VAR_env": "dev-eu"}')
    monkeypatch.setenv(ENUM, '{"TF_VAR_ENDPOINT": "https://api.example.com"}')
    monkeypatch.delenv(VARS, raising=False)
    monkeypatch.delenv(SECRETS, raising=False)
    env_inject.main()
    assert path.read_text(encoding="utf-8") == (
        "TF_VAR_endpoint<<SHIPMATE_EOF\n"
        "https://api.example.com\n"
        "SHIPMATE_EOF\n"
        "TF_VAR_env<<SHIPMATE_EOF\n"
        "dev-eu\n"
        "SHIPMATE_EOF\n"
    )


def test_every_secret_is_masked_before_anything_is_written(tmp_path, monkeypatch, capsys):
    """A `$GITHUB_ENV` written first survives a process that dies before the mask commands, and
    the next step then runs with unmasked secret values. The finished file cannot tell the two
    orders apart, so the assertion is made inside `write_env` itself.

    Mutation: in `main`, move the `mask(...)` call below `write_env(...)`.
    """
    calls = []

    def fake_write_env(pairs, path):
        calls.append((capsys.readouterr().out, pairs, path))

    monkeypatch.setattr(env_inject, "write_env", fake_write_env)
    monkeypatch.setattr(env_inject, "check_run_env", _no_run_env_check)
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "github_env"))
    monkeypatch.setenv("SHIPMATE_TF_VARS", '{"TF_VAR_env": "dev-eu"}')
    monkeypatch.setenv(SECRETS, '{"TF_VAR_token": "s3cr3t"}')
    monkeypatch.delenv(ENUM, raising=False)
    monkeypatch.delenv(VARS, raising=False)
    env_inject.main()
    assert calls == [
        (
            "::add-mask::s3cr3t\n",
            {"TF_VAR_env": "dev-eu", "TF_VAR_token": "s3cr3t"},
            str(tmp_path / "github_env"),
        )
    ]


def test_a_multi_line_secret_is_masked_line_by_line(capsys):
    """`::add-mask::` matches an exact string, so a whole-value mask leaves every log line
    carrying one line of a PEM or an embedded JSON blob unmasked. The trailing newline's empty
    line is skipped: GitHub cannot mask the empty string and warns on each attempt.

    Mutation: replace the per-line loop in `mask` with one `print` of the whole value.
    """
    env_inject.mask(["-----BEGIN KEY-----\nMIIBOgIB\n"])
    assert capsys.readouterr().out == "::add-mask::-----BEGIN KEY-----\n::add-mask::MIIBOgIB\n"


def test_a_percent_in_a_secret_is_escaped_for_the_runner(capsys):
    r"""The runner unescapes a workflow command's data, so an unescaped `%` registers a mask
    for a string the value never contains and the value itself stays in the log. `a%25b` is
    the case that matters: unescaped it registers a mask for `a%b`, and `a%25b` is never
    masked. `\r` and `\n` need no escape: `splitlines` removed them.

    Mutation: drop the `.replace('%', '%25')` in `mask`.
    """
    env_inject.mask(["a%25b", "100%"])
    assert capsys.readouterr().out == "::add-mask::a%2525b\n::add-mask::100%25\n"


def test_a_collision_refusal_names_what_the_consumer_can_act_on():
    """The refusal a consumer actually sees comes from `compose`, not from `merge_exports`
    called directly, and it must name the thing they set. `SHIPMATE_GITHUB_VARS` is the
    engine's own input name: it appears in no consumer file and in no GitHub settings page,
    so a consumer reading it has nothing to look up.

    Mutation: pass `_ENUM` instead of `_ENUM_LABEL` at `compose`'s `merge_exports` call.
    """
    with pytest.raises(SystemExit) as excinfo:
        env_inject.compose(
            {
                "SHIPMATE_TF_VARS": "{}",
                # `SHIPMATE_VARS` is a GitHub variable, so it arrives inside the enumeration
                # and `compose` lifts it out; it is never read from the process environment.
                "SHIPMATE_GITHUB_VARS": json.dumps(
                    {
                        "TF_VAR_ENDPOINT": "a",
                        "SHIPMATE_VARS": json.dumps({"TF_VAR_endpoint": "b"}),
                    }
                ),
            }
        )
    assert str(excinfo.value) == (
        "::error::TF_VAR_endpoint is supplied by both your GitHub variables and "
        "SHIPMATE_VARS. Remove it from one of them; shipmate will not choose."
    )
