"""Guard against stale engine-internal action pins.

The reusable workflow ``.github/workflows/apply-env-level.yml`` and the composite
actions reference sibling shipmate actions/workflows by full commit SHA. GitHub
does not allow a local ``./actions/...`` reference across the reusable-workflow
boundary -- inside a reusable workflow ``./`` resolves against the *consumer*
repo, which has no ``actions/`` dir -- so the SHA pin is the only mechanism.

The hazard: when a referenced action's code changes, these internal pins go
stale silently and the engine keeps running the OLD action. This bit the deploy
path once -- ``apply-env-level.yml`` pinned ``apply-cell`` at a pre-safeguard SHA,
so the ``--disable-safeguards=git-out-of-sync`` fix never reached post-merge
applies even after consumers re-pinned to the new engine SHA.

This test asserts every internal ``ship-iac/shipmate/<path>@<sha>`` reference
pins a commit whose ``<path>`` content matches the **mainline** (the merge-base
with ``main``), not HEAD. A difference means the pin is stale on the release
line: bump it to a commit that contains the current action (in practice, a
follow-up commit pinning the just-merged release SHA). Comparing against the
mainline -- rather than HEAD -- means a branch that edits a pinned action isn't
flagged for its own not-yet-merged change (the bump is impossible before the
change has a SHA); the guard still fires once that change reaches ``main``
without a pin bump. See ``_release_baseline``.

A second hazard, same shape, one directory level deeper: a composite action's
runtime dependency surface is bigger than its own directory. Composite action
steps run ``python3 "$GITHUB_ACTION_PATH/../../scripts/<name>"``, and
``$GITHUB_ACTION_PATH`` resolves into the checkout of that action's *pinned*
SHA -- so the pinned tree's ``scripts/`` is what actually executes, not the
mainline's. Those scripts also cross-load each other via the repo's
``_load("<name>")`` pattern (e.g. ``apply-comment`` loads ``summary-comment``,
which loads ``apply-gate``). A ``scripts/``-only change is invisible to the
plain per-directory diff above: the referencing ``actions/<name>`` directory
never changed, so the guard stayed green while the pinned SHA kept running the
old script. (This shipped once: an engine PR changed only ``scripts/apply-comment``
and its tests; the following pin-bump PR was still required, and until it
landed, the previous pin's ``apply-summary`` action kept executing the
pre-fix ``apply-comment``, with the guard reporting no staleness throughout.)

To close that gap, every ``actions/<name>`` pin also gets its *script*
dependency set derived -- action.yml's direct ``$GITHUB_ACTION_PATH`` script
references, transitively closed over ``_load`` -- and each of those
``scripts/<script>`` paths is diffed between the pin and the baseline exactly
like a top-level ref. See ``_dependent_script_paths``. Deliberately not a diff
of the whole ``scripts/`` directory: that would flag every pinned action on
any unrelated script edit (e.g. a ``comment-parse`` change reddening
``apply-summary``, which never runs it) -- a guard that cries wolf gets
ignored, which is worse than the gap it closes. The pinned reusable workflow
path (``.github/workflows/apply-env-level.yml``) is not treated as a
script-running action for this purpose: comparing that file itself already
covers it, and the action pins it contains are separate top-level ref-list
entries, each checked (including their own script dependencies) on their own
-- adding the workflow's pinned actions' scripts to *its* set as well would
double-count them under two different (referencing-file, sha) pairs.

The derivation itself now lives in ``dev/pinrefs.py`` so the ``dev/`` re-pin
CLIs run exactly the same logic this guard asserts on; a divergence between
"what CI calls stale" and "what the fixer rewrites" would be worse than the
duplication it replaces. This module keeps the rationale and the assertions.
"""

import pinrefs
import pytest


def test_internal_action_pins_are_current():
    refs = pinrefs.refs_at()
    assert refs, "no internal shipmate self-references found -- regex or repo layout changed?"

    baseline = pinrefs.release_baseline()
    if baseline is None:
        # No mainline ref reachable (shallow clone / detached HEAD with truncated
        # history). Falling back to HEAD would re-introduce the self-edit false
        # positive the mainline comparison exists to remove, so skip rather than
        # assert against the wrong baseline. CI checks out with fetch-depth: 0.
        pytest.skip("no mainline ref reachable (need main/origin/main with full history)")

    issues = pinrefs.pin_issues(refs, baseline)
    lines = [pinrefs.format_issue(i) for i in issues if i.kind != "missing"]
    unverifiable = [pinrefs.format_issue(i) for i in issues if i.kind == "missing"]

    if lines:
        pytest.fail(
            "stale internal action pin(s) -- bump each to a commit containing the "
            "current action (run `python dev/repin-internal.py`):\n" + "\n".join(lines)
        )
    if unverifiable:
        if pinrefs.is_shallow():
            # merge-base resolving at the tip proves nothing about older
            # history: a depth-1 clone of main resolves "merge-base HEAD
            # origin/main" trivially while every older pinned commit object is
            # absent. That is truncated history, not a dangling ref -- skip
            # rather than misdiagnose it as a force-pushed/GC'd commit.
            pytest.skip(
                "clone is shallow -- cannot tell whether the missing pin "
                "commit(s) are truncated history or genuinely gone:\n" + "\n".join(unverifiable)
            )
        # A pin commit absent from a non-shallow clone means the commit itself
        # is gone (force-push, GC), not truncated history. That is a ref
        # GitHub cannot resolve at runtime, so it must fail -- pytest.skip
        # would pass branch protection and ship the broken pin.
        pytest.fail(
            "internal pin(s) reference a commit that does not exist -- the ref "
            "cannot resolve at runtime:\n" + "\n".join(unverifiable)
        )


