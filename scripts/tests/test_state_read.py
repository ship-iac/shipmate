"""`scripts/state-read`: each cell's class from its own job's log and GitHub's job record.

Threat model: an accidental edit that (a) classes a cell whose apply may have run as `not_run`,
`none` or `not_applied`, so its state is never saved and its check completes; (b) counts a line
the cell did not print as its state line, or reprints a log line, which the runner would
execute as a workflow command; (c) matches another level's job, or a saver leg, as the cell's
job. A cell forging its own line is out of scope (its code already controls its state); a
second line refuses it.

The log fixture `fixtures/state-read/cell-job.log` is the raw log of job 133655241045 (run
43968821153, ship-iac/repo-example-stacks, runner 2.338.2), saved unfiltered from
`gh api repos/<repo>/actions/jobs/<id>/logs` with stdout to a file: it keeps the BOM and the
ANSI colour of the run headers. Tests insert state lines after its `apply mode=sleep` line in
the runner's timestamp form.
"""

import json
import os

import pytest
from _loader import ENGINE, bash_only, github_outputs, load_script, run_step, step_by

sr = load_script("state-read")
bm = load_script("build-matrix")

_FIXTURE = (ENGINE / "scripts" / "tests" / "fixtures" / "state-read" / "cell-job.log").read_bytes()
_TS = "2026-11-16T22:18:57.9900000Z "
D1 = "a" * 64
D2 = "b" * 64
LEVEL = "apply / run / L0"
OWN = {"id": 99, "name": f"{LEVEL} / wave0-read", "conclusion": None}
#: The saver job's `name:`, written by hand; `test_apply_state_saver_wiring.py` pins the
#: workflow's value to the same text.
LEG_NAME = "save-state (${{ matrix.environment }} ${{ matrix.stack }})"


def _leg(stack, env):
    return LEG_NAME.replace("${{ matrix.environment }}", env).replace("${{ matrix.stack }}", stack)


def _log(*lines):
    """The fixture with `lines` inserted after its apply output, each as the runner logs it."""
    text = _FIXTURE.decode("utf-8")
    anchor = "Z apply mode=sleep\n"
    at = text.index(anchor) + len(anchor)
    return text[:at] + "".join(f"{_TS}{line}\n" for line in lines) + text[at:]


def _job(
    stack, *, env="dev-eu", conclusion="success", attempt=1, steps=None, jid=None, level=LEVEL
):
    if steps is None:
        steps = [{"name": "Apply the cell", "conclusion": conclusion}]
    return {
        "id": jid or abs(hash((stack, env, level))) % 10**8,
        "name": f"{level} / {env} / {stack}",
        "conclusion": conclusion,
        "run_attempt": attempt,
        "steps": steps,
    }


def _check(stack, env="dev-eu"):
    return f"apply / {stack} / {env}"


class _Run:
    """`main` with the listing, the logs and the sleeps stubbed. `listings` is consumed per
    listing read (the last repeats); an exception item raises SystemExit. `logs` maps a job id
    to a list consumed per read (the last repeats)."""

    def __init__(self, monkeypatch, tmp_path, cells, listings, logs):
        self.reads, self.waits, self.listing_reads = {}, [], 0
        out = tmp_path / "out"
        out.write_text("", encoding="utf-8")
        self.out = out
        monkeypatch.setenv("SHIPMATE_BY_WAVE_JSON", json.dumps({"wave0": {c: [1] for c in cells}}))
        monkeypatch.setenv("SHIPMATE_WAVE", "wave0")
        monkeypatch.setenv("SHIPMATE_JOB_ID", "99")
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

        def fetch():
            item = listings[min(self.listing_reads, len(listings) - 1)]
            self.listing_reads += 1
            if item is None:
                raise SystemExit("command failed (1): gh api")
            return "\n".join(json.dumps(j) for j in item)

        def log(job_id):
            seq = logs[job_id]
            n = self.reads.get(job_id, 0)
            self.reads[job_id] = n + 1
            item = seq[min(n, len(seq) - 1)]
            if item is None:
                raise SystemExit("command failed (1): gh api logs")
            return item

        monkeypatch.setattr(sr.ac, "fetch_jobs", fetch)
        monkeypatch.setattr(sr, "_log", log)
        monkeypatch.setattr(sr, "_wait", self.waits.append)

    def __call__(self):
        try:
            sr.main()
            code = 0
        except SystemExit as exc:
            code = exc.code
        outputs = github_outputs(self.out)
        return code, {k: json.loads(v) for k, v in outputs.items()}


