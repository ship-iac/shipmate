# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Consumers pin this engine's actions and reusable workflows by **commit SHA**, not
by tag (see `CONTRACT.md`), so a release only reaches a repository when that
repository re-pins — and every engine reference must move in one change. Each
section below names the SHA the release tags.

## Unreleased

### Added

- **A `shipmate plan`, `shipmate apply` or `shipmate unlock` whose dispatch succeeded posts a notice reply on the pull request: `started [the <verb> run](<url>)` when the dispatch answer carries a usable run id, otherwise one naming the Actions tab. A reply that cannot be posted is a warning.**
- **`max_parallel` in `.github/shipmate-config.yml` caps every cell matrix — each plan, drift sweep, apply wave and unlock — at that many jobs at once, 10 when absent. It takes a whole number from 1 to 256 and no variable reference; anything else refuses at detect. `shipmate doctor` reads it back.**

### Changed

- **Every plan, apply wave, drift sweep and unlock now runs at most 10 cell jobs at once, the `max_parallel` default. A matrix with more cells than the cap runs in batches, and each environment's required reviewers and wait timer apply again to every batch holding one of its cells; a repository using them raises `max_parallel` to at least the number of cells in its largest wave.**
- **The apply-path detect notice on the DAG shape reports how many stacks the widest wave level holds (`the widest level holds N stacks`), instead of how many would apply concurrently. It counts stacks, not cells, and at most `max_parallel` cells of a wave run at once.**
- **The gate hold a plan run writes while it publishes its apply checks names the run and says to re-plan only once it has ended: `plan run <id> holds this gate while it publishes; re-plan (comment shipmate plan or push) if that run has ended`.**
- **`apply-complete` re-reads a lagging jobs listing after 2, 3 and 5 seconds and then every 10 seconds, instead of every 10 seconds from the first retry; the total sleep stays 110 seconds.**

### Fixed

- **`actions/dispatch` and `actions/apply-complete` send their API request bodies on stdin instead of writing them to a `body.json` in the job workspace, and the dispatch log no longer prints an empty line for a missing answer or error. A dispatch that fails to build its request body or create the temporary file for gh's error output posts the `🔴 failed:` reply too.**
- **A failed write of the sticky plan comment or the doctor report annotates gh's whole error, not only its first line. A plan comment that cannot be listed now fails the `summary` job too, instead of warning while the gate is written over the previous head's plan comment. Either failure stops the job before its gate write: a publish hold the run wrote stays until the pull request is re-planned, and a run that planned no cell leaves the head's gate as it was.**
- **The plan, both apply forms and the merge deploy refuse a Terramate stack ordering cycle, a stack ordered after itself included, when they read the run-graph and before any other ordering check, naming the stacks on it. The plan refuses even with no cell to plan, instead of failing each planned cell on the loop, and the deploy's refusal also lists the merge's pending cells. Every plan but a drift sweep now runs `terramate experimental run-graph` and refuses when it fails, a plan with no cell and one in a repository with no `needs` included.**
- **The merge deploy's stranded-check and `needs` inversion refusals say the fix pull request's cells are applied before merge under a required gate (`shipmate apply`, and `shipmate apply <env>` for an `explicit` environment), or by its deploy otherwise, instead of by its deploy alone.**
- **A plan run whose plan job did not succeed (a skipped plan job with no cell planned is not held), whose planned cell count was not reported, or whose cell summaries do not match that count publishes no apply check, so a pre-merge `shipmate apply` cannot apply its cells. Apply checks an earlier plan run of the same head published stay.**
- **A detect refusal's annotation carries every line of its message, and a literal `%0A` or `%25` in a stack path shows verbatim, as it now does in detect's warnings and notices, the drift `tags` query included. The `needs` inversion refusal's first line, the one the gate description shows, leads with its remedy, so the gate's 140-character cut drops stack names rather than the fix: `needs order inverted, fix default-branch needs or tags: <stack> / <env> before <stack> / <env>, which it runs after`.**

## [0.45.0] — 2026-10-13

Tags `45ed9ae`.

### Added

- **Applies order cells across environments by `after`. A cell whose upstream cell in another environment is pending and not applying in the run waits: its apply check stays pending, the apply comment names it, its blocker and the blocker's remedy, and the rest of the run applies. A bare `shipmate apply` also skips the `needs` successors of an environment with a waiting cell.**
- **The plan, a bare `shipmate apply` and the merge deploy refuse a `needs` order that puts a stack's upstream cell on a later env-level than the stack's own cell, naming each inverted pair and the `needs` chain.**

