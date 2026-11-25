"""apply-detect's auto form: selection, actor, provenance, pusher and mergeability."""

import json
from typing import Any

import pytest
from _detect_fixtures import (
    APP_ID,
    HELD_CASES,
    HOLDER,
    PR_NUMBER,
    RUN_ENV,
    _apply_check,
    attempted_cell,
    autoplans_read,
    behind_check,
    deferred_in_progress_jobs,
    deferred_jobs,
    dispatched_plan,
    fake_clock,
    forbid_shared_runner,
    held,
    held_check,
    holder_answers,
    listed_autoplan,
    plan_jobs,
    stub_read_locks,
    stub_read_table,
    waiting_cell,
    wave_cells,
)
from _loader import detect_report, github_outputs, load_script, workflow_jobs

aad = load_script("apply-detect")

HEAD = "a" * 40
BASE = "b" * 40
BOT = "shipmate-iac[bot]"
APP = {"id": int(APP_ID), "slug": "shipmate-iac"}
RUN = "123456"
REMEDY = "Comment `shipmate apply` on the pull request to apply it."
LISTING = autoplans_read("o/r", HEAD)
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


def _listed(entry):
    """One listing line: an autoplan of `HEAD` by `k3np`, of id `entry` for an int, of id `RUN`
    with `entry`'s keys over it for a dict; a str verbatim."""
    if isinstance(entry, str):
        return entry
    keys = entry if isinstance(entry, dict) else {"id": entry}
    return json.dumps(listed_autoplan(int(RUN), HEAD, **keys))


def _answer(body):
    if isinstance(body, BaseException):
        raise body
    return body


def _plans_body(plans):
    """The `shipmate plan` listing's page holding `plans`."""
    if isinstance(plans, BaseException):
        raise plans
    return json.dumps({"total_count": len(plans), "workflow_runs": list(plans)})


def _github(
    checks, listing, permission, pulls, calls, argv, tip=BASE, plans=(), jobs=None, runs=()
):
    """A `gh api` double answering each read `_run_auto` documents by its path prefix, or by
    its whole path from `runs`, `(path, body)` pairs; any other read, a single-run
    `actions/runs/<id>` read among them, fails the test."""
    jobs = jobs or {}
    exact = dict(runs)
    answers = [
        (
            "repos/o/r/actions/workflows/shipmate.yml/runs?event=workflow_dispatch&",
            lambda: _plans_body(plans),
        ),
        *(
            (f"repos/o/r/actions/runs/{run_id}/jobs?", lambda body=body: _answer(body))
            for run_id, body in jobs.items()
        ),
        ("repos/o/r/git/ref/heads/main", lambda: _ref(tip)),
        (f"repos/o/r/commits/{HEAD}/check-runs?", lambda: "\n".join(map(json.dumps, checks))),
        (
            "repos/o/r/actions/workflows/shipmate.yml/runs?",
            lambda: "\n".join(map(_listed, listing)),
        ),
        ("repos/o/r/collaborators/", lambda: _permission_body(permission)),
        (f"repos/o/r/pulls/{PR_NUMBER}", lambda: json.dumps(pulls.pop(0))),
    ]

    def _run(args):
        assert args[0] == "gh", args
        path = next(a for a in args if a.startswith("repos/"))
        calls.append(path)
        argv.append(args)
        if path in exact:
            return exact[path]
        answer = next((a for prefix, a in answers if path.startswith(prefix)), None)
        assert answer is not None, f"unexpected read: {args}"
        return answer()

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
    plans=(),
    jobs=None,
    runs=(),
    untagged=(),
    deps=None,
    tags=None,
):
    """main() with `SHIPMATE_AUTO` = `flag`, every GitHub and Terramate read stubbed. Defaults
    to one pending check per stack in `stacks` and env in `auto` and `other`, all recording plan run
    `RUN`, listed as an autoplan of `HEAD` pushed by `k3np`. `listing` is the head's listed
    autoplans, one `_listed` entry each, `permission` the pusher's (an exception raises from
    the read), `pulls` the successive pull request reads, `base_changed` the stacks the base
    changed (an exception raises from the read), `tip` the base branch ref's commit (an
    exception raises from the read). Each `gh api` path goes to `calls`, its whole argv to
    `argv`, each sleep to `sleeps`, each `(base tip, sorted candidate stacks, sorted stacks)` the
    overlap detection is asked about to `base_reads`. `plans` is the `shipmate plan` listing's
    runs (an exception raises from the read), `jobs` maps a run id to its jobs listing's body,
    and `runs` answers whole paths (`_github`). `untagged` adds stacks to the tag map alone,
    with no `env/*` tag and no check. `deps` replaces the run-graph, which defaults to no edges, and
    `tags` the tag map, which defaults to no tag per stack.
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
        **RUN_ENV,
    }.items():
        monkeypatch.setenv(k, v)
    planned = len(envs) * len(stacks)
    checks = (
        checks
        if checks is not None
        else [_check(st, e, cells=planned) for e in envs for st in stacks]
    )
    github = _github(
        checks,
        listing,
        permission,
        list(pulls or [PULL_OK]),
        [] if calls is None else calls,
        [] if argv is None else argv,
        tip,
        plans,
        jobs,
        runs,
    )

    tree = {e: list(stacks) for e in envs}
    monkeypatch.setattr(aad, "_run", github)
    monkeypatch.setattr(aad.bm, "_run", github)
    forbid_shared_runner(monkeypatch)
    graph = deps if deps is not None else {st: set() for st in stacks}
    monkeypatch.setattr(aad.bm, "run_graph_deps", lambda: graph)
    monkeypatch.setattr(
        aad.bm,
        "env_membership",
        lambda all_stacks=False, base="", check_names=True: (
            tree,
            tags if tags is not None else {st: [] for st in (*stacks, *untagged)},
        ),
    )
    stub_read_table(monkeypatch, [aad.bm.ec], base=_table(auto, other), order=order)
    stub_read_locks(monkeypatch, aad.cl, locks)
    monkeypatch.setattr(aad, "_sleep", (sleeps if sleeps is not None else []).append)
    reads = [] if base_reads is None else base_reads

    def _base_changed(base_ref, candidates, stacks):
        reads.append((base_ref, sorted(candidates), sorted(stacks)))
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
    assert detect_report()["skipped_envs"] == ["prod"]
    assert detect_report()["applied_exempt_envs"] == []
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
    assert detect_report()["review_held_envs"] == ["stage"]
    assert detect_report()["applied_exempt_envs"] == ["dev-eu"]


def test_an_undetermined_decision_fails_before_any_read(tmp_path, monkeypatch):
    """Mutation: call `refuse_undetermined` only when `not auto` -- `dev-eu` is held and the
    run succeeds, red. Mutation: call it after `refuse_actor` -- `calls`, red."""
    calls = []
    with pytest.raises(SystemExit) as exc:
        _run_auto(tmp_path, monkeypatch, decision="", calls=calls)
    assert exc.value.code == (
        "::error::not authorized: could not determine the PR's review decision (got ''); the "
        "apply run's `review` job passed `detect` no decision this engine knows; check the "
        "pinned engine version, then retry."
    )
    assert type(exc.value) is SystemExit
    assert calls == []


def test_the_bare_form_runs_when_the_flag_is_false(tmp_path, monkeypatch):
    """The action passes `false` for a bare apply, whose actor is no App bot.

    Mutation: route on `os.environ.get("SHIPMATE_AUTO")` being non-empty -- the actor refuses.
    Mutation: pass the candidate stacks as `refuse_behind`'s `stacks` -- `stacks/idle` is
    missing, red."""
    reads = []
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        other=("stage",),
        actor="k3np",
        flag="false",
        base_reads=reads,
        untagged=("stacks/idle",),
    )
    assert _applied(parsed) == [("stacks/app", "dev-eu"), ("stacks/app", "stage")]
    assert "provenance_run" not in parsed
    assert detect_report()["held_cells"] == []
    assert "bare_envs" not in detect_report()
    assert reads == [(BASE, ["stacks/app"], ["stacks/app", "stacks/idle"])]


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
        pytest.param("reopened", id="reopened"),
        pytest.param("", id="dispatch"),
        pytest.param(None, id="older-engine"),
    ],
)
def test_a_plan_no_push_started_refuses(tmp_path, monkeypatch, action):
    """A reopen autoplan of the head dispatches no auto apply but becomes the newest autoplan;
    its clicker authorizes nothing. The listing is read, the permission is not; `_github` fails a
    single-run read.

    Mutation: drop the action check from `provenance_reason` (every row applies). Mutation:
    admit a missing action (`act not in (*AUTO_ACTIONS, None)`; older-engine applies).
    Mutation: answer `newest` with the bound run instead of reading the listing (no listing
    call, red)."""
    calls = []
    checks = [_check("stacks/app", "dev-eu", action=action)]
    assert _refusal(tmp_path, monkeypatch, checks=checks, calls=calls) == (
        f"::error::auto apply refused: the plan records of apply / dev-eu / stacks/app "
        f"({action!r}) name no event action that starts an auto apply (opened, synchronize or "
        f"ready_for_review). {REMEDY}"
    )
    assert LISTING[3] in calls
    assert not [c for c in calls if "/collaborators/" in c]


@pytest.mark.parametrize("action", ["opened", "synchronize", "ready_for_review"])
def test_a_plan_a_push_started_applies(tmp_path, monkeypatch, action):
    """Mutation: drop `opened` from `AUTO_ACTIONS` (the opened row refuses). Mutation: drop
    `ready_for_review` (the ready row refuses)."""
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


def _app_run(name):
    return json.dumps({"id": 9, "name": name, "app": APP})


def test_a_head_whose_only_app_check_is_the_apply_run_check_refuses():
    """The apply run check is created by an apply run, not by an autoplan.

    Mutation: read the slug from every App run -- the refusal does not fire."""
    with pytest.raises(SystemExit) as e:
        aad.refuse_actor([_app_run("shipmate / apply run")], APP_ID, BOT, BOT)
    assert str(e.value) == (
        "::error::auto apply refused: no check run on the head is authored by the shipmate "
        f"App, so its bot login is unknown. {REMEDY}"
    )


def test_another_app_check_beside_the_apply_run_check_names_the_bot():
    """Mutation: drop every App run starting with `shipmate / ` -- refused."""
    lines = [_app_run("shipmate / apply run"), _app_run("shipmate / other")]
    assert aad.refuse_actor(lines, APP_ID, BOT, BOT) is None


def test_a_shipmate_plan_record_refuses_naming_shipmate_apply(tmp_path, monkeypatch):
    """A `shipmate plan` run is a `workflow_dispatch`, never in the `pull_request_target`
    listing, which holds the head's earlier autoplan.

    Mutation: drop the id check from `provenance_reason` -- the run applies."""
    assert _refusal(tmp_path, monkeypatch, listing=(int(RUN) - 1,)) == (
        f"::error::auto apply refused: plan run {RUN} is not the newest autoplan of the head "
        f"(run {int(RUN) - 1} is). {REMEDY}"
    )


@pytest.mark.parametrize(
    ("entry", "refusal"),
    [
        pytest.param(
            {"event": "workflow_dispatch"},
            f"plan run {RUN} is a `workflow_dispatch` run, not an autoplan.",
            id="event",
        ),
        pytest.param(
            {"head_sha": BASE}, f"plan run {RUN} planned {BASE}, not the head {HEAD}.", id="head"
        ),
        pytest.param(
            {"event": None},
            f"plan run {RUN} is a `missing` run, not an autoplan.",
            id="null-event",
        ),
    ],
)
def test_a_listed_run_the_listing_filter_should_drop_refuses(tmp_path, monkeypatch, entry, refusal):
    """The listing's `event=` and `head_sha=` filters are GitHub's, so the listed object is
    checked again. Mutation: drop the event check from `provenance_reason` (event applies).
    Mutation: drop the head check (head applies). Mutation: `provenance_text`'s event branch
    renders `detail` raw (null-event reads `None`)."""
    code = _refusal(tmp_path, monkeypatch, listing=(entry,))
    assert code == f"::error::auto apply refused: {refusal} {REMEDY}"


def test_a_record_older_than_the_heads_newest_autoplan_refuses(tmp_path, monkeypatch):
    """Run 123999 is a newer autoplan of the head that recorded no cell.

    Mutation: sort `auto_source`'s listing ascending -- the run applies."""
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
    autoplans = [
        a
        for a in argv
        if any("/actions/workflows/" in x and "=workflow_dispatch" not in x for x in a)
    ]
    assert autoplans == [LISTING]


