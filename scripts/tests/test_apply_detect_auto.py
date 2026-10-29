"""apply-detect's auto form: selection, actor, provenance, pusher and mergeability."""

import json
from typing import Any

import pytest
from _detect_fixtures import (
    APP_ID,
    PR_NUMBER,
    _apply_check,
    forbid_shared_runner,
    held,
    stub_read_locks,
    stub_read_table,
    wave_cells,
)
from _loader import github_outputs, load_script, workflow_jobs

aad = load_script("apply-detect")

HEAD = "a" * 40
BASE = "b" * 40
BOT = "shipmate-iac[bot]"
APP = {"id": int(APP_ID), "slug": "shipmate-iac"}
RUN = "123456"
REMEDY = "Comment `shipmate apply` on the pull request to apply it."
REPO = {"full_name": "o/r"}
PULL_OK = {
    "head": {"sha": HEAD, "repo": REPO},
    "base": {"sha": BASE, "ref": "main", "repo": REPO},
    "mergeable": True,
    "mergeable_state": "clean",
    "draft": False,
}


def _check(stack, env, plan_run=RUN, **kw):
    return _apply_check(stack, env, plan_run, cells=kw.pop("cells", 1), app=APP, **kw)


def _table(auto, other=()):
    entries: dict[str, dict[str, Any]] = {e: {"apply_mode": "auto", "review": False} for e in auto}
    entries.update({e: {} for e in other})
    return {"layout": "folder", "environments": entries}


class _Raw(str):
    """A permission read answered with this body verbatim."""


def _permission_body(permission):
    if isinstance(permission, BaseException):
        raise permission
    if isinstance(permission, _Raw):
        return str(permission)
    return json.dumps({"permission": permission})


def _ref(tip):
    if isinstance(tip, BaseException):
        raise tip
    return json.dumps({"ref": "refs/heads/main", "object": {"sha": tip, "type": "commit"}})


def _github(checks, listing, run, permission, pulls, calls, argv, tip=BASE):
    """A `gh api` double answering each read `_run_auto` documents by its path prefix."""
    answers = [
        ("repos/o/r/git/ref/heads/main", lambda: _ref(tip)),
        (f"repos/o/r/commits/{HEAD}/check-runs?", lambda: "\n".join(map(json.dumps, checks))),
        ("repos/o/r/actions/workflows/shipmate.yml/runs?", lambda: "\n".join(map(str, listing))),
        ("repos/o/r/actions/runs/", lambda: json.dumps(run)),
        ("repos/o/r/collaborators/", lambda: _permission_body(permission)),
        (f"repos/o/r/pulls/{PR_NUMBER}", lambda: json.dumps(pulls.pop(0))),
    ]

    def _run(args):
        assert args[0] == "gh", args
        path = next(a for a in args if a.startswith("repos/"))
        calls.append(path)
        argv.append(args)
        return next(answer for prefix, answer in answers if path.startswith(prefix))()

    return _run


