"""`cell.yml`, the called workflow that runs one cell, and `stage.yml`'s `cell` job that calls it.

Threat model: an accidental edit that (a) lets the job that runs pull-request code write the
state entry or hold the App key, or the job that writes the state run pull-request code;
(b) skips `save` after an `apply` that may have changed the state; (c) feeds `save` another
value than the `apply` job's state output or job id; (d) lets the stage pass the cell another
row's environment or stack, which the concurrency group and the steps read, or another value
than the unparsed `by_wave`, or runs the cells after a refused or cancelled `entry`; (e) lets
`save` write the check before its state step succeeded, or after an `apply` that did not
succeed. Whole parsed values against hand-written constants.
"""

import re

from _loader import load_script, local_action, workflow_jobs

ag = load_script("apply-gate")

CELL_WITH = {
    "cell": "${{ toJSON(matrix) }}",
    "environment": "${{ matrix.environment }}",
    "stack": "${{ matrix.stack }}",
    "head_sha": "${{ inputs.head_sha }}",
    "runs_on": "${{ inputs.runs_on }}",
    "tools": "${{ inputs.tools }}",
    "pr_number": "${{ inputs.pr_number }}",
    "auto": "${{ inputs.auto }}",
    "provenance_run": "${{ inputs.provenance_run }}",
    "base_sha": "${{ inputs.base_sha }}",
    "deploy": "${{ inputs.deploy }}",
    # Unparsed: an expression lookup ignores case, so `save` matches the check name exactly.
    "by_wave": "${{ inputs.by_wave || needs.entry.outputs.by_wave }}",
}
#: The App key passes through: with an empty literal, `save` did not receive shipmate-engine's key
#: (sample run 45911273841).
CELL_SECRETS = {
    "SHIPMATE_ENCRYPTION_KEY": "${{ secrets.SHIPMATE_ENCRYPTION_KEY }}",
    "SHIPMATE_SECRETS": "${{ secrets.SHIPMATE_SECRETS }}",
    "SHIPMATE_APP_PRIVATE_KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}
#: `stage.yml`'s whole `cell` job. A calling job takes no `runs-on`, `environment`, `concurrency`
#: or `cache-mode`. The cells run once `entry` released the stage, or, `entry` skipped, once the
#: caller released it and passed `by_wave`.
CELL_JOB = {
    "needs": ["entry"],
    "if": (
        "${{ !cancelled() && (needs.entry.result == 'success' || "
        "needs.entry.result == 'skipped' && inputs.by_wave != '') }}"
    ),
    "uses": "./.github/workflows/cell.yml",
    "permissions": {
        "contents": "read",
        "actions": "read",
        "checks": "read",
        "pull-requests": "read",
        "id-token": "write",
    },
    "strategy": {
        "fail-fast": False,
        "matrix": {"include": "${{ fromJSON(inputs.level_waves)[inputs.key] }}"},
    },
    "name": "${{ matrix.environment }} / ${{ matrix.stack }}",
    "with": CELL_WITH,
    "secrets": CELL_SECRETS,
}

SAVE = {
    "needs": ["apply"],
    "if": "${{ always() }}",
    "runs-on": "${{ inputs.runs_on }}",
    "cache-mode": "write",
    "permissions": {"actions": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "steps": [
        {
            "name": "Save the cell's state",
            "uses": local_action("state-save"),
            "with": {
                "job-id": "${{ job.check_run_id }}",
                "stack": "${{ inputs.stack }}",
                "environment": "${{ inputs.environment }}",
                "state": "${{ needs.apply.outputs.state }}",
            },
        },
        {
            "name": "Complete this cell's apply check",
            "if": "${{ needs.apply.result == 'success' }}",
            "uses": local_action("cell-complete"),
            "with": {
                "by-wave": "${{ inputs.by_wave }}",
                "stack": "${{ inputs.stack }}",
                "environment": "${{ inputs.environment }}",
                "apply-job-id": "${{ needs.apply.outputs.job_id }}",
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        },
    ],
}

#: `apply`'s first step, whole. A key from the apply environment or a repository or organization
#: secret would otherwise sit beside pull-request code.
REFUSE_KEY = {
    "name": "Refuse an apply job that holds the App key",
    "env": {
        "KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
        "BINDING": "${{ fromJSON(inputs.cell).env_binding }}",
    },
    "run": (
        'if [ -n "$KEY" ]; then\n'
        '  echo "::error::SHIPMATE_APP_PRIVATE_KEY reaches pull-request code in this apply job: '
        "the environment $BINDING, or a repository or organization secret, holds it; delete it "
        'there and keep the key only on shipmate-engine"\n'
        "  exit 1\n"
        "fi\n"
    ),
}
#: `apply`'s steps in order, by name or action.
APPLY_ORDER = [
    "Refuse an apply job that holds the App key",
    "Pre-apply check",
    "actions/checkout",
    local_action("setup"),
    "Configure cloud credentials (AWS OIDC)",
    "Apply the cell",
]


def test_the_stage_calls_the_cell_with_one_block():
    """Mutations: `stack: ${{ matrix.environment }}`; add `cache-mode: read` to `cell`;
    `by_wave: ${{ toJSON(fromJSON(inputs.by_wave || needs.entry.outputs.by_wave)) }}`
    (re-serializing merges names differing only in case); the empty literal `""` for the App
    key, which left `save` without the key in sample run 45911273841; drop `!cancelled() &&`
    from `if:`; `'failure'` for `'skipped'` in it; drop `&& inputs.by_wave != ''` from it."""
    assert workflow_jobs("stage.yml")["cell"] == CELL_JOB


def test_the_save_job_is_whole():
    """The state step's name is `apply-gate.STATE_STEP`, and the check write runs only after it
    succeeded (no status function) and only when `apply` succeeded. Mutations: `if: success()`
    on the job; bind `${{ fromJSON(inputs.cell).env_binding }}`; add an `actions/checkout`
    step; read `state` from `needs.apply.outputs.digest`; `always() &&` on the write step's
    `if:`; drop `deploy` from its `with:`; swap the two steps."""
    assert workflow_jobs("cell.yml")["save"] == SAVE


def test_the_state_steps_name_is_the_one_apply_gate_reads():
    """Mutation: rename the state step `Save the state` in `cell.yml` (and in `SAVE`)."""
    assert workflow_jobs("cell.yml")["save"]["steps"][0]["name"] == ag.STATE_STEP


def test_the_apply_job_refuses_the_app_key_first():
    """Mutations: move the refusal after `Pre-apply check`; drop `exit 1`; `-z` for `-n`; add
    `continue-on-error: true` to it."""
    steps = workflow_jobs("cell.yml")["apply"]["steps"]
    order = [s.get("name") or s["uses"].split("@")[0] for s in steps]
    assert (steps[0], order) == (REFUSE_KEY, APPLY_ORDER)


def test_a_refused_key_skips_the_apply_step():
    """The refusal fails, so `Pre-apply check`, whose `if:` carries no status function, is
    skipped rather than failed, and `Apply the cell` does not run. Mutations: prefix the
    pre-apply `if:` with `always() &&`; `!= 'success'` for `== 'failure'` in the apply step's
    `if:`."""
    steps = {s.get("name"): s for s in workflow_jobs("cell.yml")["apply"]["steps"]}
    assert not re.search(r"always\(\)|failure\(\)|cancelled\(\)", steps["Pre-apply check"]["if"])
    py = (
        steps["Apply the cell"]["if"]
        .removeprefix("${{")
        .removesuffix("}}")
        .replace("!cancelled()", "True")
        .replace("success()", "False")
        .replace("steps.pre-apply.outcome", "'skipped'")
        .replace("&&", " and ")
        .replace("||", " or ")
    )
    assert re.fullmatch(r"(?:\s|True|False|and|or|==|[()]|'[a-z]*')*", py), py
    # The fullmatch above admits only literals and operators.
    assert eval(py, {"__builtins__": {}}) is False  # noqa: S307


def test_the_apply_job_outputs_its_cells_state_and_its_job_id():
    """Mutations: read `state` from `steps.handoff.outputs.state` (no such step in the job);
    `job_id` from `github.run_id`."""
    apply = workflow_jobs("cell.yml")["apply"]
    (cell,) = [s for s in apply["steps"] if s.get("id") == "cell"]
    assert (apply["outputs"], cell["uses"]) == (
        {"state": "${{ steps.cell.outputs.state }}", "job_id": "${{ job.check_run_id }}"},
        local_action("apply-cell"),
    )
