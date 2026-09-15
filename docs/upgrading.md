# Upgrading

How a consuming repository pins this engine and moves that pin, and what the
current release needs beyond the move.

## Re-pinning

**Every engine reference moves in one change.** A repository that bumps some
pins and leaves others behind is running two engine versions against one
contract — the actions, the reusable workflow calls, and any pin inside a
composite action you wrap all name the same SHA, or they disagree.
[`../CONTRACT.md`](../CONTRACT.md) §Consumption is the rule;
[`releasing.md`](releasing.md) is the maintainer side of the same cascade.

Consumers pin by commit SHA, never by tag or branch name, optionally with a
trailing `# vX.Y.Z` comment naming the release that SHA belongs to
(`uses: <owner>/shipmate/actions/state@<sha> # v0.1.0`). The comment is for
human readers and for Dependabot's bookkeeping; the ref that resolves is always
the SHA. The SHA of record for a release is named in that release's section of
[`../CHANGELOG.md`](../CHANGELOG.md).

**Resolving a tag to its commit takes the dereferencing call.** Releases from
`v0.14.2` on are *annotated* tags, so `git/ref/tags/<tag>` returns the tag
object's SHA — 40 hex characters from a 200 response, and not a ref a workflow
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

**Ordering.** Wave ordering comes only from the Terramate `after` DAG. If
your ordering lives in the outgoing tool's configuration, it must be ported into
`after`. Nothing will report the omission, because a missing edge is
indistinguishable from a stack that is genuinely independent. Treat the outgoing
tool's config as a *lower bound* on the real graph, not as the graph: one
migration that audited the OpenTofu code instead of porting the config went from
6 declared edges to 105, and from 2 wave levels to 6.

Two detect jobs report the shape as a `::notice::` line — stack count, `after`
edge count, wave levels, and how many stacks would apply concurrently. A reader
who knows the repository can judge that last number immediately; nobody else
can. Exactly two print it: the detect job of a dispatched `shipmate apply <env>`
and the post-merge deploy's detect, so it arrives after the pull request
that would have been the place to fix the graph. A plan run does not print
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
or `TF_WORKSPACE` wins over whatever the cell was given — invisibly,
because the fingerprint is computed outside `terramate run` and so agrees on
both sides. `detect` injects a sentinel into those three variables and fails the
run when one comes back changed. [`../CONTRACT.md`](../CONTRACT.md) §Env model
has the rule and the `tm_try` form that keeps a local default.

## Opt-in: per-environment review gating

`SHIPMATE_UNGATED_ENVS` lets named environments be applied without an approving
review while the rest keep the branch ruleset's requirement. Set the
`SHIPMATE_UNGATED_ENVS` repository variable and nothing else: engine
`comment-ops.yml` passes `ungated-envs: ${{ vars.SHIPMATE_UNGATED_ENVS }}`
itself, and `vars` inherit into a called workflow, so the variable resolves in
your repository with no line to wire. See
[`getting-started.md`](getting-started.md) §"Applying chosen environments
without an approving review".

An apply is authorized in `comment-ops.yml` and enforced in the engine, so the
exemption only holds where both sides are on the same pin. Your
`.github/workflows/shipmate.yml` carries one engine pin per job — the
`comment-ops` job's, and the `targeted` and `all` jobs' calls to engine
`apply.yml` and `apply-all.yml` — and §Re-pinning's one-change rule is what
keeps them together. On this feature, breaking it fails open rather than loudly:
an apply authorized under the new pin and dispatched into a stale engine is
enforced by nothing.

Two things it does not change, worth confirming against your own policy before
you set it: an environment's `required_reviewers` still gates the deployment
(a separate control — see [`hardening.md`](hardening.md) §3–5), and the
variable is editable by anyone holding the Write role.

## Past migrations

An entry applies only if you are moving *from* a pin older than the release it
names.

The per-release sections for `0.13.0` through `0.27.0` were removed when the
environment table became the only source of a cell's identity. `CHANGELOG.md`
still links to them from those releases' own entries; the migrations they
described were between pre-table engine releases and have no consumers left to
migrate. Each release's `CHANGELOG.md` entry is the record of what changed.

### Unreleased — the environment table moves to `.github/shipmate.toml`

