"""Tests for scripts/cell-lock: the per-cell lock refs, their holders and their conditional writes.

The double answers each hand-written argv with the next queued response, so a changed call
shape fails the test rather than reaching a default. A test ends with every queued response
consumed, so a write a test expects cannot silently go missing.
"""

import json

import _shipmate
import pytest
from _detect_fixtures import autoplans_read, listed_autoplan
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
    "name: $name) { pullRequest(number: $pr) { state headRefOid timelineItems(itemTypes: "
    "[CLOSED_EVENT], last: 1) { nodes { ... on ClosedEvent { createdAt } } } } } }"
)
FAIL = SystemExit("::error::command failed (1): gh api")


class Gh:
    """`run` double: `calls[argv tuple]` is the queue of responses, a str or an exception."""

    def __init__(self):
        self.calls = {}
        self.log = []

    def on(self, argv, *responses):
        self.calls.setdefault(tuple(argv), []).extend(responses)
        return self

    def __call__(self, argv):
        self.log.append(list(argv))
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


def test_a_ref_that_targets_no_commit_refuses_naming_the_manual_remedy():
    """Mutation: drop the `object.type` check (the blob's sha is read as a commit)."""
    listed = [{"ref": AUTH_REF, "object": {"sha": OLD, "type": "blob"}}]
    gh = Gh().on(listing("shipmate/locks/dev/"), json.dumps(listed))
    with pytest.raises(SystemExit) as exc:
        cl.read_locks(REPO, ["dev"], run=gh)
    assert exc.value.code == (
        f"::error::the cell lock ref {AUTH_REF} targets a blob, not a commit; someone with write "
        f"access must delete it: git push origin :{AUTH_REF}"
    )
    assert gh.drained()


# --- acquire -------------------------------------------------------------------------------------


def test_a_created_ref_is_acquired():
    """Mutation: return no lock for a 201."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), "{}").on(PR_STATE_READ, OPEN)
    assert cl.acquire(REPO, [AUTH], PR, ACQ, run=gh) == [lock(AUTH_REF, NEW, date=LATE)]
    assert gh.drained()


def test_a_failed_create_that_reads_back_our_commit_is_acquired():
    """Mutation: refuse whenever the create fails."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, NEW))).on(PR_STATE_READ, OPEN)
    assert cl.acquire(REPO, [AUTH], PR, ACQ, run=gh) == [lock(AUTH_REF, NEW, date=LATE)]
    assert gh.drained()


def test_our_pull_request_at_another_sha_is_replaced_with_force():
    """Mutations: send `force` false; send our new commit as `beforeOid`."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(PR, "dev", "stacks/auth", "800.1")))
    gh.on(REPO_ID_READ, REPO_ID + "\n").on(update(AUTH_REF, OLD, NEW, True), "{}")
    gh.on(PR_STATE_READ, OPEN)
    assert cl.acquire(REPO, [AUTH], PR, ACQ, run=gh) == [lock(AUTH_REF, NEW, date=LATE)]
    assert gh.drained()


def test_another_pull_request_refuses_naming_it_and_rolls_back_this_calls_creates():
    """The refusal is pinned whole. Mutations: skip the rollback (DNS_REF's conditional delete
    is never sent); drop the merged-or-closed sentence from `lock_wait_remedy`."""
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
        "re-plan. If #12 has already merged or closed, comment `shipmate unlock dev --evict` once "
        "#12's apply, auto and deploy runs have ended"
    )
    assert gh.drained()


def test_a_malformed_response_refuses_and_rolls_back_this_calls_creates():
    """Mutation: catch only `SystemExit` around the install loop (the KeyError escapes and
    DNS_REF's conditional delete is never sent)."""
    gh = gh_acquire_start(DNS).on(ref_write(DNS_REF, NEW), "{}")
    gh.on(commit_write(AUTH), "{}")
    gh.on(REPO_ID_READ, REPO_ID).on(update(DNS_REF, NEW, ZERO, False), "{}")
    with pytest.raises(SystemExit) as exc:
        cl.acquire(REPO, [DNS, AUTH], PR, ACQ, run=gh)
    assert exc.value.code == (
        "::error::could not lock the cells: an API response was malformed (KeyError('sha'))"
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
    gh = _replace_failing(refs((AUTH_REF, NEW))).on(PR_STATE_READ, OPEN)
    assert cl.acquire(REPO, [AUTH], PR, ACQ, run=gh) == [lock(AUTH_REF, NEW, date=LATE)]
    assert gh.drained()


# --- fence_open ----------------------------------------------------------------------------------


MERGED_HEAD = "e" * 40


def _pr_state(state, closed_at=None, head=MERGED_HEAD):
    nodes = [{"createdAt": closed_at}] if closed_at else []
    return json.dumps({"state": state, "headRefOid": head, "timelineItems": {"nodes": nodes}})


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
OPEN = _pr_state("OPEN")
BAD_GATEWAY = SystemExit("::error::command failed (1): gh api x\ngh: Bad Gateway (HTTP 502)")


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr("_shipmate._sleep", slept.append)
    return slept


def test_repo_id_retries_a_transient_failure(sleeps):
    """The double serves both answers from one argv. Mutation: call `run([...])` without
    `retried` -- the 502 raises."""
    gh = Gh().on(REPO_ID_READ, BAD_GATEWAY, REPO_ID + "\n")
    assert (cl._repo_id(REPO, gh), gh.drained(), sleeps) == (REPO_ID, True, [2])


def test_repo_id_reads_again_after_a_failure(sleeps):
    """A failure is not cached; the success is. Mutations, each run: catch the `SystemExit`
    inside `_repo_id` and return it -- the first call returns it; drop `@functools.cache` -- the
    third call is an unexpected read."""
    gh = Gh().on(REPO_ID_READ, BAD_GATEWAY, BAD_GATEWAY, BAD_GATEWAY, REPO_ID)
    with pytest.raises(SystemExit):
        cl._repo_id(REPO, gh)
    assert (cl._repo_id(REPO, gh), cl._repo_id(REPO, gh), gh.drained()) == (REPO_ID, REPO_ID, True)


TWO_LOCKS = [lock(AUTH_REF, OLD), lock(DNS_REF, OTHER, stack="stacks/dns")]


def test_delete_each_reads_the_repository_id_once_for_every_lock(sleeps):
    """A persistent 502 costs one retried read, not one per lock. Mutation: drop the pre-loop
    `_repo_id` call -- the second lock's delete reads again and the double refuses it."""
    gh = Gh().on(REPO_ID_READ, BAD_GATEWAY, BAD_GATEWAY, BAD_GATEWAY)
    with pytest.raises(SystemExit) as exc:
        cl._delete_each(REPO, TWO_LOCKS, gh)
    assert (exc.value.code, sleeps, gh.drained()) == (BAD_GATEWAY.code, [2, 5], True)


def test_rollback_reads_the_repository_id_once_for_every_lock(sleeps):
    """The read's failure joins `reason` and no delete is sent. Mutation: drop the pre-loop
    `_repo_id` read -- the second lock's delete reads again and the double refuses it."""
    gh = Gh().on(REPO_ID_READ, BAD_GATEWAY, BAD_GATEWAY, BAD_GATEWAY)
    assert (cl._rollback(REPO, TWO_LOCKS, "::error::why", gh), sleeps, gh.drained()) == (
        f"::error::why\n{BAD_GATEWAY.code}",
        [2, 5],
        True,
    )


def test_pr_state_retries_a_transient_failure(sleeps):
    """Mutation: call `run([...])` without `retried` -- the read refuses."""
    gh = Gh().on(PR_STATE_READ, BAD_GATEWAY, OPEN)
    assert (cl._pr_state(REPO, PR, gh), gh.drained(), sleeps) == (
        ("OPEN", [], MERGED_HEAD),
        True,
        [2],
    )


@pytest.mark.parametrize(
    ("state", "closed"),
    [
        pytest.param("CLOSED", "2026-10-24T11:00:00", id="closed"),
        pytest.param("OPEN", "2026-10-24T11:00:00", id="reopened-after-commit"),
        pytest.param("OPEN", "2026-10-24T10:00:00", id="closed-same-second"),
    ],
)
def test_the_fence_refuses_and_deletes_every_installed_lock(state, closed):
    """Mutations: check `state` only (reopened-after-commit passes); compare the close `<=` the
    commit (closed-same-second passes); raise without the rollback (no delete is sent)."""
    installed = [lock(AUTH_REF, NEW), lock(DNS_REF, OTHER, stack="stacks/dns")]
    gh = Gh().on(PR_STATE_READ, _pr_state(state, closed + "Z")).on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, NEW, ZERO, False), "{}").on(update(DNS_REF, OTHER, ZERO, False), "{}")
    with pytest.raises(SystemExit) as exc:
        cl.fence_open(REPO, PR, installed, [], run=gh)
    assert exc.value.code == (
        "::error::pull request #7 was closed while this run acquired its cell locks (state "
        f"{state}, last closed {closed}+00:00); the locks it took are released"
    )
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
    cl.fence_open(REPO, PR, [lock(AUTH_REF, NEW)], [], run=gh)
    assert gh.drained()


HELD = "d" * 40
DNS_REFUSAL = (
    "::error::`dev` / `stacks/dns` is locked by #12; wait for #12 to merge or close, then "
    "re-plan. If #12 has already merged or closed, comment `shipmate unlock dev --evict` once "
    "#12's apply, auto and deploy runs have ended"
)


def _reenter_then_refuse(state):
    """AUTH re-entered (OLD replaced by NEW at LATE), then DNS refused as #12's lock."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(PR, "dev", "stacks/auth", "800.1")))
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, NEW, True), "{}")
    gh.on(commit_write(DNS), commit(OTHER, message(PR, "dev", "stacks/dns"), LATE))
    gh.on(ref_write(DNS_REF, OTHER), FAIL).on(ref_read(DNS_REF), refs((DNS_REF, HELD)))
    gh.on(commit_read(HELD), commit(HELD, message(12, "dev", "stacks/dns")))
    return gh.on(PR_STATE_READ, state)


def test_a_refusal_fences_a_lock_reentered_after_a_close():
    """Mutation: drop the `fence_open` call from `acquire`'s refusal path (AUTH_REF's
    conditional delete is never sent and the refusal names no fence)."""
    gh = _reenter_then_refuse(_pr_state("CLOSED", "2026-10-24T11:00:00Z"))
    gh.on(update(AUTH_REF, NEW, ZERO, False), "{}")
    with pytest.raises(SystemExit) as exc:
        cl.acquire(REPO, [AUTH, DNS], PR, ACQ, run=gh)
    assert exc.value.code == (
        f"{DNS_REFUSAL}\n::error::pull request #7 was closed while this run acquired its cell "
        "locks (state CLOSED, last closed 2026-10-24T11:00:00+00:00); the locks it took are "
        "released"
    )
    assert gh.drained()


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        pytest.param(FAIL, "::error::command failed (1): gh api", id="read-fails"),
        pytest.param('{"state": "OPEN"}', "'timelineItems'", id="malformed"),
        pytest.param(_pr_state(None), "state None", id="no-state"),
    ],
)
def test_an_unreadable_state_keeps_reentered_locks_and_releases_created_ones(answer, why):
    """AUTH re-entered, DNS created, then the state read fails: only DNS is rolled back.

    Mutations: treat an unreadable state as closed in `fence_open` (AUTH_REF's delete is an
    unexpected call); read any `state` but `OPEN` as closed in `_pr_state` (no-state deletes
    AUTH_REF); skip the rollback of `created` (DNS_REF's delete is never sent)."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(PR, "dev", "stacks/auth", "800.1")))
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, NEW, True), "{}")
    gh.on(commit_write(DNS), commit(OTHER, message(PR, "dev", "stacks/dns"), LATE))
    gh.on(ref_write(DNS_REF, OTHER), "{}").on(PR_STATE_READ, answer)
    gh.on(update(DNS_REF, OTHER, ZERO, False), "{}")
    with pytest.raises(SystemExit) as exc:
        cl.acquire(REPO, [AUTH, DNS], PR, ACQ, run=gh)
    assert exc.value.code == (
        f"::error::the state of pull request #7 could not be read ({why}); the cell locks this "
        "run created are released and those it re-entered are kept"
    )
    assert gh.drained()


def test_a_refusal_with_an_unreadable_state_keeps_the_reentered_lock():
    """AUTH re-entered, DNS refused as #12's lock, then the state read fails.

    Mutation: treat an unreadable state as closed in `fence_open` (AUTH_REF's delete is an
    unexpected call)."""
    gh = _reenter_then_refuse(FAIL)
    with pytest.raises(SystemExit) as exc:
        cl.acquire(REPO, [AUTH, DNS], PR, ACQ, run=gh)
    assert exc.value.code == (
        f"{DNS_REFUSAL}\n::error::the state of pull request #7 could not be read (::error::"
        "command failed (1): gh api); the cell locks this run created are released and those "
        "it re-entered are kept"
    )
    assert gh.drained()


def test_a_refusal_keeps_a_reentered_lock_of_an_open_pull_request():
    """Mutation: roll back every installed lock on a refusal (AUTH_REF's delete is an
    unexpected call)."""
    gh = _reenter_then_refuse(_pr_state("OPEN", "2026-10-24T09:00:00Z"))
    with pytest.raises(SystemExit) as exc:
        cl.acquire(REPO, [AUTH, DNS], PR, ACQ, run=gh)
    assert exc.value.code == DNS_REFUSAL
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
    quiet(gh.on(pull_read(12), pull("main")), 12).on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, OLD, ZERO, False), "{}").on(update(DNS_REF, OTHER, ZERO, False), "{}")
    stacks = {"stacks/auth", "stacks/dns", "stacks/own"}
    assert cl.evict(REPO, "dev", PR, stacks, "feat-a", "main", run=gh) == (
        [lock(AUTH_REF, OLD, pr=12), unknown(DNS_REF, OTHER)],
        [],
    )
    assert gh.drained()


