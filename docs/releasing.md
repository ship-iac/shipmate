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

## Publishing the release

After the internal-pin cascade converges and `internal-pins` is green on `main`,
cut a GitHub Release. This is what makes `shipmate doctor`'s pin-freshness
staleness comparison work for consumers, and what lets a consumer's Dependabot
propose a pin bump — Dependabot resolves a SHA-pinned action through this
repository's tag namespace.

```bash
gh release create v0.2.0 --target <release-sha> --title v0.2.0 --generate-notes
```

Three constraints, each with a specific failure mode:

- **Tag the commit consumers pin** — the release SHA, the same commit the sample
  repos are re-pinned to — not the feature merge. `doctor` resolves the tag with
  `repos/{slug}/commits/{tag}` and compares that SHA against each consumer pin;
  tagging the feature merge instead reports correctly-pinned consumers as stale.
- **Never mark a release as prerelease.** `repos/{slug}/releases/latest` returns
  only the newest non-draft, non-prerelease release, so a "prerelease" tag leaves
  the probe reporting "could not read latest release" with no visible difference
  in its output.
- **Releases are cut from `main` only.** A tag on a side branch names a commit
  that no consumer can reach by reading `main`, and one that `internal-pins`
  never ran against — the guard runs on push to `main`, so a side-branch commit
  has never had its self-pins verified.

The version line is `v0.x` while the action inputs, check names, and tag grammar
are still declared unstable in `README.md`. `--generate-notes` diffs against the
previous tag; the first release used hand-written notes because it had no
predecessor.

If a release is skipped, the probe itself is the alarm: once the samples are
re-pinned past the latest release, every sample plan run's annotations warn that
the pin differs from the latest release.
