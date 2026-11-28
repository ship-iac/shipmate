import ast
import os
import subprocess
import sys

import pytest
from _loader import ENGINE

SCRIPTS = sorted(
    p.name
    for p in (ENGINE / "scripts").iterdir()
    if p.is_file() and not p.suffix and not p.name.startswith(".") and not p.name.endswith("~")
)

#: Hand-written: every engine script, so an added, renamed or deleted one is red.
SCRIPTS_EXPECTED = [
    "apply-cell-summary",
    "apply-comment",
    "apply-complete",
    "apply-detect",
    "apply-gate",
    "apply-queue",
    "apply-release",
    "apply-run-check",
    "apply-snapshot",
    "authorize",
    "auto-dispatch",
    "base-push-plan",
    "build-matrix",
    "cell-lock",
    "claim",
    "comment-parse",
    "create-checks",
    "deploy-detect",
    "dispatch-body",
    "doctor",
    "drift-cell-summary",
    "drift-issues",
    "env-config",
    "env-inject",
    "evict-reply",
    "gate-config",
    "gate-state",
    "gate-status",
    "hcl-check",
    "lock-info",
    "mirror-checks",
    "onboard",
    "patch-checks",
    "pending-checks",
    "plan-cell-summary",
    "plan-classify",
    "plan-crypt",
    "pr-facts",
    "provider-cache-key",
    "register-app",
    "repin",
    "reply-comment",
    "rerun-autoplan",
    "state-handoff",
    "state-path",
    "state-save",
    "summary-comment",
    "tofu-error",
    "tool-versions",
    "unlock-detect",
    "unlock-result",
    "upsert-comment",
    "verify-environments",
    "waves",
]

MAIN_BLOCK = "if __name__ == '__main__':\n    run_annotated(main)"
#: Hand-written: each block that is not `MAIN_BLOCK`.
BLOCKS = {
    "apply-detect": "if __name__ == '__main__':\n    run_annotated(main, refusal_output=True)",
    "apply-complete": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "apply-release": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "apply-run-check": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "apply-snapshot": (
        "if __name__ == '__main__':\n"
        "    run_annotated(main, refusal_output=True, usage='apply-snapshot')"
    ),
    "build-matrix": "if __name__ == '__main__':\n    run_annotated(main, refusal_output=True)",
    "claim": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "cell-lock": "if __name__ == '__main__':\n    run_annotated(main, refusal_output=True)",
    "create-checks": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "dispatch-body": "if __name__ == '__main__':\n    run_annotated(main, usage='dispatch-body')",
    "gate-status": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "hcl-check": (
        "if __name__ == '__main__':\n"
        "    utf8_output()\n"
        "    run_annotated(main, refusal_output=True)"
    ),
    "patch-checks": (
        "if __name__ == '__main__':\n"
        "    run_annotated(main, usage='patch-checks (write plan on stdin, no arguments)')"
    ),
    "plan-classify": "if __name__ == '__main__':\n    run_annotated(main, sys.argv)",
    "plan-crypt": "if __name__ == '__main__':\n    run_annotated(main, sys.argv)",
    "plan-cell-summary": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "reply-comment": "if __name__ == '__main__':\n    run_annotated(main, usage='reply-comment')",
    "rerun-autoplan": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "tofu-error": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "tool-versions": "if __name__ == '__main__':\n    run_annotated(main, refusal_output=True)",
    "verify-environments": (
        "if __name__ == '__main__':\n    run_annotated(main, refusal_output=True)"
    ),
}
#: Run by an operator in a terminal, never by a workflow: `onboard` strips `::error::` for human
#: output itself, and neither `register-app` nor `repin` raises one.
TERMINAL = {"onboard", "register-app", "repin"}
TERMINAL_BLOCK = "if __name__ == '__main__':\n    utf8_output()\n    main()"
#: Imported by other scripts, never run.
LIBRARIES = {"env-config", "waves"}


def _blocks(script):
    tree = ast.parse((ENGINE / "scripts" / script).read_text(encoding="utf-8"))
    return [
        ast.unparse(node)
        for node in tree.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"
    ]


def test_the_enumeration_finds_every_engine_script_and_the_registries_name_only_scripts():
    """A parametrization from the directory cannot see a script it fails to find, and a registry
    key naming no script exempts nothing. Mutations: add a scratch script; delete a name from
    `SCRIPTS_EXPECTED`; rename a `BLOCKS` key -- each red."""
    assert SCRIPTS == SCRIPTS_EXPECTED
    assert set(BLOCKS) | TERMINAL | LIBRARIES <= set(SCRIPTS)


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_script_prints_its_refusal_through_run_annotated(script):
    """A script's `SystemExit` refusal reaches the log only through `run_annotated`, which prints
    every line of it as one escaped workflow command: a bare `main()` lets the interpreter print
    the raw text, and the annotation shows only its first line. A new script with no block is
    red.

    Mutations: revert `apply-gate`'s block to `main()`; revert `gate-status`'s to
    `main(sys.argv[1:])`; raise `plan-cell-summary`'s argument refusal in its `__main__` block
    again; drop `usage=` from `reply-comment`'s block; delete `waves` from `LIBRARIES`; add an
    extensionless script with no block -- each red."""
    if script in LIBRARIES:
        expected = []
    elif script in TERMINAL:
        expected = [TERMINAL_BLOCK]
    else:
        expected = [BLOCKS.get(script, MAIN_BLOCK)]
    assert _blocks(script) == expected


@pytest.mark.parametrize(
    ("script", "argument", "stdin", "message"),
    [
        (
            "apply-snapshot",
            "by-wave",
            '{"name": "apply / dev / dns", "id": 1}\n',
            "::error::usage: apply-snapshot",
        ),
        ("apply-release", "x", "", "::error::usage: apply-release guard|release"),
        ("dispatch-body", "--help", "", "::error::usage: dispatch-body"),
        (
            "patch-checks",
            "completed",
            '{"id": 1, "body": {"status": "completed", "conclusion": "success"}}\n',
            "::error::usage: patch-checks (write plan on stdin, no arguments)",
        ),
        ("reply-comment", "--post", "", "::error::usage: reply-comment"),
    ],
)
def test_a_command_line_argument_refuses_before_main(tmp_path, script, argument, stdin, message):
    """The env carries no `PATH`, `GH_TOKEN` or `GITHUB_REPOSITORY`, so a script that reads its
    environment, stdin or the API before refusing dies on a missing variable with another stderr,
    and none can reach a live repository. It carries `GITHUB_OUTPUT`, which a runner always sets,
    for a script that writes its refusal as an output. Mutations: drop `usage=` from
    `dispatch-body`'s `__main__` -- `main` runs and fails on its environment; move the check
    after `main(*args)` in `run_annotated` -- every `main` runs first."""
    env = {k: os.environ[k] for k in ("SYSTEMROOT",) if k in os.environ}
    env["GITHUB_OUTPUT"] = str(tmp_path / "github_output")
    proc = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / script), argument],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (1, "", message + "\n")
