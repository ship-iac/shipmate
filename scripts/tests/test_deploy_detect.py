import functools
import itertools
import json
import pathlib
import subprocess

import _shipmate
import pytest
from _detect_fixtures import (
    APP_ID,
    RUN_GRAPH_ARGV,
    _apply_check,
    answering_run_graph,
    behind_check,
    first_stage,
    forbid_shared_runner,
    partial_message,
    run_graph_capture,
    spy_env_config,
    stage_map,
    stub_read_table,
    wave_cells,
)
from _detect_fixtures import check_run as _check
from _loader import github_outputs, load_script, workflow_jobs

dd = load_script("deploy-detect")
ac = load_script("apply-complete")
pc = load_script("patch-checks")

HEAD = "a" * 40
CHECK_RUNS_URL = f"repos/acme/iac/commits/{HEAD}/check-runs?filter=all&per_page=100"
LOCKS_URL = "repos/acme/iac/git/matching-refs/shipmate/locks/dev-eu/"
PULLS_PATH = "repos/o/r/commits/merge123/pulls"
CLOSED_PATH = "repos/o/r/pulls?state=closed&base=main&sort=updated&direction=desc&per_page=100"
OWN_RUN = "900"
RUN_PAGE = "https://github.com/acme/iac/actions/runs/"
HOLDER = "77"
RUN_PATH = f"repos/acme/iac/actions/runs/{HOLDER}"
JOBS_PATH = f"{RUN_PATH}/jobs?filter=all&per_page=100&page=1"
WARNING = (
    "::warning::could not map merge123 to a merged pull request (commits/merge123/pulls stayed "
    "empty and no recently closed pull request records it as its merge commit); deploying it "
    "as a push to main"
)


def _answering(answers, paths=None):
    """A `_gh_json` double answering each path from `answers`, appending it to `paths`; an
    unexpected path raises KeyError."""

    def gh_json(path):
        if paths is not None:
            paths.append(path)
        return answers[path]

    return gh_json


def test_merged_head_exact_match_wins(monkeypatch):
    # An exact merge_commit_sha match is picked over everything else.
    pulls = [
        {
            "number": 6,
            "merge_commit_sha": "other",
            "merged_at": "2024-01-01T00:00:00Z",
            "head": {"sha": "wrong"},
        },
        {
            "number": 7,
            "merge_commit_sha": "merge123",
            "merged_at": "2024-01-02T00:00:00Z",
            "head": {"sha": "right"},
        },
    ]
    monkeypatch.setattr(dd, "_gh_json", lambda path: pulls)
    assert dd._merged_head("o/r", "merge123", "main") == ("right", "7")


def test_merged_head_only_open_pr_falls_back_to_merge_sha(monkeypatch, capsys):
    """The sole candidate is an open, unmerged pull request that merely contains the pushed
    commit. Its plans must not deploy, so the result is the merge SHA; the list was not empty,
    so no closed listing is read, and the warning names #6. Mutations: run the closed listing
    on any `(merge_sha, "")` outcome -- the listing is read, red; drop the warning -- red."""
    pulls = [
        {
            "number": 6,
            "merge_commit_sha": "unrelated",
            "merged_at": None,
            "head": {"sha": "open-pr-head"},
        },
    ]
    paths = []
    monkeypatch.setattr(dd, "_gh_json", _answering({PULLS_PATH: pulls}, paths))
    assert dd._merged_head("o/r", "merge123", "main") == ("merge123", "")
    assert paths == [PULLS_PATH]
    assert capsys.readouterr().err == (
        "::warning::could not map merge123 to the pull request that merged it: #6 contains it but "
        "none records it as its merge commit; deploying it as a push to main\n"
    )


def test_merged_head_one_merged_pull_with_another_merge_commit_is_a_push(monkeypatch, capsys):
    """A direct push of a commit an earlier merged pull request contains: #8 is merged, but its
    merge commit is another one, so its plans must not deploy. One read, no polling, no closed
    listing, the warning whole. Mutation: restore the `merged_at` fallback (`exact or next(p
    for p in pulls if p.get("merged_at"))`) -- `("merged-pr-head", "8")`, red."""
    pulls = [
        {
            "number": 8,
            "merge_commit_sha": "unrelated",
            "merged_at": "2024-01-01T00:00:00Z",
            "head": {"sha": "merged-pr-head"},
        },
    ]
    paths = []
    monkeypatch.setattr(dd, "_gh_json", _answering({PULLS_PATH: pulls}, paths))
    assert dd._merged_head("o/r", "merge123", "main") == ("merge123", "")
    assert paths == [PULLS_PATH]
    assert capsys.readouterr().err == (
        "::warning::could not map merge123 to the pull request that merged it: #8 contains it but "
        "none records it as its merge commit; deploying it as a push to main\n"
    )


def test_merged_head_mix_of_merged_and_open_with_no_merge_commit_is_a_push(monkeypatch, capsys):
    """No listed pull records `merge123` as its merge commit. GitHub sets `merge_commit_sha` to
    the commit the base branch moved to for a merge commit, a squash and a rebase alike, so a
    pull-request merge always matches and this is a direct push: the merged #8 is not picked,
    and the warning names both pulls in listed order. Mutations: restore the `merged_at`
    fallback -- `("merged-pr-head", "8")`, red; name only the first pull -- red."""
    pulls = [
        {
            "number": 6,
            "merge_commit_sha": "unrelated1",
            "merged_at": None,
            "head": {"sha": "open-pr-head"},
        },
        {
            "number": 8,
            "merge_commit_sha": "unrelated2",
            "merged_at": "2024-01-01T00:00:00Z",
            "head": {"sha": "merged-pr-head"},
        },
    ]
    monkeypatch.setattr(dd, "_gh_json", lambda path: pulls)
    assert dd._merged_head("o/r", "merge123", "main") == ("merge123", "")
    assert capsys.readouterr().err == (
        "::warning::could not map merge123 to the pull request that merged it: #6, #8 contain it "
        "but none records it as its merge commit; deploying it as a push to main\n"
    )


def test_merged_head_falls_back_to_the_closed_pull_that_merged_it(monkeypatch):
    """Every `commits/{sha}/pulls` read is empty; the closed listing holds the pull request
    whose merge commit this is, beside a later one. Its head and number come back as strings,
    the primary path's shape. Mutation: drop the fallback call -- `("merge123", "")`."""
    closed = [
        {
            "number": 9,
            "merge_commit_sha": "later",
            "merged_at": "2024-01-03T00:00:00Z",
            "head": {"sha": "wrong"},
        },
        {
            "number": 7,
            "merge_commit_sha": "merge123",
            "merged_at": "2024-01-02T00:00:00Z",
            "head": {"sha": "right"},
        },
    ]
    monkeypatch.setattr(dd, "_gh_json", _answering({PULLS_PATH: [], CLOSED_PATH: closed}))
    assert dd._merged_head("o/r", "merge123", "main", _sleep=0) == ("right", "7")


def test_merged_head_never_takes_a_closed_pull_with_another_merge_commit(monkeypatch):
    """Neither closed pull request records `merge123` as its merge commit; one is merged. A
    merged pull request is no evidence this commit is its merge. Mutation: match on
    `merged_at` alone -- `("merged-head", "8")`."""
    closed = [
        {
            "number": 8,
            "merge_commit_sha": "other",
            "merged_at": "2024-01-02T00:00:00Z",
            "head": {"sha": "merged-head"},
        },
        {"number": 6, "merge_commit_sha": "another", "merged_at": None, "head": {"sha": "x"}},
    ]
    monkeypatch.setattr(dd, "_gh_json", _answering({PULLS_PATH: [], CLOSED_PATH: closed}))
    assert dd._merged_head("o/r", "merge123", "main", _sleep=0) == ("merge123", "")


def test_merged_head_never_takes_a_closed_unmerged_pull_by_its_test_merge(monkeypatch, capsys):
    """GitHub fills `merge_commit_sha` with the test-merge commit on a closed unmerged pull
    request, so a SHA match without `merged_at` is no merge. Mutation: drop the `merged_at`
    condition -- `("unmerged-head", "6")` and no warning."""
    closed = [
        {
            "number": 6,
            "merge_commit_sha": "merge123",
            "merged_at": None,
            "head": {"sha": "unmerged-head"},
        }
    ]
    monkeypatch.setattr(dd, "_gh_json", _answering({PULLS_PATH: [], CLOSED_PATH: closed}))
    assert dd._merged_head("o/r", "merge123", "main", _sleep=0) == ("merge123", "")
    assert capsys.readouterr().err == WARNING + "\n"


def test_merged_head_warns_once_when_no_pull_request_records_the_merge(monkeypatch, capsys):
    """A direct push: no pull request at all. The deploy proceeds as a push, after one warning
    compared whole. Mutation: print the warning without its closed-listing clause -- red."""
    monkeypatch.setattr(dd, "_gh_json", _answering({PULLS_PATH: [], CLOSED_PATH: []}))
    assert dd._merged_head("o/r", "merge123", "main", _sleep=0) == ("merge123", "")
    assert capsys.readouterr().err == WARNING + "\n"


def test_merged_head_lists_the_closed_pulls_of_a_quoted_branch(monkeypatch, capsys):
    """The `base=` value is the branch URL-quoted with no safe characters; the warning names
    it as workflow-command data, so its `%` reads `%25`. Mutations: drop the `quote` --
    `base=release/50%`; drop `state=closed` -- the URL differs; drop `command_data` from the
    warning -- `release/50%`. Red each way."""
    paths = []
    closed = (
        "repos/o/r/pulls?state=closed&base=release%2F50%25&sort=updated&direction=desc&per_page=100"
    )
    monkeypatch.setattr(dd, "_gh_json", _answering({PULLS_PATH: [], closed: []}, paths))
    assert dd._merged_head("o/r", "merge123", "release/50%", _attempts=1) == ("merge123", "")
    assert paths == [PULLS_PATH, closed]
    assert capsys.readouterr().err == (
        "::warning::could not map merge123 to a merged pull request (commits/merge123/pulls "
        "stayed empty and no recently closed pull request records it as its merge commit); "
        "deploying it as a push to release/50%25\n"
    )


def test_merged_head_retries_a_transient_failure_of_the_closed_listing(monkeypatch):
    """One 502 on the closed listing is retried, not raised: a deploy with nothing pending
    must not fail on it. Mutation: read without `retried` in `gh_json` (`json.loads(fetch())`)
    -- the 502 raises."""
    closed = [
        {
            "number": 7,
            "merge_commit_sha": "merge123",
            "merged_at": "2024-01-02T00:00:00Z",
            "head": {"sha": "right"},
        }
    ]
    answers = iter(
        [SystemExit(f"::error::gh api {CLOSED_PATH}\nHTTP 502: Bad Gateway"), json.dumps(closed)]
    )
    calls = []

    def run(args):
        calls.append(args)
        if args == ["gh", "api", PULLS_PATH]:
            return "[]"
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(_shipmate, "_run", run)
    monkeypatch.setattr(_shipmate, "_sleep", lambda s: None)
    assert dd._merged_head("o/r", "merge123", "main", _attempts=1) == ("right", "7")
    assert calls == [["gh", "api", path] for path in (PULLS_PATH, CLOSED_PATH, CLOSED_PATH)]


def test_a_persistent_5xx_on_the_closed_listing_costs_three_reads(monkeypatch, capsys):
    """The listing retries inside `gh_json` only. Mutation: read it through
    `retried(lambda: _gh_json(path))` again -- nine listing calls."""
    calls = []
    sleeps = []

    def run(args):
        calls.append(args)
        if args == ["gh", "api", PULLS_PATH]:
            return "[]"
        raise SystemExit(f"::error::command failed (1): gh api {CLOSED_PATH}\nHTTP 502")

    monkeypatch.setattr(_shipmate, "_run", run)
    monkeypatch.setattr(_shipmate, "_sleep", sleeps.append)
    with pytest.raises(SystemExit) as exc:
        dd._merged_head("o/r", "merge123", "main", _attempts=1)
    notice = f"::notice::command failed (1): gh api {CLOSED_PATH}%0AHTTP 502\n"
    assert (exc.value.code, calls, sleeps, capsys.readouterr().err) == (
        f"::error::command failed (1): gh api {CLOSED_PATH}\nHTTP 502",
        [["gh", "api", PULLS_PATH]] + [["gh", "api", CLOSED_PATH]] * 3,
        [2, 5],
        notice * 2,
    )


def test_merged_head_retries_until_pulls_populated(monkeypatch):
    # commits/{sha}/pulls can transiently return [] for a few seconds after the push, from
    # association indexing lag, so it must retry rather than give up on the first empty
    # response.
    responses = [
        [],
        [],
        [
            {
                "number": 7,
                "merge_commit_sha": "merge123",
                "merged_at": "2024-01-01T00:00:00Z",
                "head": {"sha": "right"},
            }
        ],
    ]
    calls = {"n": 0}

    def fake_gh_json(path):
        result = responses[calls["n"]]
        calls["n"] += 1
        return result

    slept = []
    monkeypatch.setattr(dd, "_gh_json", fake_gh_json)
    monkeypatch.setattr(dd.time, "sleep", lambda s: slept.append(s))
    assert dd._merged_head("o/r", "merge123", "main", _attempts=5, _sleep=2) == ("right", "7")
    assert calls["n"] == 3
    assert slept == [2, 2]  # It slept before the 2nd and 3rd attempts only.