def test_cells_recording_two_runs_refuse(tmp_path, monkeypatch):
    """Mutation: drop the `len(runs) > 1` refusal -- the newest-run refusal fires instead."""
    checks = [_check("stacks/app", "dev-eu", "111"), _check("stacks/app", "prod", "222")]
    code = _refusal(tmp_path, monkeypatch, auto=("dev-eu", "prod"), checks=checks, listing=(222,))
    assert code == (
        "::error::auto apply refused: the cells to apply record different plan runs (plan run "
        "111: apply / dev-eu / stacks/app; plan run 222: apply / prod / stacks/app), so no one "
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
    assert detect_report()["review_held_envs"] == ["dev-eu", "prod"]
    assert _applied(parsed) == []
    assert (parsed["provenance_run"], parsed["base_sha"]) == ("", "")
    assert detect_report()["pusher"] == ""
    assert calls == [f"repos/o/r/commits/{HEAD}/check-runs?filter=all&per_page=100"]
    assert detect_report()["held_cells"] == []


def test_a_pusher_with_read_permission_refuses(tmp_path, monkeypatch):
    """Mutation: drop `subject=` from the `_permission_reason` call -- it names the commenter."""
    assert _refusal(tmp_path, monkeypatch, permission="read") == (
        "::error::not authorized: `shipmate apply` needs write access to this repository, and "
        "the autoplan's actor @k3np's permission is `read`."
    )


def test_an_unreadable_pusher_permission_refuses_with_the_auto_retry(tmp_path, monkeypatch):
    """A failed read is an unreadable permission.

    Mutation: drop `retry=` from the `_permission_reason` call -- it says "comment again"."""
    code = _refusal(tmp_path, monkeypatch, permission=SystemExit("::error::gh: Not Found"))
    assert code == (
        "::error::not authorized: could not read the autoplan's actor @k3np's permission on this "
        "repository, so `shipmate apply` was not run. This run's log has the API response; "
        "push again, or a writer comments `shipmate apply`."
    )


def test_a_pusher_with_write_permission_applies(tmp_path, monkeypatch):
    """Mutation: admit only `admin` in `_permission_reason` -- red."""
    calls = []
    parsed = _run_auto(tmp_path, monkeypatch, calls=calls)
    assert _applied(parsed) == [("stacks/app", "dev-eu")]
    assert "repos/o/r/collaborators/k3np/permission" in calls


def test_the_pusher_output_is_the_admitted_autoplans_actor_not_this_runs(tmp_path, monkeypatch):
    """This run's actor and triggering actor are the App's bot; the listed autoplan's actor,
    the pusher the permission check admitted, is `k3np`.

    Mutation: write `os.environ["GITHUB_ACTOR"]` as `pusher` -- red."""
    _run_auto(tmp_path, monkeypatch, actor=BOT, triggering=BOT)
    assert detect_report()["pusher"] == "k3np"


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
    assert (reads, parsed["base_sha"]) == ([(tip, ["stacks/app"], ["stacks/app"])], tip)


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
    entry = {"actor": {"login": "a b"}, "triggering_actor": {"login": "a b"}}
    assert _refusal(tmp_path, monkeypatch, listing=(entry,)) == (
        "::error::not authorized: could not read the autoplan's actor's permission on this "
        "repository, so `shipmate apply` was not run. This run's log has the API response; push "
        "again, or a writer comments `shipmate apply`."
    )


def test_a_bot_pusher_refuses_naming_the_bot(tmp_path, monkeypatch):
    """A bot's push never auto-applies, and its refusal says so rather than calling the
    permission unreadable.

    Mutation: drop the `[bot]` branch from `pusher_reason` -- the unreadable wording, red."""
    entry = {"actor": {"login": "renovate[bot]"}, "triggering_actor": {"login": "renovate[bot]"}}
    assert _refusal(tmp_path, monkeypatch, listing=(entry,)) == (
        "::error::not authorized: the autoplan was started by @renovate[bot], a bot account, "
        "which auto never authorizes; a writer comments `shipmate apply`."
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
    entry = {"actor": {"login": actor}, "triggering_actor": {"login": trigger}}
    calls = []
    assert _refusal(tmp_path, monkeypatch, listing=(entry,), calls=calls) == (
        f"::error::not authorized: the autoplan ran as @{actor} but was triggered by "
        f"@{trigger}, so it was re-run, and auto authorizes only the push or ready click "
        "itself; a writer comments `shipmate apply`."
    )
    assert not [c for c in calls if "/collaborators/" in c]


@pytest.mark.parametrize(
    ("kw", "what", "error"),
    [
        pytest.param(
            {"listing": ("abc",)},
            "autoplan listing",
            "JSONDecodeError('Expecting value: line 1 column 1 (char 0)')",
            id="listing",
        ),
    ],
)
def test_a_malformed_plan_run_read_refuses_without_a_traceback(
    tmp_path, monkeypatch, kw, what, error
):
    """Mutation: drop the `except` around `auto_source` (listing escapes as a `ValueError`)."""
    assert _refusal(tmp_path, monkeypatch, **kw) == (
        f"::error::auto apply refused: the {what} read returned a malformed answer ({error}). "
        f"{REMEDY}"
    )


@pytest.mark.parametrize(
    ("actor", "trigger"),
    [
        pytest.param({"login": None}, {"login": None}, id="null"),
        pytest.param({"login": "k3np"}, {"login": None}, id="no-trigger"),
        pytest.param({"login": ""}, {"login": ""}, id="empty"),
        pytest.param({"login": 7}, {"login": 7}, id="not-a-string"),
    ],
)
def test_a_missing_actor_login_refuses_naming_the_unknown_pusher(
    tmp_path, monkeypatch, actor, trigger
):
    """The listing's projection always emits `{"login": ...}`, null when GitHub gives none.
    Checked before the equality, so two missing logins are not the same pusher.

    Mutation: drop the `not (actor and trigger)` refusal -- null and empty read as unreadable,
    no-trigger as a re-run, red. Mutation: drop `isinstance(login, str)` in `_login`
    (not-a-string raises)."""
    entry = {"actor": actor, "triggering_actor": trigger}
    assert _refusal(tmp_path, monkeypatch, listing=(entry,)) == (
        "::error::not authorized: the autoplan run names no actor or no triggering actor login, "
        "so who started it is unknown; a writer comments `shipmate apply`."
    )


@pytest.mark.parametrize("body", ["[]", "{}", "not json"])
def test_a_malformed_permission_body_refuses_as_unreadable(tmp_path, monkeypatch, body):
    """Mutation: drop `(LookupError, TypeError, ValueError)` from `pusher_reason`'s permission
    read -- each row escapes as a malformed answer, red."""
    assert _refusal(tmp_path, monkeypatch, permission=_Raw(body)) == (
        "::error::not authorized: could not read the autoplan's actor @k3np's permission on this "
        "repository, so `shipmate apply` was not run. This run's log has the API response; "
        "push again, or a writer comments `shipmate apply`."
    )


def test_an_empty_autoplan_listing_refuses_naming_it(tmp_path, monkeypatch):
    """Mutation: drop the `newest is None` refusal -- a `TypeError` escapes."""
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
    assert detect_report()["held_cells"] == [
        {"stack": "stacks/app", "environment": "dev-eu", "cause": "locked", "holder": 7}
    ]


def test_detect_grants_exactly_these_permissions():
    """Every form reads the pull request, and a reusable workflow's job cannot exceed it; no
    permission grows.

    Mutation: drop `pull-requests: read` from `apply-run.yml`'s `detect`.
    Mutation: add `contents: write` to it."""
    assert workflow_jobs("apply-run.yml")["detect"]["permissions"] == {
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
    Mutation: pass `base_changed_stacks` no candidate stacks (`set()`) -- red.
    Mutation: pass the candidate stacks as `behind_cells`' `stacks` -- `stacks/idle` is missing,
    red."""
    reads = []
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        auto=("dev-eu", "prod"),
        stacks=("stacks/app", "stacks/dns"),
        order={"prod": ["dev-eu"]},
        base_changed={"stacks/app", "stacks/other"},
        base_reads=reads,
        untagged=("stacks/idle",),
    )
    assert _applied(parsed) == [("stacks/dns", "dev-eu")]
    assert detect_report()["held_cells"] == [
        {"stack": "stacks/app", "environment": "dev-eu", "cause": "overlap"},
        {"stack": "stacks/app", "environment": "prod", "cause": "overlap"},
    ]
    assert detect_report()["skipped_envs"] == ["prod"]
    assert reads == [
        (BASE, ["stacks/app", "stacks/dns"], ["stacks/app", "stacks/dns", "stacks/idle"])
    ]
    assert parsed["base_sha"] == BASE
    assert (
        f"::notice title=apply-all-detect::head={HEAD} cells=4 pending=4 envlevels=[1, 0, 0, 0] "
        "excluded_targeted=[] skipped_after_targeted=['prod'] review_held=[] waiting=0 locked=0 "
        "overlap=2 attempted=0 applied_exempt=[] review_decision='NONE'"
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
    assert detect_report()["held_cells"] == []


def test_an_all_overlapping_run_names_no_plan_run_but_its_base(tmp_path, monkeypatch):
    """Nothing applies, so neither the plan run nor its pusher is read; the base tip the overlap
    was computed on is still written.

    Mutation: read the provenance whenever a pull request read happened -- the run is read,
    red."""
    calls = []
    parsed = _run_auto(tmp_path, monkeypatch, base_changed={"stacks/app"}, calls=calls)
    assert _applied(parsed) == []
    assert (parsed["provenance_run"], parsed["base_sha"]) == ("", BASE)
    assert detect_report()["held_cells"] == [
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
    assert detect_report()["skipped_envs"] == ["prod", "stg"]
    assert detect_report()["skipped_after"] == {"prod": ["dev"], "stg": ["dev"]}
    assert detect_report()["bare_envs"] == ["dev"]


def test_a_held_bare_environment_is_named_held_not_left_for_a_bare_apply(tmp_path, monkeypatch):
    """Under REVIEW_REQUIRED the bare `stage` is held (its entry is not review-exempt), so it is
    named on the held line alone; the auto `dev-eu` applies.

    Mutation: leave `stopped` out of the `bare_envs` subtraction -- `stage` is named, red."""
    _run_auto(tmp_path, monkeypatch, other=("stage",), decision="REVIEW_REQUIRED")
    assert detect_report()["review_held_envs"] == ["stage"]
    assert detect_report()["bare_envs"] == []
    assert detect_report()["skipped_after"] == {}


def test_a_cell_both_locked_and_overlapping_is_held_once_per_cause():
    """Evicting the lock does not clear the overlap, so the comment must name both remedies.
    Mutation: drop overlap cells already in `locked` (the overlap entry is gone)."""
    cell = {"stack": "stacks/app", "environment": "dev"}
    assert aad.held_cells([{**cell, "holder": 7}], [cell]) == [
        {**cell, "holder": 7, "cause": "locked"},
        {**cell, "cause": "overlap"},
    ]


NEWER = 123460
PLANS_READ = [
    "gh",
    "api",
    (
        "repos/o/r/actions/workflows/shipmate.yml/runs?event=workflow_dispatch"
        "&created=%3E%3D2026-11-15T22%3A47%3A48Z&per_page=100&page=1"
    ),
]
WAIT = "Wait for it to finish, review its plan, then comment `shipmate apply`."
LISTING_REFUSAL = (
    "::error::auto apply refused: the shipmate plan runs of this pull request could not all be "
    f"listed, so a newer one may exist. {REMEDY}"
)
_FAIL = SystemExit("::error::command failed (1): gh api")


def _live_refusal(status):
    return (
        f"::error::auto apply refused: shipmate plan run {NEWER} of this pull request is "
        f"{status} and newer than plan run {RUN}. {WAIT}"
    )


def test_the_shipmate_plan_listing_is_one_read_from_the_plan_runs_creation(tmp_path, monkeypatch):
    """The listing is read from the admitted autoplan's `created_at`, `>=` encoded, every
    status. Mutation: drop the `created=` filter (the argv differs)."""
    argv = []
    _run_auto(tmp_path, monkeypatch, argv=argv)
    assert [a for a in argv if any("event=workflow_dispatch" in x for x in a)] == [PLANS_READ]


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param(
            (
                {"status": "completed", "conclusion": "success", "run_attempt": 1},
                {"id": 123999, "status": "completed", "conclusion": "success", "run_attempt": 1},
            ),
            id="zero-cell",
        ),
        pytest.param(
            (int(RUN), {"id": 123999, "status": "in_progress", "conclusion": None}),
            id="in-progress",
        ),
    ],
)
def test_a_newer_autoplan_still_refuses_before_any_plan_listing(tmp_path, monkeypatch, entries):
    """A newer autoplan of the head, one that ended planning no cell or one still running,
    refuses as before, and the `shipmate plan` listing is not read. Mutation: drop
    `provenance_reason`'s `newer` clause (both apply). Mutation: `deferred` returns True
    (zero-cell's planned jobs read it past, and run `RUN` applies)."""
    calls = []
    jobs = {123999: plan_jobs(123999)}
    assert _refusal(tmp_path, monkeypatch, listing=entries, calls=calls, jobs=jobs) == (
        f"::error::auto apply refused: plan run {RUN} is not the newest autoplan of the head "
        f"(run 123999 is). {REMEDY}"
    )
    assert not [c for c in calls if "event=workflow_dispatch" in c]


#: Push run E (`RUN`) and a later ready-for-review run R by its clicker, both ended `success`.
_PUSHED = {"status": "completed", "conclusion": "success", "run_attempt": 1}
_CLICKED = {"login": "octocat"}
_READY = {"id": 123999, **_PUSHED, "actor": _CLICKED, "triggering_actor": _CLICKED}


def test_a_ready_run_that_deferred_leaves_the_push_runs_plan_applying_as_its_clicker(
    tmp_path, monkeypatch
):
    """R's jobs show it deferred to E, so `refuse_provenance` returns E's whole listed object as
    the plan and R's as the authorizer, `authorize.pusher_reason` reads R's clicker's
    permission (octocat), and the cell applies with `pusher` octocat. Mutation: return
    `(top, top)` from `auto_source` (R is newest and refuses `newer`). Mutation: pass
    `refuse_pusher` the plan (k3np's permission is read)."""
    returned, calls = [], []
    real = aad.refuse_provenance
    monkeypatch.setattr(
        aad, "refuse_provenance", lambda *a: returned.append(real(*a)) or returned[-1]
    )
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        listing=(_PUSHED, _READY),
        jobs={123999: deferred_jobs()},
        calls=calls,
    )
    assert _applied(parsed) == [("stacks/app", "dev-eu")]
    assert returned == [
        (
            {
                "id": int(RUN),
                "event": "pull_request_target",
                "head_sha": HEAD,
                "created_at": "2026-11-15T22:47:48Z",
                "actor": {"login": "k3np"},
                "triggering_actor": {"login": "k3np"},
                "status": "completed",
                "conclusion": "success",
                "run_attempt": 1,
            },
            {
                "id": 123999,
                "event": "pull_request_target",
                "head_sha": HEAD,
                "created_at": "2026-11-15T22:47:48Z",
                "actor": {"login": "octocat"},
                "triggering_actor": {"login": "octocat"},
                "status": "completed",
                "conclusion": "success",
                "run_attempt": 1,
            },
        )
    ]
    assert [c for c in calls if "/collaborators/" in c] == [
        "repos/o/r/collaborators/octocat/permission"
    ]
    assert detect_report()["pusher"] == "octocat"


def test_a_ready_run_still_dispatching_authorizes_the_push_runs_plan(tmp_path, monkeypatch):
    """R is `in_progress` in its `defer-auto` job while the auto run it dispatched detects.
    Mutation: read a run's jobs only once it ended `completed` (R refuses `newer`)."""
    live = {**_READY, "status": "in_progress", "conclusion": None}
    calls = []
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        listing=(_PUSHED, live),
        jobs={123999: deferred_in_progress_jobs()},
        calls=calls,
    )
    assert _applied(parsed) == [("stacks/app", "dev-eu")]
    assert [c for c in calls if "/collaborators/" in c] == [
        "repos/o/r/collaborators/octocat/permission"
    ]


def test_a_running_ready_run_whose_defer_has_not_ended_refuses_as_newer(tmp_path, monkeypatch):
    """Mutation: treat any in-progress run as deferred (E applies)."""
    live = {**_READY, "status": "in_progress", "conclusion": None}
    body = json.loads(deferred_in_progress_jobs())
    for job in body["jobs"]:
        if job["name"] == "plan / defer":
            job |= {"status": "in_progress", "conclusion": None}
    jobs = {123999: json.dumps(body)}
    assert _refusal(tmp_path, monkeypatch, listing=(_PUSHED, live), jobs=jobs) == (
        f"::error::auto apply refused: plan run {RUN} is not the newest autoplan of the head "
        f"(run 123999 is). {REMEDY}"
    )


@pytest.mark.parametrize(
    ("clicker", "permission", "refusal"),
    [
        pytest.param(
            "renovate[bot]",
            "write",
            "::error::not authorized: the autoplan was started by @renovate[bot], a bot "
            "account, which auto never authorizes; a writer comments `shipmate apply`.",
            id="bot",
        ),
        pytest.param(
            "octocat",
            "read",
            "::error::not authorized: `shipmate apply` needs write access to this repository, "
            "and the autoplan's actor @octocat's permission is `read`.",
            id="reader",
        ),
    ],
)
def test_a_bot_or_reader_clicker_refuses_the_deferred_plan(
    tmp_path, monkeypatch, clicker, permission, refusal
):
    """The pusher k3np has `write`; the clicker decides. Mutation: pass `refuse_pusher` the plan
    (k3np applies)."""
    login = {"login": clicker}
    ready = {**_READY, "actor": login, "triggering_actor": login}
    kw = {"listing": (_PUSHED, ready), "jobs": {123999: deferred_jobs()}}
    assert _refusal(tmp_path, monkeypatch, permission=permission, **kw) == refusal


#: The refusal of a draft-origin plan no deferred ready run authorizes, hand-written.
DRAFT_REFUSAL = (
    "::error::auto apply refused: the plan records of apply / dev-eu / stacks/app say their "
    "plan's push landed on a draft, and no ready-for-review run of the head that deferred to "
    f"plan run {RUN} is listed, so nothing authorizes it. {REMEDY}"
)


def test_a_draft_origin_plan_with_no_deferred_ready_run_listed_refuses(tmp_path, monkeypatch):
    """Only the push run is listed and its record holds `"draft": true`: its pusher authorizes
    nothing, and no permission is read. Mutation: drop the `draft` cause from
    `provenance_reason` (k3np applies). Mutation: pass a draft record whenever `authorizer` is
    not None (`(plan, plan)` passes, k3np applies)."""
    calls = []
    checks = [_check("stacks/app", "dev-eu", draft=True)]
    assert _refusal(tmp_path, monkeypatch, checks=checks, calls=calls) == DRAFT_REFUSAL
    assert not [c for c in calls if "/collaborators/" in c]


def test_a_draft_origin_plan_under_a_deferred_ready_run_applies_as_its_clicker(
    tmp_path, monkeypatch
):
    """Mutation: refuse every draft record (the draft refusal fires)."""
    calls = []
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        checks=[_check("stacks/app", "dev-eu", draft=True)],
        listing=(_PUSHED, _READY),
        jobs={123999: deferred_jobs()},
        calls=calls,
    )
    assert _applied(parsed) == [("stacks/app", "dev-eu")]
    assert detect_report()["pusher"] == "octocat"


def test_a_plan_whose_record_holds_no_draft_key_applies_as_its_pusher(tmp_path, monkeypatch):
    """An older engine's record has no `draft` key. Mutation: read an absent key as draft
    (`record.get("draft", True) is True` in `draft_names`; the draft refusal fires)."""
    parsed = _run_auto(tmp_path, monkeypatch)
    assert _applied(parsed) == [("stacks/app", "dev-eu")]
    assert detect_report()["pusher"] == "k3np"


def test_a_cancelled_ready_run_is_the_newest_autoplan_and_refuses(tmp_path, monkeypatch):
    """A cancelled R did not defer, whatever its jobs. Mutation: read past any completed run
    without its jobs (E applies)."""
    ready = {**_READY, "conclusion": "cancelled"}
    jobs = {123999: deferred_jobs()}
    assert _refusal(tmp_path, monkeypatch, listing=(_PUSHED, ready), jobs=jobs) == (
        f"::error::auto apply refused: plan run {RUN} is not the newest autoplan of the head "
        f"(run 123999 is). {REMEDY}"
    )


@pytest.mark.parametrize(
    ("plan", "status"),
    [
        pytest.param(dispatched_plan(NEWER, PR_NUMBER), "in progress", id="app"),
        pytest.param(
            dispatched_plan(NEWER, PR_NUMBER, actor={"login": "octocat"}),
            "in progress",
            id="human",
        ),
        pytest.param(dispatched_plan(NEWER, PR_NUMBER, status="queued"), "queued", id="queued"),
        pytest.param(
            dispatched_plan(
                NEWER, PR_NUMBER, display_title=f"shipmate · plan pr={PR_NUMBER} ref=x comment="
            ),
            "in progress",
            id="unparsed-plan-title",
        ),
    ],
)
def test_a_live_newer_shipmate_plan_run_refuses(tmp_path, monkeypatch, plan, status):
    """Created in the plan run's own second with a greater id, by any actor; no jobs are read
    for a live run (the double fails any jobs read). Mutations: compare by `created_at` (equal,
    so none is newer); filter by the App's actor (human applies); ignore live runs; hard-code
    `in progress` (queued differs); ignore unparsed titles that start `shipmate · plan pr=<n> `
    (unparsed-plan-title applies)."""
    assert _refusal(tmp_path, monkeypatch, plans=[plan]) == _live_refusal(status)


def test_the_auto_form_refuses_a_live_plan_run_without_waiting(tmp_path, monkeypatch):
    """The commanded forms' wait is not the auto form's: a live newer plan run refuses at once.
    Mutation: drop the `auto` guard on `bare_main`'s `wait_for_plans` call (the status listings
    are read, and the wait times out on the fake clock)."""
    clock = fake_clock(monkeypatch, aad)
    calls = []
    refusal = _refusal(
        tmp_path, monkeypatch, plans=[dispatched_plan(NEWER, PR_NUMBER)], calls=calls
    )
    assert refusal == _live_refusal("in progress")
    assert [c for c in calls if "&status=" in c] == []
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "conclusion",
    [pytest.param("success", id="success"), pytest.param("failure", id="failure-after-detect")],
)
def test_a_completed_newer_shipmate_plan_run_that_planned_refuses(
    tmp_path, monkeypatch, conclusion
):
    """A failed newer plan whose `plan / detect` succeeded counts: it may have published no
    record, yet it planned the head again. Mutations: count no completed run; count only
    completed `success` runs (failure-after-detect applies)."""
    plan = dispatched_plan(NEWER, PR_NUMBER, status="completed", conclusion=conclusion)
    assert _refusal(tmp_path, monkeypatch, plans=[plan], jobs={NEWER: plan_jobs(NEWER)}) == (
        f"::error::auto apply refused: shipmate plan run {NEWER} of this pull request planned "
        f"after plan run {RUN}. Review its plan, then comment `shipmate apply`."
    )