### Changed

- **The apply comment's skipped line reads `ordered after an environment not fully applying this run`.**
- **Every engine job binds `shipmate-engine` with `deployment: false`, so plan, apply, deploy, drift and comment runs create no deployment on it. GitHub fails such a job while the environment has a custom deployment protection rule; `scripts/onboard` reports an enabled one with the command that removes it.**
- **Detect and `scripts/onboard` read every stack's tags with one `terramate debug show metadata` call instead of one call per stack. A stack `terramate list` names that the metadata prints no tags for refuses, naming it, and so does a metadata line that does not parse.**
- **The plan and deploy detect jobs check out the full history without file contents (`filter: blob:none`); git fetches a file's contents when a diff or read needs them.**
- **The plan workflow's `facts` job runs on `ubuntu-slim` whatever `runs_on` the caller passes.**

### Fixed

- **Gate writers on one head commit run one at a time: the plan, apply and deploy runs' `summary` jobs share a `shipmate-gate-<head sha>` concurrency group, so a refresh and a re-plan can no longer interleave a read and a write. A plan summary cancelled while pending holds the gate until a fresh plan.**
- **A plan summary greens its own `pending` gate when it published no queued apply check and every apply check on the head is already done, so an apply that finished before the summary wrote no longer leaves the gate pending. A hold found by that refresh is a warning on the plan run, not an error.**
- **A plan summary publishes no apply check its own run already published and refuses before writing anything when a newer plan run of the head holds the gate or published checks there, so a re-run never resets an applied cell to pending or overwrites a newer run's gate. It holds the gate when it cannot read the gate statuses, list the apply checks or build the ones to publish, even when no cell summary arrived, and its final write refuses over a newer run's hold.**
- **An apply run refused at its review binding posts no apply comment and writes no gate.**
- **`shipmate doctor`'s line for a valid `.github/shipmate-config.yml` shows `scripts/onboard --app-id <id> --dry-run` with its placeholder intact instead of `&lt;id&gt;`.**
- **The reply to a malformed `shipmate` comment names the token it refused, in a code span, when the token is non-empty, at most 40 characters and holds no backtick, and describes it otherwise; an unknown verb is quoted under the same rule.**
- **The plan comment names each stack / environment pair a pull request drops by removing one of a stack's two or more `env/*` tags; a stack that lost its last tag stays on the unmanaged line. An `@` in a stack or environment name anywhere in the plan, apply or doctor comment outside a code span is followed by an empty HTML comment, so a stack path cannot mention a user or team.**
- **`shipmate doctor` warns when a `pull_request` rule requires code-owner review and the default branch's `CODEOWNERS` owns no path (no file, or no line naming a pattern and an owner), since that review then holds no pull request; `scripts/onboard` reports it as a `todo`.**
- **An apply run whose gate refresh fails, on a hold found once every apply check is done or on an API error, still posts its apply comment with the run's results; the run stays red.**
- **A root stack's run-graph entry now matches its tags and checks, so its cells order, deploy and apply: the run-graph keyed the root stack differently from `terramate list` and the tag metadata, so ordering its cell into a wave refused "stack '.' is not a node in the run-graph".**
- **A refused deploy writes the merged head's gate `failure` ("deploy stopped at detect (failure), nothing applied; see the run") instead of keeping the pre-merge gate and failing its summary on a missing head SHA: deploy detect names the head before any refusal past head validation.**

## [0.44.3] — 2026-10-12

Tags `50a2d44`.

### Security