def _two_foreign_locks_first_refused():
    """Two locks of #12; the first delete fails and the ref still holds the sha read."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (DNS_REF, OTHER)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, message(12, "dev", "stacks/dns")))
    quiet(gh.on(pull_read(12), pull("main")), 12)
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(update(DNS_REF, OTHER, ZERO, False), "{}")
    return gh


_AUTH_REFUSAL = (
    f"::error::could not release the cell lock {AUTH_REF} of `dev` / `stacks/auth`; it is "
    f"still held at {OLD}"
)


def test_evict_continues_past_a_refused_delete():
    """A lock deleted after a refusal is still reported, so its holder is still told.

    Mutation: re-raise the refusal in `evict` (the second lock is never deleted)."""
    gh = _two_foreign_locks_first_refused()
    assert cl.evict(REPO, "dev", PR, {"stacks/auth", "stacks/dns"}, "feat-a", "main", run=gh) == (
        [lock(DNS_REF, OTHER, pr=12, stack="stacks/dns")],
        [(lock(AUTH_REF, OLD, pr=12), _AUTH_REFUSAL)],
    )
    assert gh.drained()


def test_evict_cli_writes_both_sets_then_refuses(monkeypatch, tmp_path):
    """Mutations: write `evicted` only after the refusal check (no output survives); drop the
    final refusal (the step reads green)."""
    gh = Gh().on(
        CHECK_RUNS_READ,
        jsonl(
            _check("apply / stacks/auth / dev", "queued", None),
            _check("apply / stacks/dns / dev", "queued", None, run_id=2),
        ),
    )
    for argv, answers in _two_foreign_locks_first_refused().calls.items():
        gh.on(list(argv), *answers)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV)
    assert exc.value.code == _AUTH_REFUSAL
    assert github_outputs(out) == {
        "evicted": json.dumps([{"stack": "stacks/dns", "environment": "dev", "holder": 12}]),
        "refused": json.dumps([{"stack": "stacks/auth", "environment": "dev", "holder": 12}]),
    }
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
    gone, refused = cl.release(REPO, PR, pending={"apply / stacks/app / prod"}, run=gh)
    assert ([g.ref for g in gone], refused) == ([AUTH_REF, dropped_ref], [])
    assert gh.drained()


LATER_REF = cl.ref_name("dev", "stacks/later")


def _unmerged(state):
    """Three of our locks, committed before, at and after the 11:00 close, an unknown holder,
    then `state` answering the state read."""
    gh = _all_locks(
        (AUTH_REF, OLD, message(PR, "dev", "stacks/auth"), EARLY),
        (DNS_REF, NEW, message(PR, "dev", "stacks/dns"), "2026-10-24T11:00:00Z"),
        (LATER_REF, OTHER, message(PR, "dev", "stacks/later"), LATE),
        (cl.ref_name("dev", "stacks/x"), TREE, "garbage", EARLY),
    )
    gh.on(PR_STATE_READ, state).on(REPO_ID_READ, REPO_ID)
    return gh.on(update(AUTH_REF, OLD, ZERO, False), "{}").on(
        update(DNS_REF, NEW, ZERO, False), "{}"
    )


def test_release_unmerged_of_a_reopened_pull_request_deletes_only_locks_by_the_close():
    """Mutations: drop the date comparison (the later lock is deleted); compare `<` (the
    same-second lock is kept); release an unknown holder."""
    gh = _unmerged(_pr_state("OPEN", "2026-10-24T11:00:00Z"))
    gone, refused = cl.release(REPO, PR, closed_at="2026-10-24T11:00:00Z", run=gh)
    assert ([g.ref for g in gone], refused) == ([AUTH_REF, DNS_REF], [])
    assert gh.drained()


def test_release_unmerged_of_a_closed_pull_request_deletes_every_lock():
    """A lock the server dated after the close still goes: git's clock and the API's can
    disagree. Mutation: apply the date fence whatever the state (the later lock is kept)."""
    gh = _unmerged(_pr_state("CLOSED", "2026-10-24T11:00:00Z"))
    gh.on(update(LATER_REF, OTHER, ZERO, False), "{}")
    gone, refused = cl.release(REPO, PR, closed_at="2026-10-24T11:00:00Z", run=gh)
    assert ([g.ref for g in gone], refused) == ([AUTH_REF, DNS_REF, LATER_REF], [])
    assert gh.drained()


def test_release_unmerged_of_a_since_merged_pull_request_keeps_its_pending_locks(monkeypatch):
    """Closed at HEAD, reopened, pushed MERGED_HEAD and merged: the early lock's apply check
    is pending on MERGED_HEAD, so the merge rule keeps it. Mutations: treat MERGED as CLOSED
    (the pending lock is deleted); treat MERGED as OPEN (the pending lock is deleted and the
    later one kept); read the checks of `SHIPMATE_HEAD_SHA` (HEAD's checks are never served)."""
    monkeypatch.setenv("SHIPMATE_HEAD_SHA", HEAD)
    monkeypatch.setenv("SHIPMATE_APP_ID", "99")
    gh = _all_locks(
        (AUTH_REF, OLD, message(PR, "dev", "stacks/auth"), EARLY),
        (DNS_REF, NEW, message(PR, "dev", "stacks/dns"), EARLY),
        (LATER_REF, OTHER, message(PR, "dev", "stacks/later"), LATE),
    )
    gh.on(PR_STATE_READ, _pr_state("MERGED", "2026-10-24T11:00:00Z")).on(REPO_ID_READ, REPO_ID)
    gh.on(
        [a.replace(HEAD, MERGED_HEAD) for a in CHECK_RUNS_READ],
        jsonl(
            _check("apply / stacks/auth / dev", "queued", None),
            _check("apply / stacks/dns / dev", "completed", "success", run_id=2),
        ),
    )
    gh.on(update(DNS_REF, NEW, ZERO, False), "{}").on(update(LATER_REF, OTHER, ZERO, False), "{}")
    gone, refused = cl.release(REPO, PR, closed_at="2026-10-24T11:00:00Z", run=gh)
    assert ([g.ref for g in gone], refused) == ([DNS_REF, LATER_REF], [])
    assert gh.drained()


def test_release_unmerged_with_an_unreadable_state_refuses_before_any_delete():
    """Mutation: read an unreadable state as closed (every lock is deleted)."""
    gh = _all_locks((AUTH_REF, OLD, message(PR, "dev", "stacks/auth"), EARLY))
    gh.on(PR_STATE_READ, FAIL)
    with pytest.raises(SystemExit) as exc:
        cl.release(REPO, PR, closed_at="2026-10-24T11:00:00Z", run=gh)
    assert exc.value.code == (
        "::error::the state of pull request #7 could not be read (::error::command failed "
        "(1): gh api)"
    )
    assert gh.drained()


def test_release_continues_past_a_refused_delete():
    """Mutation: let the refusal escape `release` (the second lock is never deleted)."""
    gh = _all_locks(
        (AUTH_REF, OLD, message(PR, "dev", "stacks/auth"), EARLY),
        (DNS_REF, OTHER, message(PR, "dev", "stacks/dns"), EARLY),
    )
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(update(DNS_REF, OTHER, ZERO, False), "{}")
    assert cl.release(REPO, PR, pending=set(), run=gh) == (
        [lock(DNS_REF, OTHER, stack="stacks/dns")],
        [(lock(AUTH_REF, OLD), _AUTH_REFUSAL)],
    )
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
    deleted = cl.delete(REPO, lock(AUTH_REF, OLD), run=gh)
    assert deleted is False
    assert gh.drained()


def test_a_delete_that_succeeds_returns_true():
    """Mutation: return False when `updateRefs` succeeds."""
    gh = Gh().on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), "{}")
    deleted = cl.delete(REPO, lock(AUTH_REF, OLD), run=gh)
    assert deleted is True
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
    assert github_outputs(out) == {
        "locks": json.dumps({"stacks/auth / dev": 12}),
        "stacked": "{}",
        "stacked_unknown": "{}",
        "blocking": "[]",
    }
    assert gh.drained()


APP = {"stack": "stacks/app", "environment": "dev"}
APP_REF = cl.ref_name("dev", "stacks/app")


def pull_read(number):
    return ["gh", "api", f"repos/{REPO}/pulls/{number}"]


def pull(base_ref, state="open", merged_at=None, merge_sha=None):
    return json.dumps(
        {
            "state": state,
            "base": {"ref": base_ref, "sha": "feat-a"},
            "merged_at": merged_at,
            "merge_commit_sha": merge_sha,
        }
    )


#: `_shipmate.LIVE_RUN_STATUSES`, hand-written.
STATUSES = ("queued", "in_progress", "waiting", "pending", "requested")
RUNS = f"repos/{REPO}/actions/workflows/shipmate.yml/runs"
NO_RUNS = json.dumps({"workflow_runs": []})
MERGE = "9" * 40


def dispatch_listing(status):
    return ["gh", "api", f"{RUNS}?event=workflow_dispatch&status={status}&per_page=100&page=1"]


def push_listing(sha):
    return ["gh", "api", f"{RUNS}?event=push&head_sha={sha}&per_page=100&page=1"]


def quiet(gh, *holders):
    """Queue empty live-run listings and an unmerged fresh read of each of `holders`."""
    for status in STATUSES:
        gh.on(dispatch_listing(status), NO_RUNS)
    for holder in holders:
        gh.on(pull_read(holder), pull("main"))
    return gh


def list_read(branch, page=1):
    """The walk's listing of the pull requests whose head is `branch`, hand-written."""
    return [
        "gh",
        "api",
        f"repos/{REPO}/pulls?state=all&head=acme%3A{branch}&per_page=100&page={page}",
    ]


def entry(number, head, base, state="open", merged_at=None, repo=REPO):
    """A pull request as the list endpoint answers it: no `merged` field."""
    head_repo = None if repo is None else {"full_name": repo}
    return {
        "number": number,
        "state": state,
        "merged_at": merged_at,
        "head": {"ref": head, "repo": head_repo},
        "base": {"ref": base},
    }


def entries(*items):
    return json.dumps(list(items))


def _marks(monkeypatch, tmp_path, gh, cells, head_ref="feat-a", default_branch="main"):
    out = tmp_path / "out"
    for key, value in {
        "GITHUB_OUTPUT": str(out),
        "GITHUB_REPOSITORY": REPO,
        "SHIPMATE_PR_NUMBER": str(PR),
        "SHIPMATE_CELLS": json.dumps(cells),
        "SHIPMATE_HEAD_REF": head_ref,
        "SHIPMATE_DEFAULT_BRANCH": default_branch,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cl, "_run", gh)
    monkeypatch.setattr(cl.sys, "argv", ["cell-lock", "marks"])
    cl.main()
    return github_outputs(out)


def _three_holders():
    """Locks of #12 on auth, #9 on dns and #5 on app."""
    gh = Gh().on(
        listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (DNS_REF, OTHER), (APP_REF, NEW))
    )
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, message(9, "dev", "stacks/dns")))
    return gh.on(commit_read(NEW), commit(NEW, message(5, "dev", "stacks/app")))


def test_marks_writes_the_stacked_holders_and_the_unknown_ones(monkeypatch, tmp_path, capsys):
    """#12 is based on this head, #9 on `b`, whose #11 is based on this head, and #5's walk
    fails. The base `sha` holds the head branch's name too, so comparing it in place of `ref`
    is observable. Mutations: compare `base.sha` to the head ref -- #9 reads direct, red; write
    the holders as a list -- red; drop `stacked_unknown` or `blocking` -- red."""
    gh = _three_holders()
    gh.on(pull_read(12), pull("feat-a")).on(pull_read(9), pull("b")).on(pull_read(5), pull("c"))
    gh.on(list_read("b"), entries(entry(11, "b", "c"), entry(10, "b", "feat-a")))
    gh.on(list_read("c"), SystemExit("::error::command failed (1): gh api\nHTTP 502"))
    assert _marks(monkeypatch, tmp_path, gh, [AUTH, DNS, APP]) == {
        "locks": json.dumps(
            {"stacks/auth / dev": 12, "stacks/dns / dev": 9, "stacks/app / dev": 5}
        ),
        "stacked": '{"9": [10], "12": []}',
        "stacked_unknown": '{"5": "command failed (1): gh api | HTTP 502"}',
        "blocking": "[9, 12]",
    }
    assert gh.drained()
    assert capsys.readouterr().out == (
        "::warning::could not tell whether #5 is stacked on this pull request (command failed "
        "(1): gh api | HTTP 502); the plan comment says so\n"
    )


def test_marks_stops_at_the_default_branch_it_is_given(monkeypatch, tmp_path):
    """#12 is based on `trunk`, the default branch here: no listing (the double refuses one).
    Mutation: read the default branch from another variable -- unexpected call."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(pull_read(12), pull("trunk"))
    assert _marks(monkeypatch, tmp_path, gh, [AUTH], default_branch="trunk") == {
        "locks": json.dumps({"stacks/auth / dev": 12}),
        "stacked": "{}",
        "stacked_unknown": "{}",
        "blocking": "[]",
    }
    assert gh.drained()


@pytest.mark.parametrize(
    ("head_ref", "default_branch"), [("", "main"), ("feat-a", "")], ids=["head", "default"]
)
def test_an_empty_branch_reads_no_holder(monkeypatch, tmp_path, head_ref, default_branch):
    """No pull read (the double refuses one). Mutation: drop either half of the empty-branch
    early return -- unexpected call."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    assert _marks(monkeypatch, tmp_path, gh, [AUTH], head_ref, default_branch) == {
        "locks": json.dumps({"stacks/auth / dev": 12}),
        "stacked": "{}",
        "stacked_unknown": "{}",
        "blocking": "[]",
    }
    assert gh.drained()


