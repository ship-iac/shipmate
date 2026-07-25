"""Guards against the real drift risk in actions/apply-cell/action.yml's
Compose cell summary step: someone adds a fail-safe step to the action and
never wires it into the blocked-reason decision. The new step would still
halt the apply (any non-zero-exit step does, composite-action-wide), but the
cell would report the *generic* "an earlier step failed" reason -- or worse,
a neighbouring fail-safe's reason -- instead of its own.

Everything here is derived from action.yml itself (step order, ids, the
Compose step's env: mappings, the heredoc's FAILSAFES list) rather than a
hand-maintained list of the four current ids -- a hardcoded list would itself
be the kind of thing that silently goes stale.
"""

import ast
import pathlib
import re

import yaml

_ACTIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "actions"
_ACTION_PATH = _ACTIONS_DIR / "apply-cell" / "action.yml"

# Ids in the guarded range that are deliberately NOT fail-safes wired into the
# Compose step's decision -- e.g. a step added only to expose an output, with
# no bearing on whether the apply can proceed. Empty today: every id'd step
# between the token mint and the apply is in fact a fail-safe. Adding an id
# here must be a conscious, reviewed choice (spelled out with a reason),
# because an unlisted id'd step in range fails the guard below instead of
# being silently skipped -- silent skipping is exactly how a real fail-safe
# could ship unwired.
NOT_A_FAILSAFE: set[str] = set()


def _steps():
    spec = yaml.safe_load(_ACTION_PATH.read_text(encoding="utf-8"))
    return (spec.get("runs") or {}).get("steps") or []


def _step_by_id(step_id):
    matches = [s for s in _steps() if s.get("id") == step_id]
    assert len(matches) == 1, f"expected exactly one step with id {step_id!r}, got {len(matches)}"
    return matches[0]


def _ids_between_token_mint_and_apply():
    """Every id'd step strictly between "Mint App installation token" (id:
    token) and "Apply the stored plan" (id: apply) -- the range whose steps
    can halt the apply and so must be attributable in the Compose decision."""
    steps = _steps()
    ids = [s.get("id") for s in steps]
    start = ids.index("token")
    end = ids.index("apply")
    assert start < end, "id 'token' must precede id 'apply' in actions/apply-cell/action.yml"
    return [s.get("id") for s in steps[start + 1 : end] if s.get("id")]


def _compose_step():
    matches = [s for s in _steps() if s.get("name") == "Compose cell summary"]
    assert len(matches) == 1, f"expected exactly one Compose cell summary step, got {len(matches)}"
    return matches[0]


def _compose_env_id_mapping():
    """Map each `steps.<id>.outcome` referenced in the Compose step's env:
    block back to its env var name, both directions."""
    env_block = _compose_step().get("env") or {}
    pattern = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outcome")
    envvar_to_id = {}
    for var_name, expr in env_block.items():
        m = pattern.search(str(expr))
        if m:
            envvar_to_id[var_name] = m.group(1)
    id_to_envvar = {step_id: var_name for var_name, step_id in envvar_to_id.items()}
    return envvar_to_id, id_to_envvar


def _failsafes():
    """The heredoc's FAILSAFES list, read via ast.literal_eval rather than
    exec -- it is a plain list of (env-var-name, message) string literals, so
    no execution is needed to recover it, and this can't be tricked by
    anything the heredoc does at runtime."""
    run = _compose_step()["run"]
    _, _, rest = run.partition("<<'PY'\n")
    body, sep, _ = rest.rpartition("\nPY")
    assert sep, "Compose cell summary heredoc has no closing PY terminator"

    tree = ast.parse(body)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FAILSAFES" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("no FAILSAFES assignment found in the Compose cell summary heredoc")


def test_not_a_failsafe_exemptions_are_not_stale():
    # Hygiene on the escape hatch itself: an exemption for an id that no
    # longer exists in the guarded range gives false confidence and must be
    # removed, not left to accumulate.
    ids_in_range = set(_ids_between_token_mint_and_apply())
    stale = NOT_A_FAILSAFE - ids_in_range
    assert not stale, f"NOT_A_FAILSAFE lists ids no longer between token mint and apply: {stale}"


def test_every_idd_step_in_range_is_wired_into_compose_env_block():
    ids_in_range = _ids_between_token_mint_and_apply()
    _, id_to_envvar = _compose_env_id_mapping()
    for step_id in ids_in_range:
        if step_id in NOT_A_FAILSAFE:
            continue
        assert step_id in id_to_envvar, (
            f"id'd step {step_id!r} sits between the token mint and the apply step, "
            "but Compose cell summary's env: block has no "
            f"steps.{step_id}.outcome mapping -- its failure would be misattributed "
            "to the generic reason (or a neighbouring fail-safe). Wire it in, or add "
            f"{step_id!r} to NOT_A_FAILSAFE with a comment explaining why it isn't one."
        )


def test_every_wired_env_var_is_also_in_failsafes():
    ids_in_range = _ids_between_token_mint_and_apply()
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
    # The reverse direction: a FAILSAFES entry whose env var no longer
    # corresponds to any id in range is dead weight that can silently hide a
    # rename/removal upstream.
    ids_in_range = set(_ids_between_token_mint_and_apply())
    envvar_to_id, _ = _compose_env_id_mapping()
    for env_key, _message in _failsafes():
        step_id = envvar_to_id.get(env_key)
        assert step_id is not None, (
            f"FAILSAFES references {env_key}, but no steps.<id>.outcome in Compose "
            "cell summary's env: block maps to it"
        )
        assert step_id in ids_in_range, (
            f"FAILSAFES entry {env_key} maps to id {step_id!r}, which is no longer "
            "between the token mint and the apply step"
        )


def test_current_failsafe_set_is_exactly_the_four_known_ids():
    # Not a substitute for the structural guards above (this one intentionally
    # would need updating the moment a fifth fail-safe is added) -- a tripwire
    # so that addition is noticed here too, not just in the structural checks.
    assert set(_ids_between_token_mint_and_apply()) - NOT_A_FAILSAFE == {
        "download",
        "decrypt",
        "fingerprint",
        "restore-state",
    }
