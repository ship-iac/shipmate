"""`manifest-load.yml` must name every action, in the one shape that parses it.

That workflow is the only guard on "GitHub can load this manifest", and it is silent about what
it does not list: add an action, forget a step, and the new manifest has no coverage. Its
discriminating power also rests on two details a reader is likely to tidy away:

  * the ref must be remote (`ship-iac/shipmate/actions/x@main`). A local `./actions/x` manifest is
    only read when the step executes, so under `if: false` it is never parsed at all -- measured
    2026-08-22, a comma-split local manifest under `if: false` passes.
  * `if: false` must stay. Without it every action actually runs, which is what made the smoke run
    look expensive: every action's required inputs, App tokens, a live pull request,
    terramate/tofu.

So compare the whole step list to one built here, rather than checking a part. The trigger is
compared the same way: `@main` is only the right tree on a push to `main`, so a workflow
retriggered anywhere else -- a pull request, where the ref is stale -- or nowhere at all would
leave correct steps that never run.
"""

from _loader import ACTIONS, workflow_yaml


def test_manifest_load_workflow_lists_every_action_as_a_skipped_remote_step():
    actions = sorted(p.parent.name for p in ACTIONS.glob("*/action.yml"))
    assert len(actions) > 15, f"expected the full action set, found {actions}"

    doc = workflow_yaml("manifest-load.yml")
    assert doc["on"] == {
        "push": {"branches": ["main"]},
        "workflow_dispatch": None,
    }
    assert list(doc["jobs"]) == ["load"], "one job; the expectation below is that job's steps"

    expected = [{"if": False, "uses": f"ship-iac/shipmate/actions/{name}@main"} for name in actions]
    assert doc["jobs"]["load"]["steps"] == expected
