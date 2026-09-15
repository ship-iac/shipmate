"""`env-config` reads the environment table from the default branch, never from the checkout.

The security property of the whole feature: values a pull request must not control are read
from `origin/<default>`, so the branch's own content cannot rewrite them. The ref-source test
runs real git against a real repository -- a mocked git passes with either ref -- and asserts
the marker in the table it parsed. If those are the default branch's bytes, everything
downstream can only see them.

The remaining tests fake the subprocess seam, and one of them compares the whole command
sequence against a hand-written constant: a partial check leaves the ref spelling open.
"""

import os
import subprocess
import sys
import types

import pytest
from _loader import load_script

ec = load_script("env-config")
eo = load_script("env-order")

#: Four tables, one per ref the fixture holds, so any read of the wrong one is visible.
#: Each carries an `env_order` of its own as well as its marker: ordering is read off this
#: same mapping, so the ref that wins has to be visible in the ordering too.
_TABLE_A = 'marker = "default-branch"\n[env_order]\nprod = ["dev-eu"]\n'
_TABLE_B = 'marker = "branch-head"\n[env_order]\ndev-eu = ["prod"]\n'
_TABLE_C = 'marker = "working-tree"\n[env_order]\nprod = []\n'
_TABLE_D = 'marker = "other-remote"\n[env_order]\nprod = ["sbx"]\n'

_TABLE_FILE = ec.CONFIG_PATH


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


def _write_table(repo, table):
    path = repo / _TABLE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(table, encoding="utf-8")


def _commit_table(repo, env, table, message):
    _write_table(repo, table)
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
    _write_table(work, _TABLE_C)
    _git(work, env, "remote", "add", "other", str(other))
    _git(work, env, "fetch", "other")
    return work, env


def test_the_fixture_distinguishes_its_refs(repo):
    """Without this the fixture could degrade to one table on every ref, and the ref-source
    guard below would pass whichever ref `read_table()` read. Reddens on a fixture that
    commits the same table twice."""
    work, env = repo
    assert _show(work, env, f"origin/main:{_TABLE_FILE}") == _TABLE_A
    assert _show(work, env, f"HEAD:{_TABLE_FILE}") == _TABLE_B
    assert _show(work, env, f"other/main:{_TABLE_FILE}") == _TABLE_D
    assert (work / _TABLE_FILE).read_text(encoding="utf-8") == _TABLE_C
    assert len({_TABLE_A, _TABLE_B, _TABLE_C, _TABLE_D}) == 4


def test_read_table_reads_the_default_branch_not_the_checkout(repo, monkeypatch):
    """Real git, so the ref spelling is exercised rather than asserted. Reddens on reading the
    working-tree path, on `HEAD` in place of `origin/<branch>`, and on a ref spelled against a
    second remote -- each of those parses a different marker."""
    work, _ = repo
    monkeypatch.chdir(work)
    monkeypatch.setattr(ec, "_default_branch", lambda run: "main")
    assert ec.read_table()["marker"] == "default-branch"


def test_the_apply_ordering_comes_from_the_default_branch_table(repo, monkeypatch):
    """The behaviour change this loader makes: `env_order` now comes from the default branch,
    so a pull request cannot reorder its own apply waves. Real git, and every ref in the
    fixture carries a different ordering -- the branch head inverts it -- so a read of the
    checkout returns {"dev-eu": ["prod"]} here rather than an empty map that an
    "it did not raise" assertion would accept.

    Mutation: spell `read_table`'s ref `HEAD:` instead of `origin/<branch>:`.
    """
    work, _ = repo
    monkeypatch.chdir(work)
    monkeypatch.setattr(ec, "_default_branch", lambda run: "main")
    assert ec.read_table()["env_order"] == {"prod": ["dev-eu"]}


def _fake_run(recorder=None, git=None):
    """A `run` seam recording `(args, check)`, answering `gh` with a branch name and `git`
    with a CompletedProcess-shaped result.

    The branch is `trunk`, not `main`: the ref assertions below would otherwise be satisfied
    by a `read_table` that hardcoded `origin/main` and left `_default_branch` dangling. The
    real-git fixture keeps `main`, which is the branch git actually creates there, and the two
    disagreeing is what makes such a hardcode visible in whichever guard sees it.
    """
    result = git or types.SimpleNamespace(returncode=0, stdout="layout = 'dry'\n", stderr="")

    def run(args, check=True):
        if recorder is not None:
            recorder.append((list(args), check))
        return "trunk\n" if args[0] == "gh" else result

    return run


def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "an-org/a-repo")


def test_read_table_runs_the_whole_command_sequence(monkeypatch):
    """The whole argv of every call, in order, against a hand-written constant -- never a
    substring, and never derived from the module. Reddens on any change to the ref or the
    path, on a dropped or reordered call, and on `check=True` for the tolerated `git show`,
    whose failure this module reports itself."""
    _env(monkeypatch)
    calls = []
    ec.read_table(run=_fake_run(calls))
    assert calls == [
        (["gh", "api", "repos/an-org/a-repo", "--jq", ".default_branch"], True),
        (["git", "show", "origin/trunk:.github/shipmate.toml"], False),
    ]


