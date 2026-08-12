# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Consumers pin this engine's actions and reusable workflows by **commit SHA**, not
by tag (see `CONTRACT.md`), so a release only reaches a repository when that
repository re-pins — and every engine reference must move in one change. Each
section below names the SHA the release tags.

The version line stays `v0.x` while action inputs, check names, and the comment
grammar are declared unstable in `README.md`.

## [0.13.0] — 2026-08-12

Tags `181413c`.

**Re-pinning is not enough for this release: every GitHub Environment is renamed,
and `plan.yml` / `drift.yml` change one line each.** There is no compatibility
shim — `docs/upgrading.md` §0.13.0 is the per-env migration, and it must happen in
the same change that moves your pins.

### Changed

- **BREAKING for consumers — plan and apply bind `<env>-plan` and
  `<env>-apply`.** The plan side was the bare `<env>`; it is now `<env>-plan`, so
  per logical env: create `<env>-plan`, copy the bare environment's variables to
  it, bind `${{ matrix.environment }}-plan` in `plan.yml`'s `plan` job and
  `drift.yml`'s `drift` job, **merge that**, and only then delete the bare
  `<env>` — `plan.yml` is `pull_request_target`, so until the edit is on the
  default branch every plan run still binds the bare environment. Create the environments
  **before** editing the workflows: a job binding an environment that does not
  exist gets an empty one auto-created by GitHub, and a flavor whose `TF_VAR_env`
  has a fallback default then plans the wrong environment instead of failing. Only
  the engine's apply waves read `SHIPMATE_SHARED_ENVS`; your plan-side binding is
  one expression for the whole repository, so a repository running **mixed** modes
  carries the engine's expression there rather than a static suffix
  (`CONTRACT.md` §Env model, `docs/upgrading.md` §0.13.0).

  Nothing else moves — `matrix.environment`, check names, tags, `explicit_envs`,
  artifact names and comment grammar all key on the bare logical env name; the
  suffix exists only where a job binds an environment.

  The rename is what makes a mis-set mode loud wherever the environment injects
  a variable. Under the old naming the dangerous direction was silent: an apply
  wave falling back to the bare `<env>` landed on the live *plan* environment and
  applied with plan variables, no reviewer and no apply role. Now either mismatch
  resolves to an environment nobody created, and the apply-match fingerprint
  refuses the cell — except on a folder-per-env layout, which injects nothing, so
  both sides hash the empty set and the apply runs inside that auto-created
  environment. There the mismatch is `shipmate doctor`'s finding, not the apply's
  (`CONTRACT.md` §Env model states the condition).

### Added

- **`SHIPMATE_SHARED_ENVS` — one environment for both paths, opt in per env.** A
  logical env named in this repository variable (comma-separated logical names,
  **no spaces after the commas**, matched case-insensitively on comma boundaries)
  binds the bare `<env>` for plan and apply alike. `-plan` and `-apply` are
  reserved suffixes for logical env names.

  What it costs, for that env: the **reviewer gate** — not relocated, removed,
  because a protection rule gates every job binding the environment and would
  stall the plan cells and the nightly drift run — and the **OIDC subject split**:
  plan and apply tokens become identical in `sub`, so no trust policy can separate
  them and plan-time branch code can assume the write role. A deployment branch
  policy scoped to the default branch becomes conditional too, since plan runs at
  the pull request's base ref. `docs/hardening.md` §6 and §7–9 state the full
  price; choose it for envs where the plan path holds no credentials at all.
- **`shipmate doctor` infers the naming per env and words its findings for it.**
  Split, shared, or ambiguous — a bare `<env>` beside a suffixed sibling warns
  that which one is bound is undetermined and the other's protection rules are
  inert. It reads environment *names*, never the variable (that needs a permission
  the App manifest does not declare), so a repository with a bare `<env>` and the
  variable unset gets no *existence* finding — bare-only is what shared mode looks
  like, and it is reported as a shared environment.

## [0.12.0] — 2026-08-11

Tags `e9eb27d`.

**Re-pinning is not enough for this release: rewrite your wrapper `secrets:`
blocks in the same change.** Every documented wrapper now passes secrets by
name, and `secrets: inherit` is no longer a supported call shape.

### Changed

- **BREAKING for consumers — pass the engine's secrets by name.** In your
  `plan.yml`, `apply.yml` and `deploy.yml`, replace `secrets: inherit` with the
  block `docs/getting-started.md` shows. Pass only what each callee declares:
  `summary.yml` takes `SHIPMATE_APP_PRIVATE_KEY` alone, while `apply.yml`,
  `apply-all.yml` and `deploy.yml` take `SHIPMATE_PLAN_PASSPHRASE` as well —
  naming a secret a callee does not declare kills the run at load time. Keeping
  `inherit` still works inside the engine's own organization, so this is not an
  immediate breakage there, but it is the only supported shape from here.

  Two reasons. `inherit` forwards *every* secret the calling repository can see
  — cloud keys, PATs, anything a later workflow added — to a workflow that is
  upgraded by moving a SHA. And it works only within one organization or
  enterprise: called from outside, it delivers nothing **and** suppresses the
  value the callee's `environment: shipmate-engine` binding would have supplied,
  so the App key never arrives and no App-authored surface exists at all — no
  `shipmate / gate`, no pending apply checks, no comment. Measured in a
  cross-organization consumer, both halves.

  Key placement does **not** change: the key stays a secret on your
  `shipmate-engine` environment. A called workflow's `environment:` resolves in
  the *calling* repository, and an environment's value wins over whatever the
  caller passes, empty included — which is why the same snippets serve
  consumers inside and outside the engine's organization.
- **The engine's own reusable calls pass named secrets too.** `apply.yml`,
  `apply-all.yml` and `deploy.yml` inherited into `apply-env-level.yml`. Both
  files live in one organization, but `inherit` is evaluated against the *run*,
  which belongs to the consumer — so a cross-organization consumer's applies
  would have executed and then stranded their `apply / <stack> / <env>` checks
  pending forever. No consumer-side change could have reached that hop.
  Adding a secret to a reusable workflow now follows an ordering rule
  (`docs/releasing.md`): declare in the callee, merge, bump the pin, then map.

### Added

- **An empty App key now names its cause.** Every job whose App-token mint is
  mandatory first checks that the key arrived and, if it did not, reports which
  of the three wiring mistakes to look at. The upstream mint reported only "must
  be set to a non-empty string", which three unrelated causes produce.
  `comment-ops` is deliberately excluded: its mints already fall back to a PR
  comment that says the same thing, and `shipmate help` answers with no App
  token at all.

