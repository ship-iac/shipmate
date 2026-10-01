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
        "tag_filter": None,
        "route": "apply",
        "error": None,
    }


def test_tag_filter_rejected_not_yet_supported():
    # Parsed for forward compatibility, but no component honors it, so it is rejected rather
    # than silently applying the whole env.
    r = cp.parse("shipmate apply dev-eu workload:app")
    assert r["is_command"] and not r["valid"] and "tag-filter" in r["error"]
    assert r["verb"] == "apply" and r["env"] == "dev-eu" and r["tag_filter"] == "workload:app"


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


def test_plan_rejects_an_env_with_a_tag_filter():
    # Reached through the no-arguments branch, not the tag branch: with `args: ""` a tag is
    # already an argument.
    r = cp.parse("shipmate plan dev-eu workload:app")
    assert r["is_command"] and not r["valid"]
    assert "takes no arguments" in r["error"]


def test_reserved_verb_destroy_is_rejected():
    r = cp.parse("shipmate destroy dev-eu")
    assert r["is_command"] and not r["valid"] and "reserved" in r["error"]
    assert r["route"] is None


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
        "tag_filter": None,
        "route": "apply",
        "error": None,
    }


def test_bare_apply_with_whitespace_and_crlf():
    r = cp.parse("\r\n  shipmate apply  \r\n")
    assert r["valid"] and r["env"] is None


def test_bare_apply_with_tag_filter_rejected():
    # A tag cannot be mistaken for an env, because ':' is outside the env charset, so bare plus
    # tag parses env=None and still rejects on the unsupported tag.
    r = cp.parse("shipmate apply workload:app")
    assert r["is_command"] and not r["valid"] and "tag-filter" in r["error"]
    assert r["env"] is None and r["tag_filter"] == "workload:app"


def test_bare_reserved_verb_rejected():
    r = cp.parse("shipmate destroy")
    assert r["is_command"] and not r["valid"] and "reserved" in r["error"]


def test_bare_plan_is_valid():
    r = cp.parse("shipmate plan")
    assert r == {
        "is_command": True,
        "valid": True,
        "verb": "plan",
        "env": None,
        "tag_filter": None,
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


def test_env_uppercase_rejected():
    """Uppercase is outside the lowercase-only env charset, so the env group does not match, but
    it does fit the tag charset, which allows uppercase, so this falls through to the tag branch
    rather than to "malformed". Pinned as a deliberate choice: the user sees a "tag-filter is
    not yet supported" error, not a missing or invalid env one."""
    r = cp.parse("shipmate apply DEV-EU")
    assert r["is_command"] and not r["valid"]
    assert r["env"] is None and r["tag_filter"] == "DEV-EU"
    assert "tag-filter" in r["error"]


def test_earlier_shipmate_prefixed_chatter_does_not_block_later_valid_command():
    # A prior shipmate-prefixed line that is not a recognized command, an unknown verb here,
    # must not win over a later line that is a full, valid command.
    r = cp.parse("shipmate is great\nshipmate apply dev-eu")
    assert r == {
        "is_command": True,
        "valid": True,
        "verb": "apply",
        "env": "dev-eu",
        "tag_filter": None,
        "route": "apply",
        "error": None,
    }


def test_pure_garbage_shipmate_line_still_errors():
    r = cp.parse("shipmate is great")
    assert r["is_command"] and not r["valid"] and r["error"]


def test_registry_shape():
    for verb, spec in cp.VERBS.items():
        assert spec["status"] in (cp.ACTIVE, cp.RESERVED), verb
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
    sorted(v for v, s in cp.VERBS.items() if s["status"] == cp.ACTIVE and s["args"] == ""),
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
        "shipmate apply dev-eu --auto",  # A token outside both optional charsets.
        "shipmate apply dev-eu; rm -rf /",
    ],
)
def test_malformed_command_points_at_help(body):
    """Everything that fails _CMD outright lands on the malformed message, and every one of
    these is a typo the verb list resolves, so it carries the same `shipmate help` hint the
    unknown-verb path does. `shipmate apply dev_eu` is not one of them: '_' is in the tag
    charset, so it parses as a tag-filter and gets the "not yet supported" error pinned
    above."""
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
    assert "reserved" in md  # Reserved verbs are shown as such, not hidden.


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


def test_help_carries_the_shared_header_and_a_footer_without_the_hint():
    """The help is the command list, so its footer links the run and names no help command.

    Mutations: render the footer with the hint; restore `### shipmate commands`.
    """
    lines = cp.help_markdown(_RUN_URL).splitlines()
    assert lines[:3] == [cp.HELP_MARKER, "### shipmate help", ""]
    assert lines[-2:] == ["", f"[run]({_RUN_URL})"]


def _main_output(tmp_path, monkeypatch, body):
    out = tmp_path / "out.txt"
    out.touch()
    monkeypatch.setenv("COMMENT_BODY", body)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    cp.main()
    return out.read_text(encoding="utf-8").splitlines()


def test_main_names_a_reserved_verb_it_does_not_route(tmp_path, monkeypatch):
    """`shipmate destroy` is refused, but its reply is headed `### shipmate destroy`.

    Mutation: write the route as the verb.
    """
    assert _main_output(tmp_path, monkeypatch, "shipmate destroy") == [
        "is_command=true",
        "valid=false",
        "verb=destroy",
        "env=",
        "route=",
        "error=verb `destroy` is reserved and not yet implemented",
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
        "error=malformed: expected `shipmate <verb> [env] [tag-filter]` (try `shipmate help`)",
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


def test_plan_appears_in_the_help_output_as_active():
    """`plan` renders as a no-argument command, backticked, with an empty status column -- the
    rendering active verbs get."""
    assert "| `shipmate plan` |  |" in cp.help_markdown(_RUN_URL)


def test_unlock_appears_in_the_help_output():
    md = cp.help_markdown(_RUN_URL)
    assert "shipmate unlock <env>" in md


def test_unlock_rejects_a_tag_filter():
    r = cp.parse("shipmate unlock dev-eu workload:app")
    assert not r["valid"]
