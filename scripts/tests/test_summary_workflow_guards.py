"""The trusted summary job must refuse forks, and execute no consumer code.

It runs on `pull_request_target`, through the consumer's plan workflow, holding the App key. Two
things keep it safe: its `if:`, and the fact that it executes no consumer repository content. The
job now lives in `plan.yml` alongside two jobs that check out and execute pull-request content --
`detect` and `plan` -- so the job-id list is pinned here too. The job checks out nothing; any
checkout step, which under `pull_request_target` would take the pull request head, changes the
ordered step list below pins.
Every assertion below is on a parsed value -- a whole `if:`/`environment:`/`with:` field --
rather than a substring of the raw file text.
The substring form was proven vacuous: four simultaneous mutations of the summary job (all three
trust guards inverted, `environment: shipmate-engine` commented out, the draft-skip deleted) left
the old suite's `1 failed, 762 passed` unchanged from baseline. A YAML comment or an inverted
operator can contain the same substring as the real guard; it cannot produce the same parsed
value.

A draft is not refused here: it reaches the job, and gate-state writes its gate pending with the
reason.
"""

import re

from _loader import (
    ACTIONS,
    ENGINE_ENVIRONMENT,
    WORKFLOWS,
    action_steps,
    action_yaml,
    load_script,
    local_action,
    step_by,
    workflow_yaml,
)

WF = WORKFLOWS / "plan.yml"
gs = load_script("gate-status")
sc = load_script("summary-comment")

#: Hand-written, never derived from the workflow. A constant lifted out of the file it checks
#: passes whatever the file says.
EXPECTED_IF = (
    "${{ !cancelled() && needs.facts.outputs.head-repo != '' && "
    "needs.facts.outputs.head-repo == github.repository && needs.defer.outputs.run == '' }}"
)
#: The whole job, as an ordered list of what each step runs: the action it uses, or its
#: `run:` script. A checkout step, another `run:` step, or any extra step at all changes this
#: list, where a substring scan would miss every one of those.
EXPECTED_STEPS = [
    "actions/download-artifact",
    "actions/download-artifact",
    local_action("summary"),
    "true",
    "true",
]
#: The two steps after the summary action whose conclusions `gate-status published` reads,
#: hand-written.
EXPECTED_AUTO_STEPS = [
    {
        "name": "Auto apply follows",
        "if": "${{ steps.summary.outputs.auto-change == 'yes' }}",
        "run": "true",
    },
    {
        "name": "No auto apply follows",
        "if": "${{ steps.summary.outputs.auto-change == 'no' }}",
        "run": "true",
    },
]
EXPECTED_SUMMARY_WITH = {
    "pr-number": "${{ needs.facts.outputs.pr-number }}",
    "head-sha": "${{ needs.facts.outputs.head-sha }}",
    "head-ref": "${{ needs.facts.outputs.head-ref }}",
    "detect-result": "${{ needs.detect.result }}",
    "detect-refusal": "${{ needs.detect.outputs.refusal }}",
    "plan-result": "${{ needs.plan.result }}",
    "planned-cells": "${{ needs.detect.outputs.count }}",
    "planned-cell-names": "${{ needs.detect.outputs.cells }}",
    "on-demand": "${{ needs.facts.outputs.on-demand }}",
    "is-draft": "${{ needs.facts.outputs.is-draft }}",
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
    "github-vars": "${{ toJSON(vars) }}",
    "unmanaged-stacks": "${{ needs.detect.outputs.unmanaged }}",
    "lost-env-tags": "${{ needs.detect.outputs.lost }}",
    "env-order": "${{ needs.detect.outputs.env_order }}",
    "command-hints": "${{ needs.detect.outputs.command_hints }}",
    "auto-envs": "${{ needs.detect.outputs.auto_envs }}",
    "targeted-envs": "${{ needs.detect.outputs.targeted_envs }}",
    "behind": "${{ needs.detect.outputs.behind }}",
}
#: `detect`'s whole outputs map: `unmanaged`, `refusal` and `cells` are what the `with:` above
#: passes on; `tools` is the version pair the plan cells install.
EXPECTED_DETECT_OUTPUTS = {
    "matrix": "${{ steps.matrix.outputs.matrix }}",
    "empty": "${{ steps.matrix.outputs.empty }}",
    "count": "${{ steps.matrix.outputs.count }}",
    "max_parallel": "${{ steps.matrix.outputs.max_parallel }}",
    "command_hints": "${{ steps.matrix.outputs.command_hints }}",
    "auto_envs": "${{ steps.matrix.outputs.auto_envs }}",
    "targeted_envs": "${{ steps.matrix.outputs.targeted_envs }}",
    "unmanaged": "${{ steps.matrix.outputs.unmanaged }}",
    "lost": "${{ steps.matrix.outputs.lost }}",
    "refusal": (
        "${{ steps.setup.outputs.refusal || steps.matrix.outputs.refusal"
        " || steps.checks.outputs.refusal }}"
    ),
    "cells": "${{ steps.matrix.outputs.cells }}",
    "env_order": "${{ steps.matrix.outputs.env_order }}",
    "behind": "${{ steps.matrix.outputs.behind }}",
    "tools": "${{ steps.setup.outputs.tools }}",
}
EXPECTED_JOB_IDS = [
    "claim",
    "facts",
    "defer",
    "defer-auto",
    "detect",
    "plan",
    "summary",
    "gate-hold",
    "outcome",
]


