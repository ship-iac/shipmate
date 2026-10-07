# AWS setup

The worked example for everything below is
[repo-example-stacks-aws](https://github.com/ship-iac/repo-example-stacks-aws) —
the only sample repository that holds cloud credentials. The semantics of record
are [`../CONTRACT.md`](../CONTRACT.md) §AWS OIDC, §Environment table and §State
backend. This page is how the sample satisfies them.

Nothing here is required to run shipmate. The engine ships no credential of its
own and is cloud-agnostic by default. The other three sample repositories run
credential-free on a local backend.

## S3 backend

State lives in one S3 bucket with native locking — `use_lockfile = true`, so
S3's conditional writes hold the lock and there is no separate lock table. The
sample's `root.tm.hcl` carries two mutually exclusive variants of the backend
block. This is the simpler one, generated when no dedicated state role is
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
same values the fan-out already carries. `profile` is for running by hand. In CI
`var.use_profile` defaults to `false` and the SDK reads the ambient OIDC session.

The other `generate_hcl "_backend.tf"` block is guarded on
`global.state_role_arn != ""` and adds an `assume_role` hop, so the backend
reaches the bucket through a dedicated state role. That is the block the sample
actually generates: `root.tm.hcl` sets `state_role_arn` to a real ARN, and only
`sandbox/box` overrides it to `""` and reaches the bucket directly. Two blocks
rather than one conditional attribute because Terramate 0.17.1 has no
`tm_unset()`, and a bare `unset` emits `assume_role = unset`, which survives
`fmt` and `validate` and dies at `init`.

Because the backend owns the state, `tofu init` records an `s3` backend and every
cell skips both `actions/state` steps: shipmate never handles a state file
([`../CONTRACT.md`](../CONTRACT.md) §State backend).

## Named profiles must be conditional

`profile` being conditional above is a constraint on how you write HCL, not a
stylistic choice. The apply jobs run inside the engine's reusable workflows and
there is no consumer step between `setup` and `apply-cell`, so there is nowhere
to write an `~/.aws/config`. The apply path holds only the OIDC session. Any
`provider` or `backend` block carrying a literal `profile` therefore fails at
apply. Gate the profile on a variable that defaults to `false` —
`var.use_profile ? "…" : null` — so the same code serves a run by hand and CI.

Such a block plans fine locally, where the named profile exists, and only fails
once it reaches the apply path.

## GitHub OIDC

Roles are named per identity: an `[identities.<name>]`
table names a plan and an apply role, and every environment whose entry carries
`identity = "<name>"` assumes them, through GitHub's OIDC provider
(`token.actions.githubusercontent.com`) — no long-lived access key anywhere. The
role's trust policy conditions the `sub` claim on the environment claim
(`environment:<env>-apply` for an apply role, `environment:<env>-plan` for a
read-only plan role), which is the only control that decides which environments
can actually assume it. A role that several environments assume must admit
each one's subject. [`hardening.md`](hardening.md) §7–9 explains why that,
and not where the role is named, is the enforcing bound.

Because the claim is inside the condition, renaming an environment breaks its
role's trust policy. Add the new subject before the rename and drop the old one
after.

**Dropping the old subject afterwards is not tidying.** GitHub auto-creates an
environment the instant a job binds its name, with none of that environment's
protection rules, and nothing in the pipeline probes IAM. A subject left trusted
for a deleted environment is a standing way back in, reachable by anyone who can
get a job to bind that name. Follow what the role reaches rather than what it is
called: a read-only plan role that may `sts:AssumeRole` a state role ends at
state write, not at read, and a plan of a state file it can rewrite is worth
nothing.

**Write the trust condition from the subject your own logs show, not from the
documented shape.** GitHub Actions issues the `sub` claim with the numeric
organization and repository ids embedded, captured from that sample repository.
Under the split naming the environment segment reads `dev-us-plan` or
`dev-us-apply`; an environment with `shared = true` binds the bare `dev-us`, the
form this capture shows:

```
repo:ship-iac@305536692/repo-example-stacks-aws@1325724489:environment:dev-us
```

while `GET repos/{owner}/{repo}/actions/oidc/customization/sub` reports
`use_immutable_subject: false`. The API contradicts the token, so its answer is
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

## The environment table

A cell resolves its role from the environment table — `.github/shipmate.toml` —
and from nothing else. An `[identities.<name>]` table names the roles once, and
each environment's entry names an identity and carries its region. The engine
reads the file from the repository's default branch, so a pull request cannot
choose which role its own plan assumes.
[`../CONTRACT.md`](../CONTRACT.md) §Environment table is the schema of record;
this is what it looks like for the AWS sample:

```toml
layout = "tf_vars"

[identities.dev]
aws.plan  = "arn:aws:iam::9817:role/shipmate-plan"
aws.apply = { app = "arn:aws:iam::9817:role/shipmate-apply", net-edge = "arn:aws:iam::9817:role/net-edge" }

[environments.dev-eu]
region    = "eu-west-1"
identity  = "dev"
workloads = ["app", "net-edge"]
```

Beyond the schema:

- **Give the two paths different roles, always.** The plan path is reachable
  from any branch, so an apply role in `aws.plan` hands it write access — see
  [`hardening.md`](hardening.md) §7–9, which has the whole argument.
- **A role is a role name or a full ARN.** A name needs `aws.account` and becomes
  `arn:aws:iam::<account>:role/<name>`; an ARN, as above, is used as written and
  must have no account beside it.
- **The credentials step authenticates against the environment's `region`.** An
  environment naming an identity without a region is refused.
- **`aws.apply` above varies by workload**, so `dev-eu` must list the workloads
  it admits. A stack tagged `workload/app` assumes the first role on the apply
  path, one tagged `workload/net-edge` the second, and both assume the one plan
  role. The tag is matched exactly as written, so two workloads whose names
  differ only in punctuation are two workloads. `{workload}` in a role string is
  the other way to vary: `shipmate-apply-{workload}` names one role per listed
  workload.
- **A tag outside `workloads` is refused at detect**, and an untagged stack in
  `dev-eu` runs with no credential, because its identity varies. Add and remove
  a workload in the order [`../CONTRACT.md`](../CONTRACT.md) §Adding and
  removing an environment gives.
- **Several environments may name one identity.** One edit to
  `[identities.dev]` then retargets all of them; `shipmate doctor`'s roles lines
  list what each environment resolves ([`../CONTRACT.md`](../CONTRACT.md)
  §Resolution).
- **A shared environment resolves `aws.apply` on both paths**
  ([`hardening.md`](hardening.md) §7–9 has the cost).
- **Adding or removing an environment takes two pull requests**
  ([`../CONTRACT.md`](../CONTRACT.md) §Adding and removing an environment). The
  first table of all has to be on the default branch before the first plan run,
  so it lands in the commit that adds the workflow file rather than in a pull
  request of its own.

## Where the credentials step goes

**The consumer writes no credentials step on any path.** Every engine job that
runs a cell carries `aws-actions/configure-aws-credentials` itself — after
`actions/setup`, before the cell action, gated on a role resolving non-empty. It
reads the role and the region the detect resolved onto the row. That is the
wave jobs of `apply-env-level.yml` and `unlock.yml`'s unlock job on the apply
side, reading `<env>-apply` (or the bare `<env>` in shared mode), and
`plan.yml`'s `plan` job and `drift.yml`'s `drift` job on the plan side, reading
`<env>-plan` (or that same bare `<env>`). A consumer job's only obligation is
`id-token: write` on itself (see
[`getting-started.md`](getting-started.md) §Required — plan).

The plan-side role is the identity's `aws.plan`. A shared environment resolves
`aws.apply` on both paths instead, so plan-time branch code holds the apply role
([`hardening.md`](hardening.md) §7–9).

**The plan and drift steps resolve a workload role too**, like the wave jobs:
an `aws.plan` map or a `{workload}` in it gives each listed workload its own
plan role.

**A step is skipped** wherever the cell resolves no role on its path — an
environment with no entry, an entry naming no identity, an identity setting
only `aws.apply` on the plan path, or an untagged cell whose identity varies by
workload. An environment naming no identity is credential-free on its own. A
cell tagged outside its environment's `workloads` is not skipped: detect refuses
it.

## A green plan does not size either policy

A plan against empty state refreshes nothing, so none of the resource reads the
run will eventually need are ever attempted. A too-tight policy on either role
first surfaces at the apply, and once state exists the plan role is only
exercised as far as the refresh reaches. Read a green plan as evidence that the
role could be assumed, not that it can read.

One trap survives an otherwise careful policy: an action that takes no
resource-level permission — `ssm:DescribeParameters` is the common one — can
never be satisfied by an ARN-scoped statement, so it needs its own statement on
`"*"` even where every other action on that service is correctly scoped to one
prefix.

## Runner choice

Only `shipmate.yml`'s `plan` job and each drift file's `drift` job accept
`runs_on:`. The documented fences in [`getting-started.md`](getting-started.md)
pass none, so those jobs run on
the `ubuntu-latest` default. Outside them the
runner is fixed:

- `ubuntu-slim`: the control jobs, which run neither `tofu` nor `terramate`.
  These are `guard` and `summary` in `apply.yml`, `review`
  in `apply-review.yml`, `snapshot` and `complete` in `apply-env-level.yml`,
  `summary` in `deploy.yml`, `guard` in `unlock.yml`, and `ops` in
  `comment-ops.yml`.
- `ubuntu-latest`: every detect job, which runs `terramate`, and every apply
  and unlock cell, which runs `tofu`.

The three credential-free samples pass `runs_on: ubuntu-slim` to `plan` and
`drift`; their cells download only `hashicorp/random`. An AWS repository's
cells pull `hashicorp/aws`. The provider cache serves a stack once its
`.terraform.lock.hcl` is committed ([`../CONTRACT.md`](../CONTRACT.md)
§Terramate safeguards, "Lock files and the provider cache") and a drift or
apply cell has saved the entry; plan cells never save. Without a committed
lock, as in `repo-example-stacks-aws`, every `init -reconfigure` downloads the
provider again; weigh the slim image against that download before passing it
to `plan` or `drift`.

## The sample's workload

Every stack manages `random_pet` and `terraform_data` null resources plus one
`aws_ssm_parameter`, named
`/shipmate/repo-example-stacks-aws/<env>/<stack path>`. The real AWS footprint of
a full fan-out is therefore one SSM parameter per stack × environment, and the S3
state object beside it.

That workload is deliberately the smallest thing that still proves a real
provider, a real remote backend and real locking. The fixtures the repository
exercises (stale plan, drift, precondition failure) come from the null resources,
while the SSM parameter and the S3 lock are what make the run indistinguishable
from a production one from the engine's side.