def _completed(conclusion="cancelled"):
    return [dispatched_plan(NEWER, PR_NUMBER, status="completed", conclusion=conclusion)]


@pytest.mark.parametrize(
    ("plans", "jobs"),
    [
        pytest.param(
            _completed(),
            {NEWER: plan_jobs(NEWER, names=["plan / claim", "release"])},
            id="cancelled-before-detect",
        ),
        pytest.param(_completed(), {NEWER: plan_jobs(NEWER, names=[])}, id="cancelled-queued"),
        pytest.param(
            _completed("startup_failure"), {NEWER: plan_jobs(NEWER, names=[])}, id="startup-failure"
        ),
        pytest.param(
            _completed("success"),
            {NEWER: plan_jobs(NEWER, detect="skipped")},
            id="detect-skipped",
        ),
        pytest.param([dispatched_plan(123450, PR_NUMBER)], {}, id="older"),
        pytest.param([dispatched_plan(int(RUN), PR_NUMBER)], {}, id="the-plan-run-itself"),
        pytest.param([dispatched_plan(NEWER, "80")], {}, id="other-pull-request"),
        pytest.param(
            [
                dispatched_plan(
                    NEWER, PR_NUMBER, display_title=f"shipmate · apply pr={PR_NUMBER} ref=x"
                )
            ],
            {},
            id="unparsed-apply-title",
        ),
    ],
)
def test_a_shipmate_plan_run_that_does_not_count_applies(tmp_path, monkeypatch, plans, jobs):
    """A completed run whose jobs listing is complete and empty never started a job, so it did
    not plan. Mutations: count every completed run (the cancelled, startup and skipped rows
    refuse); read the jobs through `run_jobs`, which reads an empty listing as unlistable
    (cancelled-queued and startup-failure refuse); drop the id filter (older refuses); `>=`
    against the plan run (the-plan-run-itself refuses); drop the pull request filter
    (other-pull-request refuses); count every unparsed title (unparsed-apply-title refuses)."""
    parsed = _run_auto(tmp_path, monkeypatch, plans=plans, jobs=jobs)
    assert _applied(parsed) == [("stacks/app", "dev-eu")]


