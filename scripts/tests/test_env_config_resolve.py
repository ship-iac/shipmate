"""`env-config` resolves one cell's identity, credential and region from the table.

Resolution turns a validated table plus a cell's coordinates -- environment, path,
workload -- into the five values the row carries. Three things here are load-bearing and
each has its own test: the role rule over an identity, the layout derivation, and that a
shared environment resolves `aws.apply` on both paths.

Every assertion compares the whole resolved object against a hand-written literal. A
membership check on one key cannot see a role taken from the wrong path or workload, which
is where the fail-open hides: a plan cell silently holding the write role.
"""

import pytest
from _loader import load_script

env_config = load_script("env-config")

_ACCOUNT = "111111111111"

#: One identity per role shape, and one environment naming each.
_ROLES = {
    "layout": "folder",
    "identities": {
        "named": {"aws": {"account": _ACCOUNT, "plan": "shipmate-plan", "apply": "shipmate-apply"}},
        "templ": {"aws": {"account": _ACCOUNT, "apply": "shipmate-apply-{workload}"}},
        "mapped": {
            "aws": {"account": {"core": _ACCOUNT, "net": "222222222222"}, "apply": "shipmate-apply"}
        },
        "full": {"aws": {"apply": "arn:aws:iam::333333333333:role/full"}},
        "pathed": {"aws": {"account": _ACCOUNT, "plan": "ci/shipmate-plan"}},
        "shared": {"aws": {"apply": "arn:aws:iam::444444444444:role/shared"}},
    },
    "environments": {
        "bare": {"region": "eu-west-1"},
        "named": {"region": "eu-west-1", "identity": "named"},
        "listed": {"region": "eu-west-1", "identity": "named", "workloads": ["net", "core"]},
        "templ": {"region": "eu-west-1", "identity": "templ", "workloads": ["core"]},
        "mapped": {"region": "eu-west-1", "identity": "mapped", "workloads": ["core", "net"]},
        "full": {"region": "eu-west-1", "identity": "full"},
        "pathed": {"region": "eu-west-1", "identity": "pathed"},
        "sbx": {"region": "eu-west-1", "identity": "shared", "shared": True},
    },
}


def _cell(role_arn, cred_region, config_path, env_binding):
    return {
        "role_arn": role_arn,
        "cred_region": cred_region,
        "tf_vars": {},
        "config_path": config_path,
        "env_binding": env_binding,
    }


_RESOLVED = [
    ("prod-us", "apply", "", _cell("", "", "apply", "prod-us-apply")),
    ("bare", "apply", "net", _cell("", "", "apply", "bare-apply")),
    (
        "named",
        "apply",
        "",
        _cell("arn:aws:iam::111111111111:role/shipmate-apply", "eu-west-1", "apply", "named-apply"),
    ),
    (
        "named",
        "plan",
        "net",
        _cell("arn:aws:iam::111111111111:role/shipmate-plan", "eu-west-1", "plan", "named-plan"),
    ),
    (
        "listed",
        "apply",
        "core",
        _cell(
            "arn:aws:iam::111111111111:role/shipmate-apply", "eu-west-1", "apply", "listed-apply"
        ),
    ),
    ("listed", "apply", "app", _cell("", "eu-west-1", "apply", "listed-apply")),
    (
        "templ",
        "apply",
        "core",
        _cell(
            "arn:aws:iam::111111111111:role/shipmate-apply-core",
            "eu-west-1",
            "apply",
            "templ-apply",
        ),
    ),
    ("templ", "apply", "app", _cell("", "eu-west-1", "apply", "templ-apply")),
    ("templ", "apply", "", _cell("", "eu-west-1", "apply", "templ-apply")),
    (
        "mapped",
        "apply",
        "net",
        _cell(
            "arn:aws:iam::222222222222:role/shipmate-apply", "eu-west-1", "apply", "mapped-apply"
        ),
    ),
    (
        "full",
        "apply",
        "app",
        _cell("arn:aws:iam::333333333333:role/full", "eu-west-1", "apply", "full-apply"),
    ),
    (
        "pathed",
        "plan",
        "",
        _cell(
            "arn:aws:iam::111111111111:role/ci/shipmate-plan", "eu-west-1", "plan", "pathed-plan"
        ),
    ),
    (
        "sbx",
        "plan",
        "",
        _cell("arn:aws:iam::444444444444:role/shared", "eu-west-1", "apply", "sbx"),
    ),
    ("full", "plan", "", _cell("", "eu-west-1", "plan", "full-plan")),
]

