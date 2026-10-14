import ast

import pytest
from _loader import ENGINE

MAIN_BLOCK = "if __name__ == '__main__':\n    run_annotated(main)"


@pytest.mark.parametrize(
    "script", ["build-matrix", "apply-detect", "deploy-detect", "unlock-detect", "upsert-comment"]
)
def test_an_annotated_script_prints_its_refusal_through_run_annotated(script):
    """Each detect script's refusal, and `upsert-comment`'s failed write with gh's stderr, reaches
    the log only through `run_annotated`, which prints every line of it as one escaped workflow
    command: a bare `main()` lets the interpreter print the raw text, and the annotation shows
    only its first line.

    Mutation: revert one script's block to `main()` -- its case red (run for each of the five)."""
    tree = ast.parse((ENGINE / "scripts" / script).read_text(encoding="utf-8"))
    blocks = [
        ast.unparse(node)
        for node in tree.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"
    ]
    assert blocks == [MAIN_BLOCK]