def _run_auto(
    tmp_path,
    monkeypatch,
    *,
    auto=("dev-eu",),
    other=(),
    stacks=("stacks/app",),
    order=None,
    checks=None,
    actor=BOT,
    triggering=BOT,
    decision="NONE",
    run=None,
    listing=(int(RUN),),
    permission="write",
    pulls=None,
    locks=None,
    flag="true",
    calls=None,
    sleeps=None,
    argv=None,
    base_changed=(),
    base_reads=None,
    tip=BASE,
):
    """main() with `SHIPMATE_AUTO` = `flag`, every GitHub and Terramate read stubbed. Defaults
    to one pending check per stack in `stacks` and env in `auto` and `other`, all recording plan run
    `RUN`, whose API body is an autoplan of `HEAD` pushed by `k3np`. `listing` is the head's
    autoplan run ids, `permission` the pusher's (an exception raises from the read), `pulls`
    the successive pull request reads, `base_changed` the stacks the base changed (an exception
    raises from the read), `tip` the base branch ref's commit (an exception raises from the
    read). Each `gh api` path goes to `calls`, its whole argv to `argv`, each
    sleep to `sleeps`, each `(base tip, sorted candidate stacks)` the overlap detection is asked
    about to `base_reads`.
    Returns the parsed GITHUB_OUTPUT."""
    envs = [*auto, *other]
    out = tmp_path / "out"
    for k, v in {
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_ACTOR": actor,
        "GITHUB_TRIGGERING_ACTOR": triggering,
        "SHIPMATE_AUTO": flag,
        "SHIPMATE_ENV": "",
        "SHIPMATE_HEAD_SHA": HEAD,
        "SHIPMATE_PR_NUMBER": PR_NUMBER,
        "GITHUB_OUTPUT": str(out),
        "SHIPMATE_APP_ID": APP_ID,
        "SHIPMATE_REVIEW_DECISION": decision,
    }.items():
        monkeypatch.setenv(k, v)
    planned = len(envs) * len(stacks)
    checks = (
        checks
        if checks is not None
        else [_check(st, e, cells=planned) for e in envs for st in stacks]
    )
    run = run or {
        "id": int(RUN),
        "event": "pull_request_target",
        "head_sha": HEAD,
        "actor": {"login": "k3np"},
        "triggering_actor": {"login": "k3np"},
    }
    github = _github(
        checks,
        listing,
        run,
        permission,
        list(pulls or [PULL_OK]),
        [] if calls is None else calls,
        [] if argv is None else argv,
        tip,
    )

    tree = {e: list(stacks) for e in envs}
    monkeypatch.setattr(aad, "_run", github)
    monkeypatch.setattr(aad.bm, "_run", github)
    forbid_shared_runner(monkeypatch)
    monkeypatch.setattr(aad.bm, "run_graph_deps", lambda: {st: set() for st in stacks})
    monkeypatch.setattr(
        aad.bm,
        "env_membership",
        lambda all_stacks=False, base="", check_names=True: (
            tree,
            {st: [] for st in stacks},
        ),
    )
    stub_read_table(monkeypatch, [aad.bm.ec], base=_table(auto, other), order=order)
    stub_read_locks(monkeypatch, aad.cl, locks)
    monkeypatch.setattr(aad, "_sleep", (sleeps if sleeps is not None else []).append)
    reads = [] if base_reads is None else base_reads

    def _base_changed(base_ref, candidates):
        reads.append((base_ref, sorted(candidates)))
        if isinstance(base_changed, BaseException):
            raise base_changed
        return set(base_changed)

    monkeypatch.setattr(aad.bm, "base_changed_stacks", _base_changed)
    aad.main()
    return github_outputs(out)


def _refusal(tmp_path, monkeypatch, **kw):
    with pytest.raises(SystemExit) as e:
        _run_auto(tmp_path, monkeypatch, **kw)
    return e.value.code


def _applied(parsed):
    return sorted((c["stack"], c["environment"]) for c in wave_cells(aad.wv, parsed))


def test_only_the_auto_environments_pending_cells_apply(tmp_path, monkeypatch):
    """`stage` is a bare environment, so it is stopped and `prod`, which needs it, skips;
    `dev-eu` applies, with the autoplan run and the base tip written.

    Mutation: drop the `stopped |= pending_envs - auto_envs` line -- `stage` and `prod` apply.
    Mutation: write `provenance_run` as `""` -- red."""
    parsed = _run_auto(
        tmp_path, monkeypatch, auto=("dev-eu", "prod"), other=("stage",), order={"prod": ["stage"]}
    )
    assert _applied(parsed) == [("stacks/app", "dev-eu")]
    assert json.loads(parsed["skipped_envs"]) == ["prod"]
    assert json.loads(parsed["applied_exempt_envs"]) == []
    assert parsed["provenance_run"] == RUN
    assert parsed["base_sha"] == BASE


def test_an_unreviewed_pull_request_applies_the_review_exempt_auto_environment(
    tmp_path, monkeypatch
):
    """Every `auto` entry holds `review: false`, so REVIEW_REQUIRED, which a review rule reports
    once the autoplan finishes, applies it and records the exemption.

    Mutation: drop `exempt` from the `held` line's `_review_reason` call -- `dev-eu` holds."""
    parsed = _run_auto(tmp_path, monkeypatch, other=("stage",), decision="REVIEW_REQUIRED")
    assert _applied(parsed) == [("stacks/app", "dev-eu")]
    assert json.loads(parsed["review_held_envs"]) == ["stage"]
    assert json.loads(parsed["applied_exempt_envs"]) == ["dev-eu"]


