"""Run comment-ops' doctor cell-summary lookup over what the API can answer.

`shipmate doctor`'s declared environment set is the cell summaries of the plan runs this head's
own apply checks recorded, and one head's cells can come from several runs. A run left out
drops the environments only that run planned, silently.
"""

import json
import pathlib
import sys

from _loader import ACTIONS, bash_only, run_step, step_by

_START = "--plan-runs"
_END = "plan_run_ids=$("


def _cells_block():
    """The plan-record read through the line that publishes the id set."""
    step = step_by("comment-ops", id="gatherdoc")
    lines = (step["run"] or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    starts = [i for i, ln in enumerate(lines) if _START in ln]
    ends = [i for i, ln in enumerate(lines) if _END in ln]
    assert len(starts) == 1 and len(ends) == 1, f"{len(starts)} reads, {len(ends)} publications"
    block = lines[starts[0] : ends[0] + 1]
    # A slice that missed the loop would assert nothing.
    assert any("gh run download" in ln for ln in block), "extracted block downloads nothing"
    return "\n".join(block)


_APP_ID = "4326562"
#: (check name, check-run id, `external_id` record). The same cell planned twice on this head,
#: where the newer check names run 1290; a cell only the older run planned; and one whose newest
#: check carries a legacy bare-hex record, naming no plan run.
_CHECK_RUNS = [
    ("apply / stacks/app / dev-eu", 1, json.dumps({"fingerprint": "a" * 64, "plan_run": "1281"})),
    ("apply / stacks/app / dev-eu", 2, json.dumps({"fingerprint": "a" * 64, "plan_run": "1290"})),
    ("apply / stacks/db / dev-us", 3, json.dumps({"fingerprint": "a" * 64, "plan_run": "1281"})),
    ("apply / stacks/cache / dev-ap", 4, "b" * 64),
]
#: What each run's `cell-summary.<env>.<slug>` artifact holds. The two copies of the replanned
#: cell differ only in the plan they describe, exactly as two runs of the same cell do.
_ARTIFACTS = {
    "1281": [
        ("cell-summary.dev-eu.stacks-app", "stacks/app", "dev-eu", 1),
        ("cell-summary.dev-us.stacks-db", "stacks/db", "dev-us", 7),
        ("cell-summary.dev-ap.stacks-cache", "stacks/cache", "dev-ap", 3),
    ],
    "1290": [("cell-summary.dev-eu.stacks-app", "stacks/app", "dev-eu", 2)],
}


def _run_block(tmp_path, undownloadable=()):
    """(surviving summaries, step output) after running the block with `gh run download` stubbed
    to the fixture.

    Each surviving summary is `(artifact name, run directory, add count)`, sorted: a mapping
    keyed on the artifact name would collapse two runs' copies of one cell into the one the glob
    happened to yield last. Runs named in `undownloadable` have no artifacts, which is what the
    stub, and the real `gh`, reports as a failed download."""
    for run, artifacts in _ARTIFACTS.items():
        if run in undownloadable:
            continue
        for name, stack_path, env, add in artifacts:
            d = tmp_path / "artifacts" / run / name
            d.mkdir(parents=True)
            (d / "cell.json").write_text(
                json.dumps({"stack_path": stack_path, "environment": env, "add": add}),
                encoding="utf-8",
            )
    (tmp_path / "check-runs.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "id": cid,
                    "name": name,
                    "status": "completed",
                    "started_at": "2026-08-24T00:00:00Z",
                    "external_id": record,
                    "app": {"id": int(_APP_ID)},
                    "app_slug": "shipmate",
                    "app_id": int(_APP_ID),
                }
            )
            + "\n"
            for name, cid, record in _CHECK_RUNS
        ),
        encoding="utf-8",
        newline="\n",
    )
    harness = (
        "set -euo pipefail\n"
        # `tr -d '\r'`: a Windows python translates a printed newline to CRLF, which a runner's
        # python does not, so without it the id list carries a CR into the download loop here and
        # nowhere in production. `pipefail` above keeps a failing python a failing pipeline.
        f"python3() {{ '{pathlib.Path(sys.executable).as_posix()}' \"$@\" | tr -d '\\r' ; }}\n"
        # `gh run download <rid> ... -D <dir>`: the real command creates the target directory and
        # extracts every matching artifact into it.
        'gh() { local rid="$3" dir="" ;'
        ' while [ $# -gt 0 ] ; do if [ "$1" = "-D" ] ; then dir="$2" ; fi ; shift ; done ;'
        ' [ -d "artifacts/$rid" ] || return 1 ;'
        ' mkdir -p "$dir" ; cp -r "artifacts/$rid/." "$dir/" ; }\n'
    ) + _cells_block()
    r = run_step(
        tmp_path,
        harness,
        {
            "GITHUB_ACTION_PATH": str(ACTIONS / "comment-ops"),
            "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
            "GITHUB_REPOSITORY": "o/r",
            "SHIPMATE_APP_ID": _APP_ID,
            "PATH": "/usr/bin:/bin",
        },
        timeout=60,
    )
    assert r.returncode == 0, f"step died: {r.stdout!r} {r.stderr!r}"
    return sorted(
        (
            cj.parent.name,
            cj.parent.parent.name,
            json.loads(cj.read_text(encoding="utf-8"))["add"],
        )
        for cj in (tmp_path / "doctor-cells").glob("*/**/cell.json")
    ), r.stdout


@bash_only
def test_every_plan_run_the_head_recorded_is_downloaded(tmp_path):
    """A cell planned in an earlier run than its siblings is still a declared
    environment: downloading only one run's summaries hides every environment
    only that run planned, and doctor then probes a subset of the truth while
    reporting no problem with the rest. Both runs' copies of the replanned cell
    survive; downloading into one shared directory lets one overwrite the other."""
    assert _run_block(tmp_path)[0] == [
        ("cell-summary.dev-ap.stacks-cache", "1281", 3),
        ("cell-summary.dev-eu.stacks-app", "1281", 1),
        ("cell-summary.dev-eu.stacks-app", "1290", 2),
        ("cell-summary.dev-us.stacks-db", "1281", 7),
    ]


@bash_only
def test_a_run_whose_summaries_cannot_be_downloaded_is_a_warning_not_a_failure(tmp_path):
    """doctor degrades rather than fails: a diagnostics command that dies over
    one missing artifact reports nothing at all, so the run is warned about and
    every other run's environments still reach the probes. Without the
    `|| echo "::warning::..."` branch the step dies on the first failed download."""
    cells, out = _run_block(tmp_path, undownloadable=("1281",))
    assert cells == [("cell-summary.dev-eu.stacks-app", "1290", 2)]
    assert "::warning::the cell summaries of plan run 1281 could not be downloaded" in out
