# Upgrading

How a consuming repository moves to a newer shipmate, and what past releases
needed beyond a pin bump.

## Re-pinning

**Every engine reference moves in one change.** A repository that bumps some
pins and leaves others behind is running two engine versions against one
contract — the actions, the reusable workflow calls, and any pin inside a
composite action you wrap all name the same SHA, or they disagree.
[`../CONTRACT.md`](../CONTRACT.md) §Consumption is the rule;
[`releasing.md`](releasing.md) is the maintainer side of the same cascade.

Consumers pin by **commit SHA**, never by tag or branch name, optionally with a
trailing `# vX.Y.Z` comment naming the release that SHA belongs to
(`uses: <owner>/shipmate/actions/state@<sha> # v0.1.0`). The comment is for
human readers and for Dependabot's bookkeeping; the ref that resolves is always
the SHA. The SHA of record for a release is named in that release's section of
[`../CHANGELOG.md`](../CHANGELOG.md).

**Resolving a tag to its commit takes the dereferencing call.** Releases from
`v0.14.2` on are *annotated* tags, so `git/ref/tags/<tag>` returns the **tag
object's** SHA — 40 hex characters from a 200 response, and not a ref a workflow
can check out. Nothing a consumer sees separates the two values, so a pin at the
tag object fails at ref resolution on every later run, in a repository whose
change said "no migration required". This form dereferences, in one call, for
annotated and lightweight tags alike:

```console
$ gh api repos/<owner>/shipmate/commits/v0.14.2 --jq .sha
741118f8bd7dae55ebce4c980c6ed4f3c7579a99
```

Locally, `git rev-parse v0.14.2^{commit}` — the `^{commit}` is the same
dereference.

## Dependabot

shipmate publishes a GitHub Release per release SHA, so a consumer with
Dependabot's `github-actions` ecosystem enabled receives a pull request bumping
its shipmate pins to the new release's SHA. Dependabot proposes and the
consumer's own `CODEOWNERS` review disposes; what lands is still a full commit
SHA.

This works from a pin that is itself a released commit. From a pin at an
untagged commit Dependabot has nothing to compare against and stays silent —
the signal is then `shipmate doctor`'s pin-freshness probe, which reports a pin
that differs from the latest release, plus the annotation it emits on every plan
run. See [`troubleshooting.md`](troubleshooting.md) §shipmate doctor.

## Migrating from another TACO

A repository that already works under another Terraform automation tool arrives
with four things expressed somewhere shipmate does not read. None of them is a
shipmate defect and none of them announces itself, so each is worth a deliberate
pass before the first plan run.

**Ordering.** Wave ordering comes **only** from the Terramate `after` DAG. If
your ordering lives in the outgoing tool's configuration, it must be ported into
`after` — and **nothing will report the omission**, because a missing edge is
indistinguishable from a stack that is genuinely independent. Treat the outgoing
tool's config as a *lower bound* on the real graph, not as the graph: one
migration that audited the OpenTofu code instead of porting the config went from
6 declared edges to 105, and from 2 wave levels to 6.

Two detect jobs report the shape as a `::notice::` line — stack count, `after`
edge count, wave levels, and how many stacks would apply concurrently. A reader
who knows the repository can judge that last number immediately; nobody else
can. Exactly two print it: the detect job of a dispatched `shipmate apply <env>`
and the post-merge deploy's detect, so it arrives **after** the pull request
that would have been the place to fix the graph. A **plan** run does not print
it, and neither does a bare `shipmate apply` — seeing no such line there says
nothing about the graph. Before that point the equivalent is
`terramate experimental run-graph --label stack.dir` run locally.

**Tags.** Environment membership is derived from `env/<name>` tags and nothing
else. Terramate tags are otherwise free-form, so a repository that predates
shipmate is likely using them for something unrelated. See
[`getting-started.md`](getting-started.md) §Before you start.

**A named AWS profile in generated HCL.** The apply path holds only the OIDC
session, so a literal `profile` in a `provider` or `backend` block fails there
while still planning fine locally. See [`aws.md`](aws.md).

