# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Consumers pin this engine's actions and reusable workflows by **commit SHA**, not
by tag (see `CONTRACT.md`), so a release only reaches a repository when that
repository re-pins — and every engine reference must move in one change. Each
section below names the SHA the release tags.

## [Unreleased]

### Added

- **Each drift workflow file can pass engine `drift.yml` a `tags` query, so one file sweeps only the cells it selects (`docs/drift.md` §Scoping a sweep).**
- **`shipmate doctor` reports a notice when no workflow file calls the engine's `drift.yml`, and warns when `shipmate.yml` still does.**
- **The plan comment names the changed stacks that carry no `env/*` tag, and a pull request changing only such stacks gets a comment.**
- **`shipmate doctor` names the AWS accounts each environment's OIDC subject reaches.**

### Changed

- **Drift moves out of `shipmate.yml` into its own workflow file: delete the `drift` job, the `schedule` trigger and the `drift` verb option from `shipmate.yml`, and save the `shipmate drift` fence in `docs/getting-started.md` under any file name in `.github/workflows/`, one file per sweep, in the re-pin commit. `scripts/onboard` writes only `shipmate.yml`.**
- **`shipmate doctor` expects `shipmate.yml`'s `verb` options to be `[plan, apply, unlock]`.**
- **A drift Issue closed because its cell is no longer managed says the stack or environment left the repository, or the stack carries no `env/*` tag.**
- **A stack with no `env/*` tag is unmanaged: it is skipped with a notice that counts the unmanaged stacks and names up to ten, instead of failing the run (`CONTRACT.md` §Tag grammar).**
- **A deploy refuses when its merged pull request holds an open apply check on a stack that lost its `env/*` tag after it was planned, and names every other open check of that merge, which it does not apply either: open a new pull request that retags each stranded stack and changes every other named stack; its deploy applies all of them. Re-running the refused deploy refuses again (`docs/troubleshooting.md`).**
- **`scripts/onboard` no longer refuses a stack with no `env/*` tag, and provisions every environment the checkout's `.github/shipmate.toml` declares as well as every one a stack tags, and names a declared entry no stack tags on its `.github/shipmate.toml` checklist item.**
- **`scripts/onboard` no longer refuses a `shared = true` entry that no stack tags; it provisions the bare `<env>` like any other entry.**

### Fixed

- **`shipmate doctor`'s failed-mint reply and App-permission warning, and `docs/github-app.md`, name the step a release adding an App permission needs: add it in the registered App's settings, then accept the request.**
- **`scripts/onboard`'s lock-file checklist item names `tofu init -backend=false`, which works before a stack's modules are installed.**
- **`shipmate doctor` matches only the engine's own reusable workflows: a call to another repository's workflow of the same name no longer counts.**
- **`shipmate doctor` warns when the default branch's `pull_request` rule requires last-push approval at 0 approving reviews, which blocks a sole maintainer's merge, and `docs/branch-protection.md` names last-push approval as the third setting a sole maintainer turns off.**
- **`shipmate doctor`'s report no longer says the commit's runs had not finished because a cell's apply check is waiting to be applied.**
- **`shipmate doctor`'s report shows the engine placeholders `<env>`, `<stack>`, `<caller job>` and `<callee job>` with their angle brackets instead of as `&lt;env&gt;`.**
- **`scripts/onboard` flags a committed lock that lacks the registry's `zh:` hashes, as a `tofu init` through a plugin cache writes it, and its remedy and `docs/getting-started.md` add `tofu providers lock -platform=linux_amd64` after `tofu init -backend=false`.**
- **`scripts/onboard`'s App installation item links the organization's App installations page instead of a URL with an `<org>` placeholder.**
- **A plan or drift detect names every stack carrying two `workload/*` tags, and any environment-table or `workloads` list refusal after them, in one run instead of stopping at the first stack.**

## [0.42.0] — 2026-10-04

Tags `1120418`.

### Added

- **`shipmate doctor` names each gated environment at 0 required approvals: a note while code-owner review is on, a warning while it is off.**
- **The apply run's log carries a notice annotation naming each gated environment applied with no approving review required.**
- **A cell whose `tofu init` fails after restoring a provider cache entry names that entry's key in an error annotation.**
- **`scripts/onboard`'s closing checklist names the stacks without a git-tracked `.terraform.lock.hcl`.**
- **`shipmate doctor` lists the role each environment resolves, per path and workload.**
- **A listed workload that no stack tags is warned about on the paths that scan the whole tree.**
- **`apply.yml` called without `environment` runs a bare apply of every environment the review decision allows, in `needs` order.**
- **The drift run's log names how many drift cell summaries it loaded.**
- **`actions/setup` checks the Terramate and OpenTofu downloads against sha256 digests pinned in `VERSIONS`, and refuses a mismatch or a runner other than Linux `x86_64` or `arm64`.**
- **The drift run closes an open `drift: <env> / <stack>` Issue whose stack or environment left the drift sweep.**

