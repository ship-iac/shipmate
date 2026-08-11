# Getting started

This page wires shipmate into one repository, in four ordered tiers, using AWS as
the worked example. [`concepts.md`](concepts.md) explains what the engine then
does with that wiring.

## Before you start

- **A repository with Terramate stacks, each tagged `env/<name>`.** The tag is
  how a stack declares its environment membership — no environment name ever
  appears in workflow YAML. Tag grammar:
  [`../CONTRACT.md`](../CONTRACT.md) §Tag grammar.

  For an existing repository this is the largest item on the page, not a
  checkbox. `build-matrix` derives environment membership solely from
  `env/<name>` tags, and Terramate tags are otherwise free-form — so a
  repository that predates shipmate is almost certainly using them for
  something else entirely, and **every** stack ends up re-tagged. The work is
  additive, mechanical and reviewable, but it is repo-wide.

  It does not have to land in one commit. `detect` only inspects the stacks a
  run touches: an untagged stack fails the whole run as soon as it is in the
  **changed** set, so re-tagging can follow the stacks you are changing anyway,
  and the failure lists every untagged stack it found for you to work down. The
  nightly drift run is the repo-wide backstop — it inspects every stack, so it
  fails until the last one is tagged.
- **The Terramate and OpenTofu versions this release is tested against.** They
  are in [`../VERSIONS`](../VERSIONS); set them as the repository variables
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
installation token, so without the App it cannot mint one: no gate status and no
apply checks at all, and the run goes red.

Register and install the App **first** — [`github-app.md`](github-app.md) is the
one-time runbook. It is a prerequisite of this tier, not an optional extra.

### Environments for this tier

Every logical environment needs a GitHub Environment pair (`<env>`,
`<env>-apply`), plus the one fixed `shipmate-engine` environment that holds the
App key ([`github-app.md`](github-app.md)). `<env>`/`<env>-apply` are never named
in workflow YAML at all — they're read from Terramate stack tags at runtime. This
tier needs `<env>` and `shipmate-engine`; `<env>-apply` is the apply tier's —
but create it now anyway. `shipmate doctor` runs on every plan run and warns for
each half of a pair that does not exist, so tier 1 with only `<env>` annotates
every pull request with "GitHub Environment `<env>-apply` does not exist" until
the apply tier is done.

Create each `<env>` with
`gh api -X PUT repos/<owner>/<repo>/environments/<name>`, then set protection
rules from Settings → Environments → `<name>` (or the API):

- **`shipmate-engine`** (create once, regardless of how many env tiers you
  run): [`github-app.md`](github-app.md) §5 creates it, and is a prerequisite of
  this tier — use the commands there rather than a bare `PUT`, which leaves the
  environment with no deployment branch policy and so releases the App private
  key to any ref that names it. No reviewers — this environment's job is scoping
  the App key to trusted workflow runs, not gating a human decision, and
  reviewers here would stall every plan and apply run waiting for an approval
  nobody is meant to give.
- **`<env>`** (plan): no reviewers, no deployment branch policy at all —
  reviewers block every plan cell, and a branch policy blocks every plan cell
  whose pull request targets a branch it does not name (plan jobs run at the
  pull request's *base* ref) ([`hardening.md`](hardening.md) #8;
  `shipmate doctor` warns on either). If your `plan.yml` needs plan-time cloud
  credentials — as the fence below does — this is also where its role goes: set
  `AWS_ROLE_ARN` and `AWS_REGION` on each `<env>`, naming a **read-only** plan
  role ([`aws.md`](aws.md) §Environment variables). A plan environment can have
  no protection at all, so anyone who can push a branch can reach whatever it
  names.
