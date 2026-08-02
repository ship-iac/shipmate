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
shipmate:doctor -->`, upserted in place like the plan comment) combining seven
live probes — a missing or mis-pinned `shipmate / gate` rule on the default
branch (no active ruleset requiring it, or one that doesn't pin
`integration_id` to the shipmate App, or that isn't strict), a missing GitHub
Environment (`<env>` / `<env>-apply`) for a tagged-in environment, the
plan/apply environment protection shape (a plan environment must have no
approval-type protection rules — required reviewers or wait timers — and no
deployment branch policy; an apply environment with no approval rule is only a
note, and "no approval rule" is deliberately not "no protection rules": GitHub
synthesizes a `branch_policy` protection rule for any environment with a
deployment branch policy, and a branch policy is not a review), whether the
`shipmate-engine` environment exists and its deployment branch policy actually
names the default branch (see `docs/hardening.md` #16 and
`docs/github-app.md` §Key-exposure boundary — this is the probe that catches
a re-pin that never (re-)creates that environment, which would otherwise leave
the App key a repository secret again with nothing else to notice), engine
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
excluded). Only five of the seven probes can produce a finding from the plan
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
one is the environment-existence probe's finding) — the `shipmate-engine`
probe degrades the same way.

The environment probes cover only the environments of the stacks a given pull
request changed — the declared set comes from that commit's plan matrix — so
the report's all-clear line names the environments it actually probed instead
of implying the repository's environments are all sound. Separately, the report
states plainly when some of the commit's workflow runs had not finished yet,
and when the warnings harvest itself could not complete (or may be truncated by
GitHub's per-step annotation cap), rather than claiming a false all-clear.

The harvest is deliberately uncurated: it reports the annotations as GitHub
recorded them, including ones from the third-party actions the engine pins, so a
deprecation warning from a pinned action reads the same as a shipmate warning.
That is intended — a known-noise denylist would eventually swallow a real
warning — so treat an unfamiliar line as upstream's until you have checked.

The line you are most likely to meet is
`Input 'app-id' has been deprecated with message: Use 'client-id' instead.`,
once per token mint on the commit, from `actions/create-github-app-token`. **It
is upstream deprecation noise, not a setting to fix** — nothing in your
repository causes it and nothing you can configure removes it. Do not go looking
for a second App credential or change `SHIPMATE_APP_ID`; that is a dead end.

Engine releases from the one that introduced this note onward do not emit it:
their mints identify the App with `client-id` rather than the deprecated
`app-id` input. Only the input's *name* changed — the value threaded is still
the one numeric App id you set as `SHIPMATE_APP_ID`, because upstream passes
either input through unaltered as the JWT `iss` claim, which GitHub accepts as
the App id or the Client id. No second credential, and that same numeric id
keeps satisfying the gate ruleset's `integration_id` pin and the apply-check
author filter.

Expect the warnings to persist for a while regardless. Adoption is re-pin-only
and staggered, so a repository pinned to an earlier engine SHA keeps emitting
them until it re-pins; and the engine's own reusable workflows pin the composite
actions they call by SHA too, so the deploy and apply paths keep emitting them
until those internal pins are bumped. Seeing the line is not evidence that your
wiring is wrong.

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
read it. The same rendered report is also written to the run's job summary, so
that a GitHub API outage which loses the comment does not discard the probes.
That surface needs repository read access too, so it admits no one the comment
did not — but it is **not** as retractable. The comment is a single sticky one
you can edit or delete, and the next `shipmate doctor` overwrites it; a job
summary cannot be edited or redacted at all, and every past run keeps its own
copy for the repository's Actions retention window (90 days by default). So if a
report disclosed something you did not want recorded, deleting the comment is not
enough — delete the workflow runs that produced it, or shorten the retention
window.

On a repository whose pull requests are **public** you can add a second layer
by gating the `issue_comment` job itself on the same
`github.event.comment.author_association` values, or by keeping the repository
private. That is belt and braces over the engine's own gate, not the primary
mitigation. Note also that `app/manifest.json` declares
`"public": false`: the shipmate App is registered per organization and intended
for repositories the installing organization controls.

## Environment setup

Every logical environment needs a GitHub Environment pair (`<env>`,
`<env>-apply`), plus the one fixed `shipmate-engine` environment that holds
the App key (`docs/github-app.md`). None of the three is ever named in
workflow YAML: `<env>`/`<env>-apply` are read from Terramate stack tags at
runtime, and `shipmate-engine` is referenced only inside shipmate's own
reusable workflows, never a consumer's (CONTRACT.md's one carve-out to "no
env names in workflow YAML — ever").

Create each with `gh api -X PUT repos/<owner>/<repo>/environments/<name>`,
then set protection rules from Settings → Environments → `<name>` (or the API):

- **`shipmate-engine`** (create once, regardless of how many env tiers you
  run): deployment branch policy **Selected branches**, naming exactly the
  default branch. No reviewers — this environment's job is scoping the App
  key to trusted workflow runs, not gating a human decision, and reviewers
  here would stall every plan and apply run waiting for an approval nobody is
  meant to give. `shipmate doctor` checks both that this environment exists
  and that its policy actually names the default branch.
- **`<env>`** (plan): no reviewers, no deployment branch policy at all — plan
  runs against a pull request head ref, which any restriction here blocks
  outright (`docs/hardening.md` §8; `shipmate doctor` warns on either).

`<env>-apply` splits by tier, and this is the split `docs/hardening.md`
describes at the credential level (§6–9) restated as environment settings:

- **dev / staging — branch policy only, self-service.** Deployment branch
  policy restricted to the default branch (closes the direct-branch-secret
  path, `docs/hardening.md` #17); no required reviewers, so `shipmate apply`
  proceeds without a human in the loop. Deliberate: these tiers exist so a
  team can self-serve, and their blast radius doesn't warrant a reviewer.
- **prod — branch policy *and* required reviewers *and* "Prevent
  self-review".** Same branch policy, plus required reviewers (a team, not
  one person) with self-review prevented (`docs/hardening.md` #6) — the one
  gate an App token cannot forge, since a reviewer decision is a human
  action a minted token cannot take. List `prod` in
  `global.shipmate.explicit_envs` too, so a bare `shipmate apply` skips it and
  it is only ever reached via the targeted `shipmate apply prod` (which then
  pauses for the environment reviewer).

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
  `apply / <stack> / <env>` checks that the App-authored `apply-cell` cannot
  complete (different identity — check-run completion is scoped to the
  creating app), and (b) an existing `shipmate / gate` status authored by the
  old identity, which no longer satisfies the newly-pinned
  `integration_id`. Push a commit (or re-run `plan.yml`) on each open PR after
  upgrading so a fresh, App-authored set of checks and gate status is
  produced.
- **Apply check-name field order flipped — every PR planned before the bump
  needs a new commit, and a re-plan is not enough.** The apply check is now
  `apply / <stack> / <env>` (was `apply / <env> / <stack>`). Nothing in branch
  protection references it (only `shipmate / gate` is required), so no ruleset
  edit is needed — but old-order checks left on a head SHA break **both**
  directions, and a re-plan fixes neither, because it adds the new names
  *alongside* the old ones on the same commit:

  - **Old-order checks still pending → the gate never greens.** `apply-gate`
    treats every `apply / `-prefixed check on the SHA as part of the work queue,
    and the new engine only ever completes the new-order names, so the old
    pending ones stay pending forever.
  - **Old-order checks already complete → the post-merge deploy re-queues work
    that is already applied.** This one has no pre-merge signal at all: the gate
    is green and the PR looks finished. On merge, `deploy-detect` reconstructs
    the new-order name for each reviewed cell, finds none of them among the
    completed checks, and treats every applied cell as pending. Each re-apply
    then trips the stale-plan fail-safe (state has moved), so the deploy on
    `main` goes red per stack instead of no-opping. Nothing is applied twice —
    the fail-safe holds — but the deploy needs recovery.

  **Do this:** on every open PR that already has apply checks, push a commit
  (any commit) so the fan-out lands on a fresh head SHA, then re-plan and, if it
  was applied pre-merge, re-apply under the new engine. Note that a check run
  **cannot be deleted or dismissed** — the Checks API offers only create, update,
  get, list and rerequest — so the only alternative to a fresh commit is
  completing the stale check-run ids yourself with a shipmate-App installation
  token (a check run is updatable only by the app that created it, and these were
  App-authored). If a deploy already went red this way, the stacks are in fact
  applied: re-plan on a follow-up PR, get "no changes" checks, and the next
  deploy no-ops green. New PRs are unaffected.
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
