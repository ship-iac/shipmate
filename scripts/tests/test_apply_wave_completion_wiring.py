"""Guards the per-wave apply-check flip and completion in `apply-env-level.yml`.

Each `waveN-release` job flips its wave's checks to `in_progress` right before that wave, from
the `by_wave` slice the caller supplied, or else `snapshot`'s; the wave needs it and runs only
when it succeeded, and it runs only when every earlier wave and release succeeded or was
skipped. A caller that supplies `by_wave` has already flipped `wave0`, so `snapshot` and
`wave0-release` skip and `wave0` runs without them. Each `waveN-complete` job writes
its own wave's checks right after that wave. It runs only when its wave reached a result: a
skipped wave's listing row carries the literal matrix template, so a completion over it matches
no cell and fails after the action's retry. It runs only when the next wave is populated: the
producer compacts the populated waves from `wave0`, so the last populated wave is the one whose
successor is empty, and `complete`, which runs right after it, writes its checks. `wave7` has
no completion job. It never fails the level (job-level
`continue-on-error`); `complete` waits for every release, wave and completion, rewrites every
check and still fails the level on a check it cannot write. No wave waits for a completion, so a
completion never delays an apply, and no completion runs before its wave.

Threat model: an accidental edit to the workflow (a dropped clause, a wrong slice, a moved
`needs:` entry). Whole parsed values against hand-written constants cover it.
"""

import re

import pytest
from _loader import workflow_jobs

WAVES = [f"wave{n}" for n in range(8)]
COMPLETIONS = [f"wave{n}-complete" for n in range(7)]

_WITH_KEY = {
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}


#: The one step list every completion job and `complete` run; each job's waves reach it only
#: through its own `env.WAVE_RESULTS`.
COMPLETION_STEPS = [
    {
        "uses": "$/actions/apply-complete",
        "with": {
            "by-wave": "${{ inputs.by_wave || needs.snapshot.outputs.by_wave }}",
            "wave-results": "${{ env.WAVE_RESULTS }}",
            "job-id": "${{ job.check_run_id }}",
            "deploy": "${{ inputs.deploy }}",
            "pr-number": "${{ inputs.pr_number }}",
            **_WITH_KEY,
        },
    }
]


def _completion(n):
    return {
        "needs": ["snapshot", f"wave{n}"],
        "if": (
            "${{ always() && (needs.snapshot.result == 'success' || inputs.by_wave != '') && "
            f"(needs.wave{n}.result == 'success' || needs.wave{n}.result == 'failure') "
            f"&& toJSON(fromJSON(inputs.waves_json).wave{n + 1}) != '[]' }}}}"
        ),
        "runs-on": "ubuntu-slim",
        "continue-on-error": True,
        "permissions": {"actions": "read", "checks": "read"},
        "environment": {"name": "shipmate-engine", "deployment": False},
        "env": {"WAVE_RESULTS": f'{{"wave{n}": "${{{{ needs.wave{n}.result }}}}"}}'},
        "steps": COMPLETION_STEPS,
    }


