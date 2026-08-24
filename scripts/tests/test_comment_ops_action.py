"""Source-derived guards on the comment-ops action's routing wiring.

The action branches on `steps.parse.outputs.route`; the routes come from
comment-parse's VERBS registry. A verb added to the registry with no branch in
the action would parse, authorize, and then silently do nothing.
"""

import json
import re

from _loader import ACTIONS, ENGINE, SCRIPTS, action_steps, action_yaml, load_script

_ACTION_FILE = ACTIONS / "comment-ops" / "action.yml"
_ACTION = _ACTION_FILE.read_text(encoding="utf-8")
_SUMMARY_ACTION = (ACTIONS / "summary" / "action.yml").read_text(encoding="utf-8")
_MANIFEST_PERMISSIONS = json.loads((ENGINE / "app" / "manifest.json").read_text(encoding="utf-8"))[
    "default_permissions"
]

# The `doctor` route's access gate. The allowlist literal lives in exactly one
# place in the action (the `guard` step's `case`); every doctor step keys off
# the boolean output it writes. The output is named for what GitHub actually
# tells us -- an association -- not for write access, which it does not verify.
_ALLOWLIST = "OWNER|MEMBER|COLLABORATOR"
_GATE = "steps.guard.outputs.privileged_association"
# The one phrase the refused commenter, the shipped help footer and the docs all
# use for what the gate checks. Deliberately not "write access": a Read-role
# collaborator and an org member with no repository access both pass this gate.
_CLAIM = "organization members and repository collaborators"
# Markers of a step that handles doctor's machinery or performs one of its
# disclosure-bearing settings reads, regardless of how the step is conditioned.
_DOCTOR_TOUCHES = (
    "steps.doctortoken.outputs.token",
    "steps.gatherdoc.outputs",
    "SHIPMATE_DOCTOR_MODE",
    "scripts/doctor",
    "rules/branches",
    "/environments",
)


cp = load_script("comment-parse")
doctor = load_script("doctor")


def test_every_active_route_has_a_branch():
    routed = set(re.findall(r"outputs\.route == '([a-z]+)'", _ACTION))
    expected = {s["route"] for s in cp.VERBS.values() if s["route"]}
    assert expected <= routed, expected - routed


def test_bot_authored_comments_are_ignored():
    assert '== *"[bot]"' in _ACTION
    # The literal match above is inert on its own -- Parse command must
    # actually be skipped when the guard trips, or the loop guard is
    # decorative and every later step still keys off an empty parse output.
    assert "steps.guard.outputs.skip != 'true'" in _ACTION


def test_doctor_step_supplies_every_env_var_doctor_reads():
    """comment-ops' doctor steps must supply every SHIPMATE_* name `doctor`
    reads at all (subscript or .get), report mode's full env contract."""
    src = (SCRIPTS / "doctor").read_text(encoding="utf-8")
    read = set(re.findall(r"os\.environ(?:\.get)?[\[(]['\"](SHIPMATE_[A-Z_]+)['\"]", src))
    for name in read:
        assert name in _ACTION, name


def test_summary_action_supplies_every_env_var_doctor_requires_in_annotate_mode():
    """`ctx_from_env()` runs in both `annotate` and `report` mode; a name read
    via subscript (`os.environ["SHIPMATE_..."]`, as opposed to `.get(...)`) is
    one annotate mode cannot run without. `actions/summary` only ever drives
    `annotate` mode -- it has no doctor `report`/`check-ids` steps of its
    own -- so its `env:` block must cover this required subset. This does not
    fold into the assertion above: that one is checked against
    `actions/comment-ops/action.yml`'s full (subscript + .get) read-set, which
    would pass even if `actions/summary` silently dropped a required name."""
    src = (SCRIPTS / "doctor").read_text(encoding="utf-8")
    required = set(re.findall(r"os\.environ\[['\"](SHIPMATE_[A-Z_]+)['\"]\]", src))
    assert required, "expected at least one required SHIPMATE_* read in doctor"
    for name in required:
        assert name in _SUMMARY_ACTION, name


def test_both_doctor_steps_supply_the_engine_repo_from_the_action_context():
    """The pin probe reports only on the engine's own pins, and learns which
    repository that is at runtime — `github.action_repository` is the owner/repo
    of the running action, so the value stays org-agnostic and is never
    hardcoded. Both doctor call sites must supply it: `actions/summary` drives
    `annotate` mode on every plan run and `actions/comment-ops` drives `report`
    mode on demand, and a site that omits it degrades that path's pin probe to
    "not verified".

    `test_doctor_step_supplies_every_env_var_doctor_reads` covers the
    comment-ops side by derivation, but it reads nothing about
    `actions/summary`, and the summary-side guard only demands doctor's
    subscript reads — this one is read with `.get`."""
    expected = "SHIPMATE_ENGINE_REPO: ${{ github.action_repository }}"
    assert expected in _ACTION
    assert expected in _SUMMARY_ACTION


