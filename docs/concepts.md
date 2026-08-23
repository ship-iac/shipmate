# Concepts

How shipmate works, for a reader deciding whether to adopt it or working out why
it behaved a certain way. [`../CONTRACT.md`](../CONTRACT.md) is the source for
exact semantics — check names, the environment model, tag grammar, pinning — and
this page is the explanation behind them.

## Fan-out (stack × environment)

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

Humans drive shipmate for a pull request through PR comments — `shipmate
apply <env>` and friends — rather than through bespoke UI or external tooling.
A private GitHub App mints the short-lived token needed to dispatch the apply
workflow from a comment (events created with the default `GITHUB_TOKEN` never
trigger other workflows); the same App also authors the apply checks, the
`shipmate / gate` status, the sticky plan comment, the `shipmate doctor`
sticky report, a fresh apply result comment on every apply run, and drift
issues, each via a freshly minted installation token. Unlike the sticky
plan comment, the apply result comment is never upserted: each run posts a
new comment with a per-cell status table and the collapsed full apply
output for every attempted cell, so a failure-then-retry sequence stays
visible as an audit trail. The plan
matrix job's own `<stack> / <env>` check-run stays on the shared
`github-actions` identity — it's the job's own auto check-run, not something
the App creates separately.
Authorizing an apply requires team membership, a mergeable PR that satisfies
the branch ruleset's review policy, and a reviewed plan for the PR's current
head; authorizing an unlock requires team membership plus the `<env>-apply`
environment its job binds.
Comment-ops keeps the entire interaction surface inside the pull request
that is already the unit of review, with an auditable history of who asked
for what and when. See `CONTRACT.md` for the full grammar and authorization
contract, and `docs/github-app.md` for one-time App setup.

## PR comment commands

Four verbs are active (`plan` and `destroy` are reserved for later):

- `shipmate apply [env]` — apply the reviewed plan for one environment, or
  every non-explicit environment when the environment is omitted.
- `shipmate doctor` — report setup problems: repository settings,
  environments, App permissions, and warnings from this commit's workflow
  runs.
- `shipmate help` — show this command list.
- `shipmate unlock <env>` — release a state lock stranded by a cancelled or
  killed apply, so the environment's stacks can apply again. Does not
  re-apply, and does not recover a partial apply.

The sticky plan comment's footer points at `shipmate help`, so the commands are
discoverable from the pull request itself. `doctor`'s environment checks cover
the environments of the stacks a given pull request changed, and its report says
which ones those were — it is a check on the settings that pull request touches,
not a repository-wide audit.

`help` and `doctor` are read-only; `apply` and `unlock` are authorized. `apply`
carries the full check (approvers-team membership, a mergeable and reviewed PR,
and a reviewed plan for the current head — see Comment-ops above); `unlock`
carries a narrower one — approvers-team membership and the `<env>-apply`
environment, but no review and no plan — because it releases a lock rather than
changing infrastructure. `help` answers
any commenter. `doctor` does not: it names the guardrails this repository is
missing — that `shipmate / gate` is not required on the default branch, that an
apply environment has no approval rule, which approvers team is configured
and whether it resolves — so the engine runs it only for a commenter GitHub
classifies as `OWNER`, `MEMBER` or `COLLABORATOR`: organization members and
repository collaborators. Anyone else gets a one-line refusal; no App token is
minted and no probe runs. Adopting the gate takes only a re-pin of the engine
SHA — no new input, no new workflow permission.

Three limits worth knowing. `author_association` is GitHub's own classification
of the author, **not a check for write access**, and it errs both ways: a
collaborator invited with only the **Read** role and an organization member whose
base repository permission is **None** are still admitted to the report, while an
organization member whose membership is **private** is reported as `NONE` and is
refused unless they are also a direct collaborator. What the gate does buy is
that an account with no declared relationship to the repository is refused.
`shipmate help` is not gated at all. And the report is an ordinary comment, so
once someone with access asks for it, everyone who can read the pull request can
read it. On a **public** repository you can add a second layer by restricting
who can trigger the
comment-ops workflow — a `github.event.comment.author_association` condition on
the `issue_comment` job, or keeping the repository private — belt and braces over
the engine's gate rather than a substitute for it. The App manifest is
`"public": false`: the shipmate App is meant for repositories the installing
organization controls.