def test_marks_reads_each_holder_once(monkeypatch, tmp_path):
    """Two cells held by #12 queue one pull read; the double refuses a second. Mutation: read
    the holder per cell -- unexpected call."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (APP_REF, OTHER)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, message(12, "dev", "stacks/app")))
    gh.on(pull_read(12), pull("feat-a"))
    assert _marks(monkeypatch, tmp_path, gh, [AUTH, APP]) == {
        "locks": json.dumps({"stacks/auth / dev": 12, "stacks/app / dev": 12}),
        "stacked": '{"12": []}',
        "stacked_unknown": "{}",
        "blocking": "[12]",
    }
    assert gh.drained()


def test_marks_output_reads_back_through_stacked_marks(monkeypatch, tmp_path, capsys):
    """`summary-comment` reads `marks`' real output, not a hand-built value: JSON object keys
    are strings. Mutation: drop the `int(...)` key conversion in `_holder_map` -- string keys,
    red."""
    sc = load_script("summary-comment")
    gh = _three_holders()
    gh.on(pull_read(12), pull("feat-a")).on(pull_read(9), pull("b")).on(pull_read(5), pull("c"))
    gh.on(list_read("b"), entries(entry(11, "b", "feat-a")))
    gh.on(list_read("c"), entries(entry(4, "c", "b", "closed", "2026-11-12T10:00:00Z")))
    out = _marks(monkeypatch, tmp_path, gh, [AUTH, DNS, APP])
    assert sc.stacked_marks(out["stacked"]) == {12: (), 9: (11,), 5: (11, 4)}
    assert sc.unknown_marks(out["stacked_unknown"]) == {}
    assert capsys.readouterr().out == ""


#: One holder per environment of `stacks/app`: #12 stacked on this head in `dev`, #9 in `stg`
#: whose walk runs out of pages, #5 in `prd` whose read fails.
_ONE_PER_ENV = (("dev", 12, NEW), ("stg", 9, OLD), ("prd", 5, OTHER))


def _per_env_double(envs):
    """A double reading the locks of `envs` (of `_ONE_PER_ENV`) and their holders."""
    gh = Gh()
    for env, holder, sha in _ONE_PER_ENV:
        if env in envs:
            gh.on(listing(f"shipmate/locks/{env}/"), refs((cl.ref_name(env, "stacks/app"), sha)))
            gh.on(commit_read(sha), commit(sha, message(holder, env, "stacks/app")))
    gh.on(pull_read(12), pull("feat-a")).on(pull_read(9), pull("x")).on(pull_read(5), FAIL)
    return _pages(gh, "x", 20, entries())


def test_the_comment_offers_no_eviction_that_evict_refuses(monkeypatch, tmp_path):
    """`marks`' output read by `summary-comment` drops the evict line in exactly the
    environments where `evict` refuses as `Blocked`: `dev` and `stg`, not `prd`, whose failed
    read may pass on evict's own read. Mutations: write `blocking` from `stacked` alone --
    the comment offers `stg`, red; in `evict`, block only on `stacked` -- `stg` is not
    `Blocked`, red."""
    sc = load_script("summary-comment")
    envs = [env for env, _, _ in _ONE_PER_ENV]
    out = _marks(
        monkeypatch, tmp_path, _per_env_double(envs), [{**APP, "environment": e} for e in envs]
    )
    counts = {"add": 1, "change": 0, "destroy": 0, "import": 0, "forget": 0}
    cells = [({**APP, "environment": e, "changed": True, **counts}, None) for e in envs]
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "1")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://gh")
    body = sc.build_comment(
        cells,
        {},
        "https://gh/run/1",
        "0" * 40,
        hints=True,
        locks=sc.lock_marks(out["locks"]),
        stacked=sc.stacked_marks(out["stacked"]),
        default_branch="main",
        unknown=sc.unknown_marks(out["stacked_unknown"]),
        blocking=sc.blocking_marks(out["blocking"], sc.stacked_marks(out["stacked"])),
    )[0]
    offered = {e for e in envs if f"`shipmate unlock {e} --evict`" in body}
    blocked = set()
    for env in envs:
        gh = _per_env_double({env}).on(
            CHECK_RUNS_READ, jsonl(_check(f"apply / stacks/app / {env}", "queued", None))
        )
        try:
            _main(monkeypatch, tmp_path, "evict", gh, {**EVICT_ENV, "SHIPMATE_ENV": env})
        except cl.Blocked:
            blocked.add(env)
        except SystemExit:
            pass
    assert (set(envs) - offered, blocked) == ({"dev", "stg"}, {"dev", "stg"})


def test_stacked_reads_a_holder_once_on_a_transient_failure(sleeps, capsys):
    """Reviewer-facing, so a transient failure reads unknown at once. Mutation: drop
    `retry=False` from `_stacked`'s read -- the double refuses the second call."""
    gh = Gh().on(pull_read(12), BAD_GATEWAY)
    stacked, unknown = cl._stacked(REPO, {12}, "feat-a", "main", gh)
    why = "command failed (1): gh api x | gh: Bad Gateway (HTTP 502)"
    assert (stacked, unknown, gh.drained(), sleeps) == (
        {},
        {12: cl.Unknown(why, exhausted=False)},
        True,
        [],
    )
    assert capsys.readouterr().out == (
        f"::warning::could not tell whether #12 is stacked on this pull request ({why}); the "
        "plan comment says so\n"
    )


def test_stacked_reads_each_listing_page_once(sleeps):
    """Reviewer-facing, so a transient listing failure reads unknown at once. Mutation: read
    the listing pages with `retry=True` inside `_stacked` -- the double refuses the retry."""
    gh = Gh().on(pull_read(12), pull("b")).on(list_read("b"), BAD_GATEWAY)
    assert cl._stacked(REPO, {12}, "feat-a", "main", gh)[1] == {
        12: cl.Unknown("command failed (1): gh api x | gh: Bad Gateway (HTTP 502)", False)
    }
    assert (gh.drained(), sleeps) == (True, [])


def chain(gh, base, state="open", memo=None, default="main"):
    """`_chain` of an open holder based on `base`, against this pull request's head `feat-a`."""
    holder = {"state": state, "base": {"ref": base}}
    memo = {} if memo is None else memo
    return cl._chain(REPO, holder, "feat-a", default, gh, memo, retry=False)


@pytest.mark.parametrize(
    ("state", "base"),
    [("closed", "feat-a"), ("closed", "b"), ("open", "main")],
    ids=["closed-direct", "closed-chained", "default-branch"],
)
def test_a_holder_not_open_or_on_the_default_branch_reads_nothing(state, base):
    """A merged or closed holder will not merge after this pull request; a holder on the
    default branch stops there. No listing (the empty double refuses one). Mutations: drop the
    `state != "open"` check -- closed-direct is `OnChain`, red; drop the `default_branch`
    stop -- default-branch lists `main`, unexpected call."""
    assert chain(Gh(), base, state) is None


def test_a_holder_answer_with_a_null_field_is_unknown():
    """Mutation: check keys only (drop the `isinstance` check) -- not stacked (None)."""
    assert chain(Gh(), "feat-a", None) == cl.Unknown(
        "ValueError(\"base.ref 'feat-a', state None\")", exhausted=False
    )


def test_stacked_at_depth_one_two_and_three():
    """Depth 1 reads no listing; the chain runs from this pull request outward. Mutations:
    drop `reversed` -- depth 3 reads `(12, 11)`, red."""
    assert chain(Gh(), "feat-a") == cl.OnChain(())
    gh = Gh().on(list_read("b"), entries(entry(11, "b", "feat-a")))
    assert (chain(gh, "b"), gh.drained()) == (cl.OnChain((11,)), True)
    gh = Gh().on(list_read("c"), entries(entry(12, "c", "b")))
    gh.on(list_read("b"), entries(entry(11, "b", "feat-a")))
    assert (chain(gh, "c"), gh.drained()) == (cl.OnChain((11, 12)), True)


def test_a_shared_head_is_stacked_when_any_of_its_pull_requests_leads_here():
    """`b` heads #10 into `main` and #11 into this head; #10 is walked first. Mutation: follow
    only the first entry -- not stacked (None)."""
    gh = Gh().on(list_read("b"), entries(entry(10, "b", "main"), entry(11, "b", "feat-a")))
    assert (chain(gh, "b"), gh.drained()) == (cl.OnChain((11,)), True)


def test_a_base_cycle_ends_not_stacked():
    """`x` and `y` are based on each other: each listed once. Mutation: drop `_walk`'s
    breadth-first `seen` check (`if onto not in seen:`) -- the walk circles through the memo
    until it runs out of pages, an exhausted `Unknown`, red."""
    gh = Gh().on(list_read("x"), entries(entry(20, "x", "y")))
    gh.on(list_read("y"), entries(entry(21, "y", "x")))
    assert (chain(gh, "x"), gh.drained()) == (None, True)


def _full_page(head, first):
    """A full page: 100 closed, unmerged pull requests of `head` into this head, none followed."""
    return entries(*(entry(n, head, "feat-a", "closed") for n in range(first, first + 100)))


def test_a_match_on_page_two_is_stacked():
    """Mutation: read page 1 only -- not stacked (None)."""
    gh = Gh().on(list_read("b"), _full_page("b", 1000))
    gh.on(list_read("b", 2), entries(entry(11, "b", "feat-a")))
    assert (chain(gh, "b"), gh.drained()) == (cl.OnChain((11,)), True)


def test_a_merged_link_mid_chain_is_followed_and_a_closed_one_is_not():
    """#11 merged into this head (`state: closed`, `merged_at` set, no `merged` field), its
    branch kept: stacked. #12, closed unmerged, is not followed. Mutations: follow only `open`
    -- not stacked (None) for `b`, red; read a `merged` field -- `KeyError`, red; follow any closed
    -- `c` reads `OnChain`, red."""
    gh = Gh().on(
        list_read("b"), entries(entry(11, "b", "feat-a", "closed", "2026-11-12T10:00:00Z"))
    )
    assert (chain(gh, "b"), gh.drained()) == (cl.OnChain((11,)), True)
    gh = Gh().on(list_read("c"), entries(entry(12, "c", "feat-a", "closed")))
    assert (chain(gh, "c"), gh.drained()) == (None, True)


@pytest.mark.parametrize(
    "listed",
    [
        entry(11, "other", "feat-a"),
        entry(11, "b", "feat-a", repo="fork/infra"),
        entry(11, "b", "feat-a", repo=None),
    ],
    ids=["other-branch", "fork", "deleted-fork"],
)
def test_a_listed_pull_request_of_another_head_is_not_followed(listed):
    """A malformed filter lists every pull request (probed). Mutations: drop the `head.ref`
    check -- other-branch reads `OnChain`; drop the repository check -- fork reads `OnChain`;
    read a null head repository as a mapping -- deleted-fork reads `Unknown`."""
    gh = Gh().on(list_read("b"), entries(listed))
    assert (chain(gh, "b"), gh.drained()) == (None, True)


def test_the_head_repository_compares_without_case():
    """GitHub names are case-insensitive. Mutation: compare `full_name` exactly -- red."""
    gh = Gh().on(list_read("b"), entries(entry(11, "b", "feat-a", repo="Acme/Infra")))
    assert chain(gh, "b") == cl.OnChain((11,))


@pytest.mark.parametrize(
    ("branch", "path"),
    [
        ("a+b", "acme%3Aa%2Bb"),
        ("a&b", "acme%3Aa%26b"),
        ("a#b", "acme%3Aa%23b"),
    ],
    ids=["plus", "ampersand", "hash"],
)
def test_a_branch_name_is_encoded_in_the_listing_query(branch, path):
    """Unencoded, `+` reads as a space, `&` starts a new query value and `#` a fragment.
    Mutation: drop `quote` -- each listing is an unexpected call."""
    read = ["gh", "api", f"repos/{REPO}/pulls?state=all&head={path}&per_page=100&page=1"]
    gh = Gh().on(read, entries(entry(11, branch, "feat-a")))
    assert (chain(gh, branch), gh.drained()) == (cl.OnChain((11,)), True)


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (FAIL, "command failed (1): gh api"),
        ('{"message": "x"}', "ValueError('page 1 of a pull request listing is not a list')"),
        (
            entries({**entry(11, "b", "feat-a"), "number": "11"}),
            "ValueError('a pull request listing entry is malformed')",
        ),
        (entries({"number": 11}), "KeyError('head')"),
        ("not json", "JSONDecodeError('Expecting value: line 1 column 1 (char 0)')"),
    ],
    ids=["failed", "not-a-list", "number", "missing", "not-json"],
)
def test_a_failed_or_malformed_listing_is_unknown(answer, why):
    """Mutations: skip a malformed entry -- None; let a failure read None --
    red; mark these `exhausted` -- red."""
    gh = Gh().on(list_read("b"), answer)
    assert chain(gh, "b") == cl.Unknown(why, exhausted=False)


def _pages(gh, branch, full, last):
    """Queue `full` full pages of `branch`, then the short page `last`."""
    for n in range(1, full + 1):
        gh.on(list_read(branch, n), _full_page(branch, 1000 * n))
    return gh.on(list_read(branch, full + 1), last)


EXHAUSTED = cl.Unknown(
    "its chain of base branches needs more than 20 pages of pull requests", exhausted=True
)


def test_a_walk_of_twenty_pages_ends_and_a_twenty_first_is_unknown():
    """Twenty pages end with the match on the last; twenty-one never read the last (the double
    queues it, so `drained` is False). Mutations: `spent > WALK_PAGES` -- the 21st page is read
    and the walk reads `OnChain`, red; `WALK_PAGES = 19` -- the first walk is `Unknown`, red."""
    gh = _pages(Gh(), "b", 19, entries(entry(11, "b", "feat-a")))
    assert (chain(gh, "b"), gh.drained()) == (cl.OnChain((11,)), True)
    gh = _pages(Gh(), "b", 20, entries(entry(11, "b", "feat-a")))
    assert (chain(gh, "b"), gh.drained()) == (EXHAUSTED, False)


def test_a_memo_hit_is_charged_to_the_holder():
    """#5 is based on `b`, which takes 20 pages (none followed); #6 on `c`, whose one page
    leads to `b`. #6 needs 21 pages whether or not #5 was walked first. Mutation: charge only
    a read the memo did not hold -- #6 reads not stacked (None) after #5, red."""
    gh = _pages(Gh(), "b", 19, entries())
    gh.on(list_read("c"), entries(entry(12, "c", "b")))
    memo = {}
    assert chain(gh, "b", memo=memo) is None
    assert chain(gh, "c", memo=memo) == EXHAUSTED
    assert gh.drained()
    alone = _pages(Gh(), "b", 19, entries()).on(list_read("c"), entries(entry(12, "c", "b")))
    assert chain(alone, "c") == EXHAUSTED


def test_a_memoised_cycle_is_listed_once_across_holders():
    """`x` and `y` are based on each other and `y` also heads #22 into this head. #5 is based
    on `x`, #6 on `y`; each listing is read once. Mutation: read every page past the memo --
    `y`'s second listing is an unexpected call, red."""
    gh = Gh().on(list_read("x"), entries(entry(20, "x", "y")))
    gh.on(list_read("y"), entries(entry(21, "y", "x"), entry(22, "y", "feat-a")))
    gh.on(pull_read(5), pull("x")).on(pull_read(6), pull("y"))
    assert cl._read_stacked(REPO, {5, 6}, "feat-a", "main", gh, retry=False) == (
        {5: (22, 20), 6: (22,)},
        {},
    )
    assert gh.drained()


