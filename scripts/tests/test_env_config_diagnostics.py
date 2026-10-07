"""The `env-config` diagnostics that report rather than refuse.

Everything else this script does refuses. These do not, and that asymmetry is the
subject: an unused table entry, or a listed workload no stack tags, warns because the table
is read from the default branch while the tags come from the feature branch, so adding or
removing an environment or a workload is a two-pull-request sequence, and refusing the
unused name alongside the missing one would deadlock both directions.

Reddens on: turning the warning into a refusal, deleting the print, firing it where no
whole-tree scan exists, and gating the missing-entry refusal on that scan.

Messages are compared whole against hand-written literals: an operator reading a warning
in a run log has no other source, so the text is part of the contract.
"""

from typing import Any

import pytest
from _loader import load_script

env_config = load_script("env-config")

#: Hand-written, not imported from the script: a constant derived from the file it checks
#: passes whatever the file says.
UNUSED_DEV_US = (
    "::warning::the environment table declares dev-us, which no stack tags. Remove the "
    "entry, or tag the stacks that belong to it. This is a warning rather than a refusal "
    "because the table is read from the default branch and the tags from this branch, so "
    "an environment arrives and leaves over two pull requests."
)
UNUSED_TWO = (
    "::warning::the environment table declares dev-us, prod-eu, which no stack tags. "
    "Remove the entry, or tag the stacks that belong to it. This is a warning rather than "
    "a refusal because the table is read from the default branch and the tags from this "
    "branch, so an environment arrives and leaves over two pull requests."
)

TABLE: dict[str, Any] = {
    "layout": "folder",
    "environments": {
        "dev-eu": {"region": "eu-west-1"},
        "dev-us": {"region": "us-east-1"},
    },
}


def _scan(*envs, **workloads):
    """A whole-tree scan: each env in `envs` tagged by no workload, then `workloads`' envs."""
    return {**dict.fromkeys(envs, frozenset()), **{e: frozenset(w) for e, w in workloads.items()}}


def _validate(table, matrix_envs=(), tagged=None):
    return env_config.validate(table, matrix_envs, tagged=tagged)


# --- 1: an unused entry warns, and never refuses --------------------------------------


def test_an_unused_entry_warns_and_returns_the_table(capsys):
    """Mutation: raise `SystemExit` instead of printing. Refusing here deadlocks both adding
    an environment (stacks on the branch, entry not yet on the default branch) and removing
    one."""
    assert _validate(TABLE, matrix_envs=("dev-eu",), tagged=_scan("dev-eu")) == TABLE
    assert capsys.readouterr().out.splitlines() == [UNUSED_DEV_US]


def test_every_unused_entry_is_named(capsys):
    """Mutation: name only the first. One entry per line, or a per-name assertion, would
    both pass that."""
    table = {**TABLE, "environments": {**TABLE["environments"], "prod-eu": {}}}
    _validate(table, matrix_envs=("dev-eu",), tagged=_scan("dev-eu"))
    assert capsys.readouterr().out.splitlines() == [UNUSED_TWO]


def test_a_fully_used_table_says_nothing(capsys):
    """Mutation: print the warning whenever `tagged` is passed, empty list or not."""
    _validate(TABLE, matrix_envs=("dev-eu",), tagged=_scan("dev-eu", "dev-us"))
    assert capsys.readouterr().out == ""


def test_an_entry_matching_only_in_case_is_unused(capsys):
    """Matching is exact, as `resolve` and the `tf_vars` coverage check match: an entry a cell's
    environment name does not equal resolves for nothing, so it IS unused.

    Mutation: lower both sides. The entry is then reported as used while `resolve` still
    misses it."""
    _validate(TABLE, matrix_envs=("dev-eu",), tagged=_scan("dev-eu", "DEV-US"))
    assert capsys.readouterr().out.splitlines() == [UNUSED_DEV_US]


