"""Tests for actions/dispatch's `verb` input: the verb selects one workflow
file and one body shape, and nothing else routes.

Five parts, the shape this file has always had:
- Body building: each verb's whole body, compared against a hand-written
  literal. Tested via subprocess execution of the python heredoc only.
- Validation before the API call: an empty verb and a verb outside
  {plan, apply, unlock} are both refused before `gh` runs.
- Degrade messages: an unlock or plan failure that matches the skew shape gets
  the skew explanation and its remedy; a 403, and a 422 about another input, do
  not.
- Apply path stays green: an apply failure prints the raw error alone.
- The routing surface: verb -> filename, the env: mapping, the absence of a
  `workflow` input, the filename regex, and comment-ops' `verb` output.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from _loader import action_steps, action_yaml, load_script, usable_bash

DISPATCH_ACTION = "dispatch"

#: verb -> the consumer workflow file it dispatches. Hand-written; never derived
#: from the action, which is the file under test.
VERB_WORKFLOW = {"plan": "plan.yml", "apply": "apply.yml", "unlock": "unlock.yml"}


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


def _extract_dispatch_run_block():
    """The full run: block of the dispatch step, exactly as written."""
    run = _dispatch_step().get("run", "")
    assert run.strip(), "dispatch step has no run block"
    return run


def _build_dispatch_body(verb, environment=""):
    """The whole parsed dispatch body for `verb`."""
    code = _extract_python_heredoc()
    env = os.environ.copy()
    env.update(
        {
            "DISPATCH_REF": "main",
            "REF": "abc123def456",
            "PR_NUMBER": "42",
            "ENVIRONMENT": environment,
            "VERB": verb,
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


# --- Body building: one whole literal per verb -------------------------------
#
# Whole-body comparison, not key-by-key membership: "carries exactly these
# inputs" and "carries no `mode` key" are the same assertion once the comparison
# is whole, and two selectors for one property disagree eventually.


def test_plan_body_is_the_dispatch_ref_and_pr_number_alone():
    """A plan body is exactly {ref: dispatch ref, inputs: {pr_number}}.

    plan.yml declares one input, so every extra key is a production 422; and the
    top-level ref is the dispatch ref, never the head SHA — a plan states no head.

    Mutations: add `ref`, `environment` or `mode` to the plan inputs; swap the
    top-level ref for the head SHA.
    """
    body = _build_dispatch_body("plan", environment="dev-eu")
    assert body == {"ref": "main", "inputs": {"pr_number": "42"}}, (
        f"plan body must carry the PR number alone, on the dispatch ref: {body}"
    )


def test_apply_body_omits_the_environment_when_it_is_empty():
    """A bare `shipmate apply` sends {ref, pr_number} and no environment key.

    An empty environment IS the bare apply, and GitHub reads an empty value for a
    required workflow_dispatch input as not provided (422) — so the key is
    omitted rather than sent empty.

    Mutation: drop the `if os.environ.get("ENVIRONMENT")` guard.
    """
    assert _build_dispatch_body("apply") == {
        "ref": "main",
        "inputs": {"ref": "abc123def456", "pr_number": "42"},
    }


def test_apply_body_carries_the_environment_when_it_is_set():
    """A targeted `shipmate apply dev-eu` sends {ref, pr_number, environment}.

    Mutation: drop the environment key entirely.
    """
    assert _build_dispatch_body("apply", environment="dev-eu") == {
        "ref": "main",
        "inputs": {"ref": "abc123def456", "pr_number": "42", "environment": "dev-eu"},
    }


def test_unlock_body_is_the_ref_and_environment_alone():
    """An unlock body is exactly {ref, environment} — no pr_number, no mode.

    unlock.yml releases a stranded lock: it applies no plan and comments on no
    pull request, so nothing on that path reads a PR number.

    Mutations: add `pr_number` to the unlock branch; re-add `inputs["mode"]`.
    """
    assert _build_dispatch_body("unlock", environment="dev-eu") == {
        "ref": "main",
        "inputs": {"ref": "abc123def456", "environment": "dev-eu"},
    }


# --- Validation before the API call ------------------------------------------

RECORDING_GH_STUB = "#!/bin/bash\nprintf '%s\\n' \"$@\" > argv.txt\necho '{}'\n"


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


def _run_dispatch(tmpdir, verb, environment="", gh_stub=RECORDING_GH_STUB):
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
    env["DISPATCH_REF"] = "main"
    env["ENVIRONMENT"] = environment
    env["VERB"] = verb
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


#: A gh stub that leaves a marker instead of answering: any invocation of it is
#: proof the step reached the API.
MARKER_GH_STUB = "#!/bin/bash\ntouch marker.txt\n"


def test_an_empty_verb_is_refused_before_any_api_call():
    """No verb, no dispatch — and the refusal names what the caller must wire.

    The outage this closes: a consumer that omits the `with:` line dispatched
    apply.yml with a plan body and got a 422 nothing on the pull request saw.

    Mutation: delete the empty-verb refusal branch — the step then reaches
    `gh api` (the case list rejects it, but only after the marker exists... and
    with the case list's own `''` arm re-added, all the way through).
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, verb="", gh_stub=MARKER_GH_STUB)
        output = result.stdout + result.stderr
        assert result.returncode != 0, f"an empty verb must be refused: {output}"
        assert not (Path(tmpdir) / "marker.txt").exists(), (
            f"gh was invoked; an empty verb must be refused before the API call: {output}"
        )
        assert "did not pass a verb" in output and "comment-ops" in output, (
            f"the refusal must name the missing input and where it comes from: {output}"
        )


