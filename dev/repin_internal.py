#!/usr/bin/env python3
"""Rewrite the engine's stale internal SHA pins.

Replaces the hand-run sed in docs/releasing.md. The staleness selection is the
same code the CI guard asserts on (dev/pinrefs.py), so "what CI calls stale" and
"what this rewrites" cannot drift.

This does not shorten the cascade. Workflows pin apply-env-level.yml, which pins
actions, so bumping an action changes that file and invalidates the pins to it --
an inherent 2-cycle needing fix -> bump-actions -> converge-workflow. Run this
once per step; it removes the hand-editing, not the steps.

    python dev/repin_internal.py                  # bump stale pins to HEAD
    python dev/repin_internal.py --to <sha>
    python dev/repin_internal.py --all --to <sha> # flatten every pin to one SHA
    python dev/repin_internal.py --check          # report, write nothing

Exit: 0 wrote or already current, 1 --check found work, 3 bad target.
"""

import argparse
import re
import sys
from typing import NamedTuple

import pinrefs


class _PlannedEdit(NamedTuple):
    """One source file's computed post-rewrite state, before anything is
    written. Carries an entry for every candidate file, including ones the
    targets did not touch (``count == 0``) -- the ``--all`` survivor
    validation needs the planned text of the whole set, not just the files
    that changed, to judge the flatten without reading disk again."""

    path: str  # repo-relative posix path
    text: str  # full new content ("\n"-delimited)
    newline: str  # newline style read_text reported for this file
    count: int  # substitutions made; 0 means this file needs no write


def _plan(root, targets, new_sha):
    """Compute the post-rewrite content of every pin-bearing source file
    under ``root``, in memory. Touches nothing on disk but the reads.

    Only the two source shapes are considered (pinrefs.source_paths on the
    working tree of ``root``): docs and other files may legitimately carry
    pin-shaped text -- docs/releasing.md itself quotes one.
    """
    planned = []
    for rel in pinrefs.source_paths(root=root):
        text, newline = pinrefs.read_text(root / rel)
        out, total = text, 0
        for path, old in sorted(targets):
            out, n = re.subn(rf"(ship-iac/shipmate/{re.escape(path)})@{old}", rf"\1@{new_sha}", out)
            total += n
        planned.append(_PlannedEdit(rel, out, newline, total))
    return planned


def _commit(root, planned):
    """Write every planned edit with a nonzero count to disk, atomically per
    file (see pinrefs.atomic_write_text), and return (source_path,
    replacements) for the files that changed.

    The only place this rewriter touches disk, and it does nothing but write --
    no git calls, no regex -- so an interrupt here can leave some files replaced
    and others not, never a torn file.
    """
    changed = []
    for p in planned:
        if not p.count:
            continue
        pinrefs.atomic_write_text(root / p.path, p.text, p.newline)
        changed.append((p.path, p.count))
    return changed


def _survivor_report(survivors, new_sha):
    """Print and return 1 if ``survivors`` is non-empty; else None.

    ``--all`` promising to flatten everything is a stronger claim than "matches
    what REF can see", so it gets its own check.
    """
    if not survivors:
        return None
    print(
        f"partial flatten -- {len(survivors)} internal reference(s) were not moved to "
        f"{new_sha[:12]}: --all promises to flatten every internal pin, but these "
        "are refs REF cannot see (a tag, a short SHA, an uppercase SHA) and so "
        "could not be judged stale or rewritten:"
    )
    for line in survivors:
        print(f"  {line}")
    return 1


def _targets(refs, new_sha, bump_all):
    """((path, old_sha) pairs to rewrite, notes to print, staleness_unknown).

    Selection comes off PinIssue.kind, never off a formatted message. Two kinds
    are deliberately excluded: "missing" (the pin's commit is absent, so we
    cannot tell whether it is stale) and "error" (git itself failed). Rewriting
    either would be a guess dressed as a fix.

    ``staleness_unknown`` is True only when no mainline ref resolved at all, so
    the caller can tell "verified nothing is stale" apart from "could not
    check" -- both leave ``targets`` empty, but they are not the same thing to
    report.
    """
    baseline = pinrefs.release_baseline()
    if bump_all:
        return {(path, sha) for path, sha, _src in refs if sha != new_sha}, [], False
    if baseline is None:
        return set(), ["no mainline ref reachable -- cannot tell which pins are stale"], True

    issues = pinrefs.pin_issues(refs, baseline)
    targets = {(i.path, i.sha) for i in issues if i.kind in pinrefs.ACTIONABLE and i.sha != new_sha}
    notes = [
        f"not bumped, cannot verify: {pinrefs.format_issue(i)}"
        for i in issues
        if i.kind not in pinrefs.ACTIONABLE
    ]
    return targets, notes, False


def _report_nothing_to_bump(refs, staleness_unknown):
    ref_note = f"{len(refs)} internal references"
    if staleness_unknown:
        print(f"nothing to bump ({ref_note}; staleness could not be determined)")
        return
    print(
        f"nothing to bump against the mainline baseline ({ref_note}, none stale "
        "there). This baseline is the mainline merge-base, so it cannot see a bump "
        "you have only committed locally -- to check convergence at the current "
        "commit, run: python dev/pin_status.py HEAD"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bump the engine's stale internal pins.")
    ap.add_argument("--to", default="HEAD", help="commit-ish to pin to (default: HEAD)")
    ap.add_argument(
        "--all", action="store_true", help="rewrite every internal pin, not just stale ones"
    )
    ap.add_argument("--check", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args(argv)

    new_sha = pinrefs.resolve(args.to)
    if new_sha is None:
        print(f"{args.to} does not resolve to a commit in this clone")
        return 3

    refs = pinrefs.refs_at()
    if not refs:
        print(
            "no internal shipmate self-references found -- the regex or repo layout has "
            "likely changed"
        )
        return 3

    targets, notes, staleness_unknown = _targets(refs, new_sha, args.all)
    for n in notes:
        print(f"note: {n}")

    if not targets:
        _report_nothing_to_bump(refs, staleness_unknown)
        return 0

    if args.check:
        print(f"would bump {len(targets)} pin(s) to {new_sha[:12]}:")
        for path, old in sorted(targets):
            print(f"  {path}@{old[:12]} -> {new_sha[:12]}")
        return 1

    return _write_and_report(targets, new_sha, args.all)


def _write_and_report(targets, new_sha, bump_all):
    """Plan every candidate file in memory, validate the ``--all``
    all-or-nothing rule against that plan, and only then commit to disk.

    This makes the *decision* atomic: when ``--all`` finds a survivor nothing is
    written at all, and the refusal is reported before any write happens.
    """
    root = pinrefs.ROOT
    planned = _plan(root, targets, new_sha)

    if bump_all:
        survivors = pinrefs.scan_survivors([(p.path, p.text) for p in planned], new_sha)
        code = _survivor_report(survivors, new_sha)
        if code is not None:
            return code

    changed = _commit(root, planned)
    total = sum(n for _rel, n in changed)
    print(f"bumped {total} reference(s) across {len(changed)} file(s) to {new_sha[:12]}:")
    for rel, n in changed:
        print(f"  {rel} ({n})")

    print(
        "commit this, then run `python dev/pin_status.py HEAD` to check convergence at the "
        "current commit before re-running this tool (it compares against the mainline, so "
        "it cannot see this commit until it reaches main)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
