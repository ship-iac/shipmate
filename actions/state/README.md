# shipmate state

Persists per-(stack × environment) OpenTofu local state across CI runs, using
`actions/cache` as the backing store instead of a remote backend.

**`path` is required and has no default.** The cell actions pass the path
`scripts/state-path` derived from the stack's `tofu init` record: the local
backend's `path` for the default workspace, or
`<workspace_dir>/<workspace>/terraform.tfstate` for a named one, relative to
the repository root. A backend other than `local` yields no path, and the cell
skips the restore. `actions/cache` keys an entry's version on the path string,
so the saver passes the restore's exact string (`./terraform.tfstate` for the
root stack).

Cache keys are scoped per stack and env and delimited with `/` so one env
name cannot prefix-match another. The key is
`state/<stack-slug>/<env>/<run_id>-<attempt><key-suffix>`: `attempt` defaults
to the job's `run_attempt` and is otherwise 1 to 4 digits with no leading zero;
`key-suffix` is empty or `-r1`, and anything else refuses. A restore tries the
exact key first, then the `state/<stack-slug>/<env>/` prefix as `restore-keys`,
the usual cross-run case, so a run picks up the most recent entry for that
stack × env, a `-r1` entry included. The attempt is in the key so a GitHub
"re-run" writes a fresh key rather than colliding with the immutable original,
which `actions/cache/save` would refuse to overwrite.

Invalid `mode` fails loud: the action validates `mode` is exactly `restore`,
`save` or `lookup` before anything else, so a typo cannot silently skip every
step.

**State is encrypted at rest with `SHIPMATE_ENCRYPTION_KEY`** through
`scripts/plan-crypt` (the same cipher as the plan artifact), bound to the key's
`state/<stack-slug>/<env>/` prefix. The apply cell seals the file; this action
never encrypts.

- `restore`: needs `encryption-key`; empty refuses before any cache call. A
  matched entry is authenticated, then decrypted in place, before tofu reads
  it. A cache miss restores nothing and runs no decrypt. An entry that fails
  authentication (another encryption key, a corrupt, truncated or altered
  entry, or one copied under another stack's or environment's key) fails it
  naming the matched cache key and no command: `docs/troubleshooting.md` tells
  the causes apart. An openssl or disk failure on the runner fails it too;
  re-run the job instead.
- `save`: saves the file at `path` as it is. A file that is absent, shorter
  than a sealed file, or without the cipher's header refuses before the save.
  `actions/cache/save` warns and succeeds when the cache stores nothing, so a
  lookup of the exact key, with no `restore-keys`, verifies the save; after a
  miss it looks again up to four times over about 30 s.
- `lookup`: runs the same exact-key lookups and never saves.
- `save` and `lookup` read no encryption key. The `saved` output is `true` only
  when a lookup finds the entry, and `false` otherwise; the action does not
  fail on a miss. The saver job reads `saved`.

**The cache is not durable storage**: GitHub evicts entries by last access,
those unused past the repository's retention period (7 days by default) and the
least recently used once its caches exceed the size limit. A restore accesses
only the newest entry under a prefix, so older entries usually go first; once
the newest is evicted, a restore usually finds none and the next plan
re-creates every resource the stack holds: harmless for the local-backend samples' `random_pet`
(and `terraform_data` in `repo-example-stacks`), a duplicate or collision for a
real resource. A state the saver could not save fails its saver job and the
wave's verdict: `docs/troubleshooting.md` §`The apply step ran and its state was
not saved`. The small window for concurrent
read/modify/write races is closed by the per-stack x environment `concurrency`
group that serializes applies and unlocks; `actions/state` itself makes no
locking guarantees.
