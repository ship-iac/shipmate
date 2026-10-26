"""`env-config` refuses a malformed environment table before any cell runs.

Validation is what stops a typo from silently disabling injection or from handing a plan
tier write credentials. Every condition here refuses -- `raise SystemExit("::error::…")` --
rather than warning: a warning hands control back to the branch content this feature exists
to keep out. The one deliberate asymmetry -- a table entry nobody uses -- warns instead, and
is not this module's subject.

Messages are compared whole against hand-written literals, never by substring and never
against a constant imported from the script: an operator reading a refusal in a run log has
no other source, so the text is part of the contract.
"""

import pytest
from _loader import load_script

env_config = load_script("env-config")


def _refusal(table, matrix_envs=()):
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate(table, matrix_envs)
    return str(excinfo.value)


# --- 1: layout ------------------------------------------------------------------------


def test_an_unknown_layout_refuses():
    """Mutation: drop the layout membership check -- a typo disables injection silently."""
    assert _refusal({"layout": "drys"}) == (
        "::error::layout is 'drys'; it must be one of tf_vars, workspace, folder."
    )


def test_a_non_string_layout_refuses():
    """Mutation: as above; a mapping is not one of the three names either."""
    assert _refusal({"layout": {"dry": True}}) == (
        "::error::layout is {'dry': True}; it must be one of tf_vars, workspace, folder."
    )


NO_LAYOUT = (
    "::error::.github/shipmate-config.yml declares no layout, so no cell can resolve its "
    "environment identity. Declare layout: tf_vars, layout: workspace or layout: folder on the "
    "default branch, which is where this table is read from."
)


def test_a_table_with_no_layout_refuses():
    """Without a layout no cell has an identity to run with, so the file is refused rather
    than read as a repository that injects nothing.

    Mutation: restore `if "layout" not in table: return table`.
    """
    assert _refusal({"environments": {"dev-eu": {"region": "eu-west-1"}}}) == NO_LAYOUT


def test_a_file_holding_only_an_ordering_refuses():
    """`needs` is a legal entry key, so a file carrying only an ordering parses cleanly and
    reaches validation. The layout refusal is the only thing that can stop it.

    Mutation: as above.
    """
    table = {"environments": {"prod-us": {"needs": ["dev-eu"]}}}
    assert _refusal(table) == NO_LAYOUT


# --- 2: tf_vars needs an entry with a region for every matrix environment -----------------


def test_tf_vars_refuses_a_matrix_environment_with_no_entry():
    """Mutation: drop the coverage check -- the layout cannot derive its variables."""
    table = {"layout": "tf_vars", "environments": {"dev-eu": {"region": "eu-west-1"}}}
    assert _refusal(table, matrix_envs=("dev-eu", "prod-us")) == (
        "::error::layout: tf_vars derives TF_VAR_env and TF_VAR_region from the "
        "environment table, and prod-us has no entry in it."
    )


def test_tf_vars_refuses_an_entry_with_no_region():
    """Mutation: check only that the entry exists, not that it carries a region."""
    table = {"layout": "tf_vars", "environments": {"dev-eu": {}}}
    assert _refusal(table, matrix_envs=("dev-eu",)) == (
        "::error::layout: tf_vars derives TF_VAR_env and TF_VAR_region from the "
        "environment table, and dev-eu has an entry with no region."
    )


@pytest.mark.parametrize("layout", ["tf_vars", "folder"])
@pytest.mark.parametrize("written", ["", " #eu-west-1", ' ""'], ids=["bare", "comment", "quoted"])
def test_an_empty_region_refuses_in_every_layout(layout, written):
    """`region:` and `region: #eu-west-1` load as the empty string. An empty TF_VAR_region
    drops out of the plan fingerprint, and the credentials step needs a region, so an empty one
    is no region in any layout.

    Mutation: delete the empty-region check in `_check_environment` -- the `tf_vars` cases
    refuse as an entry with no region instead, and the `folder` cases validate.
    """
    text = f"layout: {layout}\nenvironments:\n  dev-eu:\n    region:{written}\n"
    assert _refusal(env_config.load_config(text), matrix_envs=("dev-eu",)) == (
        "::error::environment dev-eu: region is empty. Give it a value, or remove the key."
    )


def test_a_non_tf_vars_layout_needs_no_entry():
    """Mutation: run the coverage check for every layout."""
    table = {"layout": "workspace", "environments": {}}
    assert env_config.validate(table, ("dev-eu",)) == table


# --- 5: an unimplemented environment key ----------------------------------------------


def test_an_unimplemented_provider_key_refuses():
    """Mutation: skip an unrecognised environment key instead of refusing it."""
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"azure": {"plan": {"client_id": "x"}}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: azure is not a key this engine implements. "
        "An environment holds region, vars, identity, workloads, split, needs, apply_mode, review."
    )


def test_a_misspelled_environment_key_refuses():
    """The same refusal covers a typo, so the message names every legal key.

    Mutation: as above.
    """
    table = {"layout": "folder", "environments": {"dev-eu": {"regoin": "eu-west-1"}}}
    assert _refusal(table) == (
        "::error::environment dev-eu: regoin is not a key this engine implements. "
        "An environment holds region, vars, identity, workloads, split, needs, apply_mode, review."
    )


def test_the_environment_keys_and_flags_are_exactly_these():
    """Mutation: put `tf_vars` back in `_ENV_KEYS`, or `gated` in `_FLAGS` -- red."""
    assert env_config._ENV_KEYS == (
        "region",
        "vars",
        "identity",
        "workloads",
        "split",
        "needs",
        "apply_mode",
        "review",
    )
    assert env_config._FLAGS == ("split", "review")


