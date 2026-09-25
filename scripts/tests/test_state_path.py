import json
import os
import shutil
import subprocess

import pytest
from _loader import load_script

sp = load_script("state-path")

STACK = "envs/dev-eu/eu-west-1/app"


def _local(**config):
    return {"backend": {"type": "local", "config": {"path": None, "workspace_dir": None, **config}}}


DERIVATION = [
    (None, "", "envs/dev-eu/eu-west-1/app/terraform.tfstate"),
    (None, "default", "envs/dev-eu/eu-west-1/app/terraform.tfstate"),
    (None, "dev-eu", "envs/dev-eu/eu-west-1/app/terraform.tfstate.d/dev-eu/terraform.tfstate"),
    (_local(), "", "envs/dev-eu/eu-west-1/app/terraform.tfstate"),
    (_local(), "default", "envs/dev-eu/eu-west-1/app/terraform.tfstate"),
    (_local(), "dev-eu", "envs/dev-eu/eu-west-1/app/terraform.tfstate.d/dev-eu/terraform.tfstate"),
    (
        _local(path=".state/dev-eu/terraform.tfstate"),
        "",
        "envs/dev-eu/eu-west-1/app/.state/dev-eu/terraform.tfstate",
    ),
    (
        _local(path=".state/dev-eu/terraform.tfstate"),
        "default",
        "envs/dev-eu/eu-west-1/app/.state/dev-eu/terraform.tfstate",
    ),
    (
        _local(path="custom/x.tfstate"),
        "dev-eu",
        "envs/dev-eu/eu-west-1/app/terraform.tfstate.d/dev-eu/terraform.tfstate",
    ),
    (
        _local(workspace_dir="wsd"),
        "dev-eu",
        "envs/dev-eu/eu-west-1/app/wsd/dev-eu/terraform.tfstate",
    ),
    (_local(path="./terraform.tfstate"), "", "envs/dev-eu/eu-west-1/app/terraform.tfstate"),
    (_local(path="sub//x.tfstate"), "", "envs/dev-eu/eu-west-1/app/sub/x.tfstate"),
    (_local(path="terraform.tfstate"), "", "envs/dev-eu/eu-west-1/app/terraform.tfstate"),
    ({"backend": {"type": "s3", "config": {"bucket": "b"}}}, "", ""),
    ({"backend": {"type": "s3", "config": {"bucket": "b"}}}, "dev-eu", ""),
]


def test_derivation_table():
    """Mutations: an absent record read as "" reddens the None rows; dropping the named-workspace
    branch reddens the dev-eu rows; prefixing the output with `./` reddens the folders row."""
    got = [(record, ws, sp.state_path(STACK, record, ws)) for record, ws, _ in DERIVATION]
    assert got == DERIVATION


def test_folders_path_is_byte_identical_to_the_old_suffix():
    """`actions/cache` keys on the path string, so the folders sample must keep this exact value.
    Mutation: prefixing the output with `./` reddens it."""
    assert sp.state_path(STACK, _local(path="terraform.tfstate"), "") == (
        "envs/dev-eu/eu-west-1/app/terraform.tfstate"
    )


REFUSALS = [
    {},
    {"backend": None},
    {"backend": {"config": {}}},
    {"backend": {"type": None, "config": {}}},
    {"backend": {"type": 3, "config": {}}},
    {"backend": {"type": "", "config": {}}},
    {"backend": {"type": "  ", "config": {}}},
    {"backend": {"type": "local"}},
    _local(path=3),
    _local(workspace_dir=3),
    _local(path="/etc/x"),
    _local(path="../x"),
    _local(path="a/../../x"),
    _local(path="a\nb"),
    _local(path="!x"),
    _local(path="*.tfstate"),
    [],
    "local",
]


@pytest.mark.parametrize("record", REFUSALS, ids=repr)
def test_refuses_unrecognized_shapes(record):
    """Mutations: a `backend.get("type") != "local"` check returns "" for the missing-type rows;
    dropping `.strip()` or the truthiness test returns "" for the empty-type rows."""
    with pytest.raises(SystemExit) as exc:
        sp.state_path(STACK, record, "")
    assert str(exc.value).startswith(f"::error::{STACK}: ")


def test_refuses_a_named_workspace_leaving_the_stack():
    """Mutation: dropping the `..` / `../` escape check lets `../../dev-eu/...` through."""
    with pytest.raises(SystemExit):
        sp.state_path(STACK, _local(workspace_dir="../.."), "dev-eu")


def _run_main(tmp_path, monkeypatch, env):
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for name in ("TF_DATA_DIR", "TF_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STACK", STACK)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    sp.main()
    return out.read_text(encoding="utf-8")


def _write_record(directory, content):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "terraform.tfstate").write_text(content, encoding="utf-8")


def test_main_writes_exactly_the_path(tmp_path, monkeypatch):
    """Mutation: prefixing the output with `./` reddens this."""
    _write_record(tmp_path / ".terraform", json.dumps(_local(path=".state/x.tfstate")))
    got = _run_main(tmp_path, monkeypatch, {})
    assert got == "path=envs/dev-eu/eu-west-1/app/.state/x.tfstate\n"