**The whole table leaves Terramate.** What was a `globals "shipmate"` block in
your HCL is now a flat TOML file at `.github/shipmate.toml`, read with `tomllib`
from the standard library. There is no dual-support window: this engine reads
the new location and nothing else. An older pin keeps reading the old form, so
the two coexist only across the pin boundary, not inside one release.

**This takes two merges per repository, not one.** A single pull request doing
both halves cannot pass its own checks: the new engine reads
`.github/shipmate.toml` from the **default branch**, where the file does not
exist until that pull request merges, so its own `detect` refuses.

1. **Add `.github/shipmate.toml`.** Keep the HCL table and keep the current
   engine pin. Checks run the old engine against the old table and stay green.
   Merging puts the file where the new engine will look for it.
2. **Switch.** Bump the engine pin and delete the `globals "shipmate"` block in
   the same pull request. The new engine now finds the file already on the
   default branch.

**Carry all four top-level keys in step 1**, not just `layout` and
`environments`. `env_order` and `explicit_envs` moved into the same file, and
both are **silent when absent**: a file that omits them is structurally valid
and takes the tolerant default — no ordering, and no exclusions. A repository
that had `explicit_envs = ["prod"]` and forgets it here keeps planning and
applying green, and the first bare `shipmate apply` after step 2 reaches
production. Nothing refuses, nothing warns. `shipmate doctor` echoes both values
on every report, which is the check to actually read.

**Validate the file before merging step 1.** Between the two merges it sits on
the default branch entirely unvalidated — the old engine never reads it. Parsing
it locally is the part you can do today:

```console
$ python3 -c "import tomllib,pathlib; tomllib.loads(pathlib.Path('.github/shipmate.toml').read_text(encoding='utf-8'))"
```

That catches a syntax error and a duplicate table. It does not catch a misplaced
setting, which is well-formed TOML — the engine's structural checks are what
catch that, and they first run after step 2 merges. Read the file top to bottom
against the schema in [`../CONTRACT.md`](../CONTRACT.md) §Environment table
before you merge step 1.

**Translating the block.** The parsed structure is unchanged; only the spelling
moves. Dotted keys are canonical — one `[environments.<name>]` header per
environment, tiers written inside it:

```hcl
globals "shipmate" {
  layout = "dry"
  environments = {
    "dev-eu" = {
      region = "eu-west-1"
      aws = {
        plan  = { role = "arn:aws:iam::9817:role/shipmate-plan" }
        apply = { role = "arn:aws:iam::9817:role/shipmate-apply" }
      }
    }
  }
  env_order     = { "dev-us" = ["dev-eu"] }
  explicit_envs = ["prod"]
}
```

becomes

```toml
layout        = "dry"
explicit_envs = ["prod"]

[env_order]
dev-us = ["dev-eu"]

[environments.dev-eu]
region         = "eu-west-1"
aws.plan.role  = "arn:aws:iam::9817:role/shipmate-plan"
aws.apply.role = "arn:aws:iam::9817:role/shipmate-apply"
```

Two rules the old form did not have. Top-level settings go **above the first
`[table]` header**, because a scalar written below one lands inside that table.
And one notation per environment: dotted keys plus a later
`[environments.dev-eu.aws.plan]` header is a parse error.
[`troubleshooting.md`](troubleshooting.md) §`.github/shipmate.toml` is rejected,
or its settings do not take effect has both traps and the messages they produce.

**Give the plan and apply tiers separate roles.** Dotted keys make a block-level
`aws.role` easy to write, and it covers the plan path as well as the apply path —
the plan path being reachable from any branch. Write `aws.plan.role` and
`aws.apply.role`; it is the same two lines ([`hardening.md`](hardening.md) §7–9).

**`env_order` and `explicit_envs` now come from the default branch.** This is a
behaviour change, not a spelling change. They used to be evaluated out of the
checked-out tree, so a feature branch could reorder its own apply waves or drop
its own production exclusion, and the edit took effect on that branch. It no
longer does: both are read from the same default-branch file as the identity
table, for the same reason — a pull request must not choose what its own applies
do. An edit to either takes effect when it merges.