def test_doctor_runs_in_report_mode_with_the_app_token():
    assert "SHIPMATE_DOCTOR_MODE: report" in _ACTION
    assert "SHIPMATE_DOCTOR_MODE: check-ids" in _ACTION
    assert "steps.doctortoken.outputs.token" in _ACTION


def test_doctor_sticky_marker_matches_the_script():
    assert doctor.DOCTOR_MARKER in _ACTION


def test_the_rendered_report_reaches_the_job_summary_before_the_comment_post():
    """The report costs 30-60s of probes, and both post paths can lose it: a
    failed comment listing `exit 0`s by design (so the run stays green with no
    report anywhere), and a failed PATCH/POST reds the step after the render.
    Writing `doctor.md` to the job summary first means the run page always
    carries the report even when the pull request comment does not.

    Reads the step through `_code`, so the rationale comment above the write --
    which names both `doctor.md` and the variable -- cannot satisfy any of these
    assertions on prose alone. Without that, deleting the write itself would
    leave the comment as the sole match and every assertion below would still
    pass while nothing reached the summary.

    Kills the mutations that matter: dropping the write, writing something other
    than the report, truncating instead of appending, and moving the write below
    the listing (where the early `exit 0` skips it)."""
    code = _code(_step("SHIPMATE_DOCTOR_MODE: report"))
    writes = [ln for ln in code.splitlines() if "$GITHUB_STEP_SUMMARY" in ln]
    assert len(writes) == 1, f"expected exactly one job-summary write, got {writes}"
    assert "doctor.md" in writes[0], writes[0]
    # Appending, not truncating. The runner hands each step its own summary file,
    # so this is not about other steps -- it is about not clobbering anything
    # written earlier in this same step, and never truncating a runner-owned file.
    assert ">>" in writes[0], writes[0]
    assert code.index(writes[0]) < code.index("issues/$PR_NUMBER/comments")
    assert code.index(writes[0]) < code.index("exit 0")


def test_a_lost_job_summary_write_does_not_cost_the_comment():
    """The summary write is the surface this change *added*, so it must degrade
    rather than abort: a bare `cat` under the step's `set -euo pipefail` would
    turn an unwritable summary into a red step with no comment either -- strictly
    worse than before the write existed. `|| true` is not available here (see
    test_the_doctor_upsert_does_not_swallow_a_comment_listing_failure), so the
    degrade is an `if`-guard that warns, and the variable is read with `:-` so
    `set -u` cannot kill the step on a runner that never exports it."""
    code = _code(_step("SHIPMATE_DOCTOR_MODE: report"))
    write = next(ln for ln in code.splitlines() if "$GITHUB_STEP_SUMMARY" in ln)
    assert write.lstrip().startswith("if "), write
    assert "${GITHUB_STEP_SUMMARY:-}" in write, write
    # The report still has to be posted after a failed summary write, so the
    # guard must warn and carry on rather than exit.
    assert "::warning::" in code.split(write, 1)[1].split("fi", 1)[0]


def test_help_does_not_require_the_app():
    """help must answer even when the App is not installed — the state where a
    newcomer most needs it — so it posts with the workflow token."""
    block = _ACTION.split("route == 'help'", 1)[1].split("- name:", 1)[0]
    assert "inputs.github-token" in block


def _step(marker):
    """The action.yml step block containing `marker` -- so a guard on one step's
    wiring cannot be satisfied by a coincidental match in another step."""
    steps = _ACTION.split("\n    - name:")
    matches = [s for s in steps if marker in s]
    assert len(matches) == 1, f"{marker!r} appears in {len(matches)} steps"
    return matches[0]


def test_read_only_routes_are_acknowledged_with_a_reaction():
    """`doctor` spends 30-60s in API calls before its comment appears. Without
    an acknowledgement on the triggering comment the commenter's next move is to
    comment again, so both read-only routes react -- and a failed reaction
    (comment deleted, reactions disabled) must not fail the command."""
    block = _step("content=eyes")
    assert "steps.parse.outputs.route == 'doctor'" in block
    assert "steps.parse.outputs.route == 'help'" in block
    # On the invocation itself, not just somewhere in the block: the step's own
    # comment explains the `|| true`, so a bare substring check for it stays
    # green when the operator is deleted from the `gh api` line.
    assert "-f content=eyes >/dev/null || true" in block


def test_the_rocket_reaction_stays_on_an_authorized_apply():
    # `eyes` = accepted a read-only command, `rocket` = an apply was authorized
    # and is being dispatched. The two must not collapse into one signal.
    block = _step("content=rocket")
    assert "steps.authz.outputs.authorized == 'true'" in block


def test_summary_doctor_step_reads_the_head_sha_it_was_given():
    """annotate mode must probe the same commit the plan ran on: without
    SHIPMATE_HEAD_SHA the pin probe's contents reads fall back to the default
    branch, where a pin bump on this PR is not visible yet."""
    steps = _SUMMARY_ACTION.split("\n    - name:")
    block = next(s for s in steps if "SHIPMATE_DOCTOR_MODE: annotate" in s)
    assert "SHIPMATE_HEAD_SHA: ${{ inputs.head-sha }}" in block


