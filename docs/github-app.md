# GitHub App setup (one-time)

shipmate's comment-ops path (`shipmate apply <env>` in a PR comment) needs a
private GitHub App to mint a short-lived `workflow_dispatch` token — events
created with `GITHUB_TOKEN` never trigger other workflows, so the manual
pre-merge apply cannot be kicked off with the default token. The bot identity
`shipmate[bot]` is derived automatically from the App name (`shipmate`) once
it's registered.

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

## 3. Install the App on each repo that will use comment-ops

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

## 4. Set the approvers team + propagate credentials

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

## 5. Rotate the private key (on suspicion of compromise)

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
  `members: read`. No `checks` permission — check-run writes stay on
  `GITHUB_TOKEN` (`checks: write`), since check runs are only updatable by
  the app that created them and every workflow in a repo shares the
  `github-actions` identity.
- No webhook events (`default_events: []`, `hook_attributes.active: false`) —
  comment-ops is triggered by `on: issue_comment` in the consumer repo's own
  workflow, not by the App receiving a webhook.
- Not public (`public: false`) — this App is installed only on repos your org
  controls.

---

**Trademarks.** Terramate is a trademark of Terramate GmbH; Terraform is a
trademark of HashiCorp; OpenTofu is a project of the Linux Foundation. shipmate
is an independent project and is not affiliated with, endorsed by, or sponsored
by any of them; their marks are used only to identify the tools shipmate works
with.
