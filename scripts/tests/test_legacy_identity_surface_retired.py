"""The two-mode identity surface is gone from the shipped trees and stays gone.

A cell's identity and cloud credentials come from the environment table in
`.github/shipmate.toml` and from nothing else. The mode selector that chose between
that table and a set of GitHub variables, and the `AWS_ROLE_ARN_<WORKLOAD>`
name-mangling rule that the variable arm needed, were removed outright rather
than deprecated: there are no consumers to migrate, so nothing may reintroduce
either name by accident.

Scope is the shipped trees — `scripts/`, `actions/`, `.github/workflows/`.
Deliberately not `scripts/tests/`, `CHANGELOG.md`, `docs/` or `CONTRACT.md`:
release history and the upgrade note must be able to name what was retired, and
`test_aws_oidc_wiring_guard.py` names `vars.AWS_ROLE_ARN` as the mutation it
reds on.

The gate settings the migration release read from `SHIPMATE_APPROVERS_TEAM` and
`SHIPMATE_UNGATED_ENVS` are retired the same way and for the same reason: the
fallback is gone, so a shipped file naming either variable is reading a source
no consumer sets and no resolver consults.

`SHIPMATE_SHARED_ENVS` is retired the same way: `shared = true` in an environment's
table entry replaced it.

The trees are walked by directory listing, never by a `*.py` glob: the helpers
under `scripts/` carry no extension, and a glob that reaches none of them
reports a clean tree for a tree it never read. The reach test below is what
keeps a broken walk from passing by reading nothing.
"""

from _loader import ACTIONS, ENGINE, SCRIPTS, WORKFLOWS

#: Every name the table-only model, the gate-fallback removal and the shared-environment key
#: retired, hand-written.
#: `config-mode` is the action-input spelling of `config_mode`. `AWS_ROLE_ARN` is the bare
#: name, not a prefix: nothing shipped reads any variable of that name, and
#: `test_aws_oidc_wiring_guard.py` owns it as the mutation its wiring must red on.
RETIRED = (
    "config_mode",
    "config-mode",
    "SHIPMATE_CONFIG_MODE",
    "SHIPMATE_LEGACY",
    "workload_var",
    "guard_workload_var_collisions",
    "AWS_ROLE_ARN",
    "SHIPMATE_APPROVERS_TEAM",
    "SHIPMATE_UNGATED_ENVS",
    "SHIPMATE_SHARED_ENVS",
)


#: Excluded by path, not by directory name: a `tests` name match would silently drop a
#: directory called `tests` anywhere in the three trees, and a scan that skips a directory
#: without saying so is the failure `test_the_scan_reaches_all_three_trees` exists to catch.
_EXCLUDED = (SCRIPTS / "tests",)


def _shipped_files():
    """Every file of the three shipped trees, as repo-relative paths, by directory walk."""
    trees = (SCRIPTS, ACTIONS, WORKFLOWS)
    return {
        p.relative_to(ENGINE).as_posix(): p
        for tree in trees
        for p in sorted(tree.rglob("*"))
        if p.is_file()
        and "__pycache__" not in p.parts
        and not any(p.is_relative_to(d) for d in _EXCLUDED)
    }


def test_the_scan_reaches_all_three_trees():
    """Without this the retired-name test passes on an empty scan. One extension-less
    helper, one action manifest and one workflow, so a walk that drops any tree — or
    that filters out the extension-less helpers — reds here rather than silently.

    Mutation: filter `_shipped_files` to `p.suffix == ".py"`.
    """
    found = _shipped_files()
    for path in ("scripts/build-matrix", "actions/build-matrix/action.yml"):
        assert path in found, f"the scan did not reach {path}: {sorted(found)[:10]}"
    assert ".github/workflows/plan.yml" in found


def test_no_shipped_file_names_a_retired_identity_symbol():
    """Mutation: write `config_mode` into any file under the three trees."""
    hits = {
        f"{path}:{name}"
        for path, p in _shipped_files().items()
        for name in RETIRED
        if name in p.read_text(encoding="utf-8", errors="replace")
    }
    assert hits == set(), f"retired identity symbols are back: {sorted(hits)}"
