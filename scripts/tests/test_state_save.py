"""`scripts/state-save` and `actions/state-save`: one cell's `save` job, which runs no
pull-request code, saves the state its own attempt applied or looks up an earlier attempt's.

Threat model: an accidental edit that (a) saves bytes the cell did not produce under its
prefix (an artifact taken without comparing its digest, a key built from the bundle rather
than the decision), (b) writes outside the stack (the bundle's path unchecked), (c) passes the
job without an exact-key hit, so the cell reads saved over a state no restore finds, (d) lets
a re-run save an earlier attempt's state late, or (e) reads an apply that may have run as one
that did not (an empty output, a carried row, an unsaved earlier attempt). A cell sealing other
bytes under its own digest is out of scope: its code already controls its own state.

The gh calls are stubbed: `listed_jobs` for the run's jobs, `run` for the artifact listing,
`_download` for each archive.
"""

import hashlib
import io
import json
import os
import re
import urllib.parse
import zipfile

import pytest
from _loader import (
    ENGINE,
    action_yaml,
    bash_only,
    github_outputs,
    load_script,
    local_action,
    run_step,
)

ss = load_script("state-save")

PATH = "stacks/app/.state/dev-eu/terraform.tfstate"
CIPHER = b"SHPS1" + b"\x00\x01\xff" * 30
LEVEL = "apply / run / L0"
APPLY = f"{LEVEL} / dev-eu / stacks/app / apply"
SAVE = f"{LEVEL} / dev-eu / stacks/app / save"
STARTED = [
    {"name": "Pre-apply check", "conclusion": "success"},
    {"name": "Apply the cell", "conclusion": "success"},
]
NOT_STARTED = [
    {"name": "Pre-apply check", "conclusion": "failure"},
    {"name": "Apply the cell", "conclusion": "skipped"},
]
OWN_ID = 900


def _bundle(path=PATH, cipher=CIPHER):
    return b"shipmate-state-bundle-v1\n" + path.encode() + b"\n" + cipher


def _zip(bundle, member="state.bundle"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, bundle)
    return buf.getvalue()


def _digest(bundle):
    return hashlib.sha256(bundle).hexdigest()


DIGEST = _digest(_bundle())


def _apply(attempt, steps=STARTED, *, started_at=None, conclusion="success"):
    return {
        "id": 100 + attempt,
        "run_attempt": attempt,
        "name": APPLY,
        "status": "completed",
        "conclusion": conclusion,
        "started_at": started_at or f"2026-11-17T19:4{attempt}:00Z",
        "steps": steps,
    }


def _carried(attempt, source):
    """An `apply` row a re-run lists again under `attempt` without running it: the source
    row's name and start, no steps."""
    return {**source, "id": 100 + attempt, "run_attempt": attempt, "steps": []}


def _save(attempt, conclusion):
    return {
        "id": OWN_ID if conclusion is None else 200 + attempt,
        "run_attempt": attempt,
        "name": SAVE,
        "status": "in_progress" if conclusion is None else "completed",
        "conclusion": conclusion,
        "started_at": f"2026-11-17T19:5{attempt}:00Z",
        "steps": [],
    }


def _keys(*attempts):
    return " and ".join(
        f"state/stacks-app/dev-eu/77-{k} or state/stacks-app/dev-eu/77-{k}-r1" for k in attempts
    )


#: `ss.HOSTED_ONLY` written out, so a reworded refusal reddens the two runner tests.
_HOSTED_ONLY = (
    "the save job runs only on GitHub-hosted runners; a reused self-hosted runner could alter "
    "what it saves. Use a GitHub-hosted runs_on or a remote backend"
)


def _refusal(reason, *attempts):
    return (
        f"::error::{reason}, so the state of dev-eu / stacks/app is not known saved under "
        f"{_keys(*attempts)}. {ss.REMEDY}"
    )