@pytest.mark.parametrize(
    "kw",
    [
        pytest.param({"plans": _FAIL}, id="listing-fails"),
        pytest.param({"listing": ({"created_at": None},)}, id="created-at-null"),
        pytest.param({"listing": ({"created_at": "2026-11-15T22:47:48"},)}, id="created-at-bad"),
        pytest.param({"plans": _completed(), "jobs": {NEWER: _FAIL}}, id="jobs-fail"),
        pytest.param(
            {
                "plans": _completed(),
                "jobs": {NEWER: plan_jobs(NEWER, names=["plan / claim"], total=2)},
            },
            id="jobs-count-short",
        ),
        pytest.param({"plans": [dispatched_plan(NEWER, PR_NUMBER, status=None)]}, id="status-null"),
        pytest.param(
            {"plans": [dispatched_plan(NEWER, PR_NUMBER, status="completed", conclusion=None)]},
            id="conclusion-null",
        ),
        pytest.param(
            {
                "plans": [dispatched_plan(NEWER, PR_NUMBER, run_attempt="1", status="completed")],
                "jobs": {NEWER: plan_jobs(NEWER, names=[])},
            },
            id="attempt-not-int",
        ),
        pytest.param(
            {"plans": _completed(), "jobs": {NEWER: plan_jobs(NEWER, attempt=2)}},
            id="jobs-of-another-attempt",
        ),
        pytest.param(
            {
                "plans": _completed(),
                "jobs": {NEWER: plan_jobs(NEWER, names=["plan / detect", "plan / detect"])},
            },
            id="two-detect-jobs",
        ),
    ],
)
def test_a_newer_plan_it_cannot_rule_out_refuses(tmp_path, monkeypatch, kw):
    """Mutations: answer a failed listing as complete (listing-fails applies); drop the
    timestamp check (a malformed `created_at` is read, created-at-bad applies); answer an
    unlistable jobs read as not planned (jobs-fail applies); drop the jobs listing's count check
    (jobs-count-short applies, its one listed job no detect); read a run's status unchecked
    (status-null refuses as `None` and newer, conclusion-null reads its jobs, an unqueued read);
    drop the run attempt comparison (jobs-of-another-attempt applies); drop the `run_attempt`
    type check (attempt-not-int, whose listing holds no job to compare, applies); answer
    `planned`'s `ValueError` as not planned (two-detect-jobs applies)."""
    assert _refusal(tmp_path, monkeypatch, **kw) == LISTING_REFUSAL


