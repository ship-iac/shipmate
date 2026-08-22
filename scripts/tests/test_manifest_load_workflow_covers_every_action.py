"""`manifest-load.yml` must name every action, in the one shape that parses it.

That workflow is the only guard on "GitHub can load this manifest", and it is
silent about what it does not list: add an action and forget a step and the new
manifest simply has no coverage. Its discriminating power also rests on two
details a reader is likely to tidy away --

  * the ref must be **remote** (`ship-iac/shipmate/actions/x@main`). A local
    `./actions/x` manifest is only read when the step executes, so under
    `if: false` it is never parsed at all: measured 2026-08-22, a comma-split
    local manifest under `if: false` passes.
  * `if: false` must stay. Without it every action actually runs, which is what
    made the smoke run look expensive: 19 actions, 83 required inputs between
    them, App tokens, a live PR, terramate/tofu.

So compare the whole step list to one built here, rather than checking a part.
"""

import glob
import os

import yaml

WORKFLOW = ".github/workflows/manifest-load.yml"


def test_manifest_load_workflow_lists_every_action_as_a_skipped_remote_step():
    actions = sorted(
        os.path.basename(os.path.dirname(p)) for p in glob.glob("actions/*/action.yml")
    )
    assert len(actions) > 15, f"expected the full action set, found {actions}"

    doc = yaml.safe_load(open(WORKFLOW, encoding="utf-8"))
    assert list(doc["jobs"]) == ["load"], "one job; the expectation below is that job's steps"

    expected = [{"if": False, "uses": f"ship-iac/shipmate/actions/{name}@main"} for name in actions]
    assert doc["jobs"]["load"]["steps"] == expected
