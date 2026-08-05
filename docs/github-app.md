# GitHub App setup (one-time)

shipmate's comment-ops path (`shipmate apply <env>` in a PR comment) needs a
private GitHub App to mint a short-lived `workflow_dispatch` token — events
created with `GITHUB_TOKEN` never trigger other workflows, so the manual
pre-merge apply cannot be kicked off with the default token. The same App
also authors every apply check, the `shipmate / gate` commit status, the
sticky plan/result comments, and drift issues — installation tokens minted
fresh per job, never a long-lived credential in the workflow. The bot
identity `shipmate[bot]` is derived automatically from the App name
(`shipmate`) once it's registered.

This is a runbook, not a tutorial: run the commands in order, once per GitHub
org that will use comment-ops.

## Prerequisites

- `gh` CLI, authenticated as an org owner (`gh auth status`).
- Admin rights on the org that will own the App.
- This repo checked out locally (`app/manifest.json` is read by the steps below).

## 1. Run the manifest flow (browser)

GitHub App registration via a manifest is a browser POST, not an API call.
Build a self-submitting HTML form from `app/manifest.json` and open it:

```bash
ORG=<your-org>   # e.g. ship-iac

python3 - "$ORG" <<'PY' > /tmp/shipmate-app-manifest.html
import json, sys
org = sys.argv[1]
manifest = json.load(open("app/manifest.json"))
print(f"""<!doctype html>
<form id="f" action="https://github.com/organizations/{org}/settings/apps/new?state=shipmate-setup" method="post">
<input type="hidden" name="manifest" value='{json.dumps(manifest)}'>
</form>
<script>document.getElementById("f").submit()</script>
""")
PY

# Open the file in a browser (pick the one for your OS):
open /tmp/shipmate-app-manifest.html          # macOS
xdg-open /tmp/shipmate-app-manifest.html      # Linux
start /tmp/shipmate-app-manifest.html         # Windows (cmd)
```

Confirm creation in the GitHub UI. GitHub redirects to
`https://github.com/organizations/<org>/settings/apps/<slug>?code=<code>` —
copy the `code` query-param value; it is single-use and short-lived.

## 2. Convert the code to credentials

```bash
MANIFEST_CODE=<code-from-the-redirect> \
GITHUB_REPOSITORY=<org>/shipmate \
python3 scripts/register-app
```

This calls `gh api -X POST app-manifests/$MANIFEST_CODE/conversions`, then
stores, on `GITHUB_REPOSITORY` (typically the App-owning repo itself, e.g.
`<org>/shipmate`):

- `SHIPMATE_APP_ID` — repo **variable** (app id; not secret).
- `SHIPMATE_APP_PRIVATE_KEY` — repo **secret** (PEM private key) — kept here
  only as the one place the PEM lives outside your local disk, alongside the
  App settings page.

**Do not re-run this against a consumer repo to "install" the key there.**
That would store a plain repo secret, readable by any branch's workflow —
exactly the shape steps 5–6 exist to replace. Copy the PEM from here (or
re-download it from the App settings) into each consumer repo's
`shipmate-engine` **environment** secret instead (steps 5–6, below).

## 3. Upload a logo (optional but recommended)

The manifest flow leaves the App with GitHub's default gray-box avatar. To
give it a recognizable identity in check-run lists, PR comments, and the
installations page: App settings (`.../settings/apps/shipmate`) → **Display
information** → **Upload a logo**. Purely cosmetic — everything above works
without it.

## 4. Install the App on each repo that will use comment-ops

The App must be **installed** (separately from being registered) on every
repo that runs `comment-ops.yml` / `dispatch`, e.g. the sample repos:

```
https://github.com/organizations/<org>/settings/apps/shipmate/installations
```

Click **Install**, choose **Only select repositories**, and pick:

- `repo-example-stacks`
- `repo-example-folders`
- `repo-example-workspaces`

(and any other consumer repo that wires up comment-ops). Add repos to the
installation later from the same page as new consumer repos come online.

## 5. Create the `shipmate-engine` environment