_RETIRED_KEY_MESSAGE = (
    "::error::environment dev-eu: {} is not a key this engine implements. "
    "An environment holds region, vars, identity, workloads, split, needs, apply_mode, review."
)


@pytest.mark.parametrize(
    ("key", "value"),
    [("shared", "true"), ("gated", "false"), ("explicit", "true"), ("tf_vars", "{TF_VAR_x: y}")],
)
def test_a_retired_key_refuses_as_unknown(key, value):
    """The retired spellings are unknown keys, with no alias, and the refusal lists the keys
    that replaced them.

    Mutation: re-add `shared` to `_ENV_KEYS` -- the `shared` case validates.
    """
    text = f"layout: folder\nenvironments:\n  dev-eu:\n    {key}: {value}\n"
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.load_config(text))
    assert str(excinfo.value) == _RETIRED_KEY_MESSAGE.format(key)


# --- 7: the flags -----------------------------------------------------------------------

_FLAGS = ("split", "review")


@pytest.mark.parametrize("key", _FLAGS)
@pytest.mark.parametrize("value", [True, False])
def test_a_boolean_flag_validates(key, value):
    """Mutation: drop either from the implemented environment keys."""
    table = {"layout": "folder", "environments": {"dev-eu": {key: value}}}
    assert env_config.validate_structure(table) is table


_NOT_BOOLEAN = [
    ("split", "True", "'True'"),
    ("split", "yes", "'yes'"),
    ("split", "1", "'1'"),
    ("review", "FALSE", "'FALSE'"),
    ("review", "no", "'no'"),
]


@pytest.mark.parametrize(("key", "written", "found"), _NOT_BOOLEAN, ids=range(len(_NOT_BOOLEAN)))
def test_a_flag_that_is_not_true_or_false_refuses(key, written, found):
    """Only `true` and `false`, exactly as written, are flags. `True` or `yes` reads as set to
    a person and would resolve as unset.

    Mutations: normalise the value case-insensitively in `_normalise_bool` -- the `True` and
    `FALSE` cases validate, and `test_three_independent_errors_refuse_as_three_lines` loses its
    `review` line; drop the `isinstance` check from `_check_bool` -- every case validates; or
    drop one key from `_FLAGS` -- that key's cases validate.
    """
    text = f"layout: folder\nenvironments:\n  dev-eu:\n    {key}: {written}\n"
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.load_config(text))
    assert str(excinfo.value) == (
        f"::error::environments.dev-eu.{key} must be true or false, got {found}."
    )


@pytest.mark.parametrize("key", _FLAGS)
@pytest.mark.parametrize(
    "written",
    ["{vars: SECRETISH}", "[{vars: SECRETISH}]", "{a: {vars: SECRETISH}}"],
    ids=["direct", "in-a-list", "in-a-mapping"],
)
def test_a_referenced_flag_refuses_before_the_variable_is_read(key, written):
    """The flag refusal quotes what it found, and a refusal reaches run logs and doctor's
    pull request comment, so a reference anywhere in a flag refuses before it resolves: the
    variable's value is never quoted.

    Mutations: delete the reference refusal in `_normalise_bool` -- every row's flag refusal
    quotes `sentinel-value`; test only the flag itself with `_is_reference` -- the nested rows
    do.
    """
    text = f"layout: folder\nenvironments:\n  prod:\n    {key}: {written}\n"
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.parse_table(text, {"SECRETISH": "sentinel-value"}))
    assert str(excinfo.value) == (
        f"::error::environments.prod.{key} holds a variable reference; a flag accepts no "
        "variable reference. Write true or false."
    )


@pytest.mark.parametrize("value", ["targeted", "bare"])
def test_an_apply_mode_validates(value):
    """Mutation: drop `apply_mode` from `_ENV_KEYS`, or `bare` from `_APPLY_MODES` -- red."""
    text = f"layout: folder\nenvironments:\n  dev-eu:\n    apply_mode: {value}\n"
    table = env_config.load_config(text)
    assert env_config.validate_structure(table) is table
    assert table["environments"]["dev-eu"]["apply_mode"] == value


_NOT_AN_APPLY_MODE = [
    ("Auto", "'Auto'"),
    ("true", "'true'"),
    ("Targeted", "'Targeted'"),
    ('""', "''"),
    ("[targeted]", "['targeted']"),
]


@pytest.mark.parametrize(
    ("written", "found"), _NOT_AN_APPLY_MODE, ids=range(len(_NOT_AN_APPLY_MODE))
)
def test_an_apply_mode_that_is_not_a_mode_refuses(written, found):
    """Any other value reads as set to a person and resolves as `bare`, which puts the
    environment on a bare `shipmate apply`.

    Mutation: drop the `_check_apply_mode` call from `_check_environment` -- every case
    validates.
    """
    text = f"layout: folder\nenvironments:\n  dev-eu:\n    apply_mode: {written}\n"
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.load_config(text))
    assert str(excinfo.value) == (
        f"::error::environments.dev-eu.apply_mode must be targeted, bare or auto, got {found}."
    )


def test_an_auto_apply_mode_validates_with_review_false():
    """Mutation: drop `auto` from `_APPLY_MODES` -- the value refusal fires."""
    text = "layout: folder\nenvironments:\n  dev-eu:\n    apply_mode: auto\n    review: false\n"
    table = env_config.load_config(text)
    assert env_config.validate_structure(table) is table
    assert table["environments"]["dev-eu"] == {"apply_mode": "auto", "review": False}


