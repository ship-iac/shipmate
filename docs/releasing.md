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
   grep -rlE 'ship-iac/shipmate/actions/(apply-cell|setup|state)@[0-9a-f]{40}' \
     .github/workflows actions \
     | xargs sed -i 's/@<old-sha>/@abc1234.../g'
   ```

## The guard

`scripts/tests/test_internal_pins.py` fails if any internal
`ship-iac/shipmate/<path>@<sha>` reference pins a commit whose `<path>` no longer
matches the mainline tree. A red run after an action change means step 2 above is
still pending.

It runs in its own workflow (`.github/workflows/internal-pins.yml`) on **push to
main only — never on pull_request**. The guard reads the pins from the working
tree and diffs each pinned SHA's `<path>` content against the merge-base with
`main`. On a branch this means:

- A PR that edits a *pinned action's code* is **not** flagged for its own
  not-yet-merged change — the comparison is against the fork point, and step 1's
  commit cannot pin its own unborn SHA. This is the false positive the mainline
  baseline exists to suppress.
- A PR that edits a *pin reference itself* to a SHA whose content is already
  stale (a fat-fingered step-2 bump) **is** something the guard could catch
  pre-merge — and the PR trigger did catch it.

Not running on PRs is a deliberate tradeoff: it trades that pre-merge catch of a
PR-introduced bad pin for silence during the step-1→step-2 window, when a stale
pin genuinely sits on `main` and thus on every branch's fork point — actionable
only by the release owner, not by unrelated PR authors (dependabot included).
The push-to-main run still catches a bad pin, one step later, exactly where and
when the bump is done. (The workflow checks out with `fetch-depth: 0` so the test
can read the pinned commit objects.)

Because this workflow reports **no status on PR heads**, it must **never** be
added to this repo's required status checks — a required check that never
reports deadlocks every PR.
