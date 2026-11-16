### shipmate apply dev-eu

⚪ 1 applied, 1 waiting, 1 locked at [0123456](https://github.com/acme/repo/commit/0123456789abcdef0123456789abcdef01234567) in [run #7](https://github.com/acme/repo/actions/runs/42)

#### dev-eu: 1 cell, 1 applied

🟢 stacks/dns: applied <a href="https://github.com/acme/repo/actions/runs/42">logs</a>

🔒 locked by #12: stacks/db2 / dev-eu. Wait for #12 to merge or close, then re-plan. If #12 has already merged or closed, comment `shipmate unlock dev-eu --evict` once #12's apply, auto and deploy runs have ended.
⚪ stacks/app / dev-eu: waits on stacks/db / dev-us, not applied yet; apply it first with `shipmate apply dev-us`

Comment `shipmate help` for the available commands.