@pytest.mark.parametrize("review", ["", "    review: true\n"], ids=["absent", "true"])
def test_an_auto_apply_mode_without_review_false_refuses(review):
    """An `auto` apply starts on a push, so it has no approving review to wait for.

    Mutation: drop the `review` check from `_check_apply_mode` -- both rows validate.
    """
    text = f"layout: folder\nenvironments:\n  dev-eu:\n    apply_mode: auto\n{review}"
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.load_config(text))
    assert str(excinfo.value) == (
        "::error::environments.dev-eu.apply_mode is auto, which requires review: false on the "
        "same entry. An auto apply starts on a push and does not wait for an approving review."
    )


_MODES_TABLE = {
    "environments": {
        "dev-eu": {"apply_mode": "auto", "review": False},
        "dev-us": {"apply_mode": "bare"},
        "prod": {"apply_mode": "targeted"},
        "qa": {},
        "stage": {"apply_mode": "auto", "review": False},
    }
}


def test_targeted_envs_leaves_an_auto_environment_on_a_bare_apply():
    """Mutation: `targeted_envs` keeps every mode but `bare` -- the auto entries join prod."""
    assert env_config.targeted_envs(_MODES_TABLE) == ["prod"]


def test_auto_envs_names_exactly_the_auto_entries():
    """Mutation: `auto_envs` returns the `targeted` entries -- `["prod"]`."""
    assert env_config.auto_envs(_MODES_TABLE) == ["dev-eu", "stage"]


@pytest.mark.parametrize(
    "written",
    ["{vars: SECRETISH}", "[{vars: SECRETISH}]"],
    ids=["direct", "in-a-list"],
)
def test_a_referenced_apply_mode_refuses_before_the_variable_is_read(written):
    """A variable holding `targeted` would hold an environment back from a setting outside
    the file, and the value refusal would quote the variable.

    Mutation: drop the `apply_mode` reference check from `_normalise_flags` -- the direct row
    resolves to `targeted` and validates.
    """
    text = f"layout: folder\nenvironments:\n  prod:\n    apply_mode: {written}\n"
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.parse_table(text, {"SECRETISH": "targeted"}))
    assert str(excinfo.value) == (
        "::error::environments.prod.apply_mode holds a variable reference, and it accepts "
        "none. Write targeted, bare or auto."
    )


def test_a_referenced_layout_refuses_without_quoting_the_variable():
    """`validate_structure` quotes a literal bad layout; a referenced one is named by its
    variable instead.

    Mutation: delete the referenced-layout check in `parse_table` -- the refusal quotes
    `'sentinel-value'`.
    """
    text = "layout: {vars: LAYOUT}\n"
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.parse_table(text, {"LAYOUT": "sentinel-value"}))
    assert str(excinfo.value) == (
        "::error::.github/shipmate-config.yml layout references GitHub variable LAYOUT, whose "
        "value is not one of tf_vars, workspace, folder."
    )


def test_a_layout_holding_a_nested_reference_refuses_by_type():
    """A reference inside a list is no layout, and the literal-layout refusal would quote it
    resolved.

    Mutation: delete the layout type check in `load_config` -- the refusal quotes
    `['sentinel-value']`.
    """
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(
            env_config.parse_table("layout: [{vars: LAYOUT}]\n", {"LAYOUT": "sentinel-value"})
        )
    assert str(excinfo.value) == "::error::layout must be a string, got list."


def test_a_referenced_layout_resolves():
    """Mutation: refuse every referenced layout in `parse_table` -- this refuses."""
    assert env_config.parse_table("layout: {vars: LAYOUT}\n", {"LAYOUT": "folder"}) == {
        "layout": "folder"
    }


# --- 8: vars names and values ----------------------------------------------------------


def test_vars_naming_anything_outside_the_allowlist_refuses():
    """Mutation: drop the name allowlist -- the table becomes a general env injector."""
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"vars": {"AWS_REGION": "eu-west-1"}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: vars may name only TF_VAR_* and TF_WORKSPACE, "
        "and AWS_REGION is neither."
    )


def test_vars_holding_a_non_string_refuses():
    """Caught here rather than per cell: env-inject would refuse once per cell instead.

    Mutation: check the names and not the values.
    """
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"vars": {"TF_VAR_count": 3}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: vars.TF_VAR_count must be a string, got int. "
        "A non-string value would otherwise refuse once per cell at injection time."
    )


def test_vars_may_hold_an_empty_string():
    """An explicit empty value is excluded from the plan fingerprint on both paths, so it
    is legal.

    Mutation: refuse a falsy value.
    """
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"vars": {"TF_VAR_region": "", "TF_WORKSPACE": "w"}}},
    }
    assert env_config.validate(table, ()) == table


# --- 9: malformed shape ----------------------------------------------------------------

_MALFORMED = [
    (
        {"layout": "folder", "environments": "dev-eu"},
        "::error::environments must be a mapping, got str.",
    ),
    (
        {"layout": "folder", "environments": {"dev-eu": "eu-west-1"}},
        "::error::environment dev-eu must be a mapping, got str.",
    ),
    (
        {"layout": "folder", "environments": {"dev-eu": {"region": {"name": "eu"}}}},
        "::error::environment dev-eu: region must be a string, got dict.",
    ),
    (
        {"layout": "folder", "environments": {"dev-eu": {"vars": "TF_VAR_x"}}},
        "::error::environment dev-eu: vars must be a mapping, got str.",
    ),
]


@pytest.mark.parametrize(("table", "message"), _MALFORMED, ids=range(len(_MALFORMED)))
def test_a_malformed_shape_refuses(table, message):
    """A string where an object is required, and the reverse.

    Mutation: accept the value and carry on (skip the entry rather than raise) -- a crash
    is not the property, refusal is.
    """
    assert _refusal(table) == message


# --- the three non-refusals ------------------------------------------------------------


