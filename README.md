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

Humans drive apply behavior for a pull request through PR comments —
`shipmate apply <env>` — rather than through bespoke UI or external tooling.
A private GitHub App mints the short-lived token needed to dispatch the apply
workflow from a comment (events created with the default `GITHUB_TOKEN` never
trigger other workflows); the App itself has no `checks` or `issues`
permission, so check-run writes and PR comments stay on the shared
`github-actions` identity. Authorization requires team membership, an
approved and mergeable PR, and a reviewed plan for the PR's current head.
Comment-ops keeps the entire interaction surface inside the pull request
that is already the unit of review, with an auditable history of who asked
for what and when. See `CONTRACT.md` for the full grammar and authorization
contract, and `docs/github-app.md` for one-time App setup.

## Dynamic environments

Environments are not hardcoded into workflow YAML. An environment is
defined by a GitHub Environment plus tags applied to the stacks that belong
to it; adding a new environment is a data change (create the Environment,
tag the relevant stacks), never a workflow code change. This keeps the
number of environments a repository supports independent of the complexity
of its CI configuration.

## Plan

The `plan.yml` workflow (thin and identical across repo layouts; see the
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
  env table) and creates/refreshes the aggregate **`shipmate / gate`**
  commit status, which stays non-green while any apply is pending or any
  plan cell failed.

Note on plan output: plan text lives in each `plan / <env> / <stack>` job's
**Summary**, not in a separate Checks-API check-run — the matrix job already
emits the check of that name, so a second API check would duplicate it. The
`apply` checks *are* API check-runs (created pending; they have no backing
job in `plan.yml`). The aggregate `gate` is a **commit status**, not a
check-run: a status is commit-scoped, so it cannot be misattributed to a
stale check-suite when a commit carries two plan runs (draft→ready, or a
rapid re-push) — a check-run can, silently blocking the merge forever.

To make the gate enforce apply-before-merge, configure branch protection to
require `shipmate / gate`; see [`docs/branch-protection.md`](docs/branch-protection.md).

## Deploy + drift

shipmate follows a **serverless plan→store→review→apply** model — the reviewed
plan is stored and applied verbatim, with no server or database. A consumer's
`deploy.yml` is a 19-line wrapper over the engine's reusable deploy workflow
(passing only its flavor's `state_suffix`); `drift.yml` is a thin sample-repo
workflow over shipmate actions.

- **`deploy.yml`** (`on: push main`, engine reusable
  `.github/workflows/deploy.yml`) is the **exact-plan apply** path.
  `actions/deploy-detect` maps the merge commit → its PR head SHA, takes the
  stacks whose `apply / <env> / <stack>` check is still **pending**, and orders
  them into **waves** (`scripts/waves` = topological levels of the Terramate
  `after` DAG). Pre-declared `wave0..wave7` jobs each `needs` the previous; the
  skip-propagation guard (`if: !failure() && !cancelled() && waveN != '[]'`)
  lets empty middle waves pass through without blocking successors.
  `actions/apply-cell` downloads the reviewed `.otplan` from the plan run,
  verifies the fingerprint, applies **that exact plan** (never re-plans; stale
  state → fail-safe), and completes the apply check. A stack already applied
  (pre-merge, or a no-change re-plan) has a completed check → deploy
  **no-ops** it.
- **`drift.yml`** (nightly cron) fans out over **all** stacks × envs, plans
  each with `actions/drift-cell`, and opens one labeled GitHub Issue per
  drifted stack × env — auto-closed on the next clean run. Optional Slack.
- **Generalization:** deploy + drift run unchanged across all three layouts
  (`repo-example-{stacks,folders,workspaces}`) — same pinned shipmate SHA, only
  the per-flavor state path (deploy wrapper's `state_suffix`) and, for drift,
  the per-flavor `env:` block differ (folders inject nothing, workspaces
  inject `TF_WORKSPACE`).

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
extension) plus their unit tests in `scripts/tests/`. The dev toolchain is
[Astral](https://astral.sh)'s:

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
