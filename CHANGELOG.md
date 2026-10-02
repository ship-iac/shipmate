# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Consumers pin this engine's actions and reusable workflows by **commit SHA**, not
by tag (see `CONTRACT.md`), so a release only reaches a repository when that
repository re-pins — and every engine reference must move in one change. Each
section below names the SHA the release tags.

## [Unreleased]

### Added

- **`shipmate doctor` names each gated environment at 0 required approvals: a note while code-owner review is on, a warning while it is off.**
- **The apply run's log carries a notice annotation naming each gated environment applied with no approving review required.**
- **A cell whose `tofu init` fails after restoring a provider cache entry names that entry's key in an error annotation.**
- **`scripts/onboard`'s closing checklist names the stacks without a git-tracked `.terraform.lock.hcl`.**
- **`shipmate doctor` lists the role each environment resolves, per path and workload.**
- **A listed workload that no stack tags is warned about on the paths that scan the whole tree.**

### Changed

- **Credentials live only in `[identities.<name>]`; an environment names one with `identity` and lists the workloads it admits with `workloads`.**
- **A stack carrying two `workload/*` tags is refused at detect.**
- **A workload tag outside the environment's `workloads` list is refused at detect, naming every such cell.**
- **A draft pull request's run writes `shipmate / gate` pending, naming the draft and the two ways to plan it.**
- **The bare-apply comment lists a held `explicit` environment once, with both reasons and the `shipmate apply <env>` command.**
- **An invalid `.github/shipmate.toml` is refused with every structural error it holds, one annotation each.**
- **`scripts/onboard` takes no `--team`.**
- **`scripts/onboard --key` is needed only while `shipmate-engine` holds no App private key.**
- **`scripts/onboard`'s closing checklist marks each item `ok`, `todo` or `cannot check` from what the run read.**
- **The provider cache serves only stacks with a committed `.terraform.lock.hcl`, keyed on the lock's provider addresses and versions, and drift and apply cells save it; plan cells never do.**
- **The plan and unlock workflows run with `cache-mode: read`, so pull-request HCL cannot save an Actions-cache entry that a drift or apply job restores.**
- **Plan cells render the text and JSON plans concurrently.**
- **The control jobs that run no `tofu` run on `ubuntu-slim`.**
- **The plan summary's and `shipmate doctor`'s App tokens request `environments: read`; the plan-environment secret probe has no token of its own and no not-checked warning.**
- **Comments by bots or without `shipmate` start no comment-ops job.**
- **`shipmate apply` and `shipmate unlock` require write or admin permission on the repository; the `[gate]` table and the App's `members` permission are removed.**
- **Plan, apply and doctor comments share one shape: a header, a verdict line linking the commit and the run, and one line per cell or finding. Refusals, failures, notices and help share the header and end with a run link. The help hint shows only where action is needed: on a refusal or a failure, and under an apply or doctor verdict that is not 🟢; a plan comment never shows it. The apply comment's gate, ungated and no-review lines moved to the gate check and the run log.**

### Fixed

- **The Terramate download retries transient errors three times, and a failed install names the release URL and the status it answers.**
- **`shipmate / gate`'s hold is read from every page of the head's status contexts, so a head carrying many no longer lets an apply green a held gate.**
- **`scripts/onboard` and `scripts/register-app` write UTF-8 on a Windows console, and the docs run them with `python`.**
- **The provider cache no longer serves an empty entry saved by a detect job.**
- **A stack name or annotation text spelling an HTML entity, such as `&#91;`, renders as written in a shipmate comment.**
- **A `rocket` reaction that cannot be posted no longer fails an authorized comment command before its dispatch.**

## [0.41.0] — 2026-09-28

Tags `7ff9345`.

### Added

- **A cell refuses `SHIPMATE_APP_PRIVATE_KEY` and `SHIPMATE_PLAN_PASSPHRASE` set as GitHub
  variables.**

### Changed

- **`onboard` creates the gate ruleset once `.github/workflows/shipmate.yml` is on the default
  branch**, so the first pull request merges without a bypass. A gate required before then is
  reported.
- **`onboard` renders the repository's default branch into `push: branches`**, and refuses one
  it cannot write there unquoted.
- **`onboard` qualifies the reviewer step on private repositories.**
- **Four error messages name the current cause.**

## [0.40.0] — 2026-09-28

Tags `30ba9e7`.

### Added

- **`actions/verify-app-key`**, the App-key precondition that runs before every App-token
  mint except comment-ops'. It replaces eight inline copies.

### Changed

- **`shipmate doctor` has fourteen probes, not sixteen.** The probes that warned about a
  retired `plan_run_id` or `mode` input in `shipmate.yml` are gone.
- **The apply review job shows as `<caller> / review / decision`.** Both apply paths call one
  engine `apply-review.yml`.
- **`scripts/onboard --vars-at-org` takes only `SHIPMATE_APP_ID`**; any other value, a comma
  list included, is refused. The checklist drops the older-pin caveat for
  `[gate] approver_team`.
- **`apply-snapshot`'s empty-App-id refusal** uses `apply-gate`'s wording, which points at
  `docs/github-app.md`.

### Removed

- **Composite action inputs every engine caller set to the same value:** `github-token`,
  `stack-name` on the cell actions, `setup`'s `terramate-version` / `tofu-version`,
  `cells-dir`, plan-cell's `retention-days`, and dispatch's `dispatch-ref` / `repository`.
  `setup` installs the versions in the release's `VERSIONS` file. comment-ops' unread
  `is-command` output is gone too.

### Fixed

