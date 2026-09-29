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
    "::error::.github/shipmate.toml declares no layout, so no cell can resolve its "
    'environment identity. Declare layout = "tf_vars", "workspace" or "folder" on the default '
    "branch, which is where this table is read from. A scalar written below a [table] header "
    "lands inside that table rather than at the top level, so layout must come before the "
    "first header."
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
        '::error::layout = "tf_vars" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and prod-us has no entry in it."
    )


def test_tf_vars_refuses_an_entry_with_no_region():
    """Mutation: check only that the entry exists, not that it carries a region."""
    table = {"layout": "tf_vars", "environments": {"dev-eu": {}}}
    assert _refusal(table, matrix_envs=("dev-eu",)) == (
        '::error::layout = "tf_vars" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and dev-eu has an entry with no region."
    )


def test_tf_vars_refuses_an_empty_region():
    """An empty TF_VAR_region drops out of the plan fingerprint, so it is not a region.

    Mutation: accept a present-but-empty region.
    """
    table = {"layout": "tf_vars", "environments": {"dev-eu": {"region": ""}}}
    assert _refusal(table, matrix_envs=("dev-eu",)) == (
        '::error::layout = "tf_vars" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and dev-eu has an entry with no region."
    )


def test_a_non_tf_vars_layout_needs_no_entry():
    """Mutation: run the coverage check for every layout."""
    table = {"layout": "workspace", "environments": {}}
    assert env_config.validate(table, ("dev-eu",)) == table


# --- 3: a tier that resolves a credential resolves every required field ---------------


def test_a_credential_tier_missing_a_required_field_refuses():
    """Mutation: drop the required-field check -- the credentials step fails mid-run."""
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"aws": {"apply": {"role": "arn:aws:iam::9817:role/a"}}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: aws.apply resolves a role but no region, and "
        "the aws credentials step requires one. Set aws.region, or the environment's "
        "own region."
    )


def test_a_workload_tier_missing_a_required_field_refuses():
    """A tier walker that stops at plan/apply passes the plain case and misses this one.

    Mutation: yield only the plan and apply tiers, not the workload tiers.
    """
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {
                "aws": {"apply": {"workloads": {"net-edge": {"role": "arn:aws:iam::9817:role/n"}}}}
            }
        },
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: aws.apply.workloads.net-edge resolves a role but "
        "no region, and the aws credentials step requires one. Set aws.region, or the "
        "environment's own region."
    )


def test_the_environment_region_satisfies_the_required_field():
    """The one cross-level default in the schema.

    Mutation: stop inheriting the environment's region into the provider block.
    """
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "aws": {"apply": {"role": "arn:aws:iam::9817:role/a"}},
            }
        },
    }
    assert env_config.validate(table, ()) == table


# --- 4: a field the provider does not define ------------------------------------------


def test_a_field_the_provider_does_not_define_refuses():
    """Mutation: drop the field allowlist -- aws.plan.client_id would reach a run."""
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"aws": {"plan": {"client_id": "x"}}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: aws.plan.client_id is not a field the aws "
        "provider defines. It defines region, role."
    )


def test_tf_vars_inside_a_provider_block_refuses():
    """`tf_vars` sits at environment level; a per-tier one fails every apply as stale.

    Mutation: drop the `tf_vars` case from the field check (it then refuses as an unknown
    field, with a message that does not say where `tf_vars` belongs).
    """
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"aws": {"plan": {"tf_vars": {"TF_VAR_x": "y"}}}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: aws.plan.tf_vars is not a field the aws provider "
        "defines. tf_vars sits at environment level: a per-tier one would let plan and "
        "apply inject different values, and every apply would then fail as stale."
    )


# --- 5: an unimplemented provider key -------------------------------------------------


def test_an_unimplemented_provider_key_refuses():
    """Mutation: skip an unrecognised environment key instead of refusing it."""
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"azure": {"plan": {"client_id": "x"}}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: azure is not a key this engine implements. "
        "An environment holds region, tf_vars, aws, shared, needs, explicit, gated."
    )


def test_a_misspelled_environment_key_refuses():
    """The same refusal covers a typo, so the message names every legal key.

    Mutation: as above.
    """
    table = {"layout": "folder", "environments": {"dev-eu": {"regoin": "eu-west-1"}}}
    assert _refusal(table) == (
        "::error::environment dev-eu: regoin is not a key this engine implements. "
        "An environment holds region, tf_vars, aws, shared, needs, explicit, gated."
    )


