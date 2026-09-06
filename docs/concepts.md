# Concepts

How shipmate works, for a reader deciding whether to adopt it or working out why
it behaved a certain way. [`../CONTRACT.md`](../CONTRACT.md) is the source for
exact semantics: check names, the environment model, tag grammar, pinning. This
page is the explanation behind them.

## Fan-out (stack × environment)

shipmate treats each Terramate stack and each target environment as
independent units of work. A repository with, say, three stacks (network,
database, app) and two environments (staging, production) fans out into up
to six plan/apply units, each tracked and checked independently. A change to
one stack in one environment proceeds, or is blocked, without being entangled
with unrelated stack/environment combinations. Waves of applies respect
dependency ordering only where a real dependency exists.

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
trigger other workflows). The same App also authors the apply checks, the
`shipmate / gate` status, the sticky plan comment, the `shipmate doctor`
sticky report, a fresh apply result comment on every apply run, and drift
issues, each via a freshly minted installation token.

Unlike the sticky plan comment, the apply result comment is never upserted:
each run posts a new comment with a per-cell status table and the collapsed
full apply output for every attempted cell, so a failure-then-retry sequence
stays visible as an audit trail.

The plan matrix job's own `shipmate / <stack> / <env>` check-run stays on the shared
`github-actions` identity — it's the job's own auto check-run, not something
the App creates separately. An on-demand plan is the exception: a dispatched
run's job checks land on the ref it was dispatched on, so the summary job
mirrors that run's own completed checks onto the pull request head as
App-authored copies linking back to the originals.

Authorizing an apply requires team membership, a non-draft, mergeable PR that
satisfies the branch ruleset's review policy, and a reviewed plan for the PR's
current head. Authorizing an unlock requires team membership plus the
`<env>-apply` environment its job binds.

Comment-ops keeps the entire interaction surface inside the pull request
that is already the unit of review, with an auditable history of who asked
for what and when. See `CONTRACT.md` for the full grammar and authorization
contract, and `docs/github-app.md` for one-time App setup.

## PR comment commands

Five verbs are active (`destroy` is reserved for later):

- `shipmate apply [env]` — apply the reviewed plan for one environment, or
  every non-explicit environment when the environment is omitted.
- `shipmate doctor` — report setup problems: repository settings,
  environments, App permissions, and warnings from this commit's workflow
  runs.
- `shipmate help` — show this command list.
- `shipmate plan` — plan this pull request's changed stacks on demand, the
  same plan a push produces, including on a draft. Takes no environment: there
  is one plan of record per head commit. Re-planning is safe and replaces it.
- `shipmate unlock <env>` — release a state lock stranded by a cancelled or
  killed apply, so the environment's stacks can apply again. Does not
  re-apply, and does not recover a partial apply.

The sticky plan comment's footer points at `shipmate help`, so the commands are
discoverable from the pull request itself. `doctor`'s environment checks cover
the environments of the stacks a given pull request changed, and its report says
which ones those were. It is a check on the settings that pull request touches,
not a repository-wide audit.

`help` and `doctor` are read-only. `plan` changes no infrastructure, but is open
to the same commenters `doctor` is. `apply` and `unlock` are authorized. `apply`
carries the full check: approvers-team membership, a non-draft, mergeable and
reviewed PR, and a reviewed plan for the current head (see Comment-ops above).
`unlock` carries a narrower one — approvers-team membership and the
`<env>-apply` environment, but no draft check, no review and no plan —
because it releases a lock rather than changing infrastructure.

`help` answers any commenter. `doctor` does not: it names the guardrails this
repository is missing — that `shipmate / gate` is not required on the default
branch, that an apply environment has no approval rule, which approvers team is
configured and whether it resolves. So the engine runs it only for a commenter
GitHub classifies as `OWNER`, `MEMBER` or `COLLABORATOR`: organization members
and repository collaborators. Anyone else gets a one-line refusal. No App token
is minted and no probe runs. Adopting the gate takes only a re-pin of the engine
SHA — no new input, no new workflow permission.

Three limits:

- `author_association` is GitHub's own classification of the author, not a check
  for write access, and it errs both ways. A collaborator invited with only the
  Read role and an organization member whose base repository permission is None
  are still admitted to the report. An organization member whose membership is
  private is reported as `NONE`, and is refused unless they are also a direct
  collaborator. What the gate does buy is that an account with no declared
  relationship to the repository is refused.
- `shipmate help` is not gated at all.
- The report is an ordinary comment. Once someone with access asks for it,
  everyone who can read the pull request can read it.

On a public repository you can add a second layer by restricting who can trigger
the comment-ops workflow — a `github.event.comment.author_association` condition
on the `issue_comment` job, or keeping the repository private — belt and braces
over the engine's gate rather than a substitute for it. The App manifest is
`"public": false`: the shipmate App is meant for repositories the installing
organization controls.

## Dynamic environments

