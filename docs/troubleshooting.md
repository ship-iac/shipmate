# Troubleshooting

What `shipmate doctor` reports and who may ask for it, then the failures
consumers actually meet. [`../CONTRACT.md`](../CONTRACT.md) is the source for
the semantics behind them; `branch-protection.md` and `github-app.md` are the
settings pages.

## shipmate doctor

`actions/summary` runs `scripts/doctor` on every plan run and emits its
findings as workflow annotations titled `shipmate doctor`
(`::warning title=shipmate doctor::<text>` / `::notice title=shipmate
doctor::<text>`) — read-only, never blocking. Comment `shipmate doctor` on a
pull request for a consolidated report: a sticky comment (marker `<!--
shipmate:doctor -->`, upserted in place like the plan comment) combining fourteen
live probes.

- **The `shipmate / gate` rule on the default branch is missing or mis-pinned.**
  No active ruleset requires it, or one does but does not pin `integration_id`
  to the shipmate App, or is not strict.
- **Whether the default branch's `pull_request` rule requires code-owner review
  (`hardening.md` #3–5).** An approval count alone is not reported as sufficient,
  because the shipmate App can submit an approving review. Only a CODEOWNERS
  review is out of reach of a leaked App private key, and only for changed files
  an entry actually owns — the rule is a no-op for unowned paths. This probe
  reads ruleset booleans only; the `CODEOWNERS` probe below reports a file that
  owns nothing, and no probe checks which paths an entry covers.
  `required_approving_review_count: 0` *with* code-owner review on is
  reported as a note: the supported sole-maintainer mode while
  `require_last_push_approval` is off, since at count 0 GitHub required no
  code-owner review from a pull request author who individually owns the changed
  files (measured 2026-10-11; team ownership was not). While the default
  branch's table is readable and an environment is gated, the note names the
  gated environments, which can apply without an approving review and are held
  only where a code-owner review is required for the changed files. Count 0
  with code-owner review off leaves no unforgeable merge-time control at all
  and warns, and names the gated environments in a second warning. Count 0
  with `require_last_push_approval` on warns, and omits the sole-maintainer
  note; the gated-environments note still appears. A sole maintainer cannot
  approve their own last push, so cannot merge. The
  booleans are unioned across every
  `pull_request` rule on the branch, since GitHub enforces the union across
  layered rulesets.
- **A `CODEOWNERS` that owns nothing while code-owner review is required.** The
  default branch's `.github/CODEOWNERS`, `CODEOWNERS` or `docs/CODEOWNERS`, the
  first one present as GitHub reads them, holds no line naming a pattern and at
  least one owner once a `#` comment is cut off, or none of the three exists. A
  pattern with no owner un-owns its paths. No path then has a code owner, so the
  code-owner review holds no pull request. Which paths a rule line matches is not
  checked. A failed file read degrades this probe alone; an unreadable ruleset
  leaves it silent, since the review-rule probe already reports that read.
- **A GitHub Environment (`<env>-plan` / `<env>-apply`, or a single shared
  `<env>`) is missing for a tagged-in environment.**
- **The plan/apply environment protection shape.** A plan environment must have
  no approval-type protection rules — required reviewers or wait timers — and
  no deployment branch policy. An apply environment with no approval rule is
  only a note, and "no approval rule" is deliberately not "no protection rules":
  GitHub synthesizes a `branch_policy` protection rule for any environment with
  a deployment branch policy, and a branch policy is not a review. The role each
  environment plays comes from `shared: true` in its `environments.<env>`
  entry of `.github/shipmate-config.yml`, read from the default branch's copy that every
  run binds from; when that copy cannot be read or is invalid, these environment
  probes are skipped with a note. A shared environment carrying approval rules warns that they stall the plan
  cells and every drift sweep covering it; its missing approval rules are a note. An apply or shared environment with
  no deployment branch policy warns, because every apply, unlock and deploy
  binding it refuses.
- **The secrets a plan environment holds, names only.** The API never returns a
  value. A plan cell runs branch code with whatever that environment releases
  and control 8 forbids protecting it, so the finding is a note giving the count
  — exact unless the listing was too long to read whole — and a capped list of
  the names. A crowded environment's later names are not printed, so that one
  finding cannot spend the whole report's size budget.
  `SHIPMATE_APP_PRIVATE_KEY` among them is a warning. A `SHIPMATE_SECRETS`
  holding the read key is the configuration
  [`../CONTRACT.md`](../CONTRACT.md) §Which environment supplies which key asks
  for, so seeing it
  named in this note is expected.
- **Whether the `shipmate-engine` environment exists, and whether its deployment
  branch policy actually names the default branch.** See `hardening.md` #16 and
  `github-app.md` §Key-exposure boundary.
- **A workflow file that does not parse as YAML.** One note per file, read at the
  commit under examination, naming the parser's message; `shipmate.yml` is read
  even when the listing does not reach it. The probes below skip such a file and
  still check every file that parses.
- **A workflow file other than `shipmate.yml` declares the `pull_request_target`
  trigger.** It runs at the base ref with the repository's secrets, and a
  workflow that also acts on content the pull request author controls from a job
  naming an environment hands those secrets to a fork (`hardening.md`).
  `shipmate.yml` is exempt by exact name because it uses the trigger in the one
  shape that is safe, with the credentialed job checking nothing out. The probe
  reads the same workflow files as the pin probe, at the same commit, so a pull
  request that removes the trigger is not still reported for it.
- **Engine action-pin freshness in the consumer's own workflow files.** Each is
  read at the commit under examination, so the pull request that bumps a stale
  pin is not itself reported stale. It covers only pins of the engine's own
  repository, which the probe learns at runtime from `SHIPMATE_ENGINE_REPO`, set
  from `job.workflow_repository` by the engine job that runs it, rather than
  from any hardcoded slug — another org's shared action is not shipmate's to
  report on.
- **Whether `shipmate.yml` is wired as published.** One read of the file, at
  the commit under examination, feeds four checks, so an unreadable file
  leaves all four unverified together. A call counts only when it names the
  engine repository doctor runs from, in any letter case: a fork, a renamed
  engine or a wrapper workflow does not count. When that repository could not
  be determined, the job-name, routing and drift-call checks are a note
  saying so, and the dispatch check still runs:
  - Whether its plan-calling job is named `shipmate`. GitHub names a called
    workflow's check runs `<caller job> / <callee job>`, so that name is what
    makes the plan cell checks `shipmate / <stack> / <env>`. Under another name
    the plan still runs and the gate is unaffected; what is lost is every `plan`
    link in the plan comment, which falls back to the workflow-run page instead
    of the cell's own check. A job with no `name:` is judged by its job id,
    which is what GitHub displays then.
  - Whether it can serve a dispatched verb at all. That needs the
    `workflow_dispatch` trigger every commented verb dispatches; all four inputs
    those bodies name (`verb`, `environment`, `ref` and `pr_number`); a `verb`
    offering the whole option list the file routes, since a missing option is
    refused at the dispatch form and at the API while an extra one offers a verb
    no job selects; and no input but `verb` declared `required: true`, because a
    body that leaves one empty is refused whole — so a required input of your
    own refuses every verb, not just the one it was added for. Each is refused
    at dispatch time with an HTTP 422 and no run created; the comment-handling
    run carries the error, and the dispatch failure reply on the pull request
    links it when it can be posted (`CONTRACT.md` §Comment-ops).
  - Whether each of its jobs is selected by the `if:` its event needs. The file
    gates five jobs, one per engine reusable workflow other than `drift.yml`, and the probe compares
    each job's whole `if:` against the expression that file's published fence
    carries. Each finding identifies the job by the engine workflow it calls —
    "the job calling the engine's `apply.yml`", which is the fence's `apply` —
    because a job that is missing or duplicated has no one job id to name. A
    file with no call of the engine's plan workflow is reported here, as zero
    jobs calling `plan.yml`: its dispatch is accepted and the run plans nothing.
    A wrong expression and a missing `if:` are both quoted with the expression
    to write; a job count other than one is not, since there is no single job to
    compare. Nothing else observes any of this: a verb whose job never runs
    completes green with nothing done, and a job whose `if:` is too wide runs on
    an event it was never meant to see. The fence in
    [`getting-started.md`](getting-started.md) has every expression.
  - Whether it calls the engine's `drift.yml`. A drift job in `shipmate.yml`
    runs on that file's triggers. Move the drift job to a workflow file of its
    own, and drop the `schedule` trigger and the `drift` verb option from
    `shipmate.yml` ([`drift.md`](drift.md)).
- **Whether `.github/shipmate-config.yml` at the commit under examination is valid.**
  Read through the API at that commit, never from the default branch and never
  substituted by it, so a malformed or misplaced setting is reported on the pull
  request that introduces it rather than after it merges. A missing or unreadable
  file is a note saying so, never an all-clear. Only the checks a file can be judged
  on by itself run here — the top-level keys, `layout`, `max_parallel` and the
  environment entries; `tf_vars`-layout coverage and unused entries need a plan
  matrix or a whole-tree environment scan, and the verdict names them as
  unchecked. A valid file also gets its `needs`, `explicit` and `max_parallel`
  values read back, absent ones included: `explicit` set on no entry is
  legitimate configuration that no validator can question, and it means a bare
  `shipmate apply` applies production too. Each variable reference is resolved
  and listed as `<key> from variable <NAME>`; an unset one is the invalid-file
  finding, which is how a pull request adding it learns before the merge.
  Execution keeps reading the default branch's copy — this reports, it changes
  nothing a run uses.
- **Whether the shipmate App installation still grants the manifest's full
  permission set.**
- **Whether any workflow file calls the engine's `drift.yml`.** Read at the
  commit under examination, like the pin probe. A file counts by a job whose
  `uses:` is the call, not by its name, and a call only inside a `#` comment does
  not count. A call counts only when it names the engine repository doctor runs
  from, in any letter case: a fork, a renamed engine or a wrapper workflow does
  not count. With none, no stack is ever checked for drift, which is a note:
  a repository may decline drift on purpose. The note keeps the verdict at ⚪
  with the help hint, never 🟢 no problems found.
  An unreadable file leaves the question unverified, a note, since the call
  may sit in a file the probe could not read; a file that does not parse
  silences the missing-file note for the same reason. So does an engine repository that
  could not be determined, since no call can then be recognised.

The report carries those findings together with the warning and failure
annotations GitHub already recorded on this commit's workflow runs — shipmate's
own and any other Actions workflow run on that commit; third-party-app-authored
check runs are excluded.

Only thirteen of the fourteen probes can produce a finding from the plan path's
own `annotate`-mode run (`actions/summary`). The App-permission-drift probe
only has something to report when a full-manifest permission-set mint was
actually attempted, which only `shipmate doctor` does. It is effectively
comment-path-only. `doctor`
degrades to a "could not verify" warning naming each probe that was skipped on
an API error, and always exits 0, so a probe failure never fails the plan run.
One such failure is the App token lacking read access to `rules/branches` or
`environments` — both token mints that drive doctor also request Actions read,
which the environment reads need on some configurations. One endpoint failure
can name more than one probe: a `rules/branches` failure degrades both the
gate-rule and the review-rule probe, because the two read it independently on
purpose, so neither is silenced by the other's failure. The drift-file probe's
"could not verify" finding is a note, because its worst finding is a note.

The engine-pin and fork-trigger probes degrade to a note instead. Both read
`.github/workflows`, and that read legitimately fails on the pull request that
first adds that directory, which is the first `shipmate doctor` any consumer
runs, so the first report a consumer sees carries two notes. Both also decline
rather than guessing when they cannot tell which commit to read, and the pin
probe when it cannot tell which repository the engine is. An environment that
exists but whose settings cannot be read is likewise a note naming it, rather
than the silence a nonexistent environment gets — a nonexistent environment is
the environment-existence probe's finding. The `shipmate-engine` probe degrades the
same way. The plan-environment secret probe carries two degrade levels of its
own: an environment whose secret listing fails is a note naming it, so one
unreadable environment does not silence the ones that could be read; and a
listing too long to read whole warns that whether the environment holds `SHIPMATE_APP_PRIVATE_KEY` could not be
determined, rather than reading as a routine note about the names it did see.

All three environment probes — existence, protection shape, and the
secrets a plan environment holds — cover only the environments of the stacks a
given pull request changed; the declared set comes from that commit's plan
matrix. So the report's all-clear line names the environments it actually probed
instead of implying the repository's environments are all sound, says plainly
when the set was empty, and a clean secret probe says nothing about an
environment this pull request did not touch.
The declared set is the cell summaries of the plan runs this commit's own apply
checks record, so a run whose summaries cannot be downloaded is warned about and
its environments are absent from the set.

`shipmate doctor` also emits untitled `::warning::` annotations on its own
degrade paths: an unreadable PR head SHA, unreadable plan records on this
commit's apply checks, a plan run whose cell summaries could not be downloaded,
a failed check-runs listing or reduction, a failed per-check annotations fetch,
a failed job-summary write, and a failed listing of the pull request's
comments, on which the report is not posted for that run rather than posted as
a second sticky comment. Those annotations land on the `issue_comment` run
executing `shipmate doctor`, at `github.sha`, not on the PR head SHA whose
check runs the harvest reads, so the harvest never reads them back. That job
checks out no consumer content and reads entirely through `gh api` and
`gh run download -R`, so `github.sha` is the default branch's tip.

Separately, the report states plainly when some of the commit's workflow runs
had not finished yet, and when the warnings harvest itself could not complete
(or may be truncated by GitHub's per-step annotation cap), rather than claiming
a false all-clear.

The harvest is deliberately uncurated: it reports the annotations as GitHub
recorded them, including ones from the third-party actions the engine pins, so a
deprecation warning from a pinned action reads the same as a shipmate warning.
That is intended — a known-noise denylist would eventually swallow a real
warning — so treat an unfamiliar line as upstream's until you have checked.

`shipmate doctor` never blocks the gate. It needs write access to the
repository, but no review or reviewed plan, unlike `shipmate apply`
(§Who can ask for the report, and who can see it).

## Who can ask for the report, and who can see it

The report is an inventory of what is *not* configured: that no ruleset
requires `shipmate / gate` on the default branch, that `<env>-apply` has no
approval rule so pre-merge applies to it are unreviewed, and whether the App
installation is missing permissions the manifest declares.

**What the engine enforces.** `shipmate doctor` runs only for a commenter
whose permission on the repository is `write` or `admin`, the same rule as
`plan`, `apply` and `unlock`. Any other commenter, and a commenter whose
permission could not be read, gets one `🔴 refused:` reply; no App token is
minted and no probe runs.

`shipmate help` stays open to every commenter (it lists the verbs and discloses
nothing about the repository, and a newcomer whose setup is broken still needs
it), and the report is an ordinary pull request comment, so once someone with
write access asks for it, everyone who can read the pull request can read it.
The same rendered report is also written to the run's job summary, so that a
GitHub API outage which loses the comment does not discard the probes. That
surface needs repository read access too, so it admits no one the comment did
not — but it is not as retractable. The comment is a single sticky one you can
edit or delete, and the next `shipmate doctor` overwrites it; a job summary
cannot be edited or redacted at all, and every past run keeps its own copy for
the repository's Actions retention window (90 days by default). So if a report
disclosed something you did not want recorded, deleting the comment is not
enough — delete the workflow runs that produced it, or shorten the retention
window.

`app/manifest.json` declares `"public": false`: the shipmate App is registered
per organization and intended for repositories the installing organization
controls.

## What `scripts/onboard` reports

Every line the reconciler prints is `<verb> <subject>`, optionally `: <detail>`.
The verbs:

| Verb | Meaning |
| --- | --- |
| `ok` | already as shipmate needs it; nothing was written. |
| `create` / `update` / `set` / `delete` | the write it just performed. |
| `created` | the workflow file it just wrote to `.github/workflows/`. |
| `deferred` | the gate ruleset is not created yet, because `.github/workflows/shipmate.yml` is not on the remote default branch, or the token cannot read it (a private repository answers 404 for both), and no pull request could produce `shipmate / gate`. Merge the pull request that adds the file, then run the script again. Not drift; it does not affect the exit code. |
| `pin-only` | the file matches except for the engine pin. Not drift, and it does not affect the exit code — move every engine ref to this engine's release in one commit with `scripts/repin` ([`releasing.md`](releasing.md) § Re-pin a consumer). |
| `would …` | `--dry-run`: the write that a real run would perform. |
| `differs` | it found something it will not change on your behalf. Every one exits the run 2. |

A `differs` line is not a failure of the run: the reconciler is additive, and
undoing a protection or a value a consumer set deliberately is outside its
mandate. Each one names what to do.

| `differs` line | What it means |
| --- | --- |
| `<name> branch policy` — also permits other branches | the environment — `shipmate-engine`, an `<env>-apply`, or a shared bare `<env>` — has a deployment branch policy naming branches besides the default one, so a workflow on any of them can still claim what that environment scopes. Delete the extra entries in Settings → Environments if they were not deliberate. |
| `<env>-plan` — GitHub Environment `<env>-plan` is a plan environment but has a deployment branch policy | a plan environment must have none: plan cells evaluate at the pull request's base ref, so a policy blocks every cell whose pull request targets a branch it does not name ([`hardening.md`](hardening.md) #8). Remove the policy. A plan environment that also has protection rules prints two `differs` lines: the protection-rules one below first, then this one. |
| `<env>-plan` — GitHub Environment `<env>-plan` is a plan environment but has protection rules | required reviewers or a wait timer on a plan environment stall that environment's plan cells and every drift sweep covering it. Remove them ([`hardening.md`](hardening.md) #6). |
| `<env>` — GitHub Environment `<env>` (shared between plan and apply …) has protection rules | a shared bare `<env>` is bound by the plan cells and every drift sweep covering it as well as the applies, and GitHub offers no per-job filter, so a protection rule there stalls all three. To gate applies alone, split it into `<env>-plan` / `<env>-apply` and drop `shared: true` from `environments.<env>`. |
| `<env>` — the naming the engine does not bind is also present | the naming `shared: true` in `environments.<env>` does not select already exists: a bare `<env>` where the engine binds the `<env>-plan` / `<env>-apply` pair, or either half where it binds the bare `<env>`. Nothing binds the unused naming, so the run creates and changes nothing for that environment — including the naming it does bind, which is why it is reported rather than half-written. Delete the unused naming, or move the environment to the other one by setting or dropping `shared: true` in its entry of the checkout's `.github/shipmate-config.yml`. |
| `gate ruleset` — the rulesets POST was rejected (HTTP 422) | most likely the name is taken by a ruleset whose enforcement is `evaluate` or `disabled`, which the effective-rules read cannot see; 422 has other causes, so read `gh api repos/OWNER/REPO/rulesets` first. Set it to active, or delete it and run again. |
| `gate ruleset` — rulesets need GitHub Pro, Team, Enterprise, or a public repository | the plan this repository is on has no rulesets. Configure the gate by hand from [`branch-protection.md`](branch-protection.md). |
| `gate ruleset` — `shipmate / gate` is required under another `integration_id` | the gate is required, but not pinned to the shipmate App, so a status of that name from any other identity satisfies it. Set `integration_id` to `SHIPMATE_APP_ID`. |
| `gate ruleset` — it does not require branches to be up to date (strict) | plans can go stale against the base before merge. Turn on "Require branches to be up to date before merging". |
| `gate ruleset` — `shipmate / gate` is already required, but `.github/workflows/shipmate.yml` is not on `<branch>` yet | the gate is required while the workflow file is not on the default branch, or this token cannot read it and GitHub answered 404. `pull_request_target` runs the default branch's copy, so the pull request adding the file cannot produce the gate. Disable the gate rule until that pull request merges, or merge it through a bypass actor. |
| `shipmate.yml` — the published fence, never pinned | the file holds the `@<engine-sha>` placeholder from the docs rather than a pin, which `scripts/repin` ([`releasing.md`](releasing.md) § Re-pin a consumer) cannot move. Delete the file and run the script again. |
| `shipmate.yml` — differs beyond its pin, not overwritten | the file differs from what this engine release publishes by more than its pin — a local edit, or a fence this release changed while the file stayed on an older one. The line is followed by the diff from the published fence to the file, pins masked and indented four spaces: reconcile the file by hand, or delete it and run again to take the published one. |

The closing checklist marks each item with one of three verdicts. None of them
affects the exit code: every item is yours to do, so a first run exits 0 over
its `todo` items.

| Verdict | Meaning |
| --- | --- |
| `ok` | what the run read already satisfies the item. |
| `todo` | the item is not done; what to do follows. |
| `cannot check` | the run cannot read it: the App installation needs an App JWT, whether `CODEOWNERS` entries cover the IaC, the workflows, `/.github/shipmate-config.yml` and `/.github/CODEOWNERS` is GitHub's matching, `shipmate-engine`'s custom deployment protection rules could not be listed, `drift sweeps` cannot compute coverage (see below), and a read of the repository's Actions settings failed. Confirm it by hand. |

`custom deployment protection rules on shipmate-engine` appears only when the
environment exists and has an enabled custom deployment protection rule, or its
rule list cannot be read. The `todo` names each enabled rule's App and the
command that removes it: every engine job fails under one (§Every engine job
fails before its first step).

While a `pull_request` rule on the default branch requires code-owner review,
the `CODEOWNERS` item is `todo` when the checkout has no `CODEOWNERS` file or the
first one GitHub reads holds no line that is not blank or a `#` comment: no path
then has a code owner, so that review holds no pull request.

Three items check the working tree's stacks and drift files the way a run
would, so a run that would refuse or skip shows up before the pull request
that causes it merges:

| Item | `todo` when |
| --- | --- |
| `unmanaged stacks` | a stack carries no `env/*` tag. It names them; shipmate runs none of them, which needs nothing if deliberate. |
| `stack tags` | a stack carries two `workload/*` tags, two stack paths in one environment slug alike, a stack's `workload/*` tag is outside its environment's `workloads` list (checked only when that entry names an `identity`), or that list names a workload no stack in the environment tags. |
| `drift sweeps` | no workflow file calls the engine's `drift.yml`; a stack × environment cell is in no drift file's `tags` query; a query, or one clause of it, matches no cell; or a sweep refuses: its query has an empty term, selects more than 256 cells, or keeps a cell whose `workload/*` tag its environment's `workloads` list does not name (checked only when that entry names an `identity`). A refused sweep runs no cell, so that file's cells count as unswept. |

`drift sweeps` reads every `.yml` and `.yaml` file in `.github/workflows/` and
treats one as a drift file when a job's `uses:` calls the engine's `drift.yml`,
as `shipmate doctor` does; the query is that job's `with.tags`. It reads this
checkout's copies; a drift run reads the default branch's. It reports `cannot
check` and computes no coverage when a drift file's `tags` is a `${{ }}`
expression; when a file calls `drift.yml` more than once; when a file or the
directory cannot be read, a file is not UTF-8, or a file does not parse as YAML,
since it may be a drift file; when `.github/shipmate-config.yml` is absent
or fails validation, since a drift run refuses before any cell then; or when `stack tags` reports a two-tag stack or a slug collision,
which leaves the tree incomplete.

The `.github/shipmate-config.yml` item is `todo` when the table is absent or fails
validation, or when a `needs` entry names an environment that is neither an
entry nor tagged on any stack, which orders nothing. A `needs` naming a declared
entry no stack tags yet is the provisioned-ahead order and stays `ok`.

Some disagreements are refused rather than reported: the run stops before its
first write and exits 1 — no `differs` line, and nothing else runs.

There are two. The first is the `SHIPMATE_APP_ID` repository variable
differing from `--app-id`. `--app-id` does not only set that variable: it pins
the gate ruleset's `integration_id` and selects whose private key is stored on
`shipmate-engine`. Reconciling the two separately would require a
`shipmate / gate` status the workflows — which mint their token from the
variable — can never post, and the default branch would stay blocked until an
admin deleted the ruleset. Re-run with the variable's value, or change the
variable first. The second is a run without `--key` while `shipmate-engine`
holds no `SHIPMATE_APP_PRIVATE_KEY`, or does not exist yet: there is no key to
place. Re-run with `--key <path to the App's PEM private key>`.

## Common failures

When a `shipmate apply` fails at `detect` with an `::error::` (a refusal or a
failed tool or API call), its comment shows the message's first error on one
line, in a fence under the `🔴 failed` verdict: search this page for its text.
The post-merge deploy posts no apply comment, so a deploy's `detect` error is
only in that run's `detect` annotation.

### `Saved plan is stale`, or `does not match the reviewed plan's fingerprint`

An apply cell fails at or before `tofu apply` and its
`apply / <stack> / <env>` check stays pending.

Two fail-safes of the exact-plan model produce this, and both mean the reviewed
`.otplan` can no longer be trusted. Either the state moved after the plan was
generated, and OpenTofu itself rejects the stored plan; or `apply-cell`'s
pre-apply check found that the current environment's `TF_VAR_*` set no longer
hashes to the fingerprint recorded with the plan — a variable was added,
removed or changed, a GitHub variable that an `environments.<env>.tf_vars`
reference names included ([`../CONTRACT.md`](../CONTRACT.md) §Apply-match
fingerprint). That error names the current variable names only; it never
prints a value.

The fix is a re-plan and an apply of the fresh plan. On an open pull request,
comment `shipmate plan` or push; re-running the old run's plan jobs does not
re-plan, because their artifact uploads 409. After the merge, open a new pull
request touching the stack. There is no force: an apply that silently
re-planned would apply
something nobody reviewed, and the check left pending is exactly what stops the
gate greening over an unapplied stack.

**A retry is not a re-plan, and the pending check makes the retry look
available.** A failed apply leaves its `apply / <stack> / <env>` check *pending*
rather than failed, so `snapshot` accepts a fresh `shipmate apply <env>` for that
cell — the right recovery when nothing moved (a credentials failure, a rate limit,
a cancelled run). If the apply partially succeeded, the state serial advanced
and the stored plan no longer matches it, so that retry is refused here instead.
Re-plan first; the two recoveries are not interchangeable. On a local backend,
a cell whose job log shows `The apply step ran and its state was not saved` is
the exception: the cache restores the state from before the apply, so a retry
applies the plan again unrefused. Follow
§`The apply step ran and its state was not saved` instead.

A cancelled run has one further failure mode a bare retry cannot clear: it can
die holding the state lock, and every later apply of that cell then fails
acquiring it. See §A state lock is held.

**No supported route aims an apply at a superseded plan.** That is why this
error normally means the state moved rather than that the wrong plan was chosen.
The dispatched apply workflow refuses in its `guard` job any dispatch whose
actor is not a `[bot]` — a hand-run one fails with `apply refused: the
dispatching actor is not a [bot]; comment 'shipmate apply' on the pull request
instead of running a direct workflow_dispatch` — its `review` job refuses a
`ref` that is not the open pull request's current head, and
comment-ops reads the plan run each `apply / <stack> / <env>` check on the pull
request's current head records, refusing the command when that head names none.
These fail-safes are defence in depth behind that control, not the only thing
behind it.

**If the mismatch names only variables the table derives — `TF_VAR_env` and
`TF_VAR_region` under the `tf_vars` layout, `TF_WORKSPACE` under `workspace` — look at the
environment table rather than the plan.** These come from `matrix.environment`
and the table on the default branch, and both sides resolve them from that one
table, so they hash identically whatever environment the job bound. What moves
the whole set is the table changing between the plan and the apply — an
environment's `tf_vars`, its `region`, or the `layout` itself, edited on the
default branch in between. A re-plan is the fix, as above.

**If it names a variable you set yourself, look at the consumer channels.** What
a cell receives through a GitHub variable or the `SHIPMATE_SECRETS` envelope
resolves against the environment the job bound — `<env>-plan` for a plan cell,
`<env>-apply` for an apply cell — so those `TF_VAR_*` values are part of the
fingerprint and the binding does reach it. Two causes, both on your side:

- a `TF_VAR_*` whose value differs between `<env>-plan` and `<env>-apply`, which
  fails every apply of that cell until the two are made identical or the value is
  renamed to a non-`TF_VAR_` name ([`../CONTRACT.md`](../CONTRACT.md) §"Values
  that differ between the two tiers" is the rule and states its cost);
- a `TF_VAR_*` secret **rotated** between the plan and the apply, which needs no
  configuration change at all — every plan reviewed before the rotation is
  invalidated by it. A re-plan clears this one.

An environment-naming or `shared: true` change does reach this error for the
same reason. Setting `shared: true` on an env swaps its two split environments
for the bare one, so a cell planned before the change and applied
after it reads its variables and its envelope from a different environment.
Re-plan after such a change. A mis-binding is a separate failure, refused by the
pre-flight of §`this apply would bind GitHub Environment(s) that do not exist`
before any wave starts.

### `plan-cell needs the expected-head input`, or `The plan would describe a tree nobody reviewed`

A plan cell fails on its very first step, before `terramate` runs, so the cell
produces no plan and its `shipmate / <stack> / <env>` check does not go green.
The failing plan job leaves `shipmate / gate` held red with
`shipmate / <stack> / <env> has no plan summary; fix its plan or re-plan`, or
`plan incomplete (plan job: failure); fix the failing plan checks before merging`
when no cell without a summary is known or its name does not fit — a hold, not an absence (§`shipmate / gate` never goes green, "The
gate is deliberately held") — so nothing merges until the plan cells pass.
That run publishes no apply check. Checks an earlier plan run of the same head
published stay; on a head with none, a `shipmate apply` replies "no reviewed plan
for the current PR head".

Engine `plan.yml` supplies both values the checks compare, so no consumer setting
produces either error; report it, with the run link, through the bug report
template ([`.github/ISSUE_TEMPLATE/bug.yml`](../.github/ISSUE_TEMPLATE/bug.yml)).
Each check refuses:

- **`expected-head` is missing or empty.** `plan-cell` requires it — the commit
  the run is planning — and refuses rather than publishing a plan whose
  provenance nobody can verify. Engine `plan.yml` passes
  `expected-head: ${{ needs.facts.outputs.head-sha }}` to the `plan-cell` step.
- **The commit checked out is not the commit the run says it is planning.**
  Neither plan trigger checks out the pull request's head — `pull_request_target`
  takes the base branch, `workflow_dispatch` the dispatch ref — so engine
  `plan.yml`'s `detect` and `plan` jobs name
  `ref: ${{ needs.facts.outputs.head-sha }}` on their checkout. Without it
  the cell would plan the base and report a clean plan for a pull request it
  never read, so the mismatch is refused. `build-matrix` holds the same line
  one job earlier, in `detect`, and states its half of it two ways:
  `this run checked out <sha>, which is not the commit it is planning` when the
  `ref:` is missing, and `this run did not state the commit it is planning` when
  the step's own `head-sha` input is absent.
  Neither is optional and neither has a quiet mode — a run that cannot name its
  head is refused, not planned.

### `this reviewed plan records no planned commit`, or `the reviewed plan was produced from`

An apply cell fails before the decrypt, the state restore and the apply, and its
`apply / <stack> / <env>` check stays pending. The cell summary names the
blocked reason.

The reviewed `.otplan` carries the commit it was produced from
([`../CONTRACT.md`](../CONTRACT.md) §Apply-match fingerprint), and the applying
cell compares it against its own checkout. Two ways that ends here, and the
remedy differs:

- **The record disagrees with the checkout.** The error names both commits. The
  plan describes a tree this job does not have, so it is refused rather than
  applied or silently re-planned — as with a stale plan, there is no force. The
  fix is a re-plan and an apply of the fresh plan.
- **There is no record at all.** There is nothing to compare against the
  checkout, so the apply is refused. Re-plan the stack on its pull request and
  apply the fresh plan; post-merge, a new pull request touching the stack plans and
  applies it.

This check is per cell and additive: the apply path's plan-run binding — each
cell's plan run read from an App-authored apply check on that same head — still
holds, and a repository sees this error only for a plan run that binding
accepted.

### `apply aborted: plan run`

The apply, or the post-merge deploy, stopped at `detect` before any cell job
started. The apply checks the plan run it names published do not match the cells
it planned. Three causes produce this:

- Its summary job failed partway through publishing them.
- A "Re-run all jobs" attempt of that run planned a different cell set and
  published checks beside the first attempt's.
- Its checks were published before shipmate recorded a planned cell count on
  them.

The message says what it found: `published <n> apply checks for <m> planned
cells` (fewer or more checks than cells), `records no planned cell count`, or
`records disagreeing planned cell counts`. `holding back` names the cells this
apply would have run.

Applying the published checks could lose an `after` ordering to a cell the apply
cannot see, so the apply is refused ([`../CONTRACT.md`](../CONTRACT.md)
§Apply-match fingerprint). When the summary job failed partway, its gate hold
stays too, so the pull request cannot merge over the missing checks.

If the plan run's summary job failed, re-run that run's failed jobs, then apply
again: the re-run keeps the run id, and its summary job publishes only the apply
checks the run has not yet published. Do not use "Re-run all jobs", which can
plan a different cell set and over-count.

Otherwise, or if the refusal remains, push a commit to the pull request: the new
head's plan run plans and publishes every cell afresh, and the apply proceeds
once it completes. If the pull request has already merged, open a new pull
request touching these stacks; it plans and applies them.

A `shipmate plan` comment re-plans the same head and may not clear this. If the
re-plan no longer plans a cell of the partial run, that cell's newest apply check
still names the partial run, and an apply that includes that cell refuses again.

Nothing has been applied: the refusal runs before any wave.

### `no plan-text digest recorded for`, or `the stored plan does not render to the plan text that was reviewed`

An apply is refused over the plan text a reviewer read, at one of three points.
The first is `detect`, before any cell job starts. The other two are inside an
apply cell and leave its `apply / <stack> / <env>` check pending: the
`digest-input` step refuses a digest that never reached the action, before
`tofu init`; the `plan-digest` step refuses a render that disagrees with it,
after `init` and before `tofu apply`. Each names its own blocked reason in the
cell summary.

The plan text and the stored plan are produced by the same job, which runs the
pull request's own code, so the two can be made to disagree. The trusted summary
job records the digest of the `plan.txt` it publishes in the comment
([`../CONTRACT.md`](../CONTRACT.md) §Apply-match fingerprint), and the applying
cell re-renders the stored plan with the command that wrote it and compares.

- **No digest is recorded.** Nothing is compared, so the apply is refused
  rather than run unverified. Re-plan the stacks on their pull request and apply
  again; post-merge, a new pull request touching those stacks plans and applies them.
  This is the same shape as the absent planned-commit record above, and the
  same remedy.
- **The render disagrees with the digest.** The error names both digests. Either
  the plan text published for review does not describe the plan that would run,
  or the tooling moved underneath the artifact. A pin bump that moves the tofu
  version between plan and apply reaches `tofu` first and fails earlier with
  `plan files cannot be transferred between different versions`; a provider that
  moved fails at the apply with `Inconsistent dependency lock file`. Neither
  produces this message. There is no force. Re-plan the stack on its pull request
  and review the fresh plan.

Nothing has been applied in either case: the comparison runs before
`tofu apply`, and the pending check is what keeps the gate from greening over
an unapplied stack.

### `this apply would bind GitHub Environment(s) that do not exist`

The apply run stopped in its `snapshot` job, before any wave, and the error names
every environment the applies would have bound that the repository does not have.

Why `snapshot` refuses instead of warning: GitHub creates a missing environment
on demand, with no reviewers, no wait timer and no deployment branch policy, and
the apply then runs inside it. An existing environment with no branch policy is
open the same way: a workflow on any branch can bind it. Nothing else catches
either: a cell's variables come from the environment table and
`matrix.environment`, so plan and apply hash identically whatever environment the
job actually bound, and the apply-match fingerprint pins nothing about the
binding. Existence and a branch policy, both checked before the waves, are the
only controls.

Two fixes, and the error names both because either can be the right one:

- **Create each environment named.** The apply path binds `<env>-apply` for a
  split env and the bare `<env>` for one whose `environments.<env>` entry holds
  `shared: true`. Existence is matched case-exactly against the stack's env
  tag, so an environment differing from the named binding only in case does not
  satisfy the pre-flight, and GitHub may reject a second one as a duplicate.
  Rename the existing environment (or the tag) to match rather than creating
  another.
- **Correct the environment's entry in `.github/shipmate-config.yml`.** If the
  environments you created are the ones you meant, the table is what disagrees
  with them: `shared: true` on an env that is really split, or missing on one
  that is really shared. The engine reads the default branch's copy, so the fix
  takes a merged pull request. `shipmate doctor` on a pull request reports, per
  env, which naming the default branch's table selects and which environments of
  it are missing, which is the fastest way to see which side is wrong.

Two neighbouring failures from the same step, both also fail-closed:

- **`could not list this repository's GitHub Environments`.** The check did not
  happen, so the applies are refused rather than permitted. Check first that the
  job calling `apply-env-level.yml` grants `actions: read`: permissions cap at
  each `uses:` boundary, and on a private repository listing environments 403s
  with `checks: read` alone. Every apply route shipped here grants it already, so
  a persistent 403 points at a hand-written caller. A 5xx or a rate limit clears
  on a re-run of the workflow.
- **`the environments listing is truncated`.** One page did not cover the
  repository's environments, so a missing environment cannot be told from an
  unread one. Reduce the number of environments, or re-run — this is a hard
  ceiling around 100 environments, not a transient.
- **`GitHub Environment(s) ... have no deployment branch policy`.** Each named
  environment exists, but a workflow on any branch can bind it and reach its
  secrets and OIDC role; `shipmate doctor` warns on each one before an apply
  reaches it. Run `scripts/onboard`, which scopes each apply environment to the
  default branch, or set a policy in the environment's settings; either kind
  (selected branches or protected branches) passes. A shared bare `<env>` whose
  pull requests target branches besides the default one needs those branches
  added to its policy, not the policy removed.

What this does not cover, deliberately: the plan and drift bindings (those jobs
bind the row's `env_binding`; detect refuses an unusable one, but nothing on those
paths checks that the environment exists), an environment whose branch policy
names the wrong branches (nothing checks it; `shipmate doctor` warns on a missing
policy only), an environment with no approval rules (`shipmate doctor` notes
it), and an environment deleted between the pre-flight and the wave that binds
it.

### `.github/shipmate-config.yml` is rejected, or its settings do not take effect

Every row below but the `workloads lists` warning refuses at `detect`, before
any cell starts, and every one of them is reported against the **default
branch's** copy of the file — that is the only copy execution reads.

| What `detect` says | What it means |
| --- | --- |
| `could not be read from the default branch` | the file is not on the default branch yet, or the workflow token cannot read repository contents. A pull request that only *adds* the file is refused: merge it first ([`../CONTRACT.md`](../CONTRACT.md) §Environment table, "The file must reach the default branch before the first plan run") |
| `is not valid YAML: <problem> (line <L>, column <C>)` | the parser's own message. Besides a syntax error, the file refuses a duplicate key, an anchor, an alias, a tag (`!!str`) and tab indentation. A string value starting with `{`, `[`, `*`, `&`, `!` or `#` must be quoted; a `{vars: NAME}` reference and the `workloads` and `needs` lists are the unquoted exceptions |
| `the engine needs Python 3.12 or later; this runner has …` | the `runs_on:` image is older than the floor `../CONTRACT.md` §Runner prerequisites states — `ubuntu-22.04` ships 3.10. Name a newer image |
| `the engine needs PyYAML for python3 and this runner has none` | the runner's `python3` cannot import PyYAML; see §`could not install python3-yaml`, or `the engine needs PyYAML` below |
| `declares no layout` | either the key is genuinely absent, or it is indented under another key — see the indentation trap below |
| `<key> is not a setting this engine implements` | a top-level key this engine does not have, most often a misspelled `environments`. Only `layout`, `identities`, `environments` and `max_parallel` are accepted — the message lists them. A key a newer release introduces refuses here until the repository's pin moves to that release |
| `max_parallel is …; it must be a whole number from 1 to 256` | `max_parallel` takes a bare whole number, quoted or not: no sign, space, decimal point or leading zero. GitHub runs a matrix with `max-parallel: 0` unbounded, so 0 is refused; remove the key for the default of 10 |
| `max_parallel holds a variable reference, and it accepts none` | the cap is written into the file, so what bounds the plan and drift matrices stays a merged commit. Write the number |
| `environments.<env>.<key> must be true or false, got …` | `shared`, `explicit` and `gated` hold `true` or `false`, bare or quoted. `yes`, `True` or a number would otherwise resolve to the default |
| `environments.<env>.<key> holds a variable reference; a flag accepts no variable reference` | `shared`, `explicit` and `gated` are written into the file, so which environments are shared, held back or ungated stays a merged commit. Write `true` or `false` |
| `environment <env>: region is empty` | `region:` with nothing after it, or `region: #eu-west-1`, which YAML reads as a comment. Write the region, or remove the key |
| `layout references GitHub variable <NAME>, whose value is not one of …` | the variable holds something other than `tf_vars`, `workspace` or `folder`. Fix the variable; the refusal names it rather than quoting its value |
| `environment <env>: identity names no entry under identities` | the entry names an identity the file does not declare; the message lists the ones it does. A misspelled name lands here |
| `environment <env> names identity <name>, whose roles vary by workload, and lists no workloads` | the identity's roles depend on the workload tag, so the entry must list the workloads it admits: `workloads: […]` |
| `environment <env> lists workload <w>, and identities.<name>.aws.<field> has no <w> entry` | a listed workload has no role (or, for a role name, no account) in that map. Add the entry, or remove the workload from the list |
| `<n> cell(s) carry a workload tag their environment's workloads list does not name` | each named stack's `workload/<name>` tag is outside its environment's `workloads`. Retag the stack, or add the workload to the list on the default branch in a pull request of its own first; the plan reads the default branch's table, so the same pull request cannot fix it |
| `environments.<env>.workloads lists <w>, which no stack in <env> tags` | a warning, on drift, `unlock` and a bare `shipmate apply` only. The workload was listed ahead of the branch that tags it, or its last tag was dropped: tag a stack with it, or remove it from the list once the branch dropping its last tag has merged. An environment no stack tags at all gets the unused-entry warning instead |
| `environments.<name> is not an environment name` or `environments.<name> carries the environment suffix` | an `environments.<name>` entry whose name no stack tag can carry: Terramate refuses an uppercase letter in a tag, and the name is the bare logical env name, never `<env>-plan` or `<env>-apply`. Rename the entry to the name in the stacks' `env/<name>` tags |
| `... names 'shipmate-engine', the engine's own environment` | an entry, a `needs` item, a stack's `env/*` tag or a dispatched environment names `shipmate-engine`, with or without a `-plan`/`-apply` suffix. A `shared: true` entry of that name would bind the engine's own environment, which holds the App private key, so the name is refused as a table entry, a `needs` item, a stack tag and a dispatched environment. Rename the entry, retag the stack, or comment another environment |
| `<key> references GitHub variable <NAME>, which is not set` | no repository or organization variable of that name reaches the repository. Set it with `gh variable set <NAME>`; the next run reads it, no pull request needed. A variable on a cell's `<env>-plan`, `<env>-apply` or shared `<env>` Environment is never read, and on GitHub Free an organization variable does not reach a private repository ([`../CONTRACT.md`](../CONTRACT.md) §Variable references) |
| `<key> references GitHub variable <NAME>, which is set to an empty value` | a reference never means an empty string; give the variable a value or write the value into the file |
| `<key> references GitHub variable "<name>"; GitHub variable names are uppercase` | GitHub stores every variable name uppercase. Write the reference with the spelling the message gives |
| `<key> references GitHub variable '<name>', which is not a GitHub variable name` | the name is empty or holds a character GitHub refuses in a variable name. Names are `[A-Z_][A-Z0-9_]*` |
| `<key> references GitHub variable <NAME>, but this step received no GitHub variables` | either the engine did not pass `github-vars` to the step reading the file, or no repository or organization variable reaches the repository at all. With variables set, report it as an engine defect |
| `needs is cyclic: <a> -> <b> -> <a>` | the environments' `needs` order them in a loop, so none of them can go first. The path names the loop in apply order; an env listing itself is the one-node case. Drop one of the `needs` items |
| `needs spans <n> env levels: <a> -> <b> -> …. A deploy applies at most 4 env levels` | the longest `needs` chain, named deepest-last, has more env levels than a deploy applies. Shorten the chain to at most 4 levels |

**The indentation trap: a setting indented under the wrong key.** YAML nests a
line under the key above it that is indented less, so a mis-indented line is
well-formed and lands in the wrong place. Which message you get depends on
*where* it landed.

A misplaced `layout` reports `declares no layout`, plus a second line naming
the entry the key fell under. The second line is the diagnosis:

| Where it landed | The second line |
| --- | --- |
| under `environments.dev-eu` | `environment dev-eu: layout is not a key this engine implements. An environment holds region, tf_vars, identity, workloads, shared, needs, explicit, gated.` |
| under `identities.dev` | `identities.dev.layout is not a key this engine implements. An identity holds aws.account, aws.plan and aws.apply.` |
| under `identities.dev.aws` | `identities.dev.aws.layout is not a field this engine implements. An identity holds aws.account, aws.plan and aws.apply.` |

The fix in every case is the same: write the top-level settings at column 1.

**Three settings refuse nothing when they are absent.** `needs`, `explicit`
and `max_parallel` are optional and take tolerant defaults — no ordering, no
exclusions, and at most 10 cell jobs at once per plan and drift sweep. A file
that sets `explicit` on no entry is structurally valid, and a bare
`shipmate apply` then applies every environment, production included. Nothing
warns. `shipmate doctor` echoes all three on every report for exactly this reason;
read them before merging.

**Editing them on a branch does not change what that branch applies.**
`needs`, `explicit` and `max_parallel` are read from the default branch with
the rest of the file. Validate the change with `shipmate doctor` on the pull
request, then merge it — that is what makes it take effect.

### A cell fails with no AWS credential

The credentials step is skipped and the cell fails at `tofu init` with no role
assumed. Under the `tf_vars` layout an environment with no entry does not get
this far: `detect` refuses first, because that layout needs an entry with a
region for every environment in the matrix.

Four causes, in the order they are worth checking:

1. **The environment has no `environments.<name>` entry**, or one naming no
   `identity`. The cell resolves no role.
2. **The identity is apply-only and this is a plan cell.** An identity setting
   `aws.apply` alone resolves nothing on the plan path — a supported shape, and
   the reason a plan cell can skip credentials while the apply cell of the same
   environment does not.
3. **The stack is untagged and the identity varies by workload.** An untagged
   cell gets no role from a map or a `{workload}` role; tag it with a workload
   the environment's `workloads` lists.
4. **A line in the wrong entry put the fields somewhere else.** A line nests
   under the nearest key above it that is indented less, so an `identity`
   written below the *next* environment's name belongs to that environment
   instead. Check which key each line sits under; §`.github/shipmate-config.yml`
   is rejected, or its settings do not take effect has the rest of that hazard.

Confirm the default branch's copy, which is the one the engine reads:

```bash
git show origin/<default-branch>:.github/shipmate-config.yml
```

An `environments` that is genuinely empty is a valid shape, so its absence alone
is not the diagnosis: a credential-free repository whose file holds only
`layout: workspace` resolves no role anywhere and is working as documented
([`../CONTRACT.md`](../CONTRACT.md) §Environment table).

`shipmate doctor` on the pull request reports whether the file at that commit
parses and passes the structural checks, which tells a malformed file apart from
a well-formed one that names no role.

### A state lock is held

Every apply of the cell fails acquiring the lock, and the apply result comment
carries a `state lock held:` note under its cell lines naming each such cell, the held lock's id, the
command that releases it, and — when the backend reported a usable timestamp —
when it was taken. Per-cell concurrency admits one apply at a time, so the
holder was that cell's own most recent apply run — one that was cancelled or
killed before it could release.

Comment `shipmate unlock <env>`. The environment is required; there is no bare
form. It dispatches your repository's own `.github/workflows/shipmate.yml` with
`verb: unlock`, which that file's `unlock` job selects
([`getting-started.md`](getting-started.md) publishes the file). A repository
carrying no such file gets no run at all — the dispatch is refused with a 404,
the comment-handling run carries an error saying so, and the dispatch failure
reply on the pull request links that run when it can be posted (`CONTRACT.md`
§Comment-ops). A file GitHub does
dispatch but whose jobs do not select `unlock` produces a run in which every job
skips (§A dispatched verb produced a run in which every job was skipped). It
authorizes on the commenter's write or admin permission on the repository and
the `<env>-apply` environment, not on a review — so a lock
stranded after the pull request merged is still releasable
([`../CONTRACT.md`](../CONTRACT.md) §Comment-ops has the contract). Every cell
in that environment with a pending `apply / <stack> / <env>` check is probed;
each reports in its own job, and a cell that cannot determine its lock state
fails red rather than reporting the lock absent.

Then re-apply. Unlocking is not recovery: a cancelled apply usually advanced
the state, so `shipmate apply <env>` may now be refused by the exact-plan
fail-safe of §`Saved plan is stale`, or `does not match the reviewed plan's
fingerprint` — re-plan and apply the fresh plan. The
lock is released either way; the plan is a separate question.

Two locks this verb does not reach:

- **A cell with no pending apply check.** A stack applied out of band, or one
  whose check already completed, is not in the queue, so release it the way
  it was locked: `tofu force-unlock <id>` in that stack's directory, with the
  backend's credentials, having confirmed the id against the error the next
  apply prints.
- **A lock a human is holding right now.** Someone is running OpenTofu by hand
  against the same state. That is the lock unlock must not break, and it is why
  nothing here waits on a lock or releases one it did not probe: check with
  whoever is running it before forcing anything.

### `SHIPMATE_PLAN_PASSPHRASE is not set`, or `SHIPMATE_PLAN_PASSPHRASE is empty`

The cell received no passphrase. It encrypts every plan artifact and a local
backend's cached state, so nothing proceeds without it
([`../CONTRACT.md`](../CONTRACT.md) §Plan artifact encryption):

- `SHIPMATE_PLAN_PASSPHRASE is not set` comes from `scripts/plan-crypt`: a plan
  cell at `Require the plan passphrase`, before it initializes the stack, or an
  apply cell at `Decrypt reviewed plan artifact`.
- `shipmate/actions/state: SHIPMATE_PLAN_PASSPHRASE is empty` comes from a local
  backend's state restore or save, which is where a drift cell refuses.

Set `SHIPMATE_PLAN_PASSPHRASE` as a repository or organization secret, or as the
same value on both `<env>-plan` and `<env>-apply`, and map it by name in the
`secrets:` block of `shipmate.yml`'s `plan`, `deploy` and `apply` jobs and of
each drift file's `drift` job. A secret on `shipmate-engine` or on one half of a
split environment alone reaches the wrong cells
([`getting-started.md`](getting-started.md) lists each placement).

### `plan artifact ... failed authentication`

An apply cell refused its reviewed plan at `Decrypt reviewed plan artifact`,
before openssl or any apply step read it, and its `apply / <stack> / <env>`
check stays pending. The tag the plan cell wrote does not verify under the
apply side's passphrase. The engine cannot tell the causes apart:

- **The passphrase was rotated after the plan.** Re-plan.
- **The plan and apply sides read different values**, for example different
  `SHIPMATE_PLAN_PASSPHRASE` secrets on `<env>-plan` and `<env>-apply`. A
  re-plan alone fails the same way. Make the two equal, then re-plan. On a
  local backend, set `<env>-plan`'s value to `<env>-apply`'s and never the
  reverse, because apply cells saved the cached state; on a remote backend
  either direction works.
- **The artifact is corrupt, truncated or altered.** Re-plan; a re-run
  downloads the same artifact.

To re-plan on an open pull request, comment `shipmate plan` or push; after the
merge, open a new pull request touching the stack.

### `plan artifact ... cannot be read`

The apply cell could not open its reviewed plan artifact; the error at
the end of the message says why. Nothing was applied.

- **The artifact downloaded without `stack.otplan`.** No earlier step reports
  it. Re-plan: comment `shipmate plan` or push on an open pull request; after
  the merge, open a new pull request touching the stack. A re-run downloads the
  same artifact.
- **The runner's disk failed.** The refusal comes before any apply step, so a
  re-run of the job is safe; a self-hosted runner needs its disk fixed.

### `plan artifact ... could not be decrypted: openssl or the disk failed on the runner`

openssl or the disk failed on the apply runner, not the artifact; any openssl
error is printed above the refusal. Nothing was applied. A re-plan does not
help, because the decrypt runs on the apply runner: the apply fails again until
that runner is fixed or a re-run of the job lands on a working one. Re-run the
job, which clears a transient hosted-runner disk failure, or fix a self-hosted
runner's openssl or disk, for example an openssl without `-pbkdf2` or `-iter`,
or a full disk.

### `... could not be encrypted: openssl or the disk failed on the runner`

openssl or the disk failed on the runner; any openssl error is printed above
the refusal. What to do depends on the step that refused:

- **A plan cell, encrypting its plan before upload.** Nothing was uploaded or
  applied. Re-plan once the runner works; a transient hosted-runner disk
  failure clears on its own, and a self-hosted runner needs its openssl or disk
  fixed.
- **An apply cell's `Save state`.** See
  §`The apply step ran and its state was not saved`, which says when a re-run
  of the job is safe.

### `The apply step ran and its state was not saved`

An apply cell on a local backend ran its `Apply the stored plan` step (status
`success`, `failure` or `cancelled`), and `Save state` did not store the state
that step left: the save failed or was cancelled, the exact-key lookup after it
found no entry, or there was no state file to save. The warning step fails the
cell's job, so its apply check stays pending. An apply run that was not
cancelled posts an apply comment; its row reads not recorded when its apply
succeeded, otherwise failed, and its `state not saved:` note names up to 5
lost cells whose summary artifact uploaded and counts the rest. Without that
artifact the row reads not attempted, and the pointer every `not attempted:`
note carries leads here. A cancelled run and the merge deploy post no comment, and a later run's comment does not
know the state was lost: the job log and the pending check are the signal.

Unless step 1 finds the entry, the cache still holds the stack's entry from
before the apply, or none on its first apply. What the apply did exists, and OpenTofu does not track it. A first
apply that OpenTofu refused before writing anything warns too, and this page
does not tell it apart: a log without completion lines does not prove nothing
changed, because an interrupted apply can create a resource before OpenTofu
logs or records it.

When the same job's `Apply the stored plan` step is `skipped`, nothing was
applied, this warning is not printed, and a re-run of the job is safe. In every
other case, re-plan and reconcile against the provider:

1. Check whether the save stored the entry after all:
   `gh cache list --key <key> --json key --jq '.[] | select(.key == "<key>")' --repo <owner>/<repo>`,
   with the exact `key` the `Save state` step log shows (`--key` alone matches
   by prefix). When it is printed, the save worked and only the lookup failed:
   re-plan (step 4) and never re-run the job; the re-plan reads the saved
   state. After a successful apply it shows no changes. After a failed or
   cancelled apply, OpenTofu may not have recorded, and the step log may not
   show, a resource the provider created: a create that timed out after the
   object existed, or a cancel mid-create. Neither the entry nor the log proves
   the re-plan's creates are new. Before applying the re-plan, check each
   create it shows against the provider (the cloud console or API), the new
   object of a `+/-` (`create_before_destroy`) replace included. Adopt a plain
   create that already exists with an `import` block (step 5). For a `+/-`
   (create_before_destroy) replace whose new object may already exist at the
   provider, the engine documents no recovery, and this case is not probed.
   Do not apply the re-plan or forget the address. The plan does not identify
   the object the lost apply created; the `Apply the stored plan` log names it
   only if it shows `Creation complete … [id=…]` for that address. On the open
   pull request, the re-plan completes or replaces the cell's apply check.
   After the merge, a new pull request's plan completes only its own head's
   check. The merged head's check stays pending, and nothing reads it again:
   a later deploy takes only the pending checks of its own merge's pull
   request. Never re-run that deploy, which reads it. When nothing is printed,
   continue.
2. Never re-run the job or its workflow run, the deploy included: a re-run
   applies the run's reviewed plan again whatever the check says. A
   `shipmate apply` comment, bare or for the cell's environment, and a merge of
   the pull request, whose deploy takes the pending check, apply the plan the
   cell's pending apply check holds, which is the reviewed plan until a re-plan
   replaces it. Steps 1 and 4 decide whether a re-plan is safe to apply or
   merge. After the merge, the merged head's check stays pending (step 1) and
   its deploy is never re-run. The reviewed plan was made against the state
   the cache restores, so OpenTofu applies it with no stale-plan refusal and
   creates a second set of what the lost apply created. Every other plan of
   this stack in this environment, on any pull request, repeats the lost apply
   as well (step 4): apply or merge none of them except the import and forget
   plans of steps 5 and 6, which are applied the ordinary way, with
   `shipmate apply` or by the merge deploy. The stack's plans in other
   environments are unaffected.
3. List what the lost apply did from its `Apply the stored plan` step log:
   `Creation complete ... [id=...]` and `Destruction complete` lines. The
   cell's fold-out in the apply comment shows the same output when a comment
   was posted. When the lost state was a deleted cache entry, list every apply
   of the stack and environment since the run that saved the entry the restore
   now uses, not only the deleted entry's run: each key ends with the
   `<run_id>-<run_attempt>` of the run that saved it, and
   `gh cache list --key state/<stack-slug>/<env>/ -L 100 --sort created_at --repo <owner>/<repo>`
   lists the entries that survive, newest first.
4. Re-plan on a pull request touching the stack: on an open pull request,
   comment `shipmate plan` or push; after the merge, open a new pull request.
   When step 1 found no entry, the plan repeats the lost apply. Do not apply
   it as it stands: apply a plan
   from this procedure only when each resource the lost apply touched shows
   `to import` or `to forget`, or a create, replace or destroy that steps 6 to 8
   leave no way around and you accept. Any other plan repeats the lost apply.
5. For a resource the lost apply created at an address the restored state
   lacks, add an `import` block with the id from its `Creation complete` line
   to that pull request. Its plan shows `1 to import`.

   ```hcl
   import {
     to = random_id.new[0]
     id = "<id>"
   }
   ```

6. For a resource the lost apply replaced or destroyed, the restored state
   still holds the old instance, and an `import` to an address the state holds
   is ignored without an error: the plan still replaces it. Use two pull
   requests, each planned and applied through shipmate like any change:
   - The first deletes the resource block and adds a `removed` block. Its plan
     shows the resource as `to forget`.

     ```hcl
     removed {
       from = random_id.x
       lifecycle {
         destroy = false
       }
     }
     ```

   - The second restores the resource block and adds an `import` block for
     every instance of that resource: `removed` forgets every instance of a
     `count` or `for_each` resource, and OpenTofu 1.12 accepts no instance key
     in `removed.from`. The surviving instances' ids are on the re-plan's
     `Refreshing state... [id=...]` lines, the lost apply's on its
     `Creation complete` lines.

   One plan cannot do both: a `moved` block to another address plus `removed`
   and `import` refuses with `Error: Moved object still exists`. A resource the
   lost apply only destroyed has nothing to import, and the re-plan plans its
   destroy again. Whether a real provider's refresh drops the deleted object
   from that plan is unprobed; where it does not, a `removed` block with
   `destroy = false` forgets it.
7. A resource whose provider does not implement import cannot be adopted:
   `random_pet`, which the sample stacks use, refuses with
   `Error: Resource Import Not Implemented`. Applying the re-plan re-creates
   it. That is harmless for a sample's `random_pet`, and a duplicate or a
   collision for a real one.
8. Review each plan that imports. A provider can import fewer attributes than
   it wrote and then plan a replace: `terraform_data` does not import
   `triggers_replace`, and `random_id` does not import `keepers`. Two points
   are unprobed: the import ID a resource type takes is the one its provider's
   documentation names, which may differ from the `id` in the log; and with a
   provider whose refresh notices a deleted object, the re-plan may show a
   replaced resource as a create rather than a replace.
9. Once applied, delete the `import` and `removed` blocks in a later pull
   request.

### `state cache entry ... cannot be read`

The restore could not open the matched entry: the restore may not have written
the file, or the runner's disk failed. The error at the end of the message
says which. tofu was not run. The refusal comes before any apply step, so a
re-run of the job is safe; a self-hosted runner needs its disk fixed.

### `state cache entry ... could not be decrypted: openssl or the disk failed on the runner`

openssl or the disk failed on the runner, not the entry; any openssl error is
printed above the refusal. Deleting the entry does not help. Re-run the job,
which clears a transient hosted-runner disk failure, or fix a self-hosted
runner's openssl or disk, for example an openssl without `-pbkdf2` or `-iter`,
or a full disk.

### `state cache entry ... failed authentication`

A local backend's restore matched a cache entry whose tag does not verify, and
refused before openssl or tofu read it. The refusal names no delete command:
the engine cannot tell a wrong passphrase from a damaged entry, and deleting an
intact entry loses the state it holds. A wrong passphrase is fixed by the
secret, never by deleting. Check the causes in this order:

- **The passphrase changed after the entry was saved.** Evidence: a
  `SHIPMATE_PLAN_PASSPHRASE` placement (organization, repository, `<env>-plan`
  or `<env>-apply`) updated after the entry's `createdAt` in
  `gh cache list --key <key> --json key,createdAt`. `gh secret list`, with
  `--org <org>` or `--env <environment>`, shows each placement's update time.
  Every older entry under the prefix was saved with the old value too. Set the
  old value back.
- **The plan and apply sides read different values.** Evidence: `<env>-plan`
  and `<env>-apply` each hold their own `SHIPMATE_PLAN_PASSPHRASE`. An apply
  cell saved the entry and a plan or drift cell restores it. Set
  `<env>-plan`'s value to `<env>-apply`'s, never the reverse, then re-plan or
  re-run the drift sweep.
- **The entry is corrupt, truncated or altered**, or was copied under
  another stack's or environment's key. Neither of the above holds.
  Another cell restoring its own entry proves nothing, because entries are per
  stack and environment. Delete the named entry, last:
  `gh cache delete <key> --repo <owner>/<repo>`.

Deleting has a cost. The restore then uses the next-older entry under the
prefix if one survives (entries unused for the repository's cache retention
period, 7 days by default, are evicted), or none, so the
stack is in the state §`The apply step ran and its state was not saved`
describes: its step 3 lists every apply since the entry the restore then uses.

### `could not install python3-yaml`, or `the engine needs PyYAML`

The engine's scripts parse YAML with PyYAML, through the runner's `python3`.
Before the first step that parses YAML, the engine's `python-yaml` action
checks that `python3` imports it. When it does not, the action installs
`python3-yaml` with `sudo -n apt-get`. When that install does not take, the
step warns and the job continues:

> could not install python3-yaml on this runner; the steps that read YAML report it.

The script that needs the parser then refuses with:

> the engine needs PyYAML for python3 and this runner has none. Install python3-yaml (Debian or Ubuntu) or PyYAML >= 6 for this runner's python3; CONTRACT.md section Runner prerequisites lists it.

On a plan, the gate's description is `detect failed: ` followed by the start
of this refusal, cut to the 140 characters a commit status holds.
`shipmate doctor` prints it once as a notice and skips its probes that parse
YAML.

The install needs a Debian or Ubuntu runner with passwordless `sudo`. Fix:

- A self-hosted runner without passwordless `sudo`, or not on Debian or
  Ubuntu: preinstall PyYAML 6 or later for the image's `python3` (the
  `python3-yaml` package on Debian or Ubuntu).
- A runner with passwordless `sudo` on Debian or Ubuntu: the step log shows
  `apt-get`'s own error. A package-mirror outage clears on a re-run of the
  failed job.

### `Terramate install failed`

The `Install Terramate` step in `actions/setup` failed. Every annotation starts
"Terramate <version> did not install:" and ends with the cause:

| Annotation ends with | Cause and remedy |
|---|---|
| "curl is not installed on the runner." | The runner has no `curl`. Install it on the runner image. |
| "curl exited 2 on an option it does not know; shipmate needs curl 7.71 or later (CONTRACT.md §Runner prerequisites)." | The runner's curl is older than 7.71 and rejects `--retry-all-errors`. A re-run cannot help: upgrade curl on the runner image. |
| "<url> answered HTTP 404 (curl exit <rc>); check VERSIONS and the runner's OS and architecture." | No release asset exists for that version, OS and architecture. A re-run cannot help. |
| "<url> answered HTTP <code> (curl exit <rc>); re-run the failed job." | The download failed. `<code>` is the failed download's own status; `000` means no response arrived. Re-run the failed job. |
| "tar could not extract terramate from <url>." | The downloaded archive did not unpack. |
| "the binary from <url> failed to run terramate --version." | The unpacked binary did not run. |
| "<url> delivered version <got>; check VERSIONS and the release asset." | The binary reports another version than the pinned one. |
| "VERSIONS pins no sha256 for <asset>; shipmate installs on Linux x86_64 and arm64." | The runner's OS or architecture has no pinned digest, so nothing was downloaded. Run the job on a Linux `x86_64` or `arm64` runner. |
| "<url> has sha256 <got>, not the <pinned> VERSIONS pins; the release asset differs from the pinned one." | The downloaded archive is not the one the engine release pinned. A re-run downloads the same asset: report it, and do not install it by hand. |

### `carries no sha256 line` or `Failed to validate OpenTofu CLI zip checksum`

The `Resolve versions` step in `actions/setup` fails with "<path>/VERSIONS carries
no sha256 line for <tool> <version>; paste the release's checksum lines." when the
engine's `VERSIONS` names a version without that release's checksum lines. It
happens only on an engine commit whose tool-version bump left them out
(`docs/releasing.md` §Bump Terramate or OpenTofu); pin a released engine commit.

The `Install OpenTofu` step fails with "Failed to validate OpenTofu CLI zip
checksum" when the downloaded zip's sha256 is not among the two `VERSIONS` pins
for that version: the asset differs from the pinned one, or the runner is not
Linux `amd64` or `arm64`. A re-run downloads the same asset.

curl retries HTTP 408, 429, 500, 502, 503 and 504 responses, timeouts and stalls
three times before the step fails. It also retries connection resets and TLS
errors (`--retry-all-errors`), which is why setup needs curl 7.71 or later.

### `Failed to install provider`: `existing cached package ... does not match the content of the downloaded package`

`tofu init` refused a provider package restored from the provider cache: its
content does not match the hash in the stack's `.terraform.lock.hcl`. The
cache entry is corrupt or was altered. Every cell whose lock file names the
same provider versions fails the same way until those versions change or the
entry is deleted
([`../CONTRACT.md`](../CONTRACT.md) §Terramate safeguards, "Lock files and the
provider cache").

This failure also carries the cell's `OpenTofu's init failed after restoring
provider cache entry <key>` annotation when the restore hit. Any failed init
that OpenTofu itself reports carries its first error as an annotation, naming
the cause: the backend, credentials or the configuration.

1. Read the key from the cell's error annotation, `OpenTofu's init failed
   after restoring provider cache entry <key>`, or from the line
   `Cache restored from key: <key>` in its `Initialize the stack` step log.
   It starts `tofu-providers-`.
2. Delete the entry: `gh cache delete <key> --repo <owner>/<repo>`.
3. Re-run the failed jobs. The cells download their providers, and the next
   drift or apply cell saves a fresh entry.

### `shipmate / gate` never goes green

Five distinct causes, in the order worth checking.

**No gate status was written at all, as opposed to a red or held one.** The
engine's `summary` job is skipped on a fork pull request — there is no gate, no
plan comment, and nothing on the run page saying why. The pull request cannot
merge, which is the intended direction. The head repository comes from the
`facts` job in the same engine file, so a skipped `summary` is not a wiring
mistake: check the pull request's head repository. A pull request whose head is
in a fork is refused earlier still, at `detect` ("Fork pull request
refused", below).

**The gate is pending with the draft reason.** The description reads "the pull
request was a draft when this run started, so nothing was planned; comment
`shipmate plan`, or mark it ready". Autoplan skips a draft's plan jobs, so the
gate has nothing to green over. Recover either way:

- Mark the pull request ready for review; its autoplan plans the head.
- Comment `shipmate plan`, which plans a draft. Applies still refuse a draft.

A head that already carried a `shipmate / gate` status when the draft run
reached it keeps that status; the draft status is written only on a head with
none. A draft marked ready within seconds of a push can keep the draft status:
the ready run shares the `plan-<pr>` concurrency group with the draft run, and
if the ready run is the one cancelled, no plan follows. Which of the two runs
the group cancels in that race is not verified. Comment `shipmate plan` to
recover.

**A pending apply check nothing will complete.** `gate-refresh` greens the gate
only when every shipmate-App-authored check on that commit whose name begins
`apply / ` has a latest run completed as `success` or `neutral`, and the apply
paths complete only the names the current plan run produced. A leftover pending
check under a name no current cell reconstructs (a renamed or deleted stack)
therefore holds the gate indefinitely. GitHub has no way to delete a check run, so the recovery is a
new head SHA: push a commit, and the plan run re-creates only the checks that
exist now.

**The gate is deliberately held.** A gate whose state is `failure` with a
description telling you what to do is not "no changes"; it is a hold.
`gate-state` compares the cells `detect` planned against the cell summaries the
trusted summary job actually read, and the job writes a hold when they disagree in
either direction, when the plan job failed or was skipped with cells planned,
when `detect` did not succeed, or when the planned count was not reported at
all. A run whose cell count is zero for any of those reasons must not claim
nothing changed: `deploy-detect`'s work queue *is* the pending apply checks, so
a green gate over cells nobody accounted for merges reviewed stacks that then
never apply. The summary job also holds the gate while it publishes the apply
checks, reading "plan run <id> holds this gate while it publishes; re-plan
(comment shipmate plan or push) if that run has ended". That hold stays when the
run ends before its final gate write (a failed or cancelled summary job, or a
cancelled plan job after the summary downloaded a cell summary). A sticky plan
comment that could not be listed or written, or whose pull request head could
not be read ("could not read the head of pull request #<n>"), fails the summary
job before that write too, and before any apply check is published; the error
is in the job's annotation for a failed write and in the step log for a failed
listing or head read. A held run's comment write, which only explains the red
gate, warns on those failures instead, and the gate is still written. What a
summary job that fails part way leaves on the comment is in
[`CONTRACT.md`](../CONTRACT.md) §Plan comment. The
summary job also holds the gate when it cannot read the head's gate statuses or
apply checks, or cannot build the checks to publish (a cell summary missing a key),
whether or not it downloaded a cell summary: a failed download leaves none over
planned cells. That hold reads "re-plan this pull request (comment shipmate plan
or push): its plan run ended before writing a final gate". Its apply-check
publish and its final write each refuse, writing nothing and keeping the gate
as it stands, when a newer plan run of the same head holds the gate by then. A plan run whose `summary` job was
cancelled before it ran, because a later gate writer on the same head queued
behind it (`CONTRACT.md` §Comment-ops, "The aggregate gate"), holds the gate
from its `gate-hold` job, reading "its plan summary was cancelled before it
published". That hold can land after another run's summary greened the head,
closing a gate over work already applied. Every one of these holds clears the
same way: comment `shipmate plan` or push.
A run held for its plan job or its cell count also publishes no apply check.
Checks an earlier plan run of the same head published stay; on a head with none,
a `shipmate apply` replies "no reviewed plan for the current PR head".

"Re-run failed jobs" on the plan run is safe only when that run is the newest
plan run of the head. The re-run re-enters the `plan-<pr>` concurrency group,
whose `cancel-in-progress: true` cancels a newer run in flight, or the newer run
cancels it. A re-run summary publishes no apply check its own run already
published, so it leaves alone a check an apply completed or is completing. It
refuses before writing anything when a newer plan run holds the gate or published
apply checks on the head, and ends green with that refusal as a notice. A re-run of a plan job whose artifact upload succeeded
fails, because the uploads are not `overwrite:` and 409.

`gate-refresh` refuses to green a held gate from apply checks alone: the apply
run fails with "shipmate / gate is held", pointing at this status description.

**The required check does not match the status.** The ruleset pins
`integration_id` to the shipmate App id, so a `shipmate / gate` status of the
right name authored by any other identity leaves the pull request blocked — as
does a pin to the wrong App id. See `branch-protection.md` §Reproducible
ruleset.

### An environment was held for review, or the apply was refused

The apply comment carries a line
`⚪ <env>: held, the review state does not permit applying`, or
`shipmate apply` was refused with a review reason.

Which review decision refuses a targeted apply, and which environments a bare
apply holds for each decision, is in [`../CONTRACT.md`](../CONTRACT.md)
§Comment-ops (the `gated: false` rules).

**The apply is refused although the environment is ungated.** Check which copy
of the file you edited: all three readers resolve `gated` from the
**default branch**, so a flag added on the pull request asking for the apply
does nothing until it merges. Then check the entry against the rules below. The
refusal is by *shape*: a non-boolean `gated`, or an entry name with a suffix or a
character outside the env charset, is rejected loudly, naming the key. Spelling
is never refused, and it warns only where a run has already scanned the whole
tree: `gated: false` on `environments.dev-eu2` for `dev-eu` is accepted and
`dev-eu` simply keeps its review requirement, but a run that scanned the tree
names `dev-eu2` as an entry no stack tags
([`../CONTRACT.md`](../CONTRACT.md) §What the diagnostics can and cannot see).
See
[`getting-started.md`](getting-started.md) §"Applying chosen environments without
an approving review".

**The run failed with an `environments.<env>.gated` or `environments.<name> is not an
environment name` error.** The value or name is rejected loudly rather than left
silently inert:

- anything but `true` or `false` — `gated: no`, `gated: 0` or a
  variable reference would otherwise resolve as gated while reading as ungated;
- an entry name with a `-plan` / `-apply` suffix, or outside the env charset
  (lowercase letters, digits, `-`, `_`) — the name is matched against the bare
  logical env name carried by the apply checks, so write `environments.dev-eu`,
  not `environments.dev-eu-apply`.

**`shipmate apply` answers “could not resolve the gate settings” and carries no
🚀 reaction.** A malformed entry, or any other refusal of the file, is raised
while comment-ops resolves the gate settings, which is after the write-access
check but before both the 🚀 reaction and the rest of the authorization. So for
a commenter with write access, a repository-wide breakage of `shipmate apply`
and `shipmate unlock` (every command, every environment, however well-formed)
arrives as that one comment, whatever the command said, and
the comment-ops run then fails. Which cause it was is the `::error::` annotation
on that run; open it from the Actions tab. A targeted
apply that was genuinely refused comments its own authorization reason instead,
so this comment points at the configuration rather than at the authorization. A
file broken this way also refuses every plan run, which is the louder signal of
the two.

**A held environment is also an explicit environment.** When both causes apply
it is listed once, on its held line, because the hold is the thing to
clear first. Clearing it does not release the environment into a bare
`shipmate apply`, though: its entry still holds `explicit: true`, so it still
needs a targeted `shipmate apply <env>`. The held line names that command:
``⚪ prod: held, the review state does not permit applying; once it clears, comment `shipmate apply prod` ``.

**An ungated environment that is also explicit is not held.** It gets the explicit
line, ``🟡 <env>: left pending (explicit), comment `shipmate apply <env>` `` — and that targeted apply
then succeeds without an approving review, because the environment is
ungated. `gated` and `explicit` are independent: the first decides whether a
review is required, the second only decides that a bare apply will not reach it.
An environment that must never apply unreviewed must not hold `gated: false`.

### `stack ordering cycle through <stacks>`

The annotation reads:

```text
stack ordering cycle through stacks/app, stacks/dns: a stack ordered after itself, directly or through other stacks, has no first wave. Remove one `after` or `before` entry on that loop in the stack configuration.
```

The `after` and `before` entries of the named stacks form a loop, or one stack
is `after` itself, so no stack on it can run first. The plan, both apply forms
and the merge deploy read the whole tree's run-graph and refuse before any cell
runs, so the loop need not touch the pull request's own stacks, and a plan with
no cell to plan refuses too.

1. Remove one `after` or `before` entry on the loop.
2. Re-plan (push, or comment `shipmate plan`).

A loop can still reach the default branch, for example when two pull requests
each add one half of it (each head plans without a cycle, and the second merge
closes it). The deploy of that merge then refuses, applies nothing, and points
to this entry; when the merge left cells pending, the annotation lists them
after `pending on this merge:`. The loop now lives on the
default branch, so a re-run refuses again, every later deploy refuses until a
fix lands, and every plan whose head carries the loop refuses.

1. Open a pull request that removes one `after` or `before` entry on the loop
   and changes the stack of every cell any refused deploy named after
   `pending on this merge:` (any edit `terramate list --changed` marks).
2. Its cells are applied before merge under a required gate (`shipmate apply`,
   and `shipmate apply <env>` for an `explicit` environment), or by its deploy
   otherwise. Merge it.

A refused deploy's apply checks are never read again. Its gate and `detect`
annotation read as under
[A deploy refused an apply check on a stack that lost its env tag](#a-deploy-refused-an-apply-check-on-a-stack-that-lost-its-env-tag).

A drift sweep does not refuse a cycle: its cells on the loop fail at init.

### `needs order inverted, fix default-branch needs or tags`

The annotation reads:

```text
needs order inverted, fix default-branch needs or tags: platform / dev-eu before dns / dev-us, which it runs after
- platform / dev-eu (env-level 0) after dns / dev-us (env-level 1); dev-us's level comes from dev-eu -> dev-us
Env-levels apply in order, so `after` cannot be met. Drop or reverse a `needs` entry in that chain on the default branch, or retag the stacks so the edge stays inside environments that order it.
```

A stack is `after` a stack in another environment, and `needs` puts that other
environment on a later env-level, so no order meets both. The plan, a bare
`shipmate apply` or the merge deploy refused before anything applied; a plan
refusal holds the gate with the message, flattened and cut to 140 characters, as
its description, and a deploy
refusal turns the merged head's gate to `failure`. Each `- ` line names one
inverted pair and the `needs` chain that sets the later level.

1. Pick the order you want. Remove or reverse the `needs` entry in the named
   chain in `.github/shipmate-config.yml`, or retag the stacks so the `after`
   edge stays inside environments `needs` orders the same way.
2. Merge that change on its own pull request: every reader takes the table
   from the default branch. A pull request that only changes the table plans no
   cell and is not refused.
3. On a refused plan, re-plan the original pull request (push, or comment
   `shipmate plan`). On a refused bare apply, comment `shipmate apply` again.

A refused deploy appends the cells of the merge it left pending. After a
`needs` fix merges, re-run the refused deploy: it reads `needs` from the
default branch and applies them. For a fix to tags or `after`, open a new pull
request that makes it and changes every listed stack; its cells are applied
before merge under a required gate (`shipmate apply`, and `shipmate apply
<env>` for an `explicit` environment), or by its deploy otherwise. A re-run
reads tags and `after` from the merged commit and refuses again.

A targeted `shipmate apply <env>` never refuses this: it does not order by
`needs`, and a cell whose upstream is pending in another environment waits
instead (next entry).

### An apply comment line says a cell waits on another cell

The apply comment carries a line
`⚪ <stack> / <env>: waits on <stack> / <env>, not applied yet; <remedy>`, or,
in its compact form, `⚪ waiting on <stack> / <env> (<remedy>): <cells>`.

The first cell runs after the second through `after`
([`../CONTRACT.md`](../CONTRACT.md) §Fan-out), and the second is pending and
not applying in this run: another environment on a targeted apply, or a held,
explicit or skipped environment on a bare one. The waiting cell's apply check
stays pending and the rest of the run applied. Follow the remedy:

- ``apply it first with `shipmate apply <env>` ``: apply the blocker's
  environment, then apply the waiting cell's environment again.
- ``it is held until the pull request is approved, then comment
  `shipmate apply <env>` ``: the blocker's environment is held and explicit.
  Get an approving review, then comment `shipmate apply <env>` for it.
- `it is held until the pull request is approved`: get an approving review
  (§An environment was held for review, or the apply was refused), then comment
  `shipmate apply`.
- ``its environment waits on its `needs` predecessors, which must apply first,
  including any held for review``: get any review the predecessors are held
  for, apply an explicit one with `shipmate apply <env>`, then comment
  `shipmate apply`.

Before any of these, check the run for a cell whose job log shows
`The apply step ran and its state was not saved`: any apply that covers its
environment applies that cell again. Follow
§`The apply step ran and its state was not saved` for it first.

A targeted apply always names `shipmate apply <env>`, even when the blocker's
environment is held or ordered by `needs`, because the targeted form's comment
receives no held or skipped environments. A run in which every cell waits applies nothing, succeeds, and its
verdict reads `⚪ nothing applied, cells wait on pending cells they run after`.

### A stack's plan does not show its upstream stack's change

A pull request changes two stacks joined by `after`, and the dependent reads
what its upstream changes (an output, a data source lookup). Its plan shows the
upstream's current state, not the state after the upstream's change applies:
applies follow `after`, plans do not ([`../CONTRACT.md`](../CONTRACT.md)
§Fan-out). Nothing refuses the apply: a saved plan goes stale only when its own
state changes.

Split the change into two pull requests. Apply the upstream's first, then open
the dependent's, whose plan reads the applied state.

### `Pull request #<n> head is <sha>, but this dispatch names <sha>`

The apply run failed in its `review` job, before any wave, and nothing applied.
The job reads the pull request the dispatch names and refuses unless that pull
request's current head commit is the dispatched `ref`, compared whole. A push
landed between the `shipmate apply` comment and the run. Wait for the new head's
plan, then comment `shipmate apply` again.

A run refused or cancelled in `review` posts no apply comment and leaves the
gate as it was: the red run is the signal. Find it from the dispatch reply on
the pull request when that reply links it, otherwise in the Actions tab
(`CONTRACT.md` §Comment-ops says when the reply links the run).

An apply run whose `summary` job was cancelled before it ran, because a later
gate writer on the same head queued behind it, also posts no apply comment. Its
results and its waiting cells show only on that run's page. The writer that
evicted it runs next: a plan `summary` writes the gate from its plan; an apply
`summary` refreshes it, which greens it once every apply check is done and
otherwise leaves it as it is, and fails the run over a hold; a deploy `summary` writes its deploy
verdict.

The same step refuses, also before any wave:

- **`Pull request #<n> is MERGED, not OPEN`** (or `CLOSED`). A merged pull
  request's plans apply through the merge deploy, not through `shipmate apply`.
- **`Pull request #<n> is a draft`.** It was converted to a draft after the
  comment. Mark it ready for review, then comment the apply again.
- **`Pull request #<n> comes from a fork`.** See
  §`shipmate applies and unlocks only branches of this repository`.
- **A run that fails at the query itself, with `Could not resolve to a
  PullRequest`.** The dispatched number names no pull request in this
  repository. `shipmate apply` always sends the commenting pull request's
  number, so look for a dispatch that comment-ops did not send.

### `apply aborted: the plan recorded for ... was not made on pull request #<n>`

The apply detect refused every cell it would apply whose
`apply / <stack> / <env>` check records a different pull request, or none, and
nothing applied. Each check records the pull request its plan run planned; two
open pull requests can share one head commit, and only the newest plan on that
commit decides which of them may apply. Re-plan on this pull request (push, or
comment `shipmate plan`), then comment the apply again. A cell whose check
already completed is not compared. The post-merge deploy does not read the
record's pull request.

### A pull request planned zero cells

No per-cell `shipmate / <stack> / <env>` checks appear, no plan comment is
posted — unless there is already a plan comment to keep current, `doctor`
raised a warning on that run outside the repository-settings probes, or the
pull request changes a stack with no `env/*` tag, any of which still posts one,
headlined `⚪ nothing planned` — and the gate goes green over no work.
The skipped plan job's one check shows its name expression literally,
`shipmate / ${{ matrix.stack }} / ${{ matrix.environment }}`, skipped; GitHub
renders a skipped matrix job that way.

Change detection is `terramate list --changed`, so a pull request that touches
no stack's own files and changes no generated `.tf` — an engine-pin bump, a docs
edit — plans nothing. This is expected, not a fault.

A changed stack with no `env/*` tag is unmanaged and plans nothing either. The
plan comment names it under the verdict, and the `detect` log carries a notice
naming it. A pull request that removes one `env/*` tag from a stack that keeps
another drops that stack / environment pair the same way; the plan comment names
the pair on its own line. Tag the stack `env/<name>` to bring it under shipmate ([`../CONTRACT.md`](../CONTRACT.md) §Tag
grammar).

The same rule drops a stack whose change was applied before merge and then
reverted on the branch. The branch matches the default branch again, so no
cell plans and the gate goes green, while state still holds what was applied.
Closing the pull request leaves state ahead of the default branch until drift
or the next pull request touching the stack shows it. To undo the apply, keep
the stack in the changed set until the undo applies: any edit to the stack's own
files plans the destroy of what the revert removed from configuration, and a
`removed` block forgets it from state instead.

### A dispatched verb produced a run in which every job was skipped

The comment was accepted, the dispatch succeeded and the run page shows five
skipped jobs and nothing else — which is what a healthy run also shows for the
four jobs the event did not select.

The job that serves that verb is not selected by its `if:` in
`.github/workflows/shipmate.yml`, or the file declares a `verb` option its jobs
do not cover. `shipmate doctor`'s `shipmate.yml` probe reports that job on every plan
run — with the expression to write, unless what it found was a job count other
than one — and the run is in the Actions tab, linked from the dispatch reply on
the pull request when that reply carries a link. Reconcile the job against the
fence in [`getting-started.md`](getting-started.md) §The workflow file.

### A cancelled autoplan left red checks on the pull request

A newer plan run of the same pull request cancelled the autoplan, and the
autoplan's checks (`shipmate / detect`, `shipmate / summary`,
`shipmate / gate-hold`, the `shipmate / <stack> / <env>` matrix check) stay red
or cancelled on the head.

After the newer plan run has finished, re-run the cancelled run:
`gh run rerun <id>`. Not before: the re-run re-enters the `plan-<pr>`
concurrency group, whose `cancel-in-progress: true` cancels a newer run still in
flight. When the newer run holds the gate or published apply checks on the
head, the re-run's `summary` writes nothing and ends green at its hold step,
with a notice naming that run ("plan run <id> holds the gate of <sha> and is
newer than this run", or "plan run <id>, newer than this run (<id>), published
apply checks on this head"). A newer run that takes the gate while the re-run
is publishing still fails the re-run's `Create apply checks` or
`Create/refresh gate` step. That red is harmless: the newer run owns the gate.

### Fork pull request refused

`detect` fails with `fork pull requests are not supported`, or with
`this run did not state its head repository`.

The refusal keys on the `head-repo` input, and it refuses by default: a run
that states no head repository is refused too, with a message naming the input.
No input allows a fork; engine `drift.yml`'s `sweep: "true"`, which
says the run has no pull request at all, is the only opt-out
(`docs/drift.md`). [`hardening.md`](hardening.md) §Contributors without push
access states why, and what a fork's plan cell would receive.

Push the branch to this repository (`gh pr checkout`, then push) and open the
pull request there.

### `shipmate applies and unlocks only branches of this repository`

`shipmate apply` or `shipmate unlock` was refused at comment time because the
pull request's head branch is in a fork, or in a repository that was deleted.
Nothing was dispatched. As for a plan, push the branch to this repository
(`gh pr checkout`, then push) and open the pull request there.

### Every engine job fails before its first step

All eight jobs that bind `shipmate-engine` fail before any step runs: `plan.yml`
`summary` and `gate-hold`, `comment-ops.yml` `ops`, `apply.yml` `review` and
`summary`, `apply-env-level.yml` `complete`, `deploy.yml` `summary` and
`drift.yml` `issues`. No `shipmate / gate` status, sticky comment or comment
reply is written.

`shipmate-engine` has an enabled custom deployment protection rule. Every engine
job binds that environment with `deployment: false`, which creates no deployment,
and GitHub fails such a job when the environment has a custom deployment
protection rule. `shipmate doctor` runs inside those jobs, so it cannot report
this; `scripts/onboard --app-id <id> --dry-run` does, with the command that
removes the rule. List the rules with
`gh api repos/<owner>/<repo>/environments/shipmate-engine/deployment_protection_rules`.

### Engine jobs stay queued with no runner

The run starts, but `detect`, the cells or `ops` stay queued and never start,
while the run's `ubuntu-slim` jobs, if it has any, complete. No step of the queued jobs
runs, so nothing reports it.

The `runs_on` label is one the account or the self-hosted runner pool lacks.
The default, `ubuntu-26.04-arm`, is a GitHub-hosted arm64 label. Set the
`&runner` anchor on `shipmate.yml`'s `plan` job, or the drift file's
`runs_on:`, to a label the account has, such as `ubuntu-latest` for x64.
Cancel the queued run and start a new one once the change is on the default
branch: a re-run reads the workflow file of the original run's commit, so it
keeps the old label. [`aws.md`](aws.md) §Runner choice lists the jobs each
label reaches.

### An apply check never completes after a successful apply

The apply itself succeeded, but the `apply / <stack> / <env>` check stays
pending and the trailing completion job failed.

The `shipmate-engine` environment's deployment branch policy names a branch that
does not exist — a typo, or a repository whose default branch is not `main`. A
policy naming a nonexistent branch fails closed: the completion job is denied
`SHIPMATE_APP_PRIVATE_KEY`, so it can mint no App token and can complete no
check. Fix the policy per `github-app.md` §5, which reads each repository's own
default branch rather than hardcoding one. `shipmate doctor` probes both that
the environment exists and that its policy actually names the default branch.

### A deploy refused an apply check on a stack that lost its env tag

The annotation reads:

```text
deploy aborted: apply / <stack> / <env>: the stack of each lost its env/* tag
after this pull request was planned, so its reviewed change was not applied. This deploy
applies nothing, so these other cells of this merge are not applied either:
<other stack> / <env>. Open a new pull request that retags each stranded stack and
changes every other listed stack (any edit that `terramate list --changed` marks, such
as a comment line). It plans the current default branch for all of them, this merged
change included, and its cells are applied before merge under a required gate
(`shipmate apply`, and `shipmate apply <env>` for an `explicit` environment), or by its
deploy otherwise. If an untag was deliberate, apply that stranded
change by hand instead of retagging; the pull request still changes every listed stack,
and with none listed no pull request is needed. Re-running this deploy checks out the
same commit and refuses again. This deploy's apply checks are never read again and stay
open on the merged pull request.
```

The message is one line; it is wrapped here. The not-applied-either sentence is
present only when the merge has other cells to apply. The merged pull request
changed a stack, and another pull request removed that stack's `env/*` tag, or
one of them, and merged first. The merged change reached the default branch,
but no cell of this deploy carries it. The refusal makes the stranded change
visible instead of skipping it with a notice on a path no operator watches, and
because it applies nothing, every other cell of the merge stays unapplied too.
The list names every such cell, with an apply check or without.

1. Open a new pull request that retags each stranded stack and changes every
   other listed stack, for example with a comment line.
2. Review its plan: it plans the current default branch, this merged change
   included.
3. Its cells are applied before merge under a required gate (`shipmate apply`,
   and `shipmate apply <env>` for an `explicit` environment), or by its deploy
   otherwise. Merge it.

If the untag was deliberate ([`../CONTRACT.md`](../CONTRACT.md) §Taking a stack out
of CI), do not retag. Apply the stranded change by hand, then open a pull
request that changes every listed stack; its cells are applied as in step 3.
When the message lists no other cell and no stranded stack needs a retag, no
pull request is needed.

The refused deploy's apply checks stay open on the merged pull request and are
never read again; its `summary` turns the merged head's `shipmate / gate` to
`failure` ("deploy stopped at detect (failure), nothing applied; see the run"). Do
not re-run the refused deploy: it checks out the same commit and refuses again.

The same gate description appears for any `detect` failure after it named the
head. Read the `detect` job's `::error::` annotation. A refusal states its
cause: search this page for its text. A `command failed` annotation names the
command that failed (`gh api`, `terramate`, `git`) and carries its stderr; for
that case only, re-run the deploy once, and if it fails the same way, that
stderr is the cause. When no annotation but `Process completed with exit code`
names the cause, read the failed step's log: a traceback or a failed checkout or
setup step shows there.

A gate reading `deploy gate got no detect result; see the run` means the deploy
workflow passed no `detect` result to its `summary`; the engine's `deploy.yml`
always passes one.

### The post-merge deploy was dropped as superseded

A pull request merged, the `deploy` job for that merge was cancelled before it
started, and its stacks are still pending.

There is no server-side queue behind the apply path: the consumer's `deploy` job
declares a `concurrency` group (`group: deploy-main`), and GitHub drops the older
*pending* job whenever a second merge lands while the first is still queued. The
stacks stay pending and visible, which is the recoverable state: re-run that
deploy. `deploy-detect` rebuilds its work queue from the
apply checks that are still pending, so a re-run is idempotent — anything
already applied is skipped.

A deploy whose `summary` job alone was cancelled, because a later gate writer
on the same head queued behind it, leaves the merged head's gate as it was.
Re-run that deploy the same way, unless one of its cells' job logs shows
`The apply step ran and its state was not saved`: the re-run applies that cell
again. Follow §`The apply step ran and its state was not saved` first.