def test_the_first_counted_newer_plan_ends_the_read(tmp_path, monkeypatch):
    """A live newer plan refuses without reading the jobs of a later completed one (the double
    fails any jobs read). Mutation: keep reading after the first counted run."""
    plans = [
        dispatched_plan(NEWER, PR_NUMBER),
        dispatched_plan(NEWER + 1, PR_NUMBER, status="completed"),
    ]
    assert _refusal(tmp_path, monkeypatch, plans=plans) == _live_refusal("in progress")


def test_the_auto_form_writes_its_eleven_fields_in_one_report(tmp_path, monkeypatch):
    """The auto form's report holds the bare form's nine fields plus `pusher` and `bare_envs`;
    `provenance_run` and `base_sha` stay outputs, read by the env-level jobs.

    Mutation: write `pusher` as an output instead of a report field -- red."""
    parsed = _run_auto(tmp_path, monkeypatch, actor=BOT, triggering=BOT)
    assert set(detect_report()) == {
        "excluded_envs",
        "skipped_envs",
        "skipped_after",
        "review_held_envs",
        "applied_exempt_envs",
        "review_not_required_envs",
        "waiting_cells",
        "held_cells",
        "env_order",
        "pusher",
        "bare_envs",
        "complete",
    }
    assert {"provenance_run", "base_sha"} <= set(parsed)
    assert not {"pusher", "bare_envs", "held_cells", "report"} & set(parsed)


