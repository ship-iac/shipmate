# Contributing to shipmate

shipmate is in early development — issues, ideas, and pull requests are welcome.

## Development setup

shipmate's logic is a few Python helper scripts under `scripts/` (they run as
GitHub Actions steps, so they're executable and have no `.py` extension) plus
composite actions under `actions/`. The dev toolchain is
[Astral](https://astral.sh)'s **uv + ruff + ty**; see the **Development**
section of the [README](README.md) for details.

Get the gate green before opening a PR:

```bash
uv run ruff check .          # lint (incl. flake8-bandit security S rules)
uv run ruff format .         # format (CI runs --check)
uv run pytest scripts/tests  # unit tests + shellcheck of every action run block
uv run ty check              # type-check (beta, non-blocking)
```

CI runs the same checks plus **actionlint** on the workflow files for every
pull request.

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
[repo-example-stacks](https://github.com/shipmate-iac/repo-example-stacks),
[repo-example-folders](https://github.com/shipmate-iac/repo-example-folders),
[repo-example-workspaces](https://github.com/shipmate-iac/repo-example-workspaces).

## License

By contributing you agree that your contributions are licensed under the
Apache License 2.0 (see [LICENSE](LICENSE)).