- **A missing `checks.jsonl` is annotated.** The apply result step logs a `::warning::`
  instead of rendering the comment without apply-check state.

## [0.39.0] — 2026-09-27

Tags `ca75e4e`.

### Changed — BREAKING

- **The Slack webhook is the secret `SHIPMATE_SLACK_WEBHOOK` on `shipmate-engine`,** mapped
  by name on the `deploy` and `drift` jobs. The `SLACK_WEBHOOK` variable is no longer read.
- **A `SHIPMATE_SLACK_WEBHOOK` variable fails the deploy and drift runs** in the last step of
  `deploy.yml`'s `summary` job and `drift.yml`'s `issues` job. A cell refuses one too.

### Fixed

- **The deploy failure notice fails its step on a rejected webhook** (`curl
  --fail-with-body`).

## [0.38.0] — 2026-09-27

Tags `2435bdb`.

### Fixed

- **An import-only plan counts as a change.** `scripts/plan-classify` read an `import`
  block's resource as unchanged, so its cell got a neutral apply check and never applied.
- **The plan comment's counts come from the plan text the apply verifies**, not from the plan
  cell's `cell.json`, which no longer carries `add`, `change` or `destroy`.

## [0.37.0] — 2026-09-27

Tags `352eb83`.

### Changed — BREAKING

- **`.github/shipmate.toml` takes its final key names.** Top level: `schema_version`,
  `layout` (`tf_vars`, `workspace` or `folder`), `[environments.<name>]` and `[gate]`.
  `[gate]` holds `approver_team`. An entry holds `region`, `tf_vars`, `aws`, `shared`,
  `needs`, `explicit` and `gated`. A variable reference is `{ vars = "NAME" }`. Any other
  key refuses as unknown.
- An entry name outside the env-name charset, or with a `-plan` / `-apply` suffix, refuses.
  `explicit` and `gated` are TOML booleans and accept no variable reference.

## [0.36.0] — 2026-09-26

Tags `836b768`.

### Added

- **Any string value in `.github/shipmate.toml` can name a GitHub variable** with
  `{ var = "NAME" }`, resolved by every reader of the file. An unset or empty variable or an
  invalid name refuses. `shipmate doctor` lists every reference. The detect actions,
  `comment-ops` and `summary` take a new `github-vars` input.

## [0.35.0] — 2026-09-26

Tags `6c6aed1`.

### Changed

- **A `run.env` override is refused in the cell, not at detect.** `scripts/env-inject`
  refuses before `tofu init` when a row's `tf_vars` come back changed or unset through
  `terramate run`. `run.env` may now set `TF_VAR_env` under `layout = "folder"` and
  `TF_WORKSPACE` under `dry` and `folder`.

### Fixed

- **A `run.env` that rewrites a table-resolved variable no longer passes**, including a
  stack-conditional rewrite, a `run.env` block below the root, a name from an environment's
  `vars`, and the dispatched `shipmate apply` path.

## [0.34.0] — 2026-09-25

Tags `3c9c71e`.

### Changed — BREAKING

- **The state path is read from `tofu init`, not declared.** `state_suffix` is removed from
  every reusable workflow, and the `plan-cell`, `drift-cell` and `apply-cell` actions'
  `state-path` input is gone. A backend other than `local` skips the state steps.
  `scripts/onboard` drops `--state-suffix`.

## [0.33.0] — 2026-09-25

Tags `c22c2c3`.

### Changed — BREAKING

- **A shared environment is declared in the table** with `shared = true` in an
  `[environments.<env>]` entry. The `SHIPMATE_SHARED_ENVS` variable is no longer read, the
  `shared-envs` action inputs are gone, and `scripts/onboard` drops `--shared`. Every cell
  job binds `${{ matrix.env_binding }}`. `shipmate doctor` reads the mode from the default
  branch's file.

## [0.32.0] — 2026-09-25

Tags `10d16e0`.

### Changed

- **Engine actions resolve through `$/`, with no checkout.** The 25 `actions/checkout` steps
  and the `.shipmate-engine/` directory are gone. Self-hosted runners need 2.336.0 or newer.

## [0.31.0] — 2026-09-20

Tags `4172108`.

### Changed — BREAKING

- **The approvers team and the ungated-environment list move into `.github/shipmate.toml`**
  as `gate.approvers_team` and `gate.ungated_envs`, read from the default branch.
  `SHIPMATE_APPROVERS_TEAM` and `SHIPMATE_UNGATED_ENVS` are no longer read at any level.

### Added

- **`[gate]` in `.github/shipmate.toml`**, holding `approvers_team` (a bare team slug) and
  `ungated_envs` (a list of bare env names). An empty value means what it says.
- **An optional `version` key**, which must be the integer `1`.

### Changed

- **The engine pins nothing of itself.** Every job that runs an engine action checks
  `ship-iac/shipmate` out at `job.workflow_sha` into `.shipmate-engine/` and runs its actions
  from there. The composite actions are engine-internal. `internal-pins.yml`,
  `dev/pin_status.py` and `dev/repin_internal.py` are gone.
- **`actions/comment-ops` and both apply paths' detect resolve `gate.ungated_envs` from the
  default branch's file.** comment-ops reads it through the contents API.
- **An unresolvable gate table refuses `shipmate apply` and `shipmate unlock` comments.**
- **The user-facing sentences name the setting, not the variable.**
- **`shipmate doctor` reports the approvers team from the file** and warns when the slug does
  not resolve in the org (`report` mode only).
- **`scripts/onboard` no longer writes `SHIPMATE_APPROVERS_TEAM`;** `--team` is printed in
  the by-hand checklist. `--vars-at-org` accepts `SHIPMATE_APP_ID` only.

