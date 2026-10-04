"""Source-derived guards on the comment-ops action's routing wiring.

The action branches on `steps.parse.outputs.route`; the routes come from
comment-parse's VERBS registry. A verb added to the registry with no branch in
the action would parse, authorize, and then silently do nothing.
"""

import json
import os
import re
import shlex
import sys

import pytest
from _loader import (
    ACTIONS,
    ENGINE,
    SCRIPTS,
    action_steps,
    action_yaml,
    bash_only,
    load_script,
    run_step,
    step_by,
)

_ACTION_FILE = ACTIONS / "comment-ops" / "action.yml"
_ACTION = _ACTION_FILE.read_text(encoding="utf-8")
_SUMMARY_ACTION = (ACTIONS / "summary" / "action.yml").read_text(encoding="utf-8")
_MANIFEST_PERMISSIONS = json.loads((ENGINE / "app" / "manifest.json").read_text(encoding="utf-8"))[
    "default_permissions"
]

# The verdict of the one permission decision, `access`, which every gated route keys on.
_GATE = "steps.access.outputs.authorized"
#: Every valid command but `help`, hand-written: `permission` and `access` run on exactly these.
#: The route is empty for a non-command, an invalid command and a bot's comment, and a verb added
#: later is gated by default: `access` exits `unknown verb` on it, which fails closed.
_GATED_ROUTES_IF = "steps.parse.outputs.route != '' && steps.parse.outputs.route != 'help'"
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
    expected = {verb for verb, spec in cp.VERBS.items() if spec["status"] == cp.ACTIVE}
    assert expected <= routed, expected - routed


def test_bot_authored_comments_are_ignored():
    assert '== *"[bot]"' in _ACTION
    # The literal match above is inert on its own: Parse command must be skipped when the
    # guard trips, or the loop guard is decorative and every later step keys off an empty
    # parse output.
    assert "steps.guard.outputs.skip != 'true'" in _ACTION


#: The one SHIPMATE_* name doctor reads that no action supplies: the workflow step that runs the
#: action sets it from `job.workflow_repository`, a context no composite action can read
#: (test_neither_doctor_step_passes_the_engine_repo).
_NOT_SUPPLIED = {"SHIPMATE_ENGINE_REPO"}


def test_doctor_step_supplies_every_env_var_doctor_reads():
    """comment-ops' doctor steps must supply every SHIPMATE_* name `doctor` reads at all (subscript
    or .get), report mode's full env contract."""
    src = (SCRIPTS / "doctor").read_text(encoding="utf-8")
    read = set(re.findall(r"os\.environ(?:\.get)?[\[(]['\"](SHIPMATE_[A-Z_]+)['\"]", src))
    assert read >= _NOT_SUPPLIED, "exemption names a variable doctor no longer reads"
    for name in read - _NOT_SUPPLIED:
        assert name in _ACTION, name


def test_summary_action_supplies_every_env_var_doctor_requires_in_annotate_mode():
    """`ctx_from_env()` runs in both `annotate` and `report` mode, and a name read via subscript
    (`os.environ["SHIPMATE_..."]`, not `.get(...)`) is one annotate mode cannot run without.
    `actions/summary` only ever drives `annotate` mode -- it has no doctor `report`/`check-ids`
    steps of its own -- so its `env:` block must cover this required subset. It does not fold into
    test_doctor_step_supplies_every_env_var_doctor_reads, whose full (subscript + .get) read-set of
    `actions/comment-ops/action.yml` passes even if summary drops a name."""
    src = (SCRIPTS / "doctor").read_text(encoding="utf-8")
    required = set(re.findall(r"os\.environ\[['\"](SHIPMATE_[A-Z_]+)['\"]\]", src))
    assert required, "expected at least one required SHIPMATE_* read in doctor"
    for name in required:
        assert name in _SUMMARY_ACTION, name


def test_neither_doctor_step_passes_the_engine_repo():
    """`github.action_repository` is empty for a `$/` action, so passing it fed doctor an empty
    slug and degraded the pin probe to "not verified" on both paths. The calling step supplies
    the slug from `job.workflow_repository` instead
    (test_engine_self_reference.py::test_the_doctor_steps_pass_the_engine_repository); an action
    re-introducing the variable would shadow it.
    Mutation: add `SHIPMATE_ENGINE_REPO:` back to either action."""
    assert "SHIPMATE_ENGINE_REPO" not in _ACTION
    assert "SHIPMATE_ENGINE_REPO" not in _SUMMARY_ACTION


def test_doctor_runs_in_report_mode_with_the_app_token():
    assert "SHIPMATE_DOCTOR_MODE: report" in _ACTION
    assert "SHIPMATE_DOCTOR_MODE: check-ids" in _ACTION
    assert "steps.doctortoken.outputs.token" in _ACTION


def test_doctor_sticky_marker_matches_the_script():
    assert doctor.DOCTOR_MARKER in _ACTION


def test_the_rendered_report_reaches_the_job_summary_before_the_comment_post():
    """The report costs 30-60s of probes, and both post paths can lose it: a failed comment listing
    `exit 0`s by design, so the run stays green with no report anywhere, and a failed PATCH/POST
    reds the step after the render. Writing `doctor.md` to the job summary first means the run page
    always carries the report even when the comment does not.

    Read through `_code`, so the rationale comment above the write -- which names both `doctor.md`
    and the variable -- cannot satisfy these assertions on prose alone; without that, deleting the
    write would leave the comment as the sole match.

    Kills dropping the write, writing something other than the report, truncating instead of
    appending, and moving the write below the listing, where the early `exit 0` skips it."""
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
    """The summary write must degrade rather than abort: a bare `cat` under the step's `set -euo
    pipefail` turns an unwritable summary into a red step with no comment either, strictly worse
    than writing none. `|| true` is not available here (see
    test_the_doctor_upsert_does_not_swallow_a_comment_listing_failure), so the degrade is an
    `if`-guard that warns, and the variable is read with `:-` so `set -u` cannot kill the step on a
    runner that never exports it."""
    code = _code(_step("SHIPMATE_DOCTOR_MODE: report"))
    write = next(ln for ln in code.splitlines() if "$GITHUB_STEP_SUMMARY" in ln)
    assert write.lstrip().startswith("if "), write
    assert "${GITHUB_STEP_SUMMARY:-}" in write, write
    # The report still has to be posted after a failed summary write, so the
    # guard must warn and carry on rather than exit.
    assert "::warning::" in code.split(write, 1)[1].split("fi", 1)[0]


def test_help_does_not_require_the_app():
    """help must answer even when the App is not installed — the state where a newcomer most needs
    it — so it posts with the workflow token."""
    post = step_by("comment-ops", name="Post help")
    assert post["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
    }


def _step(marker):
    """The action.yml step block containing `marker` -- so a guard on one step's wiring cannot be
    satisfied by a coincidental match in another step."""
    steps = _ACTION.split("\n    - name:")
    matches = [s for s in steps if marker in s]
    assert len(matches) == 1, f"{marker!r} appears in {len(matches)} steps"
    return matches[0]


def test_the_routes_that_change_no_infrastructure_are_acknowledged_with_a_reaction():
    """`doctor` spends 30-60s in API calls before its comment appears, and a `plan` is a dispatched
    run that takes as long to surface. Without an acknowledgement on the triggering comment the
    commenter's next move is to comment again, so every route that changes no infrastructure reacts
    -- and a failed reaction (comment deleted, reactions disabled) must not fail the command."""
    block = _step("content=eyes")
    assert "steps.parse.outputs.route == 'doctor'" in block
    assert "steps.parse.outputs.route == 'help'" in block
    assert "steps.parse.outputs.route == 'plan'" in block
    # On the invocation itself, not somewhere in the block: the step's own comment explains
    # the `|| true`, so a bare substring check for it stays green when the operator is
    # deleted from the `gh api` line.
    assert "-f content=eyes >/dev/null || true" in block


def test_the_rocket_reaction_stays_on_an_authorized_dispatch():
    # `eyes` = accepted a command that changes no infrastructure, `rocket` = a dispatch was
    # authorized. The two must not collapse into one signal. It reads both routes' verdicts,
    # not the apply route's alone: keyed on `authz` it stays silent on authorized plans.
    # Mutation: `||` -> `&&` in React on accept's `if:`.
    [step] = [s for s in action_steps("comment-ops") if "content=rocket" in s.get("run", "")]
    assert step["if"] == _AUTHORIZED


