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
  `env/<name>` tags, and Terramate tags are otherwise free-form. So a
  repository that predates shipmate is almost certainly using them for
  something else entirely, and every stack ends up re-tagged. The work is
  additive, mechanical and reviewable, but it is repo-wide.

  It does not have to land in one commit. `detect` only inspects the stacks a
  run touches: an untagged stack fails the whole run as soon as it is in the
  changed set, so re-tagging can follow the stacks you are changing anyway. The
  failure lists every untagged stack it found for you to work down. The
  nightly drift run is the repo-wide backstop — it inspects every stack, so it
  fails until the last one is tagged.
- **The Terramate and OpenTofu versions this release is tested against.** They
  are in [`../VERSIONS`](../VERSIONS), and the workflows below read them from the
  repository variables `TERRAMATE_VERSION` and `TOFU_VERSION`. `scripts/onboard`
  sets both from that file; set them by hand only if you are not running it.
- **`gh` authenticated with admin on the repository.** Every tier creates
  environments, variables or rulesets.
- **Remote state you control, or a local backend materialized in the working
  tree.** AWS S3 is what [`aws.md`](aws.md) covers.

The four tiers are ordered and each depends on the one before. Tier 1 alone is
not a working installation; read tier 2's first paragraphs before deciding to
stop early.

## Required — plan

This tier gets you a plan per changed stack × environment on every pull request,
and — because the engine plan workflow's `summary` job creates them — the pending
`apply / <stack> / <env>` checks and the `shipmate / gate` commit status that
branch protection will require.

**This tier is not usable alone.** The `summary` job mints a GitHub App
installation token, so without the App it cannot mint one: no gate status and no
apply checks at all, and the run goes red.

Register and install the App first. [`github-app.md`](github-app.md) is the
one-time runbook. It is a prerequisite of this tier, not an optional extra.

### Quick path

`scripts/onboard` reconciles this tier and the two below in one command. Run it
from inside the consumer checkout, on its default branch, with `gh` authenticated
against that repository, `terramate` on `PATH`, and an engine checkout sitting on
a `vX.Y.Z` release tag:

```bash
python3 <engine-checkout>/scripts/onboard \
  --team <approvers-team-slug> --app-id <app-id> \
  --key shipmate-app.private-key.pem
```

It writes:

- `shipmate-engine` and, for every environment your stacks' `env/<name>` tags
  declare, an `<env>-plan` / `<env>-apply` pair — each apply environment scoped to
  the default branch, with the App key on `shipmate-engine` and any
  repository-level copy of that key deleted;
- the `SHIPMATE_APP_ID`, `SHIPMATE_APPROVERS_TEAM`, `TERRAMATE_VERSION` and
  `TOFU_VERSION` repository variables;
- a `shipmate-gate` ruleset requiring `shipmate / gate` under the App;
- the six workflow shims under `.github/workflows/`, rendered from this page and
  [`drift.md`](drift.md) and pinned to the engine checkout's release.

It reads before it writes and creates or updates only what differs, so a second
run over a configured repository changes nothing. What it will not touch — a
variable holding another value, an environment carrying a protection it did not
set — it reports as a `differs` line and exits 2
([`troubleshooting.md`](troubleshooting.md) §What `scripts/onboard` reports).
`--dry-run` reports every change and performs no write.

It then prints what it cannot know, because those values are yours: the cloud
role and region, the env identity your layout injects, `SHIPMATE_PLAN_PASSPHRASE`,
`SLACK_WEBHOOK`, adding the repository to the App installation, environment
reviewers, a `CODEOWNERS` entry, and the pull request carrying the six shims.

Branch, commit, push and pull request are yours: the script writes files and
stops. The tier sections below are the spec it implements — read them to know
what you are getting, and to configure a repository by hand instead.

### Environments for this tier

Every logical environment needs a GitHub Environment pair (`<env>-plan`,
`<env>-apply`), plus the one fixed `shipmate-engine` environment that holds the
App key ([`github-app.md`](github-app.md)). Neither half is ever named in
workflow YAML: the logical env comes from Terramate stack tags at runtime, and
the suffix is added where the job binds the environment.

