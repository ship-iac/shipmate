# CONTRACT

This document is the naming, environment, tag-grammar, and pinning contract
that every shipmate action and every consuming repository must follow. Where
a value is marked verbatim, it must be used exactly as written — these
strings are parsed by other parts of the system (check-name matching,
comment-ops, tag-based stack selection) and are not free-form prose.

## Check names

Every plan and apply unit of work reports as its own GitHub check, using
these names verbatim:

- plan job check-run: `plan / <env> / <stack>`
- apply check-run: `apply / <stack> / <env>`

`<env>` and `<stack>` are placeholders substituted with the actual
environment name and the Terramate stack path (as emitted by
`terramate list` / `experimental run-graph --label stack.dir`, e.g.
`stacks/network`) for that unit of work (for example,
`plan / staging / stacks/network` and
`apply / stacks/network / staging`). Both lead with their verb. The plan check
is a job's name and ends in the stack, so the cells of one environment differ in
their last segment, and every cell job name (plan, drift, apply wave, unlock)
follows the same `<env> / <stack>` order. The apply check keeps
`apply / <stack> / <env>` on purpose: it is the App's own check, built by
`apply-gate.check_name`, not a job's name, so the two orders differ by design.
The check name uses
the stack path, never a display name — so the code that *creates* the apply
check (`pending-checks`, run by `actions/summary`),
*completes* it (`scripts/apply-complete`, run by `actions/apply-complete` in
`apply-env-level.yml`'s `complete` job), *filters the still-pending queue*
(`deploy-detect` / `apply-detect` / `unlock-detect`, which
only ever have the path), and *reports it* (`apply-comment`) all reconstruct the identical name
from the one value they share.

The engine creates no plan check: it is the plan matrix job's own
auto-generated check-run, whose name is the job's `name:` (`<env> / <stack>`),
and GitHub names a called workflow's check runs `<caller job> / <callee job>`.
The consumer's plan-calling job is named `plan`, so the check is
`plan / <env> / <stack>`; the same rule makes the engine plan workflow's
summary job report as `plan / summary`. `scripts/apply-gate` builds the cell
job name (`cell_job_name`) and the plan check name (`plan_check_name`) for
every reader that looks one up.

That job name is a contract element a consumer must match. `shipmate doctor`
reports a plan-calling job carrying another name; until it is fixed,
`summary-comment` — which resolves each cell line's plan link by an exact
`plan / <env> / <stack>` lookup across every check run on the head SHA —
finds no match and every `plan` link falls back to the workflow-run URL.

The consumer's `shipmate.yml` gates seven jobs on the event (§Post-plan
topology), so every run also carries one one-segment check-run per job that did
not run, named by that job's display name — `release`, `comment`, `deploy`,
`apply`, `auto`, `unlock` on a plan run — with conclusion `skipped`. They are display
artefacts of the file's shape, and nothing functional depends on them:
`shipmate / gate` is the required check, `scripts/summary-comment` resolves plan
links by an exact three-segment name, and `scripts/mirror-checks` copies only
names beginning `plan / `. `shipmate doctor`'s annotation harvest is the
one reader that does see them — it takes every `github-actions` run on the
commit — and takes nothing from them: a skipped run carries no annotations, and
it counts as completed, so it holds no all-clear back.

The apply check keeps its `apply / ` verb prefix: it is created pending (by
`actions/summary`) on the same head SHA that carries the plan job's check, and
it is what keeps `apply-gate` / `scripts/gate-status`, which select the pending-apply
queue by the `apply / ` prefix, from ever picking up a plan check.

The apply-env-level wave job is named like the plan cell job, `<env> / <stack>`,
and two scripts find a cell's wave job by that name: `apply-gate.is_cell_job`
matches `L<n> / <env> / <stack>`, the env-level job of `apply.yml` or
`deploy.yml` and its cell job, as the whole job name or as a suffix after a
` / ` boundary, since the consumer's calling job prepends its own name. The
`L<n>` segment keeps out any other job whose name ends in ` / <env> / <stack>`,
such as `apply / detect` for env `apply` and root stack `detect`. `scripts/apply-comment`
(see Apply result comment, below) resolves each cell line's log link this way,
building the name forward from the row's stack and environment. No match falls
back to the workflow-run URL. The names and the matcher are locked together by
two source-derived tests, `test_apply_comment.py::test_wave_job_name_matches_the_cell_job_grammar`
(every wave job's name, and the matcher on a nested name) and
`test_apply_comment.py::test_every_env_level_job_is_named_for_its_level` (every
`L<n>` name in `apply.yml` and `deploy.yml`), because a rename of either would degrade
every link to the run URL silently — that fallback being the designed behaviour, no
observable would fail. A rename that leaves no wave job matching also fails
`apply-complete`, which retries the listing and then fails the run.

`scripts/apply-complete` is the second reader: given the pre-flight snapshot of
check ids a run may complete, it reads each snapshotted `apply / <stack> / <env>`
check name back to its stack and environment (`apply-gate.parse_check_name`; an
environment name holds no ` / `) and matches that cell's wave job in the same
run's job-name listing with the same matcher, to decide which cells the run's
own job conclusions actually earned. When some cells still match, a rename of
the apply-env-level job's `name:` would break this reader too with only a
`::warning::` naming the unmatched cells (the job stays green) — not degrading to
a fallback URL, but leaving the unmatched cells' snapshotted checks pending
forever.

Job display names in the apply/deploy path nest: a called reusable workflow's
job displays as `<caller job> / <callee job>`, applied at every level, and GHA
cannot suppress a level. The apply leaf is therefore three deep, e.g.
`deploy / L0 / <env> / <stack>`. The intermediate names are kept short
(`L0`..`L3` for env-levels in `apply.yml` / `deploy.yml`); the consumer's
calling job supplies the verb as the outermost segment (`deploy` on the deploy
path, `apply` on a dispatched apply). Its file is named `shipmate`, so the
pull request's checks UI renders that workflow name and then the job path
(`shipmate / plan / facts`); the check-run names the engine matches on start
after the workflow name. The leaf and the `L<n>` level name are
load-bearing (`is_cell_job` requires both); the outermost name is
display-only, and the job ids are what `needs:` refers to.

In addition to the per-unit checks, one aggregate commit status rolls up
the full fan-out into a single required status, named verbatim:

- `shipmate / gate`

Branch protection rules should require `shipmate / gate`, not the
individual per-unit checks, so that the set of required checks does not
need to be edited every time a stack or environment is added or removed.

`shipmate / gate` is the only required check — it is matched by exact string
in a repository ruleset. The plan checks' names come from the plan-calling
job's name rather than being written anywhere: the engine plan workflow's own
jobs report as `plan / facts`, `plan / detect` and `plan / summary`. A job's
check run is always created by the GitHub Actions app, so its name cannot say
which tool produced it; the checks list says it by rendering the workflow name
`shipmate` before every job's check run (`shipmate / plan / detect`), while the
name the engine matches on starts at `plan / `. These are not required checks,
and nothing in the engine reconstructs the non-cell ones.

Everything in the check/status namespace is ASCII and slash-delimited, which
is GitHub's own convention for status contexts (`ci/circleci`), and — for
`shipmate / gate` specifically — the property that matters most: the one
shipmate string an operator types by hand into a ruleset must be typeable and
free of lookalike characters. A required context that differs from the posted
one by an invisible character is never satisfied, so every pull request is
unmergeable while the status itself renders green.

The middot is reserved for `shipmate.yml`'s `run-name` (`shipmate · plan`,
`shipmate · comment`, `shipmate · deploy`, and the verb of a dispatched run) — a
run title GitHub renders and nothing matches on, where it says which event or
verb this run serves. That file's own `name:` is plain `shipmate`. The
published drift fence sets no `run-name`, so a sweep's run is titled by the
file's own `name:` (`shipmate drift` in the published fence).

The gate is a commit status rather than a check-run deliberately: a check-run
is bound to a check-suite, and an imperatively-created one attaches to an
arbitrary suite when a commit carries more than one plan run (a draft→ready
transition or a rapid re-push spawns two runs = two suites). The merge
evaluator then reads the live suite, finds no gate there, and blocks the PR
forever while the green gate sits in the stale suite. A commit status is
commit-scoped and immune. (The per-unit `plan`/`apply` checks stay check-runs;
they are not required, so a stale-suite copy is only cosmetic.)

`shipmate / gate` is created (and refreshed on every plan run) by the
`summary` action, or held by `plan.yml`'s `gate-hold` when the per-head group
evicts a pending `summary`, and is completed to success by whichever of these happens
first, each through `scripts/gate-status` and one at a time per head
(§Comment-ops, "The aggregate gate"):