### Removed

- **The `SHIPMATE_APPROVERS_TEAM` and `SHIPMATE_UNGATED_ENVS` repository variables,** and the
  `approvers-team` / `ungated-envs` inputs of `actions/comment-ops`, `actions/apply-detect`
  and `actions/apply-all-detect`.
- **`scripts/onboard` stops reporting the retired six-file workflow layout** and the
  `TERRAMATE_VERSION` and `TOFU_VERSION` repository variables.
- **`docs/upgrading.md` §Past migrations.**

### Fixed

- **An uppercase entry in `explicit_envs` or `gate.ungated_envs` is refused** rather than
  silently matching nothing. Breaking for a file carrying such an entry.
- **A key that references an environment no stack tags now warns by name:** `explicit_envs`,
  `env_order` keys and predecessors, and `gate.ungated_envs`.

## [0.30.0] — 2026-09-15

Tags `f1688c0`.

### Changed — BREAKING

- **The environment table moves out of Terramate globals into `.github/shipmate.toml`,** as
  four top-level keys — `layout`, `environments`, `env_order`, `explicit_envs` — read from
  the default branch with `tomllib`. The `globals "shipmate"` block is no longer read.

### Changed

- **`env_order` and `explicit_envs` are read from the default branch**, not the checked-out
  tree.
- **Validation is strict about top-level keys and reserved names.** A fifth top-level key is
  refused by name, as is a top-level control name used as an `env_order` key.
- **One validation entry point covers all four fields.**
- **`shipmate doctor` validates the file at the commit under examination** through the
  contents API.
- **Reading the table needs Python 3.11 on the runner.** `scripts/env-config` refuses with
  the version it found.
- **`scripts/onboard`'s by-hand checklist asks for `.github/shipmate.toml`.**

### Removed

- **The Terramate worktree-and-evaluate read path**: no `git worktree`, no
  `terramate experimental eval`, no second evaluation in `scripts/env-order`.
- **The `layout = null` refusal**, which TOML cannot express.

## [0.29.0] — 2026-09-14

Tags `1923428`.

### Added

- **A consumer's GitHub variables and a `SHIPMATE_SECRETS` envelope reach every cell.**
  `TF_VAR_*` suffixes are lowercased; `SHIPMATE_VARS` and `SHIPMATE_SECRETS` are JSON
  envelopes, the secret one masked line by line. Reserved names, invalid keys and names two
  channels supply are refused. `CONTRACT.md` §Consumer variables and secrets is the policy.

### Changed

- **Every callable workflow whose jobs run a cell declares `SHIPMATE_SECRETS`,** and the
  `plan`, `deploy`, `drift`, `targeted`, `all` and `unlock` jobs of `shipmate.yml` map it.

## [0.28.0] — 2026-09-13

Tags `b0d9a41`.

### Added

- **A repository declares its environment identity, roles and regions in a
  `globals "shipmate"` table read from the default branch.** `global.shipmate.layout` is
  required; a repository without the block is refused. An unused entry warns. The four
  `actions/*-cell` gain a `tf-vars` input and the four detect actions a `shared-envs` one.
- **`scripts/onboard --vars-at-org` skips the variables an organization already sets**
  (`SHIPMATE_APP_ID`, `SHIPMATE_APPROVERS_TEAM`), after verifying each against the
  organization variables that reach the repository.

### Changed — BREAKING

- **A cell's identity and credentials come from the environment table.** The GitHub
  Environment variables `AWS_ROLE_ARN`, `AWS_REGION`, `TF_VAR_env`, `TF_VAR_region`,
  `TF_WORKSPACE` and `AWS_ROLE_ARN_<WORKLOAD>` are no longer read.
- **One writer sets a cell's identity variables:** `scripts/env-inject` writes what the table
  resolved into `$GITHUB_ENV`. Every matrix row carries `role_arn`, `cred_region`, `tf_vars`
  and `config_path`; the table's workload tier is keyed by the raw `workload/<name>` tag.
- **`scripts/onboard` no longer tells a consumer to create the five identity variables.**
- **The Terramate and OpenTofu versions come from the engine release's `VERSIONS` file,** not
  from the `TERRAMATE_VERSION` / `TOFU_VERSION` repository variables. `scripts/onboard` no
  longer writes either name. An explicitly empty `setup` version input resolves to the
  pinned version.

## [0.27.1] — 2026-09-12

Tags `55bf06b`.

### Changed

- **The helper scripts share one module loader,** `scripts/_shipmate.py`.
- **The wave and environment-level limits are enforced inside the functions that emit
  them** (`pad_waves`, `waves_by_env_level`). A change spanning too many levels now prints
  the DAG-shape and `apply-detect` notices before it refuses.
- **`shipmate doctor`'s five `shipmate.yml` probes share one warning renderer.**
- **Drift reporting reuses the shared command runner.**

### Fixed

- **CodeQL code-quality findings**; the `SIM` and `RET` ruff rule sets are enforced in CI.

## [0.27.0] — 2026-09-07

Tags `457ea2b`.

### Changed — BREAKING

- **One `.github/workflows/shipmate.yml` per consumer, replacing `plan.yml`,
  `comment-ops.yml`, `deploy.yml`, `drift.yml`, `apply.yml` and `unlock.yml`.** Five
  triggers and seven jobs, each selected by its own `if:`. Check names are unchanged.

### Changed

- **`actions/dispatch` sends every verb to that one filename** and names the verb in the
  dispatch body.