**`terramate.config.run.env` rewriting `TF_VAR_*`.** Terramate applies `run.env`
after the ambient environment, so an assignment to `TF_VAR_env`, `TF_VAR_region`
or `TF_WORKSPACE` wins over what the GitHub Environment injected — invisibly,
because the fingerprint is computed outside `terramate run` and so agrees on
both sides. `detect` injects a sentinel into those three variables and fails the
run when one comes back changed. [`../CONTRACT.md`](../CONTRACT.md) §Env model
has the rule and the `tm_try` form that keeps a local default.

## Opt-in: per-environment review gating

`SHIPMATE_UNGATED_ENVS` lets named environments be applied without an approving
review while the rest keep the branch ruleset's requirement. The exemption is
opt-in and **re-pinning exempts nothing on its own** (it does change two other
things for every consumer — see below):

1. set the `SHIPMATE_UNGATED_ENVS` repository variable,
2. add `ungated-envs: ${{ vars.SHIPMATE_UNGATED_ENVS }}` to the `comment-ops`
   step in your `comment-ops.yml`, and
3. re-pin **both** engine references in your `apply.yml` — the targeted job's
   `.github/workflows/apply.yml@` and the bare job's
   `.github/workflows/apply-all.yml@` — to this release too, not only
   `comment-ops.yml`. The files carry separate pins, and an apply is authorized
   in `comment-ops.yml` but enforced in the engine: bump only the first and an
   unreviewed apply is dispatched into an engine that enforces nothing —
   **every** pending environment applies through a stale `apply-all.yml@`, and
   the named environment applies through a stale `apply.yml@`. This is
   §Re-pinning's one-change rule; on this feature breaking it fails **open**
   rather than loudly. The two are not equally likely: the bare-apply edge
   needs the full opt-in aligned, while the targeted edge is also reached by
   the step-2 literal mis-wiring below on its own, no opt-in needed — see
   §"Applying chosen environments without an approving review" in
   [`getting-started.md`](getting-started.md) for that ranking in full.

With the variable unset, every environment keeps its review requirement, so
*what applies* is unchanged. Three things do change for everyone, opted in or
not, because both apply workflows now re-read the review decision themselves in
an unconditional `review` job:

- **One extra `shipmate-engine` deployment record per apply run.** The
  workflow's `summary` job already creates one; this is the second. If your
  repository has put required reviewers on the `shipmate-engine` environment,
  that approval is now requested at the **start** of an apply run rather than
  at its end — no shipmate doc recommends that configuration, but the timing
  change is real.
- **A broken App-key wiring is now a loud early failure.** The `review` job
  mints an App token, and it runs before anything applies — so a repository
  whose `SHIPMATE_APP_PRIVATE_KEY` or `SHIPMATE_APP_ID` never reaches the
  engine now fails the run at the start, with the three-cause diagnosis, rather
  than applying and then failing to complete its apply checks. If your applies
  have been completing with stranded checks, this is where you will see why.
- **A pre-existing TOCTOU is closed.** Until now only comment-ops read the
  review decision, so an approval dismissed between the comment and the
  dispatch applied anyway. The engine re-reads it at apply time and holds.

With the variable set but the workflow line left out, `shipmate apply` refuses
exactly as it does today — comment-ops receives an empty list, so both the
targeted and the bare form are refused. That is the expected failure mode of a
half-finished opt-in, and it is the one worth recognizing: the refusal is not a
bug. Writing it as a **literal** list rather than the variable reference in
step 2 now costs a wasted run rather than an unreviewed apply — the engine reads
the variable itself on both paths and refuses what the variable does not exempt.
See [`getting-started.md`](getting-started.md) §"Applying chosen environments
without an approving review".

Two things it does not change, worth confirming against your own policy before
you set it: an environment's `required_reviewers` still gates the deployment
(a separate control — see [`hardening.md`](hardening.md) §3–5), and the
variable is editable by anyone holding the **Write** role.

## Past migrations