def test_a_holder_walks_a_branch_another_holder_queued():
    """#5 is based on `a`, whose #30 leads to `b`, whose #31 leads here; #6 is based on `c`,
    whose #40 also leads to `b`. Each listing is read once. Mutation: keep one `seen` set
    across holders (in `memo`) -- `b` is already seen for #6, which reads not stacked, red."""
    gh = Gh().on(list_read("a"), entries(entry(30, "a", "b")))
    gh.on(list_read("b"), entries(entry(31, "b", "feat-a")))
    gh.on(list_read("c"), entries(entry(40, "c", "b")))
    gh.on(pull_read(5), pull("a")).on(pull_read(6), pull("c"))
    assert cl._read_stacked(REPO, {5, 6}, "feat-a", "main", gh, retry=False) == (
        {5: (31, 30), 6: (31, 40)},
        {},
    )
    assert gh.drained()


def test_a_near_link_wins_before_a_wide_subgraph_spends_the_budget():
    """`b` heads #5 into `x`, whose listing takes 19 pages, and #6 into this head. Breadth
    first, #6 is found on `b`'s own page and `x` is never listed (the double queues none).
    Mutation: test `head_ref` when a branch is dequeued, not when its link is found -- `x` is
    listed first, an unexpected call, red."""
    gh = Gh().on(list_read("b"), entries(entry(5, "b", "x"), entry(6, "b", "feat-a")))
    assert (chain(gh, "b"), gh.drained()) == (cl.OnChain((6,)), True)


def test_the_named_chain_is_a_shortest_one():
    """`c` heads #3 into `d`, whose #4 leads here, and #7 into `e`, whose #9 leads to `f`,
    whose #10 leads here. Mutation: `queue.pop()` (depth first) -- `(10, 9, 7)`, red."""
    gh = Gh().on(list_read("c"), entries(entry(3, "c", "d"), entry(7, "c", "e")))
    gh.on(list_read("d"), entries(entry(4, "d", "feat-a")))
    gh.on(list_read("e"), entries(entry(9, "e", "f")))
    gh.on(list_read("f"), entries(entry(10, "f", "feat-a")))
    assert chain(gh, "c") == cl.OnChain((4, 3))


def test_nothing_is_stacked_on_the_default_branch():
    """This pull request's head is the default branch: no holder is stacked on it and none is
    read, so #12's failing read stays queued. Mutation: drop `_read_stacked`'s `head_ref ==
    default_branch` return -- #12 reads unknown, red."""
    gh = Gh().on(pull_read(12), FAIL)
    assert cl._read_stacked(REPO, {12}, "main", "main", gh, retry=False) == ({}, {})
    assert gh.calls == {tuple(pull_read(12)): [FAIL]}


def test_marks_on_the_default_branch_reads_no_holder(monkeypatch, tmp_path, capsys):
    """A failing holder read would warn and name #12 unknown. Mutation: drop `_read_stacked`'s
    `head_ref == default_branch` return -- red."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(pull_read(12), FAIL)
    assert _marks(monkeypatch, tmp_path, gh, [AUTH], head_ref="main") == {
        "locks": json.dumps({"stacks/auth / dev": 12}),
        "stacked": "{}",
        "stacked_unknown": "{}",
        "blocking": "[]",
    }
    assert gh.calls[tuple(pull_read(12))] == [FAIL]
    assert capsys.readouterr().out == ""


HEAD = "d" * 40
EMPTY_WAVES = json.dumps({f"wave{i}": [] for i in range(8)})
CHECK_RUNS_READ = [
    "gh",
    "api",
    "--paginate",
    f"repos/{REPO}/commits/{HEAD}/check-runs?filter=all&per_page=100",
    "--jq",
    ".check_runs[]",
]


EVICT_ENV = {
    "SHIPMATE_ENV": "dev",
    "SHIPMATE_HEAD_REF": "feat-a",
    "SHIPMATE_DEFAULT_BRANCH": "main",
}


def _main(monkeypatch, tmp_path, command, gh, env):
    """Run `cell-lock <command>` with `env` over a base environment (None unsets a variable);
    returns the step outputs it wrote."""
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    base = {
        "GITHUB_OUTPUT": str(out),
        "GITHUB_REPOSITORY": REPO,
        "SHIPMATE_PR_NUMBER": str(PR),
        "SHIPMATE_HEAD_SHA": HEAD,
        "SHIPMATE_APP_ID": "99",
        "GITHUB_RUN_ID": "900",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SERVER_URL": "https://github.com",
    }
    for key, value in {**base, **env}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    monkeypatch.setattr(cl, "_run", gh)
    monkeypatch.setattr(cl.sys, "argv", ["cell-lock", command])
    cl.main()
    return github_outputs(out)


def _waves(level0=EMPTY_WAVES):
    return {
        "SHIPMATE_ENVLEVEL0_WAVES": level0,
        **{f"SHIPMATE_ENVLEVEL{n}_WAVES": EMPTY_WAVES for n in (1, 2, 3)},
    }


def test_acquire_locks_every_wave_cell_then_fences(monkeypatch, tmp_path):
    """Mutation: skip `fence_open` (the state read is never sent)."""
    gh = gh_acquire_start(AUTH).on(ref_write(AUTH_REF, NEW), "{}")
    gh.on(PR_STATE_READ, _pr_state("OPEN"))
    level0 = json.dumps({"wave0": [], "wave1": [AUTH]})
    _main(monkeypatch, tmp_path, "acquire", gh, _waves(level0))
    assert gh.drained()


def test_acquire_locks_the_union_of_every_level_and_wave(monkeypatch, tmp_path):
    """Mutation: read only `SHIPMATE_ENVLEVEL0_WAVES` (the other levels' cells stay unlocked)."""
    cells = [
        (0, "wave0", AUTH),
        (1, "wave2", {"stack": "stacks/app", "environment": "stage"}),
        (2, "wave0", {"stack": "stacks/dns", "environment": "prod"}),
        (3, "wave7", {"stack": "stacks/edge", "environment": "qa"}),
    ]
    gh = Gh().on(TREE_WRITE, json.dumps({"sha": TREE}))
    env = _waves()
    for n, wave, cell in cells:
        sha = str(n + 1) * 40
        msg = message(PR, cell["environment"], cell["stack"])
        gh.on(commit_write(cell), commit(sha, msg, LATE))
        gh.on(ref_write(cl.ref_name(cell["environment"], cell["stack"]), sha), "{}")
        env[f"SHIPMATE_ENVLEVEL{n}_WAVES"] = json.dumps({wave: [cell]})
    gh.on(PR_STATE_READ, _pr_state("OPEN"))
    _main(monkeypatch, tmp_path, "acquire", gh, env)
    assert gh.drained()


@pytest.mark.parametrize(
    ("env", "refusal"),
    [
        pytest.param(
            {"SHIPMATE_ENVLEVEL2_WAVES": None},
            "SHIPMATE_ENVLEVEL2_WAVES must be a JSON map of waves",
            id="missing",
        ),
        pytest.param(
            {"SHIPMATE_ENVLEVEL1_WAVES": "nope"},
            "SHIPMATE_ENVLEVEL1_WAVES must be a JSON map of waves",
            id="not-json",
        ),
        pytest.param(
            {"SHIPMATE_ENVLEVEL3_WAVES": "[]"},
            "SHIPMATE_ENVLEVEL3_WAVES must be a JSON map of waves",
            id="not-a-map",
        ),
        pytest.param(
            {"SHIPMATE_ENVLEVEL0_WAVES": json.dumps({"wave0": [{"stack": 1}]})},
            "every wave cell must be a {stack, environment} object",
            id="bad-cell",
        ),
        pytest.param(
            {"GITHUB_RUN_ID": None},
            "GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT must be decimal (got: ['', '1'])",
            id="no-run-id",
        ),
        pytest.param(
            {"GITHUB_RUN_ATTEMPT": "1x"},
            "GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT must be decimal (got: ['900', '1x'])",
            id="bad-attempt",
        ),
    ],
)
def test_acquire_refuses_bad_input_before_any_call(monkeypatch, tmp_path, env, refusal):
    """Mutations: drop `KeyError` (missing), `ValueError` (not-json) or `AttributeError`
    (not-a-map) from `_wave_cells`' except; drop its `checked is None` refusal (bad-cell); drop
    the run id check (no-run-id and bad-attempt reach the API)."""
    level0 = json.dumps({"wave0": [AUTH]})
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "acquire", Gh(), {**_waves(level0), **env})
    assert exc.value.code == "::error::" + refusal


@pytest.mark.parametrize(
    ("env", "released"),
    [
        pytest.param(
            {"SHIPMATE_MERGED": "true", "SHIPMATE_RELEASE_PENDING": None}, DNS_REF, id="merged"
        ),
        pytest.param(
            {"SHIPMATE_MERGED": "true", "SHIPMATE_RELEASE_PENDING": "false"},
            DNS_REF,
            id="merged-release-pending-false",
        ),
        pytest.param(
            {"SHIPMATE_MERGED": "false", "SHIPMATE_CLOSED_AT": "2026-10-24T11:00:00Z"},
            AUTH_REF,
            id="unmerged",
        ),
    ],
)
def test_release_cli_releases_by_mode(monkeypatch, tmp_path, env, released):
    """Mutations: release nothing in the unmerged branch (`gone = []`: no delete is sent); read
    `pending` without the App filter (`parse_jsonl` for `app_runs` in `pending_names`: the
    foreign check on stacks/dns keeps its lock); read release-pending `false` as `true` (`if
    release_pending`: merged-release-pending-false deletes the pending stacks/auth lock)."""
    gh = _all_locks(
        (AUTH_REF, OLD, message(PR, "dev", "stacks/auth"), EARLY),
        (DNS_REF, NEW, message(PR, "dev", "stacks/dns"), LATE),
    )
    if env["SHIPMATE_MERGED"] == "true":
        gh.on(
            CHECK_RUNS_READ,
            jsonl(
                _check("apply / stacks/auth / dev", "queued", None),
                _check("apply / stacks/dns / dev", "queued", None, run_id=2, app=15368),
            ),
        )
    else:
        gh.on(PR_STATE_READ, _pr_state("OPEN", "2026-10-24T11:00:00Z"))
    sha = OLD if released == AUTH_REF else NEW
    gh.on(REPO_ID_READ, REPO_ID).on(update(released, sha, ZERO, False), "{}")
    _main(monkeypatch, tmp_path, "release", gh, env)
    assert gh.drained()


def test_release_cli_release_pending_deletes_every_lock_without_reading_checks(
    monkeypatch, tmp_path
):
    """A refused deploy deletes every lock its merged pull request holds, and reads no apply
    check to do it. Mutation: keep the merge rule's `pending` set under release-pending -- the
    head's checks are read, an unexpected call to the double, red."""
    gh = _all_locks(
        (AUTH_REF, OLD, message(PR, "dev", "stacks/auth"), EARLY),
        (DNS_REF, NEW, message(PR, "dev", "stacks/dns"), EARLY),
    )
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), "{}")
    gh.on(update(DNS_REF, NEW, ZERO, False), "{}")
    env = {"SHIPMATE_MERGED": "true", "SHIPMATE_RELEASE_PENDING": "true"}
    _main(monkeypatch, tmp_path, "release", gh, env)
    assert gh.drained()


def test_release_cli_release_pending_with_no_lock_left_exits_clean(monkeypatch, tmp_path, capsys):
    """A re-run after every lock is gone deletes nothing and succeeds. Mutation: refuse when
    release-pending deletes no lock (`if not gone: raise SystemExit`)."""
    gh = Gh().on(listing("shipmate/locks/"), "[]")
    env = {"SHIPMATE_MERGED": "true", "SHIPMATE_RELEASE_PENDING": "true"}
    _main(monkeypatch, tmp_path, "release", gh, env)
    assert capsys.readouterr().out == "::notice::released 0 cell lock(s) of pull request #7\n"
    assert gh.drained()


def test_release_cli_names_each_refused_delete_after_releasing_the_rest(
    monkeypatch, tmp_path, capsys
):
    """Mutation: drop the final refusal in `_release_cmd` (the step reads green)."""
    gh = _all_locks(
        (AUTH_REF, OLD, message(PR, "dev", "stacks/auth"), EARLY),
        (DNS_REF, OTHER, message(PR, "dev", "stacks/dns"), EARLY),
    )
    gh.on(PR_STATE_READ, _pr_state("CLOSED", LATE))
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), FAIL)
    gh.on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    gh.on(update(DNS_REF, OTHER, ZERO, False), "{}")
    env = {"SHIPMATE_MERGED": "false", "SHIPMATE_CLOSED_AT": LATE}
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "release", gh, env)
    assert exc.value.code == _AUTH_REFUSAL
    assert "::notice::released 1 cell lock(s) of pull request #7" in capsys.readouterr().out
    assert gh.drained()


@pytest.mark.parametrize(
    ("env", "refusal"),
    [
        pytest.param(
            {"SHIPMATE_MERGED": "yes", "SHIPMATE_CLOSED_AT": LATE},
            "SHIPMATE_MERGED must be true or false (got: 'yes')",
            id="merged-yes",
        ),
        pytest.param(
            {"SHIPMATE_MERGED": None, "SHIPMATE_CLOSED_AT": LATE},
            "SHIPMATE_MERGED must be true or false (got: '')",
            id="merged-unset",
        ),
        pytest.param(
            {"SHIPMATE_MERGED": "false", "SHIPMATE_CLOSED_AT": "2026-10-24T11:00:00"},
            "SHIPMATE_CLOSED_AT must be an ISO-8601 time (got: '2026-10-24T11:00:00')",
            id="closed-at-no-offset",
        ),
        pytest.param(
            {
                "SHIPMATE_MERGED": "false",
                "SHIPMATE_CLOSED_AT": LATE,
                "SHIPMATE_RELEASE_PENDING": "true",
            },
            "SHIPMATE_RELEASE_PENDING must be true or false, and true only with SHIPMATE_MERGED "
            "true (got: SHIPMATE_RELEASE_PENDING='true', SHIPMATE_MERGED='false')",
            id="release-pending-unmerged",
        ),
        pytest.param(
            {"SHIPMATE_MERGED": "true", "SHIPMATE_RELEASE_PENDING": "yes"},
            "SHIPMATE_RELEASE_PENDING must be true or false, and true only with SHIPMATE_MERGED "
            "true (got: SHIPMATE_RELEASE_PENDING='yes', SHIPMATE_MERGED='true')",
            id="release-pending-yes",
        ),
    ],
)
def test_release_cli_refuses_a_bad_mode_before_any_call(monkeypatch, tmp_path, env, refusal):
    """Mutations: treat any `SHIPMATE_MERGED` but `true` as unmerged (merged-yes and
    merged-unset release by `SHIPMATE_CLOSED_AT`); drop the `_when` check (closed-at-no-offset
    reaches the API); accept release-pending `true` whatever `SHIPMATE_MERGED` says
    (release-pending-unmerged releases by `SHIPMATE_CLOSED_AT`); accept any release-pending
    value (release-pending-yes reads the head's checks)."""
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "release", Gh(), env)
    assert exc.value.code == "::error::" + refusal