@bash_only
def test_a_failed_rocket_reaction_does_not_fail_the_authorized_command(tmp_path):
    """A failed step skips the caller's dispatch, so a failed cosmetic reaction would drop an
    authorized command.

    Mutation: drop `|| true` from the `rocket` POST -- the step exits non-zero with no notice.
    """
    gh_path = tmp_path / "gh"
    gh_path.write_text("#!/bin/bash\nexit 1\n")
    gh_path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["GH_TOKEN"] = "test_token"  # noqa: S105
    env["COMMENT_ID"] = "7"
    env["GITHUB_REPOSITORY"] = "org/repo"

    result = run_step(tmp_path, step_by("comment-ops", name="React on accept")["run"], env)
    assert (result.returncode, result.stdout) == (
        0,
        "::notice title=comment-ops::command authorized (the caller's next step dispatches it)\n",
    )


def test_summary_doctor_step_reads_the_head_sha_it_was_given():
    """annotate mode must probe the same commit the plan ran on: without SHIPMATE_HEAD_SHA the pin
    probe's contents reads fall back to the default branch, where a pin bump on this PR is not
    visible yet."""
    steps = _SUMMARY_ACTION.split("\n    - name:")
    block = next(s for s in steps if "SHIPMATE_DOCTOR_MODE: annotate" in s)
    assert "SHIPMATE_HEAD_SHA: ${{ inputs.head-sha }}" in block


def test_unreadable_head_sha_marks_the_harvest_failed_before_exiting():
    """The read-only degrade path for an unreadable PR head SHA must record harvest_failed=true, and
    head_sha/plan_run_ids, to GITHUB_OUTPUT before it exits, or the render step reads an empty
    SHIPMATE_HARVEST_FAILED and the sticky comment claims the warning harvest ran clean.

    Sliced from the `if [[ ! "$head"` condition itself, not from the step's start, and pinned to a
    single `exit 0` in the step: all three substrings also appear elsewhere in the step for other
    paths, so a slice from the start stays green with the degrade branch deleted."""
    block = _ACTION.split("id: gatherdoc", 1)[1].split("- name:", 1)[0]
    assert block.count("exit 0") == 1
    degrade = block.split('if [[ ! "$head"', 1)[1].split("exit 0", 1)[0]
    assert "head_sha=" in degrade
    assert "plan_run_ids=" in degrade
    assert "harvest_failed=true" in degrade


def test_harvest_failed_env_falls_back_to_true_when_gatherdoc_did_not_run():
    """The render step's `if:` keys only on `route` and `doctortoken.outcome`, not on
    `gatherdoc.outcome`, so if gatherdoc reds or is skipped its `harvest_failed` output is unset. An
    empty string is falsy in a GHA expression and a literal 'false' is truthy, so `|| 'true'` treats
    "gatherdoc did not run" as a failed harvest, passing a real `false` through unchanged."""
    assert (
        "SHIPMATE_HARVEST_FAILED: ${{ steps.gatherdoc.outputs.harvest_failed || 'true' }}"
        in _ACTION
    )


def test_the_check_runs_projection_carries_every_field_this_step_answers_from_it():
    """One listing answers every question doctor asks about this head, so its projection carries all
    of them: `status` for the harvest-pending flag (without it every run reads as unfinished and the
    report tells every commenter to come back later, forever), `started_at` for check-ids' ranking,
    `external_id` for the plan record each apply check carries, and the nested `app` object
    apply-gate's fail-closed App filter reads -- the flattened `app_slug`/`app_id` pair beside it is
    what doctor's own reducer reads."""
    assert _projection(_gatherdoc_step()["run"]) == _CHECK_RUNS_PROJECTION


def test_a_check_run_in_that_shape_yields_its_plan_run():
    """Why `_CHECK_RUNS_PROJECTION` carries `app: {id: .app.id}` and not only the flattened pair
    doctor's reducer reads: `plan_runs_by_name` filters on the NESTED id, so a projection carrying
    only `external_id` maps every name to nothing. This test cannot see the projection -- it is a
    hand-written line, reddening on the reader rather than on projection drift, and
    test_the_check_runs_projection_carries_every_field_this_step_answers_from_it is what pins the
    file."""
    line = json.dumps(
        {
            "id": 7,
            "name": "apply / stacks/app / dev-eu",
            "status": "completed",
            "started_at": "2026-08-24T00:00:00Z",
            "external_id": json.dumps({"fingerprint": "a" * 64, "plan_run": "1281"}),
            "app": {"id": 4326562},
            "app_slug": "shipmate",
            "app_id": 4326562,
        }
    )
    mapping = load_script("apply-gate").plan_runs_by_name([line], "4326562")
    assert mapping == {"apply / stacks/app / dev-eu": "1281"}


def test_the_plan_records_are_read_from_the_listing_before_any_cell_download():
    """The runs to download from are derived from the listing, so the listing has to be fetched
    first — and read through apply-gate's `--plan-runs` mode, the one reader of the `external_id`
    record."""
    body = _code(_gatherdoc_step()["run"])
    assert body.index("> check-runs.jsonl") < body.index("gh run download")


def test_the_doctor_lookup_reads_the_record_through_the_one_reader():
    """`plan_runs_by_name`, behind apply-gate's `--plan-runs` mode, is the only reader of the
    `external_id` record; a second reader here would be a second definition of what a usable record
    is."""
    body = _code(_gatherdoc_step()["run"])
    assert '"$GITHUB_ACTION_PATH/../../scripts/apply-gate" --plan-runs' in body


def test_the_doctor_lookup_asks_the_plan_workflow_for_nothing():
    """doctor read the cell summaries of the newest successful run of `.github/workflows/plan.yml`,
    which a consumer is free to rename and which says nothing about whether that run planned this
    head. The head's own apply checks name their plan runs, so no workflow-path lookup is left."""
    assert "workflows/plan.yml" not in _code(_gatherdoc_step()["run"])


def test_the_render_step_reads_the_harvest_pending_flag_the_reduction_wrote():
    """`check-ids` mode writes `harvest_pending` as a step output of the gather step; the render
    step must read it into `SHIPMATE_HARVEST_PENDING`, or the flag is computed and thrown away and
    the false all-clear comes back. No `|| 'true'` fallback here, unlike SHIPMATE_HARVEST_FAILED: if
    gatherdoc never ran there is no harvest at all and `harvest_failed` already says so, so a second
    "some runs may still be going" is noise.

    The writer half -- that `check-ids` mode emits that step output -- is covered by
    test_doctor.py::test_check_ids_mode_writes_the_harvest_pending_step_output."""
    assert "SHIPMATE_HARVEST_PENDING: ${{ steps.gatherdoc.outputs.harvest_pending }}" in _ACTION


def test_the_render_step_reads_the_id_set_the_gather_step_published():
    """A typo in this reference (`outputs.plan_run_id`) is an empty string, and doctor then reports
    that the commit carries no plan records -- the silent degrade the plural rename exists to make
    loud. test_doctor_step_supplies_every_env_var_doctor_reads only checks that the NAME appears
    somewhere in the action."""
    assert "SHIPMATE_PLAN_RUN_IDS: ${{ steps.gatherdoc.outputs.plan_run_ids }}" in _ACTION


def test_harvest_flag_is_set_inside_the_loop_and_written_once_after_it():
    """The annotations loop's per-id failure fallback (`echo '[]'`) is byte-identical to "this check
    run had no annotations", so the loop must flip the shared `harvest_failed` shell variable -- and
    that variable must be written to GITHUB_OUTPUT exactly once, after the loop, so a harvest with
    zero check-run ids still writes it and a per-id failure is not overwritten by a later clean
    iteration.

    Kills both mutations: dropping the in-loop `harvest_failed=true` fails the loop-body assertion,
    and moving the GITHUB_OUTPUT write inside the loop fails the ordering and loop-body redirect
    assertions."""
    block = _ACTION.split("id: gatherdoc", 1)[1].split("- name:", 1)[0]
    assert block.count("harvest_failed=$harvest_failed") == 1
    assert block.index("harvest_failed=$harvest_failed") > block.index("done < check-ids.tsv")
    loop_body = block.split("while IFS=", 1)[1].split("done <", 1)[0]
    assert "harvest_failed=true" in loop_body
    assert '>> "$GITHUB_OUTPUT"' not in loop_body


def _steps_conditioned_on(route):
    """(name, if-expression) for every step whose `if:` names this route and no other -- derived
    from the file, so a step added later is covered without anyone extending a hardcoded list
    here. Steps shared with another route (the `eyes` acknowledgement, the permission read and
    decision) are excluded, so a shared step is never counted as one route's."""
    other = {"doctor", "help", "apply", "plan", "unlock"} - {route}
    out = []
    for step in action_steps("comment-ops"):
        cond = step.get("if") or ""
        if f"outputs.route == '{route}'" not in cond:
            continue
        if any(f"outputs.route == '{o}'" in cond for o in other):
            continue
        out.append((step.get("name"), cond))
    return out


