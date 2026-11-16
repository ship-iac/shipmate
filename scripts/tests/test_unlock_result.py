"""`scripts/unlock-result`: the one reply an unlock run posts, compared whole against
hand-written text, and the records it reads it from.

Threat model: accidental regression -- an outcome rendered as another, a record read under the
wrong cell, a stack name rendered raw, a failure that reds the run or posts nothing.
"""

import json
from typing import Any

import pytest
from _loader import load_script

ur = load_script("unlock-result")

_RUN = "https://github.com/org/repo/actions/runs/7777"
_FOOT = f"[comment run]({_RUN})"
_HINT_FOOT = f"[comment run]({_RUN}). Comment `shipmate help` for the available commands."
_BARE = (
    "A bare `shipmate unlock` releases OpenTofu state locks only; this pull request's cell locks "
    "stay until it merges or closes, and `--evict` releases another pull request's."
)
_COMMENT = {"html_url": "https://github.com/org/repo/pull/9#issuecomment-555"}
_ID = "0b1c2d3e-aaaa-bbbb-cccc-0123456789ab"
_CELLS = [{"stack": s, "environment": "dev-eu"} for s in ("stacks/app", "stacks/db", "stacks/dns")]


@pytest.fixture
def gh(monkeypatch, tmp_path):
    """Recording stubs for the comment read (`ur.run`) and the POST (`rc.run`), each with the real
    `run` signature. Returns (posts, state): set `state["comment"]` to the read's answer (a
    `SystemExit` raises it) and `state["post"]` to a `SystemExit` to fail the POST."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "7777")
    monkeypatch.setenv("COMMENT_ID", "555")
    monkeypatch.setenv("SHIPMATE_ENV", "dev-eu")
    monkeypatch.setenv("SHIPMATE_DETECT_RESULT", "success")
    monkeypatch.setenv("SHIPMATE_DOWNLOAD_OUTCOME", "success")
    monkeypatch.setenv("SHIPMATE_CELLS", json.dumps(_CELLS))
    monkeypatch.setenv("SHIPMATE_RECORDS", str(tmp_path / "records"))
    posts = []
    state: dict[str, Any] = {"comment": _COMMENT, "post": None}

    def read(argv, secrets=(), stdin=None, *, env=None):
        assert argv == ["gh", "api", "repos/org/repo/issues/comments/555"]
        if isinstance(state["comment"], SystemExit):
            raise state["comment"]
        return json.dumps(state["comment"])

    def post(argv, secrets=(), stdin=None, *, env=None):
        if state["post"] is not None:
            raise state["post"]
        posts.append((argv[4], stdin))
        return "{}"

    monkeypatch.setattr(ur, "run", read)
    monkeypatch.setattr(ur.rc, "run", post)
    return posts, state


def _record(tmp_path, name, **rec):
    """Write record `rec` as `<name>.json`, where `download-artifact` merges every record."""
    d = tmp_path / "records"
    d.mkdir(exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(rec), encoding="utf-8")


def _records(tmp_path, *outcomes):
    """One well-formed record per cell index; None leaves that index without a record."""
    for i, outcome in enumerate(outcomes):
        if outcome is not None:
            lock_id = _ID if outcome == "released" else ""
            _record(tmp_path, f"shipmate-unlock-{i}", index=i, outcome=outcome, lock_id=lock_id)


def _body(outcome, text):
    foot = _HINT_FOOT if outcome == "failed" else _FOOT
    circle = "🔴 failed: " if outcome == "failed" else "⚪ "
    return f"### shipmate unlock dev-eu\n\n{circle}{text}\n\n{_BARE}\n\n{foot}"


def _post(outcome, text):
    return [("repos/org/repo/issues/9/comments", _body(outcome, text))]


def test_released_cells_are_listed_with_their_lock_ids(gh, tmp_path):
    """Mutations: read `none` as released (all three listed); drop the lock id."""
    posts, _ = gh
    _records(tmp_path, "released", "none", "released")
    ur.main()
    assert posts == _post(
        "notice", f"released 2 state lock(s) in dev-eu: stacks/app ({_ID}), stacks/dns ({_ID})."
    )


def test_every_probed_cell_without_a_lock_is_a_notice(gh, tmp_path):
    """Mutation: return the released list for an all-`none` run (`released 0 state lock`)."""
    posts, _ = gh
    _records(tmp_path, "none", "none", "none")
    ur.main()
    assert posts == _post(
        "notice", "released no state lock in dev-eu: no probed cell held a state lock."
    )


def test_an_empty_queue_names_unlock_detects_reason(gh, monkeypatch):
    """The reason is `unlock-detect.empty_queue`, the text its `::warning::` prints
    (test_unlock_detect.py pins that print whole). Mutations: return the `none held` text for an
    empty queue; change the `empty_queue` text (this test and test_unlock_detect.py both red)."""
    posts, _ = gh
    monkeypatch.setenv("SHIPMATE_CELLS", "[]")
    ur.main()
    assert posts == _post(
        "notice",
        "released no state lock in dev-eu: no cell in dev-eu has a pending apply check, so no "
        "lock was probed; a lock on a cell whose check already completed, or on a stack applied "
        "out of band, is released out of band; see the state-lock section of "
        "docs/troubleshooting.md.",
    )


def test_a_failed_probe_and_a_missing_leg_fail_the_reply_and_keep_the_released(gh, tmp_path):
    """Mutations: read `failed` as released; drop the `failed` clause; drop the `did not run`
    clause; post `notice` when a cell failed; drop the released clause from a failed reply; drop
    `failed` from reply-comment's footer hint outcomes."""
    posts, _ = gh
    _records(tmp_path, "failed", None, "released")
    ur.main()
    assert posts == _post(
        "failed",
        "could not determine the lock of stacks/app; stacks/db did not run; released 1 state "
        f"lock(s) in dev-eu: stacks/dns ({_ID}).",
    )


