"""Exercise deploy.yml's post-merge gate verdict over the env-level results.

The verdict is bash inside `.github/workflows/deploy.yml`, the one gate writer with no script
behind it, and it decides whether `shipmate / gate` greens on main. Every failure mode here is a
false green: a result string that should read as "deploy incomplete" but computes `success`
merges a pull request whose stacks were never applied. The block is extracted out of the YAML and
run, rather than asserted about as text, so the guard tracks behaviour and not phrasing.
"""

import pytest
from _loader import bash_only, run_step, workflow_yaml

_STEP = "Complete gate on the merged PR head SHA"
_WRITE_MARKER = 'gh api "repos/$GITHUB_REPOSITORY/statuses/'


def _jobs():
    return workflow_yaml("deploy.yml")["jobs"]


def _summary_step(name):
    found = [s for s in _jobs()["summary"]["steps"] if s.get("name") == name]
    assert len(found) == 1, f"deploy.yml summary job has {len(found)} steps named {name!r}"
    return found[0]


def _lf(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _gate_run():
    return _lf(_summary_step(_STEP)["run"])


def _verdict_block():
    """The gate step's bash up to, and not including, the status write: the HEAD_SHA guard and
    the concl/title decision, with nothing that calls out."""
    head, sep, _ = _gate_run().partition(_WRITE_MARKER)
    assert sep, f"gate step no longer computes concl/title before {_WRITE_MARKER!r}"
    return head


def _run(tmp_path, results, head_sha="deadbeef"):
    script = _verdict_block() + '\nprintf "%s|%s\\n" "$concl" "$title"\n'
    return run_step(
        tmp_path, script, {"RESULTS": results, "HEAD_SHA": head_sha, "PATH": "/usr/bin:/bin"}
    )


def _verdict_code():
    """The verdict block's code lines. Comments are dropped, because the block documents the
    pipeline it replaced and a guard reading that would fail on prose."""
    lines = _verdict_block().splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))


def test_verdict_scan_is_pipeline_free():
    """`... | grep -q` exits on its first match, so the writer ahead of it takes SIGPIPE and the
    pipeline reports 141 under `pipefail`, which the `if` reads as "no bad result found" and
    greens the gate on a failed deploy."""
    assert "grep" not in _verdict_code(), (
        "the gate verdict scans results through a pipeline again — a reader that "
        "exits early inverts the verdict (SIGPIPE 141 reads as all-clean)"
    )


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
        # No results at all: detect ran and every env-level job vanished. Nothing was applied,
        # so the gate must not green.
        ("", "failure"),
    ],
)
def test_verdict(tmp_path, results, expected):
    r = _run(tmp_path, results)
    assert r.returncode == 0, r.stderr
    concl, _, title = r.stdout.strip().partition("|")
    assert concl == expected, f"{results!r} -> {concl} ({title}), expected {expected}"
    assert title == ("all env-levels applied" if expected == "success" else "deploy incomplete")


_HEAD = "a" * 40


def _status_argv(state, title):
    return [
        "api",
        f"repos/acme/demo/statuses/{_HEAD}",
        "-f",
        f"state={state}",
        "-f",
        "context=shipmate / gate",
        "-f",
        f"description={title} (deploy env-level applies completed)",
        "-f",
        "target_url=https://example.invalid/acme/demo/actions/runs/999",
    ]


#: The whole `gh` argv each verdict sends, hand-written: a value read back from the step passes
#: whatever the step says. Pins the endpoint, the method (POST, implied by the fields) and every
#: field. The values match the env `test_status_body` supplies.
_POSTED = {
    "success": _status_argv("success", "all env-levels applied"),
    "failure,success": _status_argv("failure", "deploy incomplete"),
}


@bash_only
@pytest.mark.parametrize("results", sorted(_POSTED))
def test_status_body(tmp_path, results):
    """The whole step runs with `gh` stubbed to record its argv. Mutations: drop the description
    suffix; post `context="shipmate/gate"`; post `-F` for any field; post to `$GITHUB_SHA`
    instead of `$HEAD_SHA`; add `-X GET`."""
    wrote = tmp_path / "wrote"
    stub = 'gh() { printf \'%s\\n\' "$@" > "$WROTE" ; }\n'
    r = run_step(
        tmp_path,
        stub + _gate_run(),
        {
            "RESULTS": results,
            "HEAD_SHA": _HEAD,
            # A different SHA, so posting to the merge commit instead of the head reddens.
            "GITHUB_SHA": "b" * 40,
            "GITHUB_REPOSITORY": "acme/demo",
            "GITHUB_SERVER_URL": "https://example.invalid",
            "GITHUB_RUN_ID": "999",
            "WROTE": str(wrote),
            "PATH": "/usr/bin:/bin",
        },
    )
    assert r.returncode == 0, r.stderr
    assert wrote.read_text(encoding="utf-8").splitlines() == _POSTED[results]


@bash_only
def test_missing_head_sha_fails_loud(tmp_path):
    """detect died, so no head SHA: fail rather than post a gate on nothing."""
    r = _run(tmp_path, "success", head_sha="")
    assert r.returncode == 1, r.stdout
    assert "::error::" in r.stdout
