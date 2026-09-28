"""Executable coverage for the Compose cell summary step's result and reason decision in
actions/apply-cell/action.yml.

The step runs `scripts/apply-cell-summary`. The sibling YAML-shape guards (ids exist,
continue-on-error is set, files land under $RUNNER_TEMP) leave the branching logic itself
unexercised, so this file loads that script -- never a hand-copy, which could silently drift from
what ships -- and calls its `main()` under a controlled environment for every outcome combination
the decision branches on. One guard here pins that the step still invokes it, because a test over
a script nothing runs asserts nothing.

Why the step matches `== "failure"` rather than `!= "success"`: a composite step failure halts
every later step whose `if` defaults to `success()`, so a fail-safe that never ran reads
'skipped', not 'failure'. An unrelated earlier failure -- any step in that range carrying no id,
so no FAILSAFES row can name it -- must read as the generic "an earlier step failed" reason,
never as a specific fail-safe's message that in fact never ran.
"""

import json

import pytest
from _loader import load_script, run_lines, step_by


def test_the_compose_step_runs_the_script_this_file_exercises():
    # A whole run line, not a substring: a commented-out invocation writes no cell.json, and
    # apply-comment then renders an applied cell as never attempted.
    assert 'python3 "$GITHUB_ACTION_PATH/../../scripts/apply-cell-summary"' in run_lines(
        step_by("apply-cell", name="Compose cell summary")
    )


def _run_compose(
    monkeypatch,
    tmp_path,
    *,
    download="skipped",
    planned_head="skipped",
    decrypt="skipped",
    fingerprint="skipped",
    digest_input="skipped",
    init="skipped",
    locate="skipped",
    restore="skipped",
    plan_digest="skipped",
    apply="skipped",
    stack="stacks/app",
    stack_name="app",
    env="dev-eu",
):
    """Run the real script with os.environ patched to a given outcome combination and
    RUNNER_TEMP pointed at a tmp dir, then return the resulting cell.json as a dict."""
    monkeypatch.setenv("STACK", stack)
    monkeypatch.setenv("STACK_NAME", stack_name)
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("DOWNLOAD_OUTCOME", download)
    monkeypatch.setenv("PLANNED_HEAD_OUTCOME", planned_head)
    monkeypatch.setenv("DECRYPT_OUTCOME", decrypt)
    monkeypatch.setenv("FINGERPRINT_OUTCOME", fingerprint)
    monkeypatch.setenv("DIGEST_INPUT_OUTCOME", digest_input)
    monkeypatch.setenv("INIT_OUTCOME", init)
    monkeypatch.setenv("LOCATE_OUTCOME", locate)
    monkeypatch.setenv("RESTORE_OUTCOME", restore)
    monkeypatch.setenv("PLAN_DIGEST_OUTCOME", plan_digest)
    monkeypatch.setenv("APPLY_OUTCOME", apply)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    load_script("apply-cell-summary").main()

    cell_path = tmp_path / "cell.json"
    assert cell_path.exists(), "the script did not write cell.json under RUNNER_TEMP"
    return json.loads(cell_path.read_text(encoding="utf-8"))


#: Every outcome an all-green cell reads: each fail-safe and the apply succeeded. Hand-written, as
#: is the table below, never derived from FAILSAFES: a derived row passes whatever that list says.
_ALL_SUCCESS = {
    "download": "success",
    "planned_head": "success",
    "decrypt": "success",
    "fingerprint": "success",
    "restore": "success",
    "digest_input": "success",
    "init": "success",
    "locate": "success",
    "plan_digest": "success",
    "apply": "success",
}


@pytest.mark.parametrize(
    ("failed", "reason"),
    [
        pytest.param(
            "download", "reviewed plan artifact missing or expired — re-run plan", id="download"
        ),
        pytest.param(
            "planned_head",
            "reviewed plan records no commit or was produced from a different one — re-plan",
            id="planned_head",
        ),
        pytest.param(
            "decrypt",
            "plan artifact could not be decrypted — passphrase/config mismatch",
            id="decrypt",
        ),
        pytest.param(
            "fingerprint",
            "environment does not match the reviewed plan's fingerprint — re-plan",
            id="fingerprint",
        ),
        pytest.param(
            "digest_input",
            "no plan-text digest reached this action — re-pin every engine reference to one commit",
            id="digest_input",
        ),
        # Before the apply step was split, a failed init reported result="failed" with no reason
        # -- the bucket a real apply error lands in, which may have mutated infrastructure. Its
        # own row rather than the digest's, so that pre-existing gap is not hidden behind a new
        # message.
        pytest.param("init", "tofu init failed — see the job log", id="init"),
        pytest.param(
            "locate",
            "shipmate cannot tell where this stack's local state lives — see the job log",
            id="locate_state",
        ),
        pytest.param("restore", "state restore failed", id="restore_state"),
        pytest.param(
            "plan_digest",
            "the stored plan does not render to the plan text that was reviewed — re-plan",
            id="plan_digest",
        ),
    ],
)
def test_a_failed_failsafe_blocks_with_its_own_reason(monkeypatch, tmp_path, failed, reason):
    """Mutation: drop a row from FAILSAFES, `LOCATE_OUTCOME` for instance, and that row reds."""
    cell = _run_compose(monkeypatch, tmp_path, **{failed: "failure"})
    assert (cell["result"], cell["reason"]) == ("blocked", reason)


