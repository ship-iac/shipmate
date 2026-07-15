# PRD 2 Design: Deploy Waves + Drift

**Date:** 2026-07-15 · **Branch:** `prd-2-deploy-waves-drift` (in `shipmate`) ·
**Depends on:** PRD 1 (merged, engine `main` = `2478c05`).

Source of record: `../project/prd-2-deploy-waves-drift.md`, `shipmate/CONTRACT.md`,
`shipmate/CLAUDE.md`. This design **deviates** from the PRD text in two places
(marked 🔶) after a brainstorming session; the deviations are deliberate and
approved.

## Mental model — serverless Terrateam

shipmate reproduces [Terrateam's](https://docs.terrateam.io/overview/how-it-works)
user-facing model with **no server or database**:

| Terrateam (hosted) | shipmate (serverless) |
|---|---|
| Plan auto on PR open/update | `preview.yml` (PRD 1) |
| Apply via PR comment (pre-merge) | `mate apply <env>` (PRD 3) |
| Apply auto on merge (post-merge) | `deploy.yml` (PRD 2) |
| "A successful plan must run first" | fail-safe: no fresh reviewed plan → no apply |
| Layered Runs (topological, deps first, next layer only on success) | **waves** (topological levels; each `needs` prior; fail → skip) |
| Locking / no concurrent modify | per-env `concurrency` group + real backend lock |
| Server-side pending-apply queue | GitHub **check-runs** (`apply / <env> / <stack>` pending) |
| Drift Detection | `drift.yml` |

The lifecycle is **plan → store → review → apply**: plan-cell (PRD 1) generates
and **stores** the reviewed `.otplan` as a GHA artifact and opens a *pending*
apply check; apply-cell (this PRD) **applies that exact stored plan**, never
re-plans.

The one architectural gap vs Terrateam: no DB to hold a durable pending-apply
queue, so GHA's "drop superseded queued run" behavior surfaces as **Deviation B**.

## Components

### 1. `shipmate/actions/apply-cell` — exact-plan apply (shared by PRD 2 + PRD 3)

The core new engine deliverable. One action, invoked identically by `deploy.yml`
(this PRD) and `apply.yml` (PRD 3):

1. **Fetch the reviewed plan.** Download this stack×env's `.otplan` +
   `fingerprint.txt` from the **PR's preview run** (cross-run download: resolve
   the preview run by `head_sha`, then `gh run download <run-id> -n <artifact>`).
   The artifact path preserved by plan-cell is `<stack>/stack.otplan` (PRD-1
   note) — download and place it there.
2. **Verify fingerprint.** Recompute the fingerprint over the current
   environment and compare to the artifact's `fingerprint.txt`. Fingerprint now
   covers `TF_VAR_*` values **and** `TF_WORKSPACE` (Deviation/Q4). Mismatch →
   **fail-safe**, error lists variable **names** only (never values).
3. **Restore state** via `actions/state` (mode `restore`) at the flavor's real
   path. Stacks flavor path is stack-relative `<stack>/.state` (PRD-1 carryover
   fix), not repo-root `.state`.
4. **Apply the stored plan.** `terramate script run --no-recursive -C <stack> apply`,
   where the sample repo's Terramate `apply` script runs `tofu apply stack.otplan`.
   Applying a saved plan file is inherently non-interactive — no `-auto-approve`.
   If state changed since the plan was generated, tofu rejects the stale plan →
   **fail-safe "saved plan is stale, re-plan"** (CONTRACT invariant).
5. **Save state** via `actions/state` (mode `save`).
6. **Complete the apply check.** Locate the pending `apply / <env> / <stack>`
   check-run by name on the PR **head SHA** and PATCH it to
   `completed` / `success`. (plan-cell created it `queued` with
   `external_id = fingerprint`.)
7. **Never re-plan.** Missing artifact / stale plan / fingerprint mismatch /
   no successful preview → leave the apply check pending, emit a loud
   `::error::`, fail the cell.

Mirrors plan-cell's security posture: author-controlled inputs
(`stack`, `env`, `stack-name`) pass via step `env:`, never interpolated into
`run:`; JSON bodies built in python.

### 2. `shipmate/scripts/waves` — topological leveling

Pure-core + `main()` shape, like `build-matrix` (unit-tested core). Reuses
build-matrix's cell shape (`{stack, environment, workload}`) and env-tag fan-out.

- Parse `terramate experimental run-graph` **dot** output → build the full stack
  DAG → `graphlib.TopologicalSorter` → topological **levels over the full graph**.
- Then **filter each level to the work set** (the stacks passed in). This
  preserves transitive ordering through *unchanged* stacks (A→B→C with only A, C
  in the set: C still lands one level after A). Empty middle levels are normal.
- After-edges are stack-level, so a stack×env cell's wave = its stack's level;
  a wave may therefore span environments (fixture: `dns@dev-us` feeds
  `platform@dev-eu` → dns is wave 0, platform wave 1, regardless of env). This
  is how the cross-env edge is respected for free.
