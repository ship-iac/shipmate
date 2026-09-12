# GitHub App setup (one-time)

A prerequisite of [`getting-started.md`](getting-started.md#required--plan)
§Required — plan, not an optional extra: without the App that tier cannot mint a
token, so no gate status and no apply checks.

shipmate's comment-ops path (`shipmate apply <env>` in a PR comment) needs a
private GitHub App to mint a short-lived `workflow_dispatch` token. Events
created with `GITHUB_TOKEN` never trigger other workflows, so the manual
pre-merge apply cannot be kicked off with the default token. The same App
also authors every apply check, the `shipmate / gate` commit status, the
sticky plan/result comments, and drift issues — installation tokens minted
fresh per job, never a long-lived credential in the workflow. The bot
identity is derived automatically from the App name once it's registered, so
an App named `shipmate-acme` comments as `shipmate-acme[bot]`.

This is a runbook, not a tutorial: run the commands in order. Steps 1–4 are
once per GitHub org: register the App and install it, selecting the repositories
it may act on. Steps 5–6 onboard one repository, and are written for the
repository you are setting up now. `scripts/onboard` performs both, plus the rest
of that repository's setup. Onboarding several at once is that script in a
loop — see the appendix.

## Prerequisites

- `gh` CLI, authenticated as an org owner (`gh auth status`).
- Admin rights on the org that will own the App.
- This repo checked out locally (`app/manifest.json` is read by the steps below).

## 1. Register the App

GitHub App registration via a manifest is a browser POST, not an API call, and
GitHub answers it with a redirect carrying a single-use `code`. One command
does the whole leg:

```bash
python3 scripts/register-app \
  --name shipmate-<your-org> \
  --repo <your-org>/shipmate \
  --out shipmate-app.private-key.pem
```

It builds the self-submitting form from `app/manifest.json`, opens it in your
browser, and receives GitHub's redirect on a loopback listener it started
first, so the `code` never leaves the machine and there is nothing to copy.
Confirm the registration in the GitHub UI when the browser lands on it; the
terminal continues by itself. §2 covers what the command then stores.

**Edit the name.** GitHub App names are unique across all of GitHub, and
`ship-iac` already holds `shipmate`, so a verbatim paste is rejected with "Name
has already been taken" — after the browser POST, which is a slow way to find
out. `app/manifest.json` therefore ships `shipmate-<your-org>` as a placeholder,
and `--name` overrides it. Later sections' App-settings URLs
(`.../settings/apps/shipmate`) name the App you registered, so substitute
accordingly.

The knock-on is already handled: the bot identity is derived from the App name,
so a renamed App comments as `shipmate-<your-org>[bot]` rather than
`shipmate[bot]`. The sticky plan and doctor comments are looked up by their
marker plus a `Bot` comment author, never by a hardcoded login.

The manifest's `redirect_url` (`http://127.0.0.1:8723/callback`) is a working
default for anyone hand-building the form instead; `register-app` overrides it
with the port its listener actually bound.

## 2. What step 1 stored

The command in step 1 converts the captured code
(`gh api -X POST app-manifests/<code>/conversions`) and then stores two things:

- `SHIPMATE_APP_ID` — a repository variable on `--repo` (typically the
  App-owning repo itself, e.g. `<org>/shipmate`). The app id is not a secret.
  It also prints it, as `App created: id=… slug=…`.
- The private key — the file named by `--out`, which must not already exist:
  the command refuses rather than overwrite, because the key it replaced could
  never be minted again. It is created mode `0600` on Linux and macOS. On
  Windows the mode is not applied at all — the file inherits the directory's
  ACLs — so run the command from a directory only you can read.

**No repository secret is created.** Nothing else on this page creates one
either: a repository secret is readable by any workflow on any branch, while the
`shipmate-engine` environment secret §5 and §6 place the key in is scoped to one
ref. That difference is the whole key-exposure boundary; §Key-exposure boundary
states it in full.

Keep the `--out` file: §6 reads it for every consumer repository, and `gh`
cannot read a secret's value back once set. Shred it once every repository has
it (`shred -u shipmate-app.private-key.pem` or equivalent). If you lose it
before then, generate a replacement — App settings → Private keys →
Generate a private key — rather than re-registering. An App holds several
keys and each one mints valid tokens, so generating one invalidates nothing.

## 3. Upload a logo (optional but recommended)

The manifest flow leaves the App with GitHub's default gray-box avatar. To
give it a recognizable identity in check-run lists, PR comments, and the
installations page: App settings (`.../settings/apps/shipmate`) → Display
information → Upload a logo. Purely cosmetic — everything above works
without it.

## 4. Install the App in your organization

Registration and installation are separate. One installation per organization
covers every repository that runs shipmate's comment-ops and dispatch:

```
https://github.com/organizations/<org>/settings/apps/shipmate/installations
```

Click `Install`, choose `Only select repositories`, then pick every IaC
repository shipmate will serve. A repository added later is an edit to that
selection on the same page, not a second installation. `All repositories` also
works and widens nothing the App can do — its permission set is the manifest's
either way — but the selection is the record of which repositories are shipmate
consumers, and the key is authority over every one of them (§Key-exposure
boundary).

## 5. Create the `shipmate-engine` environment

`SHIPMATE_APP_PRIVATE_KEY` is a secret on this environment, never a repository
or org secret. §Key-exposure boundary explains why that scoping is what keeps the
key out of a branch-authored workflow. Create it once in your repository, with a
deployment branch policy naming exactly the default branch — or run
`scripts/onboard`, which creates it, scopes it, and does §6 as well
([`getting-started.md`](getting-started.md) §Quick path):

```bash
REPO=<owner>/<repo>

DEFAULT_BRANCH=$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)
if [ -z "$DEFAULT_BRANCH" ]; then
  echo "could not read $REPO's default branch — creating nothing" >&2
  exit 1
fi
gh api -X PUT "repos/$REPO/environments/shipmate-engine" --input - <<'JSON'
{ "deployment_branch_policy": { "protected_branches": false, "custom_branch_policies": true } }
JSON
gh api -X POST "repos/$REPO/environments/shipmate-engine/deployment-branch-policies" \
  -f name="$DEFAULT_BRANCH"
```

The guard matters, and it has to come before the `PUT`: a typo'd name or a
repository you cannot read leaves `DEFAULT_BRANCH` empty, and without it the
`PUT` has already created the environment with `custom_branch_policies: true`
while the `POST` writes a policy named `""` (or 422s). Either way the environment
admits no ref and fails closed, discovered only when that repository's first
apply check never completes. Re-running this against an already-onboarded
repository makes the `POST` fail with "name has already been taken", which is
harmless — the policy is already there.

Reading the default branch rather than hardcoding `main` is the point: the
policy must name that repository's own default branch, and a policy naming a
branch that does not exist fails closed — the apply-completion job is denied the
key and apply checks never complete.

No reviewers on this environment — it exists to scope a secret to a ref, not
to gate a human decision (`docs/hardening.md` #16). `shipmate doctor` checks
both that this environment exists and that its policy actually names the
default branch.

**What this costs across N repositories.** Key *creation* is per App, not per
repository: `docs/hardening.md` #13 asks for one App per trust domain, and one
key serves every repository in it. Placement is per repository either way. The
alternative, an org secret with `--visibility selected`, needs its repository
list edited for each new repo. So onboarding a repository under this scoping is
three API calls — the `PUT` and the `POST` above plus the `gh secret set --env`
in §6 — against one repository-list edit per repository for the org secret, whose
value itself is written once for the whole org. Rotation becomes N
`gh secret set --env` writes instead of that one org-secret write. Both scale as
loops — the appendix loops `scripts/onboard` over the checkouts.

## 6. Set the approvers team + propagate credentials

Each consumer repo needs `SHIPMATE_APPROVERS_TEAM` (the GitHub team slug whose
members may run `shipmate apply`) plus the app id/key from step 1. `gh` cannot
read back a secret's value once set (GitHub never exposes it), so this step reads
the `shipmate-app.private-key.pem` step 1 wrote. Keep that file until every
consumer repo has it. `scripts/onboard` does all of this, and additionally
deletes any repository-level copy of the key.

```bash
REPO=<owner>/<repo>
TEAM=<approvers-team-slug>          # the GitHub team slug, not a display name
APP_ID=<app-id-from-step-1-output>

KEY=$(cat shipmate-app.private-key.pem)
if [ -z "$KEY" ]; then
  echo "shipmate-app.private-key.pem is missing or empty — not touching the repository" >&2
  exit 1
fi

gh variable set SHIPMATE_APPROVERS_TEAM --repo "$REPO" --body "$TEAM"
gh variable set SHIPMATE_APP_ID --repo "$REPO" --body "$APP_ID"
gh secret set SHIPMATE_APP_PRIVATE_KEY --repo "$REPO" --env shipmate-engine \
  --body "$KEY"
# The environment secret is only scoping if no repository secret of the same
# name survives it: an environment secret is withheld from jobs that do not name
# the environment, but a repository secret is readable by any workflow on any
# branch without naming anything. Ignore the error when there was none.
gh secret delete SHIPMATE_APP_PRIVATE_KEY --repo "$REPO" 2>/dev/null || true
```

**Confirm the repository secret is gone.** Nothing else will tell you:
listing a repository's secrets needs a permission the App manifest does not
declare, so `shipmate doctor` cannot see this and reports all clear on a
repository whose environment is shaped correctly while the key is still
branch-readable (`docs/hardening.md` #16):

```bash
gh secret list --repo "$REPO"
```

`SHIPMATE_APP_PRIVATE_KEY` must not appear in that output; it should appear only
under the environment (`gh secret list --repo "$REPO" --env shipmate-engine`).

**Both variables (variables, not secrets) may be set once at the organization
level instead.** `vars` resolve organization → repository → environment, so a
consumer repo holding neither copy reads the organization value and nothing else
in the pipeline changes. Set `SHIPMATE_APPROVERS_TEAM` per repository wherever
the approving team differs.

`gh variable set --org` defaults to `--visibility private`, which reaches
private repositories only — an organization-wide default leaves every public
consumer resolving the name as empty. Two visibilities reach a public
repository, neither preferred over the other: `all` is the simple one,
`selected` the scoped one. `scripts/onboard` accepts both.

```bash
gh variable set SHIPMATE_APP_ID --org <org> --visibility all \
  --body "<app-id-from-step-1-output>"
gh variable set SHIPMATE_APPROVERS_TEAM --org <org> --visibility selected \
  --repos "<repo>,<repo>" --body "<approvers-team-slug>"
```

Then tell `scripts/onboard` which names are already set there, and it stops
writing them per repository:

```bash
python3 <engine-checkout>/scripts/onboard \
  --team <approvers-team-slug> --app-id <app-id> \
  --key shipmate-app.private-key.pem \
  --vars-at-org SHIPMATE_APP_ID,SHIPMATE_APPROVERS_TEAM
```

The flag takes a comma-separated list of names. A name `onboard` does not itself
set is refused, because it would filter nothing and still report success.

**Every asserted name is verified, not trusted.** `onboard` reads
`GET /repos/{owner}/{repo}/actions/organization-variables`, which returns
only the organization variables whose visibility reaches this repository, and
refuses before its first write when an asserted name is missing from that list
or carries a value other than the one this run would have written. A name left
on the default `private` visibility for a public consumer is caught here rather
than at the first run. The read needs no token scope beyond the `repo` access
onboarding already has, and it needs `gh` 2.93.0 or newer, for
`--paginate --slurp`.

**Private consumers need GitHub Team or Enterprise.** Organization variables do
not reach private repositories on GitHub Free at all, whatever each variable's
visibility says, and that bounds both names. `onboard` refuses rather than let
it reach a run. It reads the plan through `gh api orgs/<org>`, which reports it
only to an organization owner, so a token that cannot read the plan is refused
the same way, with an instruction to re-run as an owner. Without the refusal the
failure surfaces at the first `shipmate apply`: an empty
`SHIPMATE_APPROVERS_TEAM` makes every membership check 404, and the comment is
rejected as "not a member of the required approvers team ``".

**The residual cost.** Onboarding a public repository this way still needs only
repository admin, as every other run does. A private one needs an organization
owner, because the GitHub Free check reads a field `GET /orgs/{org}` shows to
nobody else.

A repository-level copy of an asserted name still overrides the organization
value, because repository resolution wins. `onboard` reports one as a `differs`
line and exits 2, and never deletes it — removing a value it did not write is
outside what it reconciles. A `SHIPMATE_APP_ID` copy holding a value other than
`--app-id` is refused outright before any of this, as it is without the flag.

`SHIPMATE_APP_PRIVATE_KEY` cannot follow it there: environment secrets are
scoped to one repository's environment, so it has to be set per-repo as
above. That is one more reason step 5 (creating the environment) has to happen in
every consumer repo, not once for the org.

## 7. Rotate the private key (on suspicion of compromise)

1. In the App settings (`.../settings/apps/shipmate`), under `Private keys`,
   click `Generate a private key`. GitHub downloads a new PEM; the old key(s)
   remain valid until you delete them.
2. Store the new key everywhere it's used:

   ```bash
   REPOS="<owner>/<repo> <owner>/<repo>"   # every repository the App is installed on

   KEY=$(cat new-key.pem)
   if [ -z "$KEY" ]; then
     echo "new-key.pem is missing or empty — not touching any repository" >&2
     exit 1
   fi

   for REPO in $REPOS; do
     gh secret set SHIPMATE_APP_PRIVATE_KEY --repo "$REPO" --env shipmate-engine \
       --body "$KEY"
   done
   ```

   One new key, written N times — the key is not regenerated per repository.
   Read it once, before the loop, and abort when it is empty: a `cat` of the
   wrong filename inside the loop expands to the empty string, and
   `gh secret set --body ""` then succeeds N times and destroys the working key
   in every consumer repository. Step 3 deletes the old key next, so no App token
   could be minted anywhere — no `shipmate / gate` status gets written and every
   open pull request blocks on a required check that cannot arrive.
3. Back in App settings, delete the old private key so it can no longer
   mint tokens.
4. Shred the local PEM file (`shred -u new-key.pem` or equivalent) once it's
   stored in secrets.

## Reference: what the App can and can't do

- Permissions: `actions: write`, `pull_requests: write`, `contents: read`,
  `members: read`, `checks: write`, `statuses: write`, `issues: write`,
  `environments: read` (doctor's plan-environment secret listing — names only;
  no GitHub API returns a secret's value, and this permission cannot write one).
  Minted in its own non-fatal step, so an installation that has not accepted
  the request leaves the `shipmate / gate` status and the apply checks
  untouched; it costs two warnings in the `shipmate doctor` report — that probe
  reporting itself as not performed, and the App-permission-drift probe, whose
  full-manifest mint asks for this permission too and so fails until Accept.
  Both clear on Accept — see §Re-approve after permission changes.
- The App mints a fresh installation token per job and authors every
  `apply / <stack> / <env>` check (create pending, complete on apply), the
  aggregate `shipmate / gate` commit status, the sticky plan comment, the
  `shipmate doctor` sticky report, the apply result comments, and drift
  issues. The plan matrix job's own `shipmate / <stack> / <env>` auto check-run stays
  on the `github-actions` identity — it's the job's own check-run, not
  something a separate API call creates, so there's nothing for the App to
  author there. On an on-demand plan the App does author a copy of it: a
  dispatched run's job checks attach to the ref it was dispatched on, so
  they are mirrored onto the pull request head (`checks: write`, already
  granted for the apply checks).
- No webhook events (`default_events: []`, `hook_attributes.active: false`) —
  comment-ops is triggered by `on: issue_comment` in the consumer repo's own
  workflow, not by the App receiving a webhook.
- Not public (`public: false`) — this App is installed only on repos your org
  controls.

## Re-approve after permission changes

Expanding `default_permissions` in `app/manifest.json` (as this project did
to add `checks`/`statuses`/`issues`, and later `environments`) does not take
effect immediately for an already-installed App. GitHub puts the wider grant in
a pending request that an org owner must approve:

```
https://github.com/organizations/<org>/settings/apps/shipmate/installations
```

Open the installation, review the pending permission request, and Accept
it. Until that happens, API calls using the new scopes (e.g. the App's
`statuses: write` gate POST) fail with a permission error even though the
manifest and the installed App's token both look correct. The gap is the
un-approved request, not a code or config bug.

## Key-exposure boundary

**Why this exists at all:** a server-hosted GitHub App keeps its private key on
its own service, so the key is never in reach of the repository's contributors.
shipmate has no service, so the key lives in your repository — which makes
*where* it lives in the repository the entire security boundary. That is the
trade the README's "Why setup is not two clicks" section describes, stated
concretely below.

`SHIPMATE_APP_PRIVATE_KEY` is a secret on the `shipmate-engine` GitHub
Environment, not a repository or org secret. That environment's deployment
branch policy is a custom policy naming the default branch only
(`docs/hardening.md` #16; `shipmate doctor` checks both that the environment
exists and that its policy actually says so). GitHub evaluates a deployment
branch policy against the ref the triggering job runs at, which is what does the
actual work here:

- **On the plan path the workflow file holds the line, not the deployment branch
  policy.** Under `pull_request_target` *every* job in the plan run, the
  checkout-bearing `plan` matrix job included, evaluates at
  `refs/heads/<base>` and therefore satisfies the policy. And the `plan` job's
  `environment:` is branch-authored data: it is `matrix.environment`, which
  `build-matrix` derives from `env/*` Terramate tags in the head checkout, so a
  pull request that tags a stack `env/shipmate-engine` produces a plan cell
  naming an environment of its choosing (`shipmate-engine-plan` with the
  `-plan` suffix that binding adds, the bare `shipmate-engine` in a repository
  that shares one environment between plan and apply). What makes that inert is
  that the whole job graph is engine-owned: `secrets.SHIPMATE_APP_PRIVATE_KEY`
  is named in exactly one job of engine `plan.yml`, the one that checks nothing
  out, and a branch author cannot edit that file. The consumer's `shipmate.yml`
  names the secret in the `secrets:` block of each job that passes it, and
  `pull_request_target` runs the base copy of that file rather than the pull
  request's own. A dispatched `shipmate plan` is the same shape one ref along:
  every job evaluates at the ref the dispatch named (the default branch) and
  runs that copy of the file. That one file holds every trigger, so which job
  runs is decided by the `if:` expressions in the base or default-branch copy —
  never by anything the pull request writes.

  **The constraint that follows: no job in engine `plan.yml` other than
  `summary` may reference a `shipmate-engine` secret.** Adding one hands it to a
  plan cell whose environment the branch chooses.
  `scripts/tests/test_cells_hold_no_app_key.py` is the guard.
- **A `push` to a non-default branch cannot reach the key.** Measured, not
  inferred: such a job is refused before its first step, because a branch ref
  matches no pattern the policy names.
- **The jobs that can reach the key all run at the default-branch ref.** The
  autoplan reaches the `plan` job through `pull_request_target`, which evaluates
  at the base branch ref rather than the pull request head, so its trusted
  `summary` job — inside the engine's reusable `.github/workflows/plan.yml` —
  satisfies the policy. The other trigger that reaches it, the
  `workflow_dispatch` a commented `shipmate plan` sends, is dispatched on the
  default branch and satisfies the policy the way `push` does; the dispatch body
  states the verb and a pull request number, and no ref a commenter picks decides
  which workflow file runs.
  It reads the key from this environment, which resolves in the *calling*
  repository, so the caller passes the secret by name and holds nothing itself
  (that is also what makes a consumer in another organization work). The apply and
  deploy paths reach the key the same way: `workflow_dispatch` from
  comment-ops, or `push` to the default branch. Nothing that starts from
  arbitrary branch content ever does.
- **The one job that holds the key runs no repository content.** That
  `summary` job has no checkout step, and a consumer cannot add one: they call
  the workflow, they do not own its steps. Reaching the key from a
  pull-request-side trigger is safe only in that shape — see `CONTRACT.md`
  §Post-plan topology and `docs/hardening.md`.
- **The plan-text digest is authored in that job, and does not widen the
  boundary.** The `summary` job hashes the whole `plan.txt` it already downloads
  for the plan comment: reading author-produced data is not executing it, and no
  new secret enters the job. Re-rendering the plan there instead of
  hashing it was rejected for exactly this reason — the render needs the plan
  artifact decrypted, which would put `SHIPMATE_PLAN_PASSPHRASE` in the job that
  holds the App key.
- The *token* minted from the key is still readable in plaintext by any step
  in the job that mints it, same as before the key moved — the environment
  boundary controls which jobs can mint one, not what a job does with it
  once minted. The `integration_id`-pinned gate ruleset
  (`docs/branch-protection.md`) is what defends against a token minted inside
  one of those trusted jobs being used to forge a `shipmate / gate` status
  (via a supply-chain compromise reached through that job), and against any
  *other* identity — `GITHUB_TOKEN`'s `github-actions` identity, or a
  different GitHub App — posting a status under the same context, which
  without the pin would satisfy the required check outright.
- The `summary` job's `if:` is load-bearing rather than belt-and-braces. It
  refuses when the head repository the `facts` job resolved differs from
  `github.repository`, and when that job reports a draft nobody explicitly asked
  to plan. The three values fall on two sides of the guard's parentheses: the
  head repository is outside them, so an empty value — what a failed `facts` job
  yields — refuses unconditionally and no trigger rescues it; the draft flag and
  the on-demand flag are the two sides of one disjunct, so a missing draft flag
  refuses every autoplan run and is rescued by a requested plan exactly as an
  explicit draft is. All three are produced one job earlier in the same
  engine-owned file, so nothing a consumer writes can weaken them. Under
  `pull_request_target` a fork's pull request *does* reach the base ref, so
  nothing else would stop that job; and the environment admits a draft's run,
  whose plan jobs an autoplan skips, so without the second clause it would write
  a gate over a plan that never ran (a *requested* plan of a draft does run, and
  `on-demand` is how the caller says so). Being on the job, a refusal creates no
  deployment at all.

What none of this defends against is a change to the trusted workflow files
themselves — the consumer's `shipmate.yml`, and the engine's `plan.yml`,
`apply.yml` and the rest — landing on the default
branch, where they *would* satisfy the environment's policy. That path runs
through an ordinary pull request and merge — no `pull_request`- or
`pull_request_target`-triggered job that checks out branch content is ever in a
position to skip review and reach the key directly, unlike the
old repository-secret model. The backstop there is `require_code_owner_review`
on the branch ruleset (`docs/hardening.md` #4): a GitHub App cannot be a
CODEOWNER, so the App itself can never approve a change to its own trust
boundary — a human owner has to.

Push access to a consumer repository is still meaningful authority: it lets
someone author the pull request that proposes such a change and, on a
sole-maintainer repository with `required_approving_review_count: 0`, merge
it too (see `docs/hardening.md` §1 and §3–5). It is no longer, by itself,
enough to read the key outright the way an unreviewed branch push once was.

## Appendix: onboarding several repositories at once

First add every repository to the installation's selection (§4, one page, no
loop). Then run `scripts/onboard` once per consumer checkout. The script is the
one implementation of §5 and §6: a second, hand-written loop drifts from it, and
a repository configured by a drifted loop looks onboarded while the App key sits
on an environment that admits any ref.

Each run needs the engine checkout on a `vX.Y.Z` release tag, `terramate` on
`PATH` in the consumer checkout — its `env/<name>` tags are where the
environment set comes from — and `gh` authenticated with admin on that
repository. It refuses rather than half-configuring when one of those is missing.
Add `--vars-at-org SHIPMATE_APP_ID,SHIPMATE_APPROVERS_TEAM` to each run when
those variables live at the organization level (§6); a private consumer then
needs an organization owner rather than repository admin, because the plan read
behind that flag answers to nobody else.

```bash
ENGINE=<path-to-engine-checkout>    # on a release tag
CHECKOUTS="<path>/<repo> <path>/<repo>"
TEAM=<approvers-team-slug>          # may differ per repo; pass it per repo either way
APP_ID=<app-id-from-step-1-output>
KEY=$PWD/shipmate-app.private-key.pem

for DIR in $CHECKOUTS; do
  echo "== $DIR"
  ( cd "$DIR" && python3 "$ENGINE/scripts/onboard" --team "$TEAM" --app-id "$APP_ID" --key "$KEY" )
done
```

The subshell is what keeps the loop where it started: the script reads the
repository from `gh` and the environments from the working directory, so a `cd`
that leaked would reconcile the previous repository a second time. `$KEY` is
absolute for the same reason.

Read the output, not the exit codes. A run exits 0 when everything matched or was
created, and 2 when something differs and it was left alone — but `argparse` also
exits 2 on a usage error, so a loop branching on 2 cannot tell a drifted
repository from a mistyped flag. Add `--dry-run` for a first pass that reports
what every repository would get and writes nothing.

Each run ends with the checklist of what it cannot set: the cloud role and
region, the env identity your layout injects, `SHIPMATE_PLAN_PASSPHRASE`,
`SLACK_WEBHOOK`, environment reviewers, a `CODEOWNERS` entry, and the pull
request carrying the workflow file.

Then confirm, per repository, that no repository-level
`SHIPMATE_APP_PRIVATE_KEY` survived — `shipmate doctor` cannot check this for
you (§6). The script deletes one and reports the deletion, so this reads back
what the run claims:

```bash
for DIR in $CHECKOUTS; do
  echo "== $DIR"
  ( cd "$DIR" && gh secret list )
done
```

**Trademarks.** Terramate is a trademark of Terramate GmbH; Terraform is a
trademark of HashiCorp; OpenTofu is a project of the Linux Foundation. shipmate
is an independent project and is not affiliated with, endorsed by, or sponsored
by any of them; their marks are used only to identify the tools shipmate works
with.
