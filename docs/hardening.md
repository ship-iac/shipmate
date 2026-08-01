# Recommended settings (hardening)

`docs/branch-protection.md` covers the settings shipmate *needs* to function.
This page covers the settings that decide **who can make the engine do things**,
and it starts from one fact:

> **Push access to a consumer repository is authority over the engine.**

A branch may carry its own workflow files. GitHub runs them, with the
repository's secrets, on push — before a pull request exists, before review,
and before `CODEOWNERS` applies. Such a workflow can read
`SHIPMATE_APP_PRIVATE_KEY`, mint an App installation token, and then forge the
`shipmate / gate` status (the pinned `integration_id` is satisfied, because the
forgery *is* the App), approve the pull request as the App, complete
`apply / <stack> / <env>` checks without applying anything, and dispatch
applies without going through comment-ops authorization.

Nothing in the engine can prevent that — it is upstream of any workflow
shipmate ships. The settings below limit who reaches that position, and keep
one gate standing that a minted token cannot pass.

Fork pull requests are outside this **as long as every workflow that can see a
secret is triggered by `pull_request`**, as the sample repos' are: a fork's
`pull_request` run receives no secrets, so the key is unreachable. Adding a
`pull_request_target` workflow — the usual way to label or comment on fork pull
requests — reverses that: it runs with the full repository secret set against
content the fork author controls, and the attack above becomes reachable
without any push access. Don't add one to a repository that holds the App key.
See "Contributors without push access" for the trade-off that follows.

## Checklist

| # | Setting | Where | Closes |
|---|---------|-------|--------|
| 1 | Write access only for people trusted to apply | Repo/team access | Everything below is downstream of this |
| 2 | Push ruleset restricting `.github/workflows/**` *and every other path a workflow executes* | Repo/org ruleset (push target) | Branch-authored workflows |
| 3 | Required check `shipmate / gate` with `integration_id`, strict | Branch ruleset | External gate forgery |
| 4 | ≥1 approving review, code-owner review, dismiss stale, require approval of most recent push | Branch ruleset | Self-merge |
| 5 | Block force-push and deletion on the default branch | Branch ruleset | History rewrite after apply |
| 6 | Required reviewers + "Prevent self-review" on **every** `<env>-apply` that holds a secret | Environment | **Unforgeable** — the last line of defense, and what makes row 7 hold |
| 7 | Cloud credentials only as `<env>-apply` environment secrets — never repo-level | Environment secrets | Repo-wide secret exposure (bounded *only* in combination with row 6) |
| 8 | Plan environments hold read-only, blast-radius-free credentials, no approval rules, no branch policy | Environment | Plan-time code execution |
| 9 | OIDC with an `environment:` claim condition instead of static keys | Cloud IdP | Long-lived credential theft (**not wired in the engine yet** — see §7–9) |
| 10 | Default `GITHUB_TOKEN` = read-only; Actions may not approve PRs | Settings → Actions | Token privilege creep |
| 11 | Require approval for all outside collaborators' workflow runs | Settings → Actions | Drive-by fork execution |
| 12 | Allowed-actions list (this engine + pinned third parties) | Settings → Actions | Supply chain |
| 13 | One App per trust domain; org secret `--visibility selected` | App registration | Cross-repo blast radius |
| 14 | Rotate the App key when push access is revoked | Runbook | Ex-member with a copied PEM |
| 15 | Shorten Actions retention | Settings → Actions | `shipmate doctor` report disclosure |

## 1. Write access

Grant repository write only to people you would let apply to production
unreviewed, because that is what it amounts to. This is the only control that
closes the branch-authored-workflow path outright; everything else narrows it.

Review the list whenever the approvers team changes — write access and
`SHIPMATE_APPROVERS_TEAM` membership are separate grants, and the first one is
the stronger of the two.

## 2. Restrict pushes that touch executable paths

A **push** ruleset (distinct from the branch ruleset in
`docs/branch-protection.md`) with a *restricted file paths* rule blocks the
malicious workflow at push time rather than at merge time.

Restrict **every path whose content a workflow job executes**, not just the
workflow files. A local composite action or a script a `run:` step invokes
executes inside the same job — including the job that has just minted an App
installation token, which any step in that job can read in plaintext — so
leaving those paths writable leaves the whole control open:

```bash
gh api -X POST repos/<owner>/<repo>/rulesets --input - <<'JSON'
{
  "name": "shipmate-workflow-files",
  "target": "push",
  "enforcement": "active",
  "rules": [
    { "type": "file_path_restriction",
      "parameters": { "restricted_file_paths": [
        ".github/workflows/**",
        ".github/actions/**"
      ] } }
  ],
  "bypass_actors": [
    { "actor_id": <TEAM_ID>, "actor_type": "Team", "bypass_mode": "always" },
    { "actor_id": <DEPENDABOT_APP_ID>, "actor_type": "Integration", "bypass_mode": "always" }
  ]
}
JSON
```