def test_unreadable_head_sha_marks_the_harvest_failed_before_exiting():
    """The read-only degrade path for an unreadable PR head SHA must record
    harvest_failed=true (and head_sha/run_id) to GITHUB_OUTPUT before it
    exits -- otherwise the render step reads an empty SHIPMATE_HARVEST_FAILED
    and the sticky comment falsely claims the warning harvest ran clean.

    Sliced from the `if [[ ! "$head"` condition itself, not from the step's
    start, and pinned to a single `exit 0` in the step: all three substrings
    also appear elsewhere in the step for other paths, so a slice from the
    step's start would stay green even with the degrade branch deleted."""
    block = _ACTION.split("id: gatherdoc", 1)[1].split("- name:", 1)[0]
    assert block.count("exit 0") == 1
    degrade = block.split('if [[ ! "$head"', 1)[1].split("exit 0", 1)[0]
    assert "head_sha=" in degrade
    assert "run_id=" in degrade
    assert "harvest_failed=true" in degrade


def test_harvest_failed_env_falls_back_to_true_when_gatherdoc_did_not_run():
    """The render step's `if:` keys only on `route` and `doctortoken.outcome`,
    not on `gatherdoc.outcome` -- if gatherdoc ever reds or is skipped, its
    `harvest_failed` output is unset. An empty string is falsy in a GHA
    expression and a literal 'false' is truthy (non-empty), so `|| 'true'`
    treats "gatherdoc didn't run" as a failed harvest while passing a real
    `false` through unchanged."""
    assert (
        "SHIPMATE_HARVEST_FAILED: ${{ steps.gatherdoc.outputs.harvest_failed || 'true' }}"
        in _ACTION
    )


def test_the_check_runs_projection_carries_the_status_the_pending_flag_needs():
    """`check-ids` mode decides the harvest-pending flag from this projection,
    so `status` must be in it — without the field every run reads as unfinished
    (`.get("status") != "completed"`), and the report would tell every commenter
    to come back later, forever."""
    block = _ACTION.split("id: gatherdoc", 1)[1].split("- name:", 1)[0]
    projection = block.split("--jq '.check_runs[]", 1)[1].split("\n", 1)[0]
    assert "status" in projection
    assert "started_at" in projection  # still ranked by start time, not id


def test_the_render_step_reads_the_harvest_pending_flag_the_reduction_wrote():
    """`check-ids` mode writes `harvest_pending` as a step output of the gather
    step; the render step must read it into `SHIPMATE_HARVEST_PENDING`, or the
    flag is computed and thrown away and the false all-clear comes back.

    No `|| 'true'` fallback here, unlike SHIPMATE_HARVEST_FAILED: if gatherdoc
    never ran there is no harvest at all, and `harvest_failed` already says so —
    a second "and some runs may still be going" would be noise, not honesty.

    The writer half — that `check-ids` mode actually emits that step output — is
    covered behaviourally by
    test_doctor.py::test_check_ids_mode_writes_the_harvest_pending_step_output."""
    assert "SHIPMATE_HARVEST_PENDING: ${{ steps.gatherdoc.outputs.harvest_pending }}" in _ACTION


def test_harvest_flag_is_set_inside_the_loop_and_written_once_after_it():
    """The annotations loop's per-id failure fallback (`echo '[]'`) is
    byte-identical to "this check run had no annotations", so the loop must
    flip the shared `harvest_failed` shell variable -- and that variable must
    be written to GITHUB_OUTPUT exactly once, *after* the loop, so a harvest
    with zero check-run ids still writes it and a per-id failure isn't
    overwritten by a later clean iteration.

    Kills both mutations of that wiring: dropping the in-loop
    `harvest_failed=true` (the loop-body assertion fails) and moving the
    GITHUB_OUTPUT write inside the loop (both the ordering assertion and the
    loop-body redirect assertion fail)."""
    block = _ACTION.split("id: gatherdoc", 1)[1].split("- name:", 1)[0]
    assert block.count("harvest_failed=$harvest_failed") == 1
    assert block.index("harvest_failed=$harvest_failed") > block.index("done < check-ids.tsv")
    loop_body = block.split("while IFS=", 1)[1].split("done <", 1)[0]
    assert "harvest_failed=true" in loop_body
    assert '>> "$GITHUB_OUTPUT"' not in loop_body


def _steps_conditioned_on(route):
    """(name, if-expression) for every step whose `if:` names exactly this
    route -- derived from the file, so a doctor step added later is covered
    without anyone remembering to extend a hardcoded list here. Steps shared
    with another route (the `eyes` acknowledgement) are excluded: they are not
    on "the doctor route" in the sense the access gate applies to."""
    other = {"doctor", "help", "apply"} - {route}
    out = []
    for step in action_steps("comment-ops"):
        cond = step.get("if") or ""
        if f"outputs.route == '{route}'" not in cond:
            continue
        if any(f"outputs.route == '{o}'" in cond for o in other):
            continue
        out.append((step.get("name"), cond))
    return out


