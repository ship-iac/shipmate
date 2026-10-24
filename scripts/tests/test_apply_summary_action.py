"""Guard the actions/apply-summary <-> scripts/apply-comment env-var coupling.

`actions/apply-summary/action.yml`'s render step feeds `scripts/apply-comment` entirely through
`env:`, never a `${{ }}`-interpolated shell body -- see test_actions_shellcheck.py's
injection-safety check. Nothing else ties the two sides together: a typo'd `env:` key in the
action, or a renamed `os.environ` read in the script, would silently render a wrong comment with
no local way to catch it, because Actions cannot be run outside GitHub. This test derives the
set of environment variables the script reads by parsing its source, rather than from a second
hardcoded list that could itself drift from either side, and asserts it is fed byte-for-byte by
the render step's `env:` block.

It also asserts the artifact-download `pattern` lines up with the `apply-summary.<env>.<slug>`
name `actions/apply-cell` uploads under.

GITHUB_SERVER_URL, GITHUB_REPOSITORY and GITHUB_RUN_ID are ambient: GHA sets them for every step
of every job, composite or plain, so there is nothing for a caller to thread and no `env:` key to
typo. The rest of the engine relies on the same availability -- actions/summary's gate-write step
reads all three without declaring them in `env:`. They are excluded from the "fed by render step"
assertion, but still required to come out of `_read_names()`, so a rename in the script
(GITHUB_RUN_ID to RUN_ID, say) surfaces as a clear diff instead of vanishing from this guard.
"""

import os
import re

from _loader import (
    ACTIONS,
    ENGINE,
    action_steps,
    action_yaml,
    bash_only,
    load_script,
    run_with_gate_gh,
    step_by,
    workflow_yaml,
)

SCRIPT = ENGINE / "scripts" / "apply-comment"
_APPLY_COMMENT = load_script("apply-comment")

# Ambient in every GHA step, so excluded from the "fed by render step" assertion below.
_AMBIENT_GHA_VARS = frozenset({"GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"})
# Read with a default equal to the directory the download step writes, so the step feeds none.
_SCRIPT_DEFAULTED = frozenset({"CELLS"})

_SUBSCRIPT_RE = re.compile(r'os\.environ\[\s*f?([\'"])([A-Za-z0-9_{}]+)\1\s*\]')
_GET_RE = re.compile(r'os\.environ\.get\(\s*f?([\'"])([A-Za-z0-9_{}]+)\1')
_RANGE_RE = re.compile(r"range\((\d+|(?:[a-z]+\.)?[A-Z][A-Z0-9_]*)\)")
_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")


def _range_width(token):
    """The N in a `range(N)` bound: a literal, or a constant of `scripts/apply-comment` or of a
    module it loads (`wv.MAX_ENV_LEVELS`), read off the loaded module."""
    if token.isdigit():
        return int(token)
    width = _APPLY_COMMENT
    for attr in token.split("."):
        width = getattr(width, attr, None)
    assert isinstance(width, int), f"range({token}) is not an int constant of apply-comment"
    return width


def _read_names():
    """Every environment variable name `scripts/apply-comment` reads, derived from its source.

    A literal name is captured as-is. A name carrying an f-string placeholder -- only
    `SHIPMATE_ENVLEVEL{i}_WAVES`, expanded over the `range(...)` on the same source line -- is
    expanded to its concrete instances, so the derived set is comparable to an `env:` block's
    keys."""
    names = set()
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        raws = [m.group(2) for m in _SUBSCRIPT_RE.finditer(line)]
        raws += [m.group(2) for m in _GET_RE.finditer(line)]
        for raw in raws:
            if "{" not in raw:
                names.add(raw)
                continue
            rm = _RANGE_RE.search(line)
            assert rm, (
                f"env var pattern {raw!r} carries an f-string placeholder but "
                f"no range(N) on the same line to expand it against: {line!r}"
            )
            for i in range(_range_width(rm.group(1))):
                names.add(_PLACEHOLDER_RE.sub(str(i), raw, count=1))
    return names


