"""`env-config` reads the environment table from the default branch, never from the checkout.

The security property of the whole feature: values a pull request must not control are read
from `origin/<default>`, so the branch's own content cannot rewrite them. The ref-source test
runs real git against a real repository -- a mocked git passes with either ref -- and asserts
the marker in the table it parsed. If those are the default branch's bytes, everything
downstream can only see them.

The remaining tests fake the subprocess seam, and two of them compare the whole command
sequence against a hand-written constant: a partial check leaves the ref spelling open.

The no-checkout reader is here for the same reason: it reads the same file over the same
ref by a different mechanism, so the divergence guard below drives both over one fixture.
"""

import base64
import json
import os
import subprocess
import sys
import types

import pytest
from _loader import load_script

ec = load_script("env-config")

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


#: One fixture both readers are driven over. The multi-line string's indentation is the
#: part a transformation applied by one reader and not the other shows up in.
_SHARED_TEXT = (
    'layout = "dry"\n'
    "version = 1\n"
    "\n"
    "[environments.dev-eu]\n"
    'region = "eu-west-1"\n'
    'note = """\n'
    "  indented\n"
    '"""\n'
    "\n"
    "[env_order]\n"
    'prod = ["dev-eu"]\n'
)

#: Hand-written, never derived from `_SHARED_TEXT`: a derived expectation agrees with
#: whatever the parser did.
_SHARED_TABLE = {
    "layout": "dry",
    "version": 1,
    "environments": {"dev-eu": {"region": "eu-west-1", "note": "  indented\n"}},
    "env_order": {"prod": ["dev-eu"]},
}

#: The one explanation both refusals owe a consumer, spelled out here rather than read off
#: either reader.
_REFUSAL_STORY = (
    "The engine reads the environment table from the default branch, never from this "
    "branch, so the file must be merged there before the first plan."
)


def _blob(text, encoding="base64"):
    content = "" if text is None else base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"encoding": encoding, "content": content}


def _contents_run(text, recorder=None, encoding="base64"):
    """A `run` seam answering the default-branch query with `trunk` and the contents API
    with `text` as a blob. `gh api` output is text, so the blob is handed back as JSON."""

    def run(args, check=True):
        if recorder is not None:
            recorder.append((list(args), check))
        if "--jq" in args:
            return "trunk\n"
        return json.dumps(_blob(text, encoding))

    return run


def test_a_non_base64_answer_is_unreadable_not_empty():
    """A file over 1 MB answers with `encoding: "none"` and empty content. Reddens on
    accepting any encoding: that empty content decodes to a readable EMPTY file, which a
    caller reads as a repository whose settings are simply all absent."""
    path = "repos/an-org/a-repo/contents/x"
    assert ec.contents_text(path, fetch=lambda _p: _blob(None, "none")) is None


def test_contents_text_decodes_the_blob_to_its_exact_text():
    """Reddens on returning the blob as delivered: base64 of valid TOML is not valid TOML,
    and nothing between here and `parse_table` would notice on its own."""
    blob = _blob(_SHARED_TEXT)
    assert ec.contents_text("p", fetch=lambda _path: blob) == _SHARED_TEXT


def test_contents_text_leaves_a_leading_byte_order_mark_in_place():
    """`tomllib` refuses a U+FEFF and `git show` delivers one, so this reader must too, or
    two readers reach different verdicts on one file. Reddens on adding the
    U+FEFF `removeprefix` that `_workflow_text` needs and this must not have."""
    text = "﻿" + _SHARED_TEXT
    blob = _blob(text)
    assert ec.contents_text("p", fetch=lambda _path: blob) == text


def test_read_table_at_default_branch_runs_the_whole_command_sequence(monkeypatch):
    """The whole argv of every call, in order, against a hand-written constant. Reddens on
    any change to the ref, which is the point: a later change making the ref a parameter a
    caller can point at a feature branch would let branch content decide its own
    authorization."""
    _env(monkeypatch)
    calls = []
    ec.read_table_at_default_branch(run=_contents_run(_SHARED_TEXT, calls))
    assert calls == [
        (["gh", "api", "repos/an-org/a-repo", "--jq", ".default_branch"], True),
        (["gh", "api", "repos/an-org/a-repo/contents/.github/shipmate.toml?ref=trunk"], True),
    ]


