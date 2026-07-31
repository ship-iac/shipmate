"""Exercise deploy.yml's post-merge gate verdict over the env-level results.

The verdict is bash inside `.github/workflows/deploy.yml` — the one gate writer
with no script behind it — and it decides whether `shipmate / gate` greens on
main. Every failure mode here is a false green: a result string that should read
as "deploy incomplete" but computes `success` merges a PR whose stacks were
never applied. The block is extracted out of the YAML and run, rather than
asserted about as text, so the guard tracks behaviour and not phrasing.
"""

import subprocess

import pytest
import yaml
from _loader import WORKFLOWS, usable_bash

_STEP = "Complete gate on the merged PR head SHA"
_PY_MARKER = 'python3 - "$concl" "$title"'


def _gate_run():
    spec = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text(encoding="utf-8"))
    steps = spec["jobs"]["summary"]["steps"]
    found = [s for s in steps if s.get("name") == _STEP]
    assert len(found) == 1, f"deploy.yml summary job has {len(found)} steps named {_STEP!r}"
    return found[0]["run"].replace("\r\n", "\n").replace("\r", "\n")


def _verdict_block():
    """The gate step's bash up to (not including) the status-body heredoc: the
    HEAD_SHA guard and the concl/title decision, with nothing that calls out."""
    head, sep, _ = _gate_run().partition(_PY_MARKER)
    assert sep, f"gate step no longer computes concl/title before {_PY_MARKER!r}"
    return head


def _run(results, head_sha="deadbeef"):
    script = _verdict_block() + '\nprintf "%s|%s\\n" "$concl" "$title"\n'
    return subprocess.run(
        [usable_bash(), "-c", script],
        capture_output=True,
        text=True,
        env={"RESULTS": results, "HEAD_SHA": head_sha, "PATH": "/usr/bin:/bin"},
    )


def _verdict_code():
    """The verdict block's code lines. Comments are dropped: the block documents
    the pipeline it replaced, and a guard reading that would fail on prose."""
    lines = _verdict_block().splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))


def test_verdict_scan_is_pipeline_free():
    """`... | grep -q` exits on its first match, so the writer ahead of it takes
    SIGPIPE and the pipeline reports 141 under `pipefail` — which the `if` reads
    as "no bad result found" and greens the gate on a failed deploy."""
    assert "grep" not in _verdict_code(), (
        "the gate verdict scans results through a pipeline again — a reader that "
        "exits early inverts the verdict (SIGPIPE 141 reads as all-clean)"
    )


bash_only = pytest.mark.skipif(usable_bash() is None, reason="no working bash on PATH")


@bash_only
@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ("success", "success"),
        ("success,skipped", "success"),
        ("skipped,skipped,skipped,skipped", "success"),
        ("success,failure", "failure"),
        ("failure,success", "failure"),
        ("success,cancelled", "failure"),
        ("skipped,timed_out", "failure"),
        ("success,skipped,neutral", "failure"),
        # No results at all: detect ran, every env-level job vanished. Nothing
        # was applied, so the gate must not green.
        ("", "failure"),
    ],
)
def test_verdict(results, expected):
    r = _run(results)
    assert r.returncode == 0, r.stderr
    concl, _, title = r.stdout.strip().partition("|")
    assert concl == expected, f"{results!r} -> {concl} ({title}), expected {expected}"
    assert title == ("all env-levels applied" if expected == "success" else "deploy incomplete")


@bash_only
def test_missing_head_sha_fails_loud():
    """detect died -> no head SHA -> fail rather than post a gate on nothing."""
    r = _run("success", head_sha="")
    assert r.returncode == 1, r.stdout
    assert "::error::" in r.stdout