## [0.11.0] — 2026-08-11

Tags `4034746`.

**Re-pinning is enough for this release, but read the two notes first.** No
consumer workflow file has to be rewritten. However, one new `detect`-time
assertion can fail a repository that has been planning green, and it does so
because that repository was already applying under the wrong environment — see
below. The one-time App bootstrap also changed shape; that only matters if you
are registering a new App.

### Added

- **`AWS_ROLE_ARN_<WORKLOAD>` selects the apply role per workload.** The engine
  read exactly one `AWS_ROLE_ARN` from the Environment a cell binds to, which
  forced one GitHub Environment per (workload × env × region) on any repository
  whose Environments are shared across workloads. Each wave job now prefers
  `AWS_ROLE_ARN_<WORKLOAD>` when the cell carries a `workload/<name>` tag and
  falls back to `AWS_ROLE_ARN`, so one Environment can serve several workloads.
  `<WORKLOAD>` is the tag upper-cased with `-` mapped to `_`. Opt-in: a
  repository that sets only `AWS_ROLE_ARN` is unaffected on every path. The
  suffix is carried onto artifact-derived cells too, so the dispatched and bare
  `shipmate apply` paths behave like the post-merge deploy.
- **`detect` asserts that the injected environment survives `terramate run`.**
  See Changed — this is the note to read before re-pinning.
- **The apply DAG's shape is reported.** The dispatched `shipmate apply` detect
  and the post-merge deploy detect print stack count, `after` edge count,
  topological level count and the size of the largest level. A missing `after`
  edge cannot be detected — "no declared dependency" is a legitimate state — so
  this reports the shape and lets a reviewer who knows the repository judge it.
  A plan run does not print it; `docs/upgrading.md` says so and points at
  `terramate experimental run-graph --label stack.dir` for the point where the
  information is still actionable.
- **`docs/upgrading.md` gains a migrating-from-another-tool section.** Ordering,
  tags, conditional AWS profiles and `run.env` are all the same shape: things a
  working repository already expresses somewhere the engine does not read.

### Changed

- **`detect` now fails when `terramate.config.run.env` rewrites `TF_VAR_env`,
  `TF_VAR_region` or `TF_WORKSPACE`.** `run.env` is applied to the child process
  *after* the ambient environment, so it wins over what the GitHub Environment
  injected — and `plan-classify --fingerprint-only` runs outside `terramate run`,
  so plan and apply both hash the correct job environment while `tofu` on both
  sides ran under the rewritten one. The fingerprint agrees, the exact-plan
  invariant holds, and every cell can collapse onto one state key with plan,
  gate and apply all green. `detect` injects a sentinel and asserts it comes
  back unchanged. To keep a local default, put the injected name first in the
  chain: `tm_try(env.TF_VAR_env, env.env, "dev")`. `TF_DATA_DIR` needs the same
  resolution or the per-environment `.terraform` split drifts from what `tofu`
  actually receives. A config that cannot be evaluated in `detect` at all — a
  bare `env.X` supplied only by the plan Environment — is reported as a warning
  rather than failing the run.
- **A workload-variable collision fails loud.** `workload/net-edge` and
  `workload/net_edge` both map to `AWS_ROLE_ARN_NET_EDGE`, so one Environment
  variable would serve two workloads and a wave job would apply under the wrong
  IAM identity. `detect` refuses rather than picking one, naming both tags.
- **The untagged-stack failure names every untagged stack and the count**, not
  the first one it hit, so a migration can be re-run and watched shrink.
- **The matrix-ceiling error points at remedies that exist.** `MATRIX_LIMIT` is
  enforced only where cells are built from Terramate tags, so when it trips
  there is no successful plan run and no reviewed `.otplan` — a targeted
  `shipmate apply <env>` is not a way past it. Splitting the change is the
  general remedy; where the fan-out comes from a shared local module, which is
  one atomic change by nature, the only lever is reducing the environments in
  play. `CONTRACT.md` §Fan-out states the ceiling.
