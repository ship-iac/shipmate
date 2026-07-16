# shipmate

shipmate is a set of GitHub Actions composite actions and supporting scripts
that orchestrate infrastructure-as-code delivery using the Terramate CLI and
OpenTofu. There is no server, no database, and no long-running service:
everything shipmate does happens inside a GitHub Actions workflow run,
reading and writing state through GitHub's own primitives (Environments,
caches, checks, PR comments) and the Terramate/OpenTofu CLIs. When the
workflow run ends, shipmate's job ends with it.

Consuming repositories pin every shipmate action **by commit SHA**, never by
a tag or branch name. This is a deliberate supply-chain choice: a commit SHA
is immutable, so a consumer's workflow behavior cannot change underneath it
without an explicit, reviewed bump of the pinned SHA. See `CONTRACT.md` for
the full contract this project follows, including check names, the
environment model, tag grammar, and pinning rules.

## Fan-out (stack x environment)

shipmate treats each Terramate stack and each target environment as
independent units of work. A repository with, say, three stacks (network,
database, app) and two environments (staging, production) fans out into up
to six plan/apply units, each tracked and checked independently. This lets
a change to one stack in one environment proceed (or be blocked) without
being entangled with unrelated stack/environment combinations, and lets
waves of applies respect dependency ordering only where a real dependency
exists.

## Checks-first

Every unit of work — a plan, an apply — surfaces as its own GitHub check
with a predictable, parseable name (see `CONTRACT.md`). Checks are the
primary UI: reviewers approve or block a pull request by looking at check
status and check output, not by reading raw workflow logs. An aggregate
check rolls up the fan-out into a single required status so branch
protection rules stay simple even as the number of underlying units grows.

## Comment-ops

Humans drive plan/apply behavior for a pull request through PR comments
(for example, requesting a re-plan, or approving an apply for a specific
stack/environment unit) rather than through bespoke UI or external tooling.
Comment-ops keep the entire interaction surface inside the pull request
that is already the unit of review, with an auditable history of who asked
for what and when.

## Dynamic environments

Environments are not hardcoded into workflow YAML. An environment is
defined by a GitHub Environment plus tags applied to the stacks that belong
to it; adding a new environment is a data change (create the Environment,
tag the relevant stacks), never a workflow code change. This keeps the
number of environments a repository supports independent of the complexity
of its CI configuration.

## Preview (phase 1)

The `preview.yml` workflow (thin and identical across repo layouts; see the
`repo-example-*` samples) runs on every pull request:

- **`detect`** — `terramate fmt --check`, a stale-codegen check
  (`terramate generate --detailed-exit-code`), and `actions/build-matrix`,
  which computes the plan matrix from the *changed* stacks × their `env/*`
  tags. Environment membership comes purely from stack tags — no environment
  names in YAML, no GitHub API/token needed.
- **`plan`** — one matrix job per stack × environment, bound to that GitHub
  Environment (which injects `TF_VAR_*` / `TF_WORKSPACE` / nothing, per
  layout). Each job is the `plan / <env> / <stack>` check; `actions/plan-cell`
  writes the **full plan text to the job's step summary** (reachable one click
  from the check), uploads the `.otplan` + a TF_VAR fingerprint as an
  artifact, and creates the `apply / <env> / <stack>` check **pending** (or
  completed "no changes").
- **`summary`** — `actions/summary` upserts one sticky PR comment (a stack ×
  env table) and creates/refreshes the aggregate **`shipmate / checkmate`**
  gate check, which stays non-green while any apply is pending or any plan
  cell failed.

Note on plan output: plan text lives in each `plan / <env> / <stack>` job's
**Summary**, not in a separate Checks-API check-run — the matrix job already
emits the check of that name, so a second API check would duplicate it. The
`apply` and `checkmate` checks *are* API check-runs (created pending; they
have no backing job in `preview.yml`).

To make the gate enforce apply-before-merge, configure branch protection to
require `shipmate / checkmate`; see [`docs/branch-protection.md`](docs/branch-protection.md).

## Deploy + drift (phase 2)

shipmate is a **serverless [TACO](https://docs.taco.io)**: the same
plan→store→review→apply model with no server or database. `deploy.yml` and
`drift.yml` are thin sample-repo workflows over shipmate actions.

- **`deploy.yml`** (`on: push main`) is the **exact-plan apply** path.
  `actions/deploy-detect` maps the merge commit → its PR head SHA, takes the
  stacks whose `apply / <env> / <stack>` check is still **pending**, and orders
  them into **waves** (`scripts/waves` = topological levels of the Terramate
  `after` DAG). Pre-declared `wave0..wave7` jobs each `needs` the previous; the
  skip-propagation guard (`if: !failure() && !cancelled() && waveN != '[]'`)
  lets empty middle waves pass through without blocking successors.
  `actions/apply-cell` downloads the reviewed `.otplan` from the preview run,
  verifies the fingerprint, applies **that exact plan** (never re-plans; stale
  state → fail-safe), and completes the apply check. A stack already applied
  (pre-merge via phase 3, or a no-change re-plan) has a completed check → deploy
  **no-ops** it.
- **`drift.yml`** (nightly cron) fans out over **all** stacks × envs, plans
  each with `actions/drift-cell`, and opens one labeled GitHub Issue per
  drifted stack × env — auto-closed on the next clean run. Optional Slack.
- **Generalization:** deploy + drift run unchanged across all three layouts
  (`repo-example-{stacks,folders,workspaces}`) — same pinned shipmate SHA, only
  the per-flavor `env:` block and state path differ (folders inject nothing,
  workspaces inject `TF_WORKSPACE`).

Two model notes vs a hosted TACO: with no server-side queue, GHA can drop a
**superseded** deploy run — its stacks stay pending + visible and are recovered
by re-running that deploy; and the manual **pre-merge** exact-plan apply
(`mate apply`) is delivered in phase 3 (it needs the GitHub App to update
checks).

---

**shipmate is not affiliated with Terramate GmbH.**

---

See `CONTRACT.md` for the full naming, environment, tag-grammar, and
pinning contract that every shipmate action and every consuming repository
follows.
