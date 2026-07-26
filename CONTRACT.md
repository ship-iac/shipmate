# CONTRACT

This document is the naming, environment, tag-grammar, and pinning contract
that every shipmate action and every consuming repository must follow. Where
a value is marked verbatim, it must be used exactly as written — these
strings are parsed by other parts of the system (check-name matching,
comment-ops, tag-based stack selection) and are not free-form prose.

## Check names

Every plan and apply unit of work reports as its own GitHub check, using
these names verbatim:

- plan job check-run: `<stack> / <env>`
- apply check-run: `apply / <env> / <stack>`

`<env>` and `<stack>` are placeholders substituted with the actual
environment name and the Terramate **stack path** (as emitted by
`terramate list` / `experimental run-graph`, e.g. `stacks/network`) for that
unit of work (for example, `stacks/network / staging` and
`apply / staging / stacks/network`). The check name uses the stack **path**,
never a display name — so the code that *creates* the apply check
(`plan-cell`), *completes* it (`apply-cell`), and *filters the still-pending
queue* (`deploy-detect`, which only ever has the path) all reconstruct the
identical name from the one value they share.

The plan check has **no verb prefix**: it is the plan matrix job's own
auto-generated check-run, whose name is the job's `name:` (`<stack> / <env>`).
The consuming workflow is named `shipmate · plan`, so GitHub's UI already
shows the verb — `shipmate · plan / <stack> / <env>`. The apply check
**keeps** its `apply / ` verb prefix: `plan-cell` creates it pending on the
**same** head SHA that carries the plan job's check, so that prefix is the
disambiguator that keeps the two apart — and keeps `apply-gate` /
`gate-refresh`, which select the pending-apply queue by the `apply / ` prefix,
from ever picking up a plan check. For the same reason `build-matrix` rejects
a stack whose path is exactly `apply` — its plan check `apply / <env>` would
fall inside the apply-check namespace.