An entry applies only if you are moving *from* a pin older than the release it
names. The entries below `0.2.0` predate the first tagged release, or
`CHANGELOG.md` does not pin one; they are kept for repositories moving from a
very old pin.

### Unreleased — name the planned commit, and drain pending applies before re-pinning

**Re-pinning alone is not enough.** `plan-cell` now takes a **required**
`expected-head` input — the commit the run is planning — so add it to the
`plan-cell` step of your `.github/workflows/plan.yml` in the same change that
moves your pins:

```yaml
        with:
          expected-head: ${{ github.event.pull_request.head.sha }}
```

The same SHA the `plan` job's checkout already names.
[`getting-started.md`](getting-started.md) §Required — plan has the whole step.

**The pin bump and this edit must land in the same commit.** A wrapper that
re-pins without it has **every** plan cell refuse on its next pull request,
naming the missing input, and the gate holds red (`plan incomplete`) so nothing
merges. That is not fix-forward: the re-pin's own pull request planned green
because `pull_request_target` runs the **base** branch's `plan.yml`, still at the
old pin — so once it merges, the default branch carries the new pin without the
input, and the pull request that would *add* the input is itself planned by that
broken base wrapper. Recovery then needs a ruleset bypass or temporarily
un-requiring `shipmate / gate`. Landing both in one commit avoids the whole
sequence; it is the one failure here that no later pull request can clear on
its own.

**Drain pending applies before the re-pin merges.** A reviewed plan produced
before this release carries no recorded commit, and an apply refuses such a plan
rather than tolerating it — there is nothing to compare it against. Pre-merge
that costs a re-plan: push to the pull request, or re-run the plan workflow, and
apply the fresh plan. **The post-merge deploy path is the one a push cannot
fix.** A cell whose `apply / <stack> / <env>` check was still pending when the
re-pin merged holds an old-format plan, its pull request is already merged, and
there is no pull request left to push to — the remedy there is a follow-up pull
request touching those stacks, which plans them afresh and applies on its own
merge. The refusal is correct in both cases; the way to avoid meeting it at all
is to land the re-pin with nothing pending.

**A wrapper that does not name the head SHA on its checkout now fails loudly.**
`plan-cell` refuses when the commit it has checked out is not the one the run
says it is planning. Since `0.10.0` the plan path is `pull_request_target`,
which checks out the **base** branch unless the checkout names
`ref: ${{ github.event.pull_request.head.sha }}` — a wrapper missing that line
used to report a clean plan for a pull request it never read, and is refused
from this release on. Fix the checkout, not the `expected-head` value.

Nothing else in this release needs consumer action: no environment to create or
rename, no permission to add, and no change to the apply or comment-ops
wrappers. The run-level head check the apply path already performed is
unchanged — the new per-cell check is additive.

### 0.16.1 — make every `apply.yml` wrapper input optional before using `unlock`

**Re-pinning alone is not enough if you use `shipmate unlock`.** In your
`.github/workflows/apply.yml`, declare every `workflow_dispatch` input
`required: false` with an explicit default:

```yaml
      ref:
        description: PR head SHA to apply
        required: false
        default: ''
      pr_number:
        description: PR number
        required: false
        default: ''
      plan_run_id:
        description: Plan run id with the reviewed plans. Empty for `shipmate unlock`, which applies no plan
        required: false
        default: ''
```

`plan_run_id` is the one that breaks: `shipmate unlock` applies no plan, so the
engine dispatches it with an empty run id, and **GitHub reads an empty value for
a `required: true` `workflow_dispatch` input as "not provided"** — HTTP 422
before the workflow starts, naming an input you never typed. Every unlock
dispatch fails that way until this edit lands. `ref` and `pr_number` are the same
class and are changed here for the same reason: the wrapper is dispatched only by
the engine, from a body the engine builds, so `required` protects no caller and
`apply-detect` is what validates these values. Nothing breaks if you leave those
two required today.

Nothing else in `0.16.1`/`0.16.2` needs consumer action beyond re-pinning — both
are engine-side fixes (an action manifest GitHub could not parse, and a lock
parser blind to the ANSI colour OpenTofu emits on a runner).