def _guard_case_block():
    """The body of the `guard` step's `case "$COMMENT_ASSOCIATION" in … esac`.

    Sliced out rather than pattern-matched over the whole file: a shell `case`
    pattern may contain glob metacharacters, so a *widening* branch such as
    `[A-Z]*OR)` (which admits CONTRIBUTOR and FIRST_TIME_CONTRIBUTOR) is
    invisible to any regex enumerating `[A-Z_|]+` tokens. Counting `;;` and the
    gate-opening assignment inside the sliced block sees it regardless of how
    the pattern is spelled or laid out."""
    guard = _step("privileged_association=false")
    assert guard.count('case "$COMMENT_ASSOCIATION" in') == 1, guard
    return guard.split('case "$COMMENT_ASSOCIATION" in', 1)[1].split("esac", 1)[0]


def test_the_doctor_access_allowlist_is_written_exactly_once():
    """`doctor`'s report enumerates the guardrails a repository is missing, so
    the route is gated on the commenter's association. Five copies of the
    allowlist in five `if:` conditions is how such a gate drifts open, so it is
    computed once in the `guard` step and every doctor step keys off its
    boolean output.

    Two layers of counting, because either alone leaves a hole:

    * File-wide, the gate-opening assignment `privileged_association=true` occurs
      exactly once. Nothing anywhere in the action -- a second `case`, an `if`
      after `esac`, an alias variable -- can set the gate true for another
      association without breaking this count. (Duplicate `GITHUB_OUTPUT` keys
      are last-write-wins, so a later write really would decide the gate.)
    * Within the one `case`, the shape: exactly two branches, exactly one of
      which opens the gate, the allowlist first and the fail-closed default
      last -- so a widening branch is caught whether it is spelled literally
      (`CONTRIBUTOR)`), with glob metacharacters (`[A-Z]*OR)`, `!(NONE)`), or
      appended to an existing line.

    The block assertions alone would miss a widening written outside the slice;
    the file-wide count alone would miss a reordering inside it (deny branch
    first, allowlist unreachable)."""
    assert _ACTION.count(_ALLOWLIST) == 1, _ACTION.count(_ALLOWLIST)
    assert _ACTION.count("privileged_association=true") == 1, _ACTION.count(
        "privileged_association=true"
    )
    block = _guard_case_block()
    assert block.count(";;") == 2, block
    assert block.count("privileged_association=true") == 1, block
    assert block.count("privileged_association=false") == 1, block
    branches = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
    assert len(branches) == 2, branches
    assert branches[0].startswith(f'{_ALLOWLIST}) echo "privileged_association=true"'), branches
    # Fails closed on an absent `comment` context (empty string) and on every
    # association not named above -- the default must be the deny branch.
    assert branches[-1].startswith('*) echo "privileged_association=false"'), branches
    # The association reaches bash through env:, like every other author-derived
    # value (test_no_template_expr_in_run enforces the second half globally).
    assert "COMMENT_ASSOCIATION: ${{ github.event.comment.author_association }}" in _step(
        "privileged_association=false"
    )


def test_every_doctor_route_step_is_gated_on_the_association():
    """Derived from the action, not from a list of step names: a new step gated
    on the doctor route that forgets the gate fails here. The count is asserted
    too, so a derivation that silently stops matching cannot read as coverage.

    This covers only steps conditioned on the route; a step that touches
    doctor's machinery under some other condition (or none) is caught by
    test_every_step_that_touches_doctor_machinery_is_gated instead."""
    steps = _steps_conditioned_on("doctor")
    assert len(steps) == 7, [n for n, _ in steps]
    for name, cond in steps:
        assert _GATE in cond, name
    # Exactly one step runs when the gate is closed (the rejection); every
    # other doctor step demands it open. An inverted condition on a probe step
    # would satisfy a bare "references the gate" assertion.
    rejects = [n for n, c in steps if f"{_GATE} != 'true'" in c]
    assert len(rejects) == 1, rejects
    for name, cond in steps:
        if name not in rejects:
            assert f"{_GATE} == 'true'" in cond, name


def test_every_step_that_touches_doctor_machinery_is_gated():
    """Keyed on what a step *does*, not on how it is conditioned -- the
    complement to the route-derived guard above, which by construction cannot
    see a step with no `if:` at all, or one gated only on
    `steps.doctortoken.outcome`. Either shape would still mint/use the App
    token, run `scripts/doctor`, or read the settings the report discloses."""
    steps = _ACTION.split("\n    - name:")
    hits = [s for s in steps if any(m in s for m in _DOCTOR_TOUCHES)]
    # Sanity floor: gatherdoc and the render step both qualify today, so an
    # empty or single hit means the marker list has gone stale and this guard
    # is inspecting nothing.
    assert len(hits) >= 2, len(hits)
    for s in hits:
        assert _GATE in s, s.splitlines()[0]


