import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

import pytest

_D = pathlib.Path(__file__).resolve().parents[1]


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cp = _load("comment-parse")


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
    # Parsed for forward-compat but no component honors it → reject rather than
    # silently apply the whole env.
    r = cp.parse("shipmate apply dev-eu workload:app")
    assert r["is_command"] and not r["valid"] and "tag-filter" in r["error"]
    assert r["verb"] == "apply" and r["env"] == "dev-eu" and r["tag_filter"] == "workload:app"


def test_leading_trailing_whitespace_and_crlf():
    r = cp.parse("\r\n  shipmate apply dev-eu  \r\n")
    assert r["valid"] and r["env"] == "dev-eu"


def test_command_on_first_matching_line_of_multiline():
    r = cp.parse("thanks!\nshipmate apply dev-us\n/cc @team")
    assert r["valid"] and r["env"] == "dev-us"


def test_reserved_verb_plan_is_rejected():
    r = cp.parse("shipmate plan dev-eu")
    assert r["is_command"] and not r["valid"] and r["verb"] == "plan"
    assert "reserved" in r["error"]


def test_reserved_verb_destroy_is_rejected():
    r = cp.parse("shipmate destroy dev-eu")
    assert r["is_command"] and not r["valid"] and "reserved" in r["error"]


def test_unknown_verb_is_rejected():
    r = cp.parse("shipmate frobnicate dev-eu")
    assert r["is_command"] and not r["valid"] and "unknown verb" in r["error"]


def test_bare_apply_targets_all_envs():
    # env is optional: bare `shipmate apply` = apply every non-explicit env.
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
    # A tag can't be mistaken for an env (':' is outside the env charset);
    # bare + tag parses env=None and still rejects on the unsupported tag.
    r = cp.parse("shipmate apply workload:app")
    assert r["is_command"] and not r["valid"] and "tag-filter" in r["error"]
    assert r["env"] is None and r["tag_filter"] == "workload:app"


def test_bare_reserved_verb_rejected():
    r = cp.parse("shipmate plan")
    assert r["is_command"] and not r["valid"] and "reserved" in r["error"]


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
    # Uppercase is outside the env charset (env is lowercase-only), so the env
    # group doesn't match -- but it DOES fit the tag charset (which allows
    # uppercase), so this now falls through to the tag branch rather than
    # "malformed". Pinned here as a deliberate, tested choice: the user sees a
    # "tag-filter is not yet supported" error, not a missing/invalid-env one.
    r = cp.parse("shipmate apply DEV-EU")
    assert r["is_command"] and not r["valid"]
    assert r["env"] is None and r["tag_filter"] == "DEV-EU"
    assert "tag-filter" in r["error"]


def test_earlier_shipmate_prefixed_chatter_does_not_block_later_valid_command():
    # A prior shipmate-prefixed line that isn't a recognized command (unknown
    # verb) must not win over a later line that is a full, valid command.
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
        assert spec["args"] in ("", "[env]"), verb
        assert spec["desc"].strip(), verb
        if spec["status"] == cp.ACTIVE:
            assert spec["route"], verb
        else:
            assert spec["route"] is None, verb


def test_route_per_verb():
    assert cp.parse("shipmate apply dev-eu")["route"] == "apply"
    assert cp.parse("shipmate doctor")["route"] == "doctor"
    assert cp.parse("shipmate help")["route"] == "help"
    assert cp.parse("shipmate plan")["route"] is None
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
        "shipmate Doctor",  # verb charset is lowercase-only
        "shipmate  apply",  # double space
        "shipmate apply dev-eu --auto",  # a token outside both optional charsets
        "shipmate apply dev-eu; rm -rf /",
    ],
)
def test_malformed_command_points_at_help(body):
    # Everything that fails _CMD outright lands on the malformed message, and
    # every one of these is a typo the verb list resolves -- so it carries the
    # same `shipmate help` hint the unknown-verb path does. (`shipmate apply
    # dev_eu` is NOT one of them: '_' is in the tag charset, so it parses as a
    # tag-filter and gets the "not yet supported" error, pinned above.)
    r = cp.parse(body)
    assert r["is_command"] is True
    assert r["valid"] is False
    assert "malformed" in r["error"]
    assert "shipmate help" in r["error"]


def test_help_markdown_lists_every_verb():
    md = cp.help_markdown()
    assert md.startswith(cp.HELP_MARKER)
    for verb, spec in cp.VERBS.items():
        assert f"shipmate {verb}" in md
        assert spec["desc"].split(".")[0] in md
    assert "reserved" in md  # reserved verbs are shown as such, not hidden


def test_help_has_no_bare_command_line():
    """A bare line matching the grammar would make the help comment itself a
    command and retrigger comment-ops on the bot's own comment."""
    for line in cp.help_markdown().splitlines():
        assert cp._CMD.match(line.strip()) is None, line
    # is_command is set by _CMD OR _SHIPMATE_LINE (^shipmate\b) -- the
    # per-line check above only covers _CMD, so also assert the property
    # that actually matters: parsing the whole rendered comment must not
    # be recognized as a command at all.
    assert cp.parse(cp.help_markdown())["is_command"] is False


def test_main_writes_route_output(tmp_path, monkeypatch):
    # Pins main()'s route= output line: `actions/comment-ops` branches on
    # steps.parse.outputs.route, so a rename on either side must fail here,
    # not fail silently green.
    out = tmp_path / "out.txt"
    out.touch()
    monkeypatch.setenv("COMMENT_BODY", "shipmate apply dev-eu")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    cp.main()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert "route=apply" in lines


def test_main_help_markdown_flag(monkeypatch, capsys):
    # Pins the --help-markdown CLI surface: it must print help_markdown() to
    # stdout and return WITHOUT ever needing GITHUB_OUTPUT -- unset it to
    # prove that path isn't touched (main() would raise KeyError otherwise).
    monkeypatch.setattr("sys.argv", ["comment-parse", "--help-markdown"])
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    cp.main()
    assert capsys.readouterr().out.startswith(cp.HELP_MARKER)