This tier needs `<env>-plan` and `shipmate-engine`. `<env>-apply` is the apply
tier's, but create it now anyway — unless that env shares one environment
(below), where creating both a bare `<env>` and an `<env>-apply` is the
ambiguous naming doctor warns about.

`shipmate doctor` runs on every plan run and warns for each half of a
pair that does not exist, so tier 1 with only `<env>-plan` annotates every pull
request with "GitHub Environment `<env>-apply` does not exist" until the apply
tier is done. (With neither half created you get one warning naming both, and
the shared alternative below.)

**One environment instead of two.** A logical env may share a single bare
`<env>` between plan and apply: create `<env>` alone (no `-plan`, no `-apply`),
list it in the `SHIPMATE_SHARED_ENVS` repository variable (comma-separated, no
spaces). It costs the reviewer gate and the OIDC subject split for that env.
Those are not recoverable without splitting the environment again. Read
[`hardening.md`](hardening.md) §6 and §7–9 for the full price before choosing
it.

The variable is the whole configuration: the engine reads it on both sides — the
apply waves with an `-apply` fallback suffix, the plan and drift cells with
`-plan` — so a repository may share some envs and split others with nothing to
edit in a workflow file ([`../CONTRACT.md`](../CONTRACT.md) §Env model).

Create each environment with
`gh api -X PUT repos/<owner>/<repo>/environments/<name>`, then set protection
rules from Settings → Environments → `<name>` (or the API). `scripts/onboard`
creates all of them, including `shipmate-engine` and its branch policy:

- **`shipmate-engine`** (create once, regardless of how many env tiers you
  run): [`github-app.md`](github-app.md) §5 creates it, and is a prerequisite of
  this tier — use the commands there rather than a bare `PUT`, which leaves the
  environment with no deployment branch policy and so releases the App private
  key to any ref that names it. No reviewers — this environment's job is scoping
  the App key to trusted workflow runs, not gating a human decision. Reviewers
  here would stall every plan and apply run waiting for an approval nobody is
  meant to give.
- **`<env>-plan`** (plan): no reviewers, no deployment branch policy at all —
  reviewers block every plan cell, and a branch policy blocks every plan cell
  whose pull request targets a branch it does not name (plan jobs run at the
  pull request's *base* ref) ([`hardening.md`](hardening.md) #8;
  `shipmate doctor` warns on either). Plan-time cloud credentials go here too:
  set `AWS_ROLE_ARN` and `AWS_REGION` on each `<env>-plan`, naming a read-only
  plan role, and the engine's plan and drift cells assume it
  ([`aws.md`](aws.md) §Environment variables). Unset *here* is not unset:
  `vars` resolve organization → repository → environment, so a plan cell whose
  own `<env>-plan` names no role reads a repository- or organization-level
  `AWS_ROLE_ARN` instead — including one set for the apply path. Only where no
  level sets one does the step skip and the cell hold no cloud credential at
  all. A plan environment can have no protection at all, so anyone who can push
  a branch can reach whatever role a plan cell resolves; what refuses it is that
  role's own trust-policy claim condition
  ([`hardening.md`](hardening.md) §7–9), not where the variable was set.
- **The variables your layout injects.** On each `<env>-plan` and (in the
  apply tier) each `<env>-apply`: `TF_VAR_env` and `TF_VAR_region` where the backend
  path and resources are built from them, `TF_WORKSPACE` for workspace-per-env,
  nothing for folder-per-env, whose leaves fix env and region by path. The
  engine's plan job reads `vars.TF_VAR_env` / `vars.TF_VAR_region` from the
  environment the cell binds. Unset, they render empty, and an S3 backend `key` built from them collapses to one shared state
  object for every environment.
  [`../CONTRACT.md`](../CONTRACT.md) §Env model is the per-layout table;
  [`concepts.md`](concepts.md) explains where they land.

