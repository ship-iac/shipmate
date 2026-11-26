"""`cell.yml`, the called workflow that runs one wave cell, and the eight wave jobs that call it.

Threat model: an accidental edit that (a) lets the job that runs pull-request code write the
state entry or hold the App key, or the job that writes the state run pull-request code;
(b) skips `save` after an `apply` that may have changed the state; (c) feeds `save` another
value than the `apply` job's state output or job id; (d) lets one wave pass the cell another
row's environment or stack, which the concurrency group and the steps read, or another value
than the level's unparsed `by_wave`; (e) lets `save` write the check before its state step
succeeded, or after an `apply` that did not succeed. Whole parsed values against hand-written
constants.
"""

import re

from _loader import load_script, local_action, workflow_jobs

ag = load_script("apply-gate")

WAVES = [f"wave{n}" for n in range(8)]

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
    "by_wave": "${{ inputs.by_wave || needs.snapshot.outputs.by_wave }}",
}
#: The App key passes through: with an empty literal, `save` did not receive shipmate-engine's key
#: (sample run 45911273841).
CELL_SECRETS = {
    "SHIPMATE_ENCRYPTION_KEY": "${{ secrets.SHIPMATE_ENCRYPTION_KEY }}",
    "SHIPMATE_SECRETS": "${{ secrets.SHIPMATE_SECRETS }}",
    "SHIPMATE_APP_PRIVATE_KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}
#: The keys of a calling wave job. GitHub refuses `runs-on` and `environment` on a job that calls
#: a workflow; a repeated `concurrency` group fails the call at once; a `cache-mode: read` there
#: fails `save` at startup.
WAVE_KEYS = {"needs", "if", "uses", "permissions", "strategy", "name", "with", "secrets"}

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


def test_every_wave_calls_the_cell_with_one_block():
    """Mutations: `stack: ${{ matrix.environment }}` on `wave3`; add `cache-mode: read` to
    `wave0`; `by_wave: ${{ toJSON(fromJSON(inputs.by_wave || needs.snapshot.outputs.by_wave)) }}`
    on `wave2` (re-serializing merges names differing only in case);
    the empty literal `""` for the App key in the shared secrets block, which left `save` without
    the key in sample run 45911273841."""
    jobs = workflow_jobs("apply-env-level.yml")
    found = {w: (set(jobs[w]), jobs[w]["uses"], jobs[w]["with"], jobs[w]["secrets"]) for w in WAVES}
    expected = (WAVE_KEYS, "./.github/workflows/cell.yml", CELL_WITH, CELL_SECRETS)
    assert found == dict.fromkeys(WAVES, expected)


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