def test_the_bare_form_runs_when_the_flag_is_false(tmp_path, monkeypatch):
    """The action passes `false` for a bare apply, whose actor is no App bot.

    Mutation: route on `os.environ.get("SHIPMATE_AUTO")` being non-empty -- the actor refuses.
    Mutation: drop `auto and` from the overlap read's condition -- the base side is read."""
    reads = []
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        other=("stage",),
        actor="k3np",
        flag="false",
        base_changed={"stacks/app"},
        base_reads=reads,
    )
    assert _applied(parsed) == [("stacks/app", "dev-eu"), ("stacks/app", "stage")]
    assert "provenance_run" not in parsed
    assert json.loads(parsed["held_cells"]) == []
    assert "bare_envs" not in parsed
    assert reads == []


@pytest.mark.parametrize("actor", ["github-actions[bot]", "other-app[bot]", "shipmate-iac"])
def test_an_actor_that_is_not_the_apps_bot_refuses(tmp_path, monkeypatch, actor):
    """Mutation: compare `actor.endswith("[bot]")` -- the first two rows pass."""
    assert _refusal(tmp_path, monkeypatch, actor=actor) == (
        f"::error::auto apply refused: the run's actor is {actor!r}, not the shipmate App: only "
        f"the App dispatches an auto apply, after an autoplan. {REMEDY}"
    )


@pytest.mark.parametrize("triggering", ["k3np", "github-actions[bot]", ""])
def test_a_rerun_by_anyone_but_the_apps_bot_refuses(tmp_path, monkeypatch, triggering):
    """A human's "Re-run all jobs" keeps the App as `actor` and makes the human the triggering
    actor. Mutation: drop the `triggering_actor` refusal -- every row applies."""
    assert _refusal(tmp_path, monkeypatch, triggering=triggering) == (
        f"::error::auto apply refused: the run's triggering actor is {triggering!r}, not the "
        f"shipmate App: a re-run of an auto apply never applies. {REMEDY}"
    )


@pytest.mark.parametrize(
    "action",
    [
        pytest.param("ready_for_review", id="ready"),
        pytest.param("reopened", id="reopened"),
        pytest.param("", id="dispatch"),
        pytest.param(None, id="older-engine"),
    ],
)
def test_a_plan_no_push_started_refuses(tmp_path, monkeypatch, action):
    """A ready-for-review or reopen autoplan of the head dispatches no auto apply but becomes
    the newest autoplan; its clicker is no pusher. Nothing about the run or the pusher is read.

    Mutation: drop `refuse_pushed` from `bare_main` (every row applies). Mutation: admit a
    missing action (`actions.get(n) not in (*ag.PUSH_ACTIONS, None)`; older-engine applies)."""
    calls = []
    checks = [_check("stacks/app", "dev-eu", action=action)]
    assert _refusal(tmp_path, monkeypatch, checks=checks, calls=calls) == (
        f"::error::auto apply refused: the plan records of apply / stacks/app / dev-eu "
        f"({action!r}) name no push event action (opened or synchronize), so no push started "
        f"that plan. {REMEDY}"
    )
    assert not [c for c in calls if "/actions/" in c or "/collaborators/" in c]


@pytest.mark.parametrize("action", ["opened", "synchronize"])
def test_a_plan_a_push_started_applies(tmp_path, monkeypatch, action):
    """Mutation: drop `opened` from `PUSH_ACTIONS` (the opened row refuses)."""
    checks = [_check("stacks/app", "dev-eu", action=action)]
    assert _applied(_run_auto(tmp_path, monkeypatch, checks=checks)) == [("stacks/app", "dev-eu")]


def test_a_head_without_an_app_check_refuses(tmp_path, monkeypatch):
    """Mutation: drop the `if not slugs` refusal -- the actor refusal fires instead."""
    other = {"id": 15368, "slug": "github-actions"}
    code = _refusal(
        tmp_path, monkeypatch, checks=[_apply_check("stacks/app", "dev-eu", cells=1, app=other)]
    )
    assert code == (
        "::error::auto apply refused: no check run on the head is authored by the shipmate "
        f"App, so its bot login is unknown. {REMEDY}"
    )


def test_a_shipmate_plan_record_refuses_naming_shipmate_apply(tmp_path, monkeypatch):
    """Mutation: drop the `event` refusal -- the run applies."""
    run = {"id": int(RUN), "event": "workflow_dispatch", "head_sha": HEAD}
    assert _refusal(tmp_path, monkeypatch, run=run) == (
        f"::error::auto apply refused: plan run {RUN} is a `workflow_dispatch` run, not an "
        f"autoplan: an auto apply applies only the plan a push started. {REMEDY}"
    )


