# Development

Repository layout, toolchain, and testing model. Contribution etiquette and
licensing: [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Repo structure

- `actions/` — the composite actions consumers reference.
- `scripts/` — the Python helpers behind those actions. They run as GitHub
  Actions steps, so they are executable and have no `.py` extension.
  `scripts/tests/` holds their unit tests.
- `dev/` — maintainer tooling you run by hand, never from a workflow. Nothing in
  `actions/` or `.github/workflows/` references it and it adds no action input.
  It exists because the engine pins its own actions by commit SHA (see
  [`CONTRACT.md`](../CONTRACT.md)), so those pins need maintaining:
  - `pinrefs.py` — the shared model: finds the pins, works out what each one
    runs, and diffs a pin against a baseline commit.
  - `pin_status.py` — is a given commit safe to pin?
  - `repin_internal.py` — bump the engine's own stale pins.
  - `repin_consumer.py` — re-pin a consuming repo, refusing an unsafe target.

  [`releasing.md`](releasing.md) is the runbook that drives them.
  `pyproject.toml` puts `dev/` on the pytest `pythonpath`. That is how the
  tests under `scripts/tests/` import `pinrefs`.
- `app/` — the GitHub App manifest.
- `docs/` — these pages.

## Toolchain

The dev toolchain is [Astral](https://astral.sh)'s uv + ruff + ty, with
pytest. uv manages the dev environment and the pinned tool versions through
`pyproject.toml` and `uv.lock`. It is for tooling alone: shipmate ships no
importable package (`[tool.uv] package = false`) and has no runtime
dependencies — the helper scripts are standard library only.

Get this green before opening a PR:

```bash
uv run ruff check .          # lint (incl. flake8-bandit security S rules)
uv run ruff format .         # format (CI runs --check)
uv run pytest scripts/tests  # unit tests + shellcheck of every action run block
uv run ty check              # type-check (beta, non-blocking)
```

CI runs the same checks plus actionlint on the workflow files for every pull
request. Fix lint findings rather than suppressing them; use `# noqa` only with
a written rationale (see the one `S603` in `scripts/build-matrix`).

## Writing

Text exists where code cannot carry the meaning. Two regimes:

- Contract: module docstrings of `scripts/*` state the CLI contract (env vars, inputs, outputs, exit codes); public functions state behavior, errors, and invariants. As full as the contract needs.
- Implementation: a comment is a last resort. Keep one only for a non-obvious why, a workaround (name the external cause), a consequence a maintainer could miss, the evidence behind a value, a coupling that fails silently, or a do-not-refactor warning. Three lines maximum. Longer rationale goes in the docstring, `CONTRACT.md`, or the pull request.

What earns a comment in this repository: a GitHub Actions platform constraint (outputs cannot contain `-`; a failed `if: always()` step still skips later `success()` steps), two sites that must stay byte-identical, a security property, a fail-safe ordering rule.

Never: restate the code, narrate the edit, reference a review or conversation, list callers, add section or file banners, keep commented-out code, use a `TODO` without an issue (`TODO #123: reason`).

Test docstrings state what the test pins and the mutation that reddens it. Nothing else.

Commits: Conventional Commits, subject imperative and at most 72 characters, scope = directory or action name, body only when the subject cannot carry the why. Pull requests: the template's "What and why" in one or two sentences.

## Testing model

`scripts/tests/` holds the unit tests for the helper scripts. The sample repos
are the end-to-end harness — a real plan → apply → deploy cycle cannot run from
this repository alone.

Two traps when driving a sample repo:

- **A PR that only touches `.github/workflows/` plans zero cells.** Change
  detection is `terramate list --changed`, so a re-pin-only PR fans out to
  nothing and its gate goes green over no work. It proves the pins parse and
  nothing about the engine.
- **Bump `global.version` to get a real cycle in `repo-example-stacks`**, then
  run `terramate generate` and `terramate fmt`. The version feeds the stacks'
  `app_version` variable and its `triggers_replace`, so bumping it moves every
  stack. The consumer's `detect` job runs `terramate fmt --check` plus
  `terramate generate --detailed-exit-code`. Both fail the run on bad
  formatting or stale codegen before any cell starts.

## Guard tests must be able to fail

Many tests here are guards: they pin an invariant in a workflow or action
file rather than exercise a function. A guard that cannot fail is worse than no
guard, because the next reader trusts it. Once, a four-way sabotage of
`.github/workflows/summary.yml` — all three trust guards inverted, `environment:`
commented out, the draft-skip deleted — left the suite byte-identical to green.

- **Assert parsed values, not substrings of file text.** `yaml.safe_load` the
  file, take the job or step, and compare whole expressions. A substring
  assertion is satisfied by the same words appearing in a *comment*, and by an
  inverted operator (`==`→`!=`, `&&`→`||`) that keeps the text intact.
  `scripts/tests/test_apply_cell_failsafe_wiring_guard.py` is the model to copy.
- **Mutation-prove every guard.** Break the thing the test names, run it, watch
  it fail, revert, watch it pass. A guard nobody has seen fail is unverified,
  whatever its name says.
- **Prove one break per promise the name and docstring make.** Mutation-proving
  only covers the mutation you thought of. A test here whose docstring claimed
  to guard prose in three files, with a body of `assert len(doctor.PROBES) == 9`
  (since fixed), was mutation-proved against `PROBES` — the one thing it could
  detect — and shipped through three review passes; reverting any of the prose
  sites left it green. Read your test's name and docstring as a list of claims
  and break each one. A guard whose proof is narrower than its name is the
  "cannot fail" failure wearing a disguise, and it survives review *because* a
  proof exists.
- **When the guarded thing is one fully known value, compare the whole value
  against a hand-written constant.** Checking a part — a substring, a token, a
  window, an operator denylist — is right about the example you were shown and
  leaves the class open. The constant must be hand-written, never derived from
  the file it checks, because a derived vector passes whatever the file says.
  And use one selector per property: two selectors for the same property will
  disagree eventually. This is less code, not more discipline:
  `scripts/tests/test_engine_owns_tofu_invocation.py` adopted the whole-value
  form — each cell's entire `terramate run` line, tokenized, against a
  hand-written constant — as a net deletion of a helper, three tests and a
  denylist, after ten rounds in which every fix checked a part and reasoned
  about the rest.
  `test_current_failsafe_set_is_exactly_the_four_known_ids` in
  `scripts/tests/test_apply_cell_failsafe_wiring_guard.py` is the shape. Its
  module docstring argues against hardcoded lists for the *structural* guards
  around it; this one is a deliberate tripwire.
- **Write the guard's threat model down, and stop when it is covered.** One
  guard here took ten review rounds, most of them defending against a deliberate
  hostile edit to an engine file that is SHA-pinned and reviewed on every PR — an
  author who can make that edit does not need the evasion. The realistic failure
  is accidental regression: a reverted line, a dropped flag, a commented-out
  invocation. State that in the module docstring, along with what the guard
  deliberately does not cover. Without it, every "here is an evasion" review
  finding reads as actionable and the hardening loop never ends.
  `scripts/tests/test_docs_yaml_parses.py` is a short example: its docstring
  names the failure it expects and the ceiling it accepts.