def test_merged_head_gives_up_after_attempts_exhausted_all_empty(monkeypatch):
    # If every attempt returns [] the retry loop must stop at _attempts, read the closed
    # listing once, and fall back to the merge SHA, not loop forever or raise.
    paths = []
    monkeypatch.setattr(dd, "_gh_json", _answering({PULLS_PATH: [], CLOSED_PATH: []}, paths))
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)
    assert dd._merged_head("o/r", "merge123", "main", _attempts=3, _sleep=0) == ("merge123", "")
    assert paths == [PULLS_PATH, PULLS_PATH, PULLS_PATH, CLOSED_PATH]


def _cell(stack, env="dev-eu"):
    # The whole row `full_tree` emits, `workload` included: a double that omits a key the
    # real builder always adds cannot fail on a guard that pins the row shape, and the table
    # path keys its workload tier on that key.
    return {"stack": stack, "environment": env, "workload": ""}


def _lock_answer(locks):
    """`_run_main`'s answer to a lock-ref or lock-commit path under its `locks` keyword, None
    for any other path."""
    shas = {f"{i:040x}": key for i, key in enumerate(locks, 1)} if isinstance(locks, dict) else {}

    def answer(path):
        if not path.startswith(
            ("repos/acme/iac/git/matching-refs/", "repos/acme/iac/git/commits/")
        ):
            return None
        if isinstance(locks, BaseException):
            raise locks
        if path.startswith("repos/acme/iac/git/commits/"):
            stack, env = shas[path.rsplit("/", 1)[1]]
            holder = locks[(stack, env)]
            record = {"pr": holder, "env": env, "stack": stack, "acquisition": "1.1"}
            message = json.dumps(record) if holder is not None else "not a lock record"
            return json.dumps({"committer": {"date": "2026-10-24T00:00:00Z"}, "message": message})
        env = path.removeprefix("repos/acme/iac/git/matching-refs/shipmate/locks/").rstrip("/")
        return json.dumps(
            [
                {"ref": dd.ad.cl.ref_name(e, s), "object": {"type": "commit", "sha": sha}}
                for sha, (s, e) in shas.items()
                if e == env
            ]
        )

    return answer


def _stub_merged_head(monkeypatch, pull_reads, answer, run):
    """`_run_main`'s `_merged_head`: a double returning `answer`, or with `pull_reads` the real
    one reading through `run` without sleeping. Apart from `_run_main` to keep it under C901."""
    if pull_reads is None:
        monkeypatch.setattr(dd, "_merged_head", lambda repo, merge_sha, branch: answer)
    else:
        monkeypatch.setattr(dd, "_gh_json", functools.partial(dd._gh_json, run=run))
        monkeypatch.setattr(dd.time, "sleep", lambda s: None)


def _in_flight(stack, env="dev-eu", url=RUN_PAGE + HOLDER, **kw):
    """An App apply check a release flipped to `in_progress`, naming run page `url`."""
    return {**_apply_check(stack, env, **kw), "status": "in_progress", "details_url": url}


def _holder(status, jobs=None, total=None, attempt=1, again=None):
    """`_run_main`'s `runs`: run `HOLDER` read with `status` on `attempt`, and `jobs` as its
    one-page jobs listing advertising `total` jobs (default: as many as it holds). `again`, a run
    object or an exception, answers the second run read (default: the first answer again)."""
    first = json.dumps({"id": int(HOLDER), "status": status, "run_attempt": attempt})
    second = first if again is None else again
    if isinstance(second, dict):
        second = json.dumps({"id": int(HOLDER), **second})
    answers = {RUN_PATH: [first, second]}
    if jobs is not None:
        count = len(jobs) if total is None else total
        answers[JOBS_PATH] = json.dumps({"total_count": count, "jobs": jobs})
    return answers


def _answer(runs, path):
    """`_run_main`'s answer to `path` from `runs`: its text, or its exception raised. A list
    answers each read with its next item, popped from that list, and every later read with its
    last; `_run_main` passes its own copy of each list, so a parametrized case keeps its data."""
    held = (runs or {}).get(path)
    if isinstance(held, list):
        held = held.pop(0) if len(held) > 1 else held[0]
    if isinstance(held, BaseException):
        raise held
    return held


def _run_main(
    tmp_path,
    monkeypatch,
    *,
    cells,
    checks,
    urls=None,
    deps=None,
    order=None,
    table=None,
    reads=None,
    stacks=None,
    head=HEAD,
    pr_number="7",
    tag_reads=None,
    dot=None,
    locks=None,
    pull_reads=None,
    runs=None,
    attempt="1",
):
    """main() over the merged pull request's head, with every GitHub and Terramate call
    stubbed. Without `pull_reads`, `_merged_head` is stubbed rather than fed, so the only `gh api`
    paths collected into `urls` are the ones the work set itself asks for; `head` and `pr_number`
    are what the stub returns.

    `stacks`, a `{stack: [tags]}` map, runs the real `compute_cells` over that tree instead of
    the double, and leaves `cells` unused. Without it, `all_tags` tags each stack of `cells`
    with the env of each of its cells. Each `all_tags` call appends the map it answers to
    `tag_reads` when one is given.

    `order` is folded into the stubbed table rather than stubbed on `wv`: the ordering map is
    a field of the mapping this path loads, so a double on the reader would mask a caller
    that stopped passing the table. One entry is appended to `reads` per `read_table` call.

    `dot`, run-graph text, keeps `run_graph_deps` real and answers its Terramate call; `deps`
    otherwise replaces the run-graph.

    `locks`, a `{(stack, env): holder}` map, answers the lock refs the real `cell-lock` reads
    (none by default); a `None` holder's commit carries no lock record, as an unknown holder's
    does. An exception for `locks` is raised by every lock-ref read instead.

    `pull_reads`, a `{path: JSON text}` map, leaves `_merged_head` real on `GITHUB_SHA` =
    `head` and branch `main`, and answers its reads by path; those reads land in `urls` too, and
    `pr_number` is unused.

    `runs`, a `{path: JSON text or exception}` map, answers the holding-run reads by path; an
    exception is raised by every read of its path.

    `attempt` is this run's `GITHUB_RUN_ATTEMPT`, set here because CI runs this suite inside a
    workflow run whose own attempt is in the environment.

    Returns the parsed GITHUB_OUTPUT."""
    out = tmp_path / "out.txt"
    for k, v in {
        "GITHUB_REPOSITORY": "acme/iac",
        "GITHUB_SHA": "merge123" if pull_reads is None else head,
        "GITHUB_REF_NAME": "main",
        "GITHUB_OUTPUT": str(out),
        "SHIPMATE_APP_ID": APP_ID,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": OWN_RUN,
        "GITHUB_RUN_ATTEMPT": attempt,
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SHIPMATE_BASE_SHA", raising=False)
    stub_read_table(monkeypatch, (dd.bm.ec,), table, order, reads=reads)
    jsonl = "\n".join(json.dumps(c) for c in checks)
    # A copy per call, so `_answer` never consumes a parametrized case's own lists.
    runs = {k: list(v) if isinstance(v, list) else v for k, v in (runs or {}).items()}
    lock_answer = _lock_answer(locks)

    def _run(args):
        # Every terramate call must be stubbed, because CI installs uv alone: a real
        # invocation passes on a developer machine and fails there.
        assert args[0] == "gh", args
        path = next(a for a in args if a.startswith("repos/"))
        if urls is not None:
            urls.append(path)
        return _answer(runs, path) or (pull_reads or {}).get(path) or lock_answer(path) or jsonl

    _stub_merged_head(monkeypatch, pull_reads, (head, pr_number), _run)
    # `compute_cells` returns (env->workloads map, rows, tree names, unmanaged stacks, reported
    # unmanaged stacks); a double returning fewer unpacks into the wrong names and fails somewhere
    # unrelated.
    if stacks is None:
        monkeypatch.setattr(
            dd.bm,
            "compute_cells",
            lambda all_stacks=False, base="", tags="", tag_map=None: (
                {c["environment"]: frozenset({c["workload"]} - {""}) for c in cells},
                cells,
                [{"environment": c["environment"], "stack": c["stack"]} for c in cells],
                [],
                [],
            ),
        )
        tags = {}
        for c in cells:
            tags.setdefault(c["stack"], []).append(f"env/{c['environment']}")
    else:
        monkeypatch.setattr(dd.bm, "_list_stacks", lambda all_stacks, base: list(stacks))
        tags = stacks
    reads = [] if tag_reads is None else tag_reads

    def all_tags():
        reads.append(tags)
        return tags

    monkeypatch.setattr(dd.bm, "all_tags", all_tags)
    # The check-run listing is fetched through apply-detect's own `_run`, and the tree walk
    # through deploy-detect's build-matrix. Both are stubbed so a `gh api` call from either
    # module lands in `urls`.
    runner = _run
    if dot is None:
        stubbed = deps or {c["stack"]: set() for c in cells}
        monkeypatch.setattr(dd.bm, "run_graph_deps", lambda: stubbed)
    else:
        runner = answering_run_graph(_run, dot)
    monkeypatch.setattr(dd.ad, "_run", runner)
    monkeypatch.setattr(dd.bm, "_run", runner)
    forbid_shared_runner(monkeypatch)
    dd.main()
    return github_outputs(out)


def test_main_reads_the_head_listing_once_and_makes_no_plan_run_lookup(tmp_path, monkeypatch):
    """The post-merge path resolves no plan run from the plan workflow's runs, and it reads the
    head's check-run listing exactly once: two reads of one listing disagree when a re-plan
    lands between them, re-opening a name the first read saw completed while the cell still
    carries the first read's record. Whole-list comparison against a hand-written constant, so
    a reinstated `actions/workflows/plan.yml/runs` lookup and a second fetch of the listing
    each redden this one assertion."""
    urls = []
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app")],
        checks=[_apply_check("stacks/app", cells=1)],
        urls=urls,
    )
    assert len(wave_cells(parsed)) == 1  # Not vacuous: a cell exists.
    assert urls == [CHECK_RUNS_URL, LOCKS_URL]


def test_main_writes_an_empty_pr_number_for_a_push_with_no_pull_request(tmp_path, monkeypatch):
    """An empty `pr_number` skips the summary's lock release. Mutation: write `pr_number` as
    `pr_number or head` -- the head SHA is written instead."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app")],
        checks=[_apply_check("stacks/app", cells=1)],
        pr_number="",
    )
    assert (parsed["head_sha"], parsed["pr_number"]) == (HEAD, "")


def test_main_refuses_a_malformed_merged_head_before_any_api_read(tmp_path, monkeypatch):
    """The merged head is interpolated into the check-run listing's URL, so a malformed one
    is refused before any `gh api` path is requested.

    Mutation: delete the `validate_head_sha` call -- `check_runs` refuses instead, with its
    `head SHA` message. Mutation: move the call after `_check_run_lines` -- the same."""
    urls = []
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app")],
            checks=[_apply_check("stacks/app", cells=1)],
            urls=urls,
            head="1/../../x",
        )
    assert str(exc_info.value) == (
        "::error::the deploy's head SHA (the merged pull request's head from "
        "commits/merge123/pulls or the closed pull requests of main, else GITHUB_SHA) must be "
        "a 40-char lowercase hex SHA (got: '1/../../x')"
    )
    assert urls == []


def test_main_gives_each_cell_the_plan_run_its_own_check_names(tmp_path, monkeypatch):
    # The recovery shape: dev-us was re-planned by a later run while dev-eu is still named by
    # the first. Each applies from the run that planned it, so one shared id -- all a
    # plan-workflow-runs lookup can give -- is not enough.
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="111", cells=1),
            _apply_check("stacks/app", "dev-us", plan_run="222", cells=1),
        ],
    )
    assert sorted((c["environment"], c["plan_run_id"]) for c in wave_cells(parsed)) == [
        ("dev-eu", "111"),
        ("dev-us", "222"),
    ]


def test_main_refuses_a_cell_whose_check_records_no_plan_text_digest(tmp_path, monkeypatch):
    """Through this detect's own entry point, for the same reason as the missing-run refusal:
    nobody is watching a post-merge run, so a call site that stopped passing the digests would
    apply every cell unverified with nothing said. The cell HAS a usable plan run, so the
    missing-run refusal cannot absorb this one."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
            checks=[
                _apply_check("stacks/app", "dev-eu", plan_run="42", cells=1),
                _apply_check("stacks/app", "dev-us", plan_run="43", plan_sha256=None, cells=1),
            ],
        )
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan-text digest recorded for apply / dev-us / stacks/app: "
        "the reviewed plan text cannot be checked against the plan that would be applied, so "
        "this apply is refused rather than run unverified. Re-plan these stacks on their pull "
        "request, then apply again; if that pull request has already merged, a new pull "
        "request touching them plans and applies them afresh."
    )


