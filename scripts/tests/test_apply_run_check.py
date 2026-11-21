import json
from types import SimpleNamespace

import _shipmate
import pytest
from _loader import load_script

arc = load_script("apply-run-check")

_SHA = "a" * 40
_URL = "https://github.com/acme/demo/actions/runs/99"
_LIST = (
    [
        "gh",
        "api",
        "--paginate",
        (
            f"repos/acme/demo/commits/{_SHA}/check-runs"
            "?filter=all&per_page=100&check_name=shipmate%20%2F%20apply%20run"
        ),
        "--jq",
        ".check_runs[] | {id, name, status, details_url, app: {id: .app.id}}",
    ],
    None,
)
_CREATE = {
    "name": "shipmate / apply run",
    "head_sha": _SHA,
    "status": "in_progress",
    "details_url": _URL,
    "output": {
        "title": "Apply running",
        "summary": "[The apply run](https://github.com/acme/demo/actions/runs/99) is running. "
        "This check ends when the run posts its result on the pull request.",
    },
}
_POST = (["gh", "api", "-X", "POST", "repos/acme/demo/check-runs", "--input", "-"], _CREATE)
_FINISH = {
    "status": "completed",
    "conclusion": "neutral",
    "details_url": _URL,
    "output": {
        "title": "Apply ended",
        "summary": "[The apply run](https://github.com/acme/demo/actions/runs/99) ended. "
        "Its result is on the pull request.",
    },
}
_SWEEP = {
    "status": "completed",
    "conclusion": "neutral",
    "output": {
        "title": "Apply ended",
        "summary": "The linked apply run ended without ending this check. "
        "[A later apply run](https://github.com/acme/demo/actions/runs/99) ended it.",
    },
}
_SLEEP = ("sleep", 10)


def _patch(cid, body):
    return (["gh", "api", "-X", "PATCH", f"repos/acme/demo/check-runs/{cid}", "--input", "-"], body)


def _read(run_id):
    return (["gh", "api", f"repos/acme/demo/actions/runs/{run_id}"], None)


def _check(cid, status, url, app=42):
    return {
        "id": cid,
        "name": "shipmate / apply run",
        "status": status,
        "details_url": url,
        "app": {"id": app},
    }


def _run_url(run_id):
    return f"https://github.com/acme/demo/actions/runs/{run_id}"


@pytest.fixture
def gh(monkeypatch):
    """`apply-run-check` in run 99 of acme/demo, App 42. `calls` records each call as
    `(argv, parsed stdin)` and each relist sleep as `("sleep", seconds)`; `listings` answers the
    listings in turn; `runs` maps a run id to the status its read answers, None for a 404;
    `fail` holds the check ids whose PATCH fails; a `post` other than None is the code the
    POST's `SystemExit` carries."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("SHIPMATE_APP_ID", "42")
    state = SimpleNamespace(calls=[], listings=[], runs={}, fail=set(), post=None)
    not_found = "gh: Not Found (HTTP 404)"

    def run(args, secrets=(), stdin=None, *, env=None):
        state.calls.append((args, None if stdin is None else json.loads(stdin)))
        if args[2] == "--paginate":
            return "".join(json.dumps(c) + "\n" for c in state.listings.pop(0))
        if args[2] == "-X":
            if args[3] == "POST" and state.post is not None:
                raise SystemExit(state.post)
            if args[3] == "PATCH" and args[4].rsplit("/", 1)[1] in state.fail:
                raise SystemExit(f"command failed (1): {' '.join(args)}\n{not_found}")
            return "{}"
        status = state.runs[args[2].rsplit("/", 1)[1]]
        if status is None:
            raise SystemExit(f"command failed (1): {' '.join(args)}\n{not_found}")
        return json.dumps({"status": status})

    monkeypatch.setattr(_shipmate, "run", run)
    monkeypatch.setattr(arc, "run", run)
    monkeypatch.setattr(arc, "_sleep", lambda s: state.calls.append(("sleep", s)))
    return state


def test_the_check_name_is_one_literal():
    """Mutation: `RUN_CHECK = "apply / run"`."""
    assert _shipmate.RUN_CHECK == "shipmate / apply run"


def test_finish_completes_only_this_runs_open_app_check(gh, capsys):
    """One listing, filtered by name, then one PATCH. Mutations: drop `name=RUN_CHECK` -> the
    listing argv; match `details_url` with `startswith` -> run 999's check 3 is PATCHed too;
    drop the `status` filter -> completed check 2 is PATCHed; iterate `parse_jsonl` output
    instead of `app_runs` -> App 7's check 4 is PATCHed."""
    gh.listings = [
        [
            _check(1, "in_progress", _URL),
            _check(2, "completed", _URL),
            _check(3, "in_progress", _run_url(999)),
            _check(4, "in_progress", _URL, app=7),
        ]
    ]
    arc.main(["finish", _SHA])
    assert gh.calls == [_LIST, _patch(1, _FINISH)]
    assert capsys.readouterr() == ("", "")