def test_an_unknown_verb_is_refused_before_any_api_call():
    """A verb outside {plan, apply, unlock} never reaches the API.

    Mutation: add a fourth value to the `case` list.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, verb="bogus", gh_stub=MARKER_GH_STUB)
        output = result.stdout + result.stderr
        assert result.returncode != 0, "an unknown verb must be refused"
        assert not (Path(tmpdir) / "marker.txt").exists(), (
            f"gh was invoked; validation must reject before the API call: {output}"
        )
        assert "::error::verb must be plan, apply or unlock (got: bogus)" in output, (
            f"expected error message not found in output: {output}"
        )


# --- The routing surface -----------------------------------------------------


@pytest.mark.parametrize(("verb", "workflow"), sorted(VERB_WORKFLOW.items()))
def test_each_verb_dispatches_its_own_workflow_file(verb, workflow):
    """One entry point per verb: plan.yml, apply.yml, unlock.yml.

    Mutation: swap two of the three filenames in the `case`.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, argv = _run_dispatch(tmpdir, verb=verb, environment="dev-eu")
        assert result.returncode == 0, f"{verb} dispatch failed: {result.stdout}{result.stderr}"
        assert f"repos/org/repo/actions/workflows/{workflow}/dispatches" in argv, (
            f"{verb} must be dispatched at {workflow}, gh saw: {argv!r}"
        )


def test_the_action_declares_no_workflow_input():
    """The whole input vector, hand-written.

    The retired `workflow` input overrode the filename. One static name cannot
    serve three verbs, so it misroutes two of them — the verb→file mapping is the
    contract now, not an overridable default.

    Mutation: re-add `workflow` (or drop `verb`, or give `verb` a default).
    """
    inputs = action_yaml(DISPATCH_ACTION)["inputs"]
    assert sorted(inputs) == [
        "app-id",
        "dispatch-ref",
        "environment",
        "pr-number",
        "private-key",
        "ref",
        "repository",
        "verb",
    ], f"the dispatch action's inputs changed: {sorted(inputs)}"
    assert "default" not in inputs["verb"], (
        "`verb` must have no default: a guessed verb dispatches one verb's body "
        f"at another verb's workflow, got {inputs['verb']!r}"
    )


#: The whole guard line, hand-written. Compared line-wise rather than as a
#: substring so that the same words in a comment cannot satisfy it.
FILENAME_REGEX_GUARD = (
    '[[ "$WORKFLOW" =~ ^[A-Za-z0-9._-]+$ ]] || '
    '{ echo "::error::workflow must match ^[A-Za-z0-9._-]+$ (got: $WORKFLOW)"; exit 1; }'
)


def test_the_resolved_filename_is_checked_before_it_reaches_an_api_path():
    """The filename regex still guards the value interpolated into the API path.

    Structural by necessity: the `case` now resolves `WORKFLOW` to one of three
    literals, so no runtime input can make this check fire. It is the last line
    between a verb value and an API path and costs one line, so it stays — and
    only its presence is observable.

    Mutation: delete the regex check line.
    """
    lines = [line.strip() for line in _extract_dispatch_run_block().splitlines()]
    assert FILENAME_REGEX_GUARD in lines, (
        "the resolved workflow filename is no longer checked before use"
    )


def test_dispatch_step_env_mapping_is_complete():
    """Pin the whole env: mapping of the dispatch step to ensure VERB was wired.

    This catches the case where the python code is correct but VERB was never
    added to the step's env: block in the first place — and that WORKFLOW is no
    longer read from an input.

    Mutation: rename VERB in the env: block, or re-add WORKFLOW.
    """
    step = _dispatch_step()
    env = step.get("env") or {}

    expected_env_vars = {
        "GH_TOKEN",
        "REPO",
        "DISPATCH_REF",
        "ENVIRONMENT",
        "VERB",
        "REF",
        "PR_NUMBER",
    }
    actual_env_vars = set(env.keys())

    assert actual_env_vars == expected_env_vars, (
        f"dispatch step env vars should be {sorted(expected_env_vars)}, "
        f"got {sorted(actual_env_vars)}"
    )


