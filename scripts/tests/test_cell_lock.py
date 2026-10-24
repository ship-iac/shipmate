"""Tests for scripts/cell-lock: the per-cell lock refs, their holders and their conditional writes.

The double answers each hand-written argv with the next queued response, so a changed call
shape fails the test rather than reaching a default. A test ends with every queued response
consumed, so a write a test expects cannot silently go missing.
"""

import json

import pytest
from _loader import github_outputs, jsonl, load_script

cl = load_script("cell-lock")
ag = load_script("apply-gate")

REPO = "acme/infra"
PR = 7
ACQ = "900.1"
ZERO = "0" * 40
TREE = "e" * 40
NEW = "a" * 40
OLD = "b" * 40
OTHER = "c" * 40
REPO_ID = "R_kgDOtest"
EARLY = "2026-10-24T10:00:00Z"
LATE = "2026-10-24T12:00:00Z"

#: `cell_key("stacks/auth")`, computed once by `hashlib.sha256(b"stacks/auth").hexdigest()`.
AUTH_KEY = "61d1da342935a43412763af6dfb6fb2b6545eba65f2fd3be1ca875a8a96c355d"
AUTH = {"stack": "stacks/auth", "environment": "dev"}
AUTH_REF = f"refs/shipmate/locks/dev/{AUTH_KEY}"
DNS = {"stack": "stacks/dns", "environment": "dev"}
DNS_REF = cl.ref_name("dev", "stacks/dns")

UPDATE_REFS = (
    "query=mutation($repo: ID!, $name: GitRefname!, $before: GitObjectID!, "
    "$after: GitObjectID!, $force: Boolean!) { updateRefs(input: {repositoryId: $repo, "
    "refUpdates: [{name: $name, beforeOid: $before, afterOid: $after, force: $force}]}) "
    "{ clientMutationId } }"
)
PR_STATE = (
    "query=query($owner: String!, $name: String!, $pr: Int!) { repository(owner: $owner, "
    "name: $name) { pullRequest(number: $pr) { state timelineItems(itemTypes: [CLOSED_EVENT], "
    "last: 1) { nodes { ... on ClosedEvent { createdAt } } } } } }"
)
FAIL = SystemExit("::error::command failed (1): gh api")


class Gh:
    """`run` double: `calls[argv tuple]` is the queue of responses, a str or an exception."""

    def __init__(self):
        self.calls = {}

    def on(self, argv, *responses):
        self.calls.setdefault(tuple(argv), []).extend(responses)
        return self

    def __call__(self, argv):
        queue = self.calls.get(tuple(argv))
        assert queue, f"unexpected call: {argv}"
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def drained(self):
        return {k: v for k, v in self.calls.items() if v} == {}


def message(pr, env, stack, acquisition=ACQ):
    return json.dumps({"pr": pr, "env": env, "stack": stack, "acquisition": acquisition})


def listing(path):
    return ["gh", "api", f"repos/{REPO}/git/matching-refs/{path}"]


def refs(*pairs):
    return json.dumps([{"ref": r, "object": {"sha": s, "type": "commit"}} for r, s in pairs])


def commit_read(sha):
    return ["gh", "api", f"repos/{REPO}/git/commits/{sha}"]


def commit(sha, msg, date=EARLY):
    return json.dumps({"sha": sha, "message": msg, "committer": {"date": date}})


def ref_read(ref):
    return listing(ref.removeprefix("refs/"))


def update(ref, before, after, force):
    return [
        "gh",
        "api",
        "graphql",
        "-f",
        UPDATE_REFS,
        "-f",
        f"repo={REPO_ID}",
        "-f",
        f"name={ref}",
        "-f",
        f"before={before}",
        "-f",
        f"after={after}",
        "-F",
        f"force={'true' if force else 'false'}",
    ]


REPO_ID_READ = ["gh", "api", f"repos/{REPO}", "--jq", ".node_id"]
TREE_WRITE = [
    "gh",
    "api",
    "-X",
    "POST",
    f"repos/{REPO}/git/trees",
    "-f",
    "tree[][path]=lock",
    "-f",
    "tree[][mode]=100644",
    "-f",
    "tree[][type]=blob",
    "-f",
    f"tree[][content]={ACQ}",
]


def commit_write(cell, pr=PR):
    msg = message(pr, cell["environment"], cell["stack"])
    return ["gh", "api", "-X", "POST", f"repos/{REPO}/git/commits", "-f", f"message={msg}"] + [
        "-f",
        f"tree={TREE}",
    ]


