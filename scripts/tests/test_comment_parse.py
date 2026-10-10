import pathlib
import shutil
import subprocess
import sys

import pytest
from _loader import load_script

cp = load_script("comment-parse")

_RUN_URL = "https://github.com/org/repo/actions/runs/7777"


def test_valid_apply():
    r = cp.parse("shipmate apply dev-eu")
    assert r == {
        "is_command": True,
        "valid": True,
        "verb": "apply",
        "env": "dev-eu",
        "route": "apply",
        "error": None,
    }


def test_leading_trailing_whitespace_and_crlf():
    r = cp.parse("\r\n  shipmate apply dev-eu  \r\n")
    assert r["valid"] and r["env"] == "dev-eu"


def test_command_on_first_matching_line_of_multiline():
    r = cp.parse("thanks!\nshipmate apply dev-us\n/cc @team")
    assert r["valid"] and r["env"] == "dev-us"


def test_plan_rejects_an_env():
    """`plan` takes no arguments on purpose: there is one plan of record per head, and a run
    holding one environment's artifacts leaves every other environment's workset empty at the
    next apply.

    Fails when `args` becomes `"[env]"`, which accepts this line and ships exactly that."""
    r = cp.parse("shipmate plan dev-eu")
    assert r["is_command"] and not r["valid"] and r["verb"] == "plan"
    assert "takes no arguments" in r["error"]
    assert r["route"] is None


def test_doctor_rejects_an_env():
    r = cp.parse("shipmate doctor dev-eu")
    assert not r["valid"] and "takes no arguments" in r["error"]


def test_help_rejects_an_env():
    r = cp.parse("shipmate help dev-eu")
    assert not r["valid"] and "takes no arguments" in r["error"]


def test_unknown_verb_is_rejected():
    r = cp.parse("shipmate frobnicate dev-eu")
    assert r["is_command"] and not r["valid"] and "unknown verb" in r["error"]


def test_bare_apply_targets_all_envs():
    # env is optional: a bare `shipmate apply` applies every non-explicit env.
    r = cp.parse("shipmate apply")
    assert r == {
        "is_command": True,
        "valid": True,
        "verb": "apply",
        "env": None,
        "route": "apply",
        "error": None,
    }


def test_bare_apply_with_whitespace_and_crlf():
    r = cp.parse("\r\n  shipmate apply  \r\n")
    assert r["valid"] and r["env"] is None


def test_bare_plan_is_valid():
    r = cp.parse("shipmate plan")
    assert r == {
        "is_command": True,
        "valid": True,
        "verb": "plan",
        "env": None,
        "route": "plan",
        "error": None,
    }


def test_bare_unknown_verb_rejected():
    r = cp.parse("shipmate frobnicate")
    assert r["is_command"] and not r["valid"] and "unknown verb" in r["error"]


def test_injection_attempt_is_rejected():
    r = cp.parse("shipmate apply dev-eu; rm -rf /")
    assert r["is_command"] and not r["valid"]


def test_backtick_injection_in_env_rejected():
    r = cp.parse("shipmate apply $(whoami)")
    assert r["is_command"] and not r["valid"]


def test_non_command_comment_is_not_a_command():
    r = cp.parse("LGTM, merging after CI")
    assert not r["is_command"] and not r["valid"]


def test_shipmatey_prefix_is_not_a_command():
    # 'shipmate' must be a whole word, not a prefix of another word.
    r = cp.parse("shipmatey apply dev-eu")
    assert not r["is_command"]


_MALFORMED = "malformed: expected `shipmate <verb> [env]` (try `shipmate help`)"


@pytest.mark.parametrize(
    "body",
    [
        "shipmate apply dev-eu foo",
        "shipmate apply DEV-EU",
        "shipmate unlock DEV-EU",
        "shipmate apply dev_eu",
        "shipmate plan Foo",
    ],
)
def test_a_token_outside_the_grammar_is_malformed(body):
    """The env charset is lowercase letters, digits and `-`, and nothing may follow the env, so
    these fail the grammar outright. Pinned as a deliberate choice: the user sees the malformed
    error with its help hint, not a missing or invalid env one. `dev_eu` is a valid environment
    name that a comment cannot target.

    Mutation: restore the `(?: (?P<tag>[A-Za-z0-9][A-Za-z0-9/_:.-]*))?` group in `_CMD`, and
    every case parses and gets a verb-specific error instead.
    """
    r = cp.parse(body)
    assert (r["is_command"], r["valid"], r["route"], r["error"]) == (True, False, None, _MALFORMED)


def test_destroy_is_an_unknown_verb():
    """Mutation: re-add `destroy` to `VERBS`, and it gets a route or a verb-specific error."""
    r = cp.parse("shipmate destroy")
    assert (r["is_command"], r["valid"], r["route"], r["error"]) == (
        True,
        False,
        None,
        "unknown verb `destroy` (try `shipmate help`)",
    )