## Dynamic environments

Environments are not hardcoded into workflow YAML. An environment is
defined by the GitHub Environments named after it (`<env>-plan` and
`<env>-apply`, or one shared `<env>` — `../CONTRACT.md` §Env model) plus tags
applied to the stacks that belong to it; adding a new environment is a data change
(create its Environments, tag the relevant stacks), never a workflow code
change. This keeps the
number of environments a repository supports independent of the complexity
of its CI configuration.

## The plan path

The `plan.yml` workflow (the same shape across repo layouts — what differs is
the `plan` job's per-flavor `env:` block, `TF_VAR_*` or `TF_WORKSPACE` or
nothing, and whether it carries a cloud credentials step at all; see the
`repo-example-*` samples) runs on every pull request and has **three jobs**:
`detect`, `plan`, and `summary`. It triggers on `pull_request_target`, which
runs at the base ref — that is what lets the trusted `summary` job reach the App
key, and `detect` and `plan` therefore have to name
`ref: ${{ github.event.pull_request.head.sha }}` on their checkout explicitly or
they plan the base branch instead. Give `detect` the
display name `shipmate / detect`, since its check run is created by GitHub
Actions (a job's check run always is) and a bare `detect` in the checks list
says nothing about which tool produced it. See [`CONTRACT.md`](../CONTRACT.md)
§Check names for the `shipmate / ` namespace and §Post-plan topology for the
full picture below:

- **`detect`** — `terramate fmt --check`, a stale-codegen check
  (`terramate generate --detailed-exit-code`), and `actions/build-matrix`,
  which computes the plan matrix from the *changed* stacks × their `env/*`
  tags. Environment membership comes purely from stack tags — no environment
  names in YAML, no GitHub API/token needed.
- **`plan`** — one matrix job per stack × environment, bound to that GitHub
  Environment (which injects `TF_VAR_*` / `TF_WORKSPACE` / nothing, per
  layout). Each job is the `<stack> / <env>` check (shown as
  `shipmate · plan / <stack> / <env>` in the UI); `actions/plan-cell`
  writes the **full plan text to the job's step summary** (reachable one click
  from the check), uploads the `.otplan` + a TF_VAR fingerprint as an
  artifact. `detect` binds no environment; `plan` binds only the plan
  environment for the cell it is planning, never one holding an App credential.
  `detect` also
  **refuses fork pull requests**: a plan would run the fork's own
  Terramate/OpenTofu code on your runners with your plan environment's
  variables, so the branch has to live in the repository. There is no input to
  allow them.
- **`summary`** — `uses:` the engine's reusable
  `.github/workflows/summary.yml`, passing `SHIPMATE_APP_PRIVATE_KEY` by name
  (never `secrets: inherit` — `docs/getting-started.md`) plus the pull
  request number, the head SHA, the two other jobs' results and the planned cell
  count. The credentialed work happens inside that callee, in a single job bound
  to the fixed `shipmate-engine` GitHub Environment (`docs/github-app.md`) with
  **no checkout of its own**: it downloads this run's cell summaries and calls
  `actions/summary`, which creates the matching
  `apply / <stack> / <env>` check **pending** (or completed "no changes") and
  upserts one sticky PR comment (a stack × env table) and the aggregate
  **`shipmate / gate`** commit status, which stays non-green while any apply is
  pending or any plan cell failed. That job declines outright — before its first
  step — on a fork pull request or a draft.

Fork pull requests do not get that far anyway:
`detect` refuses them (see above), so a fork's plan fails fast rather than
fanning out plan cells over fork-authored code. A pull request that changed
no stacks gets no plan comment at all — nothing is posted when there are no
cells, no comment already on the pull request, and no `doctor` warning to
point at — so docs-only and pin-bump changes stay quiet apart from their
checks. An existing comment is still updated, so a plan that was pushed away
never leaves a stale table behind; and a run whose cell count is zero for any
*other* reason (failed detect, all cells failed, cell artifacts
undownloadable) writes nothing and leaves the previous plan standing rather
than claiming "no stacks changed" — the gate fails those runs.