def test_a_record_from_another_head_refuses(tmp_path, monkeypatch):
    """Mutation: drop the `head_sha` refusal -- the run applies."""
    run = {"id": int(RUN), "event": "pull_request_target", "head_sha": BASE}
    assert _refusal(tmp_path, monkeypatch, run=run) == (
        f"::error::auto apply refused: plan run {RUN} planned {BASE}, not the head {HEAD}. {REMEDY}"
    )


def test_a_record_older_than_the_heads_newest_autoplan_refuses(tmp_path, monkeypatch):
    """Run 123999 is a newer autoplan of the head that recorded no cell.

    Mutation: `min` for `max` over the listing -- the run applies."""
    code = _refusal(tmp_path, monkeypatch, listing=(123999, int(RUN)))
    assert code == (
        f"::error::auto apply refused: plan run {RUN} is not the newest autoplan of the head "
        f"(run 123999 is). {REMEDY}"
    )


def test_the_autoplan_listing_reads_every_page(tmp_path, monkeypatch):
    """Mutation: drop `--paginate` from the listing argv. Mutation: drop `event=` from its
    query."""
    argv = []
    _run_auto(tmp_path, monkeypatch, argv=argv)
    assert [a for a in argv if any("/actions/workflows/" in x for x in a)] == [
        [
            "gh",
            "api",
            "--paginate",
            (
                "repos/o/r/actions/workflows/shipmate.yml/runs?event=pull_request_target"
                f"&head_sha={HEAD}&per_page=100"
            ),
            "--jq",
            ".workflow_runs[].id",
        ]
    ]


def test_cells_recording_two_runs_refuse(tmp_path, monkeypatch):
    """Mutation: drop the `len(runs) > 1` refusal -- the newest-run refusal fires instead."""
    checks = [_check("stacks/app", "dev-eu", "111"), _check("stacks/app", "prod", "222")]
    code = _refusal(tmp_path, monkeypatch, auto=("dev-eu", "prod"), checks=checks, listing=(222,))
    assert code == (
        "::error::auto apply refused: the cells to apply record different plan runs (plan run "
        "111: apply / stacks/app / dev-eu; plan run 222: apply / stacks/app / prod), so no one "
        f"autoplan planned them all. {REMEDY}"
    )


def test_changes_requested_holds_every_environment(tmp_path, monkeypatch):
    """`review: false` exempts from REVIEW_REQUIRED only; nothing applies, so nothing about the
    run, the pusher or the pull request is read.

    Mutation: replace the `held` partition with `[]` -- both environments apply."""
    calls = []
    parsed = _run_auto(
        tmp_path, monkeypatch, auto=("dev-eu", "prod"), decision="CHANGES_REQUESTED", calls=calls
    )
    assert json.loads(parsed["review_held_envs"]) == ["dev-eu", "prod"]
    assert _applied(parsed) == []
    assert (parsed["provenance_run"], parsed["base_sha"]) == ("", "")
    assert calls == [f"repos/o/r/commits/{HEAD}/check-runs?filter=all&per_page=100"]
    assert json.loads(parsed["held_cells"]) == []


def test_a_pusher_with_read_permission_refuses(tmp_path, monkeypatch):
    """Mutation: drop `subject=` from the `_permission_reason` call -- it names the commenter."""
    assert _refusal(tmp_path, monkeypatch, permission="read") == (
        "::error::not authorized: `shipmate apply` needs write access to this repository, and "
        "the pusher @k3np's permission is `read`."
    )


def test_an_unreadable_pusher_permission_refuses_with_the_auto_retry(tmp_path, monkeypatch):
    """A failed read is an unreadable permission.

    Mutation: drop `retry=` from the `_permission_reason` call -- it says "comment again"."""
    code = _refusal(tmp_path, monkeypatch, permission=SystemExit("::error::gh: Not Found"))
    assert code == (
        "::error::not authorized: could not read the pusher @k3np's permission on this "
        "repository, so `shipmate apply` was not run. This run's log has the API response; "
        "push again, or a writer comments `shipmate apply`."
    )


def test_a_pusher_with_write_permission_applies(tmp_path, monkeypatch):
    """Mutation: admit only `admin` in `_permission_reason` -- red."""
    calls = []
    parsed = _run_auto(tmp_path, monkeypatch, calls=calls)
    assert _applied(parsed) == [("stacks/app", "dev-eu")]
    assert "repos/o/r/collaborators/k3np/permission" in calls