def test_no_records_at_all_reads_every_cell_as_not_run(gh, tmp_path):
    """No record directory exists. Mutation: read a missing record as `none`
    (`recs.get(i, ("none", ""))`)."""
    posts, _ = gh
    ur.main()
    assert posts == _post("failed", "stacks/app, stacks/db, stacks/dns did not run.")


def test_a_failed_detect_says_no_cell_was_probed(gh, monkeypatch, tmp_path):
    """A failed `detect` hands over no `cells`. Mutations: delete the `detect_result` check;
    parse `cells` whatever `detect`'s result (`''` is not JSON, so the reply names the download)."""
    posts, _ = gh
    monkeypatch.setenv("SHIPMATE_DETECT_RESULT", "failure")
    monkeypatch.setenv("SHIPMATE_CELLS", "")
    _records(tmp_path, "released", "released", "released")
    ur.main()
    assert posts == _post("failed", "the unlock run failed before probing any cell; see the run.")


def test_a_failed_download_is_reported_not_read_as_cells_that_did_not_run(gh, monkeypatch):
    """Mutation: drop the `download_outcome == "failure"` branch (every cell reads `did not
    run`)."""
    posts, _ = gh
    monkeypatch.setenv("SHIPMATE_DOWNLOAD_OUTCOME", "failure")
    ur.main()
    assert posts == _post("failed", "could not read the cells' results; see the run.")


@pytest.mark.parametrize(
    "name, rec",
    [
        ("shipmate-unlock-1", {"index": 0, "outcome": "released", "lock_id": _ID}),
        ("shipmate-unlock-3", {"index": 3, "outcome": "released", "lock_id": _ID}),
        ("shipmate-unlock-1", {"index": True, "outcome": "released", "lock_id": _ID}),
        ("shipmate-unlock-1", {"index": 1, "outcome": "cleared", "lock_id": _ID}),
        ("shipmate-unlock-01", {"index": 1, "outcome": "released", "lock_id": _ID}),
        ("shipmate-unlock-1-x", {"index": 1, "outcome": "released", "lock_id": _ID}),
    ],
)
def test_a_record_that_fails_a_check_reads_as_did_not_run(gh, tmp_path, name, rec):
    """A record's cell is its file name's index, which must match the record's `index`; an
    index outside `cells` names no cell, since only `cells` is iterated. Mutations, one per case
    but the out-of-range one: drop `rec["index"] != index`; `type(...) is not int` to
    `not isinstance(..., int)` (`true` reads as 1); drop the outcome check (`cleared` reads as no
    lock held); a name pattern of `([0-9]+)` (`shipmate-unlock-01` reads as 1); `fullmatch` to
    `match` (`shipmate-unlock-1-x` reads as 1)."""
    posts, _ = gh
    _records(tmp_path, "none", None, "none")
    _record(tmp_path, name, **rec)
    ur.main()
    assert posts == _post("failed", "stacks/db did not run.")