def test_earlier_shipmate_prefixed_chatter_does_not_block_later_valid_command():
    # A prior shipmate-prefixed line that is not a recognized command, an unknown verb here,
    # must not win over a later line that is a full, valid command.
    r = cp.parse("shipmate is great\nshipmate apply dev-eu")
    assert r == {
        "is_command": True,
        "valid": True,
        "verb": "apply",
        "env": "dev-eu",
        "route": "apply",
        "error": None,
    }


def test_pure_garbage_shipmate_line_still_errors():
    r = cp.parse("shipmate is great")
    assert r["is_command"] and not r["valid"] and r["error"]


def test_registry_shape():
    for verb, spec in cp.VERBS.items():
        assert spec["args"] in ("", "[env]", "<env>"), verb
        assert spec["desc"].strip(), verb


def test_route_per_verb():
    assert cp.parse("shipmate apply dev-eu")["route"] == "apply"
    assert cp.parse("shipmate doctor")["route"] == "doctor"
    assert cp.parse("shipmate help")["route"] == "help"
    assert cp.parse("shipmate plan")["route"] == "plan"
    assert cp.parse("nothing to see here")["route"] is None


@pytest.mark.parametrize(
    "verb",
    sorted(v for v, s in cp.VERBS.items() if s["args"] == ""),
)
def test_no_arg_verb_rejects_arguments(verb):
    r = cp.parse(f"shipmate {verb} dev-eu")
    assert r["is_command"] is True
    assert r["valid"] is False
    assert "takes no arguments" in r["error"]
    assert r["route"] is None


def test_unknown_verb_points_at_help():
    r = cp.parse("shipmate frobnicate")
    assert r["valid"] is False
    assert "shipmate help" in r["error"]


@pytest.mark.parametrize(
    "body",
    [
        "shipmate Doctor",  # The verb charset is lowercase-only.
        "shipmate  apply",  # A double space.
        "shipmate apply dev-eu --auto",  # A token after the env.
        "shipmate apply dev-eu; rm -rf /",
    ],
)
def test_malformed_command_points_at_help(body):
    """Everything that fails _CMD outright lands on the malformed message, and every one of
    these is a typo the verb list resolves, so it carries the same `shipmate help` hint the
    unknown-verb path does."""
    r = cp.parse(body)
    assert r["is_command"] is True
    assert r["valid"] is False
    assert "malformed" in r["error"]
    assert "shipmate help" in r["error"]


def test_help_markdown_lists_every_verb():
    md = cp.help_markdown(_RUN_URL)
    assert md.startswith(cp.HELP_MARKER)
    for verb, spec in cp.VERBS.items():
        assert f"shipmate {verb}" in md
        assert spec["desc"].split(".")[0] in md


def test_help_has_no_bare_command_line():
    """A bare line matching the grammar would make the help comment itself a command and
    retrigger comment-ops on the bot's own comment."""
    for line in cp.help_markdown(_RUN_URL).splitlines():
        assert cp._CMD.match(line.strip()) is None, line
    # is_command is set by _CMD or _SHIPMATE_LINE (^shipmate\b), and the per-line check above
    # covers only _CMD, so the property that matters is asserted too: parsing the whole rendered
    # comment must not be recognized as a command at all.
    assert cp.parse(cp.help_markdown(_RUN_URL))["is_command"] is False


def test_main_writes_route_output(tmp_path, monkeypatch):
    # Pins main()'s route= output line: `actions/comment-ops` branches on
    # steps.parse.outputs.route, so a rename on either side must fail here rather than pass
    # silently green.
    out = tmp_path / "out.txt"
    out.touch()
    monkeypatch.setenv("COMMENT_BODY", "shipmate apply dev-eu")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    cp.main()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert "route=apply" in lines


#: The whole help comment, hand-written.
_HELP = (
    "<!-- shipmate:help -->\n"
    "### shipmate help\n"
    "\n"
    "Comment one of these on a pull request:\n"
    "\n"
    "| command | what it does |\n"
    "| --- | --- |\n"
    "| `shipmate apply [env]` | Apply the reviewed plan for one environment, or every "
    "non-explicit environment when the environment is omitted. |\n"
    "| `shipmate doctor` | Report setup problems: repository settings, environments, App "
    "permissions, and warnings from this commit's workflow runs. |\n"
    "| `shipmate help` | Show this command list. |\n"
    "| `shipmate plan` | Plan this pull request's changed stacks on demand: the same plan a "
    "push produces, including on a draft. Re-planning is safe and replaces the plan of record "
    "for the current head. |\n"
    "| `shipmate unlock <env>` | Release a state lock stranded by a cancelled or killed "
    "apply, so the environment's stacks can apply again. Does not re-apply, and does not "
    "recover a partial apply. |\n"
    "\n"
    "The environment is a GitHub Environment name such as `dev-eu`. Applying requires write "
    "access to this repository, an approving review where the branch ruleset asks for one, a "
    "plan for the current head commit, and a pull request that is not a draft. Unlocking "
    "requires write access to this repository, but no review, no plan and no such readiness. "
    "`doctor` and `help` are read-only, and `plan` changes no infrastructure. `help` answers "
    "anyone; `doctor` and `plan` require write access to this repository.\n"
    "\n"
    "[run](https://github.com/org/repo/actions/runs/7777)"
)