def _find_step(steps, *, uses_contains=None, run_contains=None):
    for step in steps:
        if uses_contains and uses_contains in (step.get("uses") or ""):
            return step
        if run_contains and run_contains in (step.get("run") or ""):
            return step
    return None


def test_render_step_feeds_every_env_var_the_script_reads():
    read_names = _read_names()
    assert read_names, (
        "no os.environ reads found in scripts/apply-comment -- parser or script changed?"
    )

    steps = action_yaml("apply-summary")["runs"]["steps"]
    render_step = _find_step(steps, run_contains="scripts/apply-comment")
    assert render_step is not None, "no run step invokes scripts/apply-comment"

    fed = set((render_step.get("env") or {}).keys())
    required = read_names - _AMBIENT_GHA_VARS - _SCRIPT_DEFAULTED

    missing = required - fed
    assert not missing, (
        f"render step does not feed {sorted(missing)} — scripts/apply-comment "
        "reads these via os.environ but the action's env: block omits them"
    )
    extra = fed - required
    assert not extra, (
        f"render step's env: block feeds {sorted(extra)} which "
        "scripts/apply-comment never reads — stale input or a typo'd name "
        "that was meant to match one of the required vars"
    )


# Hand-written, never derived from the action; asserted whole by
# test_render_step_env_block_matches_the_expected_mapping.
_RENDER_ENV = {
    "SHIPMATE_ENVIRONMENT": "${{ inputs.environment }}",
    "SHIPMATE_ENVLEVEL0_WAVES": "${{ inputs.envlevel0-waves }}",
    "SHIPMATE_ENVLEVEL1_WAVES": "${{ inputs.envlevel1-waves }}",
    "SHIPMATE_ENVLEVEL2_WAVES": "${{ inputs.envlevel2-waves }}",
    "SHIPMATE_ENVLEVEL3_WAVES": "${{ inputs.envlevel3-waves }}",
    "SHIPMATE_RESULTS": "${{ inputs.results }}",
    "SHIPMATE_EXCLUDED_ENVS": "${{ inputs.excluded-envs }}",
    "SHIPMATE_SKIPPED_ENVS": "${{ inputs.skipped-envs }}",
    "SHIPMATE_REVIEW_HELD_ENVS": "${{ inputs.review-held-envs }}",
    "SHIPMATE_APPLIED_EXEMPT_ENVS": "${{ inputs.applied-exempt-envs }}",
    "SHIPMATE_REVIEW_NOT_REQUIRED_ENVS": "${{ inputs.review-not-required-envs }}",
    "SHIPMATE_WAITING_CELLS": "${{ inputs.waiting-cells }}",
    "SHIPMATE_DETECT_REFUSAL": "${{ inputs.detect-refusal }}",
    "SHIPMATE_ENV_ORDER": "${{ inputs.env-order }}",
    "SHIPMATE_CHECKS": "checks.jsonl",
    "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
    "SHIPMATE_HEAD_SHA": "${{ inputs.head-sha }}",
}


def test_render_step_env_block_matches_the_expected_mapping():
    """One whole-value comparison of the render step's `env:` block against a hand-written
    mapping, never one derived from the file, per the repo's assert-the-whole rule. The read-set
    guard above compares env: keys only, so it cannot see two values swapped between two keys,
    and every SHIPMATE_*_ENVS input is a JSON array of env names -- a swap type-checks, renders,
    and names the wrong environments under the wrong sentence, "held" naming what applied
    unreviewed and the reverse. A second selector for two of the keys would relocate the hole
    rather than close it.

    Mutation: drop `SHIPMATE_HEAD_SHA` from the render step, so every verdict reads `at an
    unknown commit` -- red.
    Mutation: drop `SHIPMATE_WAITING_CELLS` from the render step, so a fully waiting run
    reads `🟢 no pending applies` -- red.
    Mutation: drop `SHIPMATE_DETECT_REFUSAL` from the render step, so a refused apply's comment
    never names the refusal -- red.
    Mutation: drop `SHIPMATE_ENV_ORDER` from the render step, so the comment orders environments
    by name -- red."""
    render_step = _find_step(
        action_yaml("apply-summary")["runs"]["steps"], run_contains="scripts/apply-comment"
    )
    assert (render_step.get("env") or {}) == _RENDER_ENV


