"""Guards how each cell finds, restores and saves local state.

The state path is what `scripts/state-path` read from the stack's `tofu init` record, never a
caller input. An empty path means a backend other than `local` owns state, so every
`actions/state` step is skipped: a restore that still ran would fail the cell, because no cache
entry exists. `actions/cell-init` locates and restores state for every cell; only apply-cell
saves, through `cell-init`'s outputs. The cell's call step is pinned whole in
test_provider_cache.py.

`actions/state` encrypts the file before every save and decrypts it after every restore that
matched an entry, with SHIPMATE_PLAN_PASSPHRASE; an empty passphrase refuses both modes.

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
    "shell": "bash",
    "env": {"STACK": "${{ inputs.stack }}"},
    "run": (
        "terramate run --disable-safeguards=git-out-of-sync --no-recursive -C "
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
        "passphrase": "${{ inputs.plan-passphrase }}",
    },
}

#: always(), so a failed or cancelled apply's partial state persists; gated on the restore's
#: success, so state never restored is never saved, and a remote backend's skipped restore skips
#: the save too.
_SAVE = {
    "name": "Save state",
    "if": "${{ always() && steps.init.outputs.restore-outcome == 'success' }}",
    "uses": local_action("state"),
    "with": {
        "stack-slug": "${{ steps.ids.outputs.slug }}",
        "env": "${{ inputs.env }}",
        "mode": "save",
        "path": "${{ steps.init.outputs.state-path }}",
        "passphrase": "${{ inputs.plan-passphrase }}",
    },
}


def test_locate_state_reads_the_init_record_inside_terramate_run():
    """A one-line `run:` fails the step on a non-zero init, so the default `success()` condition
    skips it; the absent `if:` is part of the whole step. Mutation: run
    `python3 .../scripts/state-path` bare, outside `terramate run`."""
    assert step_by(_INIT_ACTION, name="Locate state") == _LOCATE


def test_restore_state_uses_the_located_path():
    """Mutations: `if: always()` on the restore; `stack-slug: ${{ inputs.stack }}`; drop the
    `passphrase` line, and every restore of local state refuses."""
    assert step_by(_INIT_ACTION, name="Restore state") == _RESTORE


def test_apply_cell_saves_state_to_the_located_path():
    """Mutations: drop `always()` from the `if`; save with `mode: restore`; drop the `passphrase`
    line, and every save refuses."""
    assert step_by("apply-cell", name="Save state") == _SAVE


_REFUSAL = (
    "::error::shipmate/actions/state: SHIPMATE_PLAN_PASSPHRASE is empty. Cached local state is "
    "encrypted with it, so every restore and save needs it. Set the secret and forward it to the "
    "shipmate workflows."
)

_VALIDATE_RUN = (
    'case "$MODE" in\n'
    "  restore|save) ;;\n"
    "  *) echo \"::error::shipmate/actions/state: 'mode' must be 'restore' or 'save', got "
    "'$MODE'\"; exit 1 ;;\n"
    "esac\n"
    'if [ -z "$PASSPHRASE" ]; then\n'
    f'  echo "{_REFUSAL}"\n'
    "  exit 1\n"
    "fi\n"
)

#: What a failed state encrypt prints: only apply-cell saves, after its apply ran.
_SAVE_WARNING = (
    "::error::shipmate/actions/state: the apply already ran and its state was not saved. Do not "
    "re-run the job: a re-run applies the stored plan again against the pre-apply cache entry. "
    "See docs/troubleshooting.md."
)

_ENCRYPT_RUN = (
    'if [ ! -s "$STATE_PATH" ]; then\n'
    '  echo "::notice::shipmate/actions/state: no state to save at $STATE_PATH '
    '(absent or empty)."\n'
    '  echo "skip=true" >> "$GITHUB_OUTPUT"\n'
    "  exit 0\n"
    "fi\n"
    'python3 "$GITHUB_ACTION_PATH/../../scripts/plan-crypt" encrypt "$STATE_PATH" || {\n'
    f'  echo "{_SAVE_WARNING}"\n'
    "  exit 1\n"
    "}\n"
)

_KEY = (
    "state/${{ inputs.stack-slug }}/${{ inputs.env }}/"
    "${{ github.run_id }}-${{ github.run_attempt }}"
)

#: The whole step list, `uses:` refs cut. No step carries `continue-on-error` or a status
#: function, so a refused decrypt fails the action, cell-init's `restore-outcome` is not
#: `success` and apply-cell never saves over the entry; a refused encrypt skips `Save state`.
_STATE_STEPS = [
    {
        "name": "Validate mode",
        "shell": "bash",
        "env": {"MODE": "${{ inputs.mode }}", "PASSPHRASE": "${{ inputs.passphrase }}"},
        "run": _VALIDATE_RUN,
    },
    {
        "name": "Restore state",
        "id": "restore",
        "if": "${{ inputs.mode == 'restore' }}",
        "uses": "actions/cache/restore",
        "with": {
            "path": "${{ inputs.path }}",
            "key": _KEY,
            "restore-keys": "state/${{ inputs.stack-slug }}/${{ inputs.env }}/\n",
        },
    },
    {
        "name": "Decrypt state",
        "if": "${{ inputs.mode == 'restore' && steps.restore.outputs.cache-matched-key != '' }}",
        "shell": "bash",
        "env": {
            "PLAN_PASSPHRASE": "${{ inputs.passphrase }}",
            "STATE_PATH": "${{ inputs.path }}",
            "KEY": "${{ steps.restore.outputs.cache-matched-key }}",
        },
        "run": (
            'python3 "$GITHUB_ACTION_PATH/../../scripts/plan-crypt" decrypt-state '
            '"$STATE_PATH" "$KEY"'
        ),
    },
    {
        "name": "Encrypt state",
        "id": "encrypt",
        "if": "${{ inputs.mode == 'save' }}",
        "shell": "bash",
        "env": {"PLAN_PASSPHRASE": "${{ inputs.passphrase }}", "STATE_PATH": "${{ inputs.path }}"},
        "run": _ENCRYPT_RUN,
    },
    {
        "name": "Save state",
        "if": "${{ inputs.mode == 'save' && steps.encrypt.outputs.skip != 'true' }}",
        "uses": "actions/cache/save",
        "with": {"path": "${{ inputs.path }}", "key": _KEY},
    },
]


def test_the_state_action_is_exactly_these_steps():
    """Mutations: delete `Encrypt state`; delete `Decrypt state`; invert the decrypt `if:`'s
    `!=` to `==`; move `Encrypt state` after `Save state`; add `continue-on-error: true` to
    `Decrypt state`, and a refused decrypt hands tofu the ciphertext and lets apply-cell save it;
    drop the skip clause from `Save state`'s `if:`, and a 0-byte file is saved."""
    steps = [
        {**s, "uses": s["uses"].split("@")[0]} if "uses" in s else s for s in action_steps("state")
    ]
    assert steps == _STATE_STEPS


