"""The trusted post-plan workflow must not be fireable by branch-authored runs."""

import pathlib
import re

WF = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows/summary.yml"


def text():
    return WF.read_text(encoding="utf-8")


def test_guards_on_the_triggering_workflow_file_path():
    # `workflow_run.workflows:` matches by workflow NAME, so a branch file named
    # "shipmate · plan" with `on: push` would otherwise fire this workflow.
    assert "workflow_run.path" in text()


def test_guards_on_the_triggering_event_being_pull_request():
    assert "workflow_run.event" in text()
    assert "'pull_request'" in text()


def test_guards_on_the_head_repository_being_this_repository():
    # Without this a fork's artifacts would author checks and a gate under the
    # App identity — the fork safe-harbour inverts once this job holds the key.
    assert "head_repository.full_name" in text()


def test_serialises_gate_writes_per_head_sha_at_job_level():
    # Workflow-level `concurrency:` is ignored in a called workflow, so pinning
    # it there would assert a string that does nothing at runtime.
    body = text().split("jobs:", 1)[1]
    assert re.search(r"concurrency:\s*\n\s*group:.*head_sha", body)
    assert "concurrency:" not in text().split("jobs:", 1)[0]


def test_skips_draft_pull_requests():
    assert ".draft" in text()


def test_privileged_job_names_the_engine_environment():
    assert "environment: shipmate-engine" in text()


def test_privileged_job_does_not_check_out_the_pull_request_head():
    # The job holding the App key must never execute repository code.
    assert "actions/checkout" not in text()


def test_artifact_count_input_falls_back_to_a_sentinel_never_zero():
    # `scripts/gate-state` does int(os.environ.get("SHIPMATE_ARTIFACT_COUNT",
    # "0")); an empty string reaches int("") and raises, which kills the
    # action and writes NO gate at all. The `with:` value must never be able
    # to resolve to an empty string -- and the fallback must be '1', not '0':
    # the counting step's own failure branch reports 1 (evidence unknown,
    # never "nothing changed") precisely so a shortfall against cell_count
    # trips gate-state's hold path instead of greening the gate. A '0'
    # fallback here would contradict that sentinel design.
    assert re.search(
        r"artifact-count:\s*\$\{\{\s*steps\.artifacts\.outputs\.count\s*\|\|\s*'1'\s*\}\}",
        text(),
    )


def test_artifact_count_step_cannot_abort_without_emitting_a_count():
    # A `gh api` failure inside the counting step must not raise the step (and
    # so the whole job -- every later step's plain `if:` is implicitly
    # `success() && ...`) to failure, which would skip the summary step
    # entirely and leave no gate written at all. The step must catch the
    # failure itself and still emit a `count=` output on every path.
    body = text().split("id: artifacts", 1)[1].split("- uses:", 1)[0]
    assert "set -euo pipefail" not in body
    assert body.count('echo "count=') >= 2


def test_artifact_count_aggregates_every_page_before_counting():
    # `gh api --paginate` evaluates `--jq` once PER PAGE. The matrix cap is
    # 256 cells, comfortably more than one 100-artifact page, so a bare
    # `[.artifacts[] | ...] | length` filter emits one count PER PAGE (e.g.
    # "100\n56"): a multi-line value that corrupts $GITHUB_OUTPUT and fails
    # the step. `--slurp` must wrap every page into one array first, and the
    # `--jq` filter must walk `.[]` (the pages) before `.artifacts[]`.
    body = text().split("id: artifacts", 1)[1].split("- uses:", 1)[0]
    assert "--paginate --slurp" in body
    assert "[.[].artifacts[]" in body


def test_supersede_check_considers_every_completed_run_not_only_successful():
    # A FAILING run's own record has conclusion "failure". Filtering the
    # candidate list to conclusion == "success" excludes a failing run from
    # ever seeing itself as the newest run for its head SHA, so it always
    # defers to an older successful run and a stale green gate outlives a
    # newer red one. The candidate filter must be status-based, not
    # conclusion-based.
    body = text().split("id: newest", 1)[1].split("- name:", 1)[0]
    jq_line = next(line for line in body.splitlines() if "--jq '" in line)
    assert 'select(.status == "completed")' in jq_line
    assert "conclusion" not in jq_line


def test_supersede_check_aggregates_pages_and_tiebreaks_on_run_id():
    # Same per-page evaluation hazard as the artifact count, plus: two runs
    # for the same head SHA (draft->ready) can share a run_started_at second,
    # and a plain max_by(.run_started_at) tie can make BOTH runs consider
    # themselves superseded, leaving no gate written at all. Break the tie on
    # run id.
    body = text().split("id: newest", 1)[1].split("- name:", 1)[0]
    assert "--paginate --slurp" in body
    assert re.search(r"sort_by\(\.run_started_at,\s*\.id\)", body)


def test_draft_probe_fails_closed_on_an_unreadable_response():
    # `[ "$(gh api ...)" = "true" ]` is exempt from `set -e` (the failing
    # command is only an argument to `[`, not the executed command), so a
    # failed lookup reads as an empty string -- "not a draft" -- and fails
    # OPEN. The draft value must be captured on its own line first, so its
    # failure can be handled explicitly and read as "treat as a draft."
    body = text().split("id: pr", 1)[1].split("- name:", 1)[0]
    assert re.search(r'draft=\$\(gh api .*--jq \.draft\)\s*\|\|\s*draft=""', body)
    assert '[ -z "$draft" ]' in body
