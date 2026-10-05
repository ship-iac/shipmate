# Drift detection

Nothing else in shipmate depends on drift detection, but a repository with no
drift workflow file never has a stack checked against real infrastructure, and
`shipmate doctor` warns about it. Read [What it costs](#what-it-costs) before
you add one.

Each drift workflow file is one sweep: its cron fans out over the stack ×
environment cells its `tags` query selects, every cell when it sets none — not
the changed set — and plans each one. A separate `issues` job then turns
those results into GitHub Issues: one labelled `drift` Issue per drifted stack ×
environment, titled `drift: <env> / <stack>`, updated in place while the drift
persists and closed with a "Drift resolved" comment on the next clean run of
its cell. An open `drift` Issue whose title starts with `drift: ` and names no
stack × environment cell left in the repository is closed too, by any sweep
whatever its query, with a comment that its stack or environment left the
repository; an Issue titled any other way is never closed. That close runs
only when the sweep planned the default branch's current head: a re-run of an
old sweep, or an older sweep finishing after a newer one, leaves those Issues
open with a notice, and a head it cannot read fails the run. A sweep that plans
no cell — every stack deleted, or a query matching nothing — skips the `issues`
job and closes nothing, so after deleting every stack close its Issues by
hand. The lookup is over open Issues only, so drift that returns later
opens a fresh Issue rather than reopening the closed one.
To reach Slack, subscribe GitHub's Slack app
(`/github subscribe <owner>/<repo> issues workflows`): it posts Issue opens and
closes and workflow runs, not an update to an Issue that stays open.

A cell whose plan attempt did not succeed is not treated as clean: `drift-cell`
records `plan_ok: false`, and `actions/drift-issues` skips that cell entirely,
leaving any open Issue for it untouched rather than auto-closing it.

## The workflow

The sweep is the `drift` job of `.github/workflows/shipmate-drift.yml`
([`getting-started.md`](getting-started.md) §The workflow file), which
`scripts/onboard` writes pinned beside `shipmate.yml`. Two things reach it:
that file's `schedule` trigger, and a `workflow_dispatch` of that file
(`gh workflow run shipmate-drift.yml`), which takes no input.

The engine's jobs run on `ubuntu-latest` unless the `drift` job passes a
`runs_on:` input — the published fence omits it, as
[repo-example-stacks-aws](https://github.com/ship-iac/repo-example-stacks-aws)
does. Pass it only for a different label your plan actually offers; one it does
not leaves every job waiting for a runner that never arrives.

**The state path is read from `tofu init`, not configured.** A local backend's
state is restored from the path `tofu init` records before each drift cell
plans; a remote backend owns its state and the restore is skipped
([`../CONTRACT.md`](../CONTRACT.md) §State backend).

**`contents: read`, `id-token: write` and `actions: read` are all required** on
the `drift` job, cloud credentials or not. A called workflow's permissions are
capped at the `uses:` boundary: the engine's `drift` matrix job requests the
first two, its `issues` job the first and the third, and a calling job granting
less kills the run at startup with no job and no log.

**The credential split is the point.** The engine's `drift` matrix job binds the
plan environment of the cell it is planning — the bare `<env>` for an env
holding `shared = true`, `<env>-plan` otherwise
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
missing, `drift-issues` would not see the cell — no Issue and a
green nightly run over real drift. Both gated jobs also refuse to run off the
default branch, resolved from the API by `detect` rather than read from the
`schedule` event payload.

**The fork and head-commit refusals do not apply here, and the engine says so
once.** `build-matrix` refuses by default a run that states neither its head
repository — the fork refusal on the plan path
([`hardening.md`](hardening.md) §"Contributors without push access") — nor the
commit it is planning, because a `workflow_dispatch` of a sweep and a dispatched
plan are the same event and the event name cannot tell them apart. A sweep has
neither to state, so engine `drift.yml` passes `no-pull-request: "true"`. It is
set in engine-owned YAML, in the one workflow with no pull-request context at
all; no consumer file can set it, and no plan run carries it.

The engine runs `aws-actions/configure-aws-credentials` inside the `drift` job,
in the same position as on the plan path, gated on a role resolving non-empty.
That role is the `aws.plan` of the identity the environment names in the table,
or its `aws.apply` for an environment holding `shared = true`, per workload where
that field varies by workload. An environment with no entry, or naming no
identity, or whose identity sets no such role, resolves no role and the step is
skipped. A drift cell runs only
default-branch code, so what an over-scoped role costs here is write access
where a read-only one belongs; the role's trust-policy claim condition is what
refuses it. See
[`aws.md`](aws.md) §Where the credentials step goes and
[`hardening.md`](hardening.md) §7–9.

**Every sweep is also where a stale table entry surfaces.** The drift path is
one of the three paths that scan the whole tree, so it is where the engine warns that
the environment table declares an environment no stack tags — a leftover entry,
or a typo in a key. It reports a `needs` predecessor matching no tag the same
way: it is inert, and the warning names what it therefore fails to do. It also
names a workload an environment's `workloads` lists and no stack in that
environment tags. A pull request introducing that typo says nothing about it:
the plan path sees only the changed set
([`../CONTRACT.md`](../CONTRACT.md) §Environment table).

## What it costs

`build-matrix` runs with `all-stacks: "true"` and an empty `base-sha`, so a
sweep without a `tags` query plans every stack × environment in the repository,
every night — runner minutes scale with the whole tree, not the changed set. Each cell is a
`tofu init` plus a `tofu plan` against real state, which also means real backend
and provider API traffic on that schedule. The knobs are the cron expression and
how many environments you tag stacks into, and how you split the tree across
drift files. A sweep is one matrix, held to the same 256-cell limit as a plan
run, counted after its `tags` query: above it `detect` refuses the sweep before
any cell starts and names the remedy, splitting it across more drift files.

## Scoping a sweep

One drift workflow file is one sweep, with its own crons, its own matrix and its
own 256-cell limit. To spread the tree across several sweeps, or past the limit,
copy the `shipmate drift` fence under another filename and `name:`, set its
crons, and give its `drift` job a `tags` query:

```yaml
with:
  tags: env/prod-eu,env/prod-us
```

Write the query in block form, as above: in a `{ }` flow mapping the comma
splits the value, and GitHub refuses the file.

`scripts/onboard` reconciles only `shipmate-drift.yml`: it writes that file
again when it is missing, and reports it `differs` once you add a query. Keep
that file as one of your sweeps; the other drift files are yours.

The query grammar:

- `,` separates OR clauses, and `:` separates the terms of one clause, which
  must all match. `:` binds tighter: `env/dev-eu:workload/app,env/sbx` is
  (`env/dev-eu` and `workload/app`) or `env/sbx`.
- A term is a tag in its on-disk form (`env/dev-eu`, never `env:dev-eu`),
  matched exactly and case-sensitively. Whitespace around a term is dropped.
- A cell matches against its stack's tags minus every `env/*` tag other than its
  own. A stack tagged `env/dev-eu` and `env/dev-us` under the query `env/dev-eu`
  sweeps only its `dev-eu` cell.
- An empty term (a trailing `,` or a doubled separator) fails the run.
- A term no stack carries makes its own clause match nothing, and a notice names
  it; the other clauses still sweep. A query matching no cell is an empty sweep
  with a notice, so a drift file can precede its first tagged stack.

Keep the queries disjoint. A cell two queries select is planned by both sweeps,
which update the same Issue, and two sweeps starting in the same minute can race
and open a duplicate.

A cell outside every query is never checked. Narrowing a query strands the open
Issues of the cells it drops: no sweep plans those cells and they are still in
the repository, so nothing closes the Issues. Close them by hand.

Every sweep runs the repo-wide checks over the whole tree, whatever its query
selects:

- A stack with no `env/*` tag anywhere in the tree fails the sweep — the
  repo-wide backstop ([`../CONTRACT.md`](../CONTRACT.md) §Tag grammar).
- Two stack paths in one environment that slug alike (`net/edge` and `net-edge`
  both render `plan.<env>.net-edge`) fail the sweep
  ([`../CONTRACT.md`](../CONTRACT.md) §Plan artifacts). A plan run catches such
  a pair only when it changes both.
- A stack carrying two `workload/*` tags fails the sweep.
- Under `layout = "tf_vars"`, an environment a stack tags with no entry in the
  environment table fails the sweep.
- The unused-entry warnings above name what the table declares and no stack
  tags.