def test_main_refuses_a_run_that_declares_more_cells_than_it_published(tmp_path, monkeypatch):
    """Each diff cell has its check, so the missing-run refusal stays silent; run 42 declares a
    third cell this deploy cannot see, and the remedy is a new pull request, never a re-run of
    the merged pull request's plan run. Mutations: delete the `refuse_incomplete` call in
    `with_plan_runs` -- both cells deploy, red; `merged=pr_number is not None` -- the re-run
    remedy, red."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app"), _cell("stacks/dns")],
            checks=[
                _apply_check("stacks/app", plan_run="42", cells=3),
                _apply_check("stacks/dns", plan_run="42", cells=3, id=2),
            ],
        )
    assert str(exc_info.value) == partial_message(
        "plan run 42 published 2 apply checks for 3 planned cells, holding back "
        "apply / dev-eu / stacks/app, apply / dev-eu / stacks/dns",
        rerun=True,
        merged=True,
    )


def test_main_refuses_a_cell_whose_check_records_no_plan_run(tmp_path, monkeypatch):
    # The loud refusal this path most needs: nobody is watching a post-merge run, so a default
    # would apply a cell from nowhere and a skip would leave the check pending with nothing
    # said. The message names the cell to re-plan.
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
            checks=[
                _apply_check("stacks/app", "dev-eu", plan_run="42", cells=1),
                _check(
                    name="apply / dev-us / stacks/app",
                    status="queued",
                    conclusion=None,
                    external_id="b" * 64,
                ),
            ],
        )
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan run recorded for apply / dev-us / stacks/app: the "
        "apply check names no plan run to apply from (post-merge, the cell may have no apply "
        "check at all), so the apply is refused. Re-plan these stacks on their pull request, "
        "then apply again; if that pull request has already merged, a new pull request "
        "touching them plans and applies them afresh."
    )


def test_main_warns_of_an_unmapped_merge_before_refusing_its_record_less_cell(
    tmp_path, monkeypatch, capsys
):
    """No pull request maps the merge, so it deploys as a push: through `run_annotated`, the
    one stream the runner's log interleaves, stderr holds the warning and then the push path's
    refusal of its pending cell, whose check records no plan run, compared whole.
    Mutation: write the warning to stdout -- stderr holds the refusal alone.
    Mutation: print the warning from `main` after `with_plan_runs` -- the same.
    Mutation: make the lock read fail -- its failure is raised instead of the refusal."""
    closed = "repos/acme/iac/pulls?state=closed&base=main&sort=updated&direction=desc&per_page=100"
    main = dd.main
    monkeypatch.setattr(dd, "main", lambda: dd.run_annotated(main))
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app")],
            checks=[
                _check(
                    name="apply / dev-eu / stacks/app",
                    status="queued",
                    conclusion=None,
                    external_id="b" * 64,
                )
            ],
            pull_reads={f"repos/acme/iac/commits/{HEAD}/pulls": "[]", closed: "[]"},
        )
    assert exc_info.value.code == 1
    assert capsys.readouterr().err == (
        f"::warning::could not map {HEAD} to a merged pull request (commits/{HEAD}/pulls stayed "
        "empty and no recently closed pull request records it as its merge commit); deploying "
        "it as a push to main\n"
        "::error::apply aborted: no plan run recorded for apply / dev-eu / stacks/app: the "
        "apply check names no plan run to apply from (post-merge, the cell may have no apply "
        "check at all), so the apply is refused. Re-plan these stacks on their pull request, "
        "then apply again; if that pull request has already merged, a new pull request "
        "touching them plans and applies them afresh.\n"
    )


def test_main_lets_a_record_less_completed_check_through(tmp_path, monkeypatch):
    """dev-us was applied pre-merge and its completed check carries a bare-hex record. The
    attachment runs after the pending filter, so an applied cell never refuses the deploy."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="42", cells=1),
            _check(name="apply / dev-us / stacks/app", external_id="b" * 64),
        ],
    )
    assert [c["environment"] for c in wave_cells(parsed)] == ["dev-eu"]


def test_main_refuses_a_cell_whose_only_check_is_named_stack_first(tmp_path, monkeypatch):
    """A completed `apply / stacks/app / dev` with a valid record is not the cell's check, so the
    cell stays pending and refuses for want of a record. Mutation: `filter_pending` also treats
    `apply / <stack> / <env>` as done -- the deploy applies nothing and does not refuse."""
    old = {
        **_apply_check("stacks/app", "dev", plan_run="42", cells=1),
        "name": "apply / stacks/app / dev",
        "status": "completed",
        "conclusion": "success",
    }
    with pytest.raises(SystemExit) as exc_info:
        _run_main(tmp_path, monkeypatch, cells=[_cell("stacks/app", "dev")], checks=[old])
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan run recorded for apply / dev / stacks/app: the "
        "apply check names no plan run to apply from (post-merge, the cell may have no apply "
        "check at all), so the apply is refused. Re-plan these stacks on their pull request, "
        "then apply again; if that pull request has already merged, a new pull request "
        "touching them plans and applies them afresh."
    )


def test_a_transposed_one_segment_cell_reads_the_stack_first_check_as_its_own(
    tmp_path, monkeypatch
):
    """Pins a stated limit so that any change to it is seen: `apply / dns / dev-us`, written
    stack first for stack `dns` in `dev-us`, is also the env-first name of stack `dev-us` in
    env `dns`, so that cell reads as applied and the deploy neither applies nor refuses it.
    Records carry no cell identity, so nothing tells the two apart. Mutation: `filter_pending`
    keeps the `dev-us` / `dns` cell pending -- the whole output changes."""
    done = {
        **_apply_check("dev-us", "dns", plan_run="42", cells=1),
        "status": "completed",
        "conclusion": "success",
    }
    parsed = _run_main(tmp_path, monkeypatch, cells=[_cell("dev-us", "dns")], checks=[done])
    assert parsed == {
        "head_sha": HEAD,
        "pr_number": "7",
        "push": '{"stacks": ["dev-us"], "outside": true}',
        "levels": "[]",
        "stages": "[]",
        "keys": "[]",
        "counts": "[]",
        "cells": "[]",
        "empty": "true",
        "warm": '[{"stack": "", "tf_vars": {}}]',
        "max_parallel": "10",
    }


def test_main_queues_an_in_progress_check_as_pending(tmp_path, monkeypatch):
    """A pre-merge release flips a check to `in_progress`; a run that ended before applying its
    cell leaves it there, and the merge deploy owns it. Mutation: `ag.done_names` counts any
    non-`queued` status as done -> dev-us is dropped."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="42", cells=2),
            _in_flight("stacks/app", "dev-us", plan_run="42", cells=2, id=2),
        ],
        runs=_holder(
            "completed",
            [
                _job(
                    name="deploy / waves / L0-W0 / dev-us / stacks/app / apply",
                    steps=[],
                    conclusion="cancelled",
                )
            ],
        ),
    )
    assert [c["environment"] for c in wave_cells(parsed)] == ["dev-eu", "dev-us"]


def test_main_notice_reports_the_no_op_evidence(tmp_path, monkeypatch, capsys):
    # A no-op post-merge deploy is indistinguishable from a broken work set without these
    # counts, and acceptance reads them off this one line. Whole line against a hand-written
    # constant, so a dropped field reddens here.
    _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/dns", "dev-eu")],
        checks=[
            _apply_check("stacks/app", "dev-eu", plan_run="42", cells=1),
            _check(name="apply / dev-eu / stacks/dns"),
        ],
    )
    assert (
        f"::notice title=deploy-detect::base= head={HEAD} cells=2 completed=1 pending=1 "
        "stages=[1] empty=False" in capsys.readouterr().out.splitlines()
    )


def test_a_forged_completed_check_does_not_mark_a_cell_applied(tmp_path, monkeypatch):
    """A completed+success check of the same name from another identity (github-actions, app id
    15368) must not count the cell as applied. It is also the newer run of that name, so only
    the App filter keeps the cell in, and main() is what feeds that filter its app id -- so
    this is the behavioural pin on the threading test_detect_app_scoping only sees
    structurally."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app")],
        checks=[
            _apply_check("stacks/app", cells=1),
            _check(name="apply / dev-eu / stacks/app", id=2, app={"id": 15368}),
        ],
    )
    assert [c["stack"] for c in wave_cells(parsed)] == ["stacks/app"]


def test_main_emits_the_dag_shape_notice(tmp_path, monkeypatch, capsys):
    # The post-merge path is where a flat DAG applies the whole repository at once, so this is
    # the run that most needs the line. deploy-detect has its own main(), and importing
    # apply-detect emits nothing on its own.
    _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/a")],
        checks=[_apply_check("stacks/a", cells=1)],
        deps={"stacks/a": set(), "stacks/b": {"stacks/a"}},
    )
    assert (
        "::notice::2 stacks, 1 after edges, 2 dependency levels; 1 stacks would apply concurrently"
        in capsys.readouterr().out.splitlines()
    )


def test_main_prints_the_dag_shape_notice_before_a_stage_count_refusal(
    tmp_path, monkeypatch, capsys
):
    """The notice is the line that explains a run of more stages than a matrix holds, so it
    prints before `stage_outputs` refuses.

    Mutation: print the DAG-shape notice after `stage_outputs` -- the notice is missing.
    """
    stacks = [f"stacks/s{i}" for i in range(257)]
    with pytest.raises(SystemExit, match="spans 257 stages"):
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell(s) for s in stacks],
            checks=[_apply_check(s, cells=257) for s in stacks],
            deps={s: ({stacks[i - 1]} if i else set()) for i, s in enumerate(stacks)},
        )
    assert capsys.readouterr().out.splitlines() == [
        (
            "::notice::257 stacks, 256 after edges, 257 dependency levels; "
            "1 stacks would apply concurrently"
        )
    ]


#: Two cells whose stage outputs total about 468,000 characters: under the 500,000 cap alone.
_LONG_STACKS = [f"stacks/{c * 117_000}" for c in "ab"]

#: A `push` of 60,000 characters, inside `PUSH_CAP`, written before the stage outputs.
_LONG_PUSH = '{"stacks": ["' + "p" * 59_960 + '"], "outside": false}'


def _long_deploy(tmp_path, monkeypatch):
    return _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell(s, "dev") for s in _LONG_STACKS],
        checks=[_apply_check(s, "dev", cells=2) for s in _LONG_STACKS],
    )


def test_main_writes_stage_outputs_under_the_output_cap(tmp_path, monkeypatch):
    """The no-refusal side of the over-cap test below. Mutation: `OUTPUT_CAP = 400_000` --
    refuses, red."""
    parsed = _long_deploy(tmp_path, monkeypatch)
    assert json.loads(parsed["counts"]) == [2]


def test_main_refuses_when_the_earlier_push_carries_the_outputs_over_the_cap(tmp_path, monkeypatch):
    """The stage outputs and `warm` fit alone; with the `push` written earlier in the step they
    cross the cap. The early writes stay, so a refused deploy's summary still has its head.

    Mutations: count only the current call's outputs in `write_outputs` -- no refusal, red;
    drop the cap check -- no refusal, red."""
    monkeypatch.setattr(dd, "push_stacks", lambda *_: _LONG_PUSH)
    with pytest.raises(SystemExit) as exc:
        _long_deploy(tmp_path, monkeypatch)
    assert str(exc.value) == (
        "::error::the outputs for 2 cells total 528949 characters (> 500000), past what a "
        "job's outputs carry; split the change."
    )
    assert github_outputs(tmp_path / "out.txt") == {
        "head_sha": HEAD,
        "pr_number": "7",
        "push": _LONG_PUSH,
    }


def test_main_takes_the_ordering_map_from_the_loaded_table(tmp_path, monkeypatch):
    """The merge path orders its env-levels from the mapping it already loaded, not from the
    checked-out tree. The assertion is on a populated split, because the broken shape returns
    an empty ordering rather than raising.

    Mutation: replace `main`'s `bm.ec.env_order(table)` with `table.get("env_order", {})`
    -- dev-us joins dev-eu in `L0-W0`.
    """
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
        checks=[
            _apply_check("stacks/app", "dev-eu", cells=2),
            _apply_check("stacks/app", "dev-us", cells=2),
        ],
        order={"dev-us": ["dev-eu"]},
    )
    assert {k: [c["environment"] for c in v] for k, v in stage_map(parsed).items()} == {
        "L0-W0": ["dev-eu"],
        "L1-W0": ["dev-us"],
    }


def test_main_loads_the_environment_table_exactly_once(tmp_path, monkeypatch):
    """One parse per operation: the cells' identity and the ordering map come off the same
    read of the default branch, so a branch moving mid-run cannot give them different answers.

    Mutation: add `bm.ec.read_table()` beside `main`'s `bm.ec.env_order` call -- the count
    becomes 2.
    """
    reads = []
    _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app", "dev-eu"), _cell("stacks/app", "dev-us")],
        checks=[
            _apply_check("stacks/app", "dev-eu", cells=2),
            _apply_check("stacks/app", "dev-us", cells=2),
        ],
        order={"dev-us": ["dev-eu"]},
        reads=reads,
    )
    assert len(reads) == 1