### 0.14.0 — check `explicit_envs` for suffixed entries, then re-pin

**Re-pinning is enough, with one exception.** Grep your Terramate globals for
`explicit_envs` and remove any `-plan` / `-apply` suffix from its entries before
you bump the pin. The value is matched against the bare logical env name on the
apply checks, so a suffixed entry used to validate and exclude nothing — the env
you meant to hold back from a bare `shipmate apply` was applied by it. It is now
a loud failure naming the entry and the bare name to write instead. Every
documented environment name carries a suffix since `0.13.0`, which is what makes
this mistake likely; the fix is a one-line edit, and it can ship before the pin
bump.

**One edge worth knowing:** `shipmate doctor` no longer infers an environment
naming mode from a partial listing. A repository with more than 100 GitHub
Environments now gets doctor's degrade warnings for the environment probes where
it previously got environment findings — nothing else about the run changes.

Nothing else in `0.14.0` needs consumer action: no workflow edit, no environment
to create, rename or delete, and no permission to add. The new pre-flight that
refuses an apply binding a non-existent environment needs `actions: read` on the
`snapshot` job, which the documented `apply.yml`, `apply-all.yml` and
`deploy.yml` wrappers already grant — check it only if you wrote your wrappers
by hand.

### 0.13.0 — every environment is renamed, and re-pinning alone is not enough

Plan and apply now bind `<env>-plan` and `<env>-apply`; the bare `<env>` means
one environment shared between both paths. There is no compatibility shim: the
old naming (`<env>` for plan, `<env>-apply` for apply) resolves to environments
that no longer play those roles, so every logical env is migrated by hand, per
env, in the same change that moves your pins.

**Which step moves the binding is not the same in the two modes, and it is the
easiest thing on this page to get backwards.** In **split** mode
`SHIPMATE_SHARED_ENVS` does nothing and the workflow edit flips the binding; in
**shared** mode the variable flips the binding and the delete is only cleanup. So
an all-split repository never sets the variable at all and binds a static
`${{ matrix.environment }}-plan`; read the two ordered lists below with that
straight, and the mixed case after them.

**Create the environments first, then edit the workflows.** Doing it the other
way round means plan cells bind an environment that does not exist yet, GitHub
auto-creates it empty, and the plan that follows is **quiet either way** — with a
`TF_VAR_env` fallback default it plans a name nobody chose, and without one it
plans an *empty* environment name, which is a different state address rather than
a failure. Measured on Terramate 0.17.1 / OpenTofu 1.12.4: `${{ vars.X }}` for a
variable the environment does not hold still sets the `env:` key, to the empty
string; a `run.env` chain of the form
`tm_try(env.TF_VAR_env, env.env, "dev")` passes that empty string through rather
than falling back; and `TF_VAR_env=` satisfies a `variable "env"` that declares no
default. **Do not count on a missing default to make this loud.**

Split mode (the default, keeps the reviewer gate):

1. **If the plan side assumes a cloud role through OIDC, widen that role's trust
   policy before you create the environment.** The environment claim is *inside*
   the `sub` condition ([`aws.md`](aws.md) §GitHub OIDC), so a role conditioned on
   `…:environment:<env>` stops matching the moment plan cells bind `<env>-plan`,
   and the only symptom is a bare `AccessDenied — Not authorized to perform
   sts:AssumeRoleWithWebIdentity` with nothing wrong-looking in the policy, the
   provider or the workflow. **Add** `…:environment:<env>-plan` alongside the
   existing subject — the condition value takes a list — rather than replacing it:
   until step 3's edit is on the default branch, plan runs still bind the bare
   environment and a single-value rewrite breaks every one of them. Write the
   subject in the immutable form your own OIDC logs show, not the documented shape
   (same section).