- the plan run's own `summary`, right after it writes `pending`, when it
  published no queued apply check and every `apply / <stack> / <env>` check on
  the PR head is already complete (an apply's refresh ran before that write);
- the pre-merge apply path (`gate-refresh`, called from the apply
  workflow's summary job) once every `apply / <stack> / <env>` check on
  the PR head is complete — a targeted `shipmate apply <env>` of only some
  environments leaves the gate pending;
- the post-merge deploy, which completes the gate on the merged PR's head
  SHA after its env-level applies finish.

When `apply-env-level.yml`'s `complete` job completes an `apply / <stack> / <env>`
check, it completes only the check-run ids `actions/apply-snapshot` recorded for
that name in the `snapshot` job, before the first wave.
A plan re-run can create a fresh duplicate apply check; a duplicate created
*after the snapshot* is therefore left pending (its plan was not applied by this run),
while duplicates that predate the apply are all completed so the gate never
sticks. This keeps `shipmate / gate` from greening on a plan the apply
never used.

## Env model

- Every logical environment (for example `staging`, `production`) is carried by
  GitHub Environments named after it: `staging-plan` and `staging-apply` by
  default, or a single `staging` under `split: false` (both namings below). The
  Environment is always the unit of binding, apply-gating, protection, and the
  plan/apply split; the engine reads no identity variable from it. What a cell
  injects depends on how the consumer repo models environments (its IaC layout):

  | Repo layout | Env identity injected | Mechanism |
  |-------------|--------------------------------------------------|-----------|
  | **Variable-driven backend**, `layout: tf_vars` (one stack config deployed N×; backend path `…/${var.env}/${var.region}/…`) | `TF_VAR_env`, `TF_VAR_region` | OpenTofu variables drive the backend path and resources |
  | **Workspace-per-env** | `TF_WORKSPACE` | OpenTofu auto-selects (and auto-creates) the named workspace |
  | **Folder-per-env/region** (leaf per env×region, hardcoded state) | *none* | env/region are fixed by the leaf's path; each leaf owns its state |

  This is the `tf_vars` layout's injection (`TF_VAR_env`/`TF_VAR_region`) — the
  target for real consumer repos and shipmate's internal adoption. The other
  two are proven-generalization layouts (sample repos
  `repo-example-workspaces` / `repo-example-folders`). The folder layout
  trades away shipmate's "add an env = table entry + GitHub Environment + tags,
  zero code" property: adding an env there means adding leaf directories (a code
  change). Membership in an environment is always by tag, regardless of layout.

  **The environment table supplies those values.** Every repository declares a
  `layout` in `.github/shipmate-config.yml`, and every cell takes its identity from
  that file on the repository's default branch (§Environment table). There is no
  second source.
- **One writer puts those variables in the cell's process.**
  `scripts/env-inject` writes every entry of the row's `tf_vars` into
  `$GITHUB_ENV` under the name the table resolved, in a step that runs before
  the cell's `terramate run`. It composes the consumer channels of §Consumer
  variables and secrets into that same write, and refuses any name two channels
  supply. Two sources for one name would make precedence load-bearing, so there
  is exactly one writer and no precedence rule. `terramate.config.run.env` is
  applied after it, inside `terramate run`; the `run.env` rule below says which
  names it may set.
  - **The injected names are lowercase after the prefix.** `TF_VAR_ENV` is a
    different variable from the `TF_VAR_env` OpenTofu reads, and the table's
    `vars` allowlist accepts either spelling. Nothing refuses the mis-cased one:
    plan and apply resolve it from the same table, so it hashes identically on
    both sides. The variable simply never reaches OpenTofu, and the stack runs on
    whatever default it declares for the name it does read.
  - A `tf_vars` entry holding the empty string is written through as empty rather
    than dropped, which is what the fingerprint already excludes (see
    Apply-match fingerprint, below).
  - Each cell action takes one identity input, with no default: `tf-vars`, the
    row's resolved `tf_vars` serialised with `toJSON`. Anything but a JSON
    object of strings is refused before `terramate run`; the empty object is a
    legitimate value and an omitted input is not. Each row also carries
    `role_arn`, `cred_region`, `config_path` and `env_binding`, resolved from the
    same table.
  - The consumer channels are two further inputs on the same actions:
    `github-vars` (`toJSON(vars)`, the enumeration) and `consumer-secrets` (the
    `SHIPMATE_SECRETS` envelope). Both default to empty and neither is required:
    a repository using neither channel is the normal case. That asymmetry with
    `tf-vars` is deliberate — an omitted identity is a cell with no environment
    and must fail closed, while an omitted channel is a cell with no extras.
- Protected environments (typically anything beyond the lowest-trust
  environment) carry required reviewers configured on the GitHub
  Environment itself, so approval gating is enforced by GitHub, not by
  workflow logic.
- **Plan and apply bind different GitHub Environments by default
  (`split: true`).** For a logical env `<env>`, plan jobs (and each drift sweep covering it) bind
  `<env>-plan`, apply jobs bind `<env>-apply`. This lets apply carry stricter
  protection rules (required reviewers, wait timers) than plan, even though both
  act against the same logical environment. "The apply environment" below means
  `<env>-apply` by default and the bare `<env>` under `split: false`.
- **Detect resolves the binding, and every cell job reads it from its row.**
  `scripts/env-config` computes `env_binding` from the default-branch table
  when detect stamps each matrix row, and the eight wave jobs of
  `apply-env-level.yml`, engine `unlock.yml`'s `unlock` job, engine `plan.yml`'s
  `plan` job and engine `drift.yml`'s `drift` job all bind it with the same
  expression:

  ```yaml
  environment: ${{ matrix.env_binding }}
  ```

  A row whose `env_binding` is missing, not a string, or empty refuses at
  detect, before any matrix is written: GitHub binds no environment for an
  empty `environment:`, and the cell would run outside every environment
  control.

  A repository may therefore mix modes freely — some envs shared, some split —
  and no workflow file names either.
- **A logical env may opt into one shared environment (`split: false`).**
  `split: false` in its `environments.<env>` entry makes both paths bind the
  bare `<env>` — one environment, no suffix. The price is stated in
  `docs/hardening.md` (§6 and §7–9): a protection rule on a shared environment
  gates the plan cells and every drift sweep covering it too, so the reviewer gate is
  given up rather than relocated, and plan and apply OIDC tokens become identical
  in `sub`, so no trust policy can separate them.
  - The key is read from the default branch like the rest of the file, so a
    pull request cannot move its own cells between the two namings. Changing it
    is an ordinary pull request to the default branch, under the same review as
    any other change to the table.
  - `split` must be `true` or `false`: any other value (`no`, `False`) refuses
    at detect rather than reading as split. An absent key and `split: true`
    both mean split.
  - **`-plan` and `-apply` are reserved suffixes for logical env names.** A
    logical env literally named `foo-apply` makes the naming undecidable (is an
    existing `foo-apply` that env's shared environment, or `foo`'s apply
    environment?) and binds `foo-apply-apply` on the apply path. An
    `environments.<name>` entry, a `needs` item, an `env/<name>` stack tag or a
    dispatched environment carrying the suffix refuses. `shipmate-engine` is
    reserved too, and the same four refuse it: a `split: false` entry of that
    name would bind the engine's own environment, which holds the App private
    key, and one rule for every name is simpler than a shared-only rule.
- **A binding that disagrees with the environment names is invisible to the
  fingerprint, on every layout.** Both sides derive a cell's variables from
  `matrix.environment` and the same default-branch table, so they hash
  identically whatever environment the job actually bound. The apply-match
  fingerprint pins plan and apply agreeing on values; it pins nothing about the
  binding. What catches a mis-binding is the environment pre-flight in the next
  bullet, which is unconditional on the apply-side binding, plus `shipmate
  doctor`, which reports a naming mismatch advisorily on a pull request.

  That is the reason for the symmetric `-plan` / `-apply` naming: the pre-flight
  refuses only a binding that names nothing or names an environment with no
  deployment branch policy, and under an asymmetric naming the dangerous
  direction names something: an apply wave bound to the live plan environment
  binds one that exists, and is refused only while that environment carries no
  branch policy. A cell that applies
  inside an auto-created environment holds no reviewers, no wait timer and no
  deployment branch policy, against the right code and the right state.
- **A binding that names no existing environment, or one with no deployment
  branch policy, is refused before any wave applies.** `apply-env-level.yml`'s `snapshot` job — which every apply route
  fans through, and which runs before `wave0` — reads the `env_binding` of
  every cell in the incoming matrix, lists the repository's environments once,
  and fails the run naming every binding the repository does not have, plus
  both ways to fix it: create that environment, or correct the environment's
  entry in `.github/shipmate-config.yml`. It then fails the run naming every
  binding whose `deployment_branch_policy` is null: a workflow on any branch can
  bind such an environment and reach its secrets and OIDC role. Which kind of
  policy (custom branches or protected branches) is the consumer's choice. It is
  what refuses a binding naming no environment: it compares existence and the
  presence of a branch policy, not table content, and a cell resolves its variables
  from the table whatever it bound. It runs once per `apply-env-level.yml` call,
  so an env-ordered deploy can have completed an earlier level's applies before a
  later level is refused — a partial deploy, not an unverified apply: every level
  verifies its own environments before its own waves.
  - Its own failures are fail-closed as well. A listing that could not be read,
    or one whose `total_count` exceeds the number of environments returned,
    fails the run rather than letting the applies through — a check that did not
    happen is not a passed check. A transient failure clears on a re-run of the
    workflow.
  - It costs a caller nothing, but not because it needs nothing: listing
    environments needs `actions: read` — on a private repository the default
    `GITHUB_TOKEN` gets a 403 with `checks: read` alone — and every apply route's
    call site already grants `actions: read` for its wave jobs and `complete`, so
    the `uses:` permission cap is already high enough. A hand-written wrapper
    calling `apply-env-level.yml` must grant it. No App key or App permission is
    involved.
  - Deliberately out of scope, so what it promises stays readable: the
    plan-side environments, which engine `plan.yml` and `drift.yml` bind but
    this pre-flight does not enumerate; an environment whose branch policy names
    the wrong branches, which nothing checks (`shipmate doctor` warns on a
    missing policy only); an environment with no approval rules, which `shipmate
    doctor` notes; and an environment created or deleted in the window between
    the pre-flight and the wave jobs.
- **No env names in workflow YAML — ever.** Workflow files must not
  hardcode `staging`, `production`, or any other environment name. Workflows
  discover environments dynamically from stack tags (see Tag grammar,
  below) and GitHub Environment configuration. Adding a new environment is
  purely a data change: add its entry to the environment table, create its
  GitHub Environments (`<env>-plan` and `<env>-apply`, or one bare `<env>`
  when its entry holds `split: false`), then tag the stacks that belong to it. The
  table entry merges on its own pull request, before the tags — §Adding and
  removing an environment has why the order is not optional. No workflow YAML is
  edited to add or remove an environment — every cell job binds the row's
  `env_binding`, written once, for every env. The one carve-out is `shipmate-engine` — a single fixed
  environment name, not a logical environment a consumer defines or names
  itself, that exists purely to scope the App private key to the
  default-branch ref (see `docs/github-app.md` §Key-exposure boundary).
  It appears only inside the engine's reusable workflows — `plan.yml`'s
  `summary` and `gate-hold` jobs, `comment-ops.yml`'s `ops` job, `drift.yml`'s
  `issues` job, `release.yml`'s `release` job, and the apply path (`apply.yml`'s
  `review`, `lock` and `summary`, `apply-env-level.yml`'s `complete`,
  `deploy.yml`'s `summary`). No consumer file names it: the consumer's `shipmate.yml`
  and each drift workflow file pass the key by name and bind no environment of
  their own. Each of those
  engine jobs runs at a ref the environment's default-branch policy admits —
  the base ref under
  `pull_request_target` (`closed` included), the default branch under `issue_comment` and `push`,
  a drift file's `schedule`, and a `workflow_dispatch` of
  `shipmate.yml` or a drift file at the default branch — which is what lets them declare it at all. What never
  happens is a *logical* environment name
  (`staging`, `dev-eu`) hardcoded anywhere — `shipmate-engine` is the one
  literal exception, spelled identically everywhere it appears because it
  names one fixed thing, not a per-repo variable.
- For every name in the row's `tf_vars` — the layout's derived names plus the
  environment's own `vars` — the value `tofu` receives is the table's. A
  consuming repository's `terramate.config.run.env` may read any name, and may
  set any name the row does not hold. Terramate applies `run.env` to the child
  process after the ambient environment, so an assignment to one of the row's
  names wins over whatever `env-inject` wrote — and silently: the plan/apply
  fingerprint is computed outside `terramate run`, so both sides hash the same
  correct job environment while `tofu` on both sides ran under the rewritten
  one. Cells then collapse onto one state key with plan, gate and apply all
  green. To give local runs a default, read the injected name first in the
  chain — `tm_try(env.TF_VAR_env, env.env, "dev")`. To let `run.env` own a
  variable, leave it out of the environment's `vars`.
  - What the row does not hold stays the consumer's. `TF_VAR_env` is an
    ordinary variable under `layout: folder`, and `TF_WORKSPACE` is the
    table's only under `workspace` or through a `vars` entry. Under the
    `tf_vars` and `folder` layouts, a `run.env` `TF_WORKSPACE` selects the same
    workspace at plan and apply, and the cell actions run `scripts/state-path`
    inside `terramate run` (§State backend), so the state path the cell records
    is that workspace's.
    `TF_DATA_DIR` is the same: consumer-declared, never derived by the table,
    and read inside `terramate run` by `scripts/state-path`. The export policy
    still reserves both names from GitHub variables and envelopes in every
    layout (§Export policy), so `run.env` is where a consumer sets them.
  - Each cell checks the rule before `tofu init`. In the plan, apply, drift and
    unlock cells, `scripts/env-inject` runs, after writing `$GITHUB_ENV`,
    `terramate run --no-recursive -C <stack>` with a child that prints the
    row's `tf_vars` names and their values as JSON and nothing else — never the
    whole environment, which holds the raw `SHIPMATE_SECRETS` envelope. The
    expected values stay in the `env-inject` process: `run.env` can rewrite the
    child's `SHIPMATE_TF_VARS` as easily as `TF_VAR_env`. The cell refuses when
    a name comes back different from the table's value or unset, when
    `terramate run` exits non-zero, when the child's output is not a JSON
    object, and when the step received no stack. A row whose `tf_vars` is empty
    (`folder` with no `vars`) runs nothing. The comparison is against the real
    value, so a `run.env` literal equal to it passes in that cell and refuses in
    any cell where it differs.
  - `run.env` is evaluated per stack, and a `terramate.config.run.env` block
    below the root applies to the stacks under it, so no one stack answers for
    the tree. Each cell checks its own stack, which covers every path that runs
    a cell, the dispatched and bare `shipmate apply` included. A repository
    with an overriding `run.env` therefore fails once per affected cell, not
    once at detect.

## Environment table

A repository declares its environments, their regions and the credentials they
name in `.github/shipmate-config.yml`, a YAML file read with PyYAML.
The file is required, and so is `layout`: it is the only source of a cell's
environment identity (the layout's variables), so a repository without one has
nothing for its cells to run as. The two absences refuse at the same site,
`scripts/env-config` (§Refusals) — a file absent from the default branch, and a
file that parses but declares no `layout`.

The file holds five top-level settings and no others: `layout`,
`identities`, `environments`, `max_parallel`, `command_hints`. Any other top-level
key refuses, naming the offending key and the five that are allowed. An engine refuses a key
it does not implement, so a repository moves its pin before it adds a key a newer
release introduces.

Reading it needs Python 3.12 and PyYAML, as §Runner prerequisites states.
`scripts/env-config` checks both before the parse and refuses naming the
contract clause, and the version found when the Python is older.

**The engine reads the file from the repository's default branch, never from the
branch under test.** `scripts/env-config` reads `.github/shipmate-config.yml` through
the contents API with no ref, which GitHub answers from the default branch, and
resolves each cell's environment identity and credential from what that returns.
A pull request cannot change which role
its own plan assumes, which region it authenticates against, or which workspace
it plans; changing any of those takes a merge to the default branch. The read
names `GITHUB_REPOSITORY`, which is the base repository under both pull-request
events, and a fork pull request is still refused in `detect` before it plans.

**`needs`, `apply_mode`, `review`, `max_parallel` and `command_hints` come from the
default branch too.** They are read from the same parsed mapping as the environment
table, and a branch therefore cannot reorder its own apply waves, drop its own
production exclusion, exempt itself from the review requirement, raise its own
fan-out or change what its own plan comment shows.

**The file must reach the default branch before the first plan run.** A pull
request that only *adds* it is refused, because the branch its plan is compared
against still has none. For a new consumer the file lands in the same commit as
the workflow file, on the default branch.

Every failure to read it refuses the run: a failed contents read (the file
absent on the default branch, or a token without contents read), a file over
1 MB, and a body that is not valid YAML — the parser's message, with its line
and column, is surfaced as the refusal. "This repository has no file" and "the
file could not be read" cannot be told apart without reading it, so treating a
read failure as absence would hand the decision back to branch content.

### Keys

The file is read with a strings-only YAML loader: every value stays exactly as
written, so `111111111111`, `4402` and `false` need no quotes, and an account id
keeps a leading `0`. Indentation is the one structure rule.

```yaml
# .github/shipmate-config.yml
layout: tf_vars                    # tf_vars | workspace | folder
max_parallel: 10                   # cell jobs one plan or drift matrix runs at once
# command_hints: true              # end a plan comment with a changed cell with its commands

identities:
  dev:
    aws:
      account: 111111111111        # a role name below becomes arn:aws:iam::<account>:role/<name>
      plan: shipmate-plan-{workload}
      apply: shipmate-apply-{workload}
  prod:
    aws:
      apply: arn:aws:iam::4402:role/shipmate-apply   # a full ARN is used as written

environments:
  dev-eu:
    region: eu-west-1              # the layout's TF_VAR_region and the credentials step's region
    identity: dev
    workloads: [core, net-edge]    # the workload tags this environment admits
    review: false                  # applies without an approving review
  prod:
    region: eu-west-1
    identity: prod
    needs: [dev-eu]                # dev-eu fully applies first
    apply_mode: targeted           # a bare `shipmate apply` skips it
    vars:
      TF_VAR_account: 4402         # merged over the layout's TF_VAR_*
  sbx:
    region: eu-west-1
    split: false                   # one bare `sbx` Environment on both paths
```

An environment entry holds `region`, `vars`, `identity`, `workloads`,
`split`, `needs`, `apply_mode` and `review`, and nothing else. `split: false`
binds the environment
as one bare `<env>` on both paths instead of the `<env>-plan` / `<env>-apply` pair
(§Env model). `apply_mode: targeted` keeps the environment out of a bare
`shipmate apply`, and `review: false` exempts it from the review requirement
(§Comment-ops). `split` and `review` hold `true` or `false`, bare or quoted, and
default to `true`: writing `false` gives something up. `apply_mode` holds
`targeted`, `bare` or `auto` and defaults to `bare`; `auto` requires `review: false`
on the same entry.

**`command_hints: true` ends the plan comment with its commands.** It holds
`true` or `false`, bare or quoted, and is `false` when absent. When it is `true`,
a plan comment with a changed cell on a pull request that is not a draft ends
with a folded `shipmate commands` section naming `shipmate apply` and `shipmate
help` (§Plan comment). It changes no plan or apply.

**`max_parallel` caps the plan and drift matrices.** It is a whole number from
1 to 256, and 10 when absent: each plan matrix and drift sweep runs at most that
many cell jobs at once, and the rest queue, so a plan or sweep over the cap
takes longer, in batches. The cap is per matrix per run. 10 is half of the 20
concurrent jobs GitHub allows standard GitHub-hosted runners on Free (40 on
Pro, 60 on Team, 500 on Enterprise;
[Actions limits](https://docs.github.com/en/actions/reference/limits)), so one
plan or sweep leaves room for other jobs, but a plan and a drift sweep running
together can fill a Free account's pool: stagger the drift files' crons. The
default is sized for GitHub-hosted runner limits; a repository whose `runs_on`
names a larger self-hosted pool raises it. The cap also bounds how many stacks
call one cloud account's APIs at once: each cell's `tofu` runs up to 10
operations in parallel, and some provider APIs throttle per account. GitHub runs a matrix with `max-parallel: 0` unbounded, so
0 refuses, and so does anything that is not a whole number from 1 to 256.

**Apply waves and unlock are not capped.** Both bind `<env>-apply`, and GitHub
applies a bound environment's wait timer again to every batch of a capped
matrix (probed on a public repository with a wait timer; required reviewers
were not probed and are assumed to do the same), and a capped cell
queued behind a live apply's concurrency group would hold a slot the others
need. Plans and drift
sweeps bind `<env>-plan`, which carries no reviewers (§Env model). The one
exception is a `split: false` environment: it binds its bare `<env>` on plans
and drift too, so its reviewers or wait timer apply again to each batch of a
plan or sweep over the cap.

One quoting rule: a string value starting with `{`, `[`, `*`, `&`, `!` or `#`
is quoted (`"{workload}-plan"`). Unquoted, YAML reads the character as syntax:
`{` and `[` open a mapping or list, which is meant only in a reference
(`{vars: NAME}`) and in the `workloads` and `needs` lists; `*`, `&` and `!` an
alias, anchor or tag, each refused; and `#` a comment that empties the value. Only a
`vars` value may be empty, so only there is a `#`-led value not refused.

**An identity names credentials once.** `identities.<name>` holds three
fields, each optional, all under `aws`:

| Field | Holds |
| --- | --- |
| `aws.account` | the 12-digit account id |
| `aws.plan` | the plan path's role |
| `aws.apply` | the apply path's role |

- **A field is a string or a map keyed by workload**
  (`{core: 111111111111, network: 222222222222}`). A workload name, as a
  map key or a `workloads` element, follows the environment-name charset, and
  `vars`, `role` and `region` are reserved: `{vars: …}` is a variable
  reference, and a `role` or `region` key would read as a setting of the field
  rather than as a workload.
- **A role is a role name or a full ARN.** A name (`shipmate-plan`, or under an
  IAM path `ci/shipmate-plan`) becomes `arn:aws:iam::<account>:role/<name>`. A
  value starting `arn:` is used as written, which covers a third account,
  `aws-cn` and `aws-us-gov`.
- **The account rule is per workload, over both paths.** A workload whose role is
  a name on either path needs an account; one whose role is an ARN must have
  none. A name on one path and an ARN on the other refuses: write both as ARNs.
  An account map may hold keys no environment lists, because another environment
  naming the identity may list them.
- **`{workload}` is the only placeholder.** A role may carry it, and it is filled
  from the cell's `workload/<name>` tag exactly as written, and only when the
  environment's `workloads` lists that name. Any other brace in a role refuses,
  so a role under an IAM path holding a brace cannot be written.
- **No `aws.plan` means the plan path has no cloud access**, which is legal as
  apply-only access. An identity setting neither `aws.plan` nor `aws.apply`
  refuses.

**An environment names one identity** with `identity: <name>`, and the
credentials step then authenticates against the environment's `region`, which
such an entry must set. An identity varies by workload when any field is a map
or a role carries `{workload}`. `workloads` lists the workload tags the
environment admits, and it is the one membership rule:

| The environment's identity | `workloads` | A cell tagged outside the list | An untagged cell |
| --- | --- | --- | --- |
| varies by workload | required | refused at detect | no credential |
| does not vary | optional | refused at detect when a list is written | the identity's credential |
| none | refused | runs credential-free; the tag is inert | no credential |

Every workload the list names must resolve on each path the environment
consults: a listed workload missing from a role map refuses, and so does one
missing from an account map when its role is a name. A shared environment
consults `aws.apply` on both paths, so naming an identity that sets `aws.plan`
from one refuses.

In the fence above, `dev-eu` is the first row, `prod` the second and `sbx` the
third.

**The file is data.** It has no expression language: no anchors, aliases or
tags, no functions, no way to derive one entry from another. The file has two
indirections, a variable reference (§Variable references) and one placeholder,
`{workload}`; each fills a value from a closed source (a named variable; the
environment's `workloads`), and neither derives one entry from another. A repository with many environments
repeats `identity`, `workloads`, `region` and its flags on each entry,
deliberately — an audit of one environment reads one entry. There is no
`defaults` entry and no merge rule between environments: an environment holds
no credential field, the identity it names arrives whole, and an identity reaches
only the environments that name it. The cost is that one edit to an identity
retargets every environment naming it; `shipmate doctor`'s roles lines list what
each environment resolves (§Resolution).

**An indentation mistake nests a key under the previous one.** A setting
indented too far lands inside the entry above it: `layout` indented under
`identities.dev` becomes `identities.dev.layout`. The strict key checks catch it,
in several places: a misplaced `layout` always reaches the missing-`layout`
refusal, and also refuses as an unimplemented environment key, identity key or
identity field, depending on where it landed. `docs/troubleshooting.md` has the
message for each position.

**An identity is optional.** An environment naming none runs no credentials
step — which is how the three credential-free sample repositories work. A file
holding only `layout: workspace` is a complete, warning-free table.

**What each layout derives**, before the environment's own `vars` merges over it:

| `layout` | Injected |
|---|---|
| `tf_vars` | `TF_VAR_env = <environment key>`, `TF_VAR_region = <region>` |
| `workspace` | `TF_WORKSPACE = <environment key>` |
| `folder` | nothing |

`vars` accepts only `TF_VAR_*` and `TF_WORKSPACE` names and only string values.
Without that allowlist the table would be a general environment injector and
would stop describing environment identity.

### Variable references

Any string value in the file except `split`, `apply_mode`, `review`,
`max_parallel` and `command_hints` may instead name a GitHub variable:

```yaml
# .github/shipmate-config.yml
layout: tf_vars

identities:
  prod:
    aws:
      apply: {vars: PROD_APPLY_ROLE}

environments:
  prod:
    needs: [{vars: FIRST_ENV}]
    region: {vars: PROD_REGION}
    identity: prod
    vars:
      TF_VAR_account: {vars: PROD_ACCOUNT}
```

- **Shape.** Exactly `{vars: NAME}`, in flow or block form: a mapping with one
  key, `vars`, holding a string, named after GitHub's `vars` context. It is valid at every
  string position, list items and an identity map's values included, except
  `split`, `apply_mode`, `review`, `max_parallel` and `command_hints` (see Values),
  and it replaces the whole string before `{workload}` is filled. Any other
  mapping is ordinary data for the checks in §Refusals.
- **Resolution.** Every reader of the file replaces each reference with the
  variable's value before it validates anything: every detect, comment-ops'
  gate resolution, `shipmate doctor` and `scripts/onboard`. The checks then run
  on the values, unchanged. A file holding no reference reads no variable.
- **Scope.** A reference reads repository and organization variables;
  `scripts/onboard` gives a repository variable precedence over an organization
  variable of the same name, as GitHub does. The variables of a cell's
  `<env>-plan`, `<env>-apply` or shared `<env>` Environment are never read: no
  job that reads the file binds one. Of the jobs that read the file, comment-ops
  and the plan `summary` job are the two that bind `shipmate-engine`, so a
  variable of the same name on that Environment shadows the repository value in
  those two jobs, and nowhere else.
- **Values.** A resolved value is always a string. `split`, `apply_mode`,
  `review`, `max_parallel` and `command_hints` accept no reference: one refuses before it
  resolves, so the refusal never quotes the variable's value. A referenced
  `layout` whose value is not a layout refuses naming the variable, not the
  value. The name is
  uppercase (`[A-Z_][A-Z0-9_]*`), as GitHub stores it.
- **Variables only, never secrets.** A resolved value is not hidden: it lands in
  job outputs, the detect `matrix` among them, and in step logs, and
  `configure-aws-credentials` prints `role-to-assume`. Secret cell inputs travel
  in `SHIPMATE_SECRETS` (§Consumer variables and secrets).
- **Pull requests.** A pull request adding a reference is not checked by its own
  plan, because plans read the default branch's file. `shipmate doctor` on that
  pull request resolves the branch's file, reports an unset or empty variable
  as an invalid file, and lists every reference as `<key> from variable <NAME>`.
  After the merge, a missing variable refuses every run until it is set; the fix
  is a variable edit, not a pull request.
- **Authority.** A referenced value is governed by whoever GitHub permits to
  edit the repository's or the organization's variable it names, not by whoever
  can merge to the default branch. That includes a referenced `needs` item.
  Who may edit a variable is a GitHub permission setting, outside this
  contract. A variable edit takes effect on the next run.
- **Plan and apply.** A cell's role and credentials region are outside the
  apply-match fingerprint (§Apply-match fingerprint): a variable feeding either,
  changed between plan and apply, reaches the apply with no re-plan. A
  referenced `environments.<env>.vars` value is inside it, and so is
  `region` under the `tf_vars` layout, which derives `TF_VAR_region`: changing
  that variable between plan and apply refuses the apply as stale, and a re-plan
  clears it.

### Refusals

Every condition below refuses at detect, before any cell starts. A file that
loads and resolves gets one refusal naming every structural error it holds. `shipmate doctor` reports
every structural row, the ones judged on the file alone, on the pull request that
introduces it. The absent-file, runner, `tf_vars` coverage, workload-tag and
variable rows depend on the run as well as the file, so they are not structural.

| Condition | Why |
|---|---|
| `.github/shipmate-config.yml` is absent from the default branch, or unreadable | absence and a failed read cannot be told apart, so neither may be read as "this repository has no table" |
| The file is not valid YAML, or holds a duplicate key, an anchor, an alias, a tag or tab indentation | the parser's message, with its line and column, is the refusal. A duplicate key would otherwise win silently, and the file gains nothing from anchors, aliases or tags |
| The runner's Python is older than 3.12, or cannot import PyYAML | §Runner prerequisites requires both; the refusal names the missing one, and the version found |
| The table declares no `layout` | it is the only source of a cell's environment identity, and a `layout` indented under another key lands inside it rather than at the top level, so a misplaced `layout` arrives here as an undeclared one |
| `layout` is not `tf_vars`, `workspace` or `folder` | a typo would silently disable injection. A referenced `layout` is named by its variable, never its value |
| `layout: tf_vars` and a matrix environment has no entry, or an entry with no region | the layout cannot derive its variables |
| An empty `region` (`region:`, or `region: #eu-west-1`, which YAML reads as empty) | an empty region derives no `TF_VAR_region` the fingerprint can tell apart, and the credentials step needs one |
| A cell's `workload/<name>` tag its environment's `workloads` does not list | a listed workload is the only one the default branch grants a role to. One refusal names every such cell. Checked only for the cells the run plans, applies or unlocks: a completed, excluded or held cell is not refused, and neither is an untagged cell, a tag in an environment naming no identity, or one in an environment writing no list |
| An environment name outside the env-name charset, carrying a `-plan`/`-apply` suffix, or `shipmate-engine` | the name is matched against the bare logical env name from a stack's tag, so such an entry resolves for nothing, and anything it declares orders nothing. A `split: false` entry named `shipmate-engine` would bind the engine's own environment, which holds the App private key, so that name is refused as a table entry, a `needs` item, a stack tag and a dispatched environment |
| An environment key other than `region`, `vars`, `identity`, `workloads`, `split`, `needs`, `apply_mode`, `review` | catches a misspelled key and a top-level setting indented under an entry; credentials live in `identities.<name>`, never in an entry |
| `identity` that is not a string, or names no `identities.<name>` entry | the refusal lists the identities the file declares |
| An environment naming an identity with no non-empty string `region` | the credentials step authenticates against the environment's region and requires one |
| `workloads` on an environment naming no identity | without an identity a workload tag is inert, so the list would admit nothing |
| `workloads` that is not a non-empty list of distinct strings | membership is read from this list alone, so a shape it cannot mean is refused rather than guessed at |
| An environment naming an identity whose roles vary by workload, with no `workloads` | a varying identity grants a role only to a listed workload, so the environment must say which |
| A listed workload missing from the identity's `aws.plan` or `aws.apply` map, or, when its role is a name, from its `aws.account` map | the cell would resolve no role for a workload the environment admits |
| For one workload, a role name on one path and a full ARN on the other | the account rule is per workload over both paths; write both as ARNs |
| For one workload, a role name and no account | a name becomes `arn:aws:iam::<account>:role/<name>` and has no account to go in it |
| For one workload, a full ARN and an account | an ARN carries its own account, so the file would hold two for one role |
| `split` or `review` that is not `true` or `false`, or `apply_mode` that is not `targeted`, `bare` or `auto` | any other value would otherwise resolve to the default: `split: no` binds the split pair the repository believes it gave up, `apply_mode: Targeted` puts the environment on a bare `shipmate apply`, and `review: no` leaves it under review. A variable reference refuses before it resolves, even one that would resolve to a valid value, so the refusal never quotes its value |
| `apply_mode: auto` on an entry that does not hold `review: false` | an `auto` apply starts on a push and does not wait for an approving review, so an entry under review cannot take it. One refusal names both keys |
| A `split: false` environment naming an identity that sets `aws.plan` | a shared environment is one GitHub Environment, and it resolves `aws.apply` on both paths; the file would read as a read-only plan role while every plan cell assumes the apply role |
| `vars` naming anything outside `TF_VAR_*` / `TF_WORKSPACE`, or holding a non-string | see the allowlist above |
| Malformed shape | a string where a mapping is required, and the reverse |
| An identity key other than `aws`, or an `aws` field other than `account`, `plan`, `apply` | an identity holds only `aws.account`, `aws.plan` and `aws.apply`, and the refusal names them |
| An identity setting neither `aws.plan` nor `aws.apply` | it grants no credential; apply-only is legal, all-empty is not |
| An identity field that is neither a string nor a map keyed by workload, or a map value that is not a string | a field resolves to one string per cell, so no other shape has a meaning |
| A workload name, as a map key or a `workloads` element, outside the env-name charset, or `vars`, `role` or `region` | a workload name is a Terramate tag; `{vars: …}` is a variable reference, and a `role` or `region` key would read as a setting of the field rather than as a workload |
| An empty string or an empty map in an identity field | that resolves to a skipped credentials step, not to a credential |
| `aws.account` that is not 12 digits | an AWS account id is exactly 12 digits, a leading `0` included |
| A brace in a role outside `{workload}` | `{workload}` is the only placeholder |
| `max_parallel` that is not a whole number from 1 to 256, or holds a variable reference | GitHub runs a matrix with `max-parallel: 0` unbounded and fails the run on an empty or non-numeric value; the cap is written into the file, like the flags |
| `command_hints` that is not `true` or `false`, or holds a variable reference | anything else reads as set to a person and would resolve as unset; the setting is written into the file, like the flags |
| A top-level key other than `layout`, `identities`, `environments`, `max_parallel`, `command_hints` | catches a misspelled `environments`, which would otherwise yield zero environments and skip every cell's credentials step |
| Malformed `needs` | one entry point validates every field, so an ordering error refuses at detect rather than when an apply finally reads it |
| A cycle across `needs`, a self-edge included | an ordering with no first environment sorts into no levels at all, and the refusal is decidable from the file alone, so it lands with the other structural checks rather than at the apply that topologically sorts it |
| A reference to a variable that is unset or empty, whose name holds a lowercase letter, or a name that is not a GitHub variable name | §Variable references; the refusal names the key path and the variable, never a value |
| A file holding a reference, read by a step whose variables input is absent or empty | the engine did not pass `github-vars` to that step, or the repository reaches no variables at all; named as such rather than blamed on one variable |

### Resolution

The table chooses on every path; a reference in it is resolved from a repository
or organization variable (§Variable references). The role comes from the
identity the environment names. An environment absent from the table or naming
no identity, a path the identity sets no role for, a tag outside the entry's
`workloads`, and an untagged cell whose identity varies by workload each resolve
an empty role, and the cell's credentials step is skipped explicitly. A
`split: false` environment resolves `aws.apply` on both paths. Each row carries
what its detect resolved: `role_arn`, `cred_region` (the entry's `region` when it
names an identity, else empty), `tf_vars`, `config_path` — the path actually
consulted, which is diagnostic and read by nothing — and `env_binding`, the
GitHub Environment the cell's job binds: the bare `<env>` when shared,
`<env>-plan` or `<env>-apply` for the calling path otherwise.

**`shipmate doctor` lists the role each environment resolves**, one notice per
environment naming an identity: `` `<env>` resolves these roles at the commit
under examination: … ``, one item per path and listed workload, and `no role`
for a path the identity grants nothing on. An identity that does not vary shows
`every cell`, or `untagged and listed cells` when the entry writes `workloads`,
because a tag outside the list resolves no role. The notices share an
8,000-character budget, so the report stays under GitHub's comment limit: an
environment over it alone has its items cut and counted, and one final notice
counts the environments left out. One notice per GitHub Environment the roles
bind then names the distinct AWS accounts its roles reach: `` `<subject>`
reaches these AWS accounts at the commit under examination: … ``. The OIDC
subject is per environment, so every cell bound to it can assume each of those
roles. A subject with no role gets no notice. The account notices share their
own 4,000-character budget, cut and counted as the roles notices are, and their
final notice points back to the roles notices. Doctor reads the branch's table,
so it is the audit read for an identity edit before it merges. Both notices show
a referenced role as comment-ops resolves it, and the account drawn from it: a
`shipmate-engine` Environment variable of the same name wins there, and never in
a cell (§Variable references).

### Adding and removing an environment

**The table comes from the default branch and the environment list from the
feature branch, so an environment that needs a table entry arrives and leaves
over two pull requests.** The order is configuration first when adding,
configuration last when removing:

1. **Adding:** merge the table entry on its own. Until the stacks land, the
   entry is unused and every run warns about it by name.
2. Then merge the branch that tags the stacks. The warning stops.
3. **Removing:** merge the branch that untags the stacks first. The entry is now
   unused and warns.
4. Then merge the removal of the entry.

Doing it the other way round fails rather than warns, and not always as a
refusal: a branch whose stacks produce a matrix row for an environment the
default branch's table does not name is refused at detect under the `tf_vars`
layout, while
under `folder` or `workspace` nothing refuses it — the cell's credentials step
resolves no role, skips, and the cell fails at `tofu init`. The sequence applies to
any environment that needs an entry: every environment under the `tf_vars`
layout, and under
`workspace` or `folder` any environment naming an identity. An
environment needing no entry at all lands in one pull request.

The same order applies to a `workloads` entry: merge the workload into the list
before the branch that adds its `workload/<name>` tag, and remove it after the
branch that drops the last tag. A tag outside the list refuses at detect, and a
listed workload no stack tags warns, so this order passes through a warning and
never a refusal.

**An unused entry warns rather than refusing, and that is what makes the
sequence available.** Refusing both the missing entry and the unused one leaves
no order in which the two pull requests can land — every add and every remove
deadlocks. The refusal stays on the side that would otherwise run a cell with no
environment identity at all.

An entry name holding an uppercase letter refuses (§Refusals), so no entry can
differ from a tag only in case. Entry keys are matched exactly against the
tag-derived environment names, as resolution matches them, so a lowercase entry
no stack tags is unused and warns.

### What the diagnostics can and cannot see

The unused-entry warning, and its sibling naming a listed workload that no stack
in its environment tags, are computed over every stack's environment membership
on the sweep paths only: every drift sweep, `unlock`, and a bare
`shipmate apply`. The plan, deploy and targeted-apply paths read every stack's
tags too, but build membership over a changed set or a workset, compute neither
warning, and stay silent.

**A `needs` predecessor is checked the same way.** It names an environment
rather than declaring one, and a predecessor matching no tag warns by name under
the same rule, on the same paths, naming what it therefore fails to do: it is fail-open,
and orders nothing. A predecessor needs no entry of its own, so it warns rather
than refuses. `apply_mode` and `review` sit on the entry itself, so a flag on a
misspelled entry is named by the unused-entry warning above. The charset refusal
catches only names no environment name could take; a plain misspelling reaches
these warnings instead.

**A typo'd table key therefore does not surface on the pull request that
introduces it.** It surfaces on the next drift sweep, `unlock` or bare
`shipmate apply`. Refusals are unaffected — they need only the environments
already in the matrix, so a tagged environment missing from the table still
refuses on every path.

## State backend

A consumer declares nothing about where state lives. Each cell reads it from the
record `tofu init` wrote, `${TF_DATA_DIR:-.terraform}/terraform.tfstate` in the
stack, and from `TF_WORKSPACE` (unset means `default`). `scripts/state-path`
runs after a successful init, inside `terramate run`, as init is, so it sees the
`TF_DATA_DIR` and `TF_WORKSPACE` OpenTofu saw. It yields one path relative to
the repository root, or none:

| init record | workspace | state path |
| --- | --- | --- |
| absent | `default` | `<stack>/terraform.tfstate` |
| absent | `<ws>` | `<stack>/terraform.tfstate.d/<ws>/terraform.tfstate` |
| `type: local` | `default` | `<stack>/<path or "terraform.tfstate">` |
| `type: local` | `<ws>` | `<stack>/<workspace_dir or "terraform.tfstate.d">/<ws>/terraform.tfstate` |
| any other `type` | any | none: the backend owns its state |

- **A path** — a local backend. The cell restores that path through
  `actions/state` before it plans or applies, and `apply-cell` saves it after the
  apply (`plan-cell` and `drift-cell` restore only; neither writes state). The
  cached file is encrypted with `SHIPMATE_ENCRYPTION_KEY` (§Plan artifact
  encryption), because any workflow run, a fork's pull request included, can
  restore a default-branch cache entry. An empty encryption key refuses the restore
  and the save. An entry that fails authentication and an openssl
  or disk failure on the runner each fail the restore naming the matched cache
  key, so tofu never reads it and the cell skips its save. An encryption key
  rotation, different values on `<env>-plan` and `<env>-apply`, a corrupt,
  truncated or altered entry, and one copied under another stack's or
  environment's key each fail authentication; `docs/troubleshooting.md`
  §`state cache entry ... failed authentication` tells them apart.
  The save is verified: `actions/state` looks its entry up by the exact save
  key, with no `restore-keys`, and its `saved` output is `true` only on that
  hit, because `actions/cache/save` warns and succeeds when the cache stores
  nothing. `Save state` does not fail on a miss. `apply-cell`'s
  `Warn that the applied state was not saved` step fails the cell's job, so its
  apply check stays pending, whenever an apply step that ran over a restored
  state is followed by a save that failed, was cancelled, was not found, or was
  skipped for an absent or empty state file; `docs/troubleshooting.md`
  §`The apply step ran and its state was not saved` has the recovery.
- **None** — a backend such as S3 owns the state. Both `actions/state` steps are
  skipped, shipmate never handles a state file, and the backend and its locking
  are the consumer's configuration.

`.terraform/environment` is never read: the engine selects a
workspace through `TF_WORKSPACE` alone and never runs `tofu workspace select`.

**The derivation fails closed.** A cell that reads a local backend as remote
restores nothing, applies, discards the state and still reports `applied` — a
green gate over infrastructure nothing recorded. So:

- An absent record after a successful init is the default local backend, never
  "no state".
- Only a backend `type` other than `local` yields no path.
- Any record shape the derivation does not recognize refuses, naming the stack.
  So does a path that is absolute, one that normalizes outside the stack
  directory or to the directory itself, and one holding a character outside
  `[A-Za-z0-9._/-]` (`actions/cache` reads a newline in `path:` as a second path
  and `!` or `*` as a pattern).
- A failed init fails the cell before the derivation runs. A refused derivation
  blocks an apply cell with the reason `shipmate cannot tell where this stack's
  local state lives; see the job log`.

**Trust.** The path comes from the checked-out commit's backend configuration.
`plan-cell` and `drift-cell` never save state, and `actions/cache` versions an
entry on its path string, so a branch that points its backend elsewhere plans
against empty state, visibly. `apply-cell` saves, and it runs the reviewed
commit, whose backend block was part of the review.

**On the state key.** Where the consumer's backend derives a key per stack,
derive it from `terramate.stack.path.absolute` (as `docs/aws.md` does), which
is unique by construction across the whole tree. A key built from
`${global.workload}/${terramate.stack.name}` is not: a stack's default name is
its directory's basename, so `accounts/sandbox/network` and
`stacks/prod/network` both name `network` and, when both carry the same
`workload/<name>` tag, render one key and share one state file.

Nothing else differs between a local and a non-local backend. The exact-plan
`.otplan` artifact flow, the fingerprint verification, the wave ordering, and the
apply checks are identical either way — a non-local-backend cell has no state
artifact, so it can never be blocked on one.

## AWS OIDC (optional)

The engine is cloud-agnostic by default and ships no credential of its own. A
consumer opts in per environment, by naming an identity (`identities.<name>`) from
that environment's entry on the default branch (§Environment table). The table
chooses on every path: a reference in it is resolved from a repository or
organization variable (§Variable references), and no variable the table does
not reference can widen what it resolved. The trust policy on each role is the
enforcing control (`docs/hardening.md` §7–9): the table decides which role a
cell names, not who may assume it.

With no role resolving — no identity named, or no entry for the environment —
the credentials step is skipped and the job holds no cloud credential at all,
which is how the sample repos run credential-free. Every cell-running job on
every path is wired the same way: the wave jobs of `apply-env-level.yml`,
`unlock.yml`'s unlock job, `plan.yml`'s `plan` job and `drift.yml`'s `drift`
job each request `id-token: write` and run
`aws-actions/configure-aws-credentials`, gated on a role resolving non-empty,
before the cell step. The step reads the row, which the detect resolved from the
identity's `aws.apply` on the first two and its `aws.plan` on the other two,
except that a shared environment (`split: false`) resolves `aws.apply` on
every path. The `snapshot` and `complete` jobs deliberately get no token.

On the apply path the engine passes through whatever role the environment
resolves, and nothing more: which role that is — and whether two environments'
roles live in different AWS accounts — is the consumer's configuration, not
something the engine resolves further or validates.

**An environment resolves the roles of the identity its entry names, and nothing
else.** An identity is a role named once and picked up by every environment that
names it, so one edit to `identities.<name>` retargets all of them. The reach
is bounded and visible: only the entries carrying `identity: <name>`, and
`shipmate doctor`'s roles lines list every role each one resolves
(§Resolution). An environment with no entry, or naming no identity, resolves
none. The only bound on what a resolved role may do is its own trust-policy
claim condition (`docs/hardening.md` §7–9).

**The plan and drift paths run that same step, per-workload roles included.** An
identity's `aws.plan` is what a consumer sets for plan-time and drift-time cloud
access, written as a map keyed by workload or with `{workload}` for a
per-workload role. The
engine runs the step on every cell path, so no consumer workflow file decides
whether a cell gets credentials; the environment table alone decides the role.

**A plan cell executes branch-authored Terramate/OpenTofu** — a provider or an
`external` data source runs at plan time — so whatever the identity's `aws.plan`
names is assumed while running code from the branch under review. What
*authorizes* it is the role's trust policy, and nothing in the engine bounds it
further: a policy conditioned on
`repo:<owner>/<repo>:environment:<env>-apply` refuses the `<env>-plan` token, so
the credentials step fails and the cell fails loudly before `tofu init`. A
repository-wide claim condition does not refuse it, and that configuration hands
apply credentials to a plan of any branch someone with push access can push
(fork pull requests are refused in `detect` before a cell exists).
`docs/hardening.md` §7–9 is the threat model.

It follows that the plan/apply role split is the consumer's to configure and to
enforce in the roles' trust policies: an identity's `aws.plan` and `aws.apply`,
and a trust-policy claim condition on each role. The engine verifies nothing about
either role, including whether the two differ. See `docs/hardening.md` §7–9 for
the threat model this bounds.

The engine reads no `AWS_*` environment variable itself. The credentials step
exports the assumed role's short-lived session variables into the job, and
OpenTofu's provider consumes them. They are `AWS_*`, so they are excluded from
the apply-match fingerprint by construction — it hashes only non-empty
`TF_VAR_*` plus `TF_WORKSPACE` (see Apply-match fingerprint, below).

**Every calling job whose callee runs a cell must grant `id-token: write`, cloud
or not.** GitHub caps a called workflow's permissions at each `uses:` boundary,
so the `plan`, `drift`, `deploy`, `apply`, `auto` and `unlock` jobs of the
consumer's `shipmate.yml` grant it; its `comment-ops` job does not, because no
job in engine `comment-ops.yml` requests it. This applies to a consumer that
uses no cloud credentials whatsoever: without the grant the run fails at
workflow-resolution time, the same failure mode `apply-env-level.yml` already
documents for its `checks: read` requirement. The grant belongs on the calling
job and nowhere else — a job-level `permissions:` block replaces the
workflow-level default rather than intersecting it, so a top-level block that
omits `id-token` takes nothing away from a job that declares it.

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
A stack carries at most one `workload/<name>` tag; a stack carrying two
is refused at detect when a run builds its cell. A stack the unlock queue
leaves out does not refuse the run, and every apply form builds cells only for
stacks with an apply check.

Terramate refuses an uppercase letter in a tag, so an environment name is
lowercase letters, digits, `-` and `_` — never uppercase. The `environments`
table's own keys and each `needs` item are held to that charset, to the
`-plan`/`-apply` suffix rule and to the reserved `shipmate-engine`, and an
uppercase name is refused rather than left to match nothing. An `env/<name>` tag
takes an environment-table name (lowercase letters, digits, `-`, `_`, no
`-plan`/`-apply` suffix, not `shipmate-engine`). A run whose scan includes
a stack carrying any other `env/*` tag refuses: plan and deploy scan the changed
stacks, drift and a bare `shipmate apply` the whole tree. A targeted
`shipmate apply` and `shipmate unlock` check only the environment they act on.

A stack with no `env/*` tag is unmanaged: plan, deploy, apply and drift skip
it, and plan, deploy and drift print a notice counting the unmanaged stacks they
scanned (the changed set on plan and deploy, the whole tree on drift) and naming
the first ten, then `, and M more`. A bare
`shipmate apply`, `shipmate unlock` and `scripts/onboard` print it too, over the
whole tree, and `scripts/onboard` also lists the stacks on its `unmanaged stacks`
checklist item. The plan
comment names the unmanaged stacks a pull request changes, under its verdict. A
`workload/*` tag on an unmanaged stack is never checked. An `env/<name>` tag
whose environment has no entry in the environment table still refuses the run
under `layout: tf_vars`. The trade-off is accepted: a forgotten tag on a new
stack is a notice and a line in the plan comment, not a refusal, and the gate
goes green over a stack that plans nothing. When a pull request removes a
stack's last `env/*` tag, plan detect also writes a `::warning::` naming it,
because the same green gate then hides a live stack's unapplied change. A pull
request removing one `env/*` tag from a stack that keeps another gets a
`::warning::` and a plan comment line naming each `<stack> / <env>` pair, for the
same reason. A
deploy refuses when its merged pull request holds an open apply check for a
stack and environment the merge no longer pairs: the stack, changed by that
pull request, lost that `env/*` tag (or every one) after the pull request was
planned, so no cell would ever apply its reviewed change. The refusal applies
nothing, so it also names every other cell of that merge still to apply, as `<stack> / <env>`.
A new pull request that retags each stranded stack and changes every other
named stack plans the current default branch; its cells are applied before
merge under a required gate, or by its deploy otherwise. After a deliberate untag, the stranded change is applied by hand instead,
and the pull request still changes every named stack, or is not needed when
none is named. Re-running the refused deploy checks out the same commit and
refuses again ([`docs/troubleshooting.md`](docs/troubleshooting.md) §A deploy
refused an apply check on a stack that lost its env tag). A check of a stack
that is no longer a stack at the merge commit, deleted on the default branch
after planning, is not refused: it strands with nothing said, because no remedy
exists for a deleted stack.

**A drift sweep's `tags` query narrows what the sweep plans, never what it
scans.** Engine `drift.yml` takes it as an input, one literal query per drift
workflow file:

- `,` separates OR clauses and `:` separates the terms of one clause, which
  must all match, so `:` binds tighter. Terramate forbids `:` inside a tag, so a
  `:` in a query is always the operator.
- A term is a tag in its on-disk form, stripped of surrounding whitespace and
  matched exactly. A cell matches against its stack's tags minus every `env/*`
  tag other than its own, so a stack tagged for two environments matches a
  one-environment query in that environment's cell alone.
- An empty term refuses the run. A clause matching no cell, such as one naming
  a tag no stack carries, matches nothing on its own; one notice names every
  such clause and the terms no stack carries. A query matching no cell is an
  empty sweep with a notice, not a refusal.
- `build-matrix` refuses a query on any run that does not pass
  `sweep: true`, which only engine `drift.yml` passes: on a plan run
  it would drop changed stacks, and a dropped stack gets no apply check while
  `shipmate / gate` greens.

The repo-wide checks run over the full tree every sweep scans, before the query
applies: slug collisions, two `workload/*` tags on one stack,
`tf_vars`-layout coverage of every tagged environment, and the unused-entry
warnings. The 256-cell matrix limit counts the cells the query keeps.

Three checks run per selected cell, after the query, so a cell it drops escapes
them: resolving the cell's row from the environment table, the refusal of a row
whose `env_binding` names no GitHub Environment, and the refusal of a
`workload/*` tag its environment's `workloads` list does not name.

### Taking a stack out of CI

1. Remove every `env/*` tag from the stack in a pull request. Its plan comment
   names the stack as unmanaged, its plan run warns that the stack lost its last
   `env/*` tag, and merging deploys nothing for it.
2. Work on the stack by hand.
3. Retag the stack in a later pull request to bring it back, or delete it.

Removing one environment's tag from a stack that keeps others takes only that
pair out; its plan comment and its plan run's warning name the `<stack> / <env>`
pair.

Every plan's `detect` still runs `terramate fmt --check` and
`terramate generate --detailed-exit-code` over the whole repository, unmanaged
stacks included, in its `hcl-check` step. Keep an unmanaged stack formatted and
its generated code current, or every plan's `detect` fails, naming the files.

The stack's open drift Issues close on the next sweep that plans at least one
cell and planned the default branch's current head, as Issues of a cell no
longer managed. A sweep left with no cell skips its
`issues` job ([`docs/drift.md`](docs/drift.md)), so close them by hand then.

A stack that exists once, such as one in a management account, is a
single-stack environment, not an unmanaged stack: give it an environment of its
own ([`docs/aws.md`](docs/aws.md) §The environment table).

## Comment-ops

`shipmate <verb> [env]` in a PR comment drives shipmate's
comment-based commands. The grammar is strict and anchored — the whole
comment line must match one regex, and the parsed values are never
interpolated into a shell. A comment authored by a bot account (any login
ending `[bot]`) is ignored outright before parsing — a loop guard, since
shipmate's own comments (help output, apply results, the doctor report) can
themselves contain text that matches the command grammar. The engine's `ops`
job starts only for a pull-request comment whose author is not a bot and whose
body contains `shipmate` in any case; every other comment starts no runner.

| verb | args | authorization |
|---|---|---|
| `shipmate apply [env]` | optional env | apply requirements, below |
| `shipmate doctor` | none | write access (below); read-only |
| `shipmate help` | none | none — read-only, open to any commenter |
| `shipmate plan` | none | write access (below); changes no infrastructure |
| `shipmate unlock <env> [--evict]` | required env | write access and same repository, plus the `<env>-apply` environment — no review policy, no mergeable check, no draft check, no reviewed plan (below); with `--evict`, write access alone |

`shipmate unlock <env> --evict` is its own route, `evict`, answered inside the
comment-handling run and never dispatched. It deletes each cell lock in `<env>`
that another pull request (or an unknown holder) holds on a stack with an
App-authored `apply / <stack> / <env>` check on this pull request's head,
comments once on each holder's pull request, `` 🔓 <cells> released by
@`<login>` from #<pr>; re-plan before applying them. `` (each cell as
`` `<env>` / `<stack>` ``), and replies naming the evicted cells, or that there
was none. A lock whose delete is refused does not stop the others: the
holders of the deleted ones are still told. A refused delete or a notice that
cannot be posted turns the reply into `🔴 failed:`, naming the cells not
released and the holders not told, and fails the run. §Cell locks has the
store and the rest of the lock's lifecycle.

`plan`, `doctor`, `apply`, `unlock` and `unlock --evict` require the commenter's
write access (the "write access" apply requirement below defines it), read and decided once
per comment before any step that mints a token, reads the pull request or
dispatches; the 👀 acknowledgement on `plan` and `doctor` comes first. A refused
commenter gets one `🔴 refused:` reply naming the verb and the permission read,
or saying the permission could not be read. `help` answers anyone.

Every dispatching verb dispatches the same file,
`.github/workflows/shipmate.yml`, and the `verb` input in the dispatch body
selects the job: `plan` → the `plan` job, `apply` → the `apply` job, `unlock` → the `unlock`
job. A fourth verb, `auto` → the `auto` job, is sent only by a plan run's `summary`
job after an autoplan (§`apply_mode: auto`); no comment sends it, and
`actions/dispatch` refuses it. That file carries four triggers; `workflow_dispatch` is the one a
commented verb reaches, and the other three (`pull_request_target`,
`issue_comment`, `push`) fire from their own events. `doctor`, `help` and
`unlock --evict` dispatch nothing: each is answered inside the comment-ops run itself. A
repository with no `.github/workflows/shipmate.yml`, or one that declares no
`workflow_dispatch` trigger or not the inputs a verb's dispatch sends, fails at
dispatch time on that comment-handling run — for every verb alike, since they share the file. That
run is not visible from the pull request, so the refusal of an unwired verb or
an unknown one, a failure building the request or creating the temporary file
for gh's error output, and a rejected API call each try to post a `🔴 failed:`
reply there whose footer links the run. The run holds the error; the failure
reply never carries the API's answer. A failure reply that cannot be posted is a
warning on the comment-handling run, and the step still fails with its own
status. A dispatch that succeeded then tries to post the
started-run reply, a `⚪` notice:

- With a usable run id it reads `started [the <verb> run](<url>)`, linking the
  run it started by a URL the engine builds from the `workflow_run_id` the API
  returns (accepted only as 1 to 20 digits, never the API's `html_url`).
- Without one it reads `started the <verb> run; it is listed in this
  repository's Actions tab` and links no started run.
- It may not be posted at all: the run has already started, so a reply that
  fails is a warning on the comment-handling run, not a failure, and the pull
  request gets nothing.

The started run collapses this reply as outdated once it posts the verb's
result: a plan run after it writes the gate, when it created or updated the
sticky plan comment; an apply run once it posts its result comment.
`scripts/collapse-reply` minimizes each `github-actions[bot]` comment holding a
line equal to the verdict that links that run; a failed collapse is a warning on
that run. A run that posts no result leaves the reply: an apply refused at
review, an `unlock`, a run that ends or is cancelled before its result, a
superseded plan, a plan whose comment was not written, and a plan whose
apply-check publish or gate write fails (for example a newer run's hold refuses
it once the head moved). A reply with no run link is never collapsed. A result
posted before the reply exists leaves the reply too: nothing matches yet, and
the reply is posted one API round trip after the dispatch answers, while the
result needs at least three sequential jobs.

Every reply comment-ops posts (a refusal or failure in place of running a
command, or a notice posted alongside the dispatch it reports) has one shape: the
header `### shipmate <verb>` (with `<env>` when the command named one, and
`### shipmate` alone when the comment did not parse or named no known verb), a
blank line, one verdict line, a blank line, and a footer linking the
comment-handling run that posted the reply, never the run a dispatch started.
A refusal or a failure asks the commenter to act, so its
footer carries the help hint:

``[comment run](<run url>). Comment `shipmate help` for the available commands.``

A notice asks nothing, so its footer is `[comment run](<run url>)` alone.

The verdict line is `🔴 refused: <reason>` when the engine decided not to run
the command (grammar, authorization, an unresolvable `.github/shipmate-config.yml`),
`🔴 failed: <reason>` when it could not (an App token mint, a workflow
dispatch, an errored read or decision of the commenter's permission), and
`⚪ <text>` for a notice, the dispatch's started-run reply included.
`scripts/reply-comment` renders and posts all of them.

`shipmate plan` plans the pull request's changed stacks on demand, authoring
what a push-triggered plan authors: the sticky plan comment, the per-cell plan
checks, the plan artifacts an apply consumes, and `shipmate / gate` (§Plan
comment; a dispatched run's cell checks reach the pull request head as
App-authored mirrors, described with the App identities below). It differs in
two ways: a run that plans no cell still posts the `⚪ nothing planned`
comment, and once its comment and its gate are written it collapses the started
reply that links it (above).
It takes no arguments — there is one plan of record per head commit, and a
run holding a single environment's artifacts would leave every other
environment's workset empty at the next apply, so an env alongside it is
rejected. Unlike autoplan it plans a draft pull request —
a plan a draft can hold but not apply, since the apply requirements below
refuse a draft.
Re-issuing it re-plans rather than reporting the existing plan current: the new
run's plan replaces the plan of record for the current head, and doing so is
safe because a plan changes nothing but shipmate's own comment, checks and
artifacts. A commenter without write access is refused as above and nothing
is dispatched; the plan route mints no App token of its own. A pull request
whose head is in another repository is refused in its own words and likewise
not dispatched: the run's own fork refusal (§Post-plan topology) lands on
checks attached to the dispatch ref, where the pull request cannot see it. That
is a message, not a layer — it reads the same head repository that guard reads,
and a head it cannot read is dispatched, leaving the refusal where it is
enforced.

A verb documented as taking no arguments (`doctor`, `help`, `plan`) rejects an
env given alongside it with an explicit "takes no arguments" error rather than
silently ignoring the extra token. An unknown verb's error points the commenter at
`shipmate help`, and so does every other refusal of a line that does not match
the grammar: for `apply` or `unlock`, the reply names a token after the env
("unexpected `<token>` after the environment") or an env outside lowercase
letters, digits, `-` and `_`, starting with a letter or digit ("environment
`<token>` is not ..."); any other shape (a capitalized verb, a double space)
gets "malformed: expected `shipmate <verb> [env]`". The reply quotes the
commenter's token, an unknown verb included, only inside a code span, and only
when it is non-empty, at most 40 characters and free of backticks; otherwise it
says "an unexpected token", "the environment name" or "an unknown verb". The reply to a refused line names no environment in its
header, and names its verb only when the verb is known and the line fits the
grammar (`### shipmate help` for `shipmate help foo`).
`scripts/comment-parse`'s `VERBS` registry is the single
source of truth this table is derived from: it drives the parser, the
`--help-markdown` rendering that `shipmate help` posts verbatim (marked with
the HTML comment `<!-- shipmate:help -->`), and the reject-hint text, so the
three cannot drift from each other. Unlike the `:summary` and `:doctor`
markers below, `<!-- shipmate:help -->` is not an upsert key — nothing reads
it back: `shipmate help` posts a fresh comment every time, and the marker
only labels the output as shipmate's own. The list sits under the header
`### shipmate help`, carries no verdict line (it examines nothing), and ends with
`[comment run](<run url>)` alone, without the help hint.

`shipmate doctor` posts a consolidated, sticky report — one comment per pull
request, identified by the HTML marker `<!-- shipmate:doctor -->` (distinct
from the plan comment's `<!-- shipmate:summary -->`) and upserted in place the
same way. It opens with the header `### shipmate doctor` and a verdict line
counting errors (🔴, harvested failure annotations), warnings (🟠) and notices
(⚪), zero counts omitted, plus `harvest incomplete` (⚪) when the harvest failed
or is still pending; the line takes the worst circle, reads `🟢 no problems found`
when there is nothing to count, and ends
`at [<sha7>](<commit url>) in [run #<n>](<run url>)`, linking the commit examined
and this run (`at an unknown commit in [run #<n>](<run url>)` when the commit
is not a 40-character lowercase hex SHA). Each finding and harvested annotation
carries the same circles. Under any verdict but 🟢 the report ends with the help
hint ``Comment `shipmate help` for the available commands.``; a 🟢 report has
no footer. It combines fourteen live settings probes, listed in
`docs/troubleshooting.md` §shipmate doctor with their scopes and degrade paths,
and a harvest of the warning and failure annotations GitHub already recorded on
this commit's workflow runs (shipmate's own and any other Actions workflow run
on that commit; third-party-app-authored check runs are excluded).
Only thirteen of the fourteen probes can produce a finding from the plan path's own
`annotate`-mode invocation: the App-permission-drift probe reports only on a
full-manifest permission-set mint, which only `shipmate doctor` attempts. An
empty harvest is
reported as an all-clear only when the harvest both completed and had nothing
left to wait for: if any of the commit's relevant check runs had not finished
when the report was rendered, it says so and asks for the command again once
they have, and if the harvest itself could not be read in full it says that
too — the two are separate statements, since a run that has not finished has
recorded nothing yet while a run that could not be read may have recorded
plenty.

The plan comment carries no doctor findings; the plan body carries only their
count (see §Plan comment). Instead,
`actions/summary` runs `scripts/doctor` on every plan run and emits its
findings as workflow-command annotations, verbatim:

- `::warning title=shipmate doctor::<text>` for a misconfiguration,
- `::notice title=shipmate doctor::<text>` for an informational finding.

The `title=shipmate doctor` string is exactly what `shipmate doctor`'s harvest
step filters check-run annotations on, so it never re-reports a finding the
live probes already re-state fresh against current settings — this is
machine-read, not a formatting choice, and a mismatch between the annotate
call and the harvest filter is a regression. `shipmate doctor` is entirely
read-only: it dispatches nothing and changes no setting, and writes nothing
but its own sticky comment, a copy of its report in the job summary
and an `eyes` reaction on the triggering comment (`doctor`, `help` and `plan`
all get that acknowledgement as soon as the command is accepted — `rocket`
marks an authorized dispatch, whether `apply`, `unlock` or `plan`, instead; a
reaction that cannot be posted is ignored), a `🔴 failed:` reply when it
cannot mint an App token or when reading or deciding the commenter's permission
errors, a `🔴 refused:` reply when the commenter may not have
the report (below), and untitled `::warning::` annotations on its own degrade
paths, which `docs/troubleshooting.md` §shipmate doctor lists. `shipmate doctor`
never affects `shipmate / gate`.

Because the report enumerates the guardrails a repository is *missing* — a
default branch no ruleset gates, an apply environment with no approval rule, an App
installation short of the manifest's permissions — the `doctor` route requires
the commenter's write access (above). A refused commenter gets the refusal
reply and nothing else happens — no App token is minted, no probe runs, no
report is composed. `shipmate help` is not gated — it renders the verb list
and discloses nothing about the repository. The report is an ordinary pull
request comment, so once someone with write access asks for it, everyone who
can read the pull request can read it. `app/manifest.json` declares
`"public": false` for a related reason: the App is registered per organization
and intended for repositories the installing organization controls.

The env is optional for `apply`. A targeted `shipmate apply <env>` applies one
environment; a bare `shipmate apply` applies every environment that has a
reviewed plan for the current PR head, in `needs` env-levels (see Env
apply order, below), except environments whose entry holds `apply_mode: targeted`
in `.github/shipmate-config.yml` and environments held for review (below). In both
forms a cell that runs after a pending cell not applying in that run waits and
stays pending (see Env apply order). `targeted`
environments (typically production) must always be named: their
`apply / <stack> / <env>` checks stay pending under a bare apply — so
`shipmate / gate` keeps gating the merge — until someone runs
`shipmate apply <env>` for them. An entry without `apply_mode`, or with
`apply_mode: bare` or `auto`, is part of a bare apply. Any value but
`targeted`, `bare` or `auto` fails loud, `Targeted` and `true` included: read as `bare`, it would put the
environment on a bare apply. An `auto` environment also applies without a
comment, after its autoplan (§`apply_mode: auto`).

`apply_mode` constrains the bare pre-merge `shipmate apply` only. The
post-merge deploy applies every cell whose apply check is still pending,
`targeted` environments included; the control on that path is the apply
environment's required reviewers — which a shared environment cannot carry (see
§Env model) — not this setting. The asymmetry is deliberate:
after merge the pull request is closed, and the apply requirements above include
mergeable, so a deploy that honoured the exclusion would strand those cells
with no path to apply at all: their `apply / <stack> / <env>` checks would sit
pending forever.

A parsed `shipmate apply <env>` command is authorized only when it satisfies
apply requirements — named, Atlantis-style, checked in order, each with
its own actionable rejection reason:

- **write access**: the commenter's permission on the repository is `write` or
  `admin`, read from GitHub's repository permission for the commenter at comment
  time. GitHub reports the `maintain` role as `write` and `triage` as `read`,
  and a custom role as the base role it extends, so a maintainer can apply and
  a triager cannot. `read` and `none` are refused, and so are any other value
  and a permission that could not be read. Every account can read a public
  repository, so there a commenter who is not a collaborator has `read` and is
  refused;
- **same repository**: the pull request's head branch is in this repository.
  A fork's head, or one whose repository was deleted, is refused: shipmate
  applies and unlocks only branches of this repository;
- **not a draft**: the pull request is not a draft. `shipmate plan` plans a
  draft on request, so a draft head can carry apply checks with plan runs on
  them; a draft says the change is not ready for review, and applying it is
  what that must not permit. Keyed on the pull request's `draft` flag, not on
  `mergeable_state` — that field has one slot and reports `dirty` for a draft
  with conflicts;
- **mergeable**: the pull request is mergeable;
- **reviewed**: the pull request satisfies the branch ruleset's review policy
  — GitHub's `reviewDecision` is `APPROVED`, or is null (no rule requires a
  review, or the only review rule is code-owner review and no changed file has an
  owner; normalized to the explicit `NONE` sentinel in transit). The
  ruleset (required approving reviews, CODEOWNERS, last-push approval) is the
  single source of review policy; shipmate imposes none of its own. A ruleset
  requiring zero approvals reports no decision even when an approval exists,
  but a `CHANGES_REQUESTED` review still blocks. Any other value — including
  an absent or empty decision — fails closed with a wiring-error reason. An
  environment whose entry holds `review: false` is exempt from this requirement,
  and from no other (below);
- **undiverged**: at least one `apply / <stack> / <env>` check on the pull
  request's current head names the plan run its plan came from (each check
  records that run at plan time). The records are read from that head's own
  check runs, so no plan run reached here can belong to another head and none is
  compared against one: a head new commits landed on carries no apply checks
  yet, names no plan run, and is refused — re-plan required. A plan run with
  incomplete evidence publishes no apply check (§Plan comment), so a head whose
  only plan runs are such runs is in the same state.

`undiverged` is only the comment-time half of shipmate's exact-plan rule.
The cell that applies re-verifies the reviewed `.otplan` against current state,
against the TF_VAR fingerprint recorded at plan time, and against the commit the
plan was produced from (§Apply-match fingerprint). Each refuses in its own
words — OpenTofu's "saved plan is stale" for the state case, "current env does
not match the reviewed plan's fingerprint" and "the reviewed plan was produced
from …" for the other two — and none of them re-plans.
Together they are strictly stronger than a base-divergence check: a plan
the base branch never moved under, but that state moved under, is refused too.

Review policy is read from GitHub's own `reviewDecision` — the verdict the
branch ruleset computes. The apply path never parses `CODEOWNERS` and keeps no
second copy of the rule: whatever the ruleset requires (approval counts,
code-owner review, last-push approval) is what the apply path waits for, and
there is no owners parser here to disagree with it.

A bare `shipmate apply` is authorized once at comment time, by the same six
apply requirements. Comment time is never the whole review decision: both comment
forms re-read `reviewDecision` server-side before anything applies (below), so
an approval dismissed between the comment and the dispatch holds; the `auto`
form re-reads it too. Both comment forms
dispatch the consumer's `shipmate.yml` with `verb: apply`; the optional
`environment` input selects the form `apply.yml` runs (set → targeted, empty → bare). Both
share the same App-minted `workflow_dispatch` mechanism and the same per-env
`apply-<env>-<stack>` concurrency groups. The third form, `auto`, is dispatched
with `verb: auto` by the plan run and has no comment time; detect decides what
comment-ops decides for the other two (§`apply_mode: auto`).

The dispatch is bound to its pull request after it arrives, because a dispatch
can reach `apply.yml` without passing comment-ops: its `guard` job admits any
`[bot]` actor, and a workflow's own `GITHUB_TOKEN` dispatches as one. The
`review` job reads the pull request `pr_number` names and refuses the run, before
any wave, unless it is open, not a draft, not from another repository, and its
head commit equals the dispatched `ref`, compared whole. A merged pull request is
refused too: its plans apply through the merge deploy. So at dispatch time the
`review` job re-checks open, draft, same repository, head and review decision;
mergeability is checked at comment time only, because GitHub computes it
asynchronously and a conflict bypasses no review. The `auto` form checks it in
detect instead (§`apply_mode: auto`). Then each apply form's
detect refuses a cell it would apply whose apply check records another pull
request, or none (§Apply-match fingerprint); the remedy is a re-plan on this pull
request.

`review: false` on an entry in `.github/shipmate-config.yml` lets that environment be
applied without an approving review. The entry's name is matched against the env
on the apply checks:

```yaml
# .github/shipmate-config.yml
layout: tf_vars

environments:
  dev-eu:
    region: eu-west-1
    review: false
```

It exempts one requirement, `reviewed`, and only its `REVIEW_REQUIRED`
value. Everything else still decides, on a review-exempt environment exactly as on any
other: `write access`, `same repository`, `not a draft`, `mergeable`,
`undiverged` and the exact-plan rule are unchanged; a `CHANGES_REQUESTED` review still refuses,
because an explicit human "no" is not an absent review; and an unknown or
absent decision still fails closed. Nor does it touch the `<env>-apply`
environment's required reviewers — that is a different control, gating the
deployment rather than the code review (see `docs/hardening.md`).

`review` holds `true` or `false`. Any other value — `no`, `False`, `0`, a variable
reference — is a loud configuration error naming `environments.<env>.review`: a
silently inert value would leave an operator believing an environment is
review-exempt when it is not. The entry's own name is held to the env-name charset and the
`-plan` / `-apply` suffix rule, so an entry no stack's tag can name is refused
rather than left to exempt nothing, and it may not be the reserved
`shipmate-engine` (§Tag grammar).

**Absent means under review**: an entry without `review`, or with `review: true`, keeps
the ruleset's review requirement. There is no second source for an absent key
to fall through to.

Opting in takes two things, and the setting alone is not enough:

1. `review: false` on the environment's entry on the default branch, and
2. the consumer's `shipmate.yml` pinning its `.github/workflows/apply.yml@`
   reference (the `apply` job) to the same commit as its `comment-ops.yml`
   reference, as §Consumption's one-change rule requires. `comment-ops.yml`
   authorizes an apply under the exemption that `apply.yml` enforces; pinned at
   different commits, an apply authorized under one engine's rule is enforced by
   another's, or by none.

**One source, three readers.** `actions/comment-ops` resolves the review-exempt set
before it authorizes, and each apply form's detect resolves it again before it
enforces —
`scripts/gate-config` and every form of `scripts/apply-detect`, all
three reading `.github/shipmate-config.yml` on the **default branch** through the same
`env-config` reader. The comment-ops job checks out no consumer content, and
that reader needs none: it reads the file through the contents API. What keeps
the three from disagreeing is not a shared spelling but a shared reader: the
strict top-level key check refuses a misspelled setting outright, and the
boolean and entry-name checks refuse a value that would match nothing, so there
is no value a consumer can write that one reader honours and another ignores. A
pull request cannot grant itself the exemption, because its own edit to the file
is not read until it merges.
`scripts/tests/test_engine_comment_ops_workflow.py` pins the whole `with:` block
of the step that resolves it, so a second source cannot be threaded back in as an
input without failing there.

The decision has two seats, because `authorize` returns one verdict per
dispatch while a bare apply spans many environments:

`apply.yml` re-reads `reviewDecision` itself in a `review` job,
rather than trusting a dispatch input, and that
job is unconditional — the
default branch's `review: false` entries are the only source of this policy, and only
engine-owned scripts read it.

- **Targeted `shipmate apply <env>`** — the env is known at comment time, so
  `actions/comment-ops` refuses early: `REVIEW_REQUIRED` on a review-exempt env
  authorizes, on any other env it refuses with the usual message extended to
  name the env's `review` setting. The engine's `apply.yml` then
  re-applies the identical rule to the freshly read decision and refuses the
  run before any wave, leaving every apply check — and the gate — pending.
- **Bare `shipmate apply`** — authorized at comment time whenever any entry is
  review-exempt, then partitioned per environment by the engine's `apply.yml`.
  `NONE` / `APPROVED` apply everything; `REVIEW_REQUIRED` applies the
  review-exempt environments and holds the rest — all of them when none is
  review-exempt; `CHANGES_REQUESTED`, an unknown value, or no decision at all
  holds everything, review-exempt environments included.
- **`auto`** — no comment authorizes it; `apply.yml` partitions the decision as
  for a bare apply. Every `auto` environment holds `review: false`, so
  `REVIEW_REQUIRED` applies them, and `CHANGES_REQUESTED`, an unknown value or
  no decision holds every one.

A held environment travels the path `targeted`-environment exclusions already
travel: dropped before env-levels are built, its `apply / <stack> / <env>`
checks left pending — so `shipmate / gate` stays pending and the merge
stays blocked — and environments ordered after it are skipped. The apply run
names both halves: its result comment names the environments held for review,
and a notice annotation in its log names each environment permitted to apply
under the exemption (§Apply result comment).

`review` can only relax an existing review requirement, never create one. When
no branch rule requires an approving review, the decision is `NONE` (unless a
requested-changes review stands, which still refuses), and both paths apply an
environment under review without one. A notice annotation in the apply run's log
names each `review: true` environment that applied that way (§Apply result
comment), and `shipmate doctor` names the environments under review when it can read the default
branch's table: in a note while code-owner review is on, since they are still
held wherever a code-owner review is required (a pull request changing owned
files, opened by someone who does not own those files), and in a warning beside
the code-owner warning while that review is off. With the table unreadable, doctor
reports only the sole-maintainer note, or only the code-owner warning when that
review is off.

What bounds the exemption is the default branch, not an admin boundary. Anyone who
can open a pull request can propose `review: false`; what they cannot do is have it take
effect on that pull request, because all three readers resolve the file from the
default branch. Relaxing the gate is therefore a merged commit, under whatever
the branch ruleset requires of one, rather than a line in the pull request that
benefits from it. `shipmate doctor` validates the file it is in and reports a malformed
entry, but does not list the `review: false` entries; its count-0 finding names
their complement, the environments under review. `review` accepts no variable
reference (§Variable references), so the decision stays a merged commit.

`shipmate unlock <env>` releases an OpenTofu state lock stranded by a cancelled
or killed apply. The env is required: a destructive verb gets no wildcard,
so there is no bare form. It never releases a cell lock (§Cell locks), and
`shipmate unlock <env> --evict` never releases a state lock.

It is authorized at comment time by the commenter's write access and a head
branch in this repository (the `write access` and `same repository` apply
requirements), plus the `<env>-apply` environment its job binds — the same required reviewers and the
same OIDC subject an apply of that environment binds. The other four apply
requirements are deliberately absent: reviewed, because an approving review
reviews a diff and an unlock applies none; not a draft, because a draft says
the change is not ready for review, which bounds applying it and not releasing
its state lock; mergeable and undiverged,
because the case this verb exists for is a state lock stranded by a cancelled
post-merge deploy, and a merged pull request reports `mergeable: null` — which
the apply path reads as "still computing" — so keeping them would leave exactly
that lock permanently unreleasable.

Because that environment is part of the authorization, the run refuses when it
does not exist or has no deployment branch policy — the same pre-flight the
apply path runs before its first wave, on the unlock queue. GitHub would
otherwise create the environment on demand with no reviewers, no wait timer and
no branch policy, and keep it: an unlock into a missing environment would retire
the reviewer gate for every later apply of that env.

Its queue is the cells in that environment whose `apply / <stack> / <env>` check
is still pending, not the reviewed plan artifacts: a state lock outlives the run
that stranded it, and those artifacts may be long expired. Each queued cell runs
`tofu init`, then a probe (`tofu plan -input=false -refresh=false`, which takes
the lock and so prints the holder's `Lock Info:` block when one is held), then
`tofu force-unlock -force <id>` on the id parsed from that block — and nothing
else. Three outcomes per cell: no lock held and lock released are both green,
while `could not be determined` fails the cell with an `::error::` and is
never reported as "no lock held". A cell whose apply check is not pending is not
in the queue and not reachable by this verb.

It needs no IAM change. On an S3 backend with `use_lockfile`, releasing a
state lock is the same `s3:DeleteObject` on `…/terraform.tfstate.tflock` that taking
one already requires, so a role that can apply an environment can already
unlock it.

The unlock run itself posts no comment — that would need
`pull-requests: write`, which consumers do not grant `unlock.yml`. The `rocket`
reaction confirms acceptance. The comment-handling run, which holds that
permission, is the one that may post the started-run reply; §Comment-ops says
when it is posted and when it links the unlock run. Per-cell detail lands in
each job's step summary, and a failure reds the run.

**Unlocking is not recovery.** A cancelled apply usually advanced state, so the
reviewed plan may now be stale and the next apply refuses under the exact-plan
fail-safe. The order is unlock, then `shipmate apply <env>`, then a re-plan if
that refuses (`docs/troubleshooting.md`).

The GitHub App carries this permission set: `actions: write`,
`pull_requests: write`, `contents: write`, `checks: write`,
`statuses: write`, `issues: write`, `environments: read` — the last for
`shipmate doctor`'s plan-environment secret listing (names only; no GitHub REST
path returns a secret's value, and this permission cannot write one).
`contents: write` is for the cell lock refs alone (§Cell locks): only the mints
that write a lock request it, and comment-ops' doctor probe, which requests the
whole set to detect an installation missing a permission and reads nothing
with that token. Every other mint names the permissions it needs, `contents:
write` never among them.
Beyond minting the `workflow_dispatch`
token for comment-ops (a dispatch needs `actions: write`; the `ops` job in
`comment-ops.yml` grants its `GITHUB_TOKEN` only `actions: read`, and
`actions/dispatch` mints the App token with `actions: write` in a step that runs
only once the command is authorized) and reading the apply checks that name the
reviewed plan run for authorization, the App authors every
check/status/comment/issue that crosses a workflow-run boundary:

- **Apply checks** (`apply / <stack> / <env>`) — created pending by
  `actions/summary`, completed by `actions/apply-complete` in
  `apply-env-level.yml`'s `complete` job (bound to `shipmate-engine`) — both
  mint an App installation token, so completion works across the create/complete
  workflow-run boundary (check runs are only updatable by the app that
  created them; using the same App on both sides keeps that identity
  consistent).
- **The aggregate gate** (`shipmate / gate`) — read and written only by
  `scripts/gate-status`, under an App token, called by `actions/summary` in
  `plan.yml`'s `summary` job (the plan run's gate) and by `actions/gate-refresh`
  in `apply.yml`'s `summary` job (the pre-merge refresh), `deploy.yml`'s
  `summary` job (the post-merge verdict) and `plan.yml`'s `gate-hold` job (a
  hold). The three `summary` jobs run in one job-level concurrency group,
  `shipmate-gate-<head sha>` with `cancel-in-progress: false`, shared across
  runs and called workflows, so one of them at a time reads and writes a head's
  gate. An empty head SHA (a failed plan `facts`, or a deploy `detect` that
  failed before it validated the merged head) keys the group on the run id
  instead, so that job shares no group. A deploy `detect` writes the head before
  any refusal past head validation, so a refused deploy's `summary` writes
  `failure` on the merged head. GitHub keeps one pending job per
  group, so a third arrival cancels the pending one. What each eviction costs:
  - a pending plan `summary`: its run publishes nothing, and `gate-hold` writes
    a hold naming the recovery, `shipmate plan`. `gate-hold` writes outside the
    group, since a hold only closes the gate, and is not ordered against the
    `summary` that evicted its own: its hold can land after that `summary`
    greened the head, a closed gate over applied work that `shipmate plan`
    reopens;
  - a pending apply `summary`: the evicted run posts no apply comment, so its
    results and its waiting cells show only in the run itself. The writer that
    evicted it runs next: a plan `summary` writes the gate from its plan; an
    apply `summary` refreshes it, which greens it once every apply check is done
    and otherwise leaves it as it is, and fails the run over a hold; a deploy `summary` writes
    its deploy verdict. Its waiting cells keep their apply checks pending, so no
    refresh greens the gate until they apply;
  - a pending deploy `summary`: the merged head keeps its last gate and the
    merged pull request keeps the cell locks that run owed; re-run that
    deploy, as for any superseded deploy, unless a cell's job log shows
    `The apply step ran and its state was not saved` (`docs/troubleshooting.md`
    has the section of that name).

  One race stays open. A refresh already past its status read and before its
  `success` write, one API round trip, can overwrite a `gate-hold` hold. It
  needs three gate writers for one head within a minute (a running apply
  `summary`, a pending same-head re-plan `summary`, a third arrival evicting it)
  and the hold landing inside that round trip. Closing it needs a conditional
  write, which GitHub commit statuses lack.
- **The sticky plan comment** — App-authored (marker + any Bot author for the
  lookup, since a consumer org's App bot login may differ from
  `shipmate[bot]`), upserted in place by `actions/summary` through
  `scripts/upsert-comment`, which reads the marker from the body's first line
  and matches it only at the start of a comment.
- **The `shipmate doctor` sticky report** — App-authored (its own marker +
  Bot-author lookup, same posture as the plan comment), upserted in place by
  `actions/comment-ops` through the same `scripts/upsert-comment`.
- **The apply result comment** — posted fresh by `actions/apply-summary` on
  every apply run (targeted, bare and `auto`); unlike the sticky plan
  comment it is never upserted against a marker, so a failure-then-retry
  sequence stays visible across separate comments — an audit trail.
- **The collapsed started reply** — the reply itself is authored by
  `github-actions[bot]`; `actions/summary` and `actions/apply-summary` minimize
  it as outdated (GraphQL `minimizeComment`) through `scripts/collapse-reply`,
  under the App token they already mint with `pull_requests: write`
  (§Comment-ops).
- **Drift issues** — `actions/drift-cell` holds no App token and authors
  nothing; it only plans each stack × env and uploads a drift-summary
  artifact. A separate `issues` job, bound to `shipmate-engine`, calls
  `actions/drift-issues`, which downloads those artifacts and, under an App
  token, opens/updates/closes the drift Issues.

The plan matrix job's own `plan / <env> / <stack>` auto check-run is the one
exception: it's the job's own check-run (GitHub creates it for the job
itself), not something a separate API call authors, so it necessarily stays
on the `github-actions` identity regardless of App permissions. An on-demand
plan carries an App-authored *copy* of it: GitHub attaches a dispatched run's
job checks to the commit of the ref it was dispatched on, never to the pull
request head, so `actions/summary` mirrors that run's own completed checks
whose names begin `plan / ` — the cells plus `plan / facts` and
`plan / detect`, and none of the consumer file's skipped sibling jobs — onto
the head as App-authored check-runs holding a fixed line and a link back to the
original, whose step summary keeps the plan text. They exist so the pull request
shows the cells, a failed one above all — and because the sticky comment
resolves each
cell line's plan link across the checks on the head, which is why the mirror runs
before the comment is built. No gate or apply queue reads them: neither the
cell names nor `plan / facts` / `plan / detect` fall inside the
`apply / ` namespace those select on. `shipmate doctor`'s annotation harvest is
the one other reader, and it skips them for the same reason: outside that
namespace it takes only the `github-actions` rows, because a mirrored copy
shares a name with the autoplan check it was copied from and carries none of
its annotations.

`shipmate apply` and `deploy.yml` share the same per-env, per-stack
`apply-<env>-<stack>` concurrency group, declared with
`cancel-in-progress: false` on every wave job (§Fan-out), so exactly one apply
ever runs against a given stack × environment at a time, regardless of whether
it was triggered by a pre-merge comment or a post-merge push. A second apply for
the same cell queues behind the first rather than racing or cancelling it, and
is then refused by the exact-plan fail-safe if the first advanced the state.

## Cell locks

A cell (one stack in one environment) that an open pull request applies is
locked to that pull request until it merges or closes. Another pull request's
apply leaves that cell pending and names the holder, so a fresh plan of one
pull request cannot roll back what another applied. The lock is per cell, never
per environment. The merge deploy ignores cell locks: it applies a locked cell,
and the lock stays with its holder, whose saved plan is then stale and refuses
until re-planned.

**Store.** A locked cell is one git ref,
`refs/shipmate/locks/<env>/<key>`, where `<key>` is the lowercase hex SHA-256
of the UTF-8 stack path: one path segment, so nested stacks never meet git's
directory/file conflict. The ref targets a parentless commit whose tree is one
file, `lock`, and whose message is one line of JSON,
`{"pr": <number>, "env": "<env>", "stack": "<stack>", "acquisition": "<run id>.<run attempt>"}`.
The commit carries no committer of its own, so its committer date is the
server's time of the acquisition. The ref name is the lock's identity. A
message that is not exactly that record, or whose `env` and `stack` name
another ref, is a lock of unknown holder, which renders as `an unreadable lock`;
closing or merging never releases it, and only `shipmate unlock <env> --evict`
does. A lock ref that targets anything but a commit refuses every read of its
environment's locks (a `release`, every environment's) with
`` the cell lock ref <ref> targets a <type>, not a commit; someone with write
access must delete it: git push origin :<ref> ``, because no conditional delete
can remove it. No workflow run fires on a lock ref's creation or deletion.
Every read lists its refs in one unpaginated `matching-refs` call, observed
complete at 120 refs; a repository holding more locks than one listing returns
is unprobed.

Every delete is GraphQL `updateRefs` conditional on the commit the deleter read,
so two releasers cannot delete a lock taken after the one they read. That
mutation answers a moved ref, a missing ref and a server fault with one untyped
error, so after an error the ref is read again: absent or on another commit is
not this deleter's lock, and done; still on the same commit refuses.

**Lifecycle.**

| Event | Job | Effect |
| --- | --- | --- |
| `shipmate apply`, bare or targeted | `apply.yml` `detect` | Reads the locks of the pending cells' environments; a bare apply reads none in an environment held for review or excluded as targeted. A pending cell another pull request, or an unknown holder, locks stays pending and out of the waves, exactly like a waiting cell: the cells that run after it wait, and on a bare apply the environments `needs` orders after its environment are skipped (§Env apply order). The apply result comment names it on a locked line (§Apply result comment) |
| the same run | `apply.yml` `lock`, after `detect` and before the first env-level | Locks every cell the env-levels will apply. A cell this pull request already holds is re-entered: its ref is moved to this run's new commit, conditional on the commit read, so a release still running against the old commit cannot delete it. Any other holder refuses the run (below) |
| merge | consumer `release` job (`release.yml`) | Deletes every lock the pull request holds except those whose `apply / <stack> / <env>` check is still pending on its head, including locks on cells the pull request applied and then dropped from its diff |
| merge deploy | `deploy.yml` `summary` | After the env-levels, even when one failed or was skipped, deletes every lock the merged pull request holds whose cell has no pending apply check. A cell whose apply failed keeps its lock through its pending check |
| close without merge | consumer `release` job (`release.yml`) | Lists the pull request's locks, then reads its state. Still closed, it deletes every one of them; merged since, it applies the merge rule: every one but those whose `apply / <stack> / <env>` check is still pending on the head it merged at; reopened since, only those whose commit is no newer than the close. A state that cannot be read refuses and deletes none |
| `shipmate unlock <env> --evict` | comment-ops `ops` | Deletes the other pull requests' locks on this pull request's cells in `<env>` (§Comment-ops) |
| plan | `plan.yml` `summary` | Ends each changed cell's plan comment line that another pull request locks with `🔒 locked by #<n>` (`🔒 locked by an unreadable lock` for an unknown holder) |

**Acquisition.** For each cell, `lock` creates the ref. When the create fails,
the ref is read rather than the status parsed: this run's commit is acquired,
this pull request's earlier commit is re-entered, and another pull request's
commit, an unknown holder's or an absent ref refuses. A refusal deletes the
locks this run created and puts those it re-entered through the close check
below, deleting them unless it passes. After every ref is installed, `lock`
reads the pull request's state and newest close event and refuses, deleting every lock it installed, unless the pull request is open and
that close (if any) is older than every installed lock's commit. A commit
created before a close and installed after a reopen therefore refuses. A state
read that fails or answers malformed refuses too, but deletes only the locks
this run created: a re-entered lock is the pull request's earlier hold, and
deleting it on an unconfirmed close would free a cell it may still hold.

**Limit: the race between `detect` and `lock`.** `detect` reads the locks and
`lock` takes them in a later job. A cell another pull request acquires in
between refuses the whole run in `lock`, naming the holder
(`` `<env>` / `<stack>` is locked by #<n>; wait for #<n> to merge or close, then
re-plan, or comment `shipmate unlock <env> --evict` ``), and no cell applies.
This is the one case in which a locked cell stops the pull request's other
cells. Commenting `shipmate apply` again, or "Re-run all jobs", recomputes: its
`detect` leaves that cell pending and the rest apply. "Re-run failed jobs" does
not re-run `detect`, so its `lock` tries the same cells again.

**Limit: every env-level is locked up front.** `lock` acquires the cells of
every env-level before `L0` starts. When an earlier level fails, the later
levels are skipped, and their cells stay locked to this pull request until it
merges or closes, or another pull request evicts them with
`shipmate unlock <env> --evict`.

**Every apply cell runs a pre-apply check.** "Re-run failed jobs" re-runs the failed wave
jobs and neither `detect` nor `lock`, and a first attempt's wave can wait hours on an
environment approval. So on every attempt each pre-merge apply cell first runs the
pre-apply check, read-only, as the wave job's first step, before its cloud
credentials. It refuses unless the pull request holds the cell's lock (an eviction,
or a release after a close, takes it away), with
`` apply aborted for `<env>` / `<stack>`: pull request #<pr> no longer holds its
cell lock (<why>); comment `shipmate apply` again to lock it or name its holder ``,
and unless the pull request is open at the applied head. Under `apply_mode: auto`
it also refuses unless the run attempt is `1`, the pull request is not a draft, the plan run detect bound is
still the head's newest autoplan and the run the cell's newest plan record names, that
run's pusher still passes detect's pusher check (§`apply_mode: auto`, same function and
wording), and the base branch's tip, read from its ref, is still the one detect read,
whatever the stack. Mergeability is not re-read: a base move refuses already. A read that fails refuses with
`` apply aborted for `<env>` / `<stack>`: could not confirm <what> (<why>); push,
or comment `shipmate apply` on the pull request ``, and every other mismatch names
itself with the same remedy; a closed pull request refuses with `nothing applies`. A refused cell assumes no cloud role, and the apply
result comment gives it the refusal, less its `apply aborted for` prefix, as the
blocked reason, or `could not confirm the pull request still allows this apply (see
the job log)` when no refusal arrived. The merge deploy passes no pull request, so
its cells skip the check.

**Release is reconciled, not queued.** On a merge, each run of the `release`
job and of the deploy's `summary`, re-runs included, deletes each lock the
merged pull request holds whose cell has no pending apply check. The deploy's
release step runs whenever `deploy-detect` found the merged pull request, before
and apart from its work queue, so either job alone releases every such lock,
even when the deploy applies no cell. The `release` job alone handles a close
without merge and a merge that starts no deploy. A deploy that completed a
cell's check and then was cancelled, or failed its delete, leaves a lock its
re-run still releases.

**Failure.** A lock read or write that fails refuses, in `detect`, `lock`,
each pre-merge apply cell, the `release` job, the deploy's release step (whose failure fails the deploy's
`summary`) and `--evict`. A release and an eviction continue past a delete that
refuses, then fail naming each lock left held. A failed release stays owed until
a re-run of that job succeeds. The plan comment's marker alone degrades: a lock read that fails there
warns, and the comment marks no cell.

