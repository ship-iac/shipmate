import json
from datetime import UTC, datetime, timedelta

import _shipmate
import pytest
from _loader import ENGINE_ENVIRONMENT, action_steps, action_yaml, load_script, workflow_jobs

ar = load_script("apply-release")

_URL = "https://github.com/acme/demo/actions/runs/77"
_STARTED = "2026-11-23T17:07:47Z"


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
    ticks = iter(range(100))
    t0 = datetime(2026, 11, 23, 17, 7, 47, 250000, tzinfo=UTC)
    monkeypatch.setattr(ar, "_now", lambda: t0 + timedelta(seconds=next(ticks)))
    out = []

    def run(args, secrets=(), stdin=None):
        if args[2] != "-X":
            out.append(("GET", args[2]))
            return json.dumps({"merged": args[2].endswith("/pulls/7")})
        assert stdin is not None
        out.append((args[4], json.loads(stdin)))
        return "{}"

    monkeypatch.setattr(ar.pc, "run", run)
    return out


_SNAP = {"apply / dev / web": [3], "apply / dev / app": [2, 5]}


def _release(monkeypatch, stage=_SNAP):
    """Run release mode over a snapshot holding `stage` as `L0-W1` beside an `L0-W0` stage."""
    by_wave = {"L0-W0": {"apply / dev / dns": [9]}, "L0-W1": stage}
    monkeypatch.setenv("SHIPMATE_BY_WAVE_JSON", json.dumps(by_wave))
    monkeypatch.setenv("SHIPMATE_STAGE_KEY", "L0-W1")
    ar.main(["release"])


def test_each_id_gets_exactly_the_status_the_run_link_and_one_start_time(monkeypatch, patched):
    """Never `output`: that would replace the plan text. The clock advances a second per read, so
    every id carries the first read. Mutations: add `output` to the body; drop `details_url`;
    write `queued`; drop `started_at`; call `_now()` per id; format with `isoformat()`."""
    _release(monkeypatch)
    flip = {"status": "in_progress", "details_url": _URL, "started_at": _STARTED}
    assert patched == [
        ("repos/acme/demo/check-runs/2", flip),
        ("repos/acme/demo/check-runs/5", flip),
        ("repos/acme/demo/check-runs/3", flip),
    ]


def test_a_pre_merge_re_run_flips_nothing(monkeypatch, patched):
    """Mutation: `apply-release` PATCHes directly instead of through `patch-checks.write` -> three
    PATCHes."""
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    _release(monkeypatch)
    assert patched == []


_FLIP = {"status": "in_progress", "details_url": _URL, "started_at": _STARTED}
_FLIPS = [
    ("repos/acme/demo/check-runs/2", _FLIP),
    ("repos/acme/demo/check-runs/5", _FLIP),
    ("repos/acme/demo/check-runs/3", _FLIP),
]


def test_a_pre_merge_run_of_a_merged_pull_request_flips_nothing(monkeypatch, patched, capsys):
    """The merge deploy owns its pending checks. Mutation: `patch-checks` skips the merged
    read (`elif False`) -> three PATCHes."""
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "7")
    _release(monkeypatch)
    assert patched == [("GET", "repos/acme/demo/pulls/7")]
    assert capsys.readouterr().err == (
        "::notice::pull request #7 is merged; its merge deploy owns every apply check this run "
        "did not apply\n"
    )


def test_a_pre_merge_run_of_an_open_pull_request_reads_it_once_then_flips(monkeypatch, patched):
    """Mutation: flip nothing whatever the read answers -> no PATCH."""
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "8")
    _release(monkeypatch)
    assert patched == [("GET", "repos/acme/demo/pulls/8"), *_FLIPS]


def test_a_merge_deploy_flips_without_reading_its_pull_request(monkeypatch, patched):
    """The deploy passes the merged pull request, and its flips must land. Mutation: read the
    pull request on a deploy too (drop `if not deploy_mode()`) -> a read and no PATCH."""
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", "7")
    monkeypatch.setenv("SHIPMATE_DEPLOY", "true")
    _release(monkeypatch)
    assert patched == _FLIPS


