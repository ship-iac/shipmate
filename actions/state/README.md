# shipmate state

Persists per-(stack × environment) OpenTofu local state (a `path` directory,
default `.state`) across CI runs, using `actions/cache` as the backing store
instead of a remote backend. Cache keys are scoped per stack and env:
`state-<stack-slug>-<env>-<run_id>` on save, restored via the exact-match key
first and, failing that, the `state-<stack-slug>-<env>-` prefix as
`restore-keys` (so a run picks up the most recent state for that
stack × env even if the run id differs). **State loss is acceptable**: this
is a cache, not a source of truth, and GitHub cache entries can be evicted at
any time. Sample-repo stacks use `null_resource`s that simply re-create on a
cache miss, and the small window for concurrent read/modify/write races is
closed later by a per-env `concurrency` group that serializes applies —
`actions/state` itself makes no locking guarantees.

## Usage

Call once with `mode: restore` before plan/apply, and once with `mode: save`
after apply:

```yaml
- name: Restore state
  uses: ./actions/state
  with:
    stack-slug: ${{ matrix.stack-slug }}
    env: dev-eu
    mode: restore

# ... run `tofu plan` / `tofu apply` against ${{ inputs.path }} (default .state) ...

- name: Save state
  uses: ./actions/state
  with:
    stack-slug: ${{ matrix.stack-slug }}
    env: dev-eu
    mode: save
```