def test_the_verb_output_of_comment_ops_is_the_parsed_route_alone():
    """comment-ops hands the route through unchanged — no fallback expression.

    A fallback here is the outage: `|| 'apply'` sends every route a branch does
    not name to the apply wrapper, carrying another verb's body.

    Mutation: append `|| 'apply'` to the expression, or rename the output.
    """
    assert action_yaml("comment-ops")["outputs"]["verb"]["value"] == (
        "${{ steps.parse.outputs.route }}"
    )


def test_every_route_comment_ops_can_dispatch_is_accepted_here():
    """comment-parse's dispatching routes and this action's `case` cannot drift.

    Nothing else couples them: a route comment-ops emits that the case rejects
    fails only at runtime, with the whole suite green. The literal below sits
    beside the derivation on purpose — a check parametrized from the registry
    cannot catch a mistake in the registry.

    Mutations: remove `plan` (or `unlock`) from the case list; add a fourth
    dispatching route to VERBS.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")

    parse = load_script("comment-parse")
    # `doctor` and `help` are answered in place; `destroy` is reserved with
    # route None. What is left is exactly what can reach a dispatch.
    routes = {
        spec["route"]
        for spec in parse.VERBS.values()
        if spec["route"] not in (None, "doctor", "help")
    }
    assert routes == {"apply", "plan", "unlock"}, (
        f"comment-parse dispatches routes {sorted(routes)}; extend the case list deliberately"
    )

    for route in sorted(routes):
        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = _run_dispatch(tmpdir, verb=route, environment="dev-eu")
            output = result.stdout + result.stderr
            assert result.returncode == 0, f"verb={route!r} was rejected: {output}"
            assert "::error::verb must be" not in output, (
                f"verb={route!r} is emitted by comment-ops but rejected here: {output}"
            )


def test_dispatch_success_exits_zero():
    """When gh succeeds, the script exits 0.

    Mutation: make the wrapper return non-zero on success.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, verb="unlock", environment="dev-eu")
        assert result.returncode == 0, (
            f"success should exit 0, got {result.returncode}: {result.stderr}"
        )


# --- Degrade messages --------------------------------------------------------
#
# Only a failure that actually matches the skew shape gets the skew explanation:
# a 403 or a rate limit explained as skew sends the operator to edit workflows
# that are fine. Each message's remedy is pinned too: neither failure is fixed by
# a re-pin, and a message that says otherwise has shipped here before.

_NO_TRIGGER_STUB = (
    "#!/bin/bash\n"
    "echo \"gh: Workflow does not have 'workflow_dispatch' trigger (HTTP 422)\" >&2\n"
    "exit 22\n"
)
_NOT_FOUND_STUB = "#!/bin/bash\necho 'gh: Not Found (HTTP 404)' >&2\nexit 1\n"
_FORBIDDEN_STUB = "#!/bin/bash\necho 'HTTP 403: Forbidden' >&2\nexit 1\n"
_UNLOCK_SKEW = "has no unlock.yml"
_PLAN_SKEW = "does not accept a dispatched plan"
# The remedy half. The consumer authors `unlock.yml` and edits `plan.yml` by
# hand; a pin bump does neither, and `docs/releasing.md` is the maintainer's
# runbook, not the page that tells them.
_UNLOCK_REMEDY = "docs/upgrading.md section 0.21.0"
_PLAN_REMEDY = "docs/upgrading.md section 0.20.0"