def test_evict_cli_evicts_only_cells_with_an_app_check(monkeypatch, tmp_path):
    """Mutation: `parse_jsonl` for `app_runs` in `pending_names` (the foreign check on
    stacks/dns evicts its lock)."""
    gh = Gh().on(
        CHECK_RUNS_READ,
        jsonl(
            _check("apply / stacks/auth / dev", "queued", None),
            _check("apply / stacks/dns / dev", "queued", None, run_id=2, app=15368),
        ),
    )
    gh.on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (DNS_REF, OTHER)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, message(12, "dev", "stacks/dns")))
    quiet(gh.on(pull_read(12), pull("main")), 12)
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), "{}")
    out = _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV)
    assert out == {
        "evicted": json.dumps([{"stack": "stacks/auth", "environment": "dev", "holder": 12}]),
        "refused": "[]",
    }
    assert gh.drained()


def _stacked_line(holder, count):
    return (
        f"#{holder} is stacked on this pull request and holds {count} of its cell(s) in dev; "
        "applying this pull request after evicting it would overwrite what it applied. Retarget "
        f"#{holder} to main and merge it first, or close it, which releases its locks."
    )


def _evict_three(*checks):
    """A double reading `checks` and the locks of #12 on auth and app and of #9 on dns."""
    gh = Gh().on(CHECK_RUNS_READ, jsonl(*checks))
    gh.on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (DNS_REF, OTHER), (APP_REF, NEW)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, message(9, "dev", "stacks/dns")))
    return gh.on(commit_read(NEW), commit(NEW, message(12, "dev", "stacks/app")))


_THREE_PENDING = (
    _check("apply / stacks/auth / dev", "queued", None),
    _check("apply / stacks/dns / dev", "queued", None, run_id=2),
    _check("apply / stacks/app / dev", "queued", None, run_id=3),
)


def test_evict_cli_refuses_stacked_holders_in_one_error_and_deletes_nothing(monkeypatch, tmp_path):
    """The double queues no delete or runs listing, so either is an unexpected call. Mutations:
    skip the stacked check; run it after `_delete_each`; run `_refuse_live_runs` before it (each:
    unexpected call); raise one `::error::` per holder; write `evicted=[]` beside `blocked` --
    each red."""
    gh = _evict_three(*_THREE_PENDING)
    gh.on(pull_read(12), pull("feat-a")).on(pull_read(9), pull("feat-a"))
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV)
    assert exc.value.code == f"::error::{_stacked_line(9, 1)}\n{_stacked_line(12, 2)}"
    assert github_outputs(out) == {"blocked": "true"}
    assert gh.drained()


_EXHAUSTED_LINE = (
    "could not tell whether #9 is stacked on this pull request: its chain of base branches "
    "needs more than 20 pages of pull requests. Commenting again refuses the same way: retarget "
    "#9 to main, or wait until it merges or closes, after which the eviction is not blocked by it."
)


def test_evict_cli_names_a_stacked_holders_chain(monkeypatch, tmp_path):
    """#12 is based on `c`, #14 heads `c` into `b`, #13 heads `b` into this head; #9 is based
    on `main`. Mutation: drop `stacked_through` from the refusal -- red."""
    gh = _evict_three(*_THREE_PENDING)
    gh.on(pull_read(12), pull("c")).on(pull_read(9), pull("main"))
    gh.on(list_read("c"), entries(entry(14, "c", "b")))
    gh.on(list_read("b"), entries(entry(13, "b", "feat-a")))
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV)
    assert exc.value.code == (
        "::error::#12 is stacked on this pull request through #13, #14 and holds 2 of its cell(s) "
        "in dev; applying this pull request after evicting it would overwrite what it applied. "
        "Retarget #12 to main and merge it first, or close it, which releases its locks."
    )
    assert github_outputs(out) == {"blocked": "true"}
    assert gh.drained()


def test_evict_cli_blocks_on_a_walk_out_of_pages_before_a_failed_read(monkeypatch, tmp_path):
    """#9's walk runs out of pages and #12's read fails: commenting again cannot pass #9, so
    the refusal is `blocked`, and it names both, each with its reason. Mutations: drop the
    exhausted half of `blocking` -- no `blocked`, red; check failed reads before `blocked` --
    red; leave the unread holders out of `Blocked` -- red."""
    gh = _evict_three(*_THREE_PENDING)
    gh.on(pull_read(12), FAIL).on(pull_read(9), pull("x"))
    _pages(gh, "x", 20, entries())
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV)
    assert exc.value.code == (
        f"::error::{_EXHAUSTED_LINE}\ncould not tell whether #12 is stacked on this pull "
        "request: command failed (1): gh api"
    )
    assert github_outputs(out) == {"blocked": "true"}
    assert gh.calls[tuple(list_read("x", 21))] == [entries()]
    del gh.calls[tuple(list_read("x", 21))]
    assert gh.drained()


def test_evict_cli_refuses_a_holder_it_cannot_judge_without_blocked(monkeypatch, tmp_path):
    """#12's read fails and #9's answer has no `base`: each gets its `could not tell` line,
    cause on one line, and nothing is deleted or written. Mutations: treat a failed read as not
    stacked (`except: continue`) -- unexpected delete call; drop `_why` -- the cause splits."""
    gh = _evict_three(*_THREE_PENDING)
    gh.on(
        pull_read(12), SystemExit("::error::command failed (1): gh api\ngh: Not Found (HTTP 404)")
    )
    gh.on(pull_read(9), json.dumps({"state": "open"}))
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV)
    assert exc.value.code == (
        "::error::could not tell whether #9 is stacked on this pull request: KeyError('base')\n"
        "could not tell whether #12 is stacked on this pull request: command failed (1): gh api "
        "| gh: Not Found (HTTP 404)"
    )
    assert github_outputs(out) == {}
    assert gh.drained()


@pytest.mark.parametrize(
    ("answer", "cause"),
    [
        pytest.param(
            {"state": None, "base": {"ref": "feat-a"}}, "base.ref 'feat-a', state None", id="state"
        ),
        pytest.param(
            {"state": "open", "base": {"ref": None}}, "base.ref None, state 'open'", id="base"
        ),
    ],
)
def test_evict_cli_refuses_a_holder_answer_with_a_null_field(monkeypatch, tmp_path, answer, cause):
    """A null field is malformed, not "not stacked". Mutation: check keys only (drop the
    `isinstance` check) -- #9 reads as not stacked and its delete is an unexpected call."""
    gh = _evict_three(*_THREE_PENDING)
    gh.on(pull_read(12), pull("main")).on(pull_read(9), json.dumps(answer))
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV)
    assert exc.value.code == (
        f"::error::could not tell whether #9 is stacked on this pull request: ValueError({cause!r})"
    )
    assert github_outputs(out) == {}
    assert gh.drained()


@pytest.mark.parametrize(
    "key", ["SHIPMATE_HEAD_REF", "SHIPMATE_DEFAULT_BRANCH"], ids=["head-ref", "default"]
)
def test_evict_cli_refuses_an_empty_branch_before_any_read(monkeypatch, tmp_path, key):
    """The empty double refuses every call. Mutation: default each to empty and skip the
    check -- the check-run read is an unexpected call."""
    env = {**EVICT_ENV, key: ""}
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "evict", Gh(), env)
    assert exc.value.code == (
        "::error::SHIPMATE_HEAD_REF and SHIPMATE_DEFAULT_BRANCH must name branches (got: "
        f"{env['SHIPMATE_HEAD_REF']!r}, {env['SHIPMATE_DEFAULT_BRANCH']!r})"
    )


def test_evict_cli_targets_only_pending_cells_failed_included(monkeypatch, tmp_path):
    """auth's check failed and #12 is not stacked: evicted. app's check succeeded, so stacked
    #9's lock there is not a target and #9 is never read (the double refuses the read).
    Mutations: evict over every apply check (`latest_by_name`) -- #9's read is unexpected;
    target only checks with `status != completed` -- auth's lock stays."""
    gh = Gh().on(
        CHECK_RUNS_READ,
        jsonl(
            _check("apply / stacks/auth / dev", "completed", "failure"),
            _check("apply / stacks/app / dev", "completed", "success", run_id=2),
        ),
    )
    gh.on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (APP_REF, NEW)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(NEW), commit(NEW, message(9, "dev", "stacks/app")))
    quiet(gh.on(pull_read(12), pull("main")), 12)
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), "{}")
    assert _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV) == {
        "evicted": json.dumps([{"stack": "stacks/auth", "environment": "dev", "holder": 12}]),
        "refused": "[]",
    }
    assert gh.drained()


def test_evict_refusal_past_the_cap_keeps_whole_sentences_and_logs_every_holder(
    monkeypatch, tmp_path, capsys
):
    """Twenty stacked holders, #996..#1015, one cell each: sixteen sentences and the tail fit
    `REASON_CAP`, seventeen and the tail pass it by two characters. Mutations: size the kept
    sentences without the tail -- seventeen kept and the output cut mid-tail, red; drop the
    second error -- the log loses #1012..#1015, red."""
    holders = range(996, 1016)
    stacks = [f"stacks/s{i:02}" for i in range(20)]
    shas = [f"{i + 1:040d}" for i in range(20)]
    gh = Gh().on(
        CHECK_RUNS_READ,
        jsonl(
            *(_check(f"apply / {s} / dev", "queued", None, run_id=i) for i, s in enumerate(stacks))
        ),
    )
    gh.on(
        listing("shipmate/locks/dev/"),
        refs(*((cl.ref_name("dev", s), sha) for s, sha in zip(stacks, shas, strict=True))),
    )
    for h, s, sha in zip(holders, stacks, shas, strict=True):
        gh.on(commit_read(sha), commit(sha, message(h, "dev", s)))
        gh.on(pull_read(h), pull("feat-a"))
    lines = [_stacked_line(h, 1) for h in holders]
    tail = "and 4 more holder(s); the run log names every one"
    with pytest.raises(SystemExit):
        _shipmate.run_annotated(
            lambda: _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV), refusal_output=True
        )
    assert github_outputs(tmp_path / "out") == {
        "blocked": "true",
        "refusal": " ".join([*lines[:16], tail]),
    }
    assert capsys.readouterr().err == (
        f"::error::{_shipmate.command_data(chr(10).join([*lines[:16], tail]))}\n"
        f"::error::{_shipmate.command_data(chr(10).join(lines[16:]))}\n"
    )
    assert gh.drained()


def test_evict_refusal_cuts_a_lone_sentence_past_the_cap(monkeypatch, tmp_path, capsys):
    """#12's read fails with a 5,000-character cause: the reply keeps the sentence cut to
    3,970 characters, `...` and the tail, 4,000 in all, and the log has it whole. Mutation:
    drop the cut and return the tail alone when no sentence fits -- the reply names no holder,
    red."""
    cause = "x" * 5000
    gh = Gh().on(CHECK_RUNS_READ, jsonl(_check("apply / stacks/auth / dev", "queued", None)))
    gh.on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(pull_read(12), SystemExit(f"::error::{cause}"))
    sentence = f"could not tell whether #12 is stacked on this pull request: {cause}"
    with pytest.raises(SystemExit):
        _shipmate.run_annotated(
            lambda: _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV), refusal_output=True
        )
    refusal = github_outputs(tmp_path / "out")["refusal"]
    assert refusal == (
        "could not tell whether #12 is stacked on this pull request: "
        + "x" * 3910
        + "... the run log has it in full"
    )
    assert len(refusal) == 4000
    assert capsys.readouterr().err == (
        f"::error::{_shipmate.command_data(chr(10).join([refusal[:3973], refusal[3974:]]))}\n"
        f"::error::{sentence}\n"
    )


def test_evict_on_the_default_branch_reads_no_holder(monkeypatch, tmp_path):
    """Nothing is stacked on the default branch, so #12 is read only by the live-run check (the
    double queues one read) and the eviction proceeds. Mutation: drop `_read_stacked`'s
    `head_ref == default_branch` return -- #12, based on `main`, reads stacked and the evict
    refuses, red."""
    gh = Gh().on(CHECK_RUNS_READ, jsonl(_check("apply / stacks/auth / dev", "queued", None)))
    gh.on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    quiet(gh, 12).on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, OLD, ZERO, False), "{}")
    env = {**EVICT_ENV, "SHIPMATE_HEAD_REF": "main"}
    assert _main(monkeypatch, tmp_path, "evict", gh, env) == {
        "evicted": json.dumps([{"stack": "stacks/auth", "environment": "dev", "holder": 12}]),
        "refused": "[]",
    }
    assert gh.drained()


def test_evict_releases_an_unknown_holder_and_a_closed_one_based_on_this_branch():
    """An unknown holder is never read; a closed holder will not merge after this one.
    Mutation: drop `_chain`'s `state != "open"` return -- #12 refuses as stacked."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (DNS_REF, OTHER)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, "garbage"))
    quiet(gh.on(pull_read(12), pull("feat-a", "closed")), 12).on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, OLD, ZERO, False), "{}").on(update(DNS_REF, OTHER, ZERO, False), "{}")
    stacks = {"stacks/auth", "stacks/dns"}
    assert cl.evict(REPO, "dev", PR, stacks, "feat-a", "main", run=gh) == (
        [lock(AUTH_REF, OLD, pr=12), unknown(DNS_REF, OTHER)],
        [],
    )
    assert gh.drained()


def test_evict_retries_a_transient_holder_read(sleeps):
    """The evict check fails closed, so a transient failure is retried before it refuses.
    Mutation: read with `retry=False` -- a `could not tell` refusal."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    quiet(gh.on(pull_read(12), BAD_GATEWAY, pull("main")), 12).on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, OLD, ZERO, False), "{}")
    assert cl.evict(REPO, "dev", PR, {"stacks/auth"}, "feat-a", "main", run=gh) == (
        [lock(AUTH_REF, OLD, pr=12)],
        [],
    )
    assert (gh.drained(), sleeps) == (True, [2])


