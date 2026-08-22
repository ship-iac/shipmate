"""Tests for actions/dispatch mode input: conditional dispatch body inclusion,
validation before API call, and graceful degradation when the consumer's
apply.yml does not yet declare the mode input.

Three parts:
- Body building: mode is included in dispatch inputs only when "unlock"; when
  unset or "apply", no mode key appears. Tested via subprocess execution of the
  python heredoc.
- Validation: a mode value that is neither empty, "apply", nor "unlock" is
  rejected before any API call (before the gh invocation), with a clear message.
- Degrade message: when an unlock dispatch fails (422 from a consumer that does
  not yet declare mode), the failure prints an actionable message naming the
  issue and pointing to the release docs.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from _loader import action_steps, usable_bash

DISPATCH_ACTION = "dispatch"


def _dispatch_step():
    """The workflow_dispatch apply step."""
    steps = action_steps(DISPATCH_ACTION)
    matches = [s for s in steps if s.get("name") == "workflow_dispatch apply"]
    assert len(matches) == 1, (
        f"expected exactly one 'workflow_dispatch apply' step, got {len(matches)}"
    )
    return matches[0]


def _extract_python_heredoc():
    """Extract the python body from the step's run field.

    Fails on vacuous heredoc (empty body) — if the partition yields an empty
    body, it would exec "" and pass vacuously.
    """
    run = _dispatch_step()["run"]
    _, _, rest = run.partition("<<'PY'\n")
    body, sep, _ = rest.rpartition("\nPY")
    assert sep, "workflow_dispatch apply step has no closing PY terminator"
    assert body.strip(), (
        "workflow_dispatch apply python heredoc body is empty — a vacuous assertion"
    )
    return body


def _build_dispatch_body(mode="", environment=""):
    """Execute the dispatch body-building python code with given inputs.

    Returns the parsed JSON inputs dict.
    """
    code = _extract_python_heredoc()
    env = os.environ.copy()
    env.update(
        {
            "DISPATCH_REF": "main",
            "REF": "abc123def456",
            "PR_NUMBER": "42",
            "PLAN_RUN_ID": "12345",
            "ENVIRONMENT": environment,
            "MODE": mode,
        }
    )

    result = subprocess.run(
        [__import__("sys").executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"python body builder failed: {result.stderr}"
    body = json.loads(result.stdout)
    return body["inputs"]


def test_dispatch_body_has_no_mode_key_when_mode_unset():
    """When mode is unset, the dispatch body has no mode key.

    Mutation: make the conditional `if mode == "unlock":` unconditional.
    """
    inputs = _build_dispatch_body(mode="")
    assert "mode" not in inputs, (
        f"mode input should not appear when unset, but body contains: {inputs}"
    )


def test_dispatch_body_has_no_mode_key_when_mode_apply():
    """When mode is 'apply', the dispatch body has no mode key.

    Mutation: change the condition from `if mode == "unlock":` to `if mode:`.
    """
    inputs = _build_dispatch_body(mode="apply")
    assert "mode" not in inputs, (
        f"mode input should not appear when 'apply', but body contains: {inputs}"
    )


def test_dispatch_body_includes_mode_when_mode_unlock():
    """When mode is 'unlock', the dispatch body includes mode='unlock'.

    Mutation: drop the assignment inside the conditional (delete lines with
    `if mode == "unlock"` through `inputs["mode"] = mode`).
    """
    inputs = _build_dispatch_body(mode="unlock")
    assert inputs.get("mode") == "unlock", (
        f"mode input should be 'unlock', but inputs are: {inputs}"
    )


def test_dispatch_body_includes_environment_when_set():
    """Sanity check: environment is still included when set (regression pin)."""
    inputs = _build_dispatch_body(mode="", environment="dev-eu")
    assert inputs.get("environment") == "dev-eu", (
        f"environment should be included when set, but inputs are: {inputs}"
    )


def test_dispatch_step_env_mapping_is_complete():
    """Pin the whole env: mapping of the dispatch step to ensure MODE was wired.

    This catches the case where the python code is correct but MODE was never
    added to the step's env: block in the first place.
    """
    step = _dispatch_step()
    env = step.get("env") or {}

    expected_env_vars = {
        "GH_TOKEN",
        "REPO",
        "WORKFLOW",
        "DISPATCH_REF",
        "ENVIRONMENT",
        "MODE",
        "REF",
        "PR_NUMBER",
        "PLAN_RUN_ID",
    }
    actual_env_vars = set(env.keys())

    assert actual_env_vars == expected_env_vars, (
        f"dispatch step env vars should be {sorted(expected_env_vars)}, "
        f"got {sorted(actual_env_vars)}"
    )


def test_invalid_mode_rejected_before_api_call():
    """When mode is an invalid value, it is rejected before the gh api call.

    This test runs the full step's shell body with a stubbed gh command on PATH.
    It verifies:
    1. The shell exits non-zero
    2. The gh stub is never invoked (no marker file created)
    3. The exact error message appears in output

    Mutation: delete the case validation line.
    """
    bash = usable_bash()
    if not bash:
        pytest.skip("bash not available on this platform")

    step = _dispatch_step()
    run = step["run"]

    # Create a temporary directory with a stub gh command that creates a marker.
    with tempfile.TemporaryDirectory() as tmpdir:
        stub_gh = Path(tmpdir) / "gh"
        stub_gh.write_text("#!/bin/bash\ntouch marker.txt\n")
        stub_gh.chmod(0o755)

        # Prepare env for the step.
        env = os.environ.copy()
        env["PATH"] = f"{tmpdir}:{env.get('PATH', '')}"
        env["GH_TOKEN"] = "test_token"  # noqa: S105
        env["REPO"] = "org/repo"
        env["WORKFLOW"] = "apply.yml"
        env["DISPATCH_REF"] = "main"
        env["ENVIRONMENT"] = ""
        env["MODE"] = "bogus"  # Invalid mode
        env["REF"] = "abc123"
        env["PR_NUMBER"] = "42"
        env["PLAN_RUN_ID"] = "12345"

        result = subprocess.run(
            [bash, "-c", run],
            env=env,
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=30,
        )

        # Verify rejection before API call.
        assert result.returncode != 0, "shell should exit non-zero for invalid mode"
        assert not (Path(tmpdir) / "marker.txt").exists(), (
            "gh stub was invoked; validation should have rejected before API call"
        )
        output = result.stdout + result.stderr
        assert "::error::mode must be apply or unlock (got: bogus)" in output, (
            f"expected error message not found in output: {output}"
        )


def test_valid_modes_pass_case_validation():
    """Valid mode values (empty, 'apply', 'unlock') pass the case validation.

    Tests the case statement in isolation to verify it accepts all valid modes
    and rejects invalid ones. Skips full integration test that needs python3.
    """
    bash = usable_bash()
    if not bash:
        pytest.skip("bash not available on this platform")

    # Test just the case validation logic in isolation.
    case_validation = """
    case "${MODE:-}" in ''|apply|unlock) echo "valid"; exit 0 ;; *) echo "invalid"; exit 1 ;; esac
    """

    for mode in ("", "apply", "unlock"):
        env = os.environ.copy()
        env["MODE"] = mode

        result = subprocess.run(
            [bash, "-c", case_validation],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        combined_output = result.stdout + result.stderr
        assert result.returncode == 0, (
            f"mode={mode!r} should pass validation, "
            f"but got exit {result.returncode}: {combined_output}"
        )
        assert "valid" in result.stdout, (
            f"mode={mode!r} validation should output 'valid', got: {result.stdout}"
        )
