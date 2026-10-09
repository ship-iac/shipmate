"""A whole configuration file, parsed and validated as written.

It is YAML text rather than a mapping: a mapping skips the parser, and with it the spelling a
consumer actually writes -- block and flow mappings, flow lists, comments beside values.

The resolution assertions here are this feature's only functional-equivalence evidence: a
whole resolved cell, compared against a hand-written constant, for a file that went through
the real parser.
"""

import pytest
from _loader import load_script

ec = load_script("env-config")

#: Every top-level key, and every entry key but `shared`.
CANONICAL = """\
layout: tf_vars                    # tf_vars | workspace | folder, required

identities:
  dev:
    aws:
      account: 981781037707
      plan: shipmate-plan          # a role name, under aws.account
      apply: shipmate-apply
  prod:
    aws:
      plan: arn:aws:iam::981781037707:role/prod-plan
      apply:                       # a map gives each workload its own role
        app: arn:aws:iam::981781037707:role/prod-apply
        net-edge: arn:aws:iam::981781037707:role/net-edge

environments:
  dev-eu:
    region: eu-west-1
    gated: false                   # optional: a targeted apply needs no approving review
    identity: dev
  dev-us:
    region: us-east-1
    needs: [dev-eu]                # optional: envs that must fully apply first
  prod:
    region: eu-west-1
    explicit: true                 # optional: a bare `shipmate apply` skips it
    identity: prod
    workloads: [app, net-edge]     # the workload tags prod admits
    tf_vars:
      TF_VAR_tier: core            # optional, merged over the derived TF_VAR_*
"""


def test_the_canonical_file_validates():
    """Every top-level key, a string and a map role field, a workload list, an
    ordering and both entry flags.

    Mutation: remove any of the three names from the allowed top-level set, or `"identity"`,
    `"workloads"`, `"needs"`, `"explicit"` or `"gated"` from the allowed entry keys.
    """
    table = ec.parse_table(CANONICAL)
    assert ec.validate(table, ("dev-eu", "prod")) is table
    assert ec.validate_structure(table) is table


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
            "env_binding": "prod-apply",
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
            "env_binding": "prod-apply",
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
            "env_binding": "prod-plan",
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
            "env_binding": "dev-eu-apply",
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
            "env_binding": "dev-eu-plan",
        },
    ),
]


@pytest.mark.parametrize(("env", "path", "workload", "expected"), _CELLS)
def test_the_canonical_file_resolves_each_cell(env, path, workload, expected):
    """A file the real parser read, through the real validator, into whole cells compared
    against hand-written constants -- never against values derived from the file.

    Mutations, each reddening one row: look the role map up by a fixed key (rows 0 and 1
    resolve one role); resolve the plan field for an apply cell, or the reverse (rows 1 and 2
    swap roles); drop the environment's own `tf_vars` from the merge (`TF_VAR_tier`
    disappears); stop taking `cred_region` from the environment's region (every `cred_region`
    empties).
    """
    table = ec.validate(ec.parse_table(CANONICAL), ("dev-eu", "prod"))
    assert ec.resolve(table, env, path, workload) == expected
