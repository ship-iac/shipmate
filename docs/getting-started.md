# Getting started

This page wires shipmate into one repository, in four ordered tiers, using AWS as
the worked example. [`concepts.md`](concepts.md) explains what the engine then
does with that wiring.

## Before you start

- **A repository with Terramate stacks, each tagged `env/<name>`.** The tag is
  how a stack declares its environment membership — no environment name ever
  appears in workflow YAML. Tag grammar:
  [`../CONTRACT.md`](../CONTRACT.md) §Tag grammar.
- **The Terramate and OpenTofu versions the engine pins.** They are in
  [`../VERSIONS`](../VERSIONS); set them as the repository variables
  `TERRAMATE_VERSION` and `TOFU_VERSION`, which the workflows below read.
- **`gh` authenticated with admin on the repository** — every tier creates
  environments, variables or rulesets.
- **Remote state you control**, or a local backend materialized in the working
  tree. AWS S3 is what [`aws.md`](aws.md) covers.

The four tiers are ordered and each depends on the one before. Tier 1 alone is
not a working installation; read tier 2's first paragraphs before deciding to
stop early.

## Required — plan

This tier gets you a plan per changed stack × environment on every pull request,
and — because `plan.yml`'s `summary` job creates them — the pending
`apply / <stack> / <env>` checks and the `shipmate / gate` commit status that
branch protection will require.

**This tier is not usable alone.** The `summary` job mints a GitHub App
installation token, so without the App there is no `summary` job: no gate status
and no apply checks at all.

Register and install the App **first** — [`github-app.md`](github-app.md) is the
one-time runbook. It is a prerequisite of this tier, not an optional extra.

### Environments for this tier

Every logical environment needs a GitHub Environment pair (`<env>`,
`<env>-apply`), plus the one fixed `shipmate-engine` environment that holds the
App key ([`github-app.md`](github-app.md)). `<env>`/`<env>-apply` are never named
in workflow YAML at all — they're read from Terramate stack tags at runtime. This
tier needs `<env>` and `shipmate-engine`; `<env>-apply` is the apply tier's.

Create each with `gh api -X PUT repos/<owner>/<repo>/environments/<name>`, then
set protection rules from Settings → Environments → `<name>` (or the API):

- **`shipmate-engine`** (create once, regardless of how many env tiers you
  run): deployment branch policy **Selected branches**, naming exactly the
  default branch. No reviewers — this environment's job is scoping the App
  key to trusted workflow runs, not gating a human decision, and reviewers
  here would stall every plan and apply run waiting for an approval nobody is
  meant to give. `shipmate doctor` checks both that this environment exists
  and that its policy actually names the default branch.
- **`<env>`** (plan): no reviewers, no deployment branch policy at all —
  reviewers block every plan cell, and a branch policy blocks every plan cell
  whose pull request targets a branch it does not name (plan jobs run at the
  pull request's *base* ref) ([`hardening.md`](hardening.md) §8;
  `shipmate doctor` warns on either).

### The plan workflow

`plan.yml` is three jobs — `detect`, `plan`, `summary` — not a thin wrapper. The
`ref: ${{ github.event.pull_request.head.sha }}` on the two checkouts is
load-bearing: `pull_request_target` runs at the *base* ref, so without it the run
plans the base branch and reports a clean plan for code it never read.