# --- 6: a provider block resolving no credential on any tier ---------------------------


def test_a_provider_block_resolving_no_credential_refuses():
    """A block that authenticates nothing is dead config, and skipping it silently is
    the fail-open reading.

    Mutation: drop the any-tier check.
    """
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"aws": {"region": "eu-west-1"}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: the aws block resolves no role on any tier. "
        "Give aws, aws.plan, aws.apply or a workload a role, or remove the block."
    )


def test_a_tier_setting_an_empty_role_refuses():
    """An empty role is not a credential: it resolves to a silently skipped credentials
    step, which is the dead configuration refusal 6 exists to stop, reached with an empty
    string instead of a missing key.

    Mutation: delete this check -- the plan tier then runs uncredentialed with no refusal
    anywhere. Widening refusal 6 to truth instead does not cover it: the apply tier
    satisfies `any` on its own.
    """
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {
                "aws": {
                    "region": "eu-west-1",
                    "apply": {"role": "arn:aws:iam::9817:role/apply"},
                    "plan": {"role": ""},
                }
            }
        },
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: aws.plan sets an empty role, which resolves to a "
        "skipped credentials step rather than to a credential. Give it a role, or remove "
        "the key."
    )


def test_an_apply_only_tier_passes():
    """Apply-only cloud access: the plan path resolves an empty credential and skips the
    step.

    Mutation: refuse when any tier lacks a role -- this reds while the refusal-6 fixture
    stays green, which is what separates the two.
    """
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "aws": {"apply": {"role": "arn:aws:iam::9817:role/a"}},
            }
        },
    }
    assert env_config.validate(table, ()) == table


# --- 7: the shared key ----------------------------------------------------------------

_PLAN_AND_APPLY = {
    "region": "eu-west-1",
    "aws": {
        "plan": {"role": "arn:aws:iam::9817:role/p"},
        "apply": {"role": "arn:aws:iam::9817:role/a"},
    },
}


_FLAGS = ("shared", "explicit", "gated")


@pytest.mark.parametrize("key", _FLAGS)
@pytest.mark.parametrize("value", [True, False])
def test_a_boolean_flag_validates(key, value):
    """Mutation: drop any of the three from the implemented environment keys."""
    table = {"layout": "folder", "environments": {"dev-eu": {key: value}}}
    assert env_config.validate_structure(table) is table


_NOT_BOOLEAN = [
    ("shared", "true", "str"),
    ("shared", 1, "int"),
    ("shared", "yes", "str"),
    ("explicit", "true", "str"),
    ("explicit", {"vars": "X"}, "dict"),
    ("gated", "false", "str"),
    ("gated", 0, "int"),
]


@pytest.mark.parametrize(("key", "value", "found"), _NOT_BOOLEAN, ids=range(len(_NOT_BOOLEAN)))
def test_a_flag_that_is_not_a_boolean_refuses(key, value, found):
    """A quoted `"true"` reads as set to a person and resolves as unset. For `explicit` that
    is the fail-open case: the environment lands on a bare `shipmate apply`.

    Mutations: drop the `isinstance` check from `_check_flags` -- every case validates; or
    drop one key from the checked flags -- that key's cases validate.
    """
    table = {"layout": "folder", "environments": {"dev-eu": {key: value}}}
    assert _refusal(table) == (
        f"::error::environments.dev-eu.{key} must be a boolean, got {found}. Write "
        f"{key} = true or {key} = false, unquoted."
    )


def test_a_referenced_flag_refuses_as_the_string_it_resolves_to():
    """A reference resolves before validation, so `explicit = { vars = "X" }` reaches the
    boolean check as the variable's string value, and that is the message a run prints.

    Mutation: drop `explicit` from the checked flags -- this validates.
    """
    text = 'layout = "folder"\n[environments.prod]\nexplicit = { vars = "HOLD" }\n'
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(env_config.parse_table(text, {"HOLD": "true"}))
    assert str(excinfo.value) == (
        "::error::environments.prod.explicit must be a boolean, got str. Write "
        "explicit = true or explicit = false, unquoted."
    )