**Permissions.** `detect` and each pre-merge apply cell read locks with their
`GITHUB_TOKEN` at `contents: read`. Every lock write mints an App token with `contents: write`
in a job bound to `shipmate-engine`: `apply.yml`'s `lock`, `release.yml`'s
`release`, `deploy.yml`'s `summary` and `comment-ops.yml`'s `ops`. An
installation that has not accepted `contents: write` fails each of those
mints, so every `shipmate apply` refuses at `lock` (`docs/github-app.md`
§Re-approve after permission changes).

## `apply_mode: auto`

An environment whose entry holds `apply_mode: auto` applies after its autoplan,
with no comment. The entry must also hold `review: false` (§Refusals): an
`auto` apply starts on a push, so there is no approving review to wait for. A
bare and a targeted `shipmate apply` reach an `auto` environment as they reach a
`bare` one.

**Dispatch.** After it writes the gate, a plan run's `summary` job dispatches
`.github/workflows/shipmate.yml` with exactly `{verb: "auto", ref: <head sha>,
pr_number: <n>}` when all of these hold (`scripts/auto-dispatch`):

- no newer plan run of the head superseded this one, and the sticky comment
  upsert did not find the head moved;
- the run is a `pull_request_target` run, an autoplan, on its first attempt: a
  `shipmate plan` run and a re-run dispatch nothing;