@pytest.mark.parametrize("changed", [(), ("stacks/app",)], ids=["record", "record-and-overlap"])
def test_a_behind_record_holds_its_cell_once_and_its_sibling_applies(
    tmp_path, monkeypatch, changed
):
    """`stacks/app`'s newest check is a behind record over an older run's queued check with a
    digest: the cell is held with cause `overlap`, once when the fresh overlap also names it,
    and `stacks/dns` applies.

    Mutation: drop the behind records from the holds (`frozenset()` for `ag.behind_names(...)`)
    -- the cell reaches `with_plan_runs` and the digest refusal fires, red.
    Mutation: refuse the whole run as the commanded forms do -- red.
    Mutation: list a cell under both causes twice -- red."""
    checks = [
        _check("stacks/app", "dev-eu", cells=2, id=1),
        _check("stacks/dns", "dev-eu", cells=2, id=2),
        behind_check("stacks/app", "dev-eu", plan_run="123457", cells=1, id=3, app=APP),
    ]
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        stacks=("stacks/app", "stacks/dns"),
        checks=checks,
        base_changed=set(changed),
    )
    assert _applied(parsed) == [("stacks/dns", "dev-eu")]
    assert detect_report()["held_cells"] == [
        {"stack": "stacks/app", "environment": "dev-eu", "cause": "overlap"}
    ]