def ref_write(ref, sha):
    return ["gh", "api", "-X", "POST", f"repos/{REPO}/git/refs", "-f", f"ref={ref}", "-f"] + [
        f"sha={sha}"
    ]


def lock(ref, sha, pr=PR, env="dev", stack="stacks/auth", date=EARLY):
    return cl.Lock(ref, sha, date, pr, env, stack, None if pr is None else ACQ)


def unknown(ref, sha, date=EARLY):
    return cl.Lock(ref, sha, date, None, None, None, None)


def gh_acquire_start(cell, sha=NEW):
    """A double that has written the tree and `cell`'s commit `sha`."""
    gh = Gh().on(TREE_WRITE, json.dumps({"sha": TREE}))
    msg = message(PR, cell["environment"], cell["stack"])
    return gh.on(commit_write(cell), commit(sha, msg, LATE))


# --- names -------------------------------------------------------------------------------------


def test_cell_key_is_the_full_sha256_of_the_utf8_path():
    """Mutations: hash `stack` without `.encode("utf-8")` (TypeError); cut the digest to 16
    characters."""
    assert cl.cell_key("stacks/auth") == AUTH_KEY


def test_ref_name_is_one_segment_per_part_after_refs():
    """Mutation: use the raw stack path in place of `cell_key` (six segments)."""
    name = cl.ref_name("dev-eu", "stacks/a/b")
    assert name.removeprefix("refs/").split("/") == [
        "shipmate",
        "locks",
        "dev-eu",
        cl.cell_key("stacks/a/b"),
    ]


# --- reading a lock ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        pytest.param(message(PR, "dev", "stacks/auth")[:-1] + ', "x": 1}', id="extra-key"),
        pytest.param(message(str(PR), "dev", "stacks/auth"), id="string-pr"),
        pytest.param(message(True, "dev", "stacks/auth"), id="bool-pr"),
        pytest.param("not json", id="invalid-json"),
        pytest.param(message(PR, "dev", "stacks/dns"), id="other-ref"),
    ],
)
def test_a_message_that_is_not_a_record_of_its_own_ref_reads_as_unknown_holder(msg):
    """Mutations: drop the key-set check (extra-key); drop the `int` check (string-pr) or the
    `bool` exclusion (bool-pr); let `json.loads` raise (invalid-json); drop the
    `ref_name(env, stack) == ref` check (other-ref)."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, msg))
    assert cl.read_locks(REPO, ["dev"], run=gh) == {AUTH_REF: unknown(AUTH_REF, OLD)}
    assert gh.drained()


def test_a_record_round_trips_through_its_message():
    """Mutation: swap `env` and `stack` in the parsed Lock."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(PR, "dev", "stacks/auth") + "\n"))
    assert cl.read_locks(REPO, ["dev"], run=gh) == {AUTH_REF: lock(AUTH_REF, OLD)}
    assert gh.drained()


# --- acquire -------------------------------------------------------------------------------------


def test_a_created_ref_is_acquired():
    """Mutation: return no lock for a 201."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), "{}")
    assert cl.acquire(REPO, [AUTH], PR, ACQ, run=gh) == [lock(AUTH_REF, NEW, date=LATE)]
    assert gh.drained()


def test_a_failed_create_that_reads_back_our_commit_is_acquired():
    """Mutation: refuse whenever the create fails."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, NEW)))
    assert cl.acquire(REPO, [AUTH], PR, ACQ, run=gh) == [lock(AUTH_REF, NEW, date=LATE)]
    assert gh.drained()