class _Job:
    """`main` with gh stubbed. `jobs` is the run's listing (None: it failed), `artifacts` the
    artifact listing, `archives` maps an id to its zip. `calls` records every gh call."""

    def __init__(
        self,
        monkeypatch,
        tmp_path,
        *,
        state,
        attempt=1,
        jobs=None,
        artifacts=(),
        archives=None,
        short=False,
        **env,
    ):
        self.calls = []
        self.out = tmp_path / "out"
        self.out.write_text("", encoding="utf-8")
        self.ws = tmp_path / "ws"
        self.ws.mkdir()
        base = {
            "RUNNER_ENVIRONMENT": "github-hosted",
            "STACK": "stacks/app",
            "ENV_NAME": "dev-eu",
            "STATE": state,
            "JOB_ID": str(OWN_ID),
            "GITHUB_RUN_ATTEMPT": str(attempt),
            "GITHUB_RUN_ID": "77",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_WORKSPACE": str(self.ws),
            "GITHUB_OUTPUT": str(self.out),
        }
        for k, v in {**base, **env}.items():
            monkeypatch.setenv(k, v)

        def listed(repo, run_id, run=None, *, counted=False, job_filter="latest"):
            self.calls.append(("jobs", repo, run_id, job_filter, counted))
            return None if counted and short else jobs

        def run(argv):
            self.calls.append(argv)
            return "".join(json.dumps(a) + "\n" for a in artifacts)

        def download(artifact_id):
            self.calls.append(artifact_id)
            return (archives or {})[artifact_id]

        monkeypatch.setattr(ss, "listed_jobs", listed)
        monkeypatch.setattr(ss, "run", run)
        monkeypatch.setattr(ss, "_download", download)

    def __call__(self):
        try:
            ss.main()
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, github_outputs(self.out)

    def artifact_calls(self):
        return [c for c in self.calls if not (isinstance(c, tuple) and c[0] == "jobs")]


def _name(attempt):
    return f"state.dev-eu.stacks-app.{attempt}"


def _one(attempt=1, bundle=None):
    """The listing and archives of one artifact of `attempt` holding `bundle`."""
    return {
        "artifacts": [{"id": 1, "name": _name(attempt)}],
        "archives": {1: _zip(_bundle() if bundle is None else bundle)},
    }


JOBS_LISTED = ("jobs", "o/r", "77", "all", True)


@pytest.mark.parametrize(
    ("artifacts", "archives", "ok"),
    [
        ([{"id": 1, "name": _name(1)}], {1: _zip(_bundle())}, True),
        ([{"id": 1, "name": _name(1)}], {1: _zip(_bundle(cipher=b"SHPS1planted" * 9))}, False),
        (
            [{"id": 1, "name": _name(1)}, {"id": 2, "name": _name(1)}],
            {1: _zip(_bundle(cipher=b"SHPS1planted" * 9)), 2: _zip(_bundle())},
            True,
        ),
        (
            [{"id": 1, "name": _name(1)}, {"id": 2, "name": _name(1)}],
            {1: _zip(_bundle()), 2: _zip(_bundle())},
            True,
        ),
        ([{"id": 3, "name": "state.dev-eu.other.1"}], {3: _zip(_bundle())}, False),
    ],
    ids=["one", "squatted", "planted-beside", "identical-copies", "other-name"],
)
def test_only_an_artifact_of_the_cells_name_whose_bytes_match_is_saved(
    monkeypatch, tmp_path, artifacts, archives, ok
):
    """Mutations: accept the first artifact without comparing its digest; refuse two identical
    copies; drop the name filter on the listing."""
    job = _Job(
        monkeypatch,
        tmp_path,
        state=f"{DIGEST} attempt=1",
        artifacts=artifacts,
        archives=archives,
    )
    code, out = job()
    if ok:
        assert (code, out) == (
            0,
            {"kind": "save", "attempt": "1", "path": PATH, "slug": "stacks-app"},
        )
        assert (job.ws / PATH).read_bytes() == CIPHER
    else:
        no_match = f"no artifact of this run named {_name(1)} holds the cell's state bundle"
        assert (code, out) == (_refusal(no_match, 1), {})
        assert not (job.ws / PATH).exists()


def test_the_listing_asks_for_the_cells_artifact_name(monkeypatch, tmp_path):
    """The name comes from `state-handoff.artifact_name`, the one derivation. Mutation: build
    the name with `-` joins."""
    job = _Job(monkeypatch, tmp_path, state=f"{DIGEST} attempt=1")
    job()
    assert job.calls == [
        [
            "gh",
            "api",
            "--paginate",
            "--jq",
            ".artifacts[]",
            f"repos/o/r/actions/runs/77/artifacts?name={_name(1)}&per_page=100",
        ]
    ]


