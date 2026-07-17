# CONTRACT

This document is the naming, environment, tag-grammar, and pinning contract
that every shipmate action and every consuming repository must follow. Where
a value is marked verbatim, it must be used exactly as written — these
strings are parsed by other parts of the system (check-name matching,
comment-ops, tag-based stack selection) and are not free-form prose.

## Check names

Every plan and apply unit of work reports as its own GitHub check, using
these names verbatim:

- `plan / <env> / <stack>`
- `apply / <env> / <stack>`

`<env>` and `<stack>` are placeholders substituted with the actual
environment name and the Terramate **stack path** (as emitted by
`terramate list` / `experimental run-graph`, e.g. `stacks/network`) for that
unit of work (for example, `plan / staging / stacks/network`). The check name
uses the stack **path**, never a display name — so the code that *creates* the
apply check (`plan-cell`), *completes* it (`apply-cell`), and *filters the
still-pending queue* (`deploy-detect`, which only ever has the path) all
reconstruct the identical name from the one value they share.

In addition to the per-unit checks, one aggregate check rolls up the full
fan-out into a single required status, named verbatim:

- `shipmate / checkmate`

Branch protection rules should require `shipmate / checkmate`, not the
individual per-unit checks, so that the set of required checks does not
need to be edited every time a stack or environment is added or removed.

## Env model

- One GitHub Environment exists per logical environment (for example,
  `staging`, `production`). The Environment is always the unit of binding,
  apply-gating, protection, and the plan/apply split — **even when it carries
  no variables**. What it injects depends on how the consumer repo models
  environments (its IaC layout):

  | Repo layout | Env identity injected by the GitHub Environment | Mechanism |
  |-------------|--------------------------------------------------|-----------|
  | **DRY / dynamic backend** (one stack config deployed N×; backend path `…/${var.env}/${var.region}/…`) | `TF_VAR_env`, `TF_VAR_region` | OpenTofu variables drive the backend path and resources |
  | **Workspace-per-env** | `TF_WORKSPACE` | OpenTofu auto-selects (and auto-creates) the named workspace |
  | **Folder-per-env/region** (leaf per env×region, hardcoded state) | *none* | env/region are fixed by the leaf's path; each leaf owns its state |

  This is the **DRY model's** injection (`TF_VAR_env`/`TF_VAR_region`) — the
  target for real consumer repos and shipmate's internal adoption. The other
  two are proven-generalization layouts (sample repos
  `repo-example-workspaces` / `repo-example-folders`). Note the folder layout
  trades away shipmate's "add an env = GitHub Environment + tags, zero code"
  property: adding an env there means adding leaf directories (a code change).
  Membership in an environment is always by **tag**, regardless of layout.
- Protected environments (typically anything beyond the lowest-trust
  environment) carry required reviewers configured on the GitHub
  Environment itself, so approval gating is enforced by GitHub, not by
  workflow logic.
- Plan and apply are split into distinct GitHub Environments: plan jobs run
  against `<env>`, apply jobs run against `<env>-apply`. This lets apply
  carry stricter protection rules (required reviewers, wait timers) than
  plan, even though both act against the same logical environment.
- **No env names in workflow YAML — ever.** Workflow files must not
  hardcode `staging`, `production`, or any other environment name. Workflows
  discover environments dynamically from stack tags (see Tag grammar,
  below) and GitHub Environment configuration. Adding a new environment is
  purely a data change: create the GitHub Environment, then tag the stacks
  that belong to it. No workflow YAML is edited to add or remove an
  environment.

## Tag grammar

Two forms of the same concept exist, because Terramate does not permit `:`
in tag values:

- **Conceptual** form (used in documentation, discussion, and design):
  `env:<name>` and `workload:<name>`. For example, `env:staging` or
  `workload:api`.
- **On-disk** form (the literal tag value written into Terramate stack
  configuration, since Terramate forbids `:` in tags): `env/<name>` and
  `workload/<name>`. For example, the stack configuration carries the tag
  `env/staging`, not `env:staging`.

