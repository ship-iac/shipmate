#!/usr/bin/env python3
"""Re-pin a consumer repo's shipmate references to one commit.

Run from an engine clone (it needs engine history to judge the target):

    python dev/repin-consumer.py --repo ../repo-example-stacks --sha <sha> --label v0.2.0

Two rules from docs/releasing.md, previously prose-only, are enforced here:

* Every engine ref moves together. actions/summary creates the pending apply
  check and actions/apply-cell -- pinned indirectly inside apply-env-level.yml --
  completes it, each building the name independently; a pin pair straddling a
  change to that grammar creates one name and looks for another, and every wave
  job dies before restoring state. There is deliberately no stale-only mode.
* An intermediate commit of the internal-pin cascade is never a valid target.
  dev/pin-status.py decides; --force overrides it deliberately and loudly.

Exit: 0 wrote, 1 refused, 3 bad target or repo path.
"""

import argparse
import pathlib
import re
import sys

import pinrefs

_DEV = pinrefs.ROOT / "dev"


def _load(fname):
    import importlib.util
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader(fname.replace("-", "_").removesuffix(".py"), str(_DEV / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_ps = _load("pin-status.py")
pin_status = _ps.pin_status
unreachable_from_main = _ps.unreachable_from_main
format_issue = pinrefs.format_issue

# A consumer ref is any path under the engine slug, pinned by SHA, optionally
# carrying a trailing comment this tool owns (the release annotation).
#
# [^\S\n]* not \s*: \s matches newlines, so a pin line followed by a standalone
# comment line would capture that comment as the trailing one, and a --label
# rewrite would delete it and join the two lines. No sample workflow carries
# that shape today, which is exactly why it has to be closed here rather than
# noticed later.
_CONSUMER_REF = re.compile(r"(ship-iac/shipmate/[^@\s]+)@[0-9a-f]{40}([^\S\n]*#[^\n]*)?")


def rewrite_consumer(root, new_sha, label):
    """Point every engine ref in ``root``'s workflows at ``new_sha``.

    With ``label``, the trailing comment becomes ``# <label>`` (docs/releasing.md
    annotates each consumer pin ``# vX.Y.Z``). Without, an existing comment is
    left untouched. Third-party pins are unaffected -- the pattern is anchored on
    the engine slug.
    """

    def sub(m):
        comment = f" # {label}" if label else (m.group(2) or "")
        return f"{m.group(1)}@{new_sha}{comment}"

    changed = []
    wf = root / ".github" / "workflows"
    for f in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
        text = f.read_text(encoding="utf-8")
        out, n = _CONSUMER_REF.subn(sub, text)
        if n and out != text:
            pinrefs.write_text(f, out)
            changed.append((f.relative_to(root).as_posix(), n))
    return changed


def _resolve_sha(sha):
    """Full 40-hex sha for ``sha``, or None if it does not resolve here."""
    r = pinrefs.git("rev-parse", "--verify", f"{sha}^{{commit}}")
    if r.returncode != 0:
        print(f"{sha} does not resolve to a commit in this engine clone")
        return None
    return r.stdout.strip()


def _safe_to_pin(new_sha, force):
    """True if ``new_sha`` may be pinned, printing a refusal or override notice."""
    problems = [format_issue(i) for i in pin_status(new_sha)]
    if problems and not force:
        print(f"refusing to pin {new_sha[:12]}: its own internal pins are not current --")
        for m in problems:
            print(f"  {m}")
        print("this is an intermediate commit of the internal-pin cascade; pin the")
        print("converged commit instead (docs/releasing.md), or pass --force.")
        return False
    if problems:
        print(f"overriding: {new_sha[:12]} has {len(problems)} internal pin problem(s)")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Re-pin a consumer repo to one engine commit.")
    ap.add_argument("--repo", required=True, help="path to the consumer repo checkout")
    ap.add_argument("--sha", required=True, help="engine commit-ish to pin to")
    ap.add_argument("--label", help="trailing comment to write, e.g. v0.2.0")
    ap.add_argument(
        "--force", action="store_true", help="pin even if the target is not safe to pin"
    )
    args = ap.parse_args(argv)

    root = pathlib.Path(args.repo).resolve()
    if not (root / ".github" / "workflows").is_dir():
        print(f"{root} has no .github/workflows -- not a consumer repo checkout")
        return 3

    new_sha = _resolve_sha(args.sha)
    if new_sha is None:
        return 3

    if unreachable_from_main(new_sha):
        print(f"warning: {new_sha[:12]} is not an ancestor of main -- this pin can stop resolving")

    if not _safe_to_pin(new_sha, args.force):
        return 1

    changed = rewrite_consumer(root, new_sha, args.label)
    if not changed:
        print(f"no engine references found under {root / '.github' / 'workflows'}")
        return 0
    total = sum(n for _rel, n in changed)
    print(f"re-pinned {total} reference(s) across {len(changed)} file(s) to {new_sha[:12]}:")
    for rel, n in changed:
        print(f"  {rel} ({n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
