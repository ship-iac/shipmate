"""`scripts/apply-complete own` and `actions/cell-complete`, which a cell's `save` job runs to
complete that cell's apply checks `success` once its `apply` job succeeded and its state step
saved the state.

Threat model: an accidental edit that writes another outcome than `success`, drops the job link
or the record title, accepts check ids or a job id `apply` (pull-request code) could have
shaped, or lets a failed write end the job green. Whole values against hand-written constants.
"""

import io
import json
import os

import _shipmate
import pytest
from _loader import (
    ENGINE,
    action_steps,
    bash_only,
    load_script,
    local_action,
    run_with_gh_recorder,
)

ac = load_script("apply-complete")
pc = load_script("patch-checks")

RUN_URL = "https://github.com/acme/demo/actions/runs/999"
JOB = "140089314582"
SUCCESS = {"status": "completed", "conclusion": "success", "details_url": f"{RUN_URL}/job/{JOB}"}
IDS_REFUSAL = "::error::the cell's apply-check ids are not a non-empty JSON list of ids"
ARABIC_INDIC_12 = chr(0x661) + chr(0x662)


@pytest.fixture(autouse=True)
def _run_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")


def _own(capsys, ids, job=JOB):
    ac.main(["own", ids, job])
    out, err = capsys.readouterr()
    assert err == ""
    return [json.loads(line) for line in out.splitlines()]


def test_own_prints_one_success_entry_per_id_linked_to_the_apply_job(capsys):
    """`toJSON` pretty-prints the list, so it arrives multi-line. A `None` title restores the
    record title. Mutations: link the run page without `/job/<id>`; print `conclusion:
    neutral`."""
    assert _own(capsys, "[\n  11,\n  12\n]") == [
        {"id": 11, "body": SUCCESS, "title": None},
        {"id": 12, "body": SUCCESS, "title": None},
    ]


@pytest.mark.parametrize(
    "ids", ["", "null", "[]", "{}", "11", '["11"]', "[true]", "[0]", "[11, null]"]
)
def test_own_refuses_ids_that_are_not_a_non_empty_list_of_ids(capsys, ids):
    """A cell with no `by_wave` entry passes `null`. Mutations: drop the `ids and` clause
    (`[]` writes nothing and passes); drop `i > 0` (`[0]` passes)."""
    with pytest.raises(SystemExit) as exc:
        ac.main(["own", ids, JOB])
    assert (str(exc.value), capsys.readouterr().out) == (IDS_REFUSAL, "")


@pytest.mark.parametrize("job", ["", "12a", "1 2", "-1", ARABIC_INDIC_12, "1" * 21])
def test_own_refuses_a_job_id_that_is_not_1_to_20_digits(capsys, job):
    """`apply` runs pull-request code, and the id is bound into a link. Mutation:
    `job_id.isdigit()` for `is_run_id` (Arabic-Indic digits and 21 digits pass)."""
    with pytest.raises(SystemExit) as exc:
        ac.main(["own", "[11]", job])
    assert (str(exc.value), capsys.readouterr().out) == (
        "::error::the apply job id is not 1 to 20 digits",
        "",
    )


@pytest.fixture
def patch(monkeypatch):
    """`patch-checks` in this process with retry sleeps skipped. Every check reads titled
    `Failed: boom` with the record title `+1 ~0 -0`; pull request 7 reads merged. Returns
    `(gets, patches, fail)`: the GET paths, each PATCH's `(path, body)`, and the check ids whose
    PATCH fails."""
    monkeypatch.setattr(_shipmate, "_sleep", lambda _s: None)
    gets, patches, fail = [], [], set()
    check = {
        "status": "queued",
        "external_id": json.dumps({"plan_run": "1", "title": "+1 ~0 -0"}),
        "output": {"title": "Failed: boom", "summary": "plan"},
    }

    def run(args, secrets=(), stdin=None):
        if len(args) == 3:
            gets.append(args[2])
            return json.dumps({"merged": True} if "/pulls/" in args[2] else check)
        patches.append((args[4], json.loads(stdin)))
        if args[4].rsplit("/", 1)[1] in fail:
            raise SystemExit(f"command failed (1): {' '.join(args)}\ngh: Forbidden (HTTP 403)")
        return "{}"

    monkeypatch.setattr(pc, "run", run)
    return gets, patches, fail


def _write(monkeypatch, capsys, entries):
    lines = "".join(json.dumps(e) + "\n" for e in entries)
    monkeypatch.setattr(pc.sys, "stdin", io.StringIO(lines))
    pc.main()
    capsys.readouterr()


RESTORED = {**SUCCESS, "output": {"title": "+1 ~0 -0", "summary": "plan"}}