def _report(**kw):
    base = {"state": [], "none": [], "not_applied": [], "not_run": [], "refused": {}}
    return {**base, **kw}


def _row(stack, digest, attempt="1"):
    return {
        "check": _check(stack),
        "stack": stack,
        "environment": "dev-eu",
        "slug": stack.replace("/", "-"),
        "digest": digest,
        "attempt": attempt,
    }


def test_a_mixed_wave_classes_every_cell(monkeypatch, tmp_path):
    """Mutations: map `not-applied` to `none`; class `missing` as `none`; drop the `steps == []`
    branch, and a never-started job's unreadable log refuses it."""
    jobs = [
        OWN,
        _job("a", jid=1),
        _job("b", jid=2),
        _job(
            "c",
            jid=3,
            conclusion="failure",
            steps=[{"name": "Apply the cell", "conclusion": "skipped"}],
        ),
        _job("d", jid=4, conclusion="cancelled", steps=[]),
        _job("e", jid=5),
    ]
    logs = {
        1: [_log(f"shipmate-state-digest={D1} attempt=1")],
        2: [_log("shipmate-state-digest=none attempt=1")],
        3: [_log("shipmate-state-digest=not-applied attempt=1")],
        4: [None],
        5: [_log("shipmate-state-digest=missing attempt=1")],
    }
    cells = [_check(s) for s in "abcde"]
    code, out = _Run(monkeypatch, tmp_path, cells, [jobs], logs)()
    assert (code, out) == (
        0,
        {
            "cells": [_row("a", D1)],
            "report": _report(
                state=[_check("a")],
                none=[_check("b")],
                not_applied=[_check("c")],
                not_run=[_check("d")],
                refused={_check("e"): "the apply ran and left no state file"},
            ),
        },
    )


@pytest.mark.parametrize(
    ("lines", "reason"),
    [
        (
            [f"shipmate-state-digest={D1} attempt=1", f"shipmate-state-digest={D2} attempt=1"],
            "2 state lines in the cell's log",
        ),
        ([f"x shipmate-state-digest={D1} attempt=1"], "no state line in the cell's log"),
        ([f"  shipmate-state-digest={D1} attempt=1"], "no state line in the cell's log"),
        (
            [f'\x1b[36;1mecho "shipmate-state-digest={D1} attempt=1"\x1b[0m'],
            "no state line in the cell's log",
        ),
        ([f"shipmate-state-digest={D1} attempt=1 x"], "no state line in the cell's log"),
    ],
    ids=["two", "mid-line", "indented", "ansi-header", "trailing"],
)
def test_only_one_whole_state_line_counts(monkeypatch, tmp_path, lines, reason):
    """Mutations: `LINE.search` for `LINE.fullmatch`; count only `shipmate-state-digest=`
    lines (`LINE` without its timestamp, matched by `search`)."""
    logs = {1: [_log(*lines)]}
    code, out = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, _job("a", jid=1)]], logs)()
    assert (code, out["report"]) == (0, _report(refused={_check("a"): reason}))


def test_a_line_without_the_runners_timestamp_counts_zero(monkeypatch, tmp_path):
    """Mutation: drop the timestamp from `LINE`."""
    log = _FIXTURE.decode("utf-8") + f"shipmate-state-digest={D1} attempt=1\n"
    code, out = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, _job("a", jid=1)]], {1: [log]})()
    assert out["report"] == _report(refused={_check("a"): "no state line in the cell's log"})


_SKIPPED = [
    {"name": "Checkout", "conclusion": "cancelled"},
    {"name": "Apply the cell", "conclusion": "skipped"},
]


@pytest.mark.parametrize(
    ("conclusion", "steps", "kind"),
    [
        ("cancelled", [], "not_run"),
        ("cancelled", _SKIPPED, "not_run"),
        ("failure", _SKIPPED, "not_run"),
        ("cancelled", [{"name": "Checkout", "conclusion": "cancelled"}], "refused"),
        ("failure", [{"name": "Apply the cell", "conclusion": "failure"}], "refused"),
        ("cancelled", [{"name": "Apply the cell", "conclusion": "cancelled"}], "refused"),
        ("success", _SKIPPED, "refused"),
        ("success", [], "refused"),
    ],
    ids=[
        "cancelled-no-steps",
        "cancelled-step-skipped",
        "failure-step-skipped",
        "no-apply-step",
        "step-failure",
        "step-cancelled",
        "success-step-skipped",
        "success-no-steps",
    ],
)
def test_no_line_is_not_run_only_from_the_jobs_own_record(
    monkeypatch, tmp_path, conclusion, steps, kind
):
    """Mutations: `not_run` on no line alone; `not_run` when the step is absent from a
    non-empty list; refuse a `failure` job with the step `skipped`; `not_run` for a `success`
    job with the step `skipped`."""
    job = _job("a", jid=1, conclusion=conclusion, steps=steps)
    code, out = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, job]], {1: [_log()]})()
    expected = (
        _report(not_run=[_check("a")])
        if kind == "not_run"
        else _report(refused={_check("a"): "no state line in the cell's log"})
    )
    assert (code, out["report"]) == (0, expected)


