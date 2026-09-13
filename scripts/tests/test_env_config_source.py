"""`env-config` reads the environment table from the default branch, never from the checkout.

The security property of the whole feature: values a pull request must not control are read
from `origin/<default>`, so the branch's own content cannot rewrite them. The `worktree()`
tests run real git against a real repository -- a mocked git passes with either ref -- and
never reach terramate, because CI installs `uv` and actionlint and nothing else. What they
assert is the file inside the returned worktree path: if those are the default branch's bytes,
the evaluation that follows can only see them.

The `evaluate()` / `read_table()` tests fake the subprocess seam. `-C <path>` is the hop
between the two halves: drop it and terramate evaluates the pull request checkout while every
other property here stays green.
"""

import json
import os
import pathlib
import subprocess
import types

import pytest
from _loader import load_script

ec = load_script("env-config")

#: Four tables, one per ref the fixture holds, so any read of the wrong one is visible.
_TABLE_A = 'globals "shipmate" {\n  marker = "default-branch"\n}\n'
_TABLE_B = 'globals "shipmate" {\n  marker = "branch-head"\n}\n'
_TABLE_C = 'globals "shipmate" {\n  marker = "working-tree"\n}\n'
_TABLE_D = 'globals "shipmate" {\n  marker = "other-remote"\n}\n'

_TABLE_FILE = "shipmate.tm.hcl"

#: stdout each tool answers with when the fake is not making it fail.
_OK_STDOUT = {"gh": "main\n", "git": "", "terramate": "{}"}


def _git_env(tmp_path):
    """Git's environment with the machine's own config out of the way.

    A global `commit.gpgsign`, `core.hooksPath` or identity would otherwise decide whether
    the fixture's commits happen at all.
    """
    cfg = tmp_path / "gitconfig"
    cfg.write_text("", encoding="utf-8")
    return dict(
        os.environ,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(cfg),
        GIT_AUTHOR_NAME="shipmate tests",
        GIT_AUTHOR_EMAIL="tests@example.invalid",
        GIT_COMMITTER_NAME="shipmate tests",
        GIT_COMMITTER_EMAIL="tests@example.invalid",
    )


def _git(cwd, env, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, check=True, capture_output=True, text=True
    ).stdout


def _commit_table(repo, env, table, message):
    (repo / _TABLE_FILE).write_text(table, encoding="utf-8")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-m", message)


def _init(root, env, table):
    root.mkdir()
    _git(root, env, "init", "-b", "main")
    _git(root, env, "config", "core.autocrlf", "false")
    _commit_table(root, env, table, "table")
    return root


def _show(repo, env, ref):
    return _git(repo, env, "show", ref)


@pytest.fixture
def repo(tmp_path):
    """A clone whose `origin/main` carries table A, whose `HEAD` carries table B, whose
    working tree carries table C, and which has a second remote `other` carrying table D."""
    env = _git_env(tmp_path)
    origin = _init(tmp_path / "origin", env, _TABLE_A)
    other = _init(tmp_path / "other", env, _TABLE_D)
    work = tmp_path / "work"
    _git(tmp_path, env, "clone", str(origin), str(work))
    _git(work, env, "config", "core.autocrlf", "false")
    _commit_table(work, env, _TABLE_B, "branch head")
    (work / _TABLE_FILE).write_text(_TABLE_C, encoding="utf-8")
    _git(work, env, "remote", "add", "other", str(other))
    _git(work, env, "fetch", "other")
    return work, env


def _worktree_table(repo_dir, monkeypatch, branch="main", name="wt"):
    """`worktree()` against the fixture, returning the table file it put on disk."""
    monkeypatch.chdir(repo_dir)
    path = str(repo_dir.parent / name)
    returned = ec.worktree(branch, path, ec._run)
    return (pathlib.Path(returned) / _TABLE_FILE).read_text(encoding="utf-8")


def test_the_fixture_distinguishes_its_refs(repo):
    """Without this the fixture could degrade to one table on every ref, and properties 1-3
    below would pass whichever ref `worktree()` read. Reddens on a fixture that commits the
    same table twice."""
    work, env = repo
    assert _show(work, env, f"origin/main:{_TABLE_FILE}") == _TABLE_A
    assert _show(work, env, f"HEAD:{_TABLE_FILE}") == _TABLE_B
    assert _show(work, env, f"other/main:{_TABLE_FILE}") == _TABLE_D
    assert (work / _TABLE_FILE).read_text(encoding="utf-8") == _TABLE_C
    assert len({_TABLE_A, _TABLE_B, _TABLE_C, _TABLE_D}) == 4


def test_worktree_holds_the_default_branch_bytes(repo, monkeypatch):
    """Reddens on `HEAD` in place of `origin/<branch>`: the worktree would then hold the
    pull request's own table."""
    work, _ = repo
    assert _worktree_table(work, monkeypatch) == _TABLE_A


def test_worktree_reads_origin_not_another_remote(repo, monkeypatch):
    """Reddens on a ref spelled against the second remote (`other/<branch>`)."""
    work, _ = repo
    assert _worktree_table(work, monkeypatch) == _TABLE_A


def test_worktree_ignores_the_working_tree(repo, monkeypatch):
    """Table C sits uncommitted in the checkout. Reddens on copying the working tree instead
    of adding a worktree."""
    work, _ = repo
    assert _worktree_table(work, monkeypatch) == _TABLE_A