def test_mergeability_null_on_every_read_refuses_after_the_bound(tmp_path, monkeypatch):
    """Mutation: `_MERGEABLE_READS = 11` -- the read count reddens. Mutation: break on the first
    read -- the read count reddens. Mutation: drop `retry=_AUTO_RETRY` from `wait_mergeable` --
    the commenter's `re-run` wording, red."""
    calls, sleeps = [], []
    pull = {**PULL_OK, "mergeable": None, "mergeable_state": "unknown"}
    code = _refusal(tmp_path, monkeypatch, pulls=[pull] * 12, calls=calls, sleeps=sleeps)
    assert code == (
        "::error::GitHub is still computing this PR's mergeability; wait a moment, then comment "
        "`shipmate apply`."
    )
    assert calls.count(f"repos/o/r/pulls/{PR_NUMBER}") == 12
    assert sleeps == [5] * 11


def test_mergeability_null_then_true_applies(tmp_path, monkeypatch):
    """Mutation: break after the first read -- refuses."""
    sleeps = []
    pull = {**PULL_OK, "mergeable": None, "mergeable_state": "unknown"}
    parsed = _run_auto(tmp_path, monkeypatch, pulls=[pull, PULL_OK], sleeps=sleeps)
    assert _applied(parsed) == [("stacks/app", "dev-eu")]
    assert sleeps == [5]


def test_a_read_whose_head_moved_refuses(tmp_path, monkeypatch):
    """Mutation: drop the `head.sha` comparison -- the second read applies."""
    pull = {**PULL_OK, "mergeable": None, "mergeable_state": "unknown"}
    moved = {**PULL_OK, "head": {"sha": "c" * 40}}
    assert _refusal(tmp_path, monkeypatch, pulls=[pull, moved]) == (
        f"::error::auto apply refused: pull request #{PR_NUMBER} head is {'c' * 40}, but this "
        f"run applies {HEAD}. The new head's autoplan decides its own apply."
    )


def test_a_fork_head_refuses(tmp_path, monkeypatch):
    """Mutation: drop `_head_repo_reason` from the final refusal -- the run applies."""
    fork = {**PULL_OK, "head": {"sha": HEAD, "repo": {"full_name": "fork/r"}}}
    assert _refusal(tmp_path, monkeypatch, pulls=[fork]) == (
        "::error::this pull request's head is in `fork/r`; shipmate applies and unlocks only "
        "branches of this repository."
    )


def test_the_base_tip_is_the_branch_refs_not_the_pull_requests_base_sha(tmp_path, monkeypatch):
    """Probed on a sample pull request: after a push to the base, `base.sha` kept the old tip
    for 20 s and more while `git/ref/heads/<base.ref>` answered the new one at once, so overlap
    computed on `base.sha` misses the base's newest change.

    Mutation: return `pr["base"]["sha"]` from `wait_mergeable` -- overlap and `base_sha` use
    the stale tip, red."""
    reads, tip = [], "c" * 40
    parsed = _run_auto(tmp_path, monkeypatch, base_reads=reads, tip=tip)
    assert (reads, parsed["base_sha"]) == ([(tip, ["stacks/app"])], tip)


@pytest.mark.parametrize(
    ("pull", "tip", "why"),
    [
        pytest.param(
            {**PULL_OK, "base": {"sha": BASE, "repo": REPO}}, BASE, "KeyError('ref')", id="no-ref"
        ),
        pytest.param(
            PULL_OK,
            SystemExit("::error::command failed (1): gh api"),
            "command failed (1): gh api",
            id="read-fails",
        ),
        pytest.param(
            PULL_OK, "main", "branch 'main' points at 'main', not a commit sha", id="bad-sha"
        ),
    ],
)
def test_a_base_tip_that_cannot_be_read_refuses(tmp_path, monkeypatch, pull, tip, why):
    """Mutation: drop `LookupError` from the `except` (no-ref escapes as a KeyError). Mutation:
    drop the `is_head_sha` refusal in `base_tip` (bad-sha applies)."""
    assert _refusal(tmp_path, monkeypatch, pulls=[pull], tip=tip) == (
        f"::error::auto apply refused: could not read the base tip of pull request "
        f"#{PR_NUMBER} ({why}). {REMEDY}"
    )