```yaml
name: shipmate · plan
on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]
concurrency:
  group: plan-${{ github.event.pull_request.number }}
  cancel-in-progress: true
permissions:
  contents: read
jobs:
  detect:
    name: shipmate / detect
    if: github.event.pull_request.draft == false
    runs-on: ubuntu-slim
    permissions:
      contents: read
    outputs:
      matrix: ${{ steps.matrix.outputs.matrix }}
      empty: ${{ steps.matrix.outputs.empty }}
      count: ${{ steps.matrix.outputs.count }}
    steps:
      # `pull_request_target` checks out the BASE by default. Naming the head
      # SHA is what makes this a plan of the pull request; without it the run
      # plans the base branch and reports a clean plan for code it never read.
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0
      - uses: ship-iac/shipmate/actions/setup@<engine-sha>  # see the latest release
        with:
          terramate-version: ${{ vars.TERRAMATE_VERSION }}
          tofu-version: ${{ vars.TOFU_VERSION }}
      - name: fmt check
        run: terramate fmt --check
      - name: stale codegen check
        run: terramate generate --detailed-exit-code
      - id: matrix
        uses: ship-iac/shipmate/actions/build-matrix@<engine-sha>  # see the latest release
        with:
          base-sha: ${{ github.event.pull_request.base.sha }}

  plan:
    needs: detect
    if: needs.detect.outputs.empty == 'false'
    runs-on: ubuntu-slim
    permissions:
      contents: read
      id-token: write
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.detect.outputs.matrix) }}
    environment: ${{ matrix.environment }}
    name: ${{ matrix.stack }} / ${{ matrix.environment }}
    env:
      TF_VAR_env: ${{ vars.TF_VAR_env }}
      TF_VAR_region: ${{ vars.TF_VAR_region }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0
      - uses: ship-iac/shipmate/actions/setup@<engine-sha>  # see the latest release
        with:
          terramate-version: ${{ vars.TERRAMATE_VERSION }}
          tofu-version: ${{ vars.TOFU_VERSION }}
      - uses: aws-actions/configure-aws-credentials@e6de054238d6b7531b4efff3b6587d9aade6a06c # v6.2.3
        with:
          role-to-assume: ${{ vars.AWS_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - uses: ship-iac/shipmate/actions/plan-cell@<engine-sha>  # see the latest release
        with:
          stack: ${{ matrix.stack }}
          stack-name: ${{ matrix.stack }}
          env: ${{ matrix.environment }}
          plan-passphrase: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}

  # Everything trusted happens inside the engine's reusable workflow: one job,
  # `environment: shipmate-engine`, no checkout, and both trust conditions (the
  # fork refusal and the draft skip) on its own `if:`. `permissions` is not
  # optional — a callee's permissions are capped by this job's, and granting
  # less kills the run at startup with no job and no log.
  summary:
    needs: [detect, plan]
    if: ${{ !cancelled() }}
    uses: ship-iac/shipmate/.github/workflows/summary.yml@<engine-sha>  # see the latest release
    permissions:
      contents: read
    secrets: inherit
    with:
      pr-number: ${{ github.event.pull_request.number }}
      head-sha: ${{ github.event.pull_request.head.sha }}
      detect-result: ${{ needs.detect.result }}
      plan-result: ${{ needs.plan.result }}
      planned-cells: ${{ needs.detect.outputs.count }}
```

Permissions on this path are narrow: the top level and each job grant
`contents: read`, and the `summary` caller grants only that — the trusted work
inside it runs on a freshly minted App token, not on `GITHUB_TOKEN`. The one
addition above is AWS-specific: this sample's `plan` job grants `id-token: write`
solely so `configure-aws-credentials` can assume the read-only plan role. A
consumer with no plan-time cloud credentials drops both that grant and the
credentials step.

## Required — apply

This tier gets you `shipmate apply` in a pull request comment (a pre-merge apply
of the reviewed plan) and an idempotent post-merge apply on push to the default
branch.

**The two are coupled, and the coupling decides which files you need.** If you
require `shipmate / gate` as a branch-protection check — tier 3 — then applies
have to happen **pre-merge**, because the gate stays non-green while any
`apply / <stack> / <env>` check is pending. A repository that only runs
`deploy.yml` can never green the gate, so the merge that would trigger
`deploy.yml` deadlocks. `deploy.yml` alone is therefore sufficient only for a
repository that does *not* require the gate.

### Environment setup

`<env>-apply` splits by tier, and this is the split
[`hardening.md`](hardening.md) describes at the credential level (§6–9) restated
as environment settings. Create each with
`gh api -X PUT repos/<owner>/<repo>/environments/<name>`, then set protection
rules from Settings → Environments → `<name>` (or the API):

