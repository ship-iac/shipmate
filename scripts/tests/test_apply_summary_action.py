"""Guard the actions/apply-summary <-> scripts/apply-comment env-var coupling.

`actions/apply-summary/action.yml`'s render step feeds `scripts/apply-comment`
entirely through `env:` (never a `${{ }}`-interpolated shell body -- see
test_actions_shellcheck.py's injection-safety check). Nothing else ties the two
sides together: a typo'd `env:` key in the action, or a renamed
`os.environ` read in the script, would silently render a wrong comment with no
local way to catch it (Actions can't be run outside GitHub). This test derives
the set of environment variables the script actually reads by parsing its
source (not a second hardcoded list, which could itself drift from either
side) and asserts it is fed byte-for-byte by the render step's `env:` block.

Also asserts the artifact-download `pattern` lines up with the
`apply-summary.<env>.<slug>` name `actions/apply-cell` uploads under.
"""

import pathlib
import re

import yaml

ENGINE = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ENGINE / "scripts" / "apply-comment"
ACTION = ENGINE / "actions" / "apply-summary" / "action.yml"
APPLY_CELL_ACTION = ENGINE / "actions" / "apply-cell" / "action.yml"

# GHA sets these three for every step of every job (composite or plain) --
# there is nothing for a caller to thread and no `env:` key to typo. The rest
# of the engine relies on the same ambient availability (e.g.
# actions/summary's gate-write step reads GITHUB_SERVER_URL/REPOSITORY/RUN_ID
# without declaring them in `env:`). Excluded from the "fed by render step"
# assertion below -- but still required to come out of `_read_names()`, so a
# rename in the script (e.g. GITHUB_RUN_ID -> RUN_ID) still surfaces as a
# clear diff instead of silently vanishing from this guard.
_AMBIENT_GHA_VARS = frozenset({"GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"})

_SUBSCRIPT_RE = re.compile(r'os\.environ\[\s*f?([\'"])([A-Za-z0-9_{}]+)\1\s*\]')
_GET_RE = re.compile(r'os\.environ\.get\(\s*f?([\'"])([A-Za-z0-9_{}]+)\1')
_RANGE_RE = re.compile(r"range\((\d+)\)")
_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")


def _read_names():
    """Every environment variable name `scripts/apply-comment` reads, derived
    from its source. A literal name is captured as-is; a name carrying an
    f-string placeholder (only `SHIPMATE_ENVLEVEL{i}_WAVES`, expanded over
    `range(4)` on the same source line) is expanded to its concrete instances
    so the derived set is genuinely comparable to a `env:` block's keys."""
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
            for i in range(int(rm.group(1))):
                names.add(_PLACEHOLDER_RE.sub(str(i), raw, count=1))
    return names


def _load_action():
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


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

    steps = _load_action()["runs"]["steps"]
    render_step = _find_step(steps, run_contains="scripts/apply-comment")
    assert render_step is not None, "no run step invokes scripts/apply-comment"

    fed = set((render_step.get("env") or {}).keys())
    required = read_names - _AMBIENT_GHA_VARS

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


def test_render_step_never_interpolates_expr_directly():
    """Belt-and-suspenders alongside test_actions_shellcheck.py: every
    author-controlled value must reach the script through env:, never a
    ${{ }} interpolated into the shell body."""
    steps = _load_action()["runs"]["steps"]
    render_step = _find_step(steps, run_contains="scripts/apply-comment")
    assert "${{" not in (render_step.get("run") or "")


def test_download_pattern_matches_apply_cell_upload_prefix():
    upload_step = _find_step(_load_action()["runs"]["steps"], uses_contains="download-artifact")
    assert upload_step is not None, "no download-artifact step found"
    pattern = (upload_step.get("with") or {}).get("pattern")

    apply_cell_steps = yaml.safe_load(APPLY_CELL_ACTION.read_text(encoding="utf-8"))["runs"][
        "steps"
    ]
    upload_apply_cell_step = _find_step(apply_cell_steps, uses_contains="upload-artifact")
    assert upload_apply_cell_step is not None, "apply-cell has no upload-artifact step"
    name = (upload_apply_cell_step.get("with") or {}).get("name", "")
    # `apply-summary.${{ inputs.env }}.${{ steps.ids.outputs.slug }}` -> the
    # literal prefix before the first templated segment.
    prefix = name.split("${{", 1)[0]
    assert prefix, "could not extract a literal prefix from apply-cell's artifact name"
    assert pattern == f"{prefix}*", (
        f"apply-summary's download pattern {pattern!r} does not match "
        f"apply-cell's upload name prefix {prefix!r}"
    )