def test_a_pusher_login_that_cannot_be_read_refuses_as_unreadable(tmp_path, monkeypatch):
    """A login outside `_LOGIN` is never put into a URL.

    Mutation: `readable = True` -- reads `write` and applies."""
    run = {"id": int(RUN), "event": "pull_request_target", "head_sha": HEAD}
    run["actor"] = run["triggering_actor"] = {"login": "a b"}
    assert _refusal(tmp_path, monkeypatch, run=run) == (
        "::error::not authorized: could not read the pusher's permission on this repository, "
        "so `shipmate apply` was not run. This run's log has the API response; push again, or "
        "a writer comments `shipmate apply`."
    )


def test_a_bot_pusher_refuses_naming_the_bot(tmp_path, monkeypatch):
    """A bot's push never auto-applies, and its refusal says so rather than calling the
    permission unreadable.

    Mutation: drop the `[bot]` branch from `pusher_reason` -- the unreadable wording, red."""
    run = {
        "id": int(RUN),
        "event": "pull_request_target",
        "head_sha": HEAD,
        "actor": {"login": "renovate[bot]"},
        "triggering_actor": {"login": "renovate[bot]"},
    }
    assert _refusal(tmp_path, monkeypatch, run=run) == (
        "::error::not authorized: the head was pushed by @renovate[bot], a bot account, which "
        "auto never authorizes; a writer comments `shipmate apply`."
    )


@pytest.mark.parametrize(
    ("actor", "trigger"),
    [("k3np", "octocat"), ("renovate[bot]", "k3np")],
)
def test_an_autoplan_triggered_by_anyone_but_its_actor_refuses(
    tmp_path, monkeypatch, actor, trigger
):
    """A re-run of the autoplan keeps `actor` and names the re-runner as `triggering_actor`:
    no push authorizes it, and no permission is read.

    Mutation: drop the `actor != trigger` refusal -- the first row reads `write` and applies,
    the second refuses as a bot.
    Mutation: read the pusher from `triggering_actor` alone -- the same rows, red."""
    run = {
        "id": int(RUN),
        "event": "pull_request_target",
        "head_sha": HEAD,
        "actor": {"login": actor},
        "triggering_actor": {"login": trigger},
    }
    calls = []
    assert _refusal(tmp_path, monkeypatch, run=run, calls=calls) == (
        f"::error::not authorized: the autoplan ran as @{actor} but was triggered by "
        f"@{trigger}, so it was re-run, and auto authorizes only the push itself; a writer "
        "comments `shipmate apply`."
    )
    assert not [c for c in calls if "/collaborators/" in c]


@pytest.mark.parametrize(
    ("kw", "what", "error"),
    [
        pytest.param(
            {"run": "x"},
            f"plan run {RUN}",
            "AttributeError(\"'str' object has no attribute 'get'\")",
            id="run",
        ),
        pytest.param(
            {"listing": ("abc",)},
            "autoplan listing",
            "ValueError(\"invalid literal for int() with base 10: 'abc'\")",
            id="listing",
        ),
    ],
)
def test_a_malformed_plan_run_read_refuses_without_a_traceback(
    tmp_path, monkeypatch, kw, what, error
):
    """Mutation: drop the `except` around the run read (run escapes as an `AttributeError`).
    Mutation: drop the `except` around `newest_autoplan` (listing escapes as a `ValueError`)."""
    assert _refusal(tmp_path, monkeypatch, **kw) == (
        f"::error::auto apply refused: the {what} read returned a malformed answer ({error}). "
        f"{REMEDY}"
    )


@pytest.mark.parametrize(
    ("actor", "trigger"),
    [
        pytest.param(None, None, id="neither"),
        pytest.param({"login": "k3np"}, None, id="no-trigger"),
        pytest.param("k3np", "k3np", id="not-mappings"),
        pytest.param({"login": ""}, {"login": ""}, id="empty"),
        pytest.param({"login": 7}, {"login": 7}, id="not-a-string"),
    ],
)
def test_a_missing_actor_login_refuses_as_malformed(tmp_path, monkeypatch, actor, trigger):
    """Checked before the equality, so two missing logins are not the same pusher.

    Mutation: drop the `not (actor and trigger)` refusal -- neither and empty read as
    unreadable, no-trigger as a re-run, red. Mutation: drop `isinstance(login, str)` in `_login`
    (not-a-string raises)."""
    run = {"id": int(RUN), "event": "pull_request_target", "head_sha": HEAD}
    run.update({"actor": actor, "triggering_actor": trigger})
    assert _refusal(tmp_path, monkeypatch, run=run) == (
        "::error::not authorized: the autoplan run names no actor or no triggering actor login, "
        "so its pusher is unknown; a writer comments `shipmate apply`."
    )


