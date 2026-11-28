"""`apply-release guard`: a stage starts only once every earlier stage's cells applied and saved
in this run attempt. Rows are raw-shaped (`id`, `name`, `status`, `conclusion`, `run_attempt`,
`steps`); the re-run cases are raw captures (`fixtures/jobs-rerun-*-attempt2.txt`) whose job
names are renamed to the stage shape here, each probe `run` job standing for a cell's `apply`
and `save` rows."""

import json

import _shipmate
import pytest
from _loader import ENGINE, load_script

ar = load_script("apply-release")

APPLY = "apply / run / waves"
DEPLOY = "deploy / waves"
OWN = 900
FIXTURES = ENGINE / "scripts" / "tests" / "fixtures"


def row(name, conclusion="success", status="completed", job_id=1):
    return {
        "id": job_id,
        "name": name,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "run_attempt": 1,
        "steps": [],
    }


def cell(prefix, key, stack, apply="success", save="success", env="dev"):
    return [
        row(f"{prefix} / {key} / {env} / {stack} / apply", apply),
        row(f"{prefix} / {key} / {env} / {stack} / save", save),
    ]


def own(prefix, key):
    return row(f"{prefix} / {key} / entry", None, "in_progress", OWN)


@pytest.fixture
def listing(monkeypatch):
    """Serves each listing from a queue of job lists through the real paged reader, 100 jobs a
    page; returns `(set_listings, sleeps, paths)`. The clock advances by each sleep."""
    clock = [0.0]
    sleeps, paths, queue = [], [], []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def gh_json(path, run=None, *, retry=True):
        paths.append(path)
        jobs = queue[0] if len(queue) == 1 else queue.pop(0)
        page = int(path.rsplit("page=", 1)[1])
        return {"total_count": len(jobs), "jobs": jobs[(page - 1) * 100 : page * 100]}

    monkeypatch.setattr(_shipmate, "_sleep", sleep)
    monkeypatch.setattr(_shipmate, "gh_json", gh_json)
    monkeypatch.setattr(ar, "_clock", lambda: clock[0])
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("SHIPMATE_JOB_ID", str(OWN))
    return queue.extend, sleeps, paths


def guard(monkeypatch, keys, counts, n):
    """Run guard mode; the `::error::` it raises, or None when it passes."""
    monkeypatch.setenv("SHIPMATE_STAGE_KEYS", json.dumps(keys))
    monkeypatch.setenv("SHIPMATE_STAGE_COUNTS", json.dumps(counts))
    monkeypatch.setenv("SHIPMATE_STAGE_N", str(n))
    try:
        ar.main(["guard"])
    except SystemExit as exc:
        return exc.code
    return None


@pytest.mark.parametrize("prefix", [APPLY, DEPLOY])
def test_a_healthy_second_stage_passes_under_either_prefix(monkeypatch, listing, prefix):
    """Keys keep gaps (`L0-W0`, `L0-W3`); the guard counts by key, not index. Mutation: do not
    strip the own key (pass the whole `level_of` name to `guard_verdict`) -> refused."""
    set_listings, sleeps, paths = listing
    set_listings(
        [[*cell(prefix, "L0-W0", "dns"), *cell(prefix, "L0-W0", "app"), own(prefix, "L0-W3")]]
    )
    assert guard(monkeypatch, ["L0-W0", "L0-W3"], [2, 1], 1) is None
    assert sleeps == []
    assert paths == ["repos/acme/demo/actions/runs/77/attempts/2/jobs?per_page=100&page=1"]


def test_a_cancelled_earlier_cell_refuses_at_once(monkeypatch, listing):
    """Mutation: drop the conclusion check (`if conclusion:` for `== "success"`) -> passes."""
    set_listings, sleeps, _ = listing
    set_listings([[*cell(APPLY, "L0-W0", "dns", apply="cancelled"), own(APPLY, "L0-W1")]])
    assert guard(monkeypatch, ["L0-W0", "L0-W1"], [1, 1], 1) == (
        "::error::stage L0-W1 cannot start: cell dev / dns of stage L0-W0 concluded cancelled "
        "in its apply job"
    )
    assert sleeps == []


def test_a_present_failed_save_refuses_at_once(monkeypatch, listing):
    """A `save` job whose state step succeeded and whose check write failed still refuses.
    Mutation: count `save` rows without their conclusion -> passes."""
    set_listings, sleeps, _ = listing
    set_listings([[*cell(APPLY, "L0-W0", "dns", save="failure"), own(APPLY, "L0-W1")]])
    assert guard(monkeypatch, ["L0-W0", "L0-W1"], [1, 1], 1) == (
        "::error::stage L0-W1 cannot start: cell dev / dns of stage L0-W0 concluded failure "
        "in its save job"
    )
    assert sleeps == []


