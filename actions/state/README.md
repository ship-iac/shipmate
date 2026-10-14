# shipmate state

Persists per-(stack × environment) OpenTofu local state across CI runs, using
`actions/cache` as the backing store instead of a remote backend.

**`path` is required and has no default.** The cell actions pass the path
`scripts/state-path` derived from the stack's `tofu init` record: the local
backend's `path` for the default workspace, or
`<workspace_dir>/<workspace>/terraform.tfstate` for a named one, relative to
the repository root. A backend other than `local` yields no path, and the cell
skips both steps.

Cache keys are scoped per stack and env and delimited with `/` so one env
name cannot prefix-match another. The save key is
`state/<stack-slug>/<env>/<run_id>-<run_attempt>`. A restore tries that exact
key first, then the `state/<stack-slug>/<env>/` prefix as `restore-keys` — the
usual cross-run case — so a run picks up the most recent state for that
stack × env. `run_attempt` is in the key so a GitHub "re-run" writes a fresh
key rather than colliding with the immutable original, which
`actions/cache/save` would refuse to overwrite.

Invalid `mode` fails loud: the action validates `mode` is exactly `restore`
or `save` before anything else, so a typo cannot silently skip both steps.

**State is encrypted at rest with `passphrase`**, the consumer's
`SHIPMATE_PLAN_PASSPHRASE`, through `scripts/plan-crypt` (the same cipher as
the plan artifact). An empty `passphrase` refuses both modes before any cache
call.

- Save: the file is encrypted in place, then saved. An absent or 0-byte file is
  not saved; the step prints a notice instead. `actions/cache/save` warns and
  succeeds when the cache stores nothing, so a lookup of the exact save key, with
  no `restore-keys`, verifies the save: the `saved` output is `true` only when it
  finds the entry, and empty or `false` otherwise. The action does not fail on a
  miss; `apply-cell` reads `saved` and fails the cell when an apply ran.
- Restore: a matched entry is authenticated, then decrypted in place, before
  tofu reads it. A cache miss restores nothing and runs no decrypt. An entry
  saved unencrypted or by an earlier engine release fails the restore with the
  `gh cache` command that deletes every entry under its prefix. An entry that
  fails authentication (another passphrase, a corrupt, truncated or altered
  entry, or one copied under another stack's or environment's key) fails it
  naming the matched cache key and no command: `docs/troubleshooting.md` tells
  the causes apart. An openssl or disk failure on the runner fails it too;
  re-run the job instead. A failed restore makes the cell skip its state save,
  so the entry is never overwritten.
- The save leaves ciphertext in the workspace, so no step after it reads the
  state file.

**State loss is acceptable**: this is a cache, not a source of truth, and
GitHub cache entries can be evicted at any time. Sample-repo stacks use
`null_resource`/`random_pet`, which re-create on a cache miss. For state lost
after an apply, follow `docs/troubleshooting.md`
§`The apply step ran and its state was not saved`. The small window
for concurrent read/modify/write races is closed by the per-stack x environment
`concurrency` group that serializes applies and unlocks; `actions/state` itself makes no
locking guarantees.
