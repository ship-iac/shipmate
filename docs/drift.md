# Drift detection

**Optional.** Nothing else in shipmate depends on this workflow. Adopt it when
you want to know that real infrastructure has moved away from the code, and read
[What it costs](#what-it-costs) before you do.

A nightly cron fans out over all stacks × environments — not the changed set
— and plans each one, or over a slice of them when the run states a `tags`
filter ([Scoping a sweep](#scoping-a-sweep)). A separate `issues` job then turns
those results into GitHub Issues: one labelled `drift` Issue per drifted stack ×
environment, titled `drift: <env> / <stack>`, updated in place while the drift
persists and closed with a "Drift resolved" comment on the next clean run that
covers it. The lookup is over open Issues only, so drift that returns later
opens a fresh Issue rather than reopening the closed one.

A cell whose plan attempt did not succeed is not treated as clean: `drift-cell`
records `plan_ok: false`, and `actions/drift-issues` skips that cell entirely,
leaving any open Issue for it untouched rather than auto-closing it.

## The workflow

The drift workflow is a shim: a `schedule`, a `workflow_dispatch`, a
`permissions:` block, and one job named `shipmate` calling the engine's
reusable drift workflow. Transcribed from
[repo-example-stacks-aws](https://github.com/ship-iac/repo-example-stacks-aws)
`.github/workflows/drift.yml`.

```yaml
name: shipmate · drift
on:
  schedule:
    - cron: "17 3 * * *"   # nightly, off-peak
  workflow_dispatch:
permissions:
  contents: read
jobs:
  shipmate:
    name: shipmate
    uses: ship-iac/shipmate/.github/workflows/drift.yml@<engine-sha>  # see the latest release
    permissions:
      contents: read
      id-token: write
      actions: read
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
    with:
      state_suffix: ""
      # Empty covers every cell. Split the sweep by adding more files, one tag query each.
      tags: ""
```

The engine's jobs run on `ubuntu-latest` unless the shim passes a `runs_on:`
input; the sample repositories pass `ubuntu-slim`. A label your plan does not
provide leaves every job waiting for a runner that never arrives.

**`state_suffix` is required and may be `""`.** `""` — what the fence above
pastes, because the AWS sample uses a remote backend — means the backend owns
the state and the engine's state restore step is skipped. A local backend
materialized in the working tree passes the path segment under each stack
directory where its state file lives instead: `repo-example-stacks` passes
`.state`, and each drift cell then restores `<stack>/.state` before planning
([`../CONTRACT.md`](../CONTRACT.md) §State backend). Pasting `""` there plans
every cell against no state and reports the whole repository as drifted, every
night. It is the same value your `plan.yml` and `apply.yml` shims pass.

**`id-token: write` and `actions: read` are both required**, cloud credentials
or not. A called workflow's permissions are capped at the `uses:` boundary: the
`drift` job requests the first, the `issues` job the second, and a shim granting
less kills the run at startup with no job and no log.

**The credential split is the point.** The engine's `drift` matrix job binds the
plan environment of the cell it is planning — the bare `<env>` for an env listed
in `SHIPMATE_SHARED_ENVS`, `<env>-plan` otherwise
([`../CONTRACT.md`](../CONTRACT.md) §Env model) — and holds no App credential.
All it does with its result is upload one
`drift-summary.<env>.<stack-slug>` artifact holding a `cell.json`. The `issues`
job binds `shipmate-engine`, and `actions/drift-issues` mints the App
installation token there — it is the only job on the drift path that does. A plan
environment has to stay policy-free for planning to work, so a job naming one is
reachable from a feature branch. Keeping the App key out of it means a
compromised drift cell cannot open, edit or close an Issue.

That split makes the artifact the only channel by which a cell's drift
becomes visible. `drift-cell`'s compose and upload steps run `if: always()` and
are deliberately not `continue-on-error`: were the artifact allowed to go
missing, `drift-issues` would not see the cell — no Issue, no Slack, and
a green nightly run over real drift. Both gated jobs also refuse to run off the
default branch, resolved from the API by `detect` rather than read from the
`schedule` event payload.

**The fork and head-commit refusals do not apply here, and the engine says so
once.** `build-matrix` refuses by default a run that states neither its head
repository — the fork refusal on the plan path
([`hardening.md`](hardening.md) §"Contributors without push access") — nor the
commit it is planning, because a `workflow_dispatch` of this workflow and a
dispatched plan are the same event and the event name cannot tell them apart. A
sweep has neither to state, so engine `drift.yml` passes
`no-pull-request: "true"`. It is set in engine-owned YAML, in the one workflow
with no pull-request context at all; no consumer file can set it, and no plan
run carries it.

The engine runs `aws-actions/configure-aws-credentials` inside the `drift` job,
in the same position as on the plan path, gated on `AWS_ROLE_ARN` — or
`AWS_ROLE_ARN_<WORKLOAD>` for a cell carrying a `workload/<name>` tag — being set
on the plan environment that cell binds, and skips it when neither is. See
[`aws.md`](aws.md) §Where the credentials step goes.

## Slack (optional)

Slack needs no line in your shim. The engine's `issues` job passes
`slack-webhook: ${{ vars.SLACK_WEBHOOK }}` to `drift-issues`, and `vars` inherit
into a called workflow, so setting that one GitHub variable is the whole
configuration. Set it at repo or org level, or on the `shipmate-engine`
environment that job binds (for `vars.`, most specific wins: environment
overrides repository overrides organization). The input's default is the empty
string, so with `SLACK_WEBHOOK` unset the expression renders empty and no
notification is attempted — nothing else changes.

When it is set, `drift-issues` POSTs one message per cell that is drifted on this
run (the same cells whose Issue it created or updated), a single-line
`:ocean: drift detected: <env> / <stack>`. A rejected or timed-out webhook is not
a warning. The cell is recorded as failed and the job exits nonzero at the end,
naming it, so a revoked or rotated URL cannot leave every nightly run green while
no notification reaches anyone. Per-cell failures do not abort the remaining
cells.

## What it costs

`build-matrix` runs with `all-stacks: "true"` and an empty `base-sha`, so the
matrix is every stack × environment in the repository, every night — runner
minutes scale with the full matrix, not the changed set. Each cell is a
`tofu init` plus a `tofu plan` against real state, which also means real backend
and provider API traffic on that schedule. The knobs are the cron expression,
how many environments you tag stacks into, and the `tags` filter, which narrows
one run to a slice of the matrix — [Scoping a sweep](#scoping-a-sweep).

## Scoping a sweep

The engine's drift workflow takes an optional `tags` query that narrows the
cells one run covers. Empty — the default, and the fence above — covers every
cell.

Tags are matched in their on-disk form: `env/dev-eu`, not `env:dev-eu`.
Terramate forbids `:` inside a tag value, which is what frees `:` to be an
operator here. `,` is OR, `:` is AND, and `:` binds tighter, so
`env/dev-eu:workload/app,env/dev-us` is *(dev-eu AND app) OR dev-us*.

A cell is matched against its stack's tags with every `env/*` tag other than
its own removed. `env/dev-eu` therefore selects the dev-eu cells: a stack
tagged both `env/dev-eu` and `env/prod-eu` contributes its dev-eu cell to an
`env/dev-eu` sweep, not both of them.

**The query narrows cells, not the stacks that are inspected.** Every stack is
still listed and still has to carry an `env/*` tag. A scoped sweep fails on an
untagged stack exactly as an unscoped one does, so the repo-wide backstop
([`../CONTRACT.md`](../CONTRACT.md) §Tag grammar) survives being scoped.

**Three things fail the run rather than quietly narrowing it:**

- an empty term — a trailing comma or a doubled separator;
- a term no stack carries;
- a query that matches no cell.

A sweep that silently covered nothing would skip the `drift` and `issues` jobs
and look exactly like a healthy quiet night — every night, for as long as the
typo lives.

**Only the drift path can carry the filter.** `build-matrix` refuses a `tags`
query outside a `no-pull-request: "true"` run, whatever `all-stacks` says, and
engine `drift.yml` is the one workflow that passes it: engine `plan.yml` neither
takes a `tags` input nor states that it has no pull request. A filter on the plan
path would drop changed stacks from the matrix — a dropped stack gets no plan
cell and so no apply check, `shipmate / gate` greens over it, and the change
merges and never applies.

**Issues close per cell, on the run that covers that cell.** `drift-issues` acts
only on the cells this run produced and never sweeps open `drift` Issues for
absence, so an Issue belonging to a cell outside this run's slice is left
untouched. Under a spread sweep, resolved drift is closed by the slice that owns
it, on that slice's next run.

### Spreading a sweep across the week

One workflow file per slice — `drift-<slice>.yml` — each a copy of the shim
above with three things changed: the `name:`, the single `cron:`, and the
literal `tags:` value in its `with:` block. A repository variable cannot differ
per file, which is why `tags` is an input rather than one.

```yaml
name: shipmate · drift · dev-eu
on:
  schedule:
    - cron: "17 3 * * 1"
  workflow_dispatch:
permissions:
  contents: read
jobs:
  shipmate:
    name: shipmate
    uses: ship-iac/shipmate/.github/workflows/drift.yml@<engine-sha>  # see the latest release
    permissions:
      contents: read
      id-token: write
      actions: read
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
    with:
      state_suffix: ""
      tags: "env/dev-eu"
```

One cron and one literal query per file: the run's own workflow name is then its
slice — in the Actions list, in a re-run, and in a notification.

**Do not design the spread around the minute a cron names.** GitHub Actions
delays `schedule` triggers under load, by hours rather than minutes: in
shipmate's own sample repository a `17 3 * * *` nightly has started at 04:05Z,
09:18Z, 10:11Z, 14:18Z and 15:28Z on different days. Spread slices across
*days*, and assume neither that two crons an hour apart produce runs an hour
apart nor that one slice has finished before the next is due.

**A fully spread schedule retires the whole-tree slug check.** `build-matrix`
refuses two stack paths in one environment that slug alike (`net/edge` and
`net-edge` both render `plan.<env>.net-edge`) over the cells a run produces. The
unscoped nightly is what makes that check repo-wide
([`../CONTRACT.md`](../CONTRACT.md) §Plan artifacts). Slices alone catch such a
pair in no sweep at all: the first plan run that changes both still refuses, so
nothing applies under the wrong plan, but the warning arrives in a pull request
instead of a nightly. Keep the unscoped `drift.yml` on a weekly cron to keep it.

### An ad-hoc scoped sweep

This shape is for the unscoped `drift.yml`. Under its `on:`, replace the bare
`workflow_dispatch:` with a `tags` input, and forward that input from the shim's
`with:` block. The input's default is empty, so a dispatch that leaves it blank
sweeps every cell:

```yaml
workflow_dispatch:
  inputs:
    tags:
      description: "Optional tag query, e.g. env/dev-eu:workload/app"
      required: false
      default: ""
```

```yaml
with:
  state_suffix: ""
  tags: ${{ inputs.tags }}
```

A `drift-<slice>.yml` keeps its literal `tags:` value instead — its scope is the
slice it is named for, and its manual trigger re-runs that slice.
