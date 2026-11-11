import functools
import json

import _shipmate
import pytest
from _detect_fixtures import (
    APP_ID,
    RUN_GRAPH_ARGV,
    _apply_check,
    answering_run_graph,
    forbid_shared_runner,
    partial_message,
    run_graph_capture,
    spy_env_config,
    stub_read_table,
    wave_cells,
)
from _detect_fixtures import check_run as _check
from _loader import github_outputs, load_script

dd = load_script("deploy-detect")

HEAD = "a" * 40
CHECK_RUNS_URL = f"repos/acme/iac/commits/{HEAD}/check-runs?filter=all&per_page=100"
LOCKS_URL = "repos/acme/iac/git/matching-refs/shipmate/locks/dev-eu/"
PULLS_PATH = "repos/o/r/commits/merge123/pulls"
CLOSED_PATH = "repos/o/r/pulls?state=closed&base=main&sort=updated&direction=desc&per_page=100"
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

    Returns the parsed GITHUB_OUTPUT."""
    out = tmp_path / "out.txt"
    for k, v in {
        "GITHUB_REPOSITORY": "acme/iac",
        "GITHUB_SHA": "merge123" if pull_reads is None else head,
        "GITHUB_REF_NAME": "main",
        "GITHUB_OUTPUT": str(out),
        "SHIPMATE_APP_ID": APP_ID,
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SHIPMATE_BASE_SHA", raising=False)
    stub_read_table(monkeypatch, (dd.bm.ec,), table, order, reads=reads)
    jsonl = "\n".join(json.dumps(c) for c in checks)
    lock_answer = _lock_answer(locks)

    def _run(args):
        # Every terramate call must be stubbed, because CI installs uv alone: a real
        # invocation passes on a developer machine and fails there.
        assert args[0] == "gh", args
        path = next(a for a in args if a.startswith("repos/"))
        if urls is not None:
            urls.append(path)
        return (pull_reads or {}).get(path) or lock_answer(path) or jsonl

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
    assert len(wave_cells(dd.wv, parsed)) == 1  # Not vacuous: a cell exists.
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
    assert sorted((c["environment"], c["plan_run_id"]) for c in wave_cells(dd.wv, parsed)) == [
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
        "::error::apply aborted: no plan-text digest recorded for apply / stacks/app / dev-us: "
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
        "apply / stacks/app / dev-eu, apply / stacks/dns / dev-eu",
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
                    name="apply / stacks/app / dev-us",
                    status="queued",
                    conclusion=None,
                    external_id="b" * 64,
                ),
            ],
        )
    assert str(exc_info.value) == (
        "::error::apply aborted: no plan run recorded for apply / stacks/app / dev-us: the "
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
                    name="apply / stacks/app / dev-eu",
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
        "::error::apply aborted: no plan run recorded for apply / stacks/app / dev-eu: the "
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
            _check(name="apply / stacks/app / dev-us", external_id="b" * 64),
        ],
    )
    assert [c["environment"] for c in wave_cells(dd.wv, parsed)] == ["dev-eu"]


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
            _check(name="apply / stacks/dns / dev-eu"),
        ],
    )
    assert (
        f"::notice title=deploy-detect::base= head={HEAD} cells=2 completed=1 pending=1 "
        "envlevels=[1, 0, 0, 0] empty=False" in capsys.readouterr().out.splitlines()
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
            _check(name="apply / stacks/app / dev-eu", id=2, app={"id": 15368}),
        ],
    )
    assert [c["stack"] for c in wave_cells(dd.wv, parsed)] == ["stacks/app"]


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
        "::notice::2 stacks, 1 after edges, 2 wave levels; 1 stacks would apply concurrently"
        in capsys.readouterr().out.splitlines()
    )


def test_main_prints_the_dag_shape_notice_before_an_over_depth_refusal(
    tmp_path, monkeypatch, capsys
):
    """The notice is the line that explains a run too deep for the pre-declared wave jobs, so it
    prints before `env_level_waves` refuses.

    Mutation: print the DAG-shape notice after `env_level_waves` -- the notice is missing.
    """
    stacks = [f"stacks/s{i}" for i in range(dd.ad.wv.MAX_WAVES + 1)]
    with pytest.raises(SystemExit, match="dependency levels"):
        _run_main(
            tmp_path,
            monkeypatch,
            cells=[_cell(s) for s in stacks],
            checks=[_apply_check(s, cells=9) for s in stacks],
            deps={s: ({stacks[i - 1]} if i else set()) for i, s in enumerate(stacks)},
        )
    assert capsys.readouterr().out.splitlines() == [
        "::notice::9 stacks, 8 after edges, 9 wave levels; 1 stacks would apply concurrently"
    ]


def test_main_takes_the_ordering_map_from_the_loaded_table(tmp_path, monkeypatch):
    """The merge path orders its env-levels from the mapping it already loaded, not from the
    checked-out tree. The assertion is on a populated split, because the broken shape returns
    an empty ordering rather than raising.

    Mutation: replace `main`'s `bm.ec.env_order(table)` with `table.get("env_order", {})`
    -- dev-us joins dev-eu in env-level 0 and envlevel1 is empty.
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
    assert [c["environment"] for c in json.loads(parsed["envlevel0_waves"])["wave0"]] == ["dev-eu"]
    assert [c["environment"] for c in json.loads(parsed["envlevel1_waves"])["wave0"]] == ["dev-us"]


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
    assert [(c["environment"], c["stack"]) for c in wave_cells(dd.wv, parsed)] == [
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
    `refuse_stranded_checks` -- the file stays empty. Mutation: drop `pr_number` from it."""
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
    assert github_outputs(tmp_path / "out.txt") == {"head_sha": HEAD, "pr_number": "7"}


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
                _check(name="apply / stacks/gone / prod"),
            ],
            stacks={"stacks/app": ["env/dev-eu"], "stacks/gone": []},
            deps={"stacks/app": set(), "stacks/gone": set()},
        )
    assert str(exc_info.value) == (
        "::error::deploy aborted: apply / stacks/gone / dev-eu, apply / stacks/gone / dev-us: "
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
        "::error::deploy aborted: apply / stacks/gone / dev-eu: the stack of each lost its "
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
        "::error::deploy aborted: apply / stacks/gone / dev-eu: the stack of each lost its "
        "env/* tag after this pull request was planned, so its reviewed change was not "
        "applied. " + _REMEDY
    )


_PARTIAL_UNTAG_MESSAGE = (
    "::error::deploy aborted: apply / stacks/app / prod: the stack of each lost its env/* tag "
    "after this pull request was planned, so its reviewed change was not applied. This deploy "
    "applies nothing, so these other cells of this merge are not applied either: "
    "stacks/app / dev-eu. " + _REMEDY
)


def test_main_refuses_a_pending_apply_check_on_a_stack_moved_to_local(tmp_path, monkeypatch):
    """A stack retagged from `env/dev-eu` to `shipmate/local` after planning strands its check
    like any untagged stack. Mutation: pass `compute_cells`' fifth element,
    `reported_unmanaged`, to `refuse_stranded_checks` in `main` -- the deploy writes waves."""
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
        "::error::deploy aborted: apply / stacks/gone / dev-eu: the stack of each lost its "
        "env/* tag after this pull request was planned, so its reviewed change was not "
        "applied. " + _REMEDY
    )


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
    assert [c["stack"] for c in wave_cells(dd.wv, parsed)] == ["stacks/app"]


def test_a_name_whose_rest_is_no_env_name_is_not_the_stack_s_check(tmp_path, monkeypatch):
    """`apply / stacks/app / x / dev-eu` is the check of a stack `stacks/app / x`, absent from
    the scan; read against `stacks/app` its rest `x / dev-eu` is no env name. Mutation: drop
    the `is_env_name` check -- the deploy refuses it."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[],
        checks=[
            _apply_check("stacks/app", cells=2),
            _apply_check("stacks/app / x", "dev-eu", cells=2),
        ],
        stacks={"stacks/app": ["env/dev-eu"]},
        deps={"stacks/app": set()},
    )
    assert [c["stack"] for c in wave_cells(dd.wv, parsed)] == ["stacks/app"]


