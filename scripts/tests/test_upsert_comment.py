"""Tests for scripts/upsert-comment: the marker lookup, write, minimize and one-word result."""

import json
import os
import shutil
import subprocess
import sys

import pytest
from _loader import (
    ACTIONS,
    SCRIPTS,
    bash_only,
    load_script,
    run_with_gh_recorder,
    step_by,
)

uc = load_script("upsert-comment")

SUMMARY = "<!-- shipmate:summary -->"
DOCTOR = "<!-- shipmate:doctor -->"
_FILTER = (
    '.[] | select(.user.type == "Bot") | '
    '{id, node_id, login: .user.login, head: ((.body // "")[0:64])}'
)
LIST_ARGV = [
    "gh",
    "api",
    "--paginate",
    "--jq",
    _FILTER,
    "repos/o/r/issues/7/comments?per_page=100",
]


def _head(cid, head, login="shipmate[bot]"):
    return json.dumps({"id": cid, "node_id": f"IC_{cid}", "login": login, "head": head})


#: A pull request head, and the `--head` read's whole argv.
_HEAD = "c" * 40
PULLS_ARGV = ["gh", "api", "repos/o/r/pulls/7"]

#: The `--new` GraphQL texts, hand-written.
VISIBILITY = "query($ids: [ID!]!) { nodes(ids: $ids) { ... on IssueComment { id isMinimized } } }"
MINIMIZE = (
    "mutation($id: ID!) { minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) "
    "{ minimizedComment { isMinimized } } }"
)


def _read(*node_ids):
    """The whole argv of one visibility read of `node_ids`."""
    ids = [arg for n in node_ids for arg in ("-F", f"ids[]={n}")]
    return ["gh", "api", "graphql", "-f", f"query={VISIBILITY}", *ids]


def _minimize(node_id):
    return ["gh", "api", "graphql", "-f", f"query={MINIMIZE}", "-f", f"id={node_id}"]


def _fake_graphql(
    args, minimized=(), visibility_fails=False, visibility_answer=None, minimize_fails=()
):
    """A visibility read answers `isMinimized` true for the node ids in `minimized`, prints
    `visibility_answer` when given, or fails. A minimize of a node id in `minimize_fails` fails."""
    if args[4] == f"query={VISIBILITY}":
        if visibility_fails:
            raise SystemExit("command failed (1): gh api graphql\nHTTP 502")
        if visibility_answer is not None:
            return visibility_answer
        ids = [a.removeprefix("ids[]=") for a in args[6::2]]
        nodes = [{"id": n, "isMinimized": n in minimized} for n in ids]
        return json.dumps({"data": {"nodes": nodes}})
    assert args[4] == f"query={MINIMIZE}", args
    if args[6].removeprefix("id=") in minimize_fails:
        raise SystemExit("command failed (1): gh api graphql\nFORBIDDEN")
    return '{"data": {"minimizeComment": {"minimizedComment": {"isMinimized": true}}}}'


def _fake_head(head, fails):
    if fails:
        raise SystemExit("command failed (1): gh api\nHTTP 502\r::add-mask::x\r\n")
    return json.dumps({"head": {"sha": head}, "draft": False, "state": "open"})


def _upsert(
    monkeypatch,
    tmp_path,
    listing,
    *flags,
    first_line=SUMMARY,
    list_fails=False,
    write_fails=False,
    pr="7",
    head=_HEAD,
    head_fails=False,
    post_response='{"id": 99, "body": "response", "user": {"login": "shipmate[bot]"}}',
    **graphql,
):
    """Run `main` in-process against a fake API; return (exit code, `run` calls). The pull
    request's head reads as `head`, or fails with a gh error holding a CR and a command. A POST
    answers `post_response`; `graphql` goes to `_fake_graphql`."""
    body = tmp_path / "comment.md"
    body.write_text(f"{first_line}\n### shipmate plan\n", encoding="utf-8")
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[2].startswith("repos/o/r/pulls/"):
            return _fake_head(head, head_fails)
        if args[2] == "--paginate":
            if list_fails:
                raise SystemExit("command failed (1): gh api\nHTTP 502")
            return "".join(line + "\n" for line in listing)
        if args[2] == "graphql":
            return _fake_graphql(args, **graphql)
        if write_fails:
            raise SystemExit("command failed (1): gh api\nHTTP 403")
        return post_response

    monkeypatch.setattr(uc, "run", fake_run)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(sys, "argv", ["upsert-comment", pr, str(body), *flags])
    try:
        uc.main()
    except SystemExit as exc:
        return exc.code, calls
    return 0, calls


def _patch(cid, tmp_path):
    return [
        "gh",
        "api",
        "-X",
        "PATCH",
        f"repos/o/r/issues/comments/{cid}",
        "-F",
        f"body=@{tmp_path / 'comment.md'}",
    ]


def _post(tmp_path):
    return ["gh", "api", "repos/o/r/issues/7/comments", "-F", f"body=@{tmp_path / 'comment.md'}"]


def test_a_match_is_patched_and_prints_updated(monkeypatch, tmp_path, capsys):
    """The whole listing and PATCH argv, hand-written; stdout is the one word, never gh's response.

    Mutations: drop `per_page=100`; swap the PATCH and POST branches; print the PATCH's return.
    """
    code, calls = _upsert(monkeypatch, tmp_path, [_head(5, SUMMARY + "\n### shipmate plan")])
    assert code == 0
    assert calls == [LIST_ARGV, _patch(5, tmp_path)]
    assert capsys.readouterr().out == "updated\n"


