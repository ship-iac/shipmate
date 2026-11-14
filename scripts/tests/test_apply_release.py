import json

import _shipmate
import pytest
from _loader import action_steps, load_script

ar = load_script("apply-release")

_URL = "https://github.com/acme/demo/actions/runs/77"


@pytest.fixture
def patched(monkeypatch):
    """`apply-release` in this process on attempt 1 of a pre-merge run; returns each PATCH's
    `(path, body)`."""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.delenv("SHIPMATE_DEPLOY", raising=False)
    monkeypatch.delenv("SHIPMATE_PR_NUMBER", raising=False)
    monkeypatch.setattr(_shipmate, "_sleep", lambda _s: None)
    out = []

    def run(args, secrets=(), stdin=None):
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


def test_an_argument_refuses():
    """Mutation: drop the `if argv:` refusal -> it reads the missing snapshot instead."""
    with pytest.raises(SystemExit) as exc:
        ar.main(["x"])
    assert str(exc.value) == "::error::usage: apply-release"


#: The flip step, whole: the App token, the slice and the deploy flag, and nothing else.
_FLIP_STEP = {
    "name": "Flip the wave's apply checks to in progress",
    "shell": "bash",
    "env": {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_SNAPSHOT_JSON": "${{ inputs.snapshot }}",
        "SHIPMATE_DEPLOY": "${{ inputs.deploy }}",
    },
    "run": 'set -euo pipefail\npython3 "$GITHUB_ACTION_PATH/../../scripts/apply-release"\n',
}


def test_the_action_verifies_mints_then_flips():
    """Mutations: drop `SHIPMATE_DEPLOY` from the flip step's env; swap the mint and the flip;
    add `permission-pull-requests: read` to the mint."""
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
    }
    assert steps[2] == _FLIP_STEP
