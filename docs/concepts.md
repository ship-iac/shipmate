# Concepts

How shipmate works, for a reader deciding whether to adopt it or working out why
it behaved a certain way. [`../CONTRACT.md`](../CONTRACT.md) is the source for
exact semantics: check names, the environment model, tag grammar, pinning. This
page is the explanation behind them.

## Fan-out (stack × environment)

shipmate treats each Terramate stack and each target environment as
independent units of work. A repository with, say, three stacks (network,
database, app) and two environments (staging, production) fans out into up
to six plan/apply units, each tracked and checked independently. A change to
one stack in one environment proceeds, or is blocked, without being entangled
with unrelated stack/environment combinations. Waves of applies respect
dependency ordering only where a real dependency exists.

## Checks-first

Every unit of work — a plan, an apply — surfaces as its own GitHub check
with a predictable, parseable name (see `CONTRACT.md`). Checks are the
primary UI: reviewers approve or block a pull request by looking at check
status and check output, not by reading raw workflow logs. An aggregate
check rolls up the fan-out into a single required status so branch
protection rules stay simple even as the number of underlying units grows.

## Comment-ops

Humans drive shipmate for a pull request through PR comments — `shipmate
apply <env>` and friends — rather than through a bespoke UI or external tooling.
The pull request is already the unit of review, so the commands, their replies
and their results stay in it, with an auditable history of who asked for what
and when.

A comment cannot start a workflow on its own: events created with the default
`GITHUB_TOKEN` never trigger other workflows. So a private GitHub App mints the
short-lived token that dispatches the apply workflow from a comment. The same
App authors every check, status, comment and issue that crosses a workflow-run
boundary: the apply checks (a check run can be completed only by the App that
created it), `shipmate / gate`, the sticky plan and `doctor` comments, the
apply result comments and the drift issues.

An apply runs only for a commenter with write or admin permission, on a pull
request that is not a draft, is mergeable and satisfies the branch ruleset's
review policy, and
only against a reviewed plan for the pull request's current head.

[`CONTRACT.md`](../CONTRACT.md) §Comment-ops holds the grammar, what each verb
may do, who may run it, and the apply requirements, with the full list of
App-authored surfaces. [`github-app.md`](github-app.md) is the one-time App
setup.

## PR comment commands

`shipmate help`, commented on any pull request, lists the commands; the verb
table in [`CONTRACT.md`](../CONTRACT.md) §Comment-ops is the same list, with
each verb's arguments and authorization. A refused or failed command's reply,
and an apply or `doctor` comment whose verdict is not 🟢, end by pointing at
`shipmate help`, so the commands are discoverable from the pull request itself.

## Dynamic environments

Environments are not hardcoded into workflow YAML. An environment is
defined by its `[environments.<name>]` table in `.github/shipmate.toml`, the
GitHub Environments named after it (`<env>-plan` and `<env>-apply`, or one shared
`<env>` — `../CONTRACT.md` §Env model) plus tags applied to the stacks that
belong to it. Adding a new environment is a data change (add the table entry,
create its Environments, tag the relevant stacks), never a workflow code change.
The table entry merges on its own pull request first, because the engine reads
the table from the default branch and the tags from the feature branch
(`../CONTRACT.md` §Adding and removing an environment). The number of
environments a repository supports is therefore independent of the complexity of
its CI configuration.

## The plan path

A push to a pull request that is not a draft, or a `shipmate plan` comment,
plans the changed stacks, one cell per stack × environment, and each cell is its own `shipmate / <stack> / <env>` check. The
shape is the same across repo layouts and across repositories. What differs per
layout is which identity variables a cell's job environment carries
(`TF_VAR_env` and `TF_VAR_region`, `TF_WORKSPACE`, or nothing) and whether a
role is named for that environment at all. Both come from the repository's
environment table on the default branch.

A plan cell runs branch-authored code, so it holds no App credential. One
trusted job per run, bound to the `shipmate-engine` GitHub Environment and
checking out nothing, holds the App key and authors what the pull request
shows: the pending `apply / <stack> / <env>` checks, the sticky plan comment
and `shipmate / gate` ([`github-app.md`](github-app.md) §Key-exposure
boundary). Fork pull requests are refused before any plan cell starts
([hardening](hardening.md) §Contributors without push access).

Plan text lives in each plan job's step summary, one click from its check,
rather than in a separate check-run: the matrix job already emits the check of
that name, so a second one would duplicate it. The aggregate `gate` is a commit
status, not a check-run: a status is commit-scoped, so it cannot be
misattributed to a stale check-suite when a commit carries two plan runs
(draft→ready, or a rapid re-push) — a check-run can, silently blocking the
merge forever.

[`CONTRACT.md`](../CONTRACT.md) §Post-plan topology describes the jobs of the
plan workflow, the triggers that select it, and the trust decision;
§Check names holds the `shipmate / ` namespace; §Plan comment holds when a
comment is written and when it is left standing.

To make the gate enforce apply-before-merge, configure branch protection to
require `shipmate / gate`; see [`branch-protection.md`](branch-protection.md).
For who can make the engine act — push access is authority over it — and the
settings that bound that, see [`hardening.md`](hardening.md).

## Deploy and drift

shipmate follows a serverless plan→store→review→apply model: the reviewed plan
is stored and applied verbatim, with no server or database. An apply, pre-merge
by comment or post-merge by the `deploy` job, downloads the reviewed `.otplan`,
verifies it against the commit and the variables it was planned with, and
applies that exact plan. It never re-plans: a plan that state has moved under
is refused rather than refreshed. A cell already applied has a completed apply
check, so the post-merge deploy skips it. Applies run in waves along the
Terramate `after` DAG ([`CONTRACT.md`](../CONTRACT.md) §Fan-out, §Env apply
order), and a comment-triggered apply and a post-merge deploy share one
concurrency group per stack × environment, so they never race on the same cell.

The nightly `drift` job plans every stack × environment and opens one GitHub
Issue per drifted cell; setup is in [drift.md](drift.md).

**Remote state and cloud credentials.** Nothing configures state. A local
backend's state is cached at the path `tofu init` records; a remote backend (for
example S3) owns its state, and the engine's state restore/save steps are
skipped. Credentials are opt-in per environment: an environment that names no
`[identities.<name>]` table resolves no role, and no cloud credential enters
the job, which is how the sample repos run credential-free. The plan and drift
cells assume the identity's `aws.plan` role, or its `aws.apply` in a shared
environment, while running branch-authored code —
bounded by that role's trust policy and nothing in the engine. See
[`CONTRACT.md`](../CONTRACT.md) §State backend and §AWS OIDC for the semantics,
including why every calling job but `comment-ops` grants `id-token: write`, and
[`hardening.md`](hardening.md) §7–9 for the exposure.

One model note vs a hosted service: with no server-side queue, GHA can drop a
superseded deploy run — its stacks stay pending + visible and are recovered
by re-running that deploy.
