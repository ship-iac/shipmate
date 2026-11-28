"""The plan and drift matrices and the merge deploy's `warm` matrix run at most `max_parallel`
jobs at once; apply cells and unlock do not.

Only jobs binding `<env>-apply` are exempt: its reviewers and wait timer would fire again for
every batch, so a stage's cells and unlock stay uncapped. `warm` binds no environment, so it is
capped. `waves.yml`'s stage matrix binds nothing and runs one stage at a time, in order, and a
failed stage stops the later ones (`max-parallel: 1`, `fail-fast: true`).

Threat model: an accidental regression -- a dropped or edited `max-parallel`, a new matrix job
written without one, a cap added to a stage's cells or to unlock -- not a hostile edit to a
SHA-pinned engine file. GitHub runs a matrix with `max-parallel: 0` unbounded, and fails the run
when the expression is empty or a string, so the expression must be `fromJSON` over the detect's
validated output.

Every expected value is hand-written, never read from the files under test.
"""

from _loader import WORKFLOWS, workflow_jobs, workflow_yaml

_FROM_DETECT = "${{ fromJSON(needs.detect.outputs.max_parallel) }}"

#: The whole `strategy:` of every job in every engine workflow whose strategy holds a matrix.
EXPECTED_STRATEGIES = {
    ("plan.yml", "plan"): {
        "fail-fast": False,
        "max-parallel": _FROM_DETECT,
        "matrix": "${{ fromJSON(needs.detect.outputs.matrix) }}",
    },
    ("drift.yml", "drift"): {
        "fail-fast": False,
        "max-parallel": _FROM_DETECT,
        "matrix": "${{ fromJSON(needs.detect.outputs.matrix) }}",
    },
    ("unlock.yml", "unlock"): {
        "fail-fast": False,
        "matrix": {"include": "${{ fromJSON(needs.detect.outputs.cells) }}"},
    },
    ("waves.yml", "stage"): {
        "max-parallel": 1,
        "fail-fast": True,
        "matrix": {"include": "${{ fromJSON(inputs.stages) }}"},
    },
    ("stage.yml", "cell"): {
        "fail-fast": False,
        "matrix": {"include": "${{ fromJSON(inputs.level_waves)[inputs.key] }}"},
    },
    ("deploy.yml", "warm"): {
        "fail-fast": False,
        "max-parallel": _FROM_DETECT,
        "matrix": {"include": "${{ fromJSON(needs.detect.outputs.warm) }}"},
    },
}

_PASS_THROUGH = {
    "head_sha": "${{ needs.detect.outputs.head_sha }}",
    "runs_on": "${{ inputs.runs_on }}",
    "tools": "${{ needs.detect.outputs.tools }}",
}
_STAGES = {
    "stages": "${{ needs.detect.outputs.stages }}",
    "levels": "${{ needs.detect.outputs.levels }}",
    "keys": "${{ needs.detect.outputs.keys }}",
    "counts": "${{ needs.detect.outputs.counts }}",
}

#: Each caller's whole `waves` `with:`. The pre-merge apply passes its pull request and `lock`'s
#: snapshot of the first level; the merge deploy passes the merged pull request with
#: `deploy: true`, so each cell refuses only a lock another pull request holds.
EXPECTED_WAVES_WITH = {
    "apply-run.yml": {
        **_STAGES,
        **_PASS_THROUGH,
        "pr_number": "${{ inputs.pr_number }}",
        "auto": "${{ inputs.auto }}",
        "provenance_run": "${{ needs.detect.outputs.provenance_run }}",
        "base_sha": "${{ needs.detect.outputs.base_sha }}",
        "by_wave": "${{ needs.lock.outputs.by_wave }}",
    },
    "deploy.yml": {
        **_STAGES,
        **_PASS_THROUGH,
        "pr_number": "${{ needs.detect.outputs.pr_number }}",
        "by_wave": "",
        "deploy": True,
    },
}

#: `waves.yml`'s whole `stage` `with:`. Only level 0's stages take the caller's `by_wave`.
EXPECTED_STAGE_WITH = {
    "level_waves": "${{ fromJSON(inputs.levels)[matrix.level] }}",
    "key": "${{ matrix.key }}",
    "n": "${{ matrix.n }}",
    "first": "${{ matrix.first }}",
    "keys": "${{ inputs.keys }}",
    "counts": "${{ inputs.counts }}",
    "by_wave": "${{ matrix.level == 0 && inputs.by_wave || '' }}",
    "head_sha": "${{ inputs.head_sha }}",
    "runs_on": "${{ inputs.runs_on }}",
    "tools": "${{ inputs.tools }}",
    "pr_number": "${{ inputs.pr_number }}",
    "auto": "${{ inputs.auto }}",
    "provenance_run": "${{ inputs.provenance_run }}",
    "base_sha": "${{ inputs.base_sha }}",
    "deploy": "${{ inputs.deploy }}",
}

