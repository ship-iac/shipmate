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
stores:

- `SHIPMATE_APP_ID` — repo **variable** (app id; not secret).
- `SHIPMATE_APP_PRIVATE_KEY` — repo **secret** (PEM private key).

on `GITHUB_REPOSITORY`. Re-run with a different `GITHUB_REPOSITORY` (or use the
org secret/variable propagation in step 4) to make the credentials available to
consumer repos too.

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

## 5. Set the approvers team + propagate credentials

Each consumer repo needs `SHIPMATE_APPROVERS_TEAM` (the GitHub team slug whose
members may run `shipmate apply`) plus the app id/key from step 2. `gh` cannot read
back a secret's value once set (GitHub never exposes it), so keep the PEM from
`register-app`'s conversion around (or re-download it from App settings) until
every consumer repo has it.

Per-repo (repeat for each consumer repo):

```bash
REPO=<org>/repo-example-stacks   # repeat per consumer repo
TEAM=<approvers-team-slug>

gh variable set SHIPMATE_APPROVERS_TEAM --repo "$REPO" --body "$TEAM"
gh variable set SHIPMATE_APP_ID --repo "$REPO" --body "<app-id-from-step-2-output>"
gh secret set SHIPMATE_APP_PRIVATE_KEY --repo "$REPO" --body "$(cat shipmate-app.private-key.pem)"
```

Or once at the **org** level with restricted visibility, so every consumer
repo inherits `SHIPMATE_APP_ID` / `SHIPMATE_APP_PRIVATE_KEY` without a
per-repo copy (`SHIPMATE_APPROVERS_TEAM` may still differ per repo, so set it
per-repo as above):

```bash
gh variable set SHIPMATE_APP_ID --org <org> --visibility selected \
  --repos "repo-example-stacks,repo-example-folders,repo-example-workspaces" \
  --body "<app-id-from-step-2-output>"
gh secret set SHIPMATE_APP_PRIVATE_KEY --org <org> --visibility selected \
  --repos "repo-example-stacks,repo-example-folders,repo-example-workspaces" \
  --body "$(cat shipmate-app.private-key.pem)"
```

## 6. Rotate the private key (on suspicion of compromise)

1. In the App settings (`.../settings/apps/shipmate`), under **Private keys**,
   click **Generate a private key**. GitHub downloads a new PEM; the old key(s)
   remain valid until you delete them.
2. Store the new key everywhere it's used:

   ```bash
   gh secret set SHIPMATE_APP_PRIVATE_KEY --repo "$REPO" --body "$(cat new-key.pem)"
   # or, for org-level secrets:
   gh secret set SHIPMATE_APP_PRIVATE_KEY --org <org> --visibility selected \
     --repos "repo-example-stacks,repo-example-folders,repo-example-workspaces" \
     --body "$(cat new-key.pem)"
   ```
3. Back in App settings, **delete** the old private key so it can no longer
   mint tokens.
4. Shred the local PEM file (`shred -u new-key.pem` or equivalent) once it's
   stored in secrets.

## Reference: what the App can and can't do

- Permissions: `actions: write`, `pull_requests: write`, `contents: read`,
  `members: read`, `checks: write`, `statuses: write`, `issues: write`. The
  App mints a fresh installation token per job and authors: every
  `apply / <env> / <stack>` check (create pending, complete on apply), the
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

The private key is a secret in `SHIPMATE_APP_PRIVATE_KEY`, but the *token*
minted from it is readable in plaintext by any step in the job that mints it
— including the `pull_request`-triggered `summary` job, which runs against
untrusted PR content (the plan text, cell metadata). The
`integration_id`-pinned gate ruleset (see `docs/branch-protection.md`)
defends against two specific threats: a supply-chain compromise reached
*through* that job (a malicious dependency or action stealing the minted
token to forge a `shipmate / gate` status), and a stray/forked workflow
minting its own App token outside the reviewed path. It does **not** defend
against a trusted, write-access insider who already has `secrets: write` on
the repo — that person can read the PEM directly regardless of any ruleset
pin. If your threat model includes malicious insiders with write access,
the App's key does not solve for that; branch protection's pinned
`integration_id` only closes the *external* forgery path.

---

**Trademarks.** Terramate is a trademark of Terramate GmbH; Terraform is a
trademark of HashiCorp; OpenTofu is a project of the Linux Foundation. shipmate
is an independent project and is not affiliated with, endorsed by, or sponsored
by any of them; their marks are used only to identify the tools shipmate works
with.
