#!/usr/bin/env python3
"""Rewrite the engine's stale internal SHA pins.

Replaces the hand-run sed in docs/releasing.md. The staleness selection is the
same code the CI guard asserts on (dev/pinrefs.py), so "what CI calls stale" and
"what this rewrites" cannot drift.

This does not shorten the cascade. Workflows pin apply-env-level.yml, which pins
actions, so bumping an action changes that file and invalidates the pins to it --
an inherent 2-cycle needing fix -> bump-actions -> converge-workflow. Run this
once per step; it removes the hand-editing, not the steps.

    python dev/repin-internal.py                  # bump stale pins to HEAD
    python dev/repin-internal.py --to <sha>
    python dev/repin-internal.py --all --to <sha> # flatten every pin to one SHA
    python dev/repin-internal.py --check          # report, write nothing

Exit: 0 wrote or already current, 1 --check found work, 3 bad target.
"""

import argparse
import re
import sys

import pinrefs


def rewrite(root, targets, new_sha):
    """Substitute each ``(path, old_sha)`` in ``targets`` with ``new_sha``.

    Only the two source shapes are touched (pinrefs.source_paths on the working
    tree of ``root``): docs and other files may legitimately carry pin-shaped
    text -- docs/releasing.md itself quotes one. Returns (source_path,
    replacements) for files that changed.
    """
    changed = []
    for rel in pinrefs.source_paths(root=root):
        f = root / rel
        text = f.read_text(encoding="utf-8")
        out, total = text, 0
        for path, old in sorted(targets):
            out, n = re.subn(rf"(ship-iac/shipmate/{re.escape(path)})@{old}", rf"\1@{new_sha}", out)
            total += n
        if total:
            pinrefs.write_text(f, out)
            changed.append((rel, total))
    return changed


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bump the engine's stale internal pins.")
    ap.add_argument("--to", default="HEAD", help="commit-ish to pin to (default: HEAD)")
    ap.add_argument(
        "--all", action="store_true", help="rewrite every internal pin, not just stale ones"
    )
    ap.add_argument("--check", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args(argv)

    r = pinrefs.git("rev-parse", "--verify", f"{args.to}^{{commit}}")
    if r.returncode != 0:
        print(f"{args.to} does not resolve to a commit in this clone")
        return 3
    new_sha = r.stdout.strip()

    refs = pinrefs.refs_at()
    targets, notes, staleness_unknown = _targets(refs, new_sha, args.all)
    for n in notes:
        print(f"note: {n}")

    if not targets:
        ref_note = f"{len(refs)} internal references"
        if staleness_unknown:
            print(f"nothing to bump ({ref_note}; staleness could not be determined)")
        else:
            print(f"nothing to bump ({ref_note}, none stale against the mainline)")
        return 0

    if args.check:
        print(f"would bump {len(targets)} pin(s) to {new_sha[:12]}:")
        for path, old in sorted(targets):
            print(f"  {path}@{old[:12]} -> {new_sha[:12]}")
        return 1

    changed = rewrite(pinrefs.ROOT, targets, new_sha)
    total = sum(n for _rel, n in changed)
    print(f"bumped {total} reference(s) across {len(changed)} file(s) to {new_sha[:12]}:")
    for rel, n in changed:
        print(f"  {rel} ({n})")
    print("commit this, then re-run to converge the next level of the cascade.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
