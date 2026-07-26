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

## Doctor: settings-drift warnings and the `shipmate doctor` report

`actions/summary` runs `scripts/doctor` on every plan run and emits its
findings as workflow annotations titled `shipmate doctor`
(`::warning title=shipmate doctor::<text>` / `::notice title=shipmate
doctor::<text>`) — read-only, never blocking. Comment `shipmate doctor` on a
pull request for a consolidated report: a sticky comment (marker `<!--
shipmate:doctor -->`, upserted in place like the plan comment) combining six
live probes — a missing or mis-pinned `shipmate / gate` rule on the default
branch (no active ruleset requiring it, or one that doesn't pin
`integration_id` to the shipmate App, or that isn't strict), a missing GitHub
Environment (`<env>` / `<env>-apply`) for a tagged-in environment, the
plan/apply environment protection shape (a plan environment must have no
approval-type protection rules — required reviewers or wait timers — and no
deployment branch policy; an apply environment with no approval rule is only a
note, and "no approval rule" is deliberately not "no protection rules": GitHub
synthesizes a `branch_policy` protection rule for any environment with a
deployment branch policy, and a branch policy is not a review), engine
action-pin freshness in the consumer's own workflow files (read at the commit
under examination, so the pull request that bumps a stale pin is not itself
reported stale, and restricted to pins of the engine's own repository, which
the probe learns at runtime from the running action rather than from any
hardcoded slug — another org's shared action is not shipmate's to report on),
whether the configured approvers team resolves in the org, and
whether the shipmate App installation still grants the manifest's full
permission set — with the warning and failure annotations GitHub already
recorded on this commit's workflow runs (shipmate's own and any other
Actions workflow run on that commit; third-party-app-authored check runs are
excluded). Only four of the six probes can produce a finding from the plan
path's own `annotate`-mode run (`actions/summary`): the approvers-team probe
needs the `SHIPMATE_TEAM` environment variable, which the plan path does
not supply, and
the App-permission-drift probe only has something to report when a
full-manifest permission-set mint was actually attempted, which only
`shipmate doctor` does — both are effectively comment-path-only. `doctor`
degrades to a "could not verify" **warning** naming the probe that was skipped
on an API error, and always exits 0, so a probe failure (for example, the App
token lacking read access to `rules/branches` or `environments` — both token
mints that drive doctor also request Actions read, which the environment reads
need on some configurations) never fails the plan run. The engine-pin probe
degrades to a **note** instead: its `.github/workflows` read legitimately fails
on the pull request that first adds that directory, which is the first
`shipmate doctor` any consumer runs, and it also declines rather than guessing
when it cannot tell which repository the engine is or which commit to read.
An environment that exists but whose settings cannot be read is likewise a
note naming it, rather than the silence a nonexistent environment gets (that
one is the environment-existence probe's finding).

The environment probes cover only the environments of the stacks a given pull
request changed — the declared set comes from that commit's plan matrix — so
the report's all-clear line names the environments it actually probed instead
of implying the repository's environments are all sound. Separately, the report
states plainly when some of the commit's workflow runs had not finished yet,
and when the warnings harvest itself could not complete (or may be truncated by
GitHub's per-step annotation cap), rather than claiming a false all-clear.
`shipmate doctor` never blocks the gate, and it needs no team membership,
review or reviewed plan, unlike `shipmate apply` — but because it reports this
repository's own settings, the engine limits it to organization members and
repository collaborators (below).

### Who can ask for the report, and who can see it

The report is an inventory of what is *not* configured: that no ruleset
requires `shipmate / gate` on the default branch, that `<env>-apply` has no
approval rule so pre-merge applies to it are unreviewed, which approvers
team is configured and whether it resolves, and whether the App installation is
missing permissions the manifest declares.

**What the engine enforces.** `shipmate doctor` runs only for a commenter
GitHub classifies as `OWNER`, `MEMBER` or `COLLABORATOR` in
`github.event.comment.author_association` — organization members and repository
collaborators. Any other commenter gets a single-line refusal saying exactly
that; no App token is minted and no probe runs. `CONTRIBUTOR` is deliberately
excluded — its only signal is one merged pull request, not a standing
relationship to the repository. The gate fails closed: an association the engine
does not recognize, or an event carrying no comment context at all, counts as no
access. Nothing is required of you to adopt it beyond re-pinning the engine
SHA — it adds no action input and no workflow `permissions:` entry.

**What the engine does not enforce.** It does **not** check write access.
`author_association` is GitHub's own classification of the author's relationship
to the repository, not a permission lookup, and it is wrong in both directions:

- a collaborator invited with only the **Read** role is classified
  `COLLABORATOR`, and an organization member whose base repository permission is
  **None** is classified `MEMBER` — both are admitted to the report despite
  having no write access. If that matters for your repository, add the
  workflow-level layer below, or do not grant read-only collaborator access to
  people who should not see the settings inventory;
- conversely, an organization member whose membership is **private** is reported
  as `NONE` and will be refused unless they are also a direct collaborator; make
  the membership public or add the person as a collaborator.

What the gate does buy is that an account with no declared relationship to the
repository — a drive-by fork author on a public repository — cannot obtain the
report. Beyond that: `shipmate help` stays open to every commenter (it lists the
verbs and discloses nothing about the repository, and a newcomer whose setup is
broken still needs it), and the report is an ordinary pull request comment, so
once someone with access asks for it, everyone who can read the pull request can
read it.

On a repository whose pull requests are **public** you can add a second layer
by gating the `issue_comment` job itself on the same
`github.event.comment.author_association` values, or by keeping the repository
private. That is belt and braces over the engine's own gate, not the primary
mitigation. Note also that `app/manifest.json` declares
`"public": false`: the shipmate App is registered per organization and intended
for repositories the installing organization controls.

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