#: The whole `env:` of the permission read and of its decision, hand-written. The read runs on
#: the workflow token; the decision reads only the read's value and the parsed route.
_PERMISSION_ENV = {"GH_TOKEN": "${{ github.token }}", "USER": "${{ inputs.comment-user }}"}
_ACCESS_ENV = {
    "PERMISSION": "${{ steps.permission.outputs.permission }}",
    "SHIPMATE_VERB": "${{ steps.parse.outputs.route }}",
}
_ACCESS_RUN = 'python3 "$GITHUB_ACTION_PATH/../../scripts/authorize" --permission'


def test_the_permission_is_read_and_decided_exactly_once():
    """One read and one decision serve `plan`, `doctor`, `apply` and `unlock`; a second copy is
    how the four routes' rules drift apart, and `help` makes no call at all.

    Mutations: copy the read into `gather` (`collaborators/` appears twice); change
    `!= 'help'` to `!= 'doctor'` in either `if:`; drop `steps.parse.outputs.route != '' && `
    from `permission`'s `if:`; set `access`'s `SHIPMATE_VERB` to `apply`."""
    assert _ACTION.count("collaborators/") == 1, _ACTION.count("collaborators/")
    permission = step_by("comment-ops", id="permission")
    access = step_by("comment-ops", id="access")
    assert permission["if"] == f"${{{{ {_GATED_ROUTES_IF} }}}}"
    assert access["if"] == f"${{{{ {_GATED_ROUTES_IF} }}}}"
    assert permission["env"] == _PERMISSION_ENV
    assert access["env"] == _ACCESS_ENV
    assert access["run"] == _ACCESS_RUN
    others = [
        s.get("name")
        for s in action_steps("comment-ops")
        if s.get("id") != "access" and "--permission" in (s.get("run") or "")
    ]
    assert others == [], others


def test_every_doctor_route_step_is_gated_on_write_access():
    """Derived from the action, not from a list of step names: a new step gated on the doctor route
    that forgets the gate fails here. The count is asserted too, so a derivation that silently stops
    matching cannot read as coverage. It covers only steps conditioned on the route; a step touching
    doctor's machinery under another condition, or none, is caught by
    test_every_step_that_touches_doctor_machinery_is_gated instead.

    Each step's whole `if:` is compared against `_DOCTOR_ROUTE_IFS`.

    Mutations: drop the gate from `fullmint`; invert one `== 'true'` to `!= 'true'`; make
    `fullmint`'s `if:` `... && (steps.access.outputs.authorized == 'true' ||
    steps.doctortoken.outcome == 'success')`."""
    steps = _steps_conditioned_on("doctor")
    assert len(steps) == 5, [n for n, _ in steps]
    assert dict(steps) == _DOCTOR_ROUTE_IFS


_DOCTOR_GATED = "steps.parse.outputs.route == 'doctor' && steps.access.outputs.authorized == 'true'"
_DOCTOR_MINTED = f"${{{{ {_DOCTOR_GATED} && steps.doctortoken.outcome == 'success' }}}}"
#: The whole `if:` of every doctor-only step, hand-written.
_DOCTOR_ROUTE_IFS = {
    "Mint App token for doctor": f"${{{{ {_DOCTOR_GATED} }}}}",
    "Doctor: App token unavailable": (
        f"${{{{ {_DOCTOR_GATED} && steps.doctortoken.outcome != 'success' }}}}"
    ),
    "Doctor: probe the manifest's full permission set": _DOCTOR_MINTED,
    "Doctor: gather head SHA, declared environments, annotations": _DOCTOR_MINTED,
    "Doctor: render and upsert the sticky comment": _DOCTOR_MINTED,
}


#: The whole `if:` of the two apply/unlock steps that run before any App token exists,
#: hand-written: neither may run for a commenter `access` refused.
_APP_MINT_IF = (
    "${{ (steps.parse.outputs.route == 'apply' || steps.parse.outputs.route == 'unlock')"
    " && steps.access.outputs.authorized == 'true' }}"
)
_APP_UNAVAILABLE_IF = (
    "${{ (steps.parse.outputs.route == 'apply' || steps.parse.outputs.route == 'unlock')"
    " && steps.access.outputs.authorized == 'true' && steps.apptoken.outcome != 'success' }}"
)


def test_every_apply_and_unlock_step_after_the_decision_is_gated():
    """A commenter without write access costs no App mint and no read: every apply/unlock step
    after the refusal requires `access`'s verdict, or a mint that only a permitted commenter
    reaches. The refusal step itself requires `!= 'true'` and is pinned whole by
    test_a_commenter_without_write_access_is_refused_on_every_gated_route.

    The derived set is compared whole against `_SHARED_ROUTE_IFS`, so a new step naming either
    route fails here until it is added there.

    Mutations: drop the gate from the mint (the "App token unavailable" step would then answer a
    read-only commenter); drop it from "App token unavailable" (it fires on a skipped mint)."""
    steps = action_steps("comment-ops")
    names = [s.get("name") for s in steps]
    after = steps[names.index("Reject a commenter without write access") + 1 :]
    gated = {
        s.get("name"): s.get("if")
        for s in after
        if any(f"outputs.route == '{r}'" in (s.get("if") or "") for r in ("apply", "unlock"))
    }
    assert len(gated) == 7, list(gated)
    assert gated == _SHARED_ROUTE_IFS


def test_every_step_that_touches_doctor_machinery_is_gated():
    """Keyed on what a step does, not on how it is conditioned -- the complement to
    test_every_doctor_route_step_is_gated_on_write_access, which by construction cannot see a
    step with no `if:` at all, or one gated only on `steps.doctortoken.outcome`. Either shape would
    still mint or use the App token, run `scripts/doctor`, or read the settings the report
    discloses.

    Mutation: delete the gate from the render step."""
    steps = _ACTION.split("\n    - name:")
    hits = [s for s in steps if any(m in s for m in _DOCTOR_TOUCHES)]
    # Sanity floor: gatherdoc and the render step both qualify today, so an
    # empty or single hit means the marker list has gone stale and this guard
    # is inspecting nothing.
    assert len(hits) >= 2, len(hits)
    for s in hits:
        assert _GATE in s, s.splitlines()[0]


#: The refusal step's whole `if:`, hand-written.
_WRITE_ACCESS_REFUSAL_IF = (
    f"${{{{ {_GATED_ROUTES_IF} && steps.access.outputs.authorized != 'true' }}}}"
)


def test_a_commenter_without_write_access_is_refused_on_every_gated_route():
    """Silence is indistinguishable from a broken engine, so every route `access` refuses says
    why, on the workflow token (the App may not be installed) and with no probe results. Its env
    and body are pinned through `_REPLIES`.

    Mutations: change `!= 'help'` to `!= 'doctor'`; invert the last `!=` to `==`."""
    refusal = step_by("comment-ops", name="Reject a commenter without write access")
    assert refusal["if"] == _WRITE_ACCESS_REFUSAL_IF


def test_the_shipped_help_text_matches_the_gate_it_describes():
    """`help_markdown()`'s footer ships inside the help comment every commenter can request, and it
    asserts that `doctor` and `plan` require write access. Nothing else couples that shipped claim
    to the action, so a later relaxation of the gate would leave the engine telling commenters
    something untrue. `_GATED_ROUTES_IF` excludes `help` alone, so it gates both.

    Mutation: change `!= 'help'` to `!= 'plan'` in `access`'s `if:`."""
    footer = cp.help_markdown(_RUN_URL).split("\n\n")[-2]
    last = footer.rsplit(";", 1)[1]
    for word in ("`doctor`", "`plan`", "write access"):
        assert word in last, last
    assert step_by("comment-ops", id="access")["if"] == f"${{{{ {_GATED_ROUTES_IF} }}}}"


def test_help_is_not_gated_on_write_access():
    """`help` discloses nothing about the repository, and is most needed by someone whose setup is
    broken -- gating it would be a regression, and reading the permission for it is a wasted call.

    Mutations: add `&& steps.access.outputs.authorized == 'true'` to `Post help`'s `if:`; drop
    `&& steps.parse.outputs.route != 'help'` from `permission`'s `if:`."""
    steps = _steps_conditioned_on("help")
    assert steps, "no help-only step found"
    for name, cond in steps:
        assert _GATE not in cond, name
    for step_id in ("permission", "access"):
        assert step_by("comment-ops", id=step_id)["if"] == f"${{{{ {_GATED_ROUTES_IF} }}}}"


