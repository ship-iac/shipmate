import json

import _shipmate
import pytest
from _loader import action_steps, load_script

ar = load_script("apply-release")

_URL = "https://github.com/acme/demo/actions/runs/77"


@pytest.fixture
def patched(monkeypatch):
    """`apply-release` in this process on attempt 1 of a pre-merge run; returns each PATCH's
    `(path, body)` and each read's `("GET", path)`. A read of pull request 7 answers it
    merged."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.delenv("SHIPMATE_DEPLOY", raising=False)
    monkeypatch.delenv("SHIPMATE_PR_NUMBER", raising=False)
    monkeypatch.setattr(_shipmate, "_sleep", lambda _s: None)
    out = []

    def run(args, secrets=(), stdin=None):
        if args[2] != "-X":
            out.append(("GET", args[2]))
            return json.dumps({"merged": args[2].endswith("/pulls/7")})
        out.append((args[4], json.loads(stdin)))
        return "{}"

    monkeypatch.setattr(ar.pc, "run", run)
    return out


_SNAP = {"apply / web / dev": [3], "apply / app / dev": [2, 5]}


def test_each_id_gets_exactly_the_status_and_the_run_link(monkeypatch, patched):
    """Never `output`: that would replace the plan text. Mutations: add `output` to the body;
    drop `details_url`; write `queued`."""
    monkeypatch.setenv("SHIPMATE_SNAPSHOT_JSON", json.dumps(_SNAP))
    ar.main()
    flip = {"status": "in_progress", "details_url": _URL}
    assert patched == [
        ("repos/acme/demo/check-runs/2", flip),
        ("repos/acme/demo/check-runs/5", flip),
        ("repos/acme/demo/check-runs/3", flip),
    ]


def test_a_pre_merge_re_run_flips_nothing(monkeypatch, patched):
    """Mutation: `apply-release` PATCHes directly instead of through `patch-checks.write` -> three
    PATCHes."""
    monkeypatch.setenv("SHIPMATE_SNAPSHOT_JSON", json.dumps(_SNAP))
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    ar.main()
    assert patched == []


_FLIPS = [
    ("repos/acme/demo/check-runs/2", {"status": "in_progress", "details_url": _URL}),
    ("repos/acme/demo/check-runs/5", {"status": "in_progress", "details_url": _URL}),
    ("repos/acme/demo/check-runs/3", {"status": "in_progress", "details_url": _URL}),
]


def test_a_pre_merge_run_of_a_merged_pull_request_flips_nothing(monkeypatch, patched, capsys):
    """The merge deploy owns its pending checks. Mutation: `patch-checks` skips the merged
    read (`elif False`) -> three PATCHes."""
    monkeypatch.setenv("SHIPMATE_SNAPSHOT_JSON", json.dumps(_SNAP))
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "7")
    ar.main()
    assert patched == [("GET", "repos/acme/demo/pulls/7")]
    assert capsys.readouterr().err == (
        "::notice::pull request #7 is merged; its merge deploy owns every apply check this run "
        "did not apply\n"
    )


def test_a_pre_merge_run_of_an_open_pull_request_reads_it_once_then_flips(monkeypatch, patched):
    """Mutation: flip nothing whatever the read answers -> no PATCH."""
    monkeypatch.setenv("SHIPMATE_SNAPSHOT_JSON", json.dumps(_SNAP))
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "8")
    ar.main()
    assert patched == [("GET", "repos/acme/demo/pulls/8"), *_FLIPS]


def test_a_merge_deploy_flips_without_reading_its_pull_request(monkeypatch, patched):
    """The deploy passes the merged pull request, and its flips must land. Mutation: read the
    pull request on a deploy too (drop `if not deploy_mode()`) -> a read and no PATCH."""
    monkeypatch.setenv("SHIPMATE_SNAPSHOT_JSON", json.dumps(_SNAP))
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "7")
    monkeypatch.setenv("SHIPMATE_DEPLOY", "true")
    ar.main()
    assert patched == _FLIPS


def test_an_argument_refuses():
    """Mutation: drop the `if argv:` refusal -> it reads the missing snapshot instead."""
    with pytest.raises(SystemExit) as exc:
        ar.main(["x"])
    assert str(exc.value) == "::error::usage: apply-release"


#: The flip step, whole: the App token, the slice, the deploy flag and the pull request, and
#: nothing else.
_FLIP_STEP = {
    "name": "Flip the wave's apply checks to in progress",
    "shell": "bash",
    "env": {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_SNAPSHOT_JSON": "${{ inputs.snapshot }}",
        "SHIPMATE_DEPLOY": "${{ inputs.deploy }}",
        "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
    },
    "run": 'set -euo pipefail\npython3 "$GITHUB_ACTION_PATH/../../scripts/apply-release"\n',
}


def test_the_action_verifies_mints_then_flips():
    """Mutations: drop `SHIPMATE_DEPLOY` or `SHIPMATE_PR_NUMBER` from the flip step's env; swap
    the mint and the flip; request `permission-pull-requests` unconditionally (`read`), or invert
    the deploy test (`== 'true'`)."""
    steps = action_steps("apply-release")
    assert [s.get("name") for s in steps] == [
        "Verify the App key arrived",
        "Mint App installation token",
        "Flip the wave's apply checks to in progress",
    ]
    assert steps[1]["with"] == {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-checks": "write",
        "permission-pull-requests": "${{ inputs.deploy != 'true' && 'read' || '' }}",
    }
    assert steps[2] == _FLIP_STEP
