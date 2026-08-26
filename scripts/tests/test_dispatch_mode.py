"""Tests for actions/dispatch mode input: conditional dispatch body inclusion,
validation before API call, and graceful degradation when the consumer's
apply.yml does not yet declare the mode input.

Five parts:
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
- The plan mode: which workflow it targets, that its body carries the PR number
  alone, that apply and unlock bodies are untouched by it, that comment-ops'
  mode enumeration stays inside the accepted set, and its own skew message.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
from _loader import action_steps, action_yaml, usable_bash

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


def test_dispatch_body_has_no_plan_run_id_key():
    """The dispatch body carries no plan run id: each cell reads its own.

    Mutation: re-add `"plan_run_id": os.environ["PLAN_RUN_ID"]` to the body.
    """
    inputs = _build_dispatch_body()
    assert "plan_run_id" not in inputs, (
        f"the plan run id is per cell now, but the dispatch body carries: {inputs}"
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
        assert "::error::mode must be apply, unlock or plan (got: bogus)" in output, (
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
    python3_path.write_text(f'#!/bin/bash\nexec "{__import__("sys").executable}" "$@"\n')
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
            "echo 'gh: Unexpected inputs provided: [\"mode\"] (HTTP 422)' >&2\n"
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
        assert "does not declare the mode input" in output, f"degrade message missing: {output}"


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
            "echo 'gh: Unexpected inputs provided: [\"mode\"] (HTTP 422)' >&2\n"
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
        assert "does not declare the mode input" not in output, (
            f"degrade message should not appear on apply path: {output}"
        )


def test_unlock_non_422_failure_prints_no_degrade_message():
    """A 403 is not version skew: the raw output prints, the skew message does not.

    Mutation: drop the `[[ "$out" == *"Unexpected inputs provided"* ]]` half of
    the condition and this reddens, while the two tests above stay green.
    """
    bash = usable_bash()
    if not bash:
        pytest.skip("bash not available on this platform")

    run_block = _extract_dispatch_run_block()

    with tempfile.TemporaryDirectory() as tmpdir:
        path_dir, python3_path, gh_path = _create_stub_commands(tmpdir)

        Path(gh_path).write_text("#!/bin/bash\necho 'HTTP 403: Forbidden' >&2\nexit 1\n")
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

        result = subprocess.run(
            [bash, "-c", run_block],
            env=env,
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=30,
        )

        output = result.stdout + result.stderr
        assert result.returncode == 1, f"gh's exit status must survive, got {result.returncode}"
        assert "HTTP 403: Forbidden" in output, f"raw gh output missing: {output}"
        assert "does not declare the mode input" not in output, (
            f"a 403 is not an undeclared input: {output}"
        )


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


def test_a_422_about_another_input_is_not_reported_as_mode_skew():
    """A 422 naming an input other than `mode` is not version skew.

    The v0.16.0 E2E hit exactly this: the consumer wrapper declared
    `plan_run_id` as required, unlock dispatches it empty, and GitHub answered
    `Required input 'plan_run_id' not provided (HTTP 422)`. The old condition
    matched any 422 and told the operator to re-pin workflows that were already
    current, hiding the real cause printed one line above. That input is retired
    engine-side, so this 422 is now what an un-repinned consumer wrapper looks
    like -- still not mode skew.

    Mutation: widen the condition back to any `HTTP 422` and this reddens.
    """
    bash = usable_bash()
    if not bash:
        pytest.skip("bash not available on this platform")

    run_block = _extract_dispatch_run_block()

    with tempfile.TemporaryDirectory() as tmpdir:
        path_dir, python3_path, gh_path = _create_stub_commands(tmpdir)

        Path(gh_path).write_text(
            "#!/bin/bash\n"
            "echo \"gh: Required input 'plan_run_id' not provided (HTTP 422)\" >&2\n"
            "exit 22\n"
        )
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

        result = subprocess.run(
            [bash, "-c", run_block],
            env=env,
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=30,
        )

        output = result.stdout + result.stderr
        assert result.returncode == 22, f"gh's exit status must survive, got {result.returncode}"
        assert "Required input 'plan_run_id' not provided" in output, (
            f"the API's own message must still print: {output}"
        )
        assert "does not declare the mode input" not in output, (
            f"a 422 about plan_run_id is not mode skew: {output}"
        )


def _build_dispatch_whole_body(mode="", environment=""):
    """The whole parsed dispatch body, not just its inputs mapping."""
    code = _extract_python_heredoc()
    env = os.environ.copy()
    env.update(
        {
            "DISPATCH_REF": "main",
            "REF": "abc123def456",
            "PR_NUMBER": "42",
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
    return json.loads(result.stdout)


def test_plan_body_is_the_dispatch_ref_and_pr_number_alone():
    """A plan body is exactly {ref: dispatch ref, inputs: {pr_number}}.

    plan.yml declares one input, so every extra key is a production 422; and the
    top-level ref is the dispatch ref, never the head SHA — a plan states no head.

    Mutations: add `ref`, `environment` or `mode` to the plan inputs; swap the
    top-level ref for the head SHA.
    """
    body = _build_dispatch_whole_body(mode="plan", environment="dev-eu")
    assert body == {"ref": "main", "inputs": {"pr_number": "42"}}, (
        f"plan body must carry the PR number alone, on the dispatch ref: {body}"
    )


def test_apply_and_unlock_bodies_are_unchanged_by_the_plan_branch():
    """apply and unlock still carry ref + pr_number, environment when set, and
    mode only for unlock.

    Mutation: reuse the plan branch for apply.
    """
    assert _build_dispatch_whole_body(mode="") == {
        "ref": "main",
        "inputs": {"ref": "abc123def456", "pr_number": "42"},
    }
    assert _build_dispatch_whole_body(mode="apply", environment="dev-eu") == {
        "ref": "main",
        "inputs": {"ref": "abc123def456", "pr_number": "42", "environment": "dev-eu"},
    }
    assert _build_dispatch_whole_body(mode="unlock", environment="dev-eu") == {
        "ref": "main",
        "inputs": {
            "ref": "abc123def456",
            "pr_number": "42",
            "environment": "dev-eu",
            "mode": "unlock",
        },
    }


RECORDING_GH_STUB = "#!/bin/bash\nprintf '%s\\n' \"$@\" > argv.txt\necho '{}'\n"


def _run_dispatch(tmpdir, mode, workflow="", gh_stub=RECORDING_GH_STUB):
    """Run the shipped step with a gh stub that records its argv.

    Returns (CompletedProcess, recorded argv text).
    """
    path_dir, _, gh_path = _create_stub_commands(tmpdir)
    Path(gh_path).write_text(gh_stub)
    Path(gh_path).chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{path_dir}:{env.get('PATH', '')}"
    env["GH_TOKEN"] = "test_token"  # noqa: S105
    env["REPO"] = "org/repo"
    env["WORKFLOW"] = workflow
    env["DISPATCH_REF"] = "main"
    env["ENVIRONMENT"] = ""
    env["MODE"] = mode
    env["REF"] = "abc123"
    env["PR_NUMBER"] = "42"

    result = subprocess.run(
        [usable_bash(), "-c", _extract_dispatch_run_block()],
        env=env,
        capture_output=True,
        text=True,
        cwd=tmpdir,
        timeout=30,
    )
    argv_file = Path(tmpdir) / "argv.txt"
    return result, (argv_file.read_text() if argv_file.exists() else "")


def test_plan_with_no_workflow_input_dispatches_plan_yml():
    """mode=plan with no workflow input dispatches plan.yml, not apply.yml.

    Mutation: drop the plan branch of the workflow default.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, argv = _run_dispatch(tmpdir, mode="plan")
        assert result.returncode == 0, f"plan dispatch failed: {result.stdout}{result.stderr}"
        assert "repos/org/repo/actions/workflows/plan.yml/dispatches" in argv, (
            f"plan must be dispatched at plan.yml, gh saw: {argv!r}"
        )


