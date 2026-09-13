"""The two `env-config` diagnostics that report rather than refuse.

Everything else this script does refuses. These two do not, and that asymmetry is the
subject: an unused table entry warns because the table is read from the default branch
while the environment list comes from the feature branch, so adding or removing an
environment is a two-pull-request sequence and refusing the unused entry alongside the
missing one would deadlock both directions. The migration notice warns because a
repository with no `layout` is a supported configuration, not a broken one.

Reddens on: turning either warning into a refusal, deleting either print, firing the
unused-entry warning where no whole-tree scan exists, gating the missing-entry refusal on
that scan, and printing the migration notice once per cell instead of once per detect.

Messages are compared whole against hand-written literals: an operator reading a warning
in a run log has no other source, so the text is part of the contract.
"""

import pytest
from _loader import load_script

bm = load_script("build-matrix")
env_config = load_script("env-config")

#: Hand-written, not imported from the script: a constant derived from the file it checks
#: passes whatever the file says.
MIGRATION_NOTICE = (
    "::warning::this repository takes its environment identity from GitHub variables, "
    'which the `globals "shipmate"` environment table replaces. Declare a layout to '
    "migrate; the variables keep working until you do."
)
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


# --- 4: the migration notice, once per detect -----------------------------------------


def test_a_repository_with_no_layout_is_told_once_what_replaces_its_variables(monkeypatch, capsys):
    """State 1 of the migration runs unchanged and says so once per detect run, not once per
    cell: a forty-cell repository printing it forty times teaches people to ignore it.

    Mutations: delete the print; and move it into the per-cell path, which this test's three
    cells turn into three lines."""
    monkeypatch.setenv("SHIPMATE_SHARED_ENVS", "")
    monkeypatch.setattr(bm.ec, "read_table", lambda run=None: {})
    cells = [
        {"stack": f"stacks/{n}", "environment": "dev-eu", "workload": "", "workload_var": ""}
        for n in ("app", "db", "net")
    ]
    table, shared = bm.env_config(cells)
    rows = bm.stamp_rows(cells, table, "plan", shared)
    assert [r["config_mode"] for r in rows] == ["legacy", "legacy", "legacy"]
    assert capsys.readouterr().out.splitlines() == [MIGRATION_NOTICE]


def test_a_migrated_repository_gets_no_migration_notice(capsys):
    """Mutation: print the notice unconditionally. The sibling above is what keeps this one
    from passing with the print deleted."""
    _validate(TABLE, matrix_envs=("dev-eu",), all_envs={"dev-eu", "dev-us"})
    assert MIGRATION_NOTICE not in capsys.readouterr().out
