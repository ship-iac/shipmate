"""`env-config` refuses a malformed environment table before any cell runs.

Validation is what stops a typo from silently disabling injection or from handing a plan
tier write credentials. Every condition here refuses -- `raise SystemExit("::error::…")` --
rather than warning: a warning hands control back to the branch content this feature exists
to keep out. The two deliberate asymmetries -- a table entry nobody uses, and the migration
notice an untabled repository gets -- warn instead, and are not this module's subject.

Messages are compared whole against hand-written literals, never by substring and never
against a constant imported from the script: an operator reading a refusal in a run log has
no other source, so the text is part of the contract.
"""

import pytest
from _loader import load_script

env_config = load_script("env-config")


def _refusal(table, matrix_envs=(), shared_envs=()):
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate(table, matrix_envs, shared_envs)
    return str(excinfo.value)


# --- 1: layout ------------------------------------------------------------------------


def test_an_unknown_layout_refuses():
    """Mutation: drop the layout membership check -- a typo disables injection silently."""
    assert _refusal({"layout": "drys"}) == (
        "::error::globals \"shipmate\" layout is 'drys'; it must be one of dry, workspace, folder."
    )


def test_a_non_string_layout_refuses():
    """Mutation: as above; a mapping is not one of the three names either."""
    assert _refusal({"layout": {"dry": True}}) == (
        "::error::globals \"shipmate\" layout is {'dry': True}; it must be one of "
        "dry, workspace, folder."
    )


# --- 2: dry needs an entry with a region for every matrix environment -----------------


def test_dry_refuses_a_matrix_environment_with_no_entry():
    """Mutation: drop the coverage check -- the layout cannot derive its variables."""
    table = {"layout": "dry", "environments": {"dev-eu": {"region": "eu-west-1"}}}
    assert _refusal(table, matrix_envs=("dev-eu", "prod-us")) == (
        '::error::layout = "dry" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and prod-us has no entry in it."
    )


def test_dry_refuses_an_entry_with_no_region():
    """Mutation: check only that the entry exists, not that it carries a region."""
    table = {"layout": "dry", "environments": {"dev-eu": {}}}
    assert _refusal(table, matrix_envs=("dev-eu",)) == (
        '::error::layout = "dry" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and dev-eu has an entry with no region."
    )


def test_dry_refuses_an_empty_region():
    """An empty TF_VAR_region drops out of the plan fingerprint, so it is not a region.

    Mutation: accept a present-but-empty region.
    """
    table = {"layout": "dry", "environments": {"dev-eu": {"region": ""}}}
    assert _refusal(table, matrix_envs=("dev-eu",)) == (
        '::error::layout = "dry" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and dev-eu has an entry with no region."
    )


def test_a_non_dry_layout_needs_no_entry():
    """Mutation: run the coverage check for every layout."""
    table = {"layout": "workspace", "environments": {}}
    assert env_config.validate(table, ("dev-eu",), ()) == table


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
    assert env_config.validate(table, (), ()) == table


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


def test_vars_inside_a_provider_block_refuses():
    """`vars` sits at environment level; a per-tier one fails every apply as stale.

    Mutation: drop the `vars` case from the field check (it then refuses as an unknown
    field, with a message that does not say where `vars` belongs).
    """
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"aws": {"plan": {"vars": {"TF_VAR_x": "y"}}}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: aws.plan.vars is not a field the aws provider "
        "defines. vars sits at environment level: a per-tier one would let plan and "
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
        "An environment holds region, vars, aws."
    )


def test_a_misspelled_environment_key_refuses():
    """The same refusal covers a typo, so the message names every legal key.

    Mutation: as above.
    """
    table = {"layout": "folder", "environments": {"dev-eu": {"regoin": "eu-west-1"}}}
    assert _refusal(table) == (
        "::error::environment dev-eu: regoin is not a key this engine implements. "
        "An environment holds region, vars, aws."
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
    assert env_config.validate(table, (), ()) == table


# --- 7: a shared environment declaring plan -------------------------------------------


@pytest.mark.parametrize("listed", ["dev-eu", "Dev-EU"])
def test_a_shared_environment_declaring_plan_refuses(listed):
    """Mutation: drop the shared check, or ignore `shared_envs` entirely. Mutation:
    compare the listed spelling case-exactly -- `Dev-EU` then validates and the refusal
    the whole check exists for never fires."""
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "aws": {
                    "plan": {"role": "arn:aws:iam::9817:role/p"},
                    "apply": {"role": "arn:aws:iam::9817:role/a"},
                },
            }
        },
    }
    assert _refusal(table, shared_envs=(listed,)) == (
        "::error::environment dev-eu is shared between the plan and apply paths, so "
        "aws.plan cannot apply to it: a shared environment resolves aws.apply on both "
        "paths. Remove aws.plan, or drop dev-eu from SHIPMATE_SHARED_ENVS."
    )


def test_an_unshared_environment_may_declare_plan():
    """Mutation: refuse `plan` for every environment, shared or not."""
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "aws": {"plan": {"role": "arn:aws:iam::9817:role/p"}},
            }
        },
    }
    assert env_config.validate(table, (), ("prod-us",)) == table


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
    assert env_config.validate(table, (), ()) == table


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
    assert env_config.validate(table, ("dev-eu",), ()) == table


def test_an_empty_table_passes():
    """The majority of repositories: no table at all. Every refusal is gated on `layout`.

    Mutation: validate an absent layout as if it were `dry`.
    """
    assert env_config.validate({}, ("dev-eu",), ()) == {}


def test_an_untabled_repository_keeps_its_other_globals():
    """`global.shipmate.env_order` shares this table, and a repository that never
    migrated declares nothing else here.

    Mutation: treat an absent layout as `dry`. A top-level key allowlist would sit behind
    the same gate and so cannot be caught by this fixture;
    `test_a_whole_table_is_returned_unchanged` is the one that reds on it.
    """
    table = {"env_order": {"prod-us": ["dev-eu"]}}
    assert env_config.validate(table, ("dev-eu",), ()) == table


def test_a_whole_table_is_returned_unchanged():
    """Validation returns the table it was given, other globals included.

    Mutation: return only the `environments` mapping.
    """
    table = {
        "layout": "dry",
        "env_order": {"prod-us": ["dev-eu"]},
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "vars": {"TF_VAR_team": "core"},
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
    assert env_config.validate(table, ("dev-eu",), ()) == {
        "layout": "dry",
        "env_order": {"prod-us": ["dev-eu"]},
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "vars": {"TF_VAR_team": "core"},
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


def test_a_null_layout_refuses():
    """Terramate drops an attribute it cannot evaluate but keeps an explicit `layout = null`,
    and the two readers of the key disagree on it: `validate` looked at the value and
    `stamp_rows` at the key. Reddens on treating a null layout as an undeclared one, which
    stamps every row `table` while nothing in this module refuses."""
    assert _refusal({"layout": None}) == (
        '::error::globals "shipmate" layout is None; it must be one of dry, workspace, folder.'
    )