- the event payload's `action` is `opened` or `synchronize`, a push: an autoplan
  started by marking the pull request ready for review or reopening it, or with
  an unreadable payload, dispatches nothing, and `shipmate apply` applies it;
- the pull request is not a draft;
- the gate is `pending` over a completely published plan with an apply check
  pending;
- a changed cell of this run is in an `apply_mode: auto` environment of the
  default branch's table.

The dispatch token is an App token minted for it alone, with `actions: write`.
The consumer's `auto` job, selected by `verb: auto`, calls `apply.yml` with
`auto: true`; `detect` takes nothing else from the dispatch. `apply.yml`'s
`guard` refuses a `verb: auto` dispatch whose calling job does not pass
`auto: true`, which would otherwise run it as a bare apply, `auto: true` on
any other run, and a run without `auto: true` that is not a dispatch whose verb is
exactly `apply`, unreadable dispatch inputs included; `shipmate doctor` compares the `auto` job's `with:` against the
published one. A failed mint or
dispatch warns on the plan run and posts one reply, ``🔴 failed: auto apply not
started: the dispatch failed; comment `shipmate apply` ``. A run that cannot
decide (the table unreadable, a cell summary unparseable) warns and posts no
reply. Neither changes the gate.

**What an `auto` run applies.** It passes `apply.yml`'s `guard` and `review`
jobs as any apply dispatch does (§Comment-ops), then `detect` runs the bare form
over the pending cells of the `auto` environments. Every other pending
environment counts as not fully applying, so its `needs` successors are
skipped. A pending environment that is neither `auto`, `targeted` nor held is
left for a bare apply (`bare_envs`) and named in the result comment. Review
holds, cell locks, waiting cells and `targeted` exclusions are the bare form's.