def _code(block):
    """`block` with its shell comment lines dropped. These assertions are about what the step
    *runs*; the prose explaining why an operator was removed would otherwise keep tripping a
    substring check for that operator."""
    return "\n".join(ln for ln in block.splitlines() if not ln.strip().startswith("#"))


#: The gatherdoc listing's whole jq projection, hand-written.
_CHECK_RUNS_PROJECTION = (
    ".check_runs[] | {id, name, status, started_at, external_id, "
    "app: {id: .app.id}, app_slug: .app.slug, app_id: .app.id}"
)


def _gatherdoc_step():
    step = step_by("comment-ops", id="gatherdoc")
    assert step.get("run"), "the gatherdoc step runs no shell"
    return step


def _projection(body):
    """The check-runs listing's jq expression. One line carries it; the step's other `--jq` reads
    the head SHA."""
    lines = [ln for ln in body.splitlines() if "--jq '.check_runs[]" in ln]
    assert len(lines) == 1, f"{len(lines)} check-runs projections in the step"
    return lines[0].split("--jq '", 1)[1].rsplit("'", 1)[0]


def _report_ctx():
    return {
        "head_sha": "f" * 40,
        "plan_run_ids": ["1"],
        "envs": {"dev-eu"},
        "harvest_failed": False,
        "harvest_pending": False,
    }


def test_the_doctor_upsert_anchors_the_marker_at_the_body_start(monkeypatch):
    """`render_report` emits `DOCTOR_MARKER` as the body's first line, so the sticky lookup must
    anchor there. A `contains` match also hits any comment that merely quotes the marker: the sticky
    plan comment embeds `tofu plan` output verbatim, so a plan containing `<!-- shipmate:doctor -->`
    would be selected and PATCHed with the doctor report, the plan comment destroyed and the summary
    marker orphaned onto a comment without it."""
    code = _code(_step("body=@doctor.md"))
    assert "startswith" in code
    assert "contains" not in code
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "1")
    assert doctor.render_report([], [], _report_ctx()).splitlines()[0] == doctor.DOCTOR_MARKER


def test_the_doctor_upsert_does_not_swallow_a_comment_listing_failure():
    """`|| true` on the id lookup turns a failed listing into an empty id, which falls through to
    the create branch: a second marker-bearing Bot comment, and every later run PATCHing whichever
    one the listing returns first. The `|| true` only dodged EPIPE from `head` under `pipefail`, so
    the pipe goes rather than the error check -- and a listing failure skips the post entirely, so
    the next run recovers."""
    code = _code(_step("body=@doctor.md"))
    assert "|| true" not in code
    assert "| head -n1" not in code  # No pipe, so no EPIPE to swallow.
    assert "if ! gh api" in code
    degrade = code.split("if ! gh api", 1)[1].split("fi", 1)[0]
    assert "::warning::" in degrade
    assert "exit 0" in degrade
    # The skip must precede both writes, or it is not a skip.
    assert code.index("exit 0") < code.index("-X PATCH")
    assert code.index("exit 0") < code.index('issues/$PR_NUMBER/comments" -F body=@doctor.md')


def _mint_with(action, step_id):
    """The parsed `with:` mapping of the mint step whose id is exactly `step_id`.

    Two things a text slice from `id: <step_id>` cannot do. It matches any id that merely starts
    with the name, so a future `id: token-refresh` placed above the real mint retargets the slice
    and every permission assertion then passes against the wrong step while the real mint's grant
    can be deleted unnoticed. And it reads raw file text, where a commented-out `#
    permission-environments: read` satisfies the same assertion as the live key. So: exact id,
    asserted to really be a mint, compared as parsed values."""
    step = step_by(action, id=step_id)
    uses = step.get("uses") or ""
    assert "actions/create-github-app-token" in uses, (
        f"{action}'s `{step_id}` step is not an App-token mint (uses: {uses!r}) — "
        "the permission assertions against it would pin nothing"
    )
    return step.get("with") or {}


def _requested_permissions(mint_with):
    """`{permission: level}` actually requested by a mint, from its parsed inputs — so a
    commented-out request is absent, as it is at runtime."""
    return {
        k.removeprefix("permission-"): v
        for k, v in mint_with.items()
        if k.startswith("permission-")
    }


def test_both_doctor_token_mints_request_actions_read():
    """The environment probes read `repos/{repo}/environments` and, per environment,
    `.../environments/{name}`. The environments list has been observed working under a token without
    Actions read, but the per-environment read needs it on some configurations, and a degraded
    environment probe is invisible in the report except as a "could not verify" note.
    `app/manifest.json` already declares `actions: write`, so `actions: read` is a subset of the
    installation's granted set and needs no re-approval.

    Both sites, because they are separate mints: `actions/summary`'s token drives `annotate` mode on
    every plan run, `comment-ops`' `doctortoken` drives `report` mode on demand, and a fix applied
    to only one leaves half the environment probes degraded."""
    assert _mint_with("comment-ops", "doctortoken").get("permission-actions") == "read"
    assert _mint_with("summary", "token").get("permission-actions") == "read"


#: The permissions each doctor mint requests, hand-written. The plan-environment secret probe
#: runs on these tokens, so both carry `environments: read`.
_DOCTOR_MINT_PERMISSIONS = {
    ("summary", "token"): {
        "checks": "write",
        "statuses": "write",
        "pull-requests": "write",
        "contents": "read",
        "actions": "read",
        "environments": "read",
    },
    ("comment-ops", "doctortoken"): {
        "checks": "read",
        "contents": "read",
        "pull-requests": "write",
        "actions": "read",
        "environments": "read",
    },
}


@pytest.mark.parametrize(("action", "step_id"), list(_DOCTOR_MINT_PERMISSIONS))
def test_the_doctor_mints_request_exactly_their_permission_sets(action, step_id):
    """Mutation: drop `permission-environments: read` from either mint -- its case reddens."""
    requested = _requested_permissions(_mint_with(action, step_id))
    assert requested == _DOCTOR_MINT_PERMISSIONS[(action, step_id)]


def test_the_doctor_token_can_comment_on_a_pull_request():
    """The report is posted to `/issues/{pr}/comments`, but a pull request comment is governed by
    the pull-requests permission, not issues: a token holding only `issues: write` gets 403
    "Resource not accessible by integration" there. Both other mints that post a comment
    (`actions/summary`, `actions/apply-summary`) request pull-requests, so the doctor mint is pinned
    to the same permission, and to the absence of the one that does not work."""
    mint = _mint_with("comment-ops", "doctortoken")
    assert mint.get("permission-pull-requests") == "write"
    assert "permission-issues" not in mint


def test_the_authorization_mint_requests_only_checks_read_on_this_repository():
    """Apply and unlock are authorized by the commenter's repository permission, read with the
    workflow token, so the App token needs only the check-runs read of the reviewed-plan lookup.
    The whole mapping, so any added scope or widened level turns this red.

    Mutations: add the organization `members` permission back; widen `permission-checks` to
    `write`."""
    assert _mint_with("comment-ops", "apptoken") == {
        "client-id": "${{ inputs.app-id }}",
        "private-key": "${{ inputs.private-key }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-checks": "read",
    }


def test_fullmint_requests_the_manifests_exact_permission_set():
    """The full-set probe mint must mirror app/manifest.json: a manifest bump that skips this step
    makes the permission-drift probe test the stale set, the drift the probe exists to catch. Parsed
    inputs, not a regex over the file text: `# permission-environments: read` in a comment matched
    the live key exactly as well, so a commented-out request kept this guard green while the mint
    asked for nothing."""
    requested = _requested_permissions(_mint_with("comment-ops", "fullmint"))
    declared = {k.replace("_", "-"): v for k, v in _MANIFEST_PERMISSIONS.items()}
    assert requested == declared


#: doc -> the (start, end) markers bounding its prose permission list. Bounded, not searched
#: whole-file: `docs/github-app.md` also mentions the App's "`statuses: write` gate POST"
#: further down, so an unbounded substring assertion would survive `statuses` being deleted
#: from the list itself. Both markers are asserted present, since a vanished `end` would
#: widen the slice to the rest of the file and restore that fail-open behaviour.
_PROSE_PERMISSION_LISTS = {
    "CONTRACT.md": ("carries this permission set:", "Beyond minting"),
    "docs/github-app.md": ("- Permissions:", "\n- No webhook events"),
}


