# Releasing

Consumers pin shipmate's reusable workflows by commit SHA. The engine itself has
no pins: every engine step calls its action as `$/actions/<name>`, which GitHub
resolves in this repository at the commit of the reusable workflow the consumer
pinned, and the engine's own nested workflows, `apply-env-level.yml` and
`apply-review.yml`, are reached as `./.github/workflows/<file>`. One commit, one tree.
`scripts/tests/test_engine_self_reference.py` refuses a
`ship-iac/shipmate/<path>@<sha>` reference in any workflow or action manifest.

## Consumers move every engine ref in one change

The seven reusable workflows share inputs and secrets across a release, so a consumer re-pins
all seven `uses:` lines in one commit (§ Re-pin a consumer) and never
merges a Dependabot pull request that bumps one line alone.

### Re-pin a consumer

`<release-sha>` is the release's full 40-hex commit (`git rev-list -n1 vX.Y.Z` once tagged),
never a short SHA. `<consumer>` is the consumer repository's checkout. Use GNU sed: `-b`
keeps a CRLF file's line endings under Git Bash. On macOS, install GNU sed and run it as
`gsed`, because BSD sed rejects `-b`.

1. From the engine clone, check that `<release-sha>` is 40 hex and on `origin/main`, then
   rewrite every engine ref. The checks are `&&`-chained, so a bad SHA rewrites nothing. A
   short SHA or a tag would be written into every pin, and step 2 compares against the same
   value, so it would pass. A commit reachable only from a branch stops resolving once that
   branch is force-pushed or deleted.

   ```bash
   [[ <release-sha> =~ ^[0-9a-f]{40}$ ]] && git fetch origin main && git merge-base --is-ancestor <release-sha> origin/main && \
     sed -b -i -E 's|(ship-iac/shipmate/[^@[:space:]"]+)@[0-9a-f]{40}("?)([[:space:]]+# v[^[:space:]]*)?|\1@<release-sha>\2 # vX.Y.Z|' <consumer>/.github/workflows/*.yml
   ```

2. List the engine refs the rewrite left behind. It must print nothing, because every engine
   ref moves in one commit. A printed line is a ref the `sed` does not handle (`@main`, a
   single-quoted ref, a comment after the label): fix it by hand and run step 2 again.

   ```bash
   grep -nE 'ship-iac/shipmate/[^@[:space:]"]+@' <consumer>/.github/workflows/*.yml | grep -vE '@<release-sha>"? # vX\.Y\.Z'$'\r''?$'
   ```

3. Commit the rewrite as one commit.

The `sed` reads `.yml` files only. Every consumer file this engine renders is `.yml`; rename a
`.yaml` workflow that calls the engine, or re-pin it by hand.

## Manifest load

`.github/workflows/manifest-load.yml` lets GitHub parse every action manifest,
because GitHub is what parses them in production and `yaml.safe_load` is more
permissive. An unquoted description containing a comma inside a `{ }` flow
mapping splits — in flow context a comma is a separator — so PyYAML yields an
extra key named for the tail of the sentence and accepts the file, while
`GitHub.DistributedTask.ObjectTemplating` refuses it outright. `v0.16.0` shipped
that and every apply and deploy job died in `Set up job`, before its first step.

The workflow is one job of 21 steps, each `if: false` and each `uses:` one action
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
   ```

   Then run § Re-pin a consumer with `<consumer>` set to `../repo-example-stacks-aws` and
   `<release-sha>` set to the release commit.

   **The re-pin rewrites pins and nothing else.** When a release
   changes the consumer file's declared input contract, make those body edits on
   the scratch branch too — a new pin under an old body is the load-time
   rejection described below, not a smoke result.

   The same gap has a second form the re-pin cannot reach at all: a consumer's
   allowed-actions list is a repository setting, not a file. Under
   `docs/hardening.md` row 12 a consumer restricts `allowed_actions` to a named
   pattern list, and those patterns end `@*` — so a version bump is absorbed,
   but a third-party action this release *adds or renames* is not. The first run
   after the re-pin then stops in `Set up job` naming it.
   `scripts/tests/test_third_party_actions_consumers_must_allow.py` reddens when
   the engine's third-party set changes, so you find out while committing rather
   than from a consumer. When it does: add the pattern to `docs/hardening.md`'s
   list — it is a setting the consumer has to change by hand.

2. Drive the consumer's workflow file directly at that ref, with the body
   `actions/dispatch` would build — exactly those keys, and no others:

   ```bash
   gh workflow run shipmate.yml --repo ship-iac/repo-example-stacks-aws --ref smoke/vX.Y.Z \
     -f verb=apply -f environment=sbx -f ref=<40-char-sha> -f pr_number=<n>
   ```

   A `workflow_dispatch` runs a workflow only if the file exists on the
   repository's default branch — the same resolution constraint as the
   paragraph below — so `--ref` picks which branch's copy runs, not whether the
   file is dispatchable at all. `shipmate.yml` is on every sample's default
   branch, so `--ref` runs the scratch branch's copy. The command above sends
   exactly the keys `actions/dispatch` builds for the verb `-f verb=` names.

   **Not by commenting the verb.** An `issue_comment` workflow always runs from
   the repository's default branch, and the engine's `actions/dispatch` dispatches
   on `github.event.repository.default_branch` — so a comment
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
provided", and a `required: true` input in that file that the engine does not send
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

A `verb=plan` dispatch on the scratch branch runs the cells and stops at
`plan.yml`'s `summary`, the only job holding the App key. It binds the
`shipmate-engine` environment, whose deployment branch policy allows the
default branch only, so the job fails at startup: zero steps, no log, and the
reason only in the check-run annotations — `Branch "smoke/vX.Y.Z" is not
allowed to deploy to shipmate-engine due to environment protection rules.`
Read that run as green when every cell job succeeded and `summary` carries this
annotation; any other `summary` failure is a finding. The gate status, the plan
comment and the apply checks get their first live run after the tag. Do not
loosen the branch policy to smoke them: that hands the App key to every branch.

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
`gh release create v0.2.0 --target <sha>` is rejected on this repository — `tag_name is not a valid tag` / `Release.target_commitish is invalid`,
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
  only the newest non-draft, non-prerelease release. A prerelease `v0.2.0` leaves `latest` pointing at `v0.1.0`, so every
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

Run § Re-pin a consumer with `<consumer>` set to `../repo-example-stacks-aws`. Its
ancestor check refuses a target not reachable from `origin/main`, so it cannot re-pin a
sample to a branch commit.

**A re-pin pull request is always pins-only.** Bump `global.version` in its own
pull request afterwards, whose plan runs the new engine. Under
`pull_request_target` a plan run takes its workflow definition from the **base**
branch, which still carries the old pin, so a re-pin pull request's own plan runs
the *previous* engine, and a fail-closed check the new engine adds on data a plan
writes refuses every cell it planned after the merge, on a head with no pull
request left to push to. `v0.24.0` is the worked example: it refuses an apply
whose plan carries no plan-text digest, and a version-bumping re-pin would have
stranded eight cells that way.

The version line is `v0.x` while the action inputs, check names, and tag grammar
are still declared unstable in `README.md`. `--generate-notes` diffs against the
previous tag.

If a release is skipped, the probe is the alarm — but only on
`repo-example-stacks-aws`, and only once it moves past the release: its plan
runs' annotations then warn that the pin differs from the latest release. The
three `@main` samples carry a standing branch-ref warning instead and never
compare against a release. The state in between — engine merged, the sample not
yet re-pinned, no release cut — is silent, so do not rely on the alarm to
remember this step for you.