`detect` refuses the run before any wave in these cases. Each refusal reading
`auto apply refused:`, except the moved head's, ends ``Comment `shipmate apply`
on the pull request to apply it.``

- **Actor.** The run's `github.actor` and `github.triggering_actor` must each
  equal `<slug>[bot]`, compared whole, where `<slug>` is the `app.slug` of the
  App-authored check runs on the head; a head with no App-authored check
  refuses. A `GITHUB_TOKEN` dispatch runs as `github-actions[bot]`, which the
  `guard` job admits and this check refuses. A human's "Re-run all jobs" of an
  `auto` run makes the human its triggering actor, so the re-run refuses;
  comment `shipmate apply` instead. "Re-run failed jobs" keeps the passed
  `detect`, so each wave cell's pre-apply check refuses any `auto` run attempt
  but `1`: ``an auto apply is never re-run (run attempt '<n>'): comment
  `shipmate apply` ``.
- **Mergeability.** With a pending cell in an `auto` environment neither held
  nor `targeted`, `detect` reads the pull request
  until GitHub reports `mergeable` as non-null, at most 12 reads 5 s apart.
  Every read refuses when the head is no longer `ref`: the new head's autoplan
  decides its own apply. The same-repository, draft and mergeable apply
  requirements then decide in their comment-time wording, except that the draft
  and still-computing refusals end ``comment `shipmate apply` ``. A `mergeable`
  still null after the last read refuses as still being computed.
- **Base tip.** The base branch's tip is read from `git/ref/heads/<base.ref>`,
  never from the pull request's `base.sha`: on a probed pull request `base.sha`
  kept the old tip for 20 s and more after a push to the base, while the branch
  ref answered the new tip at once. An unreadable tip refuses.
- **Provenance.** Every cell to apply must record one plan run; that run's
  `event` must be `pull_request_target`, its `head_sha` the head, and it must be
  the newest run listed by
  `actions/workflows/shipmate.yml/runs?event=pull_request_target&head_sha=<head>`,
  every page read. A cell whose newest plan record is a `shipmate plan` run
  therefore refuses the run; `shipmate apply` applies it under the comment-time
  requirements. A newer autoplan of the head that published no apply check
  refuses it too, which the record-based binding of the bare and targeted forms
  cannot see (§Apply-match fingerprint). Every cell's record must also name the
  event action `opened` or `synchronize`: a ready-for-review or reopen autoplan
  of the head dispatches nothing yet becomes its newest autoplan, and whoever
  clicked is no pusher. A record with no action, an older engine's, refuses too:
  ``auto apply refused: the plan records of <cells> name no push event action
  (opened or synchronize), so no push started that plan.`` The wave pre-apply
  check repeats this on the cell's newest record.
- **Pusher.** The plan run's `actor`, the pusher, must equal its
  `triggering_actor` and must have write access. A re-run of the autoplan keeps
  `actor` and names the re-runner as `triggering_actor`, so it refuses:
  ``not authorized: the autoplan ran as @<actor> but was triggered by
  @<triggering actor>, so it was re-run, and auto authorizes only the push
  itself; a writer comments `shipmate apply`.`` The run body carries no event
  action, so a ready-for-review or reopen autoplan is kept out by the dispatch
  condition above, not here. The permission is read with the `detect` job's `GITHUB_TOKEN` from
  `collaborators/<login>/permission` and decided by
  `authorize._permission_reason`, the function that decides a commenter:
  ``not authorized: `shipmate apply` needs write access to this repository, and
  the pusher @<login>'s permission is `<permission>`.`` for `read` and `none`,
  and ``... and GitHub reported the pusher @<login>'s permission as `<permission>`,
  which this engine does not admit.`` for any other value. An unreadable
  permission ends ``push again, or a writer comments `shipmate apply`.`` A
  pusher whose login ends `[bot]` refuses without a read: ``not authorized: the
  head was pushed by @<login>, a bot account, which auto never authorizes; a
  writer comments `shipmate apply`.``

With no cell to apply, neither the plan run nor the pusher is read.

**Overlap.** A cell `detect` would apply is held when the base branch changed its
stack, directly or through a module, since the merge base of the base tip and the
head. Terramate's change detection runs twice in one worktree. The base-side
listing runs on the base tip from that merge base and finds the module consumers
the base's tree knows. The head-side listing runs on the merge of the base tip
into the head (`git merge-tree`, with `merge.directoryRenames=conflict`), from the
head, so the base's changes are the only changes it sees; it finds a stack the
branch wires to a module the base changed, which consumes that module only on the
branch's tree. It holds no stack the branch alone wires for a base change the
branch already holds; the base-side listing still holds a stack the base changed,
whether or not the branch holds that change. The head-side listing is skipped when
the base changed no file. A merge conflict anywhere in the repository, not only in
a stack, refuses the whole run, a file the base added under a directory the branch
renamed included: the pull request's mergeable flag is read before the base tip,
so a base commit landing between the two reads can conflict. When the merge
conflicts or the listing fails, `detect` refuses with ``change detection on the
merge of the base into the branch failed: <cause>. Update the branch from the base
and push.`` and nothing applies; `<cause>` is git's conflict lines, or the failed
step's error. Terramate lists only the stacks the base tip still has, so a stack
whose files the base deleted or renamed since that merge base (`git diff
--name-status --find-renames`) is held too, and so is a stack holding a file the
branch renamed from a path inside a stack the base-side detection lists. Applying
its plan would revert the base's change (§Fan-out).
A held cell is not applied: its apply check and the gate stay pending, it stops its
environment's `needs` successors as a locked cell does, a cell that runs after it
waits, and the result comment names it with ``⚪ held, the base changed these
stacks after this branch left it, so their plans would revert that change:
<cells>. Update the branch from the base and push.`` The push re-plans, and its
autoplan dispatches again. The bare and targeted forms run no overlap check.

**`needs` predecessors.** An `auto` environment whose `needs` predecessor does
not fully apply this run is skipped, its checks pending, and the result comment
names the predecessor with its remedy by kind: `` `shipmate apply <pred>` `` for a
`targeted` one, an approving review for a held one, followed by
`` `shipmate apply <pred>` `` when it is also `targeted`, `a cell of it does not
apply this run; see its line below` for one holding a locked, overlap or waiting
cell, and `shipmate apply` otherwise (§Apply result comment).

**Wave re-check.** Each `auto` wave cell's pre-apply check (§Cell locks) also
refuses unless the plan run `detect` bound is still the head's newest autoplan
and the run the cell's newest plan record names, and the base tip, read from the
branch ref, is still the one `detect` read. The base comparison is whole-branch,
whatever the stack: a wave cannot run Terramate's change detection before its
credentials. It fails closed, so a base that moves while a wave waits on an
environment approval refuses that wave. Update the branch from the base and
push: a bare `shipmate apply` runs no overlap check.

**Visibility.** An `auto` run posts the apply result comment as a bare apply
does, under `### shipmate apply`, also when it applies nothing: its verdict and
lines name why, and a run that applied nothing and left a pending environment
for a bare apply, a `targeted` one or a skipped one is never `🟢` (§Apply result
comment). A
`detect` refusal shows in the comment's error fence.

**Permissions.** The consumer's `auto` and `apply` jobs grant
`pull-requests: read`: `detect` and the wave pre-apply check read the pull
request, and a called workflow cannot exceed its caller's permissions.

## Post-plan topology

The consumer's workflow is one file, `.github/workflows/shipmate.yml`: four
triggers, and seven jobs each gated on the event with an `if:` and each calling
one engine reusable workflow, SHA-pinned. Top-level `permissions: {}`; every job
declares its own. The plan job passes `SHIPMATE_APP_PRIVATE_KEY`,
`SHIPMATE_ENCRYPTION_KEY` and `SHIPMATE_SECRETS` by name (never
`secrets: inherit`) and one input, `runs_on`.
Everything a `uses:` line reaches is engine-owned,
SHA-pinned YAML.

| event or verb | job | engine callee |
|---|---|---|
| `pull_request_target` other than `closed`, or `workflow_dispatch` with `verb: plan` | `plan` | `plan.yml` |
| `pull_request_target` `closed` | `release` | `release.yml` |
| `issue_comment` | `comment-ops` | `comment-ops.yml` |
| `push` | `deploy` | `deploy.yml` |
| `workflow_dispatch` with `verb: apply` | `apply` | `apply.yml` |
| `workflow_dispatch` with `verb: auto` | `auto` | `apply.yml` |
| `workflow_dispatch` with `verb: unlock` | `unlock` | `unlock.yml` |

Each of those `if:` expressions is a contract literal, not a style choice:
`shipmate doctor`'s `shipmate.yml` probe holds all seven, keyed by job id, and compares
each one whole, because a wrong expression sends a verb nowhere and produces a dispatched run
that completes with every job skipped — green, and no work done.

The drift sweep is not in that file. Each drift workflow file holds one job,
`drift`, with no `if:`, calling engine `drift.yml` from the file's own
`schedule` and `workflow_dispatch` triggers (`docs/drift.md`).

Engine `plan.yml` is five jobs: `facts`, `detect`, `plan`, `summary`,
`gate-hold`. `facts` is `actions/pr-facts`, the single producer of every
pull-request fact the others decide on. `detect` and `plan` are untrusted: they
check out the pull request's own head and hold no App credential. `summary` and
`gate-hold` are the trusted jobs — bound to `shipmate-engine` with
`deployment: false`, like every engine job binding it, and neither
checks anything out. Every App-authored
surface listed above (apply checks, the gate, the sticky comments, drift
issues) is created by a job bound to that fixed GitHub Environment
(`docs/github-app.md` §Key-exposure boundary), each running at a ref that
satisfies its default-branch-only policy for a different reason.

**The calling job's name is a contract literal.** Each job displays its verb:
`plan` carries `name: plan`, `comment-ops` `name: comment`, `deploy`
`name: deploy`, and a drift file's `drift` job `name: sweep`; `release`, `apply`
and `unlock` carry no `name:` and display as their job id.
GitHub names a called workflow's check runs `<caller job> / <callee job>`, so
only a plan job named `plan` produces `plan / <env> / <stack>` plan
cells and the `plan / summary`, `plan / facts` and `plan / detect`
names beside them. `scripts/summary-comment` resolves each cell line's `plan` link
by that exact name. A job named anything else plans correctly and every such
link falls back to the workflow-run page; `shipmate doctor` reports it.

**Adopting this topology takes one ungatable pull request.** A
`pull_request_target` run uses the workflow file on the default branch. On the
pull request that adds `shipmate.yml`, the default branch holds no file
declaring the trigger, so that pull request produces no plan run and no
`shipmate / gate`. `scripts/onboard` therefore creates the gate ruleset only
once the file is on the remote default branch, so that pull request merges
without one; re-running `onboard` after the merge creates it, and every pull
request following it gates normally. A ruleset created by hand follows the same
order: create it after that pull request merges.

For a repository migrating from another TACO, that same pull request is
checked by neither system: the outgoing tool's checks are being removed
in it, and shipmate's cannot run on it yet. Review it as the one change nothing
plans.

**Each job's `permissions:` block must be a superset of every per-job block in
the workflow it calls** — for the `plan` job, `contents: read`,
`pull-requests: read` and `id-token: write` between engine `plan.yml`'s
jobs. A called workflow's permissions are capped at each `uses:` boundary, so a
caller that grants less kills the run at startup: no job, no log, no annotation,
and no `shipmate / gate`. Fail-closed, since the pull request cannot merge
without the gate, but nothing on the run page says why. The file's top-level
`permissions: {}` grants nothing to fall back on, which is the point: a job
that loses its block gets nothing rather than everything the file granted.
`scripts/tests/test_docs_yaml_parses.py` compares each published calling job's
block against the union its callee requests. Copy the reference file whole
rather than trimming a `permissions:` block.

Under `pull_request_target` a plan run's `head_sha` and `head_branch` are the
pull request's head commit and branch, not the base branch's — which is why
each `plan` matrix job's own check-run lands on the pull request, with nothing
in the engine placing it there. What *is* base-branch under this trigger is the
checkout: `GITHUB_SHA` and `GITHUB_REF` name the base, which is why the engine's
`detect` and `plan` jobs pass `ref: ${{ needs.facts.outputs.head-sha }}`
explicitly. The two are routinely confused; they are opposite sides of the same
trigger. The file's other six jobs are skipped under that trigger (on `closed`,
`release` runs and `plan` is skipped), each
completing as a `skipped` check-run on the same head; nothing functional depends
on them, and `scripts/mirror-checks` does not copy them.

A dispatched plan has neither side: its `head_sha` is a commit on the ref it
was dispatched on (the default branch), and nothing on the run identifies the
pull request it was dispatched for. So the checkout's `ref:` is what makes it a
plan of the pull request there too, and every check-run its own jobs create
attaches to the dispatch ref rather than to the pull request — which is why the
`summary` job mirrors this run's completed `plan / ` checks onto the head
commit on an `on-demand` run.

**Requirement: no job in engine `plan.yml` other than `summary` and `gate-hold`
may reference a `shipmate-engine` secret.** Under either trigger every job in that file runs at
a ref the environment's policy admits — the base ref, or the ref the plan was
dispatched on — and the `plan` job's `environment:` is chosen by
branch-authored `env/*` tags, so the file naming no such secret outside
`summary` and `gate-hold` is what keeps the key out of branch reach.
`scripts/tests/test_cells_hold_no_app_key.py` is the guard;
`docs/github-app.md` §Key-exposure boundary has the reasoning. The consumer's
`plan` job names `SHIPMATE_APP_PRIVATE_KEY` in its own `secrets:` block, and
that copy of the file is the base branch's or the default branch's — never the
pull request's own.

The five jobs:

- **`facts`** (engine, either trigger) — `actions/pr-facts` alone. It authors
  nothing and mints no App token; it holds `pull-requests: read`, the one grant
  on this path a pull-request event does not need, because a dispatched run's
  payload carries no pull request and the number it was dispatched with is the
  only thing it can look one up by. Nothing else in the file reads the event
  payload, and nothing anywhere reads a head SHA or a head repository from a
  dispatch input: the number is all a dispatcher states, and a number cannot lie
  about its own head repository.
- **`detect` and `plan`** (engine, either trigger) — they upload plan artifacts
  and cell summaries; they author nothing and mint no App token. `detect` binds
  no environment; `plan` binds only the plan environment for the cell it is
  planning, never one holding an App credential. `pull_request_target` checks
  out the base by default, so both jobs name the pull request's head commit on
  their checkout's `ref:`, read from `actions/pr-facts`, which resolves it under
  either plan trigger; without it they would plan the base branch and report a
  clean plan for a pull request they never read. `actions/build-matrix` refuses
  that: on every trigger it compares the head SHA the run states against the
  commit it is running on. On a pull-request event it also refuses a checkout
  with no `.github/workflows/shipmate.yml` — the one path this contract lets
  the consumer's workflow live at. `actions/build-matrix` fails `detect`
  outright unless the run states a head repository equal to the running
  repository: fork pull requests are not planned, and no input permits one
  (`docs/hardening.md` §Contributors without push access states what a fork's
  plan cell would receive, and the layers that keep it out). No
  `shipmate / gate` is ever written for a fork head, so the refusal is loud
  rather than an empty matrix. The `head-repo` value the guard keys on is
  produced by `facts` in this same file rather than stated by the consumer, so
  there is no wiring a consumer can get wrong, and no event name is involved:
  the drift path already triggers on both `schedule` and `workflow_dispatch`,
  so a dispatched plan would be indistinguishable from a manual drift run. The
  drift path is unaffected because engine `drift.yml` states that it is a sweep
  with no pull request (`sweep: "true"`), which is the only opt-out
  and appears in no other engine workflow.
- **`summary`** (engine, bound to `shipmate-engine`) — it calls
  `actions/summary`, which downloads this same run's cell summaries and, under
  an App token minted inside that environment, publishes the results. This is
  what creates the pending `apply / <stack> / <env>` checks, the sticky plan
  comment, and the `shipmate / gate` status, and — on an `on-demand` run — the
  mirror of this run's completed `plan / ` checks onto the pull request's
  head, the `⚪ nothing planned` comment when no cell was planned, and the
  collapse of the started reply that links the run (§Comment-ops).
  `pull_request_target` evaluates at the base branch ref and a dispatched run at
  the ref it was dispatched on, either of which satisfies the environment's
  policy. It reads every fact it decides on from `needs.facts.outputs`, and the
  rest from the two other jobs' results; nothing is recovered from artifacts or
  from a second API lookup.
- **`gate-hold`** (engine, bound to `shipmate-engine`, `permissions: {}`) —
  runs only when `summary` was cancelled, which the gate-writer concurrency
  group does to a pending `summary` (§Comment-ops, "The aggregate gate"). It
  calls `actions/gate-refresh` to write a hold reading "re-plan this pull request
  (comment shipmate plan or push): its plan summary was cancelled before it
  published". A `summary` refused by its fork `if:` is `skipped`, not
  `cancelled`, so `gate-hold` holds the App key only for a head `summary`
  admitted. It runs on `ubuntu-slim` whatever `runs_on` the caller passes, as
  `facts` does.
- **`apply.yml` / `apply-env-level.yml` /
  `deploy.yml`** (engine, reached through the `apply` and `deploy` jobs —
  `workflow_dispatch` via comment-ops, or `push` to the default branch) — the
  jobs that mint an App token (reading the review decision, acquiring cell
  locks, completing apply checks, refreshing the gate, posting the apply result
  comment, releasing a merged pull request's cell locks) are likewise bound to
  `shipmate-engine`.
- **`release.yml`**'s `release` job (engine, reached through the consumer's
  `release` job on `pull_request_target` `closed`) — binds `shipmate-engine` to
  release the closed pull request's cell locks (§Cell locks). It checks nothing
  out and reads only the event payload: the pull request's number, head SHA,
  `merged` and `closed_at`. Like `summary`, it evaluates at the base branch
  ref.
- **`comment-ops.yml`**'s `ops` job (engine, reached through the consumer's
  `comment-ops` job on `issue_comment`) — binds `shipmate-engine` for comment
  authorization and for the `workflow_dispatch` that kicks off an apply.
  `issue_comment` evaluates at the default branch's tip, never a PR head, so it
  satisfies the policy the same way `push` does.
- **`drift.yml`**'s `issues` job (engine, reached through a drift workflow
  file's `drift` job on its `schedule` or a `workflow_dispatch`) — binds
  `shipmate-engine` to author the drift Issues, and a scheduled or manually
  dispatched run evaluates at the default branch.

Nothing matches on the workflow's `name:`. Doctor reads the consumer's
workflow files for five probes — whether each file parses as YAML, stale engine
pins, `pull_request_target` triggers, whether any file calls engine `drift.yml`,
and `shipmate.yml`'s wiring, whose checks of the plan-calling job name, dispatch wiring, event routing, the `auto` job's `with:` and `pull_request_target` types observe whether the plan comment's
per-cell links will resolve, whether a commented verb reaches anything at all,
whether the job it reaches is the one that verb names, whether the autoplan's
dispatch runs the auto form, and whether every pull
request event the fence routes starts a run, and whose sixth
check reports a drift job in it — and they report rather than fail.

The file path is still load-bearing, and nothing diagnoses a rename as the
cause: `actions/build-matrix` refuses a checkout that has no
`.github/workflows/shipmate.yml`; `actions/dispatch` sends every commented verb
to that literal filename, naming the verb in the dispatch body, so a renamed
file is dispatched nowhere, and the pull request is at most told that the
dispatch failed (§Comment-ops), with the API's refusal left in the
comment-handling run; and doctor keys on the exact name for its `pull_request_target`
exemption and for its `shipmate.yml` probe, which reads that one path.
Rename the file and planning is refused from that commit on, the `shipmate.yml`
probe reports it could not read `.github/workflows/shipmate.yml`, and the
renamed file starts drawing doctor's own `pull_request_target` warning. Each
symptom surfaces on its own — the refusal names the path it looked for — but
none of them names the rename.

No apply path matches on it to find work already planned: a dispatched, bare or
post-merge apply reads each cell's plan run from that cell's own apply check, so
a rename strands nothing that was planned before it. It does stop the dispatch
itself, for apply exactly as for plan.

The trust decision lives on the `summary` job's `if:`, in engine-owned,
SHA-pinned YAML, and so do the facts it reads:

```
!cancelled() &&
needs.facts.outputs.head-repo != '' &&
needs.facts.outputs.head-repo == github.repository
```

The fork clause yields to nothing: no trigger, `on-demand` included, rescues a
fork's run. An empty `head-repo` — the shape a failed `facts` job produces — is
a refusal rather than a pass, which is why `facts` is its own job and not
`detect`'s first step: `detect` can fail on a fmt check or stale codegen, and
`summary` must still be told which head to gate.

The job has no draft clause, because skipping a draft is autoplan's economy
rather than a trust decision. A draft's autoplan skips `detect` and `plan` and
still reaches `summary`, which writes `shipmate / gate` pending with the
description "the pull request was a draft when this run started, so nothing
was planned; comment `shipmate plan`, or mark it ready". Nothing greens that
status: `gate-refresh` greens only over completed apply checks, and a draft run
creates none. The draft status is written only on a head that carries no
`shipmate / gate` status yet: an existing one is kept, and a failed read of it
writes nothing. The sticky comment is left as it is.

**A consumer states none of these facts and can weaken none of them.**
Producing them and comparing them are the same file, so a consumer has no site
at which to write a constant `head-repo: ${{ github.repository }}`, a literal
`is-draft: false` or a literal `on-demand: true`, each of which would state the
safe answer for every run, forks and drafts included. The consumer's file passes
secrets and permissions; the engine decides everything else.

`summary` deliberately does not require `detect` or `plan` to have succeeded: a
failed detect or plan must still produce a red gate with an explanation, because
no gate at all is a pull request nobody can diagnose. `detect` and `plan` keep
the draft skip as their own condition — not a draft, *or* named on demand —
which is a cost control (it stops a draft burning runners), not a security
property, and is why a requested plan runs on a draft at all.

Why `summary` is bound to `shipmate-engine` rather than trusting the trigger
alone: `docs/hardening.md` §Contributors without push access for a fork's run,
and `docs/github-app.md` §Key-exposure boundary for a branch-authored workflow.

## Consumption

- Consuming repositories and workflows pin every shipmate reusable workflow by
  commit SHA, never by a tag or branch name (for example,
  `uses: ship-iac/shipmate/.github/workflows/plan.yml@<full-commit-sha>`, not
  `@v1` or `@main`). This guarantees that a workflow's behavior cannot change
  without an explicit, reviewed bump of the pinned SHA in the consuming
  repository.
- A pinned SHA may carry a trailing `# vX.Y.Z` comment naming the release
  that SHA belongs to
  (`uses: ship-iac/shipmate/.github/workflows/plan.yml@<sha> # v0.1.0`).
  The comment is for human readers and for Dependabot's own bookkeeping; the ref
  that resolves is always the SHA. shipmate applies the same convention to the
  third-party actions it pins internally.
- `.github/workflows/` is protected by a `CODEOWNERS` entry, so changes to
  workflow files (including pin bumps) require review from the designated
  owners before merge. This is review hygiene, not a security boundary: a
  workflow file added or modified on a feature branch runs — with the
  repository's secrets — as soon as it is pushed, long before `CODEOWNERS`
  applies. Restricting who can push, and restricting pushes that touch
  `.github/workflows/**`, are the controls that act at push time; see
  `docs/hardening.md`.
- The engine holds no pins of itself. Every engine step calls its action as `$/actions/<name>`,
  which GitHub resolves in this repository at the commit the consumer's `uses:` resolved to, so
  the consumer's one pin names the whole tree that runs. The consumer surface is the six
  reusable workflows; the composite actions are engine-internal.
- Every engine reference moves in one change: a repository that bumps some refs and leaves
  others behind runs two engine versions against one contract.
  `docs/releasing.md` § Re-pin a consumer moves a consumer's pins together. The SHA
  of record for a release is named in that release's section of `CHANGELOG.md`.
- A release tag may be annotated, so `git/ref/tags/<tag>` can return the tag object's SHA,
  which a workflow cannot check out. Resolve a tag with
  `gh api repos/ship-iac/shipmate/commits/<tag> --jq .sha`, or locally
  `git rev-parse <tag>^{commit}`; both resolve an annotated and a lightweight tag alike.
- shipmate publishes a GitHub Release per release SHA. `shipmate doctor` reports a pin that
  differs from the latest release; a pin at an untagged commit has no release to compare
  against.

## Runner prerequisites

- shipmate's actions are composite actions: their steps run under `bash`
  and call Python scripts, `git`, `curl`, `jq`, `openssl`, and the `gh` CLI. A
  runner must therefore provide: `bash`, `python3` (Python ≥ 3.12, with
  PyYAML ≥ 6 importable by it), `git` ≥ 2.38 (the `auto` overlap check runs
  `git merge-tree --write-tree`), `curl` ≥ 7.71, `jq`, `openssl`, `gh`, and a
  coreutils `sha256sum`, GNU or uutils (`setup` hashes the Terramate download
  with it; `apply-cell` hashes the rendered plan from stdin and reads the first
  field). The Ubuntu 26.04 images ship uutils (probed 2026-10-17 on
  `ubuntu-26.04`, x64, and 2026-10-18 on `ubuntu-26.04-arm`).
