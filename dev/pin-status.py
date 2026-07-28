#!/usr/bin/env python3
"""Is a commit safe to pin?

The CI guard (scripts/tests/test_internal_pins.py) asks whether the working
tree's pins are current against the mainline the branch forked from -- the right
question for in-flight work. A release target, or a SHA a consumer repo is about
to pin, asks a different one: if I pin *this* commit, does the tree it hands me
run its own current code? Same diff machinery, baseline swapped to the commit
itself.

An intermediate commit of the internal-pin cascade (docs/releasing.md) answers
no. Run this before `gh release create --target <sha>` and before re-pinning any
consumer repo.

    python dev/pin-status.py            # HEAD
    python dev/pin-status.py <commit>

Exit: 0 safe, 1 stale pins, 2 unverifiable, 3 commit-ish does not resolve (or
resolves but yields no internal references).
"""

import argparse
import sys

import pinrefs


def pin_status(commit):
    """Every issue with the pins AT ``commit``, baselined ON ``commit``."""
    return pinrefs.pin_issues(pinrefs.refs_at(commit), commit)


def resolve(commitish):
    """Full SHA for ``commitish``, or None if it does not resolve here."""
    r = pinrefs.git("rev-parse", "--verify", f"{commitish}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 else None


def unreachable_from_main(sha):
    """True when ``sha`` is not an ancestor of the mainline.

    Content-currency says nothing about reachability: a commit on a branch that
    later gets force-pushed passes every check here and then stops existing when
    GitHub garbage-collects it, at which point the pin cannot resolve at runtime.
    Reported as a warning rather than a refusal because a legitimate pre-merge
    dry run is exactly this shape.
    """
    for base in ("origin/main", "main"):
        r = pinrefs.git("merge-base", "--is-ancestor", sha, base)
        if r.returncode == 0:
            return False
        if r.returncode == 1:
            return True
    return False  # no mainline ref to judge against; say nothing


def _report(issues, sha, ref_count):
    blocking = [i for i in issues if i.kind != "missing"]
    missing = [i for i in issues if i.kind == "missing"]
    if blocking:
        pins = {(i.path, i.sha) for i in blocking}
        print(f"{sha[:12]}: NOT safe to pin -- {len(pins)} stale internal pin(s):")
        for i in blocking:
            print(f"  {pinrefs.format_issue(i)}")
        return 1
    if missing:
        missing_shas = {i.sha for i in missing}
        print(f"{sha[:12]}: unverifiable -- {len(missing_shas)} pin commit(s) missing:")
        for i in missing:
            print(f"  {pinrefs.format_issue(i)}")
        return 2
    print(f"{sha[:12]}: safe to pin ({ref_count} internal references, all current at this commit)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Report whether a commit is safe to pin.")
    ap.add_argument("commit", nargs="?", default="HEAD", help="commit-ish (default: HEAD)")
    args = ap.parse_args(argv)

    sha = resolve(args.commit)
    if sha is None:
        print(f"{args.commit} does not resolve to a commit in this clone")
        return 3

    refs = pinrefs.refs_at(sha)
    if not refs:
        print(f"{sha[:12]}: no internal self-references found -- repo layout changed?")
        return 3

    if unreachable_from_main(sha):
        print(f"warning: {sha[:12]} is not an ancestor of main -- a pin to it can stop resolving")

    return _report(pin_status(sha), sha, len(refs))


if __name__ == "__main__":
    sys.exit(main())