- **A local backend's cached state was plaintext in the default branch's Actions cache, readable by any pull-request workflow, a fork's included, and plans uploaded unencrypted when `SHIPMATE_PLAN_PASSPHRASE` was unset. Both are now encrypted, and every plan, apply and local-state restore or save refuses without the secret.** Set `SHIPMATE_PLAN_PASSPHRASE` as a repository or organization secret, and map it in each drift workflow file's `secrets:` (a local-backend drift sweep refuses at `Initialize the stack` (its `Restore state` step) without it). Then delete every `state/` cache entry: `gh cache list --repo <owner>/<repo> --key state/ --limit 1000 --json id --jq '.[].id' | xargs -n1 gh cache delete --repo <owner>/<repo>`. Deleting discards that state: OpenTofu no longer tracks the resources it recorded. A sample stack's null and random resources re-create; a real resource can collide with or be duplicated by its re-creation.
- **The apply workflow refuses a dispatch unless its pull request is open, not a draft and from this repository, and its head commit is the dispatched `ref`: the `pr_number` and `ref` inputs were bound to nothing, so a writer could apply an unreviewed head under another pull request's approval.**
- **Each apply check records the pull request its plan was made on, and an apply refuses a cell whose check records another pull request or none; re-plan on the pull request to clear it.** Plans made before the upgrade carry no pull request number and no encryption, and a deleted `state/` entry makes a local-backend plan stale: re-plan every open pull request after re-pinning, and apply or merge a pull request with pending cells before re-pinning.
- **`shipmate apply` and `shipmate unlock` refuse a pull request whose head is in a fork or a deleted repository.**
- **An apply, unlock or post-merge deploy refuses, before it starts, to bind a GitHub Environment with no deployment branch policy.** Run `shipmate doctor` before re-pinning; `scripts/onboard` sets the policy.
- **`shipmate doctor` warns on an apply or shared GitHub Environment with no deployment branch policy; run `scripts/onboard` to scope it to the default branch.**

## [0.44.2] — 2026-10-11

Tags `230b988`.

### Added

- **A plan run warns, naming each stack and environment, when a pull request removes one `env/*` tag from a stack that keeps another.**

### Fixed

- **An environment named `shipmate-engine` is refused as a table entry, a `needs` element, a stack's `env/*` tag and a dispatched environment: a `shared: true` entry of that name bound the engine's own environment, which holds the App private key.**

## [0.44.1] — 2026-10-11

Tags `39e5d0e`.

### Changed

- **The sole-maintainer posture is two values, `required_approving_review_count: 0` and `require_last_push_approval` off; code-owner review stays on instead of off or narrowed. At count 0 GitHub required no code-owner review from a pull request author who individually owns the changed files and held a non-owner's, so a `CODEOWNERS` entry for `/.github/shipmate-config.yml` and `/.github/CODEOWNERS`, added on its own pull request, holds a pull request opened with a leaked App key. A pull request changing only unowned files, and a commit pushed onto the owner's pull request, stay unreviewed. `CONTRACT.md` now holds a gated environment only on a non-owner's pull request changing owned files (`docs/branch-protection.md` §Reproducible ruleset).**

### Fixed

- **A plan summary that fails after publishing some apply checks leaves the gate held instead of greenable by an apply of the published cells.**
- **The hold for a cell-summary shortfall says to re-plan (comment `shipmate plan` or push) instead of re-running the summary job, which republished pending checks over cells applied since.**
- **An apply refused over a held gate names where the cause is, the gate's status description, and that only a new plan run clears the hold.**
- **`scripts/onboard`'s CODEOWNERS item asks for an entry covering the IaC, the workflows, `/.github/shipmate-config.yml` and `/.github/CODEOWNERS`, the plan-summary engine-mismatch refusals say to re-pin every engine reference to one commit and re-plan, and the missing-artifact and plaintext-plan apply refusals say to comment `shipmate plan` or push, or after the merge open a new pull request touching the stack, instead of re-running the plan jobs.**

## [0.44.0] — 2026-10-11

Tags `038dcf7`.

### Changed — BREAKING

- **The engine config is `.github/shipmate-config.yml`, written as YAML. Every value is read as written, and a duplicate key, an anchor, an alias or a tag refuses (`CONTRACT.md` §Keys). An empty `region`, and a variable reference on `shared`, `explicit` or `gated`, refuse too.**
- **Runners need Python 3.12 or later and PyYAML ≥ 6. The engine installs `python3-yaml` where it is missing and `sudo` needs no password (`CONTRACT.md` §Runner prerequisites).**
- **The comment grammar is `shipmate <verb> [env]`. A token after the env, or an env holding anything but lowercase letters, digits, `-` and `_`, gets the malformed error, and `shipmate destroy` gets the unknown-verb error.**
- **Runners need curl 7.71 or later (`CONTRACT.md` §Runner prerequisites). Setup always passes `--retry-all-errors`, and an older curl fails the Terramate install with a message naming the floor.**