def test_a_shared_environment_declaring_plan_refuses_without_run_context():
    """The contradiction is structural, so `validate_structure` alone refuses it: `shipmate
    doctor` validates a branch with nothing else.

    Mutation: move the check out of `_check_environment` into `validate` -- this table then
    passes `validate_structure`.
    """
    table = {"layout": "folder", "environments": {"dev-eu": {**_PLAN_AND_APPLY, "shared": True}}}
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(table)
    assert str(excinfo.value) == (
        "::error::environment dev-eu is shared between the plan and apply paths, so "
        "aws.plan cannot apply to it: a shared environment resolves aws.apply on both "
        "paths. Remove aws.plan, or set shared = false."
    )


def test_an_unshared_environment_may_declare_plan():
    """`shared = false` is the second remedy the refusal above names, so it must validate.

    Mutation: refuse `aws.plan` whenever the key is present, whatever its value.
    """
    table = {"layout": "folder", "environments": {"dev-eu": {**_PLAN_AND_APPLY, "shared": False}}}
    assert env_config.validate_structure(table) is table


def test_a_shared_environment_declaring_only_apply_validates():
    """Mutation: refuse any `aws` block in a shared environment."""
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "shared": True,
                "aws": {"apply": {"role": "arn:aws:iam::9817:role/a"}},
            }
        },
    }
    assert env_config.validate_structure(table) is table


# --- 8: tf_vars names and values ----------------------------------------------------------


def test_tf_vars_naming_anything_outside_the_allowlist_refuses():
    """Mutation: drop the name allowlist -- the table becomes a general env injector."""
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"tf_vars": {"AWS_REGION": "eu-west-1"}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: tf_vars may name only TF_VAR_* and TF_WORKSPACE, "
        "and AWS_REGION is neither."
    )


def test_tf_vars_holding_a_non_string_refuses():
    """Caught here rather than per cell: env-inject would refuse once per cell instead.

    Mutation: check the names and not the values.
    """
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"tf_vars": {"TF_VAR_count": 3}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: tf_vars.TF_VAR_count must be a string, got int. "
        "A non-string value would otherwise refuse once per cell at injection time."
    )


def test_tf_vars_may_hold_an_empty_string():
    """An explicit empty value is excluded from the plan fingerprint on both paths, so it
    is legal.

    Mutation: refuse a falsy value.
    """
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"tf_vars": {"TF_VAR_region": "", "TF_WORKSPACE": "w"}}},
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
        {"layout": "folder", "environments": {"dev-eu": {"tf_vars": "TF_VAR_x"}}},
        "::error::environment dev-eu: tf_vars must be a mapping, got str.",
    ),
    (
        {"layout": "folder", "environments": {"dev-eu": {"aws": "arn:aws:iam::9817:role/a"}}},
        "::error::environment dev-eu: aws must be a mapping, got str.",
    ),
    (
        {"layout": "folder", "environments": {"dev-eu": {"aws": {"plan": "arn"}}}},
        "::error::environment dev-eu: aws.plan must be a mapping, got str.",
    ),
    (
        {"layout": "folder", "environments": {"dev-eu": {"aws": {"role": {"arn": "a"}}}}},
        "::error::environment dev-eu: aws.role must be a string, got dict.",
    ),
    (
        {
            "layout": "folder",
            "environments": {"dev-eu": {"aws": {"apply": {"workloads": "net-edge"}}}},
        },
        "::error::environment dev-eu: aws.apply.workloads must be a mapping, got str.",
    ),
    (
        {
            "layout": "folder",
            "environments": {"dev-eu": {"aws": {"apply": {"workloads": {"net-edge": "arn"}}}}},
        },
        "::error::environment dev-eu: aws.apply.workloads.net-edge must be a mapping, got str.",
    ),
]


@pytest.mark.parametrize(("table", "message"), _MALFORMED, ids=range(len(_MALFORMED)))
def test_a_malformed_shape_refuses(table, message):
    """A string where an object is required, and the reverse.

    Mutation: accept the value and carry on (skip the entry rather than raise) -- a crash
    is not the property, refusal is.
    """
    assert _refusal(table) == message


_MISPLACED = [
    (
        {"layout": "folder", "environments": {"dev-eu": {"aws": {"workloads": {"n": {}}}}}},
        "::error::environment dev-eu: aws.workloads is a reserved key in a position the "
        "schema does not give it. plan and apply sit inside a provider block; workloads "
        "sits only under plan or apply.",
    ),
    (
        {
            "layout": "folder",
            "environments": {
                "dev-eu": {"aws": {"apply": {"workloads": {"net-edge": {"plan": {}}}}}}
            },
        },
        "::error::environment dev-eu: aws.apply.workloads.net-edge.plan is a reserved key "
        "in a position the schema does not give it. plan and apply sit inside a provider "
        "block; workloads sits only under plan or apply.",
    ),
    (
        {
            "layout": "folder",
            "environments": {"dev-eu": {"aws": {"apply": {"workloads": {"n": {"workloads": {}}}}}}},
        },
        "::error::environment dev-eu: aws.apply.workloads.n.workloads is a reserved key in "
        "a position the schema does not give it. plan and apply sit inside a provider "
        "block; workloads sits only under plan or apply.",
    ),
]


