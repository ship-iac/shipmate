# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`shipmate` is the primary code repo. The **plan of record** lives one level up in `../project/` — read `../project/prd-0-sample-repos.md` first (it carries all context + the contract), then the phase PRD you're working. Workspace topology is in `../CLAUDE.md`.

## What shipmate is

An open-source **TACO** (Terraform Automation and Collaboration) engine: GitHub Actions **composite actions + scripts** wrapping **Terramate CLI** and **OpenTofu**. No server, no database. Consumer repos hold IaC stacks + thin workflow YAML that reference shipmate's actions **by commit SHA** (not tag — consumer repos hold deploy credentials in later tiers).

The three `repo-example-{stacks,folders,workspaces}` sample repos (per PRD 0; null resources only, local state via GHA artifacts, zero cloud credentials) are simultaneously the dev substrate, shipmate's E2E CI, and generalization proof across the three common IaC repo layouts. **Any flavor-specific need must land as a shipmate input/feature — never as sample-repo patch code.**

## Core architecture (the big picture)

- **Fan-out orchestration:** one GHA job per **stack × environment**. Never `terramate run --parallel`. Plans fan out flat (read-only, order-free); applies run in **waves** = topological levels of the Terramate `after` DAG.
- **Checks-first UX:** every stack × env gets a `plan / <env> / <stack>` check (plan text in the check-run markdown, 65k cap per check → truncate with log link) and an `apply / <env> / <stack>` check created **pending** until that unit is applied. One aggregate gate: **`shipmate / checkmate`**. Gating is done by GitHub **branch protection** (required-checks), never by workflow logic.
- **Comment-ops:** `mate <verb> <env> [tag-filter]` in PR comments (e.g. `mate apply dev-eu`), Atlantis-style. Strict grammar, injection-safe, no shell interpolation of comment text. `apply` now; `plan`/`destroy` reserved.
- **Environments are a dynamic set:** one GitHub Environment per env (e.g. `dev-eu`) supplies `TF_VAR_env`, `TF_VAR_region`; a stack's env membership + workload come from Terramate **tags** (`env:dev-eu`, `workload:app`). **No env names in workflow YAML, ever.** Adding an env = create GitHub Environment + tag stacks. Prod is not special — just an environment with protection rules. Plan/apply env split (`dev-eu` / `dev-eu-apply`): plan envs allow all branches, no reviewers.

## Platform constraints that shape everything

- GHA cannot create jobs dynamically → waves are **pre-declared `wave0..wave7` jobs** with dynamic matrices. Matrix ≤ **256 cells** (fail loud in `detect`).
- Events created with `GITHUB_TOKEN` **never trigger other workflows** (anti-recursion). This is why PRD 3 needs a private **GitHub App** to mint a `workflow_dispatch` token.
- Check runs are updatable only by the app that created them; all workflows in a repo share the `github-actions` identity → cross-workflow check completion works. Check writes stay on `GITHUB_TOKEN` (`checks: write`); the App has **no** `checks` permission.
- Terramate `script` and `experimental run-graph` are experimental → **pin the CLI version** (`list --run-order` is the fallback).
- **Ordering must come from `run-graph`, not `list`:** `list` dependency filters track only data deps, not `after`/`before`. `list --changed` supplies only the changed *set*.
- **Wave computation:** compute topological levels over the **full** graph, then filter each level to the changed set. A naive edges∩changed intersection loses transitive ordering (A→B→C with only A and C changed). Empty filtered waves are normal — skip-propagation guard required (`if: ${{ !failure() && !cancelled() && needs.detect.outputs.waveN != '[]' }}`; GHA's default `success()` fails on skipped `needs`).

## Key invariants (don't break these)

- **Exact-plan applies:** apply the reviewed `.otplan` artifact from the plan run, never silently re-plan. Stale plan/state → fail safe ("saved plan is stale"), instruct re-plan.
- **TF_VAR fingerprint:** hash over `TF_VAR_*` values only, **excluding ephemeral credential env vars** (`AWS_*` etc.) — else every apply mismatches. Verify before apply; mismatch → fail with a diff of variable **names**, never values.
- **Missed-merge safety:** `deploy.yml` detects `--changed` against the **last successfully deployed ref** (tag/repo var, advanced at end of a green deploy), not the merge-point base — GHA keeps only the latest queued run per concurrency group. Also makes deploy idempotent under re-run.
- **Merge→PR mapping:** complete apply checks on the merged PR's head SHA via `GET /repos/{owner}/{repo}/commits/{sha}/pulls` (squash merges drop the PR head SHA from main).
- **Serialization:** per-env `concurrency` group shared between `deploy.yml` and `apply.yml`; exactly one TACO applies a given stack at any time.
- **Line-count budget:** ≤ ~600 bespoke script lines across the whole project. Maintain the ledger in this repo; **append per PRD, do not rewrite retroactively.** A second engineer walking the codebase in <1h is a decision-gate criterion.
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

Acceptance is driven by the criteria in each PRD (topological sort over the run-graph emitting expected waves; state round-trip via `actions/state` artifacts; failure fixtures toggling precondition failure / drift / stale plan). **No separate unit-test runner — the sample repos are the test harness.**

## Repo structure (as it lands per PRD)

- `actions/` — composite actions: `setup`, `state` (PRD 0), `plan-cell`, `summary` (PRD 1), `apply-cell` (PRD 2), `dispatch` (PRD 3).
- `scripts/` — `build-matrix` (PRD 1), `waves` (PRD 2, stdlib `graphlib.TopologicalSorter`).
- `CONTRACT.md` — the naming / env / tag contract. Line-count ledger. GitHub App manifest + registration script (PRD 3).