def test_a_misspelled_flagged_entry_warns_as_unused(capsys):
    """`explicit` is the only thing holding an environment back from a bare `shipmate
    apply`, so a flag on a misspelled entry holds nothing back and the apply runs. The flag
    sits on the entry, so the unused-entry warning is what names it.

    Mutation: drop the unused-entry print from `_report_unused`.
    """
    table = {"layout": "folder", "environments": {"prd": {"explicit": True, "gated": False}}}
    _validate(table, tagged=_scan("prod"))
    assert capsys.readouterr().out.splitlines() == [
        "::warning::the environment table declares prd, which no stack tags. Remove the "
        "entry, or tag the stacks that belong to it. This is a warning rather than a refusal "
        "because the table is read from the default branch and the tags from this branch, so "
        "an environment arrives and leaves over two pull requests."
    ]


# --- 2: no whole-tree scan, no diagnostic ---------------------------------------------


def test_no_whole_tree_scan_means_no_unused_diagnostic(capsys):
    """`tagged=None` is every path that scans a changed set or a workset rather than the
    tree, and an environment absent from those is no evidence of anything.

    Mutation: evaluate the diagnostic against `matrix_envs` when `tagged` is None. The
    sibling above is what keeps this one from passing with the print deleted."""
    assert _validate(TABLE, matrix_envs=("dev-eu",), tagged=None) == TABLE
    assert capsys.readouterr().out == ""


# --- 3: the refusal is unaffected by the scan -----------------------------------------


def test_a_tagged_environment_missing_from_the_table_refuses_without_a_scan(capsys):
    """The missing-entry refusal needs only the environments already in the matrix, so it
    fires on a targeted apply too.

    Mutation: gate `_check_dry_coverage` on `tagged is not None`. Every path without a
    whole-tree scan then stops refusing."""
    table = {"layout": "tf_vars", "environments": {"dev-eu": {"region": "eu-west-1"}}}
    with pytest.raises(SystemExit) as excinfo:
        _validate(table, matrix_envs=("dev-eu", "prod-us"), tagged=None)
    assert str(excinfo.value) == (
        '::error::layout = "tf_vars" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and prod-us has no entry in it."
    )


def test_the_refusal_precedes_the_unused_warning(capsys):
    """A table that is both incomplete and over-complete refuses; it does not warn and
    continue. Mutation: run the diagnostic before the coverage check."""
    table = {"layout": "tf_vars", "environments": {"dev-us": {"region": "us-east-1"}}}
    with pytest.raises(SystemExit):
        _validate(table, matrix_envs=("dev-eu",), tagged=_scan("dev-eu"))
    assert capsys.readouterr().out == ""


# --- 4: a reference key naming no environment warns too -------------------------------


#: Hand-written, whole: the consequence clause is the warning's entire value, and a partial
#: match would pass a message that named the key and dropped what it fails to do.
UNUSED_NEEDS = (
    "::warning::needs names ghost, which no stack tags, so it orders nothing. Remove "
    "the entry, or tag the stacks that belong to it. This is a warning rather than a "
    "refusal because the table is read from the default branch and the tags from this "
    "branch, so an environment arrives and leaves over two pull requests."
)


def test_a_needs_predecessor_naming_no_environment_warns(capsys):
    """A predecessor matching nothing reads as declared and constrains nothing. It needs no
    entry of its own, so it passes the structural checks and warns here instead: refusing it
    would make adding an environment and ordering after it two pull requests in lockstep.

    Mutations: require an entry for every predecessor in `validate_structure` -- this raises;
    or drop `needs` from the reference-key mapping -- nothing prints."""
    table = {"layout": "folder", "environments": {"prod": {"needs": ["ghost"]}}}
    assert env_config.validate_structure(table) is table
    assert _validate(table, tagged=_scan("prod")) == table
    assert capsys.readouterr().out.splitlines() == [UNUSED_NEEDS]


def test_reference_keys_naming_real_environments_say_nothing(capsys):
    """The flags sit on an entry, so they reference no other environment and add no warning.

    Mutation: warn whenever a reference key is present.
    """
    table = {
        "layout": "folder",
        "environments": {"prod": {"needs": ["dev"], "explicit": True, "gated": False}},
    }
    _validate(table, tagged=_scan("prod", "dev"))
    assert capsys.readouterr().out == ""


