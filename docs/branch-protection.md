# Recommended branch protection

shipmate does **no gating in workflow logic**. The apply-before-merge guarantee
is enforced entirely by GitHub branch protection requiring one aggregate check:

- **Require the status check `shipmate / gate`** (verbatim) — and *only*
  that check. The per-unit `<stack> / <env>` (plan) and `apply / <env> / <stack>`
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
        "required_status_checks": [ { "context": "shipmate / gate", "integration_id": <SHIPMATE_APP_ID> } ],
        "strict_required_status_checks_policy": true
      } }
  ]
}
JSON
```

`strict_required_status_checks_policy: true` is the "require branches up to date"
setting above.

`<SHIPMATE_APP_ID>` is the numeric GitHub App id — the same value stored in
the `SHIPMATE_APP_ID` repo/org variable (see `docs/github-app.md` step 2).
Pinning `integration_id` makes the required check match a `shipmate / gate`
status **only** when it was authored by that specific App installation: a
status of the same name posted by `GITHUB_TOKEN` (the `github-actions`
identity) or by any other GitHub App does not satisfy the rule and the PR
stays blocked. Without this pin, the ruleset only matches on `context` and
any identity that can post a commit status with that exact context string
can satisfy the required check.

## Doctor: settings-drift warnings in the sticky comment

`actions/summary` runs `scripts/doctor` on every plan run and appends its
findings to the sticky comment under a `### doctor` heading — read-only,
never blocking. It flags a missing or mis-pinned `shipmate / gate` rule on
the default branch (no active ruleset requiring it, or one that doesn't pin
`integration_id` to the shipmate App, or that isn't strict) and any missing
GitHub Environment (`<env>` / `<env>-apply`) for a tagged-in environment.
Doctor degrades to a `could not verify` note on an API error and always
exits 0, so a probe failure (for example, the App token lacking read access
to `rules/branches` or `environments`) never fails the plan run itself.

## Review policy for `shipmate apply`

shipmate delegates review policy to the branch ruleset. `shipmate apply`
blocks only when GitHub's `reviewDecision` is `REVIEW_REQUIRED` or
`CHANGES_REQUESTED`; it imposes no approval rule of its own.

- **Sole-maintainer mode** (`required_approving_review_count: 0`): `shipmate
  apply` needs no approving review (a one-person repo can never self-approve
  on GitHub) — but a `CHANGES_REQUESTED` review still blocks apply until
  resolved.
- **Team mode** (`required_approving_review_count` ≥ 1 and/or code-owner
  review): GitHub enforces the approval count / CODEOWNERS / last-push-approval,
  and `shipmate apply` stays blocked until `reviewDecision` clears. No shipmate
  config — set it on the ruleset (the `pull_request` rule).
- **Per-environment approval** (e.g. dev applies freely, prod needs a human):
  configure **required reviewers on the `<env>-apply` GitHub Environment**, not
  in the ruleset. This gates both pre-merge `shipmate apply <env>` and the
  post-merge `deploy.yml` apply, since both run against `<env>-apply`.
  - Deployment approvals differ from PR reviews: a reviewer **can approve
    their own deployment** by default, so a sole maintainer still gets a
    confirm-step on prod. Tick "Prevent self-review" on the environment for
    genuine four-eyes once there's a team.
  - Pair a reviewer-gated production env with
    `global.shipmate.explicit_envs` so the bare `shipmate apply` skips it and
    it is only ever applied via the targeted `shipmate apply <env>` (which
    then pauses for the environment reviewer).
- **Private-repo caveat:** GitHub Environment protection rules (required
  reviewers) are free on public repos but require GitHub Pro/Team/Enterprise on
  private repos — the same class of constraint as the ruleset note in
  "free-tier private repos" below.

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

- **Flip `integration_id` in the same change as the engine-SHA bump.** Move
  the ruleset's required-check `integration_id` from the `github-actions`
  identity (`15368` on github.com) to the shipmate App's numeric id
  (`SHIPMATE_APP_ID`) at the same time you bump the pinned engine SHA to a
  build that authors the gate via the App. Landing the SHA bump first (or the
  `integration_id` flip first) leaves a window where the writer and the
  pinned identity disagree and every `shipmate / gate` status is rejected as
  non-satisfying, blocking all merges until both land together.
- **Open PRs need a fresh commit or re-plan after the flip.** A PR opened
  before the upgrade may be carrying: (a) `github-actions`-authored pending
  `apply / <env> / <stack>` checks that the App-authored `apply-cell` cannot
  complete (different identity — check-run completion is scoped to the
  creating app), and (b) an existing `shipmate / gate` status authored by the
  old identity, which no longer satisfies the newly-pinned
  `integration_id`. Push a commit (or re-run `plan.yml`) on each open PR after
  upgrading so a fresh, App-authored set of checks and gate status is
  produced.
- **Gate-writer jobs no longer need `statuses: write` / `checks: write` on
  `GITHUB_TOKEN`.** The App manifest now carries both scopes, so
  `actions/summary` / `actions/gate-refresh` mint their own installation
  token instead of relying on the calling job's `GITHUB_TOKEN` permissions.
  Remove any `statuses: write` / `checks: write` grant from jobs that run
  these actions, and thread `app-id` + `private-key` into the `with:` of each
  call (for a reusable-workflow caller job, `secrets: inherit` covers it, as
  long as the called workflow declares `SHIPMATE_APP_PRIVATE_KEY` under
  `on.workflow_call.secrets`). A leftover `GITHUB_TOKEN` grant is stale, not
  harmful by itself, but remove it — the writer step doesn't use it, and it
  needlessly widens the default token's scope.
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