Everywhere this document or any other project document writes `env:<name>`
or `workload:<name>`, it is describing the concept; the literal value that
must appear in Terramate stack tag lists is the `env/<name>` /
`workload/<name>` form. A stack may carry several `env/*` tags at once (for
example, a shared stack tagged both `env/staging` and `env/production`)
when the same stack participates in more than one environment.

## Comment-ops

`mate <verb> <env> [tag-filter]` in a PR comment drives a manual, pre-merge
apply. The grammar is strict and anchored — the whole comment line must match
one regex, and the parsed values are never interpolated into a shell. `apply`
is the only active verb; `plan` and `destroy` are reserved (recognized and
rejected with a "reserved" message) so the grammar does not need to change
shape when those verbs are implemented.

A parsed `mate apply <env>` command is authorized only when **all** of the
following hold, checked in order, each with its own actionable rejection
reason:

- the commenter is a member of the configured approvers team (checked via a
  short-lived GitHub App installation token, `members:read`);
- the pull request is mergeable;
- the pull request has an approving review outstanding (the latest review per
  reviewer wins; any `CHANGES_REQUESTED` blocks);
- a reviewed plan exists for the pull request's **current** head SHA (the
  most recent successful preview run whose head matches; a plan for an older
  head means new commits landed since — stale, re-plan required).

The GitHub App used for comment-ops carries exactly this permission set:
`actions: write`, `pull_requests: write`, `contents: read`, `members: read`.
It has **no** `checks` permission and **no** `issues` permission — the App
exists only to mint a `workflow_dispatch` token (events created with the
default `GITHUB_TOKEN` never trigger other workflows, so a private App is the
only way to kick off the apply workflow from a comment) and to read team
membership for authorization. Apply checks are created and completed by
`apply.yml`'s own `GITHUB_TOKEN` (the shared `github-actions` identity), never
by the App — check runs are only updatable by the app that created them, and
every workflow in a repo shares the `github-actions` identity, so keeping
check writes on `GITHUB_TOKEN` keeps that identity consistent across the
pre-merge and post-merge paths.

`mate apply` and `deploy.yml` share the same per-env, per-stack
`apply-<env>-<stack>` concurrency group, so exactly one apply ever runs
against a given stack × environment at a time, regardless of whether it was
triggered by a pre-merge comment or a post-merge push.

## Consumption

- Consuming repositories and workflows pin every shipmate action **by
  commit SHA**, never by a tag or branch name (for example,
  `uses: <owner>/shipmate/actions/state@<full-commit-sha>`, not `@v1` or
  `@main`). This guarantees that a workflow's behavior cannot change
  without an explicit, reviewed bump of the pinned SHA in the consuming
  repository.
- `.github/workflows/` is protected by a `CODEOWNERS` entry, so changes to
  workflow files (including pin bumps) require review from the designated
  owners before merge.

## Fan-out

- One unit of work is one stack × one environment. A repository with N
  stacks and M environments (accounting for which stacks are tagged into
  which environments) fans out into up to N×M plan units and N×M apply
  units, each with its own check (see Check names, above).
- Plans fan out flat: all applicable plan units for a pull request run
  concurrently, with no ordering dependency between them.
- Applies run in waves: the `after` relationships between Terramate stacks
  form a DAG, and applies execute in topological levels of that DAG — all
  units at one level must complete before the next level's units start —
  so that a stack's applies only wait on the specific stacks it actually
  depends on, not on the entire fan-out.

## Apply-match fingerprint

Each plan stores a fingerprint (`fingerprint.txt`, artifact `external_id` on the
apply check): `sha256` over the sorted JSON of every **non-empty** `TF_VAR_*`
environment variable (name→value) **plus `TF_WORKSPACE` when it is set**.
Ephemeral credential vars (`AWS_*`, etc.) are excluded. A set-but-empty
`TF_VAR_*` is excluded from the payload, so it now hashes identically to that
variable being absent altogether — a flavor that injects nothing and a flavor
that injects an empty string for the same name fingerprint the same way.
`TF_WORKSPACE` is included because it is the workspaces-flavor env identity and
is not a `TF_VAR_*`; without it two environments of a workspaces stack would
fingerprint identically and an apply could match the wrong environment's
reviewed plan. plan-cell and apply-cell use a byte-identical algorithm
(`scripts/plan-classify`). On mismatch, apply fails safe and reports differing
variable **names** only — never values.