def test_render_step_never_interpolates_expr_directly():
    """A second guard alongside test_actions_shellcheck.py: every author-controlled value must
    reach the script through env:, never a ${{ }} interpolated into the shell body."""
    steps = action_yaml("apply-summary")["runs"]["steps"]
    render_step = _find_step(steps, run_contains="scripts/apply-comment")
    assert "${{" not in (render_step.get("run") or "")


def test_download_pattern_matches_apply_cell_upload_prefix():
    upload_step = _find_step(
        action_yaml("apply-summary")["runs"]["steps"], uses_contains="download-artifact"
    )
    assert upload_step is not None, "no download-artifact step found"
    pattern = (upload_step.get("with") or {}).get("pattern")

    upload_apply_cell_step = _find_step(action_steps("apply-cell"), uses_contains="upload-artifact")
    assert upload_apply_cell_step is not None, "apply-cell has no upload-artifact step"
    name = (upload_apply_cell_step.get("with") or {}).get("name", "")
    # From `apply-summary.${{ inputs.env }}.${{ steps.ids.outputs.slug }}`, take the literal
    # prefix before the first templated segment.
    prefix = name.split("${{", 1)[0]
    assert prefix, "could not extract a literal prefix from apply-cell's artifact name"
    assert pattern == f"{prefix}*", (
        f"apply-summary's download pattern {pattern!r} does not match "
        f"apply-cell's upload name prefix {prefix!r}"
    )


def test_download_step_tolerates_failure():
    # For comment-ops the comment is the feedback channel: a transient artifact-API 5xx or 403
    # must not skip the token mint and the POST, leaving a developer whose apply fully succeeded
    # with no pull-request comment.
    download_step = _find_step(
        action_yaml("apply-summary")["runs"]["steps"], uses_contains="download-artifact"
    )
    assert download_step.get("continue-on-error") is True


def test_download_step_failure_is_warned_not_silently_swallowed():
    steps = action_yaml("apply-summary")["runs"]["steps"]
    download_step = _find_step(steps, uses_contains="download-artifact")
    step_id = download_step.get("id")
    assert step_id, "download step needs an id so a later step can check its outcome"
    warn_step = next(
        (
            s
            for s in steps
            if f"steps.{step_id}.outcome" in str(s.get("if", ""))
            and "failure" in str(s.get("if", ""))
        ),
        None,
    )
    assert warn_step is not None, (
        "expected a step gated on the download step's failure outcome to warn about it"
    )


def test_download_step_does_not_fail_on_zero_matches():
    """`pattern:`-mode download-artifact does not fail when zero artifacts match, verified
    against v7.0.0 source: the pattern branch never throws on an empty result, unlike the
    `name:` and `artifact-ids:` single-artifact modes, so no extra 'ignore missing' option is
    needed. This guards against silently switching to `name:`, the single-artifact mode, which
    does throw on a miss."""
    download_step = _find_step(
        action_yaml("apply-summary")["runs"]["steps"], uses_contains="download-artifact"
    )
    with_ = download_step.get("with") or {}
    assert "pattern" in with_
    assert "name" not in with_


def _steps():
    return action_yaml("apply-summary")["runs"]["steps"]


_SCAN = "Scan apply checks (degrade to no data on failure)"


def _scan_step():
    return step_by("apply-summary", name=_SCAN)


def test_head_sha_is_a_required_input():
    inputs = action_yaml("apply-summary")["inputs"]
    assert "head-sha" in inputs, "apply-summary needs the head SHA to read the apply checks"
    assert inputs["head-sha"]["required"] is True
    assert "${{" not in inputs["head-sha"]["description"]  # GHA evaluates descriptions.


def test_declared_inputs_are_exactly_these():
    """The render step's `env:` reads `inputs.<name>`, and an undeclared name evaluates to empty
    without error. Mutation: rename `applied-exempt-envs` under `inputs:` alone -> the declared
    set differs."""
    assert set(action_yaml("apply-summary")["inputs"]) == {
        "pr-number",
        "environment",
        "envlevel0-waves",
        "envlevel1-waves",
        "envlevel2-waves",
        "envlevel3-waves",
        "excluded-envs",
        "skipped-envs",
        "review-held-envs",
        "applied-exempt-envs",
        "review-not-required-envs",
        "waiting-cells",
        "detect-refusal",
        "env-order",
        "results",
        "head-sha",
        "app-id",
        "private-key",
    }