def test_the_apply_step_is_the_one_the_wave_names():
    """`test_deploy_detect.py` pins the wave step's name to the same text. Mutation: rename
    `APPLY_STEP`."""
    assert sr.APPLY_STEP == "Apply the cell"


def test_a_cell_with_no_listed_job_refuses_beside_the_levels_literal_row(monkeypatch, tmp_path):
    """A wave cancelled before its matrix expanded lists only the level's literal row, which
    every unexpanded wave of the level shares. Mutation: class an unlisted cell `not_run`, as
    apply-complete's literal-row rule would."""
    literal = {
        "id": 7,
        "name": f"{LEVEL} / ${{{{ matrix.environment }}}} / ${{{{ matrix.stack }}}}",
        "conclusion": "cancelled",
        "steps": [],
    }
    run = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, literal]], {})
    code, out = run()
    assert (code, out["report"]) == (
        0,
        _report(refused={_check("a"): "the cell's job is not listed"}),
    )
    assert run.listing_reads == len(sr.LISTING_DELAYS) + 1


@pytest.mark.parametrize("stack", ["stacks/app", "a/b/c", ".", "x"])
@bash_only
def test_slug_of_equals_the_stack_slug_step(tmp_path, stack):
    """Mutation: replace `/` with `_` in `slug_of`."""
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    body = step_by("apply-cell", name="Stack slug")["run"]
    r = run_step(tmp_path, body, {**os.environ, "STACK": stack, "GITHUB_OUTPUT": str(out)})
    assert r.returncode == 0
    assert github_outputs(out) == {"slug": bm.slug_of(stack)}


def test_the_reader_and_the_collision_guard_take_the_slug_from_slug_of(monkeypatch, tmp_path):
    """Mutations: a second inline derivation in the reader (`stack.replace("/", "-")`); the
    same in `guard_slug_collisions`."""
    monkeypatch.setattr(sr.bm, "slug_of", lambda stack: "S")
    logs = {1: [_log(f"shipmate-state-digest={D1} attempt=1")]}
    _, out = _Run(monkeypatch, tmp_path, [_check("a/b")], [[OWN, _job("a/b", jid=1)]], logs)()
    assert out["cells"][0]["slug"] == "S"
    monkeypatch.setattr(bm, "slug_of", lambda stack: "same")
    with pytest.raises(SystemExit):
        bm.guard_slug_collisions(
            [{"environment": "dev", "stack": "a"}, {"environment": "dev", "stack": "b"}]
        )


def test_a_zero_read_is_read_again(monkeypatch, tmp_path):
    """Mutation: refuse on the first zero read (drop the re-read)."""
    logs = {1: [_log(), _log(), _log(f"shipmate-state-digest={D1} attempt=1")]}
    run = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, _job("a", jid=1)]], logs)
    _, out = run()
    assert (out["report"], run.reads[1]) == (_report(state=[_check("a")]), 3)


def test_zero_on_every_read_refuses_after_the_budget(monkeypatch, tmp_path):
    """Mutation: one read only (`LOG_DELAYS = ()`) -- the read count reddens."""
    run = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, _job("a", jid=1)]], {1: [_log()]})
    _, out = run()
    assert out["report"] == _report(refused={_check("a"): "no state line in the cell's log"})
    assert run.reads[1] == len(sr.LOG_DELAYS) + 1 == 5


def test_two_lines_refuse_without_a_re_read(monkeypatch, tmp_path):
    """Mutation: re-read on any count other than one."""
    two = _log(f"shipmate-state-digest={D1} attempt=1", f"shipmate-state-digest={D1} attempt=1")
    run = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, _job("a", jid=1)]], {1: [two]})
    run()
    assert run.reads[1] == 1


def test_an_unreadable_log_refuses_after_the_budget(monkeypatch, tmp_path):
    """Mutation: treat a failed read as zero lines (`found = []`)."""
    run = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, _job("a", jid=1)]], {1: [None]})
    _, out = run()
    assert out["report"] == _report(refused={_check("a"): "the cell's log could not be read"})


