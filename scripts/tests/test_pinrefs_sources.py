"""Source-location and tree-reading tests for the pin model.

The working-tree reader is the behavior test_internal_pins.py already relied on;
the commit reader is new and is what pin-status needs -- pins as they were at a
historical commit, not as they are on disk now.
"""

import pinrefs
import pytest

# Real history fixture used by test_refs_at_commit_reads_that_commits_tree_not_disk
# below. In a shallow clone this commit is absent, refs_at() on it returns [],
# and that test would pass vacuously ({} != now instead of a real diff). A bare
# module-level `assert` would raise at import time and abort collection of this
# whole module -- skip loudly instead so a shallow checkout doesn't turn an
# unrelated scripts/ edit's test run red. CI checks out with fetch-depth: 0,
# where this condition is always False.
CONVERGED = "83b37bb5e0baa9256b1ea49f4725cf7f55157c8a"
pytestmark = pytest.mark.skipif(
    not pinrefs.commit_present(CONVERGED),
    reason=(
        f"{CONVERGED[:12]} is not in this clone -- these tests read real history; "
        "check out with fetch-depth: 0"
    ),
)


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
    at_pin = {(path, sha) for path, sha, _src in pinrefs.refs_at(CONVERGED)}
    now = {(path, sha) for path, sha, _src in pinrefs.refs_at()}
    assert at_pin != now


def test_source_paths_raises_git_failure_when_ls_tree_fails(monkeypatch):
    # A failed `git ls-tree` and a commit with genuinely no pin-bearing files
    # both used to yield [] here -- indistinguishable to every caller. This
    # asserts ls-tree failing raises GitFailure rather than being swallowed
    # into an empty list.
    class _R:
        returncode = 128
        stdout = ""
        stderr = "fatal: bad object deadbeef\n"

    monkeypatch.setattr(pinrefs, "git", lambda *a: _R())

    with pytest.raises(pinrefs.GitFailure) as exc_info:
        pinrefs.source_paths("deadbeef")

    assert exc_info.value.commit == "deadbeef"
    assert "bad object deadbeef" in exc_info.value.stderr


def test_source_paths_working_tree_read_does_not_call_git(monkeypatch):
    # Only the commit-tree branch calls ls-tree; a working-tree read
    # (commit=None) must not touch git at all, so the GitFailure guard cannot
    # change its behavior.
    def fail(*_a):
        raise AssertionError("working-tree read must not invoke git")

    monkeypatch.setattr(pinrefs, "git", fail)
    assert pinrefs.source_paths()  # no raise
