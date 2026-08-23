"""Executable coverage for the Compose cell summary step's result/reason
decision in actions/apply-cell/action.yml.

The step is a Python heredoc inline in the composite action (kept there, not
extracted to scripts/, because the capture must live where the apply runs).
Until now the only tests on it asserted YAML shape (ids exist,
continue-on-error is set, files land under $RUNNER_TEMP) -- the branching
logic itself had only ever been verified by hand-tracing. This file extracts
the heredoc body straight out of action.yml (never a hand-copy, which could
silently drift from what ships) and execs it under a controlled environment
for every outcome combination the decision actually branches on.

Why the step matches `== "failure"` rather than `!= "success"`: a composite
step failure halts every later step whose `if` defaults to `success()`, so a
fail-safe that never ran reads 'skipped', not 'failure'. An unrelated earlier
failure -- the un-id'd "Snapshot pre-existing apply-check ids" step, which
sits between fingerprint and restore-state -- must read as the generic
"an earlier step failed" reason, never as a specific fail-safe's message that
in fact never ran.
"""

import json

import pytest
from _loader import action_steps


def _compose_step():
    matches = [s for s in action_steps("apply-cell") if s.get("name") == "Compose cell summary"]
    assert len(matches) == 1, f"expected exactly one Compose cell summary step, got {len(matches)}"
    return matches[0]


def _compose_heredoc_body():
    """Pull the Python source out of `python3 - <<'PY' ... PY` in the step's
    `run:` block. Splitting on the exact heredoc markers (rather than
    hand-copying the logic elsewhere) is what keeps this test bound to the
    code that actually ships."""
    run = _compose_step()["run"]
    assert "<<'PY'\n" in run, "Compose cell summary no longer opens a 'PY' heredoc"
    _, _, rest = run.partition("<<'PY'\n")
    body, sep, _ = rest.rpartition("\nPY")
    assert sep, "Compose cell summary heredoc has no closing PY terminator"
    return body


def _run_compose(
    monkeypatch,
    tmp_path,
    *,
    download="skipped",
    planned_head="skipped",
    decrypt="skipped",
    fingerprint="skipped",
    restore="skipped",
    apply="skipped",
    stack="stacks/app",
    stack_name="app",
    env="dev-eu",
):
    """Exec the real heredoc body with os.environ patched to a given outcome
    combination and RUNNER_TEMP pointed at a tmp dir, then return the
    resulting cell.json as a dict."""
    monkeypatch.setenv("STACK", stack)
    monkeypatch.setenv("STACK_NAME", stack_name)
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("DOWNLOAD_OUTCOME", download)
    monkeypatch.setenv("PLANNED_HEAD_OUTCOME", planned_head)
    monkeypatch.setenv("DECRYPT_OUTCOME", decrypt)
    monkeypatch.setenv("FINGERPRINT_OUTCOME", fingerprint)
    monkeypatch.setenv("RESTORE_OUTCOME", restore)
    monkeypatch.setenv("APPLY_OUTCOME", apply)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    body = _compose_heredoc_body()
    # The whole point of this test is to execute the exact source that ships
    # in action.yml (never a hand-copy that could drift from it) -- exec is
    # unavoidable. body is read straight out of the action's own heredoc,
    # never from external/user input.
    exec(compile(body, "<Compose cell summary heredoc>", "exec"), {})  # noqa: S102

    cell_path = tmp_path / "cell.json"
    assert cell_path.exists(), "heredoc did not write cell.json under RUNNER_TEMP"
    return json.loads(cell_path.read_text(encoding="utf-8"))


# --- the five fail-safes, checked in pipeline order -------------------------


def test_download_failure_blocks_with_its_own_reason(monkeypatch, tmp_path):
    cell = _run_compose(monkeypatch, tmp_path, download="failure")
    assert cell["result"] == "blocked"
    assert cell["reason"] == "reviewed plan artifact missing or expired — re-run plan"


def test_planned_head_failure_blocks_with_its_own_reason(monkeypatch, tmp_path):
    cell = _run_compose(monkeypatch, tmp_path, planned_head="failure")
    assert cell["result"] == "blocked"
    assert (
        cell["reason"]
        == "reviewed plan records no commit or was produced from a different one — re-plan"
    )


def test_decrypt_failure_blocks_with_its_own_reason(monkeypatch, tmp_path):
    cell = _run_compose(monkeypatch, tmp_path, decrypt="failure")
    assert cell["result"] == "blocked"
    assert cell["reason"] == "plan artifact could not be decrypted — passphrase/config mismatch"