def _summary_job():
    doc = workflow_yaml(WF)
    jobs = doc["jobs"]
    assert list(jobs) == EXPECTED_JOB_IDS, (
        f"plan.yml's jobs are {list(jobs)}; these guards cover only {EXPECTED_JOB_IDS}, and "
        "each job id is also a check-run name segment"
    )
    return jobs["summary"], doc


def test_the_trusted_job_refuses_forks():
    """The whole `if:`, compared as one value.

    Every clause is load-bearing. The decision cannot move to the consumer's file, because
    nothing inspects consumer YAML -- a consumer who dropped a clause would hand a fork an
    App-authored gate and nothing anywhere would notice. The empty-string clause is what makes an
    omitted head repository a refusal instead of a pass. The fork clause guards the App key over
    fork-authored content and yields to no trigger. There is no draft clause: a draft reaches the
    job, and gate-state writes its gate pending with the reason.

    Mutation: restore `&& (needs.facts.outputs.is-draft == 'false' ||
    needs.facts.outputs.on-demand == 'true')`. Mutation: drop `needs.defer.outputs.run == ''` (a
    ready run publishes again over the head an earlier autoplan published).
    """
    job, _ = _summary_job()
    assert " ".join(job["if"].split()) == EXPECTED_IF


def test_the_trusted_job_binds_the_engine_environment():
    job, _ = _summary_job()
    assert job["environment"] == ENGINE_ENVIRONMENT


def test_the_trusted_job_checks_out_nothing_and_runs_exactly_these_steps():
    """It runs at the base ref holding the App key. A checkout of the pull request head here
    would make it the canonical pull_request_target vulnerability, and so would any step that
    executes repository content by another route, which is why the step list is compared whole:
    any checkout step at all reddens it. Mutation: add an `actions/checkout` step ahead of the
    action."""
    job, _ = _summary_job()
    assert [str(s["uses"]).split("@")[0] if "uses" in s else s["run"] for s in job["steps"]] == (
        EXPECTED_STEPS
    )


def test_the_auto_apply_steps_follow_the_summary_action_exactly():
    """`gate-status published` reads each step's conclusion by name, so the names must equal
    `AUTO_STEP_YES` and `AUTO_STEP_NO`. Mutations: rename a step in `plan.yml` only; swap the
    two `if:` values."""
    job, _ = _summary_job()
    assert job["steps"][-2:] == EXPECTED_AUTO_STEPS
    assert (gs.AUTO_STEP_YES, gs.AUTO_STEP_NO) == ("Auto apply follows", "No auto apply follows")