`SHIPMATE_APP_PRIVATE_KEY` is a secret **on this environment**, never a
repository or org secret — see §Key-exposure boundary below for why that
scoping is what keeps the key out of a branch-authored workflow. Create it
once per consumer repo, with a deployment branch policy naming exactly the
default branch:

```bash
ORG=<org>
REPOS="repo-example-stacks repo-example-folders repo-example-workspaces"

for NAME in $REPOS; do
  REPO="$ORG/$NAME"
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
done
```

The per-repository guard matters: a typo'd name or a repository you cannot read
leaves `DEFAULT_BRANCH` empty, and without it the `PUT` has already created the
environment with `custom_branch_policies: true` while the `POST` writes a policy
named `""` (or 422s) — exactly the fail-closed environment the next paragraph
warns about, discovered only when that repository's first apply check never
completes. Re-running the loop over an already-onboarded repository makes the
`POST` fail with "name has already been taken", which is harmless — the policy is
already there.

Reading the default branch per repository rather than hardcoding `main` is the
point: the policy must name **that repository's** default branch, and a policy
naming a branch that does not exist fails closed — the apply-completion job is
denied the key and apply checks never complete.

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
`gh secret set --env` writes instead of that one org-secret write. Both are
loops, below.

## 6. Set the approvers team + propagate credentials

Each consumer repo needs `SHIPMATE_APPROVERS_TEAM` (the GitHub team slug whose
members may run `shipmate apply`) plus the app id/key from step 2. `gh` cannot read
back a secret's value once set (GitHub never exposes it), so keep the PEM from
`register-app`'s conversion around (or re-download it from App settings) until
every consumer repo has it.

Per-repo, in one pass over the consumer list:

```bash
ORG=<org>
REPOS="repo-example-stacks repo-example-folders repo-example-workspaces"
TEAM=<approvers-team-slug>          # may differ per repo; set it per repo either way
APP_ID=<app-id-from-step-2-output>

KEY=$(cat shipmate-app.private-key.pem)
if [ -z "$KEY" ]; then
  echo "shipmate-app.private-key.pem is missing or empty — not touching any repository" >&2
  exit 1
fi

for NAME in $REPOS; do
  REPO="$ORG/$NAME"
  gh variable set SHIPMATE_APPROVERS_TEAM --repo "$REPO" --body "$TEAM"
  gh variable set SHIPMATE_APP_ID --repo "$REPO" --body "$APP_ID"
  gh secret set SHIPMATE_APP_PRIVATE_KEY --repo "$REPO" --env shipmate-engine \
    --body "$KEY"
  # The environment secret is only scoping if no repository secret of the same
  # name survives it: an environment secret is withheld from jobs that do not
  # name the environment, but a repository secret is readable by any workflow on
  # any branch without naming anything. Ignore the error when there was none.
  gh secret delete SHIPMATE_APP_PRIVATE_KEY --repo "$REPO" 2>/dev/null || true
done
```

