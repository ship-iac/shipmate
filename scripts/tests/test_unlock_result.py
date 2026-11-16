"""`scripts/unlock-result`: the one reply an unlock run posts, compared whole against
hand-written text, and the records it reads it from.

Threat model: accidental regression -- an outcome rendered as another, a record read under the
wrong cell, a stack name rendered raw, a failure that reds the run or posts nothing.
"""

import io
import json
from typing import Any

import pytest
from _loader import load_script

ur = load_script("unlock-result")

_RUN = "https://github.com/org/repo/actions/runs/7777"
_FOOT = f"[unlock run]({_RUN})"
_HINT_FOOT = f"[unlock run]({_RUN}). Comment `shipmate help` for the available commands."
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


def _write(tmp_path, name, text, *, folder=None, flat=False):
    """Write `text` as `<name>.json` in the artifact directory `folder` (default `name`), or
    straight into the download path when `flat`."""
    d = tmp_path / "records"
    if not flat:
        d = d / (folder or name)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(text, encoding="utf-8")


def _rec(leg, outcome, /, cells=_CELLS, **over):
    """The record leg `leg` writes for `cells[leg]`, with `over` replacing fields."""
    rec = {
        "index": leg,
        "stack": cells[leg]["stack"],
        "environment": cells[leg]["environment"],
        "outcome": outcome,
        "lock_id": _ID if outcome == "released" else "",
    }
    return {**rec, **over}


def _records(tmp_path, *outcomes, cells=_CELLS):
    """One well-formed record per cell index, each in its artifact directory; None leaves that
    index without a record."""
    for i, outcome in enumerate(outcomes):
        if outcome is not None:
            _write(tmp_path, f"shipmate-unlock-{i}", json.dumps(_rec(i, outcome, cells)))


def _body(outcome, text):
    foot = _HINT_FOOT if outcome == "failed" else _FOOT
    circle = "🔴 failed: " if outcome == "failed" else "⚪ "
    return f"### shipmate unlock dev-eu\n\n{circle}{text}\n\n{_BARE}\n\n{foot}"


def _post(outcome, text):
    return [("repos/org/repo/issues/9/comments", _body(outcome, text))]


def test_released_cells_are_listed_with_their_lock_ids(gh, tmp_path):
    """The footer names the unlock run it links. Mutations: read `none` as released (all three
    listed); drop the lock id; drop the `unlock run` label (the footer reads `comment run`)."""
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


def test_a_failed_detect_says_no_cell_was_probed_and_reads_no_record(gh, monkeypatch, tmp_path):
    """A failed `detect` hands over no `cells`. Mutations: delete the `detect_result` check;
    parse `cells` and read the records before checking `detect` (`records` is called)."""
    posts, _ = gh
    monkeypatch.setenv("SHIPMATE_DETECT_RESULT", "failure")
    monkeypatch.setenv("SHIPMATE_CELLS", json.dumps(_CELLS))
    _records(tmp_path, "released", "released", "released")

    def records(*args):
        raise AssertionError("records read after a failed detect")

    monkeypatch.setattr(ur, "records", records)
    ur.main()
    assert posts == _post("failed", "the unlock run failed before probing any cell; see the run.")


@pytest.mark.parametrize(
    "raw",
    ["not json", '{"stack": "a"}', '[{"stack": 1, "environment": "dev-eu"}]', '[{"stack": "a"}]'],
)
def test_an_unreadable_queue_says_so(gh, monkeypatch, raw):
    """Mutations: return the download text for an unparsable queue; drop the `environment`
    check (`[{"stack": "a"}]` reads as a queue whose cell did not run)."""
    posts, _ = gh
    monkeypatch.setenv("SHIPMATE_CELLS", raw)
    ur.main()
    assert posts == _post("failed", "could not read the unlock queue; see the run.")


def test_a_failed_download_still_reads_the_records_that_arrived(gh, monkeypatch, tmp_path):
    """A cell with no record has its result unknown, not "did not run": its record may be among
    those that failed to download. Mutations: return early on a failed download (the arrived
    records are lost); drop the `could not be read` clause; render `did not run` whatever the
    download did."""
    posts, _ = gh
    monkeypatch.setenv("SHIPMATE_DOWNLOAD_OUTCOME", "failure")
    _records(tmp_path, "released", None, "none")
    ur.main()
    assert posts == _post(
        "failed",
        f"result unknown for stacks/db; released 1 state lock(s) in dev-eu: stacks/app ({_ID}); "
        "some cells' results could not be read; see the run.",
    )