- **`actions/build-matrix` requires `.github/workflows/shipmate.yml`.**
- **`scripts/mirror-checks` copies only check-runs named `shipmate / …`.**
- **`scripts/onboard` writes the one file** and reports each retired filename as `differs`.

### Added

- **A routing probe in `shipmate doctor`**, comparing each of the seven `if:` expressions
  whole against the `docs/getting-started.md` fence. The dispatch-wiring probe requires all
  four `workflow_dispatch` inputs and the exact `verb` option list.

## [0.26.0] — 2026-09-06

Tags `c622ff7`.

### Added

- **`scripts/onboard` — one command per consumer repository.** It reconciles the
  `shipmate-engine` environment, the per-environment pair, the App key, the repository
  variables, the `shipmate-gate` ruleset and the workflow shims: writes only what is absent,
  reports `differs` otherwise, supports `--dry-run`, and prints a checklist for what it
  cannot set. Shim bodies are rendered from the docs' YAML fences.

### Changed

- `docs/github-app.md` §Appendix's shell loop is replaced by a loop calling the script.
- `docs/getting-started.md` gains a Quick path; `docs/troubleshooting.md` explains every
  line the script reports.
- `scripts/register-app` points at `scripts/onboard` for the per-repository half.

## [0.25.0] — 2026-09-06

Tags `9e12610`.

### Changed — BREAKING

- **The plan, drift and comment-ops job graphs move into the engine.** A consumer's
  `plan.yml`, `drift.yml` and `comment-ops.yml` are shims: triggers, `permissions:`, and one
  job named `shipmate` calling the engine's `plan.yml`, `drift.yml` or `comment-ops.yml`.
- **Plan and drift check names gain a `shipmate / ` prefix:** `shipmate / <stack> / <env>`,
  `shipmate / facts`, `shipmate / detect` and `shipmate / summary`. `shipmate / gate` is
  unchanged.
- **`plan.yml` and `drift.yml` take a required `state_suffix` input**, and their calling jobs
  must grant `id-token: write`.

### Changed

- **The engine's reusable `summary.yml` is gone;** its job is the `summary` job of the
  engine's `plan.yml`.
- **The plan and drift cells run the apply path's AWS OIDC step**, workload override
  included. A role set at repository or organization level is now assumed by every plan and
  drift cell; only the role's trust policy bounds that.
- **`SHIPMATE_UNGATED_ENVS` is read by the engine's `comment-ops.yml`;** the consumer-written
  `ungated-envs:` input is gone.
- **`shipmate doctor` retires five probe halves and gains one**, reporting a `plan.yml` shim
  whose calling job is not named `shipmate`.

## [0.24.0] — 2026-09-05

Tags `9916735`.

### Added

- **The reviewed plan text is bound to the plan that executes.** The `summary` job records
  `sha256` over each cell's `plan.txt` on its apply check, and `actions/apply-cell` re-renders
  the stored plan and refuses any difference before `tofu apply`. A plan produced before
  this release carries no digest and is refused.

### Changed

- **Each of `actions/apply-cell`'s fail-safes has a step of its own** — `digest-input`,
  `init`, `plan-digest`, `apply` — so each refusal names its own blocked reason.

## [0.23.0] — 2026-09-05

Tags `d780e60`.

### Added

- **A dispatched verb that never starts now says so on the pull request.** `actions/dispatch`
  posts one comment for each of its three refusals — an unwired verb, an unknown one, and a
  rejected dispatch — with a link to the run that holds the error.
- **`shipmate plan` naming a fork's pull request is refused before it is dispatched.** A
  head it cannot read is dispatched anyway with a `::notice::`.

### Changed

- `shipmate doctor`'s two plan-wrapper dispatch findings name the comment.

## [0.22.2] — 2026-09-05

Tags `83d69cb`.

### Changed

- **The embedded Python in `actions/` moved to `scripts/`, where `ruff` lints it:**
  `apply-cell-summary`, `dispatch-body`, `drift-cell-summary`, `plan-cell-summary`,
  `doctor-cells` and `gate-status-body`. One block stays inline in `deploy.yml`'s `summary`
  job.
- **The guards over that logic read the scripts, and pin that the actions still run them.**

## [0.22.1] — 2026-09-05

Tags `71e1aa4`.

### Changed

- **Comments and docstrings cut to the repository cap.**
- **Docs pages read one claim per sentence.** Headings, examples, link targets, commands and
  setting values are unchanged.
- **`docs/development.md` gains §Writing.**

## [0.22.0] — 2026-08-31

Tags `cda2a93`.

### Added

- **`actions/build-matrix` takes a `tags` query, so a drift sweep can cover a slice of the
  repository.** `,` is OR, `:` is AND, in the on-disk tag form; the query narrows cells. It is
  refused unless `no-pull-request: true`, and an empty term, a term no stack carries or a
  query matching no cell fails the run.

### Changed

- **The drift Issue body says it closes on the next clean run that covers that cell.**

## [0.21.0] — 2026-08-29

Tags `11b31b5`.

### Changed — BREAKING

- **One entry point per verb.** A comment's verb selects a consumer workflow file —
  `plan.yml`, `apply.yml`, `unlock.yml`. `actions/comment-ops` exposes the route as `verb`;
  `actions/dispatch` maps it to a filename and refuses an empty one. The `mode` rail is
  retired.
- **`shipmate unlock` no longer runs on the apply path.** The new reusable
  `.github/workflows/unlock.yml` takes `{environment, ref}` and declares no secrets. The
  engine's `apply.yml` loses the `mode` input and the unlock job.

### Changed

- **`plan_workflow_error` names the `CONTRACT.md` path it enforces.**

### Added