_IDS = [
    "no-entry",
    "no-identity",
    "name-untagged",
    "name-inert-tag",
    "listed-tag",
    "unlisted-tag",
    "placeholder-listed",
    "placeholder-unlisted",
    "placeholder-untagged",
    "account-map",
    "full-arn",
    "iam-path",
    "shared-plan",
    "no-plan-role",
]


@pytest.mark.parametrize(("env", "path", "workload", "expected"), _RESOLVED, ids=_IDS)
def test_each_role_shape_resolves_one_cell(env, path, workload, expected):
    """Mutations: fill `{workload}` for an unlisted tag -- `placeholder-unlisted` reds; take
    the account from the wrong workload -- `account-map` reds; drop the shared switch --
    `shared-plan` reds; treat an unlisted tag as inert under a written list -- `unlisted-tag`
    reds.
    """
    table = env_config.validate_structure(_ROLES)
    assert env_config.resolve(table, env, path, workload) == expected


@pytest.mark.parametrize(
    ("env", "workload"),
    [("listed", ""), ("bare", "net"), ("named", "net"), ("listed", "core")],
    ids=["untagged", "no-identity", "no-list", "listed"],
)
def test_a_tag_is_outside_nothing_unless_a_written_list_omits_it(env, workload):
    """Mutations: drop the empty-tag test -- `untagged` returns the list; drop the membership
    test -- `listed` returns the list."""
    assert env_config.outside_workloads(_ROLES, env, workload) is None


def test_a_tag_off_a_written_list_returns_the_list_sorted():
    """`listed`'s identity does not vary, and its list still decides.

    Mutation: return None whenever the identity does not vary -- this returns None.
    """
    assert env_config.outside_workloads(_ROLES, "listed", "app") == ["core", "net"]


# --- the role map is keyed by the raw workload ------------------------------------------

#: Two workloads whose tag values differ only in '-' against '_'. A resolution that
#: normalized the key before the lookup could not tell them apart, and one of them would
#: apply under a role that is not its own.
_COLLIDING = {
    "layout": "folder",
    "identities": {
        "dev": {
            "aws": {
                "apply": {
                    "net-edge": "arn:aws:iam::9817:role/hyphen",
                    "net_edge": "arn:aws:iam::9817:role/underscore",
                }
            }
        }
    },
    "environments": {
        "dev-eu": {"region": "eu-west-1", "identity": "dev", "workloads": ["net-edge", "net_edge"]}
    },
}


def test_two_workloads_differing_only_in_separator_resolve_separately():
    """Mutation: normalize the key -- `workload.replace('-', '_')` -- and both cells take the
    underscore role."""
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


# --- derivation by layout -------------------------------------------------------------


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


# --- shared mode, the binding, and the path the credential came from ------------------

