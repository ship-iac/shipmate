# CONTRACT

This document is the naming, environment, tag-grammar, and pinning contract
that every shipmate action and every consuming repository must follow. Where
a value is marked verbatim, it must be used exactly as written — these
strings are parsed by other parts of the system (check-name matching,
comment-ops, tag-based stack selection) and are not free-form prose.

## Check names

Every plan and apply unit of work reports as its own GitHub check, using
these names verbatim:

- plan job check-run: `shipmate / <stack> / <env>`
- apply check-run: `apply / <stack> / <env>`

`<env>` and `<stack>` are placeholders substituted with the actual
environment name and the Terramate stack path (as emitted by
`terramate list` / `experimental run-graph --label stack.dir`, e.g.
`stacks/network`) for that unit of work (for example,
`shipmate / stacks/network / staging` and
`apply / stacks/network / staging`). Both grammars lead with one namespace
segment and then the stack, so a reader scans one column. The check name uses
the stack path, never a display name — so the code that *creates* the apply
check (`pending-checks`, run by `actions/summary`),
*completes* it (`apply-cell`), *filters the still-pending queue*
(`deploy-detect` / `apply-detect` / `apply-all-detect`, which only ever have the
path), and *reports it* (`apply-comment`) all reconstruct the identical name
from the one value they share.

The engine composes the plan check's name nowhere: it is the plan matrix job's own
auto-generated check-run, whose name is the job's `name:` (`<stack> / <env>`),
and GitHub names a called workflow's check runs `<caller job> / <callee job>`.
The consumer's plan-calling job is named `shipmate`, so the check is
`shipmate / <stack> / <env>`; the same rule makes the engine plan workflow's
summary job report as `shipmate / summary`. The workflow name does not supply
the verb.

That job name is a contract element a consumer must match. `shipmate doctor`
reports a plan-calling job carrying another name; until it is fixed,
`summary-comment` — which resolves each comment row's plan link by an exact
`shipmate / <stack> / <env>` lookup across every check run on the head SHA —
finds no match and every `[plan]` link falls back to the workflow-run URL.

The consumer's one workflow file gates seven jobs on the event (§Post-plan
topology), so every run also carries one one-segment check-run per job that did
not run, named by that job's display name — `shipmate`, `post-merge`,
`targeted`, `all`, `unlock` — with conclusion `skipped`. They are display
artefacts of the file's shape, and nothing functional depends on them:
`shipmate / gate` is the required check, `scripts/summary-comment` resolves plan
links by an exact three-segment name, and `scripts/mirror-checks` copies only
names beginning `shipmate / `. `shipmate doctor`'s annotation harvest is the
one reader that does see them — it takes every `github-actions` run on the
commit — and takes nothing from them: a skipped run carries no annotations, and
it counts as completed, so it holds no all-clear back.

The apply check keeps its `apply / ` verb prefix: it is created pending (by
`actions/summary`) on the same head SHA that carries the plan job's check, and
it is what keeps `apply-gate` / `gate-refresh`, which select the pending-apply
queue by the `apply / ` prefix, from ever picking up a plan check.