### Added

- **A pull request that removes a stack's last `env/*` tag gets a warning on its plan run naming the stack.**

### Changed

- **`shipmate doctor`'s `shipmate.yml` drift-call warning says what such a job does: it runs on that file's triggers.**
- **Per-cell jobs and the apply, drift and unlock detect jobs check out only the commit they run, so a stack that reads git history in a plan, apply, drift or unlock cell sees one commit.**
- **An empty run-graph with pending applies refuses naming the first stack that is not a run-graph node, instead of saying the run-graph produced no levels.**
- **build-matrix's `all-stacks` and `no-pull-request` inputs are one `sweep` input, read only when exactly `true`.**
- **The apply review re-read displays as `review` instead of `review / decision`.**
- **`scripts/onboard` reports a plan or shared environment's protection shape in `shipmate doctor`'s words, one `differs` line per finding, and its adoption checklist item ends with the re-run step in place of a separate gate ruleset item.**

### Fixed

- **A deploy or bare `shipmate apply` prints the DAG-shape notice before a refusal for too many dependency levels; the bare apply printed none.**
- **An `env/<name>` tag whose name the environment table could not hold (a `.`, a `/`, a `-plan`/`-apply` suffix) refuses the run; before, under `layout: folder` or `workspace`, a dotted name could make two cells share one plan artifact.**
- **`shipmate doctor` parses workflow files as YAML: an `if: >-` block scalar, a block-style `options:` list and a wholly inline `on:` mapping are read as GitHub reads them, and a file that does not parse gets one `workflow parse` notice.**
- **A deploy refuses a head SHA that is not 40 lowercase hex characters, whether it came from the merged pull request or from the pushed commit, before the deploy reads that commit's checks or artifacts.**
- **A deploy refused for an apply check on a stack that lost its `env/*` tag names every unapplied cell of the merge, and offers a remedy for a deliberate untag (`docs/troubleshooting.md` §A deploy refused an apply check on a stack that lost its env tag).**
- **`shipmate doctor`'s missing-environment finding says that a missing apply environment refuses the apply before any wave.**

## [0.43.0] — 2026-10-08

Tags `ce6faac`.

### Added

- **Each drift workflow file can pass engine `drift.yml` a `tags` query, so one file sweeps only the cells it selects (`docs/drift.md` §Scoping a sweep).**
- **`shipmate doctor` reports a notice when no workflow file calls the engine's `drift.yml`, and warns when `shipmate.yml` still does.**
- **The plan comment names the changed stacks that carry no `env/*` tag, and a pull request changing only such stacks gets a comment.**
- **`shipmate doctor` names the AWS accounts each environment's OIDC subject reaches.**
- **`scripts/onboard`'s checklist reports the working tree's stacks with no `env/*` tag, the stack-tag refusals a plan would raise, workload tags against the table, and the cells no drift file sweeps, with drift queries or clauses matching no cell and sweeps above 256 cells (`docs/troubleshooting.md` §What `scripts/onboard` reports). Its `.github/shipmate-config.yml` item is `todo` when a `needs` entry names an environment no stack tags. None changes the exit code.**

### Changed

- **Drift moves out of `shipmate.yml` into its own workflow file: delete the `drift` job, the `schedule` trigger and the `drift` verb option from `shipmate.yml`, and save the `shipmate drift` fence in `docs/getting-started.md` under any file name in `.github/workflows/`, one file per sweep, in the re-pin commit. `scripts/onboard` writes only `shipmate.yml`.**
- **`shipmate doctor` expects `shipmate.yml`'s `verb` options to be `[plan, apply, unlock]`.**
- **A drift Issue closed because its cell is no longer managed says the stack or environment left the repository, or the stack carries no `env/*` tag.**
- **A stack with no `env/*` tag is unmanaged: it is skipped with a notice that counts the unmanaged stacks and names up to ten, instead of failing the run (`CONTRACT.md` §Tag grammar).**
- **A deploy refuses when its merged pull request holds an open apply check on a stack that lost its `env/*` tag after it was planned, and names every other open check of that merge, which it does not apply either: open a new pull request that retags each stranded stack and changes every other named stack; its deploy applies all of them. Re-running the refused deploy refuses again (`docs/troubleshooting.md`).**
- **`scripts/onboard` no longer refuses a stack with no `env/*` tag, and provisions every environment the checkout's `.github/shipmate-config.yml` declares as well as every one a stack tags, and names a declared entry no stack tags on its `.github/shipmate-config.yml` checklist item.**
- **`scripts/onboard` no longer refuses a `shared: true` entry that no stack tags; it provisions the bare `<env>` like any other entry.**
- **`shipmate doctor`'s and `scripts/onboard`'s shared-environment protection messages say those rules stall every drift sweep covering the environment, and `build-matrix`'s head-repository, head-commit and `tags` refusals name a drift sweep as the workflow with no pull request.**

