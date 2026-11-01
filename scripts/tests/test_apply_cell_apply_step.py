"""actions/apply-cell's apply step must not let a cosmetic `tee` failure strand an
otherwise-successful apply's check pending, nor let a `tee` success paper over a real apply
failure.

The step pipes `terramate run ... -- tofu apply ... 2>&1 | tee "$RUNNER_TEMP/apply.txt"` under
`set -euo pipefail`. With pipefail live across that pipeline -- or plain errexit reacting to the
pipeline's last-command status -- a `tee` failure alone fails the whole step: a full
$RUNNER_TEMP, a disk hiccup mid-write, nothing to do with tofu. `Save state` has already
persisted the advanced state by the time `Complete the apply check` would run, so a
stranded-pending check here is unrecoverable without a re-plan, the saved plan now being stale
against the advanced state.

The step therefore captures tofu's own exit code via `PIPESTATUS[0]`, with errexit turned off for
that one pipeline, and exits on that captured code explicitly. A `tee` failure can then never
mask a real success, and a real apply failure still fails the step -- the
pending-apply-check-as-work-queue invariant, where a failed apply must still leave its check
pending.

The steps ahead of it bind the plan text a reviewer approved to the plan about to run: they
re-render the stored `.otplan` and refuse unless the render's digest equals the one the trusted
summary job recorded on the apply check. They are separate steps from the apply, and from each
other, so that each carries its own blocked reason in `scripts/apply-cell-summary`; at runtime
they are one sequence in one workspace, which is what this file executes. Their refusals are
guarded here on ordering, not only on exit code -- the shape check must land before `init`, and a
digest mismatch must land before the apply -- because a guard that runs after the thing it guards
exits non-zero all the same and protects nothing.
"""

import hashlib
import os
import pathlib
import sys

import pytest
from _loader import ACTIONS, action_steps, action_yaml, bash_only, run_step, step_by

#: The four steps the apply half of the cell is split across, in the order the runner executes
#: them, each as (action, step id). The three ahead of the apply each carry a blocked reason of
#: their own -- a wiring slip, a failed init and a tampered plan need different remedies -- and
#: together the four are the one script this file exercises. The init body is `cell-init`'s,
#: which apply-cell runs as its `init` step.
_STEP_IDS = (
    ("apply-cell", "digest-input"),
    ("cell-init", "init"),
    ("apply-cell", "plan-digest"),
    ("apply-cell", "apply"),
)


def _apply_step():
    return step_by("apply-cell", id="apply")


def test_the_apply_half_is_split_across_its_four_attributable_steps():
    """Ordering, and that each half is a step in its own right. A refusal folded back inside
    another step loses its blocked reason: scripts/apply-cell-summary maps a step id to one, so a
    tamper detection with no id of its own renders as `failed` with no reason -- the same
    reviewer-facing bucket as a real apply error that may have mutated infrastructure."""
    ids = [s.get("id") for s in action_steps("apply-cell") if s.get("id")]
    start = ids.index("digest-input")
    # Written out here rather than compared against _STEP_IDS: the harness concatenates the
    # bodies in that constant's order, so a swap made in both places would leave every runtime
    # assertion in this file green and this the only test able to catch it. Locating and
    # restoring state run inside `cell-init` after init, and the harness leaves them out: neither
    # touches the stored plan.
    assert tuple(ids[start : start + 4]) == ("digest-input", "init", "plan-digest", "apply")


def test_apply_step_captures_pipestatus_and_exits_on_it():
    run = _apply_step()["run"]
    assert "PIPESTATUS[0]" in run
    last_line = run.strip().splitlines()[-1].strip()
    assert last_line in ('exit "$status"', "exit $status")


#: The plan text the stubbed `tofu show` renders, and its digest. Computed rather than
#: hand-written: it is the fixture the step is fed, not a property under test. The hand-written
#: constant is `_SHOW_TEXT` in test_engine_owns_tofu_invocation.py, which pins the command that
#: produces such text.
_PLAN_TEXT = b"plan text\n"
_PLAN_SHA256 = hashlib.sha256(_PLAN_TEXT).hexdigest()