def test_fingerprint_failure_blocks_with_its_own_reason(monkeypatch, tmp_path):
    cell = _run_compose(monkeypatch, tmp_path, fingerprint="failure")
    assert cell["result"] == "blocked"
    assert cell["reason"] == "environment does not match the reviewed plan's fingerprint — re-plan"


def test_restore_state_failure_blocks_with_its_own_reason(monkeypatch, tmp_path):
    cell = _run_compose(monkeypatch, tmp_path, restore="failure")
    assert cell["result"] == "blocked"
    assert cell["reason"] == "state restore failed"


# --- the apply outcome itself, once every fail-safe succeeded ---------------


def test_apply_success_is_applied_with_empty_reason(monkeypatch, tmp_path):
    cell = _run_compose(
        monkeypatch,
        tmp_path,
        download="success",
        planned_head="success",
        decrypt="success",
        fingerprint="success",
        restore="success",
        apply="success",
    )
    assert cell["result"] == "applied"
    assert cell["reason"] == ""


def test_remote_backend_skipped_restore_is_applied_with_empty_reason(monkeypatch, tmp_path):
    # The remote-backend happy path: with an empty state-path both actions/state
    # steps skip, so restore reads 'skipped' while everything else succeeded.
    # 'skipped' matches no fail-safe (they match 'failure' exactly), which is
    # what makes a remote-backend cell unblockable on artifact state.
    cell = _run_compose(
        monkeypatch,
        tmp_path,
        download="success",
        planned_head="success",
        decrypt="success",
        fingerprint="success",
        restore="skipped",
        apply="success",
    )
    assert cell["result"] == "applied"
    assert cell["reason"] == ""


def test_apply_failure_is_failed_with_empty_reason(monkeypatch, tmp_path):
    cell = _run_compose(
        monkeypatch,
        tmp_path,
        download="success",
        planned_head="success",
        decrypt="success",
        fingerprint="success",
        restore="success",
        apply="failure",
    )
    assert cell["result"] == "failed"
    assert cell["reason"] == ""


def test_apply_cancelled_is_failed_with_empty_reason(monkeypatch, tmp_path):
    cell = _run_compose(
        monkeypatch,
        tmp_path,
        download="success",
        planned_head="success",
        decrypt="success",
        fingerprint="success",
        restore="success",
        apply="cancelled",
    )
    assert cell["result"] == "failed"
    assert cell["reason"] == ""


# --- the misattribution case: something else failed, no fail-safe tripped ---


def test_unrelated_step_failed_between_fingerprint_and_restore_reads_as_generic_blocked(
    monkeypatch, tmp_path
):
    # Mirrors the real bug: the un-id'd "Snapshot pre-existing apply-check ids"
    # step sits between fingerprint and restore-state. If it fails, every
    # fail-safe up to it reads 'success' and restore-state/apply never ran
    # ('skipped') -- the decision must not misattribute this to restore-state.
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
    # An even earlier failure (e.g. token mint) skips every one of the steps
    # this decision inspects. Still 'blocked', but the reason must stay
    # generic -- none of the fail-safes actually ran, let alone failed.
    cell = _run_compose(monkeypatch, tmp_path)  # every outcome defaults to 'skipped'
    assert cell["result"] == "blocked"
    assert cell["reason"] == "an earlier step failed before the apply ran — see the job log"


# --- precedence: first fail-safe in pipeline order wins ---------------------


def test_two_failsafes_failing_together_the_earlier_in_pipeline_order_wins(monkeypatch, tmp_path):
    # decrypt precedes restore-state in FAILSAFES; both failing must surface
    # decrypt's reason, never restore-state's -- precedence is pipeline
    # order, not e.g. severity or alphabetical.
    cell = _run_compose(monkeypatch, tmp_path, decrypt="failure", restore="failure")
    assert cell["result"] == "blocked"
    assert cell["reason"] == "plan artifact could not be decrypted — passphrase/config mismatch"


# --- schema: the renderer's contract ----------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"download": "failure"},
        {
            "download": "success",
            "planned_head": "success",
            "decrypt": "success",
            "fingerprint": "success",
            "restore": "success",
            "apply": "success",
        },
        {
            "download": "success",
            "planned_head": "success",
            "decrypt": "success",
            "fingerprint": "success",
            "restore": "success",
            "apply": "failure",
        },
        {},
    ],
)
def test_cell_json_carries_exactly_the_five_contract_keys_all_strings(
    monkeypatch, tmp_path, kwargs
):
    cell = _run_compose(monkeypatch, tmp_path, **kwargs)
    assert set(cell.keys()) == {"stack", "stack_path", "environment", "result", "reason"}
    for key, value in cell.items():
        assert isinstance(value, str), f"{key} is {type(value).__name__}, not str: {value!r}"
    assert cell["result"] in ("applied", "failed", "blocked")
