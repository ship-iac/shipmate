"""The plan and drift matrices run at most `max_parallel` jobs at once; apply waves and unlock
do not.

Apply waves and unlock bind `<env>-apply`, whose reviewers and wait timer would fire again for
every batch, so they stay uncapped.

Threat model: an accidental regression -- a dropped or edited `max-parallel`, a new matrix job
written without one, a cap added to a wave or to unlock -- not a hostile edit to a SHA-pinned
engine file. GitHub runs a matrix with `max-parallel: 0` unbounded, and fails the run when the
expression is empty or a string, so the expression must be `fromJSON` over the detect's
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
    **{
        ("apply-env-level.yml", f"wave{n}"): {
            "fail-fast": False,
            "matrix": {"include": f"${{{{ fromJSON(inputs.waves_json).wave{n} }}}}"},
        }
        for n in range(8)
    },
}

#: Each env-level call's whole `with:` in the two callers.
#: The pre-merge apply passes its pull request, whose cell locks each cell verifies; the merge
#: deploy passes none, because it ignores cell locks.
EXPECTED_ENVLEVEL_WITH = {
    (workflow, f"envlevel{n}"): {
        "waves_json": f"${{{{ needs.detect.outputs.envlevel{n}_waves }}}}",
        "head_sha": "${{ needs.detect.outputs.head_sha }}",
        "runs_on": "${{ inputs.runs_on }}",
        **extra,
    }
    for workflow, extra in (
        (
            "apply.yml",
            {
                "pr_number": "${{ inputs.pr_number }}",
                "auto": "${{ inputs.auto }}",
                "provenance_run": "${{ needs.detect.outputs.provenance_run }}",
                "base_sha": "${{ needs.detect.outputs.base_sha }}",
            },
        ),
        ("deploy.yml", {}),
    )
    for n in range(4)
}

#: The detect `max_parallel` output each workflow exposes; `None` where none may exist.
EXPECTED_DETECT_OUTPUT = {
    "plan.yml": "${{ steps.matrix.outputs.max_parallel }}",
    "drift.yml": "${{ steps.m.outputs.max_parallel }}",
    "unlock.yml": None,
    "apply.yml": None,
    "deploy.yml": None,
}


def _workflow_files():
    """Both spellings GitHub loads a workflow from."""
    return sorted(p for p in WORKFLOWS.iterdir() if p.suffix in (".yml", ".yaml"))


def test_every_matrix_job_carries_exactly_this_strategy():
    """`fail-fast: false` everywhere: on unlock, one cell that cannot determine its lock state
    must not strand its siblings' locks. Unlock's matrix reads `cells`, the only list its
    detect writes; any other `needs.detect.outputs.*` reads '' and `fromJSON('')` fails.

    Mutations: add `max-parallel` to `unlock` or to `wave3`; remove it from `plan`; write
    `drift`'s as `${{ needs.detect.outputs.max_parallel }}` with no `fromJSON`; add a matrix
    job to `unlock.yml`; add a `foo.yaml` holding a matrix job; `fail-fast: true` on a wave."""
    found = {
        (path.name, job_id): job["strategy"]
        for path in _workflow_files()
        for job_id, job in workflow_jobs(path).items()
        if (job.get("strategy") or {}).get("matrix") is not None
    }
    assert found == EXPECTED_STRATEGIES


def test_apply_env_level_takes_no_cap():
    """Mutation: declare a `max_parallel` input on `apply-env-level.yml`."""
    inputs = workflow_yaml("apply-env-level.yml")["on"]["workflow_call"]["inputs"]
    assert inputs == {
        "waves_json": {"required": True, "type": "string"},
        "head_sha": {"required": True, "type": "string"},
        "runs_on": {"required": True, "type": "string"},
        "pr_number": {"required": False, "type": "string", "default": ""},
        "auto": {"required": False, "type": "boolean", "default": False},
        "provenance_run": {"required": False, "type": "string", "default": ""},
        "base_sha": {"required": False, "type": "string", "default": ""},
    }


def test_no_env_level_call_passes_a_cap():
    """Mutation: pass `max_parallel` in `deploy.yml`'s `envlevel2` call."""
    found = {
        (workflow, job_id): job["with"]
        for workflow in ("apply.yml", "deploy.yml")
        for job_id, job in workflow_jobs(workflow).items()
        if job.get("uses") == "./.github/workflows/apply-env-level.yml"
    }
    assert found == EXPECTED_ENVLEVEL_WITH


def test_only_the_capped_workflows_expose_the_detect_output():
    """Mutations: drop `max_parallel` from `plan.yml`'s detect `outputs:` -- the plan matrix
    reads `fromJSON('')` and the run fails; add it to `unlock.yml`'s."""
    found = {
        workflow: workflow_jobs(workflow)["detect"]["outputs"].get("max_parallel")
        for workflow in EXPECTED_DETECT_OUTPUT
    }
    assert found == EXPECTED_DETECT_OUTPUT
