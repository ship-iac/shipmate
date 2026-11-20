"""apply-cell hands its state to the cell's `save` job and saves nothing itself.

Threat model: an accidental edit to `actions/apply-cell` that (a) skips the handoff after a
failed or cancelled apply, whose partial state then never reaches `save`; (b) lets a later
upload replace the bundle; (c) saves a cache entry from the cell, where pull-request code runs;
(d) applies local-backend state on a runner `save` refuses. A hostile edit to the engine file is
out of scope: it is SHA-pinned and reviewed. Whole parsed values are compared to hand-written
constants.
"""

import os

from _loader import ENGINE, action_steps, action_yaml, bash_only, run_step, step_by

_HANDOFF = {
    "name": "Hand off the state",
    "id": "handoff",
    "if": "always()",
    "shell": "bash",
    "env": {
        "APPLY_OUTCOME": "${{ steps.apply.outcome }}",
        "STATE_PATH": "${{ steps.init.outputs.state-path }}",
        "SLUG": "${{ steps.ids.outputs.slug }}",
        "ENV": "${{ inputs.env }}",
        "ENCRYPTION_KEY": "${{ inputs.encryption-key }}",
    },
    "run": 'python3 "$GITHUB_ACTION_PATH/../../scripts/state-handoff"',
}

_UPLOAD = {
    "name": "Upload the state",
    "if": "${{ always() && steps.handoff.outputs.bundle == 'true' }}",
    "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
    "with": {
        "name": "${{ steps.handoff.outputs.name }}",
        "path": "${{ runner.temp }}/state.bundle",
    },
}


def test_the_handoff_runs_after_every_apply_outcome():
    """A failed or cancelled apply still mutated state, so the `if:` is exactly `always()`.
    Mutations: `if: success()`; drop `APPLY_OUTCOME` from the `env`; read
    `steps.plan-digest.outcome` as `APPLY_OUTCOME`."""
    assert step_by("apply-cell", name="Hand off the state") == _HANDOFF


def test_the_state_output_is_the_handoffs():
    """`save` reads the cell's state from this output. Mutation: read
    `steps.apply.outputs.state`."""
    assert action_yaml("apply-cell")["outputs"] == {
        "state": {
            "description": (
                "`<value> attempt=<run attempt>` from scripts/state-handoff, which the cell's "
                "`save` job reads; empty when the handoff failed before writing it."
            ),
            "value": "${{ steps.handoff.outputs.state }}",
        }
    }


def test_the_upload_takes_the_handoffs_bundle_and_name_without_overwrite():
    """`overwrite: true` would let a later same-named upload replace the bundle.
    Mutations: `overwrite: true`; `if: success()`; a name built in the YAML instead of the
    handoff's `name` output."""
    step = step_by("apply-cell", name="Upload the state")
    assert {**step, "uses": step["uses"].split(" ")[0]} == _UPLOAD


def test_no_apply_cell_step_saves_a_cache_entry():
    """Pull-request code runs in the cell, so the cell saves nothing. Mutations: restore
    `Save provider cache` (`actions/cache/save`); restore `Save state` (`actions/state`,
    `mode: save`)."""
    saves = [
        s.get("name")
        for s in action_steps("apply-cell")
        if s.get("uses", "").split("@")[0] == "actions/cache/save"
        or (s.get("with") or {}).get("mode") == "save"
    ]
    assert saves == []


def test_the_cell_summary_reads_the_hosted_and_apply_outcomes_and_no_lost_save():
    """Mutations: keep `LOST_SAVE_OUTCOME` in the env; map `HOSTED_OUTCOME` to
    `steps.plan-digest.outcome`; map `APPLY_OUTCOME` to `steps.plan-digest.outcome`."""
    env = step_by("apply-cell", name="Compose cell summary")["env"]
    assert "LOST_SAVE_OUTCOME" not in env
    assert env["HOSTED_OUTCOME"] == "${{ steps.hosted.outcome }}"
    assert env["APPLY_OUTCOME"] == "${{ steps.apply.outcome }}"


_HOSTED_REFUSAL = (
    "::error::the state saver runs only on GitHub-hosted runners; a reused self-hosted runner "
    "could alter what it saves. Use a GitHub-hosted runs_on or a remote backend.\n"
)