**Reading the file needs Python 3.11**, which
[`../CONTRACT.md`](../CONTRACT.md) §Runner prerequisites has always required and
nothing previously exercised. If your workflow names a `runs_on:` image older
than that — `ubuntu-22.04` ships 3.10 — `detect` refuses with the version it
found. Nothing in the engine installs or pins a Python.

**A Terramate global that referenced the table is now on its own.** No sample
repository read `global.shipmate` outside the file that declared it; if yours
does, keep your own globals for that purpose. The engine's configuration and
your stack configuration are separate concerns from here on, and a consumer that
wants Terramate to see an environment value uses `terramate.config.run.env` with
`env.*`.

**Also gone: programmatic construction.** TOML has no expressions, functions or
references, so a table that was generated or merged in HCL becomes explicit
repetition. That is deliberate for a file deciding which cloud role a job
assumes, and it is a real loss for a repository with many environments.

### 0.29.0 — every cell-running job maps one more secret

**Add one line to six jobs.** In `.github/workflows/shipmate.yml`, the `plan`,
`deploy`, `drift`, `targeted`, `all` and `unlock` jobs each gain
`SHIPMATE_SECRETS: ${{ secrets.SHIPMATE_SECRETS }}` in their `secrets:` block —
and `unlock` gains a `secrets:` block, which it did not have before. The fence
in [`getting-started.md`](getting-started.md) §The workflow file carries the
line already, and `scripts/onboard` reports your file as `differs` until you add
it — it never overwrites a consumer-edited file, so the edit is yours to make.

The line is harmless for a repository that sets no such secret: each callee
declares `SHIPMATE_SECRETS` with `required: false`, and an unset secret resolves
empty. Skipping it is what costs. An unmapped secret is simply absent in the
callee — no error, no annotation — so a `SHIPMATE_SECRETS` envelope set later
never reaches a cell and nothing says why. What that envelope carries is in
[`getting-started.md`](getting-started.md) §Variables and secrets your stacks
need.

### 0.28.0 — the engine release declares the tool versions, and a cell's identity comes from the default branch

**Re-pinning is not enough: two repository variables go away.**
`actions/setup` reads the release's own root-level `VERSIONS` file at the commit
you pin, and the engine's reusable workflows no longer pass
`vars.TERRAMATE_VERSION` / `vars.TOFU_VERSION` to it. Both repository variables
become inert. Moving to other tool versions is a pin bump.

Order matters, because the two halves live on opposite sides of the re-pin:

1. Re-pin `.github/workflows/shipmate.yml` (§Re-pinning) and merge it.
2. `gh variable delete TERRAMATE_VERSION` and `gh variable delete TOFU_VERSION`.

Deleting first blanks an input the workflows on your current pin still read, and
`opentofu/setup-opentofu` resolves an empty `tofu_version` as `latest` — an
unpinned tool on every plan and apply until the re-pin lands. The same applies in
reverse: **re-create both variables before rolling a pin back** to a release
before this one.

Until you delete them, `scripts/onboard` reports each one as `differs` and exits
2 ([`troubleshooting.md`](troubleshooting.md) §What `scripts/onboard` reports).
The run writes nothing over them: it did not write them.

**A pinned older version is no longer a per-consumer lever.** A repository that
held `TOFU_VERSION` back gets the version this release pins on its next bump. To
stay on the older one, stay on the prior engine release, or open an issue for a
per-consumer override.

**Re-pinning is not enough: the environment table is now required.** A cell's
identity variables, role and region come from a `globals "shipmate"` block on
your repository's default branch, and from nothing else. (That block later moved
to `.github/shipmate.toml` — see the Unreleased entry above, which supersedes the
spelling here.) Declare `global.shipmate.layout` there before re-pinning: a
repository with no table is
refused on every run, and a pull request that only *adds* the table is refused
too, because the engine reads the table from the branch that pull request's plan
is compared against. [`../CONTRACT.md`](../CONTRACT.md) §Environment table is the
schema and the semantics of record.

Five GitHub Environment variables stop being read — `AWS_ROLE_ARN`,
`AWS_REGION`, `TF_VAR_env`, `TF_VAR_region` and `TF_WORKSPACE` — along with any
`AWS_ROLE_ARN_<WORKLOAD>`. They are inert once the re-pin lands, so deleting them
is cleanup rather than a cutover.