#: Written out in full: a template-only guard cannot catch a wrong template.
WAVE3_COMPLETE = {
    "needs": ["snapshot", "wave3"],
    "if": (
        "${{ always() && (needs.snapshot.result == 'success' || inputs.by_wave != '') && "
        "(needs.wave3.result == 'success' || needs.wave3.result == 'failure') "
        "&& toJSON(fromJSON(inputs.waves_json).wave4) != '[]' }}"
    ),
    "runs-on": "ubuntu-slim",
    "continue-on-error": True,
    "permissions": {"actions": "read", "checks": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "env": {"WAVE_RESULTS": '{"wave3": "${{ needs.wave3.result }}"}'},
    "steps": [
        {
            "uses": "$/actions/apply-complete",
            "with": {
                "by-wave": "${{ inputs.by_wave || needs.snapshot.outputs.by_wave }}",
                "wave-results": "${{ env.WAVE_RESULTS }}",
                "job-id": "${{ job.check_run_id }}",
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}

COMPLETE = {
    "needs": [
        "snapshot",
        "wave0-release",
        "wave1-release",
        "wave2-release",
        "wave3-release",
        "wave4-release",
        "wave5-release",
        "wave6-release",
        "wave7-release",
        "wave0",
        "wave1",
        "wave2",
        "wave3",
        "wave4",
        "wave5",
        "wave6",
        "wave7",
        "wave0-complete",
        "wave1-complete",
        "wave2-complete",
        "wave3-complete",
        "wave4-complete",
        "wave5-complete",
        "wave6-complete",
    ],
    "if": "${{ always() && (needs.snapshot.result == 'success' || inputs.by_wave != '') }}",
    "runs-on": "ubuntu-slim",
    "permissions": {"actions": "read", "checks": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "env": {
        "WAVE_RESULTS": (
            '{"wave0": "${{ needs.wave0.result }}", '
            '"wave1": "${{ needs.wave1.result }}", '
            '"wave2": "${{ needs.wave2.result }}", '
            '"wave3": "${{ needs.wave3.result }}", '
            '"wave4": "${{ needs.wave4.result }}", '
            '"wave5": "${{ needs.wave5.result }}", '
            '"wave6": "${{ needs.wave6.result }}", '
            '"wave7": "${{ needs.wave7.result }}"}'
        )
    },
    "steps": COMPLETION_STEPS,
}

JOB_IDS = sorted(
    ["complete", "snapshot", *WAVES] + COMPLETIONS + [f"wave{n}-release" for n in range(8)]
)

WAVE_NEEDS = {
    "wave0": ["snapshot", "wave0-release"],
    "wave1": ["wave0", "wave1-release"],
    "wave2": ["wave0", "wave1", "wave2-release"],
    "wave3": ["wave0", "wave1", "wave2", "wave3-release"],
    "wave4": ["wave0", "wave1", "wave2", "wave3", "wave4-release"],
    "wave5": ["wave0", "wave1", "wave2", "wave3", "wave4", "wave5-release"],
    "wave6": ["wave0", "wave1", "wave2", "wave3", "wave4", "wave5", "wave6-release"],
    "wave7": ["wave0", "wave1", "wave2", "wave3", "wave4", "wave5", "wave6", "wave7-release"],
}

#: Each wave's `if:`, whole: its own eligibility, then its release's success.
WAVE_IF = {
    "wave0": (
        "${{ !failure() && !cancelled() && toJSON(fromJSON(inputs.waves_json).wave0) != '[]' "
        "&& (needs.wave0-release.result == 'success' || inputs.by_wave != '') }}"
    ),
    **{
        f"wave{n}": (
            "${{ !failure() && !cancelled() && !contains(join(needs.*.result, ','), 'cancelled') "
            f"&& toJSON(fromJSON(inputs.waves_json).wave{n}) != '[]' "
            f"&& needs.wave{n}-release.result == 'success' }}}}"
        )
        for n in range(1, 8)
    },
}


def _release(n):
    snapshot_ok = "needs.snapshot.result == 'success'"
    by_wave = "needs.snapshot.outputs.by_wave"
    if n:
        snapshot_ok = "(needs.snapshot.result == 'success' || inputs.by_wave != '')"
        by_wave = "inputs.by_wave || needs.snapshot.outputs.by_wave"
    earlier = "".join(
        f" && (needs.wave{k}.result == 'success' || needs.wave{k}.result == 'skipped')"
        f" && (needs.wave{k}-release.result == 'success'"
        f" || needs.wave{k}-release.result == 'skipped')"
        for k in range(n)
    )
    return {
        "needs": ["snapshot", *(j for k in range(n) for j in (f"wave{k}", f"wave{k}-release"))],
        "if": (
            f"${{{{ !cancelled() && {snapshot_ok} && "
            f"toJSON(fromJSON(inputs.waves_json).wave{n}) != '[]'{earlier} }}}}"
        ),
        "runs-on": "ubuntu-slim",
        "permissions": {"actions": "read"},
        "environment": {"name": "shipmate-engine", "deployment": False},
        "steps": [
            {
                "uses": "$/actions/apply-release",
                "with": {
                    "snapshot": f"${{{{ toJSON(fromJSON({by_wave}).wave{n}) }}}}",
                    "deploy": "${{ inputs.deploy }}",
                    "pr-number": "${{ inputs.pr_number }}",
                    **_WITH_KEY,
                },
            }
        ],
    }


#: Written out in full: a template-only guard cannot catch a wrong template.
WAVE0_RELEASE = {
    "needs": ["snapshot"],
    "if": (
        "${{ !cancelled() && needs.snapshot.result == 'success' && "
        "toJSON(fromJSON(inputs.waves_json).wave0) != '[]' }}"
    ),
    "runs-on": "ubuntu-slim",
    "permissions": {"actions": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "steps": [
        {
            "uses": "$/actions/apply-release",
            "with": {
                "snapshot": "${{ toJSON(fromJSON(needs.snapshot.outputs.by_wave).wave0) }}",
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}

WAVE2_RELEASE = {
    "needs": ["snapshot", "wave0", "wave0-release", "wave1", "wave1-release"],
    "if": (
        "${{ !cancelled() && (needs.snapshot.result == 'success' || inputs.by_wave != '') && "
        "toJSON(fromJSON(inputs.waves_json).wave2) != '[]' && "
        "(needs.wave0.result == 'success' || needs.wave0.result == 'skipped') && "
        "(needs.wave0-release.result == 'success' || needs.wave0-release.result == 'skipped') && "
        "(needs.wave1.result == 'success' || needs.wave1.result == 'skipped') && "
        "(needs.wave1-release.result == 'success' || needs.wave1-release.result == 'skipped') }}"
    ),
    "runs-on": "ubuntu-slim",
    "permissions": {"actions": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "steps": [
        {
            "uses": "$/actions/apply-release",
            "with": {
                "snapshot": (
                    "${{ toJSON(fromJSON(inputs.by_wave || needs.snapshot.outputs.by_wave)"
                    ".wave2) }}"
                ),
                "deploy": "${{ inputs.deploy }}",
                "pr-number": "${{ inputs.pr_number }}",
                "app-id": "${{ vars.SHIPMATE_APP_ID }}",
                "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
            },
        }
    ],
}


@pytest.mark.parametrize("n", range(7))
def test_each_wave_completion_job_is_exactly(n):
    """Mutations: drop the `result` clause from `wave2-complete`'s `if:`; drop the next-wave
    clause from `wave3-complete`'s `if:`; `.wave5` for `.wave6` in `wave5-complete`'s next-wave
    clause; `.wave1` in `wave2-complete`'s `env`; `needs: [snapshot]` on `wave5-complete`; add
    `checks: write` to `wave5-complete`'s permissions; drop `continue-on-error` from
    `wave3-complete`."""
    assert workflow_jobs("apply-env-level.yml")[f"wave{n}-complete"] == _completion(n)


def test_wave3_completion_job_is_written_out():
    """Mutations: drop `continue-on-error` from `wave3-complete`; drop the next-wave clause
    from its `if:`."""
    assert workflow_jobs("apply-env-level.yml")["wave3-complete"] == WAVE3_COMPLETE


def test_complete_waits_for_every_release_wave_and_completion():
    """`complete` still fails the level, so it carries no `continue-on-error`, and no flip may
    land after it starts.

    Mutations: drop `wave6-complete` from `complete.needs`; add `continue-on-error: true` to
    `complete`; drop `wave4-release` from `complete.needs`; `wave5` under `"wave6"` in its
    `env`; `complete.if` back to `always() && needs.snapshot.result == 'success'`, which skips
    the first level's `complete` when the caller supplied `by_wave`."""
    assert workflow_jobs("apply-env-level.yml")["complete"] == COMPLETE


@pytest.mark.parametrize("job", [*COMPLETIONS, "complete"])
def test_every_completion_runs_the_one_shared_step_list(job):
    """Mutations: `${{ env.WAVE_RESULT }}` in the anchored `with:`; `complete`'s steps written
    out with a different `job-id`; the anchored `by-wave` reads
    `needs.snapshot.outputs.by_wave` alone."""
    assert workflow_jobs("apply-env-level.yml")[job]["steps"] == COMPLETION_STEPS


def test_each_wave_needs_its_release_and_no_completion():
    """Mutations: add `wave0-complete` to `wave1.needs`; drop `wave3-release` from
    `wave3.needs`."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert {w: jobs[w]["needs"] for w in WAVES} == WAVE_NEEDS


def test_each_wave_runs_only_after_its_release_succeeded():
    """Mutations: drop `&& needs.wave5-release.result == 'success'` from `wave5.if`; `!=` for
    `==` in it; drop `|| inputs.by_wave != ''` from `wave0.if`."""
    jobs = workflow_jobs("apply-env-level.yml")
    assert {w: jobs[w]["if"] for w in WAVES} == WAVE_IF


@pytest.mark.parametrize("n", range(8))
def test_each_release_job_is_exactly(n):
    """Mutations: drop `snapshot` from `wave2-release.needs`; drop the `!cancelled()` prefix from
    `wave4-release.if`; `'failure'` for `'skipped'` in one clause of `wave6-release.if`; add
    `continue-on-error: true` to `wave1-release`; `.wave2` in `wave3-release`'s slice;
    `wave3-release`'s slice reads `needs.snapshot.outputs.by_wave` alone."""
    assert workflow_jobs("apply-env-level.yml")[f"wave{n}-release"] == _release(n)


@pytest.mark.parametrize(("n", "expected"), [(0, WAVE0_RELEASE), (2, WAVE2_RELEASE)])
def test_release_jobs_are_written_out(n, expected):
    """Mutations: drop `snapshot` from `wave2-release.needs`; drop the `!cancelled()` prefix
    from `wave0-release.if`."""
    assert workflow_jobs("apply-env-level.yml")[f"wave{n}-release"] == expected


#: Runs only when the caller supplied no `by_wave`, and publishes only the per-wave map.
SNAPSHOT = {
    "if": "${{ inputs.by_wave == '' }}",
    "runs-on": "ubuntu-slim",
    "permissions": {"checks": "read", "actions": "read"},
    "environment": {"name": "shipmate-engine", "deployment": False},
    "outputs": {"by_wave": "${{ steps.snap.outputs.by_wave }}"},
    "steps": [
        {
            "uses": "$/actions/verify-environments",
            "with": {"waves-json": "${{ inputs.waves_json }}"},
        },
        {
            "id": "snap",
            "uses": "$/actions/apply-snapshot",
            "with": {
                "head-sha": "${{ inputs.head_sha }}",
                "waves-json": "${{ inputs.waves_json }}",
                **_WITH_KEY,
            },
        },
    ],
}


def test_snapshot_job_is_exactly():
    """Mutations: drop `snapshot`'s `by_wave` output; add back a `snapshot` output;
    `snapshot.if` -> `inputs.by_wave != ''`."""
    assert workflow_jobs("apply-env-level.yml")["snapshot"] == SNAPSHOT


def test_the_job_set_is_exactly():
    """Mutations: rename `wave4-complete`; delete `wave6-release`; re-add `wave7-complete`."""
    assert sorted(workflow_jobs("apply-env-level.yml")) == JOB_IDS


def _runs(expr, populated, results, supplied):
    """The value of a job's `if:` for the forms the completion jobs write: `always()`,
    `needs.<job>.result`, the `waves_json` emptiness test, the `by_wave` supplied test, `==`,
    `||`, `&&`, parentheses and quoted strings; anything else fails the test."""
    py = expr.removeprefix("${{").removesuffix("}}").strip()
    py = py.replace("inputs.by_wave != ''", str(supplied))
    py = re.sub(
        r"toJSON\(fromJSON\(inputs\.waves_json\)\.(wave\d)\) != '\[\]'",
        lambda m: str(m[1] in populated),
        py,
    )
    py = re.sub(r"needs\.([\w-]+)\.result", lambda m: repr(results[m[1]]), py)
    py = py.replace("always()", "True").replace("&&", " and ").replace("||", " or ")
    assert re.fullmatch(r"(?:\s|True|False|and|or|==|[()]|'[a-z]*')*", py), py
    # The fullmatch above admits only literals and operators.
    return eval(py, {"__builtins__": {}})  # noqa: S307


@pytest.mark.parametrize(("snapshot", "supplied"), [("success", False), ("skipped", True)])
def test_two_populated_waves_complete_the_first_and_leave_the_last_to_complete(snapshot, supplied):
    """With `wave0` and `wave1` populated and applied, `wave0-complete` runs (a later wave
    exists), `wave1-complete` and every later completion skip, and `complete` runs and writes
    wave 1, whether `snapshot` ran or the caller supplied `by_wave`.

    Mutation: drop the next-wave clause from `wave1-complete`'s `if:` -- it runs."""
    jobs = workflow_jobs("apply-env-level.yml")
    results = {"snapshot": snapshot, "wave0": "success", "wave1": "success"}
    results |= {w: "skipped" for w in WAVES[2:]}
    ran = {
        job
        for job in [*COMPLETIONS, "complete"]
        if _runs(jobs[job]["if"], {"wave0", "wave1"}, results, supplied)
    }
    assert ran == {"wave0-complete", "complete"}