def test_apply_with_no_workflow_input_still_dispatches_apply_yml():
    """mode=apply with no workflow input still dispatches apply.yml.

    Mutation: default to plan.yml unconditionally.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, argv = _run_dispatch(tmpdir, mode="apply")
        assert result.returncode == 0, f"apply dispatch failed: {result.stdout}{result.stderr}"
        assert "repos/org/repo/actions/workflows/apply.yml/dispatches" in argv, (
            f"apply must be dispatched at apply.yml, gh saw: {argv!r}"
        )


def test_explicit_workflow_input_wins_for_plan():
    """An explicit workflow input beats the plan default — the escape hatch.

    Mutation: let the plan branch overwrite a non-empty WORKFLOW.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, argv = _run_dispatch(tmpdir, mode="plan", workflow="house-plan.yml")
        assert result.returncode == 0, f"plan dispatch failed: {result.stdout}{result.stderr}"
        assert "repos/org/repo/actions/workflows/house-plan.yml/dispatches" in argv, (
            f"the explicit workflow input must win, gh saw: {argv!r}"
        )


def test_every_mode_comment_ops_emits_is_accepted_here():
    """comment-ops' mode enumeration and this action's `case` list cannot drift.

    Nothing else couples them: a mode comment-ops emits that the case rejects
    fails only at runtime, with the whole suite green.

    Mutation: remove `plan` (or `unlock`) from the case list.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")

    # Every literal the expression *yields* sits behind `&&` or `||`; the ones
    # it compares a route against sit behind `==`.
    value = action_yaml("comment-ops")["outputs"]["mode"]["value"]
    modes = set(re.findall(r"(?:&&|\|\|)\s*'([a-z-]+)'", value))
    assert modes == {"apply", "unlock", "plan"}, (
        f"comment-ops emits modes {sorted(modes)}; extend the case list deliberately"
    )

    for mode in sorted(modes):
        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = _run_dispatch(tmpdir, mode=mode)
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"mode={mode!r} was rejected: {output}"
            assert "::error::mode must be" not in output, (
                f"mode={mode!r} is emitted by comment-ops but rejected here: {output}"
            )


def test_plan_422_prints_the_repin_message():
    """A plan 422 naming the missing surface prints the re-pin message.

    Measured: GitHub answers `Workflow does not have 'workflow_dispatch' trigger
    (HTTP 422)` for a workflow with no such trigger.

    Mutation: invert the mode half of the condition.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    stub = (
        "#!/bin/bash\n"
        "echo \"gh: Workflow does not have 'workflow_dispatch' trigger (HTTP 422)\" >&2\n"
        "exit 22\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, mode="plan", gh_stub=stub)
        output = result.stdout + result.stderr
        assert result.returncode == 22, f"gh's exit status must survive: {result.returncode}"
        assert "Workflow does not have" in output, f"raw gh output missing: {output}"
        assert "does not accept a dispatched plan" in output, f"re-pin message missing: {output}"


def test_plan_403_prints_no_repin_message():
    """A plan 403 is not version skew: the raw output prints, the message does not.

    Mutation: drop the message-text half of the plan condition.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    stub = "#!/bin/bash\necho 'HTTP 403: Forbidden' >&2\nexit 1\n"
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, mode="plan", gh_stub=stub)
        output = result.stdout + result.stderr
        assert result.returncode == 1, f"gh's exit status must survive: {result.returncode}"
        assert "HTTP 403: Forbidden" in output, f"raw gh output missing: {output}"
        assert "does not accept a dispatched plan" not in output, (
            f"a 403 is not version skew: {output}"
        )


def test_the_plan_notice_states_neither_an_environment_nor_a_ref():
    """A plan has neither, so its success notice claims neither.

    Mutation: unbranch the notice, leaving the apply wording for every mode.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, mode="plan")
        output = result.stdout + result.stderr
        assert "::notice title=dispatch::dispatched plan.yml on main for PR #42" in output, (
            f"the plan notice must name the workflow, the dispatch ref and the PR: {output}"
        )
        assert "all environments" not in output and "(ref " not in output, (
            f"a plan carries no environment and no ref: {output}"
        )
