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
sequence — and a three-step one whenever the change touches an action that
`.github/workflows/apply-env-level.yml` pins, since bumping that file's action
pins changes the file and in turn invalidates the pins *to* it:

1. Merge the action change (creates the **action commit**).
2. In a follow-up commit, bump the internal pins to that SHA:

   ```bash
   python dev/repin_internal.py --to <action-sha>
   ```

3. If step 2 changed `apply-env-level.yml`, repeat: the workflows pinning it are
   now stale, so bump again to step 2's commit.

   After each bump commit, run `python dev/pin_status.py HEAD` to check
   convergence at the commit you just made. `repin_internal.py` itself compares
   the working tree against the *mainline* merge-base, so it cannot see a bump
   you have only committed locally — re-running it right after committing step
   2 prints "nothing to bump" whether or not the cascade has actually
   converged, because from the mainline's perspective nothing changed either
   way. `pin_status.py HEAD` is the only one of the two that answers "is HEAD
   itself safe to pin right now."

`dev/repin_internal.py` selects what to bump using the same code the guard
asserts on, so the two cannot disagree. `--check` reports without writing;
`--all` flattens every internal pin to a single SHA instead of bumping only the
stale subset — a bigger diff, no correctness difference, and it makes "which
tree is running" one grep.

### Adding a secret to a reusable workflow is the same cascade

`apply.yml`, `apply-all.yml` and `deploy.yml` pass named secrets to the SHA-pinned
`apply-env-level.yml`. Mapping a secret the **pinned** callee does not declare is
a hard load-time error — unlike an undeclared action input, which is silently
ignored — so a new secret lands in three steps, in this order:

1. Declare it under `on.workflow_call.secrets` in the callee, and merge.
2. Bump the callers' pin to that commit (the cascade above).
3. Only then add the line to each caller's `secrets:` block.

Skipping to step 3 breaks every apply and deploy run at workflow resolution, with
no job and no log. This ordering is why these calls once used `secrets: inherit`,
which is not validated against the callee — but inherit is evaluated against the
*run*, not the file, so it delivers nothing when the run belongs to a consumer in
another organization, and suppresses what the callee's `environment:` would have
supplied. Named secrets plus this ordering is the trade that works both ways.

### Consumers must bump every engine ref in one change

A consumer's own `uses:` pins are outside the guard's reach, and two of them are
**coupled by the apply check-run name**: `actions/summary` creates the pending
check (it runs `scripts/pending-checks` out of its own pinned checkout) and
`actions/apply-cell` — pinned indirectly, inside `apply-env-level.yml` — completes
it. Both sides build the name independently, so a consumer sitting on a pin pair
that straddles a change to that grammar creates one name and looks for another:
apply-cell then fails with `no apply check named ... nothing to complete` and
every wave job dies before restoring state.

So re-pin **all** engine references in a single commit (as the sample repos do),
and never merge a Dependabot PR that bumps one engine `uses:` line in isolation.
The same applies while a cascade is in flight in this repo: an intermediate
commit of the cascade above is not a release SHA, and nothing should ever be
pinned to one.

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

One case does **not** degrade to a skip: a pin whose commit is absent in a
**non-shallow** clone. The mainline baseline resolving is not by itself proof of
full history — a depth-1 clone of `main` resolves `merge-base HEAD origin/main`
trivially at the tip while every older pinned commit object is absent — so the
guard checks `git rev-parse --is-shallow-repository` directly. Shallow: skip,
because truncated history cannot tell a genuinely gone commit from one merely
outside the fetched range. Not shallow: fail, because the commit itself is gone
(force-push, GC) and the ref cannot resolve at runtime. A skipped test satisfies
branch protection, so this distinction is what keeps a broken pin from shipping
green.

Because this workflow reports **no status on PR heads**, it must **never** be
added to this repo's required status checks — a required check that never
reports deadlocks every PR.

## Publishing the release

After the internal-pin cascade converges and `internal-pins` is green on `main`,
cut a GitHub Release. This is what makes `shipmate doctor`'s pin-freshness
staleness comparison work for consumers, and what lets a consumer's Dependabot
propose a pin bump — Dependabot resolves a SHA-pinned action through this
repository's tag namespace.

Releases on this repository are immutable, so a published tag cannot later be
re-pointed at a different commit. It is a repository setting, not a property of
the release, toggled with `PUT` / `DELETE` on that same API path rather than a
field on the repository object — confirm it is still on before cutting:

```bash
gh api repos/ship-iac/shipmate/immutable-releases   # {"enabled":true,...}
```

Write the release's `CHANGELOG.md` section first, in its own PR, and cut the tag
at **that** merge commit: the section describes what consumers get when they
re-pin, so it belongs in the tree they pin, not in a commit that arrives after
it. A commit cannot name its own SHA, so the section's SHA line is backfilled by
the first commit after the tag.

Then cut the release:

First confirm the target is safe to pin. An intermediate commit of the cascade
above carries stale internal pins; tagging one publishes a release whose tree
runs its own old code, and the constraint below about tagging the action commit
is only the most obvious case of it:

```bash
python dev/pin_status.py <release-sha>   # exit 0 == safe to pin
```

```bash
gh release create v0.2.0 --target <release-sha> --title v0.2.0 --generate-notes
```

Three constraints, each with a specific failure mode:

- **Tag the commit consumers pin** — the release SHA, meaning `main`'s tip once
  the cascade above has converged, which is also the commit the sample repos are
  re-pinned to — not the action commit that started the cascade. `doctor`
  resolves the tag with `repos/{slug}/commits/{tag}` and compares that SHA
  against each consumer pin; tagging the action commit instead reports
  correctly-pinned consumers as stale.
- **Never mark a release as prerelease.** `repos/{slug}/releases/latest` returns
  only the newest non-draft, non-prerelease release. While the repository had no
  releases at all this failed quietly; now it fails loudly in the wrong
  direction. A prerelease `v0.2.0` leaves `latest` pointing at `v0.1.0`, so every
  consumer correctly pinned to `v0.2.0`'s SHA is told its pin differs from the
  latest release and is instructed to re-pin backwards.
- **Releases are cut from `main` only.** A tag on a side branch names a commit
  that no consumer can reach by reading `main`, and one that `internal-pins`
  never ran against — the guard runs on push to `main`, so a side-branch commit
  has never had its self-pins verified.

The order matters. Cut the release first, then re-pin the sample repos to that
same SHA, annotating each pin `# vX.Y.Z`. Re-pinning first would leave the
samples on a commit with no release, which is exactly the state the probe reads
as staleness.

```bash
for d in repo-example-stacks repo-example-folders repo-example-workspaces repo-example-stacks-aws; do
  python dev/repin_consumer.py --repo "../$d" --sha <release-sha> --label vX.Y.Z
done
```

`dev/repin_consumer.py` moves **every** engine reference in one pass (see
§ Consumers must bump every engine ref in one change) and refuses a target whose
own internal pins are stale, so it cannot re-pin a sample to an intermediate
cascade commit. `--force` overrides, loudly.

The version line is `v0.x` while the action inputs, check names, and tag grammar
are still declared unstable in `README.md`. `--generate-notes` diffs against the
previous tag; the first release used hand-written notes because it had no
predecessor.

If a release is skipped, the probe is the alarm — but only once the samples move
past it: every sample plan run's annotations then warn that the pin differs from
the latest release. The state in between — engine merged, samples not yet
re-pinned, no release cut — is silent, so do not rely on the alarm to remember
this step for you.