def test_a_rejected_doctor_commenter_is_told_with_the_workflow_token():
    """Silence is indistinguishable from a broken engine. The rejection must
    also not depend on the App (which may not be installed) and must disclose
    no probe results."""
    block = _step(_CLAIM)
    assert "inputs.github-token" in block
    assert f"{_GATE} != 'true'" in block
    assert "app-id" not in block
    # The malformed/reserved rejection is a different step with a different
    # condition -- this one must not have absorbed it.
    assert "is_command" not in block


def test_the_shipped_help_text_matches_the_gate_it_describes():
    """`help_markdown()`'s footer ships inside the help comment every commenter
    can request, and it asserts that `doctor` is restricted. Nothing else
    couples that shipped claim to the action, so a later relaxation of the gate
    would leave the engine telling commenters something untrue. Pin the three
    user-visible statements of the rule together: the help footer, the refusal
    comment, and the allowlist that actually enforces it."""
    footer = cp.help_markdown().rsplit("\n", 1)[-1]
    assert _CLAIM in footer, footer
    assert _CLAIM in _ACTION
    assert _ALLOWLIST in _ACTION


def test_help_is_not_gated_on_the_association():
    """`help` discloses nothing about the repository, and is most needed by
    someone whose setup is broken -- gating it would be a regression."""
    steps = _steps_conditioned_on("help")
    assert steps, "no help-only step found"
    for name, cond in steps:
        assert _GATE not in cond, name


def _code(block):
    """`block` with its shell comment lines dropped. These assertions are about
    what the step *runs*; the prose explaining why an operator was removed would
    otherwise keep tripping a substring check for that operator."""
    return "\n".join(ln for ln in block.splitlines() if not ln.strip().startswith("#"))


def _report_ctx():
    return {
        "head_sha": "f" * 40,
        "plan_run_id": "1",
        "envs": {"dev-eu"},
        "harvest_failed": False,
        "harvest_pending": False,
    }


def test_the_doctor_upsert_anchors_the_marker_at_the_body_start():
    """`render_report` emits `DOCTOR_MARKER` as the body's first line, so the
    sticky lookup must anchor there. A `contains` match also hits any comment
    that merely *quotes* the marker — the sticky plan comment embeds `tofu
    plan` output verbatim, so a plan that happens to contain
    `<!-- shipmate:doctor -->` would be selected and PATCHed with the doctor
    report: the plan comment destroyed, and the summary marker orphaned onto a
    comment that no longer carries it."""
    code = _code(_step("body=@doctor.md"))
    assert "startswith" in code
    assert "contains" not in code
    assert doctor.render_report([], [], _report_ctx()).splitlines()[0] == doctor.DOCTOR_MARKER


def test_the_doctor_upsert_does_not_swallow_a_comment_listing_failure():
    """`|| true` on the id lookup turned a failed listing into an empty id,
    which fell through to the create branch: a second marker-bearing Bot
    comment, and every later run PATCHing whichever one the listing happened to
    return first. The `|| true` was only there to dodge EPIPE from `head` under
    `pipefail`, so the pipe goes, not the error check — and a listing failure
    skips the post entirely so the next run recovers."""
    code = _code(_step("body=@doctor.md"))
    assert "|| true" not in code
    assert "| head -n1" not in code  # no pipe, so no EPIPE to swallow
    assert "if ! gh api" in code
    degrade = code.split("if ! gh api", 1)[1].split("fi", 1)[0]
    assert "::warning::" in degrade
    assert "exit 0" in degrade
    # The skip must precede both writes, or it is not a skip.
    assert code.index("exit 0") < code.index("-X PATCH")
    assert code.index("exit 0") < code.index('issues/$PR_NUMBER/comments" -F body=@doctor.md')


def _mint_with(action, step_id):
    """The parsed `with:` mapping of the mint step whose id is exactly
    `step_id`.

    Two things a text slice from `id: <step_id>` cannot do. It matches any id
    that merely *starts* with the name, so a future `id: token-refresh` placed
    above the real mint retargets the slice and every permission assertion then
    passes against the wrong step while the real mint's grant can be deleted
    unnoticed. And it reads raw file text, where a commented-out
    `# permission-environments: read` satisfies the same assertion as the live
    key. So: exact id, asserted to really be a mint, compared as parsed values.
    """
    steps = [s for s in action_steps(action) if s.get("id") == step_id]
    assert len(steps) == 1, f"expected exactly one `id: {step_id}` step in {action}, got {steps}"
    uses = steps[0].get("uses") or ""
    assert "actions/create-github-app-token" in uses, (
        f"{action}'s `{step_id}` step is not an App-token mint (uses: {uses!r}) — "
        "the permission assertions against it would pin nothing"
    )
    return steps[0].get("with") or {}