@pytest.mark.parametrize("stub", [_NOT_FOUND_STUB, _NO_TRIGGER_STUB])
def test_unlock_against_a_repo_with_no_unlock_wrapper_prints_the_missing_wrapper_message(stub):
    """Both shapes a missing unlock.yml produces get the missing-wrapper message.

    Measured: a workflow file that does not exist answers 404; one with no such
    trigger answers `Workflow does not have 'workflow_dispatch' trigger
    (HTTP 422)`.

    Mutation: drop either half of the message-text condition and the other
    shape stops being explained; point the remedy at `docs/releasing.md` and the
    remedy assertion reddens.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, verb="unlock", environment="dev-eu", gh_stub=stub)
        output = result.stdout + result.stderr
        assert result.returncode != 0, f"an unlock failure must exit non-zero: {output}"
        assert "HTTP 4" in output, f"raw gh output missing: {output}"
        assert _UNLOCK_SKEW in output, f"missing-wrapper message missing: {output}"
        assert _UNLOCK_REMEDY in output, f"remedy must name the upgrade guide: {output}"


def test_unlock_403_prints_no_skew_message():
    """A 403 is not version skew: the raw output prints, the message does not.

    Mutation: drop the whole `[[ ... ]]` message-text condition, so any unlock
    failure is reported as skew. Neither half alone reddens it: a 403 is neither
    a 404 nor a missing trigger.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(
            tmpdir, verb="unlock", environment="dev-eu", gh_stub=_FORBIDDEN_STUB
        )
        output = result.stdout + result.stderr
        assert result.returncode == 1, f"gh's exit status must survive, got {result.returncode}"
        assert "HTTP 403: Forbidden" in output, f"raw gh output missing: {output}"
        assert _UNLOCK_SKEW not in output, f"a 403 is not a missing wrapper: {output}"


def test_an_apply_failure_prints_no_degrade_message():
    """The apply path stays green: a failed apply prints the raw error alone.

    Mutation: remove the verb scoping so either message prints unconditionally.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, verb="apply", gh_stub=_NO_TRIGGER_STUB)
        output = result.stdout + result.stderr
        assert result.returncode == 22, f"gh's exit status must survive: {result.returncode}"
        assert "HTTP 422" in output, f"raw gh output missing: {output}"
        assert _UNLOCK_SKEW not in output and _PLAN_SKEW not in output, (
            f"no degrade message belongs on the apply path: {output}"
        )


def test_a_422_about_another_input_is_not_reported_as_skew():
    """A 422 naming an input the wrapper does not expect is not a missing wrapper.

    The v0.16.0 E2E hit this class: the consumer wrapper declared `plan_run_id`
    as required, the dispatch sent it empty, and GitHub answered `Required input
    'plan_run_id' not provided (HTTP 422)`. A condition matching any 422 told
    the operator to edit workflows that were already current, hiding the real
    cause printed one line above.

    Mutation: widen the unlock condition to any `HTTP 422` and this reddens.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    stub = (
        "#!/bin/bash\n"
        "echo \"gh: Required input 'plan_run_id' not provided (HTTP 422)\" >&2\n"
        "exit 22\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, verb="unlock", environment="dev-eu", gh_stub=stub)
        output = result.stdout + result.stderr
        assert result.returncode == 22, f"gh's exit status must survive, got {result.returncode}"
        assert "Required input 'plan_run_id' not provided" in output, (
            f"the API's own message must still print: {output}"
        )
        assert _UNLOCK_SKEW not in output, (
            f"a 422 about plan_run_id is not a missing wrapper: {output}"
        )


def test_plan_422_prints_the_skew_message():
    """A plan 422 naming the missing surface prints the skew message and remedy.

    Mutation: invert the verb half of the condition; point the remedy at
    `docs/releasing.md` and the remedy assertion reddens.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, verb="plan", gh_stub=_NO_TRIGGER_STUB)
        output = result.stdout + result.stderr
        assert result.returncode == 22, f"gh's exit status must survive: {result.returncode}"
        assert "Workflow does not have" in output, f"raw gh output missing: {output}"
        assert _PLAN_SKEW in output, f"skew message missing: {output}"
        assert _PLAN_REMEDY in output, f"remedy must name the upgrade guide: {output}"


def test_plan_403_prints_no_skew_message():
    """A plan 403 is not version skew: the raw output prints, the message does not.

    Mutation: drop the message-text half of the plan condition.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, verb="plan", gh_stub=_FORBIDDEN_STUB)
        output = result.stdout + result.stderr
        assert result.returncode == 1, f"gh's exit status must survive: {result.returncode}"
        assert "HTTP 403: Forbidden" in output, f"raw gh output missing: {output}"
        assert _PLAN_SKEW not in output, f"a 403 is not version skew: {output}"


def test_the_plan_notice_states_neither_an_environment_nor_a_ref():
    """A plan has neither, so its success notice claims neither.

    Mutation: unbranch the notice, leaving the apply wording for every verb.
    """
    if not usable_bash():
        pytest.skip("bash not available on this platform")
    with tempfile.TemporaryDirectory() as tmpdir:
        result, _ = _run_dispatch(tmpdir, verb="plan")
        output = result.stdout + result.stderr
        assert "::notice title=dispatch::dispatched plan.yml on main for PR #42" in output, (
            f"the plan notice must name the workflow, the dispatch ref and the PR: {output}"
        )
        assert "all environments" not in output and "(ref " not in output, (
            f"a plan carries no environment and no ref: {output}"
        )