def test_a_missing_cell_beside_a_duplicate_row_cell_refuses(monkeypatch, listing):
    """Mutation: count successful `apply` rows instead of distinct cells -> passes."""
    set_listings, sleeps, _ = listing
    twice = cell(APPLY, "L0-W0", "dns") * 2
    set_listings([[*twice, own(APPLY, "L0-W1")]])
    assert guard(monkeypatch, ["L0-W0", "L0-W1"], [2, 1], 1) == (
        "::error::stage L0-W1 cannot start: stage L0-W0 lists 1 of 2 cells applied and saved"
    )
    assert sleeps == [10] * 12


def test_more_cells_than_the_stage_count_refuses_at_once(monkeypatch, listing):
    """Mutation: drop the `len(parts) > count` refusal -> passes."""
    set_listings, sleeps, _ = listing
    set_listings(
        [[*cell(APPLY, "L0-W0", "dns"), *cell(APPLY, "L0-W0", "app"), own(APPLY, "L0-W1")]]
    )
    assert guard(monkeypatch, ["L0-W0", "L0-W1"], [1, 1], 1) == (
        "::error::stage L0-W1 cannot start: stage L0-W0 lists 2 cells, not 1"
    )
    assert sleeps == []


def test_a_stage_run_out_of_order_refuses(monkeypatch, listing):
    """Stage `L0-W1` lists no row at all. Mutation: skip stages with zero rows -> passes."""
    set_listings, _, _ = listing
    set_listings([[*cell(APPLY, "L0-W0", "dns"), own(APPLY, "L0-W2")]])
    assert guard(monkeypatch, ["L0-W0", "L0-W1", "L0-W2"], [1, 1, 1], 2) == (
        "::error::stage L0-W2 cannot start: stage L0-W1 lists 0 of 1 cells applied and saved"
    )


def test_the_literal_row_of_a_skipped_cell_matrix_is_not_a_cell(monkeypatch, listing):
    """A skipped stage's cell matrix lists one `skipped` row under its literal name, and a stage's
    `entry` row is no cell either. Mutation: count every row under the stage, not only
    `apply` / `save` ones -> refused."""
    set_listings, _, _ = listing
    literal = row(
        f"{APPLY} / L0-W0 / ${{{{ matrix.environment }}}} / ${{{{ matrix.stack }}}}", "skipped"
    )
    entry = row(f"{APPLY} / L0-W0 / entry", "skipped")
    set_listings([[literal, entry, *cell(APPLY, "L0-W0", "dns"), own(APPLY, "L0-W1")]])
    assert guard(monkeypatch, ["L0-W0", "L0-W1"], [1, 1], 1) is None


def test_every_page_of_the_attempt_is_read(monkeypatch, listing):
    """The earlier stage's second cell sits on page 2. Mutation: read the first page only
    (`max_pages=1` on the attempt listing) -> refused."""
    set_listings, _, paths = listing
    filler = [row(f"plan / dev / s{i}") for i in range(99)]
    jobs = [
        *cell(APPLY, "L0-W0", "dns"),
        *filler,
        *cell(APPLY, "L0-W0", "app"),
        own(APPLY, "L0-W1"),
    ]
    set_listings([jobs])
    assert guard(monkeypatch, ["L0-W0", "L0-W1"], [2, 1], 1) is None
    assert [p.rsplit("&", 1)[1] for p in paths] == ["page=1", "page=2"]


def test_an_unconcluded_row_waits_then_passes_once_it_concludes(monkeypatch, listing):
    """Mutation: refuse an unconcluded row at once -> refused."""
    set_listings, sleeps, _ = listing
    running = [
        row(f"{APPLY} / L0-W0 / dev / dns / apply"),
        row(f"{APPLY} / L0-W0 / dev / dns / save", status="in_progress"),
    ]
    set_listings(
        [[*running, own(APPLY, "L0-W1")], [*cell(APPLY, "L0-W0", "dns"), own(APPLY, "L0-W1")]]
    )
    assert guard(monkeypatch, ["L0-W0", "L0-W1"], [1, 1], 1) is None
    assert sleeps == [10]


