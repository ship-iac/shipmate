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
    "::error::.github/shipmate.toml could not be read from the default branch. The engine "
    "reads the environment table from the default branch, never from this branch, so the "
    "file must be merged there before the first plan."
)


def _blob(text, encoding="base64"):
    content = "" if text is None else base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"encoding": encoding, "content": content}


def _fake_run(monkeypatch, text="layout = 'tf_vars'\n", recorder=None, encoding="base64"):
    """Replace `ec._run` with a double answering the contents API with `text` as a blob.
    `gh api` output is text, so the blob is handed back as JSON."""

    def run(args):
        if recorder is not None:
            recorder.append(list(args))
        return json.dumps(_blob(text, encoding))

    monkeypatch.setattr(ec, "_run", run)


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
    assert calls == [["gh", "api", "repos/an-org/a-repo/contents/.github/shipmate.toml"]]


def test_the_table_is_returned_as_parsed(monkeypatch):
    """Reddens on returning the raw stdout rather than the mapping `tomllib` parsed from it.
    The whole mapping is compared, so a partial parse reddens here too."""
    _env(monkeypatch)
    _fake_run(monkeypatch, 'layout = "tf_vars"\n\n[environments.dev-eu]\nregion = "eu-west-1"\n')
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
            "shipmate.toml\ngh: Not Found (HTTP 404)"
        )

    monkeypatch.setattr(ec, "_run", run)
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value) == _UNREADABLE
    assert capsys.readouterr().err == (
        "command failed (1): gh api repos/an-org/a-repo/contents/.github/shipmate.toml\n"
        "gh: Not Found (HTTP 404)\n"
    )


def test_a_non_base64_table_refuses(monkeypatch):
    """A file over 1 MB answers with `encoding: "none"` and empty content. Reddens on
    `contents_text` accepting any encoding: the empty content then parses as an empty table."""
    _env(monkeypatch)
    _fake_run(monkeypatch, None, encoding="none")
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value) == _UNREADABLE


def test_invalid_toml_refuses_with_the_decoder_line_number(monkeypatch):
    """Reddens on swallowing `TOMLDecodeError` and returning a mapping, and on a message that
    drops the decoder's own text: the line number is the only thing that locates the typo in a
    file the runner never shows."""
    _env(monkeypatch)
    _fake_run(monkeypatch, 'layout = "tf_vars"\nregion =\n')
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
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
        ec.parse_table('layout = "tf_vars"\n')
    message = str(exc.value)
    assert message.startswith("::error::")
    assert "3.11" in message
    assert "3.10.6" in message
    assert "Runner prerequisites" in message


def test_a_failing_gh_refuses(monkeypatch):
    """The real `_run` over `_shipmate.run`, with `subprocess.run` failing. The failed call
    still prints a valid table on stdout, so a runner that stopped checking the exit code
    would parse it. Reddens on `_shipmate.run` returning stdout regardless of the exit code.
    `CONTRACT.md` lists a failed contents read as a refusal."""
    _env(monkeypatch)
    stdout = json.dumps(_blob('layout = "tf_vars"\n')).encode()

    def fake_subprocess_run(args, capture_output=False, input=None):
        return types.SimpleNamespace(returncode=1, stdout=stdout, stderr=b"gh: boom\n")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    with pytest.raises(SystemExit) as exc:
        ec.read_table()
    assert str(exc.value) == _UNREADABLE


def test_run_annotates_the_shared_runners_failure(monkeypatch, capsys):
    """`_run` re-raises `_shipmate.run`'s failure with `::error::` prepended and prints
    nothing itself: the stderr rides in the message. Reddens on returning `run(args)` with no
    `except` (the prefix is gone), and on writing stderr before raising."""

    def fake_subprocess_run(args, capture_output=False, input=None):
        return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"gh: boom\n")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    with pytest.raises(SystemExit) as exc:
        ec._run(["gh", "api", "repos/an-org/a-repo"])
    assert str(exc.value) == "::error::command failed (1): gh api repos/an-org/a-repo\ngh: boom"
    assert capsys.readouterr().err == ""


#: The multi-line string's indentation is what a transformation of the decoded text
#: shows up in.
_SHARED_TEXT = (
    'layout = "tf_vars"\n'
    "\n"
    "[environments.dev-eu]\n"
    'region = "eu-west-1"\n'
    'note = """\n'
    "  indented\n"
    '"""\n'
    "\n"
    "[environments.prod]\n"
    'needs = ["dev-eu"]\n'
)


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
    """`tomllib` refuses a U+FEFF and the contents API delivers one, so the file refuses
    rather than parsing differently for one reader. Reddens on adding the
    U+FEFF `removeprefix` that `_workflow_text` needs and this must not have."""
    text = "﻿" + _SHARED_TEXT
    blob = _blob(text)
    assert ec.contents_text("p", fetch=lambda _path: blob) == text