def test_apply_success_is_applied_with_empty_reason(monkeypatch, tmp_path):
    cell = _run_compose(monkeypatch, tmp_path, **_ALL_SUCCESS)
    assert cell["result"] == "applied"
    assert cell["reason"] == ""


def test_remote_backend_skipped_restore_is_applied_with_empty_reason(monkeypatch, tmp_path):
    # The remote-backend happy path: with an empty located path both actions/state steps skip, so
    # restore reads 'skipped' while everything else succeeded. 'skipped' matches no fail-safe,
    # which match 'failure' exactly, and that is what makes a remote-backend cell unblockable on
    # artifact state.
    cell = _run_compose(monkeypatch, tmp_path, **{**_ALL_SUCCESS, "restore": "skipped"})
    assert cell["result"] == "applied"
    assert cell["reason"] == ""


def test_apply_failure_is_failed_with_empty_reason(monkeypatch, tmp_path):
    cell = _run_compose(monkeypatch, tmp_path, **{**_ALL_SUCCESS, "apply": "failure"})
    assert cell["result"] == "failed"
    assert cell["reason"] == ""


def test_apply_cancelled_is_failed_with_empty_reason(monkeypatch, tmp_path):
    cell = _run_compose(monkeypatch, tmp_path, **{**_ALL_SUCCESS, "apply": "cancelled"})
    assert cell["result"] == "failed"
    assert cell["reason"] == ""


def test_unrelated_step_failed_after_fingerprint_reads_as_generic_blocked(monkeypatch, tmp_path):
    # An un-id'd step failing after fingerprint: every fail-safe up to it reads 'success' and
    # every later one and the apply never ran, reading 'skipped'. The decision must not
    # misattribute that to a later fail-safe.
    cell = _run_compose(
        monkeypatch,
        tmp_path,
        download="success",
        planned_head="success",
        decrypt="success",
        fingerprint="success",
        restore="skipped",
        apply="skipped",
    )
    assert cell["result"] == "blocked"
    assert cell["reason"] == "an earlier step failed before the apply ran — see the job log"


def test_everything_skipped_still_reads_as_generic_blocked_not_a_named_failsafe(
    monkeypatch, tmp_path
):
    # An even earlier failure, a token mint for instance, skips every step this decision
    # inspects. Still 'blocked', but the reason must stay generic: none of the fail-safes ran,
    # let alone failed.
    cell = _run_compose(monkeypatch, tmp_path)  # every outcome defaults to 'skipped'
    assert cell["result"] == "blocked"
    assert cell["reason"] == "an earlier step failed before the apply ran — see the job log"


def test_two_failsafes_failing_together_the_earlier_in_pipeline_order_wins(monkeypatch, tmp_path):
    # decrypt precedes restore-state in FAILSAFES, so both failing must surface decrypt's
    # reason, never restore-state's: precedence is pipeline order, not severity or alphabetical.
    cell = _run_compose(monkeypatch, tmp_path, decrypt="failure", restore="failure")
    assert cell["result"] == "blocked"
    assert cell["reason"] == "plan artifact could not be decrypted — passphrase/config mismatch"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"download": "failure"},
        _ALL_SUCCESS,
        {**_ALL_SUCCESS, "apply": "failure"},
        {},
    ],
)
def test_cell_json_carries_exactly_the_five_contract_keys_all_strings(
    monkeypatch, tmp_path, kwargs
):
    """The renderer's contract: `scripts/apply-comment`'s CELL_KEYS is this same five-key set."""
    cell = _run_compose(monkeypatch, tmp_path, **kwargs)
    assert set(cell.keys()) == {"stack", "stack_path", "environment", "result", "reason"}
    for key, value in cell.items():
        assert isinstance(value, str), f"{key} is {type(value).__name__}, not str: {value!r}"
    assert cell["result"] in ("applied", "failed", "blocked")
