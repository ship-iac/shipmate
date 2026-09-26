"""`scripts/env-inject`'s check that `terramate.config.run.env` leaves the table's values alone.

The stubbed cases hand `check_run_env` a `run` that returns a hand-written `terramate run` result,
so each refusal is compared whole. One case runs the real `terramate` against a two-stack repo;
it skips where `terramate` is not on `PATH`, which is CI's shape.
"""

import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from _loader import load_script

env_inject = load_script("env-inject")

#: Hand-written; the script's `_REPORT` must print exactly this program.
REPORT = "import json, os, sys; print(json.dumps({n: os.environ.get(n) for n in sys.argv[1:]}))"

TABLE = {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}
ENVIRON = {"STACK": "stacks/app", "HOME": "/home/runner"}
PAIRS = {**TABLE, "TF_VAR_size": "small", "API_KEY": "s3cr3t"}

OVERRIDE = (
    "::error::terramate.config.run.env sets TF_VAR_env for stacks/app to 'prod', and the "
    "environment table resolves 'dev-eu'. The table decides a cell's identity: plan and apply "
    "would both run under the rewritten value, and the fingerprint, computed outside "
    "`terramate run`, would agree. Stop assigning TF_VAR_env in run.env, or read it first: "
    'tm_try(env.TF_VAR_env, "<local default>"). See CONTRACT.md §Env model.'
)