def test_a_folder_layout_with_no_environments_passes():
    """Migrated and quiet, for a layout that injects nothing.

    Mutation: require an `environments` key.
    """
    table = {"layout": "folder"}
    assert env_config.validate(table, ("dev-eu",)) == table


def test_an_empty_table_refuses():
    """An empty file parses to an empty mapping, so this is the only site that can refuse it.

    Mutation: restore `if "layout" not in table: return table`.
    """
    assert _refusal({}) == NO_LAYOUT


def test_a_whole_table_is_returned_unchanged():
    """Validation returns the table it was given, every top-level setting included.

    Mutation: return only the `environments` mapping.
    """
    table = {
        "layout": "tf_vars",
        "identities": {
            "dev": {
                "aws": {
                    "account": "981700000000",
                    "plan": "shipmate-plan",
                    "apply": {"app": "shipmate-apply", "net-edge": "net-edge"},
                }
            }
        },
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "needs": ["prod-us"],
                "vars": {"TF_VAR_team": "core"},
                "identity": "dev",
                "workloads": ["net-edge", "app"],
            }
        },
    }
    assert env_config.validate(table, ("dev-eu",)) == {
        "layout": "tf_vars",
        "identities": {
            "dev": {
                "aws": {
                    "account": "981700000000",
                    "plan": "shipmate-plan",
                    "apply": {"app": "shipmate-apply", "net-edge": "net-edge"},
                }
            }
        },
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "needs": ["prod-us"],
                "vars": {"TF_VAR_team": "core"},
                "identity": "dev",
                "workloads": ["net-edge", "app"],
            }
        },
    }


def test_an_empty_layout_refuses():
    """Every scalar is a string, so `layout:` with no value is the empty string: a declared
    layout holding a value no layout may hold.

    Mutation: `if not table.get("layout"):` for the no-layout refusal, which reads an empty
    layout as undeclared and answers a repository that did declare one with the message
    telling it to declare one.
    """
    assert _refusal({"layout": ""}) == (
        "::error::layout is ''; it must be one of tf_vars, workspace, folder."
    )


# --- 10: the top level ------------------------------------------------------------------


def test_a_misspelled_top_level_key_refuses():
    """A misspelled `environments` yields zero environments, and under folder and workspace
    that resolves an empty role and skips the credentials step -- a cell applying real
    infrastructure with no cloud identity, reached by a typo.

    Mutation: drop the strict top-level key loop -- the table validates and injects nothing.
    """
    assert _refusal({"layout": "folder", "enviroments": {}}) == (
        "::error::enviroments is not a setting this engine implements. "
        ".github/shipmate-config.yml holds layout, identities, environments, max_parallel, "
        "command_hints."
    )


def test_every_allowed_top_level_key_is_accepted():
    """The other half of the strict-key rule: the five names are the whole allowed set, so a
    table using all five must validate. Compared against a hand-written table, never against
    the module's own constant.

    Mutation: remove a name from the allowed set -- one of these five then refuses.
    """
    table = {
        "layout": "folder",
        "identities": {},
        "environments": {},
        "max_parallel": "5",
        "command_hints": True,
    }
    assert env_config.validate(table, ()) == table


# --- max_parallel -----------------------------------------------------------------------------


def test_max_parallel_defaults_to_10():
    """Mutation: `DEFAULT_MAX_PARALLEL = 11` -- it reads 11."""
    assert env_config.max_parallel({}) == 10


@pytest.mark.parametrize(("written", "value"), [("1", 1), ("5", 5), ("256", 256)])
def test_max_parallel_reads_the_table(written, value):
    """Mutations: return `DEFAULT_MAX_PARALLEL` unconditionally -- 5 reads 10; refuse above 255
    (`<= 255`) -- 256 refuses in `validate_structure`."""
    table = {"layout": "folder", "max_parallel": written}
    env_config.validate_structure(table)
    assert env_config.max_parallel(table) == value


@pytest.mark.parametrize("value", ["0", "x", {"a": "1"}], ids=["zero", "text", "mapping"])
def test_max_parallel_refuses_an_unvalidated_bad_value(value):
    """A reader that skips `validate_structure` must still never get 0, which GitHub runs
    unbounded, or a crash.

    Mutation: drop the `_check_max_parallel` call in `max_parallel` -- "zero" returns 0 and the
    others raise `ValueError`/`TypeError` instead of the refusal."""
    with pytest.raises(SystemExit) as excinfo:
        env_config.max_parallel({"max_parallel": value})
    assert str(excinfo.value) == (
        f"::error::max_parallel is {value!r}; it must be a whole number from 1 to 256 (the most "
        "cell jobs a plan or drift matrix runs at once)."
    )


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "257", "20.5", " 20", "20 ", "+5", "0x10", "1e2", "05", "٣", "", {"a": "1"}, ["5"]],
    ids=[
        "zero",
        "negative",
        "over-256",
        "decimal",
        "leading-space",
        "trailing-space",
        "plus",
        "hex",
        "exponent",
        "leading-zero",
        "arabic-indic-digit",
        "empty",
        "mapping",
        "list",
    ],
)
def test_a_malformed_max_parallel_refuses(value):
    """GitHub runs a matrix with `max-parallel: 0` unbounded, and fails the run on an empty or
    non-numeric one, so only a whole number from 1 to 256 passes.

    Mutations: lower bound `[0-9]` instead of `[1-9]` -- "0" and "05" pass; upper bound 257 --
    "257" passes; `int(value)` with no digits-only check -- " 20", "+5" and the Arabic-Indic
    digit pass; drop `_check_max_parallel` from `validate_structure` -- every row passes.
    """
    assert _refusal({"layout": "folder", "max_parallel": value}) == (
        f"::error::max_parallel is {value!r}; it must be a whole number from 1 to 256 (the most "
        "cell jobs a plan or drift matrix runs at once)."
    )