def test_our_pull_request_at_another_sha_is_replaced_with_force():
    """Mutations: send `force` false; send our new commit as `beforeOid`."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(PR, "dev", "stacks/auth", "800.1")))
    gh.on(REPO_ID_READ, REPO_ID + "\n").on(update(AUTH_REF, OLD, NEW, True), "{}")
    assert cl.acquire(REPO, [AUTH], PR, ACQ, run=gh) == [lock(AUTH_REF, NEW, date=LATE)]
    assert gh.drained()


def test_another_pull_request_refuses_naming_it_and_rolls_back_this_calls_creates():
    """Mutation: skip the rollback (DNS_REF's conditional delete is never sent)."""
    gh = gh_acquire_start(DNS).on(ref_write(DNS_REF, NEW), "{}")
    msg = message(PR, "dev", "stacks/auth")
    gh.on(commit_write(AUTH), commit(OTHER, msg, LATE)).on(ref_write(AUTH_REF, OTHER), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(REPO_ID_READ, REPO_ID).on(update(DNS_REF, NEW, ZERO, False), "{}")
    with pytest.raises(SystemExit) as exc:
        cl.acquire(REPO, [DNS, AUTH], PR, ACQ, run=gh)
    assert exc.value.code == (
        "::error::`dev` / `stacks/auth` is locked by #12; wait for #12 to merge or close, then "
        "re-plan, or comment `shipmate unlock dev --evict`"
    )
    assert gh.drained()


def test_an_unknown_holder_refuses_naming_the_ref():
    """Mutation: drop the unknown-holder branch (it refuses as `#None`)."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, "garbage"))
    with pytest.raises(SystemExit) as exc:
        cl.acquire(REPO, [AUTH], PR, ACQ, run=gh)
    assert exc.value.code == (
        f"::error::`dev` / `stacks/auth` is locked by an unknown holder ({AUTH_REF}); comment "
        "`shipmate unlock dev --evict` to release it"
    )
    assert gh.drained()


def test_a_failed_create_with_the_ref_absent_refuses():
    """Mutation: treat an absent ref as acquired."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), FAIL)
    gh.on(ref_read(AUTH_REF), "[]")
    with pytest.raises(SystemExit) as exc:
        cl.acquire(REPO, [AUTH], PR, ACQ, run=gh)
    assert exc.value.code == (
        f"::error::could not lock `dev` / `stacks/auth`: creating {AUTH_REF} failed and the ref "
        "is absent"
    )
    assert gh.drained()


def _replace_failing(read_back):
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(PR, "dev", "stacks/auth", "800.1")))
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, NEW, True), FAIL)
    return gh.on(ref_read(AUTH_REF), read_back)


@pytest.mark.parametrize(
    "read_back",
    [
        pytest.param("[]", id="absent"),
        pytest.param(refs((AUTH_REF, OLD)), id="old-sha"),
        pytest.param(refs((AUTH_REF, OTHER)), id="foreign-sha"),
    ],
)
def test_a_failed_replace_refuses_unless_our_commit_reads_back(read_back):
    """Mutation: treat a replace error whose re-read is absent as done (absent goes green)."""
    gh = _replace_failing(read_back)
    with pytest.raises(SystemExit) as exc:
        cl.acquire(REPO, [AUTH], PR, ACQ, run=gh)
    assert exc.value.code == (
        f"::error::could not re-enter the lock on `dev` / `stacks/auth`: replacing {AUTH_REF} "
        "failed"
    )
    assert gh.drained()


def test_a_failed_replace_that_reads_back_our_commit_is_acquired():
    """Mutation: refuse on every replace error."""
    gh = _replace_failing(refs((AUTH_REF, NEW)))
    assert cl.acquire(REPO, [AUTH], PR, ACQ, run=gh) == [lock(AUTH_REF, NEW, date=LATE)]
    assert gh.drained()


# --- fence_open ----------------------------------------------------------------------------------


def _pr_state(state, closed_at=None):
    nodes = [{"createdAt": closed_at}] if closed_at else []
    return json.dumps({"state": state, "timelineItems": {"nodes": nodes}})


PR_STATE_READ = [
    "gh",
    "api",
    "graphql",
    "-f",
    PR_STATE,
    "-f",
    "owner=acme",
    "-f",
    "name=infra",
    "-F",
    f"pr={PR}",
    "--jq",
    ".data.repository.pullRequest",
]


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(_pr_state("CLOSED", "2026-10-24T11:00:00Z"), id="closed"),
        pytest.param(_pr_state("OPEN", "2026-10-24T11:00:00Z"), id="reopened-after-commit"),
        pytest.param(_pr_state("OPEN", "2026-10-24T10:00:00Z"), id="closed-same-second"),
    ],
)
def test_the_fence_refuses_and_deletes_every_installed_lock(state):
    """Mutations: check `state` only (reopened-after-commit passes); compare the close `<=` the
    commit (closed-same-second passes); raise without the rollback (no delete is sent)."""
    installed = [lock(AUTH_REF, NEW), lock(DNS_REF, OTHER, stack="stacks/dns")]
    gh = Gh().on(PR_STATE_READ, state).on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, NEW, ZERO, False), "{}").on(update(DNS_REF, OTHER, ZERO, False), "{}")
    with pytest.raises(SystemExit) as exc:
        cl.fence_open(REPO, PR, installed, run=gh)
    assert str(exc.value.code).startswith(f"::error::pull request #{PR} was closed")
    assert gh.drained()


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(_pr_state("OPEN"), id="never-closed"),
        pytest.param(_pr_state("OPEN", "2026-10-24T09:59:59Z"), id="closed-earlier"),
    ],
)
def test_the_fence_passes_an_open_pull_request_with_no_later_close(state):
    """Mutation: compare the close `>` the commit (closed-earlier refuses)."""
    gh = Gh().on(PR_STATE_READ, state)
    cl.fence_open(REPO, PR, [lock(AUTH_REF, NEW)], run=gh)
    assert gh.drained()


