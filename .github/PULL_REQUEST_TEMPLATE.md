## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `uv run ruff check .`, `uv run ruff format .`, `uv run pytest scripts/tests`
      and `uv run ty check` are green ([`docs/development.md`](https://github.com/ship-iac/shipmate/blob/main/docs/development.md#toolchain))
- [ ] No `CONTRACT.md` string changes — check names, tag grammar, comment
      grammar — or `CONTRACT.md` is updated in this change
- [ ] Docs updated
- [ ] Any guard test added or changed was mutation-proved: broken, seen to fail,
      reverted ([`docs/development.md`](https://github.com/ship-iac/shipmate/blob/main/docs/development.md#guard-tests-must-be-able-to-fail))
