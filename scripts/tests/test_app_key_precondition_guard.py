"""Every job that mints an App installation token first checks that the key
actually arrived, and says why it did not.

An empty `private-key` is the single symptom of every wiring mistake in the
credential chain, and the failure it produces is unhelpful: the upstream mint
action reports only "must be set to a non-empty string", and three unrelated
causes produce it. A cross-organization consumer hit all three in sequence.

The check lives once, in `actions/verify-app-key`, and every mint site calls it as
its first step. Threat model: accidental regression -- the call deleted in a
refactor, a new mint site added without one, or the shared message reworded. The
two halves need opposite techniques, and using either alone leaves a hole:

- **which files must carry it** is *discovered*, by reading every file that
  invokes the mint action. A hand-written list is exactly what a ninth mint site
  escapes.
- **what the call and the shared step must say** are hand-written constants
  compared whole. Reading the expectation back out of the files under test would
  pass whatever they say, and a substring would survive both a comment and an
  inverted test.

`comment-ops` is one of two files that mint and carry no precondition, and that is asserted
rather than skipped. All four of its mints are `continue-on-error: true`, each paired with a
fallback that posts the diagnosis as a pull request comment. Aborting the action first would
replace a visible answer with a red run and a log annotation, and would take `shipmate help`,
which needs no App token at all, down with it. The other is `base-push-plan`, the merge deploy's
display-only re-plan of open pull requests: its mint is `continue-on-error: true`, its next step
warns on an empty token, and an aborting check would fail the deploy's `summary` job over a
step that must never fail it.

`plan-defer` carries the call, `continue-on-error: true`: it decides only whether a ready run
repeats a plan, so a missing key plans, and its read step warns on the empty token.

`auto-dispatch` carries the call under its deferred-mode `if:`. Called with the environments
already decided, its caller `summary` verified the key as its own first step.

`apply-run.yml`'s `review` makes the call ninth, under the `admitted` gate: its six dispatch
checks read no key, its claim step verifies the key inside `actions/claim`, and its `admit` step
reads none. A run whose claim reads `taken` mints nothing more.
"""

from _loader import ACTIONS, WORKFLOWS, action_yaml, workflow_yaml

# One physical line each in the shell body: a `::error::` annotation ends at the first newline,
# so a wrapped message loses everything after cause (1).
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

_ACTION_WITH = {"app-id": "${{ inputs.app-id }}", "private-key": "${{ inputs.private-key }}"}
_WORKFLOW_WITH = {
    "app-id": "${{ vars.SHIPMATE_APP_ID }}",
    "private-key": "${{ secrets.SHIPMATE_APP_PRIVATE_KEY }}",
}
_NAME = "Verify the App key arrived"

#: The shared action's one step, whole.
_CHECK_STEP = {
    "name": _NAME,
    "shell": "bash",
    "env": {"APP_ID": "${{ inputs.app-id }}", "PRIVATE_KEY": "${{ inputs.private-key }}"},
    "run": _RUN,
}


def _call(with_):
    return {"name": _NAME, "uses": "$/actions/verify-app-key", "with": with_}


_MINT_ACTION = "actions/create-github-app-token@"

#: file -> (step list to read, expected `with:`), or None for the file that mints without a
#: precondition; the module docstring says which and why. Hand-written, and compared as a whole
#: set against what discovery finds, so a ninth mint site fails rather than escaping.
_SITES = {
    ACTIONS / "summary" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "apply-summary" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "apply-complete" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "apply-release" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "apply-snapshot" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "gate-refresh" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "dispatch" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "drift-issues" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "lock-acquire" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "lock-release" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "outcome-reply" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "apply-queue" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "apply-waiters" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "apply-run-check" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "claim" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "plan-defer" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    ACTIONS / "auto-dispatch" / "action.yml": (("runs", "steps"), _ACTION_WITH),
    WORKFLOWS / "apply-run.yml": (("jobs", "review", "steps"), _WORKFLOW_WITH),
    ACTIONS / "comment-ops" / "action.yml": None,
    ACTIONS / "base-push-plan" / "action.yml": None,
}


#: Sites whose call degrades instead of aborting; the module docstring says why.
_DEGRADING = {ACTIONS / "plan-defer" / "action.yml"}

#: Sites whose call carries an `if:`; the module docstring says why.
_CONDITIONAL = {
    ACTIONS / "auto-dispatch" / "action.yml": "${{ inputs.deferred-to != '' }}",
    WORKFLOWS / "apply-run.yml": "${{ steps.admit.outputs.admitted == 'true' }}",
}

#: Sites whose call is not their first step, by index; the module docstring says why.
_POSITION = {WORKFLOWS / "apply-run.yml": 8}


def _minting_files():
    """Every engine file that invokes the App-token mint action."""
    candidates = [*ACTIONS.glob("*/action.yml"), *WORKFLOWS.glob("*.yml")]
    return {p for p in candidates if _MINT_ACTION in p.read_text(encoding="utf-8")}


def _steps(path, keys):
    node = (action_yaml if path.is_relative_to(ACTIONS) else workflow_yaml)(path)
    for key in keys:
        node = node[key]
    return node


def test_the_mint_sites_are_the_ones_this_guard_knows_about():
    """Discovery, not the hand-written list, decides which files are in scope. Otherwise a mint
    site added later is one this file never looks at."""
    found = _minting_files()
    assert found == set(_SITES), (
        "the set of files invoking the App-token mint changed; add each new one to "
        "_SITES with the step list to read (or None, with the reason in the "
        f"docstring). Unlisted: {sorted(p.name for p in found - set(_SITES))}; "
        f"listed but no longer minting: {sorted(p.name for p in set(_SITES) - found)}"
    )


def test_every_mint_site_verifies_the_key_arrived():
    """Mutations: delete the call from one site, or wire its `private-key` to the app id; move
    `apply-snapshot`'s call after its `snap` step; drop `plan-defer`'s `continue-on-error`; drop
    `auto-dispatch`'s `if:`; drop the `if:` from `apply-run.yml` `review`'s call."""
    for path, site in _SITES.items():
        if site is None:
            continue
        keys, with_ = site
        step = _steps(path, keys)[_POSITION.get(path, 0)]
        expected = _call(with_) | ({"continue-on-error": True} if path in _DEGRADING else {})
        expected |= {"if": _CONDITIONAL[path]} if path in _CONDITIONAL else {}
        assert step == expected, (
            f"{path.name} must open with the App-key precondition call verbatim; got {step!r}"
        )


#: apply-snapshot's mint, whole.
_SNAPSHOT_MINT = {
    "name": "Mint App installation token",
    "id": "token",
    "uses": "actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510",
    "with": {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "permission-checks": "write",
    },
}

_SNAP = 'python3 "$GITHUB_ACTION_PATH/../../scripts/apply-snapshot"'

#: apply-snapshot's `snap` step, whole.
_SNAPSHOT_SNAP = {
    "id": "snap",
    "shell": "bash",
    "env": {
        "GH_TOKEN": "${{ github.token }}",
        "HEAD_SHA": "${{ inputs.head-sha }}",
        "SHIPMATE_WAVES_JSON": "${{ inputs.waves-json }}",
        "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
        "COUNTED": "${{ inputs.counted }}",
    },
    "run": (
        "set -euo pipefail\n"
        "flag=()\n"
        'if [ "$COUNTED" = true ]; then flag=(--counted); fi\n'
        'if ! python3 "$GITHUB_ACTION_PATH/../../scripts/gate-status" checks "$HEAD_SHA" \\\n'
        '  \'.check_runs[]\' "${flag[@]}" > "$RUNNER_TEMP/checks.jsonl"; then\n'
        '  msg="could not list the check runs on $HEAD_SHA; the step log names the cause"\n'
        '  echo "refusal=$msg" >> "$GITHUB_OUTPUT"\n'
        '  echo "::error::$msg"\n'
        "  exit 1\n"
        "fi\n"
        f'{_SNAP} < "$RUNNER_TEMP/checks.jsonl"\n'
    ),
}


def test_snapshot_action_steps_are_the_key_check_the_snapshot_then_the_mint():
    """The key check, the snapshot and the mint are hard: a missing, revoked or wrong key fails
    snapshot before any wave applies, instead of `complete` failing after infrastructure
    changed. The mint's token is used by no step; minting is the authenticated preflight.

    Mutations: delete the verify step; drop the mint step; restore the flip step; pass
    `--counted` whatever `COUNTED` holds; wire `by_wave` to `steps.snap.outputs.snapshot`; add
    back a `snapshot` output.
    """
    doc = action_yaml(ACTIONS / "apply-snapshot" / "action.yml")
    assert doc["runs"]["steps"] == [_call(_ACTION_WITH), _SNAPSHOT_SNAP, _SNAPSHOT_MINT]
    assert {k: v["value"] for k, v in doc["outputs"].items()} == {
        "by_wave": "${{ steps.snap.outputs.by_wave }}",
        "refusal": "${{ steps.snap.outputs.refusal }}",
    }


def test_the_shared_check_says_why_the_key_did_not_arrive():
    """Mutations: reword a cause in the message; invert `-z`."""
    steps = _steps(ACTIONS / "verify-app-key" / "action.yml", ("runs", "steps"))
    assert steps == [_CHECK_STEP]


def test_comment_ops_answers_instead_of_aborting():
    """comment-ops must not gain the precondition: every one of its mints is
    `continue-on-error` with a fallback that posts the diagnosis as a pull request comment, and
    `shipmate help` needs no App token at all."""
    doc = action_yaml("comment-ops")
    steps = doc["runs"]["steps"]
    assert not [
        s for s in steps if s.get("name") == _NAME or "verify-app-key" in (s.get("uses") or "")
    ], (
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


def test_the_deploy_re_plan_never_aborts_on_the_key():
    """`base-push-plan` must not gain the precondition, which would fail the deploy's `summary`
    job; its mint degrades instead (`test_base_push_plan.py` pins the mint and the empty-token
    warning). Mutation: open the action with the `verify-app-key` call -- red."""
    steps = action_yaml("base-push-plan")["runs"]["steps"]
    assert [s.get("uses") for s in steps if "verify-app-key" in (s.get("uses") or "")] == []
    assert [s.get("name") for s in steps if s.get("name") == _NAME] == []
