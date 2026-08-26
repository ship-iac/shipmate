# Drift detection

**Optional.** Nothing else in shipmate depends on this workflow; adopt it when
you want to know that real infrastructure has moved away from the code, and read
[What it costs](#what-it-costs) before you do.

A nightly cron fans out over **all** stacks × environments — not the changed set
— and plans each one. A separate `issues` job then turns those results into
GitHub Issues: one labelled `drift` Issue per drifted stack × environment,
titled `drift: <env> / <stack>`, updated in place while the drift persists and
closed with a "Drift resolved" comment on the next clean run. The lookup is over
**open** Issues only, so drift that returns later opens a fresh Issue rather
than reopening the closed one.

A cell whose plan attempt did not succeed is not treated as clean: `drift-cell`
records `plan_ok: false`, and `actions/drift-issues` skips that cell entirely,
leaving any open Issue for it untouched rather than auto-closing it.

## The workflow

Transcribed from
[repo-example-stacks-aws](https://github.com/ship-iac/repo-example-stacks-aws)
`.github/workflows/drift.yml`. It is a consumer-owned workflow, not a call into a
reusable engine workflow. Being a transcription, it pins the sample's
`runs-on: ubuntu-slim`; use whichever runner label your own plan offers
(`ubuntu-latest` is the safe default), or the jobs wait for a runner that never
arrives.

```yaml
name: shipmate · drift
on:
  schedule:
    - cron: "17 3 * * *"   # nightly, off-peak
  workflow_dispatch: {}     # manual trigger for acceptance
permissions:
  contents: read
jobs:
  detect:
    # Resolves the default branch from the API rather than trusting
    # github.event.repository.default_branch: whether that field is
    # populated on this workflow's `schedule` trigger is exactly the
    # question this guard must NOT depend on (see `drift`/`issues` below).
    # This job itself runs no consumer code and holds no secret, so it is
    # intentionally left ungated -- a `gh workflow run drift.yml --ref
    # <branch>` dispatch on a feature branch fails visibly at the two gated
    # jobs below instead of this job silently doing nothing.
    runs-on: ubuntu-slim
    outputs:
      matrix: ${{ steps.m.outputs.matrix }}
      empty: ${{ steps.m.outputs.empty }}
      default_branch: ${{ steps.default_branch.outputs.default_branch }}
    steps:
      - id: default_branch
        shell: bash
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          echo "default_branch=$(gh api "repos/$GITHUB_REPOSITORY" --jq .default_branch)" >> "$GITHUB_OUTPUT"
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { fetch-depth: 0 }
      - uses: ship-iac/shipmate/actions/setup@<engine-sha>  # see the latest release
        with:
          terramate-version: ${{ vars.TERRAMATE_VERSION }}
          tofu-version: ${{ vars.TOFU_VERSION }}
      - id: m
        uses: ship-iac/shipmate/actions/build-matrix@<engine-sha>  # see the latest release
        with:
          base-sha: ""
          all-stacks: "true"
          no-pull-request: "true"
  drift:
    needs: detect
    if: ${{ needs.detect.outputs.empty == 'false' && github.ref == format('refs/heads/{0}', needs.detect.outputs.default_branch) }}
    runs-on: ubuntu-slim
    permissions: { contents: read, id-token: write }
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.detect.outputs.matrix) }}
    environment: ${{ matrix.environment }}-plan
    name: ${{ matrix.stack }} / ${{ matrix.environment }}
    env:
      TF_VAR_env: ${{ vars.TF_VAR_env }}
      TF_VAR_region: ${{ vars.TF_VAR_region }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { fetch-depth: 0 }
      - uses: ship-iac/shipmate/actions/setup@<engine-sha>  # see the latest release
        with:
          terramate-version: ${{ vars.TERRAMATE_VERSION }}
          tofu-version: ${{ vars.TOFU_VERSION }}
      - uses: aws-actions/configure-aws-credentials@e6de054238d6b7531b4efff3b6587d9aade6a06c # v6.2.3
        with:
          role-to-assume: ${{ vars.AWS_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - uses: ship-iac/shipmate/actions/drift-cell@<engine-sha>  # see the latest release
        with:
          stack: ${{ matrix.stack }}
          stack-name: ${{ matrix.stack }}
          env: ${{ matrix.environment }}

  # Completes the credentialed work drift-cell no longer does: authors/closes
  # the drift Issues, from downloaded drift-summary cell artifacts, and holds
  # the only App key on the drift path.
  issues:
    needs: [detect, drift]
    # `detect.outputs.empty == 'false'` replaces the download's
    # `continue-on-error`. The empty-matrix case (nothing to plan this run)
    # downloads nothing to match the pattern, and drift-issues returns
    # silently on an empty cells directory -- indistinguishable from a LOST
    # artifact, which would then green a drift run that opened no Issue and
    # closed none. `detect` already tells the two apart, so an empty matrix
    # skips this job outright and a failed download now fails it.
    if: ${{ always() && needs.detect.outputs.empty == 'false' && github.ref == format('refs/heads/{0}', needs.detect.outputs.default_branch) }}
    runs-on: ubuntu-slim
    environment: shipmate-engine
    permissions: { actions: read }
    steps:
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with: { pattern: drift-summary.*, path: drift }
      - uses: ship-iac/shipmate/actions/drift-issues@<engine-sha>  # see the latest release
        with:
          app-id: ${{ vars.SHIPMATE_APP_ID }}
          private-key: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
          slack-webhook: ${{ vars.SLACK_WEBHOOK }}
```

**`no-pull-request: "true"` is what makes the nightly run allowed at all.**
`build-matrix` refuses by default a run that states neither its head repository
— the fork refusal on the plan path
([`hardening.md`](hardening.md) §"Contributors without push access") — nor the
commit it is planning, and both keep to the stated values rather than the event
name, because a `workflow_dispatch` of this workflow and a dispatched plan are
the same event. A drift run has neither to state, and this input is how it says
so. It belongs **only** in a workflow with no pull-request context at all: in a
plan wrapper it would turn both refusals off for every pull request. Omit it here
and the nightly goes red on `head-repo`.

**A local-backend repository must add `state-path` to the `drift-cell` step.**
The fence above is the AWS sample's, and a remote backend needs none. On a local
backend the drift wrapper is what builds the path — `repo-example-stacks` passes
`state-path: ${{ matrix.stack }}/.state`, matching the `<stack>/<state_suffix>`
its apply path passes ([`../CONTRACT.md`](../CONTRACT.md) §State backend). Omit
it and every cell plans against no state and reports the whole repository as
drifted, every night.

**The credential split is the point.** The `drift` matrix job binds the plan
environment of the cell it is planning
(`environment: ${{ matrix.environment }}-plan` above — drop the suffix if **every**
env in the repository shares one environment between plan and apply, or carry the
engine's mode expression if only some do:
[`../CONTRACT.md`](../CONTRACT.md) §Env model) and holds **no App credential**.
All it does with its result is upload one
`drift-summary.<env>.<stack-slug>` artifact holding a `cell.json`. The `issues`
job binds `shipmate-engine`, and `actions/drift-issues` mints the App
installation token there — it is the only job on the drift path that does. A plan
environment has to stay policy-free for planning to work, so a job naming one is
reachable from a feature branch; keeping the App key out of it means a
compromised drift cell cannot open, edit or close an Issue.

That split makes the artifact the **only** channel by which a cell's drift
becomes visible. `drift-cell`'s compose and upload steps run `if: always()` and
are deliberately not `continue-on-error`: were the artifact allowed to go
missing, `drift-issues` would simply not see the cell — no Issue, no Slack, and
a green nightly run over real drift. Both gated jobs also refuse to run off the
default branch, resolved from the API by `detect` rather than read from the
`schedule` event payload.

The `aws-actions/configure-aws-credentials` step is the consumer's own, in the
same position as on the plan path — see
[`aws.md`](aws.md) §Where the credentials step goes.

## Slack (optional)

The sample wires Slack through one input on `drift-issues`:
`slack-webhook: ${{ vars.SLACK_WEBHOOK }}`, a GitHub variable you may set at
repo, org, or on the `shipmate-engine` environment the `issues` job binds
(for `vars.`, most specific wins: environment overrides repository overrides
organization). The input's default is the empty string, so with `SLACK_WEBHOOK`
unset the expression renders empty and no notification is attempted — nothing
else changes.

When it is set, `drift-issues` POSTs one message per cell that is drifted on this
run (the same cells whose Issue it just created or updated), a single-line
`:ocean: drift detected: <env> / <stack>`. A rejected or timed-out webhook is not
a warning: the cell is recorded as failed and the job exits nonzero at the end,
naming it, so a revoked or rotated URL cannot leave every nightly run green while
no notification reaches anyone. Per-cell failures do not abort the remaining
cells.

## What it costs

`build-matrix` runs with `all-stacks: "true"` and an empty `base-sha`, so the
matrix is every stack × environment in the repository, every night — runner
minutes scale with the **full** matrix, not the changed set. Each cell is a
`tofu init` plus a `tofu plan` against real state, which also means real backend
and provider API traffic on that schedule. The knobs are the cron expression and
how many environments you tag stacks into; there is no partial-matrix input.