def test_release_flips_both_cells_whose_names_differ_only_in_case(monkeypatch, patched):
    """`App` and `app` are two stacks; a lookup that folds case would flip one. Mutation: fold
    the stage's check-name map (`{k.lower(): v ...}`) -> only one cell's id is PATCHed."""
    _release(monkeypatch, {"apply / dev / App": [11], "apply / dev / app": [12]})
    assert patched == [
        ("repos/acme/demo/check-runs/11", _FLIP),
        ("repos/acme/demo/check-runs/12", _FLIP),
    ]


@pytest.mark.parametrize("key", ["", "l0-w1", "L0-W2"])
def test_release_refuses_a_stage_the_snapshot_lacks(monkeypatch, patched, key):
    """An empty or case-folded key selects nothing. Mutation: release the whole snapshot when the
    key is absent -> PATCHes and no refusal."""
    monkeypatch.setenv("SHIPMATE_BY_WAVE_JSON", json.dumps({"L0-W1": _SNAP}))
    monkeypatch.setenv("SHIPMATE_STAGE_KEY", key)
    with pytest.raises(SystemExit) as exc:
        ar.main(["release"])
    assert exc.value.code == f"::error::the stage snapshot holds no stage {key!r}"
    assert patched == []


@pytest.mark.parametrize("argv", [[], [""], ["Release"], ["release", "x"]])
def test_any_mode_but_guard_or_release_refuses(patched, argv):
    """The action runs its release steps for any mode but `guard`, so this refusal is what fails
    a mistyped mode. Mutation: run release when no mode is given -> a KeyError, not the usage
    refusal."""
    with pytest.raises(SystemExit) as exc:
        ar.main(argv)
    assert exc.value.code == "::error::usage: apply-release guard|release"
    assert patched == []


def test_the_clock_reads_utc():
    """GitHub reads `started_at` as UTC (`Z`). Mutation: `datetime.now()` without `UTC` -> a
    naive datetime."""
    assert ar._now().tzinfo is UTC


_RUN = (
    'set -euo pipefail\npython3 "$GITHUB_ACTION_PATH/../../scripts/apply-release" '
    '"$SHIPMATE_MODE"\n'
)
_RELEASE = "${{ inputs.mode != 'guard' }}"

#: The guard step, whole: the job's own token and the stage inputs, no App token.
_GUARD_STEP = {
    "name": "Guard the stage",
    "if": "${{ inputs.mode == 'guard' }}",
    "shell": "bash",
    "env": {
        "GH_TOKEN": "${{ github.token }}",
        "SHIPMATE_MODE": "${{ inputs.mode }}",
        "SHIPMATE_STAGE_KEYS": "${{ inputs.keys }}",
        "SHIPMATE_STAGE_COUNTS": "${{ inputs.counts }}",
        "SHIPMATE_STAGE_N": "${{ inputs.n }}",
        "SHIPMATE_JOB_ID": "${{ inputs.job-id }}",
    },
    "run": _RUN,
}

#: The flip step, whole: the App token, the raw snapshot, the key, the deploy flag and the pull
#: request, and nothing else.
_FLIP_STEP = {
    "name": "Flip the stage's apply checks to in progress",
    "if": _RELEASE,
    "shell": "bash",
    "env": {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_MODE": "${{ inputs.mode }}",
        "SHIPMATE_BY_WAVE_JSON": "${{ inputs.by-wave }}",
        "SHIPMATE_STAGE_KEY": "${{ inputs.key }}",
        "SHIPMATE_DEPLOY": "${{ inputs.deploy }}",
        "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
    },
    "run": _RUN,
}