def _requested_permissions(mint_with):
    """`{permission: level}` actually requested by a mint, from its parsed
    inputs — so a commented-out request is absent, as it is at runtime."""
    return {
        k.removeprefix("permission-"): v
        for k, v in mint_with.items()
        if k.startswith("permission-")
    }


def test_both_doctor_token_mints_request_actions_read():
    """The environment probes read `repos/{repo}/environments` and, per
    environment, `.../environments/{name}`. The environments *list* has been
    observed working under a token without Actions read, but the
    per-environment read needs it on some configurations — and a degraded
    environment probe is invisible in the report except as a "could not
    verify" note. `app/manifest.json` already declares `actions: write`, so
    `actions: read` is a subset of the installation's granted set and needs no
    re-approval; both mints that drive doctor request it.

    Both sites, because they are separate mints: `actions/summary`'s token
    drives `annotate` mode on every plan run, `comment-ops`' `doctortoken`
    drives `report` mode on demand, and a fix applied to only one leaves half
    the environment probes degraded."""
    assert _mint_with("comment-ops", "doctortoken").get("permission-actions") == "read"
    # Selected by exact id, like the doctor mint above: `actions/summary` has a
    # second create-github-app-token step (the environments-scoped one), so "the
    # first mint in the file" would be positional rather than named.
    assert _mint_with("summary", "token").get("permission-actions") == "read"


def test_the_doctor_token_can_comment_on_a_pull_request():
    """The report is posted to `/issues/{pr}/comments`, but a pull request
    comment is governed by the pull-requests permission, not issues: a token
    holding only `issues: write` gets 403 "Resource not accessible by
    integration" there, which is how this first shipped. Both other mints that
    post a comment (`actions/summary`, `actions/apply-summary`) request
    pull-requests, so pin the doctor mint to the same permission and pin the
    absence of the one that does not work."""
    mint = _mint_with("comment-ops", "doctortoken")
    assert mint.get("permission-pull-requests") == "write"
    assert "permission-issues" not in mint


def test_fullmint_requests_the_manifests_exact_permission_set():
    """The full-set probe mint must mirror app/manifest.json: a manifest bump
    that skips this step makes the permission-drift probe test the stale set —
    exactly the drift the probe exists to catch.

    Parsed inputs, not a regex over the file text: `# permission-environments:
    read` in a comment matched the live key exactly as well, so a commented-out
    request kept this guard green while the mint asked for nothing."""
    requested = _requested_permissions(_mint_with("comment-ops", "fullmint"))
    declared = {k.replace("_", "-"): v for k, v in _MANIFEST_PERMISSIONS.items()}
    assert requested == declared


#: doc -> the (start, end) markers bounding its prose permission list. Bounded
#: rather than searched whole-file: `docs/github-app.md` also mentions the App's
#: "`statuses: write` gate POST" further down, so an unbounded substring
#: assertion would stay green with `statuses` deleted from the list itself. Both
#: markers are asserted present below — a vanished `end` would silently widen the
#: slice to the rest of the file and restore exactly that fail-open behaviour.
_PROSE_PERMISSION_LISTS = {
    "CONTRACT.md": ("carries this permission set:", "Beyond minting"),
    "docs/github-app.md": ("- Permissions:", "\n- No webhook events"),
}


def test_both_prose_permission_lists_name_every_manifest_permission():
    """`app/manifest.json` is what GitHub grants; two documents spell the same
    set out in prose for readers, and `CLAUDE.md` names `CONTRACT.md` the single
    source for the contract. The `fullmint` guard above pins only the action
    YAML, so nothing noticed when a manifest bump left `CONTRACT.md` listing
    seven permissions — this pins both lists against the manifest instead.

    Both bounding markers are asserted before the slice is taken. A missing
    `start` would search from the top of the file and a missing `end` would
    search to the bottom, and either widening makes the assertion satisfiable by
    a mention outside the list — the guard would keep passing while pinning
    nothing."""
    for doc, (start, end) in _PROSE_PERMISSION_LISTS.items():
        text = (ENGINE / doc).read_text(encoding="utf-8")
        assert start in text, (
            f"{doc}: the marker opening the permission list, {start!r}, is gone — "
            "the prose moved or was reworded; re-point _PROSE_PERMISSION_LISTS at it. "
            "This is not a permission going missing."
        )
        after = text.split(start, 1)[1]
        # `after`, not `text`: an `end` that only occurs *before* the list would
        # leave the slice running to the end of the file just as an absent one does.
        assert end in after, (
            f"{doc}: the marker closing the permission list, {end!r}, no longer follows "
            f"{start!r} — the prose moved or was reworded; re-point "
            "_PROSE_PERMISSION_LISTS at it. Left unfixed the slice would run to the end "
            "of the file and this guard would pin nothing. This is not a permission "
            "going missing."
        )
        listing = after.split(end, 1)[0]
        for name, level in _MANIFEST_PERMISSIONS.items():
            assert f"`{name}: {level}`" in listing, (
                f"{doc}'s permission list does not name `{name}: {level}`"
            )