def test_no_match_is_posted_and_prints_created(monkeypatch, tmp_path, capsys):
    """Mutations: swap the PATCH and POST branches; print the POST's return."""
    code, calls = _upsert(monkeypatch, tmp_path, [])
    assert code == 0
    assert calls == [LIST_ARGV, _post(tmp_path)]
    assert capsys.readouterr().out == "created\n"


def test_a_comment_quoting_the_marker_mid_body_is_not_matched(monkeypatch, tmp_path, capsys):
    """Mutation: `startswith` -> `in`."""
    code, calls = _upsert(monkeypatch, tmp_path, [_head(5, f"### shipmate doctor\n`{SUMMARY}`")])
    assert code == 0
    assert calls == [LIST_ARGV, _post(tmp_path)]
    assert capsys.readouterr().out == "created\n"


def test_the_first_match_wins_across_pages(monkeypatch, tmp_path):
    """`--paginate` runs `--jq` per page and concatenates the lines, so pages arrive as one list.

    Mutation: iterate the listing reversed.
    """
    listing = [_head(1, "hello"), _head(5, SUMMARY), _head(6, SUMMARY + "x")]
    code, calls = _upsert(monkeypatch, tmp_path, listing)
    assert code == 0
    assert calls == [LIST_ARGV, _patch(5, tmp_path)]


def test_update_only_with_no_match_writes_nothing(monkeypatch, tmp_path, capsys):
    """Mutation: ignore `--update-only`."""
    code, calls = _upsert(monkeypatch, tmp_path, [], "--update-only")
    assert code == 0
    assert calls == [LIST_ARGV]
    assert capsys.readouterr().out == "skipped\n"


def test_update_only_with_a_match_still_patches(monkeypatch, tmp_path, capsys):
    """Mutation: treat `--update-only` as "write nothing"."""
    code, calls = _upsert(monkeypatch, tmp_path, [_head(5, SUMMARY)], "--update-only")
    assert code == 0
    assert calls == [LIST_ARGV, _patch(5, tmp_path)]
    assert capsys.readouterr().out == "updated\n"


def test_a_listing_failure_prints_list_failed_and_writes_nothing(monkeypatch, tmp_path, capsys):
    """`_shipmate.run` raises SystemExit on a failed `gh`; the caller decides on `list-failed`.

    Mutation: drop the `except SystemExit` around the listing.
    """
    code, calls = _upsert(monkeypatch, tmp_path, [], list_fails=True)
    assert code == 0
    assert calls == [LIST_ARGV]
    out, err = capsys.readouterr()
    assert out == "list-failed\n"
    assert err == "command failed (1): gh api | HTTP 502\n"


@pytest.mark.parametrize("listing", [[], [_head(5, SUMMARY)]], ids=["post", "patch"])
def test_a_head_match_writes(monkeypatch, tmp_path, capsys, listing):
    """The head is read after the listing and before the write. Mutation: skip the head read
    before a write -- no pulls call."""
    code, calls = _upsert(monkeypatch, tmp_path, listing, "--head", _HEAD)
    write = _post(tmp_path) if not listing else _patch(5, tmp_path)
    assert (code, calls) == (0, [LIST_ARGV, PULLS_ARGV, write])
    assert capsys.readouterr().out == ("created\n" if not listing else "updated\n")


def test_a_moved_head_prints_moved_and_writes_nothing(monkeypatch, tmp_path, capsys):
    """Mutation: compare `!=` -> `==` in `_head_stop` -- red."""
    code, calls = _upsert(monkeypatch, tmp_path, [], "--head", "d" * 40)
    assert (code, calls) == (0, [LIST_ARGV, PULLS_ARGV])
    assert capsys.readouterr() == ("moved\n", "")


def test_a_failed_head_read_prints_head_failed_on_one_stderr_line(monkeypatch, tmp_path, capsys):
    """gh's error, a CR-led `::add-mask::` included, stays on one line, so none of it starts a
    workflow command. Mutations: drop the CR from `one_line`'s split -- `::add-mask::x` starts a
    line; print the raw error -- red."""
    code, calls = _upsert(monkeypatch, tmp_path, [], "--head", _HEAD, head_fails=True)
    assert (code, calls) == (0, [LIST_ARGV, PULLS_ARGV])
    assert capsys.readouterr() == (
        "head-failed\n",
        "command failed (1): gh api | HTTP 502 | ::add-mask::x\n",
    )


def test_a_null_head_prints_head_failed_not_moved(monkeypatch, tmp_path, capsys):
    """A pull request read with no head SHA is a failed read, not a moved head. Mutation: skip
    the SHA check in `_shipmate.pr_head` -- `moved`."""
    code, calls = _upsert(monkeypatch, tmp_path, [], "--head", _HEAD, head=None)
    assert (code, calls) == (0, [LIST_ARGV, PULLS_ARGV])
    assert capsys.readouterr() == (
        "head-failed\n",
        "pull request #7 has head SHA None and draft False\n",
    )


def test_update_only_with_no_match_never_reads_the_head(monkeypatch, tmp_path, capsys):
    """Nothing would be written, so a docs-only run makes no head read. Mutation: read the head
    before the `skipped` exit -- a pulls call."""
    code, calls = _upsert(monkeypatch, tmp_path, [], "--update-only", "--head", _HEAD)
    assert (code, calls) == (0, [LIST_ARGV])
    assert capsys.readouterr().out == "skipped\n"


