# shipmate documentation

Setup, operation and troubleshooting for a repository that consumes shipmate,
plus the two pages a maintainer of the engine needs.

## Consumers

In reading order.

| Page | What it is |
| --- | --- |
| [`getting-started.md`](getting-started.md) | Wire shipmate into one repository, in four ordered tiers. Start here. |
| [`aws.md`](aws.md) | S3 backend, GitHub OIDC roles, per-environment variables. |
| [`github-app.md`](github-app.md) | Register and install the App; the `shipmate-engine` environment. |
| [`branch-protection.md`](branch-protection.md) | Require `shipmate / gate`; the reproducible ruleset. |
| [`drift.md`](drift.md) | Optional nightly drift detection, and what it costs. |
| [`upgrading.md`](upgrading.md) | Re-pinning, Dependabot, past migrations. |
| [`troubleshooting.md`](troubleshooting.md) | `shipmate doctor`, who may ask for its report, and the failures consumers hit. |
| [`concepts.md`](concepts.md) | How it works: fan-out, checks-first, comment-ops, the environment/tag model. |
| [`hardening.md`](hardening.md) | Who can make the engine act at all, and what none of it fixes. |

## Maintainers

| Page | What it is |
| --- | --- |
| [`development.md`](development.md) | Repo layout, toolchain, testing model, how guard tests must be written. |
| [`releasing.md`](releasing.md) | Cutting a release and re-pinning the engine's own internal action references. |

[`../CONTRACT.md`](../CONTRACT.md) is the spec behind all of it — check names,
the environment model, tag grammar, pinning and the comment grammar. Read it
when you need the exact semantics rather than an explanation of them.
