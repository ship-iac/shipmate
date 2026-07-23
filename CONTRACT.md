# CONTRACT

This document is the naming, environment, tag-grammar, and pinning contract
that every shipmate action and every consuming repository must follow. Where
a value is marked verbatim, it must be used exactly as written — these
strings are parsed by other parts of the system (check-name matching,
comment-ops, tag-based stack selection) and are not free-form prose.

## Check names

Every plan and apply unit of work reports as its own GitHub check, using
these names verbatim:

- plan job check-run: `<stack> / <env>`
- apply check-run: `apply / <env> / <stack>`

`<env>` and `<stack>` are placeholders substituted with the actual
environment name and the Terramate **stack path** (as emitted by
`terramate list` / `experimental run-graph`, e.g. `stacks/network`) for that
unit of work (for example, `stacks/network / staging` and
`apply / staging / stacks/network`). The check name uses the stack **path**,
never a display name — so the code that *creates* the apply check
(`plan-cell`), *completes* it (`apply-cell`), and *filters the still-pending
queue* (`deploy-detect`, which only ever has the path) all reconstruct the
identical name from the one value they share.

The plan check has **no verb prefix**: it is the plan matrix job's own
auto-generated check-run, whose name is the job's `name:` (`<stack> / <env>`).
The consuming workflow is named `shipmate · plan`, so GitHub's UI already
shows the verb — `shipmate · plan / <stack> / <env>`. The apply check
**keeps** its `apply / ` verb prefix: `plan-cell` creates it pending on the
**same** head SHA that carries the plan job's check, so that prefix is the
disambiguator that keeps the two apart — and keeps `apply-gate` /
`gate-refresh`, which select the pending-apply queue by the `apply / ` prefix,
from ever picking up a plan check. For the same reason `build-matrix` rejects
a stack whose path is exactly `apply` — its plan check `apply / <env>` would
fall inside the apply-check namespace.

In addition to the per-unit checks, one aggregate **commit status** rolls up
the full fan-out into a single required status, named verbatim:

- `shipmate / gate`

Branch protection rules should require `shipmate / gate`, not the
individual per-unit checks, so that the set of required checks does not
need to be edited every time a stack or environment is added or removed.

The gate is a commit status rather than a check-run deliberately: a check-run
is bound to a check-suite, and an imperatively-created one attaches to an
arbitrary suite when a commit carries more than one plan run (a draft→ready
transition or a rapid re-push spawns two runs = two suites). The merge
evaluator then reads the live suite, finds no gate there, and blocks the PR
forever while the green gate sits in the stale suite. A commit status is
commit-scoped and immune. (The per-unit `plan`/`apply` checks stay check-runs;
they are not required, so a stale-suite copy is only cosmetic.)

`shipmate / gate` is created (and refreshed on every plan run) by the
`summary` action, and is completed to success by whichever of these happens
first:

- the pre-merge apply path (`gate-refresh`, called from the apply
  workflow's summary job) once **every** `apply / <env> / <stack>` check on
  the PR head is complete — a targeted `shipmate apply <env>` of only some
  environments leaves the gate pending;
- the post-merge deploy, which completes the gate on the merged PR's head
  SHA after its env-level applies finish.

When apply-cell completes an `apply / <env> / <stack>` check, it completes only
the check-run ids that already existed for that name **before its apply began**.
A plan re-run can create a fresh duplicate apply check; a duplicate created
*mid-apply* is therefore left pending (its plan was not applied by this run),
while duplicates that predate the apply are all completed so the gate never
sticks. This keeps `shipmate / gate` from greening on a plan the apply
never used.

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

`shipmate <verb> [env] [tag-filter]` in a PR comment drives a manual, pre-merge
apply. The grammar is strict and anchored — the whole comment line must match
one regex, and the parsed values are never interpolated into a shell. `apply`
is the only active verb; `plan` and `destroy` are reserved (recognized and
rejected with a "reserved" message) so the grammar does not need to change
shape when those verbs are implemented.

The env is optional for `apply`. A targeted `shipmate apply <env>` applies one
environment; a bare `shipmate apply` applies **every** environment that has a
reviewed plan for the current PR head, in `env_order` env-levels (see Env
apply order, below), **except** environments listed in the Terramate global
`global.shipmate.explicit_envs`. Explicit environments (typically production)
must always be named: their `apply / <env> / <stack>` checks simply stay
pending under a bare apply — so `shipmate / gate` keeps gating the
merge — until someone runs `shipmate apply <env>` for them. An absent global
(or `[]`) means a bare apply targets everything. Malformed `explicit_envs`
shapes (not a list of strings) fail loud, like `env_order`.

A parsed `shipmate apply <env>` command is authorized only when it satisfies
**apply requirements** — named, Atlantis-style, checked in order, each with
its own actionable rejection reason:

- **shipmate team**: the commenter is a member of the configured approvers
  team (checked via a short-lived GitHub App installation token,
  `members:read`);
- **mergeable**: the pull request is mergeable;
- **approved**: the pull request has an approving review outstanding (the
  latest review per reviewer wins; any `CHANGES_REQUESTED` blocks);
- **undiverged**: a reviewed plan exists for the pull request's **current**
  head SHA (the most recent successful plan run — the automatic plan run on
  pull-request open, autoplan — whose head matches; a plan for an older head
  means new commits landed since — stale, re-plan required).

A bare `shipmate apply` is authorized exactly once, by the same four apply
requirements — one authorization decision covers the whole multi-environment
run. Both forms
dispatch the consumer's single `apply.yml` wrapper; its optional `environment`
input selects the path (set → targeted, empty → bare). Both share the same
App-minted `workflow_dispatch` mechanism and the same per-env
`apply-<env>-<stack>` concurrency groups.

The GitHub App used for comment-ops carries exactly this permission set:
`actions: write`, `pull_requests: write`, `contents: read`, `members: read`.
It has **no** `checks` permission and **no** `issues` permission — the App
exists only to mint a `workflow_dispatch` token (events created with the
default `GITHUB_TOKEN` never trigger other workflows, so a private App is the
only way to kick off the apply workflow from a comment) and to read team
membership for authorization. Apply checks are created and completed by the
apply workflows' own `GITHUB_TOKEN` (the shared `github-actions` identity),
never by the App — check runs are only updatable by the app that created them,
and every workflow in a repo shares the `github-actions` identity, so keeping
check writes on `GITHUB_TOKEN` keeps that identity consistent across the
pre-merge and post-merge paths.

`shipmate apply` and `deploy.yml` share the same per-env, per-stack
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

## Runner prerequisites

- shipmate's actions are composite actions: their steps run under `bash`
  and call standard-library-only Python scripts, `git`, `curl`, `jq`, `openssl`,
  and the `gh` CLI. A runner must therefore provide: `bash`, `python3`
  (Python ≥ 3.11), `git`, `curl`, `jq`, `openssl`, and `gh`.
- Every GitHub-hosted Ubuntu image satisfies this, including the minimal
  `ubuntu-slim` image. Self-hosted runners must preinstall these tools.
- The Python scripts have **no third-party dependencies** — nothing is
  `pip install`ed at runtime, so no Python setup step (or network access
  to a package index) is required or performed.
- Terramate and OpenTofu are **not** assumed to be on the image: the
  `setup` action installs the pinned versions declared by the consuming
  repository (`TERRAMATE_VERSION` / `TOFU_VERSION`).

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

## Plan artifacts

Each planned stack × environment uploads its reviewed plan under a name built
verbatim as:

- `plan.<env>.<slug>`

where `<env>` is the environment name and `<slug>` is the Terramate stack
**path** with every `/` replaced by `-` (e.g. `stacks/app` → `stacks-app`, so
`(stacks/app, dev-eu)` → `plan.dev-eu.stacks-app`). plan-cell creates it,
apply-cell downloads it, and apply-detect matches it — all three **construct**
the name forward from the `(env, slug)` pair; **no component reverse-parses it**.

The delimiter is `.` and the environment comes first on purpose. Terramate tag
values (the source of every env name) cannot contain `.`, so the first `.`
after the `plan.` prefix is always the env↔slug boundary. This makes the name
unambiguous across all `(slug, env)` pairs — unlike the earlier
`plan-<slug>-<env>` form, where `-` appears in both fields and
`(stacks/app-dev, eu)` and `(stacks/app, dev-eu)` both rendered
`plan-stacks-app-dev-eu`, letting apply-detect enrol the wrong stack into a
wave. A slug may itself contain `.` (a path character); that is harmless
because the name is only ever built forward, never split. Two distinct stack
paths that slug to the same value still collide by construction and fail loud
in apply-detect (rename so the path→`-` slug is unique).

This naming contract is breaking for any in-flight plan artifacts: land the
change when no applies are mid-flight. It also spans two consumer workflow
files pinned independently — `plan.yml` pins `plan-cell` (the uploader) and
`apply.yml` pins the engine's reusable apply workflows, which pin
`apply-cell`/`apply-detect` (the downloader/matcher) internally. Bump both
pins **together** when adopting a build that changes this name: a partial
bump (uploader on the new name, downloader on the old, or vice versa) makes
every apply fail its reviewed-plan download fail-safe until the pins agree.

## Plan comment

The `summary` action maintains exactly one sticky comment per pull request,
identified by the HTML marker written verbatim as the comment's first line:

- `<!-- shipmate:summary -->`

The comment is edited in place on every plan run (comment lookup is
marker + `github-actions[bot]` author), so GitHub's comment revision history
doubles as the audit trail of previous plans for the PR.

Structure, in order: an overview table (one row per planned stack ×
environment: verdict emoji — 🟢 no changes / 🟡 changes / 🔴 contains
destroys — add/change/destroy counts, and a link to that cell's
`<stack> / <env>` plan-job check run), then one `<details>` section per
**changed** cell containing the rendered plan inside a `diff`-tagged code
fence (change signs moved to column 0; `~` mapped to `!`). Cells with no
changes get a table row only. Check links are built **forward** from the
cell's `(stack-path, environment)` pair using the check-name grammar above;
when the check run cannot be resolved, the link degrades to the workflow-run
URL.

GitHub caps issue-comment bodies at 65,536 characters. The comment is built
to a smaller budget: each changed cell's section degrades, in order, full
plan → truncated plan (cut at a line boundary, with a link to the check run
carrying the full text) → link-only. The overview table is never dropped.
If even the table alone cannot fit the cap, the summary fails loud rather
than posting a truncated table. Plan text is emitted only inside a backtick
fence computed to be longer than any backtick run in the text —
author-controlled plan output cannot escape the fence.

The data feeding the comment ships in the per-cell artifact
`cell-summary.<env>.<slug>` (same dot-delimited, env-first grammar as
`plan.<env>.<slug>`: the name is built forward from the `(env, slug)` pair
exactly like the plan artifact, never reverse-parsed). Consumers download it
with the glob pattern `cell-summary.*`. It contains verbatim:

- `cell.json` — keys `stack` (display name), `stack_path` (Terramate stack
  path, feeds the check-name construction), `environment`, `add`, `change`,
  `destroy` (integers), `changed` (boolean); written by `plan-cell` at plan
  time from `scripts/plan-classify` output — the summary never re-parses
  plan text.
- `plan.txt` — the `tofu show -no-color` rendering of the reviewed plan.

`plan-cell` (writer) and `summary` (reader) are pinned by the same SHA in a
consumer's `plan.yml`, so the schema upgrades atomically; the summary
fails loud on a `cell.json` missing schema keys rather than rendering around
pin skew.

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

## Plan artifact encryption

The reviewed machine plan file (`stack.otplan`) can be encrypted at rest in the
uploaded artifact. When the consumer sets the optional `plan-passphrase` input
on `plan-cell` (in `plan.yml`), the engine encrypts the plan before upload
using a single symmetric cipher: `openssl enc -aes-256-ctr -pbkdf2 -salt`,
passphrase supplied via `-pass env:` (never on the command line). `apply-cell`
decrypts it after download on **every** apply path: all three paths pass it
as the optional `SHIPMATE_PLAN_PASSPHRASE` secret into the reusable
`apply-env-level.yml` workflow — via the engine `deploy.yml` for the
merge-deploy path, via the engine `apply-all.yml` for the bare form, and via
the engine `apply.yml` for the targeted form. Consumers set the
repo/environment secret `SHIPMATE_PLAN_PASSPHRASE` and forward it with
`secrets: inherit` in their `deploy.yml` and `apply.yml` wrapper
workflows.

- **Backward compatible.** An empty/unset `plan-passphrase` leaves the plan
  plaintext and the uploaded bytes byte-identical to a no-encryption run.
- **Fail-safe on mismatch.** apply-cell refuses to proceed rather than apply the
  wrong thing: a plaintext artifact when a passphrase is configured, or an
  encrypted artifact (`Salted__` magic header) when none is, both fail loud with
  a re-plan / set-the-secret instruction. A **wrong** passphrase is not detected
  at decrypt (AES-CTR is unauthenticated and decrypts to garbage without error);
  the exact-plan invariant catches it — `tofu apply` rejects the garbage plan and
  the apply check stays pending.
- **Scope: the machine plan file only.** `fingerprint.txt` is a hash and stays
  plain. The rendered plan `plan.txt` — in the `cell-summary` artifact and in the
  PR sticky comment / check-run text — **stays plaintext**: it is the
  deliberately-public reviewer view. A consumer with secret-bearing plans must
  use provider-level `sensitive` marking so those values are redacted in the
  rendered output; encryption alone does not hide them from anyone who can read
  the PR or download the `cell-summary` artifact.
- **Both sides must agree.** `plan-cell` (encrypt) and `apply-cell` (decrypt) are
  pinned independently (`plan.yml` vs `apply.yml`); the passphrase and the
  engine SHA must match on both. A mismatch surfaces as the fail-safe above, not
  a silent wrong apply.

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
| `outdated-code` | kept | Catches hand-edited / stale generated `.tf`, complementing the plan codegen check. |

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

Targeted applies (`shipmate apply <env>`) act on a single environment and skip
env-level ordering entirely — there is nothing to order across.

A bare `shipmate apply` is the pre-merge equivalent of the merge-deploy path: it
buckets the pending applies of every non-explicit environment into the same
env-levels and applies level 0 fully before level 1, with the same
failure-skips-successor-levels rule. An environment excluded as explicit
keeps its position in the order: environments that do not depend on it run
normally at their own level, while environments ordered (transitively) after
an unapplied explicit environment are skipped with a notice — their ordering
precondition cannot be met in that run, exactly like a failed predecessor
level. Completed cells skip idempotently, so re-commenting `shipmate apply`
resumes where the previous run stopped.

The engine ships this as a reusable, parameterized workflow
(`.github/workflows/apply-env-level.yml`) that the engine's own `deploy.yml`
and `apply-all.yml` reusable workflows call once per env-level, passing that
level's pre-computed wave matrix; the workflow itself still fans applies out
stack-wave by stack-wave exactly as described above (see Fan-out).

The engine ships the merge-deploy path as the reusable workflow
`.github/workflows/deploy.yml` (deploy-detect → env-levels 0..3 via
`apply-env-level.yml` → gate completion + optional Slack notify), the
bare-apply path as `.github/workflows/apply-all.yml` (detect → env-levels
0..3 via `apply-env-level.yml` → gate refresh + result comment), and the
targeted path as `.github/workflows/apply.yml` (single-env detect → one
`apply-env-level.yml` call → gate refresh + result comment). A
consuming repo carries two thin wrappers: `deploy.yml` (`on: push` to the
default branch; passes only its flavor's `state_suffix`) and `apply.yml`
(`workflow_dispatch`; its optional `environment` input routes to the targeted
or bare engine workflow).

## OpenTofu note

OpenTofu reserves the variable name `version` as a meta-argument; it cannot
be declared as an input variable in a module or root configuration. Sample
stacks in this project therefore use `app_version` wherever a version
string for the deployed workload needs to be passed through as a
`TF_VAR_*`/OpenTofu variable, never `version`.