@pytest.mark.parametrize(
    "written",
    ["{vars: CAP}", "[{vars: CAP}]", "{a: {vars: CAP}}"],
    ids=["direct", "in-a-list", "in-a-mapping"],
)
def test_a_referenced_max_parallel_refuses_before_the_variable_is_read(written):
    """`max_parallel` is written into the file, and the malformed-value refusal quotes what it
    found, so a reference anywhere in it refuses before it resolves.

    Mutations: delete the `max_parallel` reference refusal in `_normalise_settings` -- "direct"
    validates as 7 and the nested rows quote the resolved value; test only the value itself with
    `_is_reference` there -- the nested rows quote the resolved value.
    """
    text = f"layout: folder\nmax_parallel: {written}\n"
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.parse_table(text, {"CAP": "7"}))
    assert str(excinfo.value) == (
        "::error::max_parallel holds a variable reference, and it accepts none. Write the "
        "number into the file."
    )


# --- command_hints ----------------------------------------------------------------------------


@pytest.mark.parametrize(("written", "value"), [("true", True), ("false", False)])
def test_command_hints_reads_the_table(written, value):
    """Mutation: drop `command_hints` from `_TOP_KEYS` -- both rows refuse as an unknown key;
    drop the bool conversion in `_normalise_bool` -- both rows refuse quoting the string; return
    `False` unconditionally -- "true" reads False."""
    table = env_config.validate_structure(
        env_config.parse_table(f"layout: folder\ncommand_hints: {written}\n")
    )
    assert env_config.command_hints(table) is value


def test_command_hints_defaults_to_false():
    """Mutation: default to `True` -- it reads True."""
    assert env_config.command_hints({}) is False


@pytest.mark.parametrize(
    ("written", "shown"), [("yes", "'yes'"), ("1", "'1'"), ("[true]", "['true']")]
)
def test_a_non_bool_command_hints_refuses(written, shown):
    """Only the two YAML spellings turn into a bool; anything else reads as set to a person and
    would resolve as unset.

    Mutation: accept a string in `_check_bool` (`isinstance(..., (bool, str))`) -- the two
    string rows pass; drop the `command_hints` `_check_bool` call from `validate_structure`
    -- every row passes."""
    value = env_config.load_config(f"layout: folder\ncommand_hints: {written}\n")["command_hints"]
    assert _refusal({"layout": "folder", "command_hints": value}) == (
        f"::error::command_hints must be true or false, got {shown}."
    )


def test_command_hints_refuses_an_unvalidated_bad_value():
    """A reader that skips `validate_structure` must not read the string "false" as on.

    Mutation: drop the `_check_bool` call in `command_hints` -- it returns "false",
    which is truthy."""
    with pytest.raises(SystemExit) as excinfo:
        env_config.command_hints({"command_hints": "false"})
    assert str(excinfo.value) == "::error::command_hints must be true or false, got 'false'."


@pytest.mark.parametrize(
    "written",
    ["{vars: HINTS}", "[{vars: HINTS}]", "{a: {vars: HINTS}}"],
    ids=["direct", "in-a-list", "in-a-mapping"],
)
def test_a_referenced_command_hints_refuses_before_the_variable_is_read(written):
    """`command_hints` is read from the default branch's file alone, so a reference anywhere in
    it refuses before it resolves.

    Mutation: delete the reference refusal in `_normalise_bool` -- "direct" resolves to the
    string "true" and refuses quoting it, the nested rows quote the resolved value, and every
    row of `test_a_referenced_flag_refuses_before_the_variable_is_read` reddens too.
    """
    text = f"layout: folder\ncommand_hints: {written}\n"
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.parse_table(text, {"HINTS": "true"}))
    assert str(excinfo.value) == (
        "::error::command_hints holds a variable reference, and it accepts none. Write true or "
        "false into the file."
    )


def _needs(value):
    return {"layout": "folder", "environments": {"prod": {"needs": value}}}


_ORDERING = [
    (
        _needs("dev-eu"),
        (
            "::error::environments.prod.needs must be a list of env-name strings, got str "
            "('dev-eu'); did you mean ['dev-eu']?"
        ),
    ),
    (
        _needs([1]),
        (
            "::error::environments.prod.needs elements must all be env-name strings; "
            "got non-string element(s) [1]"
        ),
    ),
    (
        _needs(["Dev"]),
        (
            "::error::environments.prod.needs entry 'Dev' is not an environment name: "
            "lowercase letters, digits, '-' and '_', with no quotes, spaces or path separators."
        ),
    ),
    (
        _needs(["dev-eu-plan"]),
        (
            "::error::environments.prod.needs entry 'dev-eu-plan' carries the environment suffix "
            "'-plan'; environments.prod.needs is matched against the bare logical env name; "
            "write 'dev-eu' instead."
        ),
    ),
    (
        _needs(["dev-eu-apply"]),
        (
            "::error::environments.prod.needs entry 'dev-eu-apply' carries the environment "
            "suffix '-apply'; environments.prod.needs is matched against the bare logical env "
            "name; write 'dev-eu' instead."
        ),
    ),
    (
        _needs(["shipmate-engine"]),
        (
            "::error::environments.prod.needs entry 'shipmate-engine' names 'shipmate-engine', "
            "the engine's own environment, which holds the shipmate App's private key; use "
            "another name."
        ),
    ),
    (
        _needs(["dev eu"]),
        (
            "::error::environments.prod.needs entry 'dev eu' is not an environment name: "
            "lowercase letters, digits, '-' and '_', with no quotes, spaces or path separators."
        ),
    ),
    (
        _needs(['"dev-eu"']),
        (
            "::error::environments.prod.needs entry '\"dev-eu\"' is not an environment name: "
            "lowercase letters, digits, '-' and '_', with no quotes, spaces or path separators."
        ),
    ),
]