def test_worktree_recovers_a_reused_path(repo, monkeypatch):
    """A runner reusing its workspace calls this twice against one path. Reddens on dropping
    the cleanup (git refuses an existing path) and on `shutil.rmtree` alone, which leaves the
    registration under `.git/worktrees/` and makes `add` refuse a missing-but-registered
    path."""
    work, _ = repo
    assert _worktree_table(work, monkeypatch) == _TABLE_A
    assert _worktree_table(work, monkeypatch) == _TABLE_A


def _fake_run(recorder=None, stdout=None):
    """A `run` seam that records `(args, check)` and answers from `stdout` by tool name."""
    answers = dict(_OK_STDOUT, **(stdout or {}))

    def run(args, check=True):
        if recorder is not None:
            recorder.append((list(args), check))
        return answers[args[0]]

    return run


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", "an-org/a-repo")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))


def test_read_table_runs_the_whole_command_sequence(monkeypatch, tmp_path):
    """The whole argv of every call, in order, against a hand-written constant.

    The `-C` argument is the hop from `worktree()`'s bytes to what terramate reads: without
    it terramate evaluates the pull request checkout, and every other property here stays
    green. Reddens on dropping `-C`, on `-C .`, on a dropped or reordered git call, and on
    `check=True` for the tolerated removal."""
    _env(monkeypatch, tmp_path)
    calls = []
    path = os.path.join(str(tmp_path), "shipmate-envcfg")
    ec.read_table(run=_fake_run(calls))
    assert calls == [
        (["gh", "api", "repos/an-org/a-repo", "--jq", ".default_branch"], True),
        (["git", "worktree", "remove", "--force", path], False),
        (["git", "worktree", "prune"], True),
        (["git", "worktree", "add", "--detach", path, "origin/main"], True),
        (
            [
                "terramate",
                "experimental",
                "eval",
                "--as-json",
                "-C",
                path,
                "tm_try(global.shipmate, {})",
            ],
            True,
        ),
    ]


def test_evaluate_reads_the_path_worktree_returned(monkeypatch, tmp_path):
    """`-C` carries what `worktree()` returned, not a second computation of the same value.
    Reddens on recomputing the path in `read_table` and passing that to `evaluate`, which the
    sequence guard above cannot see because both spellings agree there."""
    _env(monkeypatch, tmp_path)
    elsewhere = str(tmp_path / "somewhere-else")
    monkeypatch.setattr(ec, "worktree", lambda branch, path, run: elsewhere)
    calls = []
    ec.read_table(run=_fake_run(calls))
    assert calls[-1] == (
        [
            "terramate",
            "experimental",
            "eval",
            "--as-json",
            "-C",
            elsewhere,
            "tm_try(global.shipmate, {})",
        ],
        True,
    )


def test_an_absent_table_is_not_a_refusal(monkeypatch, tmp_path):
    """`tm_try` answers `{}` for a repository with no table, which is the majority. Reddens on
    raising when the global is missing."""
    _env(monkeypatch, tmp_path)
    assert ec.read_table(run=_fake_run(stdout={"terramate": "{}"})) == {}


def test_the_table_is_returned_as_parsed(monkeypatch, tmp_path):
    """Reddens on returning the raw stdout, or on dropping part of the parsed table."""
    _env(monkeypatch, tmp_path)
    table = {"env": {"dev-eu": {"region": "eu-west-1"}}}
    assert ec.read_table(run=_fake_run(stdout={"terramate": json.dumps(table)})) == table


#: (argv prefix that fails, the stdout it still prints). Each stdout is usable, so replacing
#: the refusal with a warning that returns stdout produces a table rather than a red test.
_FAILING = [
    (["gh"], "main\n"),
    (["git", "worktree", "add"], ""),
    (["terramate"], '{"env": {"dev-eu": {}}}'),
]


@pytest.mark.parametrize("prefix,stdout", _FAILING, ids=["gh", "git-worktree-add", "terramate"])
def test_a_failing_tool_refuses(prefix, stdout, monkeypatch, tmp_path):
    """Every sourcing failure refuses. Reddens on warning and returning instead of raising --
    the pattern `build-matrix`'s `assert_run_env_roundtrip()` uses deliberately, which here
    would hand a transient failure back to branch content."""
    _env(monkeypatch, tmp_path)

    def fake_subprocess_run(args, capture_output=False, text=False):
        if list(args[: len(prefix)]) == prefix:
            return types.SimpleNamespace(returncode=1, stdout=stdout, stderr="boom\n")
        return types.SimpleNamespace(returncode=0, stdout=_OK_STDOUT[args[0]], stderr="")

    monkeypatch.setattr(ec.subprocess, "run", fake_subprocess_run)
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value).startswith("::error::")


def test_unparseable_output_refuses(monkeypatch, tmp_path):
    """Reddens on returning `{}` for output that is not JSON: a table that could not be read
    is not a repository without one."""
    _env(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exc:
        ec.read_table(run=_fake_run(stdout={"terramate": "not json"}))
    assert str(exc.value).startswith("::error::")


@pytest.mark.parametrize("out", ['"oops"', "null", "[]"], ids=["string", "null", "list"])
def test_a_table_that_is_not_a_mapping_refuses(out, monkeypatch, tmp_path):
    """`globals "shipmate"` holding a scalar is JSON, so the parse succeeds and every reader
    downstream assumes a mapping. Reddens on returning the parsed scalar, which reaches
    `validate` as `'str' object has no attribute 'get'`."""
    _env(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exc:
        ec.read_table(run=_fake_run(stdout={"terramate": out}))
    assert str(exc.value).startswith("::error::")