@pytest.mark.parametrize("head", ["C" * 40, "c" * 39, "../x"])
def test_a_head_that_is_not_a_sha_is_refused_before_any_call(monkeypatch, tmp_path, head):
    """Mutation: drop the `is_head_sha` check -- red."""
    code, calls = _upsert(monkeypatch, tmp_path, [], "--head", head)
    assert (code, calls) == (
        f"::error::--head must be a 40-char lowercase hex SHA (got: {head})",
        [],
    )


def test_a_write_failure_exits_non_zero_with_an_error(monkeypatch, tmp_path, capsys):
    """Mutation: catch the write failure and print `created`."""
    code, calls = _upsert(monkeypatch, tmp_path, [], write_fails=True)
    assert code == "::error::could not write the comment: command failed (1): gh api\nHTTP 403"
    assert calls == [LIST_ARGV, _post(tmp_path)]
    assert capsys.readouterr().out == ""


def test_the_marker_is_the_body_files_first_line(monkeypatch, tmp_path, capsys):
    """A doctor body does not match the summary sticky.

    Mutation: hard-code the summary marker.
    """
    code, calls = _upsert(monkeypatch, tmp_path, [_head(5, SUMMARY)], first_line=DOCTOR)
    assert code == 0
    assert calls == [LIST_ARGV, _post(tmp_path)]
    assert capsys.readouterr().out == "created\n"


@pytest.mark.parametrize(
    "first_line",
    [
        "### shipmate plan",
        f"{SUMMARY} trailing",
        "<!-- shipmate:Summary -->",
        f"<!-- shipmate:{'a' * 41} -->",
        "",
    ],
)
def test_a_first_line_that_is_not_a_whole_marker_is_refused_before_any_call(
    monkeypatch, tmp_path, first_line
):
    """Mutations: `fullmatch` -> `match`; drop the `{1,40}` bound."""
    code, calls = _upsert(monkeypatch, tmp_path, [], first_line=first_line)
    path = tmp_path / "comment.md"
    assert code == f"::error::{path}: the first line must be a shipmate marker (got: {first_line})"
    assert calls == []


@pytest.mark.parametrize("pr", ["7a", "", "../1", "0", "007"])
def test_a_value_that_is_not_a_pull_request_number_is_refused_before_any_call(
    monkeypatch, tmp_path, pr
):
    """`PR` is bound into the API path. Mutations: delete the check; check with `isdigit()`
    (`0` and `007` red)."""
    code, calls = _upsert(monkeypatch, tmp_path, [], pr=pr)
    assert code == f"::error::PR must be a pull request number (got: {pr})"
    assert calls == []


def test_the_pull_request_number_is_checked_before_the_body_file_is_read(monkeypatch, tmp_path):
    """Mutation: open BODYFILE above the PR check (FileNotFoundError instead of the refusal)."""
    missing = tmp_path / "missing.md"
    monkeypatch.setattr(uc, "run", lambda args: pytest.fail(f"unexpected gh call: {args}"))
    monkeypatch.setattr(sys, "argv", ["upsert-comment", "0", str(missing)])
    with pytest.raises(SystemExit) as exc:
        uc.main()
    assert exc.value.code == "::error::PR must be a pull request number (got: 0)"


@pytest.mark.parametrize(
    "line",
    ["not json", '{"id": 5}', '{"head": "x"}', '{"id": 5, "head": null}', "[1]", "5"],
)
def test_an_unparseable_listing_line_prints_list_failed_and_writes_nothing(
    monkeypatch, tmp_path, capsys, line
):
    """The doctor step degrades on `list-failed`; a traceback would fail it instead.

    Mutation: drop the `try`/`except` around the line's parse.
    """
    code, calls = _upsert(monkeypatch, tmp_path, [line])
    assert code == 0
    assert calls == [LIST_ARGV]
    assert capsys.readouterr() == (
        "list-failed\n",
        f"unparseable comment listing line: {line!r}\n",
    )


@pytest.mark.parametrize(
    "flags",
    [
        ["--update"],
        ["--update-only", "x"],
        ["--update-only", "--update-only"],
        ["--head"],
        ["--head", "c" * 40, "--head", "c" * 40],
        ["--new", "--new"],
    ],
)
def test_unknown_arguments_are_refused_before_any_call(monkeypatch, tmp_path, flags):
    """Mutations: accept any third argument as `--update-only`; accept `--new` repeated
    (`--new --new` red)."""
    code, calls = _upsert(monkeypatch, tmp_path, [], *flags)
    assert code == "::error::usage: upsert-comment PR BODYFILE [--new] [--update-only] [--head SHA]"
    assert calls == []


#: `--new`'s two stderr warnings for pull request #7, hand-written up to the error.
_READ_WARNING = (
    "::warning::could not read which plan comments of pull request #7 are minimized, so all "
    "count as visible: "
)
_MINIMIZE_WARNING = (
    "::warning::could not minimize an earlier plan comment of pull request #7; it stays visible: "
)