def test_a_failed_download_fails_the_reply_even_with_every_record(gh, monkeypatch, tmp_path):
    """Mutation: drop `and not unread` (a notice says no lock was held)."""
    posts, _ = gh
    monkeypatch.setenv("SHIPMATE_DOWNLOAD_OUTCOME", "failure")
    _records(tmp_path, "none", "none", "none")
    ur.main()
    assert posts == _post("failed", "some cells' results could not be read; see the run.")


_BAD = [
    ("shipmate-unlock-1", _rec(1, "released", index=0)),
    ("shipmate-unlock-3", {**_rec(1, "released"), "index": 3}),
    ("shipmate-unlock-1", _rec(1, "released", index=True)),
    ("shipmate-unlock-1", _rec(1, "cleared")),
    ("shipmate-unlock-1", _rec(1, "released", stack="stacks/app")),
    ("shipmate-unlock-1", _rec(1, "released", environment="prod")),
    ("shipmate-unlock-01", _rec(1, "released")),
    ("shipmate-unlock-1-x", _rec(1, "released")),
]


@pytest.mark.parametrize("name, rec", _BAD)
def test_a_record_that_fails_a_check_reads_as_did_not_run(gh, tmp_path, name, rec):
    """A record's cell is its artifact name's index, which its file name and `index` must match,
    and its `stack` and `environment` must be that cell's. Mutations, one per case: drop
    `rec["index"] != index`; drop the `len(cells)` bound (IndexError); `type(...) is not int` to
    `not isinstance(..., int)` (`true` reads as 1); drop the outcome check (`cleared` reads as no
    lock held); drop the `stack` check; drop the `environment` check; a name pattern of
    `([0-9]+)` (`shipmate-unlock-01` reads as 1); `fullmatch` to `match` (`shipmate-unlock-1-x`
    reads as 1)."""
    posts, _ = gh
    _records(tmp_path, "none", None, "none")
    _write(tmp_path, name, json.dumps(rec))
    ur.main()
    assert posts == _post("failed", "stacks/db did not run.")


def test_an_artifact_holding_a_siblings_file_name_is_ignored(gh, tmp_path):
    """Artifact `shipmate-unlock-1` is read only as `shipmate-unlock-1.json`. Mutation: read any
    `.json` in the directory (`next(d.glob("*.json"))`)."""
    posts, _ = gh
    _records(tmp_path, "none", None, "none")
    _write(tmp_path, "shipmate-unlock-0", json.dumps(_rec(1, "none")), folder="shipmate-unlock-1")
    ur.main()
    assert posts == _post("failed", "stacks/db did not run.")


@pytest.mark.parametrize("flat", [1, 2])
def test_a_record_file_outside_an_artifact_directory_is_ignored(gh, tmp_path, flat):
    """The sentinel artifact makes every download two or more artifacts, so each gets its own
    directory; a file at the download root is never a record. Mutation: read a root
    `shipmate-unlock-<n>.json` as the record of `<n>` (match on its stem, read the file itself)."""
    posts, _ = gh
    for i in range(flat):
        _write(tmp_path, f"shipmate-unlock-{i}", json.dumps(_rec(i, "none")), flat=True)
    ur.main()
    assert posts == _post("failed", "stacks/app, stacks/db, stacks/dns did not run.")


def test_an_artifact_holding_only_subdirectories_claims_nothing(gh, tmp_path):
    """Artifact `shipmate-unlock-1` whose `shipmate-unlock-1.json` is a directory holding a valid
    record. Mutation: walk the artifact directory for the first `.json` file in it."""
    posts, _ = gh
    _records(tmp_path, "none", None, "none")
    nested = tmp_path / "records" / "shipmate-unlock-1" / "shipmate-unlock-1.json"
    nested.mkdir(parents=True)
    (nested / "shipmate-unlock-1.json").write_text(json.dumps(_rec(1, "none")), encoding="utf-8")
    ur.main()
    assert posts == _post("failed", "stacks/db did not run.")


