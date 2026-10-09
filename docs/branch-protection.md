# Recommended branch protection

This page is the settings shipmate needs to enforce apply-before-merge.
`docs/hardening.md` is its companion: who can make the engine act at all, and
the settings that bound that.

shipmate does no gating in workflow logic. The apply-before-merge guarantee
is enforced entirely by GitHub branch protection requiring one aggregate check:

- **Require the status check `shipmate / gate` (verbatim), and only that check.**
  The per-unit `shipmate / <stack> / <env>` (plan) and
  `apply / <stack> / <env>` checks come and go as stacks and environments
  change. Requiring the single
  `shipmate / gate` roll-up means the required-checks list never needs
  editing when a stack or environment is added or removed.
- **Require branches to be up to date before merging (strict).** Plans run
  against the pull request's branch tip, not against a merge commit, so a
  plan can describe a base the branch has not seen. Strict protection makes a
  pull request current with the base before it can merge. It gates merging
  and nothing else. It does not gate the pre-merge apply path: a `shipmate apply
  <env>` run from a stale branch applies the plan as reviewed, so a stack that
  was updated and merged to main since this branch forked is rolled back in real
  infrastructure. Update the branch before running a pre-merge apply.

`shipmate / gate` is created by `actions/summary` on the PR head commit and
resolves to:

| State | gate | Merge |
|-------|-----------|-------|
| `detect` failed with an `::error::` line (a refusal or a failed tool or API call) | `failure` — "detect failed: <first `::error::` line>", cut to 140 characters | blocked |
| `detect` failed without an `::error::` line (a fmt or codegen failure, a crash with none) | `failure` — "change detection did not succeed" | blocked |
| A plan cell failed | `failure` — "plan incomplete" | blocked |
| The plan job was cancelled | no status written at all | blocked (the required check never arrives) |
| Plans succeeded, applies still pending | `pending` | blocked |
| Nothing left to apply | `success` | allowed |

`shipmate / gate` is a commit status, not a check-run (it is commit-scoped,
so a commit that carries two plan runs — draft→ready, or a rapid re-push —
cannot strand the gate in a stale check-suite). A ruleset
`required_status_checks` entry matches a commit status by `context` exactly as
it matches a check-run.

## Reproducible ruleset (GitHub Pro / Team / Enterprise, or a public repo)

Create it after the pull request adding `.github/workflows/shipmate.yml` merges:
before then no pull request can produce `shipmate / gate`, so the ruleset blocks
the first one.

Whether a `shipmate-gate` ruleset exists decides the command. A
`scripts/onboard` run may have created one carrying the gate rule alone, and a
`POST` under a name already taken answers HTTP 422. Read its id first; it is
empty when no such ruleset exists:

```bash
id=$(gh api repos/<owner>/<repo>/rulesets \
  --jq '.[] | select(.name == "shipmate-gate") | .id')
```

Write the body outside the checkout. An untracked file left there shows up as
something to commit and trips `git-untracked`
([`getting-started.md`](getting-started.md) §Before you start):