def test_one_pair_of_words_says_whether_an_auto_apply_follows():
    """`summary-comment` writes `yes` or `no`, the `plan.yml` steps compare them,
    `gate-status published` prints them from those steps, `defer-auto` skips on `no`, and
    `actions/auto-dispatch` replies on `yes` in its decision and its failure reply;
    `plan-defer` passes the word on unchanged (`test_plan_defer`). Mutations: rename `no` in
    `_auto_state` only; rename `yes` in the decision's `AUTO_CHANGE` test only; rename `yes`
    in the failure reply's `if:` only."""
    cell = {"environment": "dev-eu", "changed": True}
    written = (sc.auto_change([cell], '["dev-eu"]'), sc.auto_change([cell], "[]"))
    job, doc = _summary_job()
    compared = tuple(s["if"].split("'")[1] for s in job["steps"][-2:])

    def state(name):
        jobs = [{"name": "plan / summary", "steps": [{"name": name, "conclusion": "success"}]}]
        return gs._auto_state(jobs)

    printed = (state(gs.AUTO_STEP_YES), state(gs.AUTO_STEP_NO))
    skipped = doc["jobs"]["defer-auto"]["if"].split("'")[-2]
    decide = step_by("auto-dispatch", id="decide")["run"]
    reply_if = step_by("auto-dispatch", name="Reply that the deferred decision failed")["if"]
    replied = (re.findall(r'"\$AUTO_CHANGE" = (\S+) \]', decide), reply_if.split("'")[-2])
    assert (written, compared, printed, skipped, replied) == (
        ("yes", "no"),
        ("yes", "no"),
        ("yes", "no"),
        "no",
        (["yes"], "yes"),
    )


def test_the_workflow_passes_exactly_these_values_to_the_summary_action():
    """Mutation: drop the `is-draft:` line -> gate-state never sees a draft.
    Mutation: drop the `unmanaged-stacks:` line -> the comment never names an unmanaged stack.
    Mutation: drop `detect`'s `unmanaged` output -> the same, silently.
    Mutation: drop the `lost-env-tags:` line -> the comment never names a lost pair.
    Mutation: drop the action's `lost-env-tags` input -> its input names differ.
    Mutation: drop `detect`'s `refusal` output -> the gate never names a detect error.
    Mutation: swap the operands of `detect`'s `refusal` output -> its expression differs.
    Mutation: drop `steps.setup.outputs.refusal ||` from `detect`'s `refusal` output -> a
    setup refusal reaches the gate as the generic text.
    Mutation: drop the `planned-cell-names:` line -> no cell with no plan summary is ever named.
    Mutation: drop the `env-order:` line -> the comment orders environments by name.
    Mutation: drop the `command-hints:` line, `detect`'s `command_hints` output or the action's
    `command-hints` input -> the comment never shows its command section.
    Mutation: drop the `auto-envs:` line, `detect`'s `auto_envs` output or the action's
    `auto-envs` input -> no auto apply is ever dispatched.
    Mutation: drop the `head-ref:` line or the action's `head-ref` input -> no lock holder is
    ever found stacked on the pull request.
    Mutation: drop the `targeted-envs:` line, `detect`'s `targeted_envs` output or the action's
    `targeted-envs` input -> the commands section offers only the bare `shipmate apply`.
    Mutation: drop `detect`'s `tools` output -> every plan cell's setup refuses.
    Mutation: drop `detect`'s `behind` output, or the `behind:` line -> the summary reads no
    behind cell and holds every gate as an evidence gap."""
    job, _ = _summary_job()
    call = [s for s in job["steps"] if "actions/summary" in str(s.get("uses", ""))]
    assert len(call) == 1
    assert call[0]["with"] == EXPECTED_SUMMARY_WITH
    assert set(action_yaml("summary")["inputs"]) == set(EXPECTED_SUMMARY_WITH)
    assert workflow_yaml(WF)["jobs"]["detect"]["outputs"] == EXPECTED_DETECT_OUTPUTS


def _job_step(name):
    job, _ = _summary_job()
    matches = [s for s in job["steps"] if s.get("name") == name]
    assert len(matches) == 1, f"summary job: {len(matches)} steps named {name!r}"
    return matches[0]


#: The whole download step, as parsed. Any extra key reddens the comparison:
#: an `if:` or a `github-token`, which the job's `contents: read` grant would not cover.
EXPECTED_DOWNLOAD_STEP = {
    "name": "Download plan cell summaries",
    "uses": "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333",
    "continue-on-error": True,
    "timeout-minutes": 1,
    "with": {"pattern": "cell-summary.*", "path": "cells"},
}


def test_the_summary_job_always_downloads_and_tolerates_a_failed_or_hung_download():
    """Unconditional, because skipping the download when zero cells were planned makes
    gate-state's "more cells than planned" branch unreachable. `continue-on-error`, because a
    failed or timed-out download must reach gate-state as a shortfall that holds the gate, not end
    the job before any gate is written. `timeout-minutes` ends the 180 s a retried download holds
    the job. `path` is the readers' default directory.

    Mutations: drop `continue-on-error`; drop `timeout-minutes`; change `path`; add
    `github-token`; add `if: ${{ needs.detect.outputs.count != '0' }}`.
    """
    assert _job_step("Download plan cell summaries") == EXPECTED_DOWNLOAD_STEP


