"""No workflow call site hands `actions/setup` a tool version.

`actions/setup` resolves both versions from the release's own `VERSIONS` file. A composite
action only warns on an undeclared input, so a call site passing `${{ vars.TOFU_VERSION }}` would
read as an override and silently do nothing.

Each step's whole `with:` block is asserted against a hand-written table: `tofu: "false"` at the
five detect jobs, which never run OpenTofu, and an empty block everywhere else.
"""

from _loader import WORKFLOWS, local_action, workflow_yaml

#: Both spellings of the action: every engine job reaches it through `$/`, and
#: `manifest-load.yml`'s remote `@main` probe is the one call site that cannot.
SETUP = {local_action("setup"), "ship-iac/shipmate/actions/setup"}

#: Setup call sites per workflow file. Hand-written: a count read back from the tree agrees
#: with whatever the tree says. This reds when a call site appears or disappears, which is
#: when the `with:` question has to be answered again.
EXPECTED_CALL_SITES = {
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

    Matched on the path part alone, so the manifest-load probe's `@main` cannot slip past.
    """
    found = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = workflow_yaml(path)
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).split("@")[0] in SETUP:
                    found.setdefault(path.name, []).append(step)
    return found


def test_every_setup_call_site_is_accounted_for():
    """Mutation: delete one `- uses: .../actions/setup@...` line."""
    assert {name: len(steps) for name, steps in _setup_steps().items()} == EXPECTED_CALL_SITES


#: Each setup step's `with:` per workflow file, in file order. Only the detect jobs skip OpenTofu;
#: a cell given `tofu: "false"` would fail at its first `tofu` command.
_SKIP_TOFU = {"tofu": "false"}
EXPECTED_WITH = {
    "apply-env-level.yml": [{}] * 8,
    "apply.yml": [_SKIP_TOFU],
    "deploy.yml": [_SKIP_TOFU],
    "drift.yml": [_SKIP_TOFU, {}],
    "manifest-load.yml": [{}],
    "plan.yml": [_SKIP_TOFU, {}],
    "unlock.yml": [_SKIP_TOFU, {}],
}


def test_setup_call_sites_pass_only_the_detect_tofu_switch():
    """Mutations: drop `tofu` from plan.yml's detect site; add `tofu: "false"` to drift.yml's
    cell site; add `tofu-version: x` to apply.yml's detect site.
    """
    got = {name: [s.get("with") or {} for s in steps] for name, steps in _setup_steps().items()}
    assert got == EXPECTED_WITH
