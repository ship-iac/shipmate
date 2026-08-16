"""Every job that mints an App installation token first checks that the key
actually arrived, and says why it did not.

An empty `private-key` is the single symptom of every wiring mistake in the
credential chain, and the failure it produces is unhelpful: the upstream mint
action reports only "must be set to a non-empty string", and three unrelated
causes produce it. A cross-organization consumer hit all three in sequence.

Threat model: accidental regression -- the step deleted in a refactor, or a new
mint site added without one. The two halves need opposite techniques, and using
either alone leaves a hole:

- **which files must carry it** is *discovered*, by reading every file that
  invokes the mint action. A hand-written list is exactly what a ninth mint site
  escapes.
- **what the step must say** is a hand-written constant compared whole. Reading
  the expectation back out of the files under test would pass whatever they say,
  and a substring would survive both a comment and an inverted test.

`comment-ops` is the one file that mints and carries no precondition, and that
is asserted rather than skipped. All four of its mints are
`continue-on-error: true`, each paired with a fallback that posts the diagnosis
*as a PR comment*; aborting the action first would replace a visible answer with
a red run and a log annotation, and would take `shipmate help` -- which needs no
App token at all -- down with it.
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


def _step(env):
    return {"name": "Verify the App key arrived", "shell": "bash", "env": env, "run": _RUN}


_MINT_ACTION = "actions/create-github-app-token@"

#: file -> (step list to read, expected env), or None for the file that mints
#: without a precondition (see the module docstring). Hand-written, and compared
#: as a whole set against what discovery finds, so a ninth mint site fails rather
#: than escaping.
_SITES = {
    ACTIONS / "summary" / "action.yml": (("runs", "steps"), _ACTION_ENV),
    ACTIONS / "apply-summary" / "action.yml": (("runs", "steps"), _ACTION_ENV),
    ACTIONS / "apply-complete" / "action.yml": (("runs", "steps"), _ACTION_ENV),
    ACTIONS / "gate-refresh" / "action.yml": (("runs", "steps"), _ACTION_ENV),
    ACTIONS / "dispatch" / "action.yml": (("runs", "steps"), _ACTION_ENV),
    ACTIONS / "drift-issues" / "action.yml": (("runs", "steps"), _ACTION_ENV),
    WORKFLOWS / "deploy.yml": (("jobs", "summary", "steps"), _WORKFLOW_ENV),
    WORKFLOWS / "apply-all.yml": (("jobs", "review", "steps"), _WORKFLOW_ENV),
    WORKFLOWS / "apply.yml": (("jobs", "review", "steps"), _WORKFLOW_ENV),
    ACTIONS / "comment-ops" / "action.yml": None,
}


def _minting_files():
    """Every engine file that invokes the App-token mint action."""
    candidates = [*ACTIONS.glob("*/action.yml"), *WORKFLOWS.glob("*.yml")]
    return {p for p in candidates if _MINT_ACTION in p.read_text(encoding="utf-8")}


def _steps(path, keys):
    node = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in keys:
        node = node[key]
    return node


def test_the_mint_sites_are_the_ones_this_guard_knows_about():
    """Discovery, not the hand-written list, decides which files are in scope --
    otherwise a mint site added later is one this file simply never looks at."""
    found = _minting_files()
    assert found == set(_SITES), (
        "the set of files invoking the App-token mint changed; add each new one to "
        "_SITES with the step list to read (or None, with the reason in the "
        f"docstring). Unlisted: {sorted(p.name for p in found - set(_SITES))}; "
        f"listed but no longer minting: {sorted(p.name for p in set(_SITES) - found)}"
    )


def test_every_mint_site_verifies_the_key_arrived():
    for path, site in _SITES.items():
        if site is None:
            continue
        keys, env = site
        step = _steps(path, keys)[0]
        assert step == _step(env), (
            f"{path.name} must open with the App-key precondition verbatim "
            f"(differences hide an inverted test or a reworded cause); got {step!r}"
        )


def test_comment_ops_answers_instead_of_aborting():
    """comment-ops must NOT gain the precondition: every one of its mints is
    `continue-on-error` with a fallback that posts the diagnosis as a PR comment,
    and `shipmate help` needs no App token at all."""
    doc = yaml.safe_load((ACTIONS / "comment-ops" / "action.yml").read_text(encoding="utf-8"))
    steps = doc["runs"]["steps"]
    assert not [s for s in steps if s.get("name") == "Verify the App key arrived"], (
        "comment-ops carries the aborting precondition; that replaces its "
        "PR-visible 'App token unavailable' answers with a red run"
    )
    unguarded = [
        s.get("id")
        for s in steps
        if _MINT_ACTION in (s.get("uses") or "") and not s.get("continue-on-error")
    ]
    assert not unguarded, (
        f"comment-ops mint step(s) {unguarded} would now hard-fail on an empty key "
        "with no PR-visible answer -- either restore continue-on-error and a "
        "fallback, or move this file into the precondition set"
    )
