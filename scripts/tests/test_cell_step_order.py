"""Each cell restores state after `tofu init` and before its plan or apply.

Restored workspace state with no `.terraform` record makes `init` ask about state migration, and
`-input=false` turns the question into a hard error, so the restore must follow init. It must
also precede the plan or apply: state restored after them is state they never read, a silent
wrong plan rather than an error. `Locate state` sits between the two because it reads the record
init writes. `actions/cell-init` holds the init, behind the provider cache restore that must
precede it, and the state restore after it; each cell calls it as one step. In drift-cell the
provider cache check and save follow the `cell-init` call, so a failed state locate or restore
skips that save; accepted, because the cell has already failed and the cost is one re-download
on the next sweep.

The whole ordered list of step names per cell, hand-written, so a moved, dropped or added step
reds here.
"""

import pytest
from _loader import action_steps

_EXPECTED = {
    "cell-init": [
        "Provider cache key",
        "Restore provider cache",
        "Initialize the stack",
        "Name the restored provider cache entry",
        "Locate state",
        "Restore state",
    ],
    "plan-cell": [
        "Record the planned commit",
        "Inject identity variables",
        "Stack slug",
        "Initialize the stack",
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
        "Check the provider cache",
        "Save provider cache",
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
        "Verify the stored plan renders as the reviewed plan text",
        "Apply the stored plan (exact-plan; stale -> fail-safe)",
        "Save state",
        "Check the provider cache",
        "Save provider cache",
        "Compose cell summary",
        "Upload apply summary",
    ],
}


@pytest.mark.parametrize("cell", sorted(_EXPECTED))
def test_each_cell_runs_its_steps_in_the_expected_order(cell):
    """Mutations: swap `Initialize the stack` and `Restore provider cache` in `cell-init`; swap
    `Locate state` and `Restore state` in `cell-init`."""
    assert [s.get("name") for s in action_steps(cell)] == _EXPECTED[cell]
