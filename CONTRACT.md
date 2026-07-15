# CONTRACT

This document is the naming, environment, tag-grammar, and pinning contract
that every shipmate action and every phase in this project must follow. Where
a value is marked verbatim, it must be used exactly as written — these
strings are parsed by other parts of the system (check-name matching,
comment-ops, tag-based stack selection) and are not free-form prose.

## Check names

Every plan and apply unit of work reports as its own GitHub check, using
these names verbatim:

- `plan / <env> / <stack>`
- `apply / <env> / <stack>`

`<env>` and `<stack>` are placeholders substituted with the actual
environment name and stack name for that unit of work (for example,
`plan / staging / network`).

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

## OpenTofu note

OpenTofu reserves the variable name `version` as a meta-argument; it cannot
be declared as an input variable in a module or root configuration. Sample
stacks in this project therefore use `app_version` wherever a version
string for the deployed workload needs to be passed through as a
`TF_VAR_*`/OpenTofu variable, never `version`.
