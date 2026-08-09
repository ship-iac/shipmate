# Recommended branch protection

This page is the settings shipmate needs to enforce apply-before-merge.
`docs/hardening.md` is its companion: who can make the engine act at all, and
the settings that bound that.

shipmate does **no gating in workflow logic**. The apply-before-merge guarantee
is enforced entirely by GitHub branch protection requiring one aggregate check:

- **Require the status check `shipmate / gate`** (verbatim) — and *only*
  that check. The per-unit `<stack> / <env>` (plan) and `apply / <stack> / <env>`
  checks come and go as stacks and environments change; requiring the single
  `shipmate / gate` roll-up means the required-checks list never needs
  editing when a stack or environment is added or removed.
- **Require branches to be up to date before merging** (strict). Plans run
  against the pull request's **branch tip**, not against a merge commit, so a
  plan can describe a base the branch has not seen. Strict protection makes a
  pull request current with the base **before it can merge** — it gates merging
  and nothing else. It does not gate the pre-merge apply path: a `shipmate apply
  <env>` run from a stale branch applies the plan as reviewed, so a stack that
  was updated and merged to main since this branch forked is rolled back in real
  infrastructure. **Update the branch before running a pre-merge apply.**

`shipmate / gate` is created by `actions/summary` on the PR head commit and
resolves to:

| State | gate | Merge |
|-------|-----------|-------|
| `detect` did not succeed | `failure` — "change detection did not succeed" | blocked |
| A plan cell failed | `failure` — "plan incomplete" | blocked |
| The plan job was cancelled | no status written at all | blocked (the required check never arrives) |
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
      } },
    { "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "require_code_owner_review": true,
        "dismiss_stale_reviews_on_push": true,
        "require_last_push_approval": true,
        "required_review_thread_resolution": false
      } },
    { "type": "non_fast_forward" },
    { "type": "deletion" }
  ]
}
JSON
```

`strict_required_status_checks_policy: true` is the "require branches up to date"
setting above.

The `pull_request` rule is what `shipmate doctor`'s review-rule probe checks, and
`require_code_owner_review` is the half of it a leaked App private key cannot
satisfy — an App cannot be a CODEOWNER. It only bites for changed files a
`CODEOWNERS` entry actually covers, so keep an entry covering the paths the IaC
and the workflows live in. A sole maintainer who wants
`required_approving_review_count: 0` should read `docs/hardening.md` §3–5 before
changing it — that page has the reasoning for each of these rules, and this block
is just its recommendation made pasteable.

`<SHIPMATE_APP_ID>` is the numeric GitHub App id — the same value stored in
the `SHIPMATE_APP_ID` repo/org variable (see `docs/github-app.md` step 2).
Pinning `integration_id` makes the required check match a `shipmate / gate`
status **only** when it was authored by that specific App installation: a
status of the same name posted by `GITHUB_TOKEN` (the `github-actions`
identity) or by any other GitHub App does not satisfy the rule and the PR
stays blocked. Without this pin, the ruleset only matches on `context` and
any identity that can post a commit status with that exact context string
can satisfy the required check.

`shipmate doctor`'s report and its authorization are in `troubleshooting.md`.

**Environment setup** lives in `getting-started.md`: §Required — plan →
§Environments for this tier has the `<env>` plan environments and
`shipmate-engine`, and §Required — apply → §Environment setup has the
`<env>-apply` environments and their dev/staging vs prod split.

## Review policy for `shipmate apply`

shipmate delegates review policy to the branch ruleset. `shipmate apply`
blocks when GitHub's `reviewDecision` is `REVIEW_REQUIRED` or
`CHANGES_REQUESTED`; it imposes no approval rule of its own. (If the decision
cannot be determined at all — a wiring failure, never a policy state — apply
fails closed rather than proceeding unreviewed.)

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
  in the ruleset (`getting-started.md` §Required — apply → §Environment setup has
  those settings). This gates both pre-merge `shipmate apply <env>` and the
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

Re-pinning, Dependabot, and the past migrations that needed more than a pin
bump: [`upgrading.md`](upgrading.md).