def test_start_creates_then_completes_checks_of_ended_runs(gh):
    """Mutations: PATCH without the run read -> PATCH 12 and no reads; take the id with
    `rsplit("/")` -> the other repository's check 13 reads run 5 and is PATCHed; sweep this run's
    own check 14 -> a read of run 99; list before the POST -> the order."""
    gh.listings = [
        [
            _check(11, "in_progress", _run_url(7)),
            _check(12, "in_progress", _run_url(8)),
            _check(13, "in_progress", "https://github.com/other/repo/actions/runs/5"),
            _check(14, "in_progress", _URL),
        ]
    ]
    gh.runs = {"7": "completed", "8": "in_progress", "5": "completed", "99": "in_progress"}
    arc.main(["start", _SHA])
    assert gh.calls == [_POST, _LIST, _read(7), _read(8), _patch(11, _SWEEP)]


def test_an_unreadable_run_warns_and_the_sweep_goes_on(gh, capsys):
    """Mutations: exit on the read failure -> SystemExit and no read of run 8; PATCH the check of
    the unreadable run -> PATCH 11."""
    gh.listings = [[_check(11, "in_progress", _run_url(7)), _check(12, "in_progress", _run_url(8))]]
    gh.runs = {"7": None, "8": "completed"}
    arc.main(["start", _SHA])
    assert gh.calls == [_POST, _LIST, _read(7), _read(8), _patch(12, _SWEEP)]
    assert capsys.readouterr() == (
        "",
        (
            "::warning::check run 11 left open: command failed (1): gh api "
            "repos/acme/demo/actions/runs/7\n"
        ),
    )


def test_a_failed_patch_warns_and_the_sweep_goes_on(gh, capsys):
    """Mutation: exit on the PATCH failure -> SystemExit and no PATCH 12."""
    gh.listings = [[_check(11, "in_progress", _run_url(7)), _check(12, "in_progress", _run_url(8))]]
    gh.runs = {"7": "completed", "8": "completed"}
    gh.fail = {"11"}
    arc.main(["start", _SHA])
    assert gh.calls == [_POST, _LIST, _read(7), _read(8), _patch(11, _SWEEP), _patch(12, _SWEEP)]
    assert capsys.readouterr() == (
        "",
        (
            "::warning::check run 11 left open: command failed (1): gh api -X PATCH "
            "repos/acme/demo/check-runs/11 --input -\n"
        ),
    )


def test_finish_lists_again_after_the_delay_when_it_finds_none(gh):
    """Another run's open check does not count as found. Mutations: no re-list -> no sleep,
    second listing or PATCH; re-list only when the whole listing is empty -> the same."""
    gh.listings = [[_check(3, "in_progress", _run_url(999))], [_check(1, "in_progress", _URL)]]
    arc.main(["finish", _SHA])
    assert gh.calls == [_LIST, _SLEEP, _LIST, _patch(1, _FINISH)]


def test_finish_with_no_check_after_the_re_list_does_nothing(gh, capsys):
    """Mutation: no re-list -> no sleep or second listing."""
    gh.listings = [[], []]
    arc.main(["finish", _SHA])
    assert gh.calls == [_LIST, _SLEEP, _LIST]
    assert capsys.readouterr() == ("", "")


def test_finish_that_finds_its_check_does_not_list_again(gh):
    """Mutation: re-list after a found check too -> a sleep and a second listing."""
    gh.listings = [[_check(1, "in_progress", _URL)], [_check(1, "in_progress", _URL)]]
    arc.main(["finish", _SHA])
    assert gh.calls == [_LIST, _patch(1, _FINISH)]


@pytest.mark.parametrize(
    ("argv", "app_id", "refusal"),
    [
        (["stop", _SHA], "42", "::error::usage: apply-run-check start|finish <sha>"),
        (
            ["start", _SHA[:39]],
            "42",
            f"::error::head SHA must be a 40-char lowercase hex SHA (got: {_SHA[:39]!r})",
        ),
        (
            ["start", _SHA],
            "",
            (
                "::error::SHIPMATE_APP_ID is empty; set the SHIPMATE_APP_ID repo/org "
                "variable to the shipmate App id (see docs/github-app.md)."
            ),
        ),
    ],
)
def test_start_refuses_before_any_call(gh, monkeypatch, argv, app_id, refusal):
    """Mutations: validate the SHA after the POST -> the POST is sent; read the App id only at the
    listing -> the POST is sent."""
    monkeypatch.setenv("SHIPMATE_APP_ID", app_id)
    with pytest.raises(SystemExit) as exc:
        arc.main(argv)
    assert exc.value.code == refusal
    assert gh.calls == []


@pytest.mark.parametrize(
    ("code", "annotation"),
    [
        (
            "command failed (1): gh api -X POST repos/acme/demo/check-runs --input -\nHTTP 500",
            (
                "::error::command failed (1): gh api -X POST repos/acme/demo/check-runs "
                "--input -%0AHTTP 500\n"
            ),
        ),
        (1, "::error::gh failed\n"),
    ],
)
def test_a_failed_post_exits_1_before_any_listing(gh, capsys, code, annotation):
    """Mutations: swallow the POST failure -> no exit and the listing runs; `exc.code` in place
    of `exit_text(exc)` -> `::error::1`."""
    gh.post = code
    with pytest.raises(SystemExit) as exc:
        _shipmate.run_annotated(arc.main, ["start", _SHA])
    assert exc.value.code == 1
    assert gh.calls == [_POST]
    assert capsys.readouterr() == ("", annotation)


def test_finish_on_a_non_sha_exits_quietly(gh, capsys):
    """Mutation: `validate_head_sha` under `finish` -> SystemExit."""
    assert arc.main(["finish", _SHA[:39]]) is None
    assert gh.calls == []
    assert capsys.readouterr() == ("", "")