@pytest.mark.parametrize(("table", "message"), _MISPLACED, ids=range(len(_MISPLACED)))
def test_a_structural_key_in_a_forbidden_position_refuses(table, message):
    """A workload role is meaningless without the path it applies to.

    Mutation: skip a structural key wherever it appears instead of refusing the position.
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
    """An empty file is valid TOML, so the parser passes it through and this is the only site
    that can refuse it.

    Mutation: restore `if "layout" not in table: return table`.
    """
    assert _refusal({}) == NO_LAYOUT


def test_a_whole_table_is_returned_unchanged():
    """Validation returns the table it was given, every top-level setting included.

    Mutation: return only the `environments` mapping.
    """
    table = {
        "layout": "tf_vars",
        "gate": {"approver_team": "deployers"},
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "needs": ["prod-us"],
                "tf_vars": {"TF_VAR_team": "core"},
                "aws": {
                    "region": "eu-central-1",
                    "plan": {"role": "arn:aws:iam::9817:role/p"},
                    "apply": {
                        "role": "arn:aws:iam::9817:role/a",
                        "workloads": {"net-edge": {"role": "arn:aws:iam::9817:role/n"}},
                    },
                },
            }
        },
    }
    assert env_config.validate(table, ("dev-eu",)) == {
        "layout": "tf_vars",
        "gate": {"approver_team": "deployers"},
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "needs": ["prod-us"],
                "tf_vars": {"TF_VAR_team": "core"},
                "aws": {
                    "region": "eu-central-1",
                    "plan": {"role": "arn:aws:iam::9817:role/p"},
                    "apply": {
                        "role": "arn:aws:iam::9817:role/a",
                        "workloads": {"net-edge": {"role": "arn:aws:iam::9817:role/n"}},
                    },
                },
            }
        },
    }


def test_an_empty_layout_refuses():
    """TOML has no null, so the unreachable `layout = null` case is retired. An empty string
    is the reachable neighbour: a declared layout holding a value no layout may hold.

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
        ".github/shipmate.toml holds schema_version, layout, environments, gate."
    )


def test_every_allowed_top_level_key_is_accepted():
    """The other half of the strict-key rule: the four names are the whole allowed set, so a
    table using all four must validate. Compared against a hand-written table, never against
    the module's own constant.

    Mutation: remove a name from the allowed set -- one of these four then refuses.
    """
    table = {
        "layout": "folder",
        "environments": {},
        "gate": {"approver_team": "deployers"},
        "schema_version": 1,
    }
    assert env_config.validate(table, ()) == table


def test_the_old_explicit_envs_list_refuses_as_unknown():
    """No alias: the old top-level list refuses through the strict top-level loop.

    Mutation: keep `"explicit_envs"` in `_TOP_KEYS` -- the file then validates.
    """
    assert _refusal({"layout": "folder", "explicit_envs": ["prod"]}) == (
        "::error::explicit_envs is not a setting this engine implements. "
        ".github/shipmate.toml holds schema_version, layout, environments, gate."
    )


def _needs(value):
    return {"layout": "folder", "environments": {"prod": {"needs": value}}}


