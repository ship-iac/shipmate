"""Tests for actions/dispatch mode input: conditional dispatch body inclusion,
validation before API call, and graceful degradation when the consumer's
apply.yml does not yet declare the mode input.

Four parts:
- Body building: mode is included in dispatch inputs only when "unlock"; when
  unset or "apply", no mode key appears. Tested via subprocess execution of the
  python heredoc only (not the full script).
- Validation before API call: a mode value that is neither empty, "apply", nor
  "unlock" is rejected before any API call (tested against extracted python +
  case validation in isolation).
- Degrade message on unlock failure: when an unlock dispatch fails with the real
  gh command, the failure prints an actionable message. Tested by executing the
  full shipped script with stubbed gh/python3 on PATH.
- Apply path stays green: when an apply dispatch fails, the raw error is printed
  with no degrade message. Tested by executing the full shipped script.
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
    env.update({
        "DISPATCH_REF": "main",
        "REF": "abc123def456",
        "PR_NUMBER": "42",
        "PLAN_RUN_ID": "12345",
        "ENVIRONMENT": environment,
        "MODE": mode,
    })

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


def _extract_dispatch_run_block():
    """Extract the full run: block from the workflow_dispatch apply step.

    Returns the raw bash script exactly as written in the action.
    """
    step = _dispatch_step()
    run = step.get("run", "")
    assert run.strip(), "dispatch step has no run block"
    return run


def _create_stub_commands(tmpdir):
    """Create stub gh and python3 commands on PATH for testing.

    Returns (path, python3_path, gh_path) tuple.
    """
    python3_path = Path(tmpdir) / "python3"
    # Use python from sys.executable instead of /usr/bin/python3
    python3_path.write_text(
        f"#!/bin/bash\n"
        f'exec "{__import__("sys").executable}" "$@"\n'
    )
    python3_path.chmod(0o755)

    gh_path = Path(tmpdir) / "gh"
    gh_path.write_text("#!/bin/bash\necho '{}'\n")  # Default: success
    gh_path.chmod(0o755)

    return str(tmpdir), str(python3_path), str(gh_path)


def test_unlock_failure_prints_degrade_message():
    """When unlock dispatch fails (422), the degrade message prints.

    Executes the shipped run block with a stubbed gh that exits 22 (HTTP 422).
    Mutation: remove the unlock scoping so message prints unconditionally.
    """
    bash = usable_bash()
    if not bash:
        pytest.skip("bash not available on this platform")

    run_block = _extract_dispatch_run_block()

    with tempfile.TemporaryDirectory() as tmpdir:
        path_dir, python3_path, gh_path = _create_stub_commands(tmpdir)

        # Stub gh to exit 22 (HTTP error) and print to stderr.
        gh_stub_content = (
            "#!/bin/bash\n"
            "echo 'HTTP 422: Unprocessable Entity' >&2\n"
            "exit 22\n"
        )
        Path(gh_path).write_text(gh_stub_content)
        Path(gh_path).chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{path_dir}:{env.get('PATH', '')}"
        env["GH_TOKEN"] = "test_token"  # noqa: S105
        env["REPO"] = "org/repo"
        env["WORKFLOW"] = "apply.yml"
        env["DISPATCH_REF"] = "main"
        env["ENVIRONMENT"] = ""
        env["MODE"] = "unlock"
        env["REF"] = "abc123"
        env["PR_NUMBER"] = "42"
        env["PLAN_RUN_ID"] = "12345"

        result = subprocess.run(
            [bash, "-c", run_block],
            env=env,
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=30,
        )

        output = result.stdout + result.stderr
        # Must fail
        assert result.returncode != 0, (
            f"unlock failure should exit non-zero, got {result.returncode}"
        )
        # Must include raw gh stderr
        assert "HTTP 422" in output, f"raw gh output missing: {output}"
        # Must include degrade message
        assert (
            "does not declare the mode input" in output
        ), f"degrade message missing: {output}"


def test_apply_failure_no_degrade_message():
    """When apply dispatch fails, no degrade message prints.

    Executes the shipped run block with gh exiting 22; MODE=apply should not
    trigger the unlock-specific message.
    Mutation: remove the unlock scoping so message prints unconditionally.
    """
    bash = usable_bash()
    if not bash:
        pytest.skip("bash not available on this platform")

    run_block = _extract_dispatch_run_block()

    with tempfile.TemporaryDirectory() as tmpdir:
        path_dir, python3_path, gh_path = _create_stub_commands(tmpdir)

        # Stub gh to exit 22.
        gh_stub_content = (
            "#!/bin/bash\n"
            "echo 'HTTP 422: Unprocessable Entity' >&2\n"
            "exit 22\n"
        )
        Path(gh_path).write_text(gh_stub_content)
        Path(gh_path).chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{path_dir}:{env.get('PATH', '')}"
        env["GH_TOKEN"] = "test_token"  # noqa: S105
        env["REPO"] = "org/repo"
        env["WORKFLOW"] = "apply.yml"
        env["DISPATCH_REF"] = "main"
        env["ENVIRONMENT"] = ""
        env["MODE"] = "apply"
        env["REF"] = "abc123"
        env["PR_NUMBER"] = "42"
        env["PLAN_RUN_ID"] = "12345"

        result = subprocess.run(
            [bash, "-c", run_block],
            env=env,
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=30,
        )

        output = result.stdout + result.stderr
        # Must fail
        assert result.returncode != 0
        # Must include raw gh stderr
        assert "HTTP 422" in output
        # Must NOT include degrade message (unlock-specific)
        assert (
            "does not declare the mode input" not in output
        ), f"degrade message should not appear on apply path: {output}"


def test_dispatch_success_exits_zero():
    """When gh succeeds, the script exits 0.

    Mutation: make the wrapper return non-zero on success.
    """
    bash = usable_bash()
    if not bash:
        pytest.skip("bash not available on this platform")

    run_block = _extract_dispatch_run_block()

    with tempfile.TemporaryDirectory() as tmpdir:
        path_dir, python3_path, gh_path = _create_stub_commands(tmpdir)

        # Stub gh to succeed.
        Path(gh_path).write_text("#!/bin/bash\necho '{}'\nexit 0\n")
        Path(gh_path).chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{path_dir}:{env.get('PATH', '')}"
        env["GH_TOKEN"] = "test_token"  # noqa: S105
        env["REPO"] = "org/repo"
        env["WORKFLOW"] = "apply.yml"
        env["DISPATCH_REF"] = "main"
        env["ENVIRONMENT"] = ""
        env["MODE"] = "unlock"
        env["REF"] = "abc123"
        env["PR_NUMBER"] = "42"
        env["PLAN_RUN_ID"] = "12345"

        result = subprocess.run(
            [bash, "-c", run_block],
            env=env,
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=30,
        )

        assert result.returncode == 0, (
            f"success should exit 0, got {result.returncode}: {result.stderr}"
        )


def test_real_case_accepts_valid_modes():
    """The real shipped case validation accepts valid modes.

    Executes the full script with each valid mode and verifies the body builder
    is reached (by checking the body.json created by python).
    Mutation: narrow the case pattern to ''|apply) to break unlock.
    """
    bash = usable_bash()
    if not bash:
        pytest.skip("bash not available on this platform")

    run_block = _extract_dispatch_run_block()

    for mode, expected_mode_in_body in [("", False), ("apply", False), ("unlock", True)]:
        with tempfile.TemporaryDirectory() as tmpdir:
            path_dir, python3_path, gh_path = _create_stub_commands(tmpdir)

            # Stub gh to output success; body.json was already built by python.
            gh_stub_content = "#!/bin/bash\necho '{}'\n"
            Path(gh_path).write_text(gh_stub_content)
            Path(gh_path).chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{path_dir}:{env.get('PATH', '')}"
            env["GH_TOKEN"] = "test_token"  # noqa: S105
            env["REPO"] = "org/repo"
            env["WORKFLOW"] = "apply.yml"
            env["DISPATCH_REF"] = "main"
            env["ENVIRONMENT"] = ""
            env["MODE"] = mode
            env["REF"] = "abc123"
            env["PR_NUMBER"] = "42"
            env["PLAN_RUN_ID"] = "12345"

            result = subprocess.run(
                [bash, "-c", run_block],
                env=env,
                capture_output=True,
                text=True,
                cwd=tmpdir,
                timeout=30,
            )

            # Script should succeed
            assert result.returncode == 0, (
                f"mode={mode!r} should pass validation and reach gh, "
                f"but got exit {result.returncode}: {result.stderr}"
            )

            # Verify body.json was created
            body_file = Path(tmpdir) / "body.json"
            assert body_file.exists(), f"body.json not created for mode={mode!r}"

            body = json.loads(body_file.read_text())
            if expected_mode_in_body:
                assert body.get("inputs", {}).get("mode") == "unlock", (
                    f"mode={mode!r} should include mode in inputs, got {body}"
                )
            else:
                assert "mode" not in body.get("inputs", {}), (
                    f"mode={mode!r} should not include mode in inputs, got {body}"
                )