This same forward-built string is also the apply-env-level job's own `name:`
(so the job's display name and the apply check-run name coincide), which
gives the apply check-run grammar a second consumer: `scripts/apply-comment`
(see Apply result comment, below) resolves each row's per-cell log link by
matching this name as a suffix of the run's job-name listing — never by
reverse-parsing it. No match falls back to the workflow-run URL. The two sides
are locked together by a source-derived test, because a rename of either would
degrade every link to the run URL silently — that fallback being the designed
behaviour, no observable would fail.

`scripts/apply-complete` is a third consumer of the identical grammar: given
the pre-flight snapshot of check ids a run may complete, it matches each
snapshotted `<stack>`/`<env>` pair's `apply / <stack> / <env>` name against
the same run's job-name listing (the same `/`-boundary suffix idiom as
`scripts/apply-comment`'s log-link match, not a second invention) to decide
which cells the run's own job conclusions actually earned. A rename of the
apply-env-level job's `name:` would silently break this consumer too — not
degrading to a fallback URL, but leaving already-snapshotted checks pending
forever.

Job display names in the apply/deploy path nest: a called reusable workflow's
job displays as `<caller job> / <callee job>`, applied at every level, and GHA
cannot suppress a level. The apply leaf is therefore three deep, e.g.
`post-merge / L0 / apply / <stack> / <env>`. The intermediate names are kept
short and non-redundant (`L0`..`L3` for env-levels in `apply-all.yml` /
`deploy.yml`, `waves` for the single-env `apply.yml`) rather than repeating the
verb the leaf already carries; the consumer's calling job supplies the outermost
segment (`post-merge` on the deploy path). Its file is named `shipmate`, so the
pull request's checks UI renders that workflow name and then the job path
(`shipmate / shipmate / facts`); the repetition is cosmetic and the check-run
names the engine matches on are unchanged. Only the leaf is
load-bearing — the intermediate names are display-only, and the job ids
they belong to are what `needs:` refers to.

In addition to the per-unit checks, one aggregate commit status rolls up
the full fan-out into a single required status, named verbatim:

- `shipmate / gate`

Branch protection rules should require `shipmate / gate`, not the
individual per-unit checks, so that the set of required checks does not
need to be edited every time a stack or environment is added or removed.

`shipmate / ` is the namespace for shipmate's plan checks and its aggregate,
non-fan-out surfaces. `shipmate / gate` is the only required member — it is
matched by exact string in a repository ruleset. The rest of the namespace
comes from the plan-calling job's name rather than being written anywhere: the
engine plan workflow's own jobs report as `shipmate / facts`, `shipmate /
detect` and `shipmate / summary`, so the checks list identifies the tool
without the consumer naming anything (a job's check run is always created by
the GitHub Actions app, and its name is the only part that can say which tool
produced it). These are not required checks, and nothing in the engine
reconstructs them.

Everything in the check/status namespace is ASCII and slash-delimited, which
is GitHub's own convention for status contexts (`ci/circleci`), and — for
`shipmate / gate` specifically — the property that matters most: the one
shipmate string an operator types by hand into a ruleset must be typeable and
free of lookalike characters. A required context that differs from the posted
one by an invisible character is never satisfied, so every pull request is
unmergeable while the status itself renders green.

The middot is reserved for the consumer file's `run-name` (`shipmate · plan`,
`shipmate · comment`, `shipmate · deploy`, `shipmate · drift`, and the verb of
a dispatched run) — a run title GitHub renders and nothing matches on, where it
says which event or verb this run serves. The workflow's own `name:` is plain
`shipmate`.

`build-matrix` rejects a stack path of exactly `apply` or exactly `shipmate`.
Neither can be mistaken for an engine surface by the engine itself: a stack
`apply` yields `shipmate / apply / <env>`, which fails `apply-gate`'s
`apply / ` prefix filter, and a stack `shipmate` yields three segments against
`shipmate / gate`'s two. They stay reserved for the human reader — `apply` is
the engine's own verb and `shipmate / ` is its own namespace, so a stack so
named reads as an engine artifact in every check list someone scans. Nest or
rename the stack.

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
  workflow's summary job) once every `apply / <stack> / <env>` check on
  the PR head is complete — a targeted `shipmate apply <env>` of only some
  environments leaves the gate pending;
- the post-merge deploy, which completes the gate on the merged PR's head
  SHA after its env-level applies finish.

When apply-cell completes an `apply / <stack> / <env>` check, it completes only
the check-run ids that already existed for that name before its apply began.
A plan re-run can create a fresh duplicate apply check; a duplicate created
*mid-apply* is therefore left pending (its plan was not applied by this run),
while duplicates that predate the apply are all completed so the gate never
sticks. This keeps `shipmate / gate` from greening on a plan the apply
never used.

## Env model

- Every logical environment (for example `staging`, `production`) is carried by
  GitHub Environments named after it: `staging-plan` and `staging-apply` by
  default, or a single `staging` in shared mode (both namings below). The
  Environment is always the unit of binding, apply-gating, protection, and the
  plan/apply split — even when it carries no variables. What it injects
  depends on how the consumer repo models environments (its IaC layout):

  | Repo layout | Env identity injected by the GitHub Environment | Mechanism |
  |-------------|--------------------------------------------------|-----------|
  | **DRY / dynamic backend** (one stack config deployed N×; backend path `…/${var.env}/${var.region}/…`) | `TF_VAR_env`, `TF_VAR_region` | OpenTofu variables drive the backend path and resources |
  | **Workspace-per-env** | `TF_WORKSPACE` | OpenTofu auto-selects (and auto-creates) the named workspace |
  | **Folder-per-env/region** (leaf per env×region, hardcoded state) | *none* | env/region are fixed by the leaf's path; each leaf owns its state |

  This is the DRY model's injection (`TF_VAR_env`/`TF_VAR_region`) — the
  target for real consumer repos and shipmate's internal adoption. The other
  two are proven-generalization layouts (sample repos
  `repo-example-workspaces` / `repo-example-folders`). The folder layout
  trades away shipmate's "add an env = GitHub Environment + tags, zero code"
  property: adding an env there means adding leaf directories (a code change).
  Membership in an environment is always by tag, regardless of layout.
- Protected environments (typically anything beyond the lowest-trust
  environment) carry required reviewers configured on the GitHub
  Environment itself, so approval gating is enforced by GitHub, not by
  workflow logic.
- **Plan and apply bind different GitHub Environments by default (split
  mode).** For a logical env `<env>`, plan jobs (and the nightly drift run) bind
  `<env>-plan`, apply jobs bind `<env>-apply`. This lets apply carry stricter
  protection rules (required reviewers, wait timers) than plan, even though both
  act against the same logical environment. "The apply environment" below means
  `<env>-apply` in split mode and the bare `<env>` in shared mode.
- **One variable binds both sides, and the engine reads it on both.**
  `SHIPMATE_SHARED_ENVS` is a repository variable, and the same expression reads
  it in the eight wave jobs of `apply-env-level.yml`, in engine `plan.yml`'s
  `plan` job and in engine `drift.yml`'s `drift` job — `-apply` as the fallback
  suffix on the first, `-plan` on the other two:

  ```yaml
  environment: >-
    ${{ contains(format(',{0},', vars.SHIPMATE_SHARED_ENVS), format(',{0},', matrix.environment))
    && matrix.environment || format('{0}-plan', matrix.environment) }}
  ```

  Both content lines sit at the same indent: a folded scalar keeps a newline
  inside the parsed value where the indent changes, and GitHub then rejects the
  expression.

  A repository may therefore mix modes freely — some envs shared, some split —
  and a consumer configures none of it. The failure this arrangement exists to
  prevent was a plan-side binding a consumer wrote by hand: a static bare
  `${{ matrix.environment }}` in a repository where only some envs are shared
  made the split envs' plan cells bind a bare `<env>` nobody created, GitHub
  auto-created it empty, and the plan ran with no `TF_VAR_*` — so it silently
  described the wrong environment and a reviewer approved it. A missing variable
  default does not make that loud: `${{ vars.X }}` sets the `env:` key to the
  empty string, a `run.env` `tm_try` chain passes an empty value through instead
  of falling back, and `TF_VAR_env=` satisfies a variable with no default — so
  the layouts differed only in *which* wrong environment got planned
  ([`docs/upgrading.md`](docs/upgrading.md) §0.13.0 has the measurement). The
  loud refusal only arrived at apply, and only where the environment injects a
  variable the fingerprint sees (see the fail-loud bullet below).
- **A logical env may opt into one shared environment (shared mode).** Listing
  it in the `SHIPMATE_SHARED_ENVS` repository variable makes both paths bind
  the bare `<env>` — one environment, no suffix. The price is stated in
  `docs/hardening.md` (§6 and §7–9): a protection rule on a shared environment
  gates the plan cells and the nightly drift run too, so the reviewer gate is
  given up rather than relocated, and plan and apply OIDC tokens become identical
  in `sub`, so no trust policy can separate them.
  - The value is a comma-separated list of logical env names, matched on
    comma boundaries, so `dev-us` does not match an entry `dev-us-2`.
  - **No spaces after the commas.** `dev-eu, dev-us` leaves `dev-us` unmatched:
    the entry is ` dev-us` and each entry is compared whole, spaces included.
    The direction is fail-safe — the env stays split, and with no `<env>-apply` environment the
    existence pre-flight refuses the run before any wave applies, and the
    apply-match fingerprint would refuse the cell too, subject to the condition
    in the fail-loud bullet below — but it is the mistake consumers actually
    make.
  - Matching is case-insensitive, because GitHub's `contains()` is:
    `SHIPMATE_SHARED_ENVS=Prod` opts `prod` into shared mode. The expression
    normalizes nothing.
  - The variable is deliberately repository-level, not a Terramate global and
    not a workflow input: a global is branch content, so a pull request could
    flip the mode and bind an environment the reviewer gate is not on, and an
    input buys nothing a repository variable does not — changing a repository
    variable needs settings access, the same trust level as the environments and
    the ruleset it interacts with.
  - **`-plan` and `-apply` are reserved suffixes for logical env names.** A
    logical env literally named `foo-apply` makes the naming undecidable (is an
    existing `foo-apply` that env's shared environment, or `foo`'s apply
    environment?) and binds `foo-apply-apply` on the apply path. Nothing
    validates this; it is a naming rule.
- **A mode that disagrees with the environment names fails loud — on every
  layout whose GitHub Environment injects at least one non-empty `TF_VAR_*` or
  `TF_WORKSPACE`.** The binding
  then resolves to an environment that does not exist, GitHub auto-creates it
  empty, no `TF_VAR_*` reaches the cell, and the apply-match fingerprint refuses
  it naming the missing variables. That is the reason for the naming: under an
  asymmetric naming the dangerous direction was silent, because the environment
  the apply wave fell back on was the live plan environment.

  **The condition, stated once for every page that leans on it.** The
  fingerprint hashes only non-empty `TF_VAR_*` plus `TF_WORKSPACE` (see
  Apply-match fingerprint, below), so it can only refuse a cell whose
  environment injects one of those. The DRY layout (`TF_VAR_env`,
  `TF_VAR_region`) and the workspace layout (`TF_WORKSPACE`) both do, and both
  get the loud refusal. The folder-per-env layout injects nothing — env
  identity is the leaf's path — so plan and apply both hash the empty set, the
  fingerprint matches, and the apply proceeds against the right code and the
  right state but inside an environment GitHub auto-created with no reviewers,
  no wait timer and no deployment branch policy. The fingerprint cannot see
  that on such a layout — it compares variable content, and a legitimately empty
  shared environment is byte-identical to an auto-created one. What refuses it
  is the separate existence pre-flight in the next bullet, which is
  unconditional on the apply-side binding — whatever the environment injects,
  within the scope its own bullet states; `shipmate doctor` additionally reports the naming mismatch
  advisorily on a pull request.
- **A binding that names no existing environment is refused before any wave
  applies.** `apply-env-level.yml`'s `snapshot` job — which every apply route
  fans through, and which runs before `wave0` — computes the apply-side binding
  for every cell in the incoming matrix, lists the repository's environments
  once, and fails the run naming every computed binding the repository does not
  have, plus both ways to fix it: create that environment, or correct
  `SHIPMATE_SHARED_ENVS`. This is a second and independent mechanism from
  the fingerprint refusal above, and it is the one that covers the layouts the
  fingerprint cannot: it compares existence, not variable content, so it
  holds whatever the environment injects, including nothing. It runs once per
  `apply-env-level.yml` call, so an env-ordered deploy can have completed an
  earlier level's applies before a later level is refused — a partial deploy, not
  an unverified apply: every level verifies its own environments before its own
  waves.
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
    this pre-flight does not enumerate; an environment that exists but is wrong
    (empty, mis-scoped, missing its role — content is the fingerprint's and
    `shipmate doctor`'s subject); and an environment created or deleted in the
    window between the pre-flight and the wave jobs.
- **No env names in workflow YAML — ever.** Workflow files must not
  hardcode `staging`, `production`, or any other environment name. Workflows
  discover environments dynamically from stack tags (see Tag grammar,
  below) and GitHub Environment configuration. Adding a new environment is
  purely a data change: create its GitHub Environments (`<env>-plan` and
  `<env>-apply`, or one bare `<env>` listed in `SHIPMATE_SHARED_ENVS`), then tag
  the stacks that belong to it. No workflow YAML is edited to add or remove an
  environment — the suffix in `plan.yml`'s binding is written once, for every
  env. The one carve-out is `shipmate-engine` — a single fixed
  environment name, not a logical environment a consumer defines or names
  itself, that exists purely to scope the App private key to the
  default-branch ref (see `docs/github-app.md` §Key-exposure boundary). It
  appears only inside the engine's reusable workflows — `plan.yml`'s
  `summary` job, `comment-ops.yml`'s `ops` job, `drift.yml`'s `issues` job, and
  the apply path (`apply.yml`, `apply-all.yml`, `apply-env-level.yml`,
  `deploy.yml`). No consumer file names it: the consumer's `shipmate.yml`
  passes the key by name and binds no environment of its own. Each of those
  engine jobs runs at a ref the environment's default-branch policy admits —
  the base ref under
  `pull_request_target`, the default branch under `issue_comment`, the nightly
  `schedule` and `push` — which is what lets them declare it at all. What never
  happens is a *logical* environment name
  (`staging`, `dev-eu`) hardcoded anywhere — `shipmate-engine` is the one
  literal exception, spelled identically everywhere it appears because it
  names one fixed thing, not a per-repo variable.
- A consuming repository's `terramate.config.run.env` must not assign
  `TF_VAR_env`, `TF_VAR_region` or `TF_WORKSPACE`. Terramate applies `run.env`
  to the child process after the ambient environment, so such an assignment
  wins over what the GitHub Environment injected — and silently: the
  plan/apply fingerprint is computed outside `terramate run`, so both sides
  hash the same correct job environment while `tofu` on both sides ran under
  the rewritten one. Every cell then collapses onto one state key with plan,
  gate and apply all green. To give local runs a default, put the injected
  name first in the chain — `tm_try(env.TF_VAR_env, env.env, "dev")`.
  `TF_DATA_DIR` needs the same resolution, or the per-env `.terraform` split
  drifts from what tofu receives.

  Every path that calls `compute_cells` injects a sentinel value into those
  three variables, runs `terramate run … -- env` for one stack,
  and fails the run when any of them comes back changed: the plan matrix's
  `detect` job, the post-merge deploy's own detect, and the nightly drift run.
  Repo-wide config, so one stack answers for the tree. The dispatched and bare
  `shipmate apply` detects reconstruct their cells from the head's own apply
  checks instead and never reach this probe — by then the plan run that would
  have caught it has already happened.

## State backend

Every reusable workflow that runs a cell — `deploy.yml`, `apply-all.yml`,
`apply.yml` and the `apply-env-level.yml` they call, plus `plan.yml` and
`drift.yml` — takes a required `state_suffix` input, and the `apply-cell` /
`drift-cell` / `plan-cell` actions an optional `state-path`:

- **Non-empty** — the consumer's state is a local backend materialized in the
  working tree. The cell's job passes `<stack>/<state_suffix>` as `state-path`
  from one expression each of those five workflows shares, and the cell restores
  that path via
  `actions/state` before the run and saves it after (`drift-cell` and
  `plan-cell` restore only; neither writes state).
- **Explicitly empty** (`state_suffix: ''`) — a remote backend (for example
  S3) owns the state. Both `actions/state` steps are skipped entirely and
  shipmate never handles a state file; the backend and its locking are the
  consumer's configuration.

`state_suffix` declares no default, so omitting it is a workflow-resolution
error, not a third mode. That loudness is deliberate: a calling job that forgot
its state configuration would otherwise restore nothing, apply, discard the
state, and still report `applied` — a green gate over infrastructure nothing
recorded. A remote backend opts in by writing the empty string. `unlock.yml` is
the one reusable workflow that declares no such input: it releases locks and
applies nothing, so passing one is a load-time rejection.

The input is repo-wide. A repository mixing local- and remote-backend stacks
has no correct value — non-empty makes `actions/cache/save` target a nonexistent
path for the remote stacks, empty silently discards the local ones — so a mixed
repository is unsupported. That forecloses a gradual migration in which one
workload moves to a remote backend first: the backend move has to be repo-wide
and land with the `state_suffix` change in every calling job in the same step.

**On the state key.** Where the consumer's backend derives a key per stack,
derive it from `terramate.stack.path.absolute` (as `docs/aws.md` does), which
is unique by construction across the whole tree. A key built from
`${workload}/${stack_name}` is not: a stack's default name is its directory's
basename, so `accounts/sandbox/network` and `stacks/prod/network` both name
`network` and, when both carry the same `workload/<name>` tag, render one key
and share one state file.

Nothing else differs between the two modes. The exact-plan `.otplan` artifact
flow, the fingerprint verification, the wave ordering, and the apply checks are
identical either way — a remote-backend cell has no state artifact, so
it can never be blocked on one.

## AWS OIDC (optional)

The engine is cloud-agnostic by default and ships no credential of its own.
A consumer opts into AWS OIDC per GitHub Environment by setting two
variables on it:

- `AWS_ROLE_ARN` — the IAM role the job assumes via GitHub's OIDC provider.
- `AWS_REGION` — the region passed to the credentials step.

On the apply path a wave job first looks for `AWS_ROLE_ARN_<WORKLOAD>`, where
`<WORKLOAD>` is the cell's `workload/<name>` tag upper-cased with `-` replaced
by `_`, and falls back to `AWS_ROLE_ARN` when the cell carries no workload tag
or that variable is unset — so one apply Environment can serve several
workloads with a role each.

With no role variable set the credentials step is skipped and the job holds no
cloud credential at all, which is how the sample repos run credential-free.
Every cell-running job on every path is wired the same way: the wave jobs of
`apply-env-level.yml`, `unlock.yml`'s unlock job, `plan.yml`'s `plan` job and
`drift.yml`'s `drift` job each request `id-token: write` and run
`aws-actions/configure-aws-credentials`, gated on one of those roles being set,
before the cell step, reading the variables from the Environment that job is
bound to — the apply Environment on the first two, the plan Environment on the
other two. The `snapshot` and `complete` jobs deliberately get no token.

On the apply path the engine passes through whatever role the apply Environment
names, and nothing more: which role that is — and whether two
environments' roles live in different AWS accounts — is a property of *where the
consumer sets the variable*, not something the engine resolves or validates.
"Per Environment" is where the consumer *should* set it, not an enforcement:
`vars` resolve organization → repository → environment, so an `AWS_ROLE_ARN` set
at repository or organization level is read identically by every cell-running
job — every wave job, and now every plan and drift cell — in every environment,
with no warning. The only real bound is the role's own
trust-policy claim condition (`docs/hardening.md` §7–9).

**The plan and drift paths run that same step, workload override included.**
Setting `AWS_ROLE_ARN` — or `AWS_ROLE_ARN_<WORKLOAD>` — on a plan Environment is
therefore all a consumer does for plan-time and drift-time cloud access; the
engine owns the step. This is a widening over the consumer-authored plan
workflow it replaced, which had a credentials step only where its author wrote
one and never resolved a per-workload role at all. A repository that set
`AWS_ROLE_ARN_<WORKLOAD>` for its applies and a bare `AWS_ROLE_ARN` for its
plans now has its plan cells assume the workload role wherever the cell carries
a `workload/<name>` tag and that variable is set on the plan Environment. Where
the two must differ, they differ by *where the variable is set*, as on the apply
path.

**The repository- or organization-level case is the consequential one.** A
consumer who set `AWS_ROLE_ARN` at repository or organization level and never
wrote a plan-path credentials step had no plan-time cloud credential at all,
because nothing read the variable there. Every plan and drift cell now does, and
a plan cell executes branch-authored Terramate/OpenTofu — a provider or an
`external` data source runs at plan time. What *authorizes* it is the role's
trust policy, and nothing in the engine bounds it further: a policy conditioned
on
`repo:<owner>/<repo>:environment:<env>-apply` refuses the `<env>-plan` token, so
the credentials step fails and the cell fails loudly before `tofu init`. A
repository-wide claim condition does not refuse it, and that configuration hands
apply credentials to a plan of any branch someone with push access can push
(fork pull requests are refused in `detect` before a cell exists). `docs/hardening.md` §7–9 is the
threat model; a consumer repinning past this change checks the claim condition
on every role a plan environment can now name.

It follows that the plan/apply role split is the consumer's to configure and to
enforce in the roles' trust policies: one variable on each Environment, and a
trust-policy claim condition on each role. The engine verifies nothing about
either role, including whether the two differ. See `docs/hardening.md` §7–9 for the
threat model this bounds.

The engine reads no `AWS_*` environment variable itself. The credentials step
exports the assumed role's short-lived session variables into the job, and
OpenTofu's provider consumes them. They are `AWS_*`, so they are excluded from
the apply-match fingerprint by construction — it hashes only non-empty
`TF_VAR_*` plus `TF_WORKSPACE` (see Apply-match fingerprint, below).

**Every calling job whose callee runs a cell must grant `id-token: write`, cloud
or not.** GitHub caps a called workflow's permissions at each `uses:` boundary,
so the `plan`, `drift`, `deploy`, `targeted`, `all` and `unlock` jobs of the
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

An `env/<name>` tag is mandatory for every stack a run inspects, and an
untagged one fails the whole run rather than being skipped. Which stacks
are inspected differs by path: the changed set on the plan and deploy
paths, so untagged stacks elsewhere in the tree do not fail a plan run until
one of them changes; every stack on the drift path, which is therefore the
repo-wide backstop that catches the rest; and none on the checks-sourced
bare-apply `detect`, which exempts the check deliberately — an untagged stack
carries no apply check and so contributes no cell anyway, and an unrelated
one must not abort an apply. Failing the whole run rather than the one stack is
deliberate too: a silently skipped stack plans and applies nothing while the
gate goes green over it, which is the one failure this contract will not trade
for convenience. The failure names every untagged stack it found, so an
incremental migration is worked down from that list rather than one re-run per
stack.

The drift path's optional `tags` filter does not retire that backstop: it
narrows the cells a run covers, not the set of stacks it inspects, and the
`env/<name>` requirement is enforced over every stack before any filtering.
`docs/drift.md` §Scoping a sweep has the query grammar.

## Comment-ops

`shipmate <verb> [env] [tag-filter]` in a PR comment drives shipmate's
comment-based commands. The grammar is strict and anchored — the whole
comment line must match one regex, and the parsed values are never
interpolated into a shell. A comment authored by a bot account (any login
ending `[bot]`) is ignored outright before parsing — a loop guard, since
shipmate's own comments (help output, apply results, the doctor report) can
themselves contain text that matches the command grammar.

| verb | status | args | authorization |
|---|---|---|---|
| `shipmate apply [env]` | active | optional env | apply requirements, below |
| `shipmate doctor` | active | none | read-only, but the commenter's `author_association` must be `OWNER`, `MEMBER` or `COLLABORATOR` (a classification, not a permission check) |
| `shipmate help` | active | none | none — read-only, open to any commenter |
| `shipmate plan` | active | none | changes no infrastructure, but the commenter's `author_association` must be `OWNER`, `MEMBER` or `COLLABORATOR` (a classification, not a permission check) — the same tier as `doctor` |
| `shipmate unlock <env>` | active | required env | team membership plus the `<env>-apply` environment — no review policy, no mergeable check, no draft check, no reviewed plan (below) |
| `shipmate destroy` | reserved | — | — |

Every dispatching verb dispatches the same file,
`.github/workflows/shipmate.yml`, and the `verb` input in the dispatch body
selects the job: `plan` → the `plan` job, `apply` with an environment → the
`targeted` job, `apply` without one → the `all` job, `unlock` → the `unlock`
job. That file carries five triggers; `workflow_dispatch` is the one a
commented verb reaches, and the other four (`pull_request_target`,
`issue_comment`, `push`, `schedule`) fire from their own events. `doctor` and
`help` dispatch nothing: both are answered inside the comment-ops run itself. A
repository whose
`shipmate.yml` is missing, or which predates this layout, fails at dispatch time
on that comment-handling run — for every verb alike, since they share the file.
That run is not visible from the pull request, so every refusal in the dispatch
step — an unwired verb, an unknown one, and a rejected API call alike — also
posts a one-line comment there linking the run. The run holds the error; the
comment never carries the API's answer.

`shipmate plan` plans the pull request's changed stacks on demand, authoring
exactly what a push-triggered plan authors and nothing more: the sticky plan
comment, the per-cell plan checks, the plan artifacts an apply consumes, and
`shipmate / gate` (§Plan comment; a dispatched run's cell checks reach the pull
request head as App-authored mirrors, described with the App identities below).
It takes no arguments — there is one plan of record per head commit, and a
run holding a single environment's artifacts would leave every other
environment's workset empty at the next apply, so an env or tag-filter
alongside it is rejected. Unlike autoplan it plans a draft pull request —
a plan a draft can hold but not apply, since the apply requirements below
refuse a draft.
Re-issuing it re-plans rather than reporting the existing plan current: the new
run's plan replaces the plan of record for the current head, and doing so is
safe because a plan changes nothing but shipmate's own comment, checks and
artifacts. A commenter without the standing the table above names gets a
single-line refusal stating `plan`'s own reason — that it runs this
repository's Terramate/OpenTofu on its runners — and nothing is dispatched:
the standing is the same once-evaluated boolean the `doctor` gate below
describes, and the plan route mints no App token of its own. A pull request
whose head is in another repository is refused in its own words and likewise
not dispatched: the run's own fork refusal (§Post-plan topology) lands on
checks attached to the dispatch ref, where the pull request cannot see it. That
is a message, not a layer — it reads the same head repository that guard reads,
and a head it cannot read is dispatched, leaving the refusal where it is
enforced.

`destroy` is recognized and rejected with a "reserved" message, so the grammar
does not need to change shape when that verb is implemented. A
verb documented as taking no arguments (`doctor`, `help`, `plan`) rejects an env or
tag-filter given alongside it with an explicit "takes no arguments" error
rather than silently ignoring the extra token; a tag-filter given to any verb
is likewise rejected as not yet supported rather than silently applied to the
whole environment. An unknown verb's error points the commenter at
`shipmate help`, and so does the "malformed" error for a line that does not
match the grammar at all (a capitalized verb, a stray token, a double space). `scripts/comment-parse`'s `VERBS` registry is the single
source of truth this table is derived from: it drives the parser, the
`--help-markdown` rendering that `shipmate help` posts verbatim (marked with
the HTML comment `<!-- shipmate:help -->`), and the reject-hint text, so the
three cannot drift from each other. Unlike the `:summary` and `:doctor`
markers below, `<!-- shipmate:help -->` is not an upsert key — nothing reads
it back: `shipmate help` posts a fresh comment every time, and the marker
only labels the output as shipmate's own.

`shipmate doctor` posts a consolidated, sticky report — one comment per pull
request, identified by the HTML marker `<!-- shipmate:doctor -->` (distinct
from the plan comment's `<!-- shipmate:summary -->`) and upserted in place the
same way. It combines fifteen live settings probes (gate ruleset,
default-branch `pull_request` rule, environment existence, environment
protection shape, plan-environment secrets, the `shipmate-engine`
environment's own existence and default-branch scoping, `pull_request_target`
triggers in the consumer's workflow files other than `shipmate.yml`, which uses
that trigger by design, engine action-pin freshness,
the plan-calling job name in the consumer's `shipmate.yml`, which must be
`shipmate` or the plan cell checks are not `shipmate / <stack> / <env>` and
every `[plan]` link in the plan comment falls back to the workflow-run page,
a retired `plan_run_id` input still declared or forwarded by the consumer's
`shipmate.yml` (a forward to a reusable workflow is rejected as the run LOADS,
so there is no job and no log to read),
a retired `mode` input on the same file — declared under `on:`, or forwarded
to the engine's reusable apply workflows, where the same load-time rejection
applies — only those two placements are read as the retired rail,
the dispatch wiring of the consumer's `shipmate.yml` — the `workflow_dispatch`
trigger every commented verb dispatches, the four inputs that dispatch sends,
and its call of the engine's plan workflow, without which the dispatch starts a
run that plans nothing; without the first two GitHub refuses the dispatch with an
HTTP 422 and creates no run at all,
the event routing of that same file — one job per engine reusable workflow,
each carrying the `if:` that selects it, so a wrong one sends a verb nowhere,
approvers-team resolvability, and App installation permission
drift — see `docs/branch-protection.md`) with a harvest of the warning and
failure annotations GitHub already recorded on this commit's workflow runs
(shipmate's own and any other Actions workflow run on that commit;
third-party-app-authored check runs are excluded). An empty harvest is
reported as an all-clear only when the harvest both completed and had nothing
left to wait for: if any of the commit's relevant check runs had not finished
when the report was rendered, it says so and asks for the command again once
they have, and if the harvest itself could not be read in full it says that
too — the two are separate statements, since a run that has not finished has
recorded nothing yet while a run that could not be read may have recorded
plenty. Only thirteen of the fifteen
probes can produce a finding from the plan path's own `annotate`-mode
invocation: the approvers-team probe needs the `SHIPMATE_TEAM` environment
variable, which the plan path does not supply, so it silently returns
nothing; the
App-permission-drift probe only has something to report when a
full-manifest permission-set mint was actually attempted, which only
`shipmate doctor` does. Both probes are effectively comment-path-only —
they surface findings only via `shipmate doctor`, never on the plan path's
own annotations.

Four of the probes are narrower than the repository. All three environment
probes (existence, protection shape, and the secrets a plan environment
holds) see only the environments of the stacks this pull request changed — the
declared set comes from the plan matrix's cell summaries — so the report's all-clear line names the environments it actually
covered instead of claiming the repository's environments are all sound, and
says plainly when the set was empty. An environment that is in the repository's
environments listing but whose own settings cannot be read becomes a note
naming it, rather than being silently skipped the way a nonexistent
environment is. The engine-pin probe reports only on pins of the engine's
own repository, which it learns at runtime from the running action's
`github.action_repository` (threaded in as `SHIPMATE_ENGINE_REPO`, never
hardcoded — a consumer's other shared actions belong to whoever ships them);
when either that or the commit under examination is unavailable it says pin
freshness was not verified rather than falling back to a weaker read.

Those warnings are not read from the sticky plan comment — a plan run writes the
full plan comment (overview table, per-changed-cell details, and a one-line
footer pointing at `shipmate help`, the only thing in that comment that mentions
the comment commands at all) but no longer appends doctor findings to it. A run
with nothing planned writes no comment at all *unless* doctor emitted a warning,
precisely so that footer never goes missing on a run that has something to point
at (see §Plan comment). Instead, `actions/summary` runs
`scripts/doctor` on every plan run and emits its findings as
workflow-command annotations, verbatim:

- `::warning title=shipmate doctor::<text>` for a misconfiguration,
- `::notice title=shipmate doctor::<text>` for an informational finding.

The `title=shipmate doctor` string is exactly what `shipmate doctor`'s harvest
step filters check-run annotations on, so it never re-reports a finding the
live probes already re-state fresh against current settings — this is
machine-read, not a formatting choice, and a mismatch between the annotate
call and the harvest filter is a regression. `shipmate doctor` is entirely
read-only: it dispatches nothing and changes no setting, and writes nothing
but its own sticky comment
and an `eyes` reaction on the triggering comment (`doctor`, `help` and `plan`
all get that acknowledgement as soon as the command is accepted — `rocket`
marks an authorized dispatch, whether `apply`, `unlock` or `plan`, instead; a
reaction that cannot be posted is ignored), a one-line error comment when it
cannot mint an App token, a one-line refusal when the commenter may not have
the report (below), and a
handful of untitled `::warning::` annotations on its own degrade paths — an
unreadable PR head SHA, unreadable plan records on this commit's apply checks,
a plan run whose cell summaries could not be downloaded or reconciled, a failed
check-runs listing or reduction, a failed per-check annotations fetch, and a
failed listing of the pull request's comments, on which the report is skipped
for that run rather than posted as a second sticky comment. Those annotations
land on the
`issue_comment` workflow run that is executing `shipmate doctor` itself, at
`github.sha` (this job does no checkout at all — it reads entirely through
`gh api`/`gh run download -R` — so `github.sha` is the default
branch's tip, not a checked-out commit), not on the PR head SHA whose check
runs the harvest reads — so there is no self-harvest loop. `shipmate doctor`
never affects `shipmate / gate`.

Because the report enumerates the guardrails a repository is *missing* — an
ungated default branch, an apply environment with no approval rule, the
configured approvers team and whether it resolves, an App installation short of
the manifest's permissions — the `doctor` route is gated on the commenter's
GitHub `author_association`.

**What the engine enforces:** `doctor` runs only
when `github.event.comment.author_association` is `OWNER`, `MEMBER` or
`COLLABORATOR` — that is, only for organization members and repository
collaborators. Any other commenter gets a single-line refusal saying so, and
nothing else happens — no App token is minted, no probe runs, no report is
composed. `CONTRIBUTOR` is deliberately excluded: its only signal is one merged
pull request, which is no standing relationship to the repository.
The allowlist is evaluated once, in the same step as the bot loop guard, and
every step on the route keys off that one boolean; it fails closed, so an
association the engine does not recognize — or an event carrying no comment
context at all — counts as no access. Consumers adopt this by re-pinning the
engine SHA: it adds no action input and needs no additional workflow
`permissions:` entry, since the association arrives on the event payload.

**What the engine does not enforce:** it does not check write access, and must
not be described as doing so. `author_association` is GitHub's own
classification of the author's relationship to the repository, not a permission
lookup, and it is wrong in both directions: a collaborator invited with only the
Read role, and an organization member whose base repository permission is
None, are both classified `COLLABORATOR`/`MEMBER` and are therefore admitted
to the report even though neither can write to the repository; conversely an
organization member whose membership is private is reported as `NONE` and
will be refused unless they are also a direct collaborator. What the gate does
buy is that an account with no declared relationship to the repository is
refused. Nor is `shipmate help` gated — it renders the verb list and discloses
nothing about the repository. And the report is an ordinary pull
request comment, so once someone with access asks for it, everyone who can read
the pull request can read it. On a repository with public pull requests you
may additionally restrict who can trigger the comment-ops workflow (for example
the same `github.event.comment.author_association` condition on the
`issue_comment` job, or keeping the repository private) — belt and braces over
the engine's own gate, not the only thing standing between an arbitrary account
and the report. `app/manifest.json` declares
`"public": false` for a related reason: the App is registered per organization
and intended for repositories the installing organization controls.

The env is optional for `apply`. A targeted `shipmate apply <env>` applies one
environment; a bare `shipmate apply` applies every environment that has a
reviewed plan for the current PR head, in `env_order` env-levels (see Env
apply order, below), except environments listed in the Terramate global
`global.shipmate.explicit_envs` and environments held for review under
`SHIPMATE_UNGATED_ENVS` (below). Explicit environments (typically production)
must always be named: their `apply / <stack> / <env>` checks stay
pending under a bare apply — so `shipmate / gate` keeps gating the
merge — until someone runs `shipmate apply <env>` for them. An absent global
(or `[]`) means a bare apply targets everything. Malformed `explicit_envs`
shapes (not a list of strings) fail loud, like `env_order`, and so does an entry
carrying a `-plan` or `-apply` suffix: the value is matched against the bare
logical env name, so a suffixed entry would skip nothing.

`explicit_envs` constrains the bare pre-merge `shipmate apply` only. The
post-merge deploy applies every cell whose apply check is still pending,
explicit environments included; the control on that path is the apply
environment's required reviewers — which a shared environment cannot carry (see
§Env model) — not this global. The asymmetry is deliberate:
after merge the pull request is closed, and the apply requirements above include
mergeable, so a deploy that honoured the global would strand those cells
with no path to apply at all: their `apply / <stack> / <env>` checks would sit
pending forever.

A parsed `shipmate apply <env>` command is authorized only when it satisfies
apply requirements — named, Atlantis-style, checked in order, each with
its own actionable rejection reason:

- **shipmate team**: the commenter is a member of the configured approvers
  team (checked via a short-lived GitHub App installation token,
  `members:read`);
- **not a draft**: the pull request is not a draft. `shipmate plan` plans a
  draft on request, so a draft head can carry apply checks with plan runs on
  them; a draft says the change is not ready for review, and applying it is
  what that must not permit. Keyed on the pull request's `draft` flag, not on
  `mergeable_state` — that field has one slot and reports `dirty` for a draft
  with conflicts;
- **mergeable**: the pull request is mergeable;
- **reviewed**: the pull request satisfies the branch ruleset's review policy
  — GitHub's `reviewDecision` is `APPROVED`, or is null (no review required by
  the ruleset; normalized to the explicit `NONE` sentinel in transit). The
  ruleset (required approving reviews, CODEOWNERS, last-push approval) is the
  single source of review policy; shipmate imposes none of its own. A ruleset
  requiring zero approvals reports no decision even when an approval exists,
  but a `CHANGES_REQUESTED` review still blocks. Any other value — including
  an absent or empty decision — fails closed with a wiring-error reason. An
  environment listed in the `SHIPMATE_UNGATED_ENVS` repository variable is
  exempt from this requirement, and from no other (below);
- **undiverged**: at least one `apply / <stack> / <env>` check on the pull
  request's current head names the plan run its plan came from (each check
  records that run at plan time). The records are read from that head's own
  check runs, so no plan run reached here can belong to another head and none is
  compared against one: a head new commits landed on carries no apply checks
  yet, names no plan run, and is refused — re-plan required.

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
branch ruleset computes. shipmate never parses `CODEOWNERS` and keeps no
second copy of the rule: whatever the ruleset requires (approval counts,
code-owner review, last-push approval) is what the apply path waits for, and
there is no owners parser here to disagree with it.

A bare `shipmate apply` is authorized once at comment time, by the same five
apply requirements. Comment time is never the whole review decision: both apply
paths re-read `reviewDecision` server-side before anything applies (below), so
an approval dismissed between the comment and the dispatch holds. Both forms
dispatch the consumer's `shipmate.yml` with `verb: apply`; the optional
`environment` input selects the job (set → `targeted`, empty → `all`). Both
share the same App-minted `workflow_dispatch` mechanism and the same per-env
`apply-<env>-<stack>` concurrency groups.

The repository variable `SHIPMATE_UNGATED_ENVS` lists the environments that
may be applied without an approving review — comma-separated bare logical
env names, matched case-insensitively against the env on the apply checks:

```
SHIPMATE_UNGATED_ENVS = dev-eu,dev-us
```

It exempts one requirement, `reviewed`, and only its `REVIEW_REQUIRED`
value. Everything else still decides, on a listed environment exactly as on any
other: `shipmate team` membership, `not a draft`, `mergeable`,
`undiverged` and the exact-plan rule are unchanged; a `CHANGES_REQUESTED` review still refuses,
because an explicit human "no" is not an absent review; and an unknown or
absent decision still fails closed. Nor does it touch the `<env>-apply`
environment's required reviewers — that is a different control, gating the
deployment rather than the code review (see `docs/hardening.md`).

The comma grammar is `SHIPMATE_SHARED_ENVS`' (§Env model): entries are compared
whole between comma boundaries, so there are no spaces around them. What
differs is the failure mode — where a mistyped entry there is silently
un-listed, here it is refused. An entry that is not a bare env name is a
loud configuration error naming the entry: surrounding whitespace and a
`-plan` / `-apply` suffix each get their own message naming the value to write
instead, and anything outside the env charset (letters, digits, `-`, `_`) —
a pasted quote, an internal space, a path separator — is refused as not an
environment name. None of them would match anything, and a silently inert
entry would leave an operator believing an environment is ungated when it is
not.

**Unset or empty is the default and exempts nothing**: every environment keeps
the ruleset's review requirement, so *what applies* is what applied before the
variable existed. This is the opposite direction from `SHIPMATE_SHARED_ENVS`,
where unset means split.

Opting in takes two things, and the variable alone is not enough:

1. the repository variable, and
2. the consumer's `shipmate.yml` pinning both engine references —
   `.github/workflows/apply.yml@` (the `targeted` job) and
   `.github/workflows/apply-all.yml@` (the `all` job) — at or past the release
   that carries this feature. §Consumption's one-change rule already requires
   it; here breaking it fails open, not loudly, and once per reference: a
   bare `shipmate apply` authorized under `REVIEW_REQUIRED` by a fresh
   `comment-ops.yml` and dispatched into an engine older than the partition
   applies every pending environment with no approving review, and a
   targeted `shipmate apply <env>` dispatched into an engine older than the
   `review` job applies that environment unreviewed. Both edges need the
   variable set: no consumer-written input can authorize a dispatch without it.

Engine `comment-ops.yml` passes
`ungated-envs: ${{ vars.SHIPMATE_UNGATED_ENVS }}` to `actions/comment-ops`, and
`vars` inherit into a called workflow, so that expression resolves in the
consumer's repository. It is read there rather than in the consumer's file
because a composite action cannot read the `vars` context, so the input is
comment-ops' only view of the list, and both apply paths read the same variable
directly and enforce on it themselves — one source, two readers, with nothing
between them a consumer can spell differently.
`scripts/tests/test_engine_comment_ops_workflow.py` pins the whole `with:` block
of that step; dropping the input refuses every listed environment at comment
time instead, with nothing naming the cause.

The decision has two seats, because `authorize` returns one verdict per
dispatch while a bare apply spans many environments:

Both engine workflows re-read `reviewDecision` themselves in a `review` job
rather than trusting a dispatch input, and that job is unconditional — the
repository variable is the only source of this policy, and only engine-owned
workflows read it.

- **Targeted `shipmate apply <env>`** — the env is known at comment time, so
  `actions/comment-ops` refuses early: `REVIEW_REQUIRED` on a listed env
  authorizes, on any other env it refuses with the usual message extended to
  name the variable the env is missing from. The engine's `apply.yml` then
  re-applies the identical rule to the freshly read decision and refuses the
  run before any wave, leaving every apply check — and the gate — pending.
- **Bare `shipmate apply`** — authorized at comment time whenever the list is
  non-empty, then partitioned per environment by the engine's `apply-all.yml`.
  `NONE` / `APPROVED` apply everything; `REVIEW_REQUIRED` applies the listed
  environments and holds the rest — all of them when the list is empty;
  `CHANGES_REQUESTED`, an unknown value, or no decision at all holds
  everything, listed environments included.

A held environment travels the path `explicit_envs` exclusions already
travel: dropped before env-levels are built, its `apply / <stack> / <env>`
checks left pending — so `shipmate / gate` stays pending and the merge
stays blocked — and environments ordered after it are skipped. The apply result
comment names both halves: which environments were held for review, and which
applied under the exemption (§Apply result comment).

The variable is not an admin boundary. GitHub grants creating, updating and
deleting Actions variables to the Write role and above, so anyone who can
push can also edit this list, and `shipmate doctor` does not read it. What it
does buy is that relaxing the gate is a separate, deliberate act against
repository settings — it cannot ride inside the pull request that benefits from
it, the way a Terramate global in the branch could.

`shipmate unlock <env>` releases an OpenTofu state lock stranded by a cancelled
or killed apply. The env is required: a destructive verb gets no wildcard,
so there is no bare form.

It is authorized by shipmate team membership at comment time plus the
`<env>-apply` environment its job binds — the same required reviewers and the
same OIDC subject an apply of that environment binds. The other four apply
requirements are deliberately absent: reviewed, because an approving review
reviews a diff and an unlock applies none; not a draft, because a draft says
the change is not ready for review, which bounds applying it and not releasing
its lock; mergeable and undiverged,
because the case this verb exists for is a lock stranded by a cancelled
post-merge deploy, and a merged pull request reports `mergeable: null` — which
the apply path reads as "still computing" — so keeping them would leave exactly
that lock permanently unreleasable.

Because that environment is half of the authorization, the run refuses when it
does not exist — the same pre-flight the apply path runs before its first wave,
on the unlock queue. GitHub would otherwise create the environment on demand
with no reviewers, no wait timer and no branch policy, and keep it: an unlock
into a missing environment would retire the reviewer gate for every later apply
of that env.

Its queue is the cells in that environment whose `apply / <stack> / <env>` check
is still pending, not the reviewed plan artifacts: a lock outlives the run
that stranded it, and those artifacts may be long expired. Each queued cell runs
`tofu init`, then a probe (`tofu plan -input=false -refresh=false`, which takes
the lock and so prints the holder's `Lock Info:` block when one is held), then
`tofu force-unlock -force <id>` on the id parsed from that block — and nothing
else. Three outcomes per cell: no lock held and lock released are both green,
while `could not be determined` fails the cell with an `::error::` and is
never reported as "no lock held". A cell whose apply check is not pending is not
in the queue and not reachable by this verb.

It needs no IAM change. On an S3 backend with `use_lockfile`, releasing a
lock is the same `s3:DeleteObject` on `…/terraform.tfstate.tflock` that taking
one already requires, so a role that can apply an environment can already
unlock it.

It reports in the run, not in a comment — a comment would need
`pull-requests: write`, which consumers do not grant `unlock.yml`. The `rocket`
reaction confirms acceptance, per-cell detail lands in each job's step summary,
and a failure reds the run.

**Unlocking is not recovery.** A cancelled apply usually advanced state, so the
reviewed plan may now be stale and the next apply refuses under the exact-plan
fail-safe. The order is unlock, then `shipmate apply <env>`, then a re-plan if
that refuses (`docs/troubleshooting.md`).

The GitHub App carries this permission set: `actions: write`,
`pull_requests: write`, `contents: read`, `members: read`, `checks: write`,
`statuses: write`, `issues: write`, `environments: read` — the last for
`shipmate doctor`'s plan-environment secret listing (names only; no GitHub REST
path returns a secret's value, and this permission cannot write one), minted in
its own non-fatal step so an installation that has not accepted the request
leaves the `shipmate / gate` status and the apply checks untouched — it costs
two warnings in the `shipmate doctor` report: that probe reporting itself as not
performed, and the App-permission-drift probe, whose full-manifest mint asks for
this permission too and so fails until Accept.
Beyond minting the `workflow_dispatch`
token for comment-ops (events created with the default `GITHUB_TOKEN` never
trigger other workflows, so a private App is the only way to kick off the
apply workflow from a comment) and reading team membership for
authorization, the App now authors every check/status/comment/issue that
crosses a workflow-run boundary:

- **Apply checks** (`apply / <stack> / <env>`) — created pending by
  `actions/summary`, completed by `actions/apply-cell` — both mint an App
  installation token, so completion works across the create/complete
  workflow-run boundary (check runs are only updatable by the app that
  created them; using the same App on both sides keeps that identity
  consistent).
- **The aggregate gate** (`shipmate / gate`) — created/refreshed by
  `actions/summary`, completed pre-merge by `actions/gate-refresh`, completed
  post-merge inline in `deploy.yml` — all three via App token.
- **The sticky plan comment** — App-authored (marker + any Bot author for the
  lookup, since a consumer org's App bot login may differ from
  `shipmate[bot]`), upserted in place by `actions/summary`.
- **The `shipmate doctor` sticky report** — App-authored (its own marker +
  Bot-author lookup, same posture as the plan comment), upserted in place by
  `actions/comment-ops`.
- **The apply result comment** — posted fresh by `actions/apply-summary` on
  every comment-ops apply run (targeted and bare); unlike the sticky plan
  comment it is never upserted against a marker, so a failure-then-retry
  sequence stays visible across separate comments — an audit trail.
- **Drift issues** — `actions/drift-cell` holds no App token and authors
  nothing; it only plans each stack × env and uploads a drift-summary
  artifact. A separate `issues` job, bound to `shipmate-engine`, downloads
  those artifacts and opens/updates/closes the drift Issues via
  `actions/drift-issues` under an App token.

The plan matrix job's own `shipmate / <stack> / <env>` auto check-run is the one
exception: it's the job's own check-run (GitHub creates it for the job
itself), not something a separate API call authors, so it necessarily stays
on the `github-actions` identity regardless of App permissions. An on-demand
plan carries an App-authored *copy* of it: GitHub attaches a dispatched run's
job checks to the commit of the ref it was dispatched on, never to the pull
request head, so `actions/summary` mirrors that run's own completed checks
whose names begin `shipmate / ` — the cells plus `shipmate / facts` and
`shipmate / detect`, and none of the consumer file's skipped sibling jobs — onto
the head as App-authored check-runs holding a fixed line and a link back to the
original, whose step summary keeps the plan text. They exist so the pull request
shows the cells, a failed one above all — and because the sticky comment
resolves each
row's plan link across the checks on the head, which is why the mirror runs
before the comment is built. No gate or apply queue reads them: neither the
cell names nor `shipmate / facts` / `shipmate / detect` fall inside the
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

## Post-plan topology

The consumer's workflow is one file, `.github/workflows/shipmate.yml`: five
triggers, and seven jobs each gated on the event with an `if:` and each calling
one engine reusable workflow, SHA-pinned. Top-level `permissions: {}`; every job
declares its own. The plan job passes `SHIPMATE_APP_PRIVATE_KEY` and
`SHIPMATE_PLAN_PASSPHRASE` by name (never `secrets: inherit`) and `state_suffix`
as its one required input. Everything a `uses:` line reaches is engine-owned,
SHA-pinned YAML.

| event or verb | job | engine callee |
|---|---|---|
| `pull_request_target`, or `workflow_dispatch` with `verb: plan` | `plan` | `plan.yml` |
| `issue_comment` | `comment-ops` | `comment-ops.yml` |
| `push` | `deploy` | `deploy.yml` |
| `schedule`, or `workflow_dispatch` with `verb: drift` | `drift` | `drift.yml` |
| `workflow_dispatch` with `verb: apply` and an `environment` | `targeted` | `apply.yml` |
| `workflow_dispatch` with `verb: apply` and no `environment` | `all` | `apply-all.yml` |
| `workflow_dispatch` with `verb: unlock` | `unlock` | `unlock.yml` |

Each of those `if:` expressions is a contract literal, not a style choice:
`shipmate doctor`'s routing probe holds all seven and compares each one whole,
because a wrong expression sends a verb nowhere and produces a dispatched run
that completes with every job skipped — green, and no work done.

Engine `plan.yml` is four jobs: `facts`, `detect`, `plan`, `summary`. `facts`
is `actions/pr-facts`, the single producer of every pull-request fact the other
three decide on. `detect` and `plan` are untrusted: they check out the pull
request's own head and hold no App credential. `summary` is the one trusted job
— `environment: shipmate-engine`, no checkout at all. Every App-authored
surface listed above (apply checks, the gate, the sticky comments, drift
issues) is created by a job bound to that fixed GitHub Environment
(`docs/github-app.md` §Key-exposure boundary), each running at a ref that
satisfies its default-branch-only policy for a different reason.

**The calling job's name is a contract literal.** `plan`, `comment-ops` and
`drift` carry `name: shipmate`; `deploy` carries `name: post-merge`; the other
three carry no `name:` and display as their job id.
GitHub names a called workflow's check runs `<caller job> / <callee job>`, so
only a plan job named `shipmate` produces `shipmate / <stack> / <env>` plan
cells and the `shipmate / summary`, `shipmate / facts` and `shipmate / detect`
names beside them. `scripts/summary-comment` resolves each row's `[plan]` link
by that exact name. A job named anything else plans correctly and every such
link falls back to the workflow-run page; `shipmate doctor` reports it.

**Adopting this topology takes one ungatable pull request.** A `pull_request`
run uses the workflow file from the pull request's own head; a
`pull_request_target` run uses the file on the default branch. The commit that
switches the trigger therefore satisfies neither — its head no longer declares
`pull_request`, and the default branch does not yet declare
`pull_request_target` — so it produces no plan run and no `shipmate / gate`.
Merge that one pull request with an administrative bypass and restore
enforcement straight after; every pull request following it gates normally.

The move from the six files this layout replaces — `plan.yml`,
`comment-ops.yml`, `deploy.yml`, `drift.yml`, `apply.yml` and `unlock.yml` — to
this one is
ungatable for the same reason, and needs the same bypass: the plan that runs on
it is the old consumer `plan.yml` on the default branch, whose pinned engine
`build-matrix` refuses a checkout carrying no `.github/workflows/plan.yml` —
which the pull request has just deleted. Merge it with an administrative bypass;
the next pull request gates normally.

For a repository migrating from another TACO, that same pull request is
ungated by both systems at once: the outgoing tool's checks are being removed
in it, and shipmate's cannot run on it yet. Review it as the one change nothing
plans.

**Each job's `permissions:` block must be a superset of every per-job block in
the workflow it calls** — for the `plan` job, `contents: read`,
`pull-requests: read` and `id-token: write` between engine `plan.yml`'s four
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
trigger. The file's other six jobs are skipped under that trigger, each
completing as a `skipped` check-run on the same head; nothing functional depends
on them, and `scripts/mirror-checks` does not copy them.

A dispatched plan has neither side: its `head_sha` is a commit on the ref it
was dispatched on (the default branch), and nothing on the run identifies the
pull request it was dispatched for. So the checkout's `ref:` is what makes it a
plan of the pull request there too, and every check-run its own jobs create
attaches to the dispatch ref rather than to the pull request — which is why the
`summary` job mirrors this run's completed `shipmate / ` checks onto the head
commit on an `on-demand` run.

**Requirement: no job in engine `plan.yml` other than `summary` may reference a
`shipmate-engine` secret.** Under either trigger every job in that file runs at
a ref the environment's policy admits — the base ref, or the ref the plan was
dispatched on — and the `plan` job's `environment:` is chosen by
branch-authored `env/*` tags, so the file naming no such secret outside
`summary` is what keeps the key out of branch reach.
`scripts/tests/test_cells_hold_no_app_key.py` is the guard;
`docs/github-app.md` §Key-exposure boundary has the reasoning. The consumer's
`plan` job names `SHIPMATE_APP_PRIVATE_KEY` in its own `secrets:` block, and
that copy of the file is the base branch's or the default branch's — never the
pull request's own.

The four jobs:

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
  repository: fork pull requests are not planned, and no input permits one. A
  fork's plan would execute the pull request's own Terramate/OpenTofu code with
  everything the plan environment holds — `pull_request_target` withholds
  nothing from a fork's run, its *secrets* included, so this refusal plus
  `plan`'s `needs: detect` is what keeps a fork out of a plan cell once
  `actions/checkout`'s own refusal to check out a fork head under that trigger
  has been turned off or replaced (`docs/hardening.md` §16). No
  `shipmate / gate` is ever written for a fork head, so the refusal is loud
  rather than an empty matrix. The `head-repo` value the guard keys on is
  produced by `facts` in this same file rather than stated by the consumer, so
  there is no wiring a consumer can get wrong, and no event name is involved:
  the drift path already triggers on both `schedule` and `workflow_dispatch`,
  so a dispatched plan would be indistinguishable from a manual drift run. The
  drift path (`all-stacks`) is unaffected because engine `drift.yml` states that
  it has no pull request (`no-pull-request: "true"`), which is the only opt-out
  and appears in no other engine workflow.
- **`summary`** (engine, `environment: shipmate-engine`) — it downloads this
  same run's cell summaries and calls `actions/summary` under an App token
  minted inside that environment. This is what creates the pending
  `apply / <stack> / <env>` checks, the sticky plan comment, and the
  `shipmate / gate` status, and — on an `on-demand` run — the mirror of this
  run's completed `shipmate / ` checks onto the pull request's head.
  `pull_request_target` evaluates at the base branch ref and a dispatched run at
  the ref it was dispatched on, either of which satisfies the environment's
  policy. It reads every fact it decides on from `needs.facts.outputs`, and the
  rest from the two other jobs' results; nothing is recovered from artifacts or
  from a second API lookup.
- **`apply.yml` / `apply-all.yml` / `apply-env-level.yml` / `deploy.yml`**
  (engine, reached through the `targeted`, `all` and `deploy` jobs —
  `workflow_dispatch` via comment-ops, or `push` to the default branch) — the
  jobs that mint an App token (completing apply checks, refreshing the gate,
  posting the apply result comment) are likewise bound to `shipmate-engine`.
- **`comment-ops.yml`**'s `ops` job (engine, reached through the consumer's
  `comment-ops` job on `issue_comment`) — binds `shipmate-engine` for comment
  authorization and for the `workflow_dispatch` that kicks off an apply.
  `issue_comment` evaluates at the default branch's tip, never a PR head, so it
  satisfies the policy the same way `push` does.
- **`drift.yml`**'s `issues` job (engine, reached through the consumer's
  `drift` job on the nightly `schedule` or a `workflow_dispatch`) — binds
  `shipmate-engine` to author the drift Issues, and a scheduled or manually
  dispatched run evaluates at the default branch.

Nothing matches on the workflow's `name:` any more. Doctor reads the consumer's
workflow files for seven probes — stale engine pins, `pull_request_target`
triggers, a retired `plan_run_id` input in `shipmate.yml`, a retired `mode`
input in the same file, its plan-calling job name, its dispatch wiring, and its
event routing; the last three observe whether the plan comment's per-cell links
will resolve, whether a commented verb reaches anything at all, and whether the
job it reaches is the one that verb names — and they report rather than fail.

The file path is still load-bearing, and nothing diagnoses a rename as the
cause: `actions/build-matrix` refuses a checkout that has no
`.github/workflows/shipmate.yml`; `actions/dispatch` sends every commented verb
to that literal filename, naming the verb in the dispatch body, so a renamed
file is dispatched nowhere, and the pull request is told only that the dispatch
failed, with the API's refusal left in the comment-handling run that comment
links; and doctor keys on the exact name for its `pull_request_target`
exemption and for the calling-job-name, `plan_run_id`, `mode`, dispatch-wiring
and routing probes, all of which report nothing on a file called anything else.
Rename the file and planning is refused from that commit on, and the renamed
file starts drawing doctor's own `pull_request_target` warning. Each symptom
surfaces on its own — the refusal names the path it looked for — but none of
them names the rename.

No apply path matches on it to find work already planned: a dispatched, bare or
post-merge apply reads each cell's plan run from that cell's own apply check, so
a rename strands nothing that was planned before it. It does stop the dispatch
itself, for apply exactly as for plan.

Both trust decisions live on the `summary` job's `if:`, in engine-owned,
SHA-pinned YAML, and so do the facts they read:

```
!cancelled() &&
needs.facts.outputs.head-repo != '' &&
needs.facts.outputs.head-repo == github.repository &&
(needs.facts.outputs.is-draft == 'false' || needs.facts.outputs.on-demand == 'true')
```

The parentheses are load-bearing: `&&` binds tighter than `||`, so without them
`on-demand` alone would satisfy the whole guard, fork included. The fork clause
yields to nothing; the draft clause yields to `on-demand`, because skipping a
draft is autoplan's economy rather than a trust decision, and a commented
`shipmate plan` on a draft is a collaborator asking for exactly that plan. An
empty `head-repo` — the shape a failed `facts` job produces — is a refusal
rather than a pass, which is why `facts` is its own job and not `detect`'s first
step: `detect` can fail on a fmt check or stale codegen, and `summary` must
still be told which head to gate.

**A consumer states none of these facts and can weaken none of them.**
Producing them and comparing them are now the same file, so the three
mis-wirings this contract used to enumerate — a constant
`head-repo: ${{ github.repository }}`, a literal `is-draft: false`, a literal
`on-demand: true`, each of which stated the safe answer for every run, forks and
drafts included — have no site left to be written at. The consumer's file passes
secrets, permissions and `state_suffix`; the engine decides everything else.

`summary` deliberately does not require `detect` or `plan` to have succeeded: a
failed detect or plan must still produce a red gate with an explanation, because
no gate at all is a pull request nobody can diagnose. `detect` and `plan` keep
their own condition — not a draft, *or* named on demand — which is a cost
control (it stops a draft burning runners), not a security property, and is why
a requested plan runs on a draft at all.

Binding `summary` to the `shipmate-engine` environment rather than trusting the
trigger alone closes two paths a trigger check alone would not:

- A fork's plan run completes normally but produces nothing further — the fork
  clause above declines the `summary` job
  (`docs/hardening.md` §"Contributors without push access"). That is the
  autoplan leg; a commented `shipmate plan` naming a fork's pull request is
  normally refused by `actions/comment-ops` before any run exists, and §Comment-ops
  states the one case that still dispatches.
- A branch-authored workflow cannot reach the key by declaring
  `environment: shipmate-engine` itself: that environment's deployment
  branch policy is scoped to the default branch, and a job triggered by a
  `push` to any other branch — or by `pull_request`, whose ref is
  `refs/pull/<n>/merge` — never satisfies it, regardless of what the workflow
  file says. `pull_request_target` is the one pull-request-side trigger that
  does satisfy it, which is why the trust conditions above are engine-owned
  (`docs/github-app.md` §Key-exposure boundary).

## Consumption

- Consuming repositories and workflows pin every shipmate action by
  commit SHA, never by a tag or branch name (for example,
  `uses: <owner>/shipmate/actions/state@<full-commit-sha>`, not `@v1` or
  `@main`). This guarantees that a workflow's behavior cannot change
  without an explicit, reviewed bump of the pinned SHA in the consuming
  repository.
- A pinned SHA may carry a trailing `# vX.Y.Z` comment naming the release
  that SHA belongs to (`uses: <owner>/shipmate/actions/state@<sha> # v0.1.0`).
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
- The engine applies this same rule to itself: it references its own actions
  internally by full commit SHA, because GitHub resolves a local `./actions/...`
  reference against the *consuming* repo once it crosses the reusable-workflow
  boundary. Maintaining those internal pins — and deciding which commits are
  safe for a consumer to pin — is what the hand-run tooling in `dev/` is for;
  `docs/releasing.md` is its runbook. None of it is referenced from `actions/`
  or `.github/workflows/`, and it adds no action input.
- **Upgrade path.** shipmate publishes a GitHub Release per release SHA. A
  consumer with Dependabot's `github-actions` ecosystem enabled therefore
  receives a pull request bumping its shipmate pins to the new release's SHA —
  Dependabot proposes, the `CODEOWNERS` review disposes, and the ref committed to
  the consuming repository remains a full commit SHA. `shipmate doctor` reports a
  pin that differs from the latest release, so the upgrade is visible whether or
  not Dependabot is enabled. This works from a pin that is itself a released
  commit; a pin at an untagged commit gives Dependabot nothing to compare
  against, and `shipmate doctor`'s warning is the only signal.

## Runner prerequisites

- shipmate's actions are composite actions: their steps run under `bash`
  and call standard-library-only Python scripts, `git`, `curl`, `jq`, `openssl`,
  and the `gh` CLI. A runner must therefore provide: `bash`, `python3`
  (Python ≥ 3.11), `git`, `curl`, `jq`, `openssl`, and `gh`.
- Every GitHub-hosted Ubuntu image satisfies this, including the minimal
  `ubuntu-slim` image, whose
  [included-software list](https://github.com/actions/runner-images/blob/066b3201a74f4551f70c221a71c49746d02c0864/images/ubuntu-slim/ubuntu-slim-Readme.md)
  names the GitHub CLI. That one is load-bearing for the drift path: the
  default-branch probe in engine `drift.yml`'s `detect` job calls `gh api`
  before that job's `setup` step. Self-hosted runners must preinstall these
  tools.
- The Python scripts have no third-party dependencies — nothing is
  `pip install`ed at runtime, so no Python setup step (or network access
  to a package index) is required or performed.
- Terramate and OpenTofu are not assumed to be on the image: the
  `setup` action installs the versions the engine release declares in its own
  root-level `VERSIONS` file, read at the commit the consumer pins. Moving to
  other versions is a pin bump. The action's `terramate-version` /
  `tofu-version` inputs still override that file, but an *empty* value is not an
  override: it resolves to the pinned version rather than to the installer's
  latest.

## Fan-out

- One unit of work is one stack × one environment. A repository with N
  stacks and M environments (accounting for which stacks are tagged into
  which environments) fans out into up to N×M plan units and N×M apply
  units, each with its own check (see Check names, above).
- The plan fan-out is bounded at 256 cells (`build-matrix`'s `MATRIX_LIMIT`,
  the GitHub Actions matrix limit). Above it `build-matrix` raises
  `MatrixTooLarge` and `detect` fails the run before any cell starts.
  The way past it is to split the change across several pull requests: the
  matrix is built over `terramate list --changed`, so a narrower diff is a
  smaller matrix. Splitting cannot help when the fan-out comes from a one-line
  edit to a shared local module — that correctly marks every dependent stack
  changed and is one atomic change by nature — and there the only lever is to
  reduce the number of environments in play. A targeted `shipmate apply <env>`
  is not a way past it: the ceiling is enforced in the plan fan-out, so a run
  that trips it produces no reviewed plan artifact for any apply path to use.
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
path with every `/` replaced by `-` (e.g. `stacks/app` → `stacks-app`, so
`(stacks/app, dev-eu)` → `plan.dev-eu.stacks-app`). plan-cell creates it and
apply-cell downloads it — both construct the name forward from the
`(env, slug)` pair; no component reverse-parses it. No detect matches on it
at all: the apply workset comes from the head's own apply checks.

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
in `build-matrix`, at matrix construction — before any artifact exists, so the
plan run refuses up front rather than an apply discovering the clash afterwards
(rename so the path→`-` slug is unique). Every path that builds a matrix
carries it: the plan and deploy paths over their changed set, the drift path
over the whole tree, and `shipmate unlock` over the target environment. Unlike
the `env/<name>` backstop, this one is not repo-wide under a `tags` filter: the
guard runs over the cells the run will produce, so two stacks that slug alike
but fall in different slices are caught by no scoped sweep. An unscoped drift
run stays the whole-tree check for that.

This naming contract is breaking for any in-flight plan artifacts: land the
change when no applies are mid-flight. It also spans two engine references
pinned independently in the consumer's `shipmate.yml` — its `plan.yml` reference
pins `plan-cell` (the uploader) and its `apply.yml` reference pins the engine's
reusable apply workflows, which pin `apply-cell` (the downloader) internally.
Bump both pins together when adopting a build that changes this name: a partial
bump (uploader on the new name, downloader on the old, or vice versa) makes
every apply fail its reviewed-plan download fail-safe until the pins agree.

## Apply summary artifacts

Each attempted-or-blocked apply cell uploads its outcome under a name built
verbatim as:

- `apply-summary.<env>.<slug>`

using the same dot-delimited, env-first grammar as `plan.<env>.<slug>` above
(the `.` delimiter is unambiguous for the same reason: an env name cannot
contain `.`). `apply-cell` uploads it (`if: always()`, so a blocked cell
still reports); `actions/apply-summary` downloads every `apply-summary.*`
artifact for the run with the glob pattern `apply-summary.*`. It contains
verbatim:

- `cell.json` — always present, keys `stack` (display name), `stack_path`
  (Terramate stack path, feeds the check-name construction), `environment`,
  `result` (one of `applied`, `failed`, `blocked`), `reason` (which fail-safe
  blocked it, or why an earlier step failed first; the empty string for
  `applied`/`failed`).
- `apply.txt` — the apply step's combined stdout+stderr, present only when
  the apply step actually ran (absent for a cell blocked before then, a failed
  init and a plan text that did not match what was reviewed included).

`apply-cell` (writer) and `scripts/apply-comment` (reader, via
`actions/apply-summary`) are pinned by the same SHA in the consumer's
`shipmate.yml`, on its `apply.yml` / `apply-all.yml` references, so the schema
upgrades atomically; the reader fails loud on a `cell.json` missing schema keys
or carrying an out-of-enum `result` rather than rendering around pin skew.

## Plan comment

The `summary` action maintains at most one sticky comment per pull request,
identified by the HTML marker written verbatim as the comment's first line:

- `<!-- shipmate:summary -->`

A run whose cell count is zero writes the empty-table body only when that
zero means "no stacks changed" — `detect` reported a planned count of zero.
Every other zero (a planned count that disagrees with the cell summaries
actually read, a failed `detect`, plan cells that all failed
before uploading a cell summary, a failed `cell-summary.*` download) leaves the
plan unknown, so the run writes nothing at all and any existing comment — the
reviewed plan for the previous push — is left standing rather than PATCHed down
to a claim about a plan nobody read. Those runs all fail `shipmate / gate`, so
the pull request still shows that something is wrong.

Where the zero *does* mean no stacks changed, the suppression is create-only:
an existing comment is always updated to the empty-table body, because a pull
request that planned changes and then pushed them away must not keep displaying
applies that no longer exist. With no comment yet, none is posted — a docs-only
or engine-pin-bump pull request carries no shipmate comment — with one
exception: a run where `doctor` emitted a warning still posts. Doctor's
findings are annotations with no file/line, so they render only on the run page
(see §Comment-ops/doctor), and this comment's footer is their only
pull-request-visible pointer. `::notice::` findings do not trigger the
exception: they are informational, and would put a comment on every quiet run.

An existing comment is edited in place on every plan run (comment lookup is marker +
any Bot author — the shipmate App's bot login is derived from the registered
App name, which a consumer org may have had to slug differently than
`shipmate[bot]`), so GitHub's comment revision history doubles as the audit
trail of previous plans for the PR.

Structure, in order: an overview table (one row per planned stack ×
environment: verdict emoji — 🟢 no changes / 🟡 changes — add/change/destroy
counts, and a link to that cell's `shipmate / <stack> / <env>` plan-job check
run), then
one `<details>` section per changed cell containing the rendered plan
inside a `diff`-tagged code fence (change signs moved to column 0; `~` mapped
to `!`). Cells with no changes get a table row only. The verdict is
deliberately two-state: a destroy count also covers ordinary replacements, so
impact is carried by the counts rather than by a severity colour. Check links
are built forward from the cell's `(stack-path, environment)` pair using
the check-name grammar above; when the check run cannot be resolved, the link
degrades to the workflow-run URL.

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
- `plan.txt` — the `tofu show -no-color` rendering of the reviewed plan. The
  `summary` job hashes these exact bytes and records the digest on the cell's
  apply check; the apply re-renders the stored plan and refuses a difference
  (§Apply-match fingerprint).

`plan-cell` (writer) and `summary` (reader) are pinned by the same SHA in the
consumer's `shipmate.yml`, on its `plan.yml` reference, so the schema upgrades
atomically; the summary fails loud on a `cell.json` missing schema keys rather
than rendering around pin skew.

The count those summaries are measured against is not an artifact: `detect`
declares it as the `count` output of `actions/build-matrix`, and the `summary`
job receives it as the `planned-cells` input. A cell count that disagrees with
it holds the gate in either direction.

## Apply result comment

Every comment-ops apply run — targeted `shipmate apply <env>` (`apply.yml`)
and bare `shipmate apply` (`apply-all.yml`); the merge-deploy path,
`deploy.yml`, stays comment-less — posts a fresh PR comment via
`actions/apply-summary`. Unlike the plan comment above, it is deliberately
not upserted against a marker: every run's comment is new, so a
failure-then-retry sequence stays visible as separate comments rather than
overwriting the evidence of the failure — an audit trail.

Structure, in order: a header naming the targeted environment or "(all
environments)"; directly beneath it, a job-level failure line — rendered
only when the job-level `SHIPMATE_RESULTS` outcome signals a failure and no
row already carries `failed`/`blocked` (the common case has its own ❌/🚫
rows and needs no extra line), reusing the header's own wording with a ❌ so
an apply run that dies before any cell reports (a missing/denied
apply environment, a job-level cancel) still surfaces a failure
signal; an overview table (one row per expected cell — status emoji
✅ applied / ⚠️ applied but not recorded / ❌ failed /
🚫 blocked / ⏭️ not attempted, stack, env, `+A ~C -D`
resources parsed from the apply output's last `Apply complete!` line, and a
per-cell log link); one `<details>` section per attempted (applied,
failed, or applied-but-not-recorded) cell with the full apply output in a
plain code fence; one
no-fence line per blocked cell naming its `reason`. An expected cell
whose artifact never arrived (its wave/env-level was skipped after an
upstream failure, or the run was cancelled before upload) renders as
not attempted unless its apply check is already done (see the derivation
rules below) — a table row only; the comment also carries a single note,
once, whenever any cell is not attempted, that those apply checks stay
pending and can be retried — naming `shipmate apply <env>` for a targeted
run, or the bare `shipmate apply` when the run covered all environments.
There is no separate "nothing pending" input: the expected cell set is the
same waves JSON `apply-detect` / `apply-all-detect` already compute, and a
cell counts as attempted when its artifact actually downloaded or its apply
check is already done — the render can never claim nothing is pending while
holding evidence that an apply ran. The footer carries the bare-apply form's
environment-disposition sentences, a gate-completion sentence (complete
or still-pending, from the gate verdict), and the run link.

The disposition sentences are four, one per cause, and both render paths carry
the same set: the footer above and the short form used when there is no table
to render are fed by one shared function, and the size fallback keeps the
footer whole — so the comment's most actionable warning cannot be lost on
either path. Excluded environments name the
`shipmate apply <env>` that applies them; skipped ones do not name a cause, since
being skipped can mean either an unapplied explicit environment or a held
one — the excluded and held sentences carry that distinction instead. The
two review sentences (see §Comment-ops) are:

- **held** — "the pull request's review state does not permit applying",
  naming the environments and asking for an approving review, or for a
  requested-changes review to be resolved or dismissed, and naming no
  command, because a held environment may also be an explicit one that a bare
  apply would not pick up even once reviewed. The sentence is deliberately
  cause-agnostic: three distinct decisions hold an environment
  (`REVIEW_REQUIRED` on an unlisted env, `CHANGES_REQUESTED`, or an unknown or
  absent decision), and only the run's own apply-all-detect notice carries
  which one, since the decision never reaches this renderer;
- **applied ungated** — the environments the run was permitted to apply
  without an approving review, per `SHIPMATE_UNGATED_ENVS`. It is the only
  audit trail an unreviewed apply leaves: `reviewDecision` is a live value
  with no history, so once the review lands nothing else in a run
  distinguishes an apply that waited for it from one that did not. It states a
  permission, never an outcome — the set is derived before any wave runs, so
  it points at the run for what actually applied and reserves "applied" for
  the ✅ rows.

Row status is derived from both the per-cell artifact and the real state of
that cell's `apply / <stack> / <env>` check on the head SHA, which
`actions/apply-summary` reads with its own App installation token (only checks
authored by the shipmate App count, judged by the newest run per name — the
same predicate the gate and both detects use). The pending apply checks are the
work queue, so they outrank the artifact, which only records what a cell's own
job believed before its check was completed:

- ✅ **applied** — the apply succeeded and its check is recorded.
- ⚠️ **applied but not recorded** — the apply succeeded but its check is still
  pending, because `Save state`, the completion-token mint or `Complete the
  apply check` failed (or the job was cancelled) after the cell summary was
  already composed. `shipmate / gate` stays pending, and the stack needs a
  re-plan: state has advanced past the reviewed `.otplan`, so the saved plan is
  stale. The comment carries one note naming the affected cells — capped, with
  any remainder summarized as a count, since the note is part of the header
  block the size fallback cannot shed and the usual cause strands a whole
  matrix at once; the table above always carries a ⚠️ row for every one of
  them. Each such cell keeps its full `<details>` output.
- A cell whose check is done but whose artifact never arrived (the
  cosmetic, `continue-on-error` upload dropped) renders ✅ with its output
  unavailable — never ⏭️, and it does not drag the "apply check stays pending"
  note along with it.
- ❌ **failed** / 🚫 **blocked** rows are never re-derived from check state. A
  red row against a completed check means some other run applied that cell:
  the row over-reports, nothing is stranded, and downgrading it would let an
  unrelated run's completed check hide a real failure in this one.

If the check scan is unavailable — an API failure (warned, degraded to no
data), an older pinned `apply-summary` that never wrote the file, or an empty
`SHIPMATE_APP_ID` (warned) — every name reads as *absent*, which means
*unknown*, and the comment falls back to artifact-only status. Absence never
manufactures a ⚠️.

`cell.json`'s `result` grammar is unchanged (`applied | failed | blocked`).
⚠️ is a display status derived at render time, never a value `apply-cell`
writes, so the fail-loud validator still rejects an out-of-enum artifact value
as pin skew.

The 65,536-character comment cap and the up-front link-only-space reserve
work the same way as the plan comment's, above, but the truncation direction
is the opposite. A plan diff is read top-down, so the plan comment's section
keeps the head. Apply output is read for its END: a failing apply's fatal
`Error:` line and a successful apply's closing `Apply complete! Resources:
...` line are both the last thing tofu prints, so each attempted cell's
section instead keeps the TAIL — full output → truncated (front cut at a
line boundary, noting that earlier output was elided, with a link to the job
log) → link-only. `apply.txt` itself is read from the end of the file
(bounded — a fixed-size read near the tail, never the whole file) and
decoded permissively: a non-UTF-8 byte anywhere in it becomes a replacement
character instead of aborting the render. Terminal escape sequences (colour
codes and similar) are stripped from that text as it is read, since the apply
output is tofu's own and is not required to be colour-free. Every
remaining attempted cell's link-only space, and every blocked cell's
one-line reason, are reserved up
front, and the render fails loud rather than post a comment that would
exceed GitHub's cap.

The data feeding the comment ships in the per-cell artifact
`apply-summary.<env>.<slug>` (see Apply summary artifacts, above); per-cell
log links resolve against the run's job-name listing using the
`apply / <stack> / <env>` check-name grammar (see Check names, above), and
degrade to the workflow-run URL on no match.

## Apply-match fingerprint

Each plan stores a fingerprint (`fingerprint.txt` in the artifact; on the apply
check, the `external_id` JSON record holds it alongside the id of the plan run
that produced the cell and the digest of that cell's plan text, below):
`sha256` over the sorted JSON of every non-empty
`TF_VAR_*` environment variable (name→value) plus `TF_WORKSPACE` when it is set.
Ephemeral credential vars (`AWS_*`, etc.) are excluded. A set-but-empty
`TF_VAR_*` is excluded from the payload, so it now hashes identically to that
variable being absent altogether — a flavor that injects nothing and a flavor
that injects an empty string for the same name fingerprint the same way.
`TF_WORKSPACE` is included because it is the workspaces-flavor env identity and
is not a `TF_VAR_*`; without it two environments of a workspaces stack would
fingerprint identically and an apply could match the wrong environment's
reviewed plan. plan-cell and apply-cell use a byte-identical algorithm
(`scripts/plan-classify`). On mismatch, apply fails safe and reports differing
variable names only — never values.

The same shape carries the planned commit. `plan-cell` reads
`git rev-parse HEAD` as its first step — before any repository content
executes — refuses an `expected-head` input that is empty or that it did not
check out, and ships the observed commit as `planned-head.txt` inside
`plan.<env>.<slug>`. The file is materialised from the captured step output
immediately before upload rather than when it is read: between the two,
`terramate` executes author-controlled HCL and provider binaries, which can
rewrite a file at repo root but not an emitted step output. `apply-cell` moves
the record out of the consumer's checkout into `$RUNNER_TEMP` before reading it
— the action runs inside the consumer's own checkout and must leave no stray
file at its repo root — and compares it against its own `git rev-parse HEAD`
before the decrypt, the state restore and the apply — a plan of another tree is
refused at the cheapest point. A record that disagrees with the checkout is
refused, and so is an absent record: there is nothing to compare, so it is
refused rather than tolerated. Most often such a plan predates the release that
binds a plan to its tree, though a mismatched engine revision produces the same
absence; either way the remedy is a re-plan. This
is additive to the plan-run binding the apply path already carries: each cell's
plan run is read from an App-authored apply check on that same head, so no plan
run from another head can be named. That binding bounds which plan run may be
applied; this one binds each individual plan to the tree it was produced from.

A third record binds the plan *text* to the plan that executes. The trusted
`summary` job takes `sha256` over the **whole** `plan.txt` it downloaded —
computed there, never copied from the cell — and writes it as the record's
`plan_sha256` field, on the queued and the neutral check alike. The comment
embeds at most the first 60 000 characters of that file and may truncate further or
degrade to a link, so on a large plan the digest covers more than the comment
shows; it binds the file, not the excerpt.
`apply-cell` re-renders the stored plan with `tofu -chdir=<stack> show
-no-color stack.otplan`, the command that produced the file, and refuses unless
the digest matches. The comparison runs after `init`, which the render needs for
its providers, and
before `tofu apply`, so a refusal applies nothing. An absent digest is refused
too, at detect: a check written before the engine recorded one carries none, and
the remedy is a re-plan. `tofu show` of a stored plan renders identically on any
runner at one tofu version, and both `show` and `apply` refuse a plan file from
another version, so the record carries no version field.

## Secrets in published output

Both text surfaces shipmate publishes — the rendered plan `plan.txt` in the
sticky plan comment and the job step summary, and `apply.txt` in the apply
result comment — carry whatever the tool wrote: shipmate never redacts
either, and neither is covered by GitHub's log secret masking. (The published
text is not byte-identical — escape sequences are stripped, undecodable bytes
become replacement characters, and over-cap sections are truncated, per Plan
comment and Apply result comment above — but none of that removes a secret.)

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

The reviewed machine plan file (`stack.otplan`) can be encrypted at rest in the
uploaded artifact. When the consumer sets the optional `plan-passphrase` input
on `plan-cell` (in `plan.yml`), the engine encrypts the plan before upload
using a single symmetric cipher: `openssl enc -aes-256-ctr -pbkdf2 -salt`,
passphrase supplied via `-pass env:` (never on the command line). `apply-cell`
decrypts it after download on every apply path: all three paths pass it
as the optional `SHIPMATE_PLAN_PASSPHRASE` secret into the reusable
`apply-env-level.yml` workflow — via the engine `deploy.yml` for the
merge-deploy path, via the engine `apply-all.yml` for the bare form, and via
the engine `apply.yml` for the targeted form. Consumers set
`SHIPMATE_PLAN_PASSPHRASE` as a repository secret and forward it by name
in the `secrets:` block of their `shipmate.yml`'s `plan`, `deploy`, `targeted`
and `all` jobs. Never `secrets: inherit`: it hands the engine the caller's whole
secret set, and across an organization boundary it delivers nothing at all.

Not an environment secret, and specifically not on `shipmate-engine`: a
secret on one environment is released only to a job that *names* that
environment, and a plan cell names its own plan environment instead. So a
passphrase scoped to `shipmate-engine` resolves to empty at
plan time and every later apply fails its plaintext-artifact check — the ref the
plan run happens to be at is beside the point. Scoping it
to a plan environment buys nothing either — those must have no branch policy at
all (`docs/hardening.md` §6), so any branch's workflow can name one and read it.
Unlike the App private key, this secret must be readable wherever plans are
produced, which is any branch; `docs/hardening.md` #7–9 says to treat it so.

- **Backward compatible.** An empty/unset `plan-passphrase` leaves the plan
  plaintext and the uploaded bytes byte-identical to a no-encryption run.
- **Fail-safe on mismatch.** apply-cell refuses to proceed rather than apply the
  wrong thing: a plaintext artifact when a passphrase is configured, or an
  encrypted artifact (`Salted__` magic header) when none is, both fail loud with
  a re-plan / set-the-secret instruction. A wrong passphrase is not detected
  at decrypt (AES-CTR is unauthenticated and decrypts to garbage without error);
  the exact-plan invariant catches it — `tofu apply` rejects the garbage plan and
  the apply check stays pending.
- **Scope: the machine plan file only.** `fingerprint.txt` and
  `planned-head.txt` are a hash and a commit sha, and stay plain. The rendered plan `plan.txt` — in the `cell-summary` artifact, the PR
  sticky comment, and the job step summary — stays plaintext: it is the
  deliberately-public reviewer view, as is `apply.txt`. Encryption protects the
  machine plan at rest and nothing else; redaction in the published text comes
  from `sensitive` marking (see Secrets in published output, above).
- **Both sides must agree.** `plan-cell` (encrypt) and `apply-cell` (decrypt) are
  pinned independently (the `plan.yml` and `apply.yml` references in the
  consumer's `shipmate.yml`); the passphrase and the engine SHA must match on
  both. A mismatch surfaces as the fail-safe above, not a silent wrong apply.

## Engine-owned tofu invocation

The engine names the `tofu` command every cell runs; a consumer repository
supplies stacks, generated `.tf`, and Terramate metadata, never the command
line. Cells call `terramate run --disable-safeguards=git-out-of-sync
--no-recursive -C <stack> -- tofu …`, so Terramate contributes the stack working
directory and the safeguard policy only:

| Cell | Commands |
|---|---|
| `plan-cell`, `drift-cell` | `tofu init -input=false -reconfigure`; `tofu plan -input=false -lock=false -out=stack.otplan` |
| `apply-cell` | `tofu init -input=false -reconfigure`; `tofu apply -input=false stack.otplan` |

Four `tofu show` invocations sit outside that wrapper, invoked directly:
`plan-cell` renders both the reviewed plan text and its JSON form, `drift-cell`
renders the JSON form, and `apply-cell` re-renders the plan text to compare
against the recorded digest (§Apply-match fingerprint). Rendering a stored plan
reads a file and touches neither state nor the stack graph, so it needs no
wrapper. `plan-cell`'s text render and `apply-cell`'s stay byte-identical to
each other; that equality is what the plan-text binding rests on.

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

**Mechanism (engine-controlled).** The cells invoke `terramate run … -- tofu …`
— `plan-cell`, `apply-cell`, `drift-cell`, each twice (an `init`, then the
`plan`/`apply`) — and every one of those invocations passes
`--disable-safeguards=git-out-of-sync`. The policy is versioned in the engine
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
`plan-cell`, `apply-cell`, and `drift-cell`, and across both invocations within
a cell — exactly `{git-out-of-sync}`. A drift between the three cells, or
between a cell's two invocations, is a defect (guarded by a test, like the
TF_VAR fingerprint).

**Consumer gitignore requirement.** A consuming repository must gitignore the
per-run machine artifacts shipmate materializes in its working tree — the
reviewed plan (`*.otplan`), the fingerprint (`fingerprint.txt`), the planned
commit record (`planned-head.txt`), OpenTofu's working directory in each stack
(`.terraform/`), and the flavor's state path when it has one (a remote backend materializes none — see State backend, above). The
reason is not a safeguard: shipmate writes into the consumer's own checkout, none
of those belong in a commit, and a `terramate run` of the consumer's own that
omits `--no-recursive` refuses on them (`git-untracked` *does* fire there).

`.terraform.lock.hcl` is the consumer's call, not shipmate's: committing it is
OpenTofu's own recommendation for pinning provider versions and hashes, and a
cell tolerates it either way — `init -reconfigure` may rewrite it, but that is a
tracked-file change and `git-uncommitted` never runs on a cell. Committing it
also makes `actions/setup`'s provider cache key
(`hashFiles('**/.terraform.lock.hcl')`) vary with the actual provider set instead
of hashing nothing.

## Env apply order

A repository may declare a partial order over its GitHub Environments so that
one environment's stacks fully apply before another's — for example, "`eu`
fully green, then `us`." The order is a Terramate global,
`global.shipmate.env_order`: a map from an environment name to the list of
environments that must complete their applies first (its predecessors). An
environment absent from the map, or the whole global absent, is unordered
relative to everything else.

The merge-deploy path topologically sorts this map into `env-levels`
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
an environment not applying this run — held for review or excluded as
explicit — are skipped with a notice — their ordering precondition cannot be
met in that run, exactly like a failed predecessor level. Completed cells skip idempotently, so re-commenting `shipmate apply`
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
`apply-env-level.yml` call → gate refresh + result comment), and the unlock path
as `.github/workflows/unlock.yml` (guard → single-env detect → one flat unlock
matrix; it takes `environment` and `ref` only, and declares no secrets). A
consuming repo reaches all four from `shipmate.yml`: the `deploy` job (on
`push` to the default branch; passes only its flavor's `state_suffix`, which it
sets to `''` on a remote backend), the `targeted` and `all` jobs (dispatched
`apply`, split on whether an `environment` was given) and the `unlock` job
(dispatched `unlock`; no `state_suffix` and no `secrets:` block). All four must
grant `id-token: write`, added in the same pull request that repins past the
change introducing it (see AWS OIDC, above).

## OpenTofu note

OpenTofu reserves the variable name `version` as a meta-argument; it cannot
be declared as an input variable in a module or root configuration. Sample
stacks in this project therefore use `app_version` wherever a version
string for the deployed workload needs to be passed through as a
`TF_VAR_*`/OpenTofu variable, never `version`.