def _validate(tmp_path, mode, passphrase):
    body = step_by("state", name="Validate mode")["run"]
    return run_step(tmp_path, body, {**os.environ, "MODE": mode, "PASSPHRASE": passphrase})


@bash_only
def test_an_empty_passphrase_refuses_both_modes(tmp_path):
    """Mutation: delete the passphrase check."""
    for mode in ("restore", "save"):
        r = _validate(tmp_path, mode, "")
        assert (r.returncode, r.stdout, r.stderr) == (1, _REFUSAL + "\n", ""), mode


@bash_only
def test_a_set_passphrase_passes_validation(tmp_path):
    """Mutation: `[ -n "$PASSPHRASE" ]` in place of `-z`, a check that refuses every caller."""
    r = _validate(tmp_path, "restore", "secret")
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def _encrypt(tmp_path, content, status=0):
    """Run `Encrypt state` with `python3` replaced by a bash function recording its argv and
    returning `status`; `content` None leaves the state file absent. Returns (process, outputs,
    recorded argv)."""
    state = tmp_path / "terraform.tfstate"
    if content is not None:
        state.write_bytes(content)
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    calls = tmp_path / "calls"
    harness = (
        f'python3() {{ printf "%s\\n" "$@" > "$CALLS" ; return {status} ; }}\n'
        + step_by("state", id="encrypt")["run"]
    )
    env = {
        **os.environ,
        "STATE_PATH": str(state),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_ACTION_PATH": "/action",
        "CALLS": str(calls),
    }
    r = run_step(tmp_path, harness, env)
    argv = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else None
    return r, github_outputs(out), argv


@bash_only
def test_an_empty_or_absent_state_file_is_skipped_not_encrypted(tmp_path):
    """Mutation: `[ ! -e "$STATE_PATH" ]` in place of `! -s`, and a 0-byte file is encrypted
    and saved, blocking the stack until its entry is deleted."""
    for name, content in (("empty", b""), ("absent", None)):
        case = tmp_path / name
        case.mkdir()
        r, outputs, argv = _encrypt(case, content)
        assert (r.returncode, outputs, argv) == (0, {"skip": "true"}, None), name


@bash_only
def test_a_failed_state_encrypt_forbids_a_re_run(tmp_path):
    """The save runs only in apply-cell, after the apply: a re-run would apply the stored plan
    again against the pre-apply entry.

    Mutation: drop the `||` warning, and the step fails on plan-crypt's line alone.
    """
    r, outputs, _ = _encrypt(tmp_path, b'{"version": 4}', status=1)
    assert (r.returncode, r.stdout, outputs) == (
        1,
        _SAVE_WARNING + "\n",
        {},
    )


@bash_only
def test_a_state_file_is_encrypted_in_place(tmp_path):
    """Mutation: `exit 0` before the `plan-crypt` line, and the save stores plaintext."""
    r, outputs, argv = _encrypt(tmp_path, b'{"version": 4}')
    assert (r.returncode, outputs, argv) == (
        0,
        {},
        ["/action/../../scripts/plan-crypt", "encrypt", str(tmp_path / "terraform.tfstate")],
    )