def _run_step(
    tmp_path,
    *,
    terramate_body,
    tee_body,
    init_body="return 0",
    tofu_body="printf 'plan text\\n'",
    plan_sha256=_PLAN_SHA256,
):
    """Execute the real, unmodified step script from action.yml with `terramate` and `tee`
    replaced by bash functions. Bash resolves a function before searching PATH, so this needs no
    fake executables and no exec bits, which are fragile to set up portably on a Windows dev
    box.

    Every tofu call goes through `terramate run`, so the `terramate` stub has one arm per tofu
    subcommand -- init, the plan-text re-render, apply -- and refuses any other call. The init and
    apply arms touch a marker before running their body, because "init never ran" and "the apply
    never ran" are the orderings the refusal tests turn on, and an exit code alone shows neither.

    `PLAN_SHA256`, `ENV` and `STACK` are exported for every caller: under `set -u` an unset
    one aborts the script before it reaches the behaviour under test, which would green a refusal
    test for the wrong reason. The default is a well-formed digest of the default `tofu` stub's
    output, so the callers testing the `tee` contract still run all the way to the apply.

    The script runs in its own empty directory, standing in for the consumer's checkout: the
    action must leave nothing at its root, and pytest's own cwd is the engine tree.
    """
    # The four step bodies in runner order, each in its own subshell under `set -e`, as the runner
    # runs each step in its own `bash -e`: a body's `exit` ends that step, and a non-zero one ends
    # the sequence. Not `( ... ) || exit $?`, which would switch off errexit inside every body.
    run = "set -e\n" + "".join(
        f"(\n{step_by(action, id=step_id)['run']}\n)\n" for action, step_id in _STEP_IDS
    )
    # `terramate_body` ends in `exit`, which dies in a subshell inside the apply pipeline but would
    # kill this whole script on a plain line, so the stub dispatches on the tofu subcommand.
    harness = (
        'terramate() { case "$*" in\n'
        f'  *"-- tofu init "*) touch "$RUNNER_TEMP/init-ran" ; {init_body} ;;\n'
        f'  *"-- tofu show "*) {tofu_body} ;;\n'
        f'  *"-- tofu apply "*) touch "$RUNNER_TEMP/apply-ran" ; {terramate_body} ;;\n'
        '  *) echo "unstubbed terramate call: $*" >&2 ; return 98 ;;\n'
        "esac ; }\n"
        f"tee() {{ {tee_body} ; }}\n"
        # The test's own interpreter: on a Windows host `python3` on PATH can be the Store alias.
        f'python3() {{ "{pathlib.Path(sys.executable).as_posix()}" "$@" ; }}\n'
    ) + run
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    env = {
        **os.environ,
        "STACK": "stacks/app",
        "RUNNER_TEMP": str(runner_temp),
        "PLAN_SHA256": plan_sha256,
        "ENV": "dev-eu",
        # The init body names `tofu-error` through it under `set -u`.
        "GITHUB_ACTION_PATH": str(ACTIONS / "cell-init"),
    }
    return run_step(tmp_path, harness, env, cwd=checkout)


def _ran(tmp_path, marker):
    return (tmp_path / "rt" / marker).exists()


@bash_only
def test_successful_apply_survives_a_failing_tee(tmp_path):
    # tee copies the output but reports failure, a disk-full write for instance. The apply itself
    # was fine.
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1" ; exit 1',
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@bash_only
def test_failed_apply_still_fails_the_step_even_if_tee_succeeds(tmp_path):
    # The pending-check invariant: a real apply failure must still fail the step, leaving the
    # apply check pending, regardless of tee's own outcome.
    r = _run_step(
        tmp_path,
        terramate_body="echo boom >&2 ; exit 7",
        tee_body='cat > "$1" ; exit 0',
    )
    assert r.returncode == 7, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@bash_only
def test_failed_apply_and_failed_tee_still_fails_the_step(tmp_path):
    r = _run_step(
        tmp_path,
        terramate_body="echo boom >&2 ; exit 7",
        tee_body='cat > "$1" ; exit 1',
    )
    assert r.returncode == 7, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@bash_only
def test_failed_init_fails_the_step_before_the_apply(tmp_path):
    """A failed init must end the sequence rather than fall through to an apply of a plan
    against an uninitialized directory. The init step exits on tofu's own status, taken from
    `PIPESTATUS[0]` past its `tee`. Mutation: `cell-init`'s init `exit "$status"` becomes
    `exit 0`."""
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='[ "${1##*/}" = apply.txt ] && echo TEE_RAN >&2 ; cat > "$1" ; exit 0',
        init_body="echo init boom >&2 ; return 5",
    )
    assert r.returncode == 5, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    # The apply is piped into tee, whose stub swallows that output into apply.txt, so
    # `"applied" not in r.stdout` holds even when the pipeline did run. tee's own stderr escapes
    # the pipe, and the stub marks only the apply's tee, not the init's.
    assert "TEE_RAN" not in r.stderr, f"apply ran despite a failed init: {r.stderr!r}"


def test_the_action_declares_a_plan_sha256_input():
    spec = action_yaml("apply-cell")["inputs"]["plan-sha256"]
    assert spec["required"] is True
    description = spec["description"]
    assert "sha256" in description and "plan" in description, (
        "plan-sha256's description must say what the value is and what refuses on it: "
        f"{description!r}"
    )


