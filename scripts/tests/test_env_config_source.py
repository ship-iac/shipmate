"""`env-config` reads the environment table from the default branch, never from the checkout.

The security property of the whole feature: values a pull request must not control are read
from the default branch, so the branch's own content cannot rewrite them. `read_table` takes no
parameter and reads the contents API with no `?ref=`, which GitHub answers from the default
branch. The whole-argv test below pins that request against a hand-written constant; GitHub's
answer to it was probed live and cannot be exercised by a unit test.

The remaining tests fake `_run`, the one subprocess seam, with its real signature.
"""

import base64
import json
import subprocess
import sys
import types

import pytest
from _loader import load_script

ec = load_script("env-config")

#: The whole refusal, hand-written rather than read off the module.
_UNREADABLE = (
    "::error::.github/shipmate-config.yml could not be read from the default branch. The engine "
    "reads the environment table from the default branch, never from this branch, so the "
    "file must be merged there before the first plan."
)


def _blob(text, encoding="base64"):
    content = "" if text is None else base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"encoding": encoding, "content": content}


def _fake_run(monkeypatch, text="layout: tf_vars\n", recorder=None, encoding="base64"):
    """Replace `_shipmate._run`, `gh_json`'s default runner, with a double answering the
    contents API with `text` as a blob. `gh api` output is text, so the blob is handed back as
    JSON."""

    def run(args):
        if recorder is not None:
            recorder.append(list(args))
        return json.dumps(_blob(text, encoding))

    monkeypatch.setattr(sys.modules["_shipmate"], "_run", run)


def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "an-org/a-repo")


def test_read_table_runs_the_whole_command_sequence(monkeypatch):
    """The whole argv of every call, in order, against a hand-written constant -- never a
    substring, and never derived from the module. Reddens on appending `?ref=HEAD` to the
    path, and on a `gh api repos/{repo} --jq .default_branch` call re-added before the read."""
    _env(monkeypatch)
    calls = []
    _fake_run(monkeypatch, recorder=calls)
    ec.read_table()
    assert calls == [["gh", "api", "repos/an-org/a-repo/contents/.github/shipmate-config.yml"]]


def test_the_table_is_returned_as_parsed(monkeypatch):
    """Reddens on returning the raw stdout rather than the mapping parsed from it. The whole
    mapping is compared, so a partial parse reddens here too."""
    _env(monkeypatch)
    _fake_run(monkeypatch, "layout: tf_vars\n\nenvironments:\n  dev-eu:\n    region: eu-west-1\n")
    assert ec.read_table() == {
        "layout": "tf_vars",
        "environments": {"dev-eu": {"region": "eu-west-1"}},
    }


def test_an_absent_file_refuses(monkeypatch, capsys):
    """A failed contents read refuses with the fixed refusal, and `gh`'s own reason reaches
    stderr without its `::error::` prefix. Reddens on dropping the stderr write, and on
    `read_table` returning `{}` when `contents_text` gives `None`: an absent file would then
    read as an empty table and reach `validate` as a missing-layout message naming the wrong
    cause."""
    _env(monkeypatch)

    def run(args):
        raise SystemExit(
            "::error::command failed (1): gh api repos/an-org/a-repo/contents/.github/"
            "shipmate-config.yml\ngh: Not Found (HTTP 404)"
        )

    monkeypatch.setattr(sys.modules["_shipmate"], "_run", run)
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value) == _UNREADABLE
    assert capsys.readouterr().err == (
        "command failed (1): gh api repos/an-org/a-repo/contents/.github/shipmate-config.yml\n"
        "gh: Not Found (HTTP 404)\n"
    )


def test_a_missing_gh_refuses_with_its_reason_on_stderr(monkeypatch, capsys):
    """A fetch failing with something other than `SystemExit` still writes its reason.
    Reddens on narrowing `_reporting_fetch`'s `except` to `SystemExit`: `contents_text`
    then swallows the `FileNotFoundError` and the refusal arrives with no reason."""
    _env(monkeypatch)

    def run(args):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(sys.modules["_shipmate"], "_run", run)
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value) == _UNREADABLE
    assert capsys.readouterr().err == (
        "FileNotFoundError: [Errno 2] No such file or directory: 'gh'\n"
    )


def test_a_logged_failure_starts_no_line_with_a_workflow_command(capsys):
    """gh's stderr rides in the message, so any line may start with `::`. Mutation: drop
    the `re.sub` in `log_failure` -- the second line stays `::error::...` and the runner
    would read it as a command."""
    ec.log_failure(SystemExit("::error::command failed (1): gh api x\n::error::injected"))
    assert capsys.readouterr().err == "command failed (1): gh api x\n: :error::injected\n"


def test_a_logged_failure_names_a_non_systemexit_type(capsys):
    """A `KeyError`'s message is only the key. Mutation: drop the type prefix in
    `log_failure` -- stderr reads `doctor: 'name'`, naming no failure."""
    ec.log_failure(KeyError("name"), "doctor")
    assert capsys.readouterr().err == "doctor: KeyError: 'name'\n"


def test_a_non_base64_table_refuses(monkeypatch):
    """A file over 1 MB answers with `encoding: "none"` and empty content. Reddens on
    `contents_text` accepting any encoding: the empty content then parses as an empty table."""
    _env(monkeypatch)
    _fake_run(monkeypatch, None, encoding="none")
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value) == _UNREADABLE


