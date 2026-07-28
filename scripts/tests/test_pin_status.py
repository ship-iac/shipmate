"""The safe-to-pin predicate.

The historical-commit cases are the acceptance evidence for this tool: they
encode the measured fact that intermediate cascade commits are unsafe to pin
while convergence commits are safe, which is what docs/releasing.md asserts in
prose and nothing checked.
"""

import importlib.util
from importlib.machinery import SourceFileLoader

import pinrefs

_DEV = pinrefs.ROOT / "dev"


def _load_cli(fname):
    """Load a hyphenated dev CLI by path (same pattern as scripts/env-order)."""
    loader = SourceFileLoader(fname.replace("-", "_").removesuffix(".py"), str(_DEV / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ps = _load_cli("pin-status.py")

# Measured on this repo's history. Convergence commits carry pins that are
# current at themselves; the two intermediate commits of the cascade that
# published the client-id mint fix do not.
CONVERGED = "83b37bb5e0baa9256b1ea49f4725cf7f55157c8a"
INTERMEDIATE_ACTION_COMMIT = "abca731ea8d35a5c6d02345e5eb77446b7f14ccf"
INTERMEDIATE_BUMP_COMMIT = "273ec9656ee5cad55a538e44c5f57e726543313c"


def _stale_pins(commit):
    """Distinct (path, sha) pins with a staleness issue at ``commit``.

    Deliberately not a count of issues: pin_issues yields one record per
    (path, sha, src) triple, so a pin referenced from two workflows produces two
    records for one wrong pin. What a reader and a fixer both care about is the
    set of pins, and asserting on issue counts would silently re-encode how many
    workflows happen to reference each action today.
    """
    return {(i.path, i.sha) for i in ps.pin_status(commit) if i.kind in pinrefs.ACTIONABLE}


def test_converged_commit_is_safe_to_pin():
    assert ps.pin_status(CONVERGED) == []


def test_action_commit_mid_cascade_is_not_safe_to_pin():
    # The fix commit changed three actions; the workflows still pin the previous
    # SHA for all three.
    assert {path for path, _sha in _stale_pins(INTERMEDIATE_ACTION_COMMIT)} == {
        "actions/apply-cell",
        "actions/apply-summary",
        "actions/gate-refresh",
    }


def test_pin_bump_commit_mid_cascade_is_not_safe_to_pin():
    # Bumping apply-env-level.yml's action pins changed that file, so the three
    # workflows pinning the file are now themselves stale -- the second turn of
    # the cascade's 2-cycle.
    assert _stale_pins(INTERMEDIATE_BUMP_COMMIT) == {
        (".github/workflows/apply-env-level.yml", "71588f81c3d2d06c367ca674d94fb12efcf05694")
    }


def test_stale_issue_records_outnumber_stale_pins_at_the_action_commit():
    """Regression on the shape of the model, not on this repo's history.

    One wrong pin referenced from N files is N issue records. A test that
    asserted on record counts would break whenever a workflow gains or loses a
    reference to an unchanged action, which is not a pin defect.
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
    # The headline counts distinct pins, not issue records -- "5 stale pins"
    # when three actions are wrong would send a reader looking for two more.
    assert "3 stale internal pin(s)" in out


def test_main_returns_three_for_a_commitish_that_does_not_resolve(capsys):
    assert ps.main(["definitely-not-a-ref"]) == 3
    assert "does not resolve" in capsys.readouterr().out


def test_ancestor_of_main_is_not_flagged_unreachable():
    assert ps.unreachable_from_main(CONVERGED) is False


def test_non_ancestor_is_flagged_unreachable(monkeypatch):
    # A real force-pushed commit cannot be fixtured, so drive the git result:
    # returncode 1 is `merge-base --is-ancestor` saying "no".
    class _R:
        returncode = 1

    monkeypatch.setattr(pinrefs, "git", lambda *a: _R())
    assert ps.unreachable_from_main("0" * 40) is True


def test_git_error_on_origin_main_falls_through_to_main(monkeypatch):
    # Regression: a git error against the first base ("origin/main") must not be
    # misread as "not an ancestor" -- it must fall through and consult "main".
    # A mock that ignores which base it was called with (e.g. always returncode=1)
    # would pass even if the implementation collapsed to `if returncode != 0`,
    # which is exactly the bug this test exists to catch.
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


def test_git_error_on_every_base_is_not_reported_unreachable(monkeypatch):
    # Regression: if every base ref errors out (no mainline ref resolves at all),
    # that is "we cannot judge", not "unreachable" -- must return False, never
    # misreport a git failure as a positive "not an ancestor" finding.
    class _R:
        def __init__(self, returncode):
            self.returncode = returncode

    monkeypatch.setattr(pinrefs, "git", lambda *a: _R(128))
    assert ps.unreachable_from_main("0" * 40) is False