def test_both_prose_permission_lists_name_every_manifest_permission():
    """`app/manifest.json` is what GitHub grants; two documents spell the same set out in prose for
    readers, and `CONTRACT.md` is the single source for the contract.
    test_fullmint_requests_the_manifests_exact_permission_set pins only the action YAML, so nothing
    noticed when a manifest bump left `CONTRACT.md` listing seven permissions -- this pins both
    lists against the manifest instead.

    Both bounding markers are asserted before the slice is taken: a missing `start` would search
    from the top of the file and a missing `end` to the bottom, and either widening makes the
    assertion satisfiable by a mention outside the list, pinning nothing."""
    for doc, (start, end) in _PROSE_PERMISSION_LISTS.items():
        text = (ENGINE / doc).read_text(encoding="utf-8")
        assert start in text, (
            f"{doc}: the marker opening the permission list, {start!r}, is gone — "
            "the prose moved or was reworded; re-point _PROSE_PERMISSION_LISTS at it. "
            "This is not a permission going missing."
        )
        after = text.split(start, 1)[1]
        # `after`, not `text`: an `end` that occurs only before the list would leave the
        # slice running to the end of the file, as an absent one does.
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
    step = step_by("comment-ops", name="Authorize")
    assert step.get("env"), "the Authorize step declares no env: block"
    return step


def _gather_step():
    step = step_by("comment-ops", id="gather")
    assert step.get("run"), "the gather step runs no shell"
    return step


def test_the_authorize_step_reads_the_files_the_gather_step_writes(tmp_path, monkeypatch):
    """`env:` key -> `os.environ` key -> file path, run rather than eyeballed. A rename on either
    side leaves authorize reading a file nobody writes, and `_read_json`'s missing-file default then
    refuses every apply -- or, for the PR, reads an empty mapping as an unmergeable pull request."""
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
        "PERMISSION": "write",
        "REVIEW_DECISION": "NONE",
        "GITHUB_OUTPUT": "out.txt",
    }.items():
        monkeypatch.setenv(key, value)
    load_script("authorize").main()
    assert "authorized=true" in (tmp_path / "out.txt").read_text(encoding="utf-8")


#: The whole `env:` of `Gather authorization inputs`, hand-written. `GH_TOKEN` is the workflow
#: token for the pull request and review reads; `APP_TOKEN` serves only the check-runs read.
_GATHER_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "APP_TOKEN": "${{ steps.apptoken.outputs.token }}",
    "OWNER": "${{ github.repository_owner }}",
    "PR_NUMBER": "${{ inputs.pr-number }}",
    "SHIPMATE_VERB": "${{ steps.parse.outputs.route }}",
    "SHIPMATE_APP_ID": "${{ inputs.app-id }}",
}

#: The whole `env:` of `Authorize`, hand-written. `PERMISSION` is the hop from the `permission`
#: step's output; a wrong name reads as empty and refuses every commenter.
_AUTHORIZE_ENV = {
    "PERMISSION": "${{ steps.permission.outputs.permission }}",
    "REVIEW_DECISION": "${{ steps.gather.outputs.review_decision }}",
    "PR_JSON": "pr.json",
    "PLAN_RUN_JSON": "plan_run.json",
    "SHIPMATE_ENV": "${{ steps.parse.outputs.env }}",
    "SHIPMATE_GATE_UNGATED_ENVS": "${{ steps.gate.outputs.ungated_envs }}",
    "SHIPMATE_VERB": "${{ steps.parse.outputs.route }}",
}


def test_the_gather_step_binds_exactly_this_env():
    """Mutation: set `GH_TOKEN` to `${{ steps.apptoken.outputs.token }}`, and the pull request
    and review reads run on the App token."""
    assert _gather_step()["env"] == _GATHER_ENV


def test_the_authorize_step_binds_exactly_this_env():
    """Mutation: bind `PERMISSION` to `steps.permission.outputs.permissions`, which no step
    writes."""
    assert _authorize_step()["env"] == _AUTHORIZE_ENV


#: A `gh` that logs the endpoint each call reads and answers it as the API does after `--jq`.
_GATHER_GH = """#!/bin/bash
for a in "$@"; do
  if [[ "$a" == repos/* || "$a" == graphql ]]; then echo "$a" >> endpoints.txt; break; fi
done
if [[ "$*" == *pulls/* ]]; then
  echo '{"mergeable": true, "mergeable_state": "clean", "head": {"sha": "abc"}}'
elif [[ "$*" == *graphql* ]]; then
  echo APPROVED
fi
"""


def _run_gather(tmp_path):
    """Run the gather step's shipped body; return (outputs, endpoints read in order)."""
    for tool, text in (
        ("gh", _GATHER_GH),
        ("python3", f'#!/bin/bash\nexec "{sys.executable}" "$@"\n'),
    ):
        (tmp_path / tool).write_text(text, encoding="utf-8", newline="\n")
        (tmp_path / tool).chmod(0o755)
    out_file = tmp_path / "github_output"
    out_file.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
        "GITHUB_ACTION_PATH": str(ACTIONS / "comment-ops"),
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_OUTPUT": str(out_file),
        "OWNER": "org",
        "USER": "alice",
        "PR_NUMBER": "42",
        "SHIPMATE_VERB": "apply",
        "SHIPMATE_APP_ID": "1",
        "APP_TOKEN": "app_token",
    }
    result = run_step(tmp_path, _gather_step()["run"], env)
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines())
    return outputs, (tmp_path / "endpoints.txt").read_text().splitlines()


@bash_only
def test_the_gather_step_reads_no_permission(tmp_path):
    """`gather` runs only after `access` authorized, so the permission is read once, before it,
    and a commenter without write access costs none of these reads (pinned by
    test_every_apply_and_unlock_step_after_the_decision_is_gated).

    Mutation: put the permission read back into gather -- the endpoints gain
    `collaborators/alice/permission`."""
    outputs, endpoints = _run_gather(tmp_path)
    assert endpoints == [
        "repos/org/repo/pulls/42",
        "graphql",
        "repos/org/repo/commits/abc/check-runs?filter=all&per_page=100",
    ]
    assert outputs == {"review_decision": "APPROVED"}


def test_the_gather_step_reads_the_plan_runs_from_the_heads_own_check_runs():
    """The reviewed-plan lookup is the head's own apply checks, each of which records the plan run
    its plan came from. The plan-workflow lookup it replaced resolved one run for the whole command
    and had to compare that run's head against the PR's -- a comparison this listing makes
    unnecessary and, being keyed on the head already, unfalsifiable."""
    body = _code(_gather_step()["run"])
    assert '"repos/$GITHUB_REPOSITORY/commits/$head/check-runs?filter=all&per_page=100"' in body
    assert "workflows/plan.yml" not in body
    # The mapping comes from apply-gate's `--plan-runs` mode: without the flag the
    # same invocation writes a gate verdict and leaves plan_run.json empty.
    assert (
        '| python3 "$GITHUB_ACTION_PATH/../../scripts/apply-gate" --plan-runs > plan_run.json'
    ) in body


def test_the_gather_step_receives_the_app_id_the_plan_run_lookup_scopes_on():
    """Only our App's apply checks may name a plan run: a same-name check from any other identity
    must not decide what a `shipmate apply` applies."""
    assert _gather_step()["env"]["SHIPMATE_APP_ID"] == "${{ inputs.app-id }}"


def test_authorize_step_receives_the_resolved_ungated_envs():
    """The resolve step's output is the only channel: `authorize` gets the list the gate
    step read from the default branch's file, in the same job, and the action takes no
    input a caller could supply a different one through.

    Mutation: add an `ungated-envs` input to the manifest and bind it here instead.
    """
    assert (
        _authorize_step()["env"]["SHIPMATE_GATE_UNGATED_ENVS"]
        == "${{ steps.gate.outputs.ungated_envs }}"
    )
    assert "ungated-envs" not in action_yaml("comment-ops")["inputs"]


_RUN_URL = "https://github.com/org/repo/actions/runs/7777"
_FOOTER = f"[run]({_RUN_URL}). Comment `shipmate help` for the available commands."
_PARSED_VERB = "${{ steps.parse.outputs.verb }}"
_PARSED_ENV = "${{ steps.parse.outputs.env }}"

#: Every reply step's shell body, hand-written: the comment comes from reply-comment alone, so
#: no step formats a header, verdict or footer of its own.
_REPLY_RUN = (
    "set -euo pipefail\n"
    'body=$(python3 "$GITHUB_ACTION_PATH/../../scripts/reply-comment")\n'
    'gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments" -f body="$body" '
    ">/dev/null\n"
)