Note on plan output: plan text lives in each `<stack> / <env>` plan job's
**Summary**, not in a separate Checks-API check-run — the matrix job already
emits the check of that name, so a second API check would duplicate it. The
`apply` checks *are* API check-runs (created pending; they have no backing
job in `plan.yml`). The aggregate `gate` is a **commit status**, not a
check-run: a status is commit-scoped, so it cannot be misattributed to a
stale check-suite when a commit carries two plan runs (draft→ready, or a
rapid re-push) — a check-run can, silently blocking the merge forever.

To make the gate enforce apply-before-merge, configure branch protection to
require `shipmate / gate`; see [`branch-protection.md`](branch-protection.md).
For who can make the engine act — push access is authority over it — and the
settings that bound that, see [`hardening.md`](hardening.md).

## Deploy and drift

shipmate follows a **serverless plan→store→review→apply** model — the reviewed
plan is stored and applied verbatim, with no server or database. A consumer's
`deploy.yml` is a 22-line wrapper over the engine's reusable deploy workflow
(passing only its flavor's `state_suffix`); `drift.yml` is a thin sample-repo
workflow over shipmate actions.

- **`deploy.yml`** (`on: push main`, engine reusable
  `.github/workflows/deploy.yml`) is the **exact-plan apply** path.
  `actions/deploy-detect` maps the merge commit → its PR head SHA, takes the
  stacks whose `apply / <stack> / <env>` check is still **pending**, and orders
  them into **waves** (`scripts/waves` = topological levels of the Terramate
  `after` DAG). Pre-declared `wave0..wave7` jobs each `needs` the previous; the
  skip-propagation guard (`if: !failure() && !cancelled() && waveN != '[]'`)
  lets empty middle waves pass through without blocking successors.
  `actions/apply-cell` downloads the reviewed `.otplan` from the plan run,
  verifies the fingerprint and the commit the plan was produced from, applies
  **that exact plan** (never re-plans; stale state → fail-safe), and completes
  the apply check. A stack already applied (pre-merge, or a no-change re-plan)
  has a completed check → deploy **no-ops** it.
- **`drift.yml`** (nightly cron) fans out over **all** stacks × envs and
  plans each with `actions/drift-cell`, which holds no App credential and
  only uploads a drift-summary artifact. A separate `issues` job, bound to
  `shipmate-engine`, downloads those artifacts and opens one labeled GitHub
  Issue per drifted stack × env via `actions/drift-issues` — auto-closed on
  the next clean run. Optional Slack. Setup is in [drift.md](drift.md).
- **Generalization:** deploy + drift run unchanged across all three layouts
  (`repo-example-{stacks,folders,workspaces}`) — same pinned shipmate SHA, only
  the per-flavor state path (deploy wrapper's `state_suffix`) and, for drift,
  the per-flavor `env:` block differ (folders inject nothing, workspaces
  inject `TF_WORKSPACE`).

**Remote state and cloud credentials.** `state_suffix` is required, but may be
the empty string: set it to `''` and a remote backend (for example S3) owns the
state, and the engine's state restore/save steps are skipped. Omitting it
altogether is a workflow-resolution error, on purpose — a forgotten state
configuration must fail loud rather than apply with no state at all.
Credentials are opt-in per GitHub Environment
through two variables, `AWS_ROLE_ARN` and `AWS_REGION` — unset, and no cloud
credential ever enters the job, which is how the sample repos run
credential-free. Because the apply jobs now request `id-token: write`, and
GitHub caps a called workflow's permissions at each `uses:` boundary, **every
consumer wrapper that calls the apply-path workflows must grant
`id-token: write` on the calling job — including consumers using no cloud
credentials at all.** See [`CONTRACT.md`](../CONTRACT.md) §State backend and
§AWS OIDC for the semantics.

One model note vs a hosted service: with no server-side queue, GHA can drop a
**superseded** deploy run — its stacks stay pending + visible and are recovered
by re-running that deploy. The manual **pre-merge** exact-plan apply
(`shipmate apply <env>` in a PR comment) shares the same exact-plan `apply-cell`
path and the same per-env, per-stack concurrency group as `deploy.yml`, so a
comment-triggered apply and a post-merge deploy can never race against the
same stack × environment; see Comment-ops above and `CONTRACT.md`.
