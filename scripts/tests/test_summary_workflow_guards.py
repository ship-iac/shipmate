"""The trusted post-plan workflow must not be fireable by branch-authored runs.

Every assertion below is on a *parsed* value (`yaml.safe_load`, then a whole
`if:`/`environment:`/`concurrency:` field, or a whole jq program string via
exact-string comparison after whitespace normalisation) rather than a
substring of the raw file text. The substring form this replaces was proven
vacuous: four simultaneous mutations of `summary.yml` -- all three trust
guards inverted, `environment: shipmate-engine` commented out, and the
draft-skip deleted -- left the old suite's `1 failed, 762 passed` unchanged
from baseline. A YAML comment or an inverted operator can contain the same
substring as the real guard; it cannot produce the same *parsed value*, which
is what every test here actually compares against.
"""

import re

import yaml
from _loader import WORKFLOWS

WF = WORKFLOWS / "summary.yml"


def _spec():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _job():
    return _spec()["jobs"]["summary"]


def _step(job, step_id):
    matches = [s for s in job["steps"] if s.get("id") == step_id]
    assert len(matches) == 1, f"expected exactly one step with id {step_id!r}, got {len(matches)}"
    return matches[0]


def _normalize(text):
    return " ".join(text.split())


def _logical_command(body, marker):
    """Join a backslash-continued shell command starting at the line
    containing `marker` into one string.

    A flag on a continuation line (as `--jq` was, in the exact bug this
    guards against) is invisible to a single-physical-line check -- joining
    first is what makes "is --jq present anywhere in this command" a claim
    the test can actually back up. Starting at `marker` (rather than scanning
    the whole step body) keeps an unrelated comment mentioning the same
    flag names out of the match.
    """
    lines = body.splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    parts = [lines[start]]
    while parts[-1].rstrip().endswith("\\"):
        start += 1
        parts.append(lines[start])
    return " ".join(p.strip().rstrip("\\").strip() for p in parts)


def _if_clauses(job):
    # The three-clause `if:` is a YAML folded scalar (`>-`); PyYAML folds it
    # to a single space-joined string, so ` && ` is the real separator between
    # the three guards -- splitting on it and comparing each clause exactly
    # (not just checking a name appears somewhere) is what catches an
    # inversion (`==`->`!=`) or a dropped clause, both of which change the
    # parsed string outright.
    if_expr = job.get("if")
    assert if_expr, "summary job carries no if: guard at all"
    return [c.strip() for c in if_expr.split(" && ")]


def test_guards_on_the_triggering_workflow_file_path():
    # `workflow_run.workflows:` matches by workflow NAME, so a branch file
    # named "shipmate · plan" with `on: push` would otherwise fire this
    # workflow -- matching the file path instead pins it to the real file.
    assert (
        _if_clauses(_job())[0] == "github.event.workflow_run.path == '.github/workflows/plan.yml'"
    )


def test_guards_on_the_triggering_event_being_pull_request():
    # Excludes a push-triggered run of that same file.
    assert _if_clauses(_job())[1] == "github.event.workflow_run.event == 'pull_request'"


def test_guards_on_the_head_repository_being_this_repository():
    # Without this a fork's artifacts would author checks and a gate under the
    # App identity — the fork safe-harbour inverts once this job holds the key.
    assert (
        _if_clauses(_job())[2]
        == "github.event.workflow_run.head_repository.full_name == github.repository"
    )


def test_serialises_gate_writes_per_head_sha_at_job_level():
    # Workflow-level `concurrency:` is ignored in a called workflow, so
    # pinning it there would assert a string that does nothing at runtime --
    # both the job-level group AND the absence of a (dead) top-level one must
    # hold, and the group must actually key on the per-SHA expression, not a
    # literal string that happens to contain "head_sha".
    job = _job()
    assert _spec().get("concurrency") is None
    concurrency = job.get("concurrency") or {}
    assert concurrency.get("group") == "shipmate-summary-${{ github.event.workflow_run.head_sha }}"
    assert concurrency.get("cancel-in-progress") is True


def _jq_program(run, marker):
    """The `jq -r --arg sha "$SHA" '<program>'` of the candidate command that
    starts at `marker`, with backslash continuations joined first."""
    cmd = _logical_command(run, marker)
    m = re.search(r"""jq -r --arg sha "\$SHA" '(.*?)'""", cmd)
    assert m, f"no candidate jq program found in: {cmd}"
    return _normalize(m.group(1))


