"""deploy.yml's `warm` job saves the provider cache entry of every distinct lock on the merged tree.

`detect` outputs `warm`, one managed stack per distinct lock digest (`deploy-detect`'s
`warm_cells`), and `warm` runs one leg per row on the cells' runner, because the entry name carries
the runner's OS and architecture. A leg checks out the merged commit, installs the tools (which
also saves the OpenTofu entry), and runs `actions/warm`: it names the entry with the cells' own
builder, looks it up, and on a miss initializes the stack through `cell-init` with
`backend: "false"` under the row's injected variables, then saves the entry at the cells' own
`path:` only when init left a file in it. A save that stored nothing is told apart by exact-key
lookups after it: a hit, also the case of another job saving the same entry first, is quiet; two
misses warn once, naming the entry.

The `if:` is load-bearing: an empty matrix fails at strategy evaluation. It is a degrade job: no
job needs it and the gate never reads it, so `continue-on-error` keeps its failure off the run's
conclusion, the cells and the lock release. The deploy run waits for it, so it holds the deploy
concurrency group up to its 15-minute timeout.

Threat model: accidental regression of an engine file that is SHA-pinned and reviewed. The job and
the action's steps are compared whole against hand-written constants.
"""

import os

from _loader import action_yaml, bash_only, run_step, step_by, workflow_jobs

_CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"

#: The whole job, hand-written: a value read back from the workflow passes whatever it says.
EXPECTED_WARM = {
    "needs": ["detect"],
    "if": "${{ needs.detect.outputs.warm != '[]' }}",
    "runs-on": "${{ inputs.runs_on }}",
    "timeout-minutes": 15,
    "permissions": {"contents": "read"},
    "continue-on-error": True,
    "strategy": {
        "fail-fast": False,
        "matrix": {"include": "${{ fromJSON(needs.detect.outputs.warm) }}"},
    },
    "name": "warm / ${{ matrix.stack }}",
    "steps": [
        {"uses": _CHECKOUT, "with": {"ref": "${{ github.sha }}", "persist-credentials": False}},
        {"uses": "$/actions/setup", "with": {"versions": "${{ needs.detect.outputs.tools }}"}},
        {
            "uses": "$/actions/warm",
            "with": {
                "stack": "${{ matrix.stack }}",
                "env": "${{ matrix.environment }}",
                "tf-vars": "${{ toJSON(matrix.tf_vars) }}",
                "github-vars": "${{ toJSON(vars) }}",
                "consumer-secrets": "${{ secrets.SHIPMATE_SECRETS }}",
            },
        },
    ],
}


def test_warm_is_a_degrade_matrix_over_detects_rows():
    """Mutations: `!= '[]'` to `== '[]'` in its `if:`; drop `fail-fast: false`; drop
    `continue-on-error`; `runs-on: ubuntu-slim`; `permissions: {}`; drop the checkout's `ref:`;
    add `tofu: "false"` to its setup step; drop `consumer-secrets` from its warm step."""
    assert workflow_jobs("deploy.yml").get("warm") == EXPECTED_WARM


def test_no_deploy_job_needs_warm():
    """A job needing `warm` would skip on a failed `warm`, or tie its own run to OpenTofu.

    Mutation: add `warm` to `summary`'s `needs`."""
    needing = [
        name
        for name, job in workflow_jobs("deploy.yml").items()
        if "warm" in ([job["needs"]] if isinstance(job.get("needs"), str) else job.get("needs", []))
    ]
    assert needing == []


_ENTRY = "${{ steps.provider-cache-key.outputs.entry }}"
_PATH = "${{ env.TF_PLUGIN_CACHE_DIR }}"
_MISS = (
    "${{ steps.provider-cache-key.outputs.entry != '' && "
    "steps.lookup.outputs.cache-hit != 'true' }}"
)
_RESTORE = "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
_SAVE = "actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
_LOOKUP = {"path": _PATH, "key": _ENTRY, "lookup-only": "true"}
_WARNING = (
    'echo "::warning title=Provider cache entry not saved::No cache entry ${ENTRY} exists after '
    'the save; the cells whose lock it serves download their providers until it is saved."'
)