### The plan workflow

`scripts/onboard` writes this file, pinned, from the fence below. `plan.yml` is a
shim: two triggers, a `permissions:` block, and one job that
calls the engine's reusable plan workflow. The four jobs behind it — `facts`,
`detect`, `plan` and `summary` — live in engine `plan.yml`, SHA-pinned, so
none of what they decide is wiring you can get wrong.

The two triggers are `pull_request_target` for the automatic plan on every push
to a pull request, and `workflow_dispatch` for the plan a reviewer asks for by
comment. Neither checks out the pull request's head by default —
`pull_request_target` runs at the *base* ref, a dispatched run at the dispatch
ref — and a dispatched run carries no pull request in its event payload at all.
The engine's `facts` job resolves the pull request once, from the event payload
on a pull-request event and from the API by the dispatched `pr_number`
otherwise, and every job below it plans the head SHA that job reports.

**`name: shipmate` on the calling job is a contract literal, not decoration.**
GitHub names a called workflow's check runs `<caller job> / <callee job>`, so
that name is what makes the plan cells `shipmate / <stack> / <env>` and lets the
plan comment's `[plan]` links resolve to them. Rename the job and the plan still
runs; every one of those links falls back to the workflow-run page instead.
`shipmate doctor` reports it.

**The filenames are load-bearing too.** `actions/build-matrix` refuses to plan a
repository that has no `.github/workflows/plan.yml`. No apply path matches that
path any more (each cell reads its plan run from its own apply check), but
`shipmate doctor` keys its calling-job-name and dispatch-wiring probes on the
filename, so a plan workflow under another name loses them silently and the
refusal is what stops it happening. `actions/dispatch` targets filenames too,
one per verb — `plan.yml` for a commented `shipmate plan`, `apply.yml` for
`shipmate apply`, `unlock.yml` for `shipmate unlock`. A shim called anything
else is dispatched by nothing, and the command silently reaches no workflow.
Create all three under exactly those names.

The engine's jobs run on `ubuntu-latest` unless the shim passes a `runs_on:`
input — the fence below omits it, as `repo-example-stacks-aws` does. Pass it
only for a different label your plan actually offers; one it does not leaves
every job waiting for a runner that never arrives.

```yaml
name: shipmate · plan
on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]
  workflow_dispatch:
    inputs:
      pr_number:
        description: Pull request number to plan
        required: true
concurrency:
  # `github.event.inputs` is readable under either trigger, unlike the `inputs` context.
  group: plan-${{ github.event.pull_request.number || github.event.inputs.pr_number }}
  cancel-in-progress: true
permissions:
  contents: read
jobs:
  # `name: shipmate` is not decoration: GitHub names a called workflow's check runs
  # `<caller job> / <callee job>`, so this is what makes the plan cells
  # `shipmate / <stack> / <env>` — the names the plan comment's links resolve.
  shipmate:
    name: shipmate
    uses: ship-iac/shipmate/.github/workflows/plan.yml@<engine-sha>  # see the latest release
    # Not optional — a callee's permissions are capped by this job's, and granting less kills
    # the run at startup with no job and no log.
    permissions:
      contents: read
      pull-requests: read
      id-token: write
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}
    with:
      # Your flavor's per-stack state path suffix; "" when a remote backend owns state.
      state_suffix: ""
```

**The `permissions:` block on that job is not optional.** A called workflow's
permissions are capped at the `uses:` boundary, and the engine's four jobs
request `contents: read`, `pull-requests: read` and `id-token: write` between
them. Grant less and the run dies at startup — no job, no log, no annotation and
no `shipmate / gate`. That is fail-closed, since nothing merges without the
gate, but nothing on the run page says why, so copy the block whole rather than
trimming it. The top-level `permissions:` block is a separate thing and does not
have to repeat the grant: a job-level block replaces the workflow default rather
than intersecting it, so what the job declares is what the callee is capped at.

