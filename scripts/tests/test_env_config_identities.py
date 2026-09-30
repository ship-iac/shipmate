"""`env-config` refuses a malformed identity, or an environment naming one wrongly.

Credentials live in `[identities.<name>]`; an environment names one with `identity` and admits
workloads with `workloads`. Every refusal here is compared whole against a hand-written literal
over a minimal table, never by substring and never against a constant read from the script: an
operator reading a refusal in a run log has no other source.
"""

import pytest
from _loader import load_script

ec = load_script("env-config")

_ARN = "arn:aws:iam::111111111111:role/apply"
_ACCOUNT = "111111111111"


def _refusal(table):
    with pytest.raises(SystemExit) as exc:
        ec.validate_structure(table)
    return str(exc.value)


def _identity(aws, **environments):
    """A folder-layout table holding one identity `dev` and the given entries."""
    return {"layout": "folder", "identities": {"dev": {"aws": aws}}, "environments": environments}


def _named(aws, **entry):
    """`_identity` with one entry `dev-eu` naming `dev`, in eu-west-1."""
    return _identity(aws, **{"dev-eu": {"region": "eu-west-1", "identity": "dev", **entry}})


# --- the environment's retired aws block ---------------------------------------------------


def test_an_environment_aws_block_names_its_replacement():
    """Mutation: drop the `aws` case from the entry key loop -- the generic unknown-key text
    appears instead."""
    table = {
        "layout": "folder",
        "environments": {"dev-eu": {"region": "eu-west-1", "aws": {"apply": {"role": _ARN}}}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: aws is retired. Credentials live in [identities.<name>] "
        "(aws.account, aws.plan, aws.apply), and the environment names one with "
        'identity = "<name>".'
    )


#: `repo-example-stacks-aws/.github/shipmate.toml` as shipped for v0.41.0, verbatim.
_STACKS_AWS_V0_41_0 = """\
schema_version = 1

# Dynamic backend: the layout derives `TF_VAR_env` from each environment's own name
# and `TF_VAR_region` from its `region`. Under `tf_vars` every environment in the matrix
# needs an entry with a non-empty region, or the run refuses at detect.
layout = "tf_vars"

# Who may drive shipmate by pull-request comment. Every repository declares its own
# team: the engine reads this file and no repository variable.
[gate]
approver_team = "deployers"

# The environment's own `region` inherits into the provider block, so the credentials
# step gets the same region the stack does. `plan` and `apply` are separate roles: the
# plan role is read-only and reachable from any branch, the apply role is not. Do not
# collapse them into one block-level `aws.role` -- that hands any-branch plan cells the
# apply role's permissions.
# Each role is a repository variable, resolved at detect, so a rename is a variable edit
# rather than a pull request. The ARNs stay public in the credentials step's log.
[environments.dev-eu]
region = "eu-west-1"
aws.plan.role = { vars = "DEV_PLAN_ROLE" }
aws.apply.role = { vars = "DEV_APPLY_ROLE" }

# dev-eu fully applies first. dev-us is a dev environment, so a targeted apply there
# needs no approving review; dev-eu's still does.
[environments.dev-us]
region = "us-east-1"
needs = ["dev-eu"]
gated = false
aws.plan.role = { vars = "DEV_PLAN_ROLE" }
aws.apply.role = { vars = "DEV_APPLY_ROLE" }

[environments.sbx]
region = "eu-west-1"
aws.plan.role = { vars = "SBX_PLAN_ROLE" }
aws.apply.role = { vars = "SBX_APPLY_ROLE" }
"""

_STACKS_AWS_VARIABLES = {
    "DEV_PLAN_ROLE": "arn:aws:iam::222222222222:role/shipmate-dev-plan",
    "DEV_APPLY_ROLE": "arn:aws:iam::222222222222:role/shipmate-dev-apply",
    "SBX_PLAN_ROLE": "arn:aws:iam::333333333333:role/shipmate-sbx-plan",
    "SBX_APPLY_ROLE": "arn:aws:iam::333333333333:role/shipmate-sbx-apply",
}


def test_the_v0_41_0_sample_table_refuses_once_per_environment():
    """The last released sample table, fed through the real parser, refuses all at once, and
    each line names `identity` as the replacement.

    Mutation: drop the `aws` case from the entry key loop -- each line becomes the generic
    unknown-key text.
    """
    table = ec.parse_table(_STACKS_AWS_V0_41_0, _STACKS_AWS_VARIABLES)
    retired = (
        "aws is retired. Credentials live in [identities.<name>] (aws.account, aws.plan, "
        'aws.apply), and the environment names one with identity = "<name>".'
    )
    assert _refusal(table) == (
        f"::error::environment dev-eu: {retired}\n"
        f"::error::environment dev-us: {retired}\n"
        f"::error::environment sbx: {retired}"
    )


# --- an identity's own shape: M2 to M14 ------------------------------------------------------

_NOT_MAPPINGS = [
    ("dev", "::error::identities must be a mapping, got str."),
    ({"dev": "x"}, "::error::identities.dev must be a mapping, got str."),
    ({"dev": {"aws": "x"}}, "::error::identities.dev.aws must be a mapping, got str."),
]


@pytest.mark.parametrize(("identities", "message"), _NOT_MAPPINGS, ids=range(len(_NOT_MAPPINGS)))
def test_a_non_mapping_identity_level_is_one_message(identities, message):
    """The entry naming the identity gets no further message: a refused identity skips the
    role checks, and a non-mapping `identities` skips every entry's identity checks.

    Mutation: drop the `_mapping` guard on `identities` -- case 0 raises a raw `TypeError`
    instead of one `SystemExit`.
    """
    table = {
        "layout": "folder",
        "identities": identities,
        "environments": {"dev-eu": {"region": "eu-west-1", "identity": "dev"}},
    }
    assert _refusal(table) == message


def test_an_identity_key_other_than_aws_refuses():
    """Mutation: drop the identity key loop -- the table validates."""
    table = {
        "layout": "folder",
        "identities": {"dev": {"aws": {"apply": _ARN}, "region": "eu-west-1"}},
    }
    assert _refusal(table) == (
        "::error::identities.dev.region is not a key this engine implements. An identity holds "
        "aws.account, aws.plan and aws.apply."
    )


_RETIRED_FIELDS = [
    (
        "role",
        "::error::identities.dev.aws.role is retired: one role on both paths hands any-branch "
        "plan cells the apply role's permissions. Write aws.plan and aws.apply.",
    ),
    (
        "region",
        "::error::identities.dev.aws.region is retired: the credentials step uses the "
        "environment's region.",
    ),
    (
        "client_id",
        "::error::identities.dev.aws.client_id is not a field this engine implements. An "
        "identity holds aws.account, aws.plan and aws.apply.",
    ),
]


@pytest.mark.parametrize(("key", "message"), _RETIRED_FIELDS, ids=[k for k, _ in _RETIRED_FIELDS])
def test_an_aws_field_the_identity_does_not_hold_refuses(key, message):
    """Mutations: drop the `role` case -- its line becomes the generic one; drop the `region`
    case -- likewise; drop the unknown-field refusal -- the `client_id` case validates."""
    assert _refusal(_identity({"apply": _ARN, key: "x"})) == message


@pytest.mark.parametrize("aws", [{"account": _ACCOUNT}, {}], ids=["account-only", "empty"])
def test_an_identity_granting_no_credential_refuses(aws):
    """Mutation: drop the neither-plan-nor-apply check -- both cases validate."""
    assert _refusal(_identity(aws)) == (
        "::error::identities.dev sets neither aws.plan nor aws.apply, so it grants no "
        "credential. Set one, or remove the identity."
    )


@pytest.mark.parametrize(
    ("field", "value", "found"),
    [("plan", 3, "int"), ("apply", ["a"], "list"), ("account", 111111111111, "int")],
)
def test_a_field_that_is_neither_string_nor_map_refuses(field, value, found):
    """An integer account is refused here, by type, before its digits are read.

    Mutation: drop the type refusal in the field check -- every case validates or raises raw.
    """
    assert _refusal(_identity({"apply": _ARN, field: value})) == (
        f"::error::identities.dev.aws.{field} must be a string or a map keyed by workload, "
        f"got {found}."
    )


def test_a_map_value_that_is_not_a_string_refuses():
    """Mutation: drop the map-value type check -- the integer reaches the empty check and
    raises raw."""
    assert _refusal(_identity({"apply": {"core": 3}})) == (
        "::error::identities.dev.aws.apply.core must be a string, got int."
    )


def test_the_retired_workload_tier_names_the_map_spelling():
    """`aws.apply.workloads.net.role = "…"` parses as a map value under key `workloads`.

    Mutation: drop the `workloads` suffix -- the line ends after `got dict.`
    """
    assert _refusal(_identity({"apply": {"workloads": {"net": {"role": _ARN}}}})) == (
        "::error::identities.dev.aws.apply.workloads must be a string, got dict. The retired "
        'aws.apply.workloads.<name>.role is written aws.apply = { <name> = "…" }.'
    )


def test_the_retired_role_key_names_the_string_spelling():
    """`aws.plan.role = "…"` parses as a map with key `role`.

    Mutation: drop the `role` map-key case -- the line becomes the charset refusal.
    """
    assert _refusal(_identity({"plan": {"role": _ARN}})) == (
        "::error::identities.dev.aws.plan.role: role is not a workload name. The retired "
        'aws.plan.role = "…" is written aws.plan = "…".'
    )


_WORKLOAD_RULE = (
    "is not a workload name: lowercase letters, digits, '-' and '_', starting with a letter or "
    "digit, and not vars or role."
)
_NOT_WORKLOADS = ["vars", "Core", "a b", "-x"]


@pytest.mark.parametrize("name", _NOT_WORKLOADS)
def test_a_map_key_that_is_not_a_workload_name_refuses(name):
    """`vars` survives reference resolution only beside a second key.

    Mutation: drop the charset check on map keys -- every case validates.
    """
    table = _identity({"apply": {name: _ARN, "core": _ARN}})
    assert _refusal(table) == f"::error::identities.dev.aws.apply.{name} {_WORKLOAD_RULE}"


@pytest.mark.parametrize("name", [*_NOT_WORKLOADS, "role"])
def test_a_list_element_that_is_not_a_workload_name_refuses(name):
    """Mutation: drop the charset check on `workloads` elements -- every case validates."""
    table = _named({"apply": _ARN}, workloads=[name])
    assert _refusal(table) == (
        f"::error::environments.dev-eu.workloads entry {name!r} {_WORKLOAD_RULE}"
    )


def test_vars_as_a_list_element_refuses():
    """The literal case beside the parametrization, which a mistake in its own list could
    empty.

    Mutation: drop `vars` from the reserved names -- this validates.
    """
    table = _named({"apply": _ARN}, workloads=["core", "vars"])
    assert _refusal(table) == (
        "::error::environments.dev-eu.workloads entry 'vars' is not a workload name: lowercase "
        "letters, digits, '-' and '_', starting with a letter or digit, and not vars or role."
    )


@pytest.mark.parametrize(
    ("aws", "where"),
    [({"plan": ""}, "aws.plan"), ({"apply": {"core": ""}}, "aws.apply.core")],
    ids=["field", "map-value"],
)
def test_an_empty_string_refuses(aws, where):
    """Mutation: drop the empty check -- both cases validate."""
    assert _refusal(_identity(aws)) == (
        f"::error::identities.dev.{where} is empty. Give it a value, or remove the key."
    )


@pytest.mark.parametrize(
    ("account", "where"),
    [
        ("1234", "aws.account"),
        ("11111111111a", "aws.account"),
        ({"core": "12"}, "aws.account.core"),
    ],
    ids=["short", "letter", "map-value"],
)
def test_an_account_that_is_not_twelve_digits_refuses(account, where):
    """Mutation: drop the account check -- every case validates."""
    assert _refusal(_identity({"apply": _ARN, "account": account})) == (
        f"::error::identities.dev.{where} is not a 12-digit AWS account id in a quoted string. "
        "A TOML integer drops a leading 0."
    )


@pytest.mark.parametrize(
    ("aws", "where"),
    [
        ({"plan": "shipmate-{env}"}, "aws.plan"),
        ({"apply": {"core": "role}"}}, "aws.apply.core"),
        ({"apply": "{workload}-{workload"}, "aws.apply"),
    ],
    ids=["placeholder", "closing", "unclosed"],
)
def test_a_brace_outside_the_placeholder_refuses(aws, where):
    """Mutation: drop the brace check -- every case validates."""
    assert _refusal(_identity(aws)) == (
        f"::error::identities.dev.{where} holds a brace outside {{workload}}, the only placeholder."
    )


# --- how an environment names an identity: M15 to M20 -------------------------------------


@pytest.mark.parametrize(
    ("identities", "identity", "names"),
    [
        ({"dev": {"aws": {"apply": _ARN}}}, "prod", "dev"),
        ({}, "dev", "none"),
        ({"b": {"aws": {"apply": _ARN}}, "a": {"aws": {"apply": _ARN}}}, 7, "a, b"),
    ],
    ids=["unknown", "none-declared", "not-a-string"],
)
def test_an_identity_that_names_nothing_refuses(identities, identity, names):
    """Mutation: drop the unknown-identity check -- all three cases validate."""
    table = {
        "layout": "folder",
        "identities": identities,
        "environments": {"dev-eu": {"region": "eu-west-1", "identity": identity}},
    }
    assert _refusal(table) == (
        "::error::environment dev-eu: identity names no [identities.<name>] table; the file "
        f"declares {names}."
    )


@pytest.mark.parametrize("region", [None, ""], ids=["absent", "empty"])
def test_an_identity_without_a_region_refuses(region):
    """Mutation: drop the region check -- both cases validate, and the credentials step fails
    mid-run."""
    entry = {"identity": "dev"} if region is None else {"identity": "dev", "region": region}
    assert _refusal(_identity({"apply": _ARN}, **{"dev-eu": entry})) == (
        "::error::environment dev-eu names identity dev but sets no region, and the "
        "credentials step requires one. Set region."
    )


def test_workloads_without_an_identity_refuses():
    """Mutation: drop the check -- the list validates and admits nothing."""
    table = {"layout": "folder", "environments": {"dev-eu": {"workloads": ["core"]}}}
    assert _refusal(table) == (
        "::error::environment dev-eu: workloads admits workloads to an identity, and the "
        "environment names none. Name an identity, or remove workloads; without one a workload "
        "tag is inert."
    )


@pytest.mark.parametrize(
    "workloads", [[], "core", [1], ["core", "core"]], ids=["empty", "string", "int", "duplicate"]
)
def test_a_workloads_value_that_is_not_a_list_of_distinct_names_refuses(workloads):
    """Mutations: accept an empty list -- the `empty` case validates; drop the duplicate test
    -- the `duplicate` case validates."""
    assert _refusal(_named({"apply": _ARN}, workloads=workloads)) == (
        "::error::environments.dev-eu.workloads must be a non-empty list of distinct "
        "workload-name strings."
    )


@pytest.mark.parametrize(
    "aws",
    [
        {"apply": "shipmate-{workload}", "account": _ACCOUNT},
        {"apply": {"core": _ARN}},
        {"apply": "shipmate-apply", "account": {"core": _ACCOUNT}},
    ],
    ids=["placeholder", "role-map", "account-map"],
)
def test_a_varying_identity_needs_a_workloads_list(aws):
    """Mutations: drop the check -- every case validates; test only for `{workload}` when
    deciding whether an identity varies -- the two map cases refuse naming workload `None`
    instead: `role-map` as missing from `aws.apply`, `account-map` as missing from
    `aws.account`."""
    assert _refusal(_named(aws)) == (
        "::error::environment dev-eu names identity dev, whose roles vary by workload, and "
        'lists no workloads. List the workloads it admits: workloads = ["…"].'
    )


def test_a_shared_environment_naming_an_identity_with_a_plan_role_refuses():
    """Mutation: drop the check -- every plan cell would assume the apply role while the file
    reads as a read-only plan role."""
    assert _refusal(_named({"plan": _ARN, "apply": _ARN}, shared=True)) == (
        "::error::environment dev-eu is shared between the plan and apply paths, so identity "
        "dev's aws.plan cannot apply to it: a shared environment uses aws.apply on both paths. "
        "Name an identity without aws.plan, or set shared = false."
    )


def test_a_shared_environment_naming_an_apply_only_identity_validates():
    """Mutation: refuse every identity in a shared environment."""
    table = _named({"apply": _ARN}, shared=True)
    assert ec.validate_structure(table) is table


# --- the role checks: M21 to M24 ----------------------------------------------------------


def test_a_listed_workload_missing_from_a_role_map_refuses():
    """Mutation: drop the map-key check -- the lookup of `net` raises a raw `KeyError`."""
    table = _named({"apply": {"core": _ARN}}, workloads=["core", "net"])
    assert _refusal(table) == (
        "::error::environment dev-eu lists workload net, and identities.dev.aws.apply has no net "
        "entry. Add it, or remove net from workloads."
    )


def test_a_listed_workload_missing_from_the_account_map_refuses():
    """Mutation: require no account for a role name -- this validates."""
    aws = {"plan": "shipmate-plan", "apply": "shipmate-apply", "account": {"core": _ACCOUNT}}
    assert _refusal(_named(aws, workloads=["core", "net"])) == (
        "::error::environment dev-eu lists workload net, and identities.dev.aws.account has no "
        "net entry. Add it, or remove net from workloads."
    )


@pytest.mark.parametrize(
    ("aws", "workloads", "cell"),
    [
        ({"plan": "shipmate-plan", "apply": _ARN, "account": _ACCOUNT}, None, "every cell"),
        ({"plan": {"core": "p"}, "apply": {"core": _ARN}}, ["core"], "workload core"),
    ],
    ids=["every-cell", "listed"],
)
def test_a_role_name_on_one_path_and_an_arn_on_the_other_refuses(aws, workloads, cell):
    """Mutation: drop the mixed-kind check -- both cases validate; in the every-cell case
    `resolve` then hands the plan path a name-derived ARN beside the apply path's full ARN."""
    entry = {} if workloads is None else {"workloads": workloads}
    assert _refusal(_named(aws, **entry)) == (
        f"::error::environment dev-eu: identities.dev gives {cell} a role name on one path and "
        "a full ARN on the other. Write both as ARNs."
    )


@pytest.mark.parametrize(
    ("aws", "workloads", "cell"),
    [
        ({"plan": "shipmate-plan"}, None, "every cell"),
        ({"plan": "shipmate-plan-{workload}"}, ["core"], "workload core"),
    ],
    ids=["every-cell", "listed"],
)
def test_a_role_name_without_an_account_refuses(aws, workloads, cell):
    """Mutation: drop the no-account check -- both cases validate and resolve no role."""
    entry = {} if workloads is None else {"workloads": workloads}
    assert _refusal(_named(aws, **entry)) == (
        f"::error::environment dev-eu: identities.dev.aws.plan is a role name for {cell}, and "
        "no account is set for it. Set identities.dev.aws.account, or write the role as a full "
        "ARN."
    )


@pytest.mark.parametrize(
    ("aws", "workloads", "cell", "gives"),
    [
        ({"apply": _ARN, "account": _ACCOUNT}, None, "every cell", "every cell"),
        (
            {"apply": {"core": _ARN}, "account": {"core": _ACCOUNT}},
            ["core"],
            "workload core",
            "core",
        ),
    ],
    ids=["every-cell", "listed"],
)
def test_an_arn_beside_an_account_refuses(aws, workloads, cell, gives):
    """Mutation: drop the ARN-and-account check -- both cases validate."""
    entry = {} if workloads is None else {"workloads": workloads}
    assert _refusal(_named(aws, **entry)) == (
        f"::error::environment dev-eu: identities.dev.aws.apply is a full ARN for {cell}, and "
        f"identities.dev.aws.account also gives {gives} an account. An ARN carries its own; "
        "remove the account, or write the role as a name."
    )


def test_each_workload_stops_at_its_first_refusal_and_the_rest_go_on():
    """`core` is a name with no account; `net` is missing from the map, which is checked
    first, so its no-account line never appears. The second environment is checked too.

    Mutation: gather once per environment instead of once per workload -- the `net` line
    disappears.
    """
    table = _identity(
        {"plan": {"core": "shipmate-plan"}},
        **{
            "dev-eu": {"region": "eu-west-1", "identity": "dev", "workloads": ["core", "net"]},
            "dev-us": {"region": "us-east-1", "identity": "dev", "workloads": ["core"]},
        },
    )
    no_account = (
        "identities.dev.aws.plan is a role name for workload core, and no account is set for "
        "it. Set identities.dev.aws.account, or write the role as a full ARN."
    )
    assert _refusal(table) == (
        f"::error::environment dev-eu: {no_account}\n"
        "::error::environment dev-eu lists workload net, and identities.dev.aws.plan has no net "
        "entry. Add it, or remove net from workloads.\n"
        f"::error::environment dev-us: {no_account}"
    )


# --- one refusal names every error ---------------------------------------------------------


def test_errors_across_identities_and_environments_refuse_as_one():
    """Two errors in one identity, one in another, and one in each of two environments, in
    check order: identities by name, then entries by name.

    Mutations: stop the identity checks at the first error -- the account and `b` lines
    disappear; check the entries before the identities -- the environment lines move first.
    """
    table = {
        "layout": "folder",
        "identities": {
            "b": {"aws": {"apply": "x-{env}"}},
            "a": {"aws": {"apply": _ARN, "account": "12"}, "region": "eu-west-1"},
        },
        "environments": {
            "prod": {"workloads": ["core"]},
            "dev": {"region": "eu-west-1", "identity": "c"},
        },
    }
    assert _refusal(table) == (
        "::error::identities.a.region is not a key this engine implements. An identity holds "
        "aws.account, aws.plan and aws.apply.\n"
        "::error::identities.a.aws.account is not a 12-digit AWS account id in a quoted string. "
        "A TOML integer drops a leading 0.\n"
        "::error::identities.b.aws.apply holds a brace outside {workload}, the only "
        "placeholder.\n"
        "::error::environment dev: identity names no [identities.<name>] table; the file "
        "declares a, b.\n"
        "::error::environment prod: workloads admits workloads to an identity, and the "
        "environment names none. Name an identity, or remove workloads; without one a workload "
        "tag is inert."
    )


def test_an_environment_naming_a_refused_identity_skips_its_role_checks():
    """The identity's own line is the only one: its name-without-account would otherwise
    appear a second time from the environment.

    Mutation: run the role checks for every named identity, refused or not.
    """
    table = _named({"plan": "shipmate-plan", "role": "x"})
    assert _refusal(table) == (
        "::error::identities.dev.aws.role is retired: one role on both paths hands any-branch "
        "plan cells the apply role's permissions. Write aws.plan and aws.apply."
    )


# --- references resolve before the checks -------------------------------------------------

_REFERENCED_ROLE = """\
layout = "folder"

[identities.dev]
aws.apply = { vars = "DEV_APPLY_ROLE" }

[environments.dev-eu]
region   = "eu-west-1"
identity = "dev"
"""


def test_a_variable_holding_a_full_arn_needs_no_account():
    """Mutation: validate the raw `load_toml` result -- `{ vars = ... }` then refuses as a map
    keyed `vars`."""
    table = ec.parse_table(_REFERENCED_ROLE, {"DEV_APPLY_ROLE": _ARN})
    assert ec.validate_structure(table) is table


def test_a_variable_holding_a_role_name_needs_an_account():
    """Mutation: as above -- the line names the `vars` key instead."""
    table = ec.parse_table(_REFERENCED_ROLE, {"DEV_APPLY_ROLE": "shipmate-apply"})
    assert _refusal(table) == (
        "::error::environment dev-eu: identities.dev.aws.apply is a role name for every cell, "
        "and no account is set for it. Set identities.dev.aws.account, or write the role as a "
        "full ARN."
    )


def test_a_variable_holding_the_placeholder_fills_it():
    """The variable's value decides that the identity varies, so the entry lists workloads.

    Mutation: as above.
    """
    text = (
        _REFERENCED_ROLE.replace(
            "[identities.dev]\n", f'[identities.dev]\naws.account = "{_ACCOUNT}"\n'
        )
        + 'workloads = ["core"]\n'
    )
    table = ec.validate_structure(ec.parse_table(text, {"DEV_APPLY_ROLE": "x-{workload}"}))
    assert ec.resolve(table, "dev-eu", "apply", "core")["role_arn"] == (
        "arn:aws:iam::111111111111:role/x-core"
    )


# --- tables that validate --------------------------------------------------------------------

#: One identity, two environments, the account map holding every workload of either.
_STAGE = """\
layout = "tf_vars"

[identities.dev]
aws.account = { core = "111111111111", network = "222222222222", product = "333333333333" }
aws.plan    = "a18n-tofu-plan"
aws.apply   = "a18n-tofu-deploy"

[environments.dev-eu]
region    = "eu-central-1"
identity  = "dev"
workloads = ["core", "network", "product"]

[environments.dev-us]
region    = "us-east-1"
identity  = "dev"
workloads = ["network", "product"]
needs     = ["dev-eu"]
"""


@pytest.mark.parametrize(
    ("path", "workload", "role"),
    [
        ("plan", "network", "arn:aws:iam::222222222222:role/a18n-tofu-plan"),
        ("apply", "product", "arn:aws:iam::333333333333:role/a18n-tofu-deploy"),
        ("apply", "core", ""),
    ],
)
def test_one_identity_serves_two_environments_each_with_its_own_list(path, workload, role):
    """`dev-us` does not list `core`, which the map still holds for `dev-eu`.

    Mutations: look the account up by environment instead of workload in the account check --
    the table refuses; take the account from the wrong workload in `_role` -- the roles swap.
    """
    table = ec.validate_structure(ec.parse_table(_STAGE, {}))
    assert ec.resolve(table, "dev-us", path, workload)["role_arn"] == role


def test_a_non_varying_identity_with_a_list_credentials_an_untagged_cell():
    """Mutation: treat an empty tag as off the list in `resolve` -- the role empties."""
    table = ec.validate_structure(_named({"apply": _ARN}, workloads=["net"]))
    assert ec.resolve(table, "dev-eu", "apply", "")["role_arn"] == _ARN


def test_an_account_map_need_not_cover_an_arn_workload():
    """`network` names its role by ARN, so it has no account entry and needs none.

    Mutation: require an account entry for every listed workload -- this refuses.
    """
    aws = {
        "account": {"core": _ACCOUNT},
        "apply": {"core": "shipmate-apply", "network": "arn:aws:iam::222222222222:role/net"},
    }
    table = _named(aws, workloads=["core", "network"])
    assert ec.validate_structure(table) is table


# --- the resolved-role matrix ----------------------------------------------------------------


def test_resolved_roles_lists_every_environment_path_and_workload():
    """One row per listed workload for a varying identity, in the list's written order; one
    row with an empty workload for a non-varying one; the apply role on both paths of a shared
    environment; no row for an environment naming no identity.

    Mutations: sort each list -- `network` moves before `core`; stop switching a shared
    environment to `aws.apply` -- its plan row empties.
    """
    table = {
        "layout": "folder",
        "identities": {
            "stage": {"aws": {"account": _ACCOUNT, "apply": "deploy-{workload}"}},
            "sbx": {"aws": {"apply": _ARN}},
        },
        "environments": {
            "dev": {"region": "eu-west-1", "identity": "stage", "workloads": ["network", "core"]},
            "sbx": {"region": "eu-west-1", "identity": "sbx", "shared": True},
            "free": {"region": "eu-west-1"},
        },
    }
    assert ec.resolved_roles(ec.validate_structure(table)) == [
        ("dev", "plan", "network", ""),
        ("dev", "plan", "core", ""),
        ("dev", "apply", "network", "arn:aws:iam::111111111111:role/deploy-network"),
        ("dev", "apply", "core", "arn:aws:iam::111111111111:role/deploy-core"),
        ("sbx", "plan", "", _ARN),
        ("sbx", "apply", "", _ARN),
    ]