class _Spy:
    """A stand-in record path whose file records every `read` size, or raises `fail`."""

    def __init__(self, data, fail=None):
        self.data, self.fail, self.reads = data, fail, []

    def open(self, mode):
        assert mode == "rb"
        spy = self

        class File(io.BytesIO):
            def read(self, size=-1):
                spy.reads.append(size)
                if spy.fail is not None:
                    raise spy.fail
                return super().read(size)

        return File(self.data)


def test_the_reader_never_reads_past_the_bound():
    """Mutation: `fh.read()` (the whole file is read before the size check)."""
    spy = _Spy(b"x" * (ur.RECORD_MAX * 4))
    assert ur._record(spy, 1, _CELLS[1]) is None
    assert spy.reads == [ur.RECORD_MAX + 1]


@pytest.mark.parametrize("fail", [MemoryError(), OSError("I/O error")])
def test_a_record_that_cannot_be_read_is_invalid(fail):
    """Mutations: drop `MemoryError` from the `except`; drop `OSError` from it."""
    assert ur._record(_Spy(b"", fail), 1, _CELLS[1]) is None


def test_three_records_all_count(gh, tmp_path):
    """Mutation: read only the first record found (`break` after `out[...] = rec`)."""
    posts, _ = gh
    _records(tmp_path, "released", "released", "released")
    ur.main()
    assert posts == _post(
        "notice",
        f"released 3 state lock(s) in dev-eu: stacks/app ({_ID}), stacks/db ({_ID}), "
        f"stacks/dns ({_ID}).",
    )


def test_an_oversized_record_is_never_decoded(gh, tmp_path):
    """A valid record padded with whitespace past the bound: its first `RECORD_MAX + 1` bytes
    still decode. Mutation: drop the `RECORD_MAX` bound (the padded record counts)."""
    posts, _ = gh
    _records(tmp_path, "none", None, "none")
    _write(tmp_path, "shipmate-unlock-1", json.dumps(_rec(1, "none")) + " " * ur.RECORD_MAX)
    ur.main()
    assert posts == _post("failed", "stacks/db did not run.")


def test_a_deeply_nested_record_reads_as_did_not_run(gh, tmp_path):
    """Regression: the reply still posts and `main` returns. Mutation: read the whole file, drop
    the bound and drop `RecursionError` from the `except` (the decoder's `RecursionError`
    escapes)."""
    posts, _ = gh
    _records(tmp_path, "none", None, "none")
    _write(tmp_path, "shipmate-unlock-1", "[" * 100_000 + "]" * 100_000)
    ur.main()
    assert posts == _post("failed", "stacks/db did not run.")


def test_a_record_the_decoder_cannot_nest_reads_as_did_not_run(gh, monkeypatch, tmp_path):
    """Within the bound on some Python versions, a nested record exhausts the decoder's
    recursion (3.12 at a depth of about 1,000). Bound lifted here to reach the decoder.
    Mutation: drop `RecursionError` from the `except`."""
    posts, _ = gh
    monkeypatch.setattr(ur, "RECORD_MAX", 10**6)
    _records(tmp_path, "none", None, "none")
    _write(tmp_path, "shipmate-unlock-1", "[" * 100_000 + "]" * 100_000)
    ur.main()
    assert posts == _post("failed", "stacks/db did not run.")


def test_a_lock_id_outside_the_charset_is_dropped(gh, tmp_path):
    """Mutation: drop the `_ID_RE` check (the forged id renders as a link)."""
    posts, _ = gh
    _records(tmp_path, "none", None, "none")
    _write(tmp_path, "shipmate-unlock-1", json.dumps(_rec(1, "released", lock_id="[x](https://e)")))
    ur.main()
    assert posts == _post("notice", "released 1 state lock(s) in dev-eu: stacks/db.")


def test_a_hostile_stack_name_renders_escaped(gh, monkeypatch, tmp_path):
    """Stack names come from the pull request's tree. Mutation: render `c["stack"]` without
    `_md_name` (a link and a team mention)."""
    posts, _ = gh
    cells = [{"stack": "[x](https://e)", "environment": "dev-eu"}]
    cells.append({"stack": "@org/team", "environment": "dev-eu"})
    monkeypatch.setenv("SHIPMATE_CELLS", json.dumps(cells))
    _records(tmp_path, "released", "failed", cells=cells)
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