#: Each reply step's header words, outcome and text, hand-written. `refused` where the engine
#: decided not to run the command, `failed` where it could not.
_REPLIES = {
    "Reject malformed / reserved command": {
        "SHIPMATE_REPLY_VERB": _PARSED_VERB,
        "SHIPMATE_REPLY_ENV": _PARSED_ENV,
        "SHIPMATE_REPLY_OUTCOME": "refused",
        "SHIPMATE_REPLY_TEXT": "${{ steps.parse.outputs.error }}",
    },
    "Reject a commenter without write access": {
        "SHIPMATE_REPLY_VERB": _PARSED_VERB,
        "SHIPMATE_REPLY_ENV": _PARSED_ENV,
        "SHIPMATE_REPLY_OUTCOME": "refused",
        "SHIPMATE_REPLY_TEXT": "${{ steps.access.outputs.reason }}",
    },
    "Permission check failed": {
        "SHIPMATE_REPLY_VERB": _PARSED_VERB,
        "SHIPMATE_REPLY_ENV": _PARSED_ENV,
        "SHIPMATE_REPLY_OUTCOME": "failed",
        "SHIPMATE_REPLY_TEXT": (
            "could not decide the commenter's permission on this repository, so this command "
            "was not run. This run's log has the error; comment again."
        ),
    },
    "Reject an unauthorized plan": {
        "SHIPMATE_REPLY_VERB": "plan",
        "SHIPMATE_REPLY_OUTCOME": "refused",
        "SHIPMATE_REPLY_TEXT": "${{ steps.planauthz.outputs.reason }}",
    },
    "Doctor: App token unavailable": {
        "SHIPMATE_REPLY_VERB": "doctor",
        "SHIPMATE_REPLY_OUTCOME": "failed",
        "SHIPMATE_REPLY_TEXT": (
            "could not mint a GitHub App token. Is the shipmate App installed on this "
            "repository, with every permission it requests accepted? Ask an org admin to "
            "install it, or to add any permission the App's settings lack and accept the "
            "request (docs/github-app.md, Re-approve after permission changes), then re-run "
            "`shipmate doctor`."
        ),
    },
    "App token unavailable (App not installed?)": {
        "SHIPMATE_REPLY_VERB": _PARSED_VERB,
        "SHIPMATE_REPLY_ENV": _PARSED_ENV,
        "SHIPMATE_REPLY_OUTCOME": "failed",
        "SHIPMATE_REPLY_TEXT": (
            "could not mint a GitHub App token. Is the shipmate App installed on this "
            "repository (with checks:read)? Ask an org admin to install it, then retry."
        ),
    },
    "Gate configuration unreadable": {
        "SHIPMATE_REPLY_VERB": _PARSED_VERB,
        "SHIPMATE_REPLY_ENV": _PARSED_ENV,
        "SHIPMATE_REPLY_OUTCOME": "refused",
        "SHIPMATE_REPLY_TEXT": (
            "could not resolve the gate settings from `.github/shipmate.toml` on the default "
            "branch, so this command was not run. The file is not merged there, it does not "
            "validate, or a variable it references is unset or empty, this run's log says "
            "which. Fix that and comment again."
        ),
    },
    "Report the review exemption": {
        "SHIPMATE_REPLY_VERB": "apply",
        "SHIPMATE_REPLY_ENV": "${{ steps.authz.outputs.environment }}",
        "SHIPMATE_REPLY_OUTCOME": "notice",
        "SHIPMATE_REPLY_TEXT": (
            "${{ steps.authz.outputs.environment }}: ungated, permitted to apply without an "
            "approving review (`gated = false` in `.github/shipmate.toml`). The apply result "
            "comment shows what applied."
        ),
    },
    "Reject with reason": {
        "SHIPMATE_REPLY_VERB": _PARSED_VERB,
        "SHIPMATE_REPLY_ENV": _PARSED_ENV,
        "SHIPMATE_REPLY_OUTCOME": "refused",
        "SHIPMATE_REPLY_TEXT": "${{ steps.authz.outputs.reason }}",
    },
}


#: The rest of every reply step's `env:`: the workflow token posts, because the App token may be
#: the very thing that failed.
_REPLY_POST_ENV = {"GH_TOKEN": "${{ github.token }}", "PR_NUMBER": "${{ inputs.pr-number }}"}


def test_every_reply_step_names_its_header_outcome_and_text():
    """The whole `env:` of every step that posts a reply, against the table above. A step
    gaining or losing a reply, or a reply changing class, fails here.

    Mutations: set `Doctor: App token unavailable`'s outcome to `refused`; drop
    `SHIPMATE_REPLY_ENV` from `Reject with reason`; bind `Gate configuration unreadable`'s
    `GH_TOKEN` to `steps.apptoken.outputs.token`.
    """
    got = {
        s["name"]: s["env"]
        for s in action_steps("comment-ops")
        if "SHIPMATE_REPLY_OUTCOME" in (s.get("env") or {})
    }
    assert got == {name: {**_REPLY_POST_ENV, **reply} for name, reply in _REPLIES.items()}


#: An issue-comment endpoint at the end of a path; `/comments/<id>/reactions` is not one.
_COMMENT_ENDPOINT = re.compile(r"/comments\b(?!/)")

#: The steps that post a comment reply-comment does not render: help prints its own frame, and
#: the doctor report is a sticky upsert of doctor's own body.
_NON_REPLY_POSTERS = {"Post help", "Doctor: render and upsert the sticky comment"}


def test_every_step_posting_a_comment_is_a_reply_step_or_a_named_poster():
    """The reply guards above select by `SHIPMATE_REPLY_OUTCOME`, so a new step posting a
    hand-formatted comment without it escapes them; this selects by the endpoint instead.

    Mutation: add a step whose `run` is `gh api -X POST
    "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments" -f body=":x: shipmate: x"` with no
    reply env -- red."""
    posters = {
        s["name"]
        for s in action_steps("comment-ops")
        if _COMMENT_ENDPOINT.search(s.get("run") or "")
    }
    assert posters == set(_REPLIES) | _NON_REPLY_POSTERS


#: The gate step's whole body: no later step keys on its outcome, so its `exit 1` is the only
#: thing that stops an unresolvable gate configuration from reaching authorization.
_GATE_UNREADABLE_RUN = _REPLY_RUN + (
    "# The resolve's own failure, re-raised: `continue-on-error` above kept the job alive\n"
    "# long enough to post this, and a green run over an unauthorized command is not a\n"
    "# verdict this action may render.\n"
    "exit 1\n"
)


#: The permission failure reply's whole body. The job has already failed by the time it runs;
#: the `exit 1` keeps the step itself from reading as a handled command.
_PERMISSION_FAILED_RUN = _REPLY_RUN + "exit 1\n"
_FAILING_REPLY_RUNS = {
    "Gate configuration unreadable": _GATE_UNREADABLE_RUN,
    "Permission check failed": _PERMISSION_FAILED_RUN,
}


def test_every_reply_step_posts_the_body_reply_comment_rendered():
    """Mutations: post `-f body=":x: shipmate: $REASON"` in `Reject with reason` instead;
    insert `exit 0` before the trailing comment of `Gate configuration unreadable`; drop
    `exit 1` from `Permission check failed`."""
    for name in _REPLIES:
        run = step_by("comment-ops", name=name)["run"]
        assert run == _FAILING_REPLY_RUNS.get(name, _REPLY_RUN), name


#: The permission failure reply's whole `if:`, hand-written.
_PERMISSION_FAILED_IF = (
    "${{ failure() && (steps.permission.outcome == 'failure'"
    " || steps.access.outcome == 'failure') }}"
)


def test_an_errored_permission_read_or_decision_is_answered():
    """`permission` and `access` carry no `continue-on-error`, so an error in either fails
    the job and skips every `success()`-gated step after it, the write-access refusal included.
    Without this reply the commenter sees the eyes reaction on `plan` or `doctor` and nothing
    else. Its env is pinned through `_REPLIES`, its body and `exit 1` through
    `_FAILING_REPLY_RUNS`, its place through `_STEP_NAMES`.

    Mutation: drop `failure() && ` (the implied `success()` then never runs it after the failure
    it reports); change `steps.access.outcome` to `steps.planauthz.outcome`."""
    step = step_by("comment-ops", name="Permission check failed")
    assert step["if"] == _PERMISSION_FAILED_IF


_EXPR = re.compile(r"\$\{\{ (.+?) \}\}")

#: A `gh` that saves the comment body it is handed and answers as the API does.
_BODY_GH = (
    "#!/bin/bash\n"
    'for a in "$@"; do if [[ "$a" == body=* ]]; then printf "%s" "${a#body=}" > body.txt; fi; '
    "done\n"
    "echo '{}'\n"
)