def test_the_slug_comes_from_the_stack_path(monkeypatch, tmp_path):
    """The `apply` job runs pull-request code, so the slug is derived from the trusted stack,
    as `apply-cell` derives it. Mutation: drop the `/` to `-` replace."""
    path = "stacks/dns/.state/dev-eu/terraform.tfstate"
    bundle = _bundle(path=path)
    job = _Job(
        monkeypatch,
        tmp_path,
        state=f"{_digest(bundle)} attempt=1",
        artifacts=[{"id": 1, "name": "state.dev-eu.stacks-dns.1"}],
        archives={1: _zip(bundle)},
        STACK="stacks/dns",
    )
    assert job() == (0, {"kind": "save", "attempt": "1", "path": path, "slug": "stacks-dns"})
    assert job.artifact_calls()[0][-1] == (
        "repos/o/r/actions/runs/77/artifacts?name=state.dev-eu.stacks-dns.1&per_page=100"
    )


@pytest.mark.parametrize("stack", ["stacks/a b", "stacks/a$b", ""])
def test_a_stack_outside_the_slug_charset_refuses_before_any_call(monkeypatch, tmp_path, stack):
    """Mutation: drop the charset test."""
    job = _Job(monkeypatch, tmp_path, state=f"{DIGEST} attempt=1", STACK=stack, **_one())
    assert job() == ("::error::stack path produced invalid slug", {})
    assert job.calls == []


def test_a_name_holding_query_characters_lists_its_artifact(monkeypatch):
    """The stub answers as the API does, by the decoded `name` parameter. Mutation: put the
    name in the query unencoded (`+` decodes to a space, `&` and `#` cut it short)."""
    name = "state.dev-eu.stacks-a+b&c#d%e.2"
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")

    def server(argv):
        asked = urllib.parse.parse_qs(urllib.parse.urlsplit(argv[-1]).query).get("name")
        return json.dumps({"id": 1, "name": name}) if asked == [name] else ""

    monkeypatch.setattr(ss, "run", server)
    assert ss._artifacts(name) == [{"id": 1, "name": name}]


@pytest.mark.parametrize(
    "path", ["stacks/other/terraform.tfstate", "stacks/app/../other/x", "/etc/x"]
)
def test_a_forged_bundle_path_refuses_at_validate(monkeypatch, tmp_path, path):
    """Mutation: skip `validate`."""
    bundle = _bundle(path=path)
    job = _Job(monkeypatch, tmp_path, state=f"{_digest(bundle)} attempt=1", **_one(1, bundle))
    code, out = job()
    assert out == {}
    assert code.startswith("::error::stacks/app: the state path ")
    assert code.endswith(_refusal("", 1).removeprefix("::error::"))
    assert list(job.ws.rglob("*")) == []


def test_the_root_stacks_path_is_passed_on_as_the_restore_names_it(monkeypatch, tmp_path):
    """`actions/cache` versions an entry on its path string, and the restore passes
    `./terraform.tfstate` for the root stack `.`. Mutation: output `os.path.normpath(path)`."""
    bundle = _bundle(path="./terraform.tfstate")
    job = _Job(
        monkeypatch,
        tmp_path,
        state=f"{_digest(bundle)} attempt=1",
        artifacts=[{"id": 1, "name": "state.dev-eu...1"}],
        archives={1: _zip(bundle)},
        STACK=".",
    )
    out = {"kind": "save", "attempt": "1", "path": "./terraform.tfstate", "slug": "."}
    assert job() == (0, out)
    assert (job.ws / "terraform.tfstate").read_bytes() == CIPHER


def test_a_bundle_without_the_header_is_refused(monkeypatch, tmp_path):
    """Mutation: drop the header test."""
    bundle = PATH.encode() + b"\n" + CIPHER
    job = _Job(monkeypatch, tmp_path, state=f"{_digest(bundle)} attempt=1", **_one(1, bundle))
    no_match = f"no artifact of this run named {_name(1)} holds the cell's state bundle"
    assert job() == (_refusal(no_match, 1), {})


@pytest.mark.parametrize("runner", ["self-hosted", "", "GitHub-hosted"])
def test_a_save_on_a_runner_not_exactly_github_hosted_refuses_before_any_call(
    monkeypatch, tmp_path, runner
):
    """Mutations: refuse only `self-hosted`; move the runner check after the download."""
    job = _Job(
        monkeypatch, tmp_path, state=f"{DIGEST} attempt=1", RUNNER_ENVIRONMENT=runner, **_one()
    )
    assert job() == (_refusal(_HOSTED_ONLY, 1), {})
    assert job.calls == []


def test_the_path_is_masked_before_any_line_names_it(monkeypatch, tmp_path, capsys):
    """Mutation: print the path before masking it."""
    job = _Job(monkeypatch, tmp_path, state=f"{DIGEST} attempt=1", **_one())
    job()
    lines = capsys.readouterr().out.splitlines()
    naming = [line for line in lines if PATH in line]
    assert naming == [f"::add-mask::{PATH}"]
    assert lines.index(naming[0]) == 0