@pytest.mark.parametrize("body", ["[]", "{}", "not json"])
def test_a_malformed_permission_body_refuses_as_unreadable(tmp_path, monkeypatch, body):
    """Mutation: drop `(LookupError, TypeError, ValueError)` from `pusher_reason`'s permission
    read -- each row escapes as a malformed answer, red."""
    assert _refusal(tmp_path, monkeypatch, permission=_Raw(body)) == (
        "::error::not authorized: could not read the pusher @k3np's permission on this "
        "repository, so `shipmate apply` was not run. This run's log has the API response; "
        "push again, or a writer comments `shipmate apply`."
    )


def test_an_empty_autoplan_listing_refuses_naming_it(tmp_path, monkeypatch):
    """Mutation: drop the `newest is None` refusal -- it reads "(run None is)"."""
    assert _refusal(tmp_path, monkeypatch, listing=()) == (
        f"::error::auto apply refused: plan run {RUN} is not listed, and no autoplan run of the "
        f"head is listed. {REMEDY}"
    )


def test_a_draft_refuses(tmp_path, monkeypatch):
    """Mutation: drop the `_ready_reason` refusal -- the run applies. Mutation: drop
    `retry=_AUTO_RETRY` from `wait_mergeable` -- the commenter's wording, red."""
    assert _refusal(tmp_path, monkeypatch, pulls=[{**PULL_OK, "draft": True}]) == (
        "::error::not authorized: this pull request is a draft. A draft can be planned but not "
        "applied. Mark it ready for review, then comment `shipmate apply`."
    )


def test_a_locked_cell_stays_pending_as_on_a_bare_apply(tmp_path, monkeypatch):
    """Mutation: pass `frozenset()` for `locked` to `settle` -- the cell applies."""
    parsed = _run_auto(tmp_path, monkeypatch, locks=held(aad.cl, "stacks/app", "dev-eu", 7))
    assert _applied(parsed) == []
    assert json.loads(parsed["held_cells"]) == [
        {"stack": "stacks/app", "environment": "dev-eu", "cause": "locked", "holder": 7}
    ]


def test_detect_grants_exactly_these_permissions():
    """The auto form reads the pull request, and a reusable workflow's job cannot exceed it.

    Mutation: drop `pull-requests: read` from `apply.yml`'s `detect`."""
    assert workflow_jobs("apply.yml")["detect"]["permissions"] == {
        "contents": "read",
        "checks": "read",
        "actions": "read",
        "pull-requests": "read",
    }


def test_a_cell_whose_stack_the_base_changed_is_held_and_stops_its_needs_successor(
    tmp_path, monkeypatch, capsys
):
    """The merge of the base into the branch changes `stacks/app` (through a module, as the probe
    found Terramate reports it): both `stacks/app` cells are held, `prod`, which needs `dev-eu`,
    skips, and the unrelated `stacks/dns` applies in `dev-eu`. The overlap is computed on the base
    tip the pull request read returned, which is the `base_sha` written.

    Mutation: pass `settle` the locked cells alone -- `stacks/app / dev-eu` applies and `prod`
    runs, red.
    Mutation: write `held_cells` with no overlap cell -- red.
    Mutation: test `c["environment"] in changed` in `overlap_cells` -- nothing is held, red.
    Mutation: print `len(locked)` for the notice's `overlap=` -- red.
    Mutation: pass `base_changed_stacks` no candidate stacks (`set()`) -- red."""
    reads = []
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        auto=("dev-eu", "prod"),
        stacks=("stacks/app", "stacks/dns"),
        order={"prod": ["dev-eu"]},
        base_changed={"stacks/app", "stacks/other"},
        base_reads=reads,
    )
    assert _applied(parsed) == [("stacks/dns", "dev-eu")]
    assert json.loads(parsed["held_cells"]) == [
        {"stack": "stacks/app", "environment": "dev-eu", "cause": "overlap"},
        {"stack": "stacks/app", "environment": "prod", "cause": "overlap"},
    ]
    assert json.loads(parsed["skipped_envs"]) == ["prod"]
    assert reads == [(BASE, ["stacks/app", "stacks/dns"])]
    assert parsed["base_sha"] == BASE
    assert (
        f"::notice title=apply-all-detect::head={HEAD} cells=4 pending=4 envlevels=[1, 0, 0, 0] "
        "excluded_targeted=[] skipped_after_targeted=['prod'] review_held=[] waiting=0 locked=0 "
        "overlap=2 applied_exempt=[] review_decision='NONE'"
    ) in capsys.readouterr().out.splitlines()