@bash_only
def test_an_empty_plan_sha256_refuses_before_init(tmp_path):
    """An unwired input arrives empty -- `required: true` is not enforced for a composite action --
    and must be refused before any work, `init` included. Asserted on the init marker, because
    a check moved below `init` still exits non-zero and would pass an exit-code-only test."""
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1"',
        plan_sha256="",
    )
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert not _ran(tmp_path, "init-ran"), "init ran before the digest shape was checked"


@bash_only
@pytest.mark.parametrize(
    "digest",
    ["a" * 63, "a" * 65, "A" * 64, "z" * 64],
    ids=["short", "long", "uppercase", "non-hex"],
)
def test_a_malformed_plan_sha256_refuses_before_init(tmp_path, digest):
    """The shape check is anchored and lowercase-hex only. Unanchoring `^[0-9a-f]{64}$` reds the
    65-character case alone; the other three are refused by length or alphabet either way, and are
    here to pin those two halves of the pattern rather than the anchors."""
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1"',
        plan_sha256=digest,
    )
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert not _ran(tmp_path, "init-ran"), "init ran before the digest shape was checked"


@bash_only
def test_a_matching_render_reaches_the_apply(tmp_path):
    """The harness runs `cell-init`'s real init body. Mutation: prefix that `run:` with
    `exit 1;`."""
    r = _run_step(tmp_path, terramate_body="echo applied ; exit 0", tee_body='cat > "$1"')
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _ran(tmp_path, "apply-ran"), "the apply was skipped for a plan that renders as reviewed"


@bash_only
def test_a_mismatched_render_refuses_and_never_applies(tmp_path):
    """The stored plan renders to text other than the reviewed one. Nothing may be applied: a
    warning here would be this step's only fail-open check. The refusal names the digest of the
    render the stub produced, so it is the wrapped `tofu show` that was compared and refused.
    Mutation: drop the stub's `tofu show` arm (the render then fails as an unstubbed call)."""
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1"',
        tofu_body="printf 'a different plan\\n'",
    )
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert not _ran(tmp_path, "apply-ran"), "applied a plan whose text was never reviewed"
    actual = hashlib.sha256(b"a different plan\n").hexdigest()
    assert (
        "::error::apply aborted for dev-eu/stacks/app: the stored plan does not render to the plan"
        f" text that was reviewed (expected {_PLAN_SHA256}, got {actual}). Nothing has been"
        " applied. Re-plan (comment shipmate plan or push), or after the merge open a new pull"
        " request touching the stack."
    ) in r.stdout.splitlines()


@bash_only
def test_the_render_lands_in_runner_temp_and_not_the_checkout(tmp_path):
    """The action runs in the consumer's checkout and must leave nothing at its root."""
    r = _run_step(tmp_path, terramate_body="echo applied ; exit 0", tee_body='cat > "$1"')
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert (tmp_path / "rt" / "rendered-plan.txt").is_file()
    assert sorted(p.name for p in (tmp_path / "checkout").iterdir()) == []


@bash_only
def test_a_render_refused_for_another_tofu_version_names_the_replan_remedy(tmp_path):
    """The render fails on a plan file from another OpenTofu version: nothing applies, the raw
    diagnostic stays in the log, and `tofu-error` names the re-plan. Mutations: drop the
    `tofu-error` line from `plan-digest`; drop its `cat` of the render's stderr; drop the
    failure branch's `exit 1` (the empty render then reaches the digest mismatch)."""
    fixture = (pathlib.Path(__file__).parent / "fixtures/tofu-version-skew.txt").as_posix()
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1"',
        tofu_body=f'cat "{fixture}" >&2 ; return 1',
    )
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert not _ran(tmp_path, "apply-ran"), "applied after the render failed"
    assert "Error: Couldn't show local plan" in r.stderr.splitlines()
    assert "does not render to the plan text" not in r.stdout, "reached the digest"
    assert (
        "::error::The stored plan was made by another OpenTofu version: `tools.tofu` in"
        " .github/shipmate-config.yml or the engine's VERSIONS changed since the plan. Nothing was"
        " applied. Re-plan (comment shipmate plan or push), or after the merge open a new pull"
        " request touching the stack."
    ) in r.stdout.splitlines()


@bash_only
def test_a_successful_render_keeps_its_stderr_in_the_log(tmp_path):
    """A warning from a render that succeeds still reaches the log. Mutation: move `plan-digest`'s
    `cat` of show.txt inside the failure branch."""
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1"',
        tofu_body="printf 'plan text\\n' ; echo 'Warning: render warning' >&2",
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "Warning: render warning" in r.stderr.splitlines()
