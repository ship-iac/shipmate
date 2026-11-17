"""Guards how each cell finds, restores and saves local state.

The state path is what `scripts/state-path` read from the stack's `tofu init` record, never a
caller input. An empty path means a backend other than `local` owns state, so every
`actions/state` step is skipped: a restore that still ran would fail the cell, because no cache
entry exists. `actions/cell-init` locates and restores state for every cell; no cell saves:
apply-cell hands its sealed state to the wave's saver job (test_apply_cell_state_save.py). The
cell's call step is pinned whole in test_provider_cache.py.

`actions/state` decrypts every restore that matched an entry with SHIPMATE_ENCRYPTION_KEY, and an
empty encryption key refuses a restore. A save stores a file the caller already sealed, and a
lookup only reads the exact key; neither reads the key.

Whole parsed steps compared to hand-written constants, never substrings: an inverted operator, a
dropped `always()` or a condition moved into a comment must fail these guards.
"""

import os

from _loader import action_steps, bash_only, github_outputs, local_action, run_step, step_by

_INIT_ACTION = "cell-init"

#: Inside `terramate run`, as init is, so the record is read with the TF_DATA_DIR and
#: TF_WORKSPACE OpenTofu saw. A bare `python3` would read `.terraform` from the repository root.
_LOCATE = {
    "name": "Locate state",
    "id": "locate-state",
    "if": "${{ inputs.backend != 'false' }}",
    "shell": "bash",
    "env": {"STACK": "${{ inputs.stack }}"},
    "run": (
        "terramate run --quiet --disable-safeguards=git-out-of-sync --no-recursive -C "
        '"$STACK" -- python3 "$GITHUB_ACTION_PATH/../../scripts/state-path"'
    ),
}

#: No status function: the implied success() skips the restore after a failed init or locate,
#: which would otherwise restore state for a stack init could not set up.
_RESTORE = {
    "name": "Restore state",
    "id": "restore-state",
    "if": "${{ steps.locate-state.outputs.path != '' }}",
    "uses": local_action("state"),
    "with": {
        "stack-slug": "${{ inputs.slug }}",
        "env": "${{ inputs.env }}",
        "mode": "restore",
        "path": "${{ steps.locate-state.outputs.path }}",
        "encryption-key": "${{ inputs.encryption-key }}",
    },
}


def test_locate_state_reads_the_init_record_inside_terramate_run():
    """A one-line `run:` fails the step on a non-zero init, so the default `success()` condition
    skips it; the absent `if:` is part of the whole step. Mutation: run
    `python3 .../scripts/state-path` bare, outside `terramate run`."""
    assert step_by(_INIT_ACTION, name="Locate state") == _LOCATE


def test_restore_state_uses_the_located_path():
    """Mutations: `if: always()` on the restore; `stack-slug: ${{ inputs.stack }}`; drop the
    `encryption-key` line, and every restore of local state refuses."""
    assert step_by(_INIT_ACTION, name="Restore state") == _RESTORE


_REFUSAL = (
    "::error::shipmate/actions/state: SHIPMATE_ENCRYPTION_KEY is empty. Cached local state is "
    "encrypted with it, so every restore needs it. Set the secret and forward it to the "
    "shipmate workflows."
)

_VALIDATE_RUN = (
    'case "$MODE" in\n'
    "  restore|save|lookup) ;;\n"
    "  *) echo \"::error::shipmate/actions/state: 'mode' must be 'restore', 'save' or 'lookup', "
    "got '$MODE'\"; exit 1 ;;\n"
    "esac\n"
    'if [ "$MODE" = restore ] && [ -z "$ENCRYPTION_KEY" ]; then\n'
    f'  echo "{_REFUSAL}"\n'
    "  exit 1\n"
    "fi\n"
)

_KEY = "${{ steps.key.outputs.key }}"

