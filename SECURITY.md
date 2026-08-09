# Security Policy

shipmate runs inside consumers' GitHub Actions CI and handles OpenTofu state and
short-lived credentials, so security reports are taken seriously.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's
private vulnerability reporting instead:

1. Open the **Security** tab of this repository.
2. Click **Report a vulnerability**.

That opens a private advisory visible only to the maintainers. We aim to
acknowledge reports within a few days.

## Scope

shipmate invokes the Terramate and OpenTofu CLIs and the GitHub API; it stores
no long-lived secrets of its own. In-scope concerns include GitHub Actions
script injection, handling of `TF_VAR_*` and state, and the integrity of the
plan→apply fingerprint. Vulnerabilities in Terramate, OpenTofu, or GitHub
Actions themselves should be reported to those projects.

## Hardening guidance

- Pin every shipmate action **by full commit SHA** (see `CONTRACT.md`) so
  upstream changes cannot reach your pipelines unreviewed.
- shipmate is pre-1.0: action inputs, check names, and the tag and comment
  grammars may change between commits. Pin by commit SHA and review each bump
  rather than taking it unread.
