"""Tests for actions/dispatch's `verb` input: every verb dispatches one consumer file, and the
verb rides in the body — selecting the job that runs — alongside one body shape per verb.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
from _loader import (
    ACTIONS,
    SCRIPTS,
    action_yaml,
    bash_only,
    load_script,
    run_step,
    step_by,
    write_python3_shim,
)

DISPATCH_ACTION = "dispatch"

#: Every verb reaches the same consumer file; the verb rides in the body and selects a job
#: there. Hand-written; never derived from the action, which is the file under test.
CONSUMER_WORKFLOW = "shipmate.yml"


def _extract_dispatch_run_block():
    """The full run: block of the dispatch step, exactly as written."""
    run = step_by(DISPATCH_ACTION, name="workflow_dispatch apply").get("run", "")
    assert run.strip(), "dispatch step has no run block"
    return run


def _build_dispatch_body(verb, environment=""):
    """The whole parsed dispatch body for `verb`."""
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
        [__import__("sys").executable, str(SCRIPTS / "dispatch-body")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"python body builder failed: {result.stderr}"
    return json.loads(result.stdout)


# Whole-body comparison, not key-by-key membership: "carries exactly these inputs" and
# "carries no `mode` key" are the same assertion once the comparison is whole, and two
# selectors for one property disagree eventually.


def test_plan_body_is_the_dispatch_ref_the_verb_and_pr_number_alone():
    """A plan body is exactly {ref: dispatch ref, inputs: {verb, pr_number}}. `verb` is what
    selects the `plan` job in `shipmate.yml`; without it every job's `if:` is false and the
    dispatched run completes having done nothing. The top-level ref is the dispatch ref rather
    than the head SHA, because a plan states no head.

    Mutations: drop the `verb` key; add `ref` or `environment` to the plan inputs; swap the
    top-level ref for the head SHA; drop `return_run_details`, which every body carries so the
    API answers with the started run's id.
    """
    body = _build_dispatch_body("plan", environment="dev-eu")
    assert body == {
        "ref": "main",
        "inputs": {"verb": "plan", "pr_number": "42"},
        "return_run_details": True,
    }, f"plan body must carry the verb and the PR number alone, on the dispatch ref: {body}"


def test_apply_body_omits_the_environment_when_it_is_empty():
    """A bare `shipmate apply` sends {verb, ref, pr_number} and no environment key, which
    `apply.yml` reads as `inputs.environment == ''` and runs as the bare form.

    Mutations: drop the `if os.environ.get("ENVIRONMENT")` guard -- the body carries an empty
    `environment`, red; drop `return_run_details`.
    """
    assert _build_dispatch_body("apply") == {
        "ref": "main",
        "inputs": {"verb": "apply", "ref": "abc123def456", "pr_number": "42"},
        "return_run_details": True,
    }


def test_apply_body_carries_the_environment_when_it_is_set():
    """A targeted `shipmate apply dev-eu` sends {verb, ref, pr_number, environment}.

    Mutations: drop the environment key entirely; drop `return_run_details`.
    """
    assert _build_dispatch_body("apply", environment="dev-eu") == {
        "ref": "main",
        "inputs": {
            "verb": "apply",
            "ref": "abc123def456",
            "pr_number": "42",
            "environment": "dev-eu",
        },
        "return_run_details": True,
    }


def test_unlock_body_is_the_verb_the_ref_and_the_environment_alone():
    """An unlock body is exactly {verb, ref, environment} — no pr_number. Releasing a stranded
    lock applies no plan and comments on no pull request, so nothing on that path reads one,
    and `pr_number` is `required: false` on `shipmate.yml` precisely so this body is legal.

    Mutations: add `pr_number` to the unlock branch; drop `verb`, and the dispatched run
    matches no job's `if:` and unlocks nothing while reporting success; drop
    `return_run_details`.
    """
    assert _build_dispatch_body("unlock", environment="dev-eu") == {
        "ref": "main",
        "inputs": {"verb": "unlock", "ref": "abc123def456", "environment": "dev-eu"},
        "return_run_details": True,
    }


#: Succeeds and writes nothing, so a test can list the step's working directory.
_SILENT_GH_STUB = "#!/bin/bash\necho '{}'\n"

#: Appends, so the dispatch call's argv survives the reply call that follows it.
#: A reply's body arrives on stdin (`body=@-`) and is appended after its argv, then a newline.
RECORDING_GH_STUB = (
    "#!/bin/bash\nprintf '%s\\n' \"$@\" >> argv.txt\n"
    '[[ " $* " == *" body=@- "* ]] && { cat; echo; } >> argv.txt\n'
    "echo '{}'\n"
)


def _on_dispatch_call(action):
    """A gh stub that runs the bash lines `action` on the dispatch call, the one whose
    argument is a dispatches path, and succeeds silently on every other call."""
    return (
        "#!/bin/bash\n"
        'for a in "$@"; do if [[ "$a" == repos/*/actions/workflows/*/dispatches ]]; then\n'
        f"{action}\nfi; done\n"
    )


def _create_stub_commands(tmp_path, gh_stub):
    """Create the `python3` shim and a `gh` stub with text `gh_stub` on PATH; returns
    (dir, python3)."""
    write_python3_shim(tmp_path)
    gh_path = tmp_path / "gh"
    gh_path.write_text(gh_stub)
    gh_path.chmod(0o755)
    return str(tmp_path), str(tmp_path / "python3")


def _run_dispatch(
    tmp_path,
    verb,
    environment="",
    gh_stub=RECORDING_GH_STUB,
    failing_script="",
    stubs=None,
    cwd=None,
):
    """Run the shipped step with a gh stub that records its argv; returns the
    CompletedProcess and the recorded argv text. When `failing_script` names an engine script,
    `python3` exits 1 for that script and runs every other one. `stubs` maps further command
    names to stub text on the same PATH directory. The step runs in `cwd`, or `tmp_path`."""
    path_dir, python3_path = _create_stub_commands(tmp_path, gh_stub)
    for name, text in (stubs or {}).items():
        (Path(path_dir) / name).write_text(text, newline="\n")
        (Path(path_dir) / name).chmod(0o755)
    if failing_script:
        real = tmp_path / "python3-real"
        Path(python3_path).rename(real)
        Path(python3_path).write_text(
            f'#!/bin/bash\nif [[ "$1" == */{failing_script} ]]; then exit 1; fi\n'
            f'exec "{real.as_posix()}" "$@"\n',
            newline="\n",
        )
        Path(python3_path).chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{path_dir}:{env.get('PATH', '')}"
    env["GH_TOKEN"] = "test_token"  # noqa: S105
    env["REPO"] = "org/repo"
    env["DISPATCH_REF"] = "main"
    env["ENVIRONMENT"] = environment
    env["VERB"] = verb
    env["REF"] = "abc123"
    env["PR_NUMBER"] = "42"
    env["COMMENT_TOKEN"] = "workflow_token"  # noqa: S105
    env["GITHUB_ACTION_PATH"] = str(ACTIONS / "dispatch")
    # Runner-supplied, and the failure comment's only content besides fixed text.
    env["GITHUB_REPOSITORY"] = "org/repo"
    env["GITHUB_SERVER_URL"] = "https://github.com"
    env["GITHUB_RUN_ID"] = "7777"
    # `mktemp` honours TMPDIR, so a temporary file the step leaves behind is visible here.
    (tmp_path / "tmp").mkdir()
    env["TMPDIR"] = str(tmp_path / "tmp")

    result = run_step(tmp_path, _extract_dispatch_run_block(), env, cwd=cwd)
    argv_file = tmp_path / "argv.txt"
    return result, (argv_file.read_text() if argv_file.exists() else "")


#: The path segment that means a dispatch was actually attempted. The comment the
#: refusals post is a `gh api` call too, so "gh ran at all" no longer separates the
#: two -- this does.
_DISPATCH_PATH = "/actions/workflows/"


@bash_only
def test_an_empty_verb_dispatches_nothing_and_says_so_on_the_pull_request(tmp_path):
    """No verb, no dispatch, and the refusal names what the caller must wire. Without it the
    body carries an empty `verb`, which GitHub reads as not provided for a `required: true`
    input and answers HTTP 422 — a raw API error on a run nobody is watching, where the wiring
    error names the missing `with:` line on the pull request instead. A consumer wired that way
    is the shape this path exists for, so the commenter is told rather than left with a rocket
    reaction and silence.

    Mutations: delete the refusal branch *and* give the `case` an `""` arm -- deleting it
    alone leaves the case's `*)` arm refusing, so the pair is the hole; drop the branch's
    `say_no_run_started` call, and the comment does not appear.
    """
    result, argv = _run_dispatch(tmp_path, verb="", gh_stub=RECORDING_GH_STUB)
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"an empty verb must be refused: {output}"
    assert _DISPATCH_PATH not in argv, f"an empty verb must dispatch nothing: {argv!r}"
    assert "did not pass a verb" in output and "comment-ops" in output, (
        f"the refusal must name the missing input and where it comes from: {output}"
    )
    assert "/issues/42/comments" in argv and _RUN_URL in argv, (
        f"the commenter must be told no run started: {argv!r}"
    )


@bash_only
def test_an_unknown_verb_dispatches_nothing_and_says_so_on_the_pull_request(tmp_path):
    """A verb outside {plan, apply, unlock} reaches no workflow, and says so.

    Mutations: add a fourth value to the `case` list; drop that arm's `say_no_run_started`.
    """
    result, argv = _run_dispatch(tmp_path, verb="bogus", gh_stub=RECORDING_GH_STUB)
    output = result.stdout + result.stderr
    assert result.returncode != 0, "an unknown verb must be refused"
    assert _DISPATCH_PATH not in argv, f"an unknown verb must dispatch nothing: {argv!r}"
    assert "::error::verb must be plan, apply or unlock (got: bogus)" in output, (
        f"expected error message not found in output: {output}"
    )
    assert "/issues/42/comments" in argv and _RUN_URL in argv, (
        f"the commenter must be told no run started: {argv!r}"
    )


@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
@bash_only
def test_every_verb_dispatches_the_one_consumer_file(tmp_path, verb):
    """One entry point for every verb: the file is fixed, and the body's `verb` selects the
    job. What kept the verbs apart before was the filename; what keeps them apart now is the
    five `if:` expressions `shipmate doctor`'s `shipmate.yml` probe compares whole.

    Mutation: make the `case` resolve a per-verb filename.
    """
    result, argv = _run_dispatch(tmp_path, verb=verb, environment="dev-eu")
    assert result.returncode == 0, f"{verb} dispatch failed: {result.stdout}{result.stderr}"
    assert f"repos/org/repo/actions/workflows/{CONSUMER_WORKFLOW}/dispatches" in argv, (
        f"{verb} must be dispatched at {CONSUMER_WORKFLOW}, gh saw: {argv!r}"
    )


def test_the_action_declares_no_workflow_input():
    """The whole input vector, hand-written. A `workflow` input would override the one filename
    every verb aims at, sending an authorized command to a file nothing else reaches: the
    consumer filename is the contract, not an overridable default.

    Mutation: re-add `workflow` (or drop `verb`, or give `verb` a default).
    """
    inputs = action_yaml(DISPATCH_ACTION)["inputs"]
    assert sorted(inputs) == [
        "app-id",
        "environment",
        "pr-number",
        "private-key",
        "ref",
        "verb",
    ], f"the dispatch action's inputs changed: {sorted(inputs)}"
    assert "default" not in inputs["verb"], (
        "`verb` must have no default: a guessed verb runs one verb's job with "
        f"another verb's inputs, got {inputs['verb']!r}"
    )


def test_dispatch_step_env_mapping_is_complete():
    """The whole env: mapping of the dispatch step, so a python body that is correct but never
    got VERB added to the step's env: block reddens here -- as does WORKFLOW still being read
    from an input, or the dispatch ref leaving the default branch. The dispatch ref picks which
    branch's copy of `shipmate.yml` runs: a head ref runs the pull request author's copy, and
    the `shipmate-engine` environment's branch policy admits only the default branch.

    Mutation: rename VERB in the env: block, re-add WORKFLOW, or set
    `DISPATCH_REF: ${{ github.head_ref }}`.
    """
    assert step_by(DISPATCH_ACTION, name="workflow_dispatch apply").get("env") == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "REPO": "${{ github.repository }}",
        "DISPATCH_REF": "${{ github.event.repository.default_branch }}",
        "ENVIRONMENT": "${{ inputs.environment }}",
        "VERB": "${{ inputs.verb }}",
        "REF": "${{ inputs.ref }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        "COMMENT_TOKEN": "${{ github.token }}",
    }


def test_the_verb_output_of_comment_ops_is_the_parsed_route_alone():
    """comment-ops hands the route through unchanged — no fallback expression.

    A fallback here is the outage: `|| 'apply'` sends every route a branch does
    not name to the apply jobs, carrying another verb's body.

    Mutation: append `|| 'apply'` to the expression, or rename the output.
    """
    assert action_yaml("comment-ops")["outputs"]["verb"]["value"] == (
        "${{ steps.parse.outputs.route }}"
    )


@bash_only
def test_every_route_comment_ops_can_dispatch_is_accepted_here(tmp_path):
    """comment-parse's dispatching routes and this action's `case` cannot drift. Nothing else
    couples them: a route comment-ops emits that the case rejects fails only at runtime, with
    the whole suite green. The literal below sits beside the derivation because a check
    parametrized from the registry cannot catch a mistake in the registry.

    Mutations: remove `plan` (or `unlock`) from the case list; add a fourth dispatching route
    to VERBS.
    """
    parse = load_script("comment-parse")
    # `doctor` and `help` are answered in place; what is left is exactly what can reach a dispatch.
    routes = {verb for verb in parse.VERBS if verb not in ("doctor", "help")}
    assert routes == {"apply", "plan", "unlock"}, (
        f"comment-parse dispatches routes {sorted(routes)}; extend the case list deliberately"
    )

    for route in sorted(routes):
        (tmp_path / route).mkdir()
        result, _ = _run_dispatch(tmp_path / route, verb=route, environment="dev-eu")
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"verb={route!r} was rejected: {output}"
        assert "::error::verb must be" not in output, (
            f"verb={route!r} is emitted by comment-ops but rejected here: {output}"
        )


@bash_only
def test_dispatch_success_exits_zero(tmp_path):
    """When gh succeeds, the script exits 0, the file holding gh's stderr is removed, and the
    job workspace (the step's working directory) stays empty.

    Mutations: make the wrapper return non-zero on success; drop the `trap` that removes
    `$errfile`; write the body to `req.json` and pass `--input req.json`.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result, _ = _run_dispatch(
        tmp_path, verb="unlock", environment="dev-eu", gh_stub=_SILENT_GH_STUB, cwd=workspace
    )
    assert result.returncode == 0, (
        f"success should exit 0, got {result.returncode}: {result.stderr}"
    )
    assert list((tmp_path / "tmp").iterdir()) == [], "the step left a temporary file behind"
    assert list(workspace.iterdir()) == [], "the step left a file in the workspace"


#: Fails the dispatch as gh does on an HTTP error, here a 500: the API's JSON error body on
#: stdout, gh's own message on stderr. The reply call succeeds.
_BODY_ON_STDOUT_STUB = _on_dispatch_call(
    """  echo '{"message":"Server Error","status":"500"}'\n"""
    "  echo 'gh: Server Error (HTTP 500)' >&2\n"
    "  exit 1"
)


@bash_only
def test_a_failed_dispatch_prints_the_api_answer_to_the_log(tmp_path):
    """gh writes an HTTP error's JSON body to stdout, and the log is where the failure reply
    sends the reader, so that body must print beside gh's stderr. The failure exit still
    removes the file holding gh's stderr and leaves the job workspace (the step's working
    directory) empty.

    Mutations: delete the `$out` `printf` line; drop the `trap` that removes `$errfile`;
    write the body to `req.json` and pass `--input req.json`.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result, _ = _run_dispatch(tmp_path, verb="apply", gh_stub=_BODY_ON_STDOUT_STUB, cwd=workspace)
    assert result.returncode == 1, f"gh's exit status must survive: {result.returncode}"
    assert '{"message":"Server Error","status":"500"}' in result.stdout.splitlines(), (
        f"the API's answer must reach the log: {result.stdout}"
    )
    assert list((tmp_path / "tmp").iterdir()) == [], "the step left a temporary file behind"
    assert list(workspace.iterdir()) == [], "the step left a file in the workspace"


@bash_only
def test_a_failed_mktemp_says_so_on_the_pull_request_and_dispatches_nothing(tmp_path):
    """`mktemp` fails here. The step exits 1 without the dispatch call, annotates the failure,
    and posts the failure reply carrying the run link.

    Mutations: delete the `mktemp` handler; drop the handler's `say_no_run_started`.
    """
    result, argv = _run_dispatch(tmp_path, verb="apply", stubs={"mktemp": "#!/bin/bash\nexit 1\n"})
    assert result.returncode == 1, f"a failed mktemp must fail the step: {result.stdout}"
    assert (
        "::error::could not create a temporary file for the dispatch" in result.stdout.splitlines()
    ), f"the failure must be annotated: {result.stdout}"
    assert _DISPATCH_PATH not in argv, f"a failed mktemp must dispatch nothing: {argv!r}"
    assert "/issues/42/comments" in argv and _RUN_URL in argv, (
        f"the commenter must be told no run started: {argv!r}"
    )


@bash_only
def test_a_failed_body_build_says_so_on_the_pull_request_and_dispatches_nothing(tmp_path):
    """`python3` exits 1 for `dispatch-body` here. The step exits 1 without the dispatch call,
    annotates the failure, and posts the failure reply carrying the run link.

    Mutations: drop the body build's `|| { ...; say_no_run_started; exit 1; }` handler; drop
    the handler's `::error::` echo.
    """
    result, argv = _run_dispatch(tmp_path, verb="apply", failing_script="dispatch-body")
    assert result.returncode == 1, f"a failed body build must fail the step: {result.stdout}"
    assert "::error::could not build the dispatch request body" in result.stdout.splitlines(), (
        f"the failure must be annotated: {result.stdout}"
    )
    assert _DISPATCH_PATH not in argv, f"a failed body build must dispatch nothing: {argv!r}"
    assert "/issues/42/comments" in argv and _RUN_URL in argv, (
        f"the commenter must be told no run started: {argv!r}"
    )


#: Saves the dispatch call's stdin to `sent.txt` only when its argv carries `--input -`;
#: every other call succeeds silently.
_STDIN_SAVING_STUB = _on_dispatch_call(
    "  prev=''\n"
    '  for b in "$@"; do\n'
    '    if [ "$prev" = --input ] && [ "$b" = - ]; then cat > sent.txt; fi\n'
    '    prev="$b"\n'
    "  done\n"
    "  exit 0"
)


@pytest.mark.parametrize(
    ("verb", "environment", "expected"),
    [
        (
            "plan",
            "dev-eu",
            (
                '{"ref": "main", "inputs": {"verb": "plan", "pr_number": "42"}, '
                '"return_run_details": true}\n'
            ),
        ),
        (
            "apply",
            "",
            (
                '{"ref": "main", "inputs": {"verb": "apply", "ref": "abc123", "pr_number": "42"}, '
                '"return_run_details": true}\n'
            ),
        ),
        (
            "apply",
            "dev-eu",
            (
                '{"ref": "main", "inputs": {"verb": "apply", "ref": "abc123", "pr_number": "42", '
                '"environment": "dev-eu"}, "return_run_details": true}\n'
            ),
        ),
        (
            "unlock",
            "dev-eu",
            (
                '{"ref": "main", "inputs": {"verb": "unlock", "ref": "abc123", '
                '"environment": "dev-eu"}, "return_run_details": true}\n'
            ),
        ),
    ],
    ids=["plan", "apply-bare", "apply-env", "unlock"],
)
@bash_only
def test_the_built_body_reaches_the_dispatch_call_on_stdin_whole(
    tmp_path, verb, environment, expected
):
    """For every verb, the dispatch call carries `--input -`, and its stdin is the hand-written
    body for that verb byte for byte, with the one newline the here-string appends.

    Mutations: drop `--input -` from the dispatch call; pass `"${body:0:5}"` instead of
    `"$body"`; run `dispatch-body` with a different `ENVIRONMENT` when the verb is `unlock`.
    """
    result, _ = _run_dispatch(
        tmp_path, verb=verb, environment=environment, gh_stub=_STDIN_SAVING_STUB
    )
    assert result.returncode == 0, f"dispatch failed: {result.stdout}{result.stderr}"
    sent = tmp_path / "sent.txt"
    assert sent.exists(), "the dispatch call did not read its body from stdin via --input -"
    assert sent.read_text(encoding="utf-8") == expected


def _silent_answer_stub(out, rc):
    """A gh stub answering the dispatch call with `out` on stdout, nothing on stderr, and exit
    `rc`; every other call succeeds and prints nothing."""
    return _on_dispatch_call(f"  printf '%s' '{out}'\n  exit {rc}")


@pytest.mark.parametrize(
    ("out", "rc"),
    [("", 0), ('{"workflow_run_id":38481239914}', 0), ('{"message":"Server Error"}', 1)],
    ids=["204-empty", "200-answer", "failure-empty-stderr"],
)
@bash_only
def test_the_log_prints_no_empty_line_for_a_missing_answer_or_error(tmp_path, out, rc):
    """An empty answer (a 204) or an empty gh stderr (most successes, and a failure gh explains
    on stdout alone) prints no line at all.

    Mutation: print both with one `printf '%s\\n' "$out" "$err"`.
    """
    result, _ = _run_dispatch(tmp_path, verb="apply", gh_stub=_silent_answer_stub(out, rc))
    assert result.returncode == rc, f"unexpected exit: {result.stdout}{result.stderr}"
    assert "" not in result.stdout.splitlines(), f"the log has an empty line: {result.stdout!r}"


# Only a failure that matches the skew shape gets the skew explanation: a 403 or a rate limit
# explained as skew sends the operator to edit workflows that are fine. The remedy is pinned
# too, because no such failure is fixed by a re-pin.

_NO_TRIGGER_STUB = (
    "#!/bin/bash\n"
    "echo \"gh: Workflow does not have 'workflow_dispatch' trigger (HTTP 422)\" >&2\n"
    "exit 22\n"
)
_NOT_FOUND_STUB = "#!/bin/bash\necho 'gh: Not Found (HTTP 404)' >&2\nexit 1\n"
_UNEXPECTED_INPUTS_STUB = (
    "#!/bin/bash\necho 'gh: Unexpected inputs provided: [\"verb\"] (HTTP 422)' >&2\nexit 22\n"
)
_FORBIDDEN_STUB = "#!/bin/bash\necho 'HTTP 403: Forbidden' >&2\nexit 1\n"
_LAYOUT_SKEW = "has no .github/workflows/shipmate.yml"
#: The whole message, hand-written. It carries no interpolation, so there is one fully known
#: value to compare and a part-check would only relocate the hole -- the remedy half in
#: particular, which must name the page that publishes the file the consumer has to author:
#: a pin bump does not write it, and `docs/releasing.md` is the maintainer's runbook.
#: `_LAYOUT_SKEW` stays a substring for the two tests that assert this message is ABSENT.
_LAYOUT_MESSAGE = (
    "::error::This repository has no .github/workflows/shipmate.yml, or that file declares no "
    "workflow_dispatch trigger or not the inputs this verb's dispatch sends. "
    "docs/getting-started.md publishes it; re-pinning cannot create it."
)


@pytest.mark.parametrize("stub", [_NOT_FOUND_STUB, _NO_TRIGGER_STUB, _UNEXPECTED_INPUTS_STUB])
@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
@bash_only
def test_a_dispatch_against_a_repo_without_shipmate_yml_prints_the_layout_message(
    tmp_path, verb, stub
):
    """All three shapes an absent or incomplete `shipmate.yml` produces get the layout message,
    for every verb — every verb now aims at that one file. Measured: a workflow file that does
    not exist answers 404; one with no such trigger answers `Workflow does not have
    'workflow_dispatch' trigger (HTTP 422)`; one declaring fewer inputs than the body names
    answers `Unexpected inputs provided`.

    Mutations: scope the condition to one verb and the other two stop being explained;
    drop any one of the three message-text halves and its shape stops being explained; point
    the remedy at `docs/releasing.md`, or reword any other part of the message, and the
    whole-value comparison reddens; delete the `$err` `printf` line and the hint prints without
    the answer it explains (gh writes it to stderr); match on `$out` alone and no shape is
    explained.
    """
    result, _ = _run_dispatch(tmp_path, verb=verb, environment="dev-eu", gh_stub=stub)
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"a {verb} failure must exit non-zero: {output}"
    # The hint explains the answer; it does not replace it. Pinned per shape, because the
    # remaining two carry no distinctive sentence of their own -- and against stdout alone,
    # because the refusal comment's own gh call leaks the same text on stderr, which is what
    # made the `"HTTP 4" in output` this replaces unable to fail.
    if stub is _NO_TRIGGER_STUB:
        assert "Workflow does not have" in result.stdout, (
            f"the API's own answer must still print beside the hint: {output}"
        )
    assert _LAYOUT_MESSAGE in output.splitlines(), (
        f"the layout message must be emitted whole and unchanged: {output}"
    )


@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
@bash_only
def test_a_403_prints_no_skew_message(tmp_path, verb):
    """A 403 is not version skew, on any verb: the raw output prints, the message does not.

    Mutation: drop the whole `[[ ... ]]` message-text condition, so any failure is reported as
    skew. No single one of its three halves reddens it, because a 403 is none of them.
    """
    result, _ = _run_dispatch(tmp_path, verb=verb, environment="dev-eu", gh_stub=_FORBIDDEN_STUB)
    output = result.stdout + result.stderr
    assert result.returncode == 1, f"gh's exit status must survive, got {result.returncode}"
    assert "HTTP 403: Forbidden" in output, f"raw gh output missing: {output}"
    assert _LAYOUT_SKEW not in output, f"a 403 is not a missing consumer file: {output}"


@bash_only
def test_a_422_about_another_input_is_not_reported_as_skew(tmp_path):
    """A 422 naming an input the wrapper does not expect is not a missing wrapper. The v0.16.0
    E2E hit this class: a consumer wrapper declared `plan_run_id` required, the dispatch sent it
    empty, and GitHub answered `Required input 'plan_run_id' not provided (HTTP 422)`. Matching
    any 422 tells the operator to edit workflows that are already current and hides the real
    cause printed one line above.

    Mutation: match any `HTTP 422` and this reddens.
    """
    stub = (
        "#!/bin/bash\n"
        "echo \"gh: Required input 'plan_run_id' not provided (HTTP 422)\" >&2\n"
        "exit 22\n"
    )
    result, _ = _run_dispatch(tmp_path, verb="unlock", environment="dev-eu", gh_stub=stub)
    output = result.stdout + result.stderr
    assert result.returncode == 22, f"gh's exit status must survive, got {result.returncode}"
    assert "Required input 'plan_run_id' not provided" in output, (
        f"the API's own message must still print: {output}"
    )
    assert _LAYOUT_SKEW not in output, (
        f"a 422 about plan_run_id is not a missing consumer file: {output}"
    )


#: Fails like `_NOT_FOUND_STUB` and records each call's token ahead of its argv, so what
#: the failure branch does after the refusal is observable rather than inferred.
_RECORDING_FAILURE_STUB = (
    "#!/bin/bash\nprintf 'token=%s\\n' \"$GH_TOKEN\" >> argv.txt\n"
    "printf '%s\\n' \"$@\" >> argv.txt\n"
    '[[ " $* " == *" body=@- "* ]] && { cat; echo; } >> argv.txt\n'
    "echo 'gh: Not Found (HTTP 404)' >&2\nexit 1\n"
)
#: The run link, hand-written from the three runner variables the step reads.
_RUN_URL = "https://github.com/org/repo/actions/runs/7777"


@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
@bash_only
def test_a_failed_dispatch_says_so_on_the_pull_request(tmp_path, verb):
    """Every failure here lands on a run whose checks attach to the dispatch ref, so the pull
    request that asked for the verb shows no check, no comment and no gate change -- and the
    commenter's last signal was comment-ops' rocket. One comment, on the pull request, carrying
    the run link.

    Mutations: delete the comment call; scope it to one verb; swap `$COMMENT_TOKEN` back to
    `$GH_TOKEN`, which is minted `actions: write` and cannot comment.
    """
    result, argv = _run_dispatch(
        tmp_path, verb=verb, environment="dev-eu", gh_stub=_RECORDING_FAILURE_STUB
    )
    assert result.returncode == 1, f"gh's exit status must survive: {result.returncode}"
    # Both calls, in one argv: the comment must not erase the record of the dispatch it
    # is reporting on.
    assert _DISPATCH_PATH in argv, f"the dispatch was never attempted: {argv!r}"
    assert "token=workflow_token\napi\n-X\nPOST\nrepos/org/repo/issues/42/comments\n" in argv, (
        f"a failed {verb} dispatch must comment with the workflow token, gh saw: {argv!r}"
    )
    assert _RUN_URL in argv, f"the comment must carry the run link, gh saw: {argv!r}"
    assert "Not Found (HTTP 404)" not in argv, (
        f"the API's answer is unbounded and must not be pasted into a comment: {argv!r}"
    )


#: Fails every call and saves the comment body it is handed.
_BODY_FAILURE_STUB = (
    "#!/bin/bash\n"
    'for a in "$@"; do if [[ "$a" == body=@- ]]; then cat > body.txt; fi; '
    "done\n"
    "echo 'gh: Not Found (HTTP 404)' >&2\nexit 1\n"
)


@bash_only
def test_a_failed_dispatch_posts_the_shared_failure_reply(tmp_path):
    """The whole comment: header naming the verb and environment, a `failed` verdict, and the
    footer that carries the run link.

    Mutations: post the old `:x: shipmate:` sentence without reply-comment; set the outcome to
    `refused`.
    """
    result, _ = _run_dispatch(
        tmp_path, verb="apply", environment="dev-eu", gh_stub=_BODY_FAILURE_STUB
    )
    assert result.returncode == 1, f"gh's exit status must survive: {result.returncode}"
    assert (tmp_path / "body.txt").read_text(encoding="utf-8") == (
        "### shipmate apply dev-eu\n\n"
        "🔴 failed: the command was authorized, but dispatching its workflow failed, so no run "
        "started. The error is in this run's log.\n\n"
        f"[run]({_RUN_URL}). Comment `shipmate help` for the available commands."
    )


def _answering_stub(answer, reply_exit=0):
    """A gh stub that answers the dispatch call with `answer` on stdout and a warning on stderr,
    records each call's token and argv, saves the reply's body and the run id handed to
    `reply-comment`, and exits `reply_exit` on the reply call. The dispatch call is the one
    whose API path argument is a dispatches path."""
    return (
        "#!/bin/bash\nprintf 'token=%s\\n' \"$GH_TOKEN\" >> argv.txt\n"
        "printf '%s\\n' \"$@\" >> argv.txt\n"
        'for a in "$@"; do if [[ "$a" == repos/*/actions/workflows/*/dispatches ]]; then\n'
        "  echo 'warning: gh noise on a successful call' >&2\n"
        f"  cat <<'ANSWER'\n{answer}\nANSWER\n"
        "  exit 0\nfi; done\n"
        "printf '%s' \"${SHIPMATE_REPLY_RUN_ID-}\" > run_id.txt\n"
        'for a in "$@"; do if [[ "$a" == body=@- ]]; then cat > body.txt; fi; '
        "done\n"
        f"exit {reply_exit}\n"
    )


#: The answer probed on 2026-10-14 (`return_run_details=true` answers 200 with
#: `workflow_run_id`, `run_url` and `html_url`), with an `html_url` that is not the engine-built
#: link and would break out of a markdown link if pasted.
_STARTED_ANSWER = (
    '{"workflow_run_id":38481239914,'
    '"run_url":"https://api.github.com/repos/org/repo/actions/runs/38481239914",'
    '"html_url":"https://github.com/org/repo/actions/runs/1) [x](https://evil.example"}'
)
_STARTED_URL = "https://github.com/org/repo/actions/runs/38481239914"


@pytest.mark.parametrize(
    ("verb", "environment", "header"),
    [
        ("apply", "dev-eu", "### shipmate apply dev-eu"),
        ("unlock", "dev-eu", "### shipmate unlock dev-eu"),
        ("plan", "", "### shipmate plan"),
    ],
)
@bash_only
def test_a_started_run_gets_a_reply_linking_it(tmp_path, verb, environment, header):
    """Every dispatching verb replies on the pull request with the whole notice: the header, a
    verdict linking the started run by a URL the engine builds from `workflow_run_id`, and the
    footer linking the comment-handling run. Posted with the workflow token, since the App
    token is minted `actions: write` and cannot comment. The stub's dispatch answer carries a
    gh warning on stderr, which must not reach the parsed answer.

    Mutations: parse `html_url` instead of `workflow_run_id`; capture the dispatch with `2>&1`
    (the warning corrupts the JSON, and the link is lost); pass no run id to the reply;
    skip the reply for one verb (`[ "$VERB" = plan ] ||`); drop `GH_TOKEN="$COMMENT_TOKEN"`
    from `reply`.
    """
    result, argv = _run_dispatch(
        tmp_path, verb=verb, environment=environment, gh_stub=_answering_stub(_STARTED_ANSWER)
    )
    assert result.returncode == 0, f"{verb} dispatch failed: {result.stdout}{result.stderr}"
    assert _DISPATCH_PATH in argv, f"the dispatch was never attempted: {argv!r}"
    assert "token=workflow_token\napi\n-X\nPOST\nrepos/org/repo/issues/42/comments\n" in argv, (
        f"the reply must be posted with the workflow token, gh saw: {argv!r}"
    )
    assert (tmp_path / "body.txt").read_text(encoding="utf-8") == (
        f"{header}\n\n⚪ started [the {verb} run]({_STARTED_URL})\n\n[run]({_RUN_URL})"
    )


@pytest.mark.parametrize(
    ("answer", "passed_id"),
    [
        ("", ""),
        ('{"workflow_run_id":"abc"}', "abc"),
        ('{"workflow_run_id":123456789012345678901}', "123456789012345678901"),
        # Over Linux's 128 KiB per-variable exec limit, so it must be cut before exec.
        ('{"workflow_run_id":"' + "9" * 200_000 + '"}', "9" * 21),
    ],
    ids=["204-empty", "non-numeric", "21-digits", "oversized"],
)
@bash_only
def test_a_started_run_without_a_usable_id_gets_a_reply_without_a_link(tmp_path, answer, passed_id):
    """A 204 (no body), a non-numeric id, a 21-digit id and an oversized one all yield the
    no-link notice, and the step exits 0: the run has started whatever the answer says. The
    id reaches `reply-comment` cut to 21 characters, whole otherwise.

    Mutations: remove the `|| run_id=""` parse guard (the 204 case exits non-zero under
    `set -e`); pass `"$run_id"` uncut (the oversized case hands over 200 000 characters).
    The id check itself is `_shipmate.is_run_id`, pinned in `test_reply_comment.py`.
    """
    result, _ = _run_dispatch(
        tmp_path, verb="apply", environment="dev-eu", gh_stub=_answering_stub(answer)
    )
    assert result.returncode == 0, f"dispatch failed: {result.stdout}{result.stderr}"
    assert (tmp_path / "body.txt").read_text(encoding="utf-8") == (
        "### shipmate apply dev-eu\n\n"
        "⚪ started the apply run; it is listed in this repository's Actions tab\n\n"
        f"[run]({_RUN_URL})"
    )
    assert (tmp_path / "run_id.txt").read_text(encoding="utf-8") == passed_id


@bash_only
def test_a_failed_reply_after_a_started_run_warns_and_exits_zero(tmp_path):
    """The run has started, so a reply that cannot be posted degrades to a warning, and the
    warning stays the only annotation on the passing step.

    Mutations: remove the `|| echo "::warning::..."` degrade, and the step exits non-zero;
    prefix `reply-comment`'s failed-POST exit with `::error::` -- an error on stderr.
    """
    result, _ = _run_dispatch(
        tmp_path,
        verb="apply",
        environment="dev-eu",
        gh_stub=_answering_stub(_STARTED_ANSWER, reply_exit=1),
    )
    assert result.returncode == 0, f"a failed reply failed the step: {result.stderr}"
    assert "::warning::could not post the dispatch reply; the run started" in (
        result.stdout.splitlines()
    ), f"the warning must print whole: {result.stdout}"
    assert "::error::" not in result.stderr, result.stderr


@bash_only
def test_a_failed_comment_does_not_mask_the_dispatch_failure(tmp_path):
    """The comment is cosmetic -- a repository whose workflow token cannot write pull requests
    still has to see the dispatch fail. The degrade is what keeps `$rc` the exit status under
    `set -e`, and its warning says the reply is missing.

    Mutations: drop the `|| echo "::warning::..."` and this exits 1 (the comment's status)
    instead of 22; revert it to `|| true` and the warning is missing.
    """
    # Succeeds at the dispatch, then refuses the comment: exactly a job without
    # `pull-requests: write`.
    stub = (
        "#!/bin/bash\n"
        'if [[ "$*" == *comments* ]]; then echo "HTTP 403: Forbidden" >&2; exit 1; fi\n'
        "echo \"gh: Workflow does not have 'workflow_dispatch' trigger (HTTP 422)\" >&2\n"
        "exit 22\n"
    )
    result, _ = _run_dispatch(tmp_path, verb="plan", gh_stub=stub)
    assert result.returncode == 22, (
        f"the dispatch's own status must survive a failed comment: {result.returncode}"
    )
    assert "::warning::could not post the dispatch failure reply" in result.stdout.splitlines(), (
        f"a missing failure reply must be stated: {result.stdout}"
    )


@bash_only
def test_the_plan_notice_states_neither_an_environment_nor_a_ref(tmp_path):
    """A plan has neither, so its success notice claims neither.

    Mutation: unbranch the notice, leaving the apply wording for every verb.
    """
    result, _ = _run_dispatch(tmp_path, verb="plan")
    output = result.stdout + result.stderr
    assert "::notice title=dispatch::dispatched shipmate.yml on main for PR #42" in output, (
        f"the plan notice must name the workflow, the dispatch ref and the PR: {output}"
    )
    assert "all environments" not in output and "(ref " not in output, (
        f"a plan carries no environment and no ref: {output}"
    )