def test_an_unreadable_file_refuses_with_read_tables_own_story(monkeypatch):
    """Reddens on returning `None` for an unreadable file: a caller then proceeds against a
    table nobody read. The explanation is asserted against `read_table`'s too -- one file,
    one story, whichever job hit the wall."""
    _env(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        ec.read_table_at_default_branch(run=_contents_run(None, encoding="none"))
    message = str(exc.value)
    assert message.startswith("::error::.github/shipmate.toml could not be read from the ")
    assert "default branch (trunk)" in message
    assert _REFUSAL_STORY in " ".join(message.split())

    git = types.SimpleNamespace(returncode=128, stdout="", stderr="fatal: no such path\n")
    with pytest.raises(SystemExit) as show_exc:
        ec.read_table(run=_fake_run(git=git))
    assert _REFUSAL_STORY in " ".join(str(show_exc.value).split())


def test_both_readers_return_the_same_table_for_the_same_bytes(monkeypatch):
    """The divergence guard. One fixture, two mechanisms, and the WHOLE parsed table
    compared to a hand-written constant on each side -- not a subset, not a key count.

    Reddens on any transformation one reader applies and the other does not: strip each
    line of the decoded text in `contents_text` and `note` loses its indentation here.
    """
    _env(monkeypatch)
    git = types.SimpleNamespace(returncode=0, stdout=_SHARED_TEXT, stderr="")
    assert ec.read_table(run=_fake_run(git=git)) == _SHARED_TABLE
    assert ec.read_table_at_default_branch(run=_contents_run(_SHARED_TEXT)) == _SHARED_TABLE


def _gate(**keys):
    """A validated-shaped table carrying one `[gate]` table. `_SHARED_TABLE` itself is the
    no-gate case, so the two differ only in the key under test."""
    return {**_SHARED_TABLE, "gate": keys}


def _warnings(capsys):
    """Only this module's own annotations, so a line of ordinary output cannot be counted
    as a warning and an emitted one cannot hide in it."""
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::warning::")]


def test_a_declared_ungated_envs_list_wins_over_the_variable(capsys):
    """The precedence the migration inverts if it is written the other way round: the file
    is the setting, the variable is what it replaces. Reddens on reading the variable first
    -- `prod` is then exempt and `sbx` is not -- and on warning about a fallback that did
    not happen."""
    assert ec.gate_ungated_envs(_gate(ungated_envs=["sbx"]), "prod") == frozenset({"sbx"})
    assert _warnings(capsys) == []


def test_a_declared_empty_ungated_envs_list_exempts_nothing(capsys):
    """The fail-open this task exists to avoid: `[]` is a declared empty list, not an
    absent key. Reddens on `gate.get("ungated_envs")` tested for truthiness, which reads
    the variable instead and hands back the exemptions an operator deliberately removed."""
    assert ec.gate_ungated_envs(_gate(ungated_envs=[]), "prod") == frozenset()
    assert _warnings(capsys) == []


def test_the_variable_is_read_when_the_file_declares_no_gate(capsys):
    """The migration release's fallback, casefolded the way the apply paths compare it.
    Reddens on returning an empty frozenset for an absent key, which exempts nothing and
    holds every environment of a consumer that has not migrated yet."""
    assert ec.gate_ungated_envs(_SHARED_TABLE, "Dev-EU,sbx") == frozenset({"dev-eu", "sbx"})
    assert len(_warnings(capsys)) == 1


def test_the_ungated_envs_fallback_warns_once_and_names_the_migration(capsys):
    """The only signal a consumer gets that it is still on the old mechanism. Reddens on
    dropping the warning, on emitting it per entry, and on a text that names neither the
    key that replaces the variable nor the fallback's removal."""
    ec.gate_ungated_envs(_SHARED_TABLE, "sbx,dev-eu")
    warnings = _warnings(capsys)
    assert len(warnings) == 1
    assert "gate.ungated_envs" in warnings[0]
    assert "SHIPMATE_UNGATED_ENVS" in warnings[0]
    assert "removes this fallback" in warnings[0]


def test_an_empty_ungated_envs_variable_warns_nothing(capsys):
    """A repository that never set the variable and has not yet added the key is
    mid-migration, not misconfigured. Reddens on warning unconditionally on the fallback
    path, which trains an operator to ignore the warning that does mean something."""
    assert ec.gate_ungated_envs(_SHARED_TABLE, "") == frozenset()
    assert _warnings(capsys) == []


def test_a_padded_variable_entry_is_refused_rather_than_silently_inert():
    """The variable is unvalidated input on this path, where the file's entries have been
    through `validate_structure`. Reddens on returning the entry, which matches no
    environment and so exempts nothing while reading as if it did."""
    with pytest.raises(SystemExit) as exc:
        ec.gate_ungated_envs(_SHARED_TABLE, "sbx, dev-eu")
    assert str(exc.value).startswith("::error::SHIPMATE_UNGATED_ENVS")


def test_a_declared_approvers_team_wins_over_the_input(capsys):
    """Same precedence over a string. Reddens on preferring the input, which authorizes
    comments against the team the repository migrated away from."""
    assert ec.gate_approvers_team(_gate(approvers_team="platform"), "old-team") == "platform"
    assert _warnings(capsys) == []


def test_the_approvers_team_input_is_read_when_the_file_declares_no_gate(capsys):
    """Reddens on dropping the warning, and on a text naming neither the key nor the
    variable the input carries."""
    assert ec.gate_approvers_team(_SHARED_TABLE, "old-team") == "old-team"
    warnings = _warnings(capsys)
    assert len(warnings) == 1
    assert "gate.approvers_team" in warnings[0]
    assert "SHIPMATE_APPROVERS_TEAM" in warnings[0]
    assert "removes this fallback" in warnings[0]


def test_an_empty_approvers_team_input_warns_nothing(capsys):
    """The team's half of the mid-migration case. Reddens on warning unconditionally."""
    assert ec.gate_approvers_team(_SHARED_TABLE, "") == ""
    assert _warnings(capsys) == []


def test_a_declared_empty_approvers_team_authorizes_nobody(capsys):
    """The `[]` fail-open on the other value: an empty slug is a declared empty team, which
    404s to `is_member=false` downstream, not an undeclared one. Reddens on
    `gate.get("approvers_team")` tested for truthiness, which falls back to the variable and
    authorizes comments against the team the repository just migrated away from. The
    fallback is non-empty deliberately -- an empty one passes under either reading."""
    assert ec.gate_approvers_team(_gate(approvers_team=""), "old-team") == ""
    assert _warnings(capsys) == []


def test_a_gate_declaring_one_key_still_falls_back_for_the_other(capsys):
    """The migration shape a repository lands mid-way: one key present, the other still on
    its variable. Reddens on testing the `[gate]` table's presence rather than the key's --
    the declared key then answers for both, so every exemption silently disappears and the
    warning that names the remaining migration goes quiet."""
    assert ec.gate_ungated_envs(_gate(approvers_team="platform"), "sbx") == frozenset({"sbx"})
    assert len(_warnings(capsys)) == 1
    assert ec.gate_approvers_team(_gate(ungated_envs=["sbx"]), "old-team") == "old-team"
    assert len(_warnings(capsys)) == 1