def _evict_auth(holder=12):
    """A double reading auth's pending check and `holder`'s lock on it, not stacked."""
    gh = Gh().on(CHECK_RUNS_READ, jsonl(_check("apply / stacks/auth / dev", "queued", None)))
    gh.on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(holder, "dev", "stacks/auth")))
    return gh.on(pull_read(holder), pull("main"))


def _dispatched(id_, title, status="in_progress", login="kp"):
    return {
        "id": id_,
        "display_title": title,
        "event": "workflow_dispatch",
        "status": status,
        "actor": {"login": login},
    }


def _refused(monkeypatch, tmp_path, gh):
    """Run `cell-lock evict`, expecting a refusal that writes no output; returns its code."""
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV)
    assert github_outputs(tmp_path / "out") == {}
    assert gh.drained()
    return exc.value.code


_LIVE = (
    "::error::no cell lock in dev was released: a holder's run may still apply, or the check "
    "could not tell. Wait for each run below to end, then comment again."
)
_URL = f"https://github.com/{REPO}/actions/runs"


def test_evict_refuses_a_holders_live_apply_run_and_an_unparseable_dispatch_run(
    monkeypatch, tmp_path
):
    """A human's `apply` titled for #12 and a dispatch run whose title does not parse each
    refuse, retryable (no `blocked`), naming the run; nothing is deleted (the double queues no
    delete). The title's `12` is a string compared as an int.

    Mutations: drop the `if lines` refusal -- the delete is an unexpected call; drop the
    unparseable-title line -- the refusal differs."""
    gh = _evict_auth()
    for status in STATUSES:
        if status != "waiting":
            gh.on(dispatch_listing(status), NO_RUNS)
    rows = [
        _dispatched(31, f"shipmate · apply pr=12 ref={OLD} comment=5", "waiting"),
        _dispatched(32, "nightly"),
    ]
    gh.on(dispatch_listing("waiting"), json.dumps({"workflow_runs": rows}))
    gh.on(pull_read(12), pull("main"))
    assert _refused(monkeypatch, tmp_path, gh) == (
        f"{_LIVE}\n#12 has a live `apply` run: {_URL}/31\n"
        f"a live run whose title is not a shipmate title may be a holder's: {_URL}/32"
    )


def test_a_live_run_refusal_past_the_cap_keeps_the_header_and_the_lines_that_fit(
    monkeypatch, tmp_path, capsys
):
    """Sixty unparseable live dispatch runs, #1001..#1060: the reply keeps the header and 33
    lines, then `and 27 more line(s); ...`, within `REASON_CAP`, and the log names all sixty.
    Mutation: join the header and lines with no `_refusal` -- the output is cut mid-line at
    `REASON_CAP`, red."""
    gh = _evict_auth()
    for status in STATUSES:
        if status != "queued":
            gh.on(dispatch_listing(status), NO_RUNS)
    rows = [_dispatched(i, "nightly", "queued") for i in range(1001, 1061)]
    gh.on(dispatch_listing("queued"), json.dumps({"workflow_runs": rows}))
    gh.on(pull_read(12), pull("main"))
    header = _LIVE.removeprefix("::error::")
    lines = [
        f"a live run whose title is not a shipmate title may be a holder's: {_URL}/{i}"
        for i in range(1001, 1061)
    ]
    tail = "and 27 more line(s); the run log names every one"
    with pytest.raises(SystemExit):
        _shipmate.run_annotated(
            lambda: _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV), refusal_output=True
        )
    assert github_outputs(tmp_path / "out") == {"refusal": " ".join([header, *lines[:33], tail])}
    assert capsys.readouterr().err == (
        f"::error::{_shipmate.command_data(chr(10).join([header, *lines[:33], tail]))}\n"
        f"::error::{_shipmate.command_data(chr(10).join(lines[33:]))}\n"
    )
    assert gh.drained()


def jobs_listing(run_id):
    return [
        "gh",
        "api",
        f"repos/{REPO}/actions/runs/{run_id}/jobs?filter=latest&per_page=100&page=1",
    ]


def deploy_jobs(caller="deploy", attempt=1, **statuses):
    """A deploy run's jobs listing on `attempt`, `{job name after "<caller> / ": status}`."""
    jobs = [
        {"name": f"{caller} / {name}", "status": s, "run_attempt": attempt}
        for name, s in statuses.items()
    ]
    return json.dumps({"jobs": jobs})


#: A deploy run whose env-levels and `summary` have ended while two `warm` legs run.
WARMING = {
    "detect": "completed",
    "warm / stacks/app": "in_progress",
    "warm / tools": "queued",
    "L0 / dev / stacks/app": "completed",
    "summary": "completed",
}


def _merged_holder_with_one_deploy_run():
    gh = _evict_auth()
    for status in STATUSES:
        gh.on(dispatch_listing(status), NO_RUNS)
    gh.on(pull_read(12), pull("main", "closed", LATE, MERGE))
    rows = [{"id": 42, "status": "queued", "run_attempt": 1}]
    return gh.on(push_listing(MERGE), json.dumps({"workflow_runs": rows}))


def test_evict_refuses_a_merged_holders_live_deploy_run(monkeypatch, tmp_path):
    """Mutations: skip a merged holder (`if not merged` for `if True`) -- the push listing is
    never read and the delete is an unexpected call; keep a run listed `completed` (in
    `live_push_runs`) -- the refusal names run 41 too."""
    gh = _evict_auth()
    for status in STATUSES:
        gh.on(dispatch_listing(status), NO_RUNS)
    gh.on(pull_read(12), pull("main", "closed", LATE, MERGE))
    rows = [{"id": 41, "status": "completed"}, {"id": 42, "status": "queued", "run_attempt": 1}]
    gh.on(push_listing(MERGE), json.dumps({"workflow_runs": rows}))
    gh.on(jobs_listing(42), deploy_jobs(detect="in_progress"))
    assert _refused(monkeypatch, tmp_path, gh) == f"{_LIVE}\n#12's deploy run is live: {_URL}/42"


@pytest.mark.parametrize("caller", ["deploy", "Deploy"])
def test_evict_proceeds_past_a_deploy_run_only_warming(monkeypatch, tmp_path, caller):
    """`warm` applies nothing, so a deploy run whose other jobs have all completed does not hold
    the eviction, whatever the consumer names its calling job. Mutations: count a `warm` leg
    as live (drop `if not _warm(...)`) -- refused; match `deploy / summary` whole -- refused
    (Deploy)."""
    jobs = deploy_jobs(caller, **WARMING)
    gh = _merged_holder_with_one_deploy_run().on(jobs_listing(42), jobs)
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), "{}")
    assert _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV) == {
        "evicted": json.dumps([{"stack": "stacks/auth", "environment": "dev", "holder": 12}]),
        "refused": "[]",
    }
    assert gh.drained()


@pytest.mark.parametrize(
    ("job", "status"),
    [
        pytest.param("detect", "in_progress", id="detect"),
        pytest.param("L0 / dev / stacks/app", "waiting", id="cell"),
        pytest.param("summary", "queued", id="summary"),
        pytest.param("summary", None, id="summary-not-listed"),
    ],
)
def test_evict_refuses_a_deploy_run_with_a_job_besides_warm_unfinished(
    monkeypatch, tmp_path, job, status
):
    """A run with only warm legs left but this one job is live. `summary` not yet listed is
    live too: a job blocked on `needs` may not be listed. Mutations: count every job as warm
    (`_warm` returns True) -- detect and cell pass; drop the `summary` listed-completed clause
    -- summary-not-listed passes."""
    statuses = {**WARMING, job: status}
    if status is None:
        del statuses[job]
    gh = _merged_holder_with_one_deploy_run().on(jobs_listing(42), deploy_jobs(**statuses))
    assert _refused(monkeypatch, tmp_path, gh) == f"{_LIVE}\n#12's deploy run is live: {_URL}/42"


def test_evict_refuses_a_deploy_run_with_a_job_of_another_attempt(monkeypatch, tmp_path):
    """The run is on attempt 2, and its listing still holds an attempt-1 job, so a re-run's
    jobs may not all be listed. Mutation: drop the `run_attempt` clause -- the delete is an
    unexpected call."""
    gh = _evict_auth()
    for status in STATUSES:
        gh.on(dispatch_listing(status), NO_RUNS)
    gh.on(pull_read(12), pull("main", "closed", LATE, MERGE))
    rows = [{"id": 42, "status": "in_progress", "run_attempt": 2}]
    gh.on(push_listing(MERGE), json.dumps({"workflow_runs": rows}))
    jobs = json.loads(deploy_jobs(attempt=2, **WARMING))["jobs"]
    jobs[0]["run_attempt"] = 1
    gh.on(jobs_listing(42), json.dumps({"jobs": jobs}))
    assert _refused(monkeypatch, tmp_path, gh) == f"{_LIVE}\n#12's deploy run is live: {_URL}/42"


def test_evict_refuses_a_deploy_run_that_lists_no_job_yet(monkeypatch, tmp_path):
    """Mutation: read an empty jobs listing as idle (drop the `if not jobs` return) -- the
    refusal names the run live instead."""
    gh = _merged_holder_with_one_deploy_run().on(jobs_listing(42), json.dumps({"jobs": []}))
    assert _refused(monkeypatch, tmp_path, gh) == (
        f"{_LIVE}\n#12's deploy run {_URL}/42 has not started"
    )


def test_evict_refuses_a_deploy_run_whose_jobs_it_cannot_list(monkeypatch, tmp_path):
    """Mutation: read a failed jobs listing as empty (`listed_jobs` returns `[]`) -- the
    refusal says the run has not started."""
    gh = _merged_holder_with_one_deploy_run().on(jobs_listing(42), FAIL)
    assert _refused(monkeypatch, tmp_path, gh) == (
        f"{_LIVE}\ncould not list the jobs of #12's deploy run {_URL}/42"
    )


def test_evict_reads_locks_then_dispatch_runs_then_the_holder_then_its_push_runs(
    monkeypatch, tmp_path
):
    """The whole read order, then the delete: a run dispatched after the lock read re-enters
    the lock under a new sha, and a holder read after the dispatch listings sees a merge they
    missed. Mutations: read the holders before the dispatch listings -- red; delete before the
    live-run check -- red."""
    gh = _evict_auth()
    for status in STATUSES:
        gh.on(dispatch_listing(status), NO_RUNS)
    gh.on(pull_read(12), pull("main", "closed", LATE, MERGE)).on(push_listing(MERGE), NO_RUNS)
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), "{}")
    _main(monkeypatch, tmp_path, "evict", gh, EVICT_ENV)
    assert gh.log == [
        CHECK_RUNS_READ,
        listing("shipmate/locks/dev/"),
        commit_read(OLD),
        pull_read(12),
        *[dispatch_listing(status) for status in STATUSES],
        pull_read(12),
        push_listing(MERGE),
        REPO_ID_READ,
        update(AUTH_REF, OLD, ZERO, False),
    ]
    assert gh.drained()


@pytest.mark.parametrize(
    ("setup", "refusal"),
    [
        pytest.param(
            lambda gh: gh.on(dispatch_listing("pending"), FAIL).on(pull_read(12), pull("main")),
            f"{_LIVE}\ncould not list the live runs of shipmate.yml",
            id="dispatch-listing",
        ),
        pytest.param(
            lambda gh: gh.on(dispatch_listing("pending"), NO_RUNS).on(
                pull_read(12), json.dumps({"merged_at": None})
            ),
            f"{_LIVE}\ncould not tell whether #12 has merged: KeyError('merge_commit_sha')",
            id="holder-read",
        ),
        pytest.param(
            lambda gh: gh.on(dispatch_listing("pending"), NO_RUNS).on(
                pull_read(12), pull("main", "closed", LATE, None)
            ),
            f"{_LIVE}\n#12 merged, but its merge commit reads None, so its deploy run is unknown",
            id="null-merge-sha",
        ),
        pytest.param(
            lambda gh: (
                gh.on(dispatch_listing("pending"), NO_RUNS)
                .on(pull_read(12), pull("main", "closed", LATE, MERGE))
                .on(push_listing(MERGE), FAIL)
            ),
            f"{_LIVE}\ncould not list the deploy runs of #12's merge commit {MERGE}",
            id="push-listing",
        ),
    ],
)
def test_evict_refuses_what_it_cannot_read(monkeypatch, tmp_path, setup, refusal):
    """Each unknown refuses, retryable, and nothing is deleted. Mutations: ignore the dispatch
    listings' `complete` -- red (dispatch-listing); `pull.get("merge_commit_sha")` -- red
    (holder-read); drop the `is_head_sha` check -- the push listing of `None` is an unexpected
    call (null-merge-sha); ignore the push listing's `complete` -- red (push-listing)."""
    gh = _evict_auth()
    for status in STATUSES:
        if status != "pending":
            gh.on(dispatch_listing(status), NO_RUNS)
    setup(gh)
    assert _refused(monkeypatch, tmp_path, gh) == refusal