def test_three_records_all_count(gh, tmp_path):
    """Mutation: read only the first record found (`break` after `out[index] = ...`)."""
    posts, _ = gh
    _records(tmp_path, "released", "released", "released")
    ur.main()
    assert posts == _post(
        "notice",
        f"released 3 state lock(s) in dev-eu: stacks/app ({_ID}), stacks/db ({_ID}), "
        f"stacks/dns ({_ID}).",
    )


def test_a_lock_id_outside_the_charset_is_dropped(gh, tmp_path):
    """Mutation: drop the `_ID_RE` check (the forged id renders as a link)."""
    posts, _ = gh
    _records(tmp_path, "none", None, "none")
    _record(tmp_path, "shipmate-unlock-1", index=1, outcome="released", lock_id="[x](https://e)")
    ur.main()
    assert posts == _post("notice", "released 1 state lock(s) in dev-eu: stacks/db.")


def test_a_hostile_stack_name_renders_escaped(gh, monkeypatch, tmp_path):
    """Stack names come from the pull request's tree. Mutation: render `c["stack"]` without
    `_md_name` (a link and a team mention)."""
    posts, _ = gh
    cells = [{"stack": "[x](https://e)"}, {"stack": "@org/team"}]
    monkeypatch.setenv("SHIPMATE_CELLS", json.dumps(cells))
    _records(tmp_path, "released", "failed")
    ur.main()
    assert posts == _post(
        "failed",
        "could not determine the lock of @<!-- -->org/team; released 1 state lock(s) in dev-eu: "
        f"&#91;x&#93;(https://e) ({_ID}).",
    )


@pytest.mark.parametrize(
    "answer",
    [
        {"html_url": "https://github.com/org/repo/issues/9#issuecomment-555"},
        {"html_url": "https://github.com/org/repo/pull/9#issuecomment-556"},
        {"html_url": "https://github.com/other/repo/pull/9#issuecomment-555"},
        {"html_url": "https://github.com/org/repo/pull/09#issuecomment-555"},
        {},
        [],
        SystemExit("::error::gh api failed\nHTTP 404"),
    ],
)
def test_a_comment_not_on_this_pull_request_posts_nothing_and_exits_0(gh, capsys, answer):
    """Mutations: `/issues/` for `/pull/` in the expected URL (the issue comment posts); match
    `/pull/<n>#` with `re.search` (the id-mismatched URL and another repository's post); drop
    `is_pr_number` (`09` posts on `issues/09`); drop the `isinstance(url, str)` guard (`{}`
    raises); drop `AttributeError` from the read's `except` (a list answer raises); drop
    `SystemExit` from it (a failed read exits 1)."""
    posts, state = gh
    state["comment"] = answer
    ur.main()
    assert posts == []
    assert capsys.readouterr().out.startswith("::warning::")


def test_a_malformed_comment_id_reads_nothing(gh, monkeypatch, capsys):
    """Bound into the API path. Mutation: drop the `_COMMENT_ID` check (the read stub's argv
    assertion fails)."""
    posts, _ = gh
    monkeypatch.setenv("COMMENT_ID", "555/../../x")
    ur.main()
    assert posts == []
    assert capsys.readouterr().out == (
        "::warning::comment_id '555/../../x' is not a comment id; no unlock reply posted\n"
    )


def test_a_failed_post_warns_and_exits_0(gh, tmp_path, capsys):
    """Mutation: catch `ValueError` instead of `SystemExit` around `rc.post` (it escapes)."""
    _, state = gh
    state["post"] = SystemExit("::error::gh api -X POST failed\nHTTP 403")
    _records(tmp_path, "none", "none", "none")
    ur.main()
    assert capsys.readouterr().out == (
        "::warning::could not post the unlock reply on #9: ::error::gh api -X POST failed | "
        "HTTP 403\n"
    )