def _authorize_step():
    step = next(s for s in action_steps("comment-ops") if s.get("name") == "Authorize")
    assert step.get("env"), "the Authorize step declares no env: block"
    return step


def _gather_step():
    step = next(s for s in action_steps("comment-ops") if s.get("id") == "gather")
    assert step.get("run"), "the gather step runs no shell"
    return step


def test_the_authorize_step_reads_the_files_the_gather_step_writes(tmp_path, monkeypatch):
    """`env:` key -> `os.environ` key -> file path, run rather than eyeballed. A
    rename on either side leaves authorize reading a file nobody writes, and
    `_read_json`'s missing-file default then refuses every apply -- or, for the
    PR, reads an empty mapping as an unmergeable pull request."""
    env = _authorize_step()["env"]
    body = _gather_step()["run"]
    for key, content in (
        ("PR_JSON", '{"mergeable": true, "mergeable_state": "clean", "head": {"sha": "abc123"}}'),
        ("PLAN_RUN_JSON", '{"apply / stacks/app / dev-eu": "555"}'),
    ):
        path = env[key]
        assert f"> {path}\n" in body, f"the gather step writes no {path}"
        (tmp_path / path).write_text(content, encoding="utf-8")
        monkeypatch.setenv(key, path)
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "IS_MEMBER": "true",
        "APPROVERS_TEAM": "deployers",
        "REVIEW_DECISION": "NONE",
        "GITHUB_OUTPUT": "out.txt",
    }.items():
        monkeypatch.setenv(key, value)
    load_script("authorize").main()
    assert "authorized=true" in (tmp_path / "out.txt").read_text(encoding="utf-8")


def test_the_gather_step_reads_the_plan_runs_from_the_heads_own_check_runs():
    """The reviewed-plan lookup is the head's own apply checks, each of which
    records the plan run its plan came from. The plan-workflow lookup it replaced
    resolved one run for the whole command and had to compare that run's head
    against the PR's -- a comparison this listing makes unnecessary and, being
    keyed on the head already, unfalsifiable."""
    body = _code(_gather_step()["run"])
    assert '"repos/$GITHUB_REPOSITORY/commits/$head/check-runs?filter=all&per_page=100"' in body
    assert "workflows/plan.yml" not in body
    # The mapping comes from apply-gate's `--plan-runs` mode: without the flag the
    # same invocation writes a gate verdict and leaves plan_run.json empty.
    assert (
        '| python3 "$GITHUB_ACTION_PATH/../../scripts/apply-gate" --plan-runs > plan_run.json'
    ) in body


def test_the_gather_step_receives_the_app_id_the_plan_run_lookup_scopes_on():
    """Only our App's apply checks may name a plan run: a same-name check from
    any other identity must not decide what a `shipmate apply` applies."""
    assert _gather_step()["env"]["SHIPMATE_APP_ID"] == "${{ inputs.app-id }}"


def test_authorize_step_receives_the_ungated_envs_input():
    assert _authorize_step()["env"]["SHIPMATE_UNGATED_ENVS"] == "${{ inputs.ungated-envs }}"


def test_the_ungated_envs_input_is_optional_and_defaults_to_empty():
    """A consumer who never writes the `ungated-envs:` line must keep the branch
    ruleset's review requirement on every environment, so the input has to be
    optional AND default to the empty string that `parse_ungated_envs` reads as
    "nothing is exempt"."""
    spec = action_yaml("comment-ops")["inputs"]["ungated-envs"]
    assert spec["required"] is False
    assert spec["default"] == ""


#: The exemption report's whole body, hand-written. It claims PERMISSION and
#: never completion -- at comment time the dispatch has not run, so any verb
#: about the outcome would be a claim this step cannot make.
_EXEMPTION_BODY = (
    ":memo: shipmate: environment \\`$ENVIRONMENT\\` is permitted to apply "
    "without an approving review, per the \\`SHIPMATE_UNGATED_ENVS\\` repository "
    "variable — see the apply result comment for what actually applied."
)


def _exemption_step():
    return next(
        s for s in action_steps("comment-ops") if s.get("name") == "Report the review exemption"
    )


def test_the_exemption_report_fires_only_when_the_exemption_fired():
    """Not on `authorized == 'true'`: an ordinary reviewed apply is authorized
    too, and this sentence over it would be false."""
    assert _exemption_step()["if"] == "${{ steps.authz.outputs.ungated_exemption == 'true' }}"
    assert _exemption_step()["env"]["ENVIRONMENT"] == "${{ steps.authz.outputs.environment }}"


def test_the_exemption_report_claims_permission_never_completion():
    run = _exemption_step()["run"]
    assert f'-f body="{_EXEMPTION_BODY}"' in run, (
        f"the exemption report's body is not the pinned sentence: {run!r}"
    )


