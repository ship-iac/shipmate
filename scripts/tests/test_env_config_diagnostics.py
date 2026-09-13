"""The one `env-config` diagnostic that reports rather than refuses.

Everything else this script does refuses. This one does not, and that asymmetry is the
subject: an unused table entry warns because the table is read from the default branch
while the environment list comes from the feature branch, so adding or removing an
environment is a two-pull-request sequence and refusing the unused entry alongside the
missing one would deadlock both directions.

Reddens on: turning the warning into a refusal, deleting the print, firing it where no
whole-tree scan exists, and gating the missing-entry refusal on that scan.

Messages are compared whole against hand-written literals: an operator reading a warning
in a run log has no other source, so the text is part of the contract.
"""

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

TABLE = {
    "layout": "folder",
    "environments": {
        "dev-eu": {"aws": {"region": "eu-west-1", "apply": {"role": "arn:aws:iam::1:role/a"}}},
        "dev-us": {"aws": {"region": "us-east-1", "apply": {"role": "arn:aws:iam::1:role/b"}}},
    },
}


def _validate(table, matrix_envs=(), shared_envs=(), all_envs=None):
    return env_config.validate(table, matrix_envs, shared_envs, all_envs=all_envs)


# --- 1: an unused entry warns, and never refuses --------------------------------------


def test_an_unused_entry_warns_and_returns_the_table(capsys):
    """Mutation: raise `SystemExit` instead of printing. Refusing here deadlocks both adding
    an environment (stacks on the branch, entry not yet on the default branch) and removing
    one."""
    assert _validate(TABLE, matrix_envs=("dev-eu",), all_envs={"dev-eu"}) == TABLE
    assert capsys.readouterr().out.splitlines() == [UNUSED_DEV_US]


def test_every_unused_entry_is_named(capsys):
    """Mutation: name only the first. One entry per line, or a per-name assertion, would
    both pass that."""
    table = {**TABLE, "environments": {**TABLE["environments"], "prod-eu": {}}}
    _validate(table, matrix_envs=("dev-eu",), all_envs={"dev-eu"})
    assert capsys.readouterr().out.splitlines() == [UNUSED_TWO]


def test_a_fully_used_table_says_nothing(capsys):
    """Mutation: print the warning whenever `all_envs` is passed, empty list or not."""
    _validate(TABLE, matrix_envs=("dev-eu",), all_envs={"dev-eu", "dev-us"})
    assert capsys.readouterr().out == ""


def test_an_entry_matching_only_in_case_is_unused(capsys):
    """Matching is exact, as `resolve` and the `dry` coverage check match: an entry a cell's
    environment name does not equal resolves for nothing, so it IS unused.

    Mutation: lower both sides. The entry is then reported as used while `resolve` still
    misses it."""
    _validate(TABLE, matrix_envs=("dev-eu",), all_envs={"dev-eu", "DEV-US"})
    assert capsys.readouterr().out.splitlines() == [UNUSED_DEV_US]


# --- 2: no whole-tree scan, no diagnostic ---------------------------------------------


def test_no_whole_tree_scan_means_no_unused_diagnostic(capsys):
    """`all_envs=None` is every path that scans a changed set or a workset rather than the
    tree, and an environment absent from those is no evidence of anything.

    Mutation: evaluate the diagnostic against `matrix_envs` when `all_envs` is None. The
    sibling above is what keeps this one from passing with the print deleted."""
    assert _validate(TABLE, matrix_envs=("dev-eu",), all_envs=None) == TABLE
    assert capsys.readouterr().out == ""


# --- 3: the refusal is unaffected by the scan -----------------------------------------


def test_a_tagged_environment_missing_from_the_table_refuses_without_a_scan(capsys):
    """The missing-entry refusal needs only the environments already in the matrix, so it
    fires on a targeted apply too.

    Mutation: gate `_check_dry_coverage` on `all_envs is not None`. Every path without a
    whole-tree scan then stops refusing."""
    table = {"layout": "dry", "environments": {"dev-eu": {"region": "eu-west-1"}}}
    with pytest.raises(SystemExit) as excinfo:
        _validate(table, matrix_envs=("dev-eu", "prod-us"), all_envs=None)
    assert str(excinfo.value) == (
        '::error::layout = "dry" derives TF_VAR_env and TF_VAR_region from the '
        "environment table, and prod-us has no entry in it."
    )


def test_the_refusal_precedes_the_unused_warning(capsys):
    """A table that is both incomplete and over-complete refuses; it does not warn and
    continue. Mutation: run the diagnostic before the coverage check."""
    table = {"layout": "dry", "environments": {"dev-us": {"region": "us-east-1"}}}
    with pytest.raises(SystemExit):
        _validate(table, matrix_envs=("dev-eu",), all_envs={"dev-eu"})
    assert capsys.readouterr().out == ""
