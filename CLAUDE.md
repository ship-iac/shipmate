# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What shipmate is

An open-source **TACO** (Terraform Automation and Collaboration) engine: GitHub Actions **composite actions + scripts** wrapping **Terramate CLI** and **OpenTofu**. No server, no database. Consumer repos hold IaC stacks + thin workflow YAML that reference shipmate's actions **by commit SHA** (not tag — consumer repos hold deploy credentials).

> **Early development.** Some pieces referenced below (comment-ops, the GitHub App) are on the roadmap, not shipped. Action inputs, check names, and tag grammar may still change.

The three `repo-example-{stacks,folders,workspaces}` sample repos (null resources only, local state via GHA artifacts, zero cloud credentials) are simultaneously the dev substrate, shipmate's E2E CI, and the generalization proof across the three common IaC repo layouts. **Any flavor-specific need must land as a shipmate input/feature — never as sample-repo patch code.**

## Core architecture (the big picture)

- **Fan-out orchestration:** one GHA job per **stack × environment**. Never `terramate run --parallel`. Plans fan out flat (read-only, order-free); applies run in **waves** = topological levels of the Terramate `after` DAG.
- **Checks-first UX:** every stack × env gets a `plan / <env> / <stack>` check (plan text in the check-run markdown, 65k cap per check → truncate with log link) and an `apply / <env> / <stack>` check created **pending** until that unit is applied. One aggregate gate: **`shipmate / checkmate`**. Gating is done by GitHub **branch protection** (required-checks), never by workflow logic.
- **Comment-ops (roadmap):** `mate <verb> <env> [tag-filter]` in PR comments (e.g. `mate apply dev-eu`), Atlantis-style. Strict grammar, injection-safe, no shell interpolation of comment text.
- **Environments are a dynamic set:** one GitHub Environment per env (e.g. `dev-eu`) supplies `TF_VAR_env`, `TF_VAR_region`; a stack's env membership + workload come from Terramate **tags** (`env:dev-eu`, `workload:app`). **No env names in workflow YAML, ever.** Adding an env = create GitHub Environment + tag stacks. Prod is not special — just an environment with protection rules. Plan/apply env split (`dev-eu` / `dev-eu-apply`): plan envs allow all branches, no reviewers.

## Platform constraints that shape everything

- GHA cannot create jobs dynamically → waves are **pre-declared `wave0..wave7` jobs** with dynamic matrices. Matrix ≤ **256 cells** (fail loud in `detect`).
- Events created with `GITHUB_TOKEN` **never trigger other workflows** (anti-recursion). This is why the manual pre-merge apply path needs a private **GitHub App** to mint a `workflow_dispatch` token.
- Check runs are updatable only by the app that created them; all workflows in a repo share the `github-actions` identity → cross-workflow check completion works. Check writes stay on `GITHUB_TOKEN` (`checks: write`); the App has **no** `checks` permission.
- Terramate `script` and `experimental run-graph` are experimental → **pin the CLI version** (`list --run-order` is the fallback).
- **Ordering must come from `run-graph`, not `list`:** `list` dependency filters track only data deps, not `after`/`before`. `list --changed` supplies only the changed *set*.
- **Wave computation:** compute topological levels over the **full** graph, then filter each level to the changed set. A naive edges∩changed intersection loses transitive ordering (A→B→C with only A and C changed). Empty filtered waves are normal — skip-propagation guard required (`if: ${{ !failure() && !cancelled() && needs.detect.outputs.waveN != '[]' }}`; GHA's default `success()` fails on skipped `needs`).

## Key invariants (don't break these)

- **Exact-plan applies:** apply the reviewed `.otplan` artifact from the plan run, never silently re-plan. Stale plan/state → fail safe ("saved plan is stale"), instruct re-plan.
- **TF_VAR fingerprint:** sha256 over sorted `TF_VAR_*` **name→value** pairs **plus `TF_WORKSPACE` when set**, **excluding ephemeral credential env vars** (`AWS_*` etc.). plan-cell and apply-cell use the byte-identical algorithm (`scripts/plan-classify`). Verify before apply; mismatch → fail with a diff of variable **names**, never values. The env set must be identical at plan and apply — a set-but-empty `TF_VAR_*` is hashed and diverges from an absent one (folders inject nothing at *both* plan and apply).
- **Post-merge detect (exact-plan):** `deploy.yml`'s `deploy-detect` maps the merge commit → its PR head SHA and orders the reviewed `.otplan` artifacts whose `apply` check is still **pending** into waves; completed checks (pre-merge-applied, or no-op) are skipped → idempotent no-op. The pending apply checks ARE the work queue — **no "last deployed ref" diff** (Terramate has no deployment memory; the marker was only ever a substitute for this queue). A GHA-superseded deploy leaves its stacks pending+visible, recovered by re-running that deploy.
- **Merge→PR mapping:** complete apply checks on the merged PR's head SHA via `GET /repos/{owner}/{repo}/commits/{sha}/pulls` (squash merges drop the PR head SHA from main).
- **Serialization:** per-env `concurrency` group shared between `deploy.yml` and the manual apply path; exactly one apply runs against a given stack at any time.
- No org-specific values anywhere — everything via inputs/vars (the authz team is a GitHub team-slug **input**). Keep the "not affiliated with Terramate GmbH" note. Never vendor or modify Terramate source (MPL-2.0 imposes nothing on tooling that only invokes the binary).

## Commands (development + acceptance, run in a sample repo)

Sample repos use a **local backend** (`path = ".state/${var.env}/${var.region}/terraform.tfstate"`, OpenTofu ≥ 1.8 early var eval) and require `TF_VAR_env` / `TF_VAR_region` set.

```bash
# Enumerate stacks for an env (tag-driven; verify the tag model per flavor)
# On-disk tags use a slash — the conceptual "env:dev-eu" is stored as "env/dev-eu"
# (Terramate forbids ':' in tags and treats ':' in --tags as an AND operator).
terramate list --tags env/dev-eu

# Reproduce the DAG (stacks flavor: 5 levels dns→platform→{auth,workers}→app→{tenant-a,tenant-b})
terramate experimental run-graph

# Changed set for a matrix (ordering comes from run-graph, not this)
terramate list --changed

# Dry-run the pipeline (all flavors: init + plan; workspaces selects its env via
# the TF_WORKSPACE env var, not a script step)
terramate script run --dry-run plan

# Stale-codegen check (used by the detect job)
terramate generate --detailed-exit-code

# Fresh-clone smoke test, zero credentials
tofu init && tofu plan   # exit 0 no-change, exit 2 changes/drift

# Single stack, no recursion (what plan-cell / apply-cell run internally)
terramate script run --no-recursive -C <stack> plan
```

Acceptance is driven end to end against the sample repos: topological sort over the run-graph emitting expected waves; state round-trip via `actions/state` artifacts; failure fixtures toggling precondition failure / drift / stale plan. **The sample repos are the E2E test harness**; `scripts/tests/` holds the Python unit tests for the helper scripts.

## Repo structure

- `actions/` — composite actions: `setup`, `state`, `build-matrix`, `plan-cell`, `summary`, `apply-cell`, `drift-cell` (a `dispatch` action for the manual apply path is on the roadmap).
- `scripts/` — `build-matrix`, `plan-classify`, `waves` (stdlib `graphlib.TopologicalSorter`), `deploy-detect`, plus `scripts/tests/`.
- `CONTRACT.md` — the naming / env / tag / pinning contract.
- `docs/branch-protection.md` — how to configure the required-check gate.
