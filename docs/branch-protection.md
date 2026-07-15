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

## POC note (free-tier private repos)

Repository rulesets and classic branch protection require a paid plan
(Pro/Team/Enterprise) **for private repositories**, or a public repository.
The POC sample repos (`repo-example-*`) are private on the free tier, so the
required-check gate could not be created live during PRD-1 acceptance.

What was verified live instead: `actions/summary` emits the correct
`shipmate / checkmate` state in every case — `pending` while apply checks are
outstanding, `failure` ("preview incomplete") when a plan cell fails, and
`success` when nothing is left to apply. The gate *enforcement* is a pure
GitHub configuration step (the ruleset above); shipmate's responsibility —
producing a correct, stable, single required status — is proven. Real
consumer repos, which hold deploy credentials and are on paid plans (or are
public), apply the ruleset above unchanged.
