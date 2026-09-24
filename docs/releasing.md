# Releasing

Consumers pin shipmate's reusable workflows by commit SHA. The engine itself has
no pins: every engine step calls its action as `$/actions/<name>`, which GitHub
resolves in this repository at the commit of the reusable workflow the consumer
pinned, and the three callers of `apply-env-level.yml` reach it as
`./.github/workflows/apply-env-level.yml`. One commit, one tree.
`scripts/tests/test_engine_self_reference.py` refuses a
`ship-iac/shipmate/<path>@<sha>` reference in any workflow or action manifest.

## Consumers move every engine ref in one change

The seven reusable workflows share inputs and secrets across a release, so a consumer re-pins
all seven `uses:` lines in one commit (`dev/repin_consumer.py` does exactly that) and never
merges a Dependabot pull request that bumps one line alone.

## Manifest load

`.github/workflows/manifest-load.yml` lets GitHub parse every action manifest,
because GitHub is what parses them in production and `yaml.safe_load` is more
permissive. An unquoted description containing a comma inside a `{ }` flow
mapping splits — in flow context a comma is a separator — so PyYAML yields an
extra key named for the tail of the sentence and accepts the file, while
`GitHub.DistributedTask.ObjectTemplating` refuses it outright. `v0.16.0` shipped
that and every apply and deploy job died in `Set up job`, before its first step.

The workflow is one job of 20 steps, each `if: false` and each `uses:` one action
at the remote ref `ship-iac/shipmate/actions/<name>@main`. Both halves are
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

Three limits, all deliberate:

- **Merge-time, not PR-time.** `uses:` takes no expressions, so the ref cannot
  follow a PR head, and `@main` is the only ref that stays correct. This runs on
  push to `main`, so it must never be a required status check. It still runs
  before any tag is cut, which is where `v0.16.0` escaped — but its push run
  covers whichever commit was the tip then, not necessarily the release SHA, so
  `## Publishing the release` below checks that commit and dispatches the
  workflow when nothing covers it.
- **Coverage is asserted locally.** The workflow is silent about actions it does
  not list, so `scripts/tests/test_manifest_load_workflow_covers_every_action.py`
  compares its whole step list against the `actions/*/` tree — a new action with
  no step, a step that lost `if: false`, and a step rewritten to a local ref each
  fail there.
- **The engine repository cannot enable SHA-pinning enforcement.** Measured
  2026-08-30: with "require actions to be pinned to a full-length commit SHA"
  on, this job fails in `Set up job` with `The action
  ship-iac/shipmate/actions/apply-all-detect@main is not allowed in
  ship-iac/shipmate because all actions must be pinned to a full-length commit
  SHA`. GitHub documents exemptions for `./path` actions and for reusable
  workflows referenced by tag; neither covers this run, whose error names the
  same repository as owner and as consumer — so a self-referencing
  `owner/repo/path@ref` is not exempt. And `uses:` takes no expressions, so the
  ref cannot be a SHA that follows `main`. For this workflow
  in this repository no form keeps both the check and the setting — short of
  moving `manifest-load.yml` to a repository that does not enable it, at the
  cost of a second repository; consumer repositories enable it
  (`docs/hardening.md` row 20) and this one does not.

## Publishing the release

After `manifest-load` is green on `main`,
cut a GitHub Release. This is what makes `shipmate doctor`'s pin-freshness
staleness comparison work for consumers, and what lets a consumer's Dependabot
propose a pin bump — Dependabot resolves a SHA-pinned action through this
repository's tag namespace.

Only `repo-example-stacks-aws` is re-pinned at release. The other three samples
reference the engine at `@main` and exercise it on their own triggers (pull
requests, merges, the nightly drift run); they carry no pin to move and no
release-freshness alarm.

Releases on this repository are immutable, so a published tag cannot later be
re-pointed at a different commit. It is a repository setting, not a property of
the release, toggled with `PUT` / `DELETE` on that same API path rather than a
field on the repository object — confirm it is still on before cutting:

```bash
gh api repos/ship-iac/shipmate/immutable-releases   # {"enabled":true,...}
```

Write the release's `CHANGELOG.md` section first, in its own PR, and cut the tag
at that merge commit: the section describes what consumers get when they
re-pin, so it belongs in the tree they pin, not in a commit that arrives after
it. A commit cannot name its own SHA, so the section's SHA line is backfilled by
the first commit after the tag.

If the tree carries an `Unreleased` heading — `## [Unreleased]` in
`CHANGELOG.md` — rename it to the version in that same PR and
`grep -rn "Unreleased" CHANGELOG.md docs/` for the cross-references that name the
section, or the release ships pointing at a heading that no longer exists.

### Smoke the live path before the tag

The runbook's ordering is right — release first, samples after — but it also
means the first time anything runs the new engine code for real is after the
tag exists. For a feature whose whole surface is a live Actions path, the
release is therefore always cut on unexercised code. `v0.16.0` was tagged with an
action manifest GitHub could not parse (apply and deploy dead), a dispatch that
could not reach the engine at all (empty `required: true` input, HTTP 422), and a
parser blind to the ANSI colour OpenTofu emits on a runner. All three are
boundary behaviours no unit test reaches, and each surfaced in the first minutes
of live use — three patch releases.