2. Create `<env>-plan` and copy the bare `<env>`'s **variables** to it
   (`TF_VAR_env`, `TF_VAR_region` / `TF_WORKSPACE`, any plan-side `AWS_ROLE_ARN` /
   `AWS_REGION`). Keep the plan environment policy-free: no reviewers, no
   deployment branch policy. Environment **secrets** cannot be copied — no API
   returns a secret's value — so re-enter each one by hand; a plan-side secret
   left behind (`SHIPMATE_PLAN_PASSPHRASE` is the one to watch) resolves to empty
   and every later apply fails its plan-decrypt fail-safe.
3. Change `environment:` to `${{ matrix.environment }}-plan` in the `plan` job of
   `plan.yml` **and** the `drift` job of `drift.yml`. Nothing else moves:
   `matrix.environment`, check names, tags, `explicit_envs` and artifact names
   all stay the bare logical name. **Verify it in both**, and do not wait for the
   night's drift run to do it for you: a green plan run says nothing about a file
   the engine cannot read, and `drift.yml` is where a mis-set binding surfaces
   last. Dispatch a drift run after the merge and check that the `<env>-plan`
   environments each pick up a fresh deployment record from it.
4. **Merge that change**, then delete the bare `<env>`. `plan.yml` runs on
   `pull_request_target`, so until the edit is on the default branch every plan
   run — the migration pull request's own included — uses the **base** copy and
   still binds the bare environment. Deleting it before the merge means the next
   plan re-creates it empty and plans with no variables, which is the hazard
   above. Between the merge and the delete, doctor warns that the naming is
   ambiguous; that warning is the migration's own to-do list.
5. Drop `…:environment:<env>` from the plan role's trust policy once the bare
   environment is deleted and no run can present it any more. Left in place, the
   old subject stays presentable to the role, and GitHub auto-creates an
   environment the moment anything binds that name — a stale subject is a
   standing way back in, and nothing in the pipeline probes IAM to notice it.
6. `<env>-apply` is unchanged, its apply role included — the name it binds and the
   claim it presents both stay as they were.

Shared mode (one environment, opt in per env):

