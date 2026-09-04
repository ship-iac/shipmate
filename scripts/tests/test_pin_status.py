"""The safe-to-pin predicate.

The historical-commit cases are the acceptance evidence for this tool. They encode the measured
fact that intermediate cascade commits are unsafe to pin while convergence commits are safe,
which docs/releasing.md asserts in prose and nothing else checks.
"""

import pin_status as ps
import pinrefs
import pytest

#: Measured on this repo's history. Convergence commits carry pins that are current at
#: themselves; the two intermediate commits of the cascade that published the client-id mint fix
#: do not.
CONVERGED = "83b37bb5e0baa9256b1ea49f4725cf7f55157c8a"
INTERMEDIATE_ACTION_COMMIT = "abca731ea8d35a5c6d02345e5eb77446b7f14ccf"
INTERMEDIATE_BUMP_COMMIT = "273ec9656ee5cad55a538e44c5f57e726543313c"

#: The engine's own root commit. Its tree predates today's actions/ and .github/workflows/ layout
#: entirely, so refs_at() on it finds no shipmate self-references: a real fixture for the
#: "resolves but has nothing to check" exit-4 case, rather than a mock of it.
NO_REFS_COMMIT = "52f6aad901fe634d6f99d9a499c3c6d25bb737f7"

#: These tests are fixtured on real history. In a shallow clone the commits are absent, refs_at()
#: returns nothing, and the "safe to pin" assertions would pass for the wrong reason. A bare
#: module-level `assert` would raise at import time and abort collection of this whole module,
#: and everything collected after it, turning an unrelated `scripts/` edit's test run red on a
#: shallow checkout with no way to tell "genuinely broken" from "just shallow"; skip loudly
#: instead. CI checks out with fetch-depth: 0, where this condition is always False.
_MISSING_FIXTURES = [
    sha
    for sha in (CONVERGED, INTERMEDIATE_ACTION_COMMIT, INTERMEDIATE_BUMP_COMMIT, NO_REFS_COMMIT)
    if not pinrefs.commit_present(sha)
]
pytestmark = pytest.mark.skipif(
    bool(_MISSING_FIXTURES),
    reason=(
        "history fixture commit(s) not in this clone: "
        + ", ".join(sha[:12] for sha in _MISSING_FIXTURES)
        + " -- these tests read real history; check out with fetch-depth: 0"
    ),
)


def _stale_pins(commit):
    """Distinct (path, sha) pins with a staleness issue at ``commit``.

    Deliberately not a count of issues: pin_issues yields one record per (path, sha, src) triple,
    so a pin referenced from two workflows produces two records for one wrong pin. What a reader
    and a fixer both care about is the set of pins, and asserting on issue counts would silently
    re-encode how many workflows happen to reference each action today.
    """
    return {(i.path, i.sha) for i in ps.pin_status(commit) if i.kind in pinrefs.ACTIONABLE}


def test_converged_commit_is_safe_to_pin():
    assert ps.pin_status(CONVERGED) == []


def test_action_commit_mid_cascade_is_not_safe_to_pin():
    # The fix commit changed three actions, and the workflows still pin the previous SHA for all
    # three.
    assert {path for path, _sha in _stale_pins(INTERMEDIATE_ACTION_COMMIT)} == {
        "actions/apply-cell",
        "actions/apply-summary",
        "actions/gate-refresh",
    }


def test_pin_bump_commit_mid_cascade_is_not_safe_to_pin():
    # Bumping apply-env-level.yml's action pins changed that file, so the three workflows pinning
    # the file are themselves stale. That is the second turn of the cascade's 2-cycle.
    assert _stale_pins(INTERMEDIATE_BUMP_COMMIT) == {
        (".github/workflows/apply-env-level.yml", "71588f81c3d2d06c367ca674d94fb12efcf05694")
    }