def test_main_unpacks_the_real_compute_cells_return(tmp_path, monkeypatch):
    """Every other test here doubles `compute_cells`; this one runs the real one, so a change to
    its return shape cannot pass behind a double that changed with it.

    Mutation: `compute_cells` returns `(tagged, cells)` -- main's unpack raises `ValueError`."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[],
        checks=[_apply_check("stacks/app", cells=1)],
        stacks={"stacks/app": ["env/dev-eu"]},
        deps={"stacks/app": set()},
    )
    assert [(c["environment"], c["stack"]) for c in wave_cells(parsed)] == [
        ("dev-eu", "stacks/app")
    ]


def test_a_deploy_reads_the_tag_metadata_once(tmp_path, monkeypatch):
    """The real `compute_cells` scans the merge, and the inversion check reads the same map.

    Mutation: call `compute_cells` in `main` without `tag_map` -- two reads."""
    reads = []
    _run_main(
        tmp_path,
        monkeypatch,
        cells=[],
        checks=[_apply_check("stacks/app", cells=1)],
        stacks={"stacks/app": ["env/dev-eu"]},
        deps={"stacks/app": set()},
        tag_reads=reads,
    )
    assert reads == [{"stacks/app": ["env/dev-eu"]}]


def test_deploy_passes_no_workload_map(tmp_path, monkeypatch):
    """The merge's changed stacks are no whole-tree scan. Mutation: keep `compute_cells`'
    workload map and pass it to `env_config` -- the spy records `{"dev-eu": frozenset()}`."""
    seen = spy_env_config(monkeypatch, dd.bm)
    _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app")],
        checks=[_apply_check("stacks/app", cells=1)],
    )
    assert seen == [None]


def test_main_refuses_a_cyclic_needs_before_sorting_env_levels(tmp_path, monkeypatch):
    """`env_levels` sorts with `levels` directly, so the only cycle refusal on this path is
    `validate`'s. Mutation: return the table unvalidated from `build-matrix`'s `env_config`
    -- `env_levels` raises a raw `CycleError`, not this `SystemExit`.
    """
    with pytest.raises(SystemExit) as exc:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app", "a"), _cell("stacks/app", "b")],
            checks=[
                _apply_check("stacks/app", "a", cells=2),
                _apply_check("stacks/app", "b", cells=2),
            ],
            order={"a": ["b"], "b": ["a"]},
        )
    assert str(exc.value) == (
        "::error::needs is cyclic: a -> b -> a: each of those must fully apply before the "
        "next, so the ordering has no first environment and no apply path can sort it. Break "
        "the chain in .github/shipmate-config.yml."
    )


def test_the_earliest_refusal_still_writes_the_head_sha(tmp_path, monkeypatch):
    """The summary writes the gate's failure on the head, and releases the pull request's locks,
    only if detect named them. Mutation: move the `head_sha` write to just before
    `refuse_stranded_checks` -- the file holds only `push`. Mutation: drop `pr_number` from
    it."""
    with pytest.raises(SystemExit) as exc:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app", "a"), _cell("stacks/app", "b")],
            checks=[
                _apply_check("stacks/app", "a", cells=2),
                _apply_check("stacks/app", "b", cells=2),
            ],
            order={"a": ["b"], "b": ["a"]},
        )
    assert str(exc.value).startswith("::error::needs is cyclic")
    assert github_outputs(tmp_path / "out.txt") == {
        "head_sha": HEAD,
        "pr_number": "7",
        "push": '{"stacks": ["stacks/app"], "outside": true}',
    }


#: The refusal's fixed remedy, hand-written.
_REMEDY = (
    "Open a new pull request that retags each stranded stack and changes every other listed "
    "stack (any edit that `terramate list --changed` marks, such as a comment line). It plans "
    "the current default branch for all of them, this merged change included, and its cells "
    "are applied before merge under a required gate (`shipmate apply`, and `shipmate apply "
    "<env>` for a `targeted` environment), or by its deploy otherwise. If an untag was "
    "deliberate, apply that stranded change by hand instead of "
    "retagging; the pull request still changes every listed stack, and with none listed no "
    "pull request is needed. Re-running this deploy checks out the same commit and refuses "
    "again. This deploy's apply checks are never read again and stay open on the merged pull "
    "request."
)


def test_main_refuses_a_pending_apply_check_on_an_unmanaged_stack(tmp_path, monkeypatch):
    """A pull request changed `stacks/gone`, which lost its `env/*` tags on the default branch
    before this merge: the scan builds it no cell, so its pending reviewed apply check would
    stay pending with nothing applied. The completed check on the same stack is not named, and
    the managed stack's pending check is not either.

    Mutation: drop the `refuse_stranded_checks` call from `main` -- the deploy writes waves.
    Mutation: drop the not-applied-either sentence -- the `stacks/app` cell goes unnamed."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[],
            checks=[
                _apply_check("stacks/app", cells=3),
                _apply_check("stacks/gone", "dev-eu", cells=3),
                _apply_check("stacks/gone", "dev-us", cells=3),
                _check(name="apply / prod / stacks/gone"),
            ],
            stacks={"stacks/app": ["env/dev-eu"], "stacks/gone": []},
            deps={"stacks/app": set(), "stacks/gone": set()},
        )
    assert str(exc_info.value) == (
        "::error::deploy aborted: apply / dev-eu / stacks/gone, apply / dev-us / stacks/gone: "
        "the stack of each lost its env/* tag after this pull request was planned, so its "
        "reviewed change was not applied. This deploy applies nothing, so these other cells of "
        "this merge are not applied either: stacks/app / dev-eu. " + _REMEDY
    )


def test_the_refusal_names_a_pending_cell_that_has_no_apply_check(tmp_path, monkeypatch):
    """`stacks/new` is a pending cell of this merge with no apply check of its own. It is still
    named, so the remedy pull request changes its stack and its merged change applies.

    Mutation: keep only the `pending` cells whose check name is in `open_names` -- the
    sentence drops `stacks/new / dev-eu`."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[],
            checks=[_apply_check("stacks/app", cells=2), _apply_check("stacks/gone", cells=2)],
            stacks={"stacks/app": ["env/dev-eu"], "stacks/new": ["env/dev-eu"], "stacks/gone": []},
            deps={"stacks/app": set(), "stacks/new": set(), "stacks/gone": set()},
        )
    assert str(exc_info.value) == (
        "::error::deploy aborted: apply / dev-eu / stacks/gone: the stack of each lost its "
        "env/* tag after this pull request was planned, so its reviewed change was not "
        "applied. This deploy applies nothing, so these other cells of this merge are not "
        "applied either: stacks/app / dev-eu, stacks/new / dev-eu. " + _REMEDY
    )


def test_a_refusal_with_no_other_cell_names_none(tmp_path, monkeypatch):
    """Mutation: render the not-applied-either sentence with an empty list -- the message
    gains `either: .`."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[],
            checks=[_apply_check("stacks/gone", cells=1)],
            stacks={"stacks/gone": []},
            deps={"stacks/gone": set()},
        )
    assert str(exc_info.value) == (
        "::error::deploy aborted: apply / dev-eu / stacks/gone: the stack of each lost its "
        "env/* tag after this pull request was planned, so its reviewed change was not "
        "applied. " + _REMEDY
    )


_PARTIAL_UNTAG_MESSAGE = (
    "::error::deploy aborted: apply / prod / stacks/app: the stack of each lost its env/* tag "
    "after this pull request was planned, so its reviewed change was not applied. This deploy "
    "applies nothing, so these other cells of this merge are not applied either: "
    "stacks/app / dev-eu. " + _REMEDY
)


def test_main_refuses_a_pending_apply_check_on_a_stack_moved_to_local(tmp_path, monkeypatch):
    """A stack retagged from `env/dev-eu` to `shipmate/local` after planning strands its check
    like any untagged stack, and `push` written before the refusal names it. Mutations: pass
    `compute_cells`' fifth element, `reported_unmanaged`, to `refuse_stranded_checks` in `main`
    -- the deploy writes waves; drop `scanned` from `push` -- `stacks` is empty; write `push`
    after `refuse_behind_checks` -- it is missing."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[],
            checks=[_apply_check("stacks/gone", cells=1)],
            stacks={"stacks/gone": ["shipmate/local"]},
            deps={"stacks/gone": set()},
        )
    assert str(exc_info.value) == (
        "::error::deploy aborted: apply / dev-eu / stacks/gone: the stack of each lost its "
        "env/* tag after this pull request was planned, so its reviewed change was not "
        "applied. " + _REMEDY
    )
    assert github_outputs(tmp_path / "out.txt") == {
        "head_sha": HEAD,
        "pr_number": "7",
        "push": '{"stacks": ["stacks/gone"], "outside": true}',
    }


def test_main_refuses_an_open_check_for_an_env_the_stack_no_longer_tags(tmp_path, monkeypatch):
    """`stacks/app` keeps `env/dev-eu` and lost `env/prod`: the `prod` check is stranded even
    though the stack is still managed. Mutation: refuse only checks of `unmanaged` stacks (the
    round-one form) -- the deploy writes waves."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[],
            checks=[
                _apply_check("stacks/app", cells=2),
                _apply_check("stacks/app", "prod", cells=2),
            ],
            stacks={"stacks/app": ["env/dev-eu"]},
            deps={"stacks/app": set()},
        )
    assert str(exc_info.value) == _PARTIAL_UNTAG_MESSAGE


def test_main_skips_an_open_check_on_a_stack_the_scan_does_not_cover(tmp_path, monkeypatch):
    """Another pull request landed `stacks/other`'s change first, so this merge's diff does
    not touch it: its open check is not this deploy's to refuse. Mutation: drop the in-scan
    condition, refusing every open check that names no scan cell -- the deploy refuses."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[],
        checks=[_apply_check("stacks/app", cells=2), _apply_check("stacks/other", cells=2)],
        stacks={"stacks/app": ["env/dev-eu"]},
        deps={"stacks/app": set()},
    )
    assert [c["stack"] for c in wave_cells(parsed)] == ["stacks/app"]


def test_a_stack_holding_the_separator_is_not_its_first_segment_s_check(tmp_path, monkeypatch):
    """`apply / prod / stacks/app / x` is the check of a stack `stacks/app / x`, absent from
    the scan, not a `prod` check of `stacks/app`. Mutation: `parse_check_name` takes the
    stack as `rest.split(" / ")[1]` -- the deploy refuses it as `stacks/app`'s stranded check."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[],
        checks=[
            _apply_check("stacks/app", cells=2),
            _apply_check("stacks/app / x", "prod", cells=2),
        ],
        stacks={"stacks/app": ["env/dev-eu"]},
        deps={"stacks/app": set()},
    )
    assert [c["stack"] for c in wave_cells(parsed)] == ["stacks/app"]


_PLANS_AFRESH = (
    "(any edit that `terramate list --changed` marks, such as a comment line). It plans the "
    "current default branch for all of them, this merged change included, and its cells are "
    "applied before merge under a required gate (`shipmate apply`, and `shipmate apply <env>` "
    "for a `targeted` environment), or by its deploy otherwise. Re-running this deploy checks "
    "out the same commit and refuses again. This deploy's apply checks are never read again and "
    "stay open on the merged pull request."
)


def test_an_open_stack_first_name_refuses_naming_the_cells_left_unapplied():
    """An open `apply / stacks/app / dev`, written before the upgrade, is outside the grammar, so
    no cell can be read from it and the deploy refuses, naming it and the pending cells. Mutation:
    skip a name `parse_check_name` cannot read again -- the call returns None."""
    with pytest.raises(SystemExit) as exc_info:
        dd.refuse_stranded_checks(
            [], [], {"apply / stacks/app / dev", "apply / x"}, [_cell("stacks/app", "dev-eu")]
        )
    assert str(exc_info.value) == (
        "::error::deploy aborted: apply / stacks/app / dev, apply / x: each is an apply check "
        "written before the upgrade that named apply checks env first, so no cell can be read "
        "from it and its reviewed change was not applied. This deploy applies nothing, so these "
        "other cells of this merge are not applied either: stacks/app / dev-eu. Open a new pull "
        "request that changes each of their stacks that still exists and every other listed stack "
        + _PLANS_AFRESH
    )


def test_an_unreadable_name_refuses_before_a_stranded_one():
    """Mutation: compute the stranded names first -- the stranded refusal fires, naming
    `apply / dev / stacks/app`."""
    names = {"apply / dev / stacks/app", "apply / stacks/app / dev"}
    with pytest.raises(SystemExit) as exc_info:
        dd.refuse_stranded_checks([], ["stacks/app"], names, [])
    assert str(exc_info.value) == (
        "::error::deploy aborted: apply / stacks/app / dev: each is an apply check written before "
        "the upgrade that named apply checks env first, so no cell can be read from it and its "
        "reviewed change was not applied. Open a new pull request that changes each of their "
        "stacks that still exists and every other listed stack " + _PLANS_AFRESH
    )