_DUPLICATE = "layout: tf_vars\nenvironments:\n  dev-eu:\n    region: eu-west-1\n  dev-eu: {}\n"


def test_invalid_yaml_refuses_with_the_parser_line_and_column(monkeypatch):
    """The whole refusal, one line. Mutation: catch `YAMLError` and re-raise `str(exc)` -- the
    `is not valid YAML` prefix goes and the parser's multi-line text arrives instead. The line
    and column are the only things that locate the typo in a file the runner never shows."""
    _env(monkeypatch)
    _fake_run(monkeypatch, _DUPLICATE)
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value) == (
        "::error::.github/shipmate-config.yml is not valid YAML: duplicate key 'dev-eu' "
        "(line 5, column 3)"
    )


def test_a_non_mapping_root_refuses_naming_its_type():
    """Mutation: delete the root check in `load_config` -- `references` then calls `.items()`
    on a list and raises `AttributeError`, which no caller reports as a refusal."""
    with pytest.raises(SystemExit) as exc:
        ec.parse_table("- a\n")
    assert str(exc.value) == (
        "::error::.github/shipmate-config.yml must hold a mapping at the top level, got list"
    )


def test_the_config_path_is_the_yaml_file():
    """Every reader takes the path from here. Mutation: set `CONFIG_PATH` to
    `.github/shipmate.yml`, the consumer's workflow file."""
    assert ec.CONFIG_PATH == ".github/shipmate-config.yml"


_FLOOR_REFUSAL = (
    "::error::the engine needs Python 3.12 or later; this runner has 3.11.9. CONTRACT.md "
    "section Runner prerequisites requires python3 >= 3.12 on every runner. Choose a newer "
    "runs_on image."
)
_PYYAML_REFUSAL = (
    "::error::the engine needs PyYAML for python3 and this runner has none. Install "
    "python3-yaml (Debian or Ubuntu) or PyYAML >= 6 for this runner's python3; CONTRACT.md "
    "section Runner prerequisites lists it."
)


def test_an_interpreter_below_the_floor_refuses_before_the_parser_is_looked_up(monkeypatch):
    """Nothing in the engine pins a Python, so an older `runs_on` image must be named rather
    than fail inside the parser. PyYAML is absent too, so the floor must be checked first.
    Mutations: `_MIN_PYTHON = (3, 11)`, or check PyYAML before the floor -- either way the
    PyYAML refusal arrives instead."""
    monkeypatch.setattr(sys, "version_info", (3, 11, 9, "final", 0))
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(SystemExit) as exc:
        ec.parse_table("layout: tf_vars\n")
    assert str(exc.value) == _FLOOR_REFUSAL


def test_a_runner_without_pyyaml_refuses_naming_the_prerequisite(monkeypatch):
    """Mutation: drop the PyYAML lookup from `runner_refusal` -- `import yaml` then raises
    `ImportError` unconverted, and no refusal names the runner prerequisite."""
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(SystemExit) as exc:
        ec.parse_table("layout: tf_vars\n")
    assert str(exc.value) == _PYYAML_REFUSAL


def test_a_failing_gh_refuses(monkeypatch):
    """The real `_run` over `_shipmate.run`, with `subprocess.run` failing. The failed call
    still prints a valid table on stdout, so a runner that stopped checking the exit code
    would parse it. Reddens on `_shipmate.run` returning stdout regardless of the exit code.
    `CONTRACT.md` lists a failed contents read as a refusal."""
    _env(monkeypatch)
    stdout = json.dumps(_blob("layout: tf_vars\n")).encode()

    def fake_subprocess_run(args, capture_output=False, input=None):
        return types.SimpleNamespace(returncode=1, stdout=stdout, stderr=b"gh: boom\n")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value) == _UNREADABLE


#: The block scalar's indentation is what a transformation of the decoded text shows up in.
_SHARED_TEXT = (
    "layout: tf_vars\n"
    "\n"
    "environments:\n"
    "  dev-eu:\n"
    "    region: eu-west-1\n"
    "    note: |\n"
    "      indented\n"
    "\n"
    "  prod:\n"
    "    needs: [dev-eu]\n"
)


def test_a_non_base64_answer_is_unreadable_not_empty():
    """A file over 1 MB answers with `encoding: "none"` and empty content. Reddens on
    accepting any encoding: that empty content decodes to a readable EMPTY file, which a
    caller reads as a repository whose settings are simply all absent."""
    path = "repos/an-org/a-repo/contents/x"
    assert ec.contents_text(path, fetch=lambda _p: _blob(None, "none")) is None


def test_contents_text_decodes_the_blob_to_its_exact_text():
    """Reddens on returning the blob as delivered: base64 of a valid config is not a valid
    config, and nothing between here and `parse_table` would notice on its own."""
    blob = _blob(_SHARED_TEXT)
    assert ec.contents_text("p", fetch=lambda _path: blob) == _SHARED_TEXT


def test_contents_text_leaves_a_leading_byte_order_mark_in_place():
    """The decoded text is returned byte for byte, a leading U+FEFF included: the parser
    reads past it. Reddens on adding a `removeprefix` here."""
    text = "\ufeff" + _SHARED_TEXT
    blob = _blob(text)
    assert ec.contents_text("p", fetch=lambda _path: blob) == text
