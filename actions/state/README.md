# shipmate state

Persists per-(stack × environment) OpenTofu local state across CI runs, using
`actions/cache` as the backing store instead of a remote backend.

**`path` is required and has no default** — pass the state location your
flavor's backend writes to, or nothing is persisted:

| Flavor | `path` to pass |
|--------|----------------|
| stacks (env-agnostic, `.state/<env>/<region>/…`) | `.state` |
| folders (state beside each leaf) | `<leaf-dir>/terraform.tfstate` |
| workspaces (`terraform.tfstate.d/<ws>`) | `terraform.tfstate.d` |

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

**State loss is acceptable**: this is a cache, not a source of truth, and
GitHub cache entries can be evicted at any time. Sample-repo stacks use
`null_resource`/`random_pet`, which re-create on a cache miss. The small window
for concurrent read/modify/write races is closed later by a per-env
`concurrency` group that serializes applies; `actions/state` itself makes no
locking guarantees.

## Usage

Call once with `mode: restore` before plan/apply, and once with `mode: save`
after apply. Pass the same `path` both times:

```yaml
- name: Restore state
  uses: ./actions/state
  with:
    stack-slug: ${{ matrix.stack-slug }}
    env: dev-eu
    mode: restore
    path: .state          # stacks flavor; see the table above for others

# ... run `tofu plan` / `tofu apply` (state lives under the path above) ...

- name: Save state
  uses: ./actions/state
  with:
    stack-slug: ${{ matrix.stack-slug }}
    env: dev-eu
    mode: save
    path: .state
```