def test_the_saver_takes_no_encryption_key():
    """The key may live only on environments `save` does not bind; the digest binds the
    bytes. Mutation: add an `encryption-key` input."""
    assert set(action_yaml("state-save")["inputs"]) == {
        "stack",
        "environment",
        "state",
        "job-id",
    }
    script = (ENGINE / "scripts" / "state-save").read_text(encoding="utf-8")
    assert "ENCRYPTION_KEY" not in script + json.dumps(action_yaml("state-save"))


@pytest.mark.parametrize("state", ["not-applied attempt=1", "none attempt=1"])
def test_attempt_one_without_a_local_state_skips_without_any_call(monkeypatch, tmp_path, state):
    """Mutation: list the run before the attempt-1 skip."""
    job = _Job(monkeypatch, tmp_path, state=state, RUNNER_ENVIRONMENT="self-hosted")
    assert (job(), job.calls) == ((0, {"kind": "skip"}), [])


@pytest.mark.parametrize("attempt", [1, 2])
def test_a_missing_state_file_refuses_on_any_attempt(monkeypatch, tmp_path, attempt):
    """Mutation: drop the `missing` refusal."""
    job = _Job(monkeypatch, tmp_path, state=f"missing attempt={attempt}", attempt=attempt)
    reason = "the apply step ran and left no state file"
    assert (job(), job.calls) == ((_refusal(reason, attempt), {}), [])


def _empty_reason(state):
    return f"the apply job's state output reads {state!r} after its apply may have run"


@pytest.mark.parametrize(
    ("steps", "conclusion", "expected"),
    [
        (NOT_STARTED, "failure", (0, {"kind": "skip"})),
        ([], "cancelled", (0, {"kind": "skip"})),
        (STARTED, "failure", (_refusal(_empty_reason(""), 1), {})),
        ([], "failure", (_refusal(_empty_reason(""), 1), {})),
    ],
    ids=["skipped", "cancelled-pending", "started", "no-steps-failed"],
)
def test_attempt_one_with_an_empty_output_reads_the_apply_row(
    monkeypatch, tmp_path, steps, conclusion, expected
):
    """An empty output after `Apply the cell` started refuses. Mutations: read a missing output
    as `not-applied`; read `steps: []` as never started (`apply-gate._applied`)."""
    jobs = [_apply(1, steps, conclusion=conclusion), _save(1, None)]
    job = _Job(monkeypatch, tmp_path, state="", jobs=jobs)
    assert job() == expected
    assert job.calls == [JOBS_LISTED]


_UNREADABLE = "the run's job listing failed or does not list this job"


@pytest.mark.parametrize(
    ("jobs", "reason"),
    [
        (None, _UNREADABLE),
        ([_apply(1, NOT_STARTED)], _UNREADABLE),
        ([_save(1, None)], _empty_reason("")),
    ],
    ids=["failed", "own-job-unlisted", "no-apply-row"],
)
def test_an_unreadable_listing_refuses(monkeypatch, tmp_path, jobs, reason):
    """Mutations: read a failed listing as empty; read a listing without this job as nothing
    unsaved; read a missing `apply` row as never started."""
    job = _Job(monkeypatch, tmp_path, state="", jobs=jobs)
    assert job() == (_refusal(reason, 1), {})


_A1 = _apply(1)
_RERUN_FAILED = [_A1, _save(1, "failure"), _carried(2, _A1), _save(2, None)]


@pytest.mark.parametrize("state", [f"{DIGEST} attempt=1", ""], ids=["carried-output", "no-output"])
def test_rerun_failed_jobs_looks_up_the_carried_attempt(monkeypatch, tmp_path, state):
    """ "Re-run failed jobs" after attempt 1 applied and its save failed: `apply` is carried,
    with its output or none. Mutations: compare the output's attempt with `<=` (an earlier
    attempt's digest saves); count a carried row as an apply of its re-run attempt
    (`apply-gate.attempts`), so an empty output refuses."""
    job = _Job(monkeypatch, tmp_path, state=state, attempt=2, jobs=_RERUN_FAILED, **_one())
    assert job() == (0, {"kind": "lookup", "attempt": "1", "path": PATH, "slug": "stacks-app"})
    assert not (job.ws / PATH).exists()