Environments are not hardcoded into workflow YAML. An environment is
defined by the GitHub Environments named after it (`<env>-plan` and
`<env>-apply`, or one shared `<env>` — `../CONTRACT.md` §Env model) plus tags
applied to the stacks that belong to it. Adding a new environment is a data
change (create its Environments, tag the relevant stacks), never a workflow code
change. The number of environments a repository supports is therefore
independent of the complexity of its CI configuration.

## The plan path

A consumer's `plan.yml` is a shim over the engine's reusable plan workflow,
which has four jobs: `facts`, `detect`, `plan`, and `summary`. The shape is the
same across repo layouts and across repositories — what differs per flavor is
which variables the plan environment injects (`TF_VAR_*`, `TF_WORKSPACE`, or
nothing) and whether a role variable is set there at all.

The shim answers to two triggers: `pull_request_target` for the automatic plan
on every push to a pull request, and `workflow_dispatch` for the plan a
commented `shipmate plan` asks for. Both run at a ref the trusted `summary` job's
environment policy admits (the base ref under `pull_request_target`, the
dispatch ref under `workflow_dispatch`), which is what lets that job reach the
App key. Neither checks out the pull request's head, so `detect` and `plan` have
to name `ref: ${{ needs.facts.outputs.head-sha }}` on their checkout explicitly,
or the run is refused for planning a tree the pull request never named.

The engine's jobs appear in the checks list under the shim's calling job name:
`shipmate / facts`, `shipmate / detect`, `shipmate / summary` and one
`shipmate / <stack> / <env>` per cell. Those check runs are created by GitHub
Actions, as a job's check run always is, and the prefix is what says which tool
produced them. See
[`CONTRACT.md`](../CONTRACT.md) §Check names for the `shipmate / ` namespace and
§Post-plan topology for the full picture below:

- **`facts`** — `actions/pr-facts`, the single producer of every pull-request
  fact the jobs below decide on: the head SHA and head repository, the base SHA,
  the pull request's number, its draft flag, and whether a human named this run
  (`on-demand`). A pull-request event answers out of its own payload. A
  dispatched run carries no pull request at all and looks it up by the number it
  was dispatched with. That is why this one job spends `pull-requests: read` and
  the others do not. Its own job rather than `detect`'s first step: `detect` can
  fail, and the `summary` job must still be told which head to gate.
- **`detect`** — `actions/build-matrix`, which computes the plan matrix from the
  *changed* stacks × their `env/*` tags, and then `terramate fmt --check` and a
  stale-codegen check (`terramate generate --detailed-exit-code`). That order is
  the fork refusal's: `build-matrix` turns a fork's head away before either
  terramate step reads the tree it wrote ([hardening](hardening.md)).
  Environment membership comes purely from stack tags — no environment names in
  YAML, no GitHub API/token needed.
- **`plan`** — one matrix job per stack × environment, bound to that GitHub
  Environment (which injects `TF_VAR_*` / `TF_WORKSPACE` / nothing, per
  layout). Each job is the `shipmate / <stack> / <env>` check (shown as
  `shipmate · plan / shipmate / <stack> / <env>` in the UI). `actions/plan-cell`
  writes the full plan text to the job's step summary (reachable one click
  from the check), and uploads the `.otplan` + a TF_VAR fingerprint as an
  artifact. `detect` binds no environment. `plan` binds only the plan
  environment for the cell it is planning, never one holding an App credential.
  `detect` also refuses fork pull requests: a plan would run the fork's own
  Terramate/OpenTofu code on your runners with your plan environment's
  variables, so the branch has to live in the repository. `facts` reports the
  pull request's head repository and `build-matrix` refuses by default — an
  unstated head repository is refused too. No input allows a fork; the one
  opt-out says the run has no pull request at all, and only the drift workflow
  sets it.
- **`summary`** — the one trusted job, bound to the fixed `shipmate-engine`
  GitHub Environment (`docs/github-app.md`) with no checkout of its own: it
  downloads this run's cell summaries and calls
  `actions/summary`, which creates the matching
  `apply / <stack> / <env>` check pending (or completed "no changes"), and
  upserts one sticky PR comment (a stack × env table) and the aggregate
  `shipmate / gate` commit status, which stays non-green while any apply is
  pending or any plan cell failed.

  That job declines outright — before its first step — on a fork pull request
  and on a draft nobody asked to plan, reading both facts from `facts` in the
  same file. An empty head repository, the shape a failed `facts` job produces,
  is a refusal rather than a pass. Nothing a consumer writes reaches that
  decision.

  On an `on-demand` run it also mirrors this run's own
  completed per-cell plan checks onto the head commit: a dispatched run's job
  check-runs attach to the dispatch ref, so without the mirror the pull request
  shows none of them — not even a failed cell, which is the state
  `shipmate plan` exists to recover from.

