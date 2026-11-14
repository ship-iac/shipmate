"""`_shipmate.parse_run_title` against dispatched run titles, each compared whole.

The first four are `display_title`s copied from sample runs dispatched on 2026-11-13: runs
43523652512 (apply), 43523656156 (auto), 43523643964 (plan) and 43523659351 (unlock).
"""

import _shipmate
import pytest

_SHA = "df148592137074dd47f043a225f9ff859175ed6b"


@pytest.mark.parametrize(
    ("title", "parsed"),
    [
        (
            f"shipmate · apply pr=422 ref={_SHA} comment=6867046611",
            {"verb": "apply", "pr": "422", "ref": _SHA, "comment": "6867046611"},
        ),
        (
            f"shipmate · auto pr=422 ref={_SHA} comment=",
            {"verb": "auto", "pr": "422", "ref": _SHA, "comment": ""},
        ),
        (
            "shipmate · plan pr=422 ref= comment=6867046611",
            {"verb": "plan", "pr": "422", "ref": "", "comment": "6867046611"},
        ),
        (
            f"shipmate · unlock pr= ref={_SHA} comment=6867046611",
            {"verb": "unlock", "pr": "", "ref": _SHA, "comment": "6867046611"},
        ),
        (
            f"shipmate · apply pr=422 ref={_SHA} comment=",
            {"verb": "apply", "pr": "422", "ref": _SHA, "comment": ""},
        ),
    ],
)
def test_a_dispatched_title_parses_into_every_slot(title, parsed):
    """Mutations: drop `plan` from the verb group -- red; require a ref for `plan` -- red;
    require a pull request for `unlock` -- red; refuse an empty `apply` comment -- red."""
    assert _shipmate.parse_run_title(title) == parsed


@pytest.mark.parametrize(
    "title",
    [
        "shipmate · release",
        "shipmate · plan",
        "shipmate · comment",
        "shipmate · deploy",
        f"shipmate · apply pr=0422 ref={_SHA} comment=",
        f"shipmate · apply pr=0 ref={_SHA} comment=",
        f"shipmate · apply pr=422 ref={_SHA} comment=1\n",
        f"shipmate · apply pr=422 ref={_SHA} comment=\n",
        f"shipmate · apply pr=4٢2 ref={_SHA} comment=",
        f"shipmate · apply pr=422 ref={_SHA} comment=1٢",
        f"shipmate · apply ref={_SHA} pr=422 comment=",
        f"shipmate · apply pr={_SHA} ref=422 comment=",
        f"shipmate · apply pr=422 ref={_SHA.upper()} comment=",
        f"shipmate · apply pr=422 ref={_SHA[:39]} comment=",
        "shipmate · apply pr=422 ref= comment=",
        f"shipmate · apply pr= ref={_SHA} comment=",
        f"shipmate · auto pr=422 ref={_SHA} comment=6867046611",
        f"shipmate · apply pr=422 ref={_SHA} comment=0",
        f"shipmate · plan pr=422 ref={_SHA} comment=",
        f"shipmate · unlock pr=422 ref={_SHA} comment=",
        f"shipmate · drift pr=422 ref={_SHA} comment=",
        f"shipmate  · apply pr=422 ref={_SHA} comment=",
        f" shipmate · apply pr=422 ref={_SHA} comment=",
        f"shipmate · apply pr=422 ref={_SHA} comment= ",
        f"shipmate · apply pr=422  ref={_SHA} comment=",
        f"shipmate - apply pr=422 ref={_SHA} comment=",
        "",
        None,
    ],
)
def test_a_title_outside_the_grammar_does_not_parse(title):
    """Leading zero, trailing newline, non-ASCII digits, swapped slots, an `auto` comment, a
    `plan` ref, an `unlock` number and malformed spacing.

    Mutations: `re.search` for `fullmatch` -- red (trailing newline, leading space); `\\d` for
    `[0-9]` in the comment id -- red (non-ASCII digit); drop the `auto` comment check -- red."""
    assert _shipmate.parse_run_title(title) is None
