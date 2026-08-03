# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Consumers pin this engine's actions and reusable workflows by **commit SHA**, not
by tag (see `CONTRACT.md`), so a release only reaches a repository when that
repository re-pins — and every engine reference must move in one change. Each
section below names the SHA the release tags.

The version line stays `v0.x` while action inputs, check names, and the comment
grammar are declared unstable in `README.md`.

## [0.3.1] — 2026-08-03

Tags `<backfilled>`.

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

- An artifact count of zero is still read as "the plan matrix was empty".
  Separating that from a listing that returned zero needs a positive empty-matrix
  signal crossing the `workflow_run` boundary; holding on zero without one would
  block every docs-only pull request.
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