def test_a_second_rerun_of_failed_jobs_looks_up_only_the_attempt_that_applied(
    monkeypatch, tmp_path
):
    """Attempt 1 applied and its save failed; attempts 2 and 3 each re-ran only the failed
    `save`, carrying `apply`. Only attempt 1 applied. Mutation: count carried rows as applies
    of their re-run attempt (`apply-gate.attempts`), which refuses naming attempts 1 and 2."""
    jobs = [
        _A1,
        _save(1, "failure"),
        _carried(2, _A1),
        {**_save(2, "failure"), "id": 202},
        _carried(3, _A1),
        _save(3, None),
    ]
    job = _Job(monkeypatch, tmp_path, state="", attempt=3, jobs=jobs, **_one())
    assert job() == (0, {"kind": "lookup", "attempt": "1", "path": PATH, "slug": "stacks-app"})


def test_a_short_listing_refuses(monkeypatch, tmp_path):
    """A listing short of its `total_count` could hide an unsaved attempt. Mutation: list with
    `counted=False`."""
    job = _Job(monkeypatch, tmp_path, state="", attempt=2, jobs=_RERUN_FAILED, short=True, **_one())
    assert job() == (_refusal(_UNREADABLE, 2), {})
    assert job.calls == [JOBS_LISTED]


def test_a_carried_digest_binds_the_looked_up_bundle(monkeypatch, tmp_path):
    """A lookup whose output carries the attempt's digest takes only matching bytes.
    Mutation: read the bundle unchecked whenever the kind is `lookup`."""
    other = _bundle(cipher=b"SHPS1planted" * 9)
    job = _Job(
        monkeypatch,
        tmp_path,
        state=f"{DIGEST} attempt=1",
        attempt=2,
        jobs=_RERUN_FAILED,
        **_one(1, other),
    )
    no_match = f"no artifact of this run named {_name(1)} holds the cell's state bundle"
    assert job() == (_refusal(no_match, 1), {})


def test_a_failed_artifact_listing_refuses_naming_the_keys(monkeypatch, tmp_path):
    """Mutation: let the listing's failure exit unwrapped, with no key and no runbook."""
    job = _Job(monkeypatch, tmp_path, state=f"{DIGEST} attempt=1")

    def failing(argv):
        raise SystemExit("command failed (1): gh api\nHTTP 404")

    monkeypatch.setattr(ss, "run", failing)
    reason = "the run's artifacts could not be listed (command failed (1): gh api | HTTP 404)"
    assert job() == (_refusal(reason, 1), {})


def test_a_lookup_without_the_attempts_artifact_refuses(monkeypatch, tmp_path):
    """Mutation: output the lookup with an empty path when no artifact is found."""
    job = _Job(monkeypatch, tmp_path, state="", attempt=2, jobs=_RERUN_FAILED)
    no_match = f"no artifact of this run named {_name(1)} holds the cell's state bundle"
    assert job() == (_refusal(no_match, 1), {})


def test_rerun_all_jobs_looks_up_the_unsaved_attempt(monkeypatch, tmp_path):
    """ "Re-run all jobs": attempt 2's pre-apply refuses, so `apply` reports `not-applied`, and
    attempt 1 is still unsaved. Mutation: `skip` on `not-applied` whatever the attempt."""
    jobs = [_A1, _save(1, "failure"), _apply(2, NOT_STARTED, conclusion="failure"), _save(2, None)]
    job = _Job(monkeypatch, tmp_path, state="not-applied attempt=2", attempt=2, jobs=jobs, **_one())
    assert job() == (0, {"kind": "lookup", "attempt": "1", "path": PATH, "slug": "stacks-app"})


def test_two_unsaved_earlier_attempts_refuse_naming_both(monkeypatch, tmp_path):
    """Attempts 1 and 2 each applied and failed to save; attempt 3 re-runs. Mutation: look up
    only the newest unsaved attempt."""
    jobs = [
        _A1,
        _save(1, "failure"),
        _apply(2, started_at="2026-11-17T19:49:00Z"),
        _save(2, "failure"),
        _apply(3, NOT_STARTED, conclusion="failure"),
        _save(3, None),
    ]
    job = _Job(monkeypatch, tmp_path, state="not-applied attempt=3", attempt=3, jobs=jobs, **_one())
    reason = "run attempts 1, 2 of this cell may have applied unsaved"
    assert job() == (_refusal(reason, 1, 2), {})
    assert job.calls == [JOBS_LISTED]


