"""The commit check-runs listing has one implementation: `_shipmate.check_runs`.

Threat model: an accidental second listing added to an action, workflow or script, which would
bypass the `filter=all` and pagination that `test_shipmate.py` pins on `check_runs`. A hostile
edit to the SHA-pinned, reviewed engine files is out of scope.

Actions and workflows are read through their parsed steps' comment-stripped `run:` lines, so a
`description:` or a shell comment naming the URL is not a listing. Scripts are read as text: the
URL is a Python string there, and no parser applies. The ref after `commits/` is one or more
path segments (a SHA, `refs/heads/<b>`, `${{ inputs.head-sha }}`). A `POST repos/<r>/check-runs`
and a `PATCH .../check-runs/<id>` are not listings and do not match.
"""

import re

from _loader import ACTIONS, ENGINE, SCRIPTS, WORKFLOWS, action_steps, run_lines, workflow_jobs

#: One ref path segment: a `${{ }}` expression, spaces included, or a run of non-space,
#: non-quote characters without a slash.
_SEGMENT = r"(?:\$\{\{[^}]*\}\}|[^/\s\"'`]+)"
_LISTING = re.compile(rf"commits/(?:{_SEGMENT}/)+check-runs(?!/)")


def _sources():
    """(engine-relative path, text) of every place a listing could be issued."""
    for path in sorted(SCRIPTS.glob("*")):
        if path.is_file():
            yield path, path.read_text(encoding="utf-8")
    for path in sorted(ACTIONS.glob("*/action.yml")):
        yield path, "\n".join(ln for s in action_steps(path) for ln in run_lines(s))
    for path in sorted(WORKFLOWS.glob("*.yml")):
        steps = [s for job in workflow_jobs(path).values() for s in job.get("steps") or []]
        yield path, "\n".join(ln for s in steps for ln in run_lines(s))


def test_only_shipmate_lists_a_commits_check_runs():
    """Mutations, each run: add `gh api "repos/$GITHUB_REPOSITORY/commits/$HEAD_SHA/check-runs"`
    to the `run:` of `actions/apply-snapshot/action.yml`; the same with `?per_page=100`; the same
    with `${{ inputs.head-sha }}` as the SHA segment; the same with `refs/heads/$B` as the
    ref."""
    found = {
        path.relative_to(ENGINE).as_posix() for path, text in _sources() if _LISTING.search(text)
    }
    assert found == {"scripts/_shipmate.py"}
