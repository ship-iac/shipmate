# Upgrading

How a consuming repository pins this engine and moves that pin, and what the
current release needs beyond the move.

## Re-pinning

**Every engine reference moves in one change.** The consumer surface is the
seven reusable workflows, and they share inputs and secrets across a release: a
repository that bumps some of those refs and leaves others behind is running two
engine versions against one contract. No composite action carries a pin — the
actions are engine-internal and run from the engine checkout at
`.shipmate-engine/`, so nothing inside them has to move.
[`../CONTRACT.md`](../CONTRACT.md) §Consumption is the rule.

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

`gate.ungated_envs` lets named environments be applied without an approving
review while the rest keep the branch ruleset's requirement. Declare it in
`.github/shipmate.toml` and nothing else — no repository setting, no workflow
line: comment-ops and both apply paths each read the file from your default
branch themselves. See
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
(a separate control — see [`hardening.md`](hardening.md) §3–5), and anyone who
can open a pull request can propose an entry — what they cannot do is have it
take effect before it merges.