- **BREAKING for the bootstrap only — `scripts/register-app` takes `--name` and
  `--out` and creates no repository secret.** It previously wrote the App
  private key to a repository secret, readable by any workflow on any branch,
  which is the exposure the engine's key placement exists to prevent. It now
  writes the PEM to `--out` with `O_EXCL` and mode `0600` (POSIX; on Windows the
  mode is not applied and the file inherits the directory's ACLs). The three
  paragraphs of remediation `docs/github-app.md` §2 carried are gone with it.
  Registration is one command: a one-shot `127.0.0.1` listener captures the
  manifest code, matched against a per-run `state`, so the code never leaves the
  machine and there is no copy-paste step. This affects you only if you register
  a new App; an existing installation is untouched.

### Fixed

- **`app/manifest.json` had no `redirect_url`,** so GitHub rejected the manifest
  POST outright and step 1 of `docs/github-app.md` failed for everyone.
- **The manifest's App name was one GitHub already reserves globally.** App
  names are unique across all of GitHub, so a verbatim paste was rejected with
  "Name has already been taken". It is a placeholder now.
- **`terramate experimental run-graph` needs `--label stack.dir`** to emit stack
  paths. Without it nodes are labelled by stack *name*, so same-named stacks in
  different directories collapse into one node — the norm in a
  `{workload}/{stack}` layout.
- **The `disable_safeguards` prohibition is narrowed** to `outdated-code` and
  `all`, which are what actually break `checkGenCode`. The two working-tree
  checks are never evaluated on a `--no-recursive` cell.
- **A named AWS `profile` in generated HCL is documented as a requirement**, not
  an example: the apply path holds only the OIDC session and there is no
  consumer step in which to write an `~/.aws/config`, so a literal `profile`
  must be conditional on a variable defaulting to false.
- **`explicit_envs`' boundary is stated.** It constrains the bare pre-merge
  `shipmate apply` only. Honouring it post-merge would strand those cells: the
  pull request is closed by then and the apply requirements include
  `mergeable`, so nothing could ever apply them.

## [0.10.0] — 2026-08-09

Tags `bbd9a74`.

**Re-pinning alone is not enough for this release.** The consumer contract for
the plan path changed: you must rewrite `.github/workflows/plan.yml` and delete
`.github/workflows/summary.yml` in the same change that moves your pins. A
repository that re-pins without rewriting gets no `shipmate / gate`, so its pull
requests cannot merge. `CONTRACT.md` §Post-plan topology is the written form,
and the `repo-example-*` samples carry the new shape from this release onward.

### Changed

- **BREAKING — the plan path is one workflow now, and every consumer must
  rewrite `plan.yml` and delete `summary.yml`.** `plan.yml` moves to
  `pull_request_target` and gains a third job, `summary`, which is
  `uses: <owner>/shipmate/.github/workflows/summary.yml@<sha>` with
  `secrets: inherit` and five inputs (`pr-number`, `head-sha`, `detect-result`,
  `plan-result`, `planned-cells`). Because `pull_request_target` checks out the
  base by default, `detect` and `plan` must now name
  `ref: ${{ github.event.pull_request.head.sha }}` on their checkout explicitly
  — without it they plan the base branch and report a clean plan for a pull
  request they never read. The consumer's `.github/workflows/summary.yml` is
  deleted; the engine's is now a `workflow_call` workflow whose single job binds
  `shipmate-engine`, checks out nothing, and carries both trust conditions (the
  fork refusal and the draft skip) on its own `if:`, where a consumer cannot
  drop them. The old `workflow_run` topology is **not supported** — a repository
  that re-pins without rewriting gets no gate, so the pull request cannot merge.
  **The migration pull request cannot gate itself, and needs a one-time
  bypass.** A `pull_request` run uses the workflow file from the pull request's
  own head, which no longer declares that trigger; a `pull_request_target` run
  uses the file on the default branch, which does not declare it yet. So the
  pull request that performs the migration produces no plan run, no checks and
  no `shipmate / gate` at all — verified, not predicted. No ordering avoids it:
  the first commit whose head declares only `pull_request_target` is
  ungatable by construction. Merge that one pull request with an administrative
  bypass (an org-admin `bypass_actor` on the ruleset, or a temporary
  `enforcement: evaluate`), and restore enforcement immediately afterwards. Every
  pull request after it gates normally.
  Nothing matches on the plan workflow's `name:` any longer, but its **file
  path stays load-bearing** and unguarded: `apply-detect`'s provenance gate,
  `deploy-detect`'s post-merge lookup, comment-ops' reviewed-plan lookup and
  doctor's `pull_request_target` exemption all address it as `plan.yml`. The
  `repo-example-*` samples carry the new shape from the release that ships this
  change onward; `CONTRACT.md` §Post-plan topology is the written form. The
  `summary` job must also grant `permissions: contents: read`: a callee's
  permissions are capped by the calling job's, and granting less kills the whole
  run at startup with no job, no log and no annotation to explain it.
- **Plans now run against the branch tip, not the merge commit.** The old
  `pull_request` trigger checked out `refs/pull/<n>/merge`; the explicit
  `head.sha` checkout does not produce a merge ref. A pull request behind its
  base now plans what the branch says rather than what the merge would produce.
  The surviving safety net is the stale-plan refusal at apply time, which still
  fails a plan whose state has moved; **require branches to be up to
  date before merging** (`docs/branch-protection.md`) is the setting that closes
  the rest.
- **A summary-job failure now reds the whole plan run.** The summary job lives
  inside the plan run rather than in a separate `workflow_run` run, so a run
  whose gate could not be written no longer reports `success`. Queries that look
  for a *successful* plan run — comment-ops' reviewed-plan lookup, its
  cell-summary fetch for `shipmate doctor`, and `deploy-detect`'s post-merge
  artifact lookup — stop finding it. Fail-closed (apply refuses, and re-running
  the run fixes it), but it is new coupling between the summary step and every
  consumer of the plan run's conclusion.
- **Two open pull requests sharing one head SHA both write the gate.** The
  newest-run-for-this-SHA arbitration went with `workflow_run`, and the caller's
  `concurrency: plan-<pr number>` group is keyed on the pull request number, so
  it does not cover this case. Last write wins, as before.
- **`shipmate doctor` has ten probes, not eleven.** The consumer plan/summary
  wiring probe is gone with the coupling it policed; `scripts/wiring` and its
  `detect`-time gate are deleted with it. Doctor's `pull_request_target` probe
  now exempts `.github/workflows/plan.yml` by exact name and warns for every
  other workflow file, because the trigger is safe only in the shape the engine
  ships — the credentialed job checking nothing out.
- **The `plan-matrix.<N>` marker artifact is gone.** `detect` declares the
  planned cell count as an action output and the caller passes it to the summary
  job as `planned-cells`, so the gate no longer reconciles an artifact listing;
  `actions/summary` loses its `artifact-count` input. The gate still holds
  whenever the cell summaries read disagree with the planned count, in either
  direction.

### Fixed

- **`CONTRACT.md` § Terramate safeguards described a protection that does not
  run.** It said `git-untracked` and `git-uncommitted` stay live on the cells and
  that "a real dirty tree must block". Terramate evaluates both only on the
  recursive path (0.17.1, `commands/run/run.go`), and every cell passes
  `--no-recursive`, so neither is ever reached: an untracked file or a modified
  tracked file in the checkout does **not** block a plan or an apply. Documented
  behaviour only — no engine behaviour changed. `outdated-code` and
  `git-out-of-sync` do run, and the engine's
  `--disable-safeguards=git-out-of-sync` is load-bearing: without it a cell on a
  reviewed SHA behind the default branch refuses outright. Two things follow for
  a consuming repository: `outdated-code`, now the only working safeguard on a
  cell, is **overridable from your own `terramate.config`** and the engine cannot
  re-enable it — do not set `disable_safeguards` there; and
  `.terraform.lock.hcl` has come **out** of the mandatory gitignore list, since
  committing it is OpenTofu's recommendation for pinning providers and a cell
  tolerates it either way. The rest of that list is unchanged and still a MUST.
  See `CONTRACT.md` § Terramate safeguards, and `docs/hardening.md` § What none
  of this fixes for the stray-`.tf` residual this leaves on reused runners.

## [0.9.0] — 2026-08-09

Tags `12bdceb`.

No action inputs, outputs, check names or comment grammar changed — but read
the `script`-block note below before re-pinning: this is the one release where
re-pinning alone can change what runs in your repository.

### Changed

- **The cells invoke `terramate run … -- tofu …` and name the commands
  themselves.** `plan-cell`, `apply-cell`, and `drift-cell` ran `terramate
  script run <name>`, whose command list lives in the consuming repository's
  HCL — author-controlled on a pull request, so the exact-plan invariant (apply
  the reviewed `stack.otplan`, nothing else) was enforced by the branch rather
  than by the engine. Terramate stays in the path for the stack working
  directory and the safeguard policy, which is unchanged
  (`--disable-safeguards=git-out-of-sync`, now asserted on every invocation
  rather than on one line per cell). See `CONTRACT.md` § Engine-owned tofu
  invocation.

  Two behaviour deltas to know before you re-pin. The apply no longer passes
  `-lock=false`, so it now takes whatever lock the configured backend has (the
  plan still runs unlocked — a plan is read-only); on a remote backend, an apply
  killed outright — a cancelled run, a concurrency displacement — can leave that
  lock held, and the next apply then fails acquiring it until someone runs
  `tofu force-unlock`. And `tofu init` runs
  outside the teed pipeline, so its output no longer reaches `apply.txt`: a cell
  whose init fails is reported `failed` and renders link-only in the apply
  comment ("Apply output unavailable"), with the error in the job log.

  A repository that still defines `script "plan"` / `script "apply"` keeps
  working, because the engine stops reading those blocks. That is also the
  behaviour change: **any command a `script` block added is no longer executed,
  and nothing signals it.** A `tofu validate` gate, a pre-step, or a wrapper
  around plan or apply stops running the moment you re-pin, so a check you added
  to fail closed fails open instead.

  New repositories need neither those blocks nor
  `terramate.config.experiments = ["scripts"]`.

## [0.8.1] — 2026-08-08

Tags `bec15e4`.

Re-pinning is all it takes: no action inputs, outputs, check names or comment
grammar changed. One behavioural difference worth knowing before you re-pin —
the `complete` step can now spend up to two minutes re-reading the run's jobs
listing before it gives up, where it previously read once.

### Fixed

- **`apply-complete` no longer strands apply checks when the run's jobs listing
  lags.** It selected check-run ids from `GET /actions/runs/{id}/jobs` by
  `conclusion == "success"`, so a cell whose conclusion had not yet propagated
  was indistinguishable from one that never ran: its ids were dropped and the
  step still exited 0. Seen live — an apply whose eight cells all succeeded left
  one check `queued` and `shipmate / gate` blocked the PR permanently, while
  every surface a human reads showed success. The step now classifies each
  snapshot cell (terminal, absent, or not yet terminal), re-reads the listing
  until nothing is unresolved, and fails loudly naming whatever it could not
  resolve.

  This is the gate failing *closed* either way — the danger was never a bad
  merge, it was that nothing said the check would sit pending forever.

- **Running out of retries no longer discards the completions the run earned.**
  The first cut of the retry loop exited before the PATCH step, so one lagging
  cell in a thirty-cell level stranded all thirty checks where previously
  twenty-nine completed. Earned ids now accumulate across attempts, are
  completed, and only then does the step fail on what is left.

- **An empty listing, a failed fetch, and the zero-match floor are retried too.**
  Each previously hard-failed on the first attempt — the most extreme forms of
  the very lag the loop exists to absorb were the cases that got none of its
  budget. Since a failed `complete` fails its env-level and `deploy.yml` skips
  every successor, that turned a transient blip into a halted multi-region
  deploy over infrastructure that had already applied.

- **Cells left pending are named, whatever the reason.** Cells that are terminal
  but not successful (a skipped or failed wave) were dropped as silently as the
  lagging ones. Nothing that leaves an apply check pending is silent now.

## [0.8.0] — 2026-08-08

Tags `5ee006b`.

**Re-pinning requires one edit if your workflows read the per-wave outputs
directly** — see below; grep for `outputs.wave` first. Otherwise re-pinning is
all it takes. The rest of this release is internal: dead surface removed and
comment prose cut, with no behaviour change.

### Removed — breaking

- `actions/apply-detect` no longer declares the per-wave outputs `wave0` …
  `wave7`. Use the aggregate `waves` object (`fromJSON(...).waveN`), which
  `apply.yml` and `apply-env-level.yml` have consumed since 0.6.0.

  **This break is silent.** GitHub Actions resolves a read of an undeclared
  composite-action output to the empty string rather than erroring, so a
  workflow still on the old shape gets `needs.detect.outputs.wave0 == ''`:
  the `!= '[]'` skip condition stays true, the wave job is not skipped, and
  `fromJSON('')` then fails at matrix expansion — the apply run dies before
  any cell applies and every apply check stays pending. Before re-pinning past
  this release, grep your workflows for `outputs.wave` and move them to
  `fromJSON(needs.detect.outputs.waves).waveN`.

- `dev/repin_consumer.rewrite_consumer`, `dev/repin_consumer._survivors` and
  `dev/repin_internal.rewrite`, and `scripts/waves`' command-line entry point
  (`main`, `--reverse`, `SHIPMATE_WORKSET`). None had a production caller;
  `dev/` is maintainer tooling and `scripts/waves` is only ever `_load`ed as a
  module, so no consumer surface is affected.

### Added

- **A guard pinning that `guard_max_waves` runs before the wave map is padded.**
  No behaviour changed and no release was ever affected — the guard call was
  present and correct in every shipped version. What was missing was anything
  holding it there: removing the call from both live callers (`apply-detect`,
  `env-order`) left the entire suite green, in this release and every one before
  it. Since `pad_waves` truncates, an unguarded regression would have dropped
  the deepest cells of a change spanning more than 8 dependency levels — the
  apply run finishing green having applied nothing for them, their apply checks
  left pending. Now pinned over the parsed AST for each caller, plus a
  behavioural test through `waves_by_env_level`.

## [0.7.2] — 2026-08-07

Tags `6973262`.

**No consumer action beyond re-pinning**, and re-pinning matters here: the fix
lives in `scripts/`, which `actions/summary` runs out of its own pinned
checkout, so a consumer still pinned to v0.7.1 keeps the old behaviour.

### Fixed

- **A plan that produced exactly one changed cell created no pending apply
  check**, so that change could not be applied by either path. `pending-checks`
  and `doctor` globbed `cell-summary.*/cell.json`, but
  `actions/download-artifact` extracts into `path` itself, with no per-artifact
  subdirectory, whenever exactly one artifact matches the pattern — so a
  one-cell plan lands at `cells/cell.json` and both readers saw an empty
  directory. Pre-merge, `shipmate apply <env>` then failed in `waves / snapshot`
  with `no App-authored apply check named 'apply / <stack> / <env>' on the head
  SHA before apply`; post-merge, `deploy-detect` read an empty work queue,
  because the pending apply checks **are** that queue. The gate stayed pending
  throughout — this failed closed, never green over unapplied infrastructure —
  but the only way past it was a second changed cell. `doctor` was also silently
  skipping every environment probe on such a plan. Both readers now glob
  recursively, matching `summary-comment`, which already did, which is why the
  plan comment and gate looked healthy while the apply path was dead.

## [0.7.1] — 2026-08-07

Tags `2fdf60f`.

**No consumer action beyond re-pinning.** If your pre-merge `shipmate apply
<env>` has been failing with `Secret SHIPMATE_APP_PRIVATE_KEY is required, but
not provided while calling`, this release is the fix and re-pinning is all it
takes.

### Fixed

- **`apply.yml`, `apply-all.yml` and `summary.yml` no longer declare
  `SHIPMATE_APP_PRIVATE_KEY` a *required* `workflow_call` secret**, which made
  the documented way of storing that key unusable on the pre-merge apply path.
  `docs/github-app.md` §5 puts the key on the `shipmate-engine` **environment**,
  and deliberately not in a repository secret — but `secrets: inherit` forwards
  only what is in the caller's context, and an environment secret never is. A
  required declaration is therefore unsatisfiable, and GitHub rejects the call
  before any step of the first callee job that binds no `environment:` — `guard`
  — so a dispatched `shipmate apply <env>` died red with **zero steps executed**.
  Nothing was applied and nothing partially applied; only the verb was dead.
  Post-merge `deploy.yml` was unaffected because its declaration was already
  optional, and `summary.yml` carried the requirement without failing only
  because its single job binds `environment: shipmate-engine`, which resolves the
  key. The credential still reaches everything that needs it: every job that
  mints an App token binds that environment and reads it there. A consumer that
  keeps the key as a repository secret instead trades a load-time rejection for a
  red run at the mint step — the behaviour `deploy.yml` has always had — and
  `shipmate doctor` names a missing key directly.

## [0.7.0] — 2026-08-07

Tags `6be3d34`.

**One consumer action beyond re-pinning, and it is not optional:** a wrapper
that calls `apply.yml`, `apply-all.yml` or `deploy.yml` must add
`id-token: write` to the calling job — and to the workflow's top-level
`permissions:` block if it declares one — in the same change that re-pins to
this release. **Including consumers that use no cloud credentials at all.**
GitHub caps a called workflow's permissions at each `uses:` boundary, so without
the grant the run fails at workflow-resolution time, taking every wave job with
it — nothing applies and nothing partially applies. Opting *in* to AWS OIDC is a
separate, later step: two variables, `AWS_ROLE_ARN` and `AWS_REGION`, on each
`<env>-apply` environment you want cloud access from. With them unset the
credentials step is skipped and the job holds no cloud credential, which is why
the sample repos on the local backend still run credential-free.

### Added

- **The apply path can mint an AWS OIDC token.** Every wave job in
  `apply-env-level.yml` carries `id-token: write` and runs a `Configure cloud
  credentials (AWS OIDC)` step gated on `vars.AWS_ROLE_ARN != ''`, before the
  cell — so the assumed role's session variables are in place for the plan
  restore and `tofu`. They are `AWS_*`, so the apply-match fingerprint excludes
  them (`CONTRACT.md` §Apply-match fingerprint). `snapshot` and `complete`
  deliberately get no token: neither reaches a provider. The nine jobs across
  `apply.yml`, `apply-all.yml` and `deploy.yml` that call `apply-env-level.yml`
  grant `id-token: write` so it survives the `uses:` boundary. The plan path is
  not wired for this.
- **`actions/apply-cell` and `actions/drift-cell` accept an empty `state-path`**
  (`required: false`, default `""`), with every `actions/state` step gated on
  it, so a consumer whose state lives in a remote backend skips artifact state
  entirely instead of round-tripping a directory the backend owns. On that path
  the restore is `skipped` rather than `failure`, so a remote-backend cell can
  never be blocked on artifact state it never had. The exact-plan `.otplan`
  artifact flow is unchanged — that is how an apply proves it is applying the
  reviewed plan, and it is not state.

### Changed

- `apply-env-level.yml` declares a top-level `permissions: {}` floor. Every job
  in it already sets its own block; the floor means a job that later loses one
  inherits nothing rather than everything the caller granted — which now
  includes `id-token: write`.
- `docs/hardening.md` controls 7–9, and checklist rows 7, 9, 18 and 19, now
  describe the credential path that exists rather than one that did not.
  Including what it does *not* bound: `id-token: write` on the wave jobs is
  unconditional, because a GitHub Actions `permissions:` block cannot be an
  expression, so an apply job can always *request* a token. What decides which
  role it can actually assume is the role's own trust policy and its
  `environment:` claim condition — not the location of the `AWS_ROLE_ARN`
  variable, which resolves organization → repository → environment and is
  therefore advisory scoping only.

### Known, and deliberately not changed here

- **`state_suffix` stays required on all four apply-path workflows**, including
  for consumers on a remote backend, who pass `state_suffix: ""` explicitly.
  Making it optional would be one line, and it is the wrong line: an *omitted*
  input would then read as "a remote backend owns state" — the same silence for
  a consumer who meant it and one who forgot. On a new or empty stack the second
  case is not visible either, because there is no state to fail against. That
  run applies real resources, skips the state save, and leaves the required gate
  green over infrastructure nothing recorded. An explicit empty string costs the
  remote-backend consumer one character and makes the choice legible in the
  wrapper.

## [0.6.0] — 2026-08-06

Tags `4fbb572`.

**One consumer action beyond re-pinning:** accept the shipmate App's pending
permission request (`environments: read`). Until an org owner does, the new
probe reports itself as not performed — in every `shipmate doctor` report, and
in the annotations the summary run writes for any pull request that planned at
least one cell (a docs-only or pin-bump pull request declares no environment, so
the probe has nothing to read and says nothing). The permission is minted in its
own non-fatal step, so the gate status and the apply checks are unaffected — but expect one further symptom of
that same one cause, and it is not a bug. The App-permission-drift probe stops
being silent: the full-manifest mint `shipmate doctor` attempts now asks for
`environments: read` too, so it fails on an installation that has not accepted
the request, and every `shipmate doctor` report carries a second warning
saying the installation is missing a permission the manifest declares. Both
clear on Accept.

### Added

- **`shipmate doctor` gained an eleventh probe: the secrets a *plan*
  environment holds.** A plan cell runs the pull request branch's own code, and
  a plan environment cannot carry approval rules or a deployment branch policy
  without stalling every cell — so whatever it holds is readable by anyone who
  can push a branch, and until now nothing observed it. The probe counts each
  declared plan environment's secrets and names them (names only — no GitHub API
  returns a secret's value), as a note pointing at `docs/hardening.md` control 8:
  the count is exact, while the printed name list is capped so one crowded
  environment cannot spend the whole report's size budget — its later names are
  not printed. A warning if `SHIPMATE_APP_PRIVATE_KEY` is among them, and — when
  the listing was too long to read whole — a warning that whether the
  environment holds it could not be determined, rather than silence.
  `<env>-apply` is deliberately not read: that is where credentials belong.
  With no `environments: read` token the finding says the check was not
  performed — never that the environment is clean.
- **`docs/hardening.md` control 8 now states the credential-free plan
  posture**: prefer a provider read path that needs no long-lived credential,
  and, when the engine's credential path exists, a read-only OIDC role
  conditioned on the *plan* environment's claim.

## [0.5.0] — 2026-08-04

Tags `a5a823f`.

**Carries the `0.4.0` section below as well**, which was written but never
tagged — so a repository re-pinning from `v0.3.1` picks up both the artifact
listing fixes described there and everything described here, in one move.

No consumer YAML change: **re-pinning is the only action required** — and the
re-pin pull request is itself where a repository with already-broken
plan/summary wiring finds out. GitHub resolves a workflow file, and with it the
`uses:` pins inside it, from the pull request's own branch, so that pull
request's `detect` job already runs the new `actions/build-matrix` and goes red
on the spot. Expect it, rather than reading it as a regression: the finding
names the condition and the file to fix, and fixing it in the same pull request
greens the run.

### Added

- **The consumer wiring the `shipmate / gate` status depends on is now
  checked on every plan run.** Four conditions live in files the engine does
  not own — the plan workflow at `.github/workflows/plan.yml`, its top-level
  `name:` being exactly `shipmate · plan` (U+00B7 middle dot), that workflow
  triggering on `pull_request` (`pull_request_target` is a different event and
  does not satisfy the engine's guard), and some workflow triggering on
  `workflow_run` for that name and calling the engine's reusable
  `.github/workflows/summary.yml` — and until now nothing checked any
  of them: a break produced a plan that ran to completion, no apply checks, no
  sticky comment, no gate status, and no error anywhere. `actions/build-matrix`
  now reads the workflow files out of the checkout it already has and **fails
  `detect`** on a confident break for a `pull_request` or
  `pull_request_target` event. On any other event — the drift schedule — the
  same finding is a warning instead, so one merged mistake cannot take nightly
  drift down in every consumer; anything uncertain is a notice and never
  blocks.
- **`shipmate doctor` gained a tenth probe** reporting those same three
  conditions on demand, at the commit under examination. Comment-ops is
  triggered by `issue_comment` and is not downstream of the summary wiring, so
  the report still answers when the wiring is broken. Doctor never fails a run,
  so the probe is `WARNING`/`NOTICE` only; `detect` is the half that blocks.

### Fixed

- **`CONTRACT.md` described the name-side failure wrongly, and the correction
  is the reason `detect` is where the check lives.** It said a renamed `name:`
  means the consumer's `workflow_run` trigger "simply never fires". GitHub in
  fact resolves `workflow_run.workflows:` against the workflow entity's
  **default-branch** name, so the renaming pull request still fires the
  trigger, still gets its gate, and merges green; the breakage begins at merge
  and applies to every pull request afterwards — including the one opened to
  fix it, which is gateless and needs an administrator. The same holds for an
  edit to the consumer's own `summary.yml`, for the neighbouring reason. Only
  the **path** half fails closed before the merge, via the engine's
  `workflow_run.path` guard. Two of the three breaks therefore merge green,
  which is why the check runs in `detect` on the pull request that introduces
  them rather than being left to doctor.
- **Doctor's `pull_request_target` probe missed the trigger in a workflow file
  written with a byte-order mark.** Doctor now decodes workflow bytes through
  the shared wiring decoder, which strips one leading U+FEFF. A BOM'd file with
  `on:` on line 1 previously failed the column-0 anchor, so the probe found no
  `on:` block at all and reported nothing — a false negative on a hardening
  probe. The pin probe was unaffected; its match is not column-anchored.

## [0.4.0] — 2026-08-03

**Never tagged.** This section was written but no release was cut, so `v0.4.0`
does not exist and no consumer ever pinned it — the changes below reached
consumers as part of `v0.5.0` instead. It could not be cut retroactively either:
the commit it described carries a stale `actions/summary` self-pin, so its tree
runs its own old code, and `docs/releasing.md` forbids tagging such a commit.
The section is kept rather than folded upward because the changes are real and
this is where their reasoning lives.

Two more green-gate-over-unapplied-infrastructure fixes, both rooted in the
Actions artifact listing. No consumer YAML change: **re-pinning is the only
action required** — but every engine reference must move in one change, because
the marker's writer lives in `actions/build-matrix` and its reader in
`.github/workflows/summary.yml`. A repository that re-pins the summary caller and
not `actions/build-matrix` publishes no marker, and every pull request holds until
both match.

### Fixed

- **A `cell-summary.*` count of zero was read as "the plan matrix was empty".**
  That reading is right for a docs-only or pin-bump pull request, and the gate
  greens quietly on it. But the artifacts listing is read-after-write eventually
  consistent and the trusted job reads it seconds after the plan run ends, so zero
  also arrived for a run that had planned stacks — indistinguishable from inside a
  job that sees only the plan run's artifacts. The gate went green, no
  `apply / <stack> / <env>` checks were created, and because `deploy.yml`'s work
  queue *is* the pending apply checks, the reviewed changes merged and were never
  applied. `detect` now publishes the planned cell count as the artifact **name**
  `plan-matrix.<N>` — a `workflow_run` event carries no job outputs, so a name in
  the listing the trusted job already reads is the only channel — and the gate
  holds unless exactly one such marker is readable. Zero now means an empty matrix
  only when the marker says so.
- **A *partial* listing greened the gate just as quietly.** The same lag could
  return some of the cell summaries: three of five listed, all three downloaded
  and parsed, so the existing shortfall check (parsed < listed) saw nothing wrong.
  One apply check per listed cell, and the two stacks missing from the listing
  merged reviewed with no apply check at all. The gate now holds unless the listed
  count equals the planned count. The counting step retries a disagreeing listing
  up to three times, five seconds apart, which narrows the eventual-consistency
  window without pretending to close it, and then warns once per cause —
  unreadable listing, no readable marker, or a listed count that disagrees with
  the marker — because the gate description is capped at 140 characters and the run
  page is where the reason has to be legible.

## [0.3.1] — 2026-08-03

Tags `1efbee8`.

Fail-safe fixes to the paths 0.3.0 relocated. No interface change: re-pinning is
the only action required.

### Fixed

- **The gate could be written green over plan evidence that was never read.** An
  unreadable artifact listing reported a count of `1`, and the hold fires only on
  a shortfall against the parsed cell count — so with one cell downloaded (that
  download is best-effort) `1 < 1` was false, one apply check was created for a
  five-stack plan, and the remaining four merged with no check at all. Because
  `deploy.yml`'s work queue *is* the pending apply checks, those changes would
  never have been applied. The sentinel is now non-numeric, and the hold fires on
  it before any comparison a cell count could satisfy.
- **A held gate was clearable by the apply it existed to prevent.** `gate-refresh`
  wrote `success` from apply check-runs alone, and `scripts/authorize` consults
  the review decision, never the gate — so `shipmate apply` on a held pull request
  greened it. `gate-refresh` now refuses to overwrite a failing gate and says why;
  the only exit from a hold is a fresh plan.
- **Two steps could lose the gate write entirely.** The supersede check aborted
  the whole job on a transient paginated API error, and the pull-request probe
  folded an API failure into "is a draft" and skipped every later step behind a
  plain `echo` — a green run with no gate, and a required check that never
  arrives. The first now warns and proceeds; the second distinguishes unreadable
  from draft and fails loudly.
- **A cell with no queued apply check applied anyway.** The refusal that used to
  run before `terramate apply` became a warning that fired only when *every* cell
  was affected, so one cell could mutate infrastructure and advance state while
  its check stayed pending forever — recoverable only by re-planning over
  already-applied infrastructure. The per-cell refusal is restored.
- **Drift could be lost silently.** Drift issues are now authored from an
  artifact, but the compose and upload steps were best-effort, so a dropped cell
  simply vanished from the glob: no issue, no Slack, green run. Both steps are
  required again. Separately, a dead Slack webhook no longer leaves the nightly
  run green — those failures are collected and re-raised like the API ones
  already were.

### Changed

- `docs/hardening.md` stated the environment-secret release rule inverted,
  contradicting `docs/github-app.md`. Both now read: an environment secret is
  released only to a job that *names* the environment; a repository secret is
  readable by any workflow on any branch without naming anything.
- The apply-cell credentials guard asserted raw text substrings and would not
  have caught a direct `secrets.SHIPMATE_APP_PRIVATE_KEY`; it is now a parsed
  assertion over the action YAML, covering `drift-cell` as well, which had none.

### Known, and deliberately not changed here

- `shipmate doctor` has no probe for the consumer `summary.yml`/`plan.yml` wiring
  the reusable workflow hard-codes. doctor runs inside the job that wiring gates,
  so a probe there cannot report its own absence. Until that is addressed, a
  consumer whose plan workflow is misnamed or missing its summary caller gets no
  gate and no diagnostic — see `CONTRACT.md` for the exact required names.

## [0.3.0] — 2026-08-03

Tags `e576103`.

**Adopting this release requires a consumer YAML change.** The post-plan summary
moved out of `plan.yml` into a `workflow_run` workflow, so a repository that
re-pins without adding `summary.yml` gets a plan that runs to completion and
never writes `shipmate / gate` — silent and permanent, the failure mode
`CONTRACT.md` warns about. Add `.github/workflows/summary.yml` calling this
release's reusable `summary.yml`, drop `plan.yml`'s in-run `summary` job, and
declare `environment: shipmate-engine` on `comment-ops.yml`'s `ops` job and
`drift.yml`'s new `issues` job. Note a `workflow_run` workflow only fires once it
is on the default branch, so land `summary.yml` before removing the in-run job —
otherwise the pull request making the change cannot be gated by it.

### Changed

- **No `pull_request`-triggered job holds the App private key.** The summary that
  authors the `apply / …` checks, the sticky comment and the `shipmate / gate`
  status now runs in a trusted `workflow_run` workflow at the default-branch ref,
  and the key is scoped to the `shipmate-engine` GitHub Environment whose
  deployment branch policy names that branch only. A branch- *or* tag-authored
  workflow naming that environment is refused before a runner starts. Apply and
  drift cells hold no App credentials at all.
- **`actions/summary` inputs are reshaped** — `run-conclusion` and
  `artifact-count` replace `plan-result` and `detect-result`, and `plan-run-url`
  is new. Breaking for anything invoking the action directly.

### Added

- **Fork pull requests are refused outright.** `actions/build-matrix` fails
  `detect` when a `pull_request` or `pull_request_target` event's head repository
  is not the running repository, with no input to permit it. A fork's plan would
  otherwise execute its own Terramate/OpenTofu code on the consumer's runners
  with the plan environment's variables. The refusal is loud rather than an empty
  matrix, because no `shipmate / gate` is ever written for a fork head.
- `actions/apply-complete`, `actions/apply-snapshot` and `actions/drift-issues` —
  the trailing trusted jobs that complete apply checks and author drift issues,
  split out of the cells that used to do it while holding the key.
- **Two `shipmate doctor` probes.** One reports any workflow file declaring
  `pull_request_target`, the fork-reachable form of the branch-authored-workflow
  attack. One reports whether the default branch's ruleset requires **code-owner**
  review: an approval count is no substitute, because the App can submit a review
  that counts toward it, and only a `CODEOWNERS` review is beyond a leaked key.
  Both state their limits rather than implying more than they check —
  `require_code_owner_review` is not proof a `CODEOWNERS` entry covers the paths
  the IaC lives in, and classic branch protection is not visible on the endpoint
  the probe reads.

### Fixed

- The `shipmate-engine` environment must not keep a repository secret of the same
  name beside it: an environment secret is only withheld from jobs that *name*
  the environment, so a leftover repository secret defeats the scoping entirely.
  `docs/github-app.md` §6 now deletes it and shows how to confirm, because no
  probe can — listing repository secrets needs a permission the App manifest does
  not declare.
- `docs/hardening.md` claimed in four places that the `<env>-apply` reviewer was
  the *only* control a leaked App key cannot satisfy. There are two, unforgeable
  at different points: a `CODEOWNERS` review at the merge, the environment
  reviewer at the apply.

## [0.2.3] — 2026-07-31

Tags `07c2de1`.

### Changed

- `terramate-io/terramate-action` **v2.0.0 → v3.3.0**, which carries a
  template-injection patch. v3's breaking change is the removal of the
  fallback-to-latest when no version is given; shipmate always passes an explicit
  `terramate-version`, so behaviour is unchanged — but a consumer wiring an empty
  value now fails instead of silently installing latest.
- `actions/download-artifact` **v7.0.0 → v8.0.1** in `actions/apply-summary` (ESM
  migration upstream, no interface change).

Both had been invisible to Dependabot until 0.2.1 widened its scan to
`actions/*` — the Terramate installer, which runs in every plan, apply and drift
job, was two majors behind.

## [0.2.2] — 2026-07-31

Tags `28d28f7`.

### Fixed

- **An approvers-team member could be refused their own apply.** comment-ops
  decided team membership with `gh api … | grep -q active`: `grep -q` exits at
  its first match, `gh` upstream can take SIGPIPE, and `pipefail` hands the `if`
  a 141 that reads as *not a member*. Same shape as the gate verdict fixed in
  0.2.1, failing closed rather than open. The state is now read into a variable
  and compared for equality, so `inactive` no longer matches as a substring
  either. Covered by `test_comment_ops_membership.py`, which runs the block
  against a stubbed API.

## [0.2.1] — 2026-07-31

Tags `8beba59`.

### Fixed

- **The post-merge gate could green a failed deploy.** `deploy.yml` decided its
  verdict with `echo "$RESULTS" | tr | grep -qvE`: `grep -q` exits at its first
  match, the writer ahead of it takes SIGPIPE, and `pipefail` hands the `if` a
  141 that reads as *no bad result found*. Unreachable at real input sizes (tens
  of bytes against a 64 KiB pipe buffer) and never observed, but wrong in the
  dangerous direction. The scan is now in-process, and an empty result string
  keeps meaning incomplete rather than all-clean.
- Drift issues link the run that found the drift. The body is rewritten on every
  drift run, so an issue that stays open points at the newest evidence.

### Internal

- Dependabot scans `actions/*` for third-party pins; `directory: /` had covered
  `.github/workflows/` and a root manifest only, so the pins inside each
  composite action had never been offered an update.
- The detect queries, `dev/` pin helpers and test loaders are single-sourced.
- Test guards that execute an action's shell body probe for a working `bash`
  first, instead of trusting a `bash.exe` that may be the Windows Store alias.

## [0.2.0] — 2026-07-29

Tags `c77e2cd`.

### Changed — BREAKING

- **The apply check name flips field order**: `apply / <env> / <stack>` becomes
  `apply / <stack> / <env>`. The check is load-bearing — it is the work queue the
  post-merge deploy reads — so old-order checks left on a head SHA break both
  ways: while **pending** they hold `shipmate / gate` open, and once **complete**
  the gate greens and the deploy re-queues already-applied cells into the
  stale-plan fail-safe. A re-plan clears neither. **Migration: after re-pinning,
  every PR planned before the bump needs a fresh commit.** Runbook in
  `docs/branch-protection.md` § Upgrading.
- Workflow and job display names read as shipmate (`shipmate · plan`,
  `shipmate / detect`, `shipmate / summary`). The check *icon* cannot follow: a
  job's check run is always authored by the `github-actions` app, so only the
  App-authored surfaces (apply checks, gate, comments, drift issues) carry
  shipmate's. `CONTRACT.md` § Check names now fixes the naming rule — ASCII and
  slash-delimited for anything matched by machine, the middot reserved for
  workflow names.

### Added

- A pull request that changed no stacks no longer gets a plan comment. The
  suppression is create-only and applies **only** when a zero cell count means
  no stacks changed (detect succeeded, empty matrix); every other zero leaves the
  plan unknown, so nothing is written and an existing comment stands rather than
  being overwritten with a claim about a plan nobody read. A run where `doctor`
  warned still posts — that comment's footer is doctor's only PR-visible pointer.

### Fixed

- App installation tokens are minted with `client-id` (the `app-id` input is
  deprecated upstream), and a `doctor` report survives losing its comment.
- `build-matrix` reserves a stack path of exactly `shipmate`, whose plan check
  would otherwise land in the namespace the summary comment's exact-name lookup
  reads.

### Internal

- The engine's own SHA pins are maintained by `dev/` tooling rather than a
  documented `sed`, `dev/pin_status.py` answers "is this commit safe to pin?",
  and two silent-failure paths in the pins guard are closed (a dangling pin in a
  non-shallow clone now fails instead of skipping; a failed `git ls-tree` raises
  instead of reading as "no references").

## [0.1.0] — 2026-07-27

Tags `aa4d8b7`. First tagged release — the baseline every section above is
relative to, and what made `shipmate doctor`'s pin-freshness comparison and a
consumer's Dependabot able to resolve a pin at all.

What it carries: per stack × environment plan fan-out with a check each and the
plan published as a sticky PR comment; wave-ordered applies over the Terramate
`after` DAG with environment-level ordering on top; exact-plan applies from the
reviewed, encrypted plan artifact, with a stale-plan fail-safe; the pre-merge
comment grammar (`shipmate apply [env]`, plus read-only `help` and `doctor`);
the aggregate `shipmate / gate` commit status, authored by the shipmate GitHub
App and pinned by `integration_id` in the consumer's ruleset; post-merge deploy
driven by the pending apply checks as its work queue; per-run apply result
comments; and nightly drift detection as auto-closing issues.

[0.3.1]: https://github.com/ship-iac/shipmate/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/ship-iac/shipmate/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/ship-iac/shipmate/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/ship-iac/shipmate/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/ship-iac/shipmate/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ship-iac/shipmate/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ship-iac/shipmate/releases/tag/v0.1.0