### Fixed

- **`shipmate doctor`'s failed-mint reply and App-permission warning, and `docs/github-app.md`, name the step a release adding an App permission needs: add it in the registered App's settings, then accept the request.**
- **`scripts/onboard`'s lock-file checklist item names `tofu init -backend=false`, which works before a stack's modules are installed.**
- **`shipmate doctor` matches only the engine's own reusable workflows: a call to another repository's workflow of the same name no longer counts.**
- **`shipmate doctor` warns when the default branch's `pull_request` rule requires last-push approval at 0 approving reviews, which blocks a sole maintainer's merge, and `docs/branch-protection.md` names last-push approval as the third setting a sole maintainer turns off.**
- **`shipmate doctor`'s report no longer says the commit's runs had not finished because a cell's apply check is waiting to be applied.**
- **`shipmate doctor`'s report shows the engine placeholders `<env>`, `<stack>`, `<caller job>` and `<callee job>` with their angle brackets instead of as `&lt;env&gt;`.**
- **`scripts/onboard` flags a committed lock that lacks the registry's `zh:` hashes, as a `tofu init` through a plugin cache writes it, and its remedy and `docs/getting-started.md` add `tofu providers lock -platform=linux_amd64` after `tofu init -backend=false`.**
- **`scripts/onboard`'s App installation item links the organization's App installations page instead of a URL with an `<org>` placeholder.**
- **A plan or drift detect names every stack carrying two `workload/*` tags, and any environment-table or `workloads` list refusal after them, in one run instead of stopping at the first stack, while a stack-path collision or the matrix cell cap still refuses on its own.**
- **A `shipmate / gate` failed by detect names the first `::error::` line in its description, as `detect failed: <that line>`, instead of the generic "change detection did not succeed" text.**
- **The environment table refuses a `needs` chain deeper than the four env levels a deploy applies, naming the chain, instead of validating and failing at deploy.**

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

- **Credentials live only in `identities.<name>`: rewrite each environment's `aws` block as an `identities.<name>` entry (`CONTRACT.md` §Environment table) in the re-pin commit.**
- **A stack carrying two `workload/*` tags is refused at detect.**
- **A workload tag outside the environment's `workloads` list is refused at detect, naming every such cell.**
- **A draft pull request's run writes `shipmate / gate` pending, naming the draft and the two ways to plan it.**
- **The bare-apply comment lists a held `explicit` environment once, with both reasons and the `shipmate apply <env>` command.**
- **An invalid `.github/shipmate-config.yml` is refused with every structural error it holds, one annotation each.**
- **`scripts/onboard` takes no `--team`.**
- **`scripts/onboard --key` is needed only while `shipmate-engine` holds no App private key.**
- **`scripts/onboard`'s closing checklist marks each item `ok`, `todo` or `cannot check` from what the run read.**
- **The provider cache serves only stacks with a committed `.terraform.lock.hcl`, keyed on the lock's provider addresses and versions, and drift and apply cells save it; plan cells never do.**
- **Plan cells render the text and JSON plans concurrently.**
- **The control jobs that run no `tofu` run on `ubuntu-slim`.**
- **The plan summary's and `shipmate doctor`'s App tokens request `environments: read`; the plan-environment secret probe has no token of its own and no not-checked warning.**
- **Comments by bots or without `shipmate` start no engine `ops` job.**
- **The workflow file's `comment-ops` job has no concurrency group, so a later comment no longer cancels a command still waiting to run; delete the `concurrency:` block from that job.**
- **`shipmate apply`, `shipmate unlock`, `shipmate plan` and `shipmate doctor` require write or admin permission on the repository; the `gate` key and the App's `members` permission are removed; delete `gate` in the re-pin commit.**
- **Plan, apply and doctor comments share one shape: a header, a verdict line linking the commit and the run, and one line per cell or finding. Refusals, failures, notices and help share the header and end with a run link. The help hint shows only where action is needed: on a refusal or a failure, and under an apply or doctor verdict that is not 🟢; a plan comment never shows it. The apply comment's gate, ungated and no-review lines moved to the gate check and the run log.**
- **A stack at path `apply` or `shipmate` plans; `build-matrix` no longer refuses either.**
- **`actions/setup` downloads Terramate with curl instead of `terramate-io/terramate-action`, which consumers may drop from their allowed-actions list; a self-hosted runner's own curl config applies.**
- **`apply-all.yml` is gone: in the re-pin commit, replace the workflow file's `targeted` and `all` jobs with one `apply` job calling `apply.yml` with `environment`, `ref` and `pr_number` (`docs/getting-started.md`).**
- **Every path reads `.github/shipmate-config.yml` through the contents API from the default branch; the could-not-be-read refusal no longer names a ref, and a failed `gh`, `git` or `terramate` call in CI annotates with its stderr below the error line.**
- **`shipmate doctor` checks `shipmate.yml`'s job name, dispatch wiring and routing as one probe, so one unreadable file degrades all three together, and a file that calls no engine plan workflow is reported once.**
- **`shipmate doctor`'s `needs` and `explicit` notices end a cut list with ` … and N more` and never cut an item inside.**
- **The `apply` and `unlock` guard refusal names the check it makes: the dispatching actor is not a `[bot]`.**
- **`shipmate doctor` reads `.github/workflows/shipmate.yml` by path, and a missing one draws the unreadable notice.**