def test_evict_of_an_unknown_holder_only_lists_no_runs():
    """An unknown holder keeps the rule without a live-run check: the double queues no listing.
    Mutation: run `_refuse_live_runs` whenever a lock is evicted (`if locks`) -- the listing is
    an unexpected call."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((DNS_REF, OTHER)))
    gh.on(commit_read(OTHER), commit(OTHER, "garbage")).on(REPO_ID_READ, REPO_ID)
    gh.on(update(DNS_REF, OTHER, ZERO, False), "{}")
    got = cl.evict(REPO, "dev", PR, {"stacks/dns"}, "feat-a", "main", run=gh)
    assert got == ([unknown(DNS_REF, OTHER)], [])
    assert gh.drained()


def test_evict_lists_shipmate_yml_whatever_workflow_the_comment_ran_in(monkeypatch, tmp_path):
    """Dispatches and merge deploys run in `shipmate.yml`, not in the comment's workflow.
    Mutation: list the file `GITHUB_WORKFLOW_REF` names in `holder_dispatch_runs` -- the
    `comment.yml` listing is an unexpected call."""
    gh = quiet(_evict_auth(), 12).on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, OLD, ZERO, False), "{}")
    env = {**EVICT_ENV, "GITHUB_WORKFLOW_REF": f"{REPO}/.github/workflows/comment.yml@main"}
    assert _main(monkeypatch, tmp_path, "evict", gh, env) == {
        "evicted": json.dumps([{"stack": "stacks/auth", "environment": "dev", "holder": 12}]),
        "refused": "[]",
    }
    assert gh.drained()


def test_evict_names_every_refusal_line_across_holders(monkeypatch, tmp_path):
    """#12's live apply run and #9's failed read are both named. Mutation: `lines =` for
    `lines +=` in `_refuse_live_runs`' holder loop -- #12's line is lost."""
    gh = _evict_three(*_THREE_PENDING)
    gh.on(pull_read(12), pull("main")).on(pull_read(9), pull("main"))
    for status in STATUSES:
        if status != "queued":
            gh.on(dispatch_listing(status), NO_RUNS)
    rows = [_dispatched(31, f"shipmate · auto pr=12 ref={OLD} comment=")]
    gh.on(dispatch_listing("queued"), json.dumps({"workflow_runs": rows}))
    gh.on(pull_read(9), SystemExit("::error::command failed (1): gh api\ngh: Not Found (HTTP 404)"))
    gh.on(pull_read(12), pull("main"))
    assert _refused(monkeypatch, tmp_path, gh) == (
        f"{_LIVE}\n#12 has a live `auto` run: {_URL}/31\n"
        "could not tell whether #9 has merged: command failed (1): gh api | gh: Not Found (HTTP "
        "404)"
    )


# --- verify --------------------------------------------------------------------------------------

BASE = "f" * 40
RUN = "4242"
PULL_READ = ["gh", "api", f"repos/{REPO}/pulls/{PR}"]
AUTOPLANS_READ = autoplans_read(REPO, HEAD)
AUTO = {"SHIPMATE_AUTO": "true", "SHIPMATE_PROVENANCE_RUN": RUN, "SHIPMATE_BASE_SHA": BASE}
RETRY = "push, or comment `shipmate apply` on the pull request"
WHERE = "apply aborted for `dev` / `stacks/auth`:"


TIP_READ = ["gh", "api", f"repos/{REPO}/git/ref/heads/main"]
RECORD_READ = [
    "gh",
    "api",
    "--paginate",
    (
        f"repos/{REPO}/commits/{HEAD}/check-runs?filter=all&per_page=100"
        "&check_name=apply%20%2F%20stacks%2Fauth%20%2F%20dev"
    ),
    "--jq",
    ".check_runs[]",
]


def _pull(state="open", head=HEAD, draft=False):
    """A pull request whose `base.sha` is stale: probed on a sample pull request, it kept the
    old tip for 20 s and more after a push to the base, so the check reads the branch ref."""
    return json.dumps(
        {
            "state": state,
            "draft": draft,
            "head": {"sha": head},
            "base": {"sha": "9" * 40, "ref": "main"},
        }
    )


def _autoplans(*ids, **keys):
    """The listing's lines: one autoplan of `HEAD` by `k3np` per id, with `keys` over each."""
    return "\n".join(json.dumps(listed_autoplan(int(i), HEAD, **keys)) for i in ids)


PERMISSION_READ = ["gh", "api", f"repos/{REPO}/collaborators/k3np/permission"]


def _tip(sha=BASE):
    return json.dumps({"ref": "refs/heads/main", "object": {"sha": sha, "type": "commit"}})


def _record(plan_run, run_id=1, action="synchronize"):
    record = {"plan_run": plan_run} | ({} if action is None else {"action": action})
    return {
        **_check("apply / stacks/auth / dev", "queued", None, run_id=run_id),
        "external_id": json.dumps(record),
    }


def _records(*plan_runs, action="synchronize"):
    return jsonl(*(_record(r, i, action) for i, r in enumerate(plan_runs, 1)))


def _held():
    """A double answering the lock read with our pull request's lock."""
    gh = Gh().on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    return gh.on(commit_read(OLD), commit(OLD, message(PR, "dev", "stacks/auth")))


def _verify(monkeypatch, tmp_path, gh, env=None):
    base = {"SHIPMATE_ENV": "dev", "STACK": "stacks/auth", "SHIPMATE_AUTO": "false"}
    return _main(monkeypatch, tmp_path, "verify", gh, {**base, **(env or {})})


def _verify_refusal(monkeypatch, tmp_path, gh, env=None):
    with pytest.raises(SystemExit) as exc:
        _verify(monkeypatch, tmp_path, gh, env)
    assert gh.drained()
    return exc.value.code


def test_verify_passes_a_cell_our_pull_request_holds_at_the_head(monkeypatch, tmp_path):
    """Outside the auto form the base tip is not read (the strict double refuses an extra read).
    Mutation: refuse when the holder is our pull request (`held.pr != pr` for `==`). Mutation:
    return before the pull request read (its queued answer is left unconsumed)."""
    gh = _held().on(PULL_READ, _pull())
    assert _verify(monkeypatch, tmp_path, gh) == {}
    assert gh.drained()


@pytest.mark.parametrize(
    ("listed", "msg", "holder"),
    [
        pytest.param("[]", None, "the cell is not locked", id="absent"),
        pytest.param(
            refs((AUTH_REF, OLD)),
            message(12, "dev", "stacks/auth"),
            "it is locked by #12",
            id="other",
        ),
        pytest.param(
            refs((AUTH_REF, OLD)), "garbage", "it is locked by an unknown holder", id="unknown"
        ),
    ],
)
def test_verify_refuses_a_cell_our_pull_request_does_not_hold(
    monkeypatch, tmp_path, listed, msg, holder
):
    """Mutations: pass when the ref is absent (`held is None or held.pr == pr`: absent passes);
    pass any lock with a holder (`held.pr is not None`: other passes); pass an unknown holder
    (`held.pr in (pr, None)`: unknown passes)."""
    gh = Gh().on(ref_read(AUTH_REF), listed)
    if msg is not None:
        gh.on(commit_read(OLD), commit(OLD, msg))
    assert _verify_refusal(monkeypatch, tmp_path, gh) == (
        f"::error::{WHERE} pull request #7 no longer holds its "
        f"cell lock ({holder}); comment `shipmate apply` again to lock it or name its holder"
    )


@pytest.mark.parametrize(
    ("commit_answer", "why"),
    [
        pytest.param(FAIL, "command failed (1): gh api", id="read-fails"),
        *[
            pytest.param(SystemExit(code), "read failed", id=f"read-exits-{code}")
            for code in (None, 1)
        ],
        pytest.param("{}", "KeyError('committer')", id="malformed"),
    ],
)
def test_verify_refuses_a_lock_it_cannot_read(monkeypatch, tmp_path, commit_answer, why):
    """Mutations: re-raise the read's `SystemExit` unchanged in `_confirm` (read-fails loses the
    cell); pass the deploy retry everywhere (`_confirm`'s default, every case ends without
    `RETRY`); `str(exc.code)` for the `why` (read-fails keeps `::error::`, read-exits-* print
    `(None)` and `(1)`); drop `LookupError` from `_confirm`'s second `except` (malformed escapes
    as a `KeyError`)."""
    gh = Gh().on(ref_read(AUTH_REF), refs((AUTH_REF, OLD))).on(commit_read(OLD), commit_answer)
    assert _verify_refusal(monkeypatch, tmp_path, gh) == (
        f"::error::{WHERE} could not confirm pull request #7 holds its cell lock ({why}); {RETRY}"
    )


@pytest.mark.parametrize(
    ("pull", "refusal"),
    [
        pytest.param(
            _pull(head=OTHER),
            f"pull request #7 is at {OTHER}, not at {HEAD}, the head this apply was requested "
            f"for; {RETRY}",
            id="head-moved",
        ),
        pytest.param(
            _pull(state="closed"), "pull request #7 is closed; nothing applies", id="closed"
        ),
    ],
)
def test_verify_refuses_a_pull_request_not_open_at_the_head(monkeypatch, tmp_path, pull, refusal):
    """Mutation: drop the head comparison (head-moved passes). Mutation: drop the state
    comparison (closed passes)."""
    gh = _held().on(PULL_READ, pull)
    assert _verify_refusal(monkeypatch, tmp_path, gh) == f"::error::{WHERE} {refusal}"


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        pytest.param(FAIL, "command failed (1): gh api", id="read-fails"),
        pytest.param(json.dumps({"state": "open"}), "KeyError('head')", id="malformed"),
    ],
)
def test_verify_refuses_a_pull_request_it_cannot_read(monkeypatch, tmp_path, answer, why):
    """Mutation: answer a failed pull request read as open at the head (`except SystemExit:
    return pull, "open", head` around `gh_json` in `_verify_pull`; read-fails passes).
    Mutation: read the fields with `.get`, defaulting the head to `head` (malformed passes)."""
    gh = _held().on(PULL_READ, answer)
    assert _verify_refusal(monkeypatch, tmp_path, gh) == (
        f"::error::{WHERE} could not confirm pull request #7 is open at {HEAD} ({why}); {RETRY}"
    )


def test_verify_auto_passes_the_newest_autoplan_at_the_detected_base(monkeypatch, tmp_path):
    """The record check reads the newest record per name: an older check run recording another
    plan run is outvoted. Mutation: route `SHIPMATE_AUTO` `true` like `false` (the listing and
    check-run reads are left unconsumed). Mutation: read the record with no `name` (an
    unexpected call)."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip()).on(AUTOPLANS_READ, _autoplans(RUN))
    gh.on(RECORD_READ, _records("1", RUN)).on(PERMISSION_READ, json.dumps({"permission": "write"}))
    assert _verify(monkeypatch, tmp_path, gh, AUTO) == {}
    assert gh.drained()


@pytest.mark.parametrize("action", ["ready_for_review", "", None])
def test_verify_auto_refuses_a_plan_no_push_started(monkeypatch, tmp_path, action):
    """A ready-for-review autoplan of the head can become the newest autoplan after detect.

    Mutation: drop the action check from `provenance_reason` (the permission read runs,
    unexpected)."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip()).on(AUTOPLANS_READ, _autoplans(RUN))
    gh.on(RECORD_READ, _records(RUN, action=action))
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} the cell's plan record names event action {action!r}, not a push "
        "(opened or synchronize); comment `shipmate apply`"
    )


@pytest.mark.parametrize("auto", [pytest.param(AUTO, id="auto"), pytest.param({}, id="comment")])
@pytest.mark.parametrize("attempt", ["2", "", "1x", None])
def test_verify_refuses_a_later_run_attempt_before_any_read(monkeypatch, tmp_path, attempt, auto):
    """ "Re-run failed jobs" keeps the first attempt's passed detect, so its actor check never
    runs again, and a re-run's release and completion jobs write no apply check. Mutation: drop
    the attempt refusal (each row reaches the lock read, unexpected). Mutation: refuse only an
    auto apply (`auto and attempt != "1"`; the comment rows reach the lock read). Mutation:
    compare `int(attempt) > 1` (the `""`, `1x` and unset rows raise `ValueError`)."""
    env = {**auto, "GITHUB_RUN_ATTEMPT": attempt}
    assert _verify_refusal(monkeypatch, tmp_path, Gh(), env) == (
        f"::error::{WHERE} an apply is never re-run (run attempt "
        f"{(attempt or '')!r}): comment `shipmate apply`"
    )


def test_verify_passes_the_first_run_attempt(monkeypatch, tmp_path):
    """Mutation: refuse attempt 1 too (`attempt != "0"`; refuses)."""
    gh = _held().on(PULL_READ, _pull())
    assert _verify(monkeypatch, tmp_path, gh, {"GITHUB_RUN_ATTEMPT": "1"}) == {}


def test_verify_auto_refuses_a_draft(monkeypatch, tmp_path):
    """A pull request converted to draft while a wave waits is no longer ready to apply.

    Mutation: drop the draft refusal (the base tip read runs, unexpected)."""
    gh = _held().on(PULL_READ, _pull(draft=True))
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} pull request #7 is a draft; mark it ready for review, then comment "
        "`shipmate apply`"
    )


@pytest.mark.parametrize("draft", [None, "false"])
def test_verify_auto_refuses_a_draft_state_that_is_not_a_boolean(monkeypatch, tmp_path, draft):
    """Mutation: `if not isinstance(draft, bool)` dropped from `_draft` (both rows pass on to the
    base tip read, unexpected)."""
    gh = _held().on(PULL_READ, _pull(draft=draft))
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} could not confirm pull request #7 is ready for review "
        f"(ValueError({f'draft is {draft!r}'!r})); {RETRY}"
    )


def test_verify_outside_auto_reads_no_draft_state(monkeypatch, tmp_path):
    """Mutation: return only for a non-draft (`auto == "false" and not pull["draft"]`; the
    draft refuses)."""
    gh = _held().on(PULL_READ, _pull(draft=True))
    assert _verify(monkeypatch, tmp_path, gh) == {}


def _pusher_gh(autoplans, permission=None):
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip()).on(AUTOPLANS_READ, autoplans)
    gh.on(RECORD_READ, _records(RUN))
    if permission is not None:
        gh.on(PERMISSION_READ, permission)
    return gh


@pytest.mark.parametrize(
    ("autoplans", "permission", "reason"),
    [
        pytest.param(
            _autoplans(RUN),
            json.dumps({"permission": "read"}),
            "not authorized: `shipmate apply` needs write access to this repository, and the "
            "pusher @k3np's permission is `read`.",
            id="read",
        ),
        pytest.param(
            _autoplans(RUN),
            FAIL,
            "not authorized: could not read the pusher @k3np's permission on this repository, "
            "so `shipmate apply` was not run. This run's log has the API response; push again, "
            "or a writer comments `shipmate apply`.",
            id="unreadable",
        ),
        pytest.param(
            _autoplans(RUN, triggering_actor={"login": "octocat"}),
            None,
            "not authorized: the autoplan ran as @k3np but was triggered by @octocat, so it was "
            "re-run, and auto authorizes only the push itself; a writer comments `shipmate "
            "apply`.",
            id="re-run",
        ),
    ],
)
def test_verify_auto_rereads_the_pushers_permission(
    monkeypatch, tmp_path, autoplans, permission, reason
):
    """The wave decides the pusher of the listed autoplan with detect's function and wording.

    Mutation: drop the pusher check from `_verify_auto` (every row passes)."""
    gh = _pusher_gh(autoplans, permission)
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == f"::error::{WHERE} {reason}"