Notes before you adopt it:

- Push rulesets are plan-gated (organization-owned repositories on paid plans).
  Check availability on yours; on a plan without them, control 1 carries the
  whole load.
- Add the workflow owners as bypass actors, or nobody can change a workflow —
  including pin bumps. A team is `"actor_type": "Team"` with the team id.
- **Dependabot** proposes shipmate pin bumps as commits touching
  `.github/workflows/`, and it is a GitHub App, so its bypass entry is
  `"actor_type": "Integration"` with Dependabot's app id — not the `Team` shape
  above. Adding it from the ruleset UI's bypass-list picker fills in the right
  id for you. Either add it (its pushes are generated from `dependabot.yml`, not
  from arbitrary branch content) or accept that pin bumps become manual — and
  note that CONTRACT.md §Consumption makes Dependabot the intended path for
  receiving engine updates.
- Add any other path your workflows execute: a `run: ./scripts/...` helper, a
  `Makefile`, a `dependabot.yml` that could be repointed. Anything a job runs.
- The rule restricts *paths*, not content: it does not stop malicious IaC code,
  which the plan cell executes from wherever the stacks live. That is control
  8's problem, and it has no path-based fix.

## 3–5. Branch ruleset

Control 3 is the ruleset in `docs/branch-protection.md` — required
`shipmate / gate` with `integration_id` pinned, `strict_required_status_checks_policy: true`.
Add to it:

```json
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
```

The two review-freshness settings do different jobs, and the apply path only
ever sees their combined result via `reviewDecision`:

- `dismiss_stale_reviews_on_push` dismisses existing approvals whenever new
  commits are pushed, so the pull request drops back to `REVIEW_REQUIRED`.
- `require_last_push_approval` requires the **most recent push** to be approved
  by someone other than the person who pushed it. It is what stops an approver
  from pushing one more commit onto an approved pull request and applying it
  themselves. Keep it even if you decide dismiss-on-push is too disruptive for
  your team — it is the one that survives that trade-off.

With `required_approving_review_count: 0` (sole-maintainer mode), `shipmate
apply` proceeds without an approving review — but a `CHANGES_REQUESTED` review
still blocks it until it is resolved or dismissed (`scripts/authorize` passes
only on `NONE` or `APPROVED`; see `docs/branch-protection.md` §"Review policy").
That is a deliberate mode, not an oversight, but do not run it with more than
one person holding push access.

## 6. Environment reviewers — the one gate that holds

Environment reviewers are **users and teams**. A GitHub App installation token
cannot be a reviewer and cannot approve a pending deployment, so this is the
only control in the list that an attacker holding the App private key cannot
satisfy.

On every `<env>-apply` environment that holds a secret or touches real
infrastructure — **including dev and staging**:

- add required reviewers (a team, not one person),
- tick **Prevent self-review**.

Do not scope this to production. An environment with no protection rules hands
its secrets to any job that names it, from any branch, with no approval and no
deployment — so an unprotected `dev-eu-apply` is a repository secret with extra
steps (this is what control 7 leans on).

For production, also list it in `global.shipmate.explicit_envs` so a bare
`shipmate apply` skips it and it is reached only by the targeted
`shipmate apply <env>`. Use the **bare environment name** there — `prod`, not
`prod-apply`; the value is matched against the environment name carried by the
apply checks (see CONTRACT.md), and a `-apply`-suffixed entry validates
silently and skips nothing.

`explicit_envs` is read from the *pull request branch* and can therefore be
edited by whoever pushed the branch. Treat it as ergonomics; the environment
reviewer is the enforcement.

What the reviewer sees is a deployment-approval prompt naming the environment —
not a diff. Approving it means "I have read this pull request's plans", so the
approval is only as good as that habit.

A **deployment branch policy** on `<env>-apply` does not restrict which code is
applied: the pre-merge apply is dispatched on the default branch and applies the
pull request head passed as an input, by design. Set one if you like, but do not
count it as a control.

## 7–9. Credentials

