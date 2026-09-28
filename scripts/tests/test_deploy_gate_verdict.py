"""Exercise deploy.yml's post-merge gate verdict over the env-level results.

The verdict is bash inside `.github/workflows/deploy.yml`, the one gate writer with no script
behind it, and it decides whether `shipmate / gate` greens on main. Every failure mode here is a
false green: a result string that should read as "deploy incomplete" but computes `success`
merges a pull request whose stacks were never applied. The block is extracted out of the YAML and
run, rather than asserted about as text, so the guard tracks behaviour and not phrasing.

The `summary` job's two Slack steps are pinned here too: the failure notice, and the step that
refuses `SHIPMATE_SLACK_WEBHOOK` set as a variable. So is the rule that no other deploy.yml job
names the webhook, which keeps it off apply-env-level.yml's wave jobs.
"""

import pytest
import yaml
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
        f"description={title} — deploy env-level applies completed",
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


_SLACK_STEP = "Slack on failed deploy (outranks drift)"

#: The Slack step, whole and hand-written: a value read back from the file passes whatever the
#: file says, and a partial comparison passes an added key such as `continue-on-error`.
_SLACK_IF = (
    "${{ always() && (contains(join(needs.*.result, ','), 'failure') || "
    "contains(join(needs.*.result, ','), 'cancelled')) }}"
)
_SLACK_ENV = {"SLACK": "${{ secrets.SHIPMATE_SLACK_WEBHOOK }}"}
_SLACK_RUN = (
    "set -euo pipefail\n"
    "# The `secrets` context is not available in a step's `if:`.\n"
    '[ -n "$SLACK" ] || exit 0\n'
    "python3 -c \"import json;open('p.json','w').write(json.dumps({'text':':rotating_light: "
    "shipmate deploy failed on main — a wave apply failed or was cancelled.'}))\"\n"
    "curl -sS --fail-with-body -X POST -H 'Content-Type: application/json' "
    '--data @p.json "$SLACK"\n'
)
_SLACK_STEP_SPEC = {"name": _SLACK_STEP, "if": _SLACK_IF, "env": _SLACK_ENV, "run": _SLACK_RUN}


def test_slack_step_reads_the_engine_secret_and_fails_on_a_rejected_post():
    """Mutations: read `vars.SLACK_WEBHOOK` again; restore `&& vars.SLACK_WEBHOOK != ''` in the
    `if:`; delete the empty-webhook exit; drop `--fail-with-body`, which lets a revoked
    webhook's 4xx exit 0; add `continue-on-error: true`, which greens a rejected post; restore
    `>/dev/null`, which discards the body `--fail-with-body` prints."""
    step = _summary_step(_SLACK_STEP)
    assert {**step, "if": " ".join(step["if"].split()), "run": _lf(step["run"])} == _SLACK_STEP_SPEC


@bash_only
def test_slack_step_with_no_webhook_calls_nothing(tmp_path):
    """No webhook set, failed wave: the step exits 0 before it writes a payload or posts.
    Mutation: delete `[ -n "$SLACK" ] || exit 0`, and the stubbed curl records a call."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    marker = tmp_path / "called"
    for name in ("curl", "python3"):
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho {name} >> "{marker.as_posix()}"\nexit 0\n')
        stub.chmod(0o755)
    r = run_step(
        tmp_path,
        _lf(_summary_step(_SLACK_STEP)["run"]),
        {"SLACK": "", "PATH": f"{stubs.as_posix()}:/usr/bin:/bin"},
    )
    assert r.returncode == 0, r.stderr
    assert not marker.exists(), f"called with no webhook: {marker.read_text()}"


_REFUSE_STEP = "Refuse a Slack webhook set as a variable"

#: Hand-written, and the same text as scripts/env-inject's refusal for this name.
_REFUSE_STEP_SPEC = {
    "name": _REFUSE_STEP,
    "if": "${{ always() && vars.SHIPMATE_SLACK_WEBHOOK != '' }}",
    "run": (
        'echo "::error::SHIPMATE_SLACK_WEBHOOK is set as a GitHub variable, and it must be a '
        "secret on the shipmate-engine environment. Its value is readable by anyone who can see "
        "the repository. Delete the variable, rotate the webhook, and run gh secret set "
        'SHIPMATE_SLACK_WEBHOOK --env shipmate-engine."\n'
        "exit 1\n"
    ),
}


def test_summary_refuses_the_webhook_set_as_a_variable_last():
    """No cell binds `shipmate-engine`, so env-inject never sees a variable set there; this step
    does. Last, so it never holds back the gate write. Mutations: delete the step; `!= ''` ->
    `== ''`; drop `always()`, which skips it after a failed gate write; move it above the gate."""
    step = _summary_step(_REFUSE_STEP)
    normalized = {**step, "if": " ".join(step["if"].split()), "run": _lf(step["run"])}
    assert normalized == _REFUSE_STEP_SPEC
    assert _jobs()["summary"]["steps"][-1]["name"] == _REFUSE_STEP


def test_only_the_summary_job_names_the_webhook():
    """Mutation: add `SHIPMATE_SLACK_WEBHOOK: ${{ secrets.SHIPMATE_SLACK_WEBHOOK }}` to
    `envlevel0`'s `secrets:`, which forwards it to apply-env-level.yml's wave jobs."""
    holders = [j for j, job in _jobs().items() if "SHIPMATE_SLACK_WEBHOOK" in yaml.safe_dump(job)]
    assert holders == ["summary"]