- Every GitHub-hosted Ubuntu image from 24.04 on, `ubuntu-slim` included,
  provides these tools other than PyYAML. PyYAML differs per image: the
  `ubuntu-24.04` and `ubuntu-26.04` x64 images ship it (probed 2026-10-15), the
  `ubuntu-26.04-arm` image ships Python 3.14.4 and PyYAML 6.0.3 (probed
  2026-10-18), and `ubuntu-slim` has none, so there it arrives through the
  install step below.
  The `ubuntu-slim` image's
  [included-software list](https://github.com/actions/runner-images/blob/066b3201a74f4551f70c221a71c49746d02c0864/images/ubuntu-slim/ubuntu-slim-Readme.md)
  names the GitHub CLI. The CLI is load-bearing: the default-branch probe in
  engine `drift.yml`'s `detect` job calls `gh api` before that job's `setup`
  step, and several `ubuntu-slim` jobs on the plan, apply and deploy paths
  call `gh api`. Self-hosted runners must preinstall these tools.
- `plan.yml`, `drift.yml`, `apply.yml`, `deploy.yml`, `unlock.yml`,
  `comment-ops.yml` and `release.yml` take a `runs_on` input, default `ubuntu-26.04-arm`;
  `apply.yml` and `deploy.yml` pass it to `apply-env-level.yml`, where it is
  required. It sets the runner of every engine job but nine, which run on
  `ubuntu-slim` whatever `runs_on` the caller passes: `plan.yml`'s `facts` and `gate-hold`,
  `apply.yml`'s `guard`, `review` and `summary`, `deploy.yml`'s `summary`,
  `apply-env-level.yml`'s `snapshot` and `complete`, and `unlock.yml`'s
  `guard`. `docs/aws.md` §Runner choice lists the jobs per workflow. A
  `runs_on` label the account lacks leaves the jobs that take `runs_on` waiting
  for a runner. An account that cannot use `ubuntu-slim` leaves the nine
  `ubuntu-slim` jobs waiting for a runner.
- PyYAML is the scripts' one third-party dependency; they parse the config
  and workflow files with it. Before the first step that parses YAML, the
  engine's `python-yaml` action checks that `python3` imports it and, when it
  does not, installs `python3-yaml` with `sudo -n apt-get`. That works on a
  Debian or Ubuntu runner with passwordless `sudo`, as GitHub-hosted Ubuntu
  runners are. Any other runner must preinstall PyYAML ≥ 6 for its
  `python3`. The install never fails the step: when it does not take, the step
  warns, and the script that needs the parser refuses, naming this section;
  `shipmate doctor` notes it once instead and skips the checks that parse YAML.
  Nothing is `pip install`ed.
- Nothing in the engine installs or pins a Python, so `scripts/env-config`
  checks `sys.version_info` before reading the config and refuses with the
  version it found and this section. A `runs_on:` image older than 3.12 —
  `ubuntu-22.04` ships 3.10 — fails at `detect`.
- Engine steps use GitHub's `$/` self-repository syntax, which needs runner 2.336.0 or newer,
  and shipmate supports it on github.com only. A self-hosted runner below that version, and
  GitHub Enterprise Server, cannot run the engine. The error such a runner shows is unmeasured.
- Terramate and OpenTofu are not assumed to be on the image: the
  `setup` action installs the versions the engine release declares in its own
  root-level `VERSIONS` file, read at the commit the consumer pins. Moving to
  other versions is a pin bump; the action takes no version input. A missing
  file or line fails the step rather than falling back to the installer's
  latest. `VERSIONS` also pins the sha256 of each tool's Linux `x86_64`/`amd64`
  and `arm64` release asset, as `sha256sum` lines copied from the release's own
  checksum file. Each download is checked against its pin: a mismatch, or a
  version or platform with no pin, fails the step rather than installing
  unverified.

## Fan-out

- One unit of work is one stack × one environment. A repository with N
  stacks and M environments (accounting for which stacks are tagged into
  which environments) fans out into up to N×M plan units and N×M apply
  units, each with its own check (see Check names, above).
- The plan fan-out is bounded at 256 cells (`build-matrix`'s `MATRIX_LIMIT`,
  the GitHub Actions matrix limit). Above it `build-matrix` refuses
  and `detect` fails the run before any cell starts.
  The way past it is to split the change across several pull requests: the
  matrix is built over `terramate list --changed` from the merge base of the
  pull request's base and head, so a narrower diff is a smaller matrix. A
  branch behind its base plans only the stacks its own commits change, not
  stacks only the base changed. A stack both changed still plans the head's
  tree against state holding the base's applied change, so its plan shows
  that change reverted: update the branch from the base before applying it.
  Splitting cannot help when the fan-out comes from a one-line
  edit to a shared local module — that correctly marks every dependent stack
  changed and is one atomic change by nature — and there the only lever is to
  reduce the number of environments in play. A drift sweep's limit counts the
  cells its `tags` query keeps, so the way past it is more drift workflow files
  with narrower queries (§Tag grammar). A targeted `shipmate apply <env>`
  is not a way past it: the ceiling is enforced in the plan fan-out, so a run
  that trips it produces no reviewed plan artifact for any apply path to use.
- Plans fan out flat: all applicable plan units for a pull request run with no
  ordering dependency between them, up to `max_parallel` at a time
  (§Environment table).
- Applies run in waves: the `after` relationships between Terramate stacks
  form a DAG over the full run-graph, and applies execute in topological
  levels of that DAG — all units at one level must complete before the next
  level's units start. A wave spans every environment of its env-level (§Env
  apply order), so `after` orders cells across environments: a cell waits on
  every lower wave of its env-level, in any environment. A cycle in the
  `after`/`before` graph, a stack ordered after itself included, has no first
  level. Every plan but a drift sweep refuses it, a plan with no cell
  included. Every apply form and the merge deploy refuse it. Each refuses when
  it reads the run-graph, before any other ordering check and before any cell
  runs, and the deploy's refusal also lists the merge's pending cells. The read
  covers the whole tree, so a cycle anywhere refuses every pull request's plan.
- A cell `(stack, env)` runs after these cells of each stack its stack is
  `after`, directly or transitively. "The same environment" below is the
  environment of the cell each step walks from, not only the starting cell's:
  - the same environment, when the upstream stack is tagged into it;
  - otherwise each of the upstream stack's environments whose env-level is at
    or before the dependent cell's, the cell the walk started from: `needs`
    orders a later instance after the dependent. When none is, every one of its
    environments, which the inversion refusal reads (§Env apply order);
  - none, when the upstream stack carries no `env/*` tag: the order passes
    through it to its own upstream stacks, in the same environment.

  The walk covers every stack and tag of the tree, so an unchanged stack in the
  middle of a chain does not cut it.
- Plans do not follow `after`: every plan reads its upstream's current state.
  A change where the dependent reads what its upstream changes (an output, a
  data source lookup) needs two pull requests: the upstream first, applied,
  then the dependent. Nothing refuses the combined pull request: a saved plan
  goes stale only when its own state changes, not its upstream's.

## Plan artifacts

Each planned stack × environment uploads its reviewed plan under a name built
verbatim as:

- `plan.<env>.<slug>`

where `<env>` is the environment name and `<slug>` is the Terramate stack
path with every `/` replaced by `-` (e.g. `stacks/app` → `stacks-app`, so
`(stacks/app, dev-eu)` → `plan.dev-eu.stacks-app`). plan-cell creates it and
apply-cell downloads it — both construct the name forward from the
`(env, slug)` pair; no component reverse-parses it. No detect matches on it
at all: the apply workset comes from the head's own apply checks.

The delimiter is `.` and the environment comes first on purpose. Terramate
accepts `.` in a tag, but the engine refuses an env name outside the
environment-name charset, which has no `.`, where tags become environments
(`build-matrix.env_membership`) and where a dispatched env arrives
(`apply-detect.validate_env`). So the first `.` after the `plan.` prefix is
always the env↔slug boundary. This makes the name
unambiguous across all `(slug, env)` pairs. A delimiter that can appear in both
fields cannot: with `-`, `(stacks/app-dev, eu)` and `(stacks/app, dev-eu)` would
both render `plan-stacks-app-dev-eu`, letting apply-detect enrol the wrong stack
into a wave. A slug may itself contain `.` (a path character); that is harmless
because the name is only ever built forward, never split. Two distinct stack
paths that slug to the same value still collide by construction and fail loud
in `build-matrix`, at matrix construction — before any artifact exists, so the
plan run refuses up front rather than an apply discovering the clash afterwards
(rename so the path→`-` slug is unique). Every path that builds a matrix
carries it: the plan and deploy paths over their changed set, the drift path
over the whole tree, and `shipmate unlock` over the target environment.

The name spans the plan and apply paths:
`plan-cell` (the uploader) and `apply-cell` (the downloader) each run as
`$/actions/<name>` at the commit of the engine reference that called them, and
§Consumption's one-change rule keeps every engine reference at one commit.

## Apply summary artifacts

Each attempted-or-blocked apply cell uploads its outcome under a name built
verbatim as:

- `apply-summary.<env>.<slug>`

using the same dot-delimited, env-first grammar as `plan.<env>.<slug>` above
(the `.` delimiter is unambiguous for the same reason: the engine refuses an
env name containing `.`). `apply-cell` uploads it (`if: always()`, so a blocked cell
still reports); `actions/apply-summary` downloads every `apply-summary.*`
artifact for the run with the glob pattern `apply-summary.*`. It contains
verbatim:

- `cell.json` — always present, keys `stack` (the Terramate stack path, displayed
  and fed to the check-name construction), `environment`,
  `result` (one of `applied`, `failed`, `blocked`), `reason` (which fail-safe
  blocked it, or why an earlier step failed first; the empty string for
  `applied`/`failed`).
- `apply.txt` — the apply step's combined stdout+stderr. The cells run
  terramate with `--quiet`, so its routine lines (`terramate: Entering stack in`
  and `terramate: Executing command`) never appear; its `Error:` lines do.
  Present only when the apply step actually ran (absent for a cell blocked
  before then, a failed init and a plan text that did not match what was
  reviewed included).

`apply-cell` (writer) and `scripts/apply-comment` (reader, via
`actions/apply-summary`) are pinned by the same SHA in the consumer's
`shipmate.yml`, on its `apply.yml` reference. The reader
fails loud on a `cell.json` missing schema keys or carrying an out-of-enum
`result`.

## Plan comment

The `summary` action maintains at most one sticky comment per pull request,
identified by the HTML marker written verbatim as the comment's first line:

- `<!-- shipmate:summary -->`

A run whose cell count is zero writes the no-changes body only when that
zero means "no stacks changed" — `detect` reported a planned count of zero.
Every other zero (a planned count that disagrees with the cell summaries
actually read, a failed `detect`, plan cells that all failed
before uploading a cell summary, a failed `cell-summary.*` download) leaves the
plan unknown. Every hold the summary's gate decision writes (a `detect` that
did not succeed, incomplete evidence) replaces the comment with the held body,
which says why and makes no claim about any plan, and creates the comment when
the pull request has none. `gate-hold`'s hold (below) leaves the comment as it
is. What a summary job that fails part way leaves on the comment is stated with
the publish hold (below). In order, blank lines between the parts:

- the marker and the header `### shipmate plan`;
- `🔴 the gate is held`, then the verdict line's
  `at [<sha7>](<commit url>) in [run #<n>](<run url>)` ending (below);
- the hold reason: the gate description before its 140-character cut, at most
  4,000 characters, in a plain backtick fence longer than any backtick run in
  it, so a stack path it names shows as written and nothing in it renders;
- when `detect` succeeded, one line per planned cell that left no cell summary,
  sorted by environment then stack: `🔴 <stack> (<env>): no plan summary read`,
  linking the cell's `plan / <env> / <stack>` check, or the run when the
  head has none. At most ten, then `and N more cells have no plan summary`. The
  cells are `detect`'s planned `cells` the run read no summary for: a cell whose
  plan matrix job did not succeed and a cell whose summary download failed read
  the same. When `tofu init` or `tofu plan` failed with an `Error:` diagnostic,
  the cell's line is followed by the first one (from `init` or `plan`), colour
  removed and cut at 1,000 characters, in a plain backtick fence longer than any
  backtick run in it. The cell's `cell-error.<env>.<slug>` artifact carries it:
  the first OpenTofu `Error:` diagnostic in the cell's `init` or `plan` log from
  its latest attempt that failed inside plan-cell. Any other plan-cell failure,
  one whose `init` and `plan` logs hold no OpenTofu `Error:` diagnostic,
  records an empty error. When an attempt uploads no error file (it failed
  before plan-cell ran: checkout, setup, credentials; it was cancelled or timed
  out; or the error write or upload step failed), the cell keeps the error an
  earlier attempt recorded, if any. When an earlier attempt of this run
  recorded the error, `recorded by attempt <n> of this run` sits between the
  line and the fence. A cell whose artifact did not arrive or carries an empty
  error shows its line alone. The cell's plan job also carries an `::error::`
  annotation with it. For the autoplan that job is the linked check and
  `shipmate doctor` harvests it. An on-demand `shipmate plan`
  keeps it on its own run: `doctor` reads only the autoplan's cell checks and
  the App-mirrored head check carries no annotations. A drift or apply cell's
  failed init, and a drift cell's failed plan, annotate the same way;
- the help hint ``Comment `shipmate help` for the available commands.``

The held body only explains a red gate, so it degrades: a failed render,
listing or write warns "the sticky plan comment was not updated with why the
gate is held; the gate description says it", and the job still writes the gate.
A run whose plan job was cancelled writes no final gate (below) and keeps the
comment as it is, with a warning; a draft autoplan keeps it too (§Post-plan
topology).

Every comment write, the held body included, reads the pull request's head
once the comment listing shows a write follows, and writes nothing when the pull
request has moved past the head this run planned, with a notice: the comment is
per pull request, and a re-run of an older run would otherwise replace the newer
head's comment. A run that writes nothing (no comment yet, nothing changed)
reads no head. A failed read warns and writes nothing for the held body, and
fails the job ("could not read the head of pull request #<n>") before any apply
check or gate is written for the plan body.

Where the zero *does* mean no stacks changed, the suppression is create-only: an
existing comment is always updated to the no-changes body, because a pull
request that planned changes and then pushed them away must not keep displaying
applies that no longer exist. With no comment yet, none is posted — a docs-only
or engine-pin-bump pull request carries no shipmate comment — with four
exceptions. A run where `doctor` emitted a warning still posts: doctor's
findings are annotations with no file/line, so they render only on the run page
(see §Comment-ops), and this comment's doctor line links that run from the pull
request. `::notice::` findings do not trigger the exception: they are
informational, and would put a comment on every quiet run. Nor do warnings from
the repository-settings probes (the gate ruleset, the review rule and
`CODEOWNERS`): they hold until an admin changes the settings, so they would put
a comment on every quiet pull request. `shipmate doctor` and the run page still
report them. A run whose comment carries the unmanaged line also posts: a pull
request that changes only stacks with no `env/*` tag plans nothing, and the
comment is where it says so. When the line is left out for size (below), this
exception does not apply. A run whose comment carries the lost pairs line also
posts: a pull request that removes one `env/*` tag from a stack that keeps
another drops that stack / environment pair, and the comment is where it says
so; the same size rule applies. A run a `shipmate plan` command started also
posts: the comment is that command's answer on the pull request (§Comment-ops).

An existing comment is edited in place on every plan run but a draft autoplan, a
cancelled plan job and a run whose pull request moved to a newer head (comment
lookup is marker +
any Bot author — the shipmate App's bot login is derived from the registered
App name, which a consumer org may have had to slug differently than
`shipmate[bot]`), so GitHub's comment revision history doubles as the audit
trail of previous plans for the PR.

Structure, in order: the marker, the header `### shipmate plan`, a blank
line, the verdict line, a blank line, the doctor line and a blank line when
`doctor` counted a warning, the unmanaged line and a blank line when
the pull request changes an unmanaged stack, the lost pairs line and a blank
line when it untags a stack / environment pair, then per environment its
heading and one line or fold-out per planned stack in it, then a blank line
and the command section when it shows (below). The environments
follow `detect`'s `env_order` output, apply order: the env-levels of the
table's `needs`, ties broken by name. An environment the output does not name
follows them by name, and an empty or invalid output (the invalid one warns)
orders every environment by name. In each environment the changed cells come
first, then the rest, each group sorted by stack, so the fold-outs sit together
above the unchanged lines. The plan body has no
footer and no help hint, whether or not `doctor` warned: both verdicts are
normal results and the verdict links the run. The one exception is the command
section, which shows only when the table's `command_hints` is `true`, a cell
changed and the pull request is not a draft (`shipmate apply` refuses a draft):

```
<details><summary>shipmate commands</summary>

Comment `shipmate apply` to apply this plan.

Comment `shipmate help` for every command.

</details>
```

The held body and a body with no changed cell never carry it. The held body
above ends with the hint. `doctor` records its decision once, as its `warned`
output, which the post-or-skip rule reads, and its `warnings` output, the count
the doctor line shows: both leave out the repository-settings probes' warnings,
a settings probe that could not run included, and count any other probe that
could not run when its note is a warning. When `doctor` fails, the doctor step
writes `warned=false` and `warnings=0`. The build step records
whether the unmanaged line and the lost pairs line rendered, as its `unmanaged`
and `lost` outputs, which the same rule reads.

- **Verdict line.** `⚪ nothing planned` when the run planned no cell (an
  autoplan posts it only under the exceptions above, a `shipmate plan` command
  always), `🟢 no changes` when no cell changes, else `🟡 N of M cells change`, then
  `at [<sha7>](<commit url>) in [run #<n>](<run url>)`. `<sha7>` is the first
  seven characters of the planned head commit and `<commit url>` is
  `<server>/<owner>/<repo>/commit/<sha>`; `<n>` is the run number and
  `<run url>` the run's page. When that commit is not a 40-character lowercase
  hex SHA the line ends `at an unknown commit in [run #<n>](<run url>)`. The
  line reads the cells only: every failed plan job holds the gate, and a held
  gate posts the held body instead. With no cell, the skipped `plan` matrix
  job's check shows its name expression literally
  (`plan / ${{ matrix.environment }} / ${{ matrix.stack }}`, skipped), a
  platform behaviour no `if:` or `name:` change removes.
- **Doctor line.** `⚠️ shipmate doctor found N warning(s) beyond repository
  settings in this run: see [the run](<run url>)`, under any verdict. `N`
  leaves out the repository-settings warnings the linked run also shows. When
  the cell lines leave it no room under the 65,536-character cap, it is left
  out with a warning.
- **Unmanaged line.** `⚪ N changed stack(s) carry no env/* tag and are not
  managed by shipmate: <paths>`, naming at most ten paths within 1,000
  characters, then `, and M more`. When not even the first path fits, it ends
  `; their paths are too long to list here.` When the cell lines leave it no
  room under the 65,536-character cap, it is left out with a warning.
- **Lost pairs line.** `⚪ N stack / environment pair(s) lost their env/* tag
  in this pull request and are no longer planned or deployed: <pairs>`, each
  `<stack> / <env>`, for the stacks that keep another `env/*` tag; a stack that
  lost its last tag is on the unmanaged line instead, so no stack is named on
  both. Same caps and fallback as the unmanaged line, ending `; their pairs are
  too long to list here.` A drift sweep and a run with no pull-request base
  have no such line. On both lines each name is escaped like a cell line's
  stack, `@` included.
- **Environment heading.** `#### <env>: N cells, M changed` (`1 cell` for one),
  with a blank line on both sides, `<env>` escaped like a cell line's.
- **Cell line.** `<circle> <stack>: <state> <a href="<url>">plan</a>`, under its
  environment's heading; the held body's lines and a plan cell's own step
  summary line name the environment, `<circle> <stack> (<env>): <state>`.
  The circle is 🟢 for no changes and 🟡 for changes, deliberately two-state: a
  destroy count also covers ordinary replacements, so impact is carried by the
  counts rather than by a severity colour. `<state>` is `no changes`, or for a
  changed cell `+a ~c -d`, then `, N import` and `, N forget` when either count
  is above zero, or `+? ~? -?` when the counts could not be derived. `<url>` is
  the cell's `plan / <env> / <stack>` plan-job check run, built forward from
  the cell's `(stack-path, environment)` pair using the check-name grammar
  above; when the check run cannot be resolved, it is the workflow-run URL. The
  link is raw HTML because a Markdown link renders literally inside
  `<summary>`. `<stack>` and `<env>` are escaped, and each `@` in them is
  followed by an empty HTML comment (`@<!-- -->`), so a stack path cannot
  mention a user or team; the apply comment's cell lines and its notes'
  bold cell names and the doctor report's check headers do the same.
  A changed cell another pull request holds a cell lock on ends its line with
  ` 🔒 locked by #<n>`, or ` 🔒 locked by an unreadable lock` for an unknown
  holder (§Cell locks). The marker is part of the bare line, so the size budget
  never sheds it. A lock read that fails warns and marks no cell.
- **Fold-out.** A changed cell with plan text renders as
  `<details><summary>` + its cell line + `</summary>`, then the rendered plan
  inside a `diff`-tagged code fence (change signs moved to column 0; `~` mapped
  to `!`). An unchanged cell is its bare line only. Consecutive bare lines are
  separated by one newline, and every fold-out has a blank line on both sides,
  without which the next line stays inside the HTML block and its link renders
  literally.

GitHub caps issue-comment bodies at 65,536 characters. The comment is built
to a smaller budget: each changed cell's fold-out degrades, in order, full
plan → truncated plan (cut at a line boundary, with a link to the check run
carrying the full text) → the cell's bare line. The space every remaining
cell's bare line and every environment heading need is reserved up front, so
an early giant plan cannot starve a later cell, and every cell keeps its line
and its link. The command section is left out before any of these steps: it is
added after every cell is sized, only when the whole body with it stays within
the budget, and never shrinks a cell; leaving it out warns nothing. If even the
bare lines alone cannot fit the cap, the summary fails loud rather than posting
a truncated comment. Plan text is emitted only inside a backtick
fence computed to be longer than any backtick run in the text —
author-controlled plan output cannot escape the fence.

The data feeding the comment ships in the per-cell artifact
`cell-summary.<env>.<slug>` (same dot-delimited, env-first grammar as
`plan.<env>.<slug>`: the name is built forward from the `(env, slug)` pair
exactly like the plan artifact, never reverse-parsed). Consumers download it
with the glob pattern `cell-summary.*`. It contains verbatim:

- `cell.json` — keys `stack` (the Terramate stack path, displayed and fed to the
  check-name construction), `environment`, `changed`
  (boolean), `fingerprint`; written by `plan-cell` at plan time. `changed`
  comes from `scripts/plan-classify`; the cell line's add, change, destroy,
  import and forget counts come from `plan.txt`'s single column-0 OpenTofu
  tally line (tofu omits a zero import or forget count), or are zero when the
  text is a no-changes or outputs-only plan; a cell with no `plan.txt`, no such
  line, or two tally lines gets one warning, and renders `+? ~? -?` when it
  changes (an unchanged cell still renders `no changes`).
- `plan.txt` — the `tofu show -no-color` rendering of the reviewed plan. The
  `summary` job hashes these exact bytes and records the digest on the cell's
  apply check; the apply re-renders the stored plan and refuses a difference
  (§Apply-match fingerprint).

`plan-cell` (writer) and `summary` (reader) are pinned by the same SHA in the
consumer's `shipmate.yml`, on its `plan.yml` reference. The summary fails loud
on a `cell.json` missing schema keys.

The count those summaries are measured against is not an artifact: `detect`
declares it as the `count` output of `actions/build-matrix`, and the `summary`
job receives it as the `planned-cells` input. A cell count that disagrees with
it holds the gate in either direction.

Before it publishes the first apply check, the `summary` job writes the gate
`failure` with a description telling you to re-plan, so a job that dies with
only some checks published leaves a hold that `gate-refresh` will not green. The
same hold is written when the job cannot read the head's gate statuses, list the
head's apply checks or build the ones to publish, whether or not it downloaded a
cell summary: a failed download leaves none over planned cells. The apply checks
publish after the sticky plan comment is written, so a plan comment that could
not be listed or written ends the job with no apply check published, and a
pre-merge `shipmate apply` refuses as it does over a partial plan. A summary job
that fails part way changes the comment only if its `Upsert sticky comment` step
wrote it (created or replaced) before the failure. A run with no check to
publish (held, draft, no cell planned, or every check already published by this
run) skips the publish step without reading anything. The job's final gate write
replaces the hold. A run whose plan job was cancelled writes no final gate, and
keeps the hold when it downloaded any cell summary.

The job publishes only the apply checks its own run has not published yet,
judged by the run id each App-authored check records. A re-run of the summary
job therefore leaves alone a check an apply has completed, or is completing,
since. It refuses before writing anything when a newer plan run of the same
head holds the gate ("plan run <id> holds the gate of <sha> and is newer than
this run") or published apply checks there ("plan run <id>, newer than this run
(<id>), published apply checks on this head"), so an older run never overwrites
a newer run's gate or checks. That refusal is a notice, and the `summary` job
ends green and runs none of its later steps, so a re-run of a cancelled autoplan
clears its checks. Its apply-check publish and its final gate write
each read the statuses again and refuse, writing nothing and failing the job, when a
newer run of the same head holds the gate by then (that run's `summary` was
evicted, and `gate-hold` writes outside the job's concurrency group); a failed
read writes nothing either, so the publish hold stands. A final `pending` gate is followed
by a refresh, unless this run published a queued apply check, which cannot be
done yet: when every apply check is already done (an apply's refresh ran before
this write), the summary greens the gate itself. A failed refresh, or one
refused by a hold, warns and leaves the `pending` standing.

A run with incomplete evidence publishes no apply check at all, changed or not: a
plan job whose result is neither `success` nor `skipped`, or `skipped` while cells
were planned; a planned count that was not reported; or a parsed cell count that
disagrees with it. The gate holds every such run except one whose plan job was
cancelled, for which it writes nothing and leaves any earlier hold standing. A
plan job skipped with no cell planned is the empty matrix and is not held.
`gate-state` holds the one rule for both decisions. The newer-run refusal above
runs first, before any cell summary is read. Apply checks an earlier plan run of
the same head published stay, and a pre-merge `shipmate apply` applies those
when that run published every one of its checks and the head's newest plan run
did too (§Apply-match fingerprint); on a head with none it names no plan run and
is refused (§Comment-ops, "undiverged").

## Apply result comment

Every apply run — targeted `shipmate apply <env>`, bare `shipmate apply` and an
`auto` apply, all through `apply.yml`; the merge-deploy path,
`deploy.yml`, stays comment-less — posts a fresh PR comment via
`actions/apply-summary`. Unlike the plan comment above, it is deliberately
not upserted against a marker: every run's comment is new, so a
failure-then-retry sequence stays visible as separate comments rather than
overwriting the evidence of the failure — an audit trail. A run refused or
cancelled at `apply.yml`'s `review` job (the pull request closed, re-pushed or
turned draft since the comment) posts no comment and writes no gate: the red run
is the signal. The started-run reply may link it (§Comment-ops says when).

Structure, in order, blank lines between the parts, each part present only when
it has content: the header, the verdict line, the error fence, one line
or fold-out per row, the notes, the footer lines, and, under any verdict but 🟢,
the help hint ``Comment `shipmate help` for the available commands.`` A 🟢
comment has no footer. A run with no rows to render is the header, the verdict
line, the error fence, the footer lines and, under any verdict but 🟢,
the hint. The error fence shows when `detect` or the `lock` job failed with an
`::error::` (a refusal or a failed tool or API call): its first error on one
line, in a plain code fence, cut at 4,000 characters and fenced longer than any
backtick run in it. A failed `detect` skips `lock`, so at most one of them
names an error. A job that failed without an `::error::` renders no fence.

- **Header.** `### shipmate apply <env>` for a targeted run, `### shipmate
  apply` for a bare or `auto` one; the environment headings and the footer lines name the
  environments.
- **Verdict line.** With rows: one count per display status, in the order
  `N failed`, `N not recorded`, `N not attempted`, `N blocked`, `N applied`,
  zero counts omitted, joined by `, `, then `N waiting` when cells wait (see
  the waiting line below), `N locked` when another pull request locks
  pending cells (see the locked line below) and `N held (base changed)` for an
  `auto` apply's overlap cells that are not also locked, so a cell under both
  causes counts once, under `N locked` (see the overlap line below). When the job-level `SHIPMATE_RESULTS`
  outcome signals a failure and no row is failed or blocked, a bare `failed`
  leads the counts, so an apply run that dies before any cell reports (a
  missing/denied apply environment, a job-level cancel) still surfaces a
  failure signal. A not-recorded row does not suppress it: it describes one
  cell in one environment and says nothing about a different environment whose
  job died before reporting. The circle is 🔴 for a failed run, else the worst
  of the rows' circles and, when cells wait, are locked or are overlap cells, ⚪
  (🔴, 🟠, 🟡, ⚪, 🟢 in that order), so such a run is never 🟢. With no
  rows, the first that holds: `🔴 failed`,
  `⚪ nothing applied, environments are held for review`,
  `⚪ nothing applied, pending cells are locked or the base also changed their
  stacks` (a locked cell and an overlap cell, the same cell or not),
  `⚪ nothing applied, pending cells are locked`,
  `⚪ nothing applied, the base also changed the pending cells' stacks`,
  `⚪ nothing applied, cells wait on pending cells they run after`,
  `⚪ nothing applied, the pending environments wait on the lines below` (a run
  that left a pending environment to a bare apply, a `targeted` one or a skipped
  one), or
  `🟢 no pending applies`. The line ends
  `at [<sha7>](<commit url>) in [run #<n>](<run url>)` for the pull request
  head, as the plan comment's does.
- **Environment heading.** `#### <env>: N cells, <counts>` (`1 cell` for one),
  `<counts>` the verdict line's counts for that environment's rows, with a
  blank line on both sides, one per environment in the plan comment's order,
  from `apply-detect`'s `env_order` output (a bare apply orders every
  environment with an apply check on the head, a targeted one names its
  environment).
