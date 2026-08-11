"""Every job that mints an App installation token first checks that the key
actually arrived, and says why it did not.

An empty `private-key` is the single symptom of every wiring mistake in the
credential chain, and the failure it produces is unhelpful: the upstream mint
action reports only "must be set to a non-empty string", and three unrelated
causes produce it. A cross-organization consumer hit all three in sequence.

Threat model: accidental regression -- the step deleted in a refactor, or a new
mint site added without one. Hence a hand-written list of the eight sites (never
a glob, which an action added later escapes silently) and a whole-step
comparison (never a substring, which a comment satisfies and an inverted test
survives).
"""

import yaml
from _loader import ACTIONS, WORKFLOWS

# One physical line each in the shell body -- a `::error::` annotation ends at
# the first newline, so a wrapped message loses everything after cause (1).
_KEY_MSG = (
    "The shipmate App private key did not reach this job. Three causes, in the order "
    "worth checking: (1) the calling workflow used 'secrets: inherit' across an "
    "organization boundary -- GitHub delivers no inherited secrets there, and inherit "
    "also suppresses the value this job's environment binding would otherwise supply, "
    "so pass the secret by name instead; (2) the caller left SHIPMATE_APP_PRIVATE_KEY "
    "out of its secrets block; (3) this job binds no environment, or binds one that "
    "holds no secret of that name. See docs/getting-started.md."
)
_APP_MSG = (
    "SHIPMATE_APP_ID is unset. Set it as a repository or organization variable "
    "(docs/github-app.md)."
)

_RUN = (
    "set -euo pipefail\n"
    'if [ -z "$PRIVATE_KEY" ]; then\n'
    f'  echo "::error::{_KEY_MSG}"\n'
    "  exit 1\n"
    "fi\n"
    f'[ -n "$APP_ID" ] || {{ echo "::error::{_APP_MSG}"; exit 1; }}\n'
)

_ACTION_ENV = {"APP_ID": "${{ inputs.app-id }}", "PRIVATE_KEY": "${{ inputs.private-key }}"}
_WORKFLOW_ENV = {
    "APP_ID": "${{ vars.SHIPMATE_APP_ID }}",
    "PRIVATE_KEY": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}


def _step(env, if_expr=None):
    step = {"name": "Verify the App key arrived", "shell": "bash", "env": env, "run": _RUN}
    if if_expr:
        step = {
            "name": step["name"],
            "if": if_expr,
            **{k: v for k, v in step.items() if k != "name"},
        }
    return step


#: (file, path to the step list, index, expected whole step). Index 0 everywhere
#: except comment-ops, which runs on every issue comment: gating its check on a
#: parsed command keeps an unrelated "lgtm" from reddening a run, and the four
#: mints in that file all sit downstream of `parse`.
_SITES = (
    (ACTIONS / "summary" / "action.yml", ("runs", "steps"), 0, _step(_ACTION_ENV)),
    (ACTIONS / "apply-summary" / "action.yml", ("runs", "steps"), 0, _step(_ACTION_ENV)),
    (ACTIONS / "apply-complete" / "action.yml", ("runs", "steps"), 0, _step(_ACTION_ENV)),
    (ACTIONS / "gate-refresh" / "action.yml", ("runs", "steps"), 0, _step(_ACTION_ENV)),
    (ACTIONS / "dispatch" / "action.yml", ("runs", "steps"), 0, _step(_ACTION_ENV)),
    (ACTIONS / "drift-issues" / "action.yml", ("runs", "steps"), 0, _step(_ACTION_ENV)),
    (
        ACTIONS / "comment-ops" / "action.yml",
        ("runs", "steps"),
        2,
        _step(_ACTION_ENV, "${{ steps.parse.outputs.is_command == 'true' }}"),
    ),
    (
        WORKFLOWS / "deploy.yml",
        ("jobs", "summary", "steps"),
        0,
        _step(_WORKFLOW_ENV),
    ),
)


def test_every_mint_site_verifies_the_key_arrived():
    for path, keys, index, expected in _SITES:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        node = doc
        for key in keys:
            node = node[key]
        assert node[index] == expected, (
            f"{path.name} step {index} must be the App-key precondition verbatim "
            f"(differences hide an inverted test or a reworded cause); got {node[index]!r}"
        )