1. **Copy `<env>-apply`'s variables and secrets onto the bare `<env>` first.**
   Both are read from whichever environment the wave binds, and step 3 flips that
   binding — not the delete in step 4. Variables copy; environment **secrets**
   cannot (no API returns a secret's value), so re-enter each one by hand and
   confirm the bare environment holds every name `<env>-apply` did. A secret that
   exists only on `<env>-apply` is unrecoverable after the delete.
2. **If applies assume a cloud role through OIDC, add the bare subject to that
   role's trust policy** — `…:environment:<env>` alongside the existing
   `…:environment:<env>-apply`, in the immutable form your own OIDC logs show
   ([`aws.md`](aws.md) §GitHub OIDC). The environment claim is inside the `sub`
   condition, so without this the role stops matching the moment step 3 binds the
   bare environment, and the failure is the same undiagnosable `AccessDenied` that
   section describes. The shared environment holds **one** `AWS_ROLE_ARN` and the
   wave jobs read it, so it must name the apply role; plan cells running branch
   code then assume that role too, which is the cost priced in
   [`hardening.md`](hardening.md) §7–9. **If your `plan.yml` carries its own
   credentials step, make this edit before step 1**: that copy already repoints the
   bare environment at the apply role while plan cells still bind that environment,
   so the `AccessDenied` arrives at step 1, not step 3. Widening early grants
   nothing — no run presents the bare subject to the apply role until then.
3. Add the logical env name to the `SHIPMATE_SHARED_ENVS` repository variable:
   comma-separated, **no spaces after the commas** (` dev-us` is not `dev-us`,
   and that env silently stays split). This is the step that moves the binding.
4. Keep the bare `<env>`, and delete `<env>-apply` once nothing binds it — left in
   place it makes the naming ambiguous, and doctor warns for exactly that.
5. Drop `…:environment:<env>-apply` from the trust policy once that environment is
   gone. Left in place, the old subject stays presentable to the role, and
   GitHub auto-creates an environment the moment anything binds that name — a
   stale subject is a standing way back in, and nothing in the pipeline probes
   IAM to notice it.
6. Know the price: a reviewer or a wait timer on that environment stalls the plan
   cells and the nightly drift run, so the reviewer gate is gone rather than
   moved, and plan and apply OIDC tokens become identical in `sub`
   ([`hardening.md`](hardening.md) §6, §7–9).

**The plan-side binding is repository-wide; only the apply side reads the
variable.** The wave jobs of the engine's `apply-env-level.yml` read
`SHIPMATE_SHARED_ENVS` per env, but `plan.yml` and `drift.yml` are yours and the
engine cannot reach their `environment:` line, so what you write there applies to
every env at once. If **every** env moves the same way, bind statically —
`${{ matrix.environment }}-plan` for all-split, `${{ matrix.environment }}` for
all-shared. For a **mixed** repository, carry the engine's own expression so one
variable drives both paths ([`../CONTRACT.md`](../CONTRACT.md) §Env model has the
rule and the indentation constraint):

```yaml
    environment: >-
      ${{ contains(format(',{0},', vars.SHIPMATE_SHARED_ENVS), format(',{0},', matrix.environment))
      && matrix.environment || format('{0}-plan', matrix.environment) }}
```

A static bare binding in a mixed repository is the quiet failure here: the split
envs' plan cells bind an environment you deleted, GitHub auto-creates it empty,
and the cell plans the wrong environment — a fallback default's name, or an empty
one — for a reviewer to approve, per the measurement above. Nothing refuses until
the apply — and on a folder-per-env layout nothing refuses at all
([`../CONTRACT.md`](../CONTRACT.md) §Env model).

**Set `SHIPMATE_SHARED_ENVS` before that expression reaches the default branch.**
The expression falls back to `<env>-plan`, and shared mode never creates one, so
merged with the variable unset every shared env's plan cells bind a `<env>-plan`
nobody made — auto-created empty, with the same outcome as above. So finish the
shared path's steps 1–3 for **every** env you are sharing before the change
carrying this expression merges.

**One silence to expect.** `shipmate doctor` infers the mode from the environment
*names* — it never reads `SHIPMATE_SHARED_ENVS`, which would need a permission the
App manifest does not declare. So a repository that keeps a bare `<env>` and never
sets the variable gets **no existence finding**, where the old code warned that
`<env>-apply` was missing: bare-only is exactly what a correctly configured shared
env looks like, and doctor reports it as one — you still get its shared-environment
findings (unreviewed applies, a warning if it carries approval rules, its secrets),
just nothing saying an environment is missing. On a layout whose environment
injects a non-empty `TF_VAR_*` or `TF_WORKSPACE` the apply itself still fails loud — it binds `<env>-apply`,
GitHub auto-creates it empty, and the apply-match fingerprint refuses the cell
naming every missing `TF_VAR_*` ([`troubleshooting.md`](troubleshooting.md)
§`Saved plan is stale`). **On a folder-per-env layout it does not**: nothing is
injected, both sides hash the empty set, and the apply runs inside that empty
environment with none of its protection rules
([`../CONTRACT.md`](../CONTRACT.md) §Env model states the condition and what it
leaves as the only control). If that is your layout, treat this silence as the
whole check and verify the mode against the environment names by hand.

### 0.12.0 — wrappers pass secrets by name, and re-pinning alone is not enough

Replace `secrets: inherit` in every wrapper job that calls an engine reusable
workflow, in the same change that moves your pins. Pass **only what each callee
declares** — naming one it does not is a load-time error that kills the run with
no job and no log:

| Your wrapper job calls | Pass |
|---|---|
| `summary.yml` (in `plan.yml`) | `SHIPMATE_APP_PRIVATE_KEY` |
| `apply.yml`, `apply-all.yml`, `deploy.yml` | that **and** `SHIPMATE_PLAN_PASSPHRASE` |

```yaml
    secrets:
      SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}
      SHIPMATE_PLAN_PASSPHRASE: ${{ secrets.SHIPMATE_PLAN_PASSPHRASE }}
```

Dropping the passphrase line where a callee declares it is the quiet failure to
watch for: the run starts, and every apply cell then fails its plan-decrypt
fail-safe. Repositories that never set `SHIPMATE_PLAN_PASSPHRASE` pass it
anyway — it resolves to empty, which is what an unencrypted consumer already
had.

Key placement does not change: `SHIPMATE_APP_PRIVATE_KEY` stays a secret on the
`shipmate-engine` environment. A called workflow's `environment:` resolves in
*your* repository and its value wins over whatever the caller passed, so a
wrapper job that binds no environment passes an empty string and the mint still
succeeds.

Inside the engine's own organization `inherit` keeps working, so a repository
that skips this stays green while handing the engine every secret it can see
([`hardening.md`](hardening.md) §What the engine receives from your repository).
Outside it, `inherit` delivers nothing **and** suppresses what the
`shipmate-engine` environment would have supplied: no `shipmate / gate`, no
pending `apply / <stack> / <env>` checks, no sticky comment.

### 0.10.0 — the plan path is one workflow, and re-pinning alone is not enough

You must rewrite `.github/workflows/plan.yml` and delete
`.github/workflows/summary.yml` in the same change that moves your pins. A
repository that re-pins without rewriting gets no `shipmate / gate`, so its pull
requests cannot merge — the old `workflow_run` topology is not supported.

`plan.yml` moves to `pull_request_target` and gains a third job, `summary`,
which is `uses: <owner>/shipmate/.github/workflows/summary.yml@<sha>` with
`secrets: { SHIPMATE_APP_PRIVATE_KEY: ${{ secrets.SHIPMATE_APP_PRIVATE_KEY }} }`
— that secret alone, since a callee rejects a name it does not declare — and
five inputs (`pr-number`, `head-sha`, `detect-result`,
`plan-result`, `planned-cells`), and `permissions: contents: read` — a callee's
permissions are capped by the calling job's, and granting less kills the whole
run at startup with no job, no log and no annotation to explain it. Because
`pull_request_target` checks out the base by default, `detect` and `plan` must
name `ref: ${{ github.event.pull_request.head.sha }}` on their checkout
explicitly — without it they plan the base branch and report a clean plan for a
pull request they never read. The consumer's own `summary.yml` is deleted; the
engine's is a `workflow_call` workflow whose single job binds `shipmate-engine`,
checks out nothing, and carries both trust conditions (the fork refusal and the
draft skip) where a consumer cannot drop them.
[`getting-started.md`](getting-started.md) §Required — plan has the current
shape, and [`../CONTRACT.md`](../CONTRACT.md) §Post-plan topology is the written
form.

**The migration pull request cannot gate itself, and needs a one-time bypass.**
A `pull_request` run uses the workflow file from the pull request's own head,
which no longer declares that trigger; a `pull_request_target` run uses the file
on the default branch, which does not declare it yet. So the pull request that
performs the migration produces no plan run, no checks and no `shipmate / gate`
at all. No ordering avoids it: the first commit whose head declares only
`pull_request_target` is ungatable by construction. Merge that one pull request
with an administrative bypass (an org-admin `bypass_actor` on the ruleset, or a
temporary `enforcement: evaluate`), and restore enforcement immediately
afterwards. Every pull request after it gates normally.

Plans now run against the branch tip rather than the merge commit: the explicit
`head.sha` checkout does not produce a merge ref, so a pull request behind its
base plans what the branch says. The surviving safety net is the stale-plan
refusal at apply time; **require branches to be up to date before merging**
([`branch-protection.md`](branch-protection.md)) closes the rest.

### 0.2.0 — apply check-name field order flipped

**Every pull request planned before the bump needs a new commit, and a re-plan
is not enough.** The apply check is now `apply / <stack> / <env>` (was `apply / <env> /
<stack>`). Nothing in branch protection references it (only `shipmate / gate` is
required), so no ruleset edit is needed — but old-order checks left on a head
SHA break **both** directions, and a re-plan fixes neither, because it adds the
new names *alongside* the old ones on the same commit:

- **Old-order checks still pending → the gate never greens.** `apply-gate`
  treats every `apply / `-prefixed check on the SHA as part of the work queue,
  and the new engine only ever completes the new-order names, so the old
  pending ones stay pending forever.
- **Old-order checks already complete → the post-merge deploy re-queues work
  that is already applied.** This one has no pre-merge signal at all: the gate
  is green and the PR looks finished. On merge, `deploy-detect` reconstructs
  the new-order name for each reviewed cell, finds none of them among the
  completed checks, and treats every applied cell as pending. Each re-apply
  then trips the stale-plan fail-safe (state has moved), so the deploy on
  `main` goes red per stack instead of no-opping. Nothing is applied twice —
  the fail-safe holds — but the deploy needs recovery.

**Do this:** on every open PR that already has apply checks, push a commit (any
commit) so the fan-out lands on a fresh head SHA, then re-plan and, if it was
applied pre-merge, re-apply under the new engine. Note that a check run **cannot
be deleted or dismissed** — the Checks API offers only create, update, get, list
and rerequest — so the only alternative to a fresh commit is completing the
stale check-run ids yourself with a shipmate-App installation token (a check run
is updatable only by the app that created it, and these were App-authored). If a
deploy already went red this way, the stacks are in fact applied: re-plan on a
follow-up PR, get "no changes" checks, and the next deploy no-ops green. New PRs
are unaffected.

### Flip `integration_id` in the same change as the engine-SHA bump

Move the ruleset's required-check `integration_id` from the `github-actions`
identity (`15368` on github.com) to the shipmate App's numeric id
(`SHIPMATE_APP_ID`) at the same time you bump the pinned engine SHA to a build
that authors the gate via the App. Landing the SHA bump first (or the
`integration_id` flip first) leaves a window where the writer and the pinned
identity disagree and every `shipmate / gate` status is rejected as
non-satisfying, blocking all merges until both land together.

### Open PRs need a fresh commit or re-plan after the flip

A PR opened before the upgrade may be carrying: (a) `github-actions`-authored
pending `apply / <stack> / <env>` checks that the App-authored `apply-cell`
cannot complete (different identity — check-run completion is scoped to the
creating app), and (b) an existing `shipmate / gate` status authored by the old
identity, which no longer satisfies the newly-pinned `integration_id`. Push a
commit (or re-run `plan.yml`) on each open PR after upgrading so a fresh,
App-authored set of checks and gate status is produced.

### Gate-writer jobs no longer need `statuses: write` / `checks: write` on `GITHUB_TOKEN`

The App manifest now carries both scopes, so `actions/summary` /
`actions/gate-refresh` mint their own installation token instead of relying on
the calling job's `GITHUB_TOKEN` permissions. Remove any `statuses: write` /
`checks: write` grant from jobs that run these actions, and thread `app-id` +
`private-key` into the `with:` of each call (for a reusable-workflow caller job,
pass `SHIPMATE_APP_PRIVATE_KEY` by name in the job's `secrets:` block instead —
the called workflow declares it under `on.workflow_call.secrets`; on the apply
and deploy paths that block carries `SHIPMATE_PLAN_PASSPHRASE` too, see
§0.12.0). A leftover
`GITHUB_TOKEN` grant is stale, not harmful by itself, but remove it — the writer
step doesn't use it, and it needlessly widens the default token's scope.

### Required status check renamed

If your branch protection currently requires the aggregate gate check under its
pre-rename name, update it to require `shipmate / gate` instead — in the **same
change** that bumps your pinned engine SHA. Otherwise GitHub keeps waiting on
the old required check forever, and PRs can't merge even though the new engine
is reporting `shipmate / gate`.

### Plan workflow renamed `preview.yml` → `plan.yml`

shipmate resolves a PR's reviewed plan by the workflow filename that produced
it. A PR whose plan was produced by an old `preview.yml` run is not recognized
after your workflow is renamed to `plan.yml` — push a commit to trigger a fresh
`plan.yml` run and get it re-reviewed before `shipmate apply` or a merge-deploy
will act on that PR.
