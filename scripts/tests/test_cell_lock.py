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
    "name: $name) { pullRequest(number: $pr) { state headRefOid timelineItems(itemTypes: "
    "[CLOSED_EVENT], last: 1) { nodes { ... on ClosedEvent { createdAt } } } } } }"
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
    "re-plan, or comment `shipmate unlock dev --evict`"
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
    gh.on(REPO_ID_READ, REPO_ID)
    gh.on(update(AUTH_REF, OLD, ZERO, False), "{}").on(update(DNS_REF, OTHER, ZERO, False), "{}")
    stacks = {"stacks/auth", "stacks/dns", "stacks/own"}
    assert cl.evict(REPO, "dev", PR, stacks, run=gh) == (
        [lock(AUTH_REF, OLD, pr=12), unknown(DNS_REF, OTHER)],
        [],
    )
    assert gh.drained()


def _two_foreign_locks_first_refused():
    """Two locks of #12; the first delete fails and the ref still holds the sha read."""
    gh = Gh().on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (DNS_REF, OTHER)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, message(12, "dev", "stacks/dns")))
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
    assert cl.evict(REPO, "dev", PR, {"stacks/auth", "stacks/dns"}, run=gh) == (
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
            _check("apply / stacks/auth / dev", "completed", "success"),
            _check("apply / stacks/dns / dev", "completed", "success", run_id=2),
        ),
    )
    for argv, answers in _two_foreign_locks_first_refused().calls.items():
        gh.on(list(argv), *answers)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "evict", gh, {"SHIPMATE_ENV": "dev"})
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
        pytest.param({"SHIPMATE_MERGED": "true"}, DNS_REF, id="merged"),
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
    foreign check on stacks/dns keeps its lock)."""
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
    ],
)
def test_release_cli_refuses_a_bad_mode_before_any_call(monkeypatch, tmp_path, env, refusal):
    """Mutations: treat any `SHIPMATE_MERGED` but `true` as unmerged (merged-yes and
    merged-unset release by `SHIPMATE_CLOSED_AT`); drop the `_when` check (closed-at-no-offset
    reaches the API)."""
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, tmp_path, "release", Gh(), env)
    assert exc.value.code == "::error::" + refusal


def test_evict_cli_evicts_only_cells_with_an_app_check(monkeypatch, tmp_path):
    """Mutation: `parse_jsonl` for `app_runs` in `_evict_cmd` (the foreign check on stacks/dns
    evicts its lock)."""
    gh = Gh().on(
        CHECK_RUNS_READ,
        jsonl(
            _check("apply / stacks/auth / dev", "completed", "success"),
            _check("apply / stacks/dns / dev", "queued", None, run_id=2, app=15368),
        ),
    )
    gh.on(listing("shipmate/locks/dev/"), refs((AUTH_REF, OLD), (DNS_REF, OTHER)))
    gh.on(commit_read(OLD), commit(OLD, message(12, "dev", "stacks/auth")))
    gh.on(commit_read(OTHER), commit(OTHER, message(12, "dev", "stacks/dns")))
    gh.on(REPO_ID_READ, REPO_ID).on(update(AUTH_REF, OLD, ZERO, False), "{}")
    out = _main(monkeypatch, tmp_path, "evict", gh, {"SHIPMATE_ENV": "dev"})
    assert out == {
        "evicted": json.dumps([{"stack": "stacks/auth", "environment": "dev", "holder": 12}]),
        "refused": "[]",
    }
    assert gh.drained()


# --- verify --------------------------------------------------------------------------------------

BASE = "f" * 40
RUN = "4242"
PULL_READ = ["gh", "api", f"repos/{REPO}/pulls/{PR}"]
AUTOPLANS_READ = [
    "gh",
    "api",
    "--paginate",
    (
        f"repos/{REPO}/actions/workflows/shipmate.yml/runs?event=pull_request_target"
        f"&head_sha={HEAD}&per_page=100"
    ),
    "--jq",
    ".workflow_runs[].id",
]
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


RUN_READ = ["gh", "api", f"repos/{REPO}/actions/runs/{RUN}"]
PERMISSION_READ = ["gh", "api", f"repos/{REPO}/collaborators/k3np/permission"]


def _plan_run(actor="k3np", trigger="k3np"):
    return json.dumps({"actor": {"login": actor}, "triggering_actor": {"login": trigger}})


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
    cell); `str(exc.code)` for the `why` (read-fails keeps `::error::`, read-exits-* print
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
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip()).on(AUTOPLANS_READ, RUN)
    gh.on(RECORD_READ, _records("1", RUN)).on(RUN_READ, _plan_run())
    gh.on(PERMISSION_READ, json.dumps({"permission": "write"}))
    assert _verify(monkeypatch, tmp_path, gh, AUTO) == {}
    assert gh.drained()


@pytest.mark.parametrize("action", ["ready_for_review", "", None])
def test_verify_auto_refuses_a_plan_no_push_started(monkeypatch, tmp_path, action):
    """A ready-for-review autoplan of the head can become the newest autoplan after detect.

    Mutation: drop the action refusal from `_verify_auto` (the run read runs, unexpected)."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip()).on(AUTOPLANS_READ, RUN)
    gh.on(RECORD_READ, _records(RUN, action=action))
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} the cell's plan record names event action {action!r}, not a push "
        "(opened or synchronize); comment `shipmate apply`"
    )


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