def test_an_unconcluded_row_refuses_at_the_deadline(monkeypatch, listing):
    """Thirteen listings, ten seconds apart, then a refusal naming the observed cell. Mutation:
    treat an unconcluded row as success -> passes."""
    set_listings, sleeps, paths = listing
    running = [
        row(f"{APPLY} / L0-W0 / dev / dns / apply"),
        row(f"{APPLY} / L0-W0 / dev / dns / save", status="queued"),
    ]
    set_listings([[*running, own(APPLY, "L0-W1")]])
    assert guard(monkeypatch, ["L0-W0", "L0-W1"], [1, 1], 1) == (
        "::error::stage L0-W1 cannot start: cell dev / dns of stage L0-W0 has no conclusion in "
        "its save job"
    )
    assert sleeps == [10] * 12
    assert len(paths) == 13


def test_a_job_outside_its_own_stage_refuses_at_once(monkeypatch, listing):
    """The calling job must run in `keys[n]`. Mutation: skip the own-key comparison -> passes."""
    set_listings, sleeps, _ = listing
    set_listings([[*cell(APPLY, "L0-W0", "dns"), own(APPLY, "L0-W2")]])
    assert guard(monkeypatch, ["L0-W0", "L0-W1"], [1, 1], 1) == (
        f"::error::stage L0-W1 cannot start: job {OWN} runs as {APPLY} / L0-W2, not in stage L0-W1"
    )
    assert sleeps == []


@pytest.mark.parametrize(
    ("keys", "counts", "n"),
    [(["L0-W0", "L0-W1"], [1], 1), (["L0-W0", "L0-W1"], [1, 1], 2), (["L0", "L0-W1"], [1, 1], 1)],
)
def test_malformed_stage_inputs_refuse(monkeypatch, listing, keys, counts, n):
    """Mutation: drop the input check -> no such refusal."""
    assert guard(monkeypatch, keys, counts, n) == (
        "::error::the stage guard needs keys and counts, JSON lists of one length, a stage index "
        "n inside them and the calling job's id"
    )


def _rerun(run_id):
    """A raw capture's jobs, each `stages (k) / <c> / run` renamed to `L0-W<k>` cell rows of env
    `dev` (apply and save) and each `stages (k) / entry` to that stage's `entry`."""
    text = (FIXTURES / f"jobs-rerun-{run_id}-attempt2.txt").read_text(encoding="utf-8")
    jobs = json.loads("".join(line for line in text.splitlines(True) if not line.startswith("#")))[
        "jobs"
    ]
    out = []
    for job in jobs:
        head, _, rest = job["name"].partition(") / ")
        if not head.startswith("stages ("):
            out.append(job)
            continue
        key = f"L0-W{head.removeprefix('stages (')}"
        if rest == "entry":
            out.append({**job, "name": f"{APPLY} / {key} / entry"})
        else:
            stack = rest.removesuffix(" / run")
            out += [
                {**job, "name": f"{APPLY} / {key} / dev / {stack} / {p}"} for p in ("apply", "save")
            ]
    return out


#: Each capture's stage counts and its third stage's `entry` job, the guard's caller.
RERUNS = {46155513332: ([2, 2, 1], 141011371805), 46159304747: ([1, 2, 1], 141023415184)}


@pytest.mark.parametrize("run_id", RERUNS)
def test_a_re_run_attempt_counts_its_carried_green_cells(monkeypatch, listing, run_id):
    """The attempt endpoint lists the cells attempt 1 passed again under attempt 2, with their
    steps and conclusions. Mutation: do not strip the own key -> refused."""
    counts, entry = RERUNS[run_id]
    set_listings, _, _ = listing
    set_listings([_rerun(run_id)])
    monkeypatch.setenv("SHIPMATE_JOB_ID", str(entry))
    assert guard(monkeypatch, ["L0-W0", "L0-W1", "L0-W2"], counts, 2) is None


def test_a_re_run_attempt_refuses_a_carried_failed_cell(monkeypatch, listing):
    """A carried cell (`y`, attempt 1's start time) whose conclusion reads `failure`. Mutation:
    drop the conclusion check -> passes."""
    jobs = _rerun(46155513332)
    for job in jobs:
        if job["name"] == f"{APPLY} / L0-W1 / dev / y / apply":
            job["conclusion"] = "failure"
    set_listings, _, _ = listing
    set_listings([jobs])
    monkeypatch.setenv("SHIPMATE_JOB_ID", "141011371805")
    assert guard(monkeypatch, ["L0-W0", "L0-W1", "L0-W2"], [2, 2, 1], 2) == (
        "::error::stage L0-W2 cannot start: cell dev / y of stage L0-W1 concluded failure in its "
        "apply job"
    )
