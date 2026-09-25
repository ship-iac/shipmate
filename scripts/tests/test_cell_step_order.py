"""Each cell restores state after `tofu init` and before its plan or apply.

Restored workspace state with no `.terraform` record makes `init` ask about state migration, and
`-input=false` turns the question into a hard error, so the restore must follow init. It must
also precede the plan or apply: state restored after them is state they never read, a silent
wrong plan rather than an error. `Locate state` sits between the two because it reads the record
init writes.

The whole ordered list of step names per cell, hand-written, so a moved, dropped or added step
reds here.
"""

import pytest
from _loader import action_steps

_EXPECTED = {
    "plan-cell": [
        "Record the planned commit",
        "Inject identity variables",
        "Stack slug",
        "Initialize the stack",
        "Locate state",
        "Restore state",
        "Plan",
        "Render + classify plan",
        "Encrypt plan artifact at rest (no-op without a passphrase)",
        "Step summary (plan text, 64KiB cap)",
        "Record the planned commit in the artifact",
        "Upload plan artifact",
        "Write cell summary",
        "Upload cell summary",
    ],
    "drift-cell": [
        "Inject identity variables",
        "Stack slug",
        "Initialize the stack",
        "Locate state",
        "Restore state",
        "Plan + classify drift",
        "Compose cell summary",
        "Upload drift summary",
    ],
    "apply-cell": [
        "Inject identity variables",
        "Stack slug",
        "Download reviewed plan artifact (fail-safe if missing)",
        "Verify the plan was produced from this commit",
        "Decrypt reviewed plan artifact (fail-safe on config/plaintext mismatch)",
        "Verify fingerprint matches the reviewed plan",
        "Check the reviewed plan text's digest reached this action",
        "Initialize the stack",
        "Locate state",
        "Restore state",
        "Verify the stored plan renders as the reviewed plan text",
        "Apply the stored plan (exact-plan; stale -> fail-safe)",
        "Save state",
        "Compose cell summary",
        "Upload apply summary",
    ],
}


@pytest.mark.parametrize("cell", sorted(_EXPECTED))
def test_each_cell_runs_its_steps_in_the_expected_order(cell):
    assert [s.get("name") for s in action_steps(cell)] == _EXPECTED[cell]