def test_main_deploys_past_an_unmanaged_stack_with_no_open_apply_check(tmp_path, monkeypatch):
    """An unmanaged stack the pull request changed, whose only apply check completed, does not
    refuse the deploy. Mutation: `stranded = sorted(unmanaged)` -- this run raises."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[],
        checks=[_apply_check("stacks/app", cells=1), _check(name="apply / dev-eu / stacks/gone")],
        stacks={"stacks/app": ["env/dev-eu"], "stacks/gone": []},
        deps={"stacks/app": set(), "stacks/gone": set()},
    )
    assert [c["stack"] for c in wave_cells(parsed)] == ["stacks/app"]


_PROBE_DEPS = {
    "dns": set(),
    "platform": {"dns"},
    "edge": {"platform"},
    "gr": {"edge", "dns"},
    "workers": set(),
}
_PROBE_CELLS = [
    _cell("dns", "dev-us"),
    _cell("platform", "dev-eu"),
    _cell("edge", "dev-us"),
    _cell("gr", "dev-eu"),
    _cell("workers", "dev-eu"),
]


def _run_probe(tmp_path, monkeypatch, order):
    return _run_main(
        tmp_path,
        monkeypatch,
        cells=_PROBE_CELLS,
        checks=[_apply_check(c["stack"], c["environment"], cells=5) for c in _PROBE_CELLS],
        deps=_PROBE_DEPS,
        order=order,
    )


def test_main_refuses_a_needs_order_that_contradicts_after(tmp_path, monkeypatch):
    """`dev-us` needs `dev-eu`, yet `platform` and `gr` (dev-eu) run after dev-us stacks.
    `workers / dev-eu` is in no pair and is named all the same: the deploy applies nothing.

    Mutation: list only the pairs' cells in the remedy -- `workers / dev-eu` drops out.
    Mutation: restore the remedy that sends a `needs` fix to a new pull request -- red.
    Mutation: restore the remedy that names only a retag for the new pull request -- red.
    Mutation: put the pair before the remedy on the first line -- red.
    Mutation: drop the `refuse_inversions` call from `main` -- the deploy writes waves."""
    with pytest.raises(SystemExit) as exc_info:
        _run_probe(tmp_path, monkeypatch, {"dev-us": ["dev-eu"]})
    assert str(exc_info.value) == (
        "::error::needs order inverted, fix default-branch needs or tags: gr / dev-eu before "
        "dns / dev-us, which it runs after\n"
        "- gr / dev-eu (env-level 0) after dns / dev-us (env-level 1); dev-us's level comes "
        "from dev-eu -> dev-us\n"
        "- gr / dev-eu (env-level 0) after edge / dev-us (env-level 1); dev-us's level comes "
        "from dev-eu -> dev-us\n"
        "- platform / dev-eu (env-level 0) after dns / dev-us (env-level 1); dev-us's level "
        "comes from dev-eu -> dev-us\n"
        "Env-levels apply in order, so `after` cannot be met. Drop or reverse a `needs` entry "
        "in that chain on the default branch, or retag the stacks so the edge stays inside "
        "environments that order it. This deploy applies nothing, so none of these cells of "
        "this merge is applied: dns / dev-us, edge / dev-us, gr / dev-eu, platform / dev-eu, "
        "workers / dev-eu. A fix by `needs`: merge it, then re-run this deploy, which reads "
        "`needs` from the default branch and applies these cells. A fix to tags or `after`: "
        "both live in this merged commit, so a re-run refuses again; open a new pull request "
        "that makes the fix and changes every listed stack (any edit `terramate list "
        "--changed` marks); its cells are applied before merge under a required gate "
        "(`shipmate apply`, and `shipmate apply <env>` for a `targeted` environment), or by "
        "its deploy otherwise."
    )


_AB_CELLS = [_cell("a", "dev-eu"), _cell("b", "dev-us")]


def test_main_refuses_a_cycle_before_an_inversion_naming_the_pending_cells(tmp_path, monkeypatch):
    """`a` and `b` are each `after` the other, and dev-us needs dev-eu, so `a / dev-eu` after
    `b / dev-us` also inverts. The deploy reads the run-graph through `bm.run_graph_deps`,
    whose sort refuses before `refuse_inversions`, and the plan's message comes first.
    `run_graph_capture("cycle-two")` is the raw capture; `_run` answers the run-graph argv, so
    `run_graph_deps` is real.

    Mutations: `deps = wv.parse_dot(bm._run([...run-graph argv...]))` for the read in `main`
    (the inversion message appears); `bm.run_graph_deps()` for `run_graph_deps(pending)` in
    `main` (no cells listed)."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=_AB_CELLS,
            checks=[_apply_check(c["stack"], c["environment"], cells=2) for c in _AB_CELLS],
            order={"dev-us": ["dev-eu"]},
            dot=run_graph_capture("cycle-two"),
        )
    assert str(exc_info.value) == (
        "::error::stack ordering cycle through a, b: a stack ordered after itself, directly or "
        "through other stacks, has no first wave. Remove one `after` or `before` entry on that "
        "loop in the stack configuration; pending on this merge: a / dev-eu, b / dev-us; see "
        "`stack ordering cycle through <stacks>` in docs/troubleshooting.md."
    )


def test_a_cycle_refusal_with_no_pending_cell_lists_none(tmp_path, monkeypatch):
    """Both cells were applied before the merge, so the deploy has no pending cell, and the
    loop still refuses it, pointing to the remedy. Capture as in the test above.

    Mutations: `if True` for `if cells` in deploy-detect's `run_graph_deps` (an empty list is
    appended); drop the troubleshooting pointer (red here and in the test above)."""
    with pytest.raises(SystemExit) as exc_info:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=_AB_CELLS,
            checks=[_check(name=f"apply / {c['environment']} / {c['stack']}") for c in _AB_CELLS],
            dot=run_graph_capture("cycle-two"),
        )
    assert str(exc_info.value) == (
        "::error::stack ordering cycle through a, b: a stack ordered after itself, directly or "
        "through other stacks, has no first wave. Remove one `after` or `before` entry on that "
        "loop in the stack configuration; see `stack ordering cycle through <stacks>` in "
        "docs/troubleshooting.md."
    )


def test_a_failed_run_graph_read_refuses_unchanged(monkeypatch):
    """Only the cycle refusal gains the cell list and pointer; a failed Terramate call keeps
    its own message.

    Mutation: `except SystemExit` for `except StackCycle` in deploy-detect's `run_graph_deps`
    (the cell list is appended)."""

    def fail(args):
        assert args == RUN_GRAPH_ARGV, args
        raise SystemExit("::error::terramate exited 1")

    monkeypatch.setattr(dd.bm, "_run", fail)
    with pytest.raises(SystemExit) as exc_info:
        dd.run_graph_deps([_cell("a")])
    assert exc_info.value.code == "::error::terramate exited 1"


def test_an_inversion_refusal_still_writes_the_head_sha(tmp_path, monkeypatch):
    """Mutation: write `head_sha` with the waves only -- the file holds only `push`."""
    with pytest.raises(SystemExit) as exc:
        _run_probe(tmp_path, monkeypatch, {"dev-us": ["dev-eu"]})
    assert str(exc.value).startswith("::error::needs order inverted")
    assert github_outputs(tmp_path / "out.txt") == {
        "head_sha": HEAD,
        "pr_number": "7",
        "push": '{"stacks": ["dns", "edge", "gr", "platform", "workers"], "outside": true}',
    }


def test_main_deploys_the_probe_with_no_needs(tmp_path, monkeypatch):
    """With no `needs` every env is level 0, so no `after` edge contradicts one.

    Mutation: `>=` for `>` in `inversions` -- level 0 = level 0 refuses."""
    parsed = _run_probe(tmp_path, monkeypatch, {})
    assert [(c["stack"], c["environment"]) for c in first_stage(parsed)] == [
        ("dns", "dev-us"),
        ("workers", "dev-eu"),
    ]


def _evict(who, hold, applied, known):
    runs = f", and for the apply, auto and deploy runs of {known} to end," if known else ""
    return (
        f"Comment `shipmate unlock dev-eu --evict` on it to release the locks {who} {hold}; "
        f"applying it after that may overwrite what {applied} applied to dev-eu. The eviction "
        "releases only locks on the new pull request's pending cells in dev-eu, any holder's, so "
        f"wait for any holder still open to merge or close{runs} first."
    )


_STEP_7 = _evict("#7", "holds", "#7", "#7")
LOCK_REMEDY = f"Open a pull request that changes these stacks. {_STEP_7} Then apply it."


#: `_locked_run`'s `push`: no previous tip in its env, so `outside` is true.
LOCKED_PUSH = '{"stacks": ["stacks/app", "stacks/dns"], "outside": true}'


def _locked_run(tmp_path, monkeypatch, locks, pr_number="12"):
    """`_run_main` over two pending dev-eu cells, stacks/app and stacks/dns, under `locks`."""
    return _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app"), _cell("stacks/dns")],
        checks=[_apply_check("stacks/app", cells=2), _apply_check("stacks/dns", cells=2)],
        pr_number=pr_number,
        locks=locks,
    )


def test_main_refuses_a_pending_cell_another_pull_request_locks(tmp_path, monkeypatch):
    """The whole refusal text, and `refused_locked` written beside the head facts and no wave.

    Mutation: drop the `refuse_locked` call -- the run writes waves and raises nothing.
    Mutation: write `refused_locked` after the raise -- absent from the outputs."""
    with pytest.raises(SystemExit) as exc:
        _locked_run(tmp_path, monkeypatch, {("stacks/dns", "dev-eu"): 7})
    assert str(exc.value) == (
        "::error::deploy refused: 1 pending cell(s) are locked by another pull request: "
        "stacks/dns / dev-eu (locked by #7). None of this deploy's 2 "
        "pending cell(s) were applied: stacks/app / dev-eu, stacks/dns / dev-eu. " + LOCK_REMEDY
    )
    assert github_outputs(tmp_path / "out.txt") == {
        "head_sha": HEAD,
        "pr_number": "12",
        "push": LOCKED_PUSH,
        "refused_locked": "true",
    }


def test_main_deploys_a_cell_the_merged_pull_request_itself_locks(tmp_path, monkeypatch):
    """Mutation: pass `0` instead of the merged pull request's number -- #12's own lock refuses."""
    parsed = _locked_run(tmp_path, monkeypatch, {("stacks/dns", "dev-eu"): 12})
    assert sorted(c["stack"] for c in wave_cells(parsed)) == ["stacks/app", "stacks/dns"]
    assert "refused_locked" not in parsed


def test_a_push_that_merged_no_pull_request_refuses_any_held_cell(tmp_path, monkeypatch):
    """Mutation: drop `or 0` -- `int("")` raises `ValueError` instead of the refusal."""
    with pytest.raises(SystemExit) as exc:
        _locked_run(tmp_path, monkeypatch, {("stacks/app", "dev-eu"): 7}, pr_number="")
    assert str(exc.value) == (
        "::error::deploy refused: 1 pending cell(s) are locked by another pull request: "
        "stacks/app / dev-eu (locked by #7). None of this deploy's 2 "
        "pending cell(s) were applied: stacks/app / dev-eu, stacks/dns / dev-eu. " + LOCK_REMEDY
    )


def test_an_unknown_holder_on_a_pending_cell_refuses_and_names_it(tmp_path, monkeypatch):
    """Both cells held, one by an unknown holder: listed sorted, each with its holder.

    Mutation: render `#None` for an unknown holder -- the text changes.
    Mutation: change `lock_who`'s text in `_shipmate` -- the text changes."""
    with pytest.raises(SystemExit) as exc:
        _locked_run(
            tmp_path, monkeypatch, {("stacks/dns", "dev-eu"): 9, ("stacks/app", "dev-eu"): None}
        )
    assert str(exc.value) == (
        "::error::deploy refused: 2 pending cell(s) are locked by another pull request: "
        "stacks/app / dev-eu (locked by an unknown holder), stacks/dns / dev-eu (locked by #9). "
        "None of this deploy's 2 pending cell(s) were applied: "
        "stacks/app / dev-eu, stacks/dns / dev-eu. Open a pull request that changes these "
        f"stacks. {_evict('#9 and an unknown holder', 'hold', 'they', '#9')} Then apply it."
    )


@pytest.mark.parametrize(
    ("app", "dns", "step"),
    [
        pytest.param(7, 7, _evict("#7", "holds", "#7", "#7"), id="one-holder-two-cells"),
        pytest.param(9, 7, _evict("#7 and #9", "hold", "they", "#7 and #9"), id="two-holders"),
    ],
)
def test_the_lock_remedy_names_each_holder_once_in_order(tmp_path, monkeypatch, app, dns, step):
    """Mutation: drop `set(...)` in `evict_release` -- one-holder-two-cells names #7 twice.
    Mutation: `dict.fromkeys` for the `sorted(...)` in `evict_release` -- two-holders names #9
    first.
    Mutation: drop the overwrite clause from `evict_release` -- red."""
    with pytest.raises(SystemExit) as exc:
        _locked_run(
            tmp_path, monkeypatch, {("stacks/app", "dev-eu"): app, ("stacks/dns", "dev-eu"): dns}
        )
    assert str(exc.value).endswith(
        "were applied: stacks/app / dev-eu, stacks/dns / dev-eu. Open a pull request that "
        f"changes these stacks. {step} Then apply it."
    )


def test_the_lock_remedy_has_one_eviction_sentence_per_environment_in_order():
    """Mutation: iterate `by_env.items()` unsorted -- prod comes first. Mutation: drop the
    wait-for-open-holders sentence -- red. Mutation: name every environment's holders in each
    run wait -- red."""
    assert dd.deploy_lock_remedy([(9, "prod"), (7, "dev")], "these stacks") == (
        "Open a pull request that changes these stacks. Comment `shipmate unlock dev --evict` on "
        "it to release the locks #7 holds; applying it after that may overwrite what #7 applied "
        "to dev. The eviction releases only locks on the new pull request's pending cells in dev, "
        "any holder's, so wait for any holder still open to merge or close, and for the apply, "
        "auto and "
        "deploy runs of #7 to end, first. Comment `shipmate unlock prod --evict` on it to release "
        "the locks #9 holds; applying it after that may overwrite what #9 applied to prod. The "
        "eviction releases only locks on the new pull request's pending cells in prod, any "
        "holder's, so wait for any holder still open to merge or close, and for the apply, auto "
        "and "
        "deploy runs of #9 to end, first. Then apply it"
    )


def test_the_lock_remedy_waits_for_no_runs_of_an_unknown_holder():
    """Only a known holder's runs are checked before an eviction. Mutation: drop the
    `if known` branch in `deploy_lock_remedy`'s wait -- `runs of None` renders."""
    assert dd.deploy_lock_remedy([(None, "dev")], "this stack") == (
        "Open a pull request that changes this stack. Comment `shipmate unlock dev --evict` on "
        "it to release the locks an unknown holder holds; applying it after that may overwrite "
        "what an unknown holder applied to dev. The eviction releases only locks on the new pull "
        "request's pending cells in dev, any holder's, so wait for any holder still open to "
        "merge or close first. Then apply it"
    )


def test_a_merge_with_no_pending_cell_reads_no_lock(tmp_path, monkeypatch):
    """A lock on another dev-eu stack does not matter when nothing is pending.

    Mutation: read the locks of every cell's env instead of the pending ones' -- the lock
    listing is read."""
    urls = []
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app")],
        checks=[_check(name="apply / dev-eu / stacks/app")],
        urls=urls,
        locks={("stacks/dns", "dev-eu"): 9},
    )
    assert urls == [CHECK_RUNS_URL]
    assert "refused_locked" not in parsed


