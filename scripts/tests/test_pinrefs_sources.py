"""Source-location and tree-reading tests for the pin model.

The working-tree reader is the behavior test_internal_pins.py already relied on;
the commit reader is new and is what pin-status needs -- pins as they were at a
historical commit, not as they are on disk now.
"""

import pinrefs
import pytest


def test_source_paths_finds_workflows_and_action_yamls():
    paths = pinrefs.source_paths()
    assert ".github/workflows/apply-env-level.yml" in paths
    assert "actions/apply-cell/action.yml" in paths
    # Nested and non-yml files must not be collected.
    assert all(p.endswith(".yml") for p in paths)
    assert all(p.count("/") == 2 for p in paths)


def test_source_paths_at_commit_matches_working_tree_on_head():
    head = pinrefs.git("rev-parse", "HEAD").stdout.strip()
    assert pinrefs.source_paths(head) == pinrefs.source_paths()


def test_source_paths_honours_an_alternate_root(tmp_path):
    # The re-pin tools reuse this against a fixture directory rather than
    # carrying a second copy of the glob.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "plan.yml").write_text("", encoding="utf-8")
    # .yaml is equally valid to GitHub; a workflow that escaped the guard by
    # spelling its extension differently is the silent hole this model prevents.
    (tmp_path / ".github" / "workflows" / "drift.yaml").write_text("", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "releasing.md").write_text("", encoding="utf-8")

    assert pinrefs.source_paths(root=tmp_path) == [
        ".github/workflows/drift.yaml",
        ".github/workflows/plan.yml",
    ]


def test_source_paths_rejects_root_together_with_a_commit():
    # Silently ignoring root would hand the caller another repo's pins.
    head = pinrefs.git("rev-parse", "HEAD").stdout.strip()
    with pytest.raises(ValueError, match="working-tree read"):
        pinrefs.source_paths(head, root=pinrefs.ROOT)


def test_refs_at_working_tree_finds_the_apply_env_level_pin():
    refs = pinrefs.refs_at()
    pinned = {path for path, _sha, _src in refs}
    assert ".github/workflows/apply-env-level.yml" in pinned
    assert "actions/setup" in pinned


def test_refs_at_commit_reads_that_commits_tree_not_disk():
    # 83b37bb is a convergence commit whose apply-env-level.yml pin differs from
    # today's. Reading it must yield that commit's SHA, not the working tree's.
    at_pin = {
        (path, sha)
        for path, sha, _src in pinrefs.refs_at("83b37bb5e0baa9256b1ea49f4725cf7f55157c8a")
    }
    now = {(path, sha) for path, sha, _src in pinrefs.refs_at()}
    assert at_pin != now