# --- script-dependency derivation (pure, no git) ----------------------------


def test_direct_script_refs_extracts_names_from_action_yaml():
    text = (
        'run: python3 "$GITHUB_ACTION_PATH/../../scripts/plan-classify" --fingerprint-only\n'
        'run: python3 "$GITHUB_ACTION_PATH/../../scripts/plan-crypt" decrypt "$STACK"\n'
    )
    assert pinrefs.direct_script_refs(text) == {"plan-classify", "plan-crypt"}


def test_direct_script_refs_empty_when_action_runs_no_scripts():
    assert pinrefs.direct_script_refs("runs:\n  using: composite\n  steps: []\n") == set()


def test_load_refs_extracts_names_from_load_calls():
    text = 'sc = _load("summary-comment")\nag = _load("apply-gate")\n'
    assert pinrefs.load_refs(text) == {"summary-comment", "apply-gate"}


def test_load_refs_empty_when_script_loads_nothing():
    assert pinrefs.load_refs("import json\nimport sys\n") == set()


def test_script_closure_walks_transitively():
    sources = {"a": '_load("b")', "b": '_load("c")', "c": ""}
    assert pinrefs.script_closure({"a"}, sources.get) == {"a", "b", "c"}


def test_script_closure_terminates_on_cycle():
    sources = {"a": '_load("b")', "b": '_load("a")'}
    assert pinrefs.script_closure({"a"}, sources.get) == {"a", "b"}


def test_script_closure_handles_missing_script():
    # A dependency name with no source on this side (added/removed between the
    # pin and the baseline) must not raise -- it stays in the derived set so the
    # caller diffs it and git reports the missing side.
    assert pinrefs.script_closure({"ghost"}, lambda _n: None) == {"ghost"}


def test_composite_action_name_matches_bare_actions_dir():
    assert pinrefs.composite_action_name("actions/apply-summary") == "apply-summary"


def test_composite_action_name_none_for_reusable_workflow():
    assert pinrefs.composite_action_name(".github/workflows/apply-env-level.yml") is None


def test_composite_action_name_none_for_nested_path():
    assert pinrefs.composite_action_name("actions/apply-cell/action.yml") is None


def test_apply_summary_dependent_scripts_include_apply_comment_chain():
    """Regression: actions/apply-summary/action.yml only names scripts/apply-comment
    directly, but apply-comment cross-loads summary-comment and apply-gate. All
    three must land in the derived set -- the exact shape of the gap that let a
    scripts/apply-comment-only change ship without a pin bump while the guard
    stayed green (see the module docstring)."""
    scripts_dir = pinrefs.ROOT / "scripts"

    def source_lookup(name):
        f = scripts_dir / name
        return f.read_text(encoding="utf-8") if f.is_file() else None

    action_yaml = (pinrefs.ROOT / "actions" / "apply-summary" / "action.yml").read_text(
        encoding="utf-8"
    )
    closure = pinrefs.script_closure(pinrefs.direct_script_refs(action_yaml), source_lookup)

    assert {"apply-comment", "summary-comment", "apply-gate"} <= closure


def test_dependent_script_paths_empty_for_reusable_workflow():
    # Comparing the reusable workflow file itself remains correct on its own; it
    # must not also pull in unrelated scripts because it contains action pins
    # (those are separate entries in the ref list).
    head = pinrefs.git("rev-parse", "HEAD").stdout.strip()
    assert (
        pinrefs.dependent_script_paths(".github/workflows/apply-env-level.yml", head, head) == set()
    )


def test_dependent_script_paths_for_apply_summary_contains_apply_comment():
    head = pinrefs.git("rev-parse", "HEAD").stdout.strip()
    dependent = pinrefs.dependent_script_paths("actions/apply-summary", head, head)
    assert {"scripts/apply-comment", "scripts/summary-comment", "scripts/apply-gate"} <= dependent


