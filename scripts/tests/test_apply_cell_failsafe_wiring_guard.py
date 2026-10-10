"""Guards the drift risk in actions/apply-cell/action.yml's Compose cell summary step: a
fail-safe step added to the action and never wired into the blocked-reason decision. The new step
still halts the apply, as any non-zero-exit step does composite-action-wide, but the cell reports
the generic "an earlier step failed" reason, or worse a neighbouring fail-safe's reason, instead
of its own.

Everything here is derived from the shipped source itself -- step order, ids and the Compose
step's `env:` mappings from action.yml, the inner outcomes `cell-init` exports from its own
action.yml, the FAILSAFES list from scripts/apply-cell-summary -- rather than a hand-maintained
list of the current ids, because a hardcoded list is itself the kind of thing that silently goes
stale.
"""

import ast
import re

from _loader import SCRIPTS, action_steps, action_yaml, step_by

#: Ids in the guarded range that are deliberately not fail-safes wired into the Compose step's
#: decision, such as a step added only to expose an output with no bearing on whether the apply
#: can proceed. Adding an id here must be a conscious, reviewed choice, spelled out with a reason,
#: because an unlisted id'd step in range fails the guard instead of being silently skipped, and
#: silent skipping is how a real fail-safe could ship unwired.
NOT_A_FAILSAFE: set[str] = set()


def _steps():
    return action_steps("apply-cell")


_OUTCOME = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outcome")
_INIT_OUTPUT = re.compile(r"steps\.init\.outputs\.([A-Za-z0-9_-]+-outcome)")


def _cell_init_outcomes():
    """`cell-init`'s outputs that re-export an inner step's `outcome`, as {output: inner id}."""
    outputs = action_yaml("cell-init")["outputs"]
    return {
        name: m.group(1)
        for name, spec in outputs.items()
        if (m := _OUTCOME.fullmatch(str(spec["value"]).strip("${} ")))
    }


def _ids_between_slug_and_apply():
    """Every id'd step strictly between "Stack slug" (id: ids, the first step, apply-cell minting
    no App token of its own) and "Apply the stored plan" (id: apply): the range whose steps can
    halt the apply and so must be attributable in the Compose decision. The `cell-init` call
    (id: init) stands for the inner steps whose outcome that action exports, so each keeps its
    own identity."""
    steps = _steps()
    ids = [s.get("id") for s in steps]
    start = ids.index("ids")
    end = ids.index("apply")
    assert start < end, "id 'ids' must precede id 'apply' in actions/apply-cell/action.yml"
    out = []
    for step_id in (s.get("id") for s in steps[start + 1 : end]):
        if step_id == "init":
            out.extend(_cell_init_outcomes().values())
        elif step_id:
            out.append(step_id)
    return out


def _compose_env_id_mapping():
    """Map each `steps.<id>.outcome` referenced in the Compose step's `env:` block, or each
    `steps.init.outputs.<x>-outcome` traced through `cell-init`'s outputs to the inner step id,
    back to its env var name, both directions."""
    env_block = step_by("apply-cell", name="Compose cell summary").get("env") or {}
    inner = _cell_init_outcomes()
    envvar_to_id = {}
    for var_name, expr in env_block.items():
        if m := _INIT_OUTPUT.search(str(expr)):
            if m.group(1) in inner:
                envvar_to_id[var_name] = inner[m.group(1)]
        elif m := _OUTCOME.search(str(expr)):
            envvar_to_id[var_name] = m.group(1)
    id_to_envvar = {step_id: var_name for var_name, step_id in envvar_to_id.items()}
    return envvar_to_id, id_to_envvar