def test_a_failed_lock_read_refuses_and_writes_no_refused_locked(tmp_path, monkeypatch):
    """Mutation: catch the read failure and continue -- the run writes waves."""
    with pytest.raises(SystemExit) as exc:
        _locked_run(tmp_path, monkeypatch, SystemExit("::error::gh api exited 1: boom"))
    assert str(exc.value) == "::error::gh api exited 1: boom"
    assert github_outputs(tmp_path / "out.txt") == {
        "head_sha": HEAD,
        "pr_number": "12",
        "push": LOCKED_PUSH,
    }


#: A lock naming one provider; `_LOCK_B` another, so the two digest differently.
_LOCK_A = 'provider "registry.opentofu.org/hashicorp/null" {\n  version = "3.2.4"\n}\n'
_LOCK_B = 'provider "registry.opentofu.org/hashicorp/random" {\n  version = "3.9.1"\n}\n'
_TF_VARS_TABLE = {"layout": "tf_vars", "environments": {"dev": {"region": "eu-west-1"}}}


def _tree(tmp_path, monkeypatch, locks):
    """A merged tree under `tmp_path`, the cwd, holding `locks`: `{stack: lock text}`."""
    for stack, text in locks.items():
        (tmp_path / stack).mkdir(parents=True)
        (tmp_path / stack / ".terraform.lock.hcl").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def test_warm_takes_one_managed_stack_per_distinct_lock(tmp_path, monkeypatch):
    """Changed or not, every managed stack is a candidate; the first per digest in path order
    wins, with the tf_vars the table resolves for its first environment by name. Unmanaged,
    local and lock-less stacks are left out. Mutations: drop the `LOCAL_TAG` clause; drop the
    `not envs` clause; key `rows` on the stack instead of the digest; take `envs[-1]`."""
    _tree(
        tmp_path,
        monkeypatch,
        {
            "a": _LOCK_A,
            "b": _LOCK_A,
            "c": _LOCK_B,
            "d": "# no provider block\n",
            "local": _LOCK_B + "\n" + _LOCK_A,
            "unmanaged": 'provider "x/y/z" {\n  version = "1.0.0"\n}\n',
        },
    )
    tag_map = {
        "a": ["env/prd", "env/dev"],
        "b": ["env/dev"],
        "c": ["env/prd"],
        "d": ["env/dev"],
        "local": ["env/dev", "shipmate/local"],
        "unmanaged": ["workload/app"],
        "missing": ["env/dev"],
    }
    assert dd.warm_cells(tag_map, _TF_VARS_TABLE) == [
        {"stack": "a", "tf_vars": {"TF_VAR_env": "dev", "TF_VAR_region": "eu-west-1"}},
        {"stack": "c", "tf_vars": {"TF_VAR_env": "prd", "TF_VAR_region": ""}},
    ]


def test_warm_past_the_matrix_limit_keeps_256_and_warns(tmp_path, monkeypatch, capsys):
    """Mutation: drop the slice (257 rows); drop the warning."""
    _tree(
        tmp_path,
        monkeypatch,
        {f"s{i:03}": f'provider "x/y/p{i}" {{\n  version = "1.0.0"\n}}\n' for i in range(258)},
    )
    rows = dd.warm_cells({f"s{i:03}": ["env/dev"] for i in range(258)}, _TF_VARS_TABLE)
    assert [r["stack"] for r in rows] == [f"s{i:03}" for i in range(256)]
    assert capsys.readouterr().err == (
        "::warning::2 of 258 distinct provider locks are not warmed: the warm matrix holds at "
        "most 256.\n"
    )


def test_no_lock_naming_a_provider_leaves_one_tools_only_row(tmp_path, monkeypatch, capsys):
    """A deploy still saves the OpenTofu release entry. Mutation: return `rows` alone."""
    _tree(tmp_path, monkeypatch, {"a": "# no provider block\n"})
    assert dd.warm_rows({"a": ["env/dev"]}, _TF_VARS_TABLE) == [{"stack": "", "tf_vars": {}}]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (KeyError("environments"), "'environments'"),
        (SystemExit("::error::refused"), "::error::refused"),
    ],
    ids=["exception", "refusal"],
)
def test_a_failed_warm_list_warns_and_warms_nothing(monkeypatch, capsys, error, said):
    """A degrade: the deploy carries on with the tools-only row, after an exception and after a
    `SystemExit` refusal, which is no `Exception`. Mutations: re-raise in `warm_rows`; catch
    `Exception` alone (reds `refusal`)."""

    def broken(tag_map, table):
        raise error

    monkeypatch.setattr(dd, "warm_cells", broken)
    assert dd.warm_rows({}, {}) == [{"stack": "", "tf_vars": {}}]
    assert capsys.readouterr().err == f"::warning::no provider cache warm-up: {said}\n"


def test_main_writes_the_warm_list(tmp_path, monkeypatch):
    """The rows and the cap come from the table the deploy loaded. Mutations: drop `warm=` from
    the final `write_outputs`; pass `{}` for the table; drop `max_parallel=`."""
    _tree(tmp_path / "tree", monkeypatch, {"stacks/app": _LOCK_A})
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app")],
        checks=[_apply_check("stacks/app", cells=1)],
        table={
            "layout": "tf_vars",
            "max_parallel": "4",
            "environments": {"dev-eu": {"region": "eu-west-1"}},
        },
    )
    assert (parsed["warm"], parsed["max_parallel"]) == (
        (
            '[{"stack": "stacks/app", '
            '"tf_vars": {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}}]'
        ),
        "4",
    )


#: The behind refusal's fixed remedy, hand-written.
_BEHIND_REMEDY = (
    "Open a new pull request that changes every listed stack (any edit that `terramate list "
    "--changed` marks, such as a comment line). It plans the current default branch for all of "
    "them, this merged change included, and its cells are applied before merge under a required "
    "gate (`shipmate apply`, and `shipmate apply <env>` for a `targeted` environment), or by its "
    "deploy otherwise. Re-running this deploy checks out the same commit and refuses again. This "
    "deploy's apply checks are never read again and stay open on the merged pull request."
)


def test_main_refuses_a_cell_planned_behind_the_base(tmp_path, monkeypatch):
    """`stacks/app`'s newest check is a behind record over an older run's queued check with a
    digest: the deploy refuses naming it and the other pending cell, never with the digest
    refusal, and writes no wave.

    Mutation: drop the `refuse_behind_checks` call -- the digest refusal, red.
    Mutation: call it after `with_plan_runs` -- the digest refusal, red."""
    with pytest.raises(SystemExit) as exc:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app"), _cell("stacks/web")],
            checks=[
                _apply_check("stacks/app", plan_run="41", cells=2, id=1),
                _apply_check("stacks/web", plan_run="41", cells=2, id=2),
                behind_check("stacks/app", plan_run="42", cells=1, id=3),
            ],
        )
    assert str(exc.value) == (
        "::error::deploy aborted: stacks/app / dev-eu: each was planned behind the base, so no "
        "reviewed plan exists for it. This deploy applies nothing, so these other cells of this "
        "merge are not applied either: stacks/web / dev-eu. " + _BEHIND_REMEDY
    )
    assert github_outputs(tmp_path / "out.txt") == {
        "head_sha": HEAD,
        "pr_number": "7",
        "push": '{"stacks": ["stacks/app", "stacks/web"], "outside": true}',
    }


def test_a_stranded_check_refuses_before_a_behind_one(tmp_path, monkeypatch):
    """`stacks/gone` lost its tags and its check is a behind record; `stacks/app`'s is one too.
    The stranded refusal names it first.

    Mutation: call `refuse_behind_checks` before `refuse_stranded_checks` -- the behind text,
    red."""
    with pytest.raises(SystemExit) as exc:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[],
            checks=[
                behind_check("stacks/app", cells=2),
                behind_check("stacks/gone", cells=2),
            ],
            stacks={"stacks/app": ["env/dev-eu"], "stacks/gone": []},
            deps={"stacks/app": set(), "stacks/gone": set()},
        )
    assert str(exc.value) == (
        "::error::deploy aborted: apply / dev-eu / stacks/gone: the stack of each lost its "
        "env/* tag after this pull request was planned, so its reviewed change was not "
        "applied. This deploy applies nothing, so these other cells of this merge are not "
        "applied either: stacks/app / dev-eu. " + _REMEDY
    )


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """An empty repository in `tmp_path` as the cwd, with this machine's git config out of the
    way and an identity CI runners lack."""
    (tmp_path / "gitconfig").write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "shipmate tests")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "tests@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    _git("init", "-q", "-b", "main")
    return repo


def _git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _commit(files):
    """Writes `files` (`{path: text}`) and commits them, returning the SHA."""
    for path, text in files.items():
        target = pathlib.Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
    _git("add", "-A")
    _git("commit", "-q", "-m", "c")
    return _git("rev-parse", "HEAD").strip()


#: The merged tree's stacks, as `all_tags` keys them.
PUSH_TAGS = {"stacks/app": ["env/dev-eu"], "stacks/dns": ["env/dev-eu"]}
PUSH_BASE = {
    "stacks/app/main.tf": "a",
    "stacks/dns/main.tf": "a",
    "modules/net/main.tf": "a",
    "README.md": "a",
}


@pytest.mark.parametrize(
    ("files", "changed", "want"),
    [
        pytest.param(
            {"stacks/app/main.tf": "b"},
            {"stacks/app"},
            '{"stacks": ["stacks/app"], "outside": false}',
            id="one-stack",
        ),
        pytest.param(
            {"modules/net/main.tf": "b"},
            {"stacks/dns", "stacks/app"},
            '{"stacks": ["stacks/app", "stacks/dns"], "outside": true}',
            id="module-only",
        ),
        pytest.param(
            {"README.md": "b", "stacks/app/sub/x.tf": "b"},
            {"stacks/app"},
            '{"stacks": ["stacks/app"], "outside": true}',
            id="non-stack-file",
        ),
    ],
)
def test_push_names_the_changed_stacks_and_a_path_outside_every_stack(
    git_repo, files, changed, want
):
    """Real git. A module change lists its dependents (`changed`, `compute_cells`') and is
    outside every stack. Mutations: `outside = False` after the diff -- the module and non-stack
    rows redden; `outside = bool(paths)` -- the one-stack row reddens."""
    before = _commit(PUSH_BASE)
    _commit(files)
    assert dd.push_stacks(before, changed, PUSH_TAGS) == want


@pytest.mark.parametrize("before", ["", "0" * 40])
def test_push_with_no_previous_tip_is_outside_and_runs_no_git(monkeypatch, before):
    """An empty or all-zero `before` (a new branch) leaves the change unknown: the widest
    answer, with no diff. Mutation: `if before:` for `if before.strip("0"):` -- the zero row
    diffs, and the stub raises."""

    def no_git(args):
        raise AssertionError(args)

    monkeypatch.setattr(dd.bm, "_run", no_git)
    assert dd.push_stacks(before, {"stacks/app"}, PUSH_TAGS) == (
        '{"stacks": ["stacks/app"], "outside": true}'
    )


def test_push_past_the_cap_names_no_stack():
    """700 stacks of 100 characters serialize past 64 KiB. Mutation: drop the cap -- the full
    list is returned."""
    changed = {f"stacks/{i:03d}/" + "x" * 89 for i in range(700)}
    assert dd.push_stacks("", changed, {}) == '{"stacks": null, "outside": true}'


def test_a_failed_diff_warns_and_writes_no_push(git_repo, capsys):
    """`push` only selects pull requests to re-plan, so a diff failure never refuses the deploy.
    Mutation: drop the `except SystemExit` -- the failure raises."""
    _commit(PUSH_BASE)
    assert dd.push_stacks("1" * 40, {"stacks/app"}, PUSH_TAGS) is None
    err = capsys.readouterr().err
    assert err.startswith(
        "::warning::the push's changed paths could not be read: command failed (128): git diff "
    )
    assert len(err.splitlines()) == 1


_TROUBLESHOOT_LOST = (
    "Follow docs/troubleshooting.md section The apply step ran and its state was not saved "
    "before applying them again."
)
_BY_HAND = "docs/troubleshooting.md section Return an in-progress apply check by hand"
_OTHERS = (
    " This deploy applies nothing, so these other cells of this merge are not applied either: "
    "stacks/web / dev-eu."
)
_LIVE = (
    "::error::deploy refused: stacks/app / dev-eu are held in progress by run 77, which is still "
    "running. Once it ends, re-run this deploy: a cell whose check that run left in progress "
    "deploys if the run never applied it and is refused otherwise." + _OTHERS
)


#: The cell's job names in a deploy, `<level> / <env> / <stack> / <part>`.
APP_APPLY = "deploy / waves / L0-W0 / dev-eu / stacks/app / apply"
APP_SAVE = "deploy / waves / L0-W0 / dev-eu / stacks/app / save"
WEB_APPLY = "deploy / waves / L0-W0 / dev-eu / stacks/web / apply"
_STARTS = itertools.count()
#: `(name, conclusion)` steps of an `apply` job whose apply step ran.
_RAN = [("Set up job", "success"), ("Apply the cell", "success")]


def _job(name=APP_APPLY, steps=None, status="completed", attempt=1, conclusion="", started=None):
    """One listed job of run attempt `attempt`; `steps`, `(name, conclusion)` pairs, default
    to a started apply. `conclusion` defaults to `success` once `completed`, else none;
    `started`, the `started_at` a carried copy shares with its original, defaults to a fresh
    one."""
    if steps is None:
        steps = _RAN
    if conclusion == "":
        conclusion = "success" if status == "completed" else None
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "started_at": started or f"2026-11-20T10:{next(_STARTS):05d}Z",
        "steps": [{"name": n, "status": "completed", "conclusion": c} for n, c in steps],
    }


def _save(conclusion="success", attempt=1, **kw):
    """The cell's listed `save` job."""
    return _job(APP_SAVE, steps=[], conclusion=conclusion, attempt=attempt, **kw)


