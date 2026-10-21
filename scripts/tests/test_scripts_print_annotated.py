import ast

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
    "apply-snapshot",
    "authorize",
    "build-matrix",
    "comment-parse",
    "deploy-detect",
    "dispatch-body",
    "doctor",
    "drift-cell-summary",
    "drift-issues",
    "env-config",
    "env-inject",
    "gate-config",
    "gate-state",
    "gate-status",
    "hcl-check",
    "lock-info",
    "mirror-checks",
    "onboard",
    "pending-checks",
    "plan-cell-summary",
    "plan-classify",
    "plan-crypt",
    "pr-facts",
    "provider-cache-key",
    "register-app",
    "repin",
    "reply-comment",
    "state-path",
    "summary-comment",
    "tofu-error",
    "unlock-detect",
    "upsert-comment",
    "verify-environments",
    "waves",
]

MAIN_BLOCK = "if __name__ == '__main__':\n    run_annotated(main)"
#: Hand-written: each block that is not `MAIN_BLOCK`.
BLOCKS = {
    "apply-detect": "if __name__ == '__main__':\n    run_annotated(main, refusal_output=True)",
    "build-matrix": "if __name__ == '__main__':\n    run_annotated(main, refusal_output=True)",
    "gate-status": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "hcl-check": (
        "if __name__ == '__main__':\n"
        "    utf8_output()\n"
        "    run_annotated(main, refusal_output=True)"
    ),
    "plan-classify": "if __name__ == '__main__':\n    run_annotated(main, sys.argv)",
    "plan-crypt": "if __name__ == '__main__':\n    run_annotated(main, sys.argv)",
    "plan-cell-summary": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
    "tofu-error": "if __name__ == '__main__':\n    run_annotated(main, sys.argv[1:])",
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
    again; delete `waves` from `LIBRARIES`; add an extensionless script with no
    block -- each red."""
    if script in LIBRARIES:
        expected = []
    elif script in TERMINAL:
        expected = [TERMINAL_BLOCK]
    else:
        expected = [BLOCKS.get(script, MAIN_BLOCK)]
    assert _blocks(script) == expected
