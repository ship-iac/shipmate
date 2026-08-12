# AWS setup

The worked example for everything below is
[repo-example-stacks-aws](https://github.com/ship-iac/repo-example-stacks-aws) —
the only sample repository that holds cloud credentials. The semantics of record
are [`../CONTRACT.md`](../CONTRACT.md) §AWS OIDC and §State backend; this page is
how the sample satisfies them.

Nothing here is required to run shipmate. The engine ships no credential of its
own and is cloud-agnostic by default; the other three sample repositories run
credential-free on a local backend.

## S3 backend

State lives in one S3 bucket with **native locking** — `use_lockfile = true`, so
S3's conditional writes hold the lock and there is no separate lock table. The
sample's `root.tm.hcl` carries two mutually exclusive variants of the backend
block; this is the simpler one, generated when no dedicated state role is
configured:

```hcl
generate_hcl "_backend.tf" {
  condition = global.state_role_arn == ""
  content {
    terraform {
      backend "s3" {
        bucket       = "repo-examples-shipmate-state"
        key          = "repo-example-stacks-aws/${var.env}/${var.region}${terramate.stack.path.absolute}/terraform.tfstate"
        region       = "eu-north-1"
        use_lockfile = true
        encrypt      = true
        profile      = var.use_profile ? "${global.workload}-${var.env}" : null
      }
    }
  }
}
```

The `key` is what makes this one state file per stack × environment: it embeds
`var.env`, `var.region` and the stack's absolute Terramate path, all from the
same values the fan-out already carries. `profile` is for running by hand — in CI
`var.use_profile` defaults to `false` and the SDK reads the ambient OIDC session.

The other `generate_hcl "_backend.tf"` block is guarded on
`global.state_role_arn != ""` and adds an `assume_role` hop so the backend reaches
the bucket through a dedicated state role. That is the block the sample actually
generates — `root.tm.hcl` sets `state_role_arn` to a real ARN, and only
`sandbox/box` overrides it to `""` and reaches the bucket directly. Two blocks
rather than one conditional attribute because Terramate 0.17.1 has no
`tm_unset()`, and a bare `unset` emits `assume_role = unset`, which survives
`fmt` and `validate` and dies at `init`.

Because the backend owns the state, the apply-path wrappers pass
`state_suffix: ""`. That is the explicitly-empty mode of
[`../CONTRACT.md`](../CONTRACT.md) §State backend: both `actions/state` steps are
skipped entirely and shipmate never handles a state file. The input declares no
default, so omitting it is a workflow-resolution error rather than a third mode.

## Named profiles must be conditional

`profile` being conditional above is a constraint on how you write HCL, not a
stylistic choice. The apply jobs run inside the engine's reusable workflows and
there is no consumer step between `setup` and `apply-cell`, so there is nowhere
to write an `~/.aws/config`; the apply path holds only the OIDC session. Any
`provider` or `backend` block carrying a literal `profile` therefore fails at
apply. Gate the profile on a variable that defaults to `false` —
`var.use_profile ? "…" : null` — so the same code serves a run by hand and CI.

Worth stating rather than discovering: such a block plans fine locally, where
the named profile exists, and only fails once it reaches the apply path.

## GitHub OIDC

Each environment gets its own IAM role, assumed through GitHub's OIDC provider
(`token.actions.githubusercontent.com`) — no long-lived access key anywhere. The
role's trust policy conditions the `sub` claim on the environment claim
(`environment:<env>-apply` for an apply role, `environment:<env>-plan` for a
read-only plan role), which is the only control that decides which environments
can actually assume it; see [`hardening.md`](hardening.md) §7–9 for why that, and
not where you put the variable, is the enforcing bound.

Because the claim is inside the condition, **renaming an environment breaks its
role's trust policy** — add the new subject before the rename and drop the old one
after; [`upgrading.md`](upgrading.md) §0.13.0 has the ordered steps for both the
split and the shared migration.

**Write the trust condition from the subject your own logs show, not from the
documented shape.** GitHub Actions issues the `sub` claim with the numeric
organization and repository ids embedded — captured from that sample repository
before its environments were renamed, so the environment segment here is a bare
`dev-us` where a split repository reads `dev-us-plan` / `dev-us-apply`:

```
repo:ship-iac@305536692/repo-example-stacks-aws@1325724489:environment:dev-us
```

while `GET repos/{owner}/{repo}/actions/oidc/customization/sub` reports
`use_immutable_subject: false` — the API contradicts the token, so its answer is
not evidence. A trust policy written against the human-readable
`repo:<owner>/<repo>:...` form then fails with a bare `AccessDenied — Not
authorized to perform sts:AssumeRoleWithWebIdentity` and nothing wrong-looking in
the policy, the provider, or the workflow. Failed `AssumeRoleWithWebIdentity`
calls are CloudTrail management events, visible in Event history with no trail
configured, and `userIdentity.userName` is the exact subject that arrived:

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity \
  --max-results 3 --region us-east-1
```

`requestParameters` is `null` on those events — the subject is only in
`userIdentity.userName`. The immutable form is also the stronger condition: a
repository renamed or recreated under an old name cannot inherit the trust.

## Environment variables

A consumer opts into AWS OIDC by setting two GitHub Environment **variables** —
not secrets, since neither is one:

- `AWS_ROLE_ARN` — the IAM role the job assumes.
- `AWS_REGION` — the region passed to the credentials step.

On the apply path a third, optional variable takes precedence:

- `AWS_ROLE_ARN_<WORKLOAD>` — the role for cells carrying a `workload/<name>`
  tag. `<WORKLOAD>` is that tag's name upper-cased with `-` replaced by `_`
  (`workload/net-edge` → `AWS_ROLE_ARN_NET_EDGE`). When the cell has no workload
  tag, or that variable is unset, the job falls back to `AWS_ROLE_ARN`.

With no role variable set the engine's credentials step is skipped and the job
holds no cloud credential at all, which is how the three non-AWS sample
repositories run credential-free.

That is the Environment-count arithmetic: without it, one role per workload
means one Environment per (env × region × workload); with it, a single
`<env>-apply` Environment can serve several workloads, each assuming its own
role.

Set them per environment, and **never at repository or organization level**:

- on each `<env>-apply` you want cloud access from — that is the apply path, where
  the engine reads them ([`hardening.md`](hardening.md) #18);
- and, only if your `plan.yml` carries its own credentials step, on each
  `<env>-plan` environment too, with a **read-only** plan role. That is what
  `repo-example-stacks-aws` does: its plan environments name a read-only role
  and its apply environments name the apply role. A plan
  environment can have no approval rules and no branch policy at all
  ([`hardening.md`](hardening.md) #8), so whatever role it names is reachable by
  anyone who can push a branch.

That per-environment scoping is advisory, not enforced. For `vars` the most
specific wins — environment overrides repository overrides organization — so an
`AWS_ROLE_ARN` set at repository or organization level is read identically by
every job in every environment that does not set its own, with no warning and
nothing in the engine to guard it. The role's trust policy is the real bound.

## Where the credentials step goes

**On the apply path the consumer writes no credentials step.** The engine's
`apply-env-level.yml` runs `aws-actions/configure-aws-credentials` in every wave
job itself — after `actions/setup`, before `apply-cell`, gated on either role
being set — reading the variables from the apply environment the job is bound to
(`<env>-apply`, or the bare `<env>` in shared mode). The wrapper's only obligation is
`id-token: write` on the calling job (see
[`getting-started.md`](getting-started.md) §Required — apply).

The plan-side role lives on the `<env>-plan` environment. In **shared mode** — a
logical env listed in `SHIPMATE_SHARED_ENVS` binds one bare `<env>` on both paths
— there is one role for both and the wave jobs read it, so it must be the apply
role: plan-time branch code and the drift run then have write access, and the
read-only plan role is unreachable for that env
([`hardening.md`](hardening.md) §7–9).

**On the plan and drift paths the step is the consumer's own**, because
`plan.yml` and `drift.yml` are consumer-owned workflows and the engine provides
no credentials step there. In both sample workflows it sits in the same position:
after the `ship-iac/shipmate/actions/setup` step and before the cell action
(`plan-cell`, `drift-cell`), so the assumed session exists by the time `tofu`
runs and setup has not yet been given a credential it does not need.

Both sample steps are unconditional, because that repository sets the variables
on every environment. If some of your environments run credential-free, guard
your step with `if: ${{ vars.AWS_ROLE_ARN != '' }}` — with the
variable unset, `configure-aws-credentials` has no role to assume and the cell
fails there rather than skipping.

## A green plan does not size either policy

A plan against **empty state** refreshes nothing, so none of the resource reads
the run will eventually need are ever attempted: a too-tight policy on either
role first surfaces at the **apply**, and once state exists the plan role is only
exercised as far as the refresh reaches. Read a green plan as evidence that the
role could be assumed, not that it can read.

One trap survives an otherwise careful policy: an action that takes **no
resource-level permission** — `ssm:DescribeParameters` is the common one — can
never be satisfied by an ARN-scoped statement, so it needs its own statement on
`"*"` even where every other action on that service is correctly scoped to one
prefix.

## Runner choice

The documented fences in [`getting-started.md`](getting-started.md) and
[`drift.md`](drift.md) use `runs-on: ubuntu-slim`, which suits the three
credential-free samples: their cells download no provider. An AWS repository
does — every cell pulls `hashicorp/aws` — and if `.terraform.lock.hcl` is
gitignored, as it is in `repo-example-stacks-aws`, every `init -reconfigure`
re-resolves it from scratch. On a cloud repository weigh the slim image against
that download before copying the label; `ubuntu-latest` remains the safe
default.

## The sample's workload

Every stack manages `random_pet` and `terraform_data` null resources plus one
`aws_ssm_parameter`, named
`/shipmate/repo-example-stacks-aws/<env>/<stack path>` — so the real AWS
footprint of a full fan-out is one SSM parameter per stack × environment, and the
S3 state object beside it.

That is deliberately the smallest thing that still proves a real provider, a real
remote backend and real locking: the fixtures the repository exercises (stale
plan, drift, precondition failure) come from the null resources, while the SSM
parameter and the S3 lock are what make the run indistinguishable from a
production one from the engine's side.
