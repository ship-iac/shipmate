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

**If the mismatch names *every* `TF_VAR_*` and it started right after an
environment-naming or `SHIPMATE_SHARED_ENVS` change, the cause is not the plan.**
The apply job bound an environment that does not exist, GitHub auto-created it
empty, and no variables reached the cell — which is the loud failure the naming
is designed to produce on any layout whose environment injects a non-empty
`TF_VAR_*` or `TF_WORKSPACE`. (A
folder-per-env layout injects none, hashes the empty set on both sides and so
never reaches this error at all; see [`../CONTRACT.md`](../CONTRACT.md) §Env
model for what that layout gives up instead.) Two ways to arrive there:

- **The mode disagrees with the names.** Split naming (`<env>-plan` +
  `<env>-apply`) with the env listed in `SHIPMATE_SHARED_ENVS`, or a single bare
  `<env>` with it not listed. Check the variable against the environments that
  actually exist — the failing job's own page names the environment it bound, and
  an empty one that nobody created is the tell.
- **Spaces after the commas in `SHIPMATE_SHARED_ENVS`.** `dev-eu, dev-us` matches
  `dev-eu` only: the second entry is ` dev-us` and the comparison is exact, so
  that env silently stays split. Same symptom, different cause.

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