def test_token_mint_also_grants_checks_read():
    mint = _find_step(_steps(), uses_contains="create-github-app-token")
    with_ = mint.get("with") or {}
    assert with_.get("permission-checks") == "read"
    # The comment POST still needs its own grant: one mint, both uses.
    assert with_.get("permission-pull-requests") == "write"


def test_token_mint_precedes_the_scan_render_and_post_steps():
    """Ordering is load-bearing: the scan step needs the token, the render step reads the file
    the scan step writes, and the POST step's GH_TOKEN below is the same mint. A reorder that
    pushed the mint below the POST would yield an empty token and a 401 rather than a workflow
    error."""
    steps = _steps()
    mint = _find_step(steps, uses_contains="create-github-app-token")
    scan = _scan_step()
    render = _find_step(steps, run_contains="scripts/apply-comment")
    post = _find_step(steps, run_contains="issues/$PR_NUMBER/comments")
    assert steps.index(mint) < steps.index(scan) < steps.index(render) < steps.index(post)
    assert (post.get("env") or {}).get("GH_TOKEN") == (
        "${{ steps." + mint["id"] + ".outputs.token }}"
    )


#: The scan step's check-runs projection, hand-written.
_SCAN_JQ = ".check_runs[] | {name, status, conclusion, id, app: {id: .app.id}}"
_SCAN_LISTING = (
    '{"name": "apply / a / dev", "status": "completed", "conclusion": "success", "id": 1, '
    '"app": {"id": 7}}\n'
)
_HEAD = "c" * 40


def _run_scan(tmp_path, checks_rc=0):
    """Run the shipped scan step; return (process, gh calls, the file the render step reads).

    The file is the one the render step's `SHIPMATE_CHECKS` names, so a rename on either side
    of that coupling reads an absent file here."""
    render = _find_step(_steps(), run_contains="scripts/apply-comment")
    checks_file = (render.get("env") or {}).get("SHIPMATE_CHECKS")
    assert checks_file, "render step's env: block has no SHIPMATE_CHECKS key"
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "HEAD_SHA": _HEAD,
        "GITHUB_ACTION_PATH": str(ACTIONS / "apply-summary"),
        "GITHUB_REPOSITORY": "acme/demo",
    }
    proc, calls = run_with_gate_gh(
        tmp_path,
        _scan_step()["run"],
        env,
        sha=_HEAD,
        checks=_SCAN_LISTING,
        checks_rc=checks_rc,
        checks_jq=_SCAN_JQ,
    )
    path = tmp_path / checks_file
    return proc, calls, path.read_text(encoding="utf-8") if path.exists() else None


