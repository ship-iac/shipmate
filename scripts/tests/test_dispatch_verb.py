"""Tests for actions/dispatch's `verb` input: every verb dispatches one consumer file, and the
verb rides in the body — selecting the job that runs — alongside one body shape per verb.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from _loader import (
    ACTIONS,
    action_yaml,
    bash_only,
    fail_script,
    load_script,
    run_step,
    step_by,
    write_python3_shim,
)

DISPATCH_ACTION = "dispatch"

#: Every verb reaches the same consumer file; the verb rides in the body and selects a job
#: there. Hand-written; never derived from the action, which is the file under test.
CONSUMER_WORKFLOW = "shipmate.yml"

DISPATCH_BODY = load_script("dispatch-body")


def _extract_dispatch_run_block():
    """The full run: block of the dispatch step, exactly as written."""
    run = step_by(DISPATCH_ACTION, name="workflow_dispatch apply").get("run", "")
    assert run.strip(), "dispatch step has no run block"
    return run


def _build_dispatch_body(verb, environment="", comment="77"):
    """The whole parsed dispatch body for `verb`, sent for comment `comment`."""
    env = {
        "DISPATCH_REF": "main",
        "REF": "abc123def456",
        "PR_NUMBER": "42",
        "ENVIRONMENT": environment,
        "COMMENT_ID": comment,
        "VERB": verb,
    }
    with patch.dict(os.environ, env):
        return DISPATCH_BODY.body()


# Whole-body comparison, not key-by-key membership: "carries exactly these inputs" and
# "carries no `mode` key" are the same assertion once the comparison is whole, and two
# selectors for one property disagree eventually.


def test_plan_body_is_the_dispatch_ref_the_verb_the_pr_number_and_the_comment():
    """A commented plan body is exactly {ref: dispatch ref, inputs: {verb, pr_number,
    comment_id}}. `verb` is what selects the `plan` job in `shipmate.yml`; without it every
    job's `if:` is false and the dispatched run completes having done nothing. The top-level ref
    is the dispatch ref rather than the head SHA, because a plan states no head. `comment_id` is
    what the run claims, so a second run of one comment does no work.

    Mutations: drop the `verb` key; add `ref` or `environment` to the plan inputs; swap the
    top-level ref for the head SHA; drop `return_run_details`, which every body carries so the
    API answers with the started run's id; drop the `comment_id` key.
    """
    body = _build_dispatch_body("plan", environment="dev-eu")
    assert body == {
        "ref": "main",
        "inputs": {"verb": "plan", "pr_number": "42", "comment_id": "77"},
        "return_run_details": True,
    }, f"plan body must carry the verb, the PR number and the comment, on the dispatch ref: {body}"


def test_the_replan_body_carries_no_comment_id():
    """The summary's comment-less replan sends no `COMMENT_ID`, and its run claims nothing.
    Mutations: send `comment_id: ''` when it is empty; refuse an empty one on a plan."""
    assert _build_dispatch_body("plan", comment="") == {
        "ref": "main",
        "inputs": {"verb": "plan", "pr_number": "42"},
        "return_run_details": True,
    }


def test_the_auto_body_carries_no_comment_id():
    """`auto` is the autoplan's own dispatch: no comment, no claim. Mutation: send an empty
    `comment_id` key."""
    assert _build_dispatch_body("auto", comment="") == {
        "ref": "main",
        "inputs": {"verb": "auto", "ref": "abc123def456", "pr_number": "42"},
        "return_run_details": True,
    }


@pytest.mark.parametrize(
    ("verb", "comment", "message"),
    [
        ("apply", "", "::error::a commented apply needs COMMENT_ID, the command comment's id"),
        ("unlock", "", "::error::a commented unlock needs COMMENT_ID, the command comment's id"),
        ("plan", "0", "::error::verb plan cannot carry COMMENT_ID '0'"),
        ("apply", "12a", "::error::verb apply cannot carry COMMENT_ID '12a'"),
        ("unlock", "1" * 21, f"::error::verb unlock cannot carry COMMENT_ID '{'1' * 21}'"),
        ("auto", "77", "::error::verb auto cannot carry COMMENT_ID '77'"),
    ],
)
def test_a_missing_or_malformed_comment_id_is_a_usage_error(verb, comment, message):
    """An apply or unlock without its comment would dispatch a run `apply.yml`'s `guard` refuses
    or that marks nothing; a malformed id is bound into the claim's API path; `auto` has no
    comment. Mutations: drop the `elif` for apply and unlock; drop the `_COMMENT_ID` check; drop
    `verb == "auto"`."""
    with pytest.raises(SystemExit) as e:
        _build_dispatch_body(verb, comment=comment)
    assert e.value.code == message


def test_apply_body_omits_the_environment_when_it_is_empty():
    """A bare `shipmate apply` sends {verb, ref, pr_number, comment_id} and no environment key,
    which `apply.yml` reads as `inputs.environment == ''` and runs as the bare form.

    Mutations: drop the `if os.environ.get("ENVIRONMENT")` guard -- the body carries an empty
    `environment`, red; drop `return_run_details`; drop the `comment_id` key.
    """
    assert _build_dispatch_body("apply") == {
        "ref": "main",
        "inputs": {"verb": "apply", "ref": "abc123def456", "pr_number": "42", "comment_id": "77"},
        "return_run_details": True,
    }


def test_apply_body_carries_the_environment_when_it_is_set():
    """A targeted `shipmate apply dev-eu` sends {verb, ref, pr_number, environment, comment_id}.

    Mutations: drop the environment key entirely; drop `return_run_details`.
    """
    assert _build_dispatch_body("apply", environment="dev-eu") == {
        "ref": "main",
        "inputs": {
            "verb": "apply",
            "ref": "abc123def456",
            "pr_number": "42",
            "environment": "dev-eu",
            "comment_id": "77",
        },
        "return_run_details": True,
    }


def test_unlock_body_is_the_verb_the_ref_the_environment_and_the_comment():
    """An unlock body is exactly {verb, ref, environment, comment_id} — no pr_number. Releasing a
    stranded lock applies no plan and comments on no pull request, so nothing on that path reads
    one, and `pr_number` is `required: false` on `shipmate.yml` precisely so this body is legal.
    `comment_id` is where the unlock run marks the command started.

    Mutations: add `pr_number` to the unlock branch; drop `verb`, and the dispatched run
    matches no job's `if:` and unlocks nothing while reporting success; drop
    `return_run_details`; drop the `comment_id` key.
    """
    assert _build_dispatch_body("unlock", environment="dev-eu") == {
        "ref": "main",
        "inputs": {
            "verb": "unlock",
            "ref": "abc123def456",
            "environment": "dev-eu",
            "comment_id": "77",
        },
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
    failing_script=None,
    stubs=None,
    cwd=None,
    comment_id="77",
):
    """Run the shipped step with a gh stub that records its argv; returns the
    CompletedProcess and the recorded argv text. `failing_script` is `(name, exit_code,
    stderr_text)`: for the engine script `name`, `python3` prints `stderr_text` to stderr and
    exits `exit_code`, and it runs every other one. `stubs` maps further command
    names to stub text on the same PATH directory. The step runs in `cwd`, or `tmp_path`."""
    path_dir, _ = _create_stub_commands(tmp_path, gh_stub)
    for name, text in (stubs or {}).items():
        (Path(path_dir) / name).write_text(text, newline="\n")
        (Path(path_dir) / name).chmod(0o755)
    if failing_script:
        fail_script(Path(path_dir), *failing_script)

    env = os.environ.copy()
    env["PATH"] = f"{path_dir}:{env.get('PATH', '')}"
    env["GH_TOKEN"] = "test_token"  # noqa: S105
    env["DISPATCH_REF"] = "main"
    env["ENVIRONMENT"] = environment
    env["VERB"] = verb
    env["REF"] = "abc123"
    env["PR_NUMBER"] = "42"
    env["COMMENT_ID"] = comment_id
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
    return result, (argv_file.read_text(encoding="utf-8") if argv_file.exists() else "")


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
    is the shape this path exists for, so the commenter is told rather than left with an eyes
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


@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
@bash_only
def test_an_empty_comment_id_dispatches_nothing_and_says_so_on_the_pull_request(tmp_path, verb):
    """Every verb this action dispatches comes from a comment, and `dispatch-body` reads an
    empty id on a plan as the comment-less replan, whose run claims nothing: a caller that drops
    the `comment-id` line would dispatch plans that a duplicate can run twice. The `plan` row is
    the one only this check refuses. Mutations: delete the check (the `plan` row dispatches);
    drop its `say_no_run_started`."""
    result, argv = _run_dispatch(tmp_path, verb=verb, gh_stub=RECORDING_GH_STUB, comment_id="")
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert _DISPATCH_PATH not in argv, f"an empty comment id must dispatch nothing: {argv!r}"
    assert (
        "::error::The engine's comment-ops.yml passed no comment-id to shipmate/actions/dispatch, "
        "so the dispatched run could not claim its comment; nothing was dispatched. This is an "
        "engine defect: report it.\n"
    ) in result.stdout
    assert "/issues/42/comments" in argv and _RUN_URL in argv, argv


@pytest.mark.parametrize("verb", ["bogus", "auto"])
@bash_only
def test_an_unknown_verb_dispatches_nothing_and_says_so_on_the_pull_request(tmp_path, verb):
    """A verb outside {plan, apply, unlock} reaches no workflow, and says so. `auto` is a literal
    row: only the plan summary may dispatch it, since the `auto` run's actor check trusts every
    `auto` dispatch to come from there, and comment-ops dispatches through this action alone.

    Mutations: add `auto` to the `case` list (the `auto` row reddens); drop that arm's
    `say_no_run_started`.
    """
    result, argv = _run_dispatch(tmp_path, verb=verb, gh_stub=RECORDING_GH_STUB)
    output = result.stdout + result.stderr
    assert result.returncode != 0, "an unknown verb must be refused"
    assert _DISPATCH_PATH not in argv, f"an unknown verb must dispatch nothing: {argv!r}"
    assert f"::error::verb must be plan, apply or unlock (got: {verb})" in output, (
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
    seven `if:` expressions `shipmate doctor`'s `shipmate.yml` probe compares whole.

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
        "comment-id",
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
    the `shipmate-engine` environment's branch policy admits only the default branch. `REPO` is
    absent: `dispatch-body` reads the runner's `GITHUB_REPOSITORY`. `SHIPMATE_APP_SLUG` comes
    from the token step, so the retry trusts only a run this App dispatched.

    Mutation: rename VERB in the env: block, re-add WORKFLOW or REPO, set
    `DISPATCH_REF: ${{ github.head_ref }}`, or drop `SHIPMATE_APP_SLUG`.
    """
    assert step_by(DISPATCH_ACTION, name="workflow_dispatch apply").get("env") == {
        "GH_TOKEN": "${{ steps.token.outputs.token }}",
        "SHIPMATE_APP_SLUG": "${{ steps.token.outputs.app-slug }}",
        "DISPATCH_REF": "${{ github.event.repository.default_branch }}",
        "ENVIRONMENT": "${{ inputs.environment }}",
        "VERB": "${{ inputs.verb }}",
        "REF": "${{ inputs.ref }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        "COMMENT_ID": "${{ inputs.comment-id }}",
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
    `$errfile`; make `post` write the body to `req.json` and pass `--input req.json`.
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


#: Fails the dispatch as gh does on a 4xx: the API's JSON error body on stdout, gh's own
#: message on stderr. Not transient, so `post` makes one attempt. The reply call succeeds.
_GH_ERROR_STUB = _on_dispatch_call(
    """  echo '{"message":"Not Found","status":"404"}'\n"""
    "  echo 'gh: Not Found (HTTP 404)' >&2\n"
    "  exit 1"
)


@bash_only
def test_a_failed_dispatch_prints_ghs_error_to_the_log(tmp_path):
    """`post` prints gh's message to stderr on a failure, and the log is where the failure reply
    sends the reader, so that line must print. The failure exit still removes the file holding
    it and leaves the job workspace (the step's working directory) empty.

    Mutations: delete the `$err` `printf` line; drop the `trap` that removes `$errfile`.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result, _ = _run_dispatch(tmp_path, verb="apply", gh_stub=_GH_ERROR_STUB, cwd=workspace)
    assert result.returncode == 1, f"a failed dispatch must fail the step: {result.returncode}"
    assert "gh: Not Found (HTTP 404)" in result.stdout.splitlines(), (
        f"gh's message must reach the log: {result.stdout}"
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
    """`dispatch-body` exits 1 here, as it does when the body cannot be built or the first
    attempt fails with a failure that is not transient. The step exits 1, annotates the failure
    itself (`post` prints it unannotated), and posts the failure reply carrying the run link.

    Mutations: drop the failure branch's `::error::` echo; drop its `say_no_run_started`.
    """
    result, argv = _run_dispatch(
        tmp_path, verb="apply", failing_script=("dispatch-body", 1, "KeyError: 'REF'")
    )
    assert result.returncode == 1, f"a failed body build must fail the step: {result.stdout}"
    assert "::error::dispatching shipmate.yml failed, so no run started" in (
        result.stdout.splitlines()
    ), f"the failure must be annotated: {result.stdout}"
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
                '{"ref": "main", "inputs": {"verb": "plan", "pr_number": "42", '
                '"comment_id": "77"}, "return_run_details": true}'
            ),
        ),
        (
            "apply",
            "",
            (
                '{"ref": "main", "inputs": {"verb": "apply", "ref": "abc123", "pr_number": "42", '
                '"comment_id": "77"}, "return_run_details": true}'
            ),
        ),
        (
            "apply",
            "dev-eu",
            (
                '{"ref": "main", "inputs": {"verb": "apply", "ref": "abc123", "pr_number": "42", '
                '"environment": "dev-eu", "comment_id": "77"}, "return_run_details": true}'
            ),
        ),
        (
            "unlock",
            "dev-eu",
            (
                '{"ref": "main", "inputs": {"verb": "unlock", "ref": "abc123", '
                '"environment": "dev-eu", "comment_id": "77"}, "return_run_details": true}'
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
    body for that verb byte for byte.

    Mutations: drop `--input -` from dispatch-body's argv; send `data[:5]` instead of `data`; run
    `dispatch-body` with a different `ENVIRONMENT` when the verb is `unlock`.
    """
    result, _ = _run_dispatch(
        tmp_path, verb=verb, environment=environment, gh_stub=_STDIN_SAVING_STUB
    )
    assert result.returncode == 0, f"dispatch failed: {result.stdout}{result.stderr}"
    sent = tmp_path / "sent.txt"
    assert sent.exists(), "the dispatch call did not read its body from stdin via --input -"
    assert sent.read_text(encoding="utf-8") == expected


def _silent_answer_stub(out):
    """A gh stub answering the dispatch call with `out` on stdout, nothing on stderr, and exit
    0; every other call succeeds and prints nothing."""
    return _on_dispatch_call(f"  printf '%s' '{out}'\n  exit 0")


@pytest.mark.parametrize(
    "out", ["", '{"workflow_run_id":38481239914}'], ids=["204-empty", "200-answer"]
)
@bash_only
def test_the_log_prints_no_empty_line_for_a_missing_answer(tmp_path, out):
    """An empty answer (a 204), and the empty `post` stderr of every success, print no line.

    Mutation: print both with one `printf '%s\\n' "$out" "$err"`.
    """
    result, _ = _run_dispatch(tmp_path, verb="apply", gh_stub=_silent_answer_stub(out))
    assert result.returncode == 0, f"unexpected exit: {result.stdout}{result.stderr}"
    assert "" not in result.stdout.splitlines(), f"the log has an empty line: {result.stdout!r}"


# Only a failure that matches the skew shape gets the skew explanation: a 403 or a rate limit
# explained as skew sends the operator to edit workflows that are fine. `dispatch-body` classifies
# (test_dispatch_post.py pins each shape); these pin that its remedy reaches the log and the reply.

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
#: The whole remedies, hand-written. `_LAYOUT_SKEW` stays a substring for the tests that assert
#: the layout remedy is ABSENT.
_LAYOUT_REMEDY = (
    "This repository has no .github/workflows/shipmate.yml, or that file declares no "
    "workflow_dispatch trigger. docs/getting-started.md publishes it; re-pinning cannot create it."
)
_INPUTS_REMEDY = (
    "This repository's .github/workflows/shipmate.yml does not declare every input this "
    "engine's dispatch sends, so it is older than the engine pin. Write the published file over "
    "it with `python <engine>/scripts/repin <consumer> <tag> --write-fence`."
)


@pytest.mark.parametrize(
    ("stub", "remedy"),
    [
        (_NOT_FOUND_STUB, _LAYOUT_REMEDY),
        (_NO_TRIGGER_STUB, _LAYOUT_REMEDY),
        (_UNEXPECTED_INPUTS_STUB, _INPUTS_REMEDY),
    ],
)
@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
@bash_only
def test_a_dispatch_to_an_old_or_missing_shipmate_yml_logs_its_remedy(tmp_path, verb, stub, remedy):
    """The remedy prints beside gh's answer for every verb. Pinned against stdout, because the
    reply's own gh call leaks the same text on stderr.

    Mutations: delete the `$err` `printf` line -> neither the answer nor the remedy prints;
    print `$out` on a failure too -> the bare remedy prints a second time."""
    result, _ = _run_dispatch(tmp_path, verb=verb, environment="dev-eu", gh_stub=stub)
    lines = result.stdout.splitlines()
    assert result.returncode != 0, f"a {verb} failure must exit non-zero: {result.stdout}"
    assert f"::error::{remedy}" in lines, f"the remedy must print whole: {result.stdout}"
    assert remedy not in lines, f"the remedy must print once, annotated: {result.stdout}"


#: Fails the dispatch with gh's answer for a fence without the sent inputs; saves the reply body.
_INPUTS_BODY_STUB = (
    "#!/bin/bash\n"
    'for a in "$@"; do if [[ "$a" == repos/*/actions/workflows/*/dispatches ]]; then\n'
    "  echo 'gh: Unexpected inputs provided: [\"comment_id\"] (HTTP 422)' >&2; exit 22\n"
    "fi; done\n"
    'for a in "$@"; do if [[ "$a" == body=@- ]]; then cat > body.txt; fi; done\n'
)


@bash_only
def test_a_dispatch_to_a_fence_without_its_inputs_replies_with_repin(tmp_path):
    """A fence not re-pinned after a pin bump 422s; the reply names the command that fixes it.

    Mutation: call `say_no_run_started` without `"$out"` -> the reply points at the log."""
    result, _ = _run_dispatch(
        tmp_path, verb="apply", environment="dev-eu", gh_stub=_INPUTS_BODY_STUB
    )
    assert result.returncode == 1, f"dispatch-body's exit status must survive: {result.stdout}"
    assert (tmp_path / "body.txt").read_text(encoding="utf-8") == (
        "### shipmate apply dev-eu\n\n"
        "🔴 failed: the command was authorized, but dispatching its workflow failed, so no run "
        f"started. {_INPUTS_REMEDY}\n\n"
        f"[comment run]({_RUN_URL}). Comment `shipmate help` for the available commands."
    )


@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
@bash_only
def test_a_403_prints_no_skew_message(tmp_path, verb):
    """A 403 is not version skew, on any verb: the raw output prints, the message does not.

    Mutation: make `dispatch-body`'s `skew_hint` return the layout remedy for any answer.
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

    Mutation: add `HTTP 422` to `dispatch-body`'s `_LAYOUT_ANSWERS` and this reddens.
    """
    stub = (
        "#!/bin/bash\n"
        "echo \"gh: Required input 'plan_run_id' not provided (HTTP 422)\" >&2\n"
        "exit 22\n"
    )
    result, _ = _run_dispatch(tmp_path, verb="unlock", environment="dev-eu", gh_stub=stub)
    output = result.stdout + result.stderr
    assert result.returncode == 1, (
        f"dispatch-body's exit status must survive, got {result.returncode}"
    )
    assert "Required input 'plan_run_id' not provided" in output, (
        f"the API's own message must still print: {output}"
    )
    assert _LAYOUT_SKEW not in output, (
        f"a 422 about plan_run_id is not a missing consumer file: {output}"
    )


#: Fails with a 403, which no skew remedy explains, and records each call's token ahead of its
#: argv, so what the failure branch does after the refusal is observable rather than inferred.
_RECORDING_FAILURE_STUB = (
    "#!/bin/bash\nprintf 'token=%s\\n' \"$GH_TOKEN\" >> argv.txt\n"
    "printf '%s\\n' \"$@\" >> argv.txt\n"
    '[[ " $* " == *" body=@- "* ]] && { cat; echo; } >> argv.txt\n'
    "echo 'gh: Forbidden (HTTP 403)' >&2\nexit 1\n"
)
#: The run link, hand-written from the three runner variables the step reads.
_RUN_URL = "https://github.com/org/repo/actions/runs/7777"
#: The verdict line of the reply to a dispatch that cannot have started a run.
_NO_RUN_STARTED = (
    "🔴 failed: the command was authorized, but dispatching its workflow failed, so no run "
    "started. The error is in this run's log."
)


@pytest.mark.parametrize("verb", ["plan", "apply", "unlock"])
@bash_only
def test_a_failed_dispatch_says_so_on_the_pull_request(tmp_path, verb):
    """Every failure here lands on a run whose checks attach to the dispatch ref, so the pull
    request that asked for the verb shows no check, no comment and no gate change -- and the
    commenter's last signal was comment-ops' eyes. One comment, on the pull request, carrying
    the run link.

    A 4xx on the first attempt cannot have started a run, and the reply says so.

    Mutations: delete the comment call; scope it to one verb; swap `$COMMENT_TOKEN` back to
    `$GH_TOKEN`, which is minted `actions: write` and cannot comment; map every nonzero exit
    of `post` to the may-have-started failure text.
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
    assert f"\n{_NO_RUN_STARTED}\n" in argv, f"the reply must say no run started: {argv!r}"
    assert "Forbidden (HTTP 403)" not in argv, (
        f"the API's answer is unbounded and must not be pasted into a comment: {argv!r}"
    )


#: Fails every call and saves the comment body it is handed.
_BODY_FAILURE_STUB = (
    "#!/bin/bash\n"
    'for a in "$@"; do if [[ "$a" == body=@- ]]; then cat > body.txt; fi; '
    "done\n"
    "echo 'gh: Forbidden (HTTP 403)' >&2\nexit 1\n"
)


@bash_only
def test_a_failed_dispatch_posts_the_shared_failure_reply(tmp_path):
    """The whole comment: header naming the verb and environment, a `failed` verdict, and the
    footer that carries the run link.

    Mutations: post the old `:x: shipmate:` sentence without reply-comment; set the outcome to
    `refused`; map every nonzero exit of `post` to the may-have-started failure text.
    """
    result, _ = _run_dispatch(
        tmp_path, verb="apply", environment="dev-eu", gh_stub=_BODY_FAILURE_STUB
    )
    assert result.returncode == 1, f"dispatch-body's exit status must survive: {result.returncode}"
    assert (tmp_path / "body.txt").read_text(encoding="utf-8") == (
        f"### shipmate apply dev-eu\n\n{_NO_RUN_STARTED}\n\n"
        f"[comment run]({_RUN_URL}). Comment `shipmate help` for the available commands."
    )


#: Succeeds on every call and saves the comment body it is handed.
_BODY_SAVING_STUB = (
    '#!/bin/bash\nfor a in "$@"; do if [[ "$a" == body=@- ]]; then cat > body.txt; fi; done\n'
)


@bash_only
def test_a_may_have_started_dispatch_replies_and_exits_zero(tmp_path):
    """`dispatch-body` exits 3 when the App's run of this command is listed after an attempt
    that answered with an error. The run started, so the step warns, posts the whole notice
    reply and passes.

    Mutation: map exit 3 to `say_no_run_started`.
    """
    result, argv = _run_dispatch(
        tmp_path,
        verb="apply",
        environment="dev-eu",
        gh_stub=_BODY_SAVING_STUB,
        failing_script=("dispatch-body", 3, ""),
    )
    assert result.returncode == 0, f"a run started: {result.stdout}{result.stderr}"
    assert (
        "::warning::the dispatch answered with an error, but this command's run is listed; not "
        "retried"
    ) in result.stdout.splitlines(), f"the warning must print whole: {result.stdout}"
    assert (tmp_path / "body.txt").read_text(encoding="utf-8") == (
        "### shipmate apply dev-eu\n\n"
        "⚪ the dispatch answered with an error, but this command's run is listed in the Actions "
        "tab, so it was not dispatched again.\n\n"
        f"[comment run]({_RUN_URL})"
    )


@bash_only
def test_a_may_have_started_reply_that_cannot_be_posted_only_warns(tmp_path):
    """A run may have started, so a notice reply whose every gh call fails warns and the step
    still passes.

    Mutation: drop the exit-3 branch's `|| echo` degrade.
    """
    result, _ = _run_dispatch(
        tmp_path,
        verb="apply",
        environment="dev-eu",
        gh_stub="#!/bin/bash\nexit 1\n",
        failing_script=("dispatch-body", 3, ""),
    )
    assert result.returncode == 0, f"a failed reply must not fail: {result.stdout}{result.stderr}"
    assert "::warning::could not post the dispatch reply" in result.stdout.splitlines(), (
        f"the failed reply must warn: {result.stdout}"
    )


@bash_only
def test_an_undetermined_dispatch_says_a_run_may_have_started(tmp_path):
    """`dispatch-body` exits 4 when its attempts ended on server or network errors, or
    the listing that would have told could not be read: a run may have started, so the failure
    reply sends the commenter to the Actions tab, and dispatch-body's message prints to the log.

    Mutations: map exit 4 to `say_no_run_started`; delete its `::error::` echo. `dispatch-body`
    prints no remedy on exit 4 (test_dispatch_post.py).
    """
    result, _ = _run_dispatch(
        tmp_path,
        verb="apply",
        environment="dev-eu",
        gh_stub=_BODY_SAVING_STUB,
        failing_script=(
            "dispatch-body",
            4,
            "gh: Bad Gateway (HTTP 502)\ngh: Unexpected inputs provided (HTTP 422)",
        ),
    )
    assert result.returncode == 1, f"an undetermined dispatch must fail: {result.returncode}"
    assert (
        "::error::dispatching shipmate.yml failed with a server or network error, so a run may "
        "have started"
    ) in result.stdout.splitlines(), f"the failure must be annotated whole: {result.stdout}"
    assert "gh: Bad Gateway (HTTP 502)" in result.stdout.splitlines(), (
        f"dispatch-body's message must reach the log: {result.stdout}"
    )
    assert (tmp_path / "body.txt").read_text(encoding="utf-8") == (
        "### shipmate apply dev-eu\n\n"
        "🔴 failed: the command was authorized, but dispatching its workflow failed with a "
        "server or network error, so a run may have started. Check the Actions tab before "
        "commenting again; the error is in this run's log.\n\n"
        f"[comment run]({_RUN_URL}). Comment `shipmate help` for the available commands."
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


@bash_only
def test_a_started_run_gets_a_reply_linking_it(tmp_path):
    """A started unlock run replies on the pull request with the whole notice: the header, a
    verdict linking the started run by a URL the engine builds from `workflow_run_id`, and the
    footer linking the comment-handling run. Posted with the workflow token, since the App
    token is minted `actions: write` and cannot comment.

    Mutations: parse `html_url` instead of `workflow_run_id`; pass no run id to the reply;
    drop `GH_TOKEN="$COMMENT_TOKEN"` from `reply`.
    """
    result, argv = _run_dispatch(
        tmp_path, verb="unlock", environment="dev-eu", gh_stub=_answering_stub(_STARTED_ANSWER)
    )
    assert result.returncode == 0, f"unlock dispatch failed: {result.stdout}{result.stderr}"
    assert _DISPATCH_PATH in argv, f"the dispatch was never attempted: {argv!r}"
    assert "token=workflow_token\napi\n-X\nPOST\nrepos/org/repo/issues/42/comments\n" in argv, (
        f"the reply must be posted with the workflow token, gh saw: {argv!r}"
    )
    assert (tmp_path / "body.txt").read_text(encoding="utf-8") == (
        "### shipmate unlock dev-eu\n\n"
        f"⚪ started [the unlock run]({_STARTED_URL})\n\n[comment run]({_RUN_URL})"
    )


@pytest.mark.parametrize(("verb", "environment"), [("plan", ""), ("apply", "dev-eu")])
@bash_only
def test_a_started_plan_or_apply_run_posts_no_reply(tmp_path, verb, environment):
    """A plan or apply run answers on the pull request itself, with its result comment or its
    outcome reply, so a successful dispatch of either verb makes no comment call and exits 0.

    Mutation: drop the `[ "$VERB" = unlock ]` condition around the started reply.
    """
    result, argv = _run_dispatch(
        tmp_path, verb=verb, environment=environment, gh_stub=_answering_stub(_STARTED_ANSWER)
    )
    assert result.returncode == 0, f"{verb} dispatch failed: {result.stdout}{result.stderr}"
    assert _DISPATCH_PATH in argv, f"the dispatch was never attempted: {argv!r}"
    assert "issues/42/comments" not in argv, f"a {verb} dispatch must not reply: {argv!r}"


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
        tmp_path, verb="unlock", environment="dev-eu", gh_stub=_answering_stub(answer)
    )
    assert result.returncode == 0, f"dispatch failed: {result.stdout}{result.stderr}"
    assert (tmp_path / "body.txt").read_text(encoding="utf-8") == (
        "### shipmate unlock dev-eu\n\n"
        "⚪ started the unlock run; it is listed in this repository's Actions tab\n\n"
        f"[comment run]({_RUN_URL})"
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
        verb="unlock",
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
    # `post` exits 22 here, a status distinct from the comment's 1; the comment is refused,
    # exactly as in a job without `pull-requests: write`.
    stub = '#!/bin/bash\necho "HTTP 403: Forbidden" >&2\nexit 1\n'
    result, _ = _run_dispatch(
        tmp_path,
        verb="plan",
        gh_stub=stub,
        failing_script=("dispatch-body", 22, "gh: Validation Failed (HTTP 422)"),
    )
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