def test_pull_request_candidates_are_head_sha_matches_lowest_number_first():
    # `.[0]` of either source picked a pull request by arbitrary index. When a
    # head SHA is the tip of two open pull requests (stacked branches) that can
    # select the draft one, which blanks the number and leaves the real pull
    # request with no gate at all. Both jq programs must filter on the run's
    # head SHA and `sort` ascending, and the API fallback must additionally
    # filter to OPEN pull requests (the event payload's entries are already
    # this run's, but `commits/{sha}/pulls` returns closed ones too).
    run = _step(_job(), "pr")["run"]
    assert _jq_program(run, "cands=$(printf") == (
        '[.[] | select((.head.sha // "") == $sha) | .number] | sort | .[]'
    )
    assert _jq_program(run, "cands=$(gh api") == (
        '[.[] | select(.state == "open" and (.head.sha // "") == $sha) | .number] | sort | .[]'
    )


def test_skips_draft_pull_requests_and_prefers_a_non_draft_candidate():
    # The deleted plan.yml summary job carried `pull_request.draft == false`;
    # restored here as a fail-closed probe over every candidate. `n` starts
    # empty and is assigned in exactly one place -- the branch that saw a
    # literal "open false" -- so a draft, a closed pull request, and an
    # unreadable response are all skipped, while the first non-draft in
    # ascending number order wins and breaks the loop.
    run = _step(_job(), "pr")["run"]
    assert re.search(r'^\s*n=""\s*$', run, re.MULTILINE), "n is not initialised empty"
    pattern = re.compile(
        r"""info=\$\(gh api "repos/\$GITHUB_REPOSITORY/pulls/\$c" --jq '\.state \+ " " \+ """
        r"""\(\.draft \| tostring\)'\) \|\| info=""\s*\n"""
        r"""\s*if \[ "\$info" = "open false" \]; then\s*\n"""
        r"""\s*n="\$c"\s*\n"""
        r"""\s*break\s*\n"""
        r"""\s*fi"""
    )
    assert pattern.search(run), f"candidate selection not found intact in the pr step:\n{run}"
    assigns = [a.strip() for a in re.findall(r'^\s*n=(?!""$)\S+', run, re.MULTILINE)]
    assert assigns == ['n="$c"'], (
        f"n is assigned somewhere other than the open/non-draft branch: {assigns}"
    )


def test_privileged_job_names_the_engine_environment():
    assert _job().get("environment") == "shipmate-engine"


def test_privileged_job_does_not_check_out_the_pull_request_head():
    # The job holding the App key must never execute repository code -- not
    # via `actions/checkout`, and not via a hand-rolled `git init && git fetch
    # && git checkout` that avoids the action but does the same thing.
    for step in _job()["steps"]:
        uses = str(step.get("uses", ""))
        assert "checkout" not in uses.lower(), f"step uses {uses!r}"
        run = str(step.get("run", ""))
        assert "git checkout" not in run, f"step run:s hand-rolls a checkout:\n{run}"
        assert "git fetch" not in run, f"step run hand-rolls a fetch:\n{run}"


def test_artifact_count_input_falls_back_to_a_sentinel_never_zero():
    # `scripts/gate-state` does int(os.environ.get("SHIPMATE_ARTIFACT_COUNT",
    # "0")); an empty string reaches int("") and raises, which kills the
    # action and writes NO gate at all. The `with:` value must never be able
    # to resolve to an empty string -- and the fallback must be '1', not '0':
    # the counting step's own failure branch reports 1 (evidence unknown,
    # never "nothing changed") precisely so a shortfall against cell_count
    # trips gate-state's hold path instead of greening the gate. A '0'
    # fallback here would contradict that sentinel design.
    action_step = next(s for s in _job()["steps"] if "actions/summary" in str(s.get("uses", "")))
    assert (
        action_step.get("with", {}).get("artifact-count")
        == "${{ steps.artifacts.outputs.count || '1' }}"
    )


def test_artifact_count_step_cannot_abort_without_emitting_a_count():
    # A `gh api` failure inside the counting step must not raise the step (and
    # so the whole job -- every later step's plain `if:` is implicitly
    # `success() && ...`) to failure, which would skip the summary step
    # entirely and leave no gate written at all. The step must catch the
    # failure itself (no `-e` flag, on any `set` invocation, in any form) and
    # still emit a `count=` output, WITH the `$GITHUB_OUTPUT` redirect, on
    # every path.
    run = _step(_job(), "artifacts")["run"]
    flag_groups = re.findall(r"\bset\s+[+-]?([a-zA-Z]+)\b", run)
    assert not any("e" in flags for flags in flag_groups), (
        f"an `e` flag reached some `set` invocation in the artifacts step: {flag_groups}"
    )
    assert 'echo "count=$count" >> "$GITHUB_OUTPUT"' in run
    assert 'echo "count=1" >> "$GITHUB_OUTPUT"' in run