@pytest.mark.parametrize(
    ("link", "run", "jobs", "refusal"), HELD_CASES.values(), ids=HELD_CASES.keys()
)
def test_an_auto_apply_refuses_a_cell_another_run_holds_and_holds_one_it_never_applied(
    tmp_path, monkeypatch, link, run, jobs, refusal
):
    """Run 77 holds `stacks/app / dev-eu`'s apply check `in_progress`: a live run, an ended
    run that applied it unsaved or saved, an unreadable run and a link naming no run refuse the
    whole apply; an ended run that never applied it attempted the cell, so the auto run holds it.

    Mutation: drop the `refuse_held` call in `bare_main` (the held check reads as pending, as
    before) -- every refusing case applies, red. Mutation: drop the `in_progress` branch of
    `attempted_cells` -- the never-applied case applies, red."""
    kw = {
        "checks": [held_check("o/r", link, cells=1, app=APP)],
        "runs": holder_answers("o/r", run, jobs),
    }
    if refusal is None:
        assert _applied(_run_auto(tmp_path, monkeypatch, **kw)) == []
        assert detect_report()["held_cells"] == [attempted_cell("stacks/app", "dev-eu")]
        return
    with pytest.raises(SystemExit) as exc:
        _run_auto(tmp_path, monkeypatch, **kw)
    assert str(exc.value) == refusal


#: The counts pending-checks writes as a fresh check's output title and into its record.
TITLE = "+1 ~0 -0"
#: A fresh check's summary: `patch-checks.with_output` keeps it when it retitles the check.
SUMMARY = "~~~diff\n+ null_resource.app\n~~~"
OWN_PAGE = "https://github.com/o/r/actions/runs/999"
HOLDER_PAGE = f"https://github.com/o/r/actions/runs/{HOLDER}"


def _titled(stack="stacks/app", shown=TITLE, record_title=TITLE, cells=1, **kw):
    """`_check` of `stack` in `dev-eu` whose record holds `record_title` (none when None) and
    whose output title is `shown`: as pending-checks writes it when `shown` is the record title,
    as apply-complete retitles it otherwise."""
    check = _check(stack, "dev-eu", cells=cells, **kw)
    record = json.loads(check["external_id"])
    if record_title is not None:
        record["title"] = record_title
    return {
        **check,
        "external_id": json.dumps(record),
        "output": {"title": shown, "summary": SUMMARY},
    }


def _holding(case, **kw):
    """`(check, runs, refusal)` of `HELD_CASES[case]`: `_titled`'s fresh check, `in_progress`
    under run `HOLDER` read as that case reads."""
    link, run, jobs, refusal = HELD_CASES[case]
    check = {**_titled(**kw), "status": "in_progress", "details_url": HOLDER_PAGE + link}
    return check, holder_answers("o/r", run, jobs), refusal


_NO_RUN = (
    "::error::apply aborted: no plan run recorded for apply / dev-eu / stacks/app: the apply "
    "check names no plan run to apply from (post-merge, the cell may have no apply check at "
    "all), so the apply is refused. Re-plan these stacks on their pull request, then apply "
    "again; if that pull request has already merged, a new pull request touching them plans and "
    "applies them afresh."
)


def _rows():
    """`{row: (checks, runs, outcome)}` over `stacks/app / dev-eu`, one per state of its newest
    App apply check an auto run can read; `outcome` is `apply`, the cause it is held for, or the
    whole refusal."""
    live, live_runs, live_refusal = _holding("live", id=1)
    deploy, deploy_runs, _ = _holding("ended never applied", id=1)
    rows = {
        "2 fresh": ([_titled()], (), "apply"),
        "2b stale": (
            [{**_titled(), "status": "completed", "conclusion": "stale"}],
            (),
            "attempted",
        ),
        "2b stale retitled": (
            [{**_titled(shown="Failed: x"), "status": "completed", "conclusion": "stale"}],
            (),
            "attempted",
        ),
        "3 retitled": ([_titled(shown="Failed: x")], (), "attempted"),
        "3 completed cancelled": (
            [{**_titled(shown="Failed: x"), "status": "completed", "conclusion": "cancelled"}],
            (),
            "attempted",
        ),
        "4 behind": ([behind_check("stacks/app", plan_run=RUN, cells=1, app=APP)], (), "overlap"),
        "5 no title": ([_titled(shown="Failed: x", record_title=None)], (), "apply"),
        "5 empty title": ([_titled(shown="Failed: x", record_title="")], (), "apply"),
        "5 title not a str": ([_titled(shown="Failed: x", record_title=5)], (), "apply"),
        "6 no record": ([{**_titled(), "external_id": None}], (), _NO_RUN),
        "6 undecodable": ([{**_titled(), "external_id": "not json"}], (), _NO_RUN),
        "7 own run": (
            [{**_titled(), "status": "in_progress", "details_url": OWN_PAGE}],
            (),
            "apply",
        ),
        "13 deploy": ([deploy], deploy_runs, "attempted"),
        "14 held over live": (
            [live, _titled(shown="Failed: x", id=2)],
            live_runs,
            live_refusal,
        ),
        "15a autoplan re-plan": (
            [_titled(shown="Failed: x", plan_run="111", id=1), _titled(id=2)],
            (),
            "apply",
        ),
        "15a autoplan re-plan over deploy": ([deploy, _titled(id=2)], deploy_runs, "apply"),
        "15b dispatched re-plan": (
            [_titled(shown="Failed: x", id=1), _titled(plan_run="555", id=2)],
            (),
            (
                "::error::auto apply refused: plan run 555 is not the newest autoplan of the head "
                f"(run {RUN} is). {REMEDY}"
            ),
        ),
        "16 fresh over live": ([live, _titled(id=2)], live_runs, live_refusal),
    }
    for row, case in (
        ("8 live", "live"),
        ("9 unreadable", "unreadable"),
        ("10 unknown", "no run named"),
        ("11 ended", "ended unsaved"),
        ("12 saved", "ended saved"),
    ):
        check, runs, refusal = _holding(case)
        rows[row] = ([check], runs, refusal)
    return rows


_ROWS = _rows()


@pytest.mark.parametrize(("checks", "runs", "outcome"), _ROWS.values(), ids=_ROWS.keys())
def test_every_state_of_a_candidates_newest_check_applies_holds_or_refuses(
    tmp_path, monkeypatch, checks, runs, outcome
):
    """Each row of the state table an auto run reads, with its whole outcome.

    Mutation: hold every candidate whose check is not `in_progress` -- `2 fresh`, red.
    Mutation: hold on a missing record title -- the `5` rows, red.
    Mutation: drop R-c (the `stale` test) -- `2b stale`, red.
    Mutation: key R-a on `status == "queued"` -- `3 completed cancelled`, red.
    Mutation: drop `attempted` from `holds` -- `3 retitled` applies, red.
    Mutation: read the oldest check per name -- `15a autoplan re-plan` holds, red.
    Mutation: hold a cell whenever any check of it is retitled -- `15a autoplan re-plan`, red.
    Mutation: call `refuse_held` over `runnable` only -- `14 held over live` holds, red.
    Mutation: delete `refuse_provenance`'s `provenance_reason` raise -- `15b` applies, red."""
    kw = {"checks": checks, "runs": runs}
    if outcome not in ("apply", "attempted", "overlap"):
        with pytest.raises(SystemExit) as exc:
            _run_auto(tmp_path, monkeypatch, **kw)
        assert str(exc.value) == outcome
        return
    parsed = _run_auto(tmp_path, monkeypatch, **kw)
    if outcome == "apply":
        assert (_applied(parsed), detect_report()["held_cells"]) == ([("stacks/app", "dev-eu")], [])
        return
    held_cell = {"stack": "stacks/app", "environment": "dev-eu", "cause": outcome}
    assert (_applied(parsed), detect_report()["held_cells"]) == ([], [held_cell])


