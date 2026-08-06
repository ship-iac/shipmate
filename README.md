# shipmate

> **Status: early development.** shipmate is a work in progress. Action inputs,
> check names, and tag grammar may change between commits. Pin by commit SHA
> (see below) and expect breaking changes.

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

Staying current does not require watching this repository's commit log.
shipmate publishes a GitHub Release per release SHA, so a consumer running
Dependabot's `github-actions` ecosystem gets a pull request bumping its pins —
to the release's commit SHA, still reviewed under the consumer's own
`CODEOWNERS` rules. `shipmate doctor` names any pin that differs from the
latest release, and the same finding appears as an annotation on every plan
run. Dependabot needs the current pin to be a released commit to propose a
bump; from an untagged pin, doctor's warning is the signal.

## Why setup is not two clicks

Most GitHub Apps install in two clicks. They can, because they are
**server-hosted**: their service holds the App's private key and performs the
privileged writes. You never handle a key, create an environment, or copy a
workflow file.

shipmate has no server, so **the App's private key lives in your repository** —
and a repository is a place your developers can push to. GitHub hands a
*repository* secret to any workflow on any branch, including a workflow file a
branch introduces. So a developer who may open pull requests but not merge them
could read that key from a branch they pushed, and with it post the
`shipmate / gate` status branch protection requires, approve their own pull
request as the App, or mark applies complete that never ran.

That is why the key is not a repository secret. It lives as an **environment**
secret on `shipmate-engine`, whose deployment branch policy names only the
default branch, and every step that mints an App token runs at the
default-branch ref (`workflow_run`, `issue_comment`, `workflow_dispatch`,
`push`). A branch-authored workflow naming that environment is denied the
deployment and gets nothing; a `pull_request` job is denied too, because its ref
(`refs/pull/<n>/merge`) matches no branch pattern. Environments are the only
scoping GitHub offers here — there is no per-workflow secret scoping.

**So the one extra environment is the price of not operating a service.** It is
not optional, and it is the difference between "the key is in your repo" and
"the key is in your repo and only the default branch can reach it". Everything
else in setup — the workflow files, the environments per deploy target — is the
same trade: work you do once, instead of a service you run forever.

`docs/github-app.md` §Key-exposure boundary has the mechanism;
`docs/hardening.md` has the full threat model and what it deliberately does not
claim.

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
Authorization requires team membership, a mergeable PR that satisfies the
branch ruleset's review policy, and a reviewed plan for the PR's current head.
Comment-ops keeps the entire interaction surface inside the pull request
that is already the unit of review, with an auditable history of who asked
for what and when. See `CONTRACT.md` for the full grammar and authorization
contract, and `docs/github-app.md` for one-time App setup.

### PR comment commands

Three verbs are active (`plan` and `destroy` are reserved for later):

- `shipmate apply [env]` — apply the reviewed plan for one environment, or
  every non-explicit environment when the environment is omitted.
- `shipmate doctor` — report setup problems: repository settings,
  environments, App permissions, and warnings from this commit's workflow
  runs.
- `shipmate help` — show this command list.

The sticky plan comment's footer points at `shipmate help`, so the commands are
discoverable from the pull request itself. `doctor`'s environment checks cover
the environments of the stacks a given pull request changed, and its report says
which ones those were — it is a check on the settings that pull request touches,
not a repository-wide audit.

`help` and `doctor` are read-only; `apply` is the one verb with a full
authorization check (approvers-team membership, a mergeable and reviewed PR, and
a reviewed plan for the current head — see Comment-ops above). `help` answers
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
defined by a GitHub Environment plus tags applied to the stacks that belong
to it; adding a new environment is a data change (create the Environment,
tag the relevant stacks), never a workflow code change. This keeps the
number of environments a repository supports independent of the complexity
of its CI configuration.

## Plan