@pytest.mark.parametrize(
    ("deploy", "pr", "attempt", "pr_read"),
    [
        ("true", "7", "1", []),
        ("false", "7", "1", ["repos/acme/demo/pulls/7"]),
        ("false", "7", "2", []),
        ("false", "", "1", []),
    ],
    ids=["deploy", "merged-pull-request", "re-run", "no-pull-request"],
)
def test_the_entries_complete_each_check_with_its_record_title_on_every_path(
    monkeypatch, capsys, patch, deploy, pr, attempt, pr_read
):
    """A merge deploy, a pre-merge run whose pull request merged, and a pre-merge re-run each
    still write a `success`, replacing the `Failed:` title with the record title. Mutations:
    `own` prints entries with no `title` key (no read, the `Failed:` title stays); `own` prints
    `status: in_progress` (a merged or re-run pre-merge run drops it)."""
    gets, patches, _ = patch
    monkeypatch.setenv("SHIPMATE_DEPLOY", deploy)
    monkeypatch.setenv("SHIPMATE_PR_NUMBER", pr)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
    _write(monkeypatch, capsys, _own(capsys, "[11, 12]"))
    checks = ["repos/acme/demo/check-runs/11", "repos/acme/demo/check-runs/12"]
    assert (gets, patches) == (pr_read + checks, [(c, RESTORED) for c in checks])


def test_a_write_that_fails_stops_and_fails(monkeypatch, capsys, patch):
    """Mutation: `continue` past a failed PATCH in `patch-checks.write` (12 is written, no
    failure)."""
    _, patches, fail = patch
    fail.add("11")
    monkeypatch.setenv("SHIPMATE_DEPLOY", "true")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    entries = _own(capsys, "[11, 12]")
    with pytest.raises(SystemExit) as exc:
        _write(monkeypatch, capsys, entries)
    assert str(exc.value).startswith("::error::failed to write apply check 11\n")
    assert [p for p, _ in patches] == ["repos/acme/demo/check-runs/11"]


_WITH_KEY = {"app-id": "${{ inputs.app-id }}", "private-key": "${{ inputs.private-key }}"}

#: The action's steps, whole: the key check, the decision, the mint, the write. A missing key or
#: a refused decision fails before the mint.
STEPS = [
    {
        "name": "Verify the App key arrived",
        "uses": local_action("verify-app-key"),
        "with": _WITH_KEY,
    },
    {
        "name": "Decide the cell's writes",
        "shell": "bash",
        "env": {
            "CHECK_IDS": "${{ inputs.check-ids }}",
            "APPLY_JOB_ID": "${{ inputs.apply-job-id }}",
        },
        "run": (
            "set -euo pipefail\n"
            'python3 "$GITHUB_ACTION_PATH/../../scripts/apply-complete" own "$CHECK_IDS" '
            '"$APPLY_JOB_ID" \\\n'
            '  > "$RUNNER_TEMP/cell-writes.jsonl"\n'
        ),
    },
    {
        "name": "Mint App installation token",
        "id": "token",
        "uses": "actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510",
        "with": {
            "client-id": "${{ inputs.app-id }}",
            "private-key": "${{ inputs.private-key }}",
            "permission-checks": "write",
            "permission-pull-requests": "read",
        },
    },
    {
        "name": "Write the cell's apply checks",
        "shell": "bash",
        "env": {
            "GH_TOKEN": "${{ steps.token.outputs.token }}",
            "SHIPMATE_DEPLOY": "${{ inputs.deploy }}",
            "SHIPMATE_PR_NUMBER": "${{ inputs.pr-number }}",
        },
        "run": (
            "set -euo pipefail\n"
            'python3 "$GITHUB_ACTION_PATH/../../scripts/patch-checks" '
            '< "$RUNNER_TEMP/cell-writes.jsonl"\n'
        ),
    },
]


def test_the_steps_are_exactly_these():
    """Mutations: drop `SHIPMATE_PR_NUMBER` from the write step; write with `github.token`; add
    `continue-on-error: true` to the write; pass the job id as the ids; move the mint first."""
    assert action_steps("cell-complete") == STEPS


@bash_only
def test_the_action_steps_fail_on_refused_ids_and_on_a_failed_write(tmp_path):
    """The two `run:` bodies as the runner runs them. Mutation: append `|| true` to the write's
    `patch-checks` line (and to `STEPS`)."""
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "GITHUB_REPOSITORY": "acme/demo",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "999",
        "GITHUB_RUN_ATTEMPT": "1",
        "SHIPMATE_DEPLOY": "true",
        "SHIPMATE_PR_NUMBER": "",
        "GITHUB_ACTION_PATH": (ENGINE / "actions" / "cell-complete").as_posix(),
        "RUNNER_TEMP": runner_temp.as_posix(),
    }
    decide, write = STEPS[1]["run"], STEPS[3]["run"]
    refused, _ = run_with_gh_recorder(
        tmp_path, decide, {**env, "CHECK_IDS": "null", "APPLY_JOB_ID": JOB}
    )
    assert (refused.returncode, refused.stderr.strip()) == (1, IDS_REFUSAL)
    decided, _ = run_with_gh_recorder(
        tmp_path, decide, {**env, "CHECK_IDS": "[11]", "APPLY_JOB_ID": JOB}
    )
    assert decided.returncode == 0, decided.stderr
    failed, calls = run_with_gh_recorder(tmp_path, write, env, write_rc=1, write_stderr="boom")
    assert (failed.returncode, calls) == (1, [["api", "repos/acme/demo/check-runs/11"]])