def test_authorize_step_supplies_every_env_var_authorize_reads():
    """Derived from `scripts/authorize`'s own source, not a hand-listed set: a
    SHIPMATE_* name added to the script later would otherwise read as empty in
    this step and silently take the fail-closed branch."""
    src = (SCRIPTS / "authorize").read_text(encoding="utf-8")
    read = set(re.findall(r"os\.environ(?:\.get)?[\[(]['\"](SHIPMATE_[A-Z_]+)['\"]", src))
    assert read, "expected at least one SHIPMATE_* read in authorize"
    assert read <= set(_authorize_step()["env"])


#: Every step the apply route gates on, with the whole `if:` expression it must
#: carry -- hand-written, not derived from the action. `unlock` reuses these
#: steps rather than growing a parallel set, so each one has to admit exactly
#: the two routes: narrow one back and an unlock parses, authorizes and then
#: silently does nothing.
_SHARED_ROUTE_IFS = {
    "Mint App token (members:read, checks:read)": (
        "${{ steps.parse.outputs.route == 'apply' || steps.parse.outputs.route == 'unlock' }}"
    ),
    "App token unavailable (App not installed?)": (
        "${{ (steps.parse.outputs.route == 'apply' || steps.parse.outputs.route == 'unlock')"
        " && steps.apptoken.outcome != 'success' }}"
    ),
    "Gather authorization inputs": (
        "${{ (steps.parse.outputs.route == 'apply' || steps.parse.outputs.route == 'unlock')"
        " && steps.apptoken.outcome == 'success' }}"
    ),
    "Authorize": (
        "${{ (steps.parse.outputs.route == 'apply' || steps.parse.outputs.route == 'unlock')"
        " && steps.apptoken.outcome == 'success' }}"
    ),
    "Reject with reason": (
        "${{ (steps.parse.outputs.route == 'apply' || steps.parse.outputs.route == 'unlock')"
        " && steps.apptoken.outcome == 'success' && steps.authz.outputs.authorized != 'true' }}"
    ),
}


def test_the_apply_route_steps_admit_exactly_apply_and_unlock():
    matched = {
        s["name"]: s.get("if")
        for s in action_steps("comment-ops")
        if s.get("name") in _SHARED_ROUTE_IFS
    }
    assert matched == _SHARED_ROUTE_IFS


def test_the_mode_output_names_the_dispatch_mode_the_route_selects():
    """The caller's dispatch step keys off this output, so a literal here sends
    every unlock down the apply path."""
    assert action_yaml("comment-ops")["outputs"]["mode"]["value"] == (
        "${{ steps.parse.outputs.route == 'unlock' && 'unlock' || 'apply' }}"
    )


#: What both verb-carrying steps must bind SHIPMATE_VERB to, hand-written. The
#: env-var guard above is a subset-of-KEYS check and cannot see the value: with
#: `Authorize`'s binding hardcoded to a literal `unlock`, every `shipmate apply`
#: authorizes on team membership alone -- no mergeable, no review policy, no
#: reviewed plan -- and the whole suite stays green.
_VERB_BINDING = "${{ steps.parse.outputs.route }}"


def test_both_verb_steps_bind_shipmate_verb_to_the_parsed_route():
    """One selector for the property, so the two occurrences cannot disagree:
    a literal on either step, or a deleted line, fails this dict equality."""
    bound = {
        s["name"]: (s.get("env") or {}).get("SHIPMATE_VERB")
        for s in action_steps("comment-ops")
        if s.get("name") in {"Gather authorization inputs", "Authorize"}
    }
    assert bound == {
        "Gather authorization inputs": _VERB_BINDING,
        "Authorize": _VERB_BINDING,
    }


#: The comment-ops verb table in CONTRACT.md: the header row that opens it and
#: the blank line that ends it. Bounded rather than whole-file, so a `shipmate
#: <verb>` mention in the surrounding prose cannot satisfy the guard.
_VERB_TABLE = ("| verb | status | args | authorization |", "\n\n")


def test_the_contract_verb_table_carries_every_active_verb():
    """CONTRACT.md calls `VERBS` "the single source of truth this table is
    derived from", but the table is hand-maintained -- so it drifted. Derived
    from the registry here, never a hand-written verb list, so the next verb
    added cannot repeat it."""
    start, end = _VERB_TABLE
    text = (ENGINE / "CONTRACT.md").read_text(encoding="utf-8")
    assert start in text, f"the verb table's header row, {start!r}, is gone"
    table = text.split(start, 1)[1].split(end, 1)[0]
    active = {v: s for v, s in cp.VERBS.items() if s["status"] == cp.ACTIVE}
    assert active, "expected at least one active verb in the registry"
    for verb, spec in active.items():
        invocation = " ".join(filter(None, ("shipmate", verb, spec["args"])))
        assert f"| `{invocation}` | active |" in table, (
            f"CONTRACT.md's verb table has no active row for `{invocation}`"
        )
