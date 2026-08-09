# Contributing to shipmate

shipmate is in early development — issues, ideas, and pull requests are welcome.

## Development setup

shipmate's logic is a few Python helper scripts under `scripts/` (they run as
GitHub Actions steps, so they're executable and have no `.py` extension) plus
composite actions under `actions/`. The dev toolchain is
[Astral](https://astral.sh)'s **uv + ruff + ty**; [`docs/development.md`](docs/development.md)
has the repo layout, the commands to run before opening a PR, and how the tests
are meant to work.

## Reporting a bug

Open an issue from the Issues tab — **Bug report** for something the engine gets
wrong, **Feature request** for something it cannot do. For a security
vulnerability use the Security tab instead, not an issue (see
[SECURITY.md](SECURITY.md)).

## Guidelines

- **Read `CONTRACT.md` first.** Check names, the environment model, tag grammar,
  and SHA-pinning are a contract that other parts of the system parse — don't
  change those strings casually.
- **Keep author-/user-controlled values out of inline shell.** Pass them via
  `env:` and reference them as shell variables; a test enforces that no `run:`
  block contains a `${{ }}` expression.
- **Fix lint findings rather than suppressing them.** Use `# noqa` / disables
  only with a written rationale (see the one `S603` in `scripts/build-matrix`).
- **Flavor-specific needs belong in shipmate**, as an action input or feature —
  never as patch code in a consuming repo.

Runnable end-to-end examples live in the sample repositories:
[repo-example-stacks](https://github.com/ship-iac/repo-example-stacks),
[repo-example-folders](https://github.com/ship-iac/repo-example-folders),
[repo-example-workspaces](https://github.com/ship-iac/repo-example-workspaces)
and [repo-example-stacks-aws](https://github.com/ship-iac/repo-example-stacks-aws),
the flat-layout one that runs against real AWS.

## License

By contributing you agree that your contributions are licensed under the
Apache License 2.0 (see [LICENSE](LICENSE)).
