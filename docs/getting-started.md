# Getting started

This page wires shipmate into one repository, in four ordered tiers, using AWS as
the worked example. [`concepts.md`](concepts.md) explains what the engine then
does with that wiring.

## Before you start

- **A repository with Terramate stacks, every stack shipmate is to run tagged
  `env/<name>`.** The tag is how a stack declares its environment membership —
  no environment name ever appears in workflow YAML. Tag grammar:
  [`../CONTRACT.md`](../CONTRACT.md) §Tag grammar.

  `build-matrix` derives environment membership solely from `env/<name>` tags,
  and Terramate tags are otherwise free-form. So a repository that predates
  shipmate is almost certainly using them for something else entirely.

  A repository may adopt with any number of stacks untagged. Each one is
  unmanaged until a pull request tags it: shipmate never plans or applies it
  ([`../CONTRACT.md`](../CONTRACT.md) §Tag grammar, which also covers taking a
  stack out of CI). `scripts/onboard` provisions every environment the table
  declares and every one a stack tags. So declare an environment in the table
  and run `scripts/onboard` before the first pull request tagging a stack into
  it merges. Otherwise that merge's deploy refuses, because the `<env>-apply` it
  binds does not exist; or, for a `shared: true` entry, it applies in the bare
  `<env>` the pull request's plan auto-created, without the default-branch
  policy `scripts/onboard` sets. Until a stack tags it, `scripts/onboard` names
  it on its `.github/shipmate-config.yml` checklist item, and every whole-tree run (the
  drift sweep, `shipmate unlock`, a bare `shipmate apply`) warns that the table
  declares an environment no stack tags; that warning is expected.
- **Nothing to set for the Terramate and OpenTofu versions.** They are in
  [`../VERSIONS`](../VERSIONS), and the `setup` action installs them from the
  engine commit your workflow file pins. Moving to other versions is a pin bump
  ([`../CONTRACT.md`](../CONTRACT.md) §Consumption), not a repository variable.
- **`gh` authenticated with admin on the repository.** Every tier creates
  environments, variables or rulesets.
- **A GitHub plan that carries the controls you intend to use.** On a private
  repository, environment required reviewers and wait timers need GitHub
  Enterprise, and rulesets and environments need Pro, Team or Enterprise.
  [`hardening.md`](hardening.md) §Plan prerequisites has the table.
- **Remote state you control, or a local backend materialized in the working
  tree.** AWS S3 is what [`aws.md`](aws.md) covers.
- **A `.gitignore` covering what shipmate writes into your working tree:**
  `*.otplan`, `fingerprint.txt`, `planned-head.txt`, `plan.txt`, `plan.json`,
  `cell.json`, `.terraform/`, and a local backend's state path. Left
  untracked, they show up as something to commit, and a `terramate run` of
  your own that omits `--no-recursive` refuses on them (`git-untracked`).
  [`../CONTRACT.md`](../CONTRACT.md) §Terramate safeguards states the rule.
- **Each stack's `.terraform.lock.hcl` committed.** It pins provider versions,
  and without it every cell downloads its providers. Write it with `tofu init -backend=false`,
  then `tofu providers lock -platform=linux_amd64`, in each stack (no backend credentials
  needed). A CLI configuration setting `plugin_cache_may_break_dependency_lock_file = true`
  makes `tofu init` record only your platform's `h1:` hash, which the runner's cache cannot
  use; `tofu providers lock` reads the OpenTofu registry whatever the cache holds. A lock from
  Terraform, or from a provider mirror on another platform, does not serve the cache either.
  [`../CONTRACT.md`](../CONTRACT.md) §Terramate safeguards, "Lock files and the provider
  cache", has the conditions and the reasoning.

The four tiers are ordered and each depends on the one before. Tier 1 alone is
not a working installation; read tier 2's first paragraphs before deciding to
stop early.

### Arriving from another TACO

A repository that already works under another Terraform automation tool arrives
with four things expressed somewhere shipmate does not read. None of them is a
shipmate defect and none of them announces itself, so each is worth a deliberate
pass before the first plan run.

**Ordering.** Wave ordering comes only from the Terramate `after` DAG. If
your ordering lives in the outgoing tool's configuration, it must be ported into
`after`. Nothing will report the omission, because a missing edge is
indistinguishable from a stack that is genuinely independent. Treat the outgoing
tool's config as a *lower bound* on the real graph, not as the graph: one
migration that audited the OpenTofu code instead of porting the config went from
6 declared edges to 105, and from 2 wave levels to 6.

Three detect jobs report the shape as a `::notice::` line — stack count, `after`
edge count, wave levels, and how many stacks would apply concurrently. A reader
who knows the repository can judge that last number immediately; nobody else
can. Exactly three print it: the detect jobs of a dispatched `shipmate apply <env>`,
of a bare `shipmate apply`, and of the post-merge deploy, so it arrives after the
plan review that would have been the place to fix the graph. A plan run does not
print it — seeing no such line there says nothing about the graph. Before that
point the equivalent is `terramate experimental run-graph --label stack.dir` run
locally.

