"""Every cell matrix runs at most `max_parallel` jobs at once.

Threat model: an accidental regression -- a dropped or edited `max-parallel`, a new matrix job
written without one, a wave written out in full, a caller that stops passing the cap -- not a
hostile edit to a SHA-pinned engine file. GitHub runs a matrix with `max-parallel: 0`
unbounded, and fails the run when the expression is empty or a string, so the expression must
be `fromJSON` over the detect's validated output.

Every expected value is hand-written, never read from the files under test.
"""

from _loader import WORKFLOWS, workflow_jobs, workflow_yaml

_FROM_DETECT = "${{ fromJSON(needs.detect.outputs.max_parallel) }}"
_FROM_INPUT = "${{ fromJSON(inputs.max_parallel) }}"

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
        "max-parallel": _FROM_DETECT,
        "matrix": {"include": "${{ fromJSON(needs.detect.outputs.cells) }}"},
    },
    **{
        ("apply-env-level.yml", f"wave{n}"): {
            "fail-fast": False,
            "max-parallel": _FROM_INPUT,
            "matrix": {"include": f"${{{{ fromJSON(inputs.waves_json).wave{n} }}}}"},
        }
        for n in range(8)
    },
}

#: Each env-level call's whole `with:` in the two callers.
EXPECTED_ENVLEVEL_WITH = {
    (workflow, f"envlevel{n}"): {
        "waves_json": f"${{{{ needs.detect.outputs.envlevel{n}_waves }}}}",
        "head_sha": "${{ needs.detect.outputs.head_sha }}",
        "max_parallel": "${{ needs.detect.outputs.max_parallel }}",
    }
    for workflow in ("apply.yml", "deploy.yml")
    for n in range(4)
}

#: The detect output each capped matrix's caller reads, per workflow.
EXPECTED_DETECT_OUTPUT = {
    "plan.yml": "${{ steps.matrix.outputs.max_parallel }}",
    "drift.yml": "${{ steps.m.outputs.max_parallel }}",
    "unlock.yml": "${{ steps.d.outputs.max_parallel }}",
    "apply.yml": "${{ steps.d.outputs.max_parallel }}",
    "deploy.yml": "${{ steps.d.outputs.max_parallel }}",
}


def test_every_matrix_job_carries_exactly_this_strategy():
    """Mutations: remove `max-parallel` from `wave5`; write `drift`'s as
    `${{ needs.detect.outputs.max_parallel }}` with no `fromJSON`; add a matrix job to
    `unlock.yml`."""
    found = {
        (path.name, job_id): job["strategy"]
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job_id, job in workflow_jobs(path.name).items()
        if (job.get("strategy") or {}).get("matrix") is not None
    }
    assert found == EXPECTED_STRATEGIES


def test_apply_env_level_requires_the_cap_as_a_string():
    """Mutation: make the input `required: false` -- a caller that drops it reaches
    `fromJSON('')`, which fails the run only once a wave starts."""
    inputs = workflow_yaml("apply-env-level.yml")["on"]["workflow_call"]["inputs"]
    assert inputs["max_parallel"] == {"required": True, "type": "string"}


def test_every_env_level_call_passes_the_cap():
    """Mutation: drop `max_parallel` from `deploy.yml`'s `envlevel2` call."""
    found = {
        (workflow, job_id): job["with"]
        for workflow in ("apply.yml", "deploy.yml")
        for job_id, job in workflow_jobs(workflow).items()
        if job.get("uses") == "./.github/workflows/apply-env-level.yml"
    }
    assert found == EXPECTED_ENVLEVEL_WITH


def test_every_capped_workflow_exposes_the_detect_output():
    """Mutation: drop `max_parallel` from `plan.yml`'s detect `outputs:` -- the plan matrix
    reads `fromJSON('')` and the run fails."""
    found = {
        workflow: workflow_jobs(workflow)["detect"]["outputs"].get("max_parallel")
        for workflow in EXPECTED_DETECT_OUTPUT
    }
    assert found == EXPECTED_DETECT_OUTPUT
