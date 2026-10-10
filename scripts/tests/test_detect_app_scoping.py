"""The detects must reach "already applied" only through the App-scoped
predicate. The `env:` line that hands each script `SHIPMATE_APP_ID` is pinned
whole by `test_detect_wiring_guard.py`.

This is structural, and not observable from a unit test of the predicate:
`app_done_names` can be correct while a detect's `main()` calls something else.
The behavioural pin on each `main()` is its detect's
test_a_forged_completed_check_does_not_mark_a_cell_applied; apply-detect holds two
(`main` and `bare_main`), which these file-text checks cannot tell apart, so its pins
are that test in test_apply_detect.py and in test_apply_all_detect.py.

The script checks here are substring and regex matches on file text, not parsed
call sites: a comment or docstring carrying `app_done_names(` satisfies the
required call, and a call spelled any other way than `ag.done_names(` escapes the
forbidden one. They catch an accidental regression of the call line; the
forged-check tests are what pin the behaviour.

A forged same-name check counted as done drops that stack from the wave matrix
and the deploy reports success, so it needs a guard that fails on the edit
rather than on the next merge.
"""

import re

from _loader import SCRIPTS

DETECTS = ("apply-detect", "deploy-detect")

# The App-scoped predicate, and the unscoped one that ignores authorship entirely.
_SCOPED_CALL = "app_done_names("
_UNSCOPED_CALL = r"\bag\.done_names\("


def _source(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_the_detects_reach_completed_applies_through_the_scoped_predicate():
    """Mutation: replace `ad.ag.app_done_names(lines, app_id)` in deploy-detect's main() with
    `ad.ag.done_names(ad.ag.parse_jsonl(lines))` -- deploy-detect no longer carries the call.
    apply-detect calls it from both `main` and `bare_main`, so one replacement there leaves the
    substring present; its two call sites are pinned by the forged-check tests named above."""
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
