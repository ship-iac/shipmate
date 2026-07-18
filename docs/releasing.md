# Releasing (engine-internal action pins)

Consumers pin shipmate's actions and reusable workflows by commit SHA. The engine
**also** references its own actions internally by SHA — most notably the reusable
`.github/workflows/apply-env-level.yml`, which pins `actions/setup` and
`actions/apply-cell`, and the composite actions, which pin `actions/state`.

GitHub does not allow a local `./actions/...` reference across the
reusable-workflow boundary (inside a reusable workflow, `./` resolves against the
*consumer* repo, which has no `actions/` directory), so these SHA pins are the
only mechanism.

## The rule

**When you change an action that another engine file pins, bump that pin.**

If you change `actions/apply-cell`, `actions/setup`, or `actions/state`, the files
that reference them by SHA must be updated to a commit that contains your change —
otherwise the deploy/apply path silently keeps running the old action.

Because a commit cannot pin its own not-yet-existing SHA, this is a two-step
sequence:

1. Merge the action change (creates the release SHA, e.g. `abc1234`).
2. In a follow-up commit, bump the internal pins to that SHA:

   ```bash
   grep -rlE 'shipmate-iac/shipmate/actions/(apply-cell|setup|state)@[0-9a-f]{40}' \
     .github/workflows actions \
     | xargs sed -i 's/@<old-sha>/@abc1234.../g'
   ```

## The guard

`scripts/tests/test_internal_pins.py` fails CI if any internal
`shipmate-iac/shipmate/<path>@<sha>` reference pins a commit whose `<path>` no
longer matches the current tree. A red run here after an action change means step
2 above is still pending. (CI checks out with `fetch-depth: 0` so the test can
read the pinned commit objects.)
