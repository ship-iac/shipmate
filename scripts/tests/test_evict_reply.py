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

    monkeypatch.setattr(er, "run", run)
    return got, fail


def _main(monkeypatch, evicted, argv=("evict-reply",)):
    monkeypatch.setenv("SHIPMATE_EVICTED", json.dumps(evicted))
    monkeypatch.setattr("sys.argv", list(argv))
    er.main()


def _body(outcome, text):
    foot = _HINT_FOOT if outcome == "failed" else _FOOT
    circle = "🔴 failed: " if outcome == "failed" else "⚪ "
    return f"### shipmate unlock dev-eu\n\n{circle}{text}\n\n{foot}"


_A = {"stack": "stacks/auth", "environment": "dev-eu", "holder": 3}
_B = {"stack": "stacks/a`b", "environment": "dev-eu", "holder": 4}
_U = {"stack": "stacks/app", "environment": "dev-eu", "holder": None}


def test_each_holder_is_told_and_the_command_names_every_evicted_cell(monkeypatch, posts):
    """Mutations: post a notice for the unknown holder; drop `@<!-- -->` from the notice (the
    commenter is mentioned); put the backticked stack in a code span."""
    got, _ = posts
    _main(monkeypatch, [_A, _B, _U])
    assert got == [
        (
            "3",
            _body(
                "notice",
                "🔓 `dev-eu` / `stacks/auth` lock released by @<!-- -->`kp` from #9; re-plan "
                "before applying it.",
            ),
        ),
        (
            "4",
            _body(
                "notice",
                "🔓 `dev-eu` / **stacks/a`b** lock released by @<!-- -->`kp` from #9; re-plan "
                "before applying it.",
            ),
        ),
        (
            "9",
            _body(
                "notice",
                "released `dev-eu` / `stacks/auth` (held by #3), `dev-eu` / **stacks/a`b** "
                "(held by #4), `dev-eu` / `stacks/app` (held by an unreadable lock).",
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

    Mutations: re-raise the notice failure (no reply is posted); drop the final `sys.exit`
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
                "released `dev-eu` / `stacks/auth` (held by #3), but could not tell #3; this "
                "run's log has the error. Tell them to re-plan before applying.",
            ),
        )
    ]


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
                "could not finish evicting the cell locks in `dev-eu`; any lock already "
                "released was not announced to its holder. This run's log has the error; "
                "comment again.\n\n```\ncould not release `x`\n```",
            ),
        )
    ]