def test_stale_issue_records_outnumber_stale_pins_at_the_action_commit():
    """Regression on the shape of the model, not on this repo's history.

    One wrong pin referenced from N files is N issue records. A test asserting on record counts
    would break whenever a workflow gains or loses a reference to an unchanged action, which is
    not a pin defect.
    """
    issues = [i for i in ps.pin_status(INTERMEDIATE_ACTION_COMMIT) if i.kind in pinrefs.ACTIONABLE]
    assert len(issues) > len(_stale_pins(INTERMEDIATE_ACTION_COMMIT))


def test_main_returns_zero_for_a_converged_commit(capsys):
    assert ps.main([CONVERGED]) == 0
    assert "safe to pin" in capsys.readouterr().out


def test_main_returns_one_and_lists_stale_pins(capsys):
    assert ps.main([INTERMEDIATE_ACTION_COMMIT]) == 1
    out = capsys.readouterr().out
    assert "apply-cell" in out
    # The headline counts distinct pins, not issue records: "5 stale pins" when three actions are
    # wrong would send a reader looking for two more.
    assert "3 stale internal pin(s)" in out


def test_main_names_this_commits_own_tree_not_the_mainline(capsys):
    # pin_status() baselines on the commit itself, never the mainline: reusing the guard's
    # "changed on the mainline since" wording here would tell a release engineer the mainline
    # moved past their target, when the fact is that this commit's own tree runs stale code.
    # Mutation: call format_issue(i) with no baseline_desc override, putting "mainline" back.
    ps.main([INTERMEDIATE_ACTION_COMMIT])
    out = capsys.readouterr().out
    assert "mainline" not in out
    assert "this commit's own tree" in out


def test_report_treats_error_kind_as_unverifiable_with_exit_2(capsys):
    # An "error" record, where git itself failed, means what "missing" means: staleness is
    # unknown. It must land in the exit-2 bucket, never folded into the exit-1 "stale" bucket,
    # because a caller branching on the documented contract -- 1 is stale, so re-pin; 2 is
    # unverifiable, so investigate -- needs the right one.
    issues = [pinrefs.PinIssue("actions/setup", "0" * 40, "x.yml", "error", error="boom")]

    code = ps._report(issues, "1" * 40, 5)

    out = capsys.readouterr().out
    assert code == 2
    assert "boom" in out
    assert "stale internal pin(s)" not in out


def test_main_returns_two_for_a_resolvable_commit_with_an_unverifiable_pin(monkeypatch, capsys):
    """Companion to test_report_treats_error_kind_as_unverifiable_with_exit_2, which calls
    _report directly: nothing else drives main() all the way to an exit 2, so the
    resolve -> refs_at -> pin_status -> _report wiring is otherwise untested. It monkeypatches
    the surfaces main() reads (ps.resolve, pinrefs.refs_at, pinrefs.pin_issues) rather than
    short-circuiting pin_status or _report, so this fails if that wiring is broken even when
    _report's own classification logic is correct in isolation."""
    monkeypatch.setattr(ps, "resolve", lambda _c: "1" * 40)
    monkeypatch.setattr(pinrefs, "refs_at", lambda *a, **k: [("actions/setup", "0" * 40, "x.yml")])
    monkeypatch.setattr(
        pinrefs,
        "pin_issues",
        lambda refs, baseline: [pinrefs.PinIssue("actions/setup", "0" * 40, "x.yml", "missing")],
    )

    code = ps.main(["HEAD"])

    out = capsys.readouterr().out
    assert code == 2
    assert "unverifiable" in out
    assert "stale internal pin(s)" not in out


def test_report_still_returns_one_when_a_real_stale_pin_is_present(capsys):
    # The other direction: error and missing must not creep into blocking, and a genuine stale
    # pin must still exit 1.
    issues = [pinrefs.PinIssue("actions/setup", "0" * 40, "x.yml", "stale")]

    code = ps._report(issues, "1" * 40, 5)

    assert code == 1
    assert "1 stale internal pin(s)" in capsys.readouterr().out


def test_main_returns_three_for_a_commitish_that_does_not_resolve(capsys):
    assert ps.main(["definitely-not-a-ref"]) == 3
    assert "does not resolve" in capsys.readouterr().out