def test_an_own_digest_over_an_unsaved_attempt_refuses_without_saving(monkeypatch, tmp_path):
    """Mutation: save first, before reading the earlier attempts."""
    jobs = [_A1, _save(1, "failure"), _apply(2), _save(2, None)]
    job = _Job(monkeypatch, tmp_path, state=f"{DIGEST} attempt=2", attempt=2, jobs=jobs, **_one(2))
    reason = "run attempts 1 of this cell may have applied unsaved"
    assert job() == (_refusal(reason, 1), {})
    assert job.calls == [JOBS_LISTED]
    assert not (job.ws / PATH).exists()


def test_an_own_digest_over_saved_attempts_saves(monkeypatch, tmp_path):
    """Mutation: refuse every own digest past attempt 1."""
    jobs = [_A1, _save(1, "success"), _apply(2), _save(2, None)]
    job = _Job(monkeypatch, tmp_path, state=f"{DIGEST} attempt=2", attempt=2, jobs=jobs, **_one(2))
    assert job() == (0, {"kind": "save", "attempt": "2", "path": PATH, "slug": "stacks-app"})


_REMOTE = [_A1, _save(1, "success"), _apply(2), _save(2, None)]


@pytest.mark.parametrize("runner", ["github-hosted", "self-hosted"])
def test_a_remote_backend_rerun_skips_on_any_runner(monkeypatch, tmp_path, runner):
    """`none attempt=2` over an attempt 1 whose save succeeded: nothing to look up, so the
    runner is never checked. Mutations: look up attempt 1's key unconditionally; check the
    runner before the decision."""
    job = _Job(
        monkeypatch,
        tmp_path,
        state="none attempt=2",
        attempt=2,
        jobs=_REMOTE,
        RUNNER_ENVIRONMENT=runner,
    )
    assert job() == (0, {"kind": "skip"})
    assert job.calls == [JOBS_LISTED]


def test_a_lookup_on_a_self_hosted_runner_refuses_before_any_artifact_call(monkeypatch, tmp_path):
    """Mutation: move the runner check after the download."""
    job = _Job(
        monkeypatch,
        tmp_path,
        state="",
        attempt=2,
        jobs=_RERUN_FAILED,
        RUNNER_ENVIRONMENT="self-hosted",
        **_one(),
    )
    assert job() == (_refusal(_HOSTED_ONLY, 1), {})
    assert job.calls == [JOBS_LISTED]


@pytest.mark.parametrize(
    "state",
    ["", "garbage", f"{DIGEST} attempt=3", "none attempt=01", f"{DIGEST.upper()} attempt=2"],
)
def test_an_empty_or_malformed_output_after_an_own_apply_refuses(monkeypatch, tmp_path, state):
    """Attempt 2 ran its own `apply`, which started `Apply the cell`. Mutations: read a
    missing output as `not-applied`; accept an output naming a later attempt."""
    jobs = [_A1, _save(1, "success"), _apply(2), _save(2, None)]
    job = _Job(monkeypatch, tmp_path, state=state, attempt=2, jobs=jobs)
    assert job() == (_refusal(_empty_reason(state), 2), {})


def test_a_steps_less_own_row_that_failed_refuses(monkeypatch, tmp_path):
    """An own `apply` row with no steps that is no carried copy may have applied. Mutation:
    read `steps: []` as never started (`apply-gate._applied`)."""
    jobs = [_A1, _save(1, "success"), _apply(2, [], conclusion="failure"), _save(2, None)]
    job = _Job(monkeypatch, tmp_path, state="", attempt=2, jobs=jobs)
    assert job() == (_refusal(_empty_reason(""), 2), {})


def test_a_download_keeps_the_archives_bytes(monkeypatch):
    """An archive is not text. Mutation: read it through `run`, which decodes stdout."""
    raw = _zip(_bundle())
    seen = []

    def fake(argv, **kwargs):
        seen.append(argv)
        return ss.subprocess.CompletedProcess(argv, 0, stdout=raw, stderr=b"")

    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(ss.subprocess, "run", fake)
    monkeypatch.setattr(ss, "run", lambda argv: raw.decode("utf-8", "replace"))
    assert ss._download(5) == raw
    assert seen == [["gh", "api", "repos/o/r/actions/artifacts/5/zip"]]


_STATE_WITH = {
    "stack-slug": "${{ steps.fetch.outputs.slug }}",
    "env": "${{ inputs.environment }}",
    "mode": "${{ steps.fetch.outputs.kind }}",
    "path": "${{ steps.fetch.outputs.path }}",
    "attempt": "${{ steps.fetch.outputs.attempt }}",
}