@bash_only
def test_scan_step_writes_the_listing_to_the_file_the_render_step_reads(tmp_path):
    """Mutations: redirect to `check.jsonl` -> the render step's file is absent; drop
    `app: {id: .app.id}` from the projection -> the stub refuses the call and the argv differs."""
    proc, calls, checks = _run_scan(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert calls == [
        [
            "api",
            "--paginate",
            f"repos/acme/demo/commits/{_HEAD}/check-runs?filter=all&per_page=100",
            "--jq",
            _SCAN_JQ,
        ]
    ]
    assert checks == _SCAN_LISTING


@bash_only
def test_a_failed_scan_degrades_to_an_empty_file_with_a_warning(tmp_path):
    """For comment-ops the comment is the feedback channel, so a checks-API blip must cost the
    check-state axis only, never the comment. Mutations: delete the `::warning::` echo -> stdout
    loses the line; replace the `if ! ...; fi` with the bare listing -> `set -e` fails the
    step."""
    proc, _, checks = _run_scan(tmp_path, checks_rc=1)
    assert (proc.returncode, proc.stdout, checks) == (
        0,
        "::warning::apply-check scan failed; the comment falls back to artifact-only status\n",
        "",
    )


def test_scan_step_never_interpolates_expr_directly():
    scan = _scan_step()
    assert "${{" not in scan["run"]


def test_scan_step_uses_the_app_token_not_the_workflow_token():
    """App permissions come from the installation, so no consumer's calling job has to grant
    checks: read. A called workflow's job permissions cannot exceed its caller's, so the
    workflow-token route would be a breaking change for every consumer wrapper."""
    scan = _scan_step()
    mint = _find_step(_steps(), uses_contains="create-github-app-token")
    assert (scan.get("env") or {}).get("GH_TOKEN") == (
        "${{ steps." + mint["id"] + ".outputs.token }}"
    )


#: The whole `with:` of `apply.yml`'s apply-summary step, hand-written.
_APPLY_SUMMARY_WITH = {
    "pr-number": "${{ inputs.pr_number }}",
    "head-sha": "${{ inputs.ref }}",
    "environment": "${{ inputs.environment }}",
    "envlevel0-waves": "${{ needs.detect.outputs.envlevel0_waves }}",
    "envlevel1-waves": "${{ needs.detect.outputs.envlevel1_waves }}",
    "envlevel2-waves": "${{ needs.detect.outputs.envlevel2_waves }}",
    "envlevel3-waves": "${{ needs.detect.outputs.envlevel3_waves }}",
    "excluded-envs": "${{ needs.detect.outputs.excluded_envs }}",
    "skipped-envs": "${{ needs.detect.outputs.skipped_envs }}",
    "review-held-envs": "${{ needs.detect.outputs.review_held_envs }}",
    "applied-exempt-envs": "${{ needs.detect.outputs.applied_exempt_envs }}",
    "review-not-required-envs": "${{ needs.detect.outputs.review_not_required_envs }}",
    "waiting-cells": "${{ needs.detect.outputs.waiting_cells }}",
    "detect-refusal": "${{ needs.detect.outputs.refusal }}",
    "env-order": "${{ needs.detect.outputs.env_order }}",
    "results": "${{ join(needs.*.result, ',') }}",
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}


def test_apply_passes_apply_summary_exactly_this_with():
    """Five detect outputs are JSON arrays of env names with identical shape, so a crossed wire
    renders a plausible comment naming the wrong environments for the wrong reason, and a dropped
    `environment` renders a targeted apply as the all-environments form. The detect outputs these
    read are pinned whole in `test_detect_wiring_guard.py`.

    Mutation: drop `environment:` from the step's `with:` -- red.
    Mutation: wire `applied_exempt_envs` into `review-not-required-envs` -- red.
    Mutation: drop `waiting-cells:` from the step's `with:`, so the input defaults to `[]`
    -- red.
    Mutation: drop `detect-refusal:` from the step's `with:` -- red.
    Mutation: drop `env-order:` from the step's `with:` -- red."""
    steps = workflow_yaml("apply.yml")["jobs"]["summary"]["steps"]
    step = _find_step(steps, uses_contains="actions/apply-summary")
    assert step["with"] == _APPLY_SUMMARY_WITH


def test_the_env_order_input_is_optional_and_empty_by_default():
    """`deploy.yml` posts no apply comment, and an empty value orders environments by name.
    Mutation: set the input `required: true` -- red."""
    declared = action_yaml("apply-summary")["inputs"]["env-order"]
    assert {k: v for k, v in declared.items() if k != "description"} == {
        "required": False,
        "default": "",
    }


def test_the_detect_refusal_input_is_optional_and_empty_by_default():
    """`deploy.yml` posts no apply comment and passes no refusal, and an empty value renders the
    comment unchanged. Mutation: set the input `required: true` -- red."""
    assert action_yaml("apply-summary")["inputs"]["detect-refusal"] == {
        "description": (
            "apply-detect's `refusal` output: the first error of the `::error::` message that "
            "failed detect (a refusal or a failed tool or API call) on one line, empty when "
            "detect raised none. Non-empty, the comment shows it in a "
            "code fence under the verdict line; empty leaves the comment unchanged."
        ),
        "required": False,
        "default": "",
    }
