# Contributing to shipmate

shipmate is in early development — issues, ideas, and pull requests are welcome.

## Development setup

shipmate's logic is a few Python helper scripts under `scripts/` (they run as
GitHub Actions steps, so they're executable and have no `.py` extension) plus
composite actions under `actions/`. The dev toolchain is
[Astral](https://astral.sh)'s **uv + ruff + ty**; see the **Development**
section of the [README](README.md) for details.

`dev/` holds tooling you run by hand, never from a workflow — nothing in
`actions/` or `.github/workflows/` references it, and it adds no action input.
It exists because the engine pins its own actions by commit SHA (see
`CONTRACT.md`), so those pins need maintaining:

- `pinrefs.py` — the shared model: finds the pins, works out what each one
  actually runs, and diffs a pin against a baseline commit.
- `pin_status.py` — is a given commit safe to pin?
- `repin_internal.py` — bump the engine's own stale pins.
- `repin_consumer.py` — re-pin a consuming repo, refusing an unsafe target.

`docs/releasing.md` is the runbook that drives them. `pyproject.toml` puts
`dev/` on the pytest `pythonpath`, which is how the tests under `scripts/tests/`
import `pinrefs`.

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
[repo-example-stacks](https://github.com/ship-iac/repo-example-stacks),
[repo-example-folders](https://github.com/ship-iac/repo-example-folders),
[repo-example-workspaces](https://github.com/ship-iac/repo-example-workspaces).

## License

By contributing you agree that your contributions are licensed under the
Apache License 2.0 (see [LICENSE](LICENSE)).
