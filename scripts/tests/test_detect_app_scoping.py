"""The three detects must reach "already applied" only through the App-scoped
predicate, and their actions must keep feeding it the App id.

Both halves are structural, and neither is observable from a unit test of the
predicate: `app_done_names` can be correct while a detect's `main()` calls
something else, and the `SHIPMATE_APP_ID` it is given comes from an `env:` line
each detect's own action file has to carry. The behavioural pin on each `main()`
is its detect's test_a_forged_completed_check_does_not_mark_a_cell_applied.

The script checks here are substring and regex matches on file text, not parsed
call sites: a comment or docstring carrying `app_done_names(` satisfies the
required call, and a call spelled any other way than `ag.done_names(` escapes the
forbidden one. They catch an accidental regression of the call line; the
forged-check tests are what pin the behaviour.

A forged same-name check counted as done drops that stack from the wave matrix
and the deploy reports success, so both need a guard that fails on the edit
rather than on the next merge.
"""

import re

from _loader import SCRIPTS, action_yaml

DETECTS = ("apply-detect", "deploy-detect", "apply-all-detect")
# `apply-detect`'s action runs both apply-form scripts.
DETECT_ACTIONS = ("apply-detect", "deploy-detect")

# The App-scoped predicate, and the unscoped one that ignores authorship entirely.
_SCOPED_CALL = "app_done_names("
_UNSCOPED_CALL = r"\bag\.done_names\("


def _source(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_the_detects_reach_completed_applies_through_the_scoped_predicate():
    """Mutation: replace `ag.app_done_names(lines, app_id)` in any detect's main() with
    `ag.done_names(ag.parse_jsonl(lines))` -- that detect no longer carries the call."""
    for name in DETECTS:
        assert _SCOPED_CALL in _source(name), f"{name} no longer calls app_done_names"


def test_no_detect_calls_the_unscoped_predicate():
    """Mutation: the same replacement -- `ag.done_names(` appears in that detect."""
    for name in DETECTS:
        assert not re.search(_UNSCOPED_CALL, _source(name)), (
            f"{name} calls ag.done_names directly -- the done predicate must be "
            "apply-gate.app_done_names, which scopes it to the App"
        )


def test_the_detects_read_the_app_id():
    """Mutation: `os.environ["SHIPMATE_APP_ID"]` -> `os.environ.get("SHIPMATE_APP_ID", "")` in
    deploy-detect's main() -- the substring is gone."""
    for name in DETECTS:
        assert 'os.environ["SHIPMATE_APP_ID"]' in _source(name), name


def test_detect_actions_forward_the_app_id_to_the_script():
    # Every detect reads SHIPMATE_APP_ID and passes it to the query, so a call-site audit of the
    # scripts alone looks complete while the env: line that supplies it is dropped, which fails
    # that detect with a KeyError.
    for name in DETECT_ACTIONS:
        spec = action_yaml(name)
        env_blocks = [step.get("env") or {} for step in (spec["runs"].get("steps") or [])]
        assert any("SHIPMATE_APP_ID" in env for env in env_blocks), (
            f"actions/{name} must pass SHIPMATE_APP_ID to the detect script"
        )