_STEPS = [
    {
        "name": "Decide and fetch the cell's state bundle",
        "id": "fetch",
        "shell": "bash",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "STACK": "${{ inputs.stack }}",
            "ENV_NAME": "${{ inputs.environment }}",
            "STATE": "${{ inputs.state }}",
            "JOB_ID": "${{ inputs.job-id }}",
        },
        "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/state-save"',
    },
    {
        "name": "Check the decision",
        "id": "check",
        "shell": "bash",
        "env": {
            "KIND": "${{ steps.fetch.outputs.kind }}",
            "ATTEMPT": "${{ steps.fetch.outputs.attempt }}",
            "STATE_PATH": "${{ steps.fetch.outputs.path }}",
            "SLUG": "${{ steps.fetch.outputs.slug }}",
        },
    },
    {
        "name": "Save or look up the state",
        "id": "primary",
        "continue-on-error": True,
        "if": "${{ steps.check.outputs.go == 'true' }}",
        "uses": local_action("state"),
        "with": {**_STATE_WITH, "key-suffix": ""},
    },
    {
        "name": "Save or look up the state under the retry key",
        "id": "retry",
        "if": (
            "${{ !cancelled() && steps.check.outputs.go == 'true' "
            "&& steps.primary.outputs.saved != 'true' }}"
        ),
        "uses": local_action("state"),
        "with": {**_STATE_WITH, "key-suffix": "-r1"},
    },
    {
        "name": "Fail unless the state entry was found",
        "id": "found",
        "if": "${{ always() && steps.check.outputs.go == 'true' }}",
        "shell": "bash",
        "env": {
            "PRIMARY_SAVED": "${{ steps.primary.outputs.saved }}",
            "RETRY_SAVED": "${{ steps.retry.outputs.saved }}",
            "STACK": "${{ inputs.stack }}",
            "ENV_NAME": "${{ inputs.environment }}",
            "SLUG": "${{ steps.fetch.outputs.slug }}",
            "ATTEMPT": "${{ steps.fetch.outputs.attempt }}",
        },
    },
]


def test_the_actions_steps_are_pinned_whole():
    """The key comes from the decision's attempt, never from the bundle. Mutations: `mode: save`
    on the `-r1` call; drop the primary's `continue-on-error` (a refused save then skips the
    retry); drop `!cancelled()` (the implied `success()` skips the retry after the primary
    failed); `attempt` from `github.run_attempt`; `if: success()` on the last step;
    `PRIMARY_SAVED` from the primary's `outcome`."""
    steps = action_yaml("state-save")["runs"]["steps"]
    assert [{k: v for k, v in s.items() if k != "run" or s["id"] == "fetch"} for s in steps] == (
        _STEPS
    )


def _if(expr, ctx, failed):
    """The value of a step's `if:`, for the forms this action writes: `always()`,
    `!cancelled()`, `steps.<id>.outcome`, `steps.<id>.outputs.<name>`, `==`, `!=`, `&&`
    and quoted strings; anything else fails the test. No status function means `success() &&`,
    and an empty expression is an absent `if:`."""
    body = expr.removeprefix("${{").removesuffix("}}").strip()
    implicit = not re.search(r"\b(always|cancelled|success|failure)\(\)", body)

    def ref(m):
        step = ctx.get(m[1], {"outcome": "", "outputs": {}})
        return repr(step["outcome"] if m[2] == "outcome" else step["outputs"].get(m[3], ""))

    py = re.sub(r"steps\.([\w-]+)\.(outcome|outputs\.([\w-]+))", ref, body)
    py = py.replace("&&", " and ").replace("always()", "True").replace("!cancelled()", "True")
    assert re.fullmatch(r"(?:\s|True|and|==|!=|'[^'\\]*')*", py), py
    # The fullmatch above admits only literals and operators; an absent `if:` is empty.
    value = eval(py, {"__builtins__": {}}) if py.strip() else True  # noqa: S307
    return value and not failed if implicit else value


INPUTS = {"stack": "stacks/app", "environment": "dev-eu"}


def _resolve(value, ctx):
    m = re.fullmatch(
        r"\$\{\{ (?:inputs\.([\w-]+)|steps\.(\w+)\.outputs\.(\w+)|github\.token) \}\}", value
    )
    if not m:
        return value
    if m[1]:
        return INPUTS[m[1]]
    if m[2]:
        return ctx.get(m[2], {"outputs": {}})["outputs"].get(m[3], "")
    return ""


