# GitHub App setup (one-time)

A prerequisite of [`getting-started.md`](getting-started.md#required--plan)
§Required — plan, not an optional extra: without the App that tier cannot mint a
token, so no gate status and no apply checks.

shipmate's comment-ops path (`shipmate apply <env>` in a PR comment) needs a
private GitHub App to mint a short-lived `workflow_dispatch` token — events
created with `GITHUB_TOKEN` never trigger other workflows, so the manual
pre-merge apply cannot be kicked off with the default token. The same App
also authors every apply check, the `shipmate / gate` commit status, the
sticky plan/result comments, and drift issues — installation tokens minted
fresh per job, never a long-lived credential in the workflow. The bot
identity is derived automatically from the App name once it's registered, so
an App named `shipmate-acme` comments as `shipmate-acme[bot]`.

This is a runbook, not a tutorial: run the commands in order. Steps 1–3 register
the App once per GitHub org; steps 4–6 onboard one repository, and are written
for the repository you are setting up now. Onboarding several at once is the
same commands in a loop — see the appendix.

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
first — so the `code` never leaves the machine and there is nothing to copy.
Confirm the registration in the GitHub UI when the browser lands on it; the
terminal continues by itself. §2 covers what the command then stores.

**Edit the name.** GitHub App names are unique **across all of GitHub**, and
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

- `SHIPMATE_APP_ID` — a repository **variable** on `--repo` (typically the
  App-owning repo itself, e.g. `<org>/shipmate`). The app id is not a secret.
  It also prints it, as `App created: id=… slug=…`.
- The private key — the file named by `--out`, which must not already exist:
  the command refuses rather than overwrite, because the key it replaced could
  never be minted again. It is created mode `0600` on Linux and macOS. **On
  Windows the mode is not applied at all** — the file inherits the directory's
  ACLs — so run the command from a directory only you can read.

**No repository secret is created**, and nothing else on this page creates one
either: a repository secret is readable by any workflow on any branch, while the
`shipmate-engine` environment secret §5 and §6 place the key in is scoped to one
ref. That is the whole key-exposure boundary, described at the end of this page.

Keep the `--out` file: §6 reads it for every consumer repository, and `gh`
cannot read a secret's value back once set. Shred it once every repository has
it (`shred -u shipmate-app.private-key.pem` or equivalent). If you lose it
before then, generate a replacement — App settings → **Private keys** →
**Generate a private key** — rather than re-registering; an App holds several
keys and each one mints valid tokens, so generating one invalidates nothing.

## 3. Upload a logo (optional but recommended)

The manifest flow leaves the App with GitHub's default gray-box avatar. To
give it a recognizable identity in check-run lists, PR comments, and the
installations page: App settings (`.../settings/apps/shipmate`) → **Display
information** → **Upload a logo**. Purely cosmetic — everything above works
without it.

## 4. Install the App on your repository

The App must be **installed** (separately from being registered) on the
repository that runs `comment-ops.yml` / `dispatch`:

```
https://github.com/organizations/<org>/settings/apps/shipmate/installations
```

Click **Install**, choose **Only select repositories**, and pick your
repository. Add more from the same page as further consumer repos come online.

## 5. Create the `shipmate-engine` environment

`SHIPMATE_APP_PRIVATE_KEY` is a secret **on this environment**, never a
repository or org secret — see §Key-exposure boundary below for why that
scoping is what keeps the key out of a branch-authored workflow. Create it once
in your repository, with a deployment branch policy naming exactly the default
branch:

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
while the `POST` writes a policy named `""` (or 422s) — exactly the fail-closed
environment the next paragraph warns about, discovered only when that
repository's first apply check never completes. Re-running this against an
already-onboarded repository makes the `POST` fail with
"name has already been taken", which is harmless — the policy is already there.

Reading the default branch rather than hardcoding `main` is the point: the
policy must name **that repository's** default branch, and a policy naming a
branch that does not exist fails closed — the apply-completion job is denied the
key and apply checks never complete.

No reviewers on this environment — it exists to scope a secret to a ref, not
to gate a human decision (`docs/hardening.md` #16). `shipmate doctor` checks
both that this environment exists and that its policy actually names the
default branch.

**What this costs across N repositories.** Key *creation* is per App, not per
repository — `docs/hardening.md` #13 asks for one App per trust domain, and one
key serves every repository in it. Placement is per repository either way: the
alternative, an org secret with `--visibility selected`, needs its repository
list edited for each new repo. So onboarding a repository under this scoping is
three API calls — the `PUT` and the `POST` above plus the `gh secret set --env`
in §6 — against one repository-list edit per repository for the org secret, whose
value itself is written once for the whole org; and rotation becomes N
`gh secret set --env` writes instead of that one org-secret write. Both scale as
loops — see the appendix.

## 6. Set the approvers team + propagate credentials

Each consumer repo needs `SHIPMATE_APPROVERS_TEAM` (the GitHub team slug whose
members may run `shipmate apply`) plus the app id/key from step 1. `gh` cannot read
back a secret's value once set (GitHub never exposes it), so this step reads the
`shipmate-app.private-key.pem` step 1 wrote — keep that file until every
consumer repo has it.

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

**Confirm the repository secret is gone**, because nothing else will tell you —
listing a repository's secrets needs a permission the App manifest does not
declare, so `shipmate doctor` cannot see this and reports all clear on a
repository whose environment is shaped correctly while the key is still
branch-readable (`docs/hardening.md` #16):

```bash
gh secret list --repo "$REPO"
```

`SHIPMATE_APP_PRIVATE_KEY` must not appear in that output; it should appear only
under the environment (`gh secret list --repo "$REPO" --env shipmate-engine`).

`SHIPMATE_APP_ID` (a variable, not a secret) **may** also be set once at the
**org** level with restricted visibility, so every consumer repo inherits it
without a per-repo copy (`SHIPMATE_APPROVERS_TEAM` may still differ per repo,
so set it per-repo as above):

```bash
gh variable set SHIPMATE_APP_ID --org <org> --visibility selected \
  --repos "<repo>,<repo>" \
  --body "<app-id-from-step-1-output>"
```

`SHIPMATE_APP_PRIVATE_KEY` cannot follow it there: environment secrets are
scoped to one repository's environment, so it has to be set per-repo as
above — one more reason step 5 (creating the environment) has to happen in
every consumer repo, not just once for the org.

## 7. Rotate the private key (on suspicion of compromise)

1. In the App settings (`.../settings/apps/shipmate`), under **Private keys**,
   click **Generate a private key**. GitHub downloads a new PEM; the old key(s)
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
3. Back in App settings, **delete** the old private key so it can no longer
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
  Both clear on Accept — see §Re-approve after permission changes. The
  App mints a fresh installation token per job and authors: every
  `apply / <stack> / <env>` check (create pending, complete on apply), the
  aggregate `shipmate / gate` commit status, the sticky plan comment, the
  `shipmate doctor` sticky report, the apply result comments, and drift
  issues. The plan matrix job's own `<stack> / <env>` auto check-run stays
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
effect immediately for an
already-installed App. GitHub puts the wider grant in a **pending request**
that an org owner must approve:

```
https://github.com/organizations/<org>/settings/apps/shipmate/installations
```

Open the installation, review the pending permission request, and **Accept**
it. Until that happens, API calls using the new scopes (e.g. the App's
`statuses: write` gate POST) fail with a permission error even though the
manifest and the installed App's token both look correct — the gap is the
un-approved request, not a code or config bug.

## Key-exposure boundary

**Why this exists at all:** a server-hosted GitHub App keeps its private key on
its own service, so the key is never in reach of the repository's contributors.
shipmate has no service, so the key lives in your repository — which makes
*where* it lives in the repository the entire security boundary. That is the
trade the README's "Why setup is not two clicks" section describes, stated
concretely below.

`SHIPMATE_APP_PRIVATE_KEY` is a secret on the **`shipmate-engine` GitHub
Environment**, not a repository or org secret — that environment's
deployment branch policy is a custom policy naming the default branch only
(`docs/hardening.md` #16; `shipmate doctor` checks both that the environment
exists and that its policy actually says so). GitHub evaluates a deployment
branch policy against the ref the triggering job runs at, which is what does
the actual work here:

- **On the plan path, the deployment branch policy is not what holds the
  line — the workflow file is.** Under `pull_request_target` *every* job in the
  plan run, the checkout-bearing `plan` matrix job included, evaluates at
  `refs/heads/<base>` and therefore satisfies the policy. And the `plan` job's
  `environment:` is branch-authored data: it is `matrix.environment`, which
  `build-matrix` derives from `env/*` Terramate tags in the head checkout, so a
  pull request that tags a stack `env/shipmate-engine` produces a plan cell
  naming an environment of its choosing (`shipmate-engine-plan` with the
  documented `-plan` suffix on that binding, the bare `shipmate-engine` in a
  repository that shares one environment between plan and apply). What makes that inert is that `pull_request_target`
  runs the **base** copy of `plan.yml`, and in the base copy the only place
  `secrets.SHIPMATE_APP_PRIVATE_KEY` is named is inside the called reusable
  workflow — a branch author cannot add a secret reference. A dispatched
  `shipmate plan` is the same shape one ref along: every job evaluates at the
  ref the dispatch named (the default branch) and runs that copy of the file,
  never the pull request's own.

  **The constraint that follows: no job in `plan.yml` other than the `summary`
  call may reference a `shipmate-engine` secret.** Adding one hands it to a plan
  cell whose environment the branch chooses.
- **A `push` to a non-default branch cannot reach the key** — measured, not
  inferred, such a job is refused before its first step, because a branch ref
  matches no pattern the policy names.
- **The jobs that can reach the key all run at the default-branch ref.** The
  plan workflow's automatic trigger is `pull_request_target`, which evaluates at
  the base branch ref rather than the pull request head, so its trusted `summary`
  job — the engine's reusable `.github/workflows/summary.yml` — satisfies the
  policy. Its second trigger, the `workflow_dispatch` a commented
  `shipmate plan` sends, is dispatched on the default branch and satisfies the
  policy the way `push` does; the dispatch body states a pull request **number**
  and nothing else, so no ref a commenter picks decides which workflow file runs.
  It reads the key from this environment, which resolves in the *calling*
  repository, so the caller passes the secret by name and holds nothing itself
  (that is also what makes a consumer in another organization work). The apply and
  deploy paths reach the key the same way: `workflow_dispatch` from
  comment-ops, or `push` to the default branch. Nothing that starts from
  arbitrary branch content ever does.
- **The one job that holds the key runs no repository content.** The reusable
  summary workflow has no checkout step, and a consumer cannot add one: they
  call the workflow, they do not own its steps. Reaching the key from a
  pull-request-side trigger is safe only in that shape — see `CONTRACT.md`
  §Post-plan topology and `docs/hardening.md`.
- The *token* minted from the key is still readable in plaintext by any step
  in the job that mints it, same as before the key moved — the environment
  boundary controls **which jobs can mint one**, not what a job does with it
  once minted. The `integration_id`-pinned gate ruleset
  (`docs/branch-protection.md`) is what defends against a token minted inside
  one of those trusted jobs being used to forge a `shipmate / gate` status
  (via a supply-chain compromise reached through that job), and against any
  *other* identity — `GITHUB_TOKEN`'s `github-actions` identity, or a
  different GitHub App — posting a status under the same context, which
  without the pin would satisfy the required check outright.
- The `summary` workflow's job `if:` is load-bearing rather than
  belt-and-braces: it refuses when the head repository the caller states
  (`head-repo`) differs from `github.repository`, when the caller states the
  pull request is a draft that nobody explicitly asked to plan (`is-draft`
  without `on-demand`), and when any of those inputs is absent or empty — an
  unstated fact is a refusal, so a caller can only fail this closed. The fork
  clause yields to no trigger; only the draft clause is widened by a requested
  plan. Under
  `pull_request_target` a fork's pull request *does* reach the base ref, so
  nothing else would stop that job; and the environment admits a draft's run,
  whose plan jobs an autoplan skips, so without the second clause it would write
  a gate over a plan that never ran (a *requested* plan of a draft does run, and
  `on-demand` is how the caller says so). Being on the job, a refusal creates no
  deployment at all.

What none of this defends against is a change to the trusted workflow files
themselves (`summary.yml`, `apply.yml`, and the rest) landing on the default
branch, where they *would* satisfy the environment's policy. That path runs
through an ordinary pull request and merge — no `pull_request`- or
`pull_request_target`-triggered job that checks out branch content is ever in a
position to skip review and reach the key directly, unlike the
old repository-secret model. The backstop there is **`require_code_owner_review`**
on the branch ruleset (`docs/hardening.md` #4): a GitHub App cannot be a
CODEOWNER, so the App itself can never approve a change to its own trust
boundary — a human owner has to.

Push access to a consumer repository is still meaningful authority: it lets
someone author the pull request that proposes such a change and, on a
sole-maintainer repository with `required_approving_review_count: 0`, merge
it too (see `docs/hardening.md` §1 and §3–5). It is no longer, by itself,
enough to read the key outright the way an unreviewed branch push once was.

## Appendix: onboarding several repositories at once

Steps 5 and 6 in a loop. Nothing about them changes per repository except the
repository, so the guards read the same way — with one difference: a repository
whose default branch cannot be read is skipped rather than aborting the run, so
one unreachable repository does not strand the rest half-onboarded. Read the
PEM once, before the loop, so a wrong filename fails immediately instead of
writing an empty secret to every repository.

```bash
REPOS="<owner>/<repo> <owner>/<repo>"
TEAM=<approvers-team-slug>          # may differ per repo; set it per repo either way
APP_ID=<app-id-from-step-1-output>

KEY=$(cat shipmate-app.private-key.pem)
if [ -z "$KEY" ]; then
  echo "shipmate-app.private-key.pem is missing or empty — not touching any repository" >&2
  exit 1
fi

for REPO in $REPOS; do
  DEFAULT_BRANCH=$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)
  if [ -z "$DEFAULT_BRANCH" ]; then
    echo "skipping $REPO: could not read its default branch" >&2
    continue
  fi
  gh api -X PUT "repos/$REPO/environments/shipmate-engine" --input - <<'JSON'
{ "deployment_branch_policy": { "protected_branches": false, "custom_branch_policies": true } }
JSON
  gh api -X POST "repos/$REPO/environments/shipmate-engine/deployment-branch-policies" \
    -f name="$DEFAULT_BRANCH"

  gh variable set SHIPMATE_APPROVERS_TEAM --repo "$REPO" --body "$TEAM"
  gh variable set SHIPMATE_APP_ID --repo "$REPO" --body "$APP_ID"
  gh secret set SHIPMATE_APP_PRIVATE_KEY --repo "$REPO" --env shipmate-engine \
    --body "$KEY"
  # Removes any same-named repository secret, which would otherwise stay
  # readable by any workflow on any branch — see §6 for why. No error when
  # there was none.
  gh secret delete SHIPMATE_APP_PRIVATE_KEY --repo "$REPO" 2>/dev/null || true
done
```

The `continue` is what makes the skip safe: it comes before the `PUT`, so a
repository whose default branch could not be read is left with no environment at
all rather than one carrying an empty branch policy.

Then confirm, per repository, that no repository-level
`SHIPMATE_APP_PRIVATE_KEY` survived — `shipmate doctor` cannot check this for
you (§6):

```bash
for REPO in $REPOS; do
  echo "== $REPO"
  gh secret list --repo "$REPO"
done
```

---

**Trademarks.** Terramate is a trademark of Terramate GmbH; Terraform is a
trademark of HashiCorp; OpenTofu is a project of the Linux Foundation. shipmate
is an independent project and is not affiliated with, endorsed by, or sponsored
by any of them; their marks are used only to identify the tools shipmate works
with.