- **Row line.** `<circle> <stack>: <state> <a href="<url>">logs</a>`, one per
  expected cell, under its environment's heading. Every row but a not
  attempted one comes first, then the not attempted rows, each group sorted by
  stack:

  | Display status | Circle | `<state>` |
  | --- | --- | --- |
  | applied | 🟢 | `+a ~c -d`, then `, N import` and `, N forget` when above zero, from the apply output's last `Apply complete!` line; `applied` when there is none |
  | not recorded | 🟠 | the applied state, then `, not recorded` |
  | failed | 🔴 | `failed` |
  | blocked | ⚪ | `blocked: <reason>` |
  | not attempted | 🟡 | `not attempted` |

  `<url>` is the cell's job, or the workflow-run URL when the job is not
  listed. An applied, failed or not-recorded row with apply output renders as a
  fold-out (`<details><summary>` + its line + `</summary>`, then the output in
  a plain code fence); every other row is its bare line. An expected cell whose
  artifact never arrived (its wave/env-level was skipped after an upstream
  failure, or the run was cancelled before upload) is not attempted unless its
  apply check is already done (see the derivation rules below).
- **Notes**, each on its own line when it has content: `state lock held:`,
  naming each cell whose apply could not acquire the lock, the lock id, when it
  was taken, and the `shipmate unlock` command; `state not saved:`, naming
  the cells whose state was not saved; `not recorded:`, naming the
  not-recorded cells; and ``not attempted: the apply checks stay pending;
  retry with `shipmate apply <env>`.`` (the bare `shipmate apply` when the run
  covered all environments), qualified to run after the `state not saved:`
  cells are recovered when that note is present, and always followed by a
  pointer to the lost-save section.
- **Footer lines**, one per line: the bare and `auto` forms' environment
  dispositions, then, in every form, one line per lock holder and environment,
  then, in the `auto` form, the overlap line, then one line per waiting cell. The comment
  states no gate line: the `shipmate / gate` status shows the gate's state.

There is no separate "nothing pending" input: the expected cell set is the
same waves JSON both `apply-detect` forms already compute, and a
cell counts as attempted when its artifact actually downloaded or its apply
check is already done — the render can never claim nothing is pending while
holding evidence that an apply ran.

The disposition lines are one per environment and cause, per lock holder and
environment, or per waiting cell.
The rows form and the no-rows form carry the same set, fed by one shared
function, and the size budget never sheds a footer line — so the comment's most
actionable warning cannot be lost on either path. In the order they render:

- **targeted** — ``🟡 <env>: left pending (targeted), comment `shipmate apply <env>` ``.
- **left pending (not auto)** — ``🟡 <env>: left pending (not `apply_mode: auto`),
  comment `shipmate apply` ``, one per pending environment an `auto` apply leaves
  to a bare apply because it is neither `auto`, `targeted` nor held
  (`bare_envs`). Only the `auto` form renders it.
- **skipped** — `⚪ <env>: skipped, waits on <pred> (<remedy>)`, more than one
  predecessor separated by `; `. Each `<pred>` is a transitive `needs`
  predecessor not fully applying for a cause of its own (`skipped_after`), and
  `<remedy>` follows its kind: `` apply it first with `shipmate apply <pred>` `` for
  a `targeted` one, `it is held until the pull request is approved` for a held
  one (`` , then comment `shipmate apply <pred>` `` when also `targeted`), `a cell of
  it does not apply this run; see its line below` for one holding a locked,
  overlap or waiting cell, and otherwise, an `auto` apply's environment that is
  not `auto`, `` comment `shipmate apply`, which applies them in order ``. A detect
  that wrote no `skipped_after` renders the cause-agnostic `⚪ <env>: skipped,
  ordered after an environment not fully applying this run`.
- **held** — `⚪ <env>: held, the review state does not permit applying`. A held
  environment that is also `targeted` is listed once, here and not on a targeted
  line, with its command: ``⚪ prod: held, the review state does not permit
  applying; once it clears, comment `shipmate apply prod` ``, since a bare
  apply would not pick it up even once the hold clears. A held environment that
  is not `targeted` names no command. When any environment is held, one line
  follows: `held: get an approving review, or resolve or dismiss a
  requested-changes review. The run log's apply-all-detect notice names the
  decision seen.` The held lines are deliberately cause-agnostic: three
  distinct decisions hold an environment (`REVIEW_REQUIRED` on a `review: true` env,
  `CHANGES_REQUESTED`, or an unknown or absent decision), and only the run's
  own apply-all-detect notice carries which one, since the decision never
  reaches this renderer.
- **locked** — ``🔒 locked by #<n>: <cells>. Wait for #<n> to merge or close,
  then re-plan, or comment `shipmate unlock <env> --evict`.``, one per pull
  request `#<n>` holding a cell lock on pending cells of `<env>` (`held_cells`
  with `cause` `locked`), naming each as `<stack> / <env>` comma-separated. A lock whose holder is
  unknown renders ``🔒 locked by an unreadable lock: <cells>. Comment
  `shipmate unlock <env> --evict`.`` Every form renders these lines.
- **overlap** — ``⚪ held, the base changed these stacks after this branch left
  it, so their plans would revert that change: <cells>. Update the branch from
  the base and push.``, one line naming each of an `auto` apply's overlap cells
  (`held_cells` with `cause` `overlap`, §`apply_mode: auto`) as
  `<stack> / <env>` comma-separated. Only the `auto` form renders it.
- **waiting** — `⚪ <stack> / <env>: waits on <stack> / <env>, not applied
  yet; <remedy>`, one per cell left pending because a cell it runs after
  (§Fan-out) is pending and not applying this run; the second pair is that
  blocker. Every form renders these lines. The blocker is a cell that waits on
  nothing itself when there is one, so it can apply now. `<remedy>` is
  `` it is locked by #<n>; wait for #<n> to merge or close, then re-plan, or
  comment `shipmate unlock <env> --evict` `` when another pull request locks
  the blocker cell (`` it is locked by an unreadable lock; comment
  `shipmate unlock <env> --evict` `` when its holder is unknown), whatever
  else holds its environment, followed by `; the base also changed it; update
  the branch from the base and push` when the blocker is also an overlap cell,
  else `the base also changed it; update the branch from the base and push`
  when the blocker is an overlap cell, else
  `` it is held until the pull request is approved, then comment
  `shipmate apply <env>` `` when the blocker's environment is held and
  `targeted`, `it is held until the pull request is approved` when it is held,
  `` its environment waits on its `needs` predecessors, which must apply first,
  including any held for review `` when it is skipped, and otherwise
  `` apply it first with `shipmate apply <env>` ``, each naming the blocker's
  environment. The targeted form has no held or skipped environments to
  consult, so its remedy names the lock's or `shipmate apply <env>`, even for a
  blocker that waits on a review or on `needs`.

Two audit facts are not comment lines. The render step prints each as one
`::notice::` workflow annotation in its own log, environment names escaped as
workflow-command data. The notices live in the run log, so they last as long as
the repository keeps its workflow logs. GitHub caps the annotations it renders
per step, so the annotations view can show fewer than were printed; the step's
log text holds every line:

- **review-exempt** — `<env>: review-exempt, permitted to apply without an approving
  review (review: false in .github/shipmate-config.yml)`, one per environment the
  all-environments run was permitted to apply without an approving review. It
  is the audit trail such an apply leaves: `reviewDecision` is a live
  value with no history, so once the review lands nothing else in a run
  distinguishes an apply that waited for it from one that did not. It states a
  permission, never an outcome — the set is derived before any wave runs, so a
  failed wave or a skipped env-level leaves an environment named here that
  never applied, and the text carries no completion verb.
- **no review required** — `<env>: no approving review was required, so the
  default review: true had nothing to enforce`, one per `review: true`
  environment a `NONE` decision authorized. It has its own cause, so it has its
  own detect output (`review_not_required_envs`, from both detects) rather than
  widening `applied_exempt_envs`. It prints in the targeted and the
  all-environments form, and names only environments with an applied, failed
  or not-recorded row — apply ran there, so infrastructure may have changed —
  so a run with no rows never prints it. It states the authorization fact and
  never that nobody reviewed: a code-owner review can still be required at an
  approval count of 0.

Row status is derived from both the per-cell artifact and the real state of
that cell's `apply / <stack> / <env>` check on the head SHA, which
`actions/apply-summary` reads with its own App installation token (only checks
authored by the shipmate App count, judged by the newest run per name — the
same predicate the gate and both detects use). The pending apply checks are the
work queue, so they outrank the artifact, which only records what a cell's own
job believed before its check was completed:

- 🟢 **applied** — the apply succeeded and its check is recorded.
- 🟠 **not recorded** — the apply succeeded but its check is still
  pending, because the check was not completed after the apply: the cell's
  job did not succeed (its state was not saved, or it was cancelled), the
  `complete` job failed, or a newer plan re-created the check.
  `shipmate / gate` stays pending, and the stack needs a re-plan. The
  `not recorded:` note ends `Re-plan.` and never says re-apply: a cell whose
  state was not saved is also named by the `state not saved:` note below. The
  `not recorded:` note names the affected cells — capped, with
  any remainder summarized as a count, since notes are part of the comment the
  size fallback cannot shed and the usual cause strands a whole matrix at once;
  the row lines always name every one of them. Each such cell keeps its full
  fold-out output.
- A cell whose check is done but whose artifact never arrived (the
  cosmetic, `continue-on-error` upload dropped) renders as a bare 🟢 `applied`
  line — never 🟡 not attempted, and it does not drag the `not attempted:`
  note along with it.
- 🔴 **failed** / ⚪ **blocked** rows are never re-derived from check state. A
  failed row against a completed check means some other run applied that cell:
  the row over-reports, nothing is stranded, and downgrading it would let an
  unrelated run's completed check hide a real failure in this one. A failed
  apply can still have changed resources, and a failed cell whose state was
  not saved is named by the `state not saved:` note.
- **state not saved** is a note, not a status: `apply-cell-summary` writes
  `state_lost` into `cell.json`, true unless the cell's
  `Warn that the applied state was not saved` step's outcome is `success` or
  `skipped`, so a cancelled or unknown outcome counts as lost. That step fires
  when the apply step ran, the state was restored, and `actions/state` did not
  report `saved`. The `state not saved:` note names up to 5 of those cells
  and counts the rest, whose job logs show
  `The apply step ran and its state was not saved`, and says: the apply step
  ran and its state was not saved or not verified; never re-run the cells'
  jobs or their workflow run; a re-run, a `shipmate apply` comment, or a merge
  can apply their reviewed plans again against the state from before the
  apply; before applying or merging any plan of a named stack in the
  environment named beside it, on any pull request, follow
  `docs/troubleshooting.md`
  §`The apply step ran and its state was not saved`, which holds the procedure.
  While it is present, the `not attempted:` note's retry command is to run
  once those cells are recovered, because any apply covering them applies
  their reviewed plans again. Every `not attempted:` note also carries a
  cause-agnostic pointer to that section, because the upload is best-effort: a
  lost cell whose artifact did not upload renders as not attempted and no note
  names it. The marker reaches only the
  comment of the run that lost the state; a later run's comment, and the
  remedies it renders, do not know. Nothing refuses a later apply of the cell.
  The warning step's `exit 1`, not the artifact, keeps the cell's apply check
  pending.

If the check scan is unavailable — an API failure (warned, degraded to no
data) or an empty `SHIPMATE_APP_ID` (warned) — every name reads as *absent*,
which means *unknown*, and the comment falls back to artifact-only status.
Absence never manufactures a not-recorded row.

`cell.json` holds the strings `stack`, `environment`, `result` and `reason`,
and the boolean `state_lost`; the validator refuses a cell missing any of them
or carrying another type. Its `result` grammar is `applied | failed | blocked`. Not recorded
is a display status derived at render time, never a value `apply-cell` writes,
so the validator still rejects an out-of-enum `result`.

The 65,536-character comment cap and the up-front reserve of every row's bare
line work the same way as the plan comment's, above, but the truncation
direction is the opposite. A plan diff is read top-down, so the plan comment's
fold-out keeps the head. Apply output is read for its END: a failing apply's
fatal `Error:` line and a successful apply's closing `Apply complete!
Resources: ...` line are both the last thing tofu prints, so each attempted
cell's fold-out instead keeps the TAIL — full output → truncated (front cut at
a line boundary, noting that earlier output was elided, with a link to the job
log) → the row's bare line. `apply.txt` itself is read from the end of the file
(bounded — a fixed-size read near the tail, never the whole file) and
decoded permissively: a non-UTF-8 byte anywhere in it becomes a replacement
character instead of aborting the render. Terminal escape sequences (colour
codes and similar) are stripped from that text as it is read, since the apply
output is tofu's own and is not required to be colour-free. Every remaining
row's bare line and every environment heading is reserved up front. When even
the bare lines exceed the cap, the comment falls back to a compact form with no
headings: every row keeps its line, which then names its environment
(`<circle> <stack> (<env>): <state>`), and its `logs` link, a blocked row's
state reads `blocked` without its reason, and,
when a row is blocked, one note follows the row lines:

`blocked: each blocked cell's reason is in its logs.`

In the compact form each environment disposition is one line naming its
environments comma-separated (`<envs>`), in the per-environment order, and the
waiting cells are one line per blocker, keeping every blocker and its remedy;
the `held:` remedy line, the locked lines and the overlap line are unchanged:

- `` 🟡 left pending (targeted): <envs>; comment `shipmate apply <env>` for each ``
- ``🟡 left pending (not `apply_mode: auto`): <envs>; comment `shipmate apply` ``
- `⚪ skipped, waits on <preds>: <envs>`, one per distinct `<preds>` with its
  remedies, as on the per-environment line
- `⚪ held, the review state does not permit applying: <envs>`, followed by
  `` ; once the hold clears, comment `shipmate apply <env>` for <envs> `` naming
  the held environments that are also `targeted`, when there are any
- `⚪ waiting on <stack> / <env> (<remedy>): <cells>`, one per blocker, naming
  every cell waiting on it comma-separated as `<stack> / <env>`, in first-seen
  order

A 256-cell run needs this form when every cell is blocked, because a blocked
reason is the one free text a bare line carries, and when it names hundreds of
environments in its dispositions, because each environment otherwise gets its
own footer line.
Past the compact form the render fails loud rather than post a comment that
would exceed GitHub's cap.

The data feeding the comment ships in the per-cell artifact
`apply-summary.<env>.<slug>` (see Apply summary artifacts, above); per-cell
log links resolve against the run's job-name listing using the
`apply / <stack> / <env>` check-name grammar (see Check names, above), and
degrade to the workflow-run URL on no match.

## Apply-match fingerprint

Each plan stores a fingerprint (`fingerprint.txt` in the artifact; on the apply
check, the `external_id` JSON record holds it alongside the id of the plan run
that produced the cell, the number of the pull request that run planned, the
digest of that cell's plan text, the number of cells that run planned, below,
and `action`, the plan run's event action, empty when its payload names none, as
a `shipmate plan` dispatch's does; §`apply_mode: auto` reads it):
`sha256` over the sorted JSON of every non-empty
`TF_VAR_*` environment variable (name→value) plus `TF_WORKSPACE` when it is set.
Ephemeral credential vars (`AWS_*`, etc.) are excluded. A set-but-empty
`TF_VAR_*` is excluded from the payload, so it hashes identically to that
variable being absent altogether — a flavor that injects nothing and a flavor
that injects an empty string for the same name fingerprint the same way.
`TF_WORKSPACE` is included because it is the workspaces-flavor env identity and
is not a `TF_VAR_*`; without it two environments of a workspaces stack would
fingerprint identically and an apply could match the wrong environment's
reviewed plan. plan-cell and apply-cell use a byte-identical algorithm
(`scripts/plan-classify`). On mismatch, apply fails safe and reports differing
variable names only — never values.

The same shape carries the planned commit. `plan-cell` reads
`git rev-parse HEAD` before any step that runs repository content, refuses an
`expected-head` input that is empty or that it did not check out, and ships the
observed commit as `planned-head.txt` inside `plan.<env>.<slug>`. The file is
materialised from the captured step output immediately before upload rather
than when it is read: between the two, `terramate` executes
author-controlled HCL and provider binaries, which can
rewrite a file at repo root but not an emitted step output. `apply-cell` moves
the record out of the consumer's checkout into `$RUNNER_TEMP` before reading it
— the action runs inside the consumer's own checkout and must leave no stray
file at its repo root — and compares it against its own `git rev-parse HEAD`
before the decrypt, the state restore and the apply — a plan of another tree is
refused at the cheapest point. A record that disagrees with the checkout is
refused, and so is an absent record: there is nothing to compare, so it is
refused rather than tolerated. The remedy is a re-plan. This is additive
to the plan-run binding the apply path already carries: each cell's plan run
is read from an App-authored apply check on that same head, so no plan run from
another head can be named. That binding bounds which plan run may be
applied; this one binds each individual plan to the tree it was produced from.
Two open pull requests can share one head commit, so the record's `pr` field
binds the plan to its pull request as well: the targeted and the bare apply
refuse, at detect, a cell they would apply whose record names another pull
request than the dispatch, or none, and the remedy is a re-plan on that pull
request. The post-merge deploy does not compare it.

A third record binds the plan *text* to the plan that executes. The trusted
`summary` job takes `sha256` over the **whole** `plan.txt` it downloaded —
computed there, never copied from the cell — and writes it as the record's
`plan_sha256` field, on the queued and the neutral check alike. The comment
embeds at most the first 60 000 characters of that file and may truncate further or
show only the cell's line, so on a large plan the digest covers more than the comment
shows; it binds the file, not the excerpt.
`apply-cell` re-renders the stored plan with `terramate run --quiet
--disable-safeguards=git-out-of-sync --no-recursive -C <stack> -- tofu show
-no-color stack.otplan`, the command that produced the file, and refuses unless
the digest matches. The comparison runs after `init`, because the render reads
the data directory `init` wrote (`TF_DATA_DIR` from the stack's `run.env`) and,
where the plan file lacks schemas, the providers it installed; and before
`tofu apply`, so a refusal applies nothing. An absent digest is refused too, at
detect; the remedy is a re-plan. `tofu show` of a stored plan renders the
same bytes on any runner at one tofu version and one `run.env` (§Engine-owned
tofu invocation says what the wrapper adds); both `show` and `apply` refuse a
plan file from another version, so the record carries no version field.

The record's `cells` field binds each apply check to the rest of its plan run:
the number of cells that run planned. The summary job publishes its apply checks
one at a time, so a job that fails partway leaves a run with fewer apply checks
than cells. A "Re-run all jobs" attempt of the same run that plans a different
cell set adds checks beside the first attempt's, so the run has more checks than
one count, or records that disagree on it. Either way an apply of the published
checks can lose an `after` ordering to a cell it cannot see. A run is complete
only when every App-authored `apply / <stack> / <env>` check whose record names it,
across all of the head's check runs and of any status, declares one `cells`
integer and that integer equals the number of distinct check names. A record
with no `cells`, written before the field existed, makes its run incomplete. The
targeted apply, the bare apply and the deploy refuse at detect, after the
absent-run, absent-digest and pull-request refusals, when a cell they would
apply records an incomplete run or the head's newest plan run is incomplete;
with no cell to apply, nothing is read. The refusal names every cause it finds
for a run: a record with no count, counts that disagree, and one count that
differs from the checks published. Its remedy follows the pull request's state.
While it is open and the summary job failed partway, the first remedy is
re-running the plan run's failed jobs: the re-run keeps the run id, and its
summary job publishes only the checks the run has not yet published. The remedy
that always works on an open pull request is a pushed commit, whose plan run
plans and publishes every cell afresh. The deploy, after the merge, offers only
a new pull request touching those stacks. A `shipmate plan` comment on the same
head is not the remedy: a re-plan that no longer plans a cell of the partial run
leaves that cell's newest check on the incomplete run.

One case leaves no record to read: a newer plan run of the same head that
published no apply check at all, because its first publish failed or incomplete
evidence withheld every check. The newest-run test then sees the older complete
run, and a bare or targeted apply applies that run's cells before the merge;
the gate holds and names the newer run, but no apply reads the gate. An `auto` apply
is not exposed to a newer autoplan: it reads the head's newest autoplan from the
Actions runs listing, not from the records (§`apply_mode: auto`). A newer
`shipmate plan` run of the head that published no apply check is not an autoplan
and is not listed, so the older autoplan's cells still apply.

## Consumer variables and secrets

A consumer's stacks reach inputs the repository does not hold through two
channels, and both land in one cell process under one export policy.
`scripts/env-inject` is the single writer of that process (§Env model).

- **GitHub variables, enumerated.** A called workflow sees the calling
  repository's variables and those of the environment the cell binds, so every
  reachable variable is offered to the cell with nothing to declare and nothing
  to map. A `TF_VAR_*` name has its suffix lowercased on export
  (`TF_VAR_ENDPOINT` → `TF_VAR_endpoint`), because GitHub uppercases a variable
  name on storage and OpenTofu matches `TF_VAR_<name>` case-sensitively on
  Linux.
- **`SHIPMATE_VARS`**, a GitHub variable holding a JSON object of strings. Its
  keys are exported verbatim, which is the only way to reach an OpenTofu
  variable whose declared name is not lowercase. It is itself a variable, so it
  arrives inside the enumeration and costs no workflow line.
- **`SHIPMATE_SECRETS`**, the same envelope shape held in a secret. Secrets have
  no enumeration: every callable workflow whose jobs run a cell declares
  `SHIPMATE_SECRETS` with `required: false`, and the calling job must also map it
  by name. An unmapped secret is absent in the callee, with no error.

An unset or empty envelope exports nothing, which is the normal case. `null` is
a written mistake and is refused, as is malformed JSON. Every extracted secret
value is masked before anything is written — line by line, because
`::add-mask::` matches an exact string — and no refusal quotes a byte of an
envelope's value. Key names are not secret and are named.

### Export policy

One policy, applied to the enumeration and to both envelopes, before any value
reaches the process.

- **Reserved names.** The names `TF_WORKSPACE`, `TF_CLI_ARGS`, `TF_LOG`,
  `TF_DATA_DIR`, `TF_PLUGIN_CACHE_DIR`, `PATH`, `LD_PRELOAD` and
  `LD_LIBRARY_PATH`, the prefixes `TF_CLI_ARGS_`, `AWS_`, `SHIPMATE_`,
  `GITHUB_` and `RUNNER_`, and — **per cell** — every `TF_VAR_*` name the
  environment table derived for that cell. The identity part is per cell
  precisely because it varies: a `folder` layout derives none, so `TF_VAR_env`
  is an ordinary consumer variable there, and a fixed list would refuse it.
  These cover cell identity, the credentials the engine established, OpenTofu's
  execution controls, and the runner's own loader and search path.
- **Same set, two responses.** The enumeration **skips** a reserved name
  silently: it sweeps up the consumer's own engine configuration, which nobody
  aimed at a cell, and refusing it would fail every run on correct
  configuration. An envelope key is **refused**: writing `TF_WORKSPACE` into
  `SHIPMATE_VARS` is a deliberate act with one meaning, and a silent drop leaves
  the author debugging the wrong thing. Four enumerated names are **refused**
  rather than skipped: `SHIPMATE_SECRETS`, `SHIPMATE_APP_PRIVATE_KEY` and
  `SHIPMATE_ENCRYPTION_KEY`, secrets set on the variable surface, and
  `SHIPMATE_PLAN_PASSPHRASE`, because a variable of that name can hold an
  encryption key. Skipping one would export nothing while its value sat
  world-readable in the repository UI, and the engine's own namespace has no
  legitimate reading as a consumer variable. The refusal for each of the first
  three says to delete the variable, rotate what it held and set the secret; for
  `SHIPMATE_PLAN_PASSPHRASE` it says to delete the variable and, if it holds the
  current `SHIPMATE_ENCRYPTION_KEY` value, choose a new one. A rotated
  `SHIPMATE_ENCRYPTION_KEY` no longer decrypts a local backend's cached state
  (§Plan artifact encryption, "Rotation").
- **Name validation.** Every envelope key must match `[A-Za-z_][A-Za-z0-9_]*`.
  `$GITHUB_ENV` is written in heredoc form, so a key holding a newline would
  inject further assignments; this is the trust boundary, so a bad key is
  refused rather than sanitised.
- **Cross-channel collisions are refused**, naming both sources. A name supplied
  by two channels has no defensible precedence, and choosing one silently is how
  a consumer ends up debugging the value that lost.

### Which environment supplies which key

| cell path | binds | tier it receives |
| --- | --- | --- |
| plan cells, drift cells | `<env>-plan` | the read key |
| apply cells, unlock cells | `<env>-apply` | the write key |
| any of the above when the env's entry holds `split: false` | bare `<env>` | one value for both paths |

Drift reads and unlock writes, so each lands on the tier that matches what it
does. **The read/write split needs the split environments**: an environment
holding `split: false` has one key serving both paths and forfeits
the split, consistent with what it already forfeits in §Env model.

### Values that differ between the two tiers

**Credentials are what should differ between `<env>-plan` and `<env>-apply`;
anything else that differs is not caught by the plan-match check.**

A non-empty `TF_VAR_*` is a fingerprint input (§Apply-match fingerprint,
above), so one that differs between the two tiers changes the fingerprint
between plan and apply, and apply refuses with `current env does not match the
reviewed plan's fingerprint`. That is fail-closed, but the message names the
variable and not the cause, so it reads as an engine bug.

**Rule: any tier-varying value uses a non-`TF_VAR_` name** — the provider's own
variable (`CONFLUENT_CLOUD_API_KEY`), never `TF_VAR_confluent_api_key`. A
`TF_VAR_` value is identical on both environments.

**The same holds over time: rotating a `TF_VAR_*` secret invalidates every
outstanding reviewed plan.** The rotation changes no configuration and needs no
tier difference — the fingerprint recorded before it no longer matches the value
the apply cell reads, and each held plan fails with the same message until it is
re-planned. Rotation is the more frequent event of the two, so a `TF_VAR_`-named
credential costs a re-plan of everything in flight each time it turns over; a
non-`TF_VAR_` name costs nothing and buys no plan-match check either.

The rule's cost is deliberate and is stated here rather than discovered: it
sends every tier-varying value into the **unhashed** namespace, where the
plan-match check cannot see it. A `CONFLUENT_CLOUD_ENDPOINT` that differs
between the tiers applies against a target nobody reviewed, silently. `AWS_*` is
already excluded from the fingerprint the same way, so this is existing
precedent rather than a new hole.

### Envelopes replace, they do not merge

An environment-level `SHIPMATE_SECRETS` replaces the repository-level one whole,
and the same holds for `SHIPMATE_VARS`: the environment level shadows the
repository level, per value, and nothing merges them key by key. An environment
envelope therefore carries every key that environment needs, not only the ones
that differ.

### These channels change nothing about what gets published

§Secrets in published output, below, is unchanged by them and must not be read
as relaxed by them. A supported envelope entry such as `TF_VAR_password` is
masked in the job log and still lands in the sticky plan comment, the step
summary and the plan artifact whenever the consumer has not marked the variable
`sensitive = true`. Redaction is the consumer's job, and OpenTofu's `sensitive`
marking is the mechanism.

## Secrets in published output

Both text surfaces shipmate publishes — the rendered plan `plan.txt` in the
sticky plan comment and the job step summary, and `apply.txt` in the apply
result comment — carry whatever the tool wrote (the cells run terramate with
`--quiet`, so its routine lines never appear): shipmate never redacts either,
and neither is covered by GitHub's log secret masking. (The published text is
not byte-identical — escape sequences are stripped, undecodable bytes become
replacement characters, and over-cap sections are truncated, per Plan comment
and Apply result comment above — but none of that removes a secret.)

Masking applies to the log stream the runner consumes. Both files are written
out of the tool's own stdout (`tofu show > plan.txt`,
`… apply 2>&1 | tee apply.txt`), and the runner never sees the bytes on the
file side of that write, so a value that appears as `***` in the job log is
written unmasked into the artifact and into the comment. **Do not rely on
masking for anything shipmate publishes**, including the step summary — the
comment and the artifact are the file bytes, and nothing redacts them on the
way out. On a public repository the comments are world-readable.