def test_the_action_guards_with_the_job_token_and_releases_with_the_app_token():
    """Mutations: `github.token` on the flip step, or the App token on the guard step; drop
    `SHIPMATE_JOB_ID` from the guard step; gate the mint on `mode == 'guard'`; drop
    `SHIPMATE_DEPLOY` from the flip step; request `permission-pull-requests` unconditionally
    (`read`); drop the verify step's `if:`."""
    steps = action_steps("apply-release")
    assert [s.get("name") for s in steps] == [
        "Verify the App key arrived",
        "Mint App installation token",
        "Guard the stage",
        "Flip the stage's apply checks to in progress",
    ]
    assert [steps[0].get("if"), steps[1].get("if")] == [_RELEASE, _RELEASE]
    assert steps[1]["with"] == {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "permission-checks": "write",
        "permission-pull-requests": "${{ inputs.deploy != 'true' && 'read' || '' }}",
    }
    assert steps[2:] == [_GUARD_STEP, _FLIP_STEP]


def test_the_action_takes_the_stage_inputs_and_no_snapshot():
    """Mutation: keep the `snapshot` input -> red."""
    assert sorted(action_yaml("apply-release")["inputs"]) == [
        "app-id",
        "by-wave",
        "counts",
        "deploy",
        "job-id",
        "key",
        "keys",
        "mode",
        "n",
        "pr-number",
        "private-key",
    ]


_APP_KEY = {
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}
#: A level's first stage pre-flights and snapshots the whole level, a later stage its own key.
_ONE_STAGE = "${{ !inputs.first && inputs.key || '' }}"

#: `stage.yml`'s whole `entry` job, hand-written. `releasing` overrides `mark`'s `refused` just
#: before the release, which selects `key` from the raw `by_wave` by exact name: an expression
#: lookup folds case.
STAGE_ENTRY = {
    "if": "${{ inputs.n > 0 || inputs.by_wave == '' }}",
    "runs-on": "ubuntu-slim",
    "permissions": {"actions": "read", "checks": "read"},
    "environment": ENGINE_ENVIRONMENT,
    "outputs": {
        "phase": "${{ steps.releasing.outputs.phase || steps.mark.outputs.phase }}",
        "by_wave": "${{ steps.snap.outputs.by_wave }}",
    },
    "steps": [
        {"id": "mark", "shell": "bash", "run": 'echo "phase=refused" >> "$GITHUB_OUTPUT"'},
        {
            "name": "Guard the earlier stages",
            "if": "${{ inputs.n > 0 }}",
            "uses": "$/actions/apply-release",
            "with": {
                "mode": "guard",
                "keys": "${{ inputs.keys }}",
                "counts": "${{ inputs.counts }}",
                "n": "${{ inputs.n }}",
                "job-id": "${{ job.check_run_id }}",
            },
        },
        {
            "if": "${{ inputs.by_wave == '' }}",
            "uses": "$/actions/verify-environments",
            "with": {"waves-json": "${{ inputs.level_waves }}", "key": _ONE_STAGE},
        },
        {
            "id": "snap",
            "if": "${{ inputs.by_wave == '' }}",
            "uses": "$/actions/apply-snapshot",
            "with": {
                "head-sha": "${{ inputs.head_sha }}",
                "waves-json": "${{ inputs.level_waves }}",
                "key": _ONE_STAGE,
                **_APP_KEY,
            },
        },
        {"id": "releasing", "shell": "bash", "run": 'echo "phase=releasing" >> "$GITHUB_OUTPUT"'},
        {
            "uses": "$/actions/apply-release",
            "with": {
                "mode": "release",
                "by-wave": "${{ inputs.by_wave || steps.snap.outputs.by_wave }}",
                "key": "${{ inputs.key }}",
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                **_APP_KEY,
            },
        },
    ],
}


def test_a_stages_entry_guards_preflights_and_releases_by_exact_name():
    """Mutations: move `releasing` after the release step; swap the `||` operands of the
    `phase` output; release `by-wave: ${{ toJSON(fromJSON(inputs.by_wave ||
    steps.snap.outputs.by_wave)[inputs.key]) }}`; drop `key` from the snapshot; `inputs.n > 1`
    on the guard; `inputs.n > 0 && inputs.by_wave == ''` in the job's `if:`."""
    assert workflow_jobs("stage.yml")["entry"] == STAGE_ENTRY