def test_main_deploys_past_an_unmanaged_stack_with_no_open_apply_check(tmp_path, monkeypatch):
    """An unmanaged stack the pull request changed, whose only apply check completed, does not
    refuse the deploy. Mutation: `stranded = sorted(unmanaged)` -- this run raises."""
    parsed = _run_main(
        tmp_path,
        monkeypatch,
        cells=[],
        checks=[_apply_check("stacks/app", cells=1), _check(name="apply / stacks/gone / dev-eu")],
        stacks={"stacks/app": ["env/dev-eu"], "stacks/gone": []},
        deps={"stacks/app": set(), "stacks/gone": set()},
    )
    assert [c["stack"] for c in wave_cells(dd.wv, parsed)] == ["stacks/app"]


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
            checks=[_check(name=f"apply / {c['stack']} / {c['environment']}") for c in _AB_CELLS],
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
    """Mutation: write `head_sha` with the waves only -- the file stays empty."""
    with pytest.raises(SystemExit) as exc:
        _run_probe(tmp_path, monkeypatch, {"dev-us": ["dev-eu"]})
    assert str(exc.value).startswith("::error::needs order inverted")
    assert github_outputs(tmp_path / "out.txt") == {"head_sha": HEAD, "pr_number": "7"}


def test_main_deploys_the_probe_with_no_needs(tmp_path, monkeypatch):
    """With no `needs` every env is level 0, so no `after` edge contradicts one.

    Mutation: `>=` for `>` in `inversions` -- level 0 = level 0 refuses."""
    parsed = _run_probe(tmp_path, monkeypatch, {})
    assert [
        (c["stack"], c["environment"]) for c in json.loads(parsed["envlevel0_waves"])["wave0"]
    ] == [
        ("dns", "dev-us"),
        ("workers", "dev-eu"),
    ]


LOCK_REMEDY = (
    "Open a pull request that changes these stacks, comment `shipmate unlock <env> --evict` on it "
    "for each environment above, then apply it."
)


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
        "refused_locked": "true",
    }


def test_main_deploys_a_cell_the_merged_pull_request_itself_locks(tmp_path, monkeypatch):
    """Mutation: pass `0` instead of the merged pull request's number -- #12's own lock refuses."""
    parsed = _locked_run(tmp_path, monkeypatch, {("stacks/dns", "dev-eu"): 12})
    assert sorted(c["stack"] for c in wave_cells(dd.wv, parsed)) == ["stacks/app", "stacks/dns"]
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
        "stacks/app / dev-eu, stacks/dns / dev-eu. " + LOCK_REMEDY
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
        checks=[_check(name="apply / stacks/app / dev-eu")],
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
    assert github_outputs(tmp_path / "out.txt") == {"head_sha": HEAD, "pr_number": "12"}