- Emit `wave0..wave7` outputs, each a JSON cell array (`[]` when empty).
- `>8` levels → fail loud "split the PR". `--reverse` flag emits destroy order.
- **Ordering comes from `run-graph`, never `list`** (list tracks only data deps,
  not `after`/`before`). The work *set* comes from the caller.

### 2b. `shipmate/actions/deploy-detect` — composite wrapper for the detect job

Consumer repos pin shipmate actions by SHA and never check out shipmate source,
so `scripts/waves` cannot be invoked from a sample workflow directly — it must be
wrapped in a shipmate composite action (mirrors how `actions/build-matrix` wraps
`scripts/build-matrix`). This composite is shipmate code (counts toward budget)
so the three sample `deploy.yml` files stay thin and identical:

1. Map merge SHA → PR head SHA (`GET /commits/{sha}/pulls`).
2. Query `apply / <env> / <stack>` check-runs for that head SHA; keep only the
   **pending** ones as the work set (skip completed → pre-merge-applied / no-op).
3. Run `scripts/waves` over the work set → emit `wave0..wave7` + `head-sha` +
   `empty` outputs.

### 3. `deploy.yml` — post-merge backstop (authored in each of the 3 sample repos)

Sample-repo workflow (fixture; excluded from line budget), uniform across flavors.

- `on: push: branches: [main]`; workflow `concurrency: { group: deploy-main,
  cancel-in-progress: false }`.
- **detect job:** a single `uses: shipmate/actions/deploy-detect` step (§2b).
  It maps merge SHA → PR head SHA, filters to **pending** apply checks (completed
  = pre-merge-applied / no-op → skipped, giving that acceptance criterion for
  free), and emits `wave0..wave7` + `head-sha`.