def _newest(jobs):
    return max(j["run_attempt"] for j in jobs)


#: An `apply` job cancelled while pending: it never applied.
_CANCELLED = _job(steps=[], conclusion="cancelled")


def _two_cells(tmp_path, monkeypatch, runs, check=None, urls=None, attempt="1"):
    """`stacks/app` in flight (`check`, default held by run `HOLDER`) beside a queued
    `stacks/web`, both pending."""
    return _run_main(
        tmp_path,
        monkeypatch,
        cells=[_cell("stacks/app"), _cell("stacks/web")],
        checks=[
            check or _in_flight("stacks/app", cells=2, id=1),
            _apply_check("stacks/web", cells=2, id=2),
        ],
        runs=runs,
        urls=urls,
        attempt=attempt,
    )


@pytest.mark.parametrize("status", ["in_progress", "queued", "waiting"])
def test_a_check_a_live_run_holds_refuses_the_deploy(tmp_path, monkeypatch, status):
    """Run 77 still runs, so its cell may be applying: the deploy refuses, naming the run, what a
    re-run after it ends does, and the other pending cell, and writes no stages.

    Mutation: drop the `refuse_in_flight` call -- deploys, red.
    Mutation: call `refuse_in_flight` after `write_outputs` -- `stages` written, red.
    Mutation: treat only `in_progress` as live -- `queued` and `waiting` read a jobs listing
    the stub does not hold and refuse as unreadable, red."""
    with pytest.raises(SystemExit) as exc:
        _two_cells(tmp_path, monkeypatch, _holder(status))
    assert str(exc.value) == _LIVE
    assert "stages" not in github_outputs(tmp_path / "out.txt")


def test_a_newer_replan_check_does_not_hide_an_older_held_one(tmp_path, monkeypatch):
    """A same-head re-plan queued a newer `stacks/app` check while run 77, still running, holds
    the older one `in_progress`: the deploy refuses as it does with the held check alone.

    Mutation: read only each cell's newest App apply check -- deploys, red."""
    checks = [
        _in_flight("stacks/app", cells=2, id=1),
        _apply_check("stacks/web", cells=2, id=2),
        _apply_check("stacks/app", cells=2, id=3),
    ]
    with pytest.raises(SystemExit) as exc:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell("stacks/app"), _cell("stacks/web")],
            checks=checks,
            runs=_holder("in_progress"),
        )
    assert str(exc.value) == _LIVE
    assert "stages" not in github_outputs(tmp_path / "out.txt")


@pytest.mark.parametrize("name", [APP_APPLY, APP_SAVE], ids=["apply", "save"])
def test_a_completed_run_listing_a_live_job_of_the_cell_is_live(tmp_path, monkeypatch, name):
    """Run 77 reads `completed` on attempt 1, but its listing shows the cell's attempt-1 `apply`
    or `save` job not `completed`: the cell is held as live.

    Mutation: drop the cell jobs' status check -- the cell reads `ended` (the queued `apply`
    job is unknown, the queued `save` did not succeed), red."""
    jobs = [_job(), _save()] if name == APP_SAVE else []
    jobs = [j for j in jobs if j["name"] != name] + [_job(name, steps=[], status="queued")]
    with pytest.raises(SystemExit) as exc:
        _two_cells(tmp_path, monkeypatch, _holder("completed", jobs))
    assert str(exc.value) == _LIVE


@pytest.mark.parametrize(
    "again",
    [{"status": "completed", "run_attempt": 2}, {"status": "queued", "run_attempt": 1}],
    ids=["attempt advanced", "queued"],
)
def test_a_rerun_between_the_reads_holds_the_cell_live(tmp_path, monkeypatch, again):
    """Run 77 reads `completed` on attempt 1 and its cell job never applied, but read again after
    the listing it is a newer attempt, or queued again: a re-run whose jobs may not be listed yet
    may still apply the cell, so the cell is held as live.

    Mutation: skip the second read -- deploys, red.
    Mutation: compare the status only -- the advanced attempt deploys, red.
    Mutation: compare the attempt only -- the queued read deploys, red."""
    runs = _holder("completed", [_CANCELLED], again=again)
    with pytest.raises(SystemExit) as exc:
        _two_cells(tmp_path, monkeypatch, runs)
    assert str(exc.value) == _LIVE


def test_a_listed_job_of_a_later_attempt_holds_the_cell_live(tmp_path, monkeypatch):
    """Both run reads show attempt 1 `completed`, but the listing already shows an attempt-2
    `detect` queued: the re-run started and the run reads lag it. The cell's attempt-1 job never
    applied, but attempt 2 may still apply it, so the cell is held as live.

    Mutation: drop the job-attempt check -- deploys, red."""
    jobs = [_CANCELLED, _job(name="deploy / detect", steps=[], status="queued", attempt=2)]
    with pytest.raises(SystemExit) as exc:
        _two_cells(tmp_path, monkeypatch, _holder("completed", jobs))
    assert str(exc.value) == _LIVE


def test_a_listing_below_the_run_attempt_holds_the_cell_live(tmp_path, monkeypatch):
    """Both run reads show attempt 2 `completed`, but the listing holds only attempt-1 jobs, the
    cell's among them never applied: the listing is stale, so it cannot show what attempt 2 did,
    and the cell is held as live.

    Mutation: hold only on a later attempt (`>`) -- deploys, red."""
    jobs = [_job(steps=[("Set up job", "success"), ("Apply the cell", "skipped")])]
    with pytest.raises(SystemExit) as exc:
        _two_cells(tmp_path, monkeypatch, _holder("completed", jobs, attempt=2))
    assert str(exc.value) == _LIVE


def test_a_stuck_job_of_another_cell_does_not_hold_the_cell(tmp_path, monkeypatch, capsys):
    """Run 77 reads `completed` and lists a job of another cell still `in_progress`; the cell's
    own job never applied, so the cell deploys with the warning.

    Mutation: count every listed job's status -- held as live, red."""
    jobs = [_job(name=WEB_APPLY, steps=[], status="in_progress"), _CANCELLED]
    parsed = _two_cells(tmp_path, monkeypatch, _holder("completed", jobs, attempt=_newest(jobs)))
    assert [c["stack"] for c in wave_cells(parsed)] == ["stacks/app", "stacks/web"]
    assert "run 77 ended before applying this cell" in capsys.readouterr().err


_NOT_STARTED = {
    "skipped": [_job(steps=[("Set up job", "success"), ("Apply the cell", "skipped")])],
    "cancelled while pending": [_CANCELLED, _save("skipped")],
    "pre-apply failed, apply failed": [
        _job(steps=[("Pre-apply check", "failure"), ("Apply the cell", "failure")]),
        _save("failure"),
    ],
    "pre-apply failed, apply succeeded": [
        _job(steps=[("Pre-apply check", "failure"), ("Apply the cell", "success")])
    ],
    "never applied, carried": [
        _job(steps=[], conclusion="cancelled", started="2026-11-20T09:00:00Z"),
        _job(steps=[], conclusion="cancelled", started="2026-11-20T09:00:00Z", attempt=2),
    ],
}


@pytest.mark.parametrize("jobs", _NOT_STARTED.values(), ids=_NOT_STARTED.keys())
def test_an_ended_run_that_never_applied_the_cell_deploys_it_with_a_warning(
    tmp_path, monkeypatch, capsys, jobs
):
    """Run 77 ended and no attempt's `apply` job applied the cell: `Apply the cell` `skipped`,
    cancelled while pending with no steps (also as attempt 2's carried copy), or a failed
    `Pre-apply check`, after which `Apply the cell` runs only to refuse.
    Probe (runs 43968821022 and 43968821153): a leg that started `Apply the cell` never lists it
    `skipped`, and a pre-apply failure lists it `success`. Nothing writes the check again, so
    the deploy queues it and warns.

    Mutation: refuse on any ended run -- refuses, red.
    Mutation: drop the `Pre-apply check` clause -- the two pre-apply cases refuse, red."""
    parsed = _two_cells(tmp_path, monkeypatch, _holder("completed", jobs, attempt=_newest(jobs)))
    assert [c["stack"] for c in wave_cells(parsed)] == ["stacks/app", "stacks/web"]
    warnings = [line for line in capsys.readouterr().err.splitlines() if "run 77" in line]
    assert warnings == [
        (
            "::warning::stacks/app / dev-eu: run 77 ended before applying this cell and left "
            "its apply check in progress; this deploy queues it"
        )
    ]


_STARTED = {
    "success, no save": [_job()],
    "success, save failed": [_job(), _save("failure")],
    "failure": [_job(steps=[("Apply the cell", "failure")], conclusion="failure")],
    "cancelled": [_job(steps=[("Apply the cell", "cancelled")], conclusion="cancelled")],
    "no conclusion": [_job(steps=[("Apply the cell", None)])],
    "unnamed step": [_job(steps=[("Run ship-iac/shipmate/actions/apply-cell@main", "success")])],
    "one of two jobs": [_CANCELLED, _job(steps=[("Apply the cell", "failure")])],
    "pre-apply passed, apply failed": [
        _job(steps=[("Pre-apply check", "success"), ("Apply the cell", "failure")]),
        _save("failure"),
    ],
    "no job": [_job(name=WEB_APPLY)],
    "older job shape": [_job(name=APP_APPLY.removesuffix(" / apply"))],
    "attempt 1 unsaved, attempt 2 refused": [
        _job(),
        _save("failure"),
        _job(steps=[("Pre-apply check", "failure"), ("Apply the cell", "failure")], attempt=2),
        _save("failure", attempt=2),
    ],
}


@pytest.mark.parametrize("jobs", _STARTED.values(), ids=_STARTED.keys())
def test_an_ended_run_whose_apply_may_have_started_refuses(tmp_path, monkeypatch, jobs):
    """Run 77 ended and an attempt's `apply` job lists `Apply the cell` as started (any
    conclusion but `skipped`) after a `Pre-apply check` that did not fail, or lists steps
    without either (an older engine), or one of two jobs did, and no `save` of that attempt or a
    later one succeeded; or the run lists no job of the cell under the job names this engine
    reads: the cell may have applied without saving its state, so the deploy refuses and names
    the procedure.

    Mutation: deploy every ended run -- deploys, red.
    Mutation: drop `not mine or` in `holder_state` -- the no-job cases deploy, red.
    Mutation: treat only `success` as started -- the other started cases deploy, red.
    Mutation: count any `Pre-apply check` conclusion as not started -- the pre-apply-passed case
    deploys, red.
    Mutation: classify a `save` `failure` after an applied attempt as saved -- the save-failed
    cases read `saved`, red."""
    with pytest.raises(SystemExit) as exc:
        _two_cells(tmp_path, monkeypatch, _holder("completed", jobs, attempt=_newest(jobs)))
    assert str(exc.value) == (
        "::error::deploy refused: stacks/app / dev-eu were left in progress by run 77, which "
        "ended after their apply may have started, so their state may not be saved. "
        + _TROUBLESHOOT_LOST
        + _OTHERS
    )


_SAVED = {
    "applied, saved": [_job(), _save()],
    "unknown, saved": [_job(steps=[]), _save()],
    "saved by a later attempt": [
        _job(),
        _save("failure"),
        _job(steps=[("Pre-apply check", "failure"), ("Apply the cell", "failure")], attempt=2),
        _save(attempt=2),
    ],
    "saved, carried": [
        _job(started="2026-11-20T09:00:00Z"),
        _save(started="2026-11-20T09:00:01Z"),
        _job(started="2026-11-20T09:00:00Z", attempt=2, steps=[]),
        _save(started="2026-11-20T09:00:01Z", attempt=2),
    ],
}


@pytest.mark.parametrize("jobs", _SAVED.values(), ids=_SAVED.keys())
def test_an_ended_run_that_saved_any_state_of_the_cell_refuses_naming_the_by_hand_return(
    tmp_path, monkeypatch, jobs
):
    """Run 77 ended after an attempt applied the cell, or may have, and that attempt's `save` or
    a later one succeeded: the state is saved but the check stays in progress, so the deploy
    refuses and points at the by-hand return.

    Mutation: read an unknown `apply` verdict as not applied -- the unknown case deploys, red.
    Mutation: read every ended run that applied as `ended` -- the state-loss text, red."""
    with pytest.raises(SystemExit) as exc:
        _two_cells(tmp_path, monkeypatch, _holder("completed", jobs, attempt=_newest(jobs)))
    assert str(exc.value) == (
        "::error::deploy refused: stacks/app / dev-eu were left in progress by run 77, which "
        "may have applied them and saved any state their apply left. Follow "
        f"{_BY_HAND}, then re-run this deploy." + _OTHERS
    )


