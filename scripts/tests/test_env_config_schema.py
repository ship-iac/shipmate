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


def _refusal(table, matrix_envs=(), shared_envs=()):
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate(table, matrix_envs, shared_envs)
    return str(excinfo.value)


# --- 1: layout ------------------------------------------------------------------------


def test_an_unknown_layout_refuses():
    """Mutation: drop the layout membership check -- a typo disables injection silently."""
    assert _refusal({"layout": "drys"}) == (
        "::error::layout is 'drys'; it must be one of dry, workspace, folder."
    )


def test_a_non_string_layout_refuses():
    """Mutation: as above; a mapping is not one of the three names either."""
    assert _refusal({"layout": {"dry": True}}) == (
        "::error::layout is {'dry': True}; it must be one of dry, workspace, folder."
    )


NO_LAYOUT = (
    "::error::.github/shipmate.toml declares no layout, so no cell can resolve its "
    'environment identity. Declare layout = "dry", "workspace" or "folder" on the default '
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
    """`env_order` is a legal top-level key, so a file carrying only that parses cleanly and
    reaches validation. The layout refusal is the only thing that can stop it.

    Mutation: as above.
    """
    table = {"env_order": {"prod-us": ["dev-eu"]}}
    assert _refusal(table) == NO_LAYOUT


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


def test_an_empty_layout_refuses():
    """TOML has no null, so the unreachable `layout = null` case is retired. An empty string
    is the reachable neighbour: a declared layout holding a value no layout may hold.

    Mutation: `if not table.get("layout"):` for the no-layout refusal, which reads an empty
    layout as undeclared and answers a repository that did declare one with the message
    telling it to declare one.
    """
    assert _refusal({"layout": ""}) == (
        "::error::layout is ''; it must be one of dry, workspace, folder."
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
        ".github/shipmate.toml holds layout, environments, env_order, explicit_envs, "
        "gate, version."
    )


def test_every_allowed_top_level_key_is_accepted():
    """The other half of the strict-key rule: the six names are the whole allowed set, so a
    table using all six must validate. Compared against a hand-written table, never against
    the module's own constant.

    This is also where the `explicit_envs` half of "nothing cross-checks a control against
    `environments`" is pinned: `explicit_envs` names `prod` and `environments` is empty, a
    shape the canonical-file guard cannot catch because it declares every environment it
    excludes.

    Mutations: remove a name from the allowed set -- one of these six then refuses; or make
    `validate_structure` require an `environments` entry for every `explicit_envs` name.
    """
    table = {
        "layout": "folder",
        "environments": {},
        "env_order": {"prod": ["dev-eu"]},
        "explicit_envs": ["prod"],
        "gate": {"approvers_team": "deployers", "ungated_envs": ["dev-eu"]},
        "version": 1,
    }
    assert env_config.validate(table, (), ()) == table


_ORDERING = [
    (
        {"layout": "folder", "env_order": "dev-eu"},
        "::error::env_order must be a mapping of env -> [predecessor envs], got str ('dev-eu')",
    ),
    (
        {"layout": "folder", "env_order": {"prod": "dev-eu"}},
        "::error::env_order['prod'] must be a list of predecessor envs, got str "
        "('dev-eu'); did you mean ['dev-eu']?",
    ),
    (
        {"layout": "folder", "env_order": {"prod": ["dev-eu", 123]}},
        "::error::env_order['prod'] predecessors must all be env-name strings; "
        "got non-string element(s) [123]",
    ),
    (
        {"layout": "folder", "explicit_envs": "prod"},
        "::error::explicit_envs must be a list of env-name strings, got str ('prod'); "
        "did you mean ['prod']?",
    ),
    (
        {"layout": "folder", "explicit_envs": ["prod", 123]},
        "::error::explicit_envs elements must all be env-name strings; "
        "got non-string element(s) [123]",
    ),
    (
        {"layout": "folder", "explicit_envs": ["prod-apply"]},
        "::error::explicit_envs entry 'prod-apply' carries the environment suffix "
        "'-apply'; explicit_envs is matched against the bare logical env name — write "
        "'prod' instead.",
    ),
    (
        {"layout": "folder", "explicit_envs": ["prod-plan"]},
        "::error::explicit_envs entry 'prod-plan' carries the environment suffix "
        "'-plan'; explicit_envs is matched against the bare logical env name — write "
        "'prod' instead.",
    ),
]


@pytest.mark.parametrize(("table", "message"), _ORDERING, ids=range(len(_ORDERING)))
def test_the_single_entry_point_validates_ordering_and_exclusions(table, message):
    """`env_order` and `explicit_envs` are checked by the same entry point as `layout` and
    `environments`, so a "valid configuration" verdict cannot leave an ordering or exclusion
    error to surface when an apply finally reads the field.

    Both environment suffixes are cases here, not one: the refusal exists because every
    documented environment name carries `-plan` or `-apply`, and a suffixed entry matches no
    apply check, excludes nothing and lets prod apply on a bare `shipmate apply`.

    Mutations: delete the `validate_env_order` and `validate_explicit_envs` calls from
    `validate_structure` -- every case here validates instead of refusing; or drop either
    entry from the suffix tuple in `validate_explicit_envs` -- that suffix's case validates.
    """
    assert _refusal(table) == message


def test_a_tier_word_that_is_not_the_trailing_suffix_is_accepted():
    """The other half of the suffix rule: only a trailing `-plan`/`-apply` is the tier
    suffix. A refusal here would strand every exclusion a repository declared under a name
    that merely carries one of the two words.

    `eu-plan-1` is the case that discriminates, and it is the only one: the suffixes are
    matched with their hyphen, so `plan-eu` and `apply-svc` survive a containment test too
    and pin the leading-word half rather than this one.

    Mutation: `e.endswith(suffix)` -> `suffix in e` in `validate_explicit_envs` -- `eu-plan-1`
    then refuses.
    """
    table = {"layout": "folder", "explicit_envs": ["prod", "plan-eu", "apply-svc", "eu-plan-1"]}
    assert env_config.validate(table, (), ()) == table


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
def test_a_cyclic_env_order_refuses_structurally(order, cycle):
    """Acyclicity is decidable from the file alone, so it belongs with the structural checks:
    without it `validate_structure` passes a file that `env_levels` later refuses with a raw
    `CycleError`, and `shipmate doctor` certifies it as sound in the meantime.

    Three cases, because each pins something the others cannot: two nodes for the ordinary
    cycle, a self-edge (an env listed as its own predecessor is a cycle too), and three nodes
    for the direction the path is rendered in -- `graphlib` reports each node before its
    successor, so reversing the join silently mislabels every cycle longer than two.

    Mutation: delete the `TopologicalSorter` block from `validate_env_order` -- all three
    tables validate.
    """
    table = {"layout": "folder", "env_order": order}
    with pytest.raises(SystemExit) as excinfo:
        env_config.validate_structure(table)
    assert str(excinfo.value) == f"::error::env_order is cyclic: {cycle}{_CYCLE_TAIL}"


def test_a_deep_acyclic_env_order_still_validates():
    """The other half of the cycle rule: a legitimate chain must keep validating. A check that
    refuses a valid ordering is worse than the defect it fixes, and three levels is what the
    engine's own `MAX_ENV_LEVELS` cap allows.

    Mutation: refuse any env that is both a key and a predecessor rather than a cycle --
    `stage` is both, and this legitimate chain then refuses.
    """
    table = {
        "layout": "folder",
        "env_order": {"stage": ["dev"], "prod": ["stage"], "prod-us": ["prod", "stage"]},
    }
    assert env_config.validate_structure(table) is table


# --- 11: version and the gate table -------------------------------------------------------


def test_a_declared_version_1_is_accepted():
    """Mutation: remove `"version"` from `_TOP_KEYS` -- the strict top-level loop then
    refuses a file declaring the version this engine implements."""
    table = {"layout": "folder", "version": 1}
    assert env_config.validate(table, (), ()) == table


def test_an_absent_version_is_accepted():
    """`version` is optional: every file written before the key existed omits it, and the
    engine must keep reading those.

    Mutation: make the version check unconditional -- `table["version"]` then raises on
    every file that omits it.
    """
    table = {"layout": "folder"}
    assert env_config.validate(table, (), ()) == table


def test_a_future_version_refuses_naming_the_one_implemented():
    """Mutation: compare against 2 rather than 1 -- a file this engine cannot read
    validates."""
    assert _refusal({"layout": "folder", "version": 2}) == (
        "::error::version is 2; this engine implements version 1 of .github/shipmate.toml."
    )


def test_a_boolean_version_refuses():
    """`True == 1` in Python, so a bare equality accepts `version = true` as version 1 and
    the file passes with a value no schema version can be.

    Mutation: drop the `isinstance(v, bool)` exclusion from `_check_version`.
    """
    assert _refusal({"layout": "folder", "version": True}) == (
        "::error::version is True; this engine implements version 1 of .github/shipmate.toml."
    )


def test_a_gate_table_holding_both_keys_is_accepted():
    """Mutation: remove `"gate"` from `_TOP_KEYS` -- the table a repository declares ahead of
    the release that reads it is refused as an unknown setting."""
    table = {
        "layout": "folder",
        "gate": {"approvers_team": "deployers", "ungated_envs": ["dev-eu", "dev_us"]},
    }
    assert env_config.validate(table, (), ()) == table


def test_an_absent_gate_table_is_accepted():
    """Mutation: `table.get("gate", {})` -> `table["gate"]` -- every file that declares no
    gate then raises instead of validating."""
    table = {"layout": "folder", "environments": {}}
    assert env_config.validate(table, (), ()) == table


def test_an_unknown_key_in_the_gate_table_refuses_by_name():
    """A misspelled gate key is silently inert: the setting keeps its default and the
    repository believes it declared one.

    Mutation: widen `_GATE_KEYS` to accept any key.
    """
    assert _refusal({"layout": "folder", "gate": {"approver_team": "deployers"}}) == (
        "::error::gate.approver_team is not a key this engine implements. "
        "The gate table holds approvers_team, ungated_envs."
    )


def test_a_non_string_approvers_team_refuses():
    """Mutation: drop the `_string` call inside `validate_team_slug` -- a non-string team slug
    validates here and fails once per membership query instead."""
    assert _refusal({"layout": "folder", "gate": {"approvers_team": 1}}) == (
        "::error::gate.approvers_team must be a string, got int."
    )


_NOT_A_TEAM_SLUG = (
    "is {team!r}, which is not a GitHub team slug; use the bare slug from the team's URL "
    "(letters, digits, '-' and '_'), not a display name or an @org/team reference."
)


@pytest.mark.parametrize("team", ["Platform Team", "@ship-iac/platform", "platform ", '"platform"'])
def test_an_approvers_team_that_is_not_a_slug_refuses(team):
    """The charset rule `gate.ungated_envs` already has, on the setting beside it. A display
    name, an `@org/team` reference or a padded slug 404s in the membership lookup, so every
    commenter is refused under a message naming the team as though it had resolved -- the
    same failure a missing team produces, with no diagnostic distinguishing them.

    Mutation: drop the `_TEAM_SLUG.fullmatch` check, or widen the pattern to `.*`.
    """
    assert _refusal({"layout": "folder", "gate": {"approvers_team": team}}) == (
        f"::error::gate.approvers_team {_NOT_A_TEAM_SLUG.format(team=team)}"
    )


def test_a_declared_empty_approvers_team_still_validates():
    """An empty team is legal and authorizes nobody, so the charset rule must not refuse it.

    Mutation: drop the `if team and` guard -- a repository deliberately closing the comment
    path is then refused at every read of its file, including `doctor`'s.
    """
    table = {"layout": "folder", "gate": {"approvers_team": ""}}
    assert env_config.validate_structure(table) == table


_NOT_AN_ENV_NAME = (
    "entry {entry!r} is not an environment name; entries are bare logical env names "
    "(letters, digits, '-' and '_'), with no quotes, spaces or path separators."
)


def test_an_ungated_env_with_an_internal_space_refuses():
    """An entry no environment name can match exempts nothing, and leaves an operator
    believing an environment is ungated when it is not.

    Mutation: drop the `_ENV_ENTRY` full match from `validate_env_name_list`.
    """
    assert _refusal({"layout": "folder", "gate": {"ungated_envs": ["dev eu"]}}) == (
        "::error::gate.ungated_envs " + _NOT_AN_ENV_NAME.format(entry="dev eu")
    )


def test_an_ungated_env_carrying_a_pasted_quote_refuses():
    """TOML quotes the string itself, so a pasted `"dev-eu"` reaches the engine with its
    quotes still on and matches no environment.

    Mutation: as above -- the charset is what rejects the quote characters.
    """
    assert _refusal({"layout": "folder", "gate": {"ungated_envs": ['"dev-eu"']}}) == (
        "::error::gate.ungated_envs " + _NOT_AN_ENV_NAME.format(entry='"dev-eu"')
    )


def test_an_ungated_env_carrying_an_environment_suffix_refuses():
    """The list is matched against the bare logical env name, so a suffixed entry exempts
    nothing. It passes the charset, which is why the suffix is its own check.

    Mutation: drop the `-plan`/`-apply` loop from `validate_env_name_list`.
    """
    assert _refusal({"layout": "folder", "gate": {"ungated_envs": ["dev-eu-apply"]}}) == (
        "::error::gate.ungated_envs entry 'dev-eu-apply' carries the environment suffix "
        "'-apply'; gate.ungated_envs is matched against the bare logical env name — write "
        "'dev-eu' instead."
    )


def test_a_bare_string_ungated_envs_refuses():
    """A bare string is iterated character by character, so `"dev-eu"` would exempt six
    one-character environments and the real one not at all.

    Mutation: drop the `isinstance(envs, list)` check from `validate_env_name_list`.
    """
    assert _refusal({"layout": "folder", "gate": {"ungated_envs": "dev-eu"}}) == (
        "::error::gate.ungated_envs must be a list of env-name strings, got str "
        "('dev-eu'); did you mean ['dev-eu']?"
    )


def test_an_explicit_env_with_an_internal_space_now_refuses():
    """`explicit_envs` and `gate.ungated_envs` share one env-name rule, and it is the
    stricter of the two that existed: an entry with an internal space excludes nothing,
    and the weaker rule accepted it.

    Mutation: point `validate_explicit_envs` back at its own list/suffix body, with no
    charset check.
    """
    assert _refusal({"layout": "folder", "explicit_envs": ["dev eu"]}) == (
        "::error::explicit_envs " + _NOT_AN_ENV_NAME.format(entry="dev eu")
    )