> **The engine has no cloud-credential path yet.** The plan and apply cells
> inject only `TF_VAR_env` / `TF_VAR_region` / `TF_WORKSPACE` from `vars`, plus
> shipmate's own two secrets; no job interpolates a consumer credential and no
> job requests `id-token: write`, so neither an environment secret nor OIDC
> currently reaches `tofu`. The sample repos need none (null resources, local
> state), which is why this has not surfaced. Do not move a working credential
> into an environment secret expecting the engine to pick it up — it will not,
> and every apply cell will fail at provider init. Controls 7 and 9 below state
> where credentials must land **when that path is built**; until then, this is
> an open gap, not a setting you can apply.

- **Apply credentials belong in `<env>-apply` environment secrets, never at
  repository or organization level.** A repository secret is readable by any
  workflow on any branch. An environment secret is released only to a job that
  names that environment — but *only* after that environment's protection rules
  pass, and an environment with no rules releases them to any branch on demand.
  The scoping is worth nothing without control 6; treat the two as one setting.
- **Plan environments must have no approval-type protection rules (required
  reviewers, wait timers) and no deployment branch policy** — the engine plans
  on a pull request head ref, so any of those blocks every plan cell and leaves
  the gate red until it is removed (`shipmate doctor` warns; see
  `docs/branch-protection.md`). That constraint is not negotiable, so treat a
  plan environment as readable by anyone with push access: a plan cell runs
  `terramate`/`tofu` over branch code, which is arbitrary code execution with
  whatever that environment holds. Read-only, blast-radius-free credentials
  only. Assume `SHIPMATE_PLAN_PASSPHRASE` is readable by the same people for the
  same reason.
- **Prefer OIDC** to static cloud keys when the path exists, and condition the
  trust policy on the environment claim
  (`repo:<owner>/<repo>:environment:<env>-apply`) so a token minted from a plan
  cell — or from a branch workflow — cannot assume the apply role.

## 10–12. Actions settings

Settings → Actions → General:

- **Workflow permissions**: read repository contents, and clear "Allow GitHub
  Actions to create and approve pull requests". shipmate's workflows declare the
  permissions they need per job.
- **Fork pull request workflows**: require approval for **all outside
  collaborators** (the strictest option), and never enable "Send secrets to
  workflows from fork pull requests". The same rule applies in workflow code:
  no `pull_request_target` in a repository holding the App key — it hands the
  full secret set to a run whose content a fork author controls, which is the
  no-push-access version of the attack this page opens with.
- **Allowed actions**: allow the engine (`<owner>/shipmate/*`) plus the pinned
  third-party actions the workflows use. This is supply-chain hygiene; it does
  not constrain `run:` steps.

## 13–14. The App

The private key is authority over **every repository the App is installed on**,
regardless of how the installations are split — a key holder can enumerate
installations and mint a token for any of them. Splitting one App across repos
with different levels of push access therefore lifts the weakest repository's
trust to all of them.

- Register **one App per trust domain**. Repositories with wide push access get
  their own App and their own key.
- Distribute the key as an org secret with `--visibility selected`, listing only
  the repositories that use it (`docs/github-app.md` step 5), or as a repository
  secret.
- **Rotate the key whenever push access is revoked** — the runbook is
  `docs/github-app.md` §6. Anyone who had push access could have copied the PEM
  out of a workflow run, and revoking their access does not invalidate it.

## 15. Retention and disclosure

The `shipmate doctor` report is written to the job summary as well as to a
comment, and a job summary cannot be edited or redacted. Shorten the Actions
retention window if that inventory is sensitive
(`docs/branch-protection.md` §"Who can ask for the report").

## Contributors without push access

Fork pull requests cannot reach the App key — and cannot complete a shipmate
run either. A fork's run gets no `SHIPMATE_APP_PRIVATE_KEY`, so the `summary`
job cannot mint a token, so no `shipmate / gate` status is created and the pull
request stays blocked. Comment-ops has nothing to dispatch.

So the fork model is safe but not self-service: a maintainer must bring the
branch into the repository (`gh pr checkout` then push to a branch) for it to
plan and apply. Plan that in, rather than granting push access to make the
inconvenience go away — and resist the other shortcut, a `pull_request_target`
workflow to do something useful on fork pull requests. That trades the property
this section rests on for exactly the exposure control 1 exists to limit.

## What none of this fixes

- **Plan-time code execution.** Reviewing a plan means reading output produced
  by a pipeline running the author's code. A hostile provider, an `external`
  data source, or a `terramate` script block executes during plan. Control 8
  bounds the damage; nothing eliminates it.
- **Reviewer comprehension.** The exact-plan invariant guarantees the applied
  plan is the reviewed one. It guarantees nothing about the reviewer having
  understood it.
- **Branch-controlled configuration.** Stack tags, `global.shipmate.env_order`
  and `explicit_envs` all come from the pull request branch. They shape what the
  engine does; they do not constrain what it is allowed to do.