def test_the_table_is_returned_as_parsed(monkeypatch):
    """Reddens on returning the raw stdout rather than the mapping `tomllib` parsed from it.
    The whole mapping is compared, so a partial parse reddens here too."""
    _env(monkeypatch)
    text = 'layout = "dry"\n\n[environments.dev-eu]\nregion = "eu-west-1"\n'
    git = types.SimpleNamespace(returncode=0, stdout=text, stderr="")
    assert ec.read_table(run=_fake_run(git=git)) == {
        "layout": "dry",
        "environments": {"dev-eu": {"region": "eu-west-1"}},
    }


def test_an_absent_file_refuses(monkeypatch, capsys):
    """`git show` exits nonzero when the path is not on the default branch. Reddens on
    returning an empty mapping, which would read as a repository that declares no table and
    reach `validate` as a missing-layout message naming the wrong cause."""
    _env(monkeypatch)
    git = types.SimpleNamespace(
        returncode=128, stdout="", stderr="fatal: path does not exist in 'origin/trunk'\n"
    )
    with pytest.raises(SystemExit) as exc:
        ec.read_table(run=_fake_run(git=git))
    message = str(exc.value)
    assert message.startswith("::error::")
    assert ".github/shipmate.toml" in message
    assert "default branch" in message
    assert "origin/trunk:.github/shipmate.toml" in message
    # git's own reason, or CI shows only the engine's guess at it.
    assert "fatal: path does not exist" in capsys.readouterr().err


def test_invalid_toml_refuses_with_the_decoder_line_number(monkeypatch):
    """Reddens on swallowing `TOMLDecodeError` and returning a mapping, and on a message that
    drops the decoder's own text: the line number is the only thing that locates the typo in a
    file the runner never shows."""
    _env(monkeypatch)
    git = types.SimpleNamespace(returncode=0, stdout='layout = "dry"\nregion =\n', stderr="")
    with pytest.raises(SystemExit) as exc:
        ec.read_table(run=_fake_run(git=git))
    message = str(exc.value)
    assert message.startswith("::error::.github/shipmate.toml is not valid TOML")
    assert "line 2" in message


def test_an_interpreter_below_the_floor_refuses_before_the_import(monkeypatch):
    """`tomllib` is standard-library from 3.11 only, and nothing in the engine pins a Python.
    Reddens on dropping the check: `tomllib` is then absent rather than reported, so an older
    `runs_on` image fails with a bare `ModuleNotFoundError` at `detect`. The import is removed
    here too, so a check that runs after it cannot pass."""
    monkeypatch.setattr(sys, "version_info", (3, 10, 6, "final", 0))
    monkeypatch.setitem(sys.modules, "tomllib", None)
    with pytest.raises(SystemExit) as exc:
        ec.parse_table('layout = "dry"\n')
    message = str(exc.value)
    assert message.startswith("::error::")
    assert "3.11" in message
    assert "3.10.6" in message
    assert "Runner prerequisites" in message


def test_a_failing_gh_refuses(monkeypatch):
    """`_run` itself, which every other test in this module replaces with a fake. Reddens on a
    `_run` that warns and returns `stdout` instead of raising: a failed `gh api` still prints a
    usable-looking branch name, so the run would carry on against a ref resolved from a guess.
    `CONTRACT.md` lists a failed `gh api` as a refusal."""
    _env(monkeypatch)

    def fake_subprocess_run(args, capture_output=False, text=False, env=None):
        if args[0] == "gh":
            return types.SimpleNamespace(returncode=1, stdout="trunk\n", stderr="gh: boom\n")
        # Valid TOML, so a `_run` that stopped refusing would produce a table here rather than
        # redden this test for an unrelated reason.
        return types.SimpleNamespace(returncode=0, stdout='layout = "dry"\n', stderr="")

    monkeypatch.setattr(ec.subprocess, "run", fake_subprocess_run)
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value).startswith("::error::")


def test_run_forwards_its_env_to_the_subprocess(monkeypatch):
    """The `env` argument reaches `subprocess.run`, which no caller-side test can show:
    `build-matrix`'s run.env probe stubs `_run` itself, so it pins that the probe *passes*
    an environment, not that this wrapper hands it on. Dropping it silently reverts the
    probe to the ambient environment, where every sentinel comparison passes.

    Mutation: delete `env=env` from the `subprocess.run` call -- `seen` becomes None."""
    seen = []

    def fake_subprocess_run(args, capture_output=False, text=False, env=None):
        seen.append(env)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ec.subprocess, "run", fake_subprocess_run)
    ec._run(["gh", "api", "repos/an-org/a-repo"], env={"TF_VAR_env": "SHIPMATE_RT_PROBE"})
    assert seen == [{"TF_VAR_env": "SHIPMATE_RT_PROBE"}]