class _Run:
    """A `subprocess.run` double that records each call and returns one fixed result."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.calls = []
        self.result = SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)

    def __call__(self, args, capture_output=False, text=False, env=None):
        self.calls.append(
            {"args": args, "capture_output": capture_output, "text": text, "env": env}
        )
        return self.result


def _reporting(values):
    return _Run(stdout=json.dumps(values) + "\n")


def _refusal(table=TABLE, pairs=PAIRS, environ=ENVIRON, run=None):
    with pytest.raises(SystemExit) as excinfo:
        env_inject.check_run_env(table, pairs, environ, run=run)
    return str(excinfo.value)


def test_a_changed_value_refuses_naming_both_values():
    """Mutations: compare `==` in place of `!=`; swap the reported and table values in the
    refusal's format arguments.
    """
    run = _reporting({"TF_VAR_env": "prod", "TF_VAR_region": "eu-west-1"})
    assert _refusal(run=run) == OVERRIDE


@pytest.mark.parametrize(
    "report",
    [{"TF_VAR_env": "dev-eu", "TF_VAR_region": None}, {"TF_VAR_env": "dev-eu"}],
    ids=["null", "missing"],
)
def test_an_absent_name_refuses_showing_unset(report):
    """The child reports an unset name as `null`; a report missing the key refuses the same way.

    Mutation: `reported.get(name, value)`, which fills the table's value in for the missing key.
    """
    run = _reporting(report)
    assert _refusal(run=run) == (
        "::error::terramate.config.run.env sets TF_VAR_region for stacks/app to (unset), and the "
        "environment table resolves 'eu-west-1'. The table decides a cell's identity: plan and "
        "apply would both run under the rewritten value, and the fingerprint, computed outside "
        "`terramate run`, would agree. Stop assigning TF_VAR_region in run.env, or read it first: "
        'tm_try(env.TF_VAR_region, "<local default>"). See CONTRACT.md §Env model.'
    )


def test_a_vars_only_name_is_checked():
    """The table's own `vars` are in the checked set, not only the layout's derived names.

    Mutation: loop over `("TF_VAR_env", "TF_VAR_region", "TF_WORKSPACE")` instead of the table.
    """
    table = {**TABLE, "TF_VAR_account": "123"}
    run = _reporting({**TABLE, "TF_VAR_account": "999"})
    assert _refusal(table=table, run=run) == (
        "::error::terramate.config.run.env sets TF_VAR_account for stacks/app to '999', and the "
        "environment table resolves '123'. The table decides a cell's identity: plan and apply "
        "would both run under the rewritten value, and the fingerprint, computed outside "
        "`terramate run`, would agree. Stop assigning TF_VAR_account in run.env, or read it "
        'first: tm_try(env.TF_VAR_account, "<local default>"). See CONTRACT.md §Env model.'
    )


def test_a_failing_terramate_run_refuses_and_passes_its_stderr_through_raw(capsys):
    """`init` would fail on the same `run.env` one step later, so nothing is gained by continuing.
    Terramate's stderr reaches the log byte for byte, so a mask registered for a secret holding a
    tab or a double space still matches it.

    Mutations: `return` in place of the non-zero-exit raise; the refusal carrying
    `' '.join(p.stderr.split())` again; delete the `sys.stderr.write`.
    """
    stderr = "Error: evaluating run.env\n  unknown variable env.X\tvalue  a  b\n"
    run = _Run(returncode=1, stderr=stderr)
    assert _refusal(run=run) == (
        "::error::terramate run in stacks/app exited 1 while checking "
        "terramate.config.run.env. Terramate's output is above."
    )
    assert capsys.readouterr() == ("", stderr)


@pytest.mark.parametrize("stdout", ["not json\n", '["TF_VAR_env"]\n'], ids=["unparseable", "list"])
def test_a_report_that_is_not_a_json_object_refuses(stdout):
    """Mutation: `return {}` in place of `_parse_report`'s raise, which refuses with the
    `(unset)` text instead of naming the broken report.
    """
    assert _refusal(run=_Run(stdout=stdout)) == (
        "::error::terramate run in stacks/app did not print the JSON object env-inject asked "
        "for, so terramate.config.run.env cannot be checked against the environment table."
    )


def test_a_name_outside_the_table_is_neither_asked_for_nor_checked():
    """`TF_WORKSPACE` under `dry` and `folder` is the consumer's to set in `run.env`.

    Mutation: append `"TF_WORKSPACE"` to the names the child is asked for.
    """
    run = _reporting({**TABLE, "TF_WORKSPACE": "rewritten"})
    env_inject.check_run_env(TABLE, PAIRS, ENVIRON, run=run)
    assert [call["args"][-3:] for call in run.calls] == [[REPORT, "TF_VAR_env", "TF_VAR_region"]]


def test_an_empty_table_runs_nothing_even_without_a_stack():
    """`layout = "folder"` with no `vars` has nothing to check.

    Mutation: delete the `if not table: return`, which refuses on the empty `STACK`.
    """

    def run(*args, **kwargs):
        pytest.fail("terramate ran for an empty table")

    env_inject.check_run_env({}, {"TF_VAR_size": "small"}, {"HOME": "/home/runner"}, run=run)


def test_an_empty_stack_refuses():
    """GitHub does not enforce a composite input's `required: true`.

    Mutation: `stack = environ.get("STACK", ".")`, which checks the repository root instead.
    """
    run = _reporting(TABLE)
    assert _refusal(environ={"HOME": "/home/runner"}, run=run) == (
        "::error::STACK is empty, and this cell's environment table resolves "
        "TF_VAR_env, TF_VAR_region. The cell step must pass the stack directory as STACK, "
        "so terramate.config.run.env can be checked against the table."
    )
    assert run.calls == []


def test_the_child_runs_the_engine_command_under_the_cells_environment():
    """Mutations: drop `--no-recursive`; drop `**pairs` from the child environment.

    `sys.executable` is absolute, so a `run.env` rewriting `PATH` cannot swap the interpreter.
    """
    run = _reporting(TABLE)
    env_inject.check_run_env(TABLE, PAIRS, ENVIRON, run=run)
    assert run.calls == [
        {
            "args": [
                "terramate",
                "run",
                "--disable-safeguards=git-out-of-sync",
                "--no-recursive",
                "-C",
                "stacks/app",
                "--",
                sys.executable,
                "-c",
                REPORT,
                "TF_VAR_env",
                "TF_VAR_region",
            ],
            "capture_output": True,
            "text": True,
            "env": {
                "STACK": "stacks/app",
                "HOME": "/home/runner",
                "TF_VAR_env": "dev-eu",
                "TF_VAR_region": "eu-west-1",
                "TF_VAR_size": "small",
                "API_KEY": "s3cr3t",
            },
        }
    ]


def test_the_expectation_is_the_parents_table_not_the_childs():
    """`run.env` can rewrite `SHIPMATE_TF_VARS` as easily as `TF_VAR_env`.

    Mutation: read the expected values from the child's reported `SHIPMATE_TF_VARS`.
    """
    rewritten = {"TF_VAR_env": "prod", "TF_VAR_region": "eu-west-1"}
    run = _reporting({**rewritten, "SHIPMATE_TF_VARS": json.dumps(rewritten)})
    assert _refusal(run=run) == OVERRIDE


def test_a_passing_check_prints_nothing(capsys):
    """Mutation: `print(p.stdout)` after the run."""
    env_inject.check_run_env(TABLE, PAIRS, ENVIRON, run=_reporting(TABLE))
    assert capsys.readouterr().out == ""


def test_main_checks_the_resolved_table_after_masking_and_writing(tmp_path, monkeypatch, capsys):
    """The check runs `terramate`, which can echo a secret, so the masks are registered first;
    the cell's environment is already written when it runs.

    Mutations: delete the `check_run_env` call from `main`; move it above `mask`.
    """
    path = tmp_path / "github_env"
    calls = []

    def fake_check_run_env(table, pairs, environ, run=subprocess.run):
        written = path.read_text(encoding="utf-8") if path.exists() else None
        calls.append((table, pairs, environ is os.environ, capsys.readouterr().out, written))

    monkeypatch.setattr(env_inject, "check_run_env", fake_check_run_env)
    monkeypatch.setenv("GITHUB_ENV", str(path))
    monkeypatch.setenv("SHIPMATE_TF_VARS", '{"TF_VAR_env": "dev-eu"}')
    monkeypatch.setenv("SHIPMATE_GITHUB_VARS", '{"TF_VAR_SIZE": "small"}')
    monkeypatch.setenv("SHIPMATE_SECRETS", '{"API_KEY": "s3cr3t"}')
    env_inject.main()
    assert calls == [
        (
            {"TF_VAR_env": "dev-eu"},
            {"TF_VAR_env": "dev-eu", "TF_VAR_size": "small", "API_KEY": "s3cr3t"},
            True,
            "::add-mask::s3cr3t\n",
            "API_KEY<<SHIPMATE_EOF\ns3cr3t\nSHIPMATE_EOF\n"
            "TF_VAR_env<<SHIPMATE_EOF\ndev-eu\nSHIPMATE_EOF\n"
            "TF_VAR_size<<SHIPMATE_EOF\nsmall\nSHIPMATE_EOF\n",
        )
    ]


_ROOT_TM = """terramate {
  config {
    run {
      env {
        TF_VAR_env = terramate.stack.name == "b" ? "prod" : env.TF_VAR_env
      }
    }
  }
}
"""


def _git_env(tmp_path):
    """Git's environment with the machine's own config out of the way."""
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


@pytest.mark.skipif(shutil.which("terramate") is None, reason="terramate not installed")
def test_real_terramate_refuses_only_the_stack_its_run_env_rewrites(tmp_path, monkeypatch):
    """A stack-conditional `run.env`: stack `a` passes the ambient value through, `b` rewrites it.

    Mutation: `continue` for every name in the refusal loop, which lets `b` pass.
    """
    repo = tmp_path / "repo"
    for path, text in {
        "terramate.tm.hcl": _ROOT_TM,
        "a/stack.tm.hcl": 'stack {\n  name = "a"\n}\n',
        "b/stack.tm.hcl": 'stack {\n  name = "b"\n}\n',
    }.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(text.encode())
    env = _git_env(tmp_path)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True)
    monkeypatch.chdir(repo)
    table = {"TF_VAR_env": "dev-eu"}

    env_inject.check_run_env(table, table, {**env, "STACK": "a"})
    with pytest.raises(SystemExit) as excinfo:
        env_inject.check_run_env(table, table, {**env, "STACK": "b"})
    assert str(excinfo.value) == OVERRIDE.replace("stacks/app", "b")