_ORDERING = [
    (
        _needs("dev-eu"),
        "::error::environments.prod.needs must be a list of env-name strings, got str "
        "('dev-eu'); did you mean ['dev-eu']?",
    ),
    (
        _needs([1]),
        "::error::environments.prod.needs elements must all be env-name strings; "
        "got non-string element(s) [1]",
    ),
    (
        _needs(["Dev"]),
        "::error::environments.prod.needs entry 'Dev' is not an environment name; entries are "
        "bare logical env names (lowercase letters, digits, '-' and '_'), with no quotes, "
        "spaces or path separators.",
    ),
    (
        _needs(["dev-eu-plan"]),
        "::error::environments.prod.needs entry 'dev-eu-plan' carries the environment suffix "
        "'-plan'; environments.prod.needs is matched against the bare logical env name — "
        "write 'dev-eu' instead.",
    ),
    (
        _needs(["dev-eu-apply"]),
        "::error::environments.prod.needs entry 'dev-eu-apply' carries the environment "
        "suffix '-apply'; environments.prod.needs is matched against the bare logical env "
        "name — write 'dev-eu' instead.",
    ),
    (
        _needs(["dev eu"]),
        "::error::environments.prod.needs entry 'dev eu' is not an environment name; entries "
        "are bare logical env names (lowercase letters, digits, '-' and '_'), with no quotes, "
        "spaces or path separators.",
    ),
    (
        _needs(['"dev-eu"']),
        "::error::environments.prod.needs entry '\"dev-eu\"' is not an environment name; "
        "entries are bare logical env names (lowercase letters, digits, '-' and '_'), with no "
        "quotes, spaces or path separators.",
    ),
]


@pytest.mark.parametrize(("table", "message"), _ORDERING, ids=range(len(_ORDERING)))
def test_the_single_entry_point_validates_ordering(table, message):
    """`needs` is checked by the same entry point as `layout` and `environments`, so a
    "valid configuration" verdict cannot leave an ordering error to surface when an apply
    finally reads the field.

    Both environment suffixes are cases here, not one: the refusal exists because every
    documented environment name carries `-plan` or `-apply`, and a suffixed predecessor
    matches no environment and orders nothing. A pasted quote is its own case: TOML quotes
    the string itself, so a pasted `"dev-eu"` arrives with its quotes still on.

    Mutations: delete the `validate_env_name_list` call from `_check_entries` -- every
    case validates; drop either entry from the suffix tuple in `_check_env_name` -- that
    suffix's case validates; or widen `_ENV_ENTRY` to `.+` -- the space, quote and
    uppercase cases validate.
    """
    assert _refusal(table) == message


def test_a_tier_word_that_is_not_the_trailing_suffix_is_accepted():
    """The other half of the suffix rule: only a trailing `-plan`/`-apply` is the tier
    suffix. A refusal here would strand every ordering a repository declared under a name
    that merely carries one of the two words.

    `eu-plan-1` is the case that discriminates, and it is the only one: the suffixes are
    matched with their hyphen, so `plan-eu` and `apply-svc` survive a containment test too
    and pin the leading-word half rather than this one.

    Mutation: `e.endswith(suffix)` -> `suffix in e` in `_check_env_name` -- `eu-plan-1`
    then refuses.
    """
    table = _needs(["dev", "plan-eu", "apply-svc", "eu-plan-1"])
    assert env_config.validate(table, ()) == table


