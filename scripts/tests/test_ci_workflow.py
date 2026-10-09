"""`ci.yml` runs the required `lint-test` check on the engine's Python floor, 3.12.

The ruleset requires that exact check name, and `UV_PYTHON` is the only thing that holds the
interpreter at the floor rather than at the image's default `python3`.

Mutations: rename the `lint-test` job; set `UV_PYTHON` to `3.13`; write it unquoted.
"""

from _loader import WORKFLOWS, workflow_yaml


def test_lint_test_runs_on_the_python_floor():
    jobs = workflow_yaml(WORKFLOWS / "ci.yml")["jobs"]
    assert {job_id: job.get("env") for job_id, job in jobs.items()} == {
        "lint-test": {"UV_PYTHON": "3.12"}
    }