**Redaction is the consumer's job, and OpenTofu's `sensitive` marking is the
mechanism.** A variable declared `sensitive = true`, or a provider attribute
marked sensitive in its schema, is redacted in `tofu show` output, so it does
not reach either comment through the rendered plan or a normal apply. Consumers
must mark every secret-bearing variable and output accordingly. Marking is
best-effort, not a guarantee: it suppresses the value where OpenTofu knows to,
and error paths can still surface one. GitHub masking is not a substitute even
where it does apply — it only covers values registered as GitHub secrets, not a
credential a provider fetches at runtime.

Two residual paths OpenTofu cannot redact, and shipmate does not attempt to:

- a provider or `local-exec` provisioner that writes a secret into its own
  stdout/stderr (only `apply.txt` carries stderr; this is the one exposure the
  apply surface has that the plan surface does not),
- a secret-bearing variable or output the consumer left unmarked.

Both are defects in the consuming configuration or its provider, not in the
engine, and both are equally visible in the plan artifact — the machine plan
`stack.otplan` stores values in the clear regardless of `sensitive` marking
(see Plan artifact encryption, below).

## Plan artifact encryption

The reviewed machine plan file (`stack.otplan`) is encrypted at rest in the
uploaded artifact. `plan-cell` (in `plan.yml`) encrypts the plan with its
required `encryption-key` input before upload, in the shipmate-crypt-v1 format
`scripts/plan-crypt` defines: the 18-byte header `shipmate-crypt-v1` and a
newline, a 16-byte random salt, the output of `openssl enc -aes-256-ctr -pbkdf2
-iter 1 -md sha256 -nosalt`, and an HMAC-SHA256 tag over the three. One
PBKDF2-HMAC-SHA256 derivation of 600,000 iterations over the encryption key and the
salt yields the cipher key, which reaches openssl in its environment and never
on the command line, and the tag key, each labelled with the file's kind, plan
or state, so neither authenticates as the other. A state entry's tag also
covers its cache prefix, `state/<stack>/<env>/`, so an entry copied under
another stack's or environment's key refuses. AES-256-CTR is unauthenticated:
a wrong key or a damaged byte decrypts to garbage without an error. So each
decrypt verifies the tag before openssl reads the file, and a plan artifact
that fails refuses at `Decrypt reviewed plan artifact`, before any apply step,
and a cached state entry at the state restore (§State backend). `apply-cell`
decrypts the plan after download on every apply path: both paths pass it as
the `SHIPMATE_ENCRYPTION_KEY` secret into the
reusable `apply-env-level.yml` workflow — via the engine `deploy.yml` for the
merge-deploy path, and via the engine `apply.yml` for the targeted, bare and
`auto` forms. The same cipher and secret encrypt a local backend's cached state
(§State backend). Consumers set `SHIPMATE_ENCRYPTION_KEY` as a repository or
organization secret and forward it by name in the `secrets:` block of their
`shipmate.yml`'s `plan`, `deploy`, `apply` and `auto` jobs and of each drift file's
`drift` job. Never `secrets: inherit`: it hands the engine the caller's whole
secret set, and across an organization boundary it delivers nothing at all.

Not a variable, and not a secret on one half of a split environment alone: a
secret on one environment is released only to a job that *names* that
environment, and a plan cell names its own plan environment. The same value on both `<env>-plan` and
`<env>-apply` works. These placements fail:

- **As a variable**, every cell refuses it by name (`scripts/env-inject`).
- **As a secret on `shipmate-engine`**, no plan or apply cell binds that
  environment, so the encryption key resolves to empty on both sides and every plan
  cell refuses at `Require the encryption key`, before it initializes the
  stack.
- **As a secret on `<env>-apply` alone**, every plan cell in that environment
  refuses the same way, so no apply ever has a plan.
- **As a secret on `<env>-plan` alone**, plans upload encrypted and every apply
  in that environment refuses at `Decrypt reviewed plan artifact`.
- **As different values on `<env>-plan` and `<env>-apply`**, every apply in
  that environment refuses at `Decrypt reviewed plan artifact`, and on a local
  backend a plan cell refuses the state an apply cell saved
  (`docs/troubleshooting.md` §`plan artifact ... failed authentication` has
  the remedy).

Scoping it to a plan environment buys nothing either — those must have no
branch policy at all (`docs/hardening.md` §6), so any branch's workflow can
name one and read it.
Unlike the App private key, this secret must be readable wherever plans are
produced, which is any branch; `docs/hardening.md` #7–9 says to treat it so.

- **Required.** An empty or unset encryption key refuses: `plan-cell` before it
  initializes the stack, `apply-cell` before the decrypt, and `actions/state`
  before any cache call. No plan artifact uploads unencrypted.
- **Fail-safe on mismatch.** An artifact that is not a whole shipmate-crypt-v1
  file whose tag verifies fails authentication and refuses naming the causes
  (another encryption key, a rotated one or different values on `<env>-plan` and
  `<env>-apply`, or a corrupt, truncated or altered artifact)
  and points to `docs/troubleshooting.md` for each remedy; the engine cannot
  tell them apart. An openssl or disk failure on the runner refuses and tells
  you to re-run the job or fix the runner. An artifact that cannot be read
  names a re-plan for an artifact downloaded without `stack.otplan`, and a
  re-run for a disk failure, which is safe because the refusal comes before any
  apply step. An encrypt failure, in a plan cell or a state save, names no
  re-run. The apply check stays pending in each case.
- **Scope: the machine plan file and cached local state.** `fingerprint.txt` and
  `planned-head.txt` are a hash and a commit sha, and stay plain. The rendered
  plan `plan.txt` — in the `cell-summary` artifact, the PR sticky comment, and
  the job step summary — stays plaintext: it is the
  deliberately-public reviewer view, as is `apply.txt`. Encryption protects the
  machine plan and the cached state at rest and nothing else; redaction in the
  published text comes from `sensitive` marking (see Secrets in published output, above).
- **Both sides must agree.** `plan-cell` (encrypt) and `apply-cell` (decrypt)
  run at the commit of the engine reference that called them, which
  §Consumption's one-change rule keeps the same on both paths; the encryption key
  must match on both too. A mismatch surfaces as the fail-safe above, not a
  silent wrong apply.
- **Rotation.** A new encryption key cannot decrypt anything the old one encrypted:
  outstanding plans refuse in the apply cell (the fail-safe above), and every
  cached local-state entry refuses its restore. `docs/troubleshooting.md` gives
  the remedies under each refusal's heading.

## Engine-owned tofu invocation

The engine names the `tofu` command every cell runs; a consumer repository
supplies stacks, generated `.tf`, and Terramate metadata, never the command
line. Cells call `terramate run --quiet --disable-safeguards=git-out-of-sync
--no-recursive -C <stack> -- tofu …`, so Terramate contributes the stack working
directory, its `run.env`, and the safeguard policy only:

| Cell | Commands |
|---|---|
| `plan-cell` | `tofu init -input=false -reconfigure`; `tofu plan -input=false -lock=false -out=stack.otplan`; `tofu show -no-color stack.otplan`; `tofu show -json stack.otplan` |
| `drift-cell` | `tofu init -input=false -reconfigure`; `tofu plan -input=false -lock=false -out=stack.otplan`; `tofu show -json stack.otplan` |
| `apply-cell` | `tofu init -input=false -reconfigure`; `tofu show -no-color stack.otplan`; `tofu apply -input=false stack.otplan` |
| `unlock-cell` | `tofu init -input=false -reconfigure`; `tofu plan -input=false -refresh=false`; `tofu force-unlock -force <id>` |

Every `tofu` call runs inside that wrapper, so the stack's `run.env` reaches it,
and `plan-cell`'s text render and `apply-cell`'s are the same command, which the
plan-text binding (§Apply-match fingerprint) rests on. Terramate 0.17.1 writes
its own lines to stderr only (measured), so a render's stdout is tofu's alone,
but the render depends on `run.env`: a value that differs between the plan and
apply jobs can change the render, and a changed render refuses the apply.

A consumer repository therefore needs no `script` blocks and no
`terramate.config.experiments = ["scripts"]`. `init` always passes
`-reconfigure` so a backend whose configuration is derived per-environment
re-initializes cleanly; the plan is taken with `-lock=false` (a plan is
read-only) and the apply takes the backend's lock.

Consequence for reviewers: the exact-plan invariant — apply the reviewed
`stack.otplan` and nothing else — is enforced by the engine at a pinned SHA, not
by branch content. What a pull request can still influence is what tofu *reads*:
providers, modules, and `external` data sources all execute under the cell's
credentials, and the Terramate configuration around the cell (see
`docs/hardening.md`).

## Terramate safeguards

Terramate ships four default-on safeguards that run before `terramate run` (not
before `list` / `generate` / `experimental`). Two of the four are evaluated on
every `terramate run`; the two working-tree checks are evaluated only on the
recursive, non-`--dry-run` path, so the cells' `--no-recursive` invocations
never reach them (Terramate 0.17.1, `commands/run/run.go`: `GitFileSafeguards`
is called under `if !s.DryRun` inside the recursive branch only). Of the two
that do run, shipmate disables exactly one —
it applies a specific reviewed SHA, the plan artifact reviewed on the pull
request, which on the merge-deploy path is legitimately behind `main` (the
squash-merge drops the PR-head SHA from `main`):

| Safeguard | Runs on a cell? | Policy |
|---|---|---|
| `git-out-of-sync` | yes | **disabled** — remote-freshness is the wrong assertion for the exact-plan model. Load-bearing: without the flag a cell on a reviewed SHA behind `main` refuses outright. |
| `outdated-code` | yes | kept — catches hand-edited / stale generated `.tf`, complementing the plan codegen check. Consumer-overridable — see the `terramate.config` prohibition below. |
| `git-untracked` | **no** — skipped under `--no-recursive` | never disabled by the engine, but do not rely on it: an unexpected untracked file does not block a cell. |
| `git-uncommitted` | **no** — skipped under `--no-recursive` | never disabled by the engine, but do not rely on it: a dirty tree does not block a cell. |

**Mechanism (engine-controlled).** Every `terramate run` a cell makes — in
`cell-init`, `plan-cell`, `apply-cell`, `drift-cell` and `unlock-cell`, including
`scripts/env-inject`'s `run.env` check and the `scripts/state-path` call —
passes `--disable-safeguards=git-out-of-sync`. The policy is versioned in the engine
actions (pinned by SHA), so consumers get the correct policy for free by pinning.
The engine never disables via the meta `all` keyword (it drops `outdated-code`
too — `all` is the only keyword that does; `git` covers the three git checks
only) nor via `git` (which would pre-disable the two working-tree checks should a
future Terramate start evaluating them here).

A consuming repository must not disable `outdated-code` — or `all`, which
includes it — via `disable_safeguards` in its own `terramate.config`. The
engine's flag wins for the git checks, but not for `outdated-code`:
`checkGenCode` consults the consumer's config after the engine's flags, so
`disable_safeguards = ["outdated-code"]` (or `"all"`) silences the one working
safeguard the cells still have, and the engine cannot detect or override it.
Disabling `git-untracked` or `git-uncommitted` there changes nothing for cells,
which never reach them (see the table above); it still affects the consumer's
own recursive `terramate run` invocations. The `detect` job's
`terramate generate --detailed-exit-code` still catches stale codegen —
`disable_safeguards` gates `terramate run`, not `generate` — and that step lives
in engine `plan.yml`'s `detect` job, so it is an engine backstop a consumer
cannot disable.

**Consistency invariant.** The disabled-safeguard set is identical across
the four cells and across every invocation within a cell — exactly
`{git-out-of-sync}`. A drift between cells, or between a cell's invocations,
is a defect (guarded by a test, like the
TF_VAR fingerprint).

**Consumer gitignore requirement.** A consuming repository must gitignore the
per-run machine artifacts shipmate materializes in its working tree — the
reviewed plan (`*.otplan`), the fingerprint (`fingerprint.txt`), the planned
commit record (`planned-head.txt`), the rendered plan and cell summary
(`plan.txt`, `plan.json`, `cell.json`), OpenTofu's working directory in each
stack (`.terraform/`), and a local backend's state path (a non-local backend
materializes none — see State backend, above). The reason is not a safeguard:
shipmate writes into the consumer's own checkout, none of those belong in a
commit, and a `terramate run` of the consumer's own that omits `--no-recursive`
refuses on them (`git-untracked` *does* fire there).

**Lock files and the provider cache.** `.terraform.lock.hcl` is not in that
list: commit it. It is OpenTofu's own recommendation for pinning provider
versions and hashes, and the provider cache serves only stacks that commit it.
A cell runs without one. `init -reconfigure` may rewrite a committed lock; that
is a tracked-file change and `git-uncommitted` never runs on a cell.
All four sample repositories commit one per stack.

The cache is `TF_PLUGIN_CACHE_DIR`, set by `actions/setup`:

- `plan-cell`, `drift-cell` and `apply-cell` restore it before `init`, keyed on
  the runner OS, the runner architecture and a SHA-256 digest of the provider
  addresses and versions in the stack's own committed `.terraform.lock.hcl`,
  exact match (`scripts/provider-cache-key`). Constraint strings and hash lines
  are not in the key: stacks whose locks name the same providers at the same
  versions share one entry, and each still verifies the packages against its
  own lock. A lock with no `provider` block restores nothing.
- `drift-cell` and `apply-cell` save it; `plan-cell` never does, because a plan
  cell runs unreviewed pull-request HCL without the apply role. `plan.yml` and
  `unlock.yml` declare `cache-mode: read`, so the token in those runs cannot
  save an entry whatever the cell's HCL runs. Apply cells still save with a
  cache write while running the pull request's HCL, and the key carries no
  environment, so an entry one environment's apply saves is restored by any
  environment's cells. Each saves after a restore that missed, and only when
  `init` left a file in the cache; `apply-cell` saves only after a successful
  apply, and a failed save does not fail the cell.
- An entry is keyed on its path, which lives under `RUNNER_TEMP`: a cell on a
  runner image whose `RUNNER_TEMP` differs from the saving runner's misses and
  downloads.
- The lock verifies every cached package at every reader. A cached package that
  does not match fails `init` with `does not match the content of the downloaded
  package` ([`docs/troubleshooting.md`](docs/troubleshooting.md)). When any init
  error's title starts with `Failed to install provider` after a restore that
  hit, the cell also annotates the restored entry's key and its
  `gh cache delete` command; any other failed init carries only OpenTofu's first
  error.

A lock serves the cache only when, for each provider, it names the address
OpenTofu resolves the stack's `source` to (`registry.opentofu.org/hashicorp/aws`
for `hashicorp/aws`) and carries the `h1:` hash for the runner's platform
(`linux_amd64`, or `linux_arm64` on an ARM runner). A `tofu init` or
`tofu providers lock` that reads the OpenTofu registry records every
platform's `h1:`. Otherwise OpenTofu restores the entry and downloads the
provider anyway: a lock written by Terraform (`registry.terraform.io`), one
holding only `zh:` hashes, and one holding only another platform's `h1:`, as a
`tofu init` through a provider mirror on a developer machine writes it.

Why the cache depends on a committed lock:

- OpenTofu reuses a cached provider only after checking it against the lock's
  `h1:` hash for the runner's platform. With no such hash it downloads again,
  whatever `required_providers` pins.
- The checking lock must come from Git, where it is reviewed. A lock taken from
  the cache, or `TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE`, makes the cache
  check itself: a planted entry then installs a tampered provider
  unauthenticated.
- The engine cannot write the lock in the cell: `tofu providers lock` downloads
  each provider to hash it and leaves the cache empty, so `init` would download
  a second time.

## Env apply order

A repository may declare a partial order over its GitHub Environments so that
one environment's stacks fully apply before another's — for example, "`eu`
fully green, then `us`." The order is each entry's `needs` in
`.github/shipmate-config.yml`: the list of environments that must complete their
applies first (its predecessors), as `needs: [dev-eu]` under
`environments.dev-us`. An environment without `needs` has no predecessors, and
one that is no environment's predecessor either is unordered relative to
everything else. It is read from the default branch, like every other setting
in that file, so a feature branch cannot reorder its own applies.

The merge-deploy path topologically sorts these lists into `env-levels`
(level 0 = no predecessors): all pending applies whose
environment falls in level 0 run to completion, in stack waves that span every
environment of that level (§Fan-out), before any env-level-1 apply starts, and so
on. A failure anywhere in an env-level skips every successor level's applies
for that deploy run — the failed environment's stacks stay pending, and
downstream environments are not touched until it is fixed and re-run. A cell
whose job log shows `The apply step ran and its state was not saved` is fixed
by `docs/troubleshooting.md` §`The apply step ran and its state was not saved`,
never by a re-run, which applies it again.
`MAX_ENV_LEVELS` is `4`; an env-order graph that would span more levels than
that fails validation of the table, naming one longest chain, rather than silently
truncating.

`needs` is a coarser order on top of `after`: it adds order between
environments and decides which instances of an upstream stack a cell runs after
(§Fan-out). A pending cell that runs after a
cell `needs` puts on a later env-level cannot be ordered, and the run refuses
before anything applies. The annotation reads:

```text
needs order inverted, fix default-branch needs or tags: <stack> / <env> before <stack> / <env>, which it runs after
- <stack> / <env> (env-level <n>) after <stack> / <env> (env-level <m>); <env>'s level comes from <env> -> ... -> <env>
Env-levels apply in order, so `after` cannot be met. Drop or reverse a `needs` entry in that chain on the default branch, or retag the stacks so the edge stays inside environments that order it.
```

One `- ` line per inverted pair. Four paths refuse, each over its own cells:

| Path | Dependent cells | Upstream cells |
| --- | --- | --- |
| plan (not a drift sweep) | the plan matrix | the plan matrix |
| bare `shipmate apply` | pending cells of every environment neither held, `targeted` nor skipped by `needs`, the skips a waiting or locked cell causes included; waiting cells count | every pending cell, held and `targeted` ones included |
| `auto` apply | pending cells of every `apply_mode: auto` environment neither held nor skipped by `needs`, the skips a waiting, locked or overlap cell causes included; waiting cells count | every pending cell, of every environment |
| merge deploy | every pending cell | every pending cell |

A plan refusal holds the gate, and the gate description carries its first error,
flattened and cut to 140 characters.
The refusal reads only the run's cells, never the whole tree: a pull request
that only changes `needs` plans no cell and passes, and the inversion surfaces
on the first pull request that changes both ends. The deploy refusal applies
nothing and appends the cells of the merge it leaves pending, with the remedy.
A re-run reads `needs` from the default branch, and tags and `after` from the
merged commit: after a `needs` fix merges, re-running the deploy applies the
cells; a fix to tags or `after` needs a new pull request that makes it and
changes every listed stack. A targeted apply and a drift sweep never refuse it.

A targeted `shipmate apply <env>` applies one environment's pending cells and
does not order by `needs`: it runs them as one env-level. A cell of it that
runs after a cell pending in another environment waits: it stays pending, the
apply comment names it and its blocker, and the rest apply. A run whose every
cell waits applies nothing and succeeds. On every form, a pending cell another
pull request holds a cell lock on stays pending and does not apply, and a cell
that runs after it waits; the lock goes when its holder merges or closes, or with
`shipmate unlock <env> --evict` (§Cell locks). `needs` still decides which environments an
upstream stack's cells are read from (§Fan-out), so a `needs` entry added after
the plan makes the cell wait instead of applying out of order. Each run applies what is ready, so no order of targeted applies
deadlocks.

A bare `shipmate apply` is the pre-merge equivalent of the merge-deploy path: it
buckets the pending applies of every environment not `targeted` into the same
env-levels and applies level 0 fully before level 1, with the same
failure-skips-successor-levels rule. A `targeted` environment it leaves out keeps
its position in the order: environments that do not depend on it run normally at
their own level, while environments ordered (transitively) after an environment
not fully applying this run — held for review, left out as `targeted`, holding
a waiting cell, or holding a cell another pull request locks — are skipped with a
notice — their ordering precondition cannot be met in that run, exactly like a
failed predecessor level. The apply-all-detect notice names them
`skipped_after_targeted`, for every one of those causes.
A cell that runs after a pending cell of a held, `targeted` or skipped environment,
or after a cell another pull request locks, waits, as on the targeted form, and
its environment's other cells still apply. An environment with a waiting cell is not fully
applying, so its `needs` successors are skipped, and each new skip can make
more cells wait; the bare form computes both to one fixpoint.
Completed cells skip idempotently, so re-commenting `shipmate apply` resumes
where the previous run stopped, except for a cell whose job log shows
`The apply step ran and its state was not saved`: its check is pending, so a
re-comment applies it again (`docs/troubleshooting.md`
§`The apply step ran and its state was not saved`).

An `auto` apply is the bare form over the `apply_mode: auto` environments
(§`apply_mode: auto`), with two more causes of not fully applying: every pending
environment that is not `auto`, and an `auto` environment holding a cell the
base also changed (an overlap cell). Both skip the environments ordered after
them, and a cell that runs after an overlap cell, or after a pending cell of an
environment that is not `auto`, waits. The apply-all-detect notice counts the
overlap cells as `overlap=<n>` and names every skipped environment
`skipped_after_targeted`, as on the bare form.

The engine ships this as a reusable, parameterized workflow
(`.github/workflows/apply-env-level.yml`) that the engine's own `deploy.yml`
and `apply.yml` reusable workflows call once per env-level, passing that
level's pre-computed wave matrix; the workflow itself still fans applies out
stack-wave by stack-wave exactly as described above (see Fan-out).

The engine ships the merge-deploy path as the reusable workflow
`.github/workflows/deploy.yml` (deploy-detect → env-levels 0..3 via
`apply-env-level.yml` → gate completion + cell lock release), the
apply path as `.github/workflows/apply.yml` (detect → cell lock acquisition →
env-levels 0..3 via `apply-env-level.yml` → gate refresh + result comment; a targeted apply is one
env-level, a bare one every pending environment in `needs` order, an `auto` one
every pending `auto` environment in `needs` order), and the unlock path
as `.github/workflows/unlock.yml` (guard → single-env detect → one flat unlock
matrix; it takes `environment`, `ref` and `runs_on` only, and declares `SHIPMATE_SECRETS`
and no engine secret). A
consuming repo reaches all three from `shipmate.yml`: the `deploy` job (on
`push` to the default branch; passes secrets and one input, `runs_on`), the `apply` job (dispatched
`apply`, with or without an `environment`), the `auto` job (dispatched `auto`
by a plan run; passes `auto: true`, `ref` and `pr_number`) and the `unlock` job
(dispatched `unlock`; a `secrets:` block naming
`SHIPMATE_SECRETS` alone). All four grant `id-token: write` (§AWS OIDC), and
the `deploy`, `apply` and `auto` jobs grant `pull-requests: read`.

## OpenTofu note

OpenTofu reserves the variable name `version` as a meta-argument; it cannot
be declared as an input variable in a module or root configuration. Sample
stacks in this project therefore use `app_version` wherever a version
string for the deployed workload needs to be passed through as a
`TF_VAR_*`/OpenTofu variable, never `version`.