- **`shipmate doctor` reports a consumer `apply.yml` that still carries `mode`.**

## [0.20.0] — 2026-08-27

Tags `dbcc4c5`.

### Changed — BREAKING

- **`plan.yml` gains a `workflow_dispatch` trigger with a `pr_number` input, a `facts` job
  calling `actions/pr-facts`, `head-sha` on its `build-matrix` step and `on-demand` on its
  `summary` call.** Every `github.event.pull_request.*` reference in the jobs becomes
  `needs.facts.outputs.*`.

### Added

- **`shipmate plan` is a verb.** It takes no arguments and produces the same plan a push
  does.
- **A plan can be requested on a draft pull request.** Autoplan still skips drafts; applying
  a draft is refused.
- **`actions/pr-facts`, one producer for every pull-request fact the plan path decides on.**
  A dispatch resolves the facts from the pull-request number.
- **A dispatched plan's per-cell checks are mirrored onto the pull-request head.**
- **A thirteenth `shipmate doctor` probe** reports a plan wrapper that cannot be dispatched.

### Changed

- **`build-matrix` refuses a checkout that is not the commit the run states it is planning,
  on every trigger**, comparing against `head-sha` and refusing by default.
  `no-pull-request` remains the only opt-out.
- **`detect` refuses a fork before `terramate` reads its tree.**

## [0.19.0] — 2026-08-25

Tags `270c03b`.

### Changed — BREAKING

- **The plan run id travels on each apply check, not through the wrappers.** Each
  `apply / <stack> / <env>` check records its plan run in its `external_id`, and every apply
  path reads it per cell. The `plan_run_id` dispatch input and `authorize` output are gone.

### Changed

- **A partially failed plan run's healthy cells are individually applicable, pre-merge.**
- **`shipmate doctor` reads a partially failed run's cell summaries too.**
- **`shipmate doctor` reports a wrapper that still declares or forwards `plan_run_id`.**
- **The stack-path slug collision refuses at matrix construction in `build-matrix`,** on
  every matrix-building path, drift and `shipmate unlock` included.
- **Three refusals were re-worded to what is still true:** the missing-plan-workflow
  refusal, the absent-plan-run refusal, and `apply-cell`'s absent-record refusal.

## [0.18.0] — 2026-08-23

Tags `468429a`.

### Changed — BREAKING

- **The fork refusal keys on the head repository the wrapper states, and refuses by
  default.** `actions/build-matrix` takes a `head-repo` input and no longer reads the event
  payload for this decision; `no-pull-request: "true"` is the one opt-out.
- **The trusted summary job decides on inputs, not on `github.event.*`.** The engine's
  `summary.yml` takes `head-repo` and `is-draft`, and an empty one skips the job.

### Changed

- **`shipmate doctor` gained an eleventh probe** over `plan.yml`'s `head-repo` / `is-draft`
  wiring and any `no-pull-request`. Its fork-trigger probe also reports
  `allow-unsafe-pr-checkout` set to anything but `false`.

### Fixed

- **A `pull_request_target` plan run whose event payload carries no head commit is refused
  rather than skipped.**

## [0.17.0] — 2026-08-23

Tags `5f4a022`.

### Added

- **A reviewed plan carries the commit it was produced from, and an apply refuses a plan
  produced from a different tree.** `plan-cell` takes a required `expected-head` input and
  ships `planned-head.txt`; `apply-cell` refuses a mismatch or a missing record. BREAKING: a
  plan produced before this release cannot be applied after it.

## [0.16.2] — 2026-08-22

Tags `69da4e4`.

### Fixed

- **`shipmate unlock` could not recognise a lock it was looking straight at:**
  `scripts/lock-info` did not strip OpenTofu's colour escape sequences, so the cell reported
  the lock state as undetermined.

## [0.16.1] — 2026-08-22

Tags `da5a5ff`.

### Fixed

- **`v0.16.0`'s `actions/apply-detect` manifest does not load, which breaks the apply and
  deploy paths.** An unquoted comma split an output description. A guard asserts every
  manifest's inputs and outputs carry only keys GitHub defines.
- The consumer `apply.yml` wrapper's `plan_run_id` input must be `required: false` with an
  empty default, or every unlock dispatch fails with HTTP 422.
- The dispatch failure message blames a missing `mode` input only when GitHub names `mode`.

## [0.16.0] — 2026-08-22

Tags `4fda446`.

### Added

- **A stranded state lock is visible and releasable from the pull request.** The apply
  result comment carries a 🔒 line per lock-blocked cell, and `shipmate unlock <env>`
  force-unlocks only the lock id its probe read back. Wiring needs a `mode` input on
  `apply.yml` and `comment-ops.yml`'s dispatch step.

### Changed

- **The plan comment's verdict is two-state:** `🟢` no changes, `🟡` changes. A destroy
  count no longer renders `🔴`.

### Documentation

- `docs/hardening.md` gains a **Plan prerequisites** section. Row 6 (environment required
  reviewers, wait timers) needs Enterprise on a private repository;
  `docs/branch-protection.md`'s private-repo caveat is corrected the same way.

## [0.15.0] — 2026-08-17

Tags `19de76c`.

### Added

- **Per-environment review gating (`SHIPMATE_UNGATED_ENVS`).** A repository variable naming
  the environments `shipmate apply` may apply without an approving review; it exempts
  `REVIEW_REQUIRED` only. A bare `shipmate apply` on an unreviewed pull request holds the
  unnamed environments; a targeted apply of one is refused. `comment-ops` takes an
  `ungated-envs` input.

### Changed

