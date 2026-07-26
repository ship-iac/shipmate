"""Source-derived guards on the comment-ops action's routing wiring.

The action branches on `steps.parse.outputs.route`; the routes come from
comment-parse's VERBS registry. A verb added to the registry with no branch in
the action would parse, authorize, and then silently do nothing.
"""

import importlib.util
import json
import pathlib
import re
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parents[1]
_ACTION = (_D.parent / "actions" / "comment-ops" / "action.yml").read_text(encoding="utf-8")
_SUMMARY_ACTION = (_D.parent / "actions" / "summary" / "action.yml").read_text(encoding="utf-8")


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cp = _load("comment-parse")
doctor = _load("doctor")


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
    src = (_D / "doctor").read_text(encoding="utf-8")
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
    src = (_D / "doctor").read_text(encoding="utf-8")
    required = set(re.findall(r"os\.environ\[['\"](SHIPMATE_[A-Z_]+)['\"]\]", src))
    assert required, "expected at least one required SHIPMATE_* read in doctor"
    for name in required:
        assert name in _SUMMARY_ACTION, name


def test_doctor_runs_in_report_mode_with_the_app_token():
    assert "SHIPMATE_DOCTOR_MODE: report" in _ACTION
    assert "SHIPMATE_DOCTOR_MODE: check-ids" in _ACTION
    assert "steps.doctortoken.outputs.token" in _ACTION


def test_doctor_sticky_marker_matches_the_script():
    assert doctor.DOCTOR_MARKER in _ACTION


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


def test_fullmint_requests_the_manifests_exact_permission_set():
    """The full-set probe mint must mirror app/manifest.json: a manifest bump
    that skips this step makes the permission-drift probe test the stale set —
    exactly the drift the probe exists to catch."""
    block = _ACTION.split("id: fullmint", 1)[1].split("- name:", 1)[0]
    requested = dict(re.findall(r"permission-([a-z-]+): (read|write)", block))
    manifest = json.loads((_D.parent / "app" / "manifest.json").read_text(encoding="utf-8"))
    declared = {k.replace("_", "-"): v for k, v in manifest["default_permissions"].items()}
    assert requested == declared