def test_a_run_the_base_changed_nothing_of_applies_every_cell(tmp_path, monkeypatch):
    """Mutation: hold every candidate in `overlap_cells` -- nothing applies, red."""
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        stacks=("stacks/app", "stacks/dns"),
        base_changed={"stacks/other"},
    )
    assert _applied(parsed) == [("stacks/app", "dev-eu"), ("stacks/dns", "dev-eu")]
    assert json.loads(parsed["held_cells"]) == []


def test_an_all_overlapping_run_names_no_plan_run_but_its_base(tmp_path, monkeypatch):
    """Nothing applies, so neither the plan run nor its pusher is read; the base tip the overlap
    was computed on is still written.

    Mutation: read the provenance whenever a pull request read happened -- the run is read,
    red."""
    calls = []
    parsed = _run_auto(tmp_path, monkeypatch, base_changed={"stacks/app"}, calls=calls)
    assert _applied(parsed) == []
    assert (parsed["provenance_run"], parsed["base_sha"]) == ("", BASE)
    assert json.loads(parsed["held_cells"]) == [
        {"stack": "stacks/app", "environment": "dev-eu", "cause": "overlap"}
    ]
    assert [c for c in calls if c.startswith("repos/o/r/actions/")] == []


def test_an_unresolvable_merge_base_refuses_the_run(tmp_path, monkeypatch):
    """`build-matrix.merge_base`'s refusal reaches the run whole: no output, nothing applies.

    Mutation: catch `SystemExit` around `base_changed_stacks` and hold nothing -- the run
    applies, red."""
    error = SystemExit("::error::could not resolve the merge base")
    assert _refusal(tmp_path, monkeypatch, base_changed=error) == (
        "::error::could not resolve the merge base"
    )


def test_a_skipped_auto_environment_names_the_bare_environment_it_waits_on(tmp_path, monkeypatch):
    """`dev` is bare, so the auto run leaves it to a bare apply and names it in `bare_envs`; `stg`
    needs it and `prod` needs `stg`, so both skip and both name `dev`.

    Mutation: name only the direct `needs` predecessors -- `prod` names none, red.
    Mutation: write `bare_envs` as `[]` -- red."""
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        auto=("prod", "stg"),
        other=("dev",),
        order={"stg": ["dev"], "prod": ["stg"]},
    )
    assert _applied(parsed) == []
    assert json.loads(parsed["skipped_envs"]) == ["prod", "stg"]
    assert json.loads(parsed["skipped_after"]) == {"prod": ["dev"], "stg": ["dev"]}
    assert json.loads(parsed["bare_envs"]) == ["dev"]


def test_a_held_bare_environment_is_named_held_not_left_for_a_bare_apply(tmp_path, monkeypatch):
    """Under REVIEW_REQUIRED the bare `stage` is held (its entry is not review-exempt), so it is
    named on the held line alone; the auto `dev-eu` applies.

    Mutation: leave `stopped` out of the `bare_envs` subtraction -- `stage` is named, red."""
    parsed = _run_auto(tmp_path, monkeypatch, other=("stage",), decision="REVIEW_REQUIRED")
    assert json.loads(parsed["review_held_envs"]) == ["stage"]
    assert json.loads(parsed["bare_envs"]) == []
    assert json.loads(parsed["skipped_after"]) == {}


def test_a_cell_both_locked_and_overlapping_is_held_once_per_cause():
    """Evicting the lock does not clear the overlap, so the comment must name both remedies.
    Mutation: drop overlap cells already in `locked` (the overlap entry is gone)."""
    cell = {"stack": "stacks/app", "environment": "dev"}
    assert aad.held_cells([{**cell, "holder": 7}], [cell]) == [
        {**cell, "holder": 7, "cause": "locked"},
        {**cell, "cause": "overlap"},
    ]