def test_the_held_check_apply_complete_writes_refuses_as_a_state_loss(tmp_path, monkeypatch):
    """Run 77's `complete` job holds the cell from one jobs listing (apply `success`, save
    `failure`) and writes the held check; the deploy reads that check and the same listing and
    refuses with the state-loss text, not as an unknown holder.

    Mutation: `apply-complete` links the `save` job (`<run page>/job/<id>`) -- `unknown`,
    red."""
    for k, v in {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "acme/iac",
        "GITHUB_RUN_ID": HOLDER,
    }.items():
        monkeypatch.setenv(k, v)
    listing = [
        {**_job("deploy / waves / L0-W0 / complete"), "id": 5},
        {**_job(), "id": 6},
        {**_save("failure"), "id": 7},
    ]
    rows, unresolved = ac.classify(
        {"L0-W0": {"apply / dev-eu / stacks/app": [1]}},
        {"L0-W0": "success"},
        listing,
        ac.ag.level_of(listing, "5"),
        ac._own(listing, "5").get("run_attempt"),
    )
    assert (rows, unresolved) == (
        [
            {
                "check": "apply / dev-eu / stacks/app",
                "ids": [1],
                "conclusion": None,
                "job_id": None,
                "url": None,
                "held": True,
            }
        ],
        [],
    )
    [entry] = ac.writes(rows[0], "acme/iac")
    base = _apply_check("stacks/app", cells=2, id=entry["id"])
    check = {**base, **pc.with_output(entry, base)}
    assert check["status"] == "in_progress"
    cause = dd.ad.holder_state("acme/iac", _cell("stacks/app"), check, {HOLDER: ("ended", listing)})
    assert cause == ("ended", HOLDER)
    with pytest.raises(SystemExit) as exc:
        _two_cells(tmp_path, monkeypatch, _holder("completed", listing), check=check)
    assert str(exc.value) == (
        "::error::deploy refused: stacks/app / dev-eu were left in progress by run 77, which "
        "ended after their apply may have started, so their state may not be saved. "
        + _TROUBLESHOOT_LOST
        + _OTHERS
    )


_GH_500 = SystemExit("::error::gh api repos/acme/iac/actions/runs/77 failed\nHTTP 500: boom")


_NEVER_APPLIED = [_CANCELLED]
_BAD_ATTEMPTS = [None, True, "2"]


@pytest.mark.parametrize(
    ("runs", "reads"),
    [
        ({RUN_PATH: _GH_500}, (3, 0)),
        ({**_holder("completed"), JOBS_PATH: _GH_500}, (1, 3)),
        (_holder("completed", []), (1, 1)),
        (
            _holder(
                "completed",
                [_job(name="apply / run / waves / L0-W0 / dev-eu / stacks/web / apply")],
                total=2,
            ),
            (1, 1),
        ),
        ({RUN_PATH: json.dumps({"id": 77, "run_attempt": 1})}, (1, 0)),
        ({RUN_PATH: json.dumps({"id": 77, "status": "completed"})}, (1, 0)),
        *[(_holder("completed", _NEVER_APPLIED, attempt=bad), (1, 0)) for bad in _BAD_ATTEMPTS],
        *[
            (
                _holder(
                    "completed", _NEVER_APPLIED, again={"status": "completed", "run_attempt": bad}
                ),
                (2, 1),
            )
            for bad in _BAD_ATTEMPTS
        ],
        (_holder("completed", _NEVER_APPLIED, again=_GH_500), (4, 1)),
        *[(_holder("completed", [_job(steps=[], attempt=bad)]), (1, 1)) for bad in _BAD_ATTEMPTS],
    ],
    ids=[
        "run read",
        "jobs listing",
        "empty listing",
        "short listing",
        "no status",
        "run with no attempt",
        *[f"first attempt {bad!r}" for bad in _BAD_ATTEMPTS],
        *[f"second attempt {bad!r}" for bad in _BAD_ATTEMPTS],
        "second read",
        *[f"job attempt {bad!r}" for bad in _BAD_ATTEMPTS],
    ],
)
def test_a_holding_run_that_cannot_be_read_completely_refuses(tmp_path, monkeypatch, runs, reads):
    """A run read (first or second) or jobs listing that fails after its retries, an empty
    listing, a listing short of its `total_count`, and a run read with no string status or no
    int `run_attempt` (null, true or "2", on either read) or a listed job with one each refuse
    naming the run, with no traceback: the cell may be applying. A repeat points at the by-hand
    section. `reads` counts the run reads and the jobs listing reads. Under `filter=all`,
    `total_count` counts every attempt's jobs: a probe of run 43649538511 listed 51 of 51 under
    `all` and 38 of 38 under `latest`.

    Mutation: deploy on a failed read -- deploys, red.
    Mutation: deploy on a failed listing -- deploys, red.
    Mutation: list without the count check -- the short listing deploys, red.
    Mutation: read a missing status as `completed` -- the listing is read, red.
    Mutation: `isinstance` in place of `type(...) is int` -- a `true` attempt on both reads
    deploys, red.
    Mutation: accept a job with a non-int attempt -- a traceback or a deploy, red."""
    monkeypatch.setattr(_shipmate, "_sleep", lambda s: None)
    urls = []
    with pytest.raises(SystemExit) as exc:
        _two_cells(tmp_path, monkeypatch, runs, urls=urls)
    assert str(exc.value) == (
        "::error::deploy refused: stacks/app / dev-eu: run 77 could not be read completely; "
        f"re-run this deploy. If it fails the same way, follow {_BY_HAND}." + _OTHERS
    )
    assert (urls.count(RUN_PATH), urls.count(JOBS_PATH)) == reads


OWN_JOBS_PATH = f"repos/acme/iac/actions/runs/{OWN_RUN}/jobs?filter=all&per_page=100&page=1"
#: Attempt 2's own `detect`, running.
_OWN_DETECT = _job(name="deploy / detect", steps=[], status="in_progress", attempt=2)


def _own(jobs):
    """`_run_main`'s `runs` answering this run's own jobs listing with `jobs`."""
    return {OWN_JOBS_PATH: json.dumps({"total_count": len(jobs), "jobs": jobs})}


def test_a_deploy_rerun_passes_its_own_in_progress_checks(tmp_path, monkeypatch):
    """Attempt 2 of this deploy meets the checks its attempt 1 flipped, naming this run's page
    (which carries no attempt), and attempt 1 applied and saved the cell: it deploys, reads no
    run, and lists its own jobs once.

    Mutation: drop the own-run exclusion -- run 900 is read, red.
    Mutation: compare with a URL carrying the attempt -- run 900 is read, red."""
    urls = []
    parsed = _two_cells(
        tmp_path,
        monkeypatch,
        runs=_own([_job(), _save(), _OWN_DETECT]),
        check=_in_flight("stacks/app", url=RUN_PAGE + OWN_RUN, cells=2, id=1),
        urls=urls,
        attempt="2",
    )
    assert [c["stack"] for c in wave_cells(parsed)] == ["stacks/app", "stacks/web"]
    assert [u for u in urls if "/actions/runs/" in u] == [OWN_JOBS_PATH]


@pytest.mark.parametrize(
    "check",
    [
        _in_flight("stacks/app", url=RUN_PAGE + OWN_RUN, cells=2, id=1),
        _apply_check("stacks/app", cells=2, id=1),
    ],
    ids=["own flip", "queued"],
)
def test_a_deploy_rerun_refuses_a_cell_its_earlier_attempt_left_unsaved(
    tmp_path, monkeypatch, check
):
    """Attempt 2 of this deploy lists its own run: attempt 1 applied `stacks/app` and its `save`
    failed. Whatever the check reads, the deploy refuses naming this run's earlier attempt.

    Mutation: skip the own-run read on attempt 2 -- deploys, red.
    Mutation: key the cell `ended` in `_own_unsaved` -- the ended-run text, red."""
    with pytest.raises(SystemExit) as exc:
        _two_cells(
            tmp_path,
            monkeypatch,
            runs=_own([_job(), _save("failure"), _OWN_DETECT]),
            check=check,
            attempt="2",
        )
    assert str(exc.value) == (
        "::error::deploy refused: stacks/app / dev-eu may have been applied by an earlier attempt "
        "of this deploy, run 900, without saving their state. " + _TROUBLESHOOT_LOST + _OTHERS
    )


@pytest.mark.parametrize(
    "runs",
    [
        {OWN_JOBS_PATH: _GH_500},
        _own([]),
        _own([{**_OWN_DETECT, "run_attempt": None}]),
    ],
    ids=["failed", "empty", "job attempt null"],
)
def test_a_deploy_rerun_that_cannot_list_its_own_jobs_refuses(tmp_path, monkeypatch, runs):
    """A failed, empty or malformed listing of this run's own jobs on attempt 2 refuses every
    pending cell naming this deploy's own jobs: this run always lists its own `detect`, and the
    by-hand return alone cannot clear the refusal.

    Mutation: read a listing that is not None as readable (no `newest_attempt` check) -- the
    empty and null-attempt cases deploy, red.
    Mutation: key the cells `unreadable` in `_own_unsaved` -- the other-run text, red."""
    monkeypatch.setattr(_shipmate, "_sleep", lambda s: None)
    with pytest.raises(SystemExit) as exc:
        _two_cells(
            tmp_path,
            monkeypatch,
            runs=runs,
            check=_apply_check("stacks/app", cells=2, id=1),
            attempt="2",
        )
    assert str(exc.value) == (
        "::error::deploy refused: stacks/app / dev-eu, stacks/web / dev-eu: this deploy, run 900, "
        "could not read its own jobs completely. Re-run this deploy; if it keeps failing, read its "
        "earlier attempts' cell apply and save job logs, follow docs/troubleshooting.md section "
        "The apply step ran and its state was not saved for a cell whose apply ran without saving "
        "its state, complete by hand the apply checks of cells whose change is applied and state "
        f"saved, as in {_BY_HAND}, and apply the rest through a new pull request touching their "
        "stacks."
    )


@pytest.mark.parametrize(
    "url",
    [
        f"{RUN_PAGE}{OWN_RUN}/attempts/2",
        f"{RUN_PAGE}{HOLDER}?x",
        f"https://github.com/acme/other/actions/runs/{HOLDER}",
        "",
        None,
    ],
)
def test_a_check_naming_no_run_of_this_repository_refuses(tmp_path, monkeypatch, url):
    """A `details_url` that is not exactly a run page of this repository names no holder the
    deploy can read: it refuses naming the check id and the by-hand procedure, never telling
    the reader to wait, and reads no run.

    Mutation: match the run URL by prefix -- the `/attempts/2` and `?x` cases pass or read a
    run, red.
    Mutation: reuse the live sentence for an unknown holder -- red."""
    urls = []
    with pytest.raises(SystemExit) as exc:
        _two_cells(
            tmp_path,
            monkeypatch,
            runs={},
            check=_in_flight("stacks/app", url=url, cells=2, id=41),
            urls=urls,
        )
    assert str(exc.value) == (
        "::error::deploy refused: stacks/app / dev-eu: apply check 41 is in progress with no run "
        f"named; follow {_BY_HAND}, then re-run this deploy." + _OTHERS
    )
    assert [u for u in urls if "/actions/runs/" in u] == []


def test_a_reopened_check_its_live_run_holds_refuses(tmp_path, monkeypatch):
    """A check reopened to `in_progress` keeps its old `success` conclusion; the status decides.

    Mutation: test `conclusion` instead of `status` -- deploys, red."""
    check = {**_in_flight("stacks/app", cells=2, id=1), "conclusion": "success"}
    with pytest.raises(SystemExit, match="held in progress by run 77"):
        _two_cells(tmp_path, monkeypatch, _holder("in_progress"), check=check)


def test_a_queued_check_reads_no_run(tmp_path, monkeypatch):
    """A `queued` check is pending work as before, whatever its `details_url`.

    Mutation: refuse every not-done check -- refuses, red."""
    urls = []
    check = {**_apply_check("stacks/app", cells=2, id=1), "details_url": RUN_PAGE + HOLDER}
    parsed = _two_cells(tmp_path, monkeypatch, runs={}, check=check, urls=urls)
    assert [c["stack"] for c in wave_cells(parsed)] == ["stacks/app", "stacks/web"]
    assert [u for u in urls if "/actions/runs/" in u] == []


def test_a_stranded_check_refuses_before_an_in_flight_one(tmp_path, monkeypatch):
    """`stacks/gone` lost its tags and its check is in flight; `stacks/app`'s is too. The
    stranded refusal names it first.

    Mutation: call `refuse_in_flight` before `refuse_stranded_checks` -- the in-flight text,
    red."""
    with pytest.raises(SystemExit) as exc:
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[],
            checks=[
                _in_flight("stacks/app", cells=2, id=1),
                _in_flight("stacks/gone", cells=2, id=2),
            ],
            stacks={"stacks/app": ["env/dev-eu"], "stacks/gone": []},
            deps={"stacks/app": set(), "stacks/gone": set()},
            runs=_holder("in_progress"),
        )
    assert str(exc.value).startswith("::error::deploy aborted: apply / dev-eu / stacks/gone: ")


def test_an_in_flight_check_refuses_before_its_missing_digest(tmp_path, monkeypatch):
    """`stacks/app`'s in-flight check records no plan-text digest: the in-flight refusal speaks,
    not the digest refusal, whose remedy is a re-plan.

    Mutation: call `refuse_in_flight` after `with_plan_runs` -- the digest text, red."""
    check = _in_flight("stacks/app", plan_sha256=None, cells=2, id=1)
    with pytest.raises(SystemExit, match="held in progress by run 77"):
        _two_cells(tmp_path, monkeypatch, _holder("in_progress"), check=check)


def test_the_cell_names_the_steps_the_deploy_reads():
    """The deploy finds whether a cell applied in a jobs listing by these step names.

    Mutation: rename `cell.yml`'s `Apply the cell` step -- red.
    Mutation: rename `cell.yml`'s `Pre-apply check` step -- red.
    Mutation: rename `apply-gate.APPLY_STEP` or `PRE_APPLY_STEP` -- red."""
    assert (dd.ad.ag.PRE_APPLY_STEP, dd.ad.ag.APPLY_STEP) == ("Pre-apply check", "Apply the cell")
    names = [
        (s.get("name"), s["uses"].rsplit("/", 1)[1])
        for s in workflow_jobs("cell.yml")["apply"]["steps"]
        if s.get("uses", "").endswith(("/pre-apply", "/apply-cell"))
    ]
    assert names == [("Pre-apply check", "pre-apply"), ("Apply the cell", "apply-cell")]
