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
shipmate:doctor -->`, upserted in place like the plan comment) combining ten
live probes — a missing or mis-pinned `shipmate / gate` rule on the default
branch (no active ruleset requiring it, or one that doesn't pin
`integration_id` to the shipmate App, or that isn't strict),
whether the default branch's `pull_request` rule requires **code-owner** review
(`hardening.md` #3–5 — an approval count alone is not reported as
sufficient, because the shipmate App can submit an approving review, so only a
CODEOWNERS review is out of reach of a leaked App private key, and only for
changed files an entry actually owns — the rule is a no-op for unowned paths, and
the probe reads ruleset booleans only, so it cannot see that;
`required_approving_review_count: 0` *with* code-owner review on is the supported
sole-maintainer mode, reported as a note rather than a warning, while count 0 with
code-owner review off leaves no unforgeable merge-time control at all and warns;
the booleans are unioned across every `pull_request` rule on the branch, since
GitHub enforces the union across layered rulesets),
a missing GitHub
Environment (`<env>-plan` / `<env>-apply`, or a single shared `<env>`) for a
tagged-in environment, the
plan/apply environment protection shape (a plan environment must have no
approval-type protection rules — required reviewers or wait timers — and no
deployment branch policy; an apply environment with no approval rule is only a
note, and "no approval rule" is deliberately not "no protection rules": GitHub
synthesizes a `branch_policy` protection rule for any environment with a
deployment branch policy, and a branch policy is not a review; the role each
environment plays is inferred from the environment **names** — doctor never reads
`SHIPMATE_SHARED_ENVS` — so a shared environment carrying approval rules warns
that they stall the plan cells and the nightly drift run, its missing approval
rules are a note, and its branch policy is a note only while no suffixed sibling
exists: once one does, an unmigrated `plan.yml` may still bind the bare
environment, so the policy is warned about as the plan-stall it can be. An env
with a bare `<env>` *and* a suffixed sibling warns that which naming each path
binds is undetermined, so either naming may be bound by nothing with its
protection rules reading as a control in no code path — and the missing half of
the suffixed pair is still reported), the secrets a
plan environment holds (names only — the API never returns a value; a plan
cell runs branch code with whatever that environment releases and control 8
forbids protecting it, so a note giving the count — exact unless the listing was
too long to read whole — and a capped list of
the names — a crowded environment's later names are not printed, so that one
finding cannot spend the whole report's size budget — and a warning if
`SHIPMATE_APP_PRIVATE_KEY` is one of them), whether the `shipmate-engine`
environment exists and its deployment branch policy actually names the default
branch (see `hardening.md` #16 and `github-app.md` §Key-exposure
boundary — this is the probe that catches a re-pin that never (re-)creates
that environment, which would otherwise leave the App key a repository secret
again with nothing else to notice), any workflow file other than `plan.yml`
declaring the
`pull_request_target` trigger (it runs at the base ref with the repository's
secrets, and a workflow that also acts on content the pull request author
controls from a job naming an environment hands those secrets to a fork —
`hardening.md`; `plan.yml` is exempt by exact name because it uses the
trigger in the one shape that is safe, with the credentialed job checking
nothing out; the probe reads the same workflow files as the pin
probe, at the same commit, so a pull request that removes the trigger is not
still reported for it), engine
action-pin freshness in the consumer's own workflow files (read at the commit
under examination, so the pull request that bumps a stale pin is not itself
reported stale, and restricted to pins of the engine's own repository, which
the probe learns at runtime from the running action rather than from any
hardcoded slug — another org's shared action is not shipmate's to report on),
whether the configured approvers team resolves in the org, and
whether the shipmate App installation still grants the manifest's full
permission set — with the warning and failure annotations GitHub already
recorded on this commit's workflow runs (shipmate's own and any other
Actions workflow run on that commit; third-party-app-authored check runs are
excluded). Only eight of the ten probes can produce a finding from the plan
path's own `annotate`-mode run (`actions/summary`): the approvers-team probe
needs the `SHIPMATE_TEAM` environment variable, which the plan path does
not supply, and
the App-permission-drift probe only has something to report when a
full-manifest permission-set mint was actually attempted, which only
`shipmate doctor` does — both are effectively comment-path-only. `doctor`
degrades to a "could not verify" **warning** naming each probe that was skipped
on an API error, and always exits 0, so a probe failure (for example, the App
token lacking read access to `rules/branches` or `environments` — both token
mints that drive doctor also request Actions read, which the environment reads
need on some configurations) never fails the plan run. One endpoint failure can
name more than one probe: a `rules/branches` failure degrades both the gate-rule
and the review-rule probe, because the two read it independently on purpose, so
neither is silenced by the other's failure. The engine-pin and fork-trigger
probes degrade to a **note** instead — both read `.github/workflows`, and that
read legitimately fails on the pull request that first adds that directory, which
is the first `shipmate doctor` any consumer runs, so the first report a consumer
sees carries two notes. Both also decline rather than guessing when they cannot
tell which commit to read, and the pin probe when it cannot tell which
repository the engine is.
An environment that exists but whose settings cannot be read is likewise a
note naming it, rather than the silence a nonexistent environment gets (that
one is the environment-existence probe's finding) — the `shipmate-engine`
probe degrades the same way. The plan-environment secret probe carries three
degrade levels of its own: with no `environments: read` token it **warns** that
the check was not performed — never that a plan environment is clean; an
environment whose secret listing fails is a **note** naming it, so one
unreadable environment does not silence the ones that could be read; and a
listing too long to read whole **warns** that whether the environment holds
`SHIPMATE_APP_PRIVATE_KEY` could not be determined, rather than reading as a
routine note about the names it did see.

All three environment probes — existence, protection shape, and the
secrets a plan environment holds — cover only the environments of the stacks a
given pull request changed; the declared set comes from that commit's plan
matrix. So the report's all-clear line names the environments it actually probed
instead of implying the repository's environments are all sound, and a clean
secret probe says nothing about an environment this pull request did not touch.
The declared set needs a plan run that **succeeded**, so one failed cell in a
fan-out withholds it and the report says so. **One check is exempt and reported
anyway: the ambiguous-naming warning** (a bare `<env>` beside `<env>-plan` or
`<env>-apply`), which compares environment names against each other and needs no
declared set — it therefore covers every logical environment in the repository,
not only this pull request's. That is deliberate: the moment the warning is most
wanted is mid-migration, between the merge and the delete, when a fan-out is at
its most likely to lose a cell to something unrelated.
Separately, the report states plainly when some of the commit's workflow runs
had not finished yet, and when the warnings harvest itself could not complete
(or may be truncated by GitHub's per-step annotation cap), rather than claiming
a false all-clear.

The harvest is deliberately uncurated: it reports the annotations as GitHub
recorded them, including ones from the third-party actions the engine pins, so a
deprecation warning from a pinned action reads the same as a shipmate warning.
That is intended — a known-noise denylist would eventually swallow a real
warning — so treat an unfamiliar line as upstream's until you have checked.

The line you are most likely to meet is
`Input 'app-id' has been deprecated with message: Use 'client-id' instead.`,
once per token mint on the commit, from `actions/create-github-app-token`. **It
is upstream deprecation noise, not a setting to fix** — nothing in your
repository causes it and nothing you can configure removes it. Do not go looking
for a second App credential or change `SHIPMATE_APP_ID`; that is a dead end.

Engine releases from the one that introduced this note onward do not emit it:
their mints identify the App with `client-id` rather than the deprecated
`app-id` input. Only the input's *name* changed — the value threaded is still
the one numeric App id you set as `SHIPMATE_APP_ID`, because upstream passes
either input through unaltered as the JWT `iss` claim, which GitHub accepts as
the App id or the Client id. No second credential, and that same numeric id
keeps satisfying the gate ruleset's `integration_id` pin and the apply-check
author filter.

Expect the warnings to persist for a while regardless. Adoption is re-pin-only
and staggered, so a repository pinned to an earlier engine SHA keeps emitting
them until it re-pins; and the engine's own reusable workflows pin the composite
actions they call by SHA too, so the deploy and apply paths keep emitting them
until those internal pins are bumped. Seeing the line is not evidence that your
wiring is wrong.

`shipmate doctor` never blocks the gate, and it needs no team membership,
review or reviewed plan, unlike `shipmate apply` — but because it reports this
repository's own settings, the engine limits it to organization members and
repository collaborators (below).

## Who can ask for the report, and who can see it

The report is an inventory of what is *not* configured: that no ruleset
requires `shipmate / gate` on the default branch, that `<env>-apply` has no
approval rule so pre-merge applies to it are unreviewed, which approvers
team is configured and whether it resolves, and whether the App installation is
missing permissions the manifest declares.

**What the engine enforces.** `shipmate doctor` runs only for a commenter
GitHub classifies as `OWNER`, `MEMBER` or `COLLABORATOR` in
`github.event.comment.author_association` — organization members and repository
collaborators. Any other commenter gets a single-line refusal saying exactly
that; no App token is minted and no probe runs. `CONTRIBUTOR` is deliberately
excluded — its only signal is one merged pull request, not a standing
relationship to the repository. The gate fails closed: an association the engine
does not recognize, or an event carrying no comment context at all, counts as no
access. Nothing is required of you to adopt it beyond re-pinning the engine
SHA — it adds no action input and no workflow `permissions:` entry.

**What the engine does not enforce.** It does **not** check write access.
`author_association` is GitHub's own classification of the author's relationship
to the repository, not a permission lookup, and it is wrong in both directions:

- a collaborator invited with only the **Read** role is classified
  `COLLABORATOR`, and an organization member whose base repository permission is
  **None** is classified `MEMBER` — both are admitted to the report despite
  having no write access. If that matters for your repository, add the
  workflow-level layer below, or do not grant read-only collaborator access to
  people who should not see the settings inventory;
- conversely, an organization member whose membership is **private** is reported
  as `NONE` and will be refused unless they are also a direct collaborator; make
  the membership public or add the person as a collaborator.

What the gate does buy is that an account with no declared relationship to the
repository — a drive-by fork author on a public repository — cannot obtain the
report. Beyond that: `shipmate help` stays open to every commenter (it lists the
verbs and discloses nothing about the repository, and a newcomer whose setup is
broken still needs it), and the report is an ordinary pull request comment, so
once someone with access asks for it, everyone who can read the pull request can
read it. The same rendered report is also written to the run's job summary, so
that a GitHub API outage which loses the comment does not discard the probes.
That surface needs repository read access too, so it admits no one the comment
did not — but it is **not** as retractable. The comment is a single sticky one
you can edit or delete, and the next `shipmate doctor` overwrites it; a job
summary cannot be edited or redacted at all, and every past run keeps its own
copy for the repository's Actions retention window (90 days by default). So if a
report disclosed something you did not want recorded, deleting the comment is not
enough — delete the workflow runs that produced it, or shorten the retention
window.

On a repository whose pull requests are **public** you can add a second layer
by gating the `issue_comment` job itself on the same
`github.event.comment.author_association` values, or by keeping the repository
private. That is belt and braces over the engine's own gate, not the primary
mitigation. Note also that `app/manifest.json` declares
`"public": false`: the shipmate App is registered per organization and intended
for repositories the installing organization controls.

## Common failures

### `Saved plan is stale`, or `does not match the reviewed plan's fingerprint`