**Confirm the repository secret is gone**, because nothing else will tell you —
listing a repository's secrets needs a permission the App manifest does not
declare, so `shipmate doctor` cannot see this and reports all clear on a
repository whose environment is shaped correctly while the key is still
branch-readable (`docs/hardening.md` #16):

```bash
for NAME in $REPOS; do
  echo "== $NAME"
  gh secret list --repo "$ORG/$NAME"
done
```

`SHIPMATE_APP_PRIVATE_KEY` must not appear in that output; it should appear only
under the environment (`gh secret list --repo "$ORG/$NAME" --env shipmate-engine`).

`SHIPMATE_APP_ID` (a variable, not a secret) **may** also be set once at the
**org** level with restricted visibility, so every consumer repo inherits it
without a per-repo copy (`SHIPMATE_APPROVERS_TEAM` may still differ per repo,
so set it per-repo as above):

```bash
gh variable set SHIPMATE_APP_ID --org <org> --visibility selected \
  --repos "repo-example-stacks,repo-example-folders,repo-example-workspaces" \
  --body "<app-id-from-step-2-output>"
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
   ORG=<org>
   REPOS="repo-example-stacks repo-example-folders repo-example-workspaces"

   KEY=$(cat new-key.pem)
   if [ -z "$KEY" ]; then
     echo "new-key.pem is missing or empty — not touching any repository" >&2
     exit 1
   fi

   for NAME in $REPOS; do
     gh secret set SHIPMATE_APP_PRIVATE_KEY --repo "$ORG/$NAME" --env shipmate-engine \
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
  the request loses that one probe and nothing else — see §Re-approve after
  permission changes. The
  App mints a fresh installation token per job and authors: every
  `apply / <stack> / <env>` check (create pending, complete on apply), the
  aggregate `shipmate / gate` commit status, the sticky plan comment, the
  `shipmate doctor` sticky report, the apply result comments, and drift
  issues. The plan matrix job's own `<stack> / <env>` auto check-run stays
  on the `github-actions` identity — it's the job's own check-run, not
  something a separate API call creates, so there's nothing for the App to
  author there.
- No webhook events (`default_events: []`, `hook_attributes.active: false`) —
  comment-ops is triggered by `on: issue_comment` in the consumer repo's own
  workflow, not by the App receiving a webhook.
- Not public (`public: false`) — this App is installed only on repos your org
  controls.

## Re-approve after permission changes

Expanding `default_permissions` in `app/manifest.json` (as this project did
to add `checks`/`statuses`/`issues`) does not take effect immediately for an
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

- **A `pull_request`-triggered job can never reach the key.** Its ref is
  `refs/pull/<n>/merge` — the ephemeral merge ref GitHub builds for the pull
  request — which matches no named-branch pattern a deployment branch policy
  can express. This holds regardless of which repository opened the pull
  request, so it is also why `plan.yml` (a `pull_request` workflow by
  construction) carries no App key anywhere in it: putting one there would
  satisfy no policy and only widen the blast radius for nothing.
- **The jobs that can reach the key all run at the default-branch ref.** The
  trusted `summary` workflow (`.github/workflows/summary.yml`) is triggered
  by `workflow_run` against the just-completed plan run and is itself defined
  on the default branch — a `workflow_run` job runs at the ref of the
  workflow *file*, not the PR head, so it satisfies the policy. The apply and
  deploy paths reach the key the same way: `workflow_dispatch` from
  comment-ops, or `push` to the default branch. Nothing that starts from
  arbitrary branch content ever does.
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
- The `summary` workflow adds one more explicit guard: it refuses to act when
  `github.event.workflow_run.head_repository.full_name` differs from
  `github.repository` — a fork's plan run. A fork's plan run is still a
  `pull_request` run, so the ref-mismatch argument above already keeps it
  from the key; this guard exists so that fact doesn't have to be re-derived
  every time the trigger or ref evaluation changes.

What none of this defends against is a change to the trusted workflow files
themselves (`summary.yml`, `apply.yml`, and the rest) landing on the default
branch, where they *would* satisfy the environment's policy. That path runs
through an ordinary pull request and merge — no `pull_request`-triggered job
is ever in a position to skip review and reach the key directly, unlike the
old repository-secret model. The backstop there is **`require_code_owner_review`**
on the branch ruleset (`docs/hardening.md` #4): a GitHub App cannot be a
CODEOWNER, so the App itself can never approve a change to its own trust
boundary — a human owner has to.

Push access to a consumer repository is still meaningful authority: it lets
someone author the pull request that proposes such a change and, on a
sole-maintainer repository with `required_approving_review_count: 0`, merge
it too (see `docs/hardening.md` §1 and §3–5). It is no longer, by itself,
enough to read the key outright the way an unreviewed branch push once was.

---

**Trademarks.** Terramate is a trademark of Terramate GmbH; Terraform is a
trademark of HashiCorp; OpenTofu is a project of the Linux Foundation. shipmate
is an independent project and is not affiliated with, endorsed by, or sponsored
by any of them; their marks are used only to identify the tools shipmate works
with.
