# Recommended branch protection

shipmate does **no gating in workflow logic**. The apply-before-merge guarantee
is enforced entirely by GitHub branch protection requiring one aggregate check:

- **Require the status check `shipmate / checkmate`** (verbatim) — and *only*
  that check. The per-unit `plan / <env> / <stack>` and `apply / <env> / <stack>`
  checks come and go as stacks and environments change; requiring the single
  `shipmate / checkmate` roll-up means the required-checks list never needs
  editing when a stack or environment is added or removed.
- **Require branches to be up to date before merging** (strict). Plans run on
  the PR head ref, so this closes the plan-against-stale-base gap: a PR must be
  current with the base before it can merge.

`shipmate / checkmate` is created by `actions/summary` on the PR head commit and
resolves to:

| State | checkmate | Merge |
|-------|-----------|-------|
| A plan cell (or `detect`) failed | `failure` — "preview incomplete" | blocked |
| Plans succeeded, applies still pending | `queued` (pending) | blocked |
| Nothing left to apply | `success` | allowed |

## Reproducible ruleset (GitHub Pro / Team / Enterprise, or a public repo)

```bash
gh api -X POST repos/<owner>/<repo>/rulesets --input - <<'JSON'
{
  "name": "shipmate-checkmate-gate",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "required_status_checks",
      "parameters": {
        "required_status_checks": [ { "context": "shipmate / checkmate" } ],
        "strict_required_status_checks_policy": true
      } }
  ]
}
JSON
```

`strict_required_status_checks_policy: true` is the "require branches up to date"
setting above.

## Recipe: automerge after apply

Because the merge gate is the single `shipmate / checkmate` check, GitHub's
native auto-merge composes with shipmate for free — no engine configuration,
no extra workflow. Once auto-merge is armed on a PR, finishing the applies is
the last green check, so the PR merges itself:

1. **One-time repo setting:** allow auto-merge —
   `gh repo edit <owner>/<repo> --enable-auto-merge` (or Settings → General →
   "Allow auto-merge").
2. **Per PR:** review and approve, arm auto-merge
   (`gh pr merge <n> --auto --merge`, or the "Enable auto-merge" button), then
   comment `shipmate apply`. When every environment's applies complete,
   `shipmate / checkmate` flips to `success` and GitHub merges the PR.

Properties that fall out of the existing gate semantics:

- **Explicit environments still gate.** An environment listed in
  `global.shipmate.explicit_envs` is skipped by the bare `shipmate apply` and its
  apply checks stay pending — checkmate stays pending, so auto-merge waits
  until someone runs the targeted `shipmate apply <env>`. Arming auto-merge never
  weakens the apply-before-merge guarantee; it only removes the final click.
- **Stale bases don't sneak through.** With "require branches up to date"
  (strict), a base moved since the plans ran blocks the auto-merge until the
  branch is updated — and updating re-runs the preview on the new head, which
  resets checkmate to pending until the fresh plans are applied. The
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
`actions/summary` still emits the correct `shipmate / checkmate` state in every
case — `pending` while apply checks are outstanding, `failure` ("preview
incomplete") when a plan cell fails, and `success` when nothing is left to
apply. shipmate's responsibility — producing a correct, stable, single
required status — holds regardless of plan; the ruleset above just enforces it
once the repo is public or on a paid plan.