@pytest.mark.parametrize(("table", "message"), _ORDERING, ids=range(len(_ORDERING)))
def test_the_single_entry_point_validates_ordering(table, message):
    """`needs` is checked by the same entry point as `layout` and `environments`, so a
    "valid configuration" verdict cannot leave an ordering error to surface when an apply
    finally reads the field.

    Both environment suffixes are cases here, not one: the refusal exists because every
    documented environment name carries `-plan` or `-apply`, and a suffixed predecessor
    matches no environment and orders nothing. A pasted quote is its own case: a quoted
    string inside single quotes, `'"dev-eu"'`, arrives with its inner quotes still on.

    Mutations: delete the `validate_env_name_list` call from `_check_entries` -- every
    case validates; drop either entry from the suffix tuple in `check_env_name` -- that
    suffix's case validates; widen `_ENV_ENTRY` to `.+` -- the space, quote and
    uppercase cases validate; delete the `ENGINE_ENV` check from `check_env_name`, or change
    `ENGINE_ENV`'s value -- the `shipmate-engine` case validates.
    """
    assert _refusal(table) == message


def test_a_tier_word_that_is_not_the_trailing_suffix_is_accepted():
    """The other half of the suffix rule: only a trailing `-plan`/`-apply` is the tier
    suffix. A refusal here would strand every ordering a repository declared under a name
    that merely carries one of the two words.

    `eu-plan-1` is the case that discriminates, and it is the only one: the suffixes are
    matched with their hyphen, so `plan-eu` and `apply-svc` survive a containment test too
    and pin the leading-word half rather than this one.

    Mutation: `e.endswith(suffix)` -> `suffix in e` in `check_env_name` -- `eu-plan-1`
    then refuses.
    """
    table = _needs(["dev", "plan-eu", "apply-svc", "eu-plan-1"])
    assert env_config.validate(table, ()) == table


_CYCLE_TAIL = (
    ": each of those must fully apply before the next, so the ordering has no first "
    "environment and no apply path can sort it. Break the chain in .github/shipmate-config.yml."
)
_CYCLES = [
    ({"dev": ["prod"], "prod": ["dev"]}, "dev -> prod -> dev"),
    ({"dev": ["dev"]}, "dev -> dev"),
    ({"a": ["b"], "b": ["c"], "c": ["a"]}, "a -> c -> b -> a"),
]


@pytest.mark.parametrize(("order", "cycle"), _CYCLES, ids=range(len(_CYCLES)))
def test_a_cycle_across_needs_refuses_structurally(order, cycle):
    """Acyclicity is decidable from the file alone, so it belongs with the structural checks:
    without it `validate_structure` passes a file that `env_levels` later refuses with a raw
    `CycleError`, and `shipmate doctor` certifies it as sound in the meantime.

    Three cases, because each pins something the others cannot: two nodes for the ordinary
    cycle, a self-edge (an env listed as its own predecessor is a cycle too), and three nodes
    for the direction the path is rendered in -- `graphlib` reports each node before its
    successor, so reversing the join silently mislabels every cycle longer than two.

    Mutations: delete the `TopologicalSorter` block from `_check_cycle`, or the
    `_check_cycle` call from `_check_entries` -- all three tables validate.
    """
    table = {"layout": "folder", "environments": {e: {"needs": p} for e, p in order.items()}}
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(table)
    assert str(excinfo.value) == f"::error::needs is cyclic: {cycle}{_CYCLE_TAIL}"


def test_a_deep_acyclic_needs_chain_still_validates():
    """The other half of the cycle rule: a legitimate chain must keep validating. A check that
    refuses a valid ordering is worse than the defect it fixes, and four levels, `dev` to
    `prod-us`, is what the engine's own `MAX_ENV_LEVELS` cap allows.

    Mutation: refuse any env that is both a key and a predecessor rather than a cycle --
    `stage` is both, and this legitimate chain then refuses.
    """
    table = {
        "layout": "folder",
        "environments": {
            "stage": {"needs": ["dev"]},
            "prod": {"needs": ["stage"]},
            "prod-us": {"needs": ["prod", "stage"]},
        },
    }
    assert env_config.validate_structure(table) is table


def test_a_needs_chain_deeper_than_the_env_levels_refuses_structurally():
    """Five levels: the deploy applies four, so without this check the table validates and
    refuses only at deploy, once a cell is pending in `e`. `a` is no entry, so the chain walks
    through a dangling predecessor.

    Mutation: compare the depth with `>` instead of `>=` -- this chain validates.
    """
    table = {
        "layout": "folder",
        "environments": {
            "b": {"needs": ["a"]},
            "c": {"needs": ["b"]},
            "d": {"needs": ["c"]},
            "e": {"needs": ["d"]},
        },
    }
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(table)
    assert str(excinfo.value) == (
        "::error::needs spans 5 env levels: a -> b -> c -> d -> e. A deploy applies at most "
        "4 env levels. Shorten the chain in .github/shipmate-config.yml."
    )


