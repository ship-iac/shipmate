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

The same step also binds the plan text a reviewer approved to the plan about to run: it re-renders
the stored `.otplan` and refuses unless the render's digest equals the one the trusted summary job
recorded on the apply check. Both refusals in it are guarded here on ordering, not only on exit
code -- the shape check must land before `init`, and a digest mismatch must land before the apply
-- because a guard that runs after the thing it guards exits non-zero all the same and protects
nothing.
"""

import hashlib
import os
import subprocess

import pytest
from _loader import action_steps, action_yaml, usable_bash

_BASH = usable_bash()


def _apply_step():
    matches = [s for s in action_steps("apply-cell") if s.get("id") == "apply"]
    assert len(matches) == 1, f"expected exactly one apply step (id: apply), got {len(matches)}"
    return matches[0]


def test_apply_step_captures_pipestatus_and_exits_on_it():
    run = _apply_step()["run"]
    assert "PIPESTATUS[0]" in run
    last_line = run.strip().splitlines()[-1].strip()
    assert last_line in ('exit "$status"', "exit $status")


#: The plan text the `tofu` stub renders, and its digest. Computed rather than hand-written: it is
#: the fixture the step is fed, not a property under test. The hand-written constant is `_RENDER`
#: in test_engine_owns_tofu_invocation.py, which pins the command that produces such text.
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
    """Execute the real, unmodified step script from action.yml with `terramate`, `tofu` and `tee`
    replaced by bash functions. Bash resolves a function before searching PATH, so this needs no
    fake executables and no exec bits, which are fragile to set up portably on a Windows dev
    box.

    The step reaches tofu two ways -- through `terramate run` for init and apply, and directly for
    the plan-text re-render -- so both names are stubbed. Each `terramate` arm touches a marker
    before running its body, because "init never ran" and "the apply never ran" are the orderings
    the refusal tests turn on, and an exit code alone shows neither.

    `PLAN_SHA256`, `ENV` and `STACK_NAME` are exported for every caller: under `set -u` an unset
    one aborts the script before it reaches the behaviour under test, which would green a refusal
    test for the wrong reason. The default is a well-formed digest of the default `tofu` stub's
    output, so the callers testing the `tee` contract still run all the way to the apply.

    The script runs in its own empty directory, standing in for the consumer's checkout: the
    action must leave nothing at its root, and pytest's own cwd is the engine tree.
    """
    assert _BASH is not None  # callers are skipif-gated on this; narrows the type too
    run = _apply_step()["run"]
    # The step calls terramate twice: a plain `init` line, then the teed apply. `terramate_body`
    # ends in `exit`, which dies in a subshell inside the pipeline but would kill this whole
    # script on the init line, so the stub dispatches on the tofu subcommand.
    harness = (
        f'terramate() {{ case "$*" in *"tofu apply"*) touch "$RUNNER_TEMP/apply-ran" ; '
        f'{terramate_body} ;; *) touch "$RUNNER_TEMP/init-ran" ; {init_body} ;; esac ; }}\n'
        f"tofu() {{ {tofu_body} ; }}\n"
        f"tee() {{ {tee_body} ; }}\n"
    ) + run
    script = tmp_path / "step.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    env = dict(os.environ)
    env["STACK"] = "stacks/app"
    env["RUNNER_TEMP"] = str(runner_temp)
    env["PLAN_SHA256"] = plan_sha256
    env["ENV"] = "dev-eu"
    env["STACK_NAME"] = "app"
    return subprocess.run(
        [_BASH, str(script)],
        env=env,
        cwd=str(checkout),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _ran(tmp_path, marker):
    return (tmp_path / "rt" / marker).exists()


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_successful_apply_survives_a_failing_tee(tmp_path):
    # tee copies the output but reports failure, a disk-full write for instance. The apply itself
    # was fine.
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1" ; exit 1',
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_failed_apply_still_fails_the_step_even_if_tee_succeeds(tmp_path):
    # The pending-check invariant: a real apply failure must still fail the step, leaving the
    # apply check pending, regardless of tee's own outcome.
    r = _run_step(
        tmp_path,
        terramate_body="echo boom >&2 ; exit 7",
        tee_body='cat > "$1" ; exit 0',
    )
    assert r.returncode == 7, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_failed_apply_and_failed_tee_still_fails_the_step(tmp_path):
    r = _run_step(
        tmp_path,
        terramate_body="echo boom >&2 ; exit 7",
        tee_body='cat > "$1" ; exit 1',
    )
    assert r.returncode == 7, f"stdout={r.stdout!r} stderr={r.stderr!r}"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_failed_init_fails_the_step_before_the_apply(tmp_path):
    """init runs outside the pipeline, and errexit must stop the step there rather than fall
    through to an apply of a plan against an uninitialized directory.

    The stub uses `return 5`, not `exit 5`: the init line is a plain function call in the current
    shell, so an `exit` body terminates the script whatever the ordering, and this test could
    then not fail on the regression it names -- moving `set +e` above the init line. Returning
    leaves errexit to do the work."""
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='echo TEE_RAN >&2 ; cat > "$1" ; exit 0',
        init_body="echo init boom >&2 ; return 5",
    )
    assert r.returncode == 5, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    # The apply is piped into tee, whose stub swallows that output into apply.txt, so
    # `"applied" not in r.stdout` holds even when the pipeline did run. tee's own stderr escapes
    # the pipe, and tee runs if and only if the pipeline did.
    assert "TEE_RAN" not in r.stderr, f"apply ran despite a failed init: {r.stderr!r}"


def test_the_action_declares_a_plan_sha256_input():
    spec = action_yaml("apply-cell")["inputs"]["plan-sha256"]
    assert spec["required"] is True
    description = spec["description"]
    assert "sha256" in description and "plan" in description, (
        "plan-sha256's description must say what the value is and what refuses on it: "
        f"{description!r}"
    )


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
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


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
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


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_matching_render_reaches_the_apply(tmp_path):
    r = _run_step(tmp_path, terramate_body="echo applied ; exit 0", tee_body='cat > "$1"')
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _ran(tmp_path, "apply-ran"), "the apply was skipped for a plan that renders as reviewed"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_a_mismatched_render_refuses_and_never_applies(tmp_path):
    """The stored plan renders to text other than the reviewed one. Nothing may be applied: a
    warning here would be this step's only fail-open check."""
    r = _run_step(
        tmp_path,
        terramate_body="echo applied ; exit 0",
        tee_body='cat > "$1"',
        tofu_body="printf 'a different plan\n'",
    )
    assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert not _ran(tmp_path, "apply-ran"), "applied a plan whose text was never reviewed"


@pytest.mark.skipif(_BASH is None, reason="bash not installed")
def test_the_render_lands_in_runner_temp_and_not_the_checkout(tmp_path):
    """The action runs in the consumer's checkout and must leave nothing at its root."""
    r = _run_step(tmp_path, terramate_body="echo applied ; exit 0", tee_body='cat > "$1"')
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert (tmp_path / "rt" / "rendered-plan.txt").is_file()
    assert sorted(p.name for p in (tmp_path / "checkout").iterdir()) == []