# --- foreign, evict and release over an unknown holder -------------------------------------------


def test_foreign_excludes_our_own_locks_and_includes_an_unknown_holder():
    """Mutations: report our own lock; skip a lock whose `pr` is None."""
    other = {"stack": "stacks/other", "environment": "dev"}
    free = {"stack": "stacks/free", "environment": "dev"}
    locks = {
        AUTH_REF: lock(AUTH_REF, OLD),
        DNS_REF: unknown(DNS_REF, OLD),
        cl.ref_name("dev", "stacks/other"): lock(
            cl.ref_name("dev", "stacks/other"), OLD, pr=12, stack="stacks/other"
        ),
    }
    assert cl.foreign(locks, [AUTH, DNS, other, free], PR) == [
        {"stack": "stacks/dns", "environment": "dev", "holder": None},
        {"stack": "stacks/other", "environment": "dev", "holder": 12},
    ]


def test_evict_removes_another_holder_and_an_unknown_one_in_stacks_only():
    """Mutations: evict the commenter's own lock; skip an unknown holder; ignore `stacks`."""
    own_ref = cl.ref_name("dev", "stacks/own")
    out_ref = cl.ref_name("dev", "stacks/out")
    gh = Gh().on(
        listing("shipmate/locks/dev/"),
        refs((AUTH_REF, OLD), (DNS_REF, OTHER), (own_ref, NEW), (out_ref, TREE)),
    )
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, "garbage"))
    gh.on(commit_read(NEW), commit(NEW, message(PR, "dev", "stacks/own")))
    gh.on(commit_read(TREE), commit(TREE, message(12, "dev", "stacks/out")))
    gh.on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, OLD, ZERO, False), "{}").on(update(DNS_REF, OTHER, ZERO, False), "{}")
    stacks = {"stacks/auth", "stacks/dns", "stacks/own"}
    assert cl.evict(REPO, "dev", PR, stacks, run=gh) == [
        lock(AUTH_REF, OLD, pr=12),
        unknown(DNS_REF, OTHER),
    ]
    assert gh.drained()


def test_evict_stacks_round_trip_through_check_name():
    """Mutations: drop the ` / <env>` strip (a stack keeps its env); accept another env's
    name."""
    names = {
        "apply / stacks/a / dev",
        "apply / stacks/a / dev / x / dev",
        "apply / stacks/b / dev-eu",
    }
    stacks = cl.evict_stacks(names, "dev")
    assert stacks == {"stacks/a", "stacks/a / dev / x"}
    assert {ag.check_name(s, "dev") for s in stacks} == names - {"apply / stacks/b / dev-eu"}


def _all_locks(*entries):
    """A double listing every environment's locks: `entries` of (ref, sha, message, date)."""
    gh = Gh().on(listing("shipmate/locks/"), refs(*[(r, s) for r, s, _, _ in entries]))
    for _, sha, msg, date in entries:
        gh.on(commit_read(sha), commit(sha, msg, date))
    return gh


def test_release_merged_keeps_only_our_pending_locks():
    """Mutations: keep a lock whose name is absent from `pending` (the dropped cell); release
    another pull request's lock; release a pending one."""
    pending_ref = cl.ref_name("prod", "stacks/app")
    dropped_ref = cl.ref_name("dev", "stacks/dropped")
    gh = _all_locks(
        (AUTH_REF, OLD, message(PR, "dev", "stacks/auth"), EARLY),
        (pending_ref, NEW, message(PR, "prod", "stacks/app"), EARLY),
        (dropped_ref, OTHER, message(PR, "dev", "stacks/dropped"), EARLY),
        (DNS_REF, TREE, message(12, "dev", "stacks/dns"), EARLY),
        (cl.ref_name("dev", "stacks/x"), ZERO[:-1] + "1", "garbage", EARLY),
    )
    gh.on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, OLD, ZERO, False), "{}")
    gh.on(update(dropped_ref, OTHER, ZERO, False), "{}")
    gone = cl.release(REPO, PR, pending={"apply / stacks/app / prod"}, run=gh)
    assert [g.ref for g in gone] == [AUTH_REF, dropped_ref]
    assert gh.drained()


