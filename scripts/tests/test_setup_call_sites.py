"""Every workflow call site hands `actions/setup` exactly the `with:` it should.

`actions/setup` resolves both versions from the consumer table's `tools`, else the release's
own `VERSIONS` file. A composite action only warns on an undeclared input, so a call site
passing `${{ vars.TOFU_VERSION }}` would read as an override and silently do nothing.

Each step, `uses:` aside, is asserted whole against a hand-written table: `tofu: "false"` at the
five detect jobs, which never run OpenTofu; `cache-save: "false"` at every plan.yml and unlock.yml
site, whose workflow-level `cache-mode: read` denies the save with a warning; the `id: setup` that
plan.yml's and apply.yml's detect `refusal` output reads, which an id rename would empty; and
`if: false` on the manifest-load parse probe.
"""

from _loader import WORKFLOWS, local_action, workflow_yaml

#: Both spellings of the action: every engine job reaches it through `$/`, and
#: `manifest-load.yml`'s remote `@main` probe is the one call site that cannot.
SETUP = {local_action("setup"), "ship-iac/shipmate/actions/setup"}


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


#: Each setup step but its `uses:`, per workflow file, in file order. Only the detect jobs skip
#: OpenTofu; a cell given `tofu: "false"` would fail at its first `tofu` command.
_SKIP_TOFU = {"with": {"tofu": "false"}}
_GATE_READ = {"id": "setup", **_SKIP_TOFU}
EXPECTED_STEPS = {
    "apply-env-level.yml": [{}] * 8,
    "apply.yml": [_GATE_READ],
    "deploy.yml": [_SKIP_TOFU],
    "drift.yml": [_SKIP_TOFU, {}],
    # The remote parse probe never runs a step.
    "manifest-load.yml": [{"if": False}],
    "plan.yml": [
        {"id": "setup", "with": {"tofu": "false", "cache-save": "false"}},
        {"with": {"cache-save": "false"}},
    ],
    "unlock.yml": [
        {"with": {"tofu": "false", "cache-save": "false"}},
        {"with": {"cache-save": "false"}},
    ],
}


def test_setup_call_sites_pass_only_the_tofu_and_cache_save_switches():
    """Mutations: drop `tofu` from plan.yml's detect site; add `tofu: "false"` to drift.yml's
    cell site; add `tofu-version: x` to apply.yml's detect site; delete drift.yml's cell
    site; rename plan.yml's detect site `id: setup` to `id: tools`; drop `cache-save` from
    unlock.yml's cell site; add `cache-save: "false"` to drift.yml's cell site.
    """
    got = {
        name: [{k: v for k, v in s.items() if k != "uses"} for s in steps]
        for name, steps in _setup_steps().items()
    }
    assert got == EXPECTED_STEPS
