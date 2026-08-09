# shipmate

> **Status: early development.** shipmate is a work in progress. Action inputs,
> check names, and tag grammar may change between commits. Pin by commit SHA
> (see [Documentation](#documentation)) and expect breaking changes.

shipmate is a set of GitHub Actions composite actions and supporting scripts
that orchestrate infrastructure-as-code delivery using the Terramate CLI and
OpenTofu. There is no server, no database, and no long-running service:
everything shipmate does happens inside a GitHub Actions workflow run,
reading and writing state through GitHub's own primitives (Environments,
caches, checks, PR comments) and the Terramate/OpenTofu CLIs. When the
workflow run ends, shipmate's job ends with it.

Consuming repositories pin every shipmate action **by commit SHA**, never by a
tag or branch name: a commit SHA is immutable, so a consumer's workflow behavior
cannot change underneath it without an explicit, reviewed bump. shipmate
publishes a GitHub Release per release SHA, which is what lets Dependabot's
`github-actions` ecosystem propose that bump; `shipmate doctor` names any pin
that differs from the latest release. See [`CONTRACT.md`](CONTRACT.md) §Consumption
and [`docs/upgrading.md`](docs/upgrading.md).

## Why setup is not two clicks

shipmate has no server, so **the App's private key lives in your repository** —
and GitHub hands a *repository* secret to any workflow on any branch, including
a workflow file a branch introduces. So the key is not a repository secret: it
lives as an **environment** secret on `shipmate-engine`, whose deployment branch
policy names only the default branch.

**The one extra environment is the price of not operating a service.**

[`docs/github-app.md`](docs/github-app.md) §Key-exposure boundary has the
mechanism; [`docs/hardening.md`](docs/hardening.md) has the full threat model and
what it deliberately does not claim.

## Documentation

| Page | What it is |
| --- | --- |
| [`docs/concepts.md`](docs/concepts.md) | How it works: fan-out, checks, comment-ops, the env/tag model |
| [`CONTRACT.md`](CONTRACT.md) | The spec: check names, env model, tag grammar, pinning |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Contributing |

## Example repositories

Three sample repos exercise shipmate end to end against local state with **zero
cloud credentials**, one per common IaC layout — the best place to see the
workflows wired up:

- [repo-example-stacks](https://github.com/ship-iac/repo-example-stacks) — DRY / dynamic-backend (`TF_VAR_env` / `TF_VAR_region`)
- [repo-example-folders](https://github.com/ship-iac/repo-example-folders) — folder-per-env/region (no injected vars)
- [repo-example-workspaces](https://github.com/ship-iac/repo-example-workspaces) — workspace-per-env (`TF_WORKSPACE`)

A fourth runs on real cloud, and is the only one that needs credentials:

- [repo-example-stacks-aws](https://github.com/ship-iac/repo-example-stacks-aws) — the `stacks` flavor flattened (every stack at the repo root) on AWS: S3 backend with native locking, GitHub OIDC roles, one SSM parameter per stack

## Development

The engine's logic is a few small Python helper scripts under `scripts/` plus the
composite actions under `actions/`. The dev toolchain is [Astral](https://astral.sh)'s:

```bash
uv run ruff check .            # lint (incl. security S rules)
uv run ruff format .           # auto-format  (--check to verify only)
uv run pytest scripts/tests    # unit tests
uv run ty check                # type-check (beta)
```

[`docs/development.md`](docs/development.md) has the repo layout, the testing
model, and how guard tests must be written. See also
[CONTRIBUTING.md](CONTRIBUTING.md).

---

**Trademarks.** Terramate is a trademark of Terramate GmbH; Terraform is a
trademark of HashiCorp; OpenTofu is a project of the Linux Foundation. shipmate
is an independent project and is not affiliated with, endorsed by, or sponsored
by any of them; their marks are used only to identify the tools shipmate works
with.