_SHARED = {
    "layout": "folder",
    "identities": {"dev": {"aws": {"apply": "arn:aws:iam::9817:role/apply"}}},
    "environments": {"dev-eu": {"region": "eu-west-1", "shared": True, "identity": "dev"}},
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
    """The same table without the key: the identity sets no `aws.plan`, so the apply role must
    not reach the plan path.

    Mutation: treat every environment as shared.
    """
    unshared = {
        **_SHARED,
        "environments": {"dev-eu": {"region": "eu-west-1", "identity": "dev"}},
    }
    assert env_config.resolve(unshared, "dev-eu", "plan", "") == {
        "role_arn": "",
        "cred_region": "eu-west-1",
        "tf_vars": {},
        "config_path": "plan",
        "env_binding": "dev-eu-plan",
    }


def test_shared_envs_names_the_entries_holding_shared_true(monkeypatch):
    """The retired `SHIPMATE_SHARED_ENVS` is set both bare and in `SHIPMATE_GITHUB_VARS`, so a
    fallback beside the table changes the set. Mutations: test the key for presence rather
    than for `true` -- `dev-us` joins the set; union `SHIPMATE_SHARED_ENVS` from the parsed
    `SHIPMATE_GITHUB_VARS`, or from the process environment, into `shared_envs`' result --
    `dev-us` joins."""
    monkeypatch.setenv("SHIPMATE_GITHUB_VARS", '{"SHIPMATE_SHARED_ENVS": "dev-us"}')
    monkeypatch.setenv("SHIPMATE_SHARED_ENVS", "dev-us")
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


# --- no fallback to vars.* ------------------------------------------------------------


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


# --- the environment's tf_vars merge over the derivation ------------------------------


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


# --- the two paths inject the same variables ------------------------------------------


def test_plan_and_apply_resolve_identical_tf_vars():
    """`plan-classify` hashes the process environment, so a variable that differs
    between the two paths fails every apply as "saved plan is stale".

    Mutation: derive `TF_VAR_env` on the apply path from a second source -- the entry's
    region, say, instead of the environment key.
    """
    table = {
        "layout": "tf_vars",
        "identities": {
            "dev": {
                "aws": {
                    "plan": "arn:aws:iam::9817:role/plan",
                    "apply": "arn:aws:iam::9817:role/apply",
                }
            }
        },
        "environments": {"dev-eu": {"region": "eu-west-1", "identity": "dev"}},
    }
    expected = {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}
    assert env_config.resolve(table, "dev-eu", "plan", "")["tf_vars"] == expected
    assert env_config.resolve(table, "dev-eu", "apply", "")["tf_vars"] == expected


# --- validation and resolution read one role ------------------------------------------

#: One identity per role shape, each named by one environment.
_SHAPES = {
    "layout": "folder",
    "identities": {
        "named": {"aws": {"account": "111111111111", "plan": "ci-plan", "apply": "ci-apply"}},
        "keyed": {
            "aws": {
                "account": {"core": "222222222222", "net": "333333333333"},
                "plan": "{workload}-plan",
                "apply": {"core": "core-apply", "net": "net-apply"},
            }
        },
        "full": {
            "aws": {
                "plan": "arn:aws:iam::444444444444:role/full-plan",
                "apply": "arn:aws:iam::444444444444:role/full-apply",
            }
        },
        "shared": {"aws": {"apply": "arn:aws:iam::555555555555:role/shared"}},
    },
    "environments": {
        "named": {"region": "eu-west-1", "identity": "named"},
        "keyed": {"region": "eu-west-1", "identity": "keyed", "workloads": ["net", "core"]},
        "full": {"region": "eu-west-1", "identity": "full"},
        "sbx": {"region": "eu-west-1", "identity": "shared", "shared": True},
    },
}

#: (env, path, workload) -> (the role validation checks, the ARN the cell assumes),
#: hand-written. `None` is validation's every-cell workload, resolved here as untagged.
_SHAPE_ROLES = {
    ("named", "plan", None): ("ci-plan", "arn:aws:iam::111111111111:role/ci-plan"),
    ("named", "apply", None): ("ci-apply", "arn:aws:iam::111111111111:role/ci-apply"),
    ("keyed", "plan", "net"): ("net-plan", "arn:aws:iam::333333333333:role/net-plan"),
    ("keyed", "apply", "net"): ("net-apply", "arn:aws:iam::333333333333:role/net-apply"),
    ("keyed", "plan", "core"): ("core-plan", "arn:aws:iam::222222222222:role/core-plan"),
    ("keyed", "apply", "core"): ("core-apply", "arn:aws:iam::222222222222:role/core-apply"),
    ("full", "plan", None): (
        "arn:aws:iam::444444444444:role/full-plan",
        "arn:aws:iam::444444444444:role/full-plan",
    ),
    ("full", "apply", None): (
        "arn:aws:iam::444444444444:role/full-apply",
        "arn:aws:iam::444444444444:role/full-apply",
    ),
    ("sbx", "plan", None): (
        "arn:aws:iam::555555555555:role/shared",
        "arn:aws:iam::555555555555:role/shared",
    ),
    ("sbx", "apply", None): (
        "arn:aws:iam::555555555555:role/shared",
        "arn:aws:iam::555555555555:role/shared",
    ),
}


@pytest.mark.parametrize(("cell", "roles"), _SHAPE_ROLES.items(), ids=str)
def test_validation_and_resolution_read_the_same_role(cell, roles):
    """The role rule is written twice: `_listed_roles` is what validation judges, `resolve`
    what a cell assumes. Both read one validated table here, so a change to either side's
    reading of a path, a workload map or `{workload}` reds.

    Mutations: stop `_listed_roles` filling `{workload}`; have `_pick` read the map's first
    entry instead of the workload's; have `_role` drop the account and return the bare name.
    """
    env, path, workload = cell
    table = env_config.validate_structure(_SHAPES)
    entry = table["environments"][env]
    name = entry["identity"]
    consulted = "apply" if entry.get("shared") else path
    aws = table["identities"][name]["aws"]
    listed = env_config._listed_roles(env, name, aws, (consulted,), workload)
    resolved = env_config.resolve(table, env, path, workload or "")["role_arn"]
    assert (listed, resolved) == ({consulted: roles[0]}, roles[1])
