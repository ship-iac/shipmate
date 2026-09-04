#!/usr/bin/env python3
"""Is a commit safe to pin?

The CI guard (scripts/tests/test_internal_pins.py) asks whether the working
tree's pins are current against the mainline the branch forked from -- the right
question for in-flight work. A release target, or a SHA a consumer repo is about
to pin, asks a different one: does the tree at that commit run its own current
code? Same diff machinery, baseline swapped to the commit itself.

An intermediate commit of the internal-pin cascade (docs/releasing.md) answers
no. Run this before `gh release create --target <sha>` and before re-pinning any
consumer repo.

    python dev/pin_status.py            # HEAD
    python dev/pin_status.py <commit>

Exit: 0 safe, 1 stale pins, 2 unverifiable, 3 commit-ish does not resolve,
4 resolves but yields no internal references.
"""

import argparse
import sys

import pinrefs


def pin_status(commit):
    """Every issue with the pins AT ``commit``, baselined ON ``commit``."""
    return pinrefs.pin_issues(pinrefs.refs_at(commit), commit)


resolve = pinrefs.resolve  # re-exported: this module's CLI contract names it


def unreachable_from_main(sha):
    """True when ``sha`` is not an ancestor of the mainline.

    Content currency says nothing about reachability. A commit on a branch that is
    later force-pushed passes every check here, then stops existing when GitHub
    garbage-collects it, and the pin no longer resolves at runtime. A warning rather
    than a refusal, because a legitimate pre-merge dry run is exactly this shape.
    """
    for base in ("origin/main", "main"):
        r = pinrefs.git("merge-base", "--is-ancestor", sha, base)
        if r.returncode == 0:
            return False
        if r.returncode == 1:
            return True
    return False  # No mainline ref to judge against, so say nothing.


def _report(issues, sha, ref_count):
    # "error" (git itself failed) means here what "missing" means: staleness is unknown, so both
    # are unverifiable (exit 2), never folded into the stale/dep_stale bucket (exit 1). A caller
    # branches on the documented contract: 1 = stale, go re-pin; 2 = unverifiable, go investigate.
    blocking = [i for i in issues if i.kind in pinrefs.ACTIONABLE]
    unverifiable = [i for i in issues if i.kind in ("missing", "error")]
    if blocking:
        pins = {(i.path, i.sha) for i in blocking}
        print(f"{sha[:12]}: NOT safe to pin -- {len(pins)} stale internal pin(s):")
        for i in blocking:
            print(f"  {pinrefs.format_issue(i, pinrefs.SELF_BASELINE_DESC)}")
        return 1
    if unverifiable:
        pins = {(i.path, i.sha) for i in unverifiable}
        print(f"{sha[:12]}: unverifiable -- {len(pins)} pin(s) could not be checked:")
        for i in unverifiable:
            print(f"  {pinrefs.format_issue(i, pinrefs.SELF_BASELINE_DESC)}")
        return 2
    print(f"{sha[:12]}: safe to pin ({ref_count} internal references, all current at this commit)")
    return 0


def _unverifiable(sha, exc):
    # A git failure leaves staleness unknown, which is exit 2 (unverifiable), the bucket the
    # "missing"/"error" PinIssues land in. Never exit 1: that one implies a known fix.
    print(f"{sha[:12]}: could not verify pins -- git failed: {exc.stderr}")
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="Report whether a commit is safe to pin.")
    ap.add_argument("commit", nargs="?", default="HEAD", help="commit-ish (default: HEAD)")
    args = ap.parse_args(argv)

    sha = resolve(args.commit)
    if sha is None:
        print(f"{args.commit} does not resolve to a commit in this clone")
        return 3

    try:
        refs = pinrefs.refs_at(sha)
    except pinrefs.GitFailure as exc:
        return _unverifiable(sha, exc)

    if not refs:
        print(f"{sha[:12]}: no internal self-references found -- repo layout changed?")
        return 4

    if unreachable_from_main(sha):
        print(f"warning: {sha[:12]} is not an ancestor of main -- a pin to it can stop resolving")

    try:
        issues = pin_status(sha)
    except pinrefs.GitFailure as exc:
        return _unverifiable(sha, exc)

    return _report(issues, sha, len(refs))


if __name__ == "__main__":
    sys.exit(main())