def test_a_too_deep_needs_graph_names_one_chain_the_sorted_first_at_each_tie():
    """`f` ties `e` for deepest and `x` ties `d` as `e`'s predecessor; one chain is named.

    Mutation: pick `max` instead of `min` for the deepest env or for the predecessor -- the
    message names `f` or `x`.
    """
    table = {
        "layout": "folder",
        "environments": {
            "b": {"needs": ["a"]},
            "c": {"needs": ["b"]},
            "d": {"needs": ["c"]},
            "x": {"needs": ["c"]},
            "e": {"needs": ["x", "d"]},
            "f": {"needs": ["d"]},
        },
    }
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(table)
    assert str(excinfo.value) == (
        "::error::needs spans 5 env levels: a -> b -> c -> d -> e. A deploy applies at most "
        "4 env levels. Shorten the chain in .github/shipmate-config.yml."
    )


def test_env_order_reads_needs_off_every_entry_holding_it():
    """A declared empty `needs` is an ordering entry with no predecessors, and an entry
    without `needs` is no entry at all.

    Mutations: read `needs` only when it is non-empty -- `b` drops out; or default an absent
    `needs` to `[]` -- `c` appears.
    """
    table = {
        "layout": "folder",
        "environments": {
            "c": {"region": "eu-west-1"},
            "a": {"needs": ["x", "y"]},
            "b": {"needs": []},
        },
    }
    assert env_config.env_order(table) == {"a": ["x", "y"], "b": []}


_ENTRY_NAMES = [
    (
        "Prod",
        (
            "::error::environments.Prod is not an environment name: "
            "lowercase letters, digits, '-' and '_', with no quotes, spaces or path separators."
        ),
    ),
    (
        "dev eu",
        (
            "::error::environments.dev eu is not an environment name: "
            "lowercase letters, digits, '-' and '_', with no quotes, spaces or path separators."
        ),
    ),
    (
        "dev-plan",
        (
            "::error::environments.dev-plan carries the environment suffix '-plan'; an entry "
            "name is matched against the bare logical env name; write 'dev' instead."
        ),
    ),
    (
        "shipmate-engine",
        (
            "::error::environments.shipmate-engine names 'shipmate-engine', the engine's own "
            "environment, which holds the shipmate App's private key; use another name."
        ),
    ),
    (
        "shipmate-engine-apply",
        (
            "::error::environments.shipmate-engine-apply names 'shipmate-engine', the engine's "
            "own environment, which holds the shipmate App's private key; use another name."
        ),
    ),
]


@pytest.mark.parametrize(("name", "message"), _ENTRY_NAMES, ids=range(len(_ENTRY_NAMES)))
def test_an_entry_name_no_environment_can_take_refuses(name, message):
    """Flags and ordering hang off the entry name, so an entry no Terramate tag can name
    orders nothing and is never resolved. The name is checked before the entry is read: each
    entry here is otherwise valid.

    `shipmate-engine-apply` gets the engine message, not the suffix one, whose remedy would
    be the reserved name.

    Mutations: check the charset on list items only -- drop the entry-name loop from
    `_check_entries`; every case validates. Delete the `ENGINE_ENV` check from
    `check_env_name` -- `shipmate-engine` validates and `shipmate-engine-apply` gets the
    suffix message; change `ENGINE_ENV`'s value -- both validate; compare `e` alone, without
    removing the suffix -- `shipmate-engine-apply` gets the suffix message.
    """
    assert _refusal({"layout": "folder", "environments": {name: {"region": "eu-west-1"}}}) == (
        message
    )


@pytest.mark.parametrize("name", ["shipmate-engineer", "shipmate-engine2"])
def test_a_name_extending_the_engine_environment_is_accepted(name):
    """Only the engine's own name is reserved; a longer name holding it binds another
    environment.

    Mutation: refuse `e.startswith(ENGINE_ENV)` in `check_env_name` -- both refuse.
    """
    table = {"layout": "folder", "environments": {name: {"region": "eu-west-1"}}}
    assert env_config.validate(table, ()) == table


# --- 12: the tf_vars layout, the entry's vars table, the vars reference -------------


def test_the_tf_vars_layout_derives_both_identity_variables():
    """Mutation: leave `_derived` switching on `"dry"` -- the layout then derives nothing."""
    table = {"layout": "tf_vars", "environments": {"dev-eu": {"region": "eu-west-1"}}}
    assert env_config.resolve(table, "dev-eu", "plan", "") == {
        "role_arn": "",
        "cred_region": "",
        "tf_vars": {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"},
        "config_path": "plan",
        "env_binding": "dev-eu-plan",
    }


def test_the_entry_vars_table_reaches_the_row():
    """Mutation: `resolve` reads `entry.get("tf_vars")` -- the row then carries only the
    derivation."""
    table = {
        "layout": "tf_vars",
        "environments": {"dev-eu": {"region": "eu-west-1", "vars": {"TF_VAR_x": "y"}}},
    }
    assert env_config.resolve(table, "dev-eu", "apply", "") == {
        "role_arn": "",
        "cred_region": "",
        "tf_vars": {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1", "TF_VAR_x": "y"},
        "config_path": "apply",
        "env_binding": "dev-eu-apply",
    }


_ROLE_TEXT = (
    "layout: folder\n\nidentities:\n  prod:\n    aws:\n      apply: {}\n\n"
    "environments:\n  prod:\n    region: eu-west-1\n    identity: prod\n"
)


def test_a_vars_reference_resolves():
    """Mutation: `_is_reference` reads `var` -- the mapping then reaches validation."""
    table = env_config.parse_table(
        _ROLE_TEXT.format("{vars: ROLE}"), {"ROLE": "arn:aws:iam::9817:role/r"}
    )
    assert table == {
        "layout": "folder",
        "identities": {"prod": {"aws": {"apply": "arn:aws:iam::9817:role/r"}}},
        "environments": {"prod": {"region": "eu-west-1", "identity": "prod"}},
    }
    assert env_config.validate_structure(table) == table


_VARS_ENTRY = "layout: folder\nenvironments:\n  dev:\n    vars: ENTRY\n"


def test_an_entry_holding_only_vars_with_its_variable_set_refuses():
    """`dev: {vars: ENTRY}` is a reference to the whole entry, not an entry holding `vars`, so
    it resolves to the variable's string and refuses as a non-mapping entry.

    Mutation: make `_check_environment` return early on a non-mapping entry instead of
    refusing -- this validates.
    """
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.parse_table(_VARS_ENTRY, {"ENTRY": "TF_VAR_x"}))
    assert str(excinfo.value) == "::error::environment dev must be a mapping, got str."


def test_an_entry_holding_only_vars_with_its_variable_unset_refuses():
    """Mutation: resolve an unset reference to "" instead of refusing -- this refuses with
    the non-mapping message instead."""
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.parse_table(_VARS_ENTRY, {}))
    assert str(excinfo.value) == (
        "::error::.github/shipmate-config.yml environments.dev references GitHub variable "
        "ENTRY, which is not set. A reference reads repository and organization variables; the "
        "variables of a cell's <env>-plan, <env>-apply or shared <env> Environment are never "
        "read."
    )