def test_a_reference_key_warns_only_under_a_whole_tree_scan(capsys):
    """Same rule as the table's own entries: a changed set or a workset is no evidence that
    an environment does not exist.

    Mutation: report reference keys regardless of `tagged`."""
    table = {"layout": "folder", "environments": {"prod": {"needs": ["ghost"]}}}
    _validate(table, tagged=None)
    assert capsys.readouterr().out == ""


# --- 5: a listed workload no stack tags warns ---------------------------------------------


#: One identity, one entry listing `core` and `net`. Hand-written, whole.
_LISTING = {
    "layout": "folder",
    "identities": {"dev": {"aws": {"apply": "arn:aws:iam::111111111111:role/apply"}}},
    "environments": {
        "dev-eu": {"region": "eu-west-1", "identity": "dev", "workloads": ["core", "net"]}
    },
}
UNTAGGED_NET = (
    "::warning::environments.dev-eu.workloads lists net, which no stack in dev-eu tags. "
    "Tag a stack with each, or remove it from the list once the pull request that drops its "
    "last tag has merged. This is a warning rather than a refusal because the table is read "
    "from the default branch and the tags from this branch, so a workload arrives and leaves "
    "over two pull requests."
)
UNUSED_DEV_EU = (
    "::warning::the environment table declares dev-eu, which no stack tags. Remove the "
    "entry, or tag the stacks that belong to it. This is a warning rather than a refusal "
    "because the table is read from the default branch and the tags from this branch, so "
    "an environment arrives and leaves over two pull requests."
)


def test_a_listed_workload_no_stack_tags_warns(capsys):
    """Mutation: hand `_report_unused` the matrix environments, each tagged by nothing,
    instead of `tagged` -- the warning names `core, net`."""
    table = _validate(_LISTING, matrix_envs=("dev-eu",), tagged=_scan(**{"dev-eu": {"core"}}))
    assert table == _LISTING
    assert capsys.readouterr().out.splitlines() == [UNTAGGED_NET]


def test_a_listed_workload_in_an_untagged_environment_warns_once(capsys):
    """An environment no stack tags gets the unused-entry warning alone, not a second line
    naming each of its listed workloads.

    Mutation: drop the skip for an environment absent from `tagged` -- a second line naming
    `core, net` prints.
    """
    _validate(_LISTING, tagged=_scan("prod"))
    assert capsys.readouterr().out.splitlines() == [UNUSED_DEV_EU]


def test_every_listed_workload_tagged_says_nothing(capsys):
    """Mutation: the same substitution as above -- `core, net` is then reported."""
    _validate(_LISTING, matrix_envs=("dev-eu",), tagged=_scan(**{"dev-eu": {"core", "net"}}))
    assert capsys.readouterr().out == ""


def test_a_listed_workload_is_untagged_per_environment():
    """A workload `dev-eu` lists and only a `prod` stack tags is untagged in `dev-eu`.

    Mutation: diff each list against the workloads tagged in any environment -- `net` is
    tagged in `prod`, so nothing is reported.
    """
    table = {
        "layout": "folder",
        "environments": {
            "dev-eu": {"workloads": ["core", "net"]},
            "prod": {"workloads": ["net"]},
        },
    }
    tagged = _scan(**{"dev-eu": {"core"}, "prod": {"net"}})
    assert env_config.untagged_workloads(table, tagged) == [("dev-eu", ["net"])]


def test_a_listed_workload_warns_only_under_a_whole_tree_scan(capsys):
    """A plan scans only changed stacks, so a listed workload on an unchanged one is untagged
    there. Mutation: call `_report_unused(table, tagged or {})` when `tagged` is None -- the
    unused-entry warning for dev-eu prints, alone, since an environment no stack tags gets no
    listed-workload line."""
    _validate(_LISTING, matrix_envs=("dev-eu",), tagged=None)
    assert capsys.readouterr().out == ""