### Changed

- **Credentials live only in `[identities.<name>]`: rewrite each environment's `aws` block as an `[identities.<name>]` table (`CONTRACT.md` §Environment table) in the re-pin commit.**
- **A stack carrying two `workload/*` tags is refused at detect.**
- **A workload tag outside the environment's `workloads` list is refused at detect, naming every such cell.**
- **A draft pull request's run writes `shipmate / gate` pending, naming the draft and the two ways to plan it.**
- **The bare-apply comment lists a held `explicit` environment once, with both reasons and the `shipmate apply <env>` command.**
- **An invalid `.github/shipmate.toml` is refused with every structural error it holds, one annotation each.**
- **`scripts/onboard` takes no `--team`.**
- **`scripts/onboard --key` is needed only while `shipmate-engine` holds no App private key.**
- **`scripts/onboard`'s closing checklist marks each item `ok`, `todo` or `cannot check` from what the run read.**
- **The provider cache serves only stacks with a committed `.terraform.lock.hcl`, keyed on the lock's provider addresses and versions, and drift and apply cells save it; plan cells never do.**
- **Plan cells render the text and JSON plans concurrently.**
- **The control jobs that run no `tofu` run on `ubuntu-slim`.**
- **The plan summary's and `shipmate doctor`'s App tokens request `environments: read`; the plan-environment secret probe has no token of its own and no not-checked warning.**
- **Comments by bots or without `shipmate` start no engine `ops` job.**
- **The workflow file's `comment-ops` job has no concurrency group, so a later comment no longer cancels a command still waiting to run; delete the `concurrency:` block from that job.**
- **`shipmate apply`, `shipmate unlock`, `shipmate plan` and `shipmate doctor` require write or admin permission on the repository; the `[gate]` table and the App's `members` permission are removed; delete `[gate]` in the re-pin commit.**
- **Plan, apply and doctor comments share one shape: a header, a verdict line linking the commit and the run, and one line per cell or finding. Refusals, failures, notices and help share the header and end with a run link. The help hint shows only where action is needed: on a refusal or a failure, and under an apply or doctor verdict that is not 🟢; a plan comment never shows it. The apply comment's gate, ungated and no-review lines moved to the gate check and the run log.**
- **A stack at path `apply` or `shipmate` plans; `build-matrix` no longer refuses either.**
- **`actions/setup` downloads Terramate with curl instead of `terramate-io/terramate-action`, which consumers may drop from their allowed-actions list; a self-hosted runner's own curl config applies.**
- **`apply-all.yml` is gone: in the re-pin commit, replace the workflow file's `targeted` and `all` jobs with one `apply` job calling `apply.yml` with `environment`, `ref` and `pr_number` (`docs/getting-started.md`).**
- **Every path reads `.github/shipmate.toml` through the contents API from the default branch; the could-not-be-read refusal no longer names a ref, and a failed `gh`, `git` or `terramate` call in CI annotates with its stderr below the error line.**
- **`shipmate doctor` checks `shipmate.yml`'s job name, dispatch wiring and routing as one probe, so one unreadable file degrades all three together, and a file that calls no engine plan workflow is reported once.**
- **`shipmate doctor`'s `needs` and `explicit` notices end a cut list with ` … and N more` and never cut an item inside.**
- **The `apply` and `unlock` guard refusal names the check it makes: the dispatching actor is not a `[bot]`.**
- **`shipmate doctor` reads `.github/workflows/shipmate.yml` by path, and a missing one draws the unreadable notice.**

### Removed