- **Both apply workflows run an unconditional `review` job** that re-reads the pull
  request's `reviewDecision` before anything applies. It adds one `shipmate-engine`
  deployment record per apply run and mints an App token before any wave.

## [0.14.2] — 2026-08-13

Tags `741118f`.

### Fixed

- **`shipmate doctor` reports ambiguous environment naming even when the declared
  environment set is unknown**, from the environments listing alone, for every logical
  environment in the repository.
- **A documented claim that was wrong in the fail-open direction:** a layout whose
  `TF_VAR_env` has no default does not fail loudly when a plan cell binds an auto-created
  empty environment. `docs/upgrading.md` §0.13.0 and `CONTRACT.md` §Env model are corrected.

### Added

- Documentation from a consumer's per-workload-role rename: the split/shared asymmetry in
  `docs/upgrading.md` §0.13.0, stale OIDC subjects in `docs/aws.md`, `SHIPMATE_SHARED_ENVS`
  in `docs/hardening.md` §7–9, base-branch `CODEOWNERS` in `docs/hardening.md` §3–5 and
  `docs/branch-protection.md`, and the ambiguity exemption in `docs/troubleshooting.md`.

## [0.14.1] — 2026-08-12

Tags `8cceb4a`.

### Fixed

- **A `local` backend selecting its environment through `TF_WORKSPACE` can plan at all.**
  `plan-cell`, `drift-cell` and `apply-cell` move a restored `terraform.tfstate.d` aside for
  `tofu init` and back afterwards.
- **`scripts/register-app` refuses a `--repo` owner beginning with `-`.**

### Added

- **Three documentation limits:** empty-state plans in `docs/aws.md`, retry versus re-plan in
  `docs/troubleshooting.md`, and the single-maintainer `CODEOWNERS` conflict in
  `docs/branch-protection.md`.

## [0.14.0] — 2026-08-12

Tags `f485a78`.

### Added

- **An apply that binds a GitHub Environment which does not exist is refused before any wave
  runs.** `actions/verify-environments` runs in `apply-env-level.yml`'s `snapshot` job, which
  now declares `actions: read`. An unreadable or truncated listing fails the run.

### Changed

- **BREAKING: `global.shipmate.explicit_envs` rejects an entry carrying a `-plan` or `-apply`
  suffix**, naming the bare name to write instead.
- **`shipmate doctor` fails loud when the environments listing is truncated.**

### Fixed

- **`shipmate doctor` reports what the environment names actually imply** in ambiguous
  naming: the missing half is reported again, and findings take their noun from the
  environment's role.
- **A mode/name mismatch fails loud only where the environment injects something the
  fingerprint reads.** `CONTRACT.md` §Env model states the condition; the `0.13.0` claim is
  corrected.
- **The static plan-side bindings in `docs/getting-started.md` and `docs/drift.md` are pinned
  by a guard.**

## [0.13.0] — 2026-08-12

Tags `181413c`.

### Changed

- **BREAKING: plan and apply bind `<env>-plan` and `<env>-apply`.** The plan side was the
  bare `<env>`; `plan.yml` and `drift.yml` bind `${{ matrix.environment }}-plan`.
  `matrix.environment`, check names, tags, `explicit_envs`, artifact names and comment
  grammar key on the bare name.

### Added

- **`SHIPMATE_SHARED_ENVS` — one environment for both paths, opt in per env.** A logical env
  named in it binds the bare `<env>` for plan and apply, giving up the reviewer gate and the
  OIDC subject split. `-plan` and `-apply` are reserved suffixes.
- **`shipmate doctor` infers the naming per env** — split, shared or ambiguous — and words
  its findings for it.

## [0.12.0] — 2026-08-11

Tags `e9eb27d`.

### Changed

- **BREAKING: pass the engine's secrets by name.** `secrets: inherit` is no longer a
  supported call shape. `summary.yml` takes `SHIPMATE_APP_PRIVATE_KEY`; `apply.yml`,
  `apply-all.yml` and `deploy.yml` also take `SHIPMATE_PLAN_PASSPHRASE`. The key stays a
  secret on the `shipmate-engine` environment.
- **The engine's own reusable calls into `apply-env-level.yml` pass named secrets too.**

### Added

- **An empty App key now names its cause.** Every job whose App-token mint is mandatory
  reports which of the three wiring mistakes to look at. `comment-ops` is excluded.

## [0.11.0] — 2026-08-11

Tags `4034746`.

### Added

- **`AWS_ROLE_ARN_<WORKLOAD>` selects the apply role per workload**, falling back to
  `AWS_ROLE_ARN`. `<WORKLOAD>` is the `workload/<name>` tag upper-cased with `-` mapped to
  `_`.
- **`detect` asserts that the injected environment survives `terramate run`.**
- **The apply DAG's shape is reported** by the dispatched and post-merge detects: stack
  count, `after` edge count, level count and largest level.
- **`docs/upgrading.md` gains a migrating-from-another-tool section.**

### Changed

- **`detect` fails when `terramate.config.run.env` rewrites `TF_VAR_env`, `TF_VAR_region` or
  `TF_WORKSPACE`.** A config `detect` cannot evaluate is a warning.
- **A workload-variable collision fails loud**, naming both tags.
- **The untagged-stack failure names every untagged stack and the count.**
- **The matrix-ceiling error points at remedies that exist.**
- **BREAKING for the bootstrap only: `scripts/register-app` takes `--name` and `--out` and
  creates no repository secret.** It writes the PEM to `--out` and captures the manifest code
  on a one-shot `127.0.0.1` listener.

### Fixed