@pytest.mark.parametrize(
    ("line_attempt", "job_attempt", "ok"), [(3, 2, False), (2, 2, True), (1, 2, True)]
)
def test_the_lines_attempt_may_not_exceed_the_jobs(
    monkeypatch, tmp_path, line_attempt, job_attempt, ok
):
    """A copied cell of an earlier attempt keeps its own attempt. Mutation: take the job's
    `run_attempt` for the row; drop the comparison."""
    logs = {1: [_log(f"shipmate-state-digest={D1} attempt={line_attempt}")]}
    job = _job("a", jid=1, attempt=job_attempt)
    _, out = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, job]], logs)()
    if ok:
        assert out["cells"] == [_row("a", D1, str(line_attempt))]
    else:
        assert out["report"] == _report(
            refused={_check("a"): "the state line's attempt is above the job's"}
        )


def test_another_levels_job_and_a_saver_leg_never_match(monkeypatch, tmp_path):
    """Mutations: match by `ag.is_cell_job` (any level) instead of this job's own level; name
    the leg `<env> / <stack>` (`LEG_NAME`)."""
    other = _job("a", jid=2, level="apply / run / L1")
    leg = {"id": 3, "name": f"{LEVEL} / {_leg('a', 'dev-eu')}", "conclusion": "success"}
    logs = {2: [_log(f"shipmate-state-digest={D1} attempt=1")], 3: [_log()]}
    code, out = _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, other, leg]], logs)()
    assert out["report"] == _report(refused={_check("a"): "the cell's job is not listed"})


@pytest.mark.parametrize(
    ("stack", "env"),
    [("stacks/app", "dev-eu"), (".", "dev-eu"), ("a/b/c", "prod"), ("stacks/app", ""), (".", "")],
)
def test_a_saver_leg_is_never_a_cell_job(stack, env):
    """`apply-complete` classes cells by `is_cell_job`; a leg it matched would stand in for the
    cell. Mutation: name the leg `<env> / <stack>` (`LEG_NAME`)."""
    assert not sr.ag.is_cell_job(f"{LEVEL} / {_leg(stack, env)}", stack, env)


def test_a_listing_that_never_lists_this_job_exits_without_outputs(monkeypatch, tmp_path):
    """The level comes from this job's own listed name. Mutation: drop `listed`'s `if level:`
    test."""
    run = _Run(monkeypatch, tmp_path, [_check("a")], [[_job("a", jid=1)]], {})
    code, out = run()
    assert (out, run.listing_reads) == ({}, len(sr.LISTING_DELAYS) + 1)
    assert code.startswith("::error::the run's jobs listing failed or never listed this job")


def test_a_failed_listing_exits_without_outputs(monkeypatch, tmp_path):
    """Mutation: write `cells` before the listing succeeds (return an empty match)."""
    run = _Run(monkeypatch, tmp_path, [_check("a")], [None], {})
    code, out = run()
    assert out == {}
    assert code == (
        "::error::the run's jobs listing failed or never listed this job, so the wave's cells "
        "cannot be found; no state is saved for this wave."
    )


def test_a_refused_cell_beside_a_saved_one_exits_0_with_both_outputs(monkeypatch, tmp_path):
    """Mutation: raise on any refusal."""
    logs = {1: [_log(f"shipmate-state-digest={D1} attempt=1")], 2: [_log()]}
    jobs = [OWN, _job("a", jid=1), _job("b", jid=2)]
    code, out = _Run(monkeypatch, tmp_path, [_check("a"), _check("b")], [jobs], logs)()
    assert code == 0
    assert out == {
        "cells": [_row("a", D1)],
        "report": _report(
            state=[_check("a")], refused={_check("b"): "no state line in the cell's log"}
        ),
    }


def test_no_log_line_is_printed(monkeypatch, tmp_path, capsys):
    """A reprinted `##[` or `::` line is executed by the runner. Mutation: print each matched
    line."""
    hostile = [f"::error::x shipmate-state-digest={D1} attempt=1", "##[group]boom"]
    logs = {1: [_log(f"shipmate-state-digest={D1} attempt=1", *hostile)]}
    _Run(monkeypatch, tmp_path, [_check("a")], [[OWN, _job("a", jid=1)]], logs)()
    captured = capsys.readouterr()
    printed = captured.out + captured.err
    for line in (*hostile, f"shipmate-state-digest={D1} attempt=1"):
        assert line not in printed
    assert captured.out == "state-read: apply / a / dev-eu: state\n"