`id-token: write` is there for the plan cells' cloud credentials. The engine
runs `aws-actions/configure-aws-credentials` in the `plan` job, gated on
`AWS_ROLE_ARN` — or `AWS_ROLE_ARN_<WORKLOAD>` for a cell carrying a
`workload/<name>` tag — resolving non-empty. It resolves as any `vars` does,
organization → repository → environment: the `<env>-plan` environment is where
the value belongs, not where GitHub stops looking, so a repository- or
organization-level `AWS_ROLE_ARN` set for the apply path is read by every plan
cell too. The step is skipped, and the consumer runs with no cloud credentials,
only where no level sets either variable; where one does, the role's own
trust-policy claim condition is the bound
([`hardening.md`](hardening.md) §7–9). The grant is required either way,
because the job requests it whether or not the step fires.

`state_suffix` is required and may be `""`. It is your flavor's per-stack state
path suffix when a local backend is materialized in the working tree
(`repo-example-stacks` passes `.state`), and `""` when a remote backend owns the
state ([`../CONTRACT.md`](../CONTRACT.md) §State backend) — the same value your
`apply.yml` and `deploy.yml` shims pass. Omitting it entirely is a
workflow-resolution error on purpose.

`SHIPMATE_PLAN_PASSPHRASE` is optional — unset, plan artifacts are stored
unencrypted. If you set it, it must be a repository secret, not an
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
pull request's current head, and only on a pull request that is not a
draft — the five apply requirements in
[`../CONTRACT.md`](../CONTRACT.md) §Comment-ops
([`concepts.md`](concepts.md) §Comment-ops for the shape). A refused comment
names which requirement failed.

**The two are coupled, and the coupling decides which files you need.** If you
require `shipmate / gate` as a branch-protection check — tier 3 — then applies
have to happen pre-merge, because the gate stays non-green while any
`apply / <stack> / <env>` check is pending. A repository that only runs
`deploy.yml` can never green the gate, so the merge that would trigger
`deploy.yml` deadlocks. `deploy.yml` alone is therefore sufficient only for a
repository that does *not* require the gate.

### Environment setup

These are the environment-level settings behind the credential controls
[`hardening.md`](hardening.md) #6–9 describes; the reviewer question below is
the one that page leaves to you. `scripts/onboard` creates each `<env>-apply`
with the deployment branch policy; the reviewer settings are yours, and it lists
the environments awaiting them. Create each environment with
`gh api -X PUT repos/<owner>/<repo>/environments/<name>`, then set protection
rules from Settings → Environments → `<name>` (or the API):