- **`app/manifest.json` had no `redirect_url`,** so GitHub rejected the manifest POST.
- **The manifest's App name was one GitHub already reserves globally.** It is a placeholder.
- **`terramate experimental run-graph` needs `--label stack.dir`** to emit stack paths.
- **The `disable_safeguards` prohibition is narrowed** to `outdated-code` and `all`.
- **A named AWS `profile` in generated HCL is documented as a requirement** to be
  conditional on a variable defaulting to false.
- **`explicit_envs`' boundary is stated:** it constrains the bare pre-merge `shipmate apply`
  only.

## [0.10.0] — 2026-08-09

Tags `bbd9a74`.

### Changed

- **BREAKING: the plan path is one workflow.** `plan.yml` moves to `pull_request_target` and
  gains a `summary` job calling the engine's `summary.yml` with five inputs; `detect` and
  `plan` check out `github.event.pull_request.head.sha`. The consumer's `summary.yml` is
  deleted, and the `workflow_run` topology is not supported.
- **Plans run against the branch tip, not the merge commit.**
- **A summary-job failure reds the whole plan run.**
- **Two open pull requests sharing one head SHA both write the gate;** last write wins.
- **`shipmate doctor` has ten probes, not eleven.** `scripts/wiring` is deleted; the
  `pull_request_target` probe exempts `.github/workflows/plan.yml` by exact name.
- **The `plan-matrix.<N>` marker artifact is gone.** `detect` outputs the planned cell count
  as `planned-cells`; `actions/summary` loses its `artifact-count` input.

### Fixed

- **`CONTRACT.md` § Terramate safeguards described a protection that does not run:**
  `git-untracked` and `git-uncommitted` are never evaluated on a `--no-recursive` cell.
  `.terraform.lock.hcl` leaves the mandatory gitignore list. Documentation only.

## [0.9.0] — 2026-08-09

Tags `12bdceb`.

### Changed

- **The cells invoke `terramate run … -- tofu …` and name the commands themselves**, instead
  of `terramate script run <name>`. The engine no longer reads `script "plan"` /
  `script "apply"` blocks, so any command one added no longer runs.
- **The apply no longer passes `-lock=false`.**
- **`tofu init` runs outside the teed pipeline**, so its output no longer reaches
  `apply.txt`.

## [0.8.1] — 2026-08-08

Tags `bec15e4`.

### Fixed

- **`apply-complete` no longer strands apply checks when the run's jobs listing lags.** It
  re-reads the listing for up to two minutes and fails naming what it could not resolve.
- **Running out of retries no longer discards the completions the run earned.**
- **An empty listing, a failed fetch, and the zero-match floor are retried too.**
- **Cells left pending are named, whatever the reason.**

## [0.8.0] — 2026-08-08

Tags `5ee006b`.

### Removed — breaking

- `actions/apply-detect` no longer declares the per-wave outputs `wave0` … `wave7`. Use the
  aggregate `waves` object (`fromJSON(needs.detect.outputs.waves).waveN`). A read of the old
  outputs resolves to the empty string and fails at matrix expansion.
- `dev/repin_consumer.rewrite_consumer`, `dev/repin_consumer._survivors`,
  `dev/repin_internal.rewrite`, and `scripts/waves`' command-line entry point.

### Added

- **A guard pinning that `guard_max_waves` runs before the wave map is padded.**

## [0.7.2] — 2026-08-07

Tags `6973262`.

### Fixed

- **A plan that produced exactly one changed cell created no pending apply check.**
  `pending-checks` and `doctor` now glob `cell.json` recursively.

## [0.7.1] — 2026-08-07

Tags `2fdf60f`.

### Fixed

- **`apply.yml`, `apply-all.yml` and `summary.yml` no longer declare
  `SHIPMATE_APP_PRIVATE_KEY` a *required* `workflow_call` secret**, which made a dispatched
  `shipmate apply <env>` fail with the key on the `shipmate-engine` environment.

## [0.7.0] — 2026-08-07

Tags `6be3d34`.

### Added

- **The apply path can mint an AWS OIDC token.** Every wave job in `apply-env-level.yml`
  carries `id-token: write` and runs a credentials step gated on `vars.AWS_ROLE_ARN != ''`.
  BREAKING: a job calling `apply.yml`, `apply-all.yml` or `deploy.yml` must grant
  `id-token: write`.
- **`actions/apply-cell` and `actions/drift-cell` accept an empty `state-path`**, which skips
  artifact state for a remote backend.

### Changed

- `apply-env-level.yml` declares a top-level `permissions: {}` floor.
- `docs/hardening.md` controls 7–9 and checklist rows 7, 9, 18 and 19 describe the credential
  path that exists.

## [0.6.0] — 2026-08-06

Tags `4fbb572`.

### Added

- **`shipmate doctor` gained an eleventh probe: the secrets a *plan* environment holds**,
  counted and named, with a warning if `SHIPMATE_APP_PRIVATE_KEY` is among them. The shipmate
  App requests `environments: read`.
- **`docs/hardening.md` control 8 states the credential-free plan posture.**

## [0.5.0] — 2026-08-04

Tags `a5a823f`.

Carries the `0.4.0` changes, which were never tagged.

### Added

- **The consumer wiring the `shipmate / gate` status depends on is checked on every plan
  run.** `actions/build-matrix` fails `detect` on a confident break of the plan workflow's
  path, `name:`, `pull_request` trigger or `workflow_run` summary caller; on other events it
  warns.
- **`shipmate doctor` gained a tenth probe** reporting the same conditions.

### Fixed

- **`CONTRACT.md` described the name-side failure wrongly:** a renamed `name:` breaks the
  gate from the merge onward, not on the renaming pull request.