def test_new_posts_then_minimizes_the_earlier_visible_comment(monkeypatch, tmp_path, capsys):
    """The pull request never shows zero plan comments. Mutation: minimize before the POST --
    the recorded order differs."""
    code, calls = _upsert(monkeypatch, tmp_path, [_head(5, SUMMARY)], "--new")
    assert (code, calls) == (0, [LIST_ARGV, _read("IC_5"), _post(tmp_path), _minimize("IC_5")])
    assert capsys.readouterr() == ("created\n", "")


def test_new_leaves_a_comment_graphql_reads_as_minimized(monkeypatch, tmp_path, capsys):
    """Visibility comes only from the GraphQL answer; the listing rows carry no `minimized`.
    Mutations: read visibility from `row.get("minimized")` instead of the read -- no read, and
    IC_5 is minimized again; drop the visibility filter after the read -- IC_5 is minimized."""
    listing = [_head(5, SUMMARY), _head(6, SUMMARY)]
    code, calls = _upsert(monkeypatch, tmp_path, listing, "--new", minimized={"IC_5"})
    assert (code, calls) == (
        0,
        [LIST_ARGV, _read("IC_5", "IC_6"), _post(tmp_path), _minimize("IC_6")],
    )
    assert capsys.readouterr() == ("created\n", "")


def test_new_reads_visibility_in_chunks_of_100(monkeypatch, tmp_path, capsys):
    """`nodes(ids:)` takes at most 100 ids. Mutation: one read of all ids."""
    listing = [_head(i, SUMMARY) for i in range(1, 102)]
    hidden = {f"IC_{i}" for i in range(1, 102)}
    code, calls = _upsert(monkeypatch, tmp_path, listing, "--new", minimized=hidden)
    first = _read(*(f"IC_{i}" for i in range(1, 101)))
    assert (code, calls) == (0, [LIST_ARGV, first, _read("IC_101"), _post(tmp_path)])
    assert capsys.readouterr() == ("created\n", "")


_NULL_NODE = '{"data": {"nodes": [null, null]}}'
_NO_FLAG = '{"data": {"nodes": [{"id": "IC_5"}, {"id": "IC_6", "isMinimized": true}]}}'


@pytest.mark.parametrize(
    ("fails", "answer", "error"),
    [
        (True, None, "command failed (1): gh api graphql | HTTP 502"),
        (False, _NULL_NODE, f"unexpected answer: {_NULL_NODE}"),
        (False, _NO_FLAG, f"unexpected answer: {_NO_FLAG}"),
    ],
    ids=["gh-error", "null-node", "no-isMinimized"],
)
def test_new_a_failed_visibility_read_counts_every_comment_visible(
    monkeypatch, tmp_path, capsys, fails, answer, error
):
    """The run still posts, then minimizes every same-author marker comment. Mutations:
    `sys.exit` on a failed read -- no POST; treat a failed read as "all minimized" -- no
    minimize call."""
    listing = [_head(5, SUMMARY), _head(6, SUMMARY)]
    code, calls = _upsert(
        monkeypatch,
        tmp_path,
        listing,
        "--new",
        visibility_fails=fails,
        visibility_answer=answer,
    )
    assert (code, calls) == (
        0,
        [
            LIST_ARGV,
            _read("IC_5", "IC_6"),
            _post(tmp_path),
            _minimize("IC_5"),
            _minimize("IC_6"),
        ],
    )
    assert capsys.readouterr() == ("created\n", _READ_WARNING + error + "\n")


def test_new_update_only_posts_after_a_failed_visibility_read(monkeypatch, tmp_path, capsys):
    """Mutation: treat a failed read as "no visible comment" -- `skipped`."""
    code, calls = _upsert(
        monkeypatch, tmp_path, [_head(5, SUMMARY)], "--new", "--update-only", visibility_fails=True
    )
    assert (code, calls) == (0, [LIST_ARGV, _read("IC_5"), _post(tmp_path), _minimize("IC_5")])
    assert capsys.readouterr() == (
        "created\n",
        _READ_WARNING + "command failed (1): gh api graphql | HTTP 502\n",
    )


def test_new_minimizes_only_this_authors_plan_comments(monkeypatch, tmp_path, capsys):
    """Another App's plan comment, the same App's doctor report quoting the summary marker after
    its own, and a reply are left alone. Mutations: drop the author compare -- IC_6 minimized;
    `startswith` -> `in` -- IC_7 read and minimized."""
    listing = [
        _head(5, SUMMARY + "\n### shipmate plan"),
        _head(6, SUMMARY, login="other-app[bot]"),
        _head(7, DOCTOR + SUMMARY),
        _head(8, "### shipmate apply"),
    ]
    code, calls = _upsert(monkeypatch, tmp_path, listing, "--new")
    assert (code, calls) == (
        0,
        [LIST_ARGV, _read("IC_5", "IC_6"), _post(tmp_path), _minimize("IC_5")],
    )
    assert capsys.readouterr() == ("created\n", "")


def test_new_update_only_with_only_minimized_comments_is_skipped(monkeypatch, tmp_path, capsys):
    """The newest plan comment minimized by a person, then a quiet run: nothing posted, no head
    read. Mutation: test the listed comments instead of the visible ones -- a POST follows."""
    code, calls = _upsert(
        monkeypatch,
        tmp_path,
        [_head(5, SUMMARY)],
        "--new",
        "--update-only",
        "--head",
        _HEAD,
        minimized={"IC_5"},
    )
    assert (code, calls) == (0, [LIST_ARGV, _read("IC_5")])
    assert capsys.readouterr() == ("skipped\n", "")


