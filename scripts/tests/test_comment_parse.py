import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parents[1]


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cp = _load("comment-parse")


def test_valid_apply():
    r = cp.parse("mate apply dev-eu")
    assert r == {
        "is_command": True,
        "valid": True,
        "verb": "apply",
        "env": "dev-eu",
        "tag_filter": None,
        "error": None,
    }


def test_tag_filter_rejected_not_yet_supported():
    # Parsed for forward-compat but no component honors it → reject rather than
    # silently apply the whole env.
    r = cp.parse("mate apply dev-eu workload:app")
    assert r["is_command"] and not r["valid"] and "tag-filter" in r["error"]
    assert r["verb"] == "apply" and r["env"] == "dev-eu" and r["tag_filter"] == "workload:app"


def test_leading_trailing_whitespace_and_crlf():
    r = cp.parse("\r\n  mate apply dev-eu  \r\n")
    assert r["valid"] and r["env"] == "dev-eu"


def test_command_on_first_matching_line_of_multiline():
    r = cp.parse("thanks!\nmate apply dev-us\n/cc @team")
    assert r["valid"] and r["env"] == "dev-us"


def test_reserved_verb_plan_is_rejected():
    r = cp.parse("mate plan dev-eu")
    assert r["is_command"] and not r["valid"] and r["verb"] == "plan"
    assert "reserved" in r["error"]


def test_reserved_verb_destroy_is_rejected():
    r = cp.parse("mate destroy dev-eu")
    assert r["is_command"] and not r["valid"] and "reserved" in r["error"]


def test_unknown_verb_is_rejected():
    r = cp.parse("mate frobnicate dev-eu")
    assert r["is_command"] and not r["valid"] and "unknown verb" in r["error"]


def test_bare_apply_targets_all_envs():
    # env is optional: bare `mate apply` = apply every non-explicit env.
    r = cp.parse("mate apply")
    assert r == {
        "is_command": True,
        "valid": True,
        "verb": "apply",
        "env": None,
        "tag_filter": None,
        "error": None,
    }


def test_bare_apply_with_whitespace_and_crlf():
    r = cp.parse("\r\n  mate apply  \r\n")
    assert r["valid"] and r["env"] is None


def test_bare_apply_with_tag_filter_rejected():
    # A tag can't be mistaken for an env (':' is outside the env charset);
    # bare + tag parses env=None and still rejects on the unsupported tag.
    r = cp.parse("mate apply workload:app")
    assert r["is_command"] and not r["valid"] and "tag-filter" in r["error"]
    assert r["env"] is None and r["tag_filter"] == "workload:app"


def test_bare_reserved_verb_rejected():
    r = cp.parse("mate plan")
    assert r["is_command"] and not r["valid"] and "reserved" in r["error"]


def test_bare_unknown_verb_rejected():
    r = cp.parse("mate frobnicate")
    assert r["is_command"] and not r["valid"] and "unknown verb" in r["error"]


def test_injection_attempt_is_rejected():
    r = cp.parse("mate apply dev-eu; rm -rf /")
    assert r["is_command"] and not r["valid"]


def test_backtick_injection_in_env_rejected():
    r = cp.parse("mate apply $(whoami)")
    assert r["is_command"] and not r["valid"]


def test_non_command_comment_is_not_a_command():
    r = cp.parse("LGTM, merging after CI")
    assert not r["is_command"] and not r["valid"]


def test_matey_prefix_is_not_a_command():
    # 'mate' must be a whole word, not a prefix of another word.
    r = cp.parse("matey apply dev-eu")
    assert not r["is_command"]


def test_env_uppercase_rejected():
    r = cp.parse("mate apply DEV-EU")
    assert r["is_command"] and not r["valid"]


def test_earlier_mate_prefixed_chatter_does_not_block_later_valid_command():
    # A prior mate-prefixed line that isn't a recognized command (unknown verb)
    # must not win over a later line that is a full, valid command.
    r = cp.parse("mate is great\nmate apply dev-eu")
    assert r == {
        "is_command": True,
        "valid": True,
        "verb": "apply",
        "env": "dev-eu",
        "tag_filter": None,
        "error": None,
    }


def test_pure_garbage_mate_line_still_errors():
    r = cp.parse("mate is great")
    assert r["is_command"] and not r["valid"] and r["error"]