- **Every `<env>-apply` — deployment branch policy restricted to the default
  branch.** Closes the direct-branch-secret path
  ([`hardening.md`](hardening.md) #17). This is the baseline, and it is not a
  reviewer gate: the secrets are released without a human seeing the
  deployment. A shared env has no `<env>-apply`; the policy goes on its bare
  `<env>`, where it is the one control of this set that still applies — and,
  because plan cells run at the pull request's base ref, it also refuses any
  plan cell whose base ref it does not name ([`hardening.md`](hardening.md) #6).
- **Required reviewers and "Prevent self-review" — per environment, your
  call.** This applies to every env that has an `<env>-apply`. A shared env is
  not one of
  them: a reviewer on the bare `<env>` stalls the plan cells and the nightly
  drift run too, so the gate there is unavailable rather than declined, and
  turning it on later means splitting the environment again
  ([`hardening.md`](hardening.md) #6). With them, an apply to that environment
  pauses for a named team, and that pause is the one gate an App installation
  token cannot forge, since a reviewer decision is a human action a minted
  token cannot take. Without them the tier is self-service and applies proceed
  unattended. Teams commonly gate production and leave dev self-service; the
  maximally-hardened position gates every apply environment.
  [`hardening.md`](hardening.md) #6 states what each choice costs — shipmate
  does not make it for you.
- **Pair a reviewer-gated environment with `global.shipmate.explicit_envs`.**
  List the bare env name (`prod` — neither `prod-plan` nor `prod-apply`). A bare
  `shipmate apply` then skips it, and it is only ever reached via the targeted
  `shipmate apply prod`, which pauses for the environment reviewer.

### The apply workflows

`scripts/onboard` writes `apply.yml`, `comment-ops.yml`, `unlock.yml` and
`deploy.yml` from the fences below. Two things bite everyone on this tier.

**`id-token: write` on the calling job of every shim whose callee runs a cell —
including consumers with no cloud credentials at all.** GitHub caps a called
workflow's permissions at each `uses:` boundary, and those workflows request it,
so without the grant the run fails at workflow-resolution time. It applies to
the `apply.yml`, `unlock.yml`, `deploy.yml`, `plan.yml` and `drift.yml` shims —
every one but `comment-ops.yml`, whose callee asks for no such scope. The grant
goes on the calling job and nowhere else: a job-level `permissions:` block
replaces the workflow default rather than intersecting it, so a top-level block
that omits `id-token` takes nothing away.

**`state_suffix` is required but may be `""`.** It is a `required: true` input
of every reusable workflow that runs a cell — `apply.yml`, `apply-all.yml`,
`deploy.yml` and the `apply-env-level.yml` they call, plus `plan.yml` and
`drift.yml`. `unlock.yml` is the exception and declares no such input: it
releases locks and applies nothing, and passing one is a load-time rejection
with no job and no log.

`""` — what the fences below paste, because this page's worked example is S3 —
means a remote backend owns the state, and the engine's state restore/save steps
are skipped. A local backend materialized in the working tree passes instead the
path segment under each stack directory where its state file lives:
`repo-example-stacks` passes `.state`, and the engine then restores and saves
`<stack>/.state` around every apply ([`../CONTRACT.md`](../CONTRACT.md) §State
backend). Pasting `""` there applies against no state at all. Omitting the input
entirely is a workflow-resolution error on purpose: a forgotten state
configuration must fail loud rather than apply with no state at all.

`comment-ops.yml` turns a `shipmate <verb>` pull request comment into an
authorized `workflow_dispatch` — one shim file per verb: `plan.yml` for `plan`,
`apply.yml` for `apply`, `unlock.yml` for `unlock`. The engine picks the file
from the parsed verb; a comment naming a verb whose file the repository does not
carry fails at dispatch time, and the pull request is told only that the dispatch
failed, with a link to the comment-handling run that holds the API's answer.

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
  shipmate:
    name: shipmate
    uses: ship-iac/shipmate/.github/workflows/comment-ops.yml@<engine-sha>  # see the latest release
    permissions:
      contents: read
      issues: write
      pull-requests: write
      actions: read
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
```

The engine's `ops` job behind that shim names `environment: shipmate-engine`.
`shipmate-engine` is the one *literal* environment name that appears in workflow
YAML
([`../CONTRACT.md`](../CONTRACT.md)'s one carve-out to "no env names in workflow
YAML — ever"), because it names a single fixed thing rather than a per-repo
logical environment.

Every one of its appearances is inside an engine reusable workflow, and no
consumer file names it: your shims pass the App key by name and bind no
environment of their own. The `ops` job can declare it because an
`issue_comment` run evaluates at the default branch's tip, which is what the
environment's branch policy admits — the same reason `drift.yml`'s `issues` job
can, on the nightly `schedule`.

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
      ref:
        description: PR head SHA to apply
        required: false
        default: ''
      pr_number:
        description: PR number
        required: false
        default: ''
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
      state_suffix: ""
```

The verb names the file: `shipmate unlock <env>` dispatches `unlock.yml` below,
not this wrapper, so nothing here carries it.

**Every input is `required: false` with an explicit default, and that is
deliberate.** This wrapper is dispatched only by `actions/dispatch`, minting an
App token, from a body the engine builds — no human ever fills a form here, so
`required: true` protects no real caller. What it does do is convert a value the
engine sent empty *on purpose* into a platform-level rejection: GitHub treats
an empty value for a `required: true` `workflow_dispatch` input as "not
provided", and answers HTTP 422 before the workflow starts, naming an input the
operator never typed. That is how every `shipmate unlock` dispatch failed while
this wrapper still declared the plan-run input the engine has since retired:
unlock applies no plan, so the engine sent that value empty.

The engine is the validator, and it is the only layer with enough context to be
one: `apply-detect` runs `validate_head_sha` and `validate_env`, and it knows
which values are legitimately empty. Its errors are annotations on the run
naming the actual value. Keep new inputs on *this* wrapper optional for the same
reason — `required` is the default a new input drifts back to, and it reopens
this exactly.

The plan wrapper's `pr_number` is the one documented `required: true` input, and
it shows what the rule is actually about: that dispatch carries a single input
the engine always fills, so there is no empty value for GitHub to reject, and
requiring it is what makes a hand-dispatched plan name the pull request it
plans. The unlock wrapper below is dispatched the same way and follows the same
rule.

`unlock.yml` is where `shipmate unlock <env>` lands. It calls the engine's
`unlock.yml`, which takes `environment` and `ref` and no secrets — releasing
a lock reads no plan artifact, so there is no passphrase to forward, and mapping
a secret the callee does not declare is a load-time rejection with no job and no
log. A repository without this file keeps every other verb; `shipmate unlock`
alone stops working, and it fails at dispatch time against a workflow file that
is not there.

```yaml
name: shipmate · unlock
on:
  workflow_dispatch:
    inputs:
      environment:
        description: Target environment whose stranded state locks to release
        required: false
        default: ''
      ref:
        description: PR head SHA to check out
        required: false
        default: ''
permissions:
  contents: read
  actions: read
  id-token: write
jobs:
  unlock:
    uses: ship-iac/shipmate/.github/workflows/unlock.yml@<engine-sha>  # see the latest release
    permissions: { contents: read, checks: read, actions: read, id-token: write }
    with:
      environment: ${{ inputs.environment }}
      ref: ${{ inputs.ref }}
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

### Why the shims name their secrets

Every snippet above that passes secrets at all passes them by name, and none uses
`secrets: inherit`.
Two reasons, and the second one is a hard failure:

- `inherit` hands the engine every secret your repository can see, not the two
  it uses ([`hardening.md`](hardening.md) §What the engine receives).
- **`inherit` works only within one organization or enterprise.** Called from a
  repository outside the engine's organization it delivers nothing. It does
  not fall back, it *suppresses*: the callee job binds
  `environment: shipmate-engine`, that environment resolves in your own
  repository, and its value would have been used, except that `inherit`
  replaced the secrets context wholesale. So "add the environment and keep
  `inherit`" does not work; the App key never arrives, and every App-authored
  surface silently fails to exist — no `shipmate / gate`, no pending
  `apply / <stack> / <env>` checks, no sticky comment.

Pass only what each callee declares. `drift.yml` and `comment-ops.yml` declare
`SHIPMATE_APP_PRIVATE_KEY` alone — they mint an App token and read no plan
artifact. `plan.yml`, `apply.yml`, `apply-all.yml` and `deploy.yml` declare
`SHIPMATE_PLAN_PASSPHRASE` too, because each of them writes or reads an
encrypted plan artifact. `unlock.yml` declares neither, so its shim writes no
`secrets:` block at all. Naming a secret the callee does not declare is a
load-time error that kills the run with no job and no log.

### Consumers outside the engine's organization

Nothing else changes. In particular the key placement does not: it stays a
secret on your own `shipmate-engine` environment, with a deployment branch
policy naming your default branch ([`github-app.md`](github-app.md) §5), and
it never becomes a repository or organization secret. A called workflow's
`environment:` resolves in the calling repository, so only the workflow *file*
comes from the engine's organization — the credential never leaves yours.

An environment's value also wins over whatever the caller passes, empty
included. A shim job that binds no environment therefore passes
`${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}` as an empty string and the mint still
succeeds, which is why the same snippets serve both same-organization and
cross-organization consumers.

`SHIPMATE_PLAN_PASSPHRASE` is the exception, and it is not affected by the
boundary. The wave jobs bind the env's apply environment, not `shipmate-engine`,
so that secret has no environment to be read from and must travel down the call
chain as a repository secret you pass by name.

## Required — enforce the gate

Require the status check `shipmate / gate` (verbatim) on the default branch, and
only that check. It is a commit status, not a check-run — a
`required_status_checks` entry matches a commit status by `context` exactly as it
matches a check-run. The ruleset must also pin `integration_id` to the shipmate
App's numeric id (`SHIPMATE_APP_ID`), so that a status of that name posted by any
other identity does not satisfy the rule.

[`branch-protection.md`](branch-protection.md) has the pasteable ruleset, the
gate's state table, and the upgrade notes. Configure it from there.
`scripts/onboard` creates a `shipmate-gate` ruleset carrying that one rule; the
`pull_request`, `non_fast_forward` and `deletion` rules on that page stay a
choice you make, so that a repository already carrying a `pull_request` rule
does not end up with a conflicting second one.

## Optional

### Drift detection

A nightly `drift.yml` plans every stack × environment — or a slice of them —
against real state, then opens, updates and closes drift Issues from what those
cells report. It is one more shim, and the engine jobs behind it that hold a
credential run only at the default-branch ref; it needs the `shipmate-engine`
environment from the plan tier. The workflow and its costs are in [`drift.md`](drift.md).

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

  Strict gates the merge and nothing else, so it does not protect the apply:
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

### Applying chosen environments without an approving review

The branch ruleset's review requirement is repository-wide, so requiring an
approval before merge also requires one before every apply. To keep a low-tier
environment self-service while the rest stay gated, name it in the
`SHIPMATE_UNGATED_ENVS` repository variable — comma-separated bare logical
env names, no spaces:

```
SHIPMATE_UNGATED_ENVS = dev-eu,dev-us
```

Your shims need no line for it. The engine's `ops` job passes
`ungated-envs: ${{ vars.SHIPMATE_UNGATED_ENVS }}` to `actions/comment-ops`, and
`vars` inherit into a called workflow, so the variable resolves in your own
repository. That matters because a composite action cannot read the `vars`
context itself, so the input is comment-ops' only view of the list — and both
engine apply workflows read the same variable directly and enforce on it
themselves. One source, two readers, and nothing between them a consumer can
write differently. Unset the variable and *what applies* is unchanged: every
environment keeps the ruleset's requirement.

The second part is a pin, and your `apply.yml` carries two engine
references — `.github/workflows/apply.yml@` on the targeted job and
`.github/workflows/apply-all.yml@` on the bare one. Both must sit at the same
release as `comment-ops.yml` (or later), because an apply is authorized in one
file and enforced in the other. Bump only `comment-ops.yml` and an unreviewed
apply reaches an engine that enforces nothing: through the stale `apply-all.yml@`
it applies every pending environment with no approving review, and through
the stale `apply.yml@` it applies the named environment unreviewed.

Both edges need the variable set — the exemption is opt-in and there is no
longer any consumer-written input that could authorize a dispatch without it.

What this does and does not do: a listed environment may be applied without an
approving review; every other apply requirement still decides, including
`CHANGES_REQUESTED`, and every unlisted environment keeps the requirement. A
bare `shipmate apply` on an unreviewed pull request applies the listed
environments and holds the rest — their apply checks stay pending, so
`shipmate / gate` stays pending and the merge stays blocked until they are
applied with a review in hand. The variable is editable by anyone with the
Write role. It makes relaxing the gate a deliberate change to repository
settings rather than something a pull request can do to itself, and claims
nothing beyond that. Full semantics in [`../CONTRACT.md`](../CONTRACT.md)
§Comment-ops; the trade-off against environment reviewers is in
[`hardening.md`](hardening.md) §3–5.

### Further hardening

Everything above is the minimum that works. [`hardening.md`](hardening.md) is the
numbered set of settings that bound who can make the engine act at all, and what
each one does and does not claim.
