"""Every workflow call site hands `actions/setup` exactly the `with:` it should.

`actions/setup` resolves both versions from the consumer table's `tools`, else the release's
own `VERSIONS` file, once per run: each detect job passes `resolve: "true"` and outputs the pair
as `tools`, and each cell passes its detect job's `tools` as `versions`, so a `tools` merge
mid-run cannot give one run two versions. A composite action only warns on an undeclared input,
so a call site passing `${{ vars.TOFU_VERSION }}` would read as an override and silently do
nothing.

Each step, `uses:` aside, is asserted whole against a hand-written table: `id: setup` and
`resolve: "true"` at the five detect jobs, whose `tools` output reads that id; `versions` at every
cell, from its detect job's output (`apply-env-level.yml` from its `tools` input, which
`apply.yml` and `deploy.yml` fill from theirs) and at `deploy.yml`'s `warm` job, which installs
OpenTofu only to save its cache entry; `tofu: "false"` at the detect jobs, which never run
OpenTofu; `cache-save: "false"` at every plan.yml and unlock.yml site, whose workflow-level
`cache-mode: read` denies the save with a warning; and `if: false` on the manifest-load parse
probe.
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
_DETECT = {"id": "setup", "with": {"tofu": "false", "resolve": "true"}}
_FROM_DETECT = "${{ needs.detect.outputs.tools }}"
EXPECTED_STEPS = {
    "apply-env-level.yml": [{"with": {"versions": "${{ inputs.tools }}"}}] * 8,
    "apply-run.yml": [_DETECT],
    "deploy.yml": [_DETECT, {"with": {"versions": _FROM_DETECT}}],
    "drift.yml": [_DETECT, {"with": {"versions": _FROM_DETECT}}],
    # The remote parse probe never runs a step.
    "manifest-load.yml": [{"if": False}],
    "plan.yml": [
        {"id": "setup", "with": {"tofu": "false", "cache-save": "false", "resolve": "true"}},
        {"with": {"cache-save": "false", "versions": _FROM_DETECT}},
    ],
    "unlock.yml": [
        {"id": "setup", "with": {"tofu": "false", "cache-save": "false", "resolve": "true"}},
        {"with": {"cache-save": "false", "versions": _FROM_DETECT}},
    ],
}


def test_setup_call_sites_resolve_once_and_pass_only_the_switches():
    """Mutations: drop `tofu` from plan.yml's detect site; add `tofu: "false"` to drift.yml's
    cell site; add `tofu-version: x` to apply-run.yml's detect site; delete drift.yml's cell
    site; rename plan.yml's detect site `id: setup` to `id: tools`; drop `cache-save` from
    unlock.yml's cell site; add `cache-save: "false"` to drift.yml's cell site; add
    `resolve: "true"` to plan.yml's cell site; set the wave cells' `versions` to `""`; drop
    `resolve` from deploy.yml's detect site; add `tofu: "false"` to deploy.yml's warm site.
    """
    got = {
        name: [{k: v for k, v in s.items() if k != "uses"} for s in steps]
        for name, steps in _setup_steps().items()
    }
    assert got == EXPECTED_STEPS
