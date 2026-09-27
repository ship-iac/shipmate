"""`env-config` resolves one cell's identity, credential and region from the table.

Resolution turns a validated table plus a cell's coordinates -- environment, path,
workload -- into the five values the row carries. Three things here are load-bearing and
each has its own test: the block -> path -> workload merge order, the layout derivation,
and that a shared environment resolves `aws.apply` on both paths.

Every assertion compares the whole resolved object against a hand-written literal. A
membership check on one key cannot see an inverted merge order, which is where the
fail-open hides: a plan tier silently keeping the block's write role.
"""

from _loader import load_script

env_config = load_script("env-config")


def _three_tier(path, workload):
    """One table carrying a role at all three tiers, and a region only at the block."""
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {
                "aws": {
                    "region": "eu-west-1",
                    "role": "arn:aws:iam::9817:role/block",
                    "apply": {
                        "role": "arn:aws:iam::9817:role/apply",
                        "workloads": {"net-edge": {"role": "arn:aws:iam::9817:role/net-edge"}},
                    },
                }
            }
        },
    }
    return env_config.resolve(table, "dev-eu", path, workload)


# --- 1: the three-tier merge, one boundary per test -----------------------------------


def test_the_block_tier_resolves_when_no_higher_tier_sets_the_field():
    """The plan path declares nothing, so the block's role and region stand.

    Mutation: drop the block from the merge -- the plan path resolves no role at all.
    """
    assert _three_tier("plan", "") == {
        "role_arn": "arn:aws:iam::9817:role/block",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "plan",
        "env_binding": "dev-eu-plan",
    }


def test_the_path_tier_overrides_the_block():
    """Mutation: swap the merge order so the block wins -- the apply path then resolves
    the block role, and a plan tier could never take a role away from apply either."""
    assert _three_tier("apply", "") == {
        "role_arn": "arn:aws:iam::9817:role/apply",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "apply",
        "env_binding": "dev-eu-apply",
    }


def test_the_workload_tier_overrides_the_path():
    """The workload sets only `role`, so `region` still merges in field by field.

    Mutation: swap the path/workload order, or merge whole levels instead of fields --
    the region is lost with the second.
    """
    assert _three_tier("apply", "net-edge") == {
        "role_arn": "arn:aws:iam::9817:role/net-edge",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "apply",
        "env_binding": "dev-eu-apply",
    }


def test_a_workload_with_no_tier_of_its_own_resolves_the_path_tier():
    """Mutation: refuse or resolve empty when the workload is not in the table -- most
    workloads have no override and must take the path tier's role."""
    assert _three_tier("apply", "app") == {
        "role_arn": "arn:aws:iam::9817:role/apply",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "apply",
        "env_binding": "dev-eu-apply",
    }


# --- 2: derivation by layout ----------------------------------------------------------


def _layout(layout, entry=None):
    entry = {"region": "eu-west-1"} if entry is None else entry
    table = {"layout": layout, "environments": {"dev-eu": entry}}
    return env_config.resolve(table, "dev-eu", "plan", "")