### Removed

- **`.github/shipmate-config.yml` has no `schema_version` key; the engine refuses it as an unknown setting, so delete it in the re-pin commit.**
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
  `gate.approver_team`.
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

- **`.github/shipmate-config.yml` takes its final key names.** Top level: `schema_version`,
  `layout` (`tf_vars`, `workspace` or `folder`), `environments.<name>` and `gate`.
  `gate` holds `approver_team`. An entry holds `region`, `tf_vars`, `aws`, `shared`,
  `needs`, `explicit` and `gated`. A variable reference is `{vars: NAME}`. Any other
  key refuses as unknown.
- An entry name outside the env-name charset, or with a `-plan` / `-apply` suffix, refuses.
  `explicit` and `gated` are booleans and accept no variable reference.

## [0.36.0] — 2026-09-26

Tags `836b768`.

### Added

- **Any string value in `.github/shipmate-config.yml` can name a GitHub variable** with
  `{var: NAME}`, resolved by every reader of the file. An unset or empty variable or an
  invalid name refuses. `shipmate doctor` lists every reference. The detect actions,
  `comment-ops` and `summary` take a new `github-vars` input.

## [0.35.0] — 2026-09-26

Tags `6c6aed1`.

### Changed

- **A `run.env` override is refused in the cell, not at detect.** `scripts/env-inject`
  refuses before `tofu init` when a row's `tf_vars` come back changed or unset through
  `terramate run`. `run.env` may now set `TF_VAR_env` under `layout: folder` and
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

- **A shared environment is declared in the table** with `shared: true` in an
  `environments.<env>` entry. The `SHIPMATE_SHARED_ENVS` variable is no longer read, the
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

- **The approvers team and the ungated-environment list move into `.github/shipmate-config.yml`**
  as `gate.approvers_team` and `gate.ungated_envs`, read from the default branch.
  `SHIPMATE_APPROVERS_TEAM` and `SHIPMATE_UNGATED_ENVS` are no longer read at any level.

### Added

- **`gate` in `.github/shipmate-config.yml`**, holding `approvers_team` (a bare team slug) and
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

- **The environment table moves out of Terramate globals into `.github/shipmate-config.yml`,** as
  four top-level keys — `layout`, `environments`, `env_order`, `explicit_envs` — read from
  the default branch. The `globals "shipmate"` block is no longer read.

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
- **`scripts/onboard`'s by-hand checklist asks for `.github/shipmate-config.yml`.**

### Removed

- **The Terramate worktree-and-evaluate read path**: no `git worktree`, no
  `terramate experimental eval`, no second evaluation in `scripts/env-order`.
- **The `layout: null` refusal.**

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
