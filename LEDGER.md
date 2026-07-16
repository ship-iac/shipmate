# Line-count ledger

Budget: ≤ ~600 bespoke script/YAML lines across the whole project. Append per PRD; never rewrite past rows.

Scope: this ledger counts **shipmate's own** bespoke actions/scripts only. Sample-repo (`repo-example-*`) fixture content — Terramate `script`/`generate_hcl` blocks and helper scripts like `tools/mutate-state.ps1` — is test-fixture configuration, not shipmate tooling, and is intentionally excluded from the budget.

| PRD | Component | Lines | Notes |
|-----|-----------|-------|-------|
| 0 | actions/state/action.yml | 33 | cache-based state restore/save |
| 1 | actions/setup/action.yml | 32 | terramate+opentofu install (versioned inputs), provider plugin cache |
| 1 | scripts/build-matrix | 87 | changed-stack × env-tag fan-out (tag-based env discovery), 256-cell guard |
| 1 | actions/build-matrix/action.yml | 21 | composite wrapper: run build-matrix, emit matrix/empty outputs |
| 1 | actions/plan-cell/action.yml | 114 | plan via terramate script, classify changes from plan JSON, plan text → step summary, plan+cell artifacts, pending apply check, TF_VAR fingerprint |
| 1 | actions/summary/action.yml | 87 | sticky PR comment + shipmate/checkmate gate (gated on detect/plan job results) |
| 2 | scripts/plan-classify | 42 | shared classify + TF_VAR/TF_WORKSPACE fingerprint (reused by plan-cell, apply-cell, drift-cell) |
| 2 | scripts/waves | 82 | run-graph dot → topological wave levels (stdlib graphlib) |
| 2 | scripts/deploy-detect | 69 | merge→PR head→pending-apply work set→waves + preview run id |
| 2 | actions/deploy-detect/action.yml | 52 | composite wrapper for the deploy detect job (block-style wave outputs) |
| 2 | actions/apply-cell/action.yml | 96 | exact-plan apply of the reviewed .otplan + complete the apply check (shared with PRD 3) |
| 2 | actions/drift-cell/action.yml | 74 | drift plan + labeled-issue upsert/auto-close + optional Slack |
| 2 | (edits) build-matrix, build-matrix action, plan-cell | — | build-matrix +compute_cells/all-stacks (→82); plan-cell now calls plan-classify + TF_WORKSPACE fingerprint (→121); build-matrix action +all-stacks input (→26). Deltas included in the recount below. |

**Totals (⚠️ recount):** the PRD-0/1 rows above used an inconsistent (tighter)
line count. A **consistent non-blank recount** of all bespoke shipmate
actions/scripts gives:

| PRD | non-blank lines |
|-----|-----------------|
| 0 (state) | 45 |
| 1 (setup, build-matrix core+action, plan-cell, summary) | 359 |
| 2 (plan-classify, waves, deploy-detect core+action, apply-cell, drift-cell + PRD-1 edits) | 415 |
| **Total** | **819 / ~600** |

**⚠️ DECISION-GATE FLAG:** the project is **~219 lines (~1.37×) over the
~600 budget.** PRD 2 (the plan+apply+waves+drift+checks half of the engine)
roughly doubled the codebase. This must be weighed at the 2026-08-05 decision
gate against the brief's "≤ ~600 lines / a second engineer understands it in
<1h" criterion — either **accept the overrun** as justified for a complete
TACO engine, or **trim** (candidates: the `deploy-detect` action's 11
block-style output declarations; merging `waves` into `deploy-detect`). Not
silently exceeded — surfaced per the PRD-2 plan.

(`scripts/tests/*` and all `repo-example-*` fixture content — including
`deploy.yml`/`drift.yml`/`preview.yml` and `tools/` — are excluded per the
scope note above.)