```bash
ruleset="${TMPDIR:-/tmp}/ruleset.json"
cat > "$ruleset" <<'JSON'
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

This body is the team posture. For a single maintainer, set
`required_approving_review_count` to `0`, turn `require_last_push_approval` off,
and narrow `CODEOWNERS`, which keeps code-owner review on (the single-maintainer
paragraph below has why). Turning `require_code_owner_review` off instead makes
`shipmate doctor` warn on every run; `docs/hardening.md` §3–5 names the cost.

When `$id` is non-empty, replace that ruleset by its id. The `PUT` replaces the
ruleset with the body, so carry any bypass actors it already holds into the
body as its `bypass_actors` array first. Read them with:

```bash
gh api "repos/<owner>/<repo>/rulesets/$id" --jq .bypass_actors
```

Then send the body:

```bash
gh api -X PUT "repos/<owner>/<repo>/rulesets/$id" --input "$ruleset"
```

`scripts/onboard` checks only the `shipmate / gate` entry, so its next run
reports the widened ruleset `ok` while that entry keeps the App's
`integration_id` and `strict_required_status_checks_policy: true`.

When `$id` is empty, create the ruleset:

```bash
gh api -X POST repos/<owner>/<repo>/rulesets --input "$ruleset"
```

`strict_required_status_checks_policy: true` is the "require branches up to date"
setting above.

The `pull_request` rule is what `shipmate doctor`'s review-rule probe checks, and
`require_code_owner_review` is the half of it a leaked App private key cannot
satisfy — an App cannot be a CODEOWNER. It only bites for changed files a
`CODEOWNERS` entry actually covers, so keep an entry covering the paths the IaC
and the workflows live in.

**A single maintainer cannot merge with `require_last_push_approval` on, at
any approval count.** The pusher cannot approve their own last push, so even at
`required_approving_review_count: 0` nobody is left to approve, and `shipmate
doctor` warns. `require_code_owner_review` blocks the same way where
`CODEOWNERS` covers the changed files: the author cannot approve their own pull
request, and an App cannot be a code owner. The sole-maintainer posture is
three values: `required_approving_review_count: 0`, `require_last_push_approval`
off, and `require_code_owner_review` off or a narrow `CODEOWNERS` — covering
`/.github/workflows/` alone, say, so ordinary IaC pull requests need no
code-owner approval. The alternative is a bypass actor on the ruleset, which
spends exactly the control a leaked App key cannot get past.

**A narrow `CODEOWNERS` leaves the environment table under ordinary review.**
The role a cell assumes is a line in `.github/shipmate-config.yml` on the default
branch, not a GitHub Environment variable, so changing it is a pull request
rather than a repository-settings change. Under a `CODEOWNERS` covering
`/.github/workflows/` alone that pull request needs no code-owner approval — the
rule is a no-op for changed files with no owner, and `/.github/shipmate-config.yml` is
not under `/.github/workflows/`. Nothing is bypassed: the table takes effect only
once merged to the default branch, and every other control still applies. What
moved is the bar for naming a role, from settings access to ordinary review. Add
`/.github/shipmate-config.yml` to `CODEOWNERS` if you want the code-owner half on it.

**If you narrow `CODEOWNERS`, land that on its own pull request first.** GitHub
evaluates `CODEOWNERS` from the pull request's base branch, so a narrowing
committed alongside the change it is meant to unblock does not apply to that
pull request — the old ownership still decides, and the pull request stays
unmergeable. This bites precisely on a `CODEOWNERS` covering
`/.github/workflows/`, because the pull requests it blocks are the ones that
edit a workflow — an engine re-pin, say. It
also has a floor: the narrowing is itself mergeable only because
`.github/CODEOWNERS` is not under a path it owns. A `CODEOWNERS` entry covering
`/.github/` — or the file's own path — owns the fix, and then a bypass actor is
the only way out. Before changing `required_approving_review_count` to `0`, read
`docs/hardening.md` §3–5. That page has the reasoning for each of these rules;
this block is its recommendation made pasteable.

`<SHIPMATE_APP_ID>` is the numeric GitHub App id — the same value stored in
the `SHIPMATE_APP_ID` repo/org variable (see `docs/github-app.md` step 2).
Pinning `integration_id` makes the required check match a `shipmate / gate`
status only when it was authored by that specific App installation: a
status of the same name posted by `GITHUB_TOKEN` (the `github-actions`
identity) or by any other GitHub App does not satisfy the rule and the PR
stays blocked. Without this pin, the ruleset only matches on `context` and
any identity that can post a commit status with that exact context string
can satisfy the required check.

`shipmate doctor`'s report and its authorization are in `troubleshooting.md`.

**Environment setup.** `getting-started.md` holds the environment settings:
§Required — plan → §Environments for this tier has the `<env>-plan` environments
and `shipmate-engine`, and §Required — apply → §Environment setup has the
`<env>-apply` environments.

## Review policy for `shipmate apply`

shipmate delegates review policy to the branch ruleset. `shipmate apply`
blocks when GitHub's `reviewDecision` is `REVIEW_REQUIRED` or
`CHANGES_REQUESTED`; it imposes no approval rule of its own. (If the decision
cannot be determined at all — a wiring failure, never a policy state — apply
fails closed rather than proceeding unreviewed.)

- **Sole-maintainer mode** (`required_approving_review_count: 0`, with
  `require_last_push_approval` off; §Reproducible ruleset has the three values
  and why): `shipmate apply` needs no approving review (a one-person repo can never self-approve
  on GitHub) — but a `CHANGES_REQUESTED` review still blocks apply until
  resolved. `shipmate doctor` names each gated environment when it can read
  the default branch's table, in a note while code-owner review is on and in a
  warning while it is off: `gated` can only relax an existing review
  requirement, never create one, so a gated environment is held only where a
  code-owner review is required for the changed files.
- **Team mode** (`required_approving_review_count` ≥ 1 and/or code-owner
  review): GitHub enforces the approval count / CODEOWNERS / last-push-approval.
  `shipmate apply` stays blocked until `reviewDecision` clears — for every
  environment, unless some are exempted (next bullet). No shipmate
  config — set it on the ruleset (the `pull_request` rule).
- **Team mode with named environments exempted** (`gated: false`): the
  ruleset requirement is repository-wide, so `gated: false` on an
  environment's entry in `.github/shipmate-config.yml` lets `shipmate apply` apply
  that environment without an approving review while the merge still needs one
  ([`getting-started.md`](getting-started.md) §"Applying chosen environments
  without an approving review"). What it exempts and what still blocks an apply
  is in `../CONTRACT.md` §Comment-ops, and what it costs against the
  deployment-side gate in `hardening.md` §3–5.
- **Per-environment approval** — which environments require a human is your
  policy to set, per environment (production only, every apply environment, or
  anything between; `hardening.md` #6 states the trade-off). Configure it with
  required reviewers on the `<env>-apply` GitHub Environment, not in the ruleset
  (`getting-started.md` §Required — apply → §Environment setup has those
  settings). This gates both pre-merge `shipmate apply <env>` and the
  post-merge `deploy` job's apply, since both run against the apply environment. An
  env holding `shared: true` cannot be gated this way (`hardening.md` §6).
  - Deployment approvals differ from PR reviews: a reviewer can approve
    their own deployment by default, so a sole maintainer still gets a
    confirm-step on a gated environment. Tick "Prevent self-review" on the
    environment for genuine four-eyes once there's a team.
  - Pair a reviewer-gated production env with `explicit: true` on its entry in
    `.github/shipmate-config.yml` so the bare `shipmate apply` skips it and
    it is only ever applied via the targeted `shipmate apply <env>` (which
    then pauses for the environment reviewer).
- **Private-repo caveat:** required reviewers (and wait timers) are free on
  public repos but require Enterprise on private ones — on Free, Pro and
  Team they are "only available for public repositories"
  ([GitHub docs](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)),
  and on Team the API refuses with `HTTP 422 ... Please ensure the billing plan
  supports the required reviewers protection rule` (on Free the rule may instead
  be created and silently ignored — `hardening.md` §6). Deployment branch
  policies are a different rule and do work on private repos from Pro/Team up.
  `hardening.md` §Plan prerequisites has the per-row table and the posture left
  without a reviewer gate.

## Note: free-tier private repos

Repository rulesets and classic branch protection require a paid plan
(Pro/Team/Enterprise) for private repositories, or a public repository.
On a free-tier private repo the required-check gate cannot be created at all.

This is a GitHub configuration constraint, not a shipmate one. `actions/summary`
still emits the correct `shipmate / gate` state in every case — `pending` while
apply checks are outstanding, `failure` ("plan incomplete") when a plan cell
fails, and `success` when nothing is left to apply. shipmate's responsibility —
producing a correct, stable, single required status — holds regardless of plan.
The ruleset above enforces it once the repo is public or on a paid plan.