- **Doctor's `pull_request_target` probe missed the trigger in a workflow file written with
  a byte-order mark.**

## [0.4.0] — 2026-08-03

Never tagged; these changes shipped in `0.5.0`.

### Fixed

- **A `cell-summary.*` count of zero was read as "the plan matrix was empty".** `detect`
  publishes the planned cell count as the artifact name `plan-matrix.<N>`, and the gate holds
  unless exactly one such marker is readable.
- **A *partial* listing greened the gate just as quietly.** The gate holds unless the listed
  count equals the planned count; the counting step retries up to three times.

## [0.3.1] — 2026-08-03

Tags `1efbee8`.

### Fixed

- **The gate could be written green over plan evidence that was never read.** The
  unreadable-listing sentinel is non-numeric.
- **A held gate was clearable by the apply it existed to prevent.** `gate-refresh` refuses to
  overwrite a failing gate.
- **Two steps could lose the gate write entirely:** the supersede check now warns and
  proceeds, and the pull-request probe fails loudly when unreadable.
- **A cell with no queued apply check applied anyway.** The per-cell refusal is restored.
- **Drift could be lost silently.** The compose and upload steps are required, and Slack
  webhook failures fail the nightly run.

### Changed

- `docs/hardening.md` states the environment-secret release rule the same way as
  `docs/github-app.md`.
- The apply-cell credentials guard is a parsed assertion over the action YAML, covering
  `drift-cell` as well.

## [0.3.0] — 2026-08-03

Tags `e576103`.

### Changed

- **BREAKING: no `pull_request`-triggered job holds the App private key.** The summary runs
  in a trusted `workflow_run` workflow, consumer `.github/workflows/summary.yml`, with the key
  on the `shipmate-engine` environment. `comment-ops.yml`'s `ops` job and `drift.yml`'s new
  `issues` job declare `environment: shipmate-engine`.
- **`actions/summary` inputs are reshaped** — `run-conclusion` and `artifact-count` replace
  `plan-result` and `detect-result`, and `plan-run-url` is new. Breaking for a direct caller.

### Added

- **Fork pull requests are refused outright** by `actions/build-matrix`.
- `actions/apply-complete`, `actions/apply-snapshot` and `actions/drift-issues`, the trailing
  trusted jobs that complete apply checks and author drift issues.
- **Two `shipmate doctor` probes:** workflow files declaring `pull_request_target`, and
  whether the default branch's ruleset requires code-owner review.

### Fixed

- `docs/github-app.md` §6 deletes a repository secret of the same name beside the
  `shipmate-engine` environment secret.
- `docs/hardening.md` names two controls a leaked App key cannot satisfy: a `CODEOWNERS`
  review at the merge and the environment reviewer at the apply.

## [0.2.3] — 2026-07-31

Tags `07c2de1`.

### Changed

- `terramate-io/terramate-action` **v2.0.0 → v3.3.0**, which carries a template-injection
  patch. An empty version value now fails instead of installing latest.
- `actions/download-artifact` **v7.0.0 → v8.0.1** in `actions/apply-summary`.

## [0.2.2] — 2026-07-31

Tags `28d28f7`.

### Fixed

- **An approvers-team member could be refused their own apply** when `grep -q` under
  `pipefail` returned 141. The membership state is compared for equality.

## [0.2.1] — 2026-07-31

Tags `8beba59`.

### Fixed

- **The post-merge gate could green a failed deploy** through a `grep -q` / SIGPIPE verdict.
  The scan is in-process.
- Drift issues link the run that found the drift.

### Internal

- Dependabot scans `actions/*` for third-party pins.
- The detect queries, `dev/` pin helpers and test loaders are single-sourced.
- Test guards that execute an action's shell body probe for a working `bash` first.

## [0.2.0] — 2026-07-29

Tags `c77e2cd`.

### Changed — BREAKING

- **The apply check name flips field order**: `apply / <env> / <stack>` becomes
  `apply / <stack> / <env>`.
- Workflow and job display names read as shipmate (`shipmate · plan`, `shipmate / detect`,
  `shipmate / summary`). `CONTRACT.md` § Check names fixes the naming rule.

### Added

- A pull request that changed no stacks no longer gets a plan comment.

### Fixed

- App installation tokens are minted with `client-id`, and a `doctor` report survives losing
  its comment.
- `build-matrix` reserves a stack path of exactly `shipmate`.

### Internal

- The engine's own SHA pins are maintained by `dev/` tooling, `dev/pin_status.py` answers "is
  this commit safe to pin?", and two silent-failure paths in the pins guard are closed.

## [0.1.0] — 2026-07-27

Tags `aa4d8b7`. First tagged release — the baseline every section above is
relative to.

- Per stack × environment plan fan-out with a check each and a sticky PR plan comment.
- Wave-ordered applies over the Terramate `after` DAG with environment-level ordering.
- Exact-plan applies from the reviewed, encrypted plan artifact, with a stale-plan fail-safe.
- The pre-merge comment grammar: `shipmate apply [env]`, plus read-only `help` and `doctor`.
- The aggregate `shipmate / gate` commit status, authored by the shipmate GitHub App and
  pinned by `integration_id` in the consumer's ruleset.
- Post-merge deploy driven by the pending apply checks; per-run apply result comments.
- Nightly drift detection as auto-closing issues.

[0.3.1]: https://github.com/ship-iac/shipmate/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/ship-iac/shipmate/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/ship-iac/shipmate/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/ship-iac/shipmate/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/ship-iac/shipmate/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ship-iac/shipmate/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ship-iac/shipmate/releases/tag/v0.1.0