This same forward-built string is also the apply-env-level job's own `name:`
(so the job's display name and the apply check-run name coincide), which
gives the apply check-run grammar a second consumer: `scripts/apply-comment`
(see Apply result comment, below) resolves each row's per-cell log link by
matching this name as a **suffix** of the run's job-name listing — a called
reusable workflow's jobs display as `<caller-job> / apply / <env> /
<stack>` — never by reverse-parsing it. No match falls back to the
workflow-run URL.

In addition to the per-unit checks, one aggregate **commit status** rolls up
the full fan-out into a single required status, named verbatim:

- `shipmate / gate`

Branch protection rules should require `shipmate / gate`, not the
individual per-unit checks, so that the set of required checks does not
need to be edited every time a stack or environment is added or removed.

The gate is a commit status rather than a check-run deliberately: a check-run
is bound to a check-suite, and an imperatively-created one attaches to an
arbitrary suite when a commit carries more than one plan run (a draft→ready
transition or a rapid re-push spawns two runs = two suites). The merge
evaluator then reads the live suite, finds no gate there, and blocks the PR
forever while the green gate sits in the stale suite. A commit status is
commit-scoped and immune. (The per-unit `plan`/`apply` checks stay check-runs;
they are not required, so a stale-suite copy is only cosmetic.)

`shipmate / gate` is created (and refreshed on every plan run) by the
`summary` action, and is completed to success by whichever of these happens
first:

- the pre-merge apply path (`gate-refresh`, called from the apply
  workflow's summary job) once **every** `apply / <env> / <stack>` check on
  the PR head is complete — a targeted `shipmate apply <env>` of only some
  environments leaves the gate pending;
- the post-merge deploy, which completes the gate on the merged PR's head
  SHA after its env-level applies finish.

When apply-cell completes an `apply / <env> / <stack>` check, it completes only
the check-run ids that already existed for that name **before its apply began**.
A plan re-run can create a fresh duplicate apply check; a duplicate created
*mid-apply* is therefore left pending (its plan was not applied by this run),
while duplicates that predate the apply are all completed so the gate never
sticks. This keeps `shipmate / gate` from greening on a plan the apply
never used.

## Env model

- One GitHub Environment exists per logical environment (for example,
  `staging`, `production`). The Environment is always the unit of binding,
  apply-gating, protection, and the plan/apply split — **even when it carries
  no variables**. What it injects depends on how the consumer repo models
  environments (its IaC layout):

  | Repo layout | Env identity injected by the GitHub Environment | Mechanism |
  |-------------|--------------------------------------------------|-----------|
  | **DRY / dynamic backend** (one stack config deployed N×; backend path `…/${var.env}/${var.region}/…`) | `TF_VAR_env`, `TF_VAR_region` | OpenTofu variables drive the backend path and resources |
  | **Workspace-per-env** | `TF_WORKSPACE` | OpenTofu auto-selects (and auto-creates) the named workspace |
  | **Folder-per-env/region** (leaf per env×region, hardcoded state) | *none* | env/region are fixed by the leaf's path; each leaf owns its state |

  This is the **DRY model's** injection (`TF_VAR_env`/`TF_VAR_region`) — the
  target for real consumer repos and shipmate's internal adoption. The other
  two are proven-generalization layouts (sample repos
  `repo-example-workspaces` / `repo-example-folders`). Note the folder layout
  trades away shipmate's "add an env = GitHub Environment + tags, zero code"
  property: adding an env there means adding leaf directories (a code change).
  Membership in an environment is always by **tag**, regardless of layout.
- Protected environments (typically anything beyond the lowest-trust
  environment) carry required reviewers configured on the GitHub
  Environment itself, so approval gating is enforced by GitHub, not by
  workflow logic.
- Plan and apply are split into distinct GitHub Environments: plan jobs run
  against `<env>`, apply jobs run against `<env>-apply`. This lets apply
  carry stricter protection rules (required reviewers, wait timers) than
  plan, even though both act against the same logical environment.
- **No env names in workflow YAML — ever.** Workflow files must not
  hardcode `staging`, `production`, or any other environment name. Workflows
  discover environments dynamically from stack tags (see Tag grammar,
  below) and GitHub Environment configuration. Adding a new environment is
  purely a data change: create the GitHub Environment, then tag the stacks
  that belong to it. No workflow YAML is edited to add or remove an
  environment.

## Tag grammar

Two forms of the same concept exist, because Terramate does not permit `:`
in tag values:

- **Conceptual** form (used in documentation, discussion, and design):
  `env:<name>` and `workload:<name>`. For example, `env:staging` or
  `workload:api`.
- **On-disk** form (the literal tag value written into Terramate stack
  configuration, since Terramate forbids `:` in tags): `env/<name>` and
  `workload/<name>`. For example, the stack configuration carries the tag
  `env/staging`, not `env:staging`.

Everywhere this document or any other project document writes `env:<name>`
or `workload:<name>`, it is describing the concept; the literal value that
must appear in Terramate stack tag lists is the `env/<name>` /
`workload/<name>` form. A stack may carry several `env/*` tags at once (for
example, a shared stack tagged both `env/staging` and `env/production`)
when the same stack participates in more than one environment.

## Comment-ops

`shipmate <verb> [env] [tag-filter]` in a PR comment drives shipmate's
comment-based commands. The grammar is strict and anchored — the whole
comment line must match one regex, and the parsed values are never
interpolated into a shell. A comment authored by a bot account (any login
ending `[bot]`) is ignored outright before parsing — a loop guard, since
shipmate's own comments (help output, apply results, the doctor report) can
themselves contain text that matches the command grammar.

| verb | status | args | authorization |
|---|---|---|---|
| `shipmate apply [env]` | active | optional env | apply requirements, below |
| `shipmate doctor` | active | none | none — read-only, open to any commenter |
| `shipmate help` | active | none | none — read-only, open to any commenter |
| `shipmate plan` | reserved | — | — |
| `shipmate destroy` | reserved | — | — |

`plan` and `destroy` are recognized and rejected with a "reserved" message, so
the grammar does not need to change shape when those verbs are implemented. A
verb documented as taking no arguments (`doctor`, `help`) rejects an env or
tag-filter given alongside it with an explicit "takes no arguments" error
rather than silently ignoring the extra token; a tag-filter given to any verb
is likewise rejected as not yet supported rather than silently applied to the
whole environment. An unknown verb's error points the commenter at
`shipmate help`, and so does the "malformed" error for a line that does not
match the grammar at all (a capitalized verb, a stray token, a double space). `scripts/comment-parse`'s `VERBS` registry is the single
source of truth this table is derived from: it drives the parser, the
`--help-markdown` rendering that `shipmate help` posts verbatim (marked with
the HTML comment `<!-- shipmate:help -->`), and the reject-hint text, so the
three cannot drift from each other. Unlike the `:summary` and `:doctor`
markers below, `<!-- shipmate:help -->` is not an upsert key — nothing reads
it back: `shipmate help` posts a fresh comment every time, and the marker
only labels the output as shipmate's own.

`shipmate doctor` posts a consolidated, sticky report — one comment per pull
request, identified by the HTML marker `<!-- shipmate:doctor -->` (distinct
from the plan comment's `<!-- shipmate:summary -->`) and upserted in place the
same way. It combines six live settings probes (gate ruleset, environment
pair existence, environment protection shape, engine action-pin freshness,
approvers-team resolvability, and App installation permission drift — see
`docs/branch-protection.md`) with a harvest of the warning and failure
annotations GitHub already recorded on this commit's workflow runs
(shipmate's own and any other Actions workflow run on that commit;
third-party-app-authored check runs are excluded). Only four of the six
probes can produce a finding from the plan path's own `annotate`-mode
invocation: the approvers-team probe needs the `SHIPMATE_TEAM` environment
variable, which the plan path does not supply, so it silently returns
nothing; the
App-permission-drift probe only has something to report when a
full-manifest permission-set mint was actually attempted, which only
`shipmate doctor` does. Both probes are effectively comment-path-only —
they surface findings only via `shipmate doctor`, never on the plan path's
own annotations.

Those warnings are not read from the sticky plan comment — a plan run still
writes the full plan comment (overview table, per-changed-cell details) but
no longer appends doctor findings to it. Instead, `actions/summary` runs
`scripts/doctor` on every plan run and emits its findings as
workflow-command annotations, verbatim:

- `::warning title=shipmate doctor::<text>` for a misconfiguration,
- `::notice title=shipmate doctor::<text>` for an informational finding.

The `title=shipmate doctor` string is exactly what `shipmate doctor`'s harvest
step filters check-run annotations on, so it never re-reports a finding the
live probes already re-state fresh against current settings — this is
machine-read, not a formatting choice, and a mismatch between the annotate
call and the harvest filter is a regression. `shipmate doctor` is entirely
read-only: it authorizes nothing, writes nothing but its own sticky comment
and an `eyes` reaction on the triggering comment (both read-only verbs get
that acknowledgement as soon as the command is accepted — `rocket` stays
reserved for an authorized `apply`; a reaction that cannot be posted is
ignored), a one-line error comment when it cannot mint an App token, and a
handful of untitled `::warning::` annotations on the gather step's own
degrade paths — an unreadable PR head SHA, no plan-run cell summaries for
this commit, a failed check-runs listing or reduction, a failed per-check
annotations fetch. Those gather-step annotations land on the
`issue_comment` workflow run that is executing `shipmate doctor` itself, at
`github.sha` (this job does no checkout at all — it reads entirely through
`gh api`/`gh run download -R` — so `github.sha` is simply the default
branch's tip, not a checked-out commit), not on the PR head SHA whose check
runs the harvest reads — so there is no self-harvest loop. `shipmate doctor`
never affects `shipmate / gate`.

Because the report enumerates the guardrails a repository is *missing* — an
ungated default branch, an apply environment with no protection rules, the
configured approvers team and whether it resolves, an App installation short of
the manifest's permissions — and because the verb takes no authorization, any
account that can comment on a pull request can obtain that list, and everyone
who can read the pull request can read it. That is intended for a repository
whose readers are the team that owns it; on a repository with **public** pull
requests, restrict who can trigger the comment-ops workflow (for example a
`github.event.comment.author_association` condition on the `issue_comment`
job). shipmate imposes no such restriction itself. `app/manifest.json` declares
`"public": false` for the same reason: the App is registered per organization
and intended for repositories the installing organization controls.

The env is optional for `apply`. A targeted `shipmate apply <env>` applies one
environment; a bare `shipmate apply` applies **every** environment that has a
reviewed plan for the current PR head, in `env_order` env-levels (see Env
apply order, below), **except** environments listed in the Terramate global
`global.shipmate.explicit_envs`. Explicit environments (typically production)
must always be named: their `apply / <env> / <stack>` checks simply stay
pending under a bare apply — so `shipmate / gate` keeps gating the
merge — until someone runs `shipmate apply <env>` for them. An absent global
(or `[]`) means a bare apply targets everything. Malformed `explicit_envs`
shapes (not a list of strings) fail loud, like `env_order`.

A parsed `shipmate apply <env>` command is authorized only when it satisfies
**apply requirements** — named, Atlantis-style, checked in order, each with
its own actionable rejection reason:

- **shipmate team**: the commenter is a member of the configured approvers
  team (checked via a short-lived GitHub App installation token,
  `members:read`);
- **mergeable**: the pull request is mergeable;
- **reviewed**: the pull request satisfies the branch ruleset's review policy
  — GitHub's `reviewDecision` is `APPROVED`, or is null (no review required by
  the ruleset; normalized to the explicit `NONE` sentinel in transit). The
  ruleset (required approving reviews, CODEOWNERS, last-push approval) is the
  single source of review policy; shipmate imposes none of its own. A ruleset
  requiring zero approvals reports no decision even when an approval exists,
  but a `CHANGES_REQUESTED` review still blocks. Any other value — including
  an absent or empty decision — fails closed with a wiring-error reason;
- **undiverged**: a reviewed plan exists for the pull request's **current**
  head SHA (the most recent successful plan run — the automatic plan run on
  pull-request open, autoplan — whose head matches; a plan for an older head
  means new commits landed since — stale, re-plan required).

A bare `shipmate apply` is authorized exactly once, by the same four apply
requirements — one authorization decision covers the whole multi-environment
run. Both forms
dispatch the consumer's single `apply.yml` wrapper; its optional `environment`
input selects the path (set → targeted, empty → bare). Both share the same
App-minted `workflow_dispatch` mechanism and the same per-env
`apply-<env>-<stack>` concurrency groups.

The GitHub App carries this permission set: `actions: write`,
`pull_requests: write`, `contents: read`, `members: read`, `checks: write`,
`statuses: write`, `issues: write`. Beyond minting the `workflow_dispatch`
token for comment-ops (events created with the default `GITHUB_TOKEN` never
trigger other workflows, so a private App is the only way to kick off the
apply workflow from a comment) and reading team membership for
authorization, the App now authors every check/status/comment/issue that
crosses a workflow-run boundary:

- **Apply checks** (`apply / <env> / <stack>`) — created pending by
  `actions/summary`, completed by `actions/apply-cell` — both mint an App
  installation token, so completion works across the create/complete
  workflow-run boundary (check runs are only updatable by the app that
  created them; using the same App on both sides keeps that identity
  consistent).
- **The aggregate gate** (`shipmate / gate`) — created/refreshed by
  `actions/summary`, completed pre-merge by `actions/gate-refresh`, completed
  post-merge inline in `deploy.yml` — all three via App token.
- **The sticky plan comment** — App-authored (marker + any Bot author for the
  lookup, since a consumer org's App bot login may differ from
  `shipmate[bot]`), upserted in place by `actions/summary`.
- **The `shipmate doctor` sticky report** — App-authored (its own marker +
  Bot-author lookup, same posture as the plan comment), upserted in place by
  `actions/comment-ops`.
- **The apply result comment** — posted fresh by `actions/apply-summary` on
  every comment-ops apply run (targeted and bare); unlike the sticky plan
  comment it is never upserted against a marker, so a failure-then-retry
  sequence stays visible across separate comments — an audit trail.
- **Drift issues** — opened/updated/closed by `actions/drift-cell` via App
  token.

The plan matrix job's own `<stack> / <env>` auto check-run is the one
exception: it's the job's own check-run (GitHub creates it for the job
itself), not something a separate API call authors, so it necessarily stays
on the `github-actions` identity regardless of App permissions.

`shipmate apply` and `deploy.yml` share the same per-env, per-stack
`apply-<env>-<stack>` concurrency group, so exactly one apply ever runs
against a given stack × environment at a time, regardless of whether it was
triggered by a pre-merge comment or a post-merge push.

## Consumption

- Consuming repositories and workflows pin every shipmate action **by
  commit SHA**, never by a tag or branch name (for example,
  `uses: <owner>/shipmate/actions/state@<full-commit-sha>`, not `@v1` or
  `@main`). This guarantees that a workflow's behavior cannot change
  without an explicit, reviewed bump of the pinned SHA in the consuming
  repository.
- `.github/workflows/` is protected by a `CODEOWNERS` entry, so changes to
  workflow files (including pin bumps) require review from the designated
  owners before merge.

## Runner prerequisites

- shipmate's actions are composite actions: their steps run under `bash`
  and call standard-library-only Python scripts, `git`, `curl`, `jq`, `openssl`,
  and the `gh` CLI. A runner must therefore provide: `bash`, `python3`
  (Python ≥ 3.11), `git`, `curl`, `jq`, `openssl`, and `gh`.
- Every GitHub-hosted Ubuntu image satisfies this, including the minimal
  `ubuntu-slim` image. Self-hosted runners must preinstall these tools.
- The Python scripts have **no third-party dependencies** — nothing is
  `pip install`ed at runtime, so no Python setup step (or network access
  to a package index) is required or performed.
- Terramate and OpenTofu are **not** assumed to be on the image: the
  `setup` action installs the pinned versions declared by the consuming
  repository (`TERRAMATE_VERSION` / `TOFU_VERSION`).

## Fan-out

- One unit of work is one stack × one environment. A repository with N
  stacks and M environments (accounting for which stacks are tagged into
  which environments) fans out into up to N×M plan units and N×M apply
  units, each with its own check (see Check names, above).
- Plans fan out flat: all applicable plan units for a pull request run
  concurrently, with no ordering dependency between them.
- Applies run in waves: the `after` relationships between Terramate stacks
  form a DAG, and applies execute in topological levels of that DAG — all
  units at one level must complete before the next level's units start —
  so that a stack's applies only wait on the specific stacks it actually
  depends on, not on the entire fan-out.

## Plan artifacts

Each planned stack × environment uploads its reviewed plan under a name built
verbatim as:

- `plan.<env>.<slug>`

where `<env>` is the environment name and `<slug>` is the Terramate stack
**path** with every `/` replaced by `-` (e.g. `stacks/app` → `stacks-app`, so
`(stacks/app, dev-eu)` → `plan.dev-eu.stacks-app`). plan-cell creates it,
apply-cell downloads it, and apply-detect matches it — all three **construct**
the name forward from the `(env, slug)` pair; **no component reverse-parses it**.

The delimiter is `.` and the environment comes first on purpose. Terramate tag
values (the source of every env name) cannot contain `.`, so the first `.`
after the `plan.` prefix is always the env↔slug boundary. This makes the name
unambiguous across all `(slug, env)` pairs — unlike the earlier
`plan-<slug>-<env>` form, where `-` appears in both fields and
`(stacks/app-dev, eu)` and `(stacks/app, dev-eu)` both rendered
`plan-stacks-app-dev-eu`, letting apply-detect enrol the wrong stack into a
wave. A slug may itself contain `.` (a path character); that is harmless
because the name is only ever built forward, never split. Two distinct stack
paths that slug to the same value still collide by construction and fail loud
in apply-detect (rename so the path→`-` slug is unique).

This naming contract is breaking for any in-flight plan artifacts: land the
change when no applies are mid-flight. It also spans two consumer workflow
files pinned independently — `plan.yml` pins `plan-cell` (the uploader) and
`apply.yml` pins the engine's reusable apply workflows, which pin
`apply-cell`/`apply-detect` (the downloader/matcher) internally. Bump both
pins **together** when adopting a build that changes this name: a partial
bump (uploader on the new name, downloader on the old, or vice versa) makes
every apply fail its reviewed-plan download fail-safe until the pins agree.

## Apply summary artifacts

Each attempted-or-blocked apply cell uploads its outcome under a name built
verbatim as:

- `apply-summary.<env>.<slug>`

using the same dot-delimited, env-first grammar as `plan.<env>.<slug>` above
(the `.` delimiter is unambiguous for the same reason: an env name cannot
contain `.`). `apply-cell` uploads it (`if: always()`, so a blocked cell
still reports); `actions/apply-summary` downloads every `apply-summary.*`
artifact for the run with the glob pattern `apply-summary.*`. It contains
verbatim:

- `cell.json` — always present, keys `stack` (display name), `stack_path`
  (Terramate stack path, feeds the check-name construction), `environment`,
  `result` (one of `applied`, `failed`, `blocked`), `reason` (which fail-safe
  blocked it, or why an earlier step failed first; the empty string for
  `applied`/`failed`).
- `apply.txt` — the apply step's combined stdout+stderr, present only when
  the apply step actually ran (absent for a cell blocked before then).

`apply-cell` (writer) and `scripts/apply-comment` (reader, via
`actions/apply-summary`) are pinned by the same SHA in a consumer's
`apply.yml` / `apply-all.yml`, so the schema upgrades atomically; the reader
fails loud on a `cell.json` missing schema keys or carrying an out-of-enum
`result` rather than rendering around pin skew.

## Plan comment

The `summary` action maintains exactly one sticky comment per pull request,
identified by the HTML marker written verbatim as the comment's first line:

- `<!-- shipmate:summary -->`

The comment is edited in place on every plan run (comment lookup is marker +
any Bot author — the shipmate App's bot login is derived from the registered
App name, which a consumer org may have had to slug differently than
`shipmate[bot]`), so GitHub's comment revision history doubles as the audit
trail of previous plans for the PR.

Structure, in order: an overview table (one row per planned stack ×
environment: verdict emoji — 🟢 no changes / 🟡 changes / 🔴 contains
destroys — add/change/destroy counts, and a link to that cell's
`<stack> / <env>` plan-job check run), then one `<details>` section per
**changed** cell containing the rendered plan inside a `diff`-tagged code
fence (change signs moved to column 0; `~` mapped to `!`). Cells with no
changes get a table row only. Check links are built **forward** from the
cell's `(stack-path, environment)` pair using the check-name grammar above;
when the check run cannot be resolved, the link degrades to the workflow-run
URL.

GitHub caps issue-comment bodies at 65,536 characters. The comment is built
to a smaller budget: each changed cell's section degrades, in order, full
plan → truncated plan (cut at a line boundary, with a link to the check run
carrying the full text) → link-only. The overview table is never dropped.
If even the table alone cannot fit the cap, the summary fails loud rather
than posting a truncated table. Plan text is emitted only inside a backtick
fence computed to be longer than any backtick run in the text —
author-controlled plan output cannot escape the fence.

The data feeding the comment ships in the per-cell artifact
`cell-summary.<env>.<slug>` (same dot-delimited, env-first grammar as
`plan.<env>.<slug>`: the name is built forward from the `(env, slug)` pair
exactly like the plan artifact, never reverse-parsed). Consumers download it
with the glob pattern `cell-summary.*`. It contains verbatim:

- `cell.json` — keys `stack` (display name), `stack_path` (Terramate stack
  path, feeds the check-name construction), `environment`, `add`, `change`,
  `destroy` (integers), `changed` (boolean); written by `plan-cell` at plan
  time from `scripts/plan-classify` output — the summary never re-parses
  plan text.
- `plan.txt` — the `tofu show -no-color` rendering of the reviewed plan.

`plan-cell` (writer) and `summary` (reader) are pinned by the same SHA in a
consumer's `plan.yml`, so the schema upgrades atomically; the summary
fails loud on a `cell.json` missing schema keys rather than rendering around
pin skew.

## Apply result comment

Every comment-ops apply run — targeted `shipmate apply <env>` (`apply.yml`)
and bare `shipmate apply` (`apply-all.yml`); the merge-deploy path,
`deploy.yml`, stays comment-less — posts a **fresh** PR comment via
`actions/apply-summary`. Unlike the plan comment above, it is deliberately
**not** upserted against a marker: every run's comment is new, so a
failure-then-retry sequence stays visible as separate comments rather than
overwriting the evidence of the failure — an audit trail.

Structure, in order: a header naming the targeted environment or "(all
environments)"; directly beneath it, a job-level failure line — rendered
only when the job-level `SHIPMATE_RESULTS` outcome signals a failure and no
row already carries `failed`/`blocked` (the common case has its own ❌/🚫
rows and needs no extra line), reusing the header's own wording with a ❌ so
an apply run that dies before any cell reports (a missing/denied
`<env>-apply` environment, a job-level cancel) still surfaces a failure
signal; an overview table (one row per expected cell — status emoji
✅ applied / ⚠️ applied but not recorded / ❌ failed /
🚫 blocked / ⏭️ not attempted, stack, env, `+A ~C -D`
resources parsed from the apply output's last `Apply complete!` line, and a
per-cell log link); one `<details>` section per **attempted** (applied,
failed, or applied-but-not-recorded) cell with the full apply output in a
plain code fence; one
no-fence line per **blocked** cell naming its `reason`. An expected cell
whose artifact never arrived (its wave/env-level was skipped after an
upstream failure, or the run was cancelled before upload) renders as
**not attempted** unless its apply check is already done (see the derivation
rules below) — a table row only; the comment also carries a single note,
once, whenever any cell is not attempted, that those apply checks stay
pending and can be retried — naming `shipmate apply <env>` for a targeted
run, or the bare `shipmate apply` when the run covered all environments.
There is no separate "nothing pending" input: the expected cell set is the
same waves JSON `apply-detect` / `apply-all-detect` already compute, and a
cell counts as attempted when its artifact actually downloaded or its apply
check is already done — the render can never claim nothing is pending while
holding evidence that an apply ran. The footer carries the bare-apply form's
excluded/skipped-environment sentences, a gate-completion sentence (complete
or still-pending, from the gate verdict), and the run link.

Row status is derived from **both** the per-cell artifact and the real state of
that cell's `apply / <env> / <stack>` check on the head SHA, which
`actions/apply-summary` reads with its own App installation token (only checks
authored by the shipmate App count, judged by the newest run per name — the
same predicate the gate and both detects use). The pending apply checks are the
work queue, so they outrank the artifact, which only records what a cell's own
job believed before its check was completed:

- ✅ **applied** — the apply succeeded **and** its check is recorded.
- ⚠️ **applied but not recorded** — the apply succeeded but its check is still
  pending, because `Save state`, the completion-token mint or `Complete the
  apply check` failed (or the job was cancelled) after the cell summary was
  already composed. `shipmate / gate` stays pending, and the stack needs a
  re-plan: state has advanced past the reviewed `.otplan`, so the saved plan is
  stale. The comment carries one note naming the affected cells — capped, with
  any remainder summarized as a count, since the note is part of the header
  block the size fallback cannot shed and the usual cause strands a whole
  matrix at once; the table above always carries a ⚠️ row for every one of
  them. Each such cell keeps its full `<details>` output.
- A cell whose check is **done** but whose artifact never arrived (the
  cosmetic, `continue-on-error` upload dropped) renders ✅ with its output
  unavailable — never ⏭️, and it does not drag the "apply check stays pending"
  note along with it.
- ❌ **failed** / 🚫 **blocked** rows are never re-derived from check state. A
  red row against a completed check means some other run applied that cell:
  the row over-reports, nothing is stranded, and downgrading it would let an
  unrelated run's completed check hide a real failure in this one.

If the check scan is unavailable — an API failure (warned, degraded to no
data), an older pinned `apply-summary` that never wrote the file, or an empty
`SHIPMATE_APP_ID` (warned) — every name reads as *absent*, which means
*unknown*, and the comment falls back to artifact-only status. Absence never
manufactures a ⚠️.

`cell.json`'s `result` grammar is **unchanged** (`applied | failed | blocked`).
⚠️ is a display status derived at render time, never a value `apply-cell`
writes, so the fail-loud validator still rejects an out-of-enum artifact value
as pin skew.

The 65,536-character comment cap and the up-front link-only-space reserve
work the same way as the plan comment's, above, but the truncation direction
is the opposite. A plan diff is read top-down, so the plan comment's section
keeps the head. Apply output is read for its END: a failing apply's fatal
`Error:` line and a successful apply's closing `Apply complete! Resources:
...` line are both the last thing tofu prints, so each attempted cell's
section instead keeps the TAIL — full output → truncated (front cut at a
line boundary, noting that earlier output was elided, with a link to the job
log) → link-only. `apply.txt` itself is read from the end of the file
(bounded — a fixed-size read near the tail, never the whole file) and
decoded permissively: a non-UTF-8 byte anywhere in it becomes a replacement
character instead of aborting the render. Terminal escape sequences (colour
codes and similar) are stripped from that text as it is read, since a
consumer's own `apply` script is not required to disable them. Every
remaining attempted cell's link-only space, and every blocked cell's
one-line reason, are reserved up
front, and the render fails loud rather than post a comment that would
exceed GitHub's cap.

The data feeding the comment ships in the per-cell artifact
`apply-summary.<env>.<slug>` (see Apply summary artifacts, above); per-cell
log links resolve against the run's job-name listing using the
`apply / <env> / <stack>` check-name grammar (see Check names, above), and
degrade to the workflow-run URL on no match.

## Apply-match fingerprint

Each plan stores a fingerprint (`fingerprint.txt`, artifact `external_id` on the
apply check): `sha256` over the sorted JSON of every **non-empty** `TF_VAR_*`
environment variable (name→value) **plus `TF_WORKSPACE` when it is set**.
Ephemeral credential vars (`AWS_*`, etc.) are excluded. A set-but-empty
`TF_VAR_*` is excluded from the payload, so it now hashes identically to that
variable being absent altogether — a flavor that injects nothing and a flavor
that injects an empty string for the same name fingerprint the same way.
`TF_WORKSPACE` is included because it is the workspaces-flavor env identity and
is not a `TF_VAR_*`; without it two environments of a workspaces stack would
fingerprint identically and an apply could match the wrong environment's
reviewed plan. plan-cell and apply-cell use a byte-identical algorithm
(`scripts/plan-classify`). On mismatch, apply fails safe and reports differing
variable **names** only — never values.

## Secrets in published output

Both text surfaces shipmate publishes — the rendered plan `plan.txt` in the
sticky plan comment and the job step summary, and `apply.txt` in the apply
result comment — carry whatever the tool wrote: **shipmate never redacts
either**, and neither is covered by GitHub's log secret masking. (The published
text is not byte-identical — escape sequences are stripped, undecodable bytes
become replacement characters, and over-cap sections are truncated, per Plan
comment and Apply result comment above — but none of that removes a secret.)

Masking applies to the log stream the runner consumes. Both files are written
out of the tool's own stdout (`tofu show > plan.txt`,
`… apply 2>&1 | tee apply.txt`), and the runner never sees the bytes on the
file side of that write, so a value that appears as `***` in the job log is
written unmasked into the artifact and into the comment. **Do not rely on
masking for anything shipmate publishes**, including the step summary — the
comment and the artifact are the file bytes, and nothing redacts them on the
way out. On a public repository the comments are world-readable.

**Redaction is the consumer's job, and OpenTofu's `sensitive` marking is the
mechanism.** A variable declared `sensitive = true`, or a provider attribute
marked sensitive in its schema, is redacted in `tofu show` output, so it does
not reach either comment through the rendered plan or a normal apply. Consumers
must mark every secret-bearing variable and output accordingly. Marking is
best-effort, not a guarantee: it suppresses the value where OpenTofu knows to,
and error paths can still surface one. GitHub masking is not a substitute even
where it does apply — it only covers values registered as GitHub secrets, not a
credential a provider fetches at runtime.

Two residual paths OpenTofu cannot redact, and shipmate does not attempt to:

- a provider or `local-exec` provisioner that writes a secret into its own
  stdout/stderr (only `apply.txt` carries stderr; this is the one exposure the
  apply surface has that the plan surface does not),
- a secret-bearing variable or output the consumer left unmarked.

Both are defects in the consuming configuration or its provider, not in the
engine, and both are equally visible in the plan artifact — the machine plan
`stack.otplan` stores values in the clear regardless of `sensitive` marking
(see Plan artifact encryption, below).

## Plan artifact encryption

The reviewed machine plan file (`stack.otplan`) can be encrypted at rest in the
uploaded artifact. When the consumer sets the optional `plan-passphrase` input
on `plan-cell` (in `plan.yml`), the engine encrypts the plan before upload
using a single symmetric cipher: `openssl enc -aes-256-ctr -pbkdf2 -salt`,
passphrase supplied via `-pass env:` (never on the command line). `apply-cell`
decrypts it after download on **every** apply path: all three paths pass it
as the optional `SHIPMATE_PLAN_PASSPHRASE` secret into the reusable
`apply-env-level.yml` workflow — via the engine `deploy.yml` for the
merge-deploy path, via the engine `apply-all.yml` for the bare form, and via
the engine `apply.yml` for the targeted form. Consumers set the
repo/environment secret `SHIPMATE_PLAN_PASSPHRASE` and forward it with
`secrets: inherit` in their `deploy.yml` and `apply.yml` wrapper
workflows.

- **Backward compatible.** An empty/unset `plan-passphrase` leaves the plan
  plaintext and the uploaded bytes byte-identical to a no-encryption run.
- **Fail-safe on mismatch.** apply-cell refuses to proceed rather than apply the
  wrong thing: a plaintext artifact when a passphrase is configured, or an
  encrypted artifact (`Salted__` magic header) when none is, both fail loud with
  a re-plan / set-the-secret instruction. A **wrong** passphrase is not detected
  at decrypt (AES-CTR is unauthenticated and decrypts to garbage without error);
  the exact-plan invariant catches it — `tofu apply` rejects the garbage plan and
  the apply check stays pending.
- **Scope: the machine plan file only.** `fingerprint.txt` is a hash and stays
  plain. The rendered plan `plan.txt` — in the `cell-summary` artifact, the PR
  sticky comment, and the job step summary — **stays plaintext**: it is the
  deliberately-public reviewer view, as is `apply.txt`. Encryption protects the
  machine plan at rest and nothing else; redaction in the published text comes
  from `sensitive` marking (see Secrets in published output, above).
- **Both sides must agree.** `plan-cell` (encrypt) and `apply-cell` (decrypt) are
  pinned independently (`plan.yml` vs `apply.yml`); the passphrase and the
  engine SHA must match on both. A mismatch surfaces as the fail-safe above, not
  a silent wrong apply.

## Terramate safeguards

Terramate ships four default-on safeguards that run before `terramate run` /
`terramate script run` (not before `list` / `generate` / `experimental`).
shipmate applies a **specific reviewed SHA** — the plan artifact reviewed on the
pull request — which on the merge-deploy path is legitimately **behind `main`**
(the squash-merge drops the PR-head SHA from `main`). Exactly one safeguard is
incompatible with that model; the engine disables it and keeps the rest:

| Safeguard | Policy | Rationale |
|---|---|---|
| `git-out-of-sync` | **disabled** | shipmate applies a chosen reviewed SHA that is legitimately behind `main`; remote-freshness is the wrong assertion for the exact-plan model. |
| `git-untracked` | kept | A genuinely unexpected untracked file must still block. shipmate's own artifacts are gitignored (below). |
| `git-uncommitted` | kept | A real dirty tree must block; gitignored artifacts are not tracked-file changes. |
| `outdated-code` | kept | Catches hand-edited / stale generated `.tf`, complementing the plan codegen check. |

**Mechanism (engine-controlled).** The three `terramate script run` sites —
`plan-cell`, `apply-cell`, `drift-cell` — pass `--disable-safeguards=git-out-of-sync`
on the invocation. The policy is versioned in the engine actions (pinned by SHA);
consumers get the correct policy for free by pinning, and never set it in their
own `terramate.config`. The engine never disables via the meta `git` or `all`
keywords (either would silently drop `outdated-code` / `git-untracked` /
`git-uncommitted`).

**Consistency invariant.** The disabled-safeguard set is identical across
`plan-cell`, `apply-cell`, and `drift-cell` — exactly `{git-out-of-sync}`. A
drift between the three cells is a defect (guarded by a test, like the TF_VAR
fingerprint).

**Consumer gitignore requirement.** Because `git-untracked` and
`git-uncommitted` stay live, a consuming repository **must gitignore** the
artifacts shipmate materializes in the working tree during a run — the reviewed
plan (`*.otplan`), the fingerprint (`fingerprint.txt`), and the flavor's state
path. An ungitignored artifact, or a genuinely dirty tree, then still fails
loud (by design) rather than producing a silent wrong apply.

## Env apply order

A repository may declare a partial order over its GitHub Environments so that
one environment's stacks fully apply before another's — for example, "`eu`
fully green, then `us`." The order is a Terramate global,
`global.shipmate.env_order`: a map from an environment name to the list of
environments that must complete their applies first (its predecessors). An
environment absent from the map, or the whole global absent, is unordered
relative to everything else.

The merge-deploy path topologically sorts this map into **env-levels**
(level 0 = no predecessors, or not listed at all): all pending applies whose
environment falls in level 0 run to completion (respecting the existing
stack-wave DAG within that level) before any env-level-1 apply starts, and so
on. A failure anywhere in an env-level skips every successor level's applies
for that deploy run — the failed environment's stacks stay pending, and
downstream environments are not touched until it is fixed and re-run.
`MAX_ENV_LEVELS` is `4`; an env-order graph that would span more levels than
that fails loud rather than silently truncating.

Targeted applies (`shipmate apply <env>`) act on a single environment and skip
env-level ordering entirely — there is nothing to order across.

A bare `shipmate apply` is the pre-merge equivalent of the merge-deploy path: it
buckets the pending applies of every non-explicit environment into the same
env-levels and applies level 0 fully before level 1, with the same
failure-skips-successor-levels rule. An environment excluded as explicit
keeps its position in the order: environments that do not depend on it run
normally at their own level, while environments ordered (transitively) after
an unapplied explicit environment are skipped with a notice — their ordering
precondition cannot be met in that run, exactly like a failed predecessor
level. Completed cells skip idempotently, so re-commenting `shipmate apply`
resumes where the previous run stopped.

The engine ships this as a reusable, parameterized workflow
(`.github/workflows/apply-env-level.yml`) that the engine's own `deploy.yml`
and `apply-all.yml` reusable workflows call once per env-level, passing that
level's pre-computed wave matrix; the workflow itself still fans applies out
stack-wave by stack-wave exactly as described above (see Fan-out).

The engine ships the merge-deploy path as the reusable workflow
`.github/workflows/deploy.yml` (deploy-detect → env-levels 0..3 via
`apply-env-level.yml` → gate completion + optional Slack notify), the
bare-apply path as `.github/workflows/apply-all.yml` (detect → env-levels
0..3 via `apply-env-level.yml` → gate refresh + result comment), and the
targeted path as `.github/workflows/apply.yml` (single-env detect → one
`apply-env-level.yml` call → gate refresh + result comment). A
consuming repo carries two thin wrappers: `deploy.yml` (`on: push` to the
default branch; passes only its flavor's `state_suffix`) and `apply.yml`
(`workflow_dispatch`; its optional `environment` input routes to the targeted
or bare engine workflow).

## OpenTofu note

OpenTofu reserves the variable name `version` as a meta-argument; it cannot
be declared as an input variable in a module or root configuration. Sample
stacks in this project therefore use `app_version` wherever a version
string for the deployed workload needs to be passed through as a
`TF_VAR_*`/OpenTofu variable, never `version`.
