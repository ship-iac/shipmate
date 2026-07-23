# Recommended branch protection

shipmate does **no gating in workflow logic**. The apply-before-merge guarantee
is enforced entirely by GitHub branch protection requiring one aggregate check:

- **Require the status check `shipmate / gate`** (verbatim) — and *only*
  that check. The per-unit `plan / <env> / <stack>` and `apply / <env> / <stack>`
  checks come and go as stacks and environments change; requiring the single
  `shipmate / gate` roll-up means the required-checks list never needs
  editing when a stack or environment is added or removed.
- **Require branches to be up to date before merging** (strict). Plans run on
  the PR head ref, so this closes the plan-against-stale-base gap: a PR must be
  current with the base before it can merge.

`shipmate / gate` is created by `actions/summary` on the PR head commit and
resolves to:

| State | gate | Merge |
|-------|-----------|-------|
| A plan cell (or `detect`) failed | `failure` — "plan incomplete" | blocked |
| Plans succeeded, applies still pending | `pending` | blocked |
| Nothing left to apply | `success` | allowed |

`shipmate / gate` is a **commit status**, not a check-run (it is commit-scoped,
so a commit that carries two plan runs — draft→ready, or a rapid re-push —
cannot strand the gate in a stale check-suite). The required-check contract is
unchanged: a ruleset `required_status_checks` entry matches a commit status by
`context` exactly as it matches a check-run.

## Reproducible ruleset (GitHub Pro / Team / Enterprise, or a public repo)

```bash
gh api -X POST repos/<owner>/<repo>/rulesets --input - <<'JSON'
{
  "name": "shipmate-gate",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "required_status_checks",
      "parameters": {
        "required_status_checks": [ { "context": "shipmate / gate" } ],
        "strict_required_status_checks_policy": true
      } }
  ]
}
JSON
```

`strict_required_status_checks_policy: true` is the "require branches up to date"
setting above.

## Recipe: automerge after apply

Because the merge gate is the single `shipmate / gate` check, GitHub's
native auto-merge composes with shipmate for free — no engine configuration,
no extra workflow. Once auto-merge is armed on a PR, finishing the applies is
the last green check, so the PR merges itself:

1. **One-time repo setting:** allow auto-merge —
   `gh repo edit <owner>/<repo> --enable-auto-merge` (or Settings → General →
   "Allow auto-merge").
2. **Per PR:** review and approve, arm auto-merge
   (`gh pr merge <n> --auto --merge`, or the "Enable auto-merge" button), then
   comment `shipmate apply`. When every environment's applies complete,
   `shipmate / gate` flips to `success` and GitHub merges the PR.

Properties that fall out of the existing gate semantics:

- **Explicit environments still gate.** An environment listed in
  `global.shipmate.explicit_envs` is skipped by the bare `shipmate apply` and its
  apply checks stay pending — gate stays pending, so auto-merge waits
  until someone runs the targeted `shipmate apply <env>`. Arming auto-merge never
  weakens the apply-before-merge guarantee; it only removes the final click.
- **Stale bases don't sneak through.** With "require branches up to date"
  (strict), a base moved since the plans ran blocks the auto-merge until the
  branch is updated — and updating re-runs the plan on the new head, which
  resets gate to pending until the fresh plans are applied. The
  exact-plan invariant is preserved.
- **The post-merge deploy still runs.** GitHub performs the auto-merge as the
  user who armed it (not `GITHUB_TOKEN`), so the resulting push event triggers
  `deploy.yml` normally — which no-ops idempotently when everything was
  applied pre-merge.
- **Any merge method works.** Squash merges are fine: `deploy-detect` maps the
  merge commit back to the PR head SHA via the commit→PR association, not the
  commit graph.

## Note: free-tier private repos

Repository rulesets and classic branch protection require a paid plan
(Pro/Team/Enterprise) **for private repositories**, or a public repository.
On a free-tier private repo the required-check gate cannot be created at all.

This is purely a GitHub configuration constraint, not a shipmate one:
`actions/summary` still emits the correct `shipmate / gate` state in every
case — `pending` while apply checks are outstanding, `failure` ("plan
incomplete") when a plan cell fails, and `success` when nothing is left to
apply. shipmate's responsibility — producing a correct, stable, single
required status — holds regardless of plan; the ruleset above just enforces it
once the repo is public or on a paid plan.

## Upgrading

- **Gate is now a commit status — grant `statuses: write`.** The aggregate gate
  moved from the check-runs API to the commit-statuses API. Every consumer
  workflow **job** that runs `actions/summary` or `actions/gate-refresh` must now
  grant `statuses: write` (previously `checks: write`) — in the **same change**
  that bumps your pinned engine SHA, or the gate POST 403s and the required
  `shipmate / gate` status is never reported, blocking every PR. Recommended
  minimal grants:
  - `plan.yml` summary job (runs `actions/summary`): `statuses: write` +
    `checks: read` (the sticky comment lists the per-cell `plan` check-runs to
    link them) + `pull-requests: write` (the sticky comment itself).
  - `apply.yml` / apply-dispatch summary job (runs `actions/gate-refresh`):
    `statuses: write` + `checks: read` (it scans the `apply` check-runs).

  Keep `checks: read` — do not simply swap `checks: write` → `statuses: write`,
  or the check-runs the comment/scan read will 403 (a silent link degradation on
  the plan path; a stuck gate on the apply path). The ruleset required-check
  string does not change: `shipmate / gate` matches a status by context.
- **Required status check renamed.** If your branch protection currently
  requires the aggregate gate check under its pre-rename name, update it to
  require `shipmate / gate` instead — in the **same change** that bumps your
  pinned engine SHA. Otherwise GitHub keeps waiting on the old required check
  forever, and PRs can't merge even though the new engine is reporting
  `shipmate / gate`.
- **Plan workflow renamed `preview.yml` → `plan.yml`.** shipmate resolves a
  PR's reviewed plan by the workflow filename that produced it. A PR whose
  plan was produced by an old `preview.yml` run is not recognized after your
  workflow is renamed to `plan.yml` — push a commit to trigger a fresh
  `plan.yml` run and get it re-reviewed before `shipmate apply` or a
  merge-deploy will act on that PR.