_KEY_RUN = (
    'attempt="${ATTEMPT:-$GITHUB_RUN_ATTEMPT}"\n'
    '[[ "$attempt" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "::error::shipmate/actions/state: '
    "'attempt' must be 1 to 4 digits with no leading zero, got '$attempt'\"; exit 1; }\n"
    '[ -z "$SUFFIX" ] || [ "$SUFFIX" = -r1 ] || { echo "::error::shipmate/actions/state: '
    "'key-suffix' must be empty or '-r1', got '$SUFFIX'\"; exit 1; }\n"
    'echo "prefix=state/$SLUG/$ENV_NAME/" >> "$GITHUB_OUTPUT"\n'
    'echo "key=state/$SLUG/$ENV_NAME/$GITHUB_RUN_ID-$attempt$SUFFIX" >> "$GITHUB_OUTPUT"\n'
)


def _retry(n, prev, seconds):
    """The sleep and lookup that run only when lookup `prev` ran and missed."""
    return [
        {
            "name": f"Wait before lookup {n}",
            "id": f"wait{n}",
            "if": (
                f"${{{{ steps.{prev}.outputs.cache-hit != 'true' "
                f"&& steps.{prev}.outcome == 'success' }}}}"
            ),
            "shell": "bash",
            "run": f"sleep {seconds}",
        },
        {
            "name": f"Look up the saved entry, try {n}",
            "id": f"verify{n}",
            "if": f"${{{{ steps.wait{n}.outcome == 'success' }}}}",
            "uses": "actions/cache/restore",
            "with": {"path": "${{ inputs.path }}", "key": _KEY, "lookup-only": "true"},
        },
    ]


#: The whole step list, `uses:` refs cut. No step carries `continue-on-error` or a status
#: function, so a refused decrypt fails the action and cell-init's `restore-outcome` is not
#: `success`; a refused seal check skips `Save state` and every lookup. No step encrypts.
_STATE_STEPS = [
    {
        "name": "Validate mode",
        "shell": "bash",
        "env": {"MODE": "${{ inputs.mode }}", "ENCRYPTION_KEY": "${{ inputs.encryption-key }}"},
        "run": _VALIDATE_RUN,
    },
    {
        "name": "State cache key",
        "id": "key",
        "shell": "bash",
        "env": {
            "SLUG": "${{ inputs.stack-slug }}",
            "ENV_NAME": "${{ inputs.env }}",
            "ATTEMPT": "${{ inputs.attempt }}",
            "SUFFIX": "${{ inputs.key-suffix }}",
        },
        "run": _KEY_RUN,
    },
    {
        "name": "Restore state",
        "id": "restore",
        "if": "${{ inputs.mode == 'restore' }}",
        "uses": "actions/cache/restore",
        "with": {
            "path": "${{ inputs.path }}",
            "key": _KEY,
            "restore-keys": "${{ steps.key.outputs.prefix }}",
        },
    },
    {
        "name": "Decrypt state",
        "if": "${{ inputs.mode == 'restore' && steps.restore.outputs.cache-matched-key != '' }}",
        "shell": "bash",
        "env": {
            "ENCRYPTION_KEY": "${{ inputs.encryption-key }}",
            "STATE_PATH": "${{ inputs.path }}",
            "KEY": "${{ steps.restore.outputs.cache-matched-key }}",
        },
        "run": (
            'python3 "$GITHUB_ACTION_PATH/../../scripts/plan-crypt" decrypt-state '
            '"$STATE_PATH" "$KEY"'
        ),
    },
    {
        "name": "Check the state file is sealed",
        "id": "sealed",
        "if": "${{ inputs.mode == 'save' }}",
        "shell": "bash",
        "env": {"STATE_PATH": "${{ inputs.path }}"},
        "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/plan-crypt" sealed "$STATE_PATH"',
    },
    {
        "name": "Save state",
        "if": "${{ inputs.mode == 'save' }}",
        "uses": "actions/cache/save",
        "with": {"path": "${{ inputs.path }}", "key": _KEY},
    },
    {
        "name": "Look up the saved entry",
        "id": "verify",
        "if": "${{ inputs.mode != 'restore' }}",
        "uses": "actions/cache/restore",
        "with": {"path": "${{ inputs.path }}", "key": _KEY, "lookup-only": "true"},
    },
    *_retry(2, "verify", "3"),
    *_retry(3, "verify2", "7"),
    *_retry(4, "verify3", "10"),
    *_retry(5, "verify4", "10"),
]


