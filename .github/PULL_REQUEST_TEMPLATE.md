## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `uv run ruff format .` run, then `uv run ruff check .`,
      `uv run ruff format --check .` and `uv run pytest scripts/tests` are green
      (CI runs `--check`), and `uv run ty check` shows nothing new — `ty` is beta and
      non-blocking ([`docs/development.md`](https://github.com/ship-iac/shipmate/blob/main/docs/development.md#toolchain))
- [ ] No `CONTRACT.md` string changes — check names, tag grammar, comment
      grammar — or `CONTRACT.md` is updated in this change
- [ ] Docs updated
- [ ] Any guard test added or changed was mutation-proved: broken, seen to fail,
      reverted ([`docs/development.md`](https://github.com/ship-iac/shipmate/blob/main/docs/development.md#guard-tests-must-be-able-to-fail))