- **wave0..wave7 jobs:** pre-declared (GHA can't create jobs dynamically), each
  `needs` the previous, matrix `${{ fromJSON(needs.detect.outputs.waveN) }}`.
  - **Skip-propagation guard** on every wave job:
    `if: ${{ !failure() && !cancelled() && needs.detect.outputs.waveN != '[]' }}`
    (GHA's default `success()` fails on skipped `needs`; an empty wave must not
    skip its successors).
  - Per-cell `environment: ${{ matrix.environment }}-apply` (plan/apply env
    split; protected envs pause only that cell for reviewer approval).
  - `jobs.<id>.concurrency` per-env (or stack×env) group **shared with PRD 3
    `apply.yml`** — GHA concurrency is the primary serializer between a racing
    `mate apply` and a merge deploy; the backend lock is the backstop.
  - Each cell calls `actions/apply-cell`.
- **Failure path:** failed cell → its wave fails → later waves skip (skip guard);
  a failed apply notifies the **same Slack webhook as drift** (a failed apply on
  main outranks drift; Actions tab alone is not alerting).
- **Final summary job:** re-complete `shipmate / checkmate` → success on the head
  SHA once applies are done (reuse/extend `actions/summary`).

🔶 **Deviation A (PRD "missed-merge safety" via deployed-ref tag — removed).**
The PRD specified `detect` diff `--changed` against a persisted "last
successfully deployed ref" (git tag / repo var). With **fail-safe exact-plan
apply**, detect is instead **pending-apply-check-driven** and per-merged-PR: the
work set is "checks still pending for this PR's head SHA." No deployed-ref
marker, no `--changed`-vs-last-deploy diff. Simpler, and it is what exact-plan
naturally wants (Terramate has no deployment memory; the marker was only ever a
substitute for the queue we now read from check-runs).

### 4. `drift.yml` — nightly drift detection (authored in each sample repo)

- `on: schedule` (nightly cron, off-peak).
- **Flat fan-out over ALL stacks × envs** (not `--changed`). `build-matrix` gains
  an `all-stacks` mode input (shipmate code, small edit): when set, it enumerates
  every stack via `terramate list` instead of `list --changed`, reusing the same
  env-tag fan-out and 256-cell guard. drift's detect `uses: actions/build-matrix`
  with that mode on.
- Per-cell work runs through a lean shipmate composite `actions/drift-cell`
  (shipmate code — keeps the issue-upsert logic out of sample workflows per the
  "no sample-repo patch code" invariant): restore state, plan with
  `-detailed-exitcode`.
  - exit 2 (drift) → create-or-update **one labeled GitHub Issue** per
    stack×env (stable title/label for idempotent upsert).
  - exit 0 (clean) → **auto-close** that stack×env's open drift issue if present.
- Optional **Slack webhook** input (same webhook the failed-deploy path uses).

### 5. Cross-cutting changes

- **Repos public (Q2).** Before any protection work: **scan git history of all 3
  sample repos for secrets** (expected clean — null resources, zero creds), then
  `gh repo edit --visibility public` on each (confirm with human before the
  flip). Public unlocks: `<env>-apply` reviewer protection (PRD-2 protected-cell
  acceptance) **and** PRD-1's deferred required-check gate on
  `shipmate / checkmate`.
- **Fingerprint expansion (Q4).** Add `TF_WORKSPACE` to the fingerprint hash in
  **both** `plan-cell` (PRD-1 code, edited here) and `apply-cell`, and update
  `CONTRACT.md`'s fingerprint definition. Rationale: workspaces-flavor cells
  otherwise fingerprint identically across envs (env identity is `TF_WORKSPACE`,
  not a `TF_VAR_*`), so an apply could match the wrong env's plan. plan-cell and
  apply-cell **must** use byte-identical algorithms.
- **Line budget — the top risk.** Sample-repo workflows (`deploy.yml`,
  `drift.yml`) are **excluded** (fixture, per LEDGER scope note). Counted
  shipmate code, rough estimates:

  | Component | est. lines |
  |---|---|
  | `actions/apply-cell/action.yml` | ~100 |
  | `scripts/waves` | ~70 |
  | `actions/deploy-detect/action.yml` | ~50 |
  | `actions/drift-cell/action.yml` | ~50 |
  | `build-matrix` all-stacks edit | ~10 |
  | `plan-cell` fingerprint edit | ~3 |

  ≈ **283 lines → running 374 → ~657/600**, i.e. **over the ~600 decision-gate
  budget.** Two levers, applied during planning/implementation:
  1. **Consolidate `deploy-detect` into `scripts/waves` + one thin wrapper** —
     the merge→PR mapping and pending-check filter become part of the waves
     script (python, cheaper per line than YAML steps), wrapped by a minimal
     composite. Saves the standalone action's boilerplate.
  2. **Share plan/classify between `plan-cell`, `apply-cell`, `drift-cell`** —
     the plan-render + change-classify + fingerprint block is near-identical in
     all three. Extract it to one small `scripts/` helper invoked by each,
     rather than triplicating it inline. This is the single biggest saving.

  Target after consolidation: **≤ ~600**. If it cannot land under budget, that
  is a **decision-gate flag** (brief criterion "≤ ~600 lines / second engineer
  in <1h"), surfaced to the human — not silently exceeded. The `~` allows minor
  overrun; ~657 is not minor.

## Acceptance criteria (fixture DAG `dns → platform → {auth, workers} → app → {tenant-a, tenant-b}`)

1. PR touching `dns` + `tenant-a` (wave-distant) → applies in graph order,
   cross-env edge respected (`dns@dev-us` before `platform@dev-eu`), verified
   from run timestamps.
2. PR touching `dns` + `app`, nothing between → both apply in order, empty middle
   waves, skip-propagation guard proven.
3. 🔶 **(revised, Deviation B)** Two PRs merged in quick succession → both PRs'
   apply checks are created and **surfaced**. If neither deploy run is superseded,
   both apply. A **superseded** (GHA-dropped) deploy run leaves its stacks'
   checks **pending + visible on that merged PR** (nothing silently lost),
   recovered by **re-running that deploy**. This replaces the PRD's
   "verified against deployed-ref marker" wording — the fail-safe exact-plan
   model trades silent auto-sweep for explicit visible pending state.
4. Kill a wave-1 cell mid-run → later waves skip, apply checks reflect it,
   re-running the failed cell recovers (its stored plan is still fresh).
5. A merged-then-applied PR shows all apply checks + `shipmate / checkmate`
   completed; a stack applied pre-merge (manually, simulating PRD 3) → deploy
   **no-op** for that stack (its check already completed → skipped by detect).
6. Manufactured drift (PRD 0 fixture) → issue created within one cycle; fix
   applied → issue auto-closed.
7. **Generalization:** deploy waves + drift run correctly in
   `repo-example-folders` (1:1 stack-env) and `repo-example-workspaces`
   (`TF_WORKSPACE` per env) with **zero shipmate code change** (same pinned SHA).

## Risks / notes

- Environment protection on a mid-wave cell stalls the wave until approved —
  acceptable and visible (that is what protection means); document it.
- Drift fan-out cost over all stacks × envs — off-peak schedule; chunk if
  minutes matter.
- Cross-run artifact download depends on preview artifacts still existing
  (retention 3 days). Older → fail-safe. Acceptable.
- `terramate-io/terramate-action` install flakes (HTTP 500) under fan-out with
  no retry (PRD-1 known flake) — deploy fan-out hits the same; consider a
  retry/binary-cache in `actions/setup` as a follow-up.
- `stacks/auth` carries a Terramate `script` override — apply must go through
  `terramate script run`, not raw `tofu`, so overrides are honored.

## Out of scope (PRD 3)

GitHub App, `comment-ops.yml`, `mate apply <env>` grammar/dispatch. `apply.yml`
consumes `apply-cell` but is authored in PRD 3. The workspaces fingerprint
env-identity decision is *resolved here* (Q4) so PRD 3 inherits a correct hash.
