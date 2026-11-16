### shipmate apply

⚪ 2 applied, 1 waiting, 1 locked, 1 held (base changed) at [0123456](https://github.com/acme/repo/commit/0123456789abcdef0123456789abcdef01234567) in [run #7](https://github.com/acme/repo/actions/runs/42)

#### qa: 1 cell, 1 applied

🟢 stacks/app: applied <a href="https://github.com/acme/repo/actions/runs/42">logs</a>

#### lck: 1 cell, 1 applied

🟢 stacks/db: applied <a href="https://github.com/acme/repo/actions/runs/42">logs</a>

🟡 tgt: left pending (targeted), comment `shipmate apply tgt`
⚪ s1: skipped, waits on tgt (apply it first with `shipmate apply tgt`)
⚪ s3: skipped, waits on lck (a cell of it does not apply this run; see its line below); dev (comment `shipmate apply`, which applies them in order)
⚪ s5: skipped, waits on ovl (a cell of it does not apply this run; see its line below); wt (a cell of it does not apply this run; see its line below)
⚪ rev: held, the review state does not permit applying
⚪ both: held, the review state does not permit applying; once it clears, comment `shipmate apply both`
held: get an approving review, or resolve or dismiss a requested-changes review. The run log's apply-all-detect notice names the decision seen.
🔒 locked by #7: stacks/app / lck. Wait for #7 to merge or close, then re-plan. If #7 has already merged or closed, comment `shipmate unlock lck --evict` once #7's apply, auto and deploy runs have ended.
⚪ held, the base changed these stacks after this branch left it, so their plans would revert that change: stacks/o / ovl. Update the branch from the base and push.
⚪ stacks/w / wt: waits on stacks/t / tgt, not applied yet; apply it first with `shipmate apply tgt`

Comment `shipmate help` for the available commands.