- **`.github/shipmate.toml` has no `schema_version` key; the engine refuses it as an unknown setting, so delete it in the re-pin commit.**
- **The drift workflow and `actions/build-matrix` take no `tags` input; every sweep covers every cell. In the re-pin commit, delete `tags:` from the drift job's `with:`, because an undeclared input fails the whole workflow file at load.**
- **`scripts/onboard` takes no `--vars-at-org`; it always writes the repository `SHIPMATE_APP_ID`.**
- **The deploy and drift workflows take no `SHIPMATE_SLACK_WEBHOOK` secret and post nothing to Slack; GitHub's Slack app (`/github subscribe <owner>/<repo> issues workflows`) posts workflow runs and drift Issue opens and closes, and does not re-post an Issue that stays open. In the re-pin commit, delete the secret's mapping from the workflow file's `deploy` and `drift` jobs, because a secret the callee does not declare fails the whole workflow file at load; the secret on `shipmate-engine` can be deleted. `scripts/onboard` no longer lists the secret, and no job refuses a variable of that name: one left set is skipped silently while its URL stays readable, so delete it and rotate the webhook.**
- **`shipmate doctor` no longer prunes older copies of a replanned cell before reading its environments, and drops the two warnings that pruning raised; the environments it reports are unchanged, except that a failed download no longer drops one.**
- **`cell.json` in `cell-summary.*`, `apply-summary.*` and `drift-summary.*` has no `stack_path` (plan, apply) or `stack_name` (drift) key; read `stack`.**

### Fixed

- **`shipmate plan` and `shipmate unlock` runs cannot save an Actions-cache entry: `plan.yml` and `unlock.yml` declare `cache-mode: read`.**
- **The Terramate download retries transient errors three times, and a failed install names its cause in one annotation: the URL and status for a failed download, or the missing curl.**
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

Tags `1923428`. A consumer's GitHub variables and a `SHIPMATE_SECRETS` envelope reach every cell.

## [0.28.0] — 2026-09-13

Tags `b0d9a41`. A cell's identity, roles and regions come from a `globals "shipmate"` table read from the default branch, not from GitHub Environment variables.

## [0.27.1] — 2026-09-12

Tags `55bf06b`. The helper scripts share one module loader, and the wave and environment-level limits are enforced inside the functions that emit them.

## [0.27.0] — 2026-09-07

Tags `457ea2b`. One `.github/workflows/shipmate.yml` per consumer replaces six workflow files, and `shipmate doctor` gains a routing probe.

## [0.26.0] — 2026-09-06

Tags `c622ff7`. `scripts/onboard` reconciles a consumer repository's environments, App key, variables, ruleset and workflow shims in one command.

## [0.25.0] — 2026-09-06

Tags `9e12610`. The plan, drift and comment-ops job graphs move into the engine, and plan and drift check names gain a `shipmate / ` prefix.

## [0.24.0] — 2026-09-05

Tags `9916735`. The reviewed plan text is bound to the plan that executes: `actions/apply-cell` refuses a stored plan whose rendered text differs.

## [0.23.0] — 2026-09-05

Tags `d780e60`. A dispatched verb that never starts says so on the pull request, and `shipmate plan` naming a fork's pull request is refused.

## [0.22.2] — 2026-09-05

Tags `83d69cb`. The embedded Python in `actions/` moves to `scripts/`, where `ruff` lints it.

## [0.22.1] — 2026-09-05

Tags `71e1aa4`. Comments and docstrings are cut to the repository cap, and docs pages read one claim per sentence.

## [0.22.0] — 2026-08-31

Tags `cda2a93`. `actions/build-matrix` takes a `tags` query, so a drift sweep can cover a slice of the repository.

## [0.21.0] — 2026-08-29

Tags `11b31b5`. Each verb has its own consumer workflow file, and `shipmate unlock` runs in its own reusable workflow, off the apply path.

## [0.20.0] — 2026-08-27

Tags `dbcc4c5`. `shipmate plan` is a verb, and `actions/pr-facts` is the one producer of every pull-request fact the plan path decides on.

## [0.19.0] — 2026-08-25

Tags `270c03b`. Each apply check carries its plan run id, so a partially failed plan run's healthy cells are individually applicable.

## [0.18.0] — 2026-08-23

Tags `468429a`. The fork refusal keys on the head repository the wrapper states and refuses by default.

## [0.17.0] — 2026-08-23

Tags `5f4a022`. A reviewed plan carries the commit it was produced from, and an apply refuses a plan produced from a different tree.

## [0.16.2] — 2026-08-22

Tags `69da4e4`. `scripts/lock-info` strips OpenTofu's colour escape sequences, so `shipmate unlock` recognises the lock it reads.

## [0.16.1] — 2026-08-22

Tags `da5a5ff`. Fixes `v0.16.0`'s `actions/apply-detect` manifest, which did not load and broke the apply and deploy paths.

## [0.16.0] — 2026-08-22

Tags `4fda446`. A stranded state lock is visible on the pull request and releasable with `shipmate unlock <env>`.

## [0.15.0] — 2026-08-17

Tags `19de76c`. `SHIPMATE_UNGATED_ENVS` names the environments `shipmate apply` may apply without an approving review.

## [0.14.2] — 2026-08-13

Tags `741118f`. `shipmate doctor` reports ambiguous environment naming even when the declared environment set is unknown.

## [0.14.1] — 2026-08-12

Tags `8cceb4a`. A `local` backend selecting its environment through `TF_WORKSPACE` can plan.

## [0.14.0] — 2026-08-12

Tags `f485a78`. An apply that binds a GitHub Environment which does not exist is refused before any wave runs.

## [0.13.0] — 2026-08-12

Tags `181413c`. Plan and apply bind `<env>-plan` and `<env>-apply`, and `SHIPMATE_SHARED_ENVS` opts an environment into one bare `<env>` for both.

## [0.12.0] — 2026-08-11

Tags `e9eb27d`. The engine's secrets are passed by name instead of `secrets: inherit`, and an empty App key names its cause.

## [0.11.0] — 2026-08-11

Tags `4034746`. `AWS_ROLE_ARN_<WORKLOAD>` selects the apply role per workload, and `detect` fails when `terramate.config.run.env` rewrites an identity variable.

## [0.10.0] — 2026-08-09

Tags `bbd9a74`. The plan path is one `pull_request_target` workflow with a `summary` job, and plans run against the branch tip.

## [0.9.0] — 2026-08-09

Tags `12bdceb`. The cells invoke `terramate run … -- tofu …` and name the commands themselves instead of running `script` blocks.

## [0.8.1] — 2026-08-08

Tags `bec15e4`. `apply-complete` re-reads a lagging jobs listing for up to two minutes instead of stranding apply checks.

## [0.8.0] — 2026-08-08

Tags `5ee006b`. `actions/apply-detect` drops the per-wave outputs `wave0` … `wave7` in favour of the aggregate `waves` object.

## [0.7.2] — 2026-08-07

Tags `6973262`. A plan with exactly one changed cell creates its pending apply check.

## [0.7.1] — 2026-08-07

Tags `2fdf60f`. `SHIPMATE_APP_PRIVATE_KEY` is not a required `workflow_call` secret, so a dispatched `shipmate apply <env>` runs with the key on `shipmate-engine`.

## [0.7.0] — 2026-08-07

Tags `6be3d34`. The apply path can mint an AWS OIDC token, and the apply and drift cells accept an empty `state-path` for a remote backend.

## [0.6.0] — 2026-08-06

Tags `4fbb572`. `shipmate doctor` reports the secrets a plan environment holds, warning if `SHIPMATE_APP_PRIVATE_KEY` is among them.

## [0.5.0] — 2026-08-04

Tags `a5a823f`. The consumer wiring the `shipmate / gate` status depends on is checked on every plan run and by a `shipmate doctor` probe.

## [0.4.0] — 2026-08-03

Never tagged; these changes shipped in `0.5.0`.

## [0.3.1] — 2026-08-03

Tags `1efbee8`. The gate cannot be written green over unread plan evidence, and `gate-refresh` refuses to overwrite a failing gate.

## [0.3.0] — 2026-08-03

Tags `e576103`. No `pull_request`-triggered job holds the App private key, and fork pull requests are refused outright.

## [0.2.3] — 2026-07-31

Tags `07c2de1`. `terramate-io/terramate-action` moves to v3.3.0, which carries a template-injection patch.

## [0.2.2] — 2026-07-31

Tags `28d28f7`. The approvers-team membership state is compared for equality, so a `grep -q` exit 141 under `pipefail` cannot refuse a member's apply.

## [0.2.1] — 2026-07-31

Tags `8beba59`. The post-merge gate's scan is in-process, so a `grep -q` / SIGPIPE verdict cannot green a failed deploy.

## [0.2.0] — 2026-07-29

Tags `c77e2cd`. The apply check name becomes `apply / <stack> / <env>`, and workflow and job display names read as shipmate.

## [0.1.0] — 2026-07-27

Tags `aa4d8b7`. First tagged release: per-cell plan checks, wave-ordered exact-plan applies, comment-ops, the `shipmate / gate` status, post-merge deploy and nightly drift issues.
