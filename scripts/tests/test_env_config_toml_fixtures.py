"""The two configuration files the design publishes, parsed and validated as written.

`docs/` and the design both show these exact bytes, so they are the cases a reader will
copy. One must validate and resolve; the other must refuse. Both are used verbatim rather
than paraphrased: a paraphrase drops the notation -- dotted keys, comment placement, the
ordering of top-level scalars against the first `[table]` header -- which is the part TOML
gets wrong silently.

The resolution assertions here are this feature's only functional-equivalence evidence: a
whole resolved cell, compared against a hand-written constant, for a file that went through
the real parser.
"""

import pytest
from _loader import load_script

ec = load_script("env-config")

#: The canonical file, verbatim from the design's schema section.
CANONICAL = """\
layout        = "dry"              # "dry" | "workspace" | "folder", required
explicit_envs = ["prod"]           # optional

[env_order]                        # optional: env -> envs that must fully apply first
dev-us = ["dev-eu"]

[environments.dev-eu]
region         = "eu-west-1"
aws.plan.role  = "arn:aws:iam::981781037707:role/shipmate-plan"
aws.apply.role = "arn:aws:iam::981781037707:role/shipmate-apply"

[environments.prod]
region         = "eu-west-1"
aws.plan.role  = "arn:aws:iam::981781037707:role/prod-plan"
aws.apply.role = "arn:aws:iam::981781037707:role/prod-apply"
# A workload inherits its tier's fields and overrides one:
aws.apply.workloads.net-edge.role = "arn:aws:iam::981781037707:role/net-edge"
vars.TF_VAR_tier = "core"          # optional, merged over the derived TF_VAR_*
"""

#: The misplaced control, verbatim from the design's strict-validation section.
MISPLACED_CONTROL = """\
layout = "folder"

[env_order]
prod = ["dev"]
explicit_envs = ["prod"]      # intended as a top-level control
"""


def test_the_canonical_file_validates():
    """Every top-level key the schema allows, dotted provider keys, a workload tier and an
    environment named only by `env_order`.

    Mutation: remove any of the four names from the allowed top-level set, or add a check
    that every `env_order` key has an `environments` entry -- `dev-us` has none,
    deliberately, and the design's own file declares it that way.
    """
    table = ec.parse_table(CANONICAL)
    assert ec.validate(table, ("dev-eu", "prod"), ()) is table
    assert ec.validate_structure(table) is table


def test_the_misplaced_control_refuses():
    """`explicit_envs` written below `[env_order]` parses as an ordering entry for an
    environment of that name, and `prod` then silently loses its exclusion from a bare
    apply. The file is well-formed TOML and every pre-existing check passes it.

    Mutation: delete the `_TOP_KEYS` membership check from `validate_env_order` -- the file
    validates and the exclusion is lost with no diagnostic anywhere.
    """
    table = ec.parse_table(MISPLACED_CONTROL)
    assert table == {"layout": "folder", "env_order": {"prod": ["dev"], "explicit_envs": ["prod"]}}
    with pytest.raises(SystemExit) as exc:
        ec.validate(table, (), ())
    assert str(exc.value) == (
        "::error::env_order['explicit_envs'] names a top-level setting, not an environment. "
        "A scalar written below a [table] header lands inside that table, so "
        "`explicit_envs = ...` after [env_order] becomes an ordering entry instead of a "
        "top-level setting. Move it above the first header in .github/shipmate.toml."
    )


_CELLS = [
    (
        "prod",
        "apply",
        "net-edge",
        {
            "role_arn": "arn:aws:iam::981781037707:role/net-edge",
            "cred_region": "eu-west-1",
            "tf_vars": {
                "TF_VAR_env": "prod",
                "TF_VAR_region": "eu-west-1",
                "TF_VAR_tier": "core",
            },
            "config_path": "apply",
        },
    ),
    (
        "prod",
        "apply",
        "app",
        {
            "role_arn": "arn:aws:iam::981781037707:role/prod-apply",
            "cred_region": "eu-west-1",
            "tf_vars": {
                "TF_VAR_env": "prod",
                "TF_VAR_region": "eu-west-1",
                "TF_VAR_tier": "core",
            },
            "config_path": "apply",
        },
    ),
    (
        "prod",
        "plan",
        "net-edge",
        {
            "role_arn": "arn:aws:iam::981781037707:role/prod-plan",
            "cred_region": "eu-west-1",
            "tf_vars": {
                "TF_VAR_env": "prod",
                "TF_VAR_region": "eu-west-1",
                "TF_VAR_tier": "core",
            },
            "config_path": "plan",
        },
    ),
    (
        "dev-eu",
        "apply",
        "app",
        {
            "role_arn": "arn:aws:iam::981781037707:role/shipmate-apply",
            "cred_region": "eu-west-1",
            "tf_vars": {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"},
            "config_path": "apply",
        },
    ),
    (
        "dev-eu",
        "plan",
        "app",
        {
            "role_arn": "arn:aws:iam::981781037707:role/shipmate-plan",
            "cred_region": "eu-west-1",
            "tf_vars": {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"},
            "config_path": "plan",
        },
    ),
]


@pytest.mark.parametrize(("env", "path", "workload", "expected"), _CELLS)
def test_the_canonical_file_resolves_each_cell(env, path, workload, expected):
    """A file the real parser read, through the real validator, into whole cells compared
    against hand-written constants -- never against values derived from the file.

    Mutations, each reddening one row: stop the workload tier overriding its path (row 0
    resolves the plain apply role); resolve the plan tier for an apply cell, or the reverse
    (rows 1 and 2 swap roles); drop the environment's own `vars` from the merge (`TF_VAR_tier`
    disappears); stop inheriting the environment's region into the provider block (every
    `cred_region` empties).
    """
    table = ec.validate(ec.parse_table(CANONICAL), ("dev-eu", "prod"), ())
    assert ec.resolve(table, env, path, workload, ()) == expected