def test_every_script_invocation_in_an_action_is_visible_to_the_derivation():
    """The premise the whole guard rests on, machine-checked.

    Every claim about pin currency assumes the derivation sees each script a
    pinned action actually runs. That was verified by hand once; an action.yml
    invoking a script by any other spelling -- a variable, a different relative
    path, a `cd` first -- would be invisible to SCRIPT_REF and would silently
    shrink the checked surface. This asserts every $GITHUB_ACTION_PATH mention
    in every action.yml is one the regex claims.
    """
    for action_yaml in sorted((pinrefs.ROOT / "actions").glob("*/action.yml")):
        text = action_yaml.read_text(encoding="utf-8")
        mentions = text.count("$GITHUB_ACTION_PATH")
        matched = len(pinrefs.SCRIPT_REF.findall(text))
        assert mentions == matched, (
            f"{action_yaml.relative_to(pinrefs.ROOT).as_posix()}: {mentions} "
            f"$GITHUB_ACTION_PATH mention(s) but SCRIPT_REF matched {matched} -- "
            "the script-dependency derivation cannot see the difference"
        )


def test_every_internal_ref_in_a_pin_bearing_source_is_visible_to_ref():
    """The premise the whole guard rests on, machine-checked, one level up from
    ``test_every_script_invocation_in_an_action_is_visible_to_the_derivation``.

    Every claim this guard makes about pin currency assumes REF sees every
    internal self-reference a pin-bearing source actually carries. REF only
    matches a 40-lowercase-hex SHA, by design (CONTRACT.md requires internal
    pins to be full SHAs) -- but that means a non-SHA internal pin (a tag, a
    short SHA, an uppercase SHA) is invisible to REF, and so invisible to this
    guard, to ``dev/pin-status.py``, and to ``dev/repin-internal.py --all``,
    whose whole job is not being silent about a stale or malformed internal
    pin. This asserts every ``ship-iac/shipmate/`` mention in every
    pin-bearing source is one REF actually matched.
    """
    for src in pinrefs.source_paths():
        text = (pinrefs.ROOT / src).read_text(encoding="utf-8")
        mentions = text.count("ship-iac/shipmate/")
        matched = len(pinrefs.REF.findall(text))
        assert mentions == matched, (
            f"{src}: {mentions} ship-iac/shipmate/ mention(s) but REF matched only "
            f"{matched} -- a non-SHA internal pin here would be invisible to this "
            "guard, to pin-status, and to repin-internal --all"
        )


def test_unverifiable_pin_fails_when_history_is_present(monkeypatch):
    """A pin whose commit is absent while mainline history IS present is a
    dangling ref, not a shallow clone: GitHub cannot resolve it at runtime and
    every apply job dies. It must fail, because pytest.skip passes branch
    protection -- the hole this closes.
    """
    monkeypatch.setattr(pinrefs, "refs_at", lambda *a, **k: [("actions/setup", "0" * 40, "x.yml")])
    monkeypatch.setattr(pinrefs, "release_baseline", lambda: "1" * 40)
    monkeypatch.setattr(
        pinrefs,
        "pin_issues",
        lambda *a: [pinrefs.PinIssue("actions/setup", "0" * 40, "x.yml", "missing")],
    )
    monkeypatch.setattr(pinrefs, "is_shallow", lambda: False)

    # Both outcomes are caught explicitly rather than with pytest.raises: the
    # pre-fix behavior is pytest.skip, whose exception would propagate out of
    # pytest.raises and mark THIS test skipped -- a green run, which is exactly
    # the failure mode under test. pytest.fail.Exception / pytest.skip.Exception
    # are the public handles; _pytest.outcomes is private API.
    try:
        test_internal_action_pins_are_current()
    except pytest.fail.Exception as exc:
        assert "does not exist" in str(exc)
    except pytest.skip.Exception as exc:
        pytest.fail(f"guard skipped instead of failing: {exc}")
    else:
        pytest.fail("guard returned cleanly; expected a failure")


def test_unverifiable_pin_skips_when_clone_is_shallow(monkeypatch):
    """A depth-1 clone resolves `merge-base HEAD origin/main` trivially at the
    tip even though every older pinned commit object is absent -- so a
    resolved baseline does not prove full history the way it does in a full
    clone. is_shallow() is what tells the two apart; without it this case
    would be misdiagnosed as a force-pushed/GC'd commit and hard-fail every
    shallow checkout instead of just reporting "cannot tell".
    """
    monkeypatch.setattr(pinrefs, "refs_at", lambda *a, **k: [("actions/setup", "0" * 40, "x.yml")])
    monkeypatch.setattr(pinrefs, "release_baseline", lambda: "1" * 40)
    monkeypatch.setattr(
        pinrefs,
        "pin_issues",
        lambda *a: [pinrefs.PinIssue("actions/setup", "0" * 40, "x.yml", "missing")],
    )
    monkeypatch.setattr(pinrefs, "is_shallow", lambda: True)

    with pytest.raises(pytest.skip.Exception, match="shallow"):
        test_internal_action_pins_are_current()
