"""`scripts/evict-reply`: the notices on the holders' pull requests and the reply to
`shipmate unlock <env> --evict`, each compared whole against hand-written text."""

import json

import pytest
from _loader import load_script

er = load_script("evict-reply")

_RUN = "https://github.com/org/repo/actions/runs/7777"
_FOOT = f"[comment run]({_RUN})"
_HINT_FOOT = f"[comment run]({_RUN}). Comment `shipmate help` for the available commands."


@pytest.fixture
def posts(monkeypatch):
    """Run main against a recording `run`; a PR number in `fail` makes its post raise."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "7777")
    monkeypatch.setenv("PR_NUMBER", "9")
    monkeypatch.setenv("SHIPMATE_ENV", "dev-eu")
    monkeypatch.setenv("COMMENT_USER", "kp")
    got, fail = [], set()

    def run(argv, stdin=None):
        pr = argv[4].split("/")[-2]
        if pr in fail:
            raise SystemExit("gh: HTTP 403")
        got.append((pr, stdin))
        return "{}"

    monkeypatch.setattr(er.rc, "run", run)
    return got, fail


def _main(monkeypatch, evicted, refused=(), argv=("evict-reply",)):
    monkeypatch.setenv("SHIPMATE_EVICTED", json.dumps(evicted))
    monkeypatch.setenv("SHIPMATE_REFUSED", json.dumps(list(refused)))
    monkeypatch.setattr("sys.argv", list(argv))
    er.main()


def _body(outcome, text):
    foot = _HINT_FOOT if outcome == "failed" else _FOOT
    circle = "🔴 failed: " if outcome == "failed" else "⚪ "
    return f"### shipmate unlock dev-eu\n\n{circle}{text}\n\n{foot}"


_A = {"stack": "stacks/auth", "environment": "dev-eu", "holder": 3}
_B = {"stack": "stacks/a`b", "environment": "dev-eu", "holder": 4}
_C = {"stack": "stacks/dns", "environment": "dev-eu", "holder": 3}
_U = {"stack": "stacks/app", "environment": "dev-eu", "holder": None}


def test_each_holder_is_told_once_and_the_command_names_every_evicted_cell(monkeypatch, posts):
    """Mutations: post one notice per lock (#3 gets two); post a notice for the unknown holder;
    drop `@<!-- -->` from the notice (the commenter is mentioned); put the backticked stack in a
    code span."""
    got, _ = posts
    _main(monkeypatch, [_A, _B, _C, _U])
    assert got == [
        (
            "3",
            _body(
                "notice",
                "🔓 `dev-eu` / `stacks/auth`, `dev-eu` / `stacks/dns` released by @<!-- -->`kp` "
                "from #9; re-plan before applying them.",
            ),
        ),
        (
            "4",
            _body(
                "notice",
                "🔓 `dev-eu` / **stacks/a`b** released by @<!-- -->`kp` from #9; re-plan before "
                "applying them.",
            ),
        ),
        (
            "9",
            _body(
                "notice",
                "released `dev-eu` / `stacks/auth` (held by #3), `dev-eu` / **stacks/a`b** "
                "(held by #4), `dev-eu` / `stacks/dns` (held by #3), `dev-eu` / `stacks/app` "
                "(held by an unreadable lock).",
            ),
        ),
    ]


def test_nothing_evicted_says_so(monkeypatch, posts):
    got, _ = posts
    _main(monkeypatch, [])
    assert got == [
        (
            "9",
            _body(
                "notice",
                "no lock held by another pull request on a cell this pull request changes in "
                "`dev-eu`",
            ),
        )
    ]


def test_a_notice_that_fails_still_reports_the_eviction_and_fails(monkeypatch, posts):
    """The lock is already gone, so the reply must say so.

    Mutations: re-raise the notice failure (no reply is posted); drop the untold `sys.exit`
    (the step reads green)."""
    got, fail = posts
    fail.add("3")
    with pytest.raises(SystemExit, match="eviction notice on 1 pull request"):
        _main(monkeypatch, [_A])
    assert got == [
        (
            "9",
            _body(
                "failed",
                "released `dev-eu` / `stacks/auth` (held by #3); could not tell #3. This run's "
                "log has the error. Tell them to re-plan before applying.",
            ),
        )
    ]


def test_a_partial_eviction_tells_the_holders_and_names_the_refused_cells(monkeypatch, posts):
    """Mutations: drop the `refused` clause (the reply reads as a full success); drop the
    refused `sys.exit` (the step reads green)."""
    got, _ = posts
    with pytest.raises(SystemExit, match="could not release 1 cell lock"):
        _main(monkeypatch, [_C], refused=[_A])
    assert got == [
        (
            "3",
            _body(
                "notice",
                "🔓 `dev-eu` / `stacks/dns` released by @<!-- -->`kp` from #9; re-plan before "
                "applying them.",
            ),
        ),
        (
            "9",
            _body(
                "failed",
                "released `dev-eu` / `stacks/dns` (held by #3); could not release `dev-eu` / "
                "`stacks/auth` (held by #3). This run's log has the error. Comment again to "
                "retry.",
            ),
        ),
    ]


@pytest.mark.parametrize("raw", ["", "{}", "not json"])
def test_an_unreadable_list_refuses_before_any_post(monkeypatch, posts, raw):
    """Mutation: `json.loads` without the guard (`""` and `not json` raise a traceback, `{}`
    iterates as a list)."""
    got, _ = posts
    monkeypatch.setenv("SHIPMATE_EVICTED", raw)
    monkeypatch.setenv("SHIPMATE_REFUSED", "[]")
    monkeypatch.setattr("sys.argv", ["evict-reply"])
    with pytest.raises(SystemExit) as exc:
        er.main()
    assert exc.value.code == f"::error::SHIPMATE_EVICTED must be a JSON list (got: {raw!r})"
    assert got == []


def test_a_failed_eviction_fences_the_refusal(monkeypatch, posts):
    """Mutation: post the refusal unfenced."""
    got, _ = posts
    monkeypatch.setenv("SHIPMATE_EVICT_REFUSAL", "could not release `x`")
    _main(monkeypatch, [], argv=("evict-reply", "--failed"))
    assert got == [
        (
            "9",
            _body(
                "failed",
                "could not evict the cell locks in `dev-eu`. This run's log has the error; "
                "comment again.\n\n```\ncould not release `x`\n```",
            ),
        )
    ]