def test_main_reads_the_record_from_tf_data_dir(tmp_path, monkeypatch):
    """Mutation: hard-coding `.terraform` reads the decoy and reddens this."""
    _write_record(tmp_path / ".terraform", json.dumps(_local(path="decoy.tfstate")))
    _write_record(tmp_path / "dd", json.dumps(_local(path=".state/x.tfstate")))
    got = _run_main(tmp_path, monkeypatch, {"TF_DATA_DIR": "dd"})
    assert got == "path=envs/dev-eu/eu-west-1/app/.state/x.tfstate\n"


def test_main_without_a_record_writes_the_default_path(tmp_path, monkeypatch):
    """Mutation: reading an absent record as "" (the fail-open) reddens this."""
    got = _run_main(tmp_path, monkeypatch, {"TF_WORKSPACE": "dev-eu"})
    assert got == "path=envs/dev-eu/eu-west-1/app/terraform.tfstate.d/dev-eu/terraform.tfstate\n"


def test_main_writes_empty_for_a_remote_backend(tmp_path, monkeypatch):
    """Mutation: deleting the non-local `return ""` derives a local path for s3 and reddens this."""
    _write_record(tmp_path / ".terraform", json.dumps({"backend": {"type": "s3", "config": {}}}))
    assert _run_main(tmp_path, monkeypatch, {}) == "path=\n"


@pytest.mark.parametrize("content", ["{not json", "null", "[]", '"local"', ""])
def test_main_refuses_an_unreadable_record_and_writes_nothing(tmp_path, monkeypatch, content):
    """A JSON `null` read as "no record" would pick the default local path over a real one.
    Mutation: deleting the top-level object check reddens the `null` row."""
    _write_record(tmp_path / ".terraform", content)
    with pytest.raises(SystemExit) as exc:
        _run_main(tmp_path, monkeypatch, {})
    assert str(exc.value).startswith(f"::error::{STACK}: ")
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == ""


def test_main_refuses_a_record_it_cannot_open(tmp_path, monkeypatch):
    """A directory where the record belongs. Mutation: catching only ValueError lets the OSError
    escape as a traceback instead of an `::error::` naming the stack."""
    (tmp_path / ".terraform" / "terraform.tfstate").mkdir(parents=True)
    with pytest.raises(SystemExit) as exc:
        _run_main(tmp_path, monkeypatch, {})
    assert str(exc.value).startswith(f"::error::{STACK}: ")
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == ""


def test_main_refuses_an_empty_stack(tmp_path, monkeypatch):
    """Mutation: deleting the empty-STACK check reddens this."""
    with pytest.raises(SystemExit):
        _run_main(tmp_path, monkeypatch, {"STACK": ""})
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == ""


_FOLDERS = 'terraform {\n  backend "local" {\n    path = "terraform.tfstate"\n  }\n}\n'
_WORKSPACES = 'terraform {\n  backend "local" {}\n}\n'
_STACKS = (
    'variable "env" {\n  type = string\n}\n'
    'terraform {\n  backend "local" {\n    path = ".state/${var.env}/terraform.tfstate"\n  }\n}\n'
)

REAL_TOOLCHAIN = [
    ("folders", _FOLDERS, {}, "path=envs/dev-eu/eu-west-1/app/terraform.tfstate\n"),
    (
        "workspaces",
        _WORKSPACES,
        {"TF_WORKSPACE": "dev-eu"},
        "path=envs/dev-eu/eu-west-1/app/terraform.tfstate.d/dev-eu/terraform.tfstate\n",
    ),
    (
        "stacks",
        _STACKS,
        {"TF_VAR_env": "dev-eu"},
        "path=envs/dev-eu/eu-west-1/app/.state/dev-eu/terraform.tfstate\n",
    ),
    (
        "stacks-data-dir",
        _STACKS,
        {"TF_VAR_env": "dev-eu", "TF_DATA_DIR": "dd"},
        "path=envs/dev-eu/eu-west-1/app/.state/dev-eu/terraform.tfstate\n",
    ),
    (
        "workspaces-empty-workspace",
        _WORKSPACES,
        {"TF_WORKSPACE": ""},
        "path=envs/dev-eu/eu-west-1/app/terraform.tfstate\n",
    ),
]


@pytest.mark.skipif(shutil.which("tofu") is None, reason="tofu is not on PATH")
def test_real_tofu_init_records_what_main_reads(tmp_path, monkeypatch):
    """The stacks-data-dir row reads `dd/`: a helper that ignores TF_DATA_DIR finds no record and
    writes `terraform.tfstate` instead of the `.state/` path."""
    got = []
    for name, config, env, _ in REAL_TOOLCHAIN:
        stack_dir = tmp_path / name
        stack_dir.mkdir()
        (stack_dir / "main.tf").write_text(config, encoding="utf-8")
        run_env = {k: v for k, v in os.environ.items() if not k.startswith("TF_")}
        subprocess.run(
            [shutil.which("tofu"), "init", "-input=false", "-no-color"],
            cwd=stack_dir,
            env={**run_env, **env},
            check=True,
            capture_output=True,
        )
        got.append((name, config, env, _run_main(stack_dir, monkeypatch, env)))
    assert got == REAL_TOOLCHAIN