def _run_as_wired(tmp_path, step, context):
    """Run `step`'s shipped body under its own `env:`, each `${{ X }}` in it replaced by
    `context[X]`, against a `gh` that saves the comment body, with `GITHUB_OUTPUT` at
    `github_output`; return the process result."""
    for tool, text in (
        ("gh", _BODY_GH),
        ("python3", f'#!/bin/bash\nexec "{sys.executable}" "$@"\n'),
    ):
        (tmp_path / tool).write_text(text, encoding="utf-8", newline="\n")
        (tmp_path / tool).chmod(0o755)
    context = {"github.token": "test_token", "inputs.pr-number": "42", **context}
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
        "GITHUB_ACTION_PATH": str(ACTIONS / "comment-ops"),
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "7777",
        "GITHUB_OUTPUT": str(tmp_path / "github_output"),
    }
    for key, value in step["env"].items():
        env[key] = _EXPR.sub(lambda m: context[m[1]], str(value))
    return run_step(tmp_path, step["run"], env)


def _posted_body(tmp_path, name, context):
    """Run step `name` as wired (`_run_as_wired`); return the comment body it posted."""
    result = _run_as_wired(tmp_path, step_by("comment-ops", name=name), context)
    assert result.returncode == 0, result.stderr
    return (tmp_path / "body.txt").read_text(encoding="utf-8")


def _access_reason(route, permission):
    if permission == "":
        return (
            "not authorized: could not read the commenter's permission on this repository, so "
            f"`shipmate {route}` was not run. This run's log has the API response; comment again."
        )
    return (
        f"not authorized: `shipmate {route}` needs write access to this repository, and the "
        f"commenter's permission is `{permission}`."
    )


