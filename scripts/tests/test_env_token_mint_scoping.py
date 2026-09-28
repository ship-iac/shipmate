"""The `environments: read` mint is its own step, and the gate path never asks
for it.

`shipmate doctor`'s plan-environment secret probe needs a fine-grained `environments: read`
token. That permission is declared in `app/manifest.json`, and GitHub holds a widened grant as a
pending request until an organization owner accepts it, so on every already-installed App a mint
requesting it fails until someone clicks Accept.

That makes where it is requested a correctness question, not a style one:

- `actions/summary`'s `token` mint has no `continue-on-error` and gates the apply-check creation
  and the `shipmate / gate` status. Requesting the new permission there takes the merge gate down
  in every consumer repository that has not accepted the request.
- `actions/comment-ops`' `doctortoken` mint is `continue-on-error`, but its failure
  short-circuits the whole doctor route to "could not mint a GitHub App token", so the new probe
  going dark would take the other ten with it.

So the permission is minted by a dedicated `continue-on-error` step in each action and handed to
doctor as `SHIPMATE_ENV_TOKEN`, which the probe treats as optional. These guards pin both halves:
the dedicated mints exist, are scoped and are non-fatal, and the two load-bearing mints do not
carry the permission.
"""

from _loader import action_steps, step_by

_PERM = "permission-environments"
# Not named `..._TOKEN_...`: ruff's S105 hardcoded-password rule keys on the name.
_MINT_OUTPUT_EXPR = "${{ steps.envtoken.outputs.token }}"
_MINT = "actions/create-github-app-token"

#: action -> id of the load-bearing mint that must NOT request the permission.
_GATE_MINTS = {"summary": "token", "comment-ops": "doctortoken"}

#: The doctor modes that run the probes. `check-ids` only reduces a check-runs listing, so it is
#: deliberately not here.
_PROBE_MODES = frozenset({"annotate", "report"})


def test_each_action_mints_the_environments_permission_in_its_own_step():
    for action in _GATE_MINTS:
        step = step_by(action, id="envtoken")
        assert _MINT in step["uses"], f"{action}: envtoken must be a create-github-app-token step"
        assert step.get("continue-on-error") is True, (
            f"{action}: the envtoken mint must not be fatal — it fails on every "
            "installation that has not accepted the permission request yet"
        )


def test_the_environments_mint_requests_nothing_else():
    """A second mint is only safe because it is narrow: anything else requested here is a
    permission the gate path already has under a token that works, and adding it widens what a
    failed Accept takes down."""
    for action in _GATE_MINTS:
        with_ = step_by(action, id="envtoken")["with"]
        perms = {k: v for k, v in with_.items() if k.startswith("permission-")}
        assert perms == {_PERM: "read"}, f"{action}: envtoken requests {perms}"


def test_the_gate_path_mints_do_not_request_the_environments_permission():
    for action, mint_id in _GATE_MINTS.items():
        with_ = step_by(action, id=mint_id)["with"]
        assert _PERM not in with_, (
            f"{action}: `{mint_id}` must not request `{_PERM}` — an installation that "
            "has not accepted the request yet would lose everything that mint gates"
        )


def test_each_doctor_step_receives_the_env_token():
    """The probe reads SHIPMATE_ENV_TOKEN from its own environment. A mint whose output nobody
    threads is a check that is dark on every run.

    Selected by mode rather than by the steps that already carry the token, so every
    probe-running step must thread it. Otherwise a third one added later without it would satisfy
    this by the other two."""
    for action in _GATE_MINTS:
        consumers = [
            s
            for s in action_steps(action)
            if (s.get("env") or {}).get("SHIPMATE_DOCTOR_MODE") in _PROBE_MODES
        ]
        assert consumers, f"{action}: no doctor probe step to receive SHIPMATE_ENV_TOKEN"
        for step in consumers:
            assert step["env"].get("SHIPMATE_ENV_TOKEN") == _MINT_OUTPUT_EXPR, (
                f"{action}: {step.get('name')!r} must take SHIPMATE_ENV_TOKEN from the "
                f"envtoken step, got {step['env'].get('SHIPMATE_ENV_TOKEN')!r}"
            )


def test_the_check_ids_step_is_not_a_consumer():
    """`check-ids` mode reduces a check-runs listing and runs no probe, so it has no use for the
    token. Passing it there would only widen exposure."""
    for step in action_steps("comment-ops"):
        env = step.get("env") or {}
        if env.get("SHIPMATE_DOCTOR_MODE") == "check-ids":
            assert "SHIPMATE_ENV_TOKEN" not in env