def test_artifact_count_gh_call_uses_slurp_and_never_jq_together():
    # `gh api` REJECTS `--slurp` together with `--jq` on the same invocation --
    # exit 1, before any network call ("the `--slurp` option is not supported
    # with `--jq` or `--template`"), verified empirically against gh v2.93.0.
    # `--slurp` must be present (to aggregate every page into one array) and
    # `--jq` must be ABSENT from that same `gh api` call -- the filter runs as
    # a separate, standalone `jq` piped afterwards instead.
    run = _step(_job(), "artifacts")["run"]
    gh_call = _logical_command(run, "gh api --paginate --slurp")
    assert "--slurp" in gh_call
    assert "--jq" not in gh_call


def test_artifact_count_aggregates_every_page_in_one_jq_pass():
    # `--paginate` alone (without --slurp) evaluates a jq filter once PER
    # PAGE. The matrix cap is 256 cells, comfortably more than one
    # 100-artifact page, so a per-page count would emit one line PER PAGE
    # (e.g. "100\n56"): a multi-line value that corrupts $GITHUB_OUTPUT and
    # fails the step. The whole jq filter -- walking `.[]` (the slurped pages)
    # before `.artifacts[]`, selecting on the name prefix, and counting with
    # `length` -- must match exactly, aggregating every page in the one pass.
    run = _step(_job(), "artifacts")["run"]
    jq_filter = re.search(r"\|\s*jq\s+'(.*?)'", run, re.DOTALL)
    assert jq_filter, f"no piped jq filter found in the artifacts step:\n{run}"
    assert _normalize(jq_filter.group(1)) == (
        '[.[].artifacts[] | select(.name | startswith("cell-summary."))] | length'
    )


def test_supersede_check_gh_call_uses_slurp_and_never_jq_together():
    # Same `gh api` constraint as the artifact count: `--slurp` and `--jq`
    # cannot appear on the same invocation.
    run = _step(_job(), "newest")["run"]
    gh_call = _logical_command(run, "gh api --paginate --slurp")
    assert "--slurp" in gh_call
    assert "--jq" not in gh_call


def test_supersede_check_considers_every_completed_run_and_tiebreaks_on_id():
    # Folds M10 in: this asserts the WHOLE jq program as one exact string, not
    # a first-line substring, so it catches every one of:
    #  - a success-only filter (a FAILING run's own conclusion is "failure",
    #    so filtering to conclusion == "success" excludes it from ever seeing
    #    itself as the newest run for its head SHA -- a stale green gate
    #    outlives a newer red one);
    #  - a missing/extra pipe stage (e.g. a second piped jq filter that a
    #    first-line-only check can't see);
    #  - `| first` instead of `| last` (inverts supersede: every run compares
    #    itself against the OLDEST run for its head SHA, so the newest run
    #    always considers itself superseded and no gate is ever written --
    #    this is exactly M10);
    #  - a tiebreak on `run_started_at` alone (two runs for the same head SHA,
    #    e.g. draft->ready, can share a run_started_at second; without the
    #    `.id` tiebreak both can consider themselves superseded, and no gate
    #    is written at all).
    run = _step(_job(), "newest")["run"]
    jq_filter = re.search(r"jq -r '(.*?)'", run, re.DOTALL)
    assert jq_filter, f"no piped jq -r filter found in the newest step:\n{run}"
    assert _normalize(jq_filter.group(1)) == (
        '[.[].workflow_runs[] | select(.status == "completed")] '
        "| sort_by(.run_started_at, .id) | last | .id // empty"
    )


def test_draft_probe_fails_closed_on_an_unreadable_response():
    # `[ "$(gh api ...)" = "true" ]` is exempt from `set -e` (the failing
    # command is only an argument to `[`, not the executed command), so a
    # failed lookup reads as an empty string -- "not a draft" -- and fails
    # OPEN. The state/draft pair must be captured on its own line first, with
    # its failure handled explicitly, and the ONLY value that selects a
    # candidate is the literal "open false" -- so an empty capture selects
    # nothing.
    run = _step(_job(), "pr")["run"]
    assert re.search(r'info=\$\(gh api .*--jq .*\)\s*\|\|\s*info=""', run)
    assert '[ "$info" = "open false" ]' in run


def test_plan_run_url_is_passed_to_the_summary_action():
    # GITHUB_RUN_ID inside the action is this trusted run, which holds neither
    # the plan logs nor the plan artifacts -- the comment footer, the per-cell
    # fallback links and the gate's target_url must all point at the plan run.
    action_step = next(s for s in _job()["steps"] if "actions/summary" in str(s.get("uses", "")))
    assert (
        action_step.get("with", {}).get("plan-run-url")
        == "${{ github.event.workflow_run.html_url }}"
    )
