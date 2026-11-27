"""The prose counts and lists of engine jobs that run on `ubuntu-slim` or bind `shipmate-engine`
match the workflows.

Each count is derived from the reusable engine workflows in `.github/workflows/`, those whose
`on` holds `workflow_call`, so the repository's own CI does not count: a job runs on
`ubuntu-slim` when its `runs-on` is exactly that label, and binds `shipmate-engine` when its
`environment` names it, as a string or as a mapping's `name`. Each list's backticked job ids,
`wave<i>-<x>` … `wave<j>-<x>` expanded and workflow file names dropped, must equal the derived job
ids as a multiset; which workflow a job sits under is not checked. Threat model: a job added,
moved or relabelled without the prose following it.

Mutations, each red: `twenty-six` to `twenty-five` at each of `CONTRACT.md`'s two sites,
`docs/aws.md`'s two sites and `docs/getting-started.md`'s; `twenty-nine` to `twenty-eight` in
`docs/troubleshooting.md`; `wave7-release` to `wave6-release` in each list; drop `save` from
`docs/troubleshooting.md`'s list; expand only
`wave0-<x>` … `wave7-<x>` in `_listed`; drop `and `result`` from the `unlock.yml` entry of the
`CONTRACT.md` and `docs/aws.md` lists; drop `and `replan`` from `docs/troubleshooting.md`'s list;
drop `unflip` from the `apply-run.yml` entry of each list; with `ci.yml`'s job moved to
`ubuntu-slim`, drop the `workflow_call` filter.
"""

import collections
import re

import pytest
from _loader import ENGINE, WORKFLOWS, workflow_yaml

_ONES = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_TENS = {2: "twenty", 3: "thirty", 4: "forty"}


def _words(n):
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones - 1]}" if ones else "")


def _jobs(selected):
    workflows = [workflow_yaml(path) for path in sorted(WORKFLOWS.glob("*.yml"))]
    return [
        job
        for workflow in workflows
        if "workflow_call" in workflow["on"]
        for job, spec in workflow["jobs"].items()
        if selected(spec)
    ]


def _binds_engine(spec):
    env = spec.get("environment")
    return (env.get("name") if isinstance(env, dict) else env) == "shipmate-engine"


SLIM = _jobs(lambda spec: spec.get("runs-on") == "ubuntu-slim")
ENGINE_BOUND = _jobs(_binds_engine)


def test_the_derived_counts_are_the_documented_ones():
    """The counts the prose states, hand-written. Mutation: count every workflow with
    `ci.yml`'s job on `ubuntu-slim` -- 27, red."""
    assert (len(SLIM), len(ENGINE_BOUND)) == (26, 29)


def _doc(name):
    return " ".join((ENGINE / name).read_text(encoding="utf-8").split())


def _listed(text):
    """The job ids `text` names in backticks, ranges expanded, workflow file names dropped."""
    text = re.sub(
        r"`wave(\d)-([a-z]+)` … `wave(\d)-\2`",
        lambda m: ", ".join(f"`wave{n}-{m[2]}`" for n in range(int(m[1]), int(m[3]) + 1)),
        text,
    )
    return collections.Counter(t for t in re.findall(r"`([^`]+)`", text) if not t.endswith(".yml"))


@pytest.mark.parametrize(
    ("doc", "phrase", "jobs"),
    [
        ("CONTRACT.md", "every engine job but {}, which run on", SLIM),
        ("CONTRACT.md", "cannot use `ubuntu-slim` leaves the {} `ubuntu-slim` jobs", SLIM),
        ("docs/aws.md", "every engine job but {}:", SLIM),
        ("docs/aws.md", "The input does not reach {} jobs", SLIM),
        ("docs/getting-started.md", "every engine job except {} that run", SLIM),
        ("docs/troubleshooting.md", "All {} jobs that bind `shipmate-engine`", ENGINE_BOUND),
    ],
)
def test_each_count_matches_the_workflows(doc, phrase, jobs):
    assert phrase.format(_words(len(jobs))) in _doc(doc)


@pytest.mark.parametrize(
    ("doc", "start", "end", "jobs"),
    [
        (
            "CONTRACT.md",
            "`ubuntu-slim` whatever `runs_on` the caller passes: `plan.yml`'s",
            " `docs/aws.md` §Runner choice",
            SLIM,
        ),
        ("docs/aws.md", "nor `terramate`: ", " `issues` in `drift.yml`", SLIM),
        (
            "docs/troubleshooting.md",
            "fail before any step runs: ",
            " No `shipmate / gate`",
            ENGINE_BOUND,
        ),
    ],
)
def test_each_list_names_the_workflows_jobs(doc, start, end, jobs):
    text = _doc(doc)
    begin = text.index(start) + len(start)
    assert _listed(text[begin : text.index(end, begin)]) == collections.Counter(jobs)