def _composite(tmp_path, fetched, saved):
    """Run the action's steps after `fetch`, which wrote `fetched`: shell bodies through bash,
    each `actions/state` call stubbed to output `saved[key-suffix]`. Returns whether the action
    failed, the `actions/state` calls and the shell output."""
    ctx = {"fetch": {"outcome": "success", "outputs": fetched}}
    failed, calls, log = False, [], ""
    for i, step in enumerate(action_yaml("state-save")["runs"]["steps"][1:]):
        if not _if(step.get("if", "${{ }}"), ctx, failed):
            ctx[step["id"]] = {"outcome": "skipped", "outputs": {}}
            continue
        if "uses" in step:
            with_ = {k: _resolve(v, ctx) for k, v in step["with"].items()}
            calls.append(with_)
            outcome, outputs = "success", {"saved": saved.get(with_["key-suffix"], "")}
        else:
            out = tmp_path / f"out{i}"
            out.write_text("", encoding="utf-8")
            env = {k: _resolve(v, ctx) for k, v in step["env"].items()}
            env |= {"GITHUB_OUTPUT": str(out), "GITHUB_RUN_ID": "77", "PATH": os.environ["PATH"]}
            r = run_step(tmp_path, step["run"], env)
            log += r.stdout
            outcome = "success" if r.returncode == 0 else "failure"
            outputs = github_outputs(out)
        if outcome == "failure" and not step.get("continue-on-error"):
            failed = True
        ctx[step["id"]] = {"outcome": outcome, "outputs": outputs}
    return failed, calls, log


def _call(mode, suffix, attempt="1"):
    return {
        "stack-slug": "stacks-app",
        "env": "dev-eu",
        "mode": mode,
        "path": PATH,
        "attempt": attempt,
        "key-suffix": suffix,
    }


NOT_FOUND = (
    "::error::the state of dev-eu / stacks/app was not found under state/stacks-app/dev-eu/77-1 "
    f"or state/stacks-app/dev-eu/77-1-r1. {ss.REMEDY}\n"
)


@bash_only
def test_a_skip_succeeds_with_no_cache_call(tmp_path):
    """Mutation: drop the `go` guard on the last step's `if:` (`steps.check.outcome ==
    'success'`), so a skip reports a lost state."""
    assert _composite(tmp_path, {"kind": "skip"}, {}) == (False, [], "")


@bash_only
@pytest.mark.parametrize(
    "fetched",
    [
        {},
        {"kind": "bogus", "attempt": "1", "path": PATH},
        {"kind": "lookup", "attempt": "", "path": PATH, "slug": "stacks-app"},
        {"kind": "save", "attempt": "1", "path": "", "slug": "stacks-app"},
        {"kind": "save", "attempt": "1", "path": PATH, "slug": ""},
    ],
    ids=["empty", "bogus", "lookup-no-attempt", "save-no-path", "save-no-slug"],
)
def test_an_unknown_or_incomplete_decision_fails_with_no_cache_call(tmp_path, fetched):
    """The refusal names the runbook section. Mutations: default an empty `kind` to `skip`; drop
    the empty-attempt test; drop the remedy from the refusal."""
    failed, calls, log = _composite(tmp_path, fetched, {"": "true"})
    assert (failed, calls) == (True, [])
    assert log == (
        "::error::shipmate/actions/state-save: the save decision (kind "
        f"'{fetched.get('kind', '')}', attempt '{fetched.get('attempt', '')}') is unknown or "
        f"incomplete, so this cell's state is not known saved. {ss.REMEDY}\n"
    )


@bash_only
@pytest.mark.parametrize("kind", ["save", "lookup"])
@pytest.mark.parametrize(
    ("saved", "suffixes", "ok"),
    [
        ({"": "true"}, [""], True),
        ({"-r1": "true"}, ["", "-r1"], True),
        ({}, ["", "-r1"], False),
        ({"": "false", "-r1": "false"}, ["", "-r1"], False),
    ],
    ids=["primary", "retry", "neither-empty", "neither-false"],
)
def test_a_branch_passes_only_on_a_found_entry(tmp_path, kind, saved, suffixes, ok):
    """`saved` is `true` only on an exact-key hit. A found lookup credits the earlier attempt;
    a missing one refuses. Mutations: accept any non-empty `PRIMARY_SAVED`; run the retry when
    the primary found the entry; drop the never-re-run instruction."""
    failed, calls, log = _composite(
        tmp_path, {"kind": kind, "attempt": "1", "path": PATH, "slug": "stacks-app"}, dict(saved)
    )
    assert (failed, calls) == (not ok, [_call(kind, s) for s in suffixes])
    assert log == ("" if ok else NOT_FOUND)
