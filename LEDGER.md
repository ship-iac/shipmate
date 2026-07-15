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

**Totals:** PRD 0 = 33 · PRD 1 = 341 · **running = 374 / ~600.**
(`scripts/tests/test_build_matrix.py` and all `repo-example-*` fixture content are excluded per the scope note above.)