So before cutting the tag, run the thinnest live exercise of the new path, on
the release commit, from `repo-example-stacks-aws`:

1. Re-pin `repo-example-stacks-aws` to the release commit on a scratch branch,
   never its default branch — a re-pin on `main` with no release cut yet is
   exactly the backwards staleness that makes the pin probe report
   correctly-pinned consumers as stale and tell them to re-pin backwards.

   ```bash
   git -C ../repo-example-stacks-aws checkout -b smoke/vX.Y.Z
   python dev/repin_consumer.py --repo ../repo-example-stacks-aws --sha <release-sha> --label vX.Y.Z
   ```

   **`repin_consumer.py` rewrites pins and nothing else.** When a release
   changes the consumer file's declared input contract, make those body edits on
   the scratch branch too — a new pin under an old body is the load-time
   rejection described below, not a smoke result. `docs/upgrading.md`'s section
   for the release names those edits; for this release it is replacing the six
   files with the single `.github/workflows/shipmate.yml` that
   `docs/getting-started.md` publishes.

   The same gap has a second form the tool cannot reach at all: a consumer's
   allowed-actions list is a repository setting, not a file. Under
   `docs/hardening.md` row 12 a consumer restricts `allowed_actions` to a named
   pattern list, and those patterns end `@*` — so a version bump is absorbed,
   but a third-party action this release *adds or renames* is not. The first run
   after the re-pin then stops in `Set up job` naming it.
   `scripts/tests/test_third_party_actions_consumers_must_allow.py` reddens when
   the engine's third-party set changes, so you find out while committing rather
   than from a consumer. When it does: add the pattern to `docs/hardening.md`'s
   list, and say so in `docs/upgrading.md`'s section for the release — it is a
   setting the consumer has to change by hand before they re-pin.

2. Drive the consumer's workflow file directly at that ref — but **skip this
   step for a release that introduces that file.** `shipmate.yml` is not on any
   sample's default branch until this release lands, so there is nothing here
   to drive; it gets its first live exercise after the tag, like every other
   new path. From the release after, run it with the body `actions/dispatch`
   would build — exactly those keys, and no others:

   ```bash
   gh workflow run shipmate.yml --repo ship-iac/repo-example-stacks-aws --ref smoke/vX.Y.Z \
     -f verb=apply -f environment=sbx -f ref=<40-char-sha> -f pr_number=<n>
   ```

   **Why the skip, and not a `--ref` away.** A `workflow_dispatch` runs a
   workflow only if the file exists on the repository's default branch — the
   same resolution constraint as the paragraph below — so `--ref` picks which
   branch's copy runs, not whether the file is dispatchable at all. A file the
   release *adds* is on the scratch branch only, and dispatching it answers a
   404 indistinguishable from the failure this exercise exists to detect. Drive
   a file the release *changed*, never one it introduces; the command above
   sends exactly the keys `actions/dispatch` builds for the verb `-f verb=`
   names.

   **Not by commenting the verb.** An `issue_comment` workflow always runs from
   the repository's default branch, and the engine's `comment-ops.yml` passes
   `dispatch-ref: ${{ github.event.repository.default_branch }}` — so a comment
   drives the default branch's copy of `shipmate.yml` and dispatches that same
   copy, still on the *old* pin. The scratch
   branch is never read, and the smoke goes green without touching the new
   code.
3. Throw the branch away and cut the release as below.

**What this catches, and what it cannot.** It catches the class that genuinely
needs a consumer: the consumer file's `workflow_dispatch` input declarations
meeting the body the engine sends. Either half of that pair is rejected right
here, with no job started, and nothing in this repository can see it — an input
the engine sends that the file does not declare is a 422 "Unexpected inputs
provided", and a `required: true` input in that file the engine no longer sends
is a 422 "not provided". It also resolves and
parses the engine reusable workflow at the new SHA, because that happens when the
run graph is built.

It cannot reach a composite action's manifest. The job holding the engine's `$/`
action refs never starts: `detect` runs only behind `guard`, and `guard`
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

Confirm `manifest-load` is green on that exact commit. Its push-to-main run
covers whichever commit was `main`'s tip at the time, and a run that was
cancelled or never triggered leaves no coverage at all, silently:

```bash
gh run list --workflow manifest-load.yml --json headSha,conclusion --jq '.[] | select(.headSha == "<release-sha>")'
# nothing, or not success? dispatch it -- the release SHA is main's tip by now,
# and the steps' @main resolves to that same tip:
gh workflow run manifest-load.yml --ref main
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

- **Tag the commit consumers pin.** That is the release SHA, meaning `main`'s
  tip, which is also the commit `repo-example-stacks-aws` is re-pinned to.
  `doctor` resolves the tag with `repos/{slug}/commits/{tag}` and compares that
  SHA against each consumer pin; tagging any earlier commit instead reports
  correctly-pinned consumers as stale.
- **Never mark a release as prerelease.** `repos/{slug}/releases/latest` returns
  only the newest non-draft, non-prerelease release. While the repository had no
  releases at all this failed quietly; now it fails loudly in the wrong
  direction. A prerelease `v0.2.0` leaves `latest` pointing at `v0.1.0`, so every
  consumer correctly pinned to `v0.2.0`'s SHA is told its pin differs from the
  latest release and is instructed to re-pin backwards.
- **Releases are cut from `main` only.** A tag on a side branch names a commit
  that no consumer can reach by reading `main`, and one that `manifest-load`
  never ran against — it runs on push to `main`, so a side-branch commit has
  never had its manifests parsed by GitHub.

The order matters. Cut the release first, then re-pin `repo-example-stacks-aws`
to that same SHA, annotating the pin `# vX.Y.Z`. Re-pinning first would leave
the sample on a commit with no release, which is exactly the state the probe
reads as staleness.

```bash
python dev/repin_consumer.py --repo ../repo-example-stacks-aws --sha <release-sha> --label vX.Y.Z
```

`dev/repin_consumer.py` moves every engine reference in one pass (see
§ Consumers move every engine ref in one change) and refuses a target not
reachable from `origin/main` (exit 1), so it cannot re-pin a sample to a branch
commit.

**Keep the re-pin pull request pins-only when the release adds a fail-closed
check on data a plan writes.** The usual advice is the opposite — bump
`global.version` so the plan path fans out over real changes instead of greening
on an empty matrix — and it is right for most releases. It is wrong for this
class, because of where a plan run gets its workflow definition: under
`pull_request_target` that comes from the **base** branch, which still carries
the old pin. So a re-pin pull request's own plan always runs the *previous*
engine, and every cell it plans records whatever that engine wrote. Merge it and
the post-merge deploy — which does run the new engine, from `main` — refuses
every one of those cells, on a head with no pull request left to push to.

`v0.24.0` is the worked example: it refuses an apply whose plan carries no
plan-text digest, and a version-bumping re-pin would have stranded eight cells
that way. Land the re-pin with nothing pending, then bump the version in its own
pull request — whose plan does run the new engine, and which is the first real
exercise of the new behaviour.

The version line is `v0.x` while the action inputs, check names, and tag grammar
are still declared unstable in `README.md`. `--generate-notes` diffs against the
previous tag; the first release used hand-written notes because it had no
predecessor.

If a release is skipped, the probe is the alarm — but only on
`repo-example-stacks-aws`, and only once it moves past the release: its plan
runs' annotations then warn that the pin differs from the latest release. The
three `@main` samples carry a standing branch-ref warning instead and never
compare against a release. The state in between — engine merged, the sample not
yet re-pinned, no release cut — is silent, so do not rely on the alarm to
remember this step for you.

### Prove the crossing when a release changes what reaches a cell's environment

A release that changes how a cell's `TF_VAR_*` or `TF_WORKSPACE` are set has one
property worth testing, and it can only be tested once: a plan taken on the
**old** engine still applies on the **new** one. The apply-match fingerprint
compares the plan-side variables against the apply-side ones, so any difference
between the two routes fails the apply as stale — which is the result this
ordering is built to observe, and which re-pinning first destroys. After that
the new engine is only ever checked against itself.

Run it after the tag on `repo-example-stacks-aws` when it carries the identity
the release moved. On a `@main` sample the crossing happens when the engine
pull request merges, so record step 1's plan there before that merge. In this
order:

1. **On the old pins, record a pending plan.** Open a pull request that bumps
   `global.version` and runs `terramate generate` + `terramate fmt`, let it
   plan, and leave it unapplied. A pins-only pull request plans zero cells —
   change detection is `terramate list --changed` — and `detect` fails the run
   on stale codegen or bad formatting before any cell starts.
2. **Merge the pin bump**, waiting for the sample to go quiet first.
   Commenting an apply while a plan is still running fails the next apply with
   "saved plan is stale" for an unrelated reason, which reads as this test
   failing. Merging is not optional: the comment-driven apply runs the **default
   branch's** workflow, so a bump left on a branch is not the version under
   test.
3. **Apply each recorded plan.** Every one must succeed. This is the crossing —
   plan on the old engine, apply on the new.

**Do not push to a recorded pull request between step 1 and its apply.** "Update
branch", a rebase and a merge from the default branch all push a new head, which
fires `synchronize` and re-plans on the new engine: the recorded plan is
replaced, the crossing disappears, and the apply then passes having proved
nothing. If a ruleset demands an up-to-date branch before merging, update after
the apply has run.

This is the one case that overrides "land the re-pin with nothing pending"
above. That rule exists for a release adding a fail-closed check on data a plan
writes, where a cell planned by the old engine is refused after the merge; here
a cell planned by the old engine is the measurement. The re-pin pull request
itself still stays pins-only — the version bump is its own pull request, opened
before it. A release in both classes wants both orderings at once, so split it
into two releases.