#: Every step of `actions/warm` but the populated check's and the inject step's bodies, which
#: `test_the_check_and_inject_bodies_are_the_cells_own` compares with the cells' own.
EXPECTED_STEPS = [
    {
        "name": "Provider cache key",
        "id": "provider-cache-key",
        "shell": "bash",
        "env": {"STACK": "${{ inputs.stack }}"},
        "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/provider-cache-key"',
    },
    {
        "name": "Look up the provider cache entry",
        "id": "lookup",
        "if": "${{ steps.provider-cache-key.outputs.entry != '' }}",
        "uses": _RESTORE,
        "with": _LOOKUP,
    },
    {
        "name": "Inject identity variables",
        "if": _MISS,
        "shell": "bash",
        "env": {
            "SHIPMATE_TF_VARS": "${{ inputs.tf-vars }}",
            "SHIPMATE_GITHUB_VARS": "${{ inputs.github-vars }}",
            "SHIPMATE_SECRETS": "${{ inputs.consumer-secrets }}",
            "STACK": "${{ inputs.stack }}",
        },
    },
    {
        "name": "Initialize the stack",
        "id": "init",
        "if": _MISS,
        "uses": "$/actions/cell-init",
        "with": {
            "stack": "${{ inputs.stack }}",
            "env": "${{ inputs.env }}",
            "slug": "",
            "backend": "false",
        },
    },
    {
        "name": "Check the provider cache",
        "id": "provider-cache-files",
        "if": "${{ steps.init.outcome == 'success' }}",
        "shell": "bash",
    },
    {
        "name": "Save provider cache",
        "if": "${{ steps.provider-cache-files.outputs.populated == 'true' }}",
        "continue-on-error": True,
        "uses": _SAVE,
        "with": {"path": _PATH, "key": _ENTRY},
    },
    {
        "name": "Look up the saved entry",
        "id": "verify",
        "if": "${{ steps.provider-cache-files.outputs.populated == 'true' }}",
        "uses": _RESTORE,
        "with": _LOOKUP,
    },
    {
        "name": "Wait before the second lookup",
        "id": "wait",
        "if": "${{ steps.verify.outcome == 'success' && "
        "steps.verify.outputs.cache-hit != 'true' }}",
        "shell": "bash",
        "run": "sleep 10",
    },
    {
        "name": "Look up the saved entry, try 2",
        "id": "verify2",
        "if": "${{ steps.wait.outcome == 'success' }}",
        "uses": _RESTORE,
        "with": _LOOKUP,
    },
    {
        "name": "Warn that the entry was not saved",
        "if": "${{ steps.verify2.outcome == 'success' && "
        "steps.verify2.outputs.cache-hit != 'true' }}",
        "shell": "bash",
        "env": {"ENTRY": _ENTRY},
        "run": _WARNING,
    },
]

_COMPARED_ELSEWHERE = {"Inject identity variables", "Check the provider cache"}


def test_a_leg_inits_on_a_miss_and_saves_a_populated_cache_at_the_cells_path():
    """A hit skips everything after the lookup; a miss injects the row's variables and inits
    through `cell-init` with no backend; the save runs only on a populated cache, under the one
    builder's name and the cells' own path expression; only two lookups that miss warn.
    Mutations: `!= 'true'` to `== 'true'` in the init's `if:`; drop `backend: "false"`; drop
    `lookup-only` from the first lookup; spell the save's `path:` `${{ runner.temp
    }}/.tofu-plugin-cache`; drop the populated clause from the save's `if:`; warn on the first
    lookup's miss."""
    got = [
        {k: v for k, v in step.items() if not (k == "run" and step["name"] in _COMPARED_ELSEWHERE)}
        for step in action_yaml("warm")["runs"]["steps"]
    ]
    assert got == EXPECTED_STEPS


def test_the_check_and_inject_bodies_are_the_cells_own():
    """Mutation: drop `-type f` from the warm check's `find`."""
    for name, cell in (
        ("Check the provider cache", "drift-cell"),
        ("Inject identity variables", "drift-cell"),
    ):
        assert step_by("warm", name=name)["run"] == step_by(cell, name=name)["run"]


@bash_only
def test_the_warning_names_the_entry(tmp_path):
    """Mutation: drop `${ENTRY}` from the warning."""
    body = step_by("warm", name="Warn that the entry was not saved")["run"]
    r = run_step(tmp_path, body, {**os.environ, "ENTRY": "tofu-providers-Linux-X64-abc123"})
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == (
        "::warning title=Provider cache entry not saved::No cache entry "
        "tofu-providers-Linux-X64-abc123 exists after the save; the cells whose lock it serves "
        "download their providers until it is saved.\n"
    )
