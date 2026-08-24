"""The three detects must reach "already applied" only through the App-scoped
query, and their actions must keep feeding it the App id.

Both halves are structural, and neither is observable from a unit test of the
helper: `completed_apply_names` can be correct while a detect's `main()` calls
something else, and the `SHIPMATE_APP_ID` the query is now given comes from an
`env:` line each detect's own action file has to carry. All three detects route
through the query, so all three are checked textually -- but for `apply-detect`,
which both defines the query and calls it, a substring check cannot fail: its own
definition satisfies it. That `main()` is pinned behaviourally instead, by
test_apply_detect.test_a_forged_completed_check_does_not_mark_a_cell_applied.

A forged same-name check counted as done drops that stack from the wave matrix
and the deploy reports success, so both need a guard that fails on the edit
rather than on the next merge.
"""

import re

import yaml
from _loader import ACTIONS, SCRIPTS

DETECTS = ("apply-detect", "deploy-detect", "apply-all-detect")

# The App-scoped query, and the two unscoped predicates that must not be reached
# for it directly: `done_names` ignores authorship entirely, and `app_done_names`
# is `completed_apply_names`'s own internal call.
#
# Every detect calls the query, `apply-detect` included -- it holds the lines and
# passes them in rather than bypassing to `app_done_names`. For that one the
# assertion is satisfied by the definition too, so it adds nothing there; see the
# module docstring.
_CONSUMERS = DETECTS
_SCOPED_CALL = "completed_apply_names("
_UNSCOPED_CALLS = (r"\bag\.done_names\(", r"\bag\.app_done_names\(")


def _source(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_the_detects_reach_completed_applies_through_the_scoped_query():
    # apply-detect's main() does route through completed_apply_names now, but
    # that is not what this assertion shows -- a substring match can't tell a
    # call site from a definition in the same module, and apply-detect is where
    # the definition lives. For it the property is pinned behaviourally, by
    # test_apply_detect.test_a_forged_completed_check_does_not_mark_a_cell_applied.
    # Deleting that test removes the only guard standing between apply-detect's
    # main() and a forged same-name check counting as applied.
    for name in _CONSUMERS:
        text = _source(name)
        assert _SCOPED_CALL in text, f"{name} no longer calls completed_apply_names"


def test_only_the_query_owner_calls_the_unscoped_predicates():
    # apply-detect owns the single call to apply-gate; the other two must not
    # grow a second route to the predicate, scoped or otherwise.
    for name in ("deploy-detect", "apply-all-detect"):
        text = _source(name)
        for pattern in _UNSCOPED_CALLS:
            assert not re.search(pattern, text), (
                f"{name} calls {pattern} directly -- the done predicate must arrive "
                "through apply-detect.completed_apply_names, which scopes it to the App"
            )


def test_the_query_owner_scopes_the_predicate_to_the_app():
    # This only pins that "app_done_names(" and the SHIPMATE_APP_ID read appear
    # somewhere in apply-detect's source -- it cannot tell whether main() is the
    # caller, because completed_apply_names' own definition satisfies the same
    # substring. Rewriting main() to bypass completed_apply_names (e.g. calling
    # ag.done_names directly) would leave this test green; only
    # test_apply_detect.test_a_forged_completed_check_does_not_mark_a_cell_applied
    # would catch it. Do not read this test as covering that call-site property.
    text = _source("apply-detect")
    assert "app_done_names(" in text, (
        "apply-detect must use apply-gate's App-scoped predicate; done_names alone "
        "counts a forged same-name check from any identity as applied work"
    )
    assert 'os.environ["SHIPMATE_APP_ID"]' in text


def test_detect_actions_forward_the_app_id_to_the_script():
    # Every detect reads SHIPMATE_APP_ID and passes it to the query, so a
    # call-site audit of the scripts alone looks complete while the env: line
    # that supplies it is dropped -- which fails that detect with a KeyError.
    for name in DETECTS:
        spec = yaml.safe_load((ACTIONS / name / "action.yml").read_text(encoding="utf-8"))
        env_blocks = [step.get("env") or {} for step in (spec["runs"].get("steps") or [])]
        assert any("SHIPMATE_APP_ID" in env for env in env_blocks), (
            f"actions/{name} must pass SHIPMATE_APP_ID to the detect script"
        )