_CYCLE_TAIL = (
    " — each of those must fully apply before the next, so the ordering has no first "
    "environment and no apply path can sort it. Break the chain in .github/shipmate.toml."
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
    refuses a valid ordering is worse than the defect it fixes, and three levels is what the
    engine's own `MAX_ENV_LEVELS` cap allows.

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


def test_the_old_env_order_table_refuses_as_unknown():
    """No alias: the old top-level ordering table refuses through the strict top-level loop.

    Mutation: keep `"env_order"` in `_TOP_KEYS` -- the file then validates.
    """
    assert _refusal({"layout": "folder", "env_order": {"prod": ["dev"]}}) == (
        "::error::env_order is not a setting this engine implements. "
        ".github/shipmate.toml holds schema_version, layout, environments, gate."
    )


_ENTRY_NAMES = [
    (
        "Prod",
        "::error::environments.Prod is not an environment name; entries are bare logical env "
        "names (lowercase letters, digits, '-' and '_'), with no quotes, spaces or path "
        "separators.",
    ),
    (
        "dev eu",
        "::error::environments.dev eu is not an environment name; entries are bare logical "
        "env names (lowercase letters, digits, '-' and '_'), with no quotes, spaces or path "
        "separators.",
    ),
    (
        "dev-plan",
        "::error::environments.dev-plan carries the environment suffix '-plan'; an entry "
        "name is matched against the bare logical env name — write 'dev' instead.",
    ),
]


@pytest.mark.parametrize(("name", "message"), _ENTRY_NAMES, ids=range(len(_ENTRY_NAMES)))
def test_an_entry_name_no_environment_can_take_refuses(name, message):
    """Flags and ordering hang off the entry name, so an entry no Terramate tag can name
    orders nothing and is never resolved. The name is checked before the entry is read: each
    entry here is otherwise valid.

    Mutation: check the charset on list items only -- drop the entry-name loop from
    `_check_entries`; every case validates.
    """
    assert _refusal({"layout": "folder", "environments": {name: {"region": "eu-west-1"}}}) == (
        message
    )


# --- 11: schema_version and the gate table ------------------------------------------------


def test_a_declared_schema_version_1_is_accepted():
    """Mutation: remove `"schema_version"` from `_TOP_KEYS` -- the strict top-level loop then
    refuses a file declaring the version this engine implements."""
    table = {"layout": "folder", "schema_version": 1}
    assert env_config.validate(table, ()) == table


def test_an_absent_schema_version_is_accepted():
    """`schema_version` is optional: absent reads as version 1.

    Mutation: make the version check unconditional -- `table["schema_version"]` then raises
    on every file that omits it.
    """
    table = {"layout": "folder"}
    assert env_config.validate(table, ()) == table


def test_a_future_schema_version_refuses_naming_the_one_implemented():
    """Mutation: compare against 2 rather than 1 -- a file this engine cannot read
    validates. Or read `table["version"]` in `_check_version` -- this raises `KeyError`."""
    assert _refusal({"layout": "folder", "schema_version": 2}) == (
        "::error::schema_version is 2; this engine implements version 1 of .github/shipmate.toml."
    )


def test_a_boolean_schema_version_refuses():
    """`True == 1` in Python, so a bare equality accepts `schema_version = true` as version 1
    and the file passes with a value no schema version can be.

    Mutation: drop the `isinstance(v, bool)` exclusion from `_check_version`.
    """
    assert _refusal({"layout": "folder", "schema_version": True}) == (
        "::error::schema_version is True; this engine implements version 1 of "
        ".github/shipmate.toml."
    )


def test_the_old_version_key_refuses_as_unknown():
    """No alias: the old name refuses through the strict top-level loop.

    Mutation: keep `"version"` in `_TOP_KEYS` -- the file then validates.
    """
    assert _refusal({"layout": "folder", "version": 1}) == (
        "::error::version is not a setting this engine implements. "
        ".github/shipmate.toml holds schema_version, layout, environments, gate."
    )


def test_a_gate_table_holding_its_key_is_accepted():
    """Mutation: remove `"gate"` from `_TOP_KEYS` -- the table a repository declares ahead of
    the release that reads it is refused as an unknown setting."""
    table = {"layout": "folder", "gate": {"approver_team": "deployers"}}
    assert env_config.validate(table, ()) == table


def test_an_absent_gate_table_is_accepted():
    """Mutation: `table.get("gate", {})` -> `table["gate"]` -- every file that declares no
    gate then raises instead of validating."""
    table = {"layout": "folder", "environments": {}}
    assert env_config.validate(table, ()) == table


def test_an_unknown_key_in_the_gate_table_refuses_by_name():
    """A misspelled gate key is silently inert: the setting keeps its default and the
    repository believes it declared one. The old `approvers_team` is one such key: no alias.

    Mutation: widen `_GATE_KEYS` to accept any key, or keep `"approvers_team"` in it.
    """
    assert _refusal({"layout": "folder", "gate": {"approvers_team": "deployers"}}) == (
        "::error::gate.approvers_team is not a key this engine implements. "
        "The gate table holds approver_team."
    )


def test_the_old_ungated_envs_list_refuses_as_an_unknown_gate_key():
    """No alias: the exemption now sits on each entry as `gated = false`.

    Mutation: keep `"ungated_envs"` in `_GATE_KEYS` -- the file then validates.
    """
    assert _refusal({"layout": "folder", "gate": {"ungated_envs": ["dev"]}}) == (
        "::error::gate.ungated_envs is not a key this engine implements. "
        "The gate table holds approver_team."
    )


def test_the_declared_approver_team_is_what_the_accessor_returns():
    """Mutation: read `approvers_team` in `gate_approver_team` -- it then returns ""."""
    table = env_config.validate_structure({"layout": "folder", "gate": {"approver_team": "ops"}})
    assert env_config.gate_approver_team(table) == "ops"


def test_a_non_string_approver_team_refuses():
    """Mutation: drop the `_string` call inside `validate_team_slug` -- a non-string team slug
    validates here and fails once per membership query instead."""
    assert _refusal({"layout": "folder", "gate": {"approver_team": 1}}) == (
        "::error::gate.approver_team must be a string, got int."
    )


_NOT_A_TEAM_SLUG = (
    "is {team!r}, which is not a GitHub team slug; use the bare slug from the team's URL "
    "(letters, digits, '-' and '_'), not a display name or an @org/team reference."
)


@pytest.mark.parametrize("team", ["Platform Team", "@ship-iac/platform", "platform ", '"platform"'])
def test_an_approver_team_that_is_not_a_slug_refuses(team):
    """A charset rule, as environment names have one. A display name, an `@org/team`
    reference or a padded slug 404s in the membership lookup, so every commenter is refused
    under a message naming the team as though it had resolved -- the same failure a missing
    team produces, with no diagnostic distinguishing them.

    Mutation: drop the `_TEAM_SLUG.fullmatch` check, or widen the pattern to `.*`.
    """
    assert _refusal({"layout": "folder", "gate": {"approver_team": team}}) == (
        f"::error::gate.approver_team {_NOT_A_TEAM_SLUG.format(team=team)}"
    )


def test_a_declared_empty_approver_team_still_validates():
    """An empty team is legal and authorizes nobody, so the charset rule must not refuse it.

    Mutation: drop the `if team and` guard -- a repository deliberately closing the comment
    path is then refused at every read of its file, including `doctor`'s.
    """
    table = {"layout": "folder", "gate": {"approver_team": ""}}
    assert env_config.validate_structure(table) == table


# --- 12: the tf_vars layout, the entry's tf_vars table, the vars reference -------------


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


@pytest.mark.parametrize("layout", ["dry", "vars"])
def test_the_old_layout_name_and_the_reference_key_refuse_as_layouts(layout):
    """No alias: `dry` is the old name, and `vars` names GitHub variables, not a layout.

    Mutation: leave `"dry"` in `_LAYOUTS` -- the `dry` case then validates.
    """
    assert _refusal({"layout": layout}) == (
        f"::error::layout is {layout!r}; it must be one of tf_vars, workspace, folder."
    )


def test_the_old_entry_vars_table_refuses_as_an_unknown_key():
    """Mutation: keep `"vars"` in `_ENV_KEYS` -- the entry then validates."""
    table = {"layout": "folder", "environments": {"dev-eu": {"vars": {"TF_VAR_x": "y"}}}}
    assert _refusal(table) == (
        "::error::environment dev-eu: vars is not a key this engine implements. "
        "An environment holds region, tf_vars, aws, shared, needs, explicit, gated."
    )


def test_the_entry_tf_vars_table_reaches_the_row():
    """Mutation: `resolve` reads `entry.get("vars")` -- the row then carries only the
    derivation."""
    table = {
        "layout": "tf_vars",
        "environments": {"dev-eu": {"region": "eu-west-1", "tf_vars": {"TF_VAR_x": "y"}}},
    }
    assert env_config.resolve(table, "dev-eu", "apply", "") == {
        "role_arn": "",
        "cred_region": "",
        "tf_vars": {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1", "TF_VAR_x": "y"},
        "config_path": "apply",
        "env_binding": "dev-eu-apply",
    }


_ROLE_TEXT = 'layout = "folder"\n\n[environments.prod]\nregion = "eu-west-1"\naws.apply.role = {}\n'


def test_a_vars_reference_resolves():
    """Mutation: `_is_reference` reads `var` -- the mapping then reaches validation."""
    table = env_config.parse_table(_ROLE_TEXT.format('{ vars = "ROLE" }'), {"ROLE": "r"})
    assert table == {
        "layout": "folder",
        "environments": {"prod": {"region": "eu-west-1", "aws": {"apply": {"role": "r"}}}},
    }
    assert env_config.validate_structure(table) == table


def test_the_old_var_reference_is_ordinary_data():
    """`{ var = "X" }` is a mapping again, so a string position refuses it.

    Mutation: `_is_reference` accepts `var` as well as `vars` -- the value then resolves from
    the variable set here and the table validates.
    """
    table = env_config.parse_table(_ROLE_TEXT.format('{ var = "ROLE" }'), {"ROLE": "r"})
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(table)
    assert str(excinfo.value) == (
        "::error::environment prod: aws.apply.role must be a string, got dict."
    )


def test_a_lowercase_reference_refusal_spells_the_vars_key():
    """Mutation: leave the refusal writing `{ var = ... }`."""
    with pytest.raises(SystemExit) as excinfo:
        env_config.parse_table(_ROLE_TEXT.format('{ vars = "role" }'), {})
    assert str(excinfo.value) == (
        "::error::.github/shipmate.toml environments.prod.aws.apply.role references GitHub "
        'variable "role"; GitHub variable names are uppercase. Write { vars = "ROLE" }.'
    )


# --- 12: every structural error in one refusal ------------------------------------------


def _structural(table):
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(table)
    return str(excinfo.value)


def test_three_independent_errors_refuse_as_three_lines():
    """A misspelled key, a quoted boolean in the same entry and a display name for the team
    are three typos, and one refusal names all three rather than one per run.

    Mutations: re-raise inside `_gather` -- only the first line is left; or drop the
    `_check_gate` call from `validate_structure` -- the team line is lost.
    """
    table = env_config.parse_table(
        'layout = "folder"\n\n[environments.dev-eu]\nregoin = "eu-west-1"\ngated = "false"\n\n'
        '[gate]\napprover_team = "Platform Team"\n'
    )
    assert _structural(table) == (
        "::error::environment dev-eu: regoin is not a key this engine implements. An "
        "environment holds region, tf_vars, aws, shared, needs, explicit, gated.\n"
        "::error::environments.dev-eu.gated must be a boolean, got str. Write gated = true or "
        "gated = false, unquoted.\n"
        "::error::gate.approver_team is 'Platform Team', which is not a GitHub team slug; use "
        "the bare slug from the team's URL (letters, digits, '-' and '_'), not a display name "
        "or an @org/team reference."
    )


def test_the_lines_come_in_check_order():
    """Top-level keys, then `layout`, then the entries: the order the checks run in.

    Mutations: move the `layout` check after `_check_entries` -- the layout line moves last;
    or `return` from `validate_structure` right after the `layout` check -- the entry line is
    lost.
    """
    table = {"colour": 1, "lyout": "folder", "environments": {"dev": {"regoin": "eu-west-1"}}}
    assert _structural(table) == (
        "::error::colour is not a setting this engine implements. .github/shipmate.toml holds "
        "schema_version, layout, environments, gate.\n"
        "::error::lyout is not a setting this engine implements. .github/shipmate.toml holds "
        "schema_version, layout, environments, gate.\n" + NO_LAYOUT + "\n"
        "::error::environment dev: regoin is not a key this engine implements. An environment "
        "holds region, tf_vars, aws, shared, needs, explicit, gated."
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
        "holds region, tf_vars, aws, shared, needs, explicit, gated."
    )


def test_a_refused_aws_block_skips_the_shared_plan_rule():
    """`"plan" in aws` on the string `"plan"` is a substring test that holds, so this value
    is the one that makes the rule report a second, false message when it runs anyway.

    Mutation: drop `aws_ok and` from the `aws.plan` rule in `_check_flags`.
    """
    table = {"layout": "folder", "environments": {"dev": {"aws": "plan", "shared": True}}}
    assert _structural(table) == "::error::environment dev: aws must be a mapping, got str."


def test_a_non_mapping_tf_vars_is_one_message():
    """Mutation: drop the `isinstance(tf_vars, dict)` return in `_check_vars` -- `.items()`
    on a string raises a raw `AttributeError`."""
    table = {"layout": "folder", "environments": {"dev": {"tf_vars": "x"}}}
    assert _structural(table) == "::error::environment dev: tf_vars must be a mapping, got str."


def test_a_non_mapping_gate_is_one_message():
    """Mutation: drop the `isinstance(gate, dict)` return in `_check_gate` -- each letter
    of `"ops"` is then refused as a gate key."""
    assert _structural({"layout": "folder", "gate": "ops"}) == (
        "::error::gate must be a mapping, got str."
    )


def test_one_error_is_todays_message_byte_for_byte():
    """Mutation: join the lines with `"\n".join(errors) + "\n"` -- a trailing newline."""
    assert _structural({"layout": "drys"}) == (
        "::error::layout is 'drys'; it must be one of tf_vars, workspace, folder."
    )


def test_validate_env_order_alone_still_refuses_at_its_first_error():
    """`env-order` calls it outside the structural pass, where the first refusal is the
    contract.

    Mutation: wrap each `validate_env_name_list` call in `validate_env_order` in a gatherer
    and raise the joined messages -- the second line appears.
    """
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_env_order({"a": "dev", "b": "prod"})
    assert str(excinfo.value) == (
        "::error::environments.a.needs must be a list of env-name strings, got str ('dev'); "
        "did you mean ['dev']?"
    )
