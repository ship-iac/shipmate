### shipmate apply

⚪ 1 applied, 1 held (base changed), 2 environments pending at [0123456](https://github.com/acme/repo/commit/0123456789abcdef0123456789abcdef01234567) in [run #7](https://github.com/acme/repo/actions/runs/42)

dispatched as an `apply_mode: auto` apply, after an autoplan triggered by @<!-- -->`k3np`

#### dev-eu: 1 cell, 1 applied

🟢 stacks/app: applied <a href="https://github.com/acme/repo/actions/runs/42">logs</a>

🟡 dev: left pending (not `apply_mode: auto`), comment `shipmate apply`
⚪ prod: skipped, waits on dev (comment `shipmate apply`, which applies them in order); dev-eu (a cell of it does not apply this run; see its line below)
⚪ held, the base changed these stacks after this branch left it, so their plans would revert that change: stacks/db / dev-eu. Update the branch from the base and push.

Comment `shipmate help` for the available commands.