# Upgrading

How a consuming repository moves to a newer shipmate, and what past releases
needed beyond a pin bump.

## Re-pinning

**Every engine reference moves in one change.** A repository that bumps some
pins and leaves others behind is running two engine versions against one
contract — the actions, the reusable workflow calls, and any pin inside a
composite action you wrap all name the same SHA, or they disagree.
[`../CONTRACT.md`](../CONTRACT.md) §Consumption is the rule;
[`releasing.md`](releasing.md) is the maintainer side of the same cascade.

Consumers pin by **commit SHA**, never by tag or branch name, optionally with a
trailing `# vX.Y.Z` comment naming the release that SHA belongs to
(`uses: <owner>/shipmate/actions/state@<sha> # v0.1.0`). The comment is for
human readers and for Dependabot's bookkeeping; the ref that resolves is always
the SHA. The SHA of record for a release is named in that release's section of
[`../CHANGELOG.md`](../CHANGELOG.md).

## Dependabot

shipmate publishes a GitHub Release per release SHA, so a consumer with
Dependabot's `github-actions` ecosystem enabled receives a pull request bumping
its shipmate pins to the new release's SHA. Dependabot proposes and the
consumer's own `CODEOWNERS` review disposes; what lands is still a full commit
SHA.

This works from a pin that is itself a released commit. From a pin at an
untagged commit Dependabot has nothing to compare against and stays silent —
the signal is then `shipmate doctor`'s pin-freshness probe, which reports a pin
that differs from the latest release, plus the annotation it emits on every plan
run. See [`troubleshooting.md`](troubleshooting.md) §shipmate doctor.

## Migrating from another TACO

A repository that already works under another Terraform automation tool arrives
with four things expressed somewhere shipmate does not read. None of them is a
shipmate defect and none of them announces itself, so each is worth a deliberate
pass before the first plan run.

**Ordering.** Wave ordering comes **only** from the Terramate `after` DAG. If
your ordering lives in the outgoing tool's configuration, it must be ported into
`after` — and **nothing will report the omission**, because a missing edge is
indistinguishable from a stack that is genuinely independent. Treat the outgoing
tool's config as a *lower bound* on the real graph, not as the graph: one
migration that audited the OpenTofu code instead of porting the config went from
6 declared edges to 105, and from 2 wave levels to 6.

Two detect jobs report the shape as a `::notice::` line — stack count, `after`
edge count, wave levels, and how many stacks would apply concurrently. A reader
who knows the repository can judge that last number immediately; nobody else
can. Exactly two print it: the detect job of a dispatched `shipmate apply <env>`
and the post-merge deploy's detect, so it arrives **after** the pull request
that would have been the place to fix the graph. A **plan** run does not print
it, and neither does a bare `shipmate apply` — seeing no such line there says
nothing about the graph. Before that point the equivalent is
`terramate experimental run-graph --label stack.dir` run locally.

**Tags.** Environment membership is derived from `env/<name>` tags and nothing
else. Terramate tags are otherwise free-form, so a repository that predates
shipmate is likely using them for something unrelated. See
[`getting-started.md`](getting-started.md) §Before you start.

**A named AWS profile in generated HCL.** The apply path holds only the OIDC
session, so a literal `profile` in a `provider` or `backend` block fails there
while still planning fine locally. See [`aws.md`](aws.md).

**`terramate.config.run.env` rewriting `TF_VAR_*`.** Terramate applies `run.env`
after the ambient environment, so an assignment to `TF_VAR_env`, `TF_VAR_region`
or `TF_WORKSPACE` wins over what the GitHub Environment injected — invisibly,
because the fingerprint is computed outside `terramate run` and so agrees on
both sides. `detect` injects a sentinel into those three variables and fails the
run when one comes back changed. [`../CONTRACT.md`](../CONTRACT.md) §Env model
has the rule and the `tm_try` form that keeps a local default.

## Past migrations

An entry applies only if you are moving *from* a pin older than the release it
names. The entries below `0.2.0` predate the first tagged release, or
`CHANGELOG.md` does not pin one; they are kept for repositories moving from a
very old pin.

### 0.12.0 — wrappers pass secrets by name, and re-pinning alone is not enough