The `plan.yml` workflow (thin and identical across repo layouts; see the
`repo-example-*` samples) runs on every pull request and **must keep both its
file path and its `name:`** — `.github/workflows/plan.yml`, named
`shipmate · plan` — because the `summary.yml` workflow below is chained onto
it by both: a `workflow_run` trigger that matches by **name**, and (inside
the trusted workflow that trigger calls) an explicit check of the exact
**file path**. Renaming either one, independently, gets a plan that runs but
never gates. `detect` now says so out loud, and fails the pull request on it.
The `name:` half would otherwise bite only from the merge that lands it
onward, so the renaming pull request itself still gates and merges green; see
`CONTRACT.md` §Post-plan topology for both halves and for what
`detect` does about them. Give its one non-fan-out job the
display name `shipmate / detect`, since its check run is created by GitHub
Actions (a job's check run always is) and a bare `detect` in the checks list
says nothing about which tool produced it. See [`CONTRACT.md`](CONTRACT.md)
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
  artifact. `plan.yml` holds no App credential at all — the App private key
  never enters a `pull_request`-triggered job, full stop. `detect` also
  **refuses fork pull requests**: a plan would run the fork's own
  Terramate/OpenTofu code on your runners with your plan environment's
  variables, so the branch has to live in the repository. There is no input to
  allow them.

A separate `summary.yml` workflow — triggered by `workflow_run` once
`plan.yml` completes, and bound to the fixed `shipmate-engine` GitHub
Environment (`docs/github-app.md`) — does the credentialed work: it resolves
the pull request, then calls `actions/summary`, which creates the matching
`apply / <stack> / <env>` check **pending** (or completed "no changes") and
upserts one sticky PR comment (a stack × env table) and the aggregate
**`shipmate / gate`** commit status, which stays non-green while any apply is
pending or any plan cell failed. Being a `workflow_run` job it runs at the
default-branch ref, from the workflow file on that branch rather than from the
pull request head — and it declines outright when the plan run's
`head_repository` isn't this repository. Fork pull requests do not get that far:
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
require `shipmate / gate`; see [`docs/branch-protection.md`](docs/branch-protection.md).
For who can make the engine act — push access is authority over it — and the
settings that bound that, see [`docs/hardening.md`](docs/hardening.md).

## Deploy + drift

shipmate follows a **serverless plan→store→review→apply** model — the reviewed
plan is stored and applied verbatim, with no server or database. A consumer's
`deploy.yml` is a 19-line wrapper over the engine's reusable deploy workflow
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
  verifies the fingerprint, applies **that exact plan** (never re-plans; stale
  state → fail-safe), and completes the apply check. A stack already applied
  (pre-merge, or a no-change re-plan) has a completed check → deploy
  **no-ops** it.
- **`drift.yml`** (nightly cron) fans out over **all** stacks × envs and
  plans each with `actions/drift-cell`, which holds no App credential and
  only uploads a drift-summary artifact. A separate `issues` job, bound to
  `shipmate-engine`, downloads those artifacts and opens one labeled GitHub
  Issue per drifted stack × env via `actions/drift-issues` — auto-closed on
  the next clean run. Optional Slack.
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
credentials at all.** See [`CONTRACT.md`](CONTRACT.md) §State backend and
§AWS OIDC for the semantics.

One model note vs a hosted service: with no server-side queue, GHA can drop a
**superseded** deploy run — its stacks stay pending + visible and are recovered
by re-running that deploy. The manual **pre-merge** exact-plan apply
(`shipmate apply <env>` in a PR comment) shares the same exact-plan `apply-cell`
path and the same per-env, per-stack concurrency group as `deploy.yml`, so a
comment-triggered apply and a post-merge deploy can never race against the
same stack × environment; see Comment-ops above and `CONTRACT.md`.

## Example repositories

Three sample repos exercise shipmate end to end against local state with **zero
cloud credentials**, one per common IaC layout — the best place to see the
workflows wired up:

- [repo-example-stacks](https://github.com/ship-iac/repo-example-stacks) — DRY / dynamic-backend (`TF_VAR_env` / `TF_VAR_region`)
- [repo-example-folders](https://github.com/ship-iac/repo-example-folders) — folder-per-env/region (no injected vars)
- [repo-example-workspaces](https://github.com/ship-iac/repo-example-workspaces) — workspace-per-env (`TF_WORKSPACE`)

## Development

The engine's logic lives in a few small Python helper scripts under `scripts/`
(they run as GitHub Actions steps, so they're executable and have no `.py`
extension) plus their unit tests in `scripts/tests/`. A separate `dev/` holds
maintainer tooling for the engine's own SHA pins — run by hand, never from a
workflow (see [CONTRIBUTING.md](CONTRIBUTING.md) and `docs/releasing.md`). The
dev toolchain is [Astral](https://astral.sh)'s:

- **[uv](https://docs.astral.sh/uv/)** — manages the dev environment and pinned
  tool versions (`pyproject.toml` + `uv.lock`). shipmate ships no importable
  package and has no runtime dependencies (stdlib only); uv is only for tooling.
- **[ruff](https://docs.astral.sh/ruff/)** — lint + format. The lint set
  includes `S` (flake8-bandit) for security checks.
- **[ty](https://github.com/astral-sh/ty)** — type checker (still beta, so it's
  non-blocking in CI).
- **pytest** — unit tests for the helper scripts.

```bash
uv run ruff check .            # lint (incl. security S rules)
uv run ruff format .           # auto-format  (--check to verify only)
uv run pytest scripts/tests    # unit tests
uv run ty check                # type-check (beta)
```

CI (`.github/workflows/ci.yml`) runs ruff check, `ruff format --check`, and
pytest as required checks on every pull request; ty runs non-blocking. End-to-end
behavior is exercised by the `repo-example-*` sample repositories, which run
these actions against local state with zero cloud credentials.

---

**Trademarks.** Terramate is a trademark of Terramate GmbH; Terraform is a
trademark of HashiCorp; OpenTofu is a project of the Linux Foundation. shipmate
is an independent project and is not affiliated with, endorsed by, or sponsored
by any of them; their marks are used only to identify the tools shipmate works
with.

---

See `CONTRACT.md` for the full naming, environment, tag-grammar, and
pinning contract that every shipmate action and every consuming repository
follows.