An apply cell fails at or before `tofu apply` and its
`apply / <stack> / <env>` check stays pending.

Two fail-safes of the exact-plan model produce this, and both mean the reviewed
`.otplan` can no longer be trusted. Either the state moved after the plan was
generated, and OpenTofu itself rejects the stored plan; or `apply-cell`'s
pre-apply check found that the current environment's `TF_VAR_*` set no longer
hashes to the fingerprint recorded with the plan — a variable was added,
removed or changed ([`../CONTRACT.md`](../CONTRACT.md) §Apply-match
fingerprint). That error names the current variable **names** only; it never
prints a value.

The fix is a re-plan — push, or re-run the plan workflow — and an apply of the
fresh plan. There is no force: an apply that silently re-planned would apply
something nobody reviewed, and the check left pending is exactly what stops the
gate greening over an unapplied stack.

**A retry is not a re-plan, and the pending check makes the retry look
available.** A failed apply leaves its `apply / <stack> / <env>` check *pending*
rather than failed, so `snapshot` accepts a fresh `shipmate apply <env>` for that
cell — the right recovery when nothing moved (a credentials failure, a rate limit,
a cancelled run). If the apply **partially** succeeded, the state serial advanced
and the stored plan no longer matches it, so that retry is refused here instead.
Re-plan first; the two recoveries are not interchangeable.