## Terramate safeguards

Terramate ships four default-on safeguards that run before `terramate run` /
`terramate script run` (not before `list` / `generate` / `experimental`).
shipmate applies a **specific reviewed SHA** — the plan artifact reviewed on the
pull request — which on the merge-deploy path is legitimately **behind `main`**
(the squash-merge drops the PR-head SHA from `main`). Exactly one safeguard is
incompatible with that model; the engine disables it and keeps the rest:

| Safeguard | Policy | Rationale |
|---|---|---|
| `git-out-of-sync` | **disabled** | shipmate applies a chosen reviewed SHA that is legitimately behind `main`; remote-freshness is the wrong assertion for the exact-plan model. |
| `git-untracked` | kept | A genuinely unexpected untracked file must still block. shipmate's own artifacts are gitignored (below). |
| `git-uncommitted` | kept | A real dirty tree must block; gitignored artifacts are not tracked-file changes. |
| `outdated-code` | kept | Catches hand-edited / stale generated `.tf`, complementing the preview codegen check. |

**Mechanism (engine-controlled).** The three `terramate script run` sites —
`plan-cell`, `apply-cell`, `drift-cell` — pass `--disable-safeguards=git-out-of-sync`
on the invocation. The policy is versioned in the engine actions (pinned by SHA);
consumers get the correct policy for free by pinning, and never set it in their
own `terramate.config`. The engine never disables via the meta `git` or `all`
keywords (either would silently drop `outdated-code` / `git-untracked` /
`git-uncommitted`).

**Consistency invariant.** The disabled-safeguard set is identical across
`plan-cell`, `apply-cell`, and `drift-cell` — exactly `{git-out-of-sync}`. A
drift between the three cells is a defect (guarded by a test, like the TF_VAR
fingerprint).

**Consumer gitignore requirement.** Because `git-untracked` and
`git-uncommitted` stay live, a consuming repository **must gitignore** the
artifacts shipmate materializes in the working tree during a run — the reviewed
plan (`*.otplan`), the fingerprint (`fingerprint.txt`), and the flavor's state
path. An ungitignored artifact, or a genuinely dirty tree, then still fails
loud (by design) rather than producing a silent wrong apply.

## Env apply order

A repository may declare a partial order over its GitHub Environments so that
one environment's stacks fully apply before another's — for example, "`eu`
fully green, then `us`." The order is a Terramate global,
`global.shipmate.env_order`: a map from an environment name to the list of
environments that must complete their applies first (its predecessors). An
environment absent from the map, or the whole global absent, is unordered
relative to everything else.

The merge-deploy path topologically sorts this map into **env-levels**
(level 0 = no predecessors, or not listed at all): all pending applies whose
environment falls in level 0 run to completion (respecting the existing
stack-wave DAG within that level) before any env-level-1 apply starts, and so
on. A failure anywhere in an env-level skips every successor level's applies
for that deploy run — the failed environment's stacks stay pending, and
downstream environments are not touched until it is fixed and re-run.
`MAX_ENV_LEVELS` is `4`; an env-order graph that would span more levels than
that fails loud rather than silently truncating.

Targeted applies (`mate apply <env>`) act on a single environment and skip
env-level ordering entirely — there is nothing to order across.

The engine ships this as a reusable, parameterized workflow
(`.github/workflows/apply-env-level.yml`) that a consuming repo's `deploy.yml`
calls once per env-level, passing that level's pre-computed wave matrix; the
workflow itself still fans applies out stack-wave by stack-wave exactly as
described above (see Fan-out).

## OpenTofu note

OpenTofu reserves the variable name `version` as a meta-argument; it cannot
be declared as an input variable in a module or root configuration. Sample
stacks in this project therefore use `app_version` wherever a version
string for the deployed workload needs to be passed through as a
`TF_VAR_*`/OpenTofu variable, never `version`.