def test_tf_vars_derives_both_identity_variables():
    """Mutation: emit a constant instead of the environment key -- every cell then
    fingerprints identically and applies against the wrong state."""
    assert _layout("tf_vars") == {
        "role_arn": "",
        "cred_region": "",
        "tf_vars": {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"},
        "config_path": "plan",
        "env_binding": "dev-eu-plan",
    }


def test_workspace_derives_the_workspace_name():
    """Mutation: emit `TF_VAR_env` here too -- the workspace flavor injects neither."""
    assert _layout("workspace") == {
        "role_arn": "",
        "cred_region": "",
        "tf_vars": {"TF_WORKSPACE": "dev-eu"},
        "config_path": "plan",
        "env_binding": "dev-eu-plan",
    }


def test_folder_derives_nothing():
    """Mutation: fall through to the tf_vars derivation -- folders inject nothing at plan
    and apply alike, and injecting here would change the fingerprint on one side."""
    assert _layout("folder") == {
        "role_arn": "",
        "cred_region": "",
        "tf_vars": {},
        "config_path": "plan",
        "env_binding": "dev-eu-plan",
    }


# --- 3: the one cross-level default ---------------------------------------------------


def test_the_environment_region_inherits_into_the_block():
    """Mutation: stop inheriting -- the credentials step loses its required region."""
    entry = {"region": "eu-west-1", "aws": {"role": "arn:aws:iam::9817:role/block"}}
    assert _layout("folder", entry) == {
        "role_arn": "arn:aws:iam::9817:role/block",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "plan",
        "env_binding": "dev-eu-plan",
    }


def test_the_provider_region_wins_over_the_environment_region():
    """`cred_region` is the credentials step's region; `TF_VAR_region` under tf_vars is the
    environment's own, and the two are not the same value.

    Mutation: always use the environment-level value -- the credentials step then
    authenticates in the wrong region.
    """
    entry = {
        "region": "eu-west-1",
        "aws": {"region": "us-east-1", "role": "arn:aws:iam::9817:role/block"},
    }
    assert _layout("tf_vars", entry) == {
        "role_arn": "arn:aws:iam::9817:role/block",
        "cred_region": "us-east-1",
        "tf_vars": {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"},
        "config_path": "plan",
        "env_binding": "dev-eu-plan",
    }


# --- 4: the workload tier is keyed by the raw workload --------------------------------

#: Two workloads whose tag values differ only in '-' against '_'. A resolution that
#: normalized the key before the lookup could not tell them apart, and one of them would
#: apply under a role that is not its own.
_COLLIDING = {
    "layout": "folder",
    "environments": {
        "dev-eu": {
            "region": "eu-west-1",
            "aws": {
                "apply": {
                    "role": "arn:aws:iam::9817:role/apply",
                    "workloads": {
                        "net-edge": {"role": "arn:aws:iam::9817:role/hyphen"},
                        "net_edge": {"role": "arn:aws:iam::9817:role/underscore"},
                    },
                }
            },
        }
    },
}


def test_two_workloads_differing_only_in_separator_resolve_separately():
    """Mutation: normalize the key -- `{label}.workloads.{workload.upper().replace('-', '_')}`
    -- and neither entry matches, so both cells silently take the tier's own apply role."""
    assert env_config.resolve(_COLLIDING, "dev-eu", "apply", "net-edge") == {
        "role_arn": "arn:aws:iam::9817:role/hyphen",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "apply",
        "env_binding": "dev-eu-apply",
    }
    assert env_config.resolve(_COLLIDING, "dev-eu", "apply", "net_edge") == {
        "role_arn": "arn:aws:iam::9817:role/underscore",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "apply",
        "env_binding": "dev-eu-apply",
    }


# --- 5 and 7: shared mode, the binding, and the tier the credential came from --------

_SHARED = {
    "layout": "folder",
    "environments": {
        "dev-eu": {
            "region": "eu-west-1",
            "shared": True,
            "aws": {"apply": {"role": "arn:aws:iam::9817:role/apply"}},
        }
    },
}

#: (the entry's `shared` value, or None for absent; the requested path) -> (env_binding,
#: config_path). Hand-written: a shared environment binds the bare name on both paths and
#: resolves `aws.apply` on both; every other entry binds `<env>-<path>` and keeps the path.
_BINDINGS = {
    (None, "plan"): ("dev-eu-plan", "plan"),
    (None, "apply"): ("dev-eu-apply", "apply"),
    (False, "plan"): ("dev-eu-plan", "plan"),
    (False, "apply"): ("dev-eu-apply", "apply"),
    (True, "plan"): ("dev-eu", "apply"),
    (True, "apply"): ("dev-eu", "apply"),
}


def _binding(shared, path):
    entry = {} if shared is None else {"shared": shared}
    resolved = env_config.resolve(
        {"layout": "folder", "environments": {"dev-eu": entry}}, "dev-eu", path, ""
    )
    return resolved["env_binding"], resolved["config_path"]


def test_the_binding_and_tier_follow_the_shared_key():
    """Mutations: return `f"{env}-{path}"` as `env_binding` unconditionally -- both shared rows
    red; resolve `config_path` from `path` for a shared entry -- the shared plan row reds.
    """
    assert {key: _binding(*key) for key in _BINDINGS} == _BINDINGS


def test_a_shared_environment_resolves_apply_on_the_plan_path():
    """One environment on both paths means one role.

    Mutation: resolve `aws.plan` for a shared environment -- the plan cell then resolves an
    empty role and silently skips the credentials step.
    """
    assert env_config.resolve(_SHARED, "dev-eu", "plan", "") == {
        "role_arn": "arn:aws:iam::9817:role/apply",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "apply",
        "env_binding": "dev-eu",
    }


def test_an_unshared_environment_keeps_the_requested_path():
    """The same table without the key: the plan tier declares nothing, so the apply role must
    not reach the plan path.

    Mutation: treat every environment as shared.
    """
    unshared = {
        "layout": "folder",
        "environments": {
            "dev-eu": {k: v for k, v in _SHARED["environments"]["dev-eu"].items() if k != "shared"}
        },
    }
    assert env_config.resolve(unshared, "dev-eu", "plan", "") == {
        "role_arn": "",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "plan",
        "env_binding": "dev-eu-plan",
    }


def test_shared_envs_names_the_entries_holding_shared_true():
    """Mutation: test the key for presence rather than for `true` -- `dev-us` joins the set."""
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {"shared": True},
            "dev-us": {"shared": False},
            "prod": {"region": "eu-west-1"},
        },
    }
    assert env_config.shared_envs(table) == {"dev-eu"}


_FLAGGED = {
    "layout": "folder",
    "environments": {
        "held": {"explicit": True, "gated": True},
        "open": {"explicit": False, "gated": False},
        "plain": {"region": "eu-west-1"},
        "mixed": {"explicit": True, "gated": False},
    },
}