def _cancelled(check):
    """`check` as apply-complete's `complete` job retitles it for a cancelled cell job: its
    `writes` entry through `patch-checks.with_output`."""
    complete, patch = load_script("apply-complete"), load_script("patch-checks")
    [entry] = complete.writes({"conclusion": "cancelled", "ids": [check["id"]], "url": ""}, "o/r")
    return {**check, **patch.with_output(entry, check)}


_DNS_AFTER_APP = {"stacks/app": set(), "stacks/dns": {"stacks/app"}}
_DEV_EU_TAGS = {"stacks/app": ["env/dev-eu"], "stacks/dns": ["env/dev-eu"]}


@pytest.mark.parametrize(
    "app",
    [
        _titled(shown="Failed: Error: creating null_resource", cells=2, id=1),
        _titled(shown="Cancelled", cells=2, id=1),
        _titled(shown="Not run", cells=2, id=1),
        _titled(shown="Not applied (timed_out)", cells=2, id=1),
        _titled(shown="a title no writer lists", cells=2, id=1),
        _cancelled(_titled(cells=2, id=1)),
    ],
    ids=["failed", "cancelled", "not-run", "not-applied", "unlisted", "written-by-apply-complete"],
)
def test_an_auto_run_holds_a_retitled_cell_and_its_dependent_waits(tmp_path, monkeypatch, app):
    """An earlier apply run's completion retitled `stacks/app`'s check: the cell is held
    `attempted`, and `stacks/dns`, which runs after it, waits on it.

    Mutation: drop `attempted` from `holds` -- `stacks/app` applies, red.
    Mutation: match a prefix list (`Failed: `, `Cancelled`, `Not run`, `Not applied (`)
    instead of the record title -- `unlisted`, red."""
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        stacks=("stacks/app", "stacks/dns"),
        checks=[app, _titled("stacks/dns", cells=2, id=2)],
        deps=_DNS_AFTER_APP,
        tags=_DEV_EU_TAGS,
    )
    assert _applied(parsed) == []
    assert detect_report()["held_cells"] == [attempted_cell("stacks/app", "dev-eu")]
    assert detect_report()["waiting_cells"] == [
        waiting_cell("stacks/dns", "dev-eu", "stacks/app", "dev-eu")
    ]


def test_a_duplicate_auto_run_after_an_ended_one_applies_neither_attempted_cell(
    tmp_path, monkeypatch, capsys
):
    """No earlier run is live: the first run's completion wrote `stacks/app` `Failed: x` and
    its successor `stacks/dns` `Not run`. The duplicate holds both.

    Mutation: return `[]` from `attempted_cells` -- both apply, red.
    Mutation: drop `attempted=` from the apply-all-detect notice -- red."""
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        stacks=("stacks/app", "stacks/dns"),
        checks=[
            _titled(shown="Failed: x", cells=2, id=1),
            _titled("stacks/dns", shown="Not run", cells=2, id=2),
        ],
        deps=_DNS_AFTER_APP,
        tags=_DEV_EU_TAGS,
    )
    assert _applied(parsed) == []
    assert detect_report()["held_cells"] == [
        attempted_cell("stacks/app", "dev-eu"),
        attempted_cell("stacks/dns", "dev-eu"),
    ]
    assert detect_report()["waiting_cells"] == []
    assert (
        f"::notice title=apply-all-detect::head={HEAD} cells=2 pending=2 envlevels=[0, 0, 0, 0] "
        "excluded_targeted=[] skipped_after_targeted=[] review_held=[] waiting=0 locked=0 "
        "overlap=0 attempted=2 applied_exempt=[] review_decision='NONE'"
    ) in capsys.readouterr().out.splitlines()


def test_a_cell_an_ended_run_released_and_never_applied_is_held_and_its_dependent_waits(
    tmp_path, monkeypatch
):
    """Both completion writes failed: `stacks/app`'s newest check is still `in_progress` with
    its record title under an ended run that listed its `Apply the cell` step `skipped`.

    Mutation: drop the `in_progress` branch of `attempted_cells` -- `stacks/app` applies, red."""
    app, runs, _ = _holding("ended never applied", cells=2, id=1)
    parsed = _run_auto(
        tmp_path,
        monkeypatch,
        stacks=("stacks/app", "stacks/dns"),
        checks=[app, _titled("stacks/dns", cells=2, id=2)],
        runs=runs,
        deps=_DNS_AFTER_APP,
        tags=_DEV_EU_TAGS,
    )
    assert _applied(parsed) == []
    assert detect_report()["held_cells"] == [attempted_cell("stacks/app", "dev-eu")]
    assert detect_report()["waiting_cells"] == [
        waiting_cell("stacks/dns", "dev-eu", "stacks/app", "dev-eu")
    ]


@pytest.mark.parametrize(
    ("case", "held"),
    [
        ("live", []),
        ("unreadable", []),
        ("no run named", []),
        ("ended unsaved", []),
        ("ended saved", []),
        ("ended never applied", [{"stack": "stacks/app", "environment": "dev-eu"}]),
    ],
)
def test_only_an_ended_run_that_never_applied_the_cell_holds_it(monkeypatch, case, held):
    """`attempted_cells` over a newest check `in_progress` under run 77: only a run that ended
    without applying the cell (`deploy`) holds it; every other holder leaves it to
    `refuse_held`, which refuses the run with the same text either way, so the pin is here.

    Mutation: hold any `in_progress` newest check without testing for `deploy` -- every case
    but the last, red."""
    for k, v in {"GITHUB_REPOSITORY": "o/r", **RUN_ENV}.items():
        monkeypatch.setenv(k, v)
    check, runs, _ = _holding(case)
    monkeypatch.setattr(aad, "_run", _github([], (), "write", [], [], [], runs=runs))
    cell = {"stack": "stacks/app", "environment": "dev-eu"}
    assert aad.attempted_cells("o/r", [cell], [json.dumps(check)], APP_ID, {}) == held


def test_a_retitled_cell_the_base_also_changed_is_held_once_as_overlap(tmp_path, monkeypatch):
    """One cause per held cell: an overlap cell never reaches `attempted_cells`.

    Mutation: pass every candidate to `attempted_cells` -- the cell is listed twice, red."""
    parsed = _run_auto(
        tmp_path, monkeypatch, checks=[_titled(shown="Failed: x")], base_changed={"stacks/app"}
    )
    assert _applied(parsed) == []
    assert detect_report()["held_cells"] == [
        {"stack": "stacks/app", "environment": "dev-eu", "cause": "overlap"}
    ]


def test_the_attempted_hold_and_the_holder_refusal_decide_on_one_holder_read(tmp_path, monkeypatch):
    """Run 77's first read fails and a later one reads it ended without applying the cell: both
    `attempted_cells` and `refuse_held` take the first answer, so the run refuses as unreadable
    instead of applying a cell the hold never saw as `deploy`.

    Mutation: give `refuse_held` its own holder-run cache in `bare_main` (drop its `runs`) --
    the second read says `deploy` and the cell applies, red."""
    check, runs, _ = _holding("ended never applied")
    real, reads = aad._holding_run, []

    def flaky(repo, run_id):
        reads.append(run_id)
        return ("unreadable", None) if len(reads) == 1 else real(repo, run_id)

    monkeypatch.setattr(aad, "_holding_run", flaky)
    with pytest.raises(SystemExit) as exc:
        _run_auto(tmp_path, monkeypatch, checks=[check], runs=runs)
    assert str(exc.value) == HELD_CASES["unreadable"][3]
    assert reads == [HOLDER]