Replace `secrets: inherit` in every wrapper job that calls an engine reusable
workflow, in the same change that moves your pins. Pass **only what each callee
declares** — naming one it does not is a load-time error that kills the run with
no job and no log:

| Your wrapper job calls | Pass |
|---|---|
| `summary.yml` (in `plan.yml`) | `SHIPMATE_APP_PRIVATE_KEY` |
| `apply.yml`, `apply-all.yml`, `deploy.yml` | that **and** `SHIPMATE_PLAN_PASSPHRASE` |

```yaml
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}
```

Dropping the passphrase line where a callee declares it is the quiet failure to
watch for: the run starts, and every apply cell then fails its plan-decrypt
fail-safe. Repositories that never set `SHIPMATE_PLAN_PASSPHRASE` pass it
anyway — it resolves to empty, which is what an unencrypted consumer already
had.

Key placement does not change: `SHIPMATE_APP_PRIVATE_KEY` stays a secret on the
`shipmate-engine` environment. A called workflow's `environment:` resolves in
*your* repository and its value wins over whatever the caller passed, so a
wrapper job that binds no environment passes an empty string and the mint still
succeeds.

Inside the engine's own organization `inherit` keeps working, so a repository
that skips this stays green while handing the engine every secret it can see
([`hardening.md`](hardening.md) §What the engine receives from your repository).
Outside it, `inherit` delivers nothing **and** suppresses what the
`shipmate-engine` environment would have supplied: no `shipmate / gate`, no
pending `apply / <stack> / <env>` checks, no sticky comment.

### 0.10.0 — the plan path is one workflow, and re-pinning alone is not enough

You must rewrite `.github/workflows/plan.yml` and delete
`.github/workflows/summary.yml` in the same change that moves your pins. A
repository that re-pins without rewriting gets no `shipmate / gate`, so its pull
requests cannot merge — the old `workflow_run` topology is not supported.

`plan.yml` moves to `pull_request_target` and gains a third job, `summary`,
which is `uses: <owner>/shipmate/.github/workflows/summary.yml@<sha>` with
`secrets: { SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }} }`
— that secret alone, since a callee rejects a name it does not declare — and
five inputs (`pr-number`, `head-sha`, `detect-result`,
`plan-result`, `planned-cells`), and `permissions: contents: read` — a callee's
permissions are capped by the calling job's, and granting less kills the whole
run at startup with no job, no log and no annotation to explain it. Because
`pull_request_target` checks out the base by default, `detect` and `plan` must
name `ref: ${{ github.event.pull_request.head.sha }}` on their checkout
explicitly — without it they plan the base branch and report a clean plan for a
pull request they never read. The consumer's own `summary.yml` is deleted; the
engine's is a `workflow_call` workflow whose single job binds `shipmate-engine`,
checks out nothing, and carries both trust conditions (the fork refusal and the
draft skip) where a consumer cannot drop them.
[`getting-started.md`](getting-started.md) §Required — plan has the current
shape, and [`../CONTRACT.md`](../CONTRACT.md) §Post-plan topology is the written
form.

**The migration pull request cannot gate itself, and needs a one-time bypass.**
A `pull_request` run uses the workflow file from the pull request's own head,
which no longer declares that trigger; a `pull_request_target` run uses the file
on the default branch, which does not declare it yet. So the pull request that
performs the migration produces no plan run, no checks and no `shipmate / gate`
at all. No ordering avoids it: the first commit whose head declares only
`pull_request_target` is ungatable by construction. Merge that one pull request
with an administrative bypass (an org-admin `bypass_actor` on the ruleset, or a
temporary `enforcement: evaluate`), and restore enforcement immediately
afterwards. Every pull request after it gates normally.

Plans now run against the branch tip rather than the merge commit: the explicit
`head.sha` checkout does not produce a merge ref, so a pull request behind its
base plans what the branch says. The surviving safety net is the stale-plan
refusal at apply time; **require branches to be up to date before merging**
([`branch-protection.md`](branch-protection.md)) closes the rest.

### 0.2.0 — apply check-name field order flipped

**Every pull request planned before the bump needs a new commit, and a re-plan
is not enough.** The apply check is now `apply / <stack> / <env>` (was `apply / <env> /
<stack>`). Nothing in branch protection references it (only `shipmate / gate` is
required), so no ruleset edit is needed — but old-order checks left on a head
SHA break **both** directions, and a re-plan fixes neither, because it adds the
new names *alongside* the old ones on the same commit:

- **Old-order checks still pending → the gate never greens.** `apply-gate`
  treats every `apply / `-prefixed check on the SHA as part of the work queue,
  and the new engine only ever completes the new-order names, so the old
  pending ones stay pending forever.
- **Old-order checks already complete → the post-merge deploy re-queues work
  that is already applied.** This one has no pre-merge signal at all: the gate
  is green and the PR looks finished. On merge, `deploy-detect` reconstructs
  the new-order name for each reviewed cell, finds none of them among the
  completed checks, and treats every applied cell as pending. Each re-apply
  then trips the stale-plan fail-safe (state has moved), so the deploy on
  `main` goes red per stack instead of no-opping. Nothing is applied twice —
  the fail-safe holds — but the deploy needs recovery.

**Do this:** on every open PR that already has apply checks, push a commit (any
commit) so the fan-out lands on a fresh head SHA, then re-plan and, if it was
applied pre-merge, re-apply under the new engine. Note that a check run **cannot
be deleted or dismissed** — the Checks API offers only create, update, get, list
and rerequest — so the only alternative to a fresh commit is completing the
stale check-run ids yourself with a shipmate-App installation token (a check run
is updatable only by the app that created it, and these were App-authored). If a
deploy already went red this way, the stacks are in fact applied: re-plan on a
follow-up PR, get "no changes" checks, and the next deploy no-ops green. New PRs
are unaffected.

### Flip `integration_id` in the same change as the engine-SHA bump

Move the ruleset's required-check `integration_id` from the `github-actions`
identity (`15368` on github.com) to the shipmate App's numeric id
(`SHIPMATE_APP_ID`) at the same time you bump the pinned engine SHA to a build
that authors the gate via the App. Landing the SHA bump first (or the
`integration_id` flip first) leaves a window where the writer and the pinned
identity disagree and every `shipmate / gate` status is rejected as
non-satisfying, blocking all merges until both land together.

### Open PRs need a fresh commit or re-plan after the flip

A PR opened before the upgrade may be carrying: (a) `github-actions`-authored
pending `apply / <stack> / <env>` checks that the App-authored `apply-cell`
cannot complete (different identity — check-run completion is scoped to the
creating app), and (b) an existing `shipmate / gate` status authored by the old
identity, which no longer satisfies the newly-pinned `integration_id`. Push a
commit (or re-run `plan.yml`) on each open PR after upgrading so a fresh,
App-authored set of checks and gate status is produced.

### Gate-writer jobs no longer need `statuses: write` / `checks: write` on `GITHUB_TOKEN`

The App manifest now carries both scopes, so `actions/summary` /
`actions/gate-refresh` mint their own installation token instead of relying on
the calling job's `GITHUB_TOKEN` permissions. Remove any `statuses: write` /
`checks: write` grant from jobs that run these actions, and thread `app-id` +
`private-key` into the `with:` of each call (for a reusable-workflow caller job,
pass `SHIPMATE_APP_PRIVATE_KEY` by name in the job's `secrets:` block instead —
the called workflow declares it under `on.workflow_call.secrets`; on the apply
and deploy paths that block carries `SHIPMATE_PLAN_PASSPHRASE` too, see
§0.12.0). A leftover
`GITHUB_TOKEN` grant is stale, not harmful by itself, but remove it — the writer
step doesn't use it, and it needlessly widens the default token's scope.

### Required status check renamed

If your branch protection currently requires the aggregate gate check under its
pre-rename name, update it to require `shipmate / gate` instead — in the **same
change** that bumps your pinned engine SHA. Otherwise GitHub keeps waiting on
the old required check forever, and PRs can't merge even though the new engine
is reporting `shipmate / gate`.

### Plan workflow renamed `preview.yml` → `plan.yml`

shipmate resolves a PR's reviewed plan by the workflow filename that produced
it. A PR whose plan was produced by an old `preview.yml` run is not recognized
after your workflow is renamed to `plan.yml` — push a commit to trigger a fresh
`plan.yml` run and get it re-reviewed before `shipmate apply` or a merge-deploy
will act on that PR.