#: The whole cell-error download step, as parsed.
EXPECTED_CELL_ERROR_DOWNLOAD_STEP = {
    "name": "Download cell errors",
    "if": "${{ needs.plan.result == 'failure' }}",
    "uses": "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333",
    "continue-on-error": True,
    "timeout-minutes": 1,
    "with": {"pattern": "cell-error.*", "path": "cell-errors"},
}


def test_the_summary_job_downloads_cell_errors_where_the_held_render_reads_them():
    """Reviewer-facing only, so a failed download continues: the gate reads none of it. `path` is
    `cell_errors`' default directory. `plan-cell` uploads a cell error only under `failure()`,
    which fails the plan job. Mutations: drop `continue-on-error`; change `path`; change
    `pattern` to `cell-summary.*`; `if:` to `needs.plan.result != 'success'`."""
    assert _job_step("Download cell errors") == EXPECTED_CELL_ERROR_DOWNLOAD_STEP


def test_no_engine_action_downloads_an_artifact():
    """A composite action's step takes no `timeout-minutes`, so a download there cannot be
    bounded against the 180 s a retried download holds the job (actions/toolkit#2575); every
    download is a workflow job step. Mutation: put `Download plan cell summaries` back inside
    `actions/summary`."""
    downloads = [
        path.parent.name
        for path in sorted(ACTIONS.glob("*/action.yml"))
        for step in action_steps(path)
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
    ]
    assert downloads == []


#: Hand-written: a superseded run or a moved upsert writes no apply check and no gate.
NOT_MOVED = (
    "${{ steps.hold.outputs.superseded != 'true' && steps.upsert.outputs.result != 'moved' }}"
)


def test_a_moved_head_skips_the_apply_checks_and_the_gate():
    """A `moved` upsert, `held` mode included, means the pull request left this head.
    Mutations: `&&` -> `||` in either `if:`; drop the `moved` clause from either."""
    names = ["Create apply checks (pending / no-changes)", "Create/refresh gate"]
    assert {n: step_by("summary", name=n)["if"] for n in names} == dict.fromkeys(names, NOT_MOVED)


#: The summary action's whole `outputs:` mapping, hand-written.
EXPECTED_ACTION_OUTPUTS = {
    "upsert": {
        "description": (
            "What `Write the plan comment` did: upsert-comment's one word, `moved` when a "
            "`draft` or `hold` run finds the pull request moved past its head, or `failed` when "
            "the held render or write failed. Empty when the step skipped or exited before "
            "writing."
        ),
        "value": "${{ steps.upsert.outputs.result }}",
    },
    "superseded": {
        "description": (
            "`true` when the hold step found the pull request moved past this run's head, or a "
            "newer plan run holding its gate, and this run wrote nothing on the head. Empty "
            "otherwise. A head that moves after that read reports through `upsert` as `moved`."
        ),
        "value": "${{ steps.hold.outputs.superseded }}",
    },
    "auto-change": {
        "description": (
            "`yes` when a changed cell sits in an environment `auto-envs` names, `no` when "
            "`auto-envs` is a list of names and none does, empty when it is empty or not such a "
            "list, or when the run was superseded before it built the comment. The `plan.yml` "
            "steps `Auto apply follows` and `No auto apply follows` publish it as their "
            "conclusions."
        ),
        "value": "${{ steps.build.outputs.auto_change }}",
    },
}


def test_the_summary_action_exposes_the_comment_write_and_the_supersede():
    """Mutation: map `upsert` to `${{ steps.hold.outputs.superseded }}`."""
    assert action_yaml("summary")["outputs"] == EXPECTED_ACTION_OUTPUTS


def test_the_summary_job_passes_the_action_outputs_on():
    """The plan `outcome` job reads both. Mutations: drop `superseded`; drop the step's `id`;
    move the summary action below the two auto apply steps."""
    job, _ = _summary_job()
    assert job["outputs"] == {
        "upsert": "${{ steps.summary.outputs.upsert }}",
        "superseded": "${{ steps.summary.outputs.superseded }}",
    }
    assert [s.get("id") for s in job["steps"]] == [None, None, "summary", None, None]
