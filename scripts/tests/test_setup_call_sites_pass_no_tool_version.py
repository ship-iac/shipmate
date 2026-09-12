"""No workflow call site hands `actions/setup` a tool version.

`actions/setup` resolves both versions from the release's own `VERSIONS` file when its
inputs are empty, so a call site passing `${{ vars.TOFU_VERSION }}` would *work* -- it
would silently restore the repository-variable round trip the engine stopped using, one
call site at a time, and nothing else in the suite reads these steps.

Each step's whole `with:` block is asserted, not the two input names: those two were the
only inputs any call site ever passed, so an empty block is the whole known value. A call
site that genuinely needs an input changes the constant below deliberately.
"""

import yaml
from _loader import WORKFLOWS

SETUP = "ship-iac/shipmate/actions/setup"

#: Setup call sites per workflow file. Hand-written: a count read back from the tree agrees
#: with whatever the tree says. This reds when a call site appears or disappears, which is
#: when the `with:` question has to be answered again.
EXPECTED_CALL_SITES = {
    "apply-all.yml": 1,
    "apply-env-level.yml": 8,
    "apply.yml": 1,
    "deploy.yml": 1,
    "drift.yml": 2,
    "manifest-load.yml": 1,
    "plan.yml": 2,
    "unlock.yml": 2,
}


def _setup_steps():
    """Every `actions/setup` step in the engine's workflows, keyed by file name.

    Matched on the repo part alone, so a re-pin or the manifest-load probe's `@main` cannot
    slip past.
    """
    found = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict), f"{path} did not parse to a mapping ({doc!r})"
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).split("@")[0] == SETUP:
                    found.setdefault(path.name, []).append(step)
    return found


def test_every_setup_call_site_is_accounted_for():
    """Mutation: delete one `- uses: .../actions/setup@...` line."""
    assert {name: len(steps) for name, steps in _setup_steps().items()} == EXPECTED_CALL_SITES


def test_no_setup_call_site_passes_a_tool_version():
    """Mutation: add `with: {tofu-version: ...}` back to any call site."""
    for name, steps in _setup_steps().items():
        for step in steps:
            assert (step.get("with") or {}) == {}, f"{name} passes {step.get('with')!r} to setup"