@bash_only
@pytest.mark.parametrize("permission", ["admin", "read", ""])
@pytest.mark.parametrize("route", ["plan", "doctor", "apply"])
def test_the_access_step_as_wired_decides_the_permission_for_its_route(tmp_path, route, permission):
    """Mutations: set the step's `SHIPMATE_VERB` to `apply` (the `plan` and `doctor` reasons
    name `apply`); drop `--permission` from `run` (the full decision runs, and `plan` exits
    `unknown verb`)."""
    result = _run_as_wired(
        tmp_path,
        step_by("comment-ops", id="access"),
        {
            "steps.parse.outputs.route": route,
            "steps.permission.outputs.permission": permission,
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout
    if permission == "admin":
        expected = "authorized=true\nreason=\n"
    else:
        expected = f"authorized=false\nreason={_access_reason(route, permission)}\n"
    assert (tmp_path / "github_output").read_text(encoding="utf-8") == expected


@bash_only
def test_a_command_naming_no_known_verb_is_refused_under_the_bare_header(tmp_path):
    """`shipmate aply dev-eu` parses to an empty verb and `dev-eu`: the header names neither.

    Mutations: post `$ERROR` without reply-comment; keep the env in `header` when the verb is
    empty.
    """
    body = _posted_body(
        tmp_path,
        "Reject malformed / reserved command",
        {
            "steps.parse.outputs.verb": "",
            "steps.parse.outputs.env": "dev-eu",
            "steps.parse.outputs.error": "unknown verb `aply` (try `shipmate help`)",
        },
    )
    assert body == (
        f"### shipmate\n\n🔴 refused: unknown verb `aply` (try `shipmate help`)\n\n{_FOOTER}"
    )


@bash_only
def test_an_unauthorized_plan_is_refused_under_the_plan_header(tmp_path):
    """Mutation: post `$REASON` without reply-comment."""
    body = _posted_body(
        tmp_path,
        "Reject an unauthorized plan",
        {"steps.planauthz.outputs.reason": _PLAN_FORK_REASON},
    )
    assert body == f"### shipmate plan\n\n🔴 refused: {_PLAN_FORK_REASON}\n\n{_FOOTER}"


def test_the_exemption_report_fires_only_when_the_exemption_fired():
    """Not on `authorized == 'true'`: an ordinary reviewed apply is authorized too, and this
    sentence over it would be false."""
    step = step_by("comment-ops", name="Report the review exemption")
    assert step["if"] == "${{ steps.authz.outputs.ungated_exemption == 'true' }}"


@bash_only
def test_the_exemption_report_claims_permission_never_completion(tmp_path):
    """At comment time the dispatch has not run, so any verb about the outcome would be a claim
    this step cannot make. The whole body, hand-written.

    Mutations: map the step's outcome to `refused`; give a notice the footer hint.
    """
    body = _posted_body(
        tmp_path,
        "Report the review exemption",
        {"steps.authz.outputs.environment": "dev-eu"},
    )
    assert body == (
        "### shipmate apply dev-eu\n\n"
        "⚪ dev-eu: ungated, permitted to apply without an approving review "
        "(`gated = false` in `.github/shipmate.toml`). The apply result comment shows what "
        "applied.\n\n"
        f"[run]({_RUN_URL})"
    )


def test_authorize_step_supplies_every_env_var_authorize_reads():
    """Derived from `scripts/authorize`'s own source, not a hand-listed set: a SHIPMATE_* name added
    to the script later would otherwise read as empty in this step and silently take the fail-closed
    branch."""
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
    "Mint App token (checks:read)": _APP_MINT_IF,
    "App token unavailable (App not installed?)": _APP_UNAVAILABLE_IF,
    "Resolve gate configuration": (
        "${{ (steps.parse.outputs.route == 'apply' || steps.parse.outputs.route == 'unlock')"
        " && steps.apptoken.outcome == 'success' }}"
    ),
    "Gate configuration unreadable": (
        "${{ (steps.parse.outputs.route == 'apply' || steps.parse.outputs.route == 'unlock')"
        " && steps.apptoken.outcome == 'success' && steps.gate.outcome != 'success' }}"
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


# The `verb` output's whole value is pinned in test_dispatch_verb.py, beside the
# `case` list that consumes it: one selector, one comparison.


#: What both verb-carrying steps must bind SHIPMATE_VERB to, hand-written.
#: test_authorize_step_supplies_every_env_var_authorize_reads is a subset-of-KEYS check and
#: cannot see the value: with `Authorize`'s binding hardcoded to a literal `unlock`, every
#: `shipmate apply` authorizes on write access alone -- no mergeable, no review policy, no
#: reviewed plan -- and the whole suite stays green.
_VERB_BINDING = "${{ steps.parse.outputs.route }}"


def test_both_verb_steps_bind_shipmate_verb_to_the_parsed_route():
    """One selector for the property, so the two occurrences cannot disagree: a literal on either
    step, or a deleted line, fails this dict equality."""
    bound = {
        s["name"]: (s.get("env") or {}).get("SHIPMATE_VERB")
        for s in action_steps("comment-ops")
        if s.get("name") in {"Gather authorization inputs", "Authorize"}
    }
    assert bound == {
        "Gather authorization inputs": _VERB_BINDING,
        "Authorize": _VERB_BINDING,
    }


def test_exactly_the_table_readers_receive_the_callers_variables():
    """`gate-config` and every `doctor` probe run read `.github/shipmate.toml` through
    `parse_table`, which refuses a file holding a variable reference when
    `SHIPMATE_GITHUB_VARS` is absent. Selected over every step of both actions, so a further
    holder fails too.

    Mutations: drop `SHIPMATE_GITHUB_VARS` from `Resolve gate configuration`'s `env:`, or from
    `actions/summary`'s doctor step.
    """
    bound = {
        (action, s["name"]): s["env"]["SHIPMATE_GITHUB_VARS"]
        for action in ("comment-ops", "summary")
        for s in action_steps(action)
        if "SHIPMATE_GITHUB_VARS" in (s.get("env") or {})
    }
    assert bound == {
        ("comment-ops", "Resolve gate configuration"): "${{ inputs.github-vars }}",
        (
            "comment-ops",
            "Doctor: render and upsert the sticky comment",
        ): "${{ inputs.github-vars }}",
        (
            "summary",
            "Doctor: settings-drift warnings (annotations only, never blocks)",
        ): "${{ inputs.github-vars }}",
    }


#: The comment-ops verb table in CONTRACT.md: the header row that opens it and
#: the blank line that ends it. Bounded rather than whole-file, so a `shipmate
#: <verb>` mention in the surrounding prose cannot satisfy the guard.
_VERB_TABLE = ("| verb | status | args | authorization |", "\n\n")


def test_the_contract_verb_table_carries_every_active_verb():
    """CONTRACT.md calls `VERBS` "the single source of truth this table is derived from", but the
    table is hand-maintained -- so it drifted. Derived from the registry here, never a hand-written
    verb list, so the next verb added cannot repeat it."""
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


#: Every step of the action, in order, hand-written. One constant for the whole shape: it
#: carries the plan route's placement. A step inserted, dropped or reordered fails here rather
#: than in whichever positional guard happened to care.
_STEP_NAMES = [
    "Ignore bot-authored comments",
    "Parse command",
    "Reject malformed / reserved command",
    "Post help",
    "Acknowledge a command that changes no infrastructure",
    "Read the commenter's repository permission",
    "Authorize the commenter's permission",
    "Reject a commenter without write access",
    "Permission check failed",
    "Authorize plan",
    "Reject an unauthorized plan",
    "Mint App token for doctor",
    "Doctor: App token unavailable",
    "Doctor: probe the manifest's full permission set",
    "Doctor: gather head SHA, declared environments, annotations",
    "Doctor: render and upsert the sticky comment",
    "Mint App token (checks:read)",
    "App token unavailable (App not installed?)",
    "Resolve gate configuration",
    "Gate configuration unreadable",
    "Gather authorization inputs",
    "Authorize",
    "React on accept",
    "Report the review exemption",
    "Reject with reason",
]


def test_the_action_runs_exactly_these_steps_in_this_order():
    assert [s.get("name") for s in action_steps("comment-ops")] == _STEP_NAMES


def _by_id(step_id):
    step = step_by("comment-ops", id=step_id)
    assert step.get("run"), f"the {step_id} step runs no shell"
    return step


#: The fork refusal `planauthz` can write, hand-written whole. The step is exercised rather
#: than string-matched, so what a mutation has to survive is the rendered value a commenter
#: reads, not the source line that produced it.
_PLAN_FORK_REASON = (
    "this pull request's head is in `someone/fork`, and shipmate plans only branches of this "
    "repository: a fork's plan would execute the pull request's own Terramate/OpenTofu code "
    "with everything the plan environment holds."
)


def _run_planauthz(tmp_path, *, head_repo="org/repo"):
    """Run `planauthz`'s shipped body against a stub `gh`; returns (result, outputs, gh argv).

    `head_repo=None` stubs a `gh` that fails, which is the head this step cannot read.
    """
    gh_path = tmp_path / "gh"
    answer = "exit 1\n" if head_repo is None else f"printf '%s\\n' {shlex.quote(head_repo)}\n"
    gh_path.write_text("#!/bin/bash\nprintf '%s\\n' \"$@\" >> argv.txt\n" + answer)
    gh_path.chmod(0o755)

    out_file = tmp_path / "github_output"
    out_file.write_text("", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["GH_TOKEN"] = "test_token"  # noqa: S105
    env["PR_NUMBER"] = "42"
    env["GITHUB_REPOSITORY"] = "org/repo"
    env["GITHUB_OUTPUT"] = str(out_file)

    result = run_step(tmp_path, _by_id("planauthz")["run"], env)
    outputs = dict(
        line.split("=", 1)
        for line in out_file.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    argv_file = tmp_path / "argv.txt"
    return result, outputs, (argv_file.read_text() if argv_file.exists() else "")


def test_the_plan_fork_check_runs_only_for_a_permitted_commenter():
    """`planauthz` holds the fork check and nothing else: the commenter's write access is decided
    once, by `access`, and both plan steps run only when it authorized. The whole `env:` pins
    every input-borne source the step reads, so no second permission source can enter it.

    Mutation: delete `&& steps.access.outputs.authorized == 'true'` from `planauthz`."""
    planauthz = _by_id("planauthz")
    assert planauthz["if"] == (
        "${{ steps.parse.outputs.route == 'plan' && steps.access.outputs.authorized == 'true' }}"
    )
    assert planauthz["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
    }

    reject = step_by("comment-ops", name="Reject an unauthorized plan")
    assert reject["if"] == (
        "${{ steps.parse.outputs.route == 'plan' && steps.access.outputs.authorized == 'true'"
        " && steps.planauthz.outputs.authorized != 'true' }}"
    )
    assert reject["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ inputs.pr-number }}",
        **_REPLIES["Reject an unauthorized plan"],
    }
    assert reject["run"] == _REPLY_RUN


@bash_only
def test_a_plan_of_this_repositorys_own_head_is_authorized(tmp_path):
    """The path that must stay open: a head in this repository dispatches.

    Mutation: compare `$HEAD_REPO` to anything but `$GITHUB_REPOSITORY`, and this repository's
    own pull requests stop planning.
    """
    result, outputs, argv = _run_planauthz(tmp_path, head_repo="org/repo")
    assert result.returncode == 0, result.stderr
    assert outputs == {"authorized": "true"}
    assert "-X" not in argv, f"the authorization step writes nothing, it reads: {argv!r}"
    assert "repos/org/repo/pulls/42" in argv, f"the head was never read: {argv!r}"


@bash_only
def test_a_forks_head_is_refused_here_rather_than_in_a_run_the_pull_request_cannot_see(tmp_path):
    """`build-matrix` refuses a fork head inside the dispatched run, whose checks land on the
    dispatch ref -- so a fork's `shipmate plan` shows the pull request nothing at all. The
    refusal is stated here, in its own words, before the dispatch it would have wasted.

    Mutations: invert the `$HEAD_REPO` comparison to `=`; drop `$HEAD_REPO` from the reason.
    Deleting the comparison instead leaves this green -- the `-n` arm alone still refuses a
    fork -- which is why test_a_plan_of_this_repositorys_own_head_is_authorized is the other half
    of the pair.
    """
    result, outputs, _ = _run_planauthz(tmp_path, head_repo="someone/fork")
    assert result.returncode == 0, result.stderr
    assert outputs == {"authorized": "false", "reason": _PLAN_FORK_REASON}


@pytest.mark.parametrize("head_repo", [None, "null"])
@bash_only
def test_a_head_this_step_cannot_read_leaves_the_refusal_where_it_is_enforced(tmp_path, head_repo):
    """A failed read and a deleted head repository both fall through to `build-matrix`, which is
    where a fork is actually refused. This step explains that refusal; it must never become a
    second, quieter version of it that answers on a fact it does not have.

    The fall-through is the one arm an operator cannot infer from the outcome -- a dispatched
    fork plan looks the same whether the head was unreadable or the comparison is broken -- so
    it says which.

    Mutations: drop either the `-n` or the `!= "null"` arm, and an unreadable head starts
    refusing pull requests of this repository's own branches; drop the notice, and the
    fall-through goes silent.
    """
    result, outputs, _ = _run_planauthz(tmp_path, head_repo=head_repo)
    assert result.returncode == 0, result.stderr
    assert outputs == {"authorized": "true"}
    assert "could not read this pull request's head repository" in result.stdout, (
        f"the fall-through must say why it dispatched: {result.stdout!r}"
    )


def test_no_step_on_the_plan_route_touches_the_app_key():
    """A plan needs no App token: the caller's own dispatch step mints for the dispatch, and the
    plan route reads the commenter's permission on the workflow token, never the App key. A plan
    step that quietly acquired one would widen the private key's blast radius to a route nothing
    else about this action watches. Parsed steps, so a commented-out mint reads as absent, as it
    does at runtime; every step naming the route, so the shared steps are included rather than
    excused: those name no route, so they are selected by the gated-route clause and, for the
    failure reply, by the decision's outcome.

    Mutations: put `GH_TOKEN: ${{ steps.apptoken.outputs.token }}` on `access`; the same on
    `Permission check failed`."""
    on_plan = [
        s
        for s in action_steps("comment-ops")
        if any(
            m in (s.get("if") or "")
            for m in ("outputs.route == 'plan'", _GATED_ROUTES_IF, "steps.access.outcome")
        )
    ]
    assert len(on_plan) == 7, [s.get("name") for s in on_plan]
    for step in on_plan:
        text = json.dumps(step)
        assert "inputs.private-key" not in text, step.get("name")
        assert "steps.apptoken" not in text, step.get("name")


#: The authorization verdict, hand-written whole. Both routes have to reach it: dropping either
#: arm silently unauthorizes a whole verb.
_AUTHORIZED = (
    "${{ steps.authz.outputs.authorized == 'true' || "
    "steps.planauthz.outputs.authorized == 'true' }}"
)


def test_one_verdict_answers_for_every_route():
    """Two readers of two different authorization expressions is how one policy diverges, so the
    reaction and the composite's `authorized` output carry the same whole expression. `access`
    writes `authorized=true` for every permitted commenter, so a verdict reading it would
    dispatch a doctor; `_AUTHORIZED` excludes it.

    Mutations, one at a time: drop the `planauthz` arm from the output only; `||` -> `&&` in
    React on accept's `if:` only; add `steps.access.outputs.authorized == 'true' ||` to both."""
    assert action_yaml("comment-ops")["outputs"]["authorized"]["value"] == _AUTHORIZED
    assert step_by("comment-ops", name="React on accept")["if"] == _AUTHORIZED