def test_new_takes_the_author_from_the_post_response(monkeypatch, tmp_path, capsys):
    """The App's bot login depends on its slug. Mutation: hard-code `shipmate[bot]` -- IC_6 is
    minimized instead of IC_5."""
    listing = [_head(5, SUMMARY, login="shipmate-x[bot]"), _head(6, SUMMARY)]
    code, calls = _upsert(
        monkeypatch,
        tmp_path,
        listing,
        "--new",
        post_response='{"id": 99, "user": {"login": "shipmate-x[bot]"}}',
    )
    assert (code, calls) == (
        0,
        [LIST_ARGV, _read("IC_5", "IC_6"), _post(tmp_path), _minimize("IC_5")],
    )
    assert capsys.readouterr() == ("created\n", "")


def test_new_a_failed_minimize_warns_and_attempts_the_rest(monkeypatch, tmp_path, capsys):
    """Mutations: `break` on a failed minimize -- no IC_6 call; `sys.exit` on it -- exit code."""
    listing = [_head(5, SUMMARY), _head(6, SUMMARY)]
    code, calls = _upsert(monkeypatch, tmp_path, listing, "--new", minimize_fails={"IC_5"})
    assert (code, calls) == (
        0,
        [
            LIST_ARGV,
            _read("IC_5", "IC_6"),
            _post(tmp_path),
            _minimize("IC_5"),
            _minimize("IC_6"),
        ],
    )
    assert capsys.readouterr() == (
        "created\n",
        _MINIMIZE_WARNING + "command failed (1): gh api graphql | FORBIDDEN\n",
    )


@pytest.mark.parametrize("response", ["not json", '{"id": 99}', '{"user": null}'])
def test_new_an_unparseable_post_response_warns_and_minimizes_nothing(
    monkeypatch, tmp_path, capsys, response
):
    """The comment exists, so the run prints `created`. Mutation: let the parse error raise."""
    code, calls = _upsert(
        monkeypatch, tmp_path, [_head(5, SUMMARY)], "--new", post_response=response
    )
    assert (code, calls) == (0, [LIST_ARGV, _read("IC_5"), _post(tmp_path)])
    assert capsys.readouterr() == (
        "created\n",
        _MINIMIZE_WARNING + f"unexpected answer: {response}\n",
    )


@pytest.mark.parametrize(
    ("head", "head_fails", "word", "err"),
    [
        ("d" * 40, False, "moved", ""),
        (_HEAD, True, "head-failed", "command failed (1): gh api | HTTP 502 | ::add-mask::x\n"),
    ],
    ids=["moved", "head-failed"],
)
def test_new_writes_nothing_when_the_head_check_stops(
    monkeypatch, tmp_path, capsys, head, head_fails, word, err
):
    """The head is read after the visibility read and before the POST. Mutation: skip
    `_head_stop` under `--new` -- a POST and a minimize follow."""
    code, calls = _upsert(
        monkeypatch,
        tmp_path,
        [_head(5, SUMMARY)],
        "--new",
        "--head",
        _HEAD,
        head=head,
        head_fails=head_fails,
    )
    assert (code, calls) == (0, [LIST_ARGV, _read("IC_5"), PULLS_ARGV])
    assert capsys.readouterr() == (word + "\n", err)


