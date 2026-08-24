"""Run comment-ops' doctor cell-summary lookup over what the API can answer.

`shipmate doctor`'s declared environment set is the cell summaries of the plan
runs this head's own apply checks recorded, and one head's cells can come from
several runs. Both ways the download can be wrong are silent: a run left out
drops the environments only that run planned, and a cell replanned in a later
run has the same artifact name in both, so a shared download directory reports
whichever copy extraction order happened to leave behind.
"""

import json
import pathlib
import subprocess
import sys

import pytest
from _loader import ACTIONS, action_steps, usable_bash

_START = "--plan-runs"
_END = "plan_run_ids=$("


def _cells_block():
    """The plan-record read through the line that publishes the id set."""
    step = next(s for s in action_steps("comment-ops") if s.get("id") == "gatherdoc")
    lines = (step["run"] or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    starts = [i for i, ln in enumerate(lines) if _START in ln]
    ends = [i for i, ln in enumerate(lines) if _END in ln]
    assert len(starts) == 1 and len(ends) == 1, f"{len(starts)} reads, {len(ends)} publications"
    block = lines[starts[0] : ends[0] + 1]
    # A slice that missed either half would assert nothing.
    assert any("gh run download" in ln for ln in block), "extracted block downloads nothing"
    assert any("unlink()" in ln for ln in block), "extracted block selects nothing"
    return "\n".join(block)


bash_only = pytest.mark.skipif(usable_bash() is None, reason="no working bash on PATH")

_APP_ID = "4326562"
#: The same cell planned twice on this head (the newer check names run 1290),
#: plus a cell only the older run planned.
_CHECK_RUNS = [
    ("apply / stacks/app / dev-eu", 1, "1281"),
    ("apply / stacks/app / dev-eu", 2, "1290"),
    ("apply / stacks/db / dev-us", 3, "1281"),
]
#: What each run's `cell-summary.<env>.<slug>` artifact holds. The two copies of
#: the replanned cell differ only in the plan they describe, exactly as two runs
#: of the same cell do.
_ARTIFACTS = {
    "1281": [
        ("cell-summary.dev-eu.stacks-app", "stacks/app", "dev-eu", 1),
        ("cell-summary.dev-us.stacks-db", "stacks/db", "dev-us", 7),
    ],
    "1290": [("cell-summary.dev-eu.stacks-app", "stacks/app", "dev-eu", 2)],
}


def _run_block(tmp_path):
    """The surviving cell summaries, as {artifact name: (run dir, add count)},
    after running the block with `gh run download` stubbed to the fixture."""
    for run, artifacts in _ARTIFACTS.items():
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
                    "external_id": json.dumps({"fingerprint": "a" * 64, "plan_run": run}),
                    "app": {"id": int(_APP_ID)},
                    "app_slug": "shipmate",
                    "app_id": int(_APP_ID),
                }
            )
            + "\n"
            for name, cid, run in _CHECK_RUNS
        ),
        encoding="utf-8",
        newline="\n",
    )
    harness = (
        "set -euo pipefail\n"
        # `tr -d '\r'`: a Windows python translates a printed newline to CRLF,
        # which a runner's python does not -- without it the id list carries a
        # CR into the download loop here and nowhere in production. `pipefail`
        # above keeps a failing python a failing pipeline.
        f"python3() {{ '{pathlib.Path(sys.executable).as_posix()}' \"$@\" | tr -d '\\r' ; }}\n"
        # `gh run download <rid> ... -D <dir>`: the real command creates the
        # target directory and extracts every matching artifact into it.
        'gh() { local rid="$3" dir="" ;'
        ' while [ $# -gt 0 ] ; do if [ "$1" = "-D" ] ; then dir="$2" ; fi ; shift ; done ;'
        ' [ -d "artifacts/$rid" ] || return 1 ;'
        ' mkdir -p "$dir" ; cp -r "artifacts/$rid/." "$dir/" ; }\n'
    ) + _cells_block()
    script = tmp_path / "cells.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    r = subprocess.run(
        [usable_bash(), str(script)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "GITHUB_ACTION_PATH": str(ACTIONS / "comment-ops"),
            "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
            "GITHUB_REPOSITORY": "o/r",
            "SHIPMATE_APP_ID": _APP_ID,
            "PATH": "/usr/bin:/bin",
        },
        timeout=60,
    )
    assert r.returncode == 0, f"step died: {r.stdout!r} {r.stderr!r}"
    return {
        cj.parent.name: (cj.parent.parent.name, json.loads(cj.read_text(encoding="utf-8"))["add"])
        for cj in (tmp_path / "doctor-cells").glob("*/**/cell.json")
    }


@bash_only
def test_every_plan_run_the_head_recorded_is_downloaded(tmp_path):
    """A cell planned in an earlier run than its siblings is still a declared
    environment: downloading only one run's summaries hides every environment
    only that run planned, and doctor then probes a subset of the truth while
    reporting no problem with the rest."""
    assert set(_run_block(tmp_path)) == {
        "cell-summary.dev-eu.stacks-app",
        "cell-summary.dev-us.stacks-db",
    }


@bash_only
def test_a_replanned_cell_resolves_to_the_run_its_newest_check_names(tmp_path):
    """Two runs' copies of one cell carry the same artifact name, so the copy
    doctor reports has to be chosen rather than left to extraction order: the
    run named by that cell's own newest apply check wins."""
    assert _run_block(tmp_path)["cell-summary.dev-eu.stacks-app"] == ("1290", 2)