def test_release_unmerged_deletes_only_locks_committed_by_the_close():
    """Mutations: drop the date comparison (the later lock is deleted); compare `<` (the
    same-second lock is kept); release an unknown holder."""
    later_ref = cl.ref_name("dev", "stacks/later")
    gh = _all_locks(
        (AUTH_REF, OLD, message(PR, "dev", "stacks/auth"), EARLY),
        (DNS_REF, NEW, message(PR, "dev", "stacks/dns"), "2026-10-24T11:00:00Z"),
        (later_ref, OTHER, message(PR, "dev", "stacks/later"), LATE),
        (cl.ref_name("dev", "stacks/x"), TREE, "garbage", EARLY),
    )
    gh.on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, OLD, ZERO, False), "{}").on(update(DNS_REF, NEW, ZERO, False), "{}")
    gone = cl.release(REPO, PR, closed_at="2026-10-24T11:00:00Z", run=gh)
    assert [g.ref for g in gone] == [AUTH_REF, DNS_REF]
    assert gh.drained()


def test_release_takes_exactly_one_mode():
    """Mutation: accept both modes at once."""
    with pytest.raises(ValueError):
        cl.release(REPO, PR, pending=set(), closed_at=EARLY, run=Gh())
    with pytest.raises(ValueError):
        cl.release(REPO, PR, run=Gh())


# --- delete --------------------------------------------------------------------------------------


def test_a_failed_delete_that_reads_back_the_same_sha_refuses():
    """Mutation: return False on every delete error."""
    gh = Gh().on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    with pytest.raises(SystemExit) as exc:
        cl.delete(REPO, lock(AUTH_REF, OLD), run=gh)
    assert exc.value.code == (
        f"::error::could not release the cell lock {AUTH_REF} of `dev` / `stacks/auth`; it is "
        f"still held at {OLD}"
    )
    assert gh.drained()


@pytest.mark.parametrize(
    "read_back",
    [pytest.param("[]", id="absent"), pytest.param(refs((AUTH_REF, OTHER)), id="other-sha")],
)
def test_a_failed_delete_of_a_lock_that_moved_on_is_not_ours(read_back):
    """Mutation: refuse on every delete error."""
    gh = Gh().on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), FAIL)
    gh.on(ref_read(AUTH_REF), read_back)
    assert cl.delete(REPO, lock(AUTH_REF, OLD), run=gh) is False
    assert gh.drained()


def test_a_delete_that_succeeds_returns_true():
    """Mutation: return False when `updateRefs` succeeds."""
    gh = Gh().on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), "{}")
    assert cl.delete(REPO, lock(AUTH_REF, OLD), run=gh) is True
    assert gh.drained()


# --- CLI -----------------------------------------------------------------------------------------


def _check(name, status, conclusion, run_id=1, app=99):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "id": run_id,
        "app": {"id": app},
    }


def test_pending_names_are_app_apply_checks_not_done():
    """Mutations: drop the `from_app` filter (the foreign check counts); count every name as
    pending."""
    lines = jsonl(
        _check("apply / stacks/a / dev", "completed", "success"),
        _check("apply / stacks/b / dev", "queued", None),
        _check("apply / stacks/c / dev", "completed", "failure"),
        _check("apply / stacks/d / dev", "queued", None, app=15368),
    ).splitlines()
    assert cl.pending_names(lines, "99") == {"apply / stacks/b / dev", "apply / stacks/c / dev"}


def test_marks_writes_each_foreign_holder_per_cell(monkeypatch, tmp_path):
    """Mutation: key the output by the ref in place of `<stack> / <env>`."""
    out = tmp_path / "out"
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (DNS_REF, OTHER)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, message(PR, "dev", "stacks/dns")))
    for key, value in {
        "GITHUB_OUTPUT": str(out),
        "GITHUB_REPOSITORY": REPO,
        "SHIPMATE_PR_NUMBER": str(PR),
        "SHIPMATE_CELLS": json.dumps([AUTH, DNS]),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cl, "_run", gh)
    monkeypatch.setattr(cl.sys, "argv", ["cell-lock", "marks"])
    cl.main()
    assert github_outputs(out) == {"locks": json.dumps({"stacks/auth / dev": 12})}
    assert gh.drained()
