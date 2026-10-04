# Drift detection

**Optional.** Nothing else in shipmate depends on this workflow. Adopt it when
you want to know that real infrastructure has moved away from the code, and read
[What it costs](#what-it-costs) before you do.

A nightly cron fans out over all stacks × environments — not the changed set
— and plans each one. A separate `issues` job then turns
those results into GitHub Issues: one labelled `drift` Issue per drifted stack ×
environment, titled `drift: <env> / <stack>`, updated in place while the drift
persists and closed with a "Drift resolved" comment on the next clean run of
its cell. An open `drift` Issue whose title starts with `drift: ` and names no
cell of the sweep is closed too, with a comment that its stack or environment
left the sweep; an Issue titled any other way is never closed. That close runs
only when the sweep planned the default branch's current head: a re-run of an
old sweep, or an older sweep finishing after a newer one, leaves those Issues
open with a notice, and a head it cannot read fails the run. A sweep with no cells at
all skips the `issues` job, so after deleting every stack close its Issues by
hand. The lookup is over open Issues only, so drift that returns later
opens a fresh Issue rather than reopening the closed one.
To reach Slack, subscribe GitHub's Slack app
(`/github subscribe <owner>/<repo> issues workflows`): it posts Issue opens and
closes and workflow runs, not an update to an Issue that stays open.

A cell whose plan attempt did not succeed is not treated as clean: `drift-cell`
records `plan_ok: false`, and `actions/drift-issues` skips that cell entirely,
leaving any open Issue for it untouched rather than auto-closing it.

## The workflow

The nightly sweep is the `drift` job of `.github/workflows/shipmate.yml`
([`getting-started.md`](getting-started.md) §The workflow file), which
`scripts/onboard` writes pinned. Two things reach it: that file's `schedule`
trigger, and a `workflow_dispatch` carrying `verb: drift`.

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

**The nightly sweep is also where a stale table entry surfaces.** It is one of
the three paths that scan the whole tree, so it is where the engine warns that
the environment table declares an environment no stack tags — a leftover entry,
or a typo in a key. It reports a `needs` predecessor matching no tag the same
way: it is inert, and the warning names what it therefore fails to do. It also
names a workload an environment's `workloads` lists and no stack in that
environment tags. A pull request introducing that typo says nothing about it:
the plan path sees only the changed set
([`../CONTRACT.md`](../CONTRACT.md) §Environment table).

## What it costs

`build-matrix` runs with `all-stacks: "true"` and an empty `base-sha`, so the
matrix is every stack × environment in the repository, every night — runner
minutes scale with the full matrix, not the changed set. Each cell is a
`tofu init` plus a `tofu plan` against real state, which also means real backend
and provider API traffic on that schedule. The knobs are the cron expression and
how many environments you tag stacks into. A sweep is one matrix, held to the
same 256-cell limit as a plan run: above it `detect` refuses the sweep before
any cell starts. A repository above the limit gets no drift sweep and cannot split one.

## Every sweep covers every cell

Every stack is listed and has to carry an `env/*` tag, so a sweep fails on an
untagged stack anywhere in the tree — the repo-wide backstop
([`../CONTRACT.md`](../CONTRACT.md) §Tag grammar).

`build-matrix` refuses two stack paths in one environment that slug alike
(`net/edge` and `net-edge` both render `plan.<env>.net-edge`) over the cells a
run produces, so the nightly sweep is what makes that check repo-wide
([`../CONTRACT.md`](../CONTRACT.md) §Plan artifacts). A plan run catches such a
pair only when it changes both.