def test_the_flag_accessors_read_each_entry_against_its_default(capsys):
    """`explicit` defaults to false and `gated` to true, so `plain` is in neither set, and a
    table with no entries exempts and holds back nothing. Neither accessor prints.

    Mutations: read `gated` as "absent means ungated" -- `plain` joins the ungated set;
    invert `ungated_envs` to `gated is True` -- `held` replaces `open` and `mixed`; test
    `explicit` for presence rather than `True` -- `open` joins.
    """
    assert env_config.validate_structure(_FLAGGED) is _FLAGGED
    assert env_config.explicit_envs(_FLAGGED) == ["held", "mixed"]
    assert env_config.ungated_envs(_FLAGGED) == frozenset({"open", "mixed"})
    assert env_config.explicit_envs({"layout": "folder"}) == []
    assert env_config.ungated_envs({"layout": "folder"}) == frozenset()
    assert capsys.readouterr().out == ""


def test_the_flag_accessors_read_only_a_boolean_as_set():
    """On a table nothing validated, a quoted value sets neither flag: `"false"` leaves an
    entry gated, the protected direction, and `"true"` leaves it off a bare apply's
    exclusions exactly as `validate_structure` would otherwise have refused it.

    Mutations: test `explicit` for truthiness -- `quoted` joins the explicit list; test
    `gated` for falsiness -- `zero` joins the ungated set.
    """
    table = {
        "environments": {"quoted": {"explicit": "true", "gated": "false"}, "zero": {"gated": 0}}
    }
    assert env_config.explicit_envs(table) == []
    assert env_config.ungated_envs(table) == frozenset()


# --- 6: no fallback to vars.* ---------------------------------------------------------


def test_an_environment_absent_from_the_table_resolves_no_credential(monkeypatch):
    """Once `layout` is set there is no fallback: the caller skips the credentials step.

    `workspace`, not `tf_vars` -- a matrix environment absent from a `tf_vars` table is refused
    by validation instead.

    Mutation: fall back to `AWS_ROLE_ARN`, a repository variable, which is branch-editable
    and is the whole reason the table is read from the default branch.
    """
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::9817:role/loose")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    table = {"layout": "workspace", "environments": {"dev-eu": {"region": "eu-west-1"}}}
    assert env_config.resolve(table, "prod-us", "plan", "") == {
        "role_arn": "",
        "cred_region": "",
        "tf_vars": {"TF_WORKSPACE": "prod-us"},
        "config_path": "plan",
        "env_binding": "prod-us-plan",
    }


# --- 8: the environment's tf_vars merge over the derivation ---------------------------


def _with_vars(variables):
    table = {
        "layout": "workspace",
        "environments": {"dev-eu": {"tf_vars": variables}},
    }
    return env_config.resolve(table, "dev-eu", "plan", "")["tf_vars"]


def test_tf_vars_overrides_the_derived_value():
    """Mutation: ignore `tf_vars`, or merge it under the derivation instead of over it."""
    assert _with_vars({"TF_WORKSPACE": "shared-tenant"}) == {"TF_WORKSPACE": "shared-tenant"}


def test_tf_vars_extends_the_derived_set():
    """Mutation: ignore `tf_vars` -- this reds while the override case stays green under a
    merge-order mutation, which is why the two are written separately."""
    assert _with_vars({"TF_VAR_team": "core"}) == {
        "TF_WORKSPACE": "dev-eu",
        "TF_VAR_team": "core",
    }


def test_tf_vars_may_set_a_value_to_an_explicit_empty_string():
    """`plan-classify` excludes an empty `TF_VAR_*` from the fingerprint on both sides,
    so an explicit empty value stays consistent -- but only if it is emitted.

    Mutation: drop empty values while merging.
    """
    assert _with_vars({"TF_VAR_region": ""}) == {
        "TF_WORKSPACE": "dev-eu",
        "TF_VAR_region": "",
    }


# --- 9: the two paths inject the same variables ---------------------------------------


def test_plan_and_apply_resolve_identical_tf_vars():
    """`plan-classify` hashes the process environment, so a variable that differs
    between the two paths fails every apply as "saved plan is stale".

    Mutation: derive `TF_VAR_env` on the apply path from a second source -- the entry's
    region, say, instead of the environment key.
    """
    table = {
        "layout": "tf_vars",
        "environments": {
            "dev-eu": {
                "region": "eu-west-1",
                "aws": {
                    "plan": {"role": "arn:aws:iam::9817:role/plan"},
                    "apply": {"role": "arn:aws:iam::9817:role/apply"},
                },
            }
        },
    }
    expected = {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}
    assert env_config.resolve(table, "dev-eu", "plan", "")["tf_vars"] == expected
    assert env_config.resolve(table, "dev-eu", "apply", "")["tf_vars"] == expected