def test_download_step_tolerates_failure():
    # For comment-ops the comment IS the feedback channel: a transient
    # artifact-API 5xx/403 must not skip the token mint and the POST,
    # leaving a developer whose apply fully succeeded with no PR comment.
    download_step = _find_step(_load_action()["runs"]["steps"], uses_contains="download-artifact")
    assert download_step.get("continue-on-error") is True


def test_download_step_failure_is_warned_not_silently_swallowed():
    steps = _load_action()["runs"]["steps"]
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
    """`pattern:`-mode download-artifact does not fail when zero artifacts
    match (verified against v7.0.0 source: the pattern branch never throws on
    an empty result, unlike the `name:`/`artifact-ids:` single-artifact
    modes) -- so no extra 'ignore missing' option is needed. This test
    guards against silently switching to `name:` (single-artifact mode,
    which DOES throw on a miss) in a future edit."""
    download_step = _find_step(_load_action()["runs"]["steps"], uses_contains="download-artifact")
    with_ = download_step.get("with") or {}
    assert "pattern" in with_
    assert "name" not in with_


WORKFLOWS = ENGINE / ".github" / "workflows"


def _steps():
    return _load_action()["runs"]["steps"]


def _index_of(step):
    return _steps().index(step)


def test_head_sha_is_a_required_input():
    inputs = _load_action()["inputs"]
    assert "head-sha" in inputs, "apply-summary needs the head SHA to read the apply checks"
    assert inputs["head-sha"]["required"] is True
    assert "${{" not in inputs["head-sha"]["description"]  # GHA evaluates descriptions


def test_token_mint_also_grants_checks_read():
    mint = _find_step(_steps(), uses_contains="create-github-app-token")
    with_ = mint.get("with") or {}
    assert with_.get("permission-checks") == "read"
    # The comment POST still needs its original grant -- one mint, both uses.
    assert with_.get("permission-pull-requests") == "write"


def test_token_mint_precedes_the_scan_and_render_steps():
    # Ordering is load-bearing: the scan step needs the token, and the render
    # step reads the file the scan step writes.
    steps = _steps()
    mint = _find_step(steps, uses_contains="create-github-app-token")
    scan = _find_step(steps, run_contains="check-runs")
    render = _find_step(steps, run_contains="scripts/apply-comment")
    assert steps.index(mint) < steps.index(scan) < steps.index(render)


def test_scan_step_validates_head_sha():
    scan = _find_step(_steps(), run_contains="check-runs")
    assert "^[0-9a-f]{40}$" in scan["run"]


def test_scan_step_uses_filter_all_like_the_gate():
    # A re-created check name keeps its historical runs; apply-gate judges the
    # newest run per name, which only works if every run is fetched.
    scan = _find_step(_steps(), run_contains="check-runs")
    assert "filter=all" in scan["run"]


def test_scan_step_degrades_to_an_empty_file_with_a_warning():
    # For comment-ops the comment IS the feedback channel: a checks-API blip
    # must cost the check-state axis only, never the comment.
    scan = _find_step(_steps(), run_contains="check-runs")
    assert ": > checks.jsonl" in scan["run"]
    assert "::warning::" in scan["run"]


def test_scan_step_never_interpolates_expr_directly():
    scan = _find_step(_steps(), run_contains="check-runs")
    assert "${{" not in scan["run"]


def test_scan_step_uses_the_app_token_not_the_workflow_token():
    # App permissions come from the installation, so no consumer's calling job
    # has to grant checks: read (a called workflow's job permissions cannot
    # exceed its caller's -- the workflow-token route would be a breaking
    # change for every consumer wrapper).
    scan = _find_step(_steps(), run_contains="check-runs")
    mint = _find_step(_steps(), uses_contains="create-github-app-token")
    assert (scan.get("env") or {}).get("GH_TOKEN") == (
        "${{ steps." + mint["id"] + ".outputs.token }}"
    )


def test_engine_callers_pass_head_sha_to_apply_summary():
    for wf in ("apply.yml", "apply-all.yml"):
        spec = yaml.safe_load((WORKFLOWS / wf).read_text(encoding="utf-8"))
        steps = spec["jobs"]["summary"]["steps"]
        step = _find_step(steps, uses_contains="actions/apply-summary")
        assert step is not None, f"{wf} has no apply-summary step"
        assert (step.get("with") or {}).get("head-sha") == "${{ inputs.ref }}", (
            f"{wf} must thread the head SHA into apply-summary"
        )