def test_new_a_listing_failure_prints_list_failed(monkeypatch, tmp_path, capsys):
    """Mutation: drop the `except SystemExit` around the listing."""
    code, calls = _upsert(monkeypatch, tmp_path, [], "--new", list_fails=True)
    assert (code, calls) == (0, [LIST_ARGV])
    assert capsys.readouterr() == ("list-failed\n", "command failed (1): gh api | HTTP 502\n")


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_the_listing_filter_keeps_bot_heads_only(monkeypatch, tmp_path):
    """The `--jq` string from the listing argv the script sends, run by jq over a crafted page.

    Mutations: `"Bot"` -> `"bot"`; drop `// ""`; drop `node_id`; `.user.login` -> `.user.type`.
    """
    _, calls = _upsert(monkeypatch, tmp_path, [])
    jq_filter = calls[0][calls[0].index("--jq") + 1]
    bot, human = {"type": "Bot", "login": "a[bot]"}, {"type": "User", "login": "h"}
    page = [
        {"id": 1, "node_id": "N1", "user": bot, "body": SUMMARY + "\nplan"},
        {"id": 2, "node_id": "N2", "user": human, "body": SUMMARY + "\nhuman"},
        {"id": 3, "node_id": "N3", "user": bot, "body": None},
        {"id": 4, "node_id": "N4", "user": bot, "body": "x" * 60 + "TAIL-CUT-OFF"},
    ]
    jq = subprocess.run(
        ["jq", "-c", jq_filter],
        input=json.dumps(page),
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert jq.returncode == 0, jq.stderr
    assert jq.stdout.splitlines() == [
        '{"id":1,"node_id":"N1","login":"a[bot]","head":"<!-- shipmate:summary -->\\nplan"}',
        '{"id":3,"node_id":"N3","login":"a[bot]","head":""}',
        '{"id":4,"node_id":"N4","login":"a[bot]","head":"' + "x" * 60 + 'TAIL"}',
    ]


@bash_only
def test_a_listing_that_fails_after_printing_a_match_writes_nothing(tmp_path):
    """A later page failing after an earlier one printed the sticky: no ids, so no POST, which
    would create a second sticky.

    Mutation: read the listing with a streaming reader that keeps partial output.
    """
    (tmp_path / "comment.md").write_text(SUMMARY + "\n", encoding="utf-8")
    script = (SCRIPTS / "upsert-comment").as_posix()
    proc, calls = run_with_gh_recorder(
        tmp_path,
        f'python3 "{script}" 7 comment.md\n',
        {**os.environ, "GITHUB_REPOSITORY": "o/r"},
        listing=_head(5, SUMMARY) + "\n",
        list_rc=1,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "list-failed\n"
    assert calls == [_LIST]


#: The listing each caller step makes, as the argv after `gh`.
_LIST = LIST_ARGV[1:]


def _post_of(body):
    return ["api", "repos/o/r/issues/7/comments", "-F", f"body=@{body}"]


#: The `--new` visibility read and minimize of `_head(5, ...)`'s comment, as the argv after `gh`.
_READ_5 = _read("IC_5")[1:]
_MINIMIZE_5 = _minimize("IC_5")[1:]


_SUMMARY_LIST_FAILED = (
    "::error::could not list this pull request's comments, so no plan comment was written; "
    "re-plan this pull request (comment shipmate plan or push)\n"
)
_SUMMARY_SKIPPED = (
    "nothing changed, no visible plan comment, no doctor warning outside the settings "
    "probes, no unmanaged or lost pairs line, not a shipmate plan command; nothing to post\n"
)
_SHA = "a" * 40
#: The pull request head read every writing mode makes after the comment listing.
_PULLS = ["api", "repos/o/r/pulls/7"]
_DEGRADED = (
    "::warning::the plan comment does not say why the gate is held; the gate description says it\n"
)
#: The held body for `_run_summary`'s defaults, hand-written.
_HELD_BODY = (
    f"{SUMMARY}\n### shipmate plan\n\n"
    f"🔴 the gate is held at [aaaaaaa](https://gh/o/r/commit/{_SHA}) in "
    "[run #7](https://gh/o/r/actions/runs/1)\n\n"
    "```\nthe reason\n```\n\n"
    "Comment `shipmate help` for the available commands."
)


def _run_summary(
    tmp_path,
    mode,
    *,
    warned="false",
    unmanaged="false",
    lost="false",
    on_demand="false",
    head=_SHA,
    env=None,
    **gh,
):
    """The summary action's upsert step under its own env names; return (process, gh calls).
    `env` adds to or, with None values, removes from the step's environment."""
    (tmp_path / "comment.md").write_text(SUMMARY + "\n### shipmate plan\n", encoding="utf-8")
    (tmp_path / "check-runs.jsonl").write_text("", encoding="utf-8")
    full = {
        **os.environ,
        "GH_TOKEN": "x",
        "PR": "7",
        "HEAD_SHA": _SHA,
        "MODE": mode,
        "DOCTOR_WARNED": warned,
        "UNMANAGED": unmanaged,
        "LOST": lost,
        "ON_DEMAND": on_demand,
        "SHIPMATE_HOLD_REASON": "the reason",
        "GITHUB_OUTPUT": str(tmp_path / "github_output"),
        "SHIPMATE_MISSING": "[]",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_SERVER_URL": "https://gh",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_NUMBER": "7",
        "GITHUB_ACTION_PATH": str(ACTIONS / "summary"),
    }
    for k, v in (env or {}).items():
        if v is None:
            full.pop(k, None)
        else:
            full[k] = v
    run = step_by("summary", name="Write the plan comment")["run"]
    return run_with_gh_recorder(tmp_path, run, full, head=head, **gh)


@bash_only
@pytest.mark.parametrize(
    ("mode", "stdout"),
    [
        (
            "draft",
            (
                "the pull request was a draft when this run started and was not planned; the "
                "plan comment was left as it is\n"
            ),
        ),
        (
            "hold",
            (
                "::warning::the plan job was cancelled, so this run writes no final gate; the "
                "plan comment was left as it is\n"
            ),
        ),
    ],
)
def test_summary_draft_and_hold_make_no_comment_call(tmp_path, mode, stdout):
    """Mutations: move the `hold` branch below the `upsert-comment` call; route `hold` into the
    held render (drop its branch) -- the head read and the held write follow."""
    proc, calls = _run_summary(tmp_path, mode, listing=_head(5, SUMMARY) + "\n")
    assert (proc.returncode, proc.stdout, calls) == (0, stdout, [])


@bash_only
@pytest.mark.parametrize(
    ("listing", "calls"),
    [
        ("", [_LIST, _PULLS, _post_of("held.md")]),
        (_head(5, SUMMARY), [_LIST, _READ_5, _PULLS, _post_of("held.md"), _MINIMIZE_5]),
    ],
    ids=["no-comment", "existing"],
)
def test_summary_held_posts_the_held_body(tmp_path, listing, calls):
    """A held gate posts a new comment and minimizes the earlier one, and writes upsert-comment's
    word as output `result`.
    Mutations: drop `--new` from the held write -- `existing` PATCHes; pass `--update-only` on
    the held write -- `no-comment` writes nothing; render without `SHIPMATE_COMMENT_MODE=held` --
    no held body, and no write; drop the held branch's `result=` line -- no output."""
    proc, recorded = _run_summary(tmp_path, "held", listing=listing + "\n")
    assert (proc.returncode, proc.stdout, proc.stderr, recorded) == (0, "", "", calls)
    assert (tmp_path / "held.md").read_text(encoding="utf-8") == _HELD_BODY
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == "result=created\n"


@bash_only
def test_summary_held_write_failure_degrades(tmp_path):
    """The step passes, so `Create/refresh gate` still writes the `failure` gate with its
    description; the write's error reaches the log as a warning. Mutations: `exit 1` after the
    degraded warning -- red; drop the `sed` -- an `::error::` line."""
    proc, calls = _run_summary(tmp_path, "held", write_rc=1, write_stderr="HTTP 502\n")
    assert (proc.returncode, proc.stdout, calls) == (
        0,
        _DEGRADED,
        [_LIST, _PULLS, _post_of("held.md")],
    )
    assert proc.stderr == (
        "::warning::could not write the comment: command failed (1): gh api "
        "repos/o/r/issues/7/comments -F body=@held.md%0AHTTP 502\n"
    )


@bash_only
def test_summary_held_list_failure_degrades(tmp_path):
    """gh's stderr reaches the log on one line, so no line of it can start a workflow command.
    Mutation: count only a failed run as unwritten (`[ "$result" = failed ]`) -- no warning."""
    proc, calls = _run_summary(tmp_path, "held", list_rc=1, list_stderr="::warning::x\n")
    assert (proc.returncode, proc.stdout, calls) == (0, _DEGRADED, [_LIST])
    assert proc.stderr == f"command failed (1): {' '.join(LIST_ARGV)} | ::warning::x\n"


@bash_only
def test_summary_held_render_failure_writes_no_comment(tmp_path):
    """A render that raises (no `GITHUB_RUN_NUMBER` for `provenance`) never reaches the write.
    Mutation: run `upsert-comment` with comment.md after a failed render too (`|| true` on the
    render) -- the listing and a POST follow."""
    proc, calls = _run_summary(tmp_path, "held", env={"GITHUB_RUN_NUMBER": None})
    assert (proc.returncode, proc.stdout, calls) == (0, _DEGRADED, [])
    assert not (tmp_path / "held.md").exists()


@bash_only
@pytest.mark.parametrize("mode", ["held", "post", "nothing-changed"])
def test_summary_writes_nothing_over_a_newer_head(tmp_path, mode):
    """A re-run of an old run never replaces the comment of the head the pull request has moved
    to. `result=moved` is what skips `Create apply checks` and `Create/refresh gate`.
    Mutations: drop `--head` from either upsert-comment call -- a write follows; misspell
    `moved` in the step -- no notice; drop the held branch's `result=` line -- no output."""
    proc, calls = _run_summary(tmp_path, mode, head="b" * 40, warned="true")
    assert (proc.returncode, proc.stdout, calls) == (
        0,
        (f"::notice::pull request #7 has moved past {_SHA}; its plan comment was not written\n"),
        [_LIST, _PULLS],
    )
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == "result=moved\n"


#: upsert-comment's one-line stderr for a failed head read; the stub prints no gh stderr.
_HEAD_FAILED = "command failed (1): gh api repos/o/r/pulls/7\n"


@bash_only
def test_summary_held_head_read_failure_degrades(tmp_path):
    """Mutation: fail the held step on `head-failed` -- exit 1."""
    proc, calls = _run_summary(tmp_path, "held", head_rc=1)
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        0,
        _DEGRADED,
        _HEAD_FAILED,
        [_LIST, _PULLS],
    )


@bash_only
@pytest.mark.parametrize("mode", ["post", "nothing-changed"])
def test_summary_post_head_read_failure_fails_the_step(tmp_path, mode):
    """A comment write cannot be judged current, so the job stops before the gate, as on a failed
    listing. Mutation: `exit 0` on `head-failed` -- red."""
    proc, calls = _run_summary(tmp_path, mode, head_rc=1, warned="true")
    assert (proc.returncode, proc.stdout, calls) == (
        1,
        (
            "::error::could not read the head of pull request #7, so no plan comment was "
            "written; re-plan this pull request (comment shipmate plan or push)\n"
        ),
        [_LIST, _PULLS],
    )


_POSTS = [_LIST, _PULLS, _post_of("comment.md")]


@bash_only
@pytest.mark.parametrize(
    ("mode", "warned", "unmanaged", "lost", "on_demand", "listing", "expected", "result"),
    [
        ("nothing-changed", "false", "false", "false", "false", "", [_LIST], "skipped"),
        ("post", "false", "false", "false", "false", "", _POSTS, "created"),
        ("nothing-changed", "true", "false", "false", "false", "", _POSTS, "created"),
        ("nothing-changed", "false", "true", "false", "false", "", _POSTS, "created"),
        ("nothing-changed", "false", "false", "true", "false", "", _POSTS, "created"),
        ("nothing-changed", "false", "false", "false", "true", "", _POSTS, "created"),
        (
            "nothing-changed",
            "false",
            "false",
            "false",
            "false",
            _head(5, SUMMARY),
            [_LIST, _READ_5, _PULLS, _post_of("comment.md"), _MINIMIZE_5],
            "created",
        ),
    ],
    ids=["quiet", "ordinary", "doctor-warned", "unmanaged", "lost", "on-demand", "quiet-existing"],
)
def test_summary_posts_unless_quiet_and_replaces_a_visible_comment(
    tmp_path, mode, warned, unmanaged, lost, on_demand, listing, expected, result
):
    """Nothing changed, no doctor warning, no unmanaged line, no lost pairs line and no
    `shipmate plan` command: post only over a visible plan comment, which is then minimized.
    upsert-comment's word is output `result`.

    A quiet run with no comment reads no head (`quiet`).
    Mutations: drop `--new` from the posted write (quiet-existing PATCHes); delete the
    `DOCTOR_WARNED` clause (doctor-warned red); delete the `UNMANAGED` clause (unmanaged red);
    delete the `LOST` clause (lost red); delete the `ON_DEMAND` clause (on-demand red); delete
    the `nothing-changed` clause (ordinary red); drop the posted branch's `result=` line (every
    case red).
    """
    proc, calls = _run_summary(
        tmp_path,
        mode,
        warned=warned,
        unmanaged=unmanaged,
        lost=lost,
        on_demand=on_demand,
        listing=listing + "\n",
    )
    stdout = _SUMMARY_SKIPPED if result == "skipped" else ""
    assert (proc.returncode, proc.stdout, calls) == (0, stdout, expected), proc.stderr
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == f"result={result}\n"


@bash_only
def test_summary_list_failure_fails_the_step_and_writes_nothing(tmp_path):
    """The step fails, so `Create/refresh gate` does not write a gate over a comment that still
    shows the previous head's plan.

    Mutations: misspell `list-failed` in the step; restore the `::warning::` and continue.
    """
    proc, calls = _run_summary(tmp_path, "post", list_rc=1)
    assert (proc.returncode, proc.stdout, proc.stderr, calls) == (
        1,
        _SUMMARY_LIST_FAILED,
        f"command failed (1): {' '.join(LIST_ARGV)}\n",
        [_LIST],
    )


#: A failed write's two-line gh stderr; the `%` shows `command_data` escaping it.
_WRITE_STDERR = "unexpected end of JSON input\nHTTP 502 (50% of retries)\n"


def _write_annotation(body):
    """The whole stderr of a step whose write of `body` failed with `_WRITE_STDERR`."""
    return (
        "::error::could not write the comment: command failed (1): gh api "
        f"repos/o/r/issues/7/comments -F body=@{body}"
        "%0Aunexpected end of JSON input%0AHTTP 502 (50%25 of retries)\n"
    )


@bash_only
def test_summary_write_failure_fails_the_step(tmp_path):
    """The step fails, so `Create/refresh gate` does not run, and its one annotation carries gh's
    whole stderr.

    Mutations: add `|| true` to the `upsert-comment` call; revert `upsert-comment`'s `__main__`
    block to `main()` (the raw multi-line text reaches stderr)."""
    proc, calls = _run_summary(tmp_path, "post", write_rc=1, write_stderr=_WRITE_STDERR)
    assert proc.returncode != 0
    assert calls == [_LIST, _PULLS, _post_of("comment.md")]
    assert proc.stderr == _write_annotation("comment.md")


_DOCTOR_LINE = 'python3 "$GITHUB_ACTION_PATH/../../scripts/doctor" > doctor.md\n'


def _run_doctor(tmp_path, **gh):
    """comment-ops' doctor step with the doctor run replaced by a fixed marker-led report."""
    run = step_by("comment-ops", name="Doctor: render and upsert the sticky comment")["run"]
    # A replace that misses leaves the real doctor running, passing for the wrong reason.
    assert run.count(_DOCTOR_LINE) == 1
    report = f"printf '%s\\n' '{DOCTOR}' '### shipmate doctor' > doctor.md\n"
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "PR_NUMBER": "7",
        "FULLMINT_OUTCOME": "success",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step-summary.md"),
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_ACTION_PATH": str(ACTIONS / "comment-ops"),
    }
    return run_with_gh_recorder(tmp_path, run.replace(_DOCTOR_LINE, report), env, **gh)


