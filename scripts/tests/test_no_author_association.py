"""No tracked engine file but this one names `author_association`.

Comment authorization is the commenter's repository permission, read and decided once in
`comment-ops`. GitHub's `author_association` classifies the author and checks no permission, so a
step gating on it, or a doc offering it as a gate, is a second reading of the policy. Either
reddens here.

Tracked files only (`git ls-files`), so `.venv` and untracked work never count. Every listed file
must decode as UTF-8, against a hand-written set of binary files, so a file the scan cannot read
is never silently skipped; more than 100 must be listed, so an empty listing cannot pass.

Mutations: `author_association` added to a sentence in `docs/concepts.md` reds, naming the file;
added to a comment in `actions/comment-ops/action.yml` reds; `git ls-files` run in an empty
`git init` directory reds on the floor.
"""

import shutil
import subprocess

import pytest
from _loader import ENGINE

GIT = shutil.which("git")
if GIT is None:
    pytest.skip("git is not on PATH", allow_module_level=True)

#: Tracked files that are not UTF-8 text. None today.
BINARY = set()
OWN = "scripts/tests/test_no_author_association.py"


def _tracked(root):
    listing = subprocess.run(  # noqa: S603 - fixed argv, no input
        [GIT, "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    return [p for p in listing.decode("utf-8").split("\0") if p]


def test_no_tracked_file_names_author_association():
    paths = _tracked(ENGINE)
    assert len(paths) > 100
    undecodable, naming = set(), []
    for path in paths:
        try:
            text = (ENGINE / path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            undecodable.add(path)
            continue
        if "author_association" in text and path != OWN:
            naming.append(path)
    assert undecodable == BINARY
    assert naming == [], naming