def test_help_carries_the_shared_header_and_a_footer_without_the_hint():
    """The help is the command list, so its footer links the run and names no help command.
    The whole body, so a reworded row or paragraph between the header and footer fails too.

    Mutations: render the footer with the hint; restore `### shipmate commands`; drop
    "including on a draft" from `plan`'s description.
    """
    assert cp.help_markdown(_RUN_URL) == _HELP


def _main_output(tmp_path, monkeypatch, body):
    out = tmp_path / "out.txt"
    out.touch()
    monkeypatch.setenv("COMMENT_BODY", body)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    cp.main()
    return out.read_text(encoding="utf-8").splitlines()


def test_main_writes_no_verb_for_destroy(tmp_path, monkeypatch):
    """`shipmate destroy` is an unknown verb, so its reply is headed `### shipmate` alone.

    Mutation: re-add `destroy` to `VERBS`, and the output names it.
    """
    assert _main_output(tmp_path, monkeypatch, "shipmate destroy") == [
        "is_command=true",
        "valid=false",
        "verb=",
        "env=",
        "route=",
        "error=unknown verb `destroy` (try `shipmate help`)",
    ]


def test_main_writes_no_verb_for_an_unknown_one(tmp_path, monkeypatch):
    """Mutation: write any parsed verb, and the reply would be headed `### shipmate aply dev-eu`."""
    assert _main_output(tmp_path, monkeypatch, "shipmate aply dev-eu") == [
        "is_command=true",
        "valid=false",
        "verb=",
        "env=dev-eu",
        "route=",
        "error=unknown verb `aply` (try `shipmate help`)",
    ]


def test_main_writes_no_verb_for_a_malformed_line(tmp_path, monkeypatch):
    """Mutation: write `r['verb']` without the `VERBS` check, and a line the grammar rejects
    writes `verb=None`."""
    assert _main_output(tmp_path, monkeypatch, "shipmate Apply") == [
        "is_command=true",
        "valid=false",
        "verb=",
        "env=",
        "route=",
        "error=malformed: expected `shipmate <verb> [env]` (try `shipmate help`)",
    ]


def test_parse_does_not_import_summary_comment(tmp_path):
    """Every PR comment runs comment-parse, so a summary-comment that fails at import must not
    stop a command from parsing. Runs a copy of the script beside a summary-comment that raises.

    Mutation: load summary-comment at module level, and the run exits non-zero with no output.
    """
    scripts = pathlib.Path(cp.__file__).parent
    for name in ("comment-parse", "_shipmate.py"):
        shutil.copy(scripts / name, tmp_path / name)
    (tmp_path / "summary-comment").write_text("raise RuntimeError('broken')\n", encoding="utf-8")
    out = tmp_path / "out.txt"
    out.touch()
    subprocess.run(
        [sys.executable, str(tmp_path / "comment-parse")],
        env={"COMMENT_BODY": "shipmate apply dev-eu", "GITHUB_OUTPUT": str(out)},
        check=True,
        timeout=60,
    )
    assert out.read_text(encoding="utf-8").splitlines() == [
        "is_command=true",
        "valid=true",
        "verb=apply",
        "env=dev-eu",
        "route=apply",
        "error=",
    ]


def test_main_help_markdown_flag(monkeypatch, capsys):
    # Pins the --help-markdown CLI surface: it must print help_markdown() to stdout and return
    # without ever needing GITHUB_OUTPUT. The variable is unset to prove that path is not
    # touched, since main() would raise KeyError otherwise.
    monkeypatch.setattr("sys.argv", ["comment-parse", "--help-markdown"])
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "7777")
    cp.main()
    assert capsys.readouterr().out.startswith(cp.HELP_MARKER)


def test_unlock_parses_with_an_env():
    r = cp.parse("shipmate unlock dev-eu")
    assert (r["valid"], r["route"], r["env"]) == (True, "unlock", "dev-eu")


def test_unlock_without_an_env_is_rejected():
    r = cp.parse("shipmate unlock")
    assert r["is_command"] and not r["valid"]
    assert "requires an environment" in r["error"]


def test_unlock_appears_in_the_help_output():
    md = cp.help_markdown(_RUN_URL)
    assert "shipmate unlock <env>" in md