- **The variables your layout injects**, on **each** `<env>` and (in the apply
  tier) each `<env>-apply`: `TF_VAR_env` and `TF_VAR_region` where the backend
  path and resources are built from them, `TF_WORKSPACE` for workspace-per-env,
  nothing for folder-per-env, whose leaves fix env and region by path. The plan
  fence below reads `vars.TF_VAR_env` / `vars.TF_VAR_region`; unset, they render
  empty, and an S3 backend `key` built from them collapses to one shared state
  object for every environment.
  [`../CONTRACT.md`](../CONTRACT.md) §Env model is the per-layout table;
  [`concepts.md`](concepts.md) explains where they land.

### The plan workflow

`plan.yml` is three jobs — `detect`, `plan`, `summary` — not a thin wrapper. The
`ref: ${{ github.event.pull_request.head.sha }}` on the two checkouts is
load-bearing: `pull_request_target` runs at the *base* ref, so without it the run
plans the base branch and reports a clean plan for code it never read.

**The filenames are load-bearing too.** `actions/build-matrix` refuses to plan a
repository that has no `.github/workflows/plan.yml`, and the apply path refuses a
plan run whose workflow path is not `plan.yml` — the name is matched literally.
`apply.yml` is the name `actions/dispatch` targets by default, so an apply
wrapper called anything else is dispatched by nothing and `shipmate apply`
silently reaches no workflow. Create both under exactly those names.

The fences on this page are transcribed from the sample repositories, which pin
`runs-on: ubuntu-slim`. Use whichever runner label your own plan offers —
`ubuntu-latest` is the safe default; a label your plan does not provide leaves
every job waiting for a runner that never arrives.

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
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
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

`SHIPMATE_PLAN_PASSPHRASE` is optional — unset, plan artifacts are stored
unencrypted. If you set it, it must be a **repository** secret, not an
environment one, and specifically not on `shipmate-engine`: a plan cell names its
own plan environment, so a passphrase scoped elsewhere resolves to empty at plan
time and every later apply fails its plaintext-artifact check
([`../CONTRACT.md`](../CONTRACT.md) §Plan artifact encryption).

## Required — apply

This tier gets you `shipmate apply` in a pull request comment (a pre-merge apply
of the reviewed plan) and an idempotent post-merge apply on push to the default
branch.

`shipmate apply` runs only for a member of the team named by
`SHIPMATE_APPROVERS_TEAM` (set per repository in
[`github-app.md`](github-app.md) §6), on a pull request that is mergeable and
satisfies the branch ruleset's review policy, and only against a plan for the
pull request's **current** head — the four apply requirements in
[`../CONTRACT.md`](../CONTRACT.md) §Comment-ops
([`concepts.md`](concepts.md) §Comment-ops for the shape). A refused comment
names which requirement failed.

**The two are coupled, and the coupling decides which files you need.** If you
require `shipmate / gate` as a branch-protection check — tier 3 — then applies
have to happen **pre-merge**, because the gate stays non-green while any
`apply / <stack> / <env>` check is pending. A repository that only runs
`deploy.yml` can never green the gate, so the merge that would trigger
`deploy.yml` deadlocks. `deploy.yml` alone is therefore sufficient only for a
repository that does *not* require the gate.

### Environment setup

These are the environment-level settings behind the credential controls
[`hardening.md`](hardening.md) #6–9 describes; the reviewer question below is
the one that page leaves to you. Create each environment with
`gh api -X PUT repos/<owner>/<repo>/environments/<name>`, then set protection
rules from Settings → Environments → `<name>` (or the API):

