"""The three detects must reach "already applied" only through the App-scoped
query, and their actions must keep feeding it the App id.

Both halves are structural, and neither is observable from a unit test of the
helper: `completed_apply_names` can be correct while a detect's `main()` calls
something else, and the `SHIPMATE_APP_ID` wiring lives in YAML that no script
now mentions (the env read moved into `apply-detect` when the query was
single-sourced). A forged same-name check counted as done drops that stack from
the wave matrix and the deploy reports success, so both need a guard that fails
on the edit rather than on the next merge.
"""

import re

import yaml
from _loader import ACTIONS, SCRIPTS

DETECTS = ("apply-detect", "deploy-detect", "apply-all-detect")

# The App-scoped query, and the two unscoped predicates that must not be reached
# for it directly: `done_names` ignores authorship entirely, and `app_done_names`
# is `completed_apply_names`'s own internal call.
_SCOPED_CALL = "completed_apply_names("
_UNSCOPED_CALLS = (r"\bag\.done_names\(", r"\bag\.app_done_names\(")


def _source(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_every_detect_reaches_completed_applies_through_the_scoped_query():
    for name in DETECTS:
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
    text = _source("apply-detect")
    assert "app_done_names(" in text, (
        "apply-detect must use apply-gate's App-scoped predicate; done_names alone "
        "counts a forged same-name check from any identity as applied work"
    )
    assert 'os.environ["SHIPMATE_APP_ID"]' in text


def test_detect_actions_forward_the_app_id_to_the_script():
    # The scripts that used to read SHIPMATE_APP_ID no longer name it, so this
    # is the only thing standing between the env: line and a "dead wiring"
    # cleanup -- which would fail every post-merge detect with a KeyError.
    for name in DETECTS:
        spec = yaml.safe_load((ACTIONS / name / "action.yml").read_text(encoding="utf-8"))
        env_blocks = [step.get("env") or {} for step in (spec["runs"].get("steps") or [])]
        assert any("SHIPMATE_APP_ID" in env for env in env_blocks), (
            f"actions/{name} must pass SHIPMATE_APP_ID to the detect script"
        )