A cancelled run has one further failure mode a bare retry cannot clear: it can
die holding the state lock, and every later apply of that cell then fails
acquiring it. See "A state lock is held", below.

**No supported route aims an apply at a superseded plan**, which is why this error
normally means the state moved rather than that the wrong plan was chosen.
both dispatched apply workflows refuse in their `guard` job any dispatch whose
actor is not a `[bot]` — a hand-run one fails with `apply must be dispatched by
the shipmate App via comment-ops, not by a direct workflow_dispatch` — and
comment-ops
resolves the newest successful plan run for the pull request's **current** head,
refusing the command when that head has none. These fail-safes are defence in
depth behind that control, not the only thing behind it.

**If the mismatch names *every* `TF_VAR_*` and it started right after an
environment-naming or `SHIPMATE_SHARED_ENVS` change, the cause is not the plan.**
The apply job bound an environment that does not exist, GitHub auto-created it
empty, and no variables reached the cell — which is the loud failure the naming
is designed to produce on any layout whose environment injects a non-empty
`TF_VAR_*` or `TF_WORKSPACE`. (A
folder-per-env layout injects none, hashes the empty set on both sides and so
never reaches this error at all; see [`../CONTRACT.md`](../CONTRACT.md) §Env
model for what that layout gives up instead.) In practice the pre-flight in the
next section refuses such a run before any wave starts, whatever the layout
injects, so reaching *this* error from a naming change usually means either the
environment went missing after the pre-flight passed, or it exists because an
earlier mis-set run auto-created it empty — an environment that exists satisfies
the pre-flight and still injects nothing. Two ways to arrive there:

- **The mode disagrees with the names.** Split naming (`<env>-plan` +
  `<env>-apply`) with the env listed in `SHIPMATE_SHARED_ENVS`, or a single bare
  `<env>` with it not listed. Check the variable against the environments that
  actually exist — the failing job's own page names the environment it bound, and
  an empty one that nobody created is the tell.
- **Spaces after the commas in `SHIPMATE_SHARED_ENVS`.** `dev-eu, dev-us` matches
  `dev-eu` only: the second entry is ` dev-us` and the comparison is exact, so
  that env silently stays split. Same symptom, different cause.

### `plan-cell needs the expected-head input`, or `The plan would describe a tree nobody reviewed`

A plan cell fails on its very first step, before `terramate` runs, so the cell
produces no plan and its `<stack> / <env>` check does not go green. The failing
plan job leaves `shipmate / gate` held **red** with `plan incomplete (plan job:
failure)` — a hold, not an absence (§`shipmate / gate` never goes green, "The
gate is deliberately held") — so nothing merges until the plan cells pass.

Both are wiring errors in your `.github/workflows/plan.yml`, and each names its
own fix:

- **`expected-head` is missing or empty.** `plan-cell` requires it — the commit
  the run is planning — and refuses rather than publishing a plan whose
  provenance nobody can verify. Add
  `expected-head: ${{ github.event.pull_request.head.sha }}` to the `plan-cell`
  step ([`getting-started.md`](getting-started.md) §Required — plan). This is the
  first thing a repository meets after re-pinning to the release that introduced
  the input ([`upgrading.md`](upgrading.md) §Unreleased).
- **The commit checked out is not the commit the run says it is planning.** The
  plan path is `pull_request_target`, which checks out the **base** branch by
  default, so `detect` and `plan` must name
  `ref: ${{ github.event.pull_request.head.sha }}` on their checkout. Without it
  the cell plans the base and would report a clean plan for a pull request it
  never read; that is now a refusal instead. Fix the **checkout** — passing the
  base SHA as `expected-head` to make the comparison agree is the one wrong
  reading of this error, and it restores exactly the hazard the check exists to
  close.

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
- **There is no record at all.** The plan predates the release that binds a plan
  to the tree it was produced from, so there is nothing to compare and the
  absent record is refused rather than tolerated. **A push does not always fix
  this one.** Pre-merge it does: push to the pull request (or re-run the plan
  workflow) and the fresh plan carries a record. But the post-merge deploy path
  can meet an old-format artifact for a cell that was still pending when the
  re-pin merged, and there is no pull request left to push to — the remedy there
  is a follow-up pull request touching those stacks. The way to avoid meeting it
  at all is to land the re-pin with nothing pending
  ([`upgrading.md`](upgrading.md) §Unreleased).

This check is per cell and additive: the apply path's existing run-level
verification of the plan run's head is unchanged, and a repository sees this
error only for a plan the run-level check accepted.

### `this apply would bind GitHub Environment(s) that do not exist`

The apply run stopped in its `snapshot` job, before any wave, and the error names
every environment the applies would have bound that the repository does not have.

Why it refuses instead of warning: GitHub creates a missing environment **on
demand**, with no reviewers, no wait timer and no deployment branch policy, and
the apply then runs inside it. That environment is the only control over an apply
on a layout injecting no variables — the apply-match fingerprint compares
variable *content*, and an auto-created environment is byte-identical to a
legitimately empty shared one — so existence, checked before the waves, is the
only thing that can tell them apart.

Two fixes, and the error names both because either can be the right one:

- **Create each environment named.** The apply path binds `<env>-apply` for a
  split env and the bare `<env>` for one listed in the `SHIPMATE_SHARED_ENVS`
  repository variable. Existence is matched **case-exactly** against the stack's
  env tag, while the `SHIPMATE_SHARED_ENVS` listing is case-insensitive — so an
  environment differing from the named binding only in case does not satisfy the
  pre-flight, and GitHub may reject a second one as a duplicate. Rename the
  existing environment (or the tag) to match rather than creating another.
- **Correct `SHIPMATE_SHARED_ENVS`.** If the environments you created are the
  ones you meant, the variable is what disagrees with them: an entry for an env
  that is really split, a missing entry for one that is really shared, a typo, or
  a space after a comma (`dev-eu, dev-us` leaves ` dev-us` as the entry, and
  entries are compared whole). `shipmate doctor` on a pull request reports what
  the environment names imply per env, which is the fastest way to see which side
  is wrong.

Two neighbouring failures from the same step, both also fail-closed:

- **`could not list this repository's GitHub Environments`.** The check did not
  happen, so the applies are refused rather than permitted. Check first that the
  job calling `apply-env-level.yml` grants **`actions: read`**: permissions cap at
  each `uses:` boundary, and on a **private** repository listing environments 403s
  with `checks: read` alone. Every apply route shipped here grants it already, so
  a persistent 403 points at a hand-written caller. A 5xx or a rate limit clears
  on a re-run of the workflow.
- **`the environments listing is truncated`.** One page did not cover the
  repository's environments, so a missing environment cannot be told from an
  unread one. Reduce the number of environments, or re-run — this is a hard
  ceiling around 100 environments, not a transient.

What this does **not** cover, deliberately: the plan-side binding (that is in
your own `plan.yml` / `drift.yml`, which the engine cannot read), an environment
that exists but is empty or mis-scoped (the fingerprint and `shipmate doctor`
cover content), and an environment deleted between the pre-flight and the wave
that binds it.

### A state lock is held

Every apply of the cell fails acquiring the lock, and the apply result comment
carries a 🔒 line under its table naming each such cell, the held lock's id, the
command that releases it, and — when the backend reported a usable timestamp —
when it was taken. Per-cell concurrency admits one apply at a time, so the
holder was that cell's own most recent apply run — one that was cancelled or
killed before it could release.

Comment `shipmate unlock <env>`. The environment is required; there is no bare
form. It authorizes on approvers-team membership and the `<env>-apply`
environment, not on a review — so a lock stranded after the pull request merged
is still releasable ([`../CONTRACT.md`](../CONTRACT.md) §Comment-ops has the
contract). Every cell in that environment with a pending
`apply / <stack> / <env>` check is probed; each reports in its own job, and a
cell that cannot determine its lock state fails red rather than reporting the
lock absent.

Then re-apply. **Unlocking is not recovery**: a cancelled apply usually advanced
the state, so `shipmate apply <env>` may now be refused by the exact-plan
fail-safe above — re-plan and apply the fresh plan. The lock is released either
way; the plan is a separate question.

Two locks this verb does not reach:

- **A cell with no pending apply check** — a stack applied out of band, or one
  whose check already completed. It is not in the queue, so release it the way
  it was locked: `tofu force-unlock <id>` in that stack's directory, with the
  backend's credentials, having confirmed the id against the error the next
  apply prints.
- **A lock a human is holding right now**, running OpenTofu by hand against the
  same state. That is the lock unlock must not break, and it is why nothing here
  waits on a lock or releases one it did not probe: check with whoever is
  running it before forcing anything.

### `shipmate / gate` never goes green

Three distinct causes, in the order worth checking.

**A pending apply check nothing will complete.** `gate-refresh` greens the gate
only when every shipmate-App-authored check on that commit whose name begins
`apply / ` has a latest run completed as `success` or `neutral`, and the apply
paths complete only the names the current plan run produced. A leftover pending
check under a name no current cell reconstructs — an earlier engine revision's
check-name grammar, a renamed or deleted stack — therefore holds the gate
indefinitely. GitHub has no way to delete a check run, so the recovery is a new
head SHA: push a commit, and the plan run re-creates only the checks that exist
now.

**The gate is deliberately held.** A gate whose state is `failure` with a
description telling you what to re-run is not "no changes"; it is a hold.
`gate-state` compares the cells `detect` planned against the cell summaries the
trusted summary job actually read, and writes a hold when they disagree in
either direction, when the plan job failed or was skipped with cells planned,
when `detect` did not succeed, or when the planned count was not reported at
all. A run whose cell count is zero for any of those reasons must not claim
nothing changed: `deploy-detect`'s work queue *is* the pending apply checks, so
a green gate over cells nobody accounted for merges reviewed stacks that then
never apply. Follow the description — for an evidence shortfall it says to
re-run *this summary job* and not the plan jobs, whose artifact uploads are not
`overwrite:` and 409 on a re-run. A hold clears only from a fresh plan run;
`gate-refresh` refuses to green a held gate from apply checks alone.

**The required check does not match the status.** The ruleset pins
`integration_id` to the shipmate App id, so a `shipmate / gate` status of the
right name authored by any other identity leaves the pull request blocked — as
does a pin to the wrong App id. See `branch-protection.md` §Reproducible
ruleset.

### An environment was held for review, or the apply was refused

The apply comment says *"Held — the pull request's review state does not
permit applying"*, or `shipmate apply` was refused with a review reason.

`SHIPMATE_UNGATED_ENVS` exempts the environments it names from the review
requirement and nothing else. A **targeted** `shipmate apply <env>` is decided
at comment time: an unlisted env gets the usual refusal, extended to say the
env is not listed in the variable, and the engine re-applies the same rule to
the decision it reads at apply time — a run refused there dies before any wave,
leaving the apply checks pending. A **bare** `shipmate apply` is partitioned
per environment on the apply path, from the review decision read there:

| `reviewDecision` when the apply runs | what applies |
|---|---|
| `NONE` (the ruleset requires no review) or `APPROVED` | everything pending — no partition |
| `REVIEW_REQUIRED` | the listed environments; every other pending environment is held — all of them when the variable is unset or empty |
| `CHANGES_REQUESTED` | nothing — every environment is held, listed ones included |
| anything else, or no decision arrived | nothing — every environment is held |

Held environments keep their `apply / <stack> / <env>` checks pending, so
`shipmate / gate` stays pending and the merge stays blocked; environments
ordered after a held one are skipped for the same run. The variable only ever
narrows what a `REVIEW_REQUIRED` decision holds; it is the decision that
decides, so an unreviewed pull request holds every environment in a repository
that set nothing.

**The apply is refused although the variable is set.** The `comment-ops` step
needs `ungated-envs: ${{ vars.SHIPMATE_UNGATED_ENVS }}` too; without it
comment-ops sees an empty list and refuses both forms. See
[`upgrading.md`](upgrading.md) §"Opt-in: per-environment review gating".

**The run failed with a `SHIPMATE_UNGATED_ENVS` error.** An entry that is not a
bare env name is rejected loudly rather than left silently inert, because none
of these would ever match an environment:

- a `-plan` / `-apply` suffix — the list is matched against the **bare logical**
  env name carried by the apply checks, so write `dev-eu`, not `dev-eu-apply`;
- surrounding whitespace — `dev-eu, dev-us` is an entry `" dev-us"`. The list is
  comma-separated with **no spaces**; the error names the entry and the exact
  value to write;
- anything outside the env charset (letters, digits, `-`, `_`) — a pasted
  `"dev-eu"` with its quotes, an internal space, a `/`. The variable holds the
  bare list, unquoted.

**`shipmate apply` answers with nothing at all — no reaction, no comment.** A
malformed entry is rejected inside the Authorize step, which fails the run
before either the 🚀 reaction or the refusal comment is reached. So a
repository-wide breakage of `shipmate apply` (every command, every environment,
however well-formed) is invisible on the pull request itself. The real error is
the
`::error::SHIPMATE_UNGATED_ENVS entry ...` annotation on the comment-ops
workflow run; open that run from the Actions tab and fix the variable. A
targeted apply that was genuinely refused always comments its reason, so
silence points at the variable rather than at the authorization.

**A held environment is also an explicit environment.** When both causes apply
it is reported as held, not as excluded, because the review is the thing to get
first. Getting it does not release the environment into a bare `shipmate
apply`, though: it is still listed in `global.shipmate.explicit_envs`, so it
still needs a targeted `shipmate apply <env>`. That is why the held sentence
names no command.

**A listed environment that is also explicit is not held.** It is reported as
excluded, with the usual "run `shipmate apply <env>`" — and that targeted apply
then succeeds **without** an approving review, because the environment is
listed. Listing an environment and marking it explicit are independent: the
first decides whether a review is required, the second only decides that a bare
apply will not reach it. An environment that must never apply unreviewed does
not belong in `SHIPMATE_UNGATED_ENVS`.

### A pull request planned zero cells

No `<stack> / <env>` checks appear, no plan comment is posted — unless there is
already a plan comment to keep current, or `doctor` raised a warning on that
run, either of which still posts one — and the gate goes green over no work.

Change detection is `terramate list --changed`, so a pull request that touches
no stack's own files and changes no generated `.tf` — an engine-pin bump, a docs
edit — plans nothing. This is expected, not a fault.

### Fork pull request refused

`detect` fails with `fork pull requests are not supported`.

Planning a fork head would run the pull request's own Terramate/OpenTofu code on
your runners with whatever the plan environment exposes as **variables** — those
are not secrets, and they are not withheld from a fork's run. No
`shipmate / gate` status is ever written for a fork head either, so the pull
request could not merge whatever the plan said. The refusal is loud rather than
an empty matrix, so an outside contributor is not left waiting on a gate that
cannot arrive, and it keys on the event being a pull request while treating an
undeterminable head repository as a fork.

There is no input that allows it. Push the branch to this repository
(`gh pr checkout`, then push) and open the pull request there.

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

### The post-merge deploy was dropped as superseded

A pull request merged, the deploy run for that merge was cancelled before it
started, and its stacks are still pending.

There is no server-side queue behind the apply path: the consumer `deploy.yml`
declares a run-level `concurrency` group (`group: deploy-main`), and GitHub
drops the older *pending* run whenever a second merge lands while the first is
still queued. The stacks stay pending and visible, which is the recoverable
state: re-run that deploy. `deploy-detect` rebuilds its work queue from the
apply checks that are still pending, so a re-run is idempotent — anything
already applied is skipped.
