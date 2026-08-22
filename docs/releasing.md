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

## Manifest load

`.github/workflows/manifest-load.yml` lets GitHub parse every action manifest,
because GitHub is what parses them in production and `yaml.safe_load` is more
permissive. An unquoted description containing a comma inside a `{ }` flow
mapping splits — in flow context a comma is a separator — so PyYAML yields an
extra key named for the tail of the sentence and accepts the file, while
`GitHub.DistributedTask.ObjectTemplating` refuses it outright. `v0.16.0` shipped
that and every apply and deploy job died in `Set up job`, before its first step.

The workflow is one job of 19 steps, each `if: false` and each `uses:` one action
at the **remote** ref `ship-iac/shipmate/actions/<name>@main`. Both halves are
load-bearing, measured 2026-08-22:

| step form | `if: false` | comma-split manifest |
| --- | --- | --- |
| `ship-iac/shipmate/actions/x@<ref>` | yes | **fails in `Set up job`** |
| `./actions/x` | yes | passes — never parsed |
| either form | no | fails, and the action runs |

The runner downloads and parses *remote* action manifests while setting the job
up, before any step's `if:` is evaluated; a *local* `./actions/x` manifest is only
read when its step executes. So the remote ref plus `if: false` buys the
production parse without running anything — no action needs inputs, an App token,
a live PR or `terramate`/`tofu`. The whole job takes about six seconds.
`continue-on-error` is not an alternative: it masks precisely the manifest-load
failure being hunted.

Two limits, both deliberate:

- **Merge-time, not PR-time.** `uses:` takes no expressions, so the ref cannot
  follow a PR head, and `@main` is the only ref that stays correct. Like `## The
  guard` above this runs on **push to main**, so it must never be a required
  status check. It still runs before any tag is cut, which is where `v0.16.0`
  escaped. Use `workflow_dispatch` to re-check `main` by hand immediately before
  tagging.
- **Coverage is asserted locally.** The workflow is silent about actions it does
  not list, so `scripts/tests/test_manifest_load_workflow_covers_every_action.py`
  compares its whole step list against the `actions/*/` tree — a new action with
  no step, a step that lost `if: false`, and a step rewritten to a local ref each
  fail there.

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

### Smoke the live path before the tag

The runbook's ordering is right — release first, samples after — but it also
means **the first time anything runs the new engine code for real is after the
tag exists.** For a feature whose whole surface is a live Actions path, the
release is therefore always cut on unexercised code. `v0.16.0` was tagged with an
action manifest GitHub could not parse (apply and deploy dead), a dispatch that
could not reach the engine at all (empty `required: true` input, HTTP 422), and a
parser blind to the ANSI colour OpenTofu emits on a runner. All three are
boundary behaviours no unit test reaches, and each surfaced in the first minutes
of live use — three patch releases, each with its own pin cascade.

So before cutting the tag, run the **thinnest live exercise** of the new path, on
the release commit, from one sample:

1. Re-pin **one** sample to the release commit on a **scratch branch**, never its
   default branch — a re-pin on `main` with no release cut yet is exactly the
   backwards staleness the section above warns about.

   ```bash
   git -C ../repo-example-stacks-aws checkout -b smoke/vX.Y.Z
   python dev/repin_consumer.py --repo ../repo-example-stacks-aws --sha <release-sha> --label vX.Y.Z
   ```

2. Drive the wrapper **directly at that ref**, with the body `actions/dispatch`
   would build — including the values it deliberately sends empty:

   ```bash
   gh workflow run apply.yml --repo ship-iac/repo-example-stacks-aws --ref smoke/vX.Y.Z \
     -f mode=unlock -f environment=sbx -f ref=<40-char-sha> -f pr_number=<n> -f plan_run_id=
   ```

   **Not by commenting the verb.** An `issue_comment` workflow always runs from
   the repository's default branch, and the documented `comment-ops.yml` passes
   `dispatch-ref: ${{ github.event.repository.default_branch }}` — so a comment
   drives the default branch's `comment-ops.yml` and dispatches the default
   branch's `apply.yml`, still on the *old* pin. The scratch branch is never
   read, and the smoke goes green without touching the new code.
3. Throw the branch away and cut the release as below.

**What this catches, and what it cannot.** It catches the class that genuinely
needs a consumer: the wrapper's `workflow_dispatch` input declarations meeting
the body the engine sends. An empty `-f plan_run_id=` against a `required: true`
input is rejected as "not provided" right here, with no job started, and nothing
in this repository can see that pair. It also resolves and parses the engine
reusable workflow at the new SHA, because that happens when the run graph is
built.

It cannot reach a composite action's manifest — not because those refs are
local (the engine's reusable workflows reach every action at a **remote** SHA
pin, which is exactly why `v0.16.0` died in `Set up job`), but because the job
holding them never starts: `detect` runs only behind `guard`, and `guard`
rejects any actor not ending in `[bot]` — correctly, since a direct human
dispatch is what it exists to refuse. A skipped job sets nothing up, so nothing
fetches or parses its actions. `v0.16.0`'s unparseable `apply-detect/action.yml`
survives this exercise. Catching that class is engine CI's job, not a sample's:
`## Manifest load` above does it on every push to `main`, one commit before a
tag. Nor does it cover the comment leg — parse, authorize, route — which by
construction runs the sample's default-branch workflows and so is only
exercised after the re-pin.

Smoke proves the dispatch wiring resolves; acceptance proves the behaviour is
right.

Then cut the release:

First confirm the target is safe to pin. An intermediate commit of the cascade
above carries stale internal pins; tagging one publishes a release whose tree
runs its own old code, and the constraint below about tagging the action commit
is only the most obvious case of it:

```bash
python dev/pin_status.py <release-sha>   # exit 0 == safe to pin
```

```bash
git tag -a v0.2.0 -m v0.2.0 <release-sha> && git push origin v0.2.0
gh release create v0.2.0 --title v0.2.0 --generate-notes --verify-tag
```

**Push the tag first; `--target` does not work on this repository.**
`gh release create v0.2.0 --target <sha>` was the documented form and it is
rejected — `tag_name is not a valid tag` / `Release.target_commitish is invalid`,
with the release SHA verified as `main`'s tip through the API in the same breath.
Observed on `v0.14.2`; the cause was not diagnosed, so treat only the two-step
form above as known-good. `--verify-tag` is what keeps the second command from
inventing a tag when the push did not land.

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
