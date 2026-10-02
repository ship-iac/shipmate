"""No tracked engine file but this one names GitHub's author association.

Comment authorization is the commenter's repository permission, read and decided once in
`comment-ops`. GitHub's author association classifies the author and checks no permission, so a
step gating on it, or a doc offering it as a gate, is a second reading of the policy. Either
reddens here.

Pinned spellings, case-sensitive: `author_association` (REST and the event payload),
`authorAssociation` (GraphQL), and `COLLABORATOR` as a whole word, which every allowlist of the
association's values (`OWNER|MEMBER|COLLABORATOR`, `OWNER`, `MEMBER` or `COLLABORATOR`) carries.
Lowercase `collaborators/` is the permission endpoint and does not match.

Tracked files only (`git ls-files`), so `.venv` and untracked work never count. A listed path that
is not a regular file in the working tree (deleted, renamed, a submodule) is skipped. Every file
read must decode as UTF-8, against a hand-written set of binary files, so a file the scan cannot
read is never silently skipped; more than 100 must be listed, so an empty listing cannot pass. A
missing `git` fails the guard rather than skipping it.

Mutations: `author_association` added to a sentence in `docs/concepts.md` reds, naming the file;
`authorAssociation` added to a comment in `actions/comment-ops/action.yml` reds; `OWNER|MEMBER|
COLLABORATOR` added to `CONTRACT.md` reds; `git ls-files` run in an empty `git init` directory
reds on the floor; dropping the `is_file()` skip while `docs/aws.md` is deleted reds on
`FileNotFoundError`; `_git()` skipping, or returning a bare `git`, instead of failing reds
`test_a_missing_git_fails_the_guard`.
"""

import os
import re
import shutil
import subprocess

import pytest
from _loader import ENGINE

#: Tracked files that are not UTF-8 text. None today.
BINARY = set()
OWN = "scripts/tests/test_no_author_association.py"
ASSOCIATION = re.compile(r"author_association|authorAssociation|\bCOLLABORATOR\b")


def _git():
    git = shutil.which("git")
    if git is None:
        pytest.fail("git is not on PATH, so the association guard cannot list tracked files")
    return git


def _tracked(root):
    listing = subprocess.run(  # noqa: S603 - fixed argv, no input
        [_git(), "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    return [p for p in listing.decode("utf-8").split("\0") if p]


def test_no_tracked_file_names_author_association():
    paths = _tracked(ENGINE)
    assert len(paths) > 100
    undecodable, naming = set(), []
    for path in paths:
        if not (ENGINE / path).is_file():
            continue
        try:
            text = (ENGINE / path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            undecodable.add(path)
            continue
        if ASSOCIATION.search(text) and path != OWN:
            naming.append(path)
    assert undecodable == BINARY
    assert naming == [], naming


def test_a_missing_git_fails_the_guard(monkeypatch):
    def which(cmd, mode=os.F_OK | os.X_OK, path=None):
        return None

    monkeypatch.setattr(shutil, "which", which)
    # BaseException, because pytest's skip is one too: a skip must red here, not pass as one.
    with pytest.raises(BaseException) as raised:
        _git()
    assert raised.type is pytest.fail.Exception, raised.type
    assert "git is not on PATH" in str(raised.value)