def test_verify_auto_refuses_a_moved_base(monkeypatch, tmp_path):
    """The tip is the branch ref's; `base.sha` would read stale (`_pull`). Mutation: drop the
    base comparison (the cell passes). Mutation: return `pull["base"]["sha"]` from `base_tip`
    (the stale sha refuses as the moved tip). Mutation: end with `_RETRY`, which offers a bare
    `shipmate apply` that skips the overlap check -- red."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip(OTHER))
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} the base branch moved from {BASE} to {OTHER} since this auto apply "
        "was detected; update the branch from the base and push"
    )


@pytest.mark.parametrize(
    ("autoplans", "newest"),
    [
        pytest.param(_autoplans(RUN, "4243"), "run 4243", id="newer"),
        pytest.param(_autoplans(), "not listed", id="none"),
    ],
)
def test_verify_auto_refuses_a_newer_autoplan(monkeypatch, tmp_path, autoplans, newest):
    """Mutation: `min` for `max` in `newest_autoplan` (newer passes). Mutation: skip the
    comparison when nothing is listed (`newest is not None and ...`: none passes)."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip()).on(AUTOPLANS_READ, autoplans)
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} the newest autoplan of {HEAD} is {newest}, not plan run {RUN}, which "
        f"this auto apply applies; {RETRY}"
    )


@pytest.mark.parametrize(
    ("keys", "refusal"),
    [
        pytest.param(
            {"event": "workflow_dispatch"},
            f"the newest autoplan of {HEAD} is a `workflow_dispatch` run, not a "
            "pull_request_target run",
            id="event",
        ),
        pytest.param(
            {"head_sha": OTHER},
            f"the newest autoplan of {HEAD} is a run of {OTHER}, not of {HEAD}",
            id="head",
        ),
        pytest.param(
            {"head_sha": None},
            f"the newest autoplan of {HEAD} is a run of missing, not of {HEAD}",
            id="null-head",
        ),
    ],
)
def test_verify_auto_refuses_a_listed_run_the_listing_filter_should_drop(
    monkeypatch, tmp_path, keys, refusal
):
    """The listing's `event=` and `head_sha=` filters are GitHub's, so the listed object is
    checked again, before the record read. Mutation: drop the event check from
    `provenance_reason` (event reads the record, unexpected). Mutation: drop the head check
    (head reads the record, unexpected). Mutation: word `head` as `event` (head red).
    Mutation: render the head sha as given (null-head reads `None`)."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip())
    gh.on(AUTOPLANS_READ, _autoplans(RUN, **keys))
    code = _verify_refusal(monkeypatch, tmp_path, gh, AUTO)
    assert code == f"::error::{WHERE} {refusal}; {RETRY}"


@pytest.mark.parametrize(
    ("records", "named"),
    [
        pytest.param((RUN, "4243"), "plan run 4243", id="newer-record"),
        pytest.param((), "no plan run", id="no-record"),
    ],
)
def test_verify_auto_refuses_a_cell_planned_by_another_run(monkeypatch, tmp_path, records, named):
    """Mutation: drop the record comparison (both pass)."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip()).on(AUTOPLANS_READ, _autoplans(RUN))
    gh.on(RECORD_READ, _records(*records))
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} the cell's newest plan record names {named}, not plan run {RUN}, "
        f"which this auto apply applies; {RETRY}"
    )


@pytest.mark.parametrize(
    ("failing", "what"),
    [
        pytest.param("list", f"plan run {RUN} is the newest autoplan of {HEAD}", id="list"),
        pytest.param(
            "check-runs", f"the cell's newest plan record names plan run {RUN}", id="check-runs"
        ),
    ],
)
def test_verify_auto_refuses_a_provenance_it_cannot_read(monkeypatch, tmp_path, failing, what):
    """Mutation: answer a failed read with None in `_confirm` (`return None` for the raise;
    list then reads "not listed", check-runs "no plan run")."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip())
    gh.on(AUTOPLANS_READ, FAIL if failing == "list" else _autoplans(RUN))
    if failing == "check-runs":
        gh.on(RECORD_READ, FAIL)
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} could not confirm {what} (command failed (1): gh api); {RETRY}"
    )


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        pytest.param(FAIL, "command failed (1): gh api", id="read-fails"),
        pytest.param(
            json.dumps({"ref": "refs/heads/mainline", "object": {"sha": BASE, "type": "commit"}}),
            "refs/heads/mainline is not the commit ref of branch 'main'",
            id="other-ref",
        ),
    ],
)
def test_verify_auto_refuses_a_base_tip_it_cannot_read(monkeypatch, tmp_path, answer, why):
    """Mutation: drop the ref-name check in `base_tip` (other-ref passes)."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, answer)
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} could not confirm the base tip of pull request #7 ({why}); {RETRY}"
    )


@pytest.mark.parametrize(
    "env",
    [
        pytest.param({"SHIPMATE_PROVENANCE_RUN": ""}, id="no-run"),
        pytest.param({"SHIPMATE_BASE_SHA": ""}, id="no-base"),
    ],
)
def test_verify_auto_refuses_without_its_plan_run_or_base_before_any_read(
    monkeypatch, tmp_path, env
):
    """Mutation: drop the `is_run_id(provenance)` check (no-run reaches the lock read).
    Mutation: drop the `is_head_sha(base)` check (no-base reaches the lock read)."""
    got = {**AUTO, **env}
    assert _verify_refusal(monkeypatch, tmp_path, Gh(), got) == (
        f"::error::{WHERE} an auto apply reached the wave without its plan run and base tip "
        f"(got: {got['SHIPMATE_PROVENANCE_RUN']!r}, {got['SHIPMATE_BASE_SHA']!r}); {RETRY}"
    )


@pytest.mark.parametrize(
    "env",
    [
        pytest.param({"SHIPMATE_AUTO": ""}, id="auto-empty"),
        pytest.param({"SHIPMATE_AUTO": "True"}, id="auto-cased"),
        pytest.param({"SHIPMATE_HEAD_SHA": ""}, id="head-empty"),
    ],
)
def test_verify_refuses_a_malformed_head_or_mode_before_any_read(monkeypatch, tmp_path, env):
    """Mutation: drop the `auto not in` check (auto-empty and auto-cased run the non-auto
    check). Mutation: drop the `is_head_sha(head)` check (head-empty reaches the lock read)."""
    got = {"SHIPMATE_HEAD_SHA": HEAD, "SHIPMATE_AUTO": "false", **env}
    assert _verify_refusal(monkeypatch, tmp_path, Gh(), env) == (
        f"::error::{WHERE} SHIPMATE_HEAD_SHA must be a commit sha and SHIPMATE_AUTO true or "
        f"false (got: {got['SHIPMATE_HEAD_SHA']!r}, {got['SHIPMATE_AUTO']!r})"
    )


# --- verify, deploy mode -------------------------------------------------------------------------

MERGED = 4
DEPLOY = {"SHIPMATE_DEPLOY": "true", "SHIPMATE_PR_NUMBER": str(MERGED)}
HELD_BY_9 = (
    "::error::apply aborted for `dev` / `stacks/auth`: its cell lock is held by #9, not by "
    "this deploy's pull request. Open a pull request that changes this stack. Comment "
    "`shipmate unlock dev --evict` on it to release the locks #9 holds; applying it after that "
    "may overwrite what #9 applied to dev. The eviction releases only locks on the new pull "
    "request's pending cells in dev, any holder's, so wait for any holder still open to merge or "
    "close, and for the apply, auto and deploy runs of #9 to end, first. Then apply it"
)


def _locked_by(msg):
    """A double answering the lock read with a lock whose commit message is `msg`."""
    gh = Gh().on(ref_read(AUTH_REF), refs((AUTH_REF, OLD)))
    return gh.on(commit_read(OLD), commit(OLD, msg))


def test_verify_deploy_refuses_a_cell_another_pull_request_locks(monkeypatch, tmp_path):
    """Mutation: drop the holder compare (pass any lock): the lock by #9 passes. Mutation:
    drop the overwrite clause from `evict_release` -- red."""
    gh = _locked_by(message(9, "dev", "stacks/auth"))
    assert _verify_refusal(monkeypatch, tmp_path, gh, DEPLOY) == HELD_BY_9


@pytest.mark.parametrize(
    "locked", [pytest.param(True, id="merged-holds"), pytest.param(False, id="unlocked")]
)
def test_verify_deploy_passes_a_cell_the_merge_holds_or_nobody_locks(monkeypatch, tmp_path, locked):
    """The strict double queues no `pulls/` read, so any one is an unexpected call. Mutation:
    run `_verify_pull` after the deploy check (a `pulls/` read; the merged, closed pull request
    refuses). Mutation: refuse the merged pull request's own lock (`held.pr == pr` for `!=`):
    merged-holds refuses."""
    if locked:
        gh = _locked_by(message(MERGED, "dev", "stacks/auth"))
    else:
        gh = Gh().on(ref_read(AUTH_REF), "[]")
    assert _verify(monkeypatch, tmp_path, gh, DEPLOY) == {}
    assert gh.drained()


def test_verify_deploy_passes_a_later_run_attempt(monkeypatch, tmp_path):
    """A merge deploy is recovered by re-running it. Mutation: check the attempt before the
    deploy branch returns (refuses)."""
    gh = Gh().on(ref_read(AUTH_REF), "[]")
    assert _verify(monkeypatch, tmp_path, gh, {**DEPLOY, "GITHUB_RUN_ATTEMPT": "2"}) == {}
    assert gh.drained()


def test_verify_deploy_names_the_cells_environment(monkeypatch, tmp_path):
    """Mutation: hard-code `dev` in `_verify_deploy`'s refusal -- red."""
    ref = cl.ref_name("prod", "stacks/auth")
    gh = Gh().on(ref_read(ref), refs((ref, OLD)))
    gh.on(commit_read(OLD), commit(OLD, message(9, "prod", "stacks/auth")))
    assert _verify_refusal(monkeypatch, tmp_path, gh, {**DEPLOY, "SHIPMATE_ENV": "prod"}) == (
        "::error::apply aborted for `prod` / `stacks/auth`: its cell lock is held by #9, not by "
        "this deploy's pull request. Open a pull request that changes this stack. Comment "
        "`shipmate unlock prod --evict` on it to release the locks #9 holds; applying it after "
        "that may overwrite what #9 applied to prod. The eviction releases only locks on the new "
        "pull request's pending cells in prod, any holder's, so wait for any holder still open to "
        "merge or close, and for the apply, auto and deploy runs of #9 to end, first. Then apply it"
    )


def test_lock_wait_remedy_names_the_evict_for_a_merged_or_closed_holder():
    """Mutations: drop the merged-or-closed sentence -- red; drop the run wait -- red."""
    assert _shipmate.lock_wait_remedy("#12", "dev&lt;1") == (
        "wait for #12 to merge or close, then re-plan. If #12 has already merged or closed, "
        "comment `shipmate unlock dev&lt;1 --evict` once #12's apply, auto and deploy runs have "
        "ended"
    )


def test_verify_deploy_checks_a_push_with_pull_request_zero(monkeypatch, tmp_path):
    """A push that merged no pull request passes an empty number. Mutation: drop the empty to
    `0` rule in `main` (`env_pr_number` refuses the empty number before any read)."""
    gh = _locked_by(message(9, "dev", "stacks/auth"))
    env = {**DEPLOY, "SHIPMATE_PR_NUMBER": ""}
    assert _verify_refusal(monkeypatch, tmp_path, gh, env) == HELD_BY_9


def test_verify_deploy_refuses_an_unknown_holder(monkeypatch, tmp_path):
    """Mutation: pass an unknown holder (`held.pr in (pr, None)`)."""
    gh = _locked_by("garbage")
    assert _verify_refusal(monkeypatch, tmp_path, gh, DEPLOY) == (
        "::error::apply aborted for `dev` / `stacks/auth`: its cell lock is held by an unknown "
        "holder, not by this deploy's pull request. Open a pull request that changes this stack. "
        "Comment `shipmate unlock dev --evict` on it to release the locks an unknown holder "
        "holds; applying it after that may overwrite what an unknown holder applied to dev. The "
        "eviction releases only locks on the new pull request's pending cells in dev, any "
        "holder's, so wait for any holder still open to merge or close first. Then apply it"
    )


def test_verify_deploy_refuses_a_lock_it_cannot_read(monkeypatch, tmp_path):
    """No merged pull request can push or take a comment, so the remedy is a re-run; the
    pull-request path's `RETRY` is pinned by `test_verify_refuses_a_lock_it_cannot_read`.
    Mutation: treat a failed read as unlocked (return None from the read on `SystemExit`).
    Mutation: drop the deploy retry from `_verify_deploy` (ends with `RETRY`)."""
    gh = Gh().on(ref_read(AUTH_REF), FAIL)
    assert _verify_refusal(monkeypatch, tmp_path, gh, DEPLOY) == (
        "::error::apply aborted for `dev` / `stacks/auth`: could not confirm which pull request "
        "holds its cell lock (command failed (1): gh api); re-run the failed deploy jobs"
    )


def test_verify_deploy_names_a_failed_read_on_one_line(monkeypatch, tmp_path, sleeps):
    """A gh failure whose stderr runs to a second line stays one line in the refusal, which a
    502 reaches after its retries unchanged. Mutation: return `why` unjoined from `_why` -- the
    refusal splits, red."""
    failure = SystemExit("::error::command failed (1): gh api\nHTTP 502")
    gh = Gh().on(ref_read(AUTH_REF), failure, failure, failure)
    assert _verify_refusal(monkeypatch, tmp_path, gh, DEPLOY) == (
        "::error::apply aborted for `dev` / `stacks/auth`: could not confirm which pull request "
        "holds its cell lock (command failed (1): gh api | HTTP 502); re-run the failed deploy "
        "jobs"
    )
    assert sleeps == [2, 5]


def test_verify_names_a_malformed_deploy_mode_before_an_empty_pull_request(monkeypatch, tmp_path):
    """The pull request number is resolved after the mode, so the mode's refusal names its own
    variable. Mutation: resolve the number in `main` again (`SHIPMATE_DEPLOY == "true"` reads
    `True` as false and `env_pr_number` refuses the empty number first), red."""
    env = {"SHIPMATE_DEPLOY": "True", "SHIPMATE_PR_NUMBER": ""}
    assert _verify_refusal(monkeypatch, tmp_path, Gh(), env) == (
        "::error::SHIPMATE_DEPLOY must be true or false (got: 'True')"
    )


@pytest.mark.parametrize("value", ["yes", "", "True"])
def test_verify_refuses_a_malformed_deploy_mode_before_any_read(monkeypatch, tmp_path, value):
    """Mutation: run the deploy check for any value but `false` (yes reaches the lock read).
    Mutation: treat an empty value as unset (empty runs today's check)."""
    assert _verify_refusal(monkeypatch, tmp_path, Gh(), {"SHIPMATE_DEPLOY": value}) == (
        f"::error::SHIPMATE_DEPLOY must be true or false (got: {value!r})"
    )