def _failsafes():
    """`scripts/apply-cell-summary`'s FAILSAFES list, read via ast.literal_eval rather than exec.
    It is a plain list of (env-var-name, message) string literals, so no execution is needed to
    recover it, and nothing the script does at runtime can trick this."""
    tree = ast.parse((SCRIPTS / "apply-cell-summary").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FAILSAFES" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("no FAILSAFES assignment found in scripts/apply-cell-summary")


def test_not_a_failsafe_exemptions_are_not_stale():
    # Hygiene on the escape hatch itself: an exemption for an id no longer in the guarded range
    # gives false confidence and must be removed, not left to accumulate.
    ids_in_range = set(_ids_between_slug_and_apply())
    stale = NOT_A_FAILSAFE - ids_in_range
    assert not stale, (
        f"NOT_A_FAILSAFE lists ids no longer between the stack slug and apply: {stale}"
    )


def test_every_idd_step_in_range_is_wired_into_compose_env_block():
    ids_in_range = _ids_between_slug_and_apply()
    _, id_to_envvar = _compose_env_id_mapping()
    for step_id in ids_in_range:
        if step_id in NOT_A_FAILSAFE:
            continue
        assert step_id in id_to_envvar, (
            f"id'd step {step_id!r} sits between the stack slug and the apply step, "
            "but Compose cell summary's env: block has no "
            f"steps.{step_id}.outcome mapping -- its failure would be misattributed "
            "to the generic reason (or a neighbouring fail-safe). Wire it in, or add "
            f"{step_id!r} to NOT_A_FAILSAFE with a comment explaining why it isn't one."
        )


def test_every_wired_env_var_is_also_in_failsafes():
    ids_in_range = _ids_between_slug_and_apply()
    _, id_to_envvar = _compose_env_id_mapping()
    failsafe_envvars = {env_key for env_key, _ in _failsafes()}
    for step_id in ids_in_range:
        if step_id in NOT_A_FAILSAFE:
            continue
        env_var = id_to_envvar.get(step_id)
        assert env_var is not None
        assert env_var in failsafe_envvars, (
            f"id'd step {step_id!r} is mapped to {env_var} in Compose cell summary's "
            "env: block, but FAILSAFES never checks it -- a failure here would fall "
            "through to the generic 'an earlier step failed' reason instead of "
            "naming this step."
        )


def test_every_failsafes_entry_maps_back_to_a_current_idd_step_in_range():
    # The reverse direction: a FAILSAFES entry whose env var no longer corresponds to any id in
    # range is dead weight that can silently hide a rename or removal upstream.
    ids_in_range = set(_ids_between_slug_and_apply())
    envvar_to_id, _ = _compose_env_id_mapping()
    for env_key, _message in _failsafes():
        step_id = envvar_to_id.get(env_key)
        assert step_id is not None, (
            f"FAILSAFES references {env_key}, but no steps.<id>.outcome in Compose "
            "cell summary's env: block maps to it"
        )
        assert step_id in ids_in_range, (
            f"FAILSAFES entry {env_key} maps to id {step_id!r}, which is no longer "
            "between the stack slug and the apply step"
        )


def test_current_failsafe_set_is_exactly_the_nine_known_ids():
    # Not a substitute for the structural guards: this one needs updating the moment a tenth
    # fail-safe is added, deliberately, as a tripwire so that addition is noticed here too.
    assert set(_ids_between_slug_and_apply()) - NOT_A_FAILSAFE == {
        "download",
        "planned-head",
        "decrypt",
        "fingerprint",
        "digest-input",
        "init",
        "locate-state",
        "restore-state",
        "plan-digest",
    }


def test_cell_init_outcomes_are_its_inner_steps_and_not_the_composites():
    """The composite's outcome is not init's: a failed cache key or restore step skips init, and
    the cell must report that as an earlier failure, not as a failed init. The mapping above also
    accepts `steps.init.outcome`, so the three expressions are pinned whole here. Mutations:
    point `INIT_OUTCOME` at `steps.init.outcome`; point `LOCATE_OUTCOME` at
    `steps.init.outputs.restore-outcome`."""
    env_block = step_by("apply-cell", name="Compose cell summary")["env"]
    assert {k: env_block[k] for k in ("INIT_OUTCOME", "LOCATE_OUTCOME", "RESTORE_OUTCOME")} == {
        "INIT_OUTCOME": "${{ steps.init.outputs.init-outcome }}",
        "LOCATE_OUTCOME": "${{ steps.init.outputs.locate-outcome }}",
        "RESTORE_OUTCOME": "${{ steps.init.outputs.restore-outcome }}",
    }