- **dev / staging — branch policy only, self-service.** Deployment branch
  policy restricted to the default branch (closes the direct-branch-secret
  path, [`hardening.md`](hardening.md) #17); no required reviewers, so
  `shipmate apply` proceeds without a human in the loop. Deliberate: these tiers
  exist so a team can self-serve, and their blast radius doesn't warrant a
  reviewer.
- **prod — branch policy *and* required reviewers *and* "Prevent
  self-review".** Same branch policy, plus required reviewers (a team, not
  one person) with self-review prevented ([`hardening.md`](hardening.md) #6) —
  the one gate an App token cannot forge, since a reviewer decision is a human
  action a minted token cannot take. List `prod` in
  `global.shipmate.explicit_envs` too, so a bare `shipmate apply` skips it and
  it is only ever reached via the targeted `shipmate apply prod` (which then
  pauses for the environment reviewer).

### The apply workflows

> **Two things bite everyone on this tier.**
>
> **`id-token: write` on the calling job of every wrapper that calls the
> apply-path reusable workflows — including consumers with no cloud credentials
> at all.** GitHub caps a called workflow's permissions at each `uses:`
> boundary, and the apply-path workflows request it, so without the grant the
> run fails at workflow-resolution time. If the wrapper declares a top-level
> `permissions:` block, it needs the grant there too. This applies to `apply.yml`
> and `deploy.yml` wrappers, **not** to `plan.yml`.
>
> **`state_suffix` is required but may be `""`.** It is an input of the engine's
> reusable `apply.yml` and `deploy.yml` (both `required: true`), not of the plan
> path. `""` means a remote backend owns the state, and the engine's state
> restore/save steps are skipped. Omitting the input entirely is a
> workflow-resolution error on purpose: a forgotten state configuration must
> fail loud rather than apply with no state at all.

`comment-ops.yml` turns a `shipmate <verb>` pull request comment into an
authorized `workflow_dispatch` of `apply.yml`.

```yaml
name: comment-ops
on:
  issue_comment:
    types: [created]
permissions:
  contents: read
  issues: write
  pull-requests: write
  actions: read
concurrency:
  group: comment-ops-${{ github.event.issue.number }}
  cancel-in-progress: false
jobs:
  ops:
    # Only PR comments (issue_comment fires on issues too).
    if: ${{ github.event.issue.pull_request }}
    runs-on: ubuntu-slim
    environment: shipmate-engine
    steps:
      - id: authz
        uses: ship-iac/shipmate/actions/comment-ops@<engine-sha>  # see the latest release
        with:
          app-id: ${{ vars.SHIPMATE_APP_ID }}
          private-key: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
          approvers-team: ${{ vars.SHIPMATE_APPROVERS_TEAM }}
          comment-body: ${{ github.event.comment.body }}
          comment-user: ${{ github.event.comment.user.login }}
          comment-id: ${{ github.event.comment.id }}
          pr-number: ${{ github.event.issue.number }}
          github-token: ${{ github.token }}
      - if: ${{ steps.authz.outputs.authorized == 'true' }}
        uses: ship-iac/shipmate/actions/dispatch@<engine-sha>  # see the latest release
        with:
          app-id: ${{ vars.SHIPMATE_APP_ID }}
          private-key: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
          environment: ${{ steps.authz.outputs.environment }}
          ref: ${{ steps.authz.outputs.head-sha }}
          pr-number: ${{ github.event.issue.number }}
          plan-run-id: ${{ steps.authz.outputs.plan-run-id }}
          dispatch-ref: ${{ github.event.repository.default_branch }}
          repository: ${{ github.repository }}
```

Its `ops` job names `environment: shipmate-engine` directly. `shipmate-engine`
is the one *literal* environment name that appears in workflow YAML
([`../CONTRACT.md`](../CONTRACT.md)'s one carve-out to "no env names in workflow
YAML — ever"), because it names a single fixed thing rather than a per-repo
logical environment. Most of its appearances are inside the engine's own
reusable workflows, but two consumer-owned workflows declare it directly too —
`comment-ops.yml`'s `ops` job and `drift.yml`'s `issues` job — because both mint
the App token themselves rather than delegating to a called reusable workflow,
and both run at the default-branch ref by construction (`issue_comment`, the
nightly `schedule`), which is what lets them.

`apply.yml` is the dispatch target: a targeted `shipmate apply <env>` routes to
the engine's `apply.yml`, a bare `shipmate apply` to `apply-all.yml`.

```yaml
name: shipmate · apply
on:
  workflow_dispatch:
    inputs:
      environment:
        description: Target environment (empty = bare `shipmate apply`, all non-explicit environments)
        required: false
        default: ''
      ref: { description: PR head SHA to apply, required: true }
      pr_number: { description: PR number, required: true }
      plan_run_id: { description: Plan run id with the reviewed plans, required: true }
permissions:
  contents: read
  actions: read
  pull-requests: read
  id-token: write
jobs:
  targeted:
    if: ${{ inputs.environment != '' }}
    uses: ship-iac/shipmate/.github/workflows/apply.yml@<engine-sha>  # see the latest release
    permissions: { contents: read, checks: read, actions: read, id-token: write }
    secrets: inherit
    with:
      environment: ${{ inputs.environment }}
      ref: ${{ inputs.ref }}
      pr_number: ${{ inputs.pr_number }}
      plan_run_id: ${{ inputs.plan_run_id }}
      state_suffix: ""
  all:
    if: ${{ inputs.environment == '' }}
    uses: ship-iac/shipmate/.github/workflows/apply-all.yml@<engine-sha>  # see the latest release
    permissions: { contents: read, checks: read, actions: read, id-token: write }
    secrets: inherit
    with:
      ref: ${{ inputs.ref }}
      pr_number: ${{ inputs.pr_number }}
      plan_run_id: ${{ inputs.plan_run_id }}
      state_suffix: ""
```

`deploy.yml` applies, on push to the default branch, every reviewed plan whose
apply check is still pending — so it no-ops when everything was applied
pre-merge.

```yaml
name: shipmate · deploy
on:
  push:
    branches: [main]
concurrency:
  group: deploy-main
  cancel-in-progress: false
permissions:
  contents: read
  pull-requests: read
  actions: read
  id-token: write
jobs:
  deploy:
    # Display only: the run's job path reads
    # `shipmate · deploy / post-merge / L<n> / apply / <stack> / <env>`.
    name: post-merge
    uses: ship-iac/shipmate/.github/workflows/deploy.yml@<engine-sha>  # see the latest release
    permissions: { contents: read, checks: read, pull-requests: read, actions: read, id-token: write }
    secrets: inherit
    with:
      state_suffix: ""
```

## Required — enforce the gate

Require the status check `shipmate / gate` (verbatim) on the default branch, and
only that check. It is a **commit status**, not a check-run — a
`required_status_checks` entry matches a commit status by `context` exactly as it
matches a check-run. The ruleset must also pin `integration_id` to the shipmate
App's numeric id (`SHIPMATE_APP_ID`), so that a status of that name posted by any
other identity does not satisfy the rule.

[`branch-protection.md`](branch-protection.md) has the pasteable ruleset, the
gate's state table, and the upgrade notes. Configure it from there.

## Optional

### Drift detection

A nightly `drift.yml` plans every stack × environment against real state, then
opens, updates and closes drift Issues from what those cells report. It is a
consumer-owned workflow whose credentialed jobs run only at the default-branch
ref, and it needs the `shipmate-engine` environment from the plan tier.

### Recipe: automerge after apply

Because the merge gate is the single `shipmate / gate` check, GitHub's
native auto-merge composes with shipmate for free — no engine configuration,
no extra workflow. Once auto-merge is armed on a PR, finishing the applies is
the last green check, so the PR merges itself:

1. **One-time repo setting:** allow auto-merge —
   `gh repo edit <owner>/<repo> --enable-auto-merge` (or Settings → General →
   "Allow auto-merge").
2. **Per PR:** review and approve, arm auto-merge
   (`gh pr merge <n> --auto --merge`, or the "Enable auto-merge" button), then
   comment `shipmate apply`. When every environment's applies complete,
   `shipmate / gate` flips to `success` and GitHub merges the PR.

Properties that fall out of the existing gate semantics:

- **Explicit environments still gate.** An environment listed in
  `global.shipmate.explicit_envs` is skipped by the bare `shipmate apply` and its
  apply checks stay pending — gate stays pending, so auto-merge waits
  until someone runs the targeted `shipmate apply <env>`. Arming auto-merge never
  weakens the apply-before-merge guarantee; it only removes the final click.
- **Stale bases don't sneak through.** With "require branches up to date"
  (strict), a base moved since the plans ran blocks the auto-merge until the
  branch is updated — and updating re-runs the plan on the new head, which
  resets gate to pending until the fresh plans are applied. The
  exact-plan invariant is preserved.
- **The post-merge deploy still runs.** GitHub performs the auto-merge as the
  user who armed it (not `GITHUB_TOKEN`), so the resulting push event triggers
  `deploy.yml` normally — which no-ops idempotently when everything was
  applied pre-merge.
- **Any merge method works.** Squash merges are fine: `deploy-detect` maps the
  merge commit back to the PR head SHA via the commit→PR association, not the
  commit graph.

### Further hardening

Everything above is the minimum that works. [`hardening.md`](hardening.md) is the
numbered set of settings that bound who can make the engine act at all, and what
each one does and does not claim.