def test_main_returns_four_for_a_resolvable_commit_with_no_internal_references(capsys):
    # Distinct from exit 3, where the commit-ish itself does not resolve: this SHA resolves fine,
    # its tree predates the actions/ and .github/workflows/ layout, so there is nothing here for
    # pin_status to check at all.
    assert ps.main([NO_REFS_COMMIT]) == 4
    assert "no internal self-references" in capsys.readouterr().out


def test_ancestor_of_main_is_not_flagged_unreachable():
    assert ps.unreachable_from_main(CONVERGED) is False


def test_non_ancestor_is_flagged_unreachable(monkeypatch):
    # A real force-pushed commit cannot be fixtured, so drive the git result. returncode 1 is
    # `merge-base --is-ancestor` saying "no".
    class _R:
        returncode = 1

    monkeypatch.setattr(pinrefs, "git", lambda *a: _R())
    assert ps.unreachable_from_main("0" * 40) is True


def test_git_error_on_origin_main_falls_through_to_main(monkeypatch):
    """Proves the loop structurally reaches the second base: dropping "main" from the tuple
    fails here, because fake_git raises on any base other than "origin/main" or "main".

    It does not catch the `== 1` -> `!= 0` collapse: under that mutation returncode 128 on the
    first base is itself misread as "not an ancestor" and the function still returns True, so
    this test passes for the wrong reason.
    test_git_error_on_every_base_is_not_reported_unreachable discriminates that one, every base
    erroring so the collapsed version returns True on the first base instead of the correct
    False."""

    class _R:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_git(*args):
        base = args[-1]
        if base == "origin/main":
            return _R(128)  # git error (ref missing/unresolvable), not "no"
        if base == "main":
            return _R(1)  # real "no" from merge-base --is-ancestor
        raise AssertionError(f"unexpected base {base!r}")

    monkeypatch.setattr(pinrefs, "git", fake_git)
    assert ps.unreachable_from_main("0" * 40) is True


def test_main_exits_two_when_refs_at_hits_a_git_failure(monkeypatch, capsys):
    # A GitFailure out of pinrefs.refs_at() means "could not check", neither "no internal
    # self-references" (exit 3) nor "stale" (exit 1), so it must land in the same unverifiable
    # bucket as a missing pin commit.
    monkeypatch.setattr(ps, "resolve", lambda _c: "1" * 40)
    monkeypatch.setattr(
        pinrefs,
        "refs_at",
        lambda *a, **k: (_ for _ in ()).throw(pinrefs.GitFailure("1" * 40, "fatal: bad object")),
    )

    code = ps.main(["HEAD"])

    out = capsys.readouterr().out
    assert code == 2
    assert "git failed" in out
    assert "no internal self-references" not in out


def test_main_exits_two_when_pin_status_hits_a_git_failure(monkeypatch, capsys):
    # The second call site: refs_at(sha) succeeds, so main() gets past the "no refs" check, but
    # the pin_status() call, which re-derives refs internally, hits the git failure.
    monkeypatch.setattr(ps, "resolve", lambda _c: "1" * 40)
    monkeypatch.setattr(pinrefs, "refs_at", lambda *a, **k: [("actions/setup", "0" * 40, "x.yml")])
    monkeypatch.setattr(
        ps,
        "pin_status",
        lambda _sha: (_ for _ in ()).throw(pinrefs.GitFailure("1" * 40, "fatal: bad object")),
    )

    code = ps.main(["HEAD"])

    out = capsys.readouterr().out
    assert code == 2
    assert "git failed" in out
    assert "no internal self-references" not in out


def test_git_error_on_every_base_is_not_reported_unreachable(monkeypatch):
    # Every base ref erroring out, with no mainline ref resolving at all, is "cannot judge" and
    # not "unreachable". It must return False, never misreport a git failure as a positive
    # "not an ancestor" finding.
    class _R:
        def __init__(self, returncode):
            self.returncode = returncode

    monkeypatch.setattr(pinrefs, "git", lambda *a: _R(128))
    assert ps.unreachable_from_main("0" * 40) is False