Fork pull requests do not get that far anyway: the `detect` job refuses them, so
a fork's plan fails fast rather than fanning out plan cells over fork-authored
code. A pull request that changed
no stacks gets no plan comment at all — nothing is posted when there are no
cells, no comment already on the pull request, and no `doctor` warning to
point at — so docs-only and pin-bump changes stay quiet apart from their
checks. An existing comment is still updated, so a plan that was pushed away
never leaves a stale table behind. A run whose cell count is zero for any
*other* reason (failed detect, all cells failed, cell artifacts
undownloadable) writes nothing and leaves the previous plan standing rather
than claiming "no stacks changed" — the gate fails those runs.

Plan text lives in each `shipmate / <stack> / <env>` plan job's
Summary, not in a separate Checks-API check-run — the matrix job already
emits the check of that name, so a second API check would duplicate it. The
`apply` checks *are* API check-runs (created pending; they have no backing
job in `plan.yml`). The aggregate `gate` is a commit status, not a
check-run: a status is commit-scoped, so it cannot be misattributed to a
stale check-suite when a commit carries two plan runs (draft→ready, or a
rapid re-push) — a check-run can, silently blocking the merge forever.

To make the gate enforce apply-before-merge, configure branch protection to
require `shipmate / gate`; see [`branch-protection.md`](branch-protection.md).
For who can make the engine act — push access is authority over it — and the
settings that bound that, see [`hardening.md`](hardening.md).

## Deploy and drift

shipmate follows a serverless plan→store→review→apply model: the reviewed plan
is stored and applied verbatim, with no server or database.
A consumer's `deploy.yml` is a shim over the engine's reusable deploy workflow,
passing only its flavor's `state_suffix`; `drift.yml` is a shim of the same
shape over the engine's reusable drift workflow.

- **`deploy.yml`** (`on: push main`, engine reusable
  `.github/workflows/deploy.yml`) is the exact-plan apply path.
  `actions/deploy-detect` maps the merge commit → its PR head SHA, takes the
  stacks whose `apply / <stack> / <env>` check is still pending, and orders
  them into waves (`scripts/waves` = topological levels of the Terramate
  `after` DAG). Pre-declared `wave0..wave7` jobs each `needs` the previous. The
  skip-propagation guard (`if: !failure() && !cancelled() && waveN != '[]'`)
  lets empty middle waves pass through without blocking successors.
  `actions/apply-cell` downloads the reviewed `.otplan` from the plan run,
  verifies the fingerprint and the commit the plan was produced from, applies
  that exact plan (never re-plans; stale state → fail-safe), and completes
  the apply check. A stack already applied (pre-merge, or a no-change re-plan)
  has a completed check → deploy no-ops it.
- **`drift.yml`** (nightly cron, engine reusable
  `.github/workflows/drift.yml`) fans out over all stacks × envs, or a
  slice of them, and plans each with `actions/drift-cell`, which holds no App
  credential and only uploads a drift-summary artifact. A separate `issues` job,
  bound to `shipmate-engine`, downloads those artifacts and opens one labeled
  GitHub Issue per drifted stack × env via `actions/drift-issues` — auto-closed
  on the next clean run that covers it. Optional Slack. Setup is in
  [drift.md](drift.md).
- **Generalization:** deploy + drift run unchanged across all three layouts
  (`repo-example-{stacks,folders,workspaces}`) — same pinned shipmate SHA, only
  the per-flavor state path (each shim's `state_suffix`) differs; the per-flavor
  environment variables come from the GitHub Environment a cell binds (folders
  inject nothing, workspaces inject `TF_WORKSPACE`).

**Remote state and cloud credentials.** `state_suffix` is required, but may be
the empty string. Set it to `''` and a remote backend (for example S3) owns the
state, and the engine's state restore/save steps are skipped. Omitting it
altogether is a workflow-resolution error, on purpose — a forgotten state
configuration must fail loud rather than apply with no state at all.

Credentials are opt-in per GitHub Environment
through two variables, `AWS_ROLE_ARN` and `AWS_REGION` — unset, and no cloud
credential ever enters the job, which is how the sample repos run
credential-free. The apply and unlock jobs request `id-token: write`, and GitHub
caps a called workflow's permissions at each `uses:` boundary. So the calling
job of every consumer shim but `comment-ops.yml` must grant `id-token: write` —
including consumers using no cloud credentials at all. The plan and drift cells
run the same credentials step as the apply waves, reading the role from the plan
environment each cell binds. See
[`CONTRACT.md`](../CONTRACT.md) §State backend and §AWS OIDC for the semantics.

One model note vs a hosted service: with no server-side queue, GHA can drop a
superseded deploy run — its stacks stay pending + visible and are recovered
by re-running that deploy. The manual pre-merge exact-plan apply
(`shipmate apply <env>` in a PR comment) shares the same exact-plan `apply-cell`
path and the same per-env, per-stack concurrency group as `deploy.yml`, so a
comment-triggered apply and a post-merge deploy can never race against the
same stack × environment; see Comment-ops above and `CONTRACT.md`.
