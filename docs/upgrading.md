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

### Unreleased — every cell-running job maps one more secret

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
your repository's default branch, and from nothing else. Declare
`global.shipmate.layout` there before re-pinning: a repository with no table is
refused on every run, and a pull request that only *adds* the table is refused
too, because the engine reads the table from the branch that pull request's plan
is compared against. [`../CONTRACT.md`](../CONTRACT.md) §Environment table is the
schema and the semantics of record.

Five GitHub Environment variables stop being read — `AWS_ROLE_ARN`,
`AWS_REGION`, `TF_VAR_env`, `TF_VAR_region` and `TF_WORKSPACE` — along with any
`AWS_ROLE_ARN_<WORKLOAD>`. They are inert once the re-pin lands, so deleting them
is cleanup rather than a cutover.