**Tags.** Environment membership is derived from `env/<name>` tags and nothing
else; [Before you start](#before-you-start) covers adopting a repository whose
stacks carry no `env/*` tag yet.

**A named AWS profile in generated HCL.** The apply path holds only the OIDC
session, so a literal `profile` in a `provider` or `backend` block fails there
while still planning fine locally. See [`aws.md`](aws.md).

**`terramate.config.run.env` rewriting `TF_VAR_*`.** Terramate applies `run.env`
after the ambient environment, so an assignment to a name the environment table
resolves for a cell — `TF_VAR_env` and `TF_VAR_region` under the `tf_vars` layout,
`TF_WORKSPACE` under `workspace`, any name in an environment's `tf_vars` — wins
over whatever the cell was given, invisibly, because the fingerprint is computed
outside `terramate run` and so agrees on both sides. Each cell reads those names
back through `terramate run` before `tofu init` and refuses when one comes back
changed or unset. A name the table does not resolve for the cell is yours to
set. [`../CONTRACT.md`](../CONTRACT.md) §Env model has the rule and the
`tm_try` form that keeps a local default.

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

`scripts/onboard` reconciles every tier on this page in one command. Run it
from inside the consumer checkout, with `gh` authenticated against that
repository, `terramate` on `PATH`, and an engine checkout sitting on a `vX.Y.Z`
release tag:

```bash
python <engine-checkout>/scripts/onboard \
  --app-id <app-id> --key shipmate-app.private-key.pem
```

Use `python3` where the platform has no `python` (macOS, Debian and Ubuntu
ship none by default).

`--key` is needed until the App key is on `shipmate-engine`; a later run can
leave it off.

It writes:

- `shipmate-engine` and, for every environment the checkout's
  `.github/shipmate-config.yml` declares and every one your stacks' `env/<name>` tags
  declare, an `<env>-plan` / `<env>-apply` pair — each apply environment scoped to
  the default branch, with the App key on `shipmate-engine` and any
  repository-level copy of that key deleted. An environment whose entry in the
  checkout's `.github/shipmate-config.yml` holds `shared: true` gets one bare `<env>`
  instead, under the same default-branch policy, so set it only where every
  pull request targets the default branch (plan cells for any other base are
  refused; [`hardening.md`](hardening.md) rows 8 and 17, §7–9);
- the `SHIPMATE_APP_ID` repository variable;
- a `shipmate-gate` ruleset requiring `shipmate / gate` under the App, once
  `.github/workflows/shipmate.yml` is on the default branch. Until then it reports
  `deferred gate ruleset`: the first pull request, carrying that file, merges
  normally because no ruleset requires the gate yet. Run the script again after
  merging it to create the ruleset; until you do, `shipmate doctor` reports that
  no active ruleset requires `shipmate / gate`;
- `.github/workflows/shipmate.yml`, rendered from the fence on this page and
  pinned to the engine checkout's release.

It reads before it writes and creates or updates only what differs, so a second
run over a configured repository changes nothing. What it will not touch — an
environment carrying a protection it did not set, a ruleset it did not create —
it reports as a `differs` line and exits 2
([`troubleshooting.md`](troubleshooting.md) §What `scripts/onboard` reports).
Some disagreements are not reported but refused, before the first write and
with exit 1 rather than a `differs` line. A `SHIPMATE_APP_ID` repository variable
that differs from `--app-id` stops the run, because `--app-id` also pins the gate
ruleset to an App and a ruleset pinned to one the workflows do not use blocks the
default branch. So does a run without `--key` while
`shipmate-engine` holds no `SHIPMATE_APP_PRIVATE_KEY`. `--dry-run` reports every change and performs no write.

It then prints a checklist of what it cannot set, because those values are
yours: `SHIPMATE_PLAN_PASSPHRASE`, the table with the
cloud role, region and env identity your layout injects,
adding the repository to the App installation, an approving review before apply,
a `CODEOWNERS` entry, a git-tracked `.terraform.lock.hcl` in each stack, and
the pull request carrying the workflow file. Three items check the whole tree
before a run does: `unmanaged stacks` names the stacks with no `env/*` tag,
`stack tags` the tag refusals a plan would raise and the workload tags that
disagree with the table, and `drift sweeps` the cells no drift file sweeps. It marks
each item `ok`, `todo` or `cannot check` from what the run read, and only a
`todo` prints what to do. Three items can be `cannot check`: the App
installation, which only an App JWT can read; once a `CODEOWNERS` file
exists, whether an entry in it covers `/.github/workflows/`, which is GitHub's
matching; and `drift sweeps`, when a drift file's `tags` query cannot be read,
or a stack carrying two `workload/*` tags or two stack paths that slug alike
leave no whole tree to cover
([`troubleshooting.md`](troubleshooting.md) §What `scripts/onboard` reports).

The checklist reads the working tree: stack tags, `.github/shipmate-config.yml` and
`.github/workflows/`. Re-run `scripts/onboard --app-id <id> --dry-run` after retagging a stack
or adding, editing or deleting a drift file.

Branch, commit, push and pull request are yours: the script writes files and
stops. The tier sections below are the spec it implements — read them to know
what you are getting, and to configure a repository by hand instead.

### Environments for this tier

Every logical environment needs a GitHub Environment pair (`<env>-plan`,
`<env>-apply`), plus the one fixed `shipmate-engine` environment that holds the
App key ([`github-app.md`](github-app.md)).
Neither half of a pair is ever named in workflow YAML: the logical env comes
from Terramate stack tags at runtime, and detect adds the suffix when it stamps
the cell's binding.

This tier needs `<env>-plan` and `shipmate-engine`. `<env>-apply` is the apply
tier's, but create it now anyway — unless that env shares one environment
(below), where nothing binds an `<env>-apply`.

`shipmate doctor` runs on every plan run and warns for each half of a
pair that does not exist, so tier 1 with only `<env>-plan` annotates every pull
request with "GitHub Environment `<env>-apply` does not exist" until the apply
tier is done.

**One environment instead of two.** To share a single bare `<env>` between
plan and apply, set `shared: true` in its `environments.<env>` entry of
`.github/shipmate-config.yml`, create `<env>` alone (no `-plan`, no `-apply`), and
re-run `scripts/onboard` after adding or removing the key: onboard creates the
bare `<env>` only for the entries it reads. It costs that env's reviewer gate
and OIDC subject split until the environment is split again
([`hardening.md`](hardening.md) §6 and §7–9).

The key is the whole configuration: detect reads it from the default branch's
table and stamps the binding on each cell, so a repository may share some envs
and split others with nothing to edit in a workflow file
([`../CONTRACT.md`](../CONTRACT.md) §Env model). The entry merges before the
first plan that needs it, like any other change to the table.

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
  `shipmate doctor` warns on either). Plan-time cloud credentials are not
  configured here: a plan cell's role is the `aws.plan` of the identity the
  environment names in the table ([`aws.md`](aws.md) §"The environment
  table"), and nothing but that table, a literal or a variable reference in it,
  can supply one. A plan
  environment can have no protection at all, so anyone who can push a branch
  can reach whatever role a plan cell resolves; what refuses it is that role's
  own trust-policy claim condition ([`hardening.md`](hardening.md) §7–9).
- **The environment identity your layout injects comes from the environment
  table**, `.github/shipmate-config.yml` on your repository's default branch
  ([`../CONTRACT.md`](../CONTRACT.md) §Environment table). It is required: a
  repository without one has nothing for its cells to run as, and every run
  refuses. `layout` is the discriminator — `tf_vars` derives `TF_VAR_env` and
  `TF_VAR_region` from each environment's key and its region, `workspace`
  derives `TF_WORKSPACE`, and `folder` derives nothing, its leaves fixing env
  and region by path. `scripts/env-inject` is the cell's one writer of the job
  environment: it writes what the table resolved under the names OpenTofu reads,
  and composes your own variables and secrets (§Variables and secrets your stacks
  need) into the same write, refusing any name two channels supply.
  [`../CONTRACT.md`](../CONTRACT.md) §Env model is the per-layout table;
  [`concepts.md`](concepts.md) explains where they land.

  ```yaml
  # .github/shipmate-config.yml
  layout: tf_vars

  identities:
    dev:
      aws:
        plan: arn:aws:iam::9817:role/shipmate-plan
        apply: arn:aws:iam::9817:role/shipmate-apply

  environments:
    dev-eu:
      region: eu-west-1
      identity: dev
  ```

  Give the plan and apply paths separate roles: the plan path is reachable from
  any branch ([`hardening.md`](hardening.md) §7–9).
  [`../CONTRACT.md`](../CONTRACT.md) §Environment table has the schema,
  including identities, per-workload roles, `workloads` and the order in which
  to add one, and [`aws.md`](aws.md) §"The environment table" has a
  per-workload example.

  The OIDC subject names only the environment (`environment:<env>-apply`, or
  the bare `<env>` when shared), never the workload, so every workload role
  whose trust policy accepts that subject is reachable from every apply cell of
  that environment, and for a shared environment from every plan cell too.
  Choose how finely to split environments before writing those trust policies
  ([`hardening.md`](hardening.md) §7–9).

  An environment applies without an approving review through `gated: false`
  on its own `environments.<name>` entry (§"Applying chosen environments
  without an approving review").

  **A value can come from a GitHub variable.** Write `{vars: NAME}` in
  place of any string, list items included
  (`apply: {vars: PROD_APPLY_ROLE}`), and define the name as a
  repository or organization variable. A resolved value is printed in job
  outputs and logs, so reference variables only, never secrets. Never define it
  on `shipmate-engine`: comment-ops and the plan summary bind that Environment,
  so a variable of the same name there shadows the repository value in those
  two jobs only. Run `shipmate doctor` on the pull request that adds a
  reference. [`../CONTRACT.md`](../CONTRACT.md) §Variable references has the
  scope, the shadowing rule and what a changed value does to a pending apply.

  **The table has to be on the default branch before your first plan run.** The
  engine reads it from the default branch, so a pull request that only adds the
  table is refused by the branch it is compared against. Put it in the same
  pull request as the workflow file. That pull request must change no stack:
  its merge pushes to the default branch and runs `deploy`, and a changed stack
  there has no plan run to apply from, so that deploy fails. The pull request
  merges normally, because no ruleset requires `shipmate / gate`
  yet. Re-run `scripts/onboard` after merging to create the gate ruleset; until
  then `shipmate doctor` reports that no active ruleset requires the gate.

### The workflow file

A repository holds `shipmate.yml` and one drift file per sweep, from the two
fences below. `scripts/onboard` writes `shipmate.yml`, pinned; you save each
drift file yourself, under any name in `.github/workflows/`, and pin it.
`shipmate.yml` is a shim: four triggers and five jobs, each job calling an
engine reusable workflow SHA-pinned. A drift file is one job calling engine
`drift.yml` on its cron or a dispatch. The jobs behind those calls — `facts`, `detect`, `plan` and
`summary` in engine `plan.yml`, and their equivalents on the other paths — live
in the engine, so none of what they decide is wiring you can get
wrong.

`shipmate.yml` and every drift file go in at tier 1, and three kinds of job are
this tier's: `plan`, `comment-ops` and each drift file's `drift`, which need
`<env>-plan`, `shipmate-engine` and the App key and nothing else. `deploy` runs from the
start too: on every push to the default branch it applies the merged pull request's cells still
pending, in the `<env>-apply` this tier has you create (a shared env's bare
`<env>`). The other two, `apply` and `unlock`, wait for the
environments and secrets the apply tier creates.

The plan triggers are `pull_request_target` for the automatic plan on every push
to a pull request, and `workflow_dispatch` with `verb: plan` for the plan a
reviewer asks for by comment. Neither checks out the pull request's head by
default — `pull_request_target` runs at the *base* ref, a dispatched run at the
dispatch ref — and a dispatched run carries no pull request in its event payload
at all. The engine's `facts` job resolves the pull request once, from the event
payload on a pull-request event and from the API by the dispatched `pr_number`
otherwise, and every job below it plans the head SHA that job reports.

A draft pull request's autoplan plans nothing, and `shipmate / gate` goes
pending with the draft reason. Mark the pull request ready, or comment
`shipmate plan`, which plans a draft; applies still refuse a draft.

**`name: shipmate` on a calling job is a contract literal, not decoration.**
GitHub names a called workflow's check runs `<caller job> / <callee job>`, so
that name is what makes the plan cells `shipmate / <stack> / <env>` and lets the
plan comment's `plan` links resolve to them. The `plan` and `comment-ops` jobs
and each drift file's `drift` job carry it. Rename the `plan` job and its run
still happens, but every one of those links falls back to the workflow-run page
instead; `shipmate doctor` reports it. Renaming `comment-ops` or `drift` changes
only their own check-run names: no link resolves through them, and doctor does
not check them.

**The filename is load-bearing too.** `actions/build-matrix` refuses to plan a
repository that has no `.github/workflows/shipmate.yml`; `actions/dispatch`
dispatches that one filename for every verb, choosing the job by the `verb`
input it sends; and `shipmate doctor` keys its `shipmate.yml` probe, which checks
the job name, dispatch wiring, event routing and a drift job, on it. A
file under another name is reached by nothing. A drift file's name matters to
nothing: nothing dispatches it, and doctor finds a drift file by its call of
`drift.yml`.

Which trigger reaches which job, and which engine workflow it calls:

| Trigger | Job | Engine reusable workflow |
| --- | --- | --- |
| `pull_request_target`, or `verb: plan` | `plan` | `plan.yml` |
| `issue_comment` | `comment-ops` | `comment-ops.yml` |
| `push` to the default branch | `deploy` | `deploy.yml` |
| `verb: apply` | `apply` | `apply.yml` |
| `verb: unlock` | `unlock` | `unlock.yml` |

Only the `plan` job and each drift file's `drift` job accept a `runs_on:`
input. Behind every other call the engine's detect jobs and cells run on
`ubuntu-latest` and its control jobs on `ubuntu-slim` ([`aws.md`](aws.md)
§Runner choice). The fences below omit it, as `repo-example-stacks-aws` does, so `plan` and `drift` run on
`ubuntu-latest` too. Pass it only for a different label your plan actually
offers; one it does not leaves every job of that call waiting for a runner that
never arrives.

```yaml
name: shipmate
run-name: >-
  shipmate · ${{ github.event_name == 'pull_request_target' && 'plan'
  || github.event_name == 'issue_comment' && 'comment'
  || github.event_name == 'push' && 'deploy'
  || inputs.verb }}
on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]
  issue_comment:
    types: [created]
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      # Every input is optional except the verb, and that is deliberate: one schema serves
      # three verbs, and GitHub reads an empty value for a `required: true` input as not
      # provided and answers HTTP 422 before the run starts. The engine validates instead —
      # `pr-facts` refuses a plan with no number, `apply-detect` an apply with no ref.
      verb:
        description: What to run (plan, apply or unlock)
        type: choice
        options: [plan, apply, unlock]
        required: true
      environment:
        description: Target environment (apply and unlock; empty apply = every non-explicit environment)
        required: false
        default: ''
      ref:
        description: PR head SHA (apply and unlock)
        required: false
        default: ''
      pr_number:
        description: Pull request number (plan and apply)
        required: false
        default: ''
# Floor, not a default to inherit: every job below declares its own block, so a job that
# loses one gets nothing rather than everything the file granted.
permissions: {}
jobs:
  # `name: shipmate` is not decoration: GitHub names a called workflow's check runs
  # `<caller job> / <callee job>`, so this is what makes the plan cells
  # `shipmate / <stack> / <env>` — the names the plan comment's links resolve.
  plan:
    name: shipmate
    # `github.event.inputs` is the form readable under either trigger, unlike the `inputs`
    # context, and `plan` also runs under one that is not `workflow_dispatch`; the
    # concurrency group below relies on the same thing. `apply` and `unlock` are
    # dispatch-only and keep `inputs.`, because only that form applies a declared default,
    # which is what makes an omitted `environment` key read as the empty string.
    if: github.event_name == 'pull_request_target' || (github.event_name == 'workflow_dispatch' && github.event.inputs.verb == 'plan')
    concurrency:
      # `github.event.inputs` is readable under either trigger, unlike the `inputs` context.
      group: plan-${{ github.event.pull_request.number || github.event.inputs.pr_number }}
      cancel-in-progress: true
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
      # Not a no-op when you hold no repository secret of that name: the envelope normally
      # lives on `<env>-plan` / `<env>-apply`, so this expression resolves empty and the
      # mapping is what makes the environment's value reachable. Delete it and nothing arrives.
      SHIPMATE_SECRETS: ${{ secrets.SHIPMATE_SECRETS }}
  comment-ops:
    name: shipmate
    # `issue_comment` fires on issues too; the engine's own `ops` job carries that filter, so
    # this one only has to select the event.
    if: github.event_name == 'issue_comment'
    uses: ship-iac/shipmate/.github/workflows/comment-ops.yml@<engine-sha>  # see the latest release
    permissions:
      contents: read
      issues: write
      pull-requests: write
      actions: read
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
  deploy:
    # Display only: the run's job path reads
    # `shipmate / post-merge / L<n> / apply / <stack> / <env>`.
    name: post-merge
    if: github.event_name == 'push'
    concurrency:
      group: deploy-main
      cancel-in-progress: false
    uses: ship-iac/shipmate/.github/workflows/deploy.yml@<engine-sha>  # see the latest release
    permissions: { contents: read, checks: read, pull-requests: read, actions: read, id-token: write }
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}
      SHIPMATE_SECRETS: ${{ secrets.SHIPMATE_SECRETS }}
  apply:
    if: github.event_name == 'workflow_dispatch' && inputs.verb == 'apply'
    uses: ship-iac/shipmate/.github/workflows/apply.yml@<engine-sha>  # see the latest release
    permissions: { contents: read, checks: read, actions: read, id-token: write }
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}
      SHIPMATE_SECRETS: ${{ secrets.SHIPMATE_SECRETS }}
    with:
      environment: ${{ inputs.environment }}
      ref: ${{ inputs.ref }}
      pr_number: ${{ inputs.pr_number }}
  unlock:
    if: github.event_name == 'workflow_dispatch' && inputs.verb == 'unlock'
    uses: ship-iac/shipmate/.github/workflows/unlock.yml@<engine-sha>  # see the latest release
    permissions: { contents: read, checks: read, actions: read, id-token: write }
    secrets:
      SHIPMATE_SECRETS: ${{ secrets.SHIPMATE_SECRETS }}
    with:
      environment: ${{ inputs.environment }}
      ref: ${{ inputs.ref }}
```

On a repository whose default branch is not `main`, change `branches: [main]`
to that branch; `scripts/onboard` writes the file that way.

Save the drift sweep from this second fence under any file name in
`.github/workflows/`, with `@<engine-sha>  # see the latest release` replaced by
`@<sha> # vX.Y.Z`, as `shipmate.yml`'s pin reads. Each sweep is its own file;
[`drift.md`](drift.md) §Scoping a sweep covers splitting the cells across
several:

```yaml
name: shipmate drift
on:
  schedule:
    - cron: "17 3 * * *"   # nightly, off-peak
  workflow_dispatch:
permissions: {}
jobs:
  drift:
    name: shipmate
    uses: ship-iac/shipmate/.github/workflows/drift.yml@<engine-sha>  # see the latest release
    permissions:
      contents: read
      id-token: write
      actions: read
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
      SHIPMATE_SECRETS: ${{ secrets.SHIPMATE_SECRETS }}
```

**The `permissions:` block on each calling job is not optional.** A called
workflow's permissions are capped at the `uses:` boundary, so each block above
has to grant every scope the callee's own jobs request. Grant less and the run
dies at startup — no job, no log, no annotation and no `shipmate / gate`. That
is fail-closed, since nothing merges without the gate, but nothing on the run
page says why. Copy each block whole rather than trimming it.

The top-level `permissions: {}` is a floor, not a grant the jobs inherit. A
job-level block replaces the workflow default rather than intersecting it, so
what a job declares is what its callee is capped at, and a job that loses its
block gets nothing.

**`verb` is the one required input, and every other is optional with an explicit
default.** One schema serves three verbs, and GitHub reads an empty value for a
`required: true` input as not provided, answering HTTP 422 before the run starts
— so requiring `pr_number` would refuse every `unlock`, whose dispatch body does
not carry one. No human
fills a form here either — `actions/dispatch` mints an App token and sends a body
the engine builds — so `required: true` protects no real caller.

The engine is the validator, and it is the only layer with enough context to be
one: `actions/pr-facts` refuses a dispatched plan with no number, naming it, and
`apply-detect` and `unlock-detect` run `validate_head_sha` and `validate_env`. It
knows which values are legitimately empty, and its errors are annotations on the
run naming the actual value. Keep new inputs optional for the same reason — `required` is the
default a new input drifts back to, and it reopens this exactly.

`id-token: write` is there for the cells' cloud credentials, on every job whose
callee runs one — that is every job but `comment-ops`, whose callee asks for no
such scope, and it applies to consumers with no cloud credentials at all. The
engine runs `aws-actions/configure-aws-credentials` in the `plan` job, gated on
a role resolving non-empty. That role is the `aws.plan` of the identity the
environment names in the table, read from the default branch, and nothing but
that table, a literal or a variable reference in it, can supply one. The step is
skipped, and the consumer runs with no cloud credentials, wherever the cell
resolves no role;
where it resolves one, the role's own trust-policy claim condition is the bound
([`hardening.md`](hardening.md) §7–9). The grant is required either way,
because the job requests it whether or not the step fires.

**The state path is read from `tofu init`, not configured.** A local backend's
state is cached at the path `tofu init` records for each stack, and restored
before every plan and saved after every apply; a remote backend such as this
page's S3 example owns its state, and the engine's state steps are skipped
([`../CONTRACT.md`](../CONTRACT.md) §State backend).

`SHIPMATE_PLAN_PASSPHRASE` is optional — unset, plan artifacts are stored
unencrypted. If you set it, set it as a repository or organization secret, or
as the same value on both `<env>-plan` and `<env>-apply`. An environment secret
reaches only a job that binds that environment, and a plan cell binds its own
plan environment, so these placements fail
([`../CONTRACT.md`](../CONTRACT.md) §Plan artifact encryption):

- As a variable, every cell refuses it by name.
- As a secret on `shipmate-engine`, it reaches no cell: no plan or apply cell
  binds that environment, so plans upload unencrypted with no message.
- As a secret on `<env>-apply` alone, plans upload unencrypted and every apply
  in that environment refuses its plaintext-artifact check.

## Required — apply

This tier gets you `shipmate apply` and `shipmate unlock` in a pull request
comment (a pre-merge apply of the reviewed plan, through the `apply`
job, and a lock release through `unlock`), and the environment protection that
also governs the idempotent post-merge apply the tier-1 `deploy` job runs on
push to the default branch.

`shipmate apply` runs only for a commenter with write or admin permission on the
repository, on a pull request that is mergeable and satisfies the branch
ruleset's review policy, and only against a plan for the pull request's current
head, and only on a pull request that is not a draft — the five apply
requirements in
[`../CONTRACT.md`](../CONTRACT.md) §Comment-ops
([`concepts.md`](concepts.md) §Comment-ops for the shape). A refused comment
names which requirement failed.

**The two are coupled.** If you require `shipmate / gate` as a
branch-protection check — tier 3 — then applies have to happen pre-merge, because
the gate stays non-green while any `apply / <stack> / <env>` check is pending. A
repository that only ever reaches the `deploy` job can never green the gate, so
the merge that would trigger it deadlocks. Post-merge deploys alone are
therefore sufficient only for a repository that does *not* require the gate.

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
  them: a reviewer on the bare `<env>` stalls the plan cells and every
  drift sweep covering it too, so the gate there is unavailable rather than declined, and
  turning it on later means splitting the environment again
  ([`hardening.md`](hardening.md) #6). With them, an apply to that environment
  pauses for a named team, and that pause is the one gate an App installation
  token cannot forge, since a reviewer decision is a human action a minted
  token cannot take. Without them the tier is self-service and applies proceed
  unattended. On a private repository below Enterprise, GitHub refuses required
  reviewers and wait timers, so the apply gate is then an approving-review
  `pull_request` rule on the default branch, which `scripts/onboard` does not
  create ([`branch-protection.md`](branch-protection.md) §Reproducible
  ruleset), and the set of people with write access. Teams commonly gate
  production and leave dev self-service; the maximally-hardened position gates
  every apply environment where the plan allows it.
  [`hardening.md`](hardening.md) #6 states what each choice costs — shipmate
  does not make it for you.
- **Pair a reviewer-gated environment with `explicit: true` in
  `.github/shipmate-config.yml`.**
  Set it on the bare env's entry (`environments.prod` — neither `prod-plan`
  nor `prod-apply`). A bare `shipmate apply` then skips it. While
  `shipmate / gate` is a required check, it is only ever reached via the
  targeted `shipmate apply prod`, which pauses for the environment reviewer;
  without that check, a cell left pending at merge applies post-merge, because
  the `deploy` job holds no environment back.

### The apply jobs

This tier adds no file. The `apply` and `unlock` jobs are already in
the `shipmate.yml` published above (§[The workflow file](#the-workflow-file));
what this tier does is create the environments and secrets they need. The
`comment-ops` job that dispatches them and the `deploy` job are tier 1's;
`comment-ops` is described here because this is where its verbs land.

The `comment-ops` job turns a `shipmate <verb>` pull request comment into an
authorized `workflow_dispatch` of `shipmate.yml` itself, carrying the parsed verb
as the `verb` input. The `if:` on that job selects the `issue_comment` event and
nothing else: `issue_comment` fires on issues too, and the engine's own `ops` job
carries that filter, so a consumer cannot drop it. The engine `ops` job's
`if:` also skips a comment by a bot or one without `shipmate` in it.

The engine's `ops` job behind that call names `environment: shipmate-engine`.
`shipmate-engine` is the one *literal* environment name that appears in workflow
YAML
([`../CONTRACT.md`](../CONTRACT.md)'s one carve-out to "no env names in workflow
YAML — ever"), because it names a single fixed thing rather than a per-repo
logical environment.

Every one of its appearances is inside an engine reusable workflow, and no
consumer file names it: your jobs pass the App key by name and bind no
environment of their own. The `ops` job can declare it because an
`issue_comment` run evaluates at the default branch's tip, which is what the
environment's branch policy admits — the same reason engine `drift.yml`'s
`issues` job can, on a drift file's `schedule` or a `workflow_dispatch` at the
default branch.

`shipmate apply` lands on the `apply` job, which calls the engine's `apply.yml`
with the dispatched `environment`: a targeted `shipmate apply <env>` sends one
and applies that environment alone; a bare `shipmate apply` sends none and
applies every pending environment in `needs` order. The job reads
`inputs.environment` rather than `github.event.inputs.environment`, because only
the `inputs` context applies the declared default, which is what makes an
omitted key read as the empty string.

`shipmate unlock <env>` lands on the `unlock` job. It calls the engine's
`unlock.yml`, which takes `environment`, `ref` and `SHIPMATE_SECRETS` — releasing
a lock reads no plan artifact and mints no App token, so it declares neither
engine secret, and mapping a secret the callee does not declare is a load-time
rejection with no job and no log. That is why the `unlock` job is the one job of
the file whose `secrets:` block names no engine credential.

The `deploy` job applies, on push to the default branch, every reviewed plan
whose apply check is still pending — so it no-ops when everything was applied
pre-merge. Its `name: post-merge` is display only: the run's job path reads
`shipmate / post-merge / L<n> / apply / <stack> / <env>`.

### Why the jobs name their secrets

Every snippet above that passes secrets at all passes them by name, and none uses
`secrets: inherit`.
Two reasons, and the second one is a hard failure:

- `inherit` hands the engine every secret your repository can see, not the three
  it names ([`hardening.md`](hardening.md) §What the engine receives).
- **`inherit` works only within one organization or enterprise.** Called from a
  repository outside the engine's organization it delivers nothing. It does
  not fall back, it *suppresses*: the callee job binds
  `environment: shipmate-engine`, that environment resolves in your own
  repository, and its value would have been used, except that `inherit`
  replaced the secrets context wholesale. So "add the environment and keep
  `inherit`" does not work; the App key never arrives, and every App-authored
  surface silently fails to exist — no `shipmate / gate`, no pending
  `apply / <stack> / <env>` checks, no sticky comment.

Pass only what each callee declares. `comment-ops.yml` declares
`SHIPMATE_APP_PRIVATE_KEY` alone — it mints an App token, reads no plan
artifact, and runs no cell. `plan.yml`, `apply.yml` and
`deploy.yml` declare `SHIPMATE_PLAN_PASSPHRASE` too, because each of them writes
or reads an encrypted plan artifact. Every callee that runs a cell —
`plan.yml`, `drift.yml`, `apply.yml`, `deploy.yml` and `unlock.yml` — also declares
`SHIPMATE_SECRETS`, which is why `unlock.yml` declares neither engine secret and
still takes a `secrets:` block.
Naming a secret the callee does not declare is a load-time error that kills the
run with no job and no log.

### Consumers outside the engine's organization

Register and install your own App in your own organization
([`github-app.md`](github-app.md) steps 1–4). Nothing else changes. In
particular the key placement does not: it stays a secret on your own
`shipmate-engine` environment, with a deployment branch policy naming your
default branch ([`github-app.md`](github-app.md) §5), and it never becomes a
repository or organization secret. A called workflow's `environment:` resolves
in the calling repository, so only the workflow *file* comes from the engine's
organization — the credential never leaves yours.

An environment's value also wins over whatever the caller passes, empty
included. A calling job that binds no environment therefore passes
`${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}` as an empty string and the mint still
succeeds, which is why the same fence serves both same-organization and
cross-organization consumers.

`SHIPMATE_PLAN_PASSPHRASE` is the exception, and it is not affected by the
boundary. The wave jobs bind the env's apply environment, not `shipmate-engine`,
so `shipmate-engine` cannot supply that secret: it travels down the call chain as
a repository or organization secret you pass by name, or is set as the same
value on both `<env>-plan` and `<env>-apply`.

## Required — enforce the gate

Require the status check `shipmate / gate` (verbatim) on the default branch, and
only that check. It is a commit status, not a check-run — a
`required_status_checks` entry matches a commit status by `context` exactly as it
matches a check-run. The ruleset must also pin `integration_id` to the shipmate
App's numeric id (`SHIPMATE_APP_ID`), so that a status of that name posted by any
other identity does not satisfy the rule.

[`branch-protection.md`](branch-protection.md) has the pasteable ruleset and the
gate's state table. Configure it from there, after the pull request adding
`.github/workflows/shipmate.yml` merges: a ruleset created earlier blocks that
pull request, which cannot produce the gate.
`scripts/onboard` creates a `shipmate-gate` ruleset carrying that one rule; the
`pull_request`, `non_fast_forward` and `deletion` rules on that page stay a
choice you make, so that a repository already carrying a `pull_request` rule
does not end up with a conflicting second one. To add them, follow
[`branch-protection.md`](branch-protection.md) §Reproducible ruleset, and leave
the `pull_request` rule out of the body when another ruleset already carries
one.

## Optional

### Variables and secrets your stacks need

Anything a stack needs that the repository does not hold — a provider endpoint,
an API key — reaches a cell through one of two channels: ordinary GitHub
variables, and a `SHIPMATE_SECRETS` envelope. Both are optional; a repository
that needs neither sets nothing.

**Keep configuration in Git.** Native `.tfvars` and Terramate-generated
configuration stay the first option for endpoints, sizes and resource settings.
These channels exist for inputs that genuinely come from outside the repository
— credentials, and values the repository should not hold. Do not re-create your
stacks' configuration as GitHub variables. A value in `.github/shipmate-config.yml`
can name a variable instead ([`../CONTRACT.md`](../CONTRACT.md) §Variable
references).

What carries what:

| carries | mechanism | your effort |
| --- | --- | --- |
| provider environment variables (`CONFLUENT_CLOUD_ENDPOINT`, `DATADOG_SITE`) | a plain GitHub variable, exported under its stored name | set the variable |
| `TF_VAR_*` for a conventional lower/snake_case OpenTofu name | a plain GitHub variable, suffix lowercased on export | set the variable |
| `TF_VAR_*` for an upper- or mixed-case OpenTofu name | `SHIPMATE_VARS`, a JSON envelope whose keys keep their case | write one JSON object |
| secrets, of any name shape | `SHIPMATE_SECRETS`, the same envelope shape held in a secret | write one JSON object |

**`TF_VAR_*` suffixes are lowercased on export**, which is surprising and worth
one paragraph. GitHub uppercases a variable name when it stores it — whichever
case you typed, through the API as through `gh` — and OpenTofu matches
`TF_VAR_<name>` case-sensitively on Linux. Conventional OpenTofu variable names
are lowercase, so the cell lowercases the suffix: `TF_VAR_ENDPOINT` →
`TF_VAR_endpoint` → `variable "endpoint"`, and `TF_VAR_MY_THING` reaches
`variable "my_thing"`. A variable declared with uppercase or mixed-case letters
— `variable "ENDPOINT"`, `variable "myThing"` — is reachable only through
`SHIPMATE_VARS`, whose JSON keys are exported exactly as written.

**`SHIPMATE_VARS` costs no workflow edit.** It is a GitHub variable, so it
arrives like any other one — nothing to declare, nothing to map. Only
`SHIPMATE_SECRETS` touches `shipmate.yml`, because only secrets cross the
declaration boundary; the four cell-running jobs of `shipmate.yml` and each
drift file's `drift` job already carry its line, and the comment on the `plan`
job's line says why deleting it breaks the channel — the others carry the same
line without a comment.

**One is a variable and one is a secret, and swapping them fails.** Setting
`SHIPMATE_SECRETS` as a variable is refused by name, because as a variable its
value is readable by anyone who can see the repository and nothing in it reaches
a cell; the run fails telling you to rotate what it held. A
`SHIPMATE_APP_PRIVATE_KEY` or `SHIPMATE_PLAN_PASSPHRASE` variable is refused too,
each with its own message: rotate the key and set it as a secret on
`shipmate-engine`; choose a new passphrase and set it as a repository or
organization secret. The other direction cannot be
caught: `SHIPMATE_VARS` set as a secret is never read — nothing maps it into a
cell — so the keys simply never appear, with no error anywhere.

**Set shared values once.** A repository-level variable or secret serves both
tiers, and an organization-level one serves every repository — except that on
GitHub Free, organization **variables** do not reach a **private** repository,
so a consumer on Free with a private IaC repository holds none of the variable
side at the organization tier. Add an environment-level value only where one
genuinely differs, and reserve the `<env>-plan` / `<env>-apply` split for read
and write credentials.

**Envelopes replace, they do not merge.** An environment-level
`SHIPMATE_SECRETS` replaces the repository-level one whole, and so does an
environment-level `SHIPMATE_VARS`. There is no key-by-key merge across tiers, so
an environment envelope must carry every key that environment needs, not only
the ones that differ. Repository secret `SHIPMATE_SECRETS`:

```json
{"CONFLUENT_CLOUD_API_KEY": "read-key", "DATADOG_API_KEY": "dd-key"}
```

and the same secret on the `prod-apply` environment, meaning to swap in the
write key:

```json
{"CONFLUENT_CLOUD_API_KEY": "write-key"}
```

Cells on `prod-apply` then see no `DATADOG_API_KEY` at all: the repository
envelope is not consulted, not merged. The environment value has to name both
keys.

[`../CONTRACT.md`](../CONTRACT.md) §Consumer variables and secrets is the full
policy — the names shipmate reserves, which environment supplies which key, what
a value that differs between the two tiers does to the apply-match fingerprint,
and what masking does and does not cover.

### Drift detection

Each drift file's `drift` job plans every stack × environment, or the cells its
`tags` query selects, on its schedule or a `workflow_dispatch`, against real
state, then opens, updates and closes drift Issues from what those cells
report. The engine jobs behind it that hold a credential run only at the
default-branch ref; it needs the `shipmate-engine` environment from the plan
tier. `scripts/onboard` does not write it: save it from the `shipmate drift`
fence in §The workflow file. Name, edit and split drift files freely — a `tags`
query, other crons. When no workflow file calls `drift.yml`, `shipmate doctor`
reports a notice. `scripts/onboard --app-id <id> --dry-run` reads the drift files in the
working tree and names the cells none of them sweeps, a query or clause matching
no cell, and a sweep above the 256-cell limit; re-run it after adding, editing or
deleting a drift file. Scoping a sweep and what it costs are in
[`drift.md`](drift.md).

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

- **Explicit environments still gate.** An environment whose entry holds
  `explicit: true` is skipped by the bare `shipmate apply` and its apply checks
  stay pending — gate stays pending, so auto-merge waits until someone runs the
  targeted `shipmate apply <env>`. Arming auto-merge never weakens the
  apply-before-merge guarantee; it only removes the final click.
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
  the `deploy` job normally — which no-ops idempotently when everything was
  applied pre-merge.
- **Any merge method works.** Squash merges are fine: `deploy-detect` maps the
  merge commit back to the PR head SHA via the commit→PR association, not the
  commit graph.

### Applying chosen environments without an approving review

The branch ruleset's review requirement is repository-wide, so requiring an
approval before merge also requires one before every apply. To keep a low-tier
environment self-service while the rest stay gated, set `gated: false` on its
entry in `.github/shipmate-config.yml`:

```yaml
# .github/shipmate-config.yml
layout: tf_vars

environments:
  dev-eu:
    region: eu-west-1
    gated: false
```

Your workflow file needs no line for it, and neither does a repository setting.
Comment-ops and both apply forms each resolve the flag themselves, from the file
on your **default branch** — so an edit takes effect when it merges, and a pull
request cannot exempt itself. Set it on no entry and every environment keeps
the ruleset's requirement.

The `comment-ops` and `apply` jobs must pin one engine commit:
`comment-ops.yml` authorizes an apply that `apply.yml`
enforces, so at different commits an apply authorized under one engine's rule is
enforced by another's, or by none. [`releasing.md`](releasing.md) § Re-pin a consumer
moves every pin together.

What the exemption does and does not cover, including a bare `shipmate apply`
on an unreviewed pull request, is in [`../CONTRACT.md`](../CONTRACT.md)
§Comment-ops; how it compares with an environment's `required_reviewers` is in
[`hardening.md`](hardening.md) §3–5.

### Further hardening

Everything above is the minimum that works. [`hardening.md`](hardening.md) is the
numbered set of settings that bound who can make the engine act at all, and what
each one does and does not claim.