@bash_only
def test_doctor_posts_its_report_and_writes_the_job_summary(tmp_path):
    """Mutation: change `_DOCTOR_LINE`'s text (the occurs-once assertion goes red)."""
    proc, calls = _run_doctor(tmp_path, listing=_head(5, SUMMARY) + "\n")
    assert (proc.returncode, proc.stdout, calls) == (0, "", [_LIST, _post_of("doctor.md")])
    summary = (tmp_path / "step-summary.md").read_text(encoding="utf-8")
    assert summary == f"{DOCTOR}\n### shipmate doctor\n"


@bash_only
def test_doctor_list_failure_warns_with_the_retry_hint_and_writes_nothing(tmp_path):
    """Mutation: misspell `list-failed` in the step."""
    proc, calls = _run_doctor(tmp_path, list_rc=1)
    assert (proc.returncode, proc.stdout, calls) == (
        0,
        (
            "::warning::could not list this pull request's comments; the doctor report was not "
            "posted to the pull request this run: read it in this job's summary, or comment "
            "shipmate doctor again to retry\n"
        ),
        [_LIST],
    )


@bash_only
def test_doctor_write_failure_fails_the_step(tmp_path):
    """Mutations: add `|| true` to the `upsert-comment` call; revert `upsert-comment`'s
    `__main__` block to `main()` (the raw multi-line text reaches stderr)."""
    proc, calls = _run_doctor(tmp_path, write_rc=1, write_stderr=_WRITE_STDERR)
    assert proc.returncode != 0
    assert calls == [_LIST, _post_of("doctor.md")]
    assert proc.stderr == _write_annotation("doctor.md")
