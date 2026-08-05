# Recommended settings (hardening)

`docs/branch-protection.md` covers the settings shipmate *needs* to function.
This page covers the settings that decide **who can make the engine do things**,
and it starts from one fact:

> **Push access to a consumer repository is authority over the engine.**

A branch may carry its own workflow files. GitHub runs them, with whatever
secrets that job's own environment bindings grant, on push — before a pull
request exists, before review, and before `CODEOWNERS` applies.
`SHIPMATE_APP_PRIVATE_KEY` itself is out of reach this way: the
`shipmate-engine` environment's deployment branch policy only ever satisfies
a job running at the default-branch ref (row 16; `docs/github-app.md`
§Key-exposure boundary), so a branch-pushed workflow cannot mint an App
token, and so cannot POST a `shipmate / gate` status, submit an approving
review as the App, complete `apply / <stack> / <env>` checks, or dispatch an
apply — but the gate's verdict is still computed from artifacts the
branch's own plan run produced (see "What none of this fixes"). What it
*can* still do, absent the rest of this checklist: declare
`environment: <env>-apply` itself and claim that environment's cloud
credentials directly, if that environment carries no deployment branch
policy of its own (row 17 closes this); and run arbitrary code inside
whatever a *plan* environment holds, since a plan job executes branch
content by design regardless of anything below (§7–9, "What none of this
fixes").

The arbitrary-code-execution residual is upstream of any workflow shipmate
ships — nothing in the engine can prevent it. The settings below close the
apply-environment path and limit who can reach even the residual one.

Fork pull requests are outside this, and are refused outright — the engine will
not plan a pull request whose head repository is not this repository (see
"Contributors without push access"). The App key is out of their reach twice
over: it lives only in the `shipmate-engine` GitHub Environment
(`docs/github-app.md` §Key-exposure boundary), reachable only by the trusted
`summary`/apply workflows running at the default-branch ref, and never inside a
`pull_request`-triggered job, fork or not. Adding a `pull_request_target`
workflow — the usual way to label or comment on fork pull requests — reverses
that: unlike `pull_request`, it runs at the **base** ref, which is exactly what
would satisfy `shipmate-engine`'s default-branch-only policy if such a job
declared that environment — while still checking out and acting on content the
fork author controls. That combination (a ref the policy trusts, executing
input it doesn't) is the no-push-access version of the attack this page opens
with, and it is reachable regardless of the fork refusal above, which only
governs the plan path. Don't add one to a repository that holds the App key —
`shipmate doctor` warns for any workflow file that declares the trigger. See
"Contributors without push access" for the trade-off that follows.

## Checklist

| # | Setting | Where | Closes |
|---|---------|-------|--------|
| 1 | Write access only for people trusted to apply | Repo/team access | Everything below is downstream of this |
| 2 | Push ruleset restricting `.github/workflows/**` *and every other path a workflow executes* | Repo/org ruleset (push target) | Branch-authored workflows |
| 3 | Required check `shipmate / gate` with `integration_id`, strict | Branch ruleset | A *third party* posting `shipmate / gate` under another identity |
| 4 | ≥1 approving review, code-owner review, dismiss stale, require approval of most recent push | Branch ruleset | Self-merge; the code-owner review is **unforgeable** at merge time (an App cannot be a `CODEOWNERS` entry) *provided a `CODEOWNERS` entry actually covers the IaC paths* — the rule is a no-op for changed files with no owner — and the approval *count* never is |
| 5 | Block force-push and deletion on the default branch | Branch ruleset | History rewrite after apply |
| 6 | Required reviewers + "Prevent self-review" on **every** `<env>-apply` that holds a secret | Environment | **Unforgeable** at apply time — the last line of defense once a merge has happened, and what makes row 7 hold |
| 7 | Cloud credentials only as `<env>-apply` environment secrets — never repo-level | Environment secrets | Repo-wide secret exposure (bounded *only* in combination with row 6) |
| 8 | Plan environments hold read-only, blast-radius-free credentials, no approval rules, no branch policy — ideally no secret at all (`shipmate doctor` reports what it finds) | Environment | Plan-time code execution |
| 9 | OIDC with an `environment:` claim condition instead of static keys | Cloud IdP | Long-lived credential theft (**not wired in the engine yet** — see §7–9) |
| 10 | Default `GITHUB_TOKEN` = read-only; Actions may not approve PRs | Settings → Actions | Token privilege creep |
| 11 | Require approval for all outside collaborators' workflow runs | Settings → Actions | Drive-by fork execution |
| 12 | Allowed-actions list (this engine + pinned third parties) | Settings → Actions | Supply chain |
| 13 | One App per trust domain; key as a per-repository `shipmate-engine` environment secret | App registration | Cross-repo blast radius |
| 14 | Rotate the App key when push access is revoked | Runbook | Ex-member with a copied PEM |
| 15 | Shorten Actions retention | Settings → Actions | `shipmate doctor` report disclosure |
| 16 | `shipmate-engine` Environment exists, deployment branch policy restricted to the default branch | Environment | Repository-secret App key readable by any branch |
| 17 | Deployment branch policy restricted to the default branch on every `<env>-apply` | Environment | Branch-authored workflow claiming apply-environment secrets directly |

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

`require_code_owner_review` is doing more work here than the approval count.
A GitHub App cannot be listed in `CODEOWNERS`, so a code-owner review is one of
only two controls on this page that a holder of the App private key cannot
satisfy (the other is #6). An approval *count* is not: the App holds
`pull-requests: write` and can submit an approving review that counts toward it.

That unforgeability holds only where the rule actually bites. GitHub requires a
code-owner review for a changed file **that has an owner**; with no `CODEOWNERS`
file, an entry that does not parse, or IaC paths simply left unowned, the setting
is a no-op and the App's own approving review satisfies the count on its own.
Write a `CODEOWNERS` entry covering the paths the stacks and the Terramate
configuration live in, and confirm on a real pull request that the reviewer
requirement appears. `shipmate doctor` warns when the rule requires approvals but
not code-owner review — it does not check `CODEOWNERS` coverage, and it never
fails a run, so that is a warning, not enforcement.

## 6. Environment reviewers — the gate that holds after a merge

Environment reviewers are **users and teams**. A GitHub App installation token
cannot be a reviewer and cannot approve a pending deployment, so this is the
**second** of the two controls in the list that an attacker holding the App
private key cannot satisfy — the other is the code-owner review in #3–5, which
an App cannot supply because it cannot be listed in `CODEOWNERS`. They are
unforgeable at different points, which is why both matter: code-owner review
guards the **merge**, the `<env>-apply` reviewer guards the **apply**.

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

A **deployment branch policy** on `<env>-apply`, scoped to the default branch,
does not change *which code the engine applies* — the pre-merge apply is
always dispatched on the default branch and applies the pull request head
passed as an input, policy or not. But it is very much a control on **secret
release**: without one, a branch-authored workflow that simply declares
`environment: dev-eu-apply` in its own YAML is handed that environment's
secrets directly on push, bypassing comment-ops, the gate, and everything
else in this list — the same path row 16 closes for the App key. Set it on
every `<env>-apply`, restricted to the default branch, and do count it as a
control.

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
  The strongest version of this control is a plan environment with **no secret
  in it at all**. Two ways to get there, in order of preference: read the
  provider's state through a path that needs no long-lived credential of its
  own, and — when the engine's credential path exists — a **read-only** OIDC
  role whose trust policy is conditioned on the plan environment's claim
  (`repo:<owner>/<repo>:environment:<env>`, the plan environment, not
  `<env>-apply`), so a token minted in a plan cell can never assume the apply
  role. Until then the honest statement is the one above: whatever a plan
  environment holds is readable by anyone who can push a branch.

  `shipmate doctor` reports every secret it finds in a plan environment — names
  only, since no GitHub API returns a secret's value — as a note, and warns if
  `SHIPMATE_APP_PRIVATE_KEY` is one of them. The report says the check was not
  performed, rather than reporting it clean, when the App installation has not
  accepted the `environments: read` permission the manifest declares.
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
  no `pull_request_target` in a repository holding the App key — it runs at
  the base ref (which would satisfy `shipmate-engine`'s branch policy if the
  job declared that environment) while still acting on fork-author-controlled
  content, which is the no-push-access version of the attack this page opens
  with. `shipmate doctor` reports any workflow file that declares it, by name.
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
- Set the key as a **`shipmate-engine` environment secret, per repository**
  (`docs/github-app.md` steps 5–6) — never at repository or org level, and
  never shared org-wide the way `SHIPMATE_APP_ID` (a variable, not a secret)
  may be: environment secrets are scoped to one repository's environment, so
  each consumer repo needs its own `shipmate-engine` environment and its own
  copy of the key.
- **Rotate the key whenever push access is revoked** — the runbook is
  `docs/github-app.md` §7. Anyone who had push access could have copied the
  PEM out of a workflow run, and revoking their access does not invalidate it.

**The multi-repo cost, stated plainly.** GitHub has no org-level environment
secrets — environments are per repository — so an organization whose IaC is
split across N repositories places the key N times and rotates it N times. It
does not *create* N keys: creation is per App, and #13 above already asks for
one App per trust domain, so one key covers every repository in it. That
is a genuine operational cost, and it is worth being clear about what it is and
is not:

- It is **not** a cost this scoping introduced. The alternative, an org secret
  with `--visibility selected`, is readable by any workflow on any branch in
  every selected repository — the same exposure replicated N times, each
  independently reachable by that repository's own developers. Per-repository
  scoping is unavoidable once the key lives in the repositories at all.
- It **is** work that should be automated rather than clicked: place and rotate
  across every repository in one scripted pass, not one at a time. The
  cost is three API calls per repository at onboarding (create the environment,
  add its branch policy, write the environment secret) against one
  repository-list edit per repository plus a single org-secret write for the
  alternative, and rotation as N `gh secret set --env` writes rather than that
  one org-secret write.
  `docs/github-app.md` §5–7 carries the loops.
- The only way to remove it is to stop putting the key in the repositories,
  which requires something outside GitHub Actions to hold it. That is a
  different architecture, not a setting.

**A dead end, so it is not re-proposed:** a single "control repository" holding
the key on behalf of the others does not work without a server. Triggering a
workflow in another repository requires a credential in the calling repository —
a `GITHUB_TOKEN` cannot trigger another repository's workflow — so every IaC
repository would need a secret in order to avoid having a secret.

## 15. Retention and disclosure

The `shipmate doctor` report is written to the job summary as well as to a
comment, and a job summary cannot be edited or redacted. Shorten the Actions
retention window if that inventory is sensitive
(`docs/branch-protection.md` §"Who can ask for the report").

## 16. The `shipmate-engine` environment

`SHIPMATE_APP_PRIVATE_KEY` lives as a secret on the `shipmate-engine` GitHub
Environment, not as a repository (or org) secret — see `docs/github-app.md`
§Key-exposure boundary for why that specific environment is what makes the
key unreachable from a branch-authored workflow. **Three** things must all be
true for that to hold. `shipmate doctor` checks the first two on every plan run
and on demand; it cannot check the third:

- the `shipmate-engine` environment exists;
- its deployment branch policy is a **custom** policy naming the default
  branch — not merely present, and not the "protected branches" mode, which
  restricts to whatever branch protection covers rather than the default
  branch specifically;
- **no repository (or organization) secret named `SHIPMATE_APP_PRIVATE_KEY`
  remains.** An environment secret is released only to a job that *names* the
  environment. A repository secret of the same name is readable by any workflow
  on any branch without naming anything, so leaving one in place defeats this
  control completely — the environment becomes decoration.

That third condition is the one to be careful about, because it is invisible.
Listing a repository's secrets needs a `secrets` permission `app/manifest.json`
does not declare, so the probe can only observe the environment's *shape*. A
repository that had the key as a repository secret first — the ordinary
migration path — and then created the environment without deleting it ends up
with the key still branch-readable and `shipmate doctor` reporting all clear.
Delete it as the last step of `docs/github-app.md` §6, and confirm with
`gh secret list --repo <owner>/<repo>`: `SHIPMATE_APP_PRIVATE_KEY` must not
appear there.

A re-pin of the engine that never creates this environment — the other ordinary
way to regress this — leaves the key a repository secret again, readable by any
branch's workflow. That one the probe does catch.

**Tag pushes are covered, and there is one way to uncover them.** A deployment
branch policy is typed: the entry naming your default branch is a **branch**
policy, and it matches no tag, so a workflow run at `refs/tags/…` that declares
this environment is refused before a runner starts and is handed nothing —
measured, not assumed. Someone who can push tags but not branches therefore
cannot reach the key either. What breaks that is adding a **tag** policy to
`shipmate-engine` — for release automation, say. Do not: a tag is a ref anyone
with tag-push access can create at any commit, including one carrying a workflow
of their choosing. `shipmate doctor` warns about any extra policy here, tag ones
included, because it compares the policy names against the default branch alone.

## Contributors without push access

**Fork pull requests are refused outright.** `actions/build-matrix` fails the
step when the triggering `pull_request` event's head repository is not this
repository, and there is no input, variable or setting that turns that off — it
is a rule of the engine, not a configurable. A fork's plan is refused before any
stack is enumerated and before any `tofu` process starts. It is not refused
before `detect`'s own `terramate` steps: in the reference `plan.yml`,
`terramate fmt --check` and `terramate generate --detailed-exit-code` precede
`actions/build-matrix`, so they still evaluate the fork's Terramate HCL —
globals, `tm_*` functions and generate blocks included. Moving the refusal ahead
of them means reordering your own `plan.yml`.

That refusal is about **code execution**, not secrets. A fork's `pull_request`
run receives no repository secrets, and `plan.yml` holds no App key in any case
— nothing in `plan.yml` ever does, since the key lives only in
`shipmate-engine` — but a plan cell still executes the pull request's own
Terramate/OpenTofu code and reads whatever the plan environment exposes as
*variables*, which are not secrets and are not withheld from a fork. For an
infrastructure repository that is arbitrary code execution offered to anyone who
can open a pull request.

The refusal is loud (a red step) rather than a quiet empty matrix, because a
fork pull request could not merge either way: the trusted `summary` workflow
refuses to act on a `workflow_run` whose `head_repository` differs from
`github.repository` (see `docs/github-app.md` §Key-exposure boundary), so no
`shipmate / gate` status is ever written and the required check never appears.
A green "nothing to plan" would leave an outside contributor waiting on a gate
that structurally cannot arrive. Comment-ops has nothing to dispatch either —
no gate, no reviewed plan to point `shipmate apply` at.

So the fork model is safe but not self-service: a maintainer must bring the
branch into the repository (`gh pr checkout` then push to a branch) for it to
plan and apply — which is exactly what the refusal message tells the
contributor to ask for. Plan that in, rather than granting push access to make the
inconvenience go away — and resist the other shortcut, a `pull_request_target`
workflow to do something useful on fork pull requests. That trades the property
this section rests on for exactly the exposure control 1 exists to limit.

## What none of this fixes

- **A fabricated `plan-matrix.<N>` marker.** The gate holds unless the plan run
  published exactly one readable marker naming the cell count, and the listed
  `cell-summary.*` count matches it (see `CONTRACT.md` §Plan comment). That
  marker is published by the plan run, which executes the pull request's own
  code *and its own workflow file*, so an author with **write access** can
  publish any count they like — including a zero that greens a quiet gate over
  stacks that were planned. The marker defends against an eventually-consistent
  artifacts API, not against a privileged author, who can already fabricate the
  whole artifact surface the summary reads: the cell summaries, the `cell.json`
  verdicts, the `.otplan` files. Nothing here makes the gate unforgeable from
  inside the repository; control 1 (who can push a branch) is what bounds that.
- **Plan-time code execution.** Reviewing a plan means reading output produced
  by a pipeline running the author's code. A hostile provider, an `external`
  data source, or a `terramate` script block executes during plan. Control 8
  bounds the damage; nothing eliminates it.
  `shipmate doctor`'s plan-environment secret probe reports what such an
  environment *stores*; it cannot observe what plan-time code does with it, and
  a credential the consumer's own workflow maps in from a repository secret is
  outside what it can see at all.
- **Reviewer comprehension.** The exact-plan invariant guarantees the applied
  plan is the reviewed one. It guarantees nothing about the reviewer having
  understood it.
- **Branch-controlled configuration.** Stack tags, `global.shipmate.env_order`
  and `explicit_envs` all come from the pull request branch. They shape what the
  engine does; they do not constrain what it is allowed to do.
- **The gate is an assertion, not a proof.** The App identity and pull request
  approvals are out of a push-capable developer's reach only because control 16
  keeps the App private key on the `shipmate-engine` environment, unreadable
  from a branch-authored workflow — a holder of that key can forge both; the
  gate remains an assertion produced by the author's own pipeline; the enforcing
  controls are the code-owner-required pull request approval and the
  `<env>-apply` environment reviewer. Do not claim the gate is unforgeable.