_OPTIONAL = {
    "pr_number": {"required": False, "type": "string", "default": ""},
    "auto": {"required": False, "type": "boolean", "default": False},
    "provenance_run": {"required": False, "type": "string", "default": ""},
    "base_sha": {"required": False, "type": "string", "default": ""},
    "deploy": {"required": False, "type": "boolean", "default": False},
    "by_wave": {"required": False, "type": "string", "default": ""},
}
_REQUIRED = {
    "head_sha": {"required": True, "type": "string"},
    "runs_on": {"required": True, "type": "string"},
    "tools": {"required": True, "type": "string"},
}

#: The whole `workflow_call.inputs` of the two stage workflows.
EXPECTED_INPUTS = {
    "waves.yml": {
        "stages": {"required": True, "type": "string"},
        "levels": {"required": True, "type": "string"},
        "keys": {"required": True, "type": "string"},
        "counts": {"required": True, "type": "string"},
        **_REQUIRED,
        **_OPTIONAL,
    },
    "stage.yml": {
        "level_waves": {"required": True, "type": "string"},
        "key": {"required": True, "type": "string"},
        "n": {"required": True, "type": "number"},
        "first": {"required": True, "type": "boolean"},
        "keys": {"required": True, "type": "string"},
        "counts": {"required": True, "type": "string"},
        **_REQUIRED,
        **_OPTIONAL,
    },
}

#: The detect `max_parallel` output each workflow exposes; `None` where none may exist.
EXPECTED_DETECT_OUTPUT = {
    "plan.yml": "${{ steps.matrix.outputs.max_parallel }}",
    "drift.yml": "${{ steps.m.outputs.max_parallel }}",
    "unlock.yml": None,
    "apply-run.yml": None,
    "deploy.yml": "${{ steps.d.outputs.max_parallel }}",
}


def _workflow_files():
    """Both spellings GitHub loads a workflow from."""
    return sorted(p for p in WORKFLOWS.iterdir() if p.suffix in (".yml", ".yaml"))


def test_every_matrix_job_carries_exactly_this_strategy():
    """`fail-fast: false` everywhere: on unlock, one cell that cannot determine its lock state
    must not strand its siblings' locks. Unlock's matrix reads `cells`, the only list its
    detect writes; any other `needs.detect.outputs.*` reads '' and `fromJSON('')` fails.

    Mutations: add `max-parallel` to `unlock` or to `stage.yml`'s `cell`; remove it from `plan`;
    write `drift`'s as `${{ needs.detect.outputs.max_parallel }}` with no `fromJSON`; drop it from
    `deploy.yml`'s `warm`; add a matrix
    job to `unlock.yml`; add a `foo.yaml` holding a matrix job; `max-parallel: 2` or
    `fail-fast: false` on `waves.yml`'s `stage`."""
    found = {
        (path.name, job_id): job["strategy"]
        for path in _workflow_files()
        for job_id, job in workflow_jobs(path).items()
        if (job.get("strategy") or {}).get("matrix") is not None
    }
    assert found == EXPECTED_STRATEGIES


def test_the_stage_workflows_take_no_cap():
    """Mutations: declare a `max_parallel` input on `stage.yml`; make `waves.yml`'s `tools`
    optional; `stage.yml`'s `n` of type `string`."""
    found = {w: workflow_yaml(w)["on"]["workflow_call"]["inputs"] for w in EXPECTED_INPUTS}
    assert found == EXPECTED_INPUTS


def test_the_stage_workflows_publish_no_output():
    """Mutation: declare `on.workflow_call.outputs` with a `by_wave` output on `waves.yml`."""
    found = {w: "outputs" in workflow_yaml(w)["on"]["workflow_call"] for w in EXPECTED_INPUTS}
    assert found == {"waves.yml": False, "stage.yml": False}


def test_no_stage_call_passes_a_cap():
    """Mutations: pass `max_parallel` in `deploy.yml`'s `waves` call; drop `deploy: true`
    from it; pass `deploy` in `apply-run.yml`'s; `by_wave: ''` in `apply-run.yml`'s, so the
    first stage snapshots again; `matrix.level == 1` in `waves.yml`'s `by_wave`."""
    found = {
        workflow: job["with"]
        for workflow in ("apply-run.yml", "deploy.yml")
        for job in workflow_jobs(workflow).values()
        if job.get("uses") == "./.github/workflows/waves.yml"
    }
    assert found == EXPECTED_WAVES_WITH
    assert workflow_jobs("waves.yml")["stage"]["with"] == EXPECTED_STAGE_WITH


def test_only_the_capped_workflows_expose_the_detect_output():
    """Mutations: drop `max_parallel` from `plan.yml`'s detect `outputs:` -- the plan matrix
    reads `fromJSON('')` and the run fails; add it to `unlock.yml`'s."""
    found = {
        workflow: workflow_jobs(workflow)["detect"]["outputs"].get("max_parallel")
        for workflow in EXPECTED_DETECT_OUTPUT
    }
    assert found == EXPECTED_DETECT_OUTPUT