def _hosted(tmp_path, runner_env, state_path):
    step = step_by("apply-cell", id="hosted")
    assert step["env"] == {"STATE_PATH": "${{ steps.init.outputs.state-path }}"}
    env = {**os.environ, "STATE_PATH": state_path, "RUNNER_ENVIRONMENT": runner_env}
    r = run_step(tmp_path, step["run"], env)
    return r.returncode, r.stdout


@bash_only
def test_a_local_backend_cell_refuses_a_runner_that_is_not_github_hosted(tmp_path):
    """Mutation: refuse only on `= self-hosted`, and an empty `RUNNER_ENVIRONMENT` passes."""
    for runner_env in ("self-hosted", ""):
        assert _hosted(tmp_path, runner_env, "stacks/app/terraform.tfstate") == (
            1,
            _HOSTED_REFUSAL,
        ), runner_env


@bash_only
def test_a_hosted_runner_or_a_remote_backend_passes(tmp_path):
    """Mutation: drop `[ -n "$STATE_PATH" ] &&`, and a remote-backend cell on a self-hosted
    runner refuses."""
    assert _hosted(tmp_path, "github-hosted", "stacks/app/terraform.tfstate") == (0, "")
    assert _hosted(tmp_path, "self-hosted", "") == (0, "")


def test_the_hosted_refusal_comes_after_init_and_before_the_plan_digest():
    """The state path is known only after init; before the digest, so a refusal applies nothing.
    Mutation: move the refusal after `Apply the stored plan`."""
    ids = [s.get("id") for s in action_steps("apply-cell")]
    assert ids.index("init") + 1 == ids.index("hosted") == ids.index("plan-digest") - 1


_STATE_KEY = "${{ steps.key.outputs.key }}"

_LOOKUP_STEP = {
    "name": "Look up the saved entry",
    "id": "verify",
    "if": "${{ inputs.mode != 'restore' }}",
    "uses": "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
    "with": {"path": "${{ inputs.path }}", "key": _STATE_KEY, "lookup-only": "true"},
}


def test_the_state_save_is_verified_by_an_exact_key_lookup():
    """`actions/cache/save` warns and succeeds on a cache-service error, so only a lookup of the
    exact key proves the entry exists; a `restore-keys` would let an older entry answer for it.

    Every lookup uses the same pinned `actions/cache/restore`; `test_optional_state_guard.py`
    pins the rest of each retry.

    `lookup` mode runs the same lookups without the save.

    Mutations: add `restore-keys` to the lookup; change the lookup's key; change the save's key
    to `${{ steps.key.outputs.prefix }}`; narrow the lookup's `if:` to `inputs.mode == 'save'`;
    change the SHA of `verify4`.
    """
    lookup = step_by("state", id="verify")
    assert lookup == _LOOKUP_STEP
    save = step_by("state", name="Save state")
    assert save["with"]["key"] == _STATE_KEY
    for step_id in ("verify2", "verify3", "verify4", "verify5"):
        assert step_by("state", id=step_id)["uses"] == _LOOKUP_STEP["uses"], step_id


def test_saved_is_true_only_on_an_exact_key_hit():
    """Mutations: `value: 'true'`; read `saved` from `verify5` only."""
    assert action_yaml("state")["outputs"] == {
        "saved": {
            "description": (
                "`true` only when a `save` or `lookup` found the entry under its exact key: the "
                "first lookup, or one of up to four more about 3, 10, 20 and 30 s after a miss. "
                "Anything else means no entry was found: the save failed, was cancelled or "
                "skipped, or no lookup found the entry."
            ),
            "value": (
                "${{ steps.verify.outputs.cache-hit == 'true' "
                "|| steps.verify2.outputs.cache-hit == 'true' "
                "|| steps.verify3.outputs.cache-hit == 'true' "
                "|| steps.verify4.outputs.cache-hit == 'true' "
                "|| steps.verify5.outputs.cache-hit == 'true' }}"
            ),
        }
    }


def test_the_troubleshooting_entry_states_the_re_lookup():
    """Mutation: change `four` to `three` in the sentence."""
    text = (ENGINE / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
    heading = "### `The apply step ran and its state was not saved`\n"
    entry = " ".join(text[text.index(heading) :].split("\n### ", 1)[0].split())
    assert (
        "When the first exact-key lookup finds no entry, the action looks up the same key up to "
        "four more times over about 30 s before it reports the state not saved; a failed, "
        "cancelled or skipped save runs no lookup and reports at once, and a run cancelled "
        "after the save reports the state not saved although the entry may exist."
    ) in entry