def test_the_state_action_is_exactly_these_steps():
    """Mutations: delete `Decrypt state`; invert the decrypt `if:`'s `!=` to `==`; add
    `continue-on-error: true` to `Decrypt state`, and a refused decrypt hands tofu the
    ciphertext; delete the seal check, and plaintext is saved; restore by exact key only (drop
    `restore-keys`), and no `-r1` or earlier entry is restored; move
    `Look up the saved entry` above `Save state`, and it finds nothing this run saved; delete
    `Look up the saved entry, try 3`; drop `&& steps.verify2.outcome == 'success'` from
    `Wait before lookup 3`, and it runs after a hit in `verify`, whose skipped successor
    `verify2` reads as a miss."""
    steps = [
        {**s, "uses": s["uses"].split("@")[0]} if "uses" in s else s for s in action_steps("state")
    ]
    assert steps == _STATE_STEPS


def _validate(tmp_path, mode, key):
    body = step_by("state", name="Validate mode")["run"]
    return run_step(tmp_path, body, {**os.environ, "MODE": mode, "ENCRYPTION_KEY": key})


@bash_only
def test_an_empty_encryption_key_refuses_a_restore(tmp_path):
    """Mutation: delete the encryption key check."""
    r = _validate(tmp_path, "restore", "")
    assert (r.returncode, r.stdout, r.stderr) == (1, _REFUSAL + "\n", "")


@bash_only
def test_save_and_lookup_need_no_encryption_key(tmp_path):
    """They handle ciphertext only, and the saver binds no environment that holds the key.
    Mutation: drop `[ "$MODE" = restore ] &&` from the key check."""
    for mode in ("save", "lookup"):
        r = _validate(tmp_path, mode, "")
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), mode


@bash_only
def test_a_set_encryption_key_passes_validation(tmp_path):
    """Mutation: `[ -n "$ENCRYPTION_KEY" ]` in place of `-z`, a check that refuses every caller."""
    r = _validate(tmp_path, "restore", "secret")
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def _key(tmp_path, attempt, suffix):
    out = tmp_path / f"out-{attempt}-{suffix}"
    out.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "SLUG": "s",
        "ENV_NAME": "e",
        "ATTEMPT": attempt,
        "SUFFIX": suffix,
        "GITHUB_RUN_ID": "77",
        "GITHUB_RUN_ATTEMPT": "3",
        "GITHUB_OUTPUT": str(out),
    }
    r = run_step(tmp_path, step_by("state", id="key")["run"], env)
    return r.returncode, github_outputs(out)


@bash_only
def test_the_key_takes_the_attempt_and_the_suffix(tmp_path):
    """Mutations: default `attempt` to `1`, not `GITHUB_RUN_ATTEMPT`; drop `$SUFFIX` from the
    key."""
    cases = [(a, x) for a in ("", "2") for x in ("", "-r1")]
    assert {c: _key(tmp_path, *c) for c in cases} == {
        (a, x): (0, {"prefix": "state/s/e/", "key": f"state/s/e/77-{a or '3'}{x}"})
        for a, x in cases
    }


@bash_only
def test_the_key_refuses_a_bad_attempt_or_suffix(tmp_path):
    """Mutations: accept any suffix (drop the suffix test); drop the attempt pattern."""
    for attempt, suffix in (("02", ""), ("x", ""), ("1", "-r2"), ("12345", "")):
        assert _key(tmp_path, attempt, suffix) == (1, {}), (attempt, suffix)


def test_only_save_mode_saves():
    """`lookup` never saves: an earlier attempt's late save could roll back a newer entry.
    Every `actions/cache/save` step's `if:` whole. Mutation: `if: always()` on the save step."""
    saves = [
        s.get("if") for s in action_steps("state") if "actions/cache/save@" in s.get("uses", "")
    ]
    assert saves == ["${{ inputs.mode == 'save' }}"]