def _pusher_gh(plan_run, permission=None):
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip()).on(AUTOPLANS_READ, RUN)
    gh.on(RECORD_READ, _records(RUN)).on(RUN_READ, plan_run)
    if permission is not None:
        gh.on(PERMISSION_READ, permission)
    return gh


@pytest.mark.parametrize(
    ("plan_run", "permission", "reason"),
    [
        pytest.param(
            _plan_run(),
            json.dumps({"permission": "read"}),
            "not authorized: `shipmate apply` needs write access to this repository, and the "
            "pusher @k3np's permission is `read`.",
            id="read",
        ),
        pytest.param(
            _plan_run(),
            FAIL,
            "not authorized: could not read the pusher @k3np's permission on this repository, "
            "so `shipmate apply` was not run. This run's log has the API response; push again, "
            "or a writer comments `shipmate apply`.",
            id="unreadable",
        ),
        pytest.param(
            _plan_run(trigger="octocat"),
            None,
            "not authorized: the autoplan ran as @k3np but was triggered by @octocat, so it was "
            "re-run, and auto authorizes only the push itself; a writer comments `shipmate "
            "apply`.",
            id="re-run",
        ),
    ],
)
def test_verify_auto_rereads_the_pushers_permission(
    monkeypatch, tmp_path, plan_run, permission, reason
):
    """The wave decides the pusher with detect's function and wording.

    Mutation: drop the pusher check from `_verify_auto` (every row passes)."""
    gh = _pusher_gh(plan_run, permission)
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == f"::error::{WHERE} {reason}"


@pytest.mark.parametrize(
    ("plan_run", "why"),
    [
        pytest.param(FAIL, "command failed (1): gh api", id="read-fails"),
        pytest.param(
            '"x"', "AttributeError(\"'str' object has no attribute 'get'\")", id="malformed"
        ),
    ],
)
def test_verify_auto_refuses_a_pusher_it_cannot_read(monkeypatch, tmp_path, plan_run, why):
    """Mutation: answer the run read's failure with no reason (`except SystemExit: return None`
    around it; read-fails passes)."""
    gh = _pusher_gh(plan_run)
    assert _verify_refusal(monkeypatch, tmp_path, gh, AUTO) == (
        f"::error::{WHERE} could not confirm the pusher of plan run {RUN} may apply ({why}); "
        f"{RETRY}"
    )


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
        pytest.param(f"{RUN}\n4243", "run 4243", id="newer"),
        pytest.param("", "not listed", id="none"),
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
    ("records", "named"),
    [
        pytest.param((RUN, "4243"), "plan run 4243", id="newer-record"),
        pytest.param((), "no plan run", id="no-record"),
    ],
)
def test_verify_auto_refuses_a_cell_planned_by_another_run(monkeypatch, tmp_path, records, named):
    """Mutation: drop the record comparison (both pass)."""
    gh = _held().on(PULL_READ, _pull()).on(TIP_READ, _tip()).on(AUTOPLANS_READ, RUN)
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
    gh.on(AUTOPLANS_READ, FAIL if failing == "list" else RUN)
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