- **Every `<env>-apply` — deployment branch policy restricted to the default
  branch.** Closes the direct-branch-secret path
  ([`hardening.md`](hardening.md) #17). This is the baseline, and it is not a
  reviewer gate: the secrets are released without a human seeing the
  deployment.
- **Required reviewers and "Prevent self-review" — per environment, your
  call.** With them, an apply to that environment pauses for a named team, and
  that pause is the one gate an App installation token cannot forge, since a
  reviewer decision is a human action a minted token cannot take. Without them
  the tier is self-service and applies proceed unattended. Teams commonly gate
  production and leave dev self-service; the maximally-hardened position gates
  every apply environment. [`hardening.md`](hardening.md) #6 states what each
  choice costs — shipmate does not make it for you.
- **Pair a reviewer-gated environment with `global.shipmate.explicit_envs`.**
  List the bare env name (`prod`, not `prod-apply`) so a bare `shipmate apply`
  skips it and it is only ever reached via the targeted `shipmate apply prod`
  (which then pauses for the environment reviewer).

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
> **`state_suffix` is required but may be `""`.** It is a `required: true` input
> of every apply-path reusable workflow — `apply.yml`, `apply-all.yml`,
> `deploy.yml` and the `apply-env-level.yml` they call — and of none of the plan
> path. `""` — what the fences below paste, because this page's worked example
> is S3 — means a remote backend owns the state, and the engine's state
> restore/save steps are skipped. A **local backend** materialized in the working
> tree passes instead the path segment under each stack directory where its state
> file lives: `repo-example-stacks` passes `.state`, and the engine then restores
> and saves `<stack>/.state` around every apply
> ([`../CONTRACT.md`](../CONTRACT.md) §State backend). Pasting `""` there applies
> against no state at all. Omitting the input entirely is a
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
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}
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
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}
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
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}
    with:
      state_suffix: ""
```

### Why the wrappers name their secrets

Every snippet above passes secrets by name and none uses `secrets: inherit`.
Two reasons, and the second one is a hard failure:

- `inherit` hands the engine every secret your repository can see, not the two
  it uses ([`hardening.md`](hardening.md) §What the engine receives).
- **`inherit` works only within one organization or enterprise.** Called from a
  repository outside the engine's organization it delivers nothing — and it does
  not fall back, it *suppresses*: the callee job binds
  `environment: shipmate-engine`, that environment resolves in **your**
  repository, and its value would have been used, except that `inherit`
  replaced the secrets context wholesale. So "add the environment and keep
  `inherit`" does not work; the App key never arrives, and every App-authored
  surface silently fails to exist — no `shipmate / gate`, no pending
  `apply / <stack> / <env>` checks, no sticky comment.

Pass only what each callee **declares**: `summary.yml` declares
`SHIPMATE_APP_PRIVATE_KEY` alone, while `apply.yml`, `apply-all.yml` and
`deploy.yml` declare the passphrase too. Naming a secret the callee does not
declare is a load-time error that kills the run with no job and no log.

### Consumers outside the engine's organization

Nothing else changes. In particular the key placement does not: it stays a
secret on **your** `shipmate-engine` environment, with a deployment branch
policy naming your default branch ([`github-app.md`](github-app.md) §5), and
it never becomes a repository or organization secret. A called workflow's
`environment:` resolves in the calling repository, so only the workflow *file*
comes from the engine's organization — the credential never leaves yours.

An environment's value also wins over whatever the caller passes, empty
included. A wrapper job that binds no environment therefore passes
`${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}` as an empty string and the mint still
succeeds, which is why the same snippets serve both same-organization and
cross-organization consumers.

`SHIPMATE_PLAN_PASSPHRASE` is the exception, and it is not affected by the
boundary: the wave jobs bind `<env>-apply`, not `shipmate-engine`, so that
secret has no environment to be read from and must travel down the call chain
as a repository secret you pass by name.

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
ref, and it needs the `shipmate-engine` environment from the plan tier. The
workflow and its costs are in [`drift.md`](drift.md).

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
- **Stale bases don't sneak through the merge.** With "require branches up to
  date" (strict), a base moved since the plans ran blocks the auto-merge until
  the branch is updated — and updating re-runs the plan on the new head, which
  resets gate to pending until the fresh plans are applied. The
  exact-plan invariant is preserved.

  Strict gates **merging** and nothing else, so it does not protect the apply:
  a `shipmate apply <env>` from a stale branch applies the plan as reviewed, and
  a stack updated and merged to main since this branch forked is rolled back in
  real infrastructure ([`branch-protection.md`](branch-protection.md)). Update
  the branch *before* commenting `shipmate apply`, not after.
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