def test_a_lowercase_reference_refusal_spells_the_vars_key():
    """Mutation: leave the refusal writing `{var: ...}`."""
    with pytest.raises(SystemExit) as excinfo:
        env_config.parse_table(_ROLE_TEXT.format("{vars: role}"), {})
    assert str(excinfo.value) == (
        "::error::.github/shipmate-config.yml identities.prod.aws.apply references GitHub "
        'variable "role"; GitHub variable names are uppercase. Write {vars: ROLE}.'
    )


# --- 12: every structural error in one refusal ------------------------------------------


def _structural(table):
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(table)
    return str(excinfo.value)


def test_three_independent_errors_refuse_as_three_lines():
    """A misspelled key, a capitalised boolean and a self-referencing `needs` in the same entry
    are three typos, and one refusal names all three rather than one per run.

    Mutations: re-raise inside `_gather` -- only the cycle line is left, because the other
    two are appended without it; or drop the `_check_cycle` call from `_check_entries` --
    the cycle line is lost.
    """
    table = env_config.parse_table(
        "layout: folder\n\nenvironments:\n  dev-eu:\n    regoin: eu-west-1\n    review: False\n"
        "    needs: [dev-eu]\n"
    )
    assert _structural(table) == (
        "::error::environment dev-eu: regoin is not a key this engine implements. An "
        "environment holds region, vars, identity, workloads, split, needs, apply_mode, review.\n"
        "::error::environments.dev-eu.review must be true or false, got 'False'.\n"
        "::error::needs is cyclic: dev-eu -> dev-eu: each of those must fully apply before "
        "the next, so the ordering has no first environment and no apply path can sort it. "
        "Break the chain in .github/shipmate-config.yml."
    )


def test_the_lines_come_in_check_order():
    """Top-level keys, then `layout`, then the entries: the order the checks run in.

    Mutations: move the `layout` check after `_check_entries` -- the layout line moves last;
    or `return` from `validate_structure` right after the `layout` check -- the entry line is
    lost.
    """
    table = {"colour": 1, "lyout": "folder", "environments": {"dev": {"regoin": "eu-west-1"}}}
    assert _structural(table) == (
        "::error::colour is not a setting this engine implements. "
        ".github/shipmate-config.yml holds layout, identities, environments, max_parallel, "
        "command_hints.\n"
        "::error::lyout is not a setting this engine implements. "
        ".github/shipmate-config.yml holds layout, identities, environments, max_parallel, "
        "command_hints.\n" + NO_LAYOUT + "\n"
        "::error::environment dev: regoin is not a key this engine implements. An environment "
        "holds region, vars, identity, workloads, split, needs, apply_mode, review."
    )


def test_a_non_mapping_environments_is_one_message():
    """Mutation: drop the `isinstance(environments, dict)` skip in `validate_structure` --
    the entries loop indexes a string and raises a raw `TypeError`."""
    assert _structural({"layout": "folder", "environments": "x"}) == (
        "::error::environments must be a mapping, got str."
    )


@pytest.mark.parametrize(("value", "kind"), [("x", "str"), (7, "int")])
def test_a_non_mapping_entry_skips_only_its_own_checks(value, kind):
    """The entry beside it is still checked; the non-mapping one gets its one message.

    Mutations: drop the `isinstance(entry, dict)` return in `_check_environment` -- both
    cases raise a raw exception; or drop the `isinstance(entry, dict)` test on the `needs`
    loop in `_check_entries` -- the `int` case raises a raw `TypeError`.
    """
    table = {"layout": "folder", "environments": {"dev": value, "prod": {"regoin": "eu"}}}
    assert _structural(table) == (
        f"::error::environment dev must be a mapping, got {kind}.\n"
        "::error::environment prod: regoin is not a key this engine implements. An environment "
        "holds region, vars, identity, workloads, split, needs, apply_mode, review."
    )


def test_a_non_mapping_vars_is_one_message():
    """Mutation: drop the `isinstance(env_vars, dict)` return in `_check_vars` -- `.items()`
    on a string raises a raw `AttributeError`."""
    table = {"layout": "folder", "environments": {"dev": {"vars": "x"}}}
    assert _structural(table) == "::error::environment dev: vars must be a mapping, got str."


def test_one_error_is_todays_message_byte_for_byte():
    """Mutation: join the lines with `"\n".join(errors) + "\n"` -- a trailing newline."""
    assert _structural({"layout": "drys"}) == (
        "::error::layout is 'drys'; it must be one of tf_vars, workspace, folder."
    )
