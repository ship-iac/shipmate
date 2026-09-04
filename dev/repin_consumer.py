#!/usr/bin/env python3
"""Re-pin a consumer repo's shipmate references to one commit.

Run from an engine clone (it needs engine history to judge the target):

    python dev/repin_consumer.py --repo ../repo-example-stacks --sha <sha> --label v0.2.0

Two rules from docs/releasing.md are enforced here:

* Every engine ref moves together. actions/summary creates the pending apply
  check and actions/apply-cell -- pinned indirectly inside apply-env-level.yml --
  completes it, each building the name independently. A pin pair straddling a
  change to that grammar creates one name and looks for another, and every wave
  job dies before restoring state. There is deliberately no stale-only mode.
* An intermediate commit of the internal-pin cascade is never a valid target.
  dev/pin_status.py decides; --force overrides it deliberately and loudly.

Exit: 0 wrote, 1 refused, 3 bad target or repo path.
"""

import argparse
import pathlib
import re
import sys
from typing import NamedTuple

import pin_status as _ps
import pinrefs

pin_status = _ps.pin_status
unreachable_from_main = _ps.unreachable_from_main
format_issue = pinrefs.format_issue

# A consumer ref is any path under the engine slug, pinned by SHA, optionally wrapped in a quote
# (quoted `uses:` scalars are legal YAML), optionally carrying a trailing comment this tool owns
# (the release annotation). ``_plan_consumer`` names the three load-bearing clauses.
_CONSUMER_REF = re.compile(
    r"""(?P<quote>["'])?(?P<ref>ship-iac/shipmate/[^@\s]+)@[0-9a-f]{40}
        (?(quote)(?P=quote))
        (?P<comment>[^\S\n]*\#[^\n]*)?""",
    re.VERBOSE,
)


class _PlannedFile(NamedTuple):
    """One workflow file's computed post-rewrite state, before anything is written.

    Carries an entry for every workflow under ``root``, including ones with no engine
    ref at all: the survivor validation needs the planned text of the whole set to
    judge the rewrite without reading disk again.
    """

    path: str  # Repo-relative posix path.
    text: str  # Full new content ("\n"-delimited).
    newline: str  # Newline style read_text reported for this file.
    matched: int  # Engine refs found, regardless of whether the sub was a no-op.
    changed: bool  # Whether the substitution actually altered the text.


def _plan_consumer(root, new_sha, label):
    r"""Compute the post-rewrite content of every workflow under ``root``, in
    memory. Touches nothing on disk but the reads.

    With ``label``, the trailing comment becomes ``# <label>`` (docs/releasing.md
    annotates each consumer pin ``# vX.Y.Z``). Without, an existing comment is
    left untouched. Third-party pins are unaffected -- the pattern is anchored
    on the engine slug.

    Three clauses of ``_CONSUMER_REF`` are load-bearing and non-obvious. The quote
    group is captured so ``sub`` can re-emit it BEFORE any comment; otherwise
    ``"...@<old>"`` becomes ``"...@<new> # label"``, a string Actions cannot resolve.
    ``[^\S\n]*`` excludes newlines, so a following standalone comment line is not
    captured as this ref's trailing comment and deleted by a ``--label`` rewrite.
    ``(?(quote)(?P=quote))`` requires the SAME quote character to close the ref; the
    equivalent-looking ``["']?`` would let an opening ``"`` be closed by an unrelated
    ``'`` later on the line, producing exactly the unresolvable string the captured
    quote group prevents.
    """

    def sub(m):
        quote = m.group("quote") or ""
        comment = f" # {label}" if label else (m.group("comment") or "")
        return f"{quote}{m.group('ref')}@{new_sha}{quote}{comment}"

    planned = []
    wf = root / ".github" / "workflows"
    for f in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
        text, newline = pinrefs.read_text(f)
        out, n = _CONSUMER_REF.subn(sub, text)
        rel = f.relative_to(root).as_posix()
        planned.append(_PlannedFile(rel, out, newline, n, out != text))
    return planned


def _commit_consumer(root, planned):
    """Write every planned file whose substitution actually altered the text to
    disk, atomically per file (see pinrefs.atomic_write_text).

    Returns ``(changed, matched)``. ``changed`` is ``[(path, n)]`` for the files
    written; ``matched`` is the total count of engine refs found across all files,
    independent of whether the substitution was a no-op. The two differ exactly when
    every match was already at ``new_sha``/``label`` -- the caller needs that to tell
    "matched nothing" (wrong --repo) apart from "matched N, all already current" (a
    safe re-run).

    Same shape as ``repin_internal._commit``.
    """
    changed = []
    matched = 0
    for p in planned:
        matched += p.matched
        if p.matched and p.changed:
            pinrefs.atomic_write_text(root / p.path, p.text, p.newline)
            changed.append((p.path, p.matched))
    return changed, matched


def _resolve_sha(sha):
    """Full 40-hex sha for ``sha``, or None if it does not resolve here."""
    resolved = pinrefs.resolve(sha)
    if resolved is None:
        print(f"{sha} does not resolve to a commit in this engine clone")
    return resolved


def _safe_to_pin(new_sha, force):
    """True if ``new_sha`` may be pinned, printing a refusal or override notice."""
    issues = pin_status(new_sha)
    if not issues:
        return True
    problems = [format_issue(i, pinrefs.SELF_BASELINE_DESC) for i in issues]
    if not force:
        print(f"refusing to pin {new_sha[:12]}: its own internal pins are not current --")
        for m in problems:
            print(f"  {m}")
        if any(i.kind in pinrefs.ACTIONABLE for i in issues):
            print("this is an intermediate commit of the internal-pin cascade; pin the")
            print("converged commit instead (docs/releasing.md), or pass --force.")
        else:
            print("the target's pins could not be verified in this clone -- investigate")
            print("before pinning, or pass --force.")
        return False
    print(f"overriding: {new_sha[:12]} has {len(problems)} internal pin problem(s)")
    return True


def _refuse_git_failure(new_sha, exc):
    # A git failure leaves the target's pins unknown, so refuse like an unsafe-to-pin verdict
    # (exit 1), distinct from "no self-references" (exit 3, a bad-target shape). --force must not
    # bypass this: it overrides "your pins are stale", never "your pins cannot be judged."
    print(f"{new_sha[:12]}: its pins could not be verified -- git failed: {exc.stderr}")
    return 1


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

    # A bad-target check, not a staleness call. An empty refs_at means this commit's tree has no
    # shipmate self-references (an old commit predating today's actions/ layout, or an orphan
    # commit), so pin_status would vacuously report zero issues over a pin no one can evaluate.
    try:
        has_refs = bool(pinrefs.refs_at(new_sha))
    except pinrefs.GitFailure as exc:
        return _refuse_git_failure(new_sha, exc)
    if not has_refs:
        print(
            f"{new_sha[:12]} has no shipmate self-references in this clone -- its pins "
            "cannot be judged, so it is not a valid re-pin target"
        )
        return 3

    if unreachable_from_main(new_sha):
        print(f"warning: {new_sha[:12]} is not an ancestor of main -- this pin can stop resolving")

    try:
        safe = _safe_to_pin(new_sha, args.force)
    except pinrefs.GitFailure as exc:
        return _refuse_git_failure(new_sha, exc)
    if not safe:
        return 1

    return _rewrite_and_report(root, new_sha, args.label)


def _rewrite_and_report(root, new_sha, label):
    """Plan every workflow file in memory, validate the all-or-nothing rule
    against that plan, and only then commit to disk -- same plan/validate/commit
    contract as ``repin_internal._write_and_report``.
    """
    planned = _plan_consumer(root, new_sha, label)

    survivors = pinrefs.scan_survivors([(p.path, p.text) for p in planned], new_sha)
    if survivors:
        print(
            f"partial rewrite -- {len(survivors)} engine reference(s) were not moved to "
            f"{new_sha[:12]}: the all-or-nothing rule was violated; these need attention:"
        )
        for line in survivors:
            print(f"  {line}")
        return 1

    changed, matched = _commit_consumer(root, planned)

    if not matched:
        print(f"no engine references found under {root / '.github' / 'workflows'}")
        return 0
    if not changed:
        print(f"{matched} engine reference(s) already pinned to {new_sha[:12]}; nothing to rewrite")
        return 0

    total = sum(n for _rel, n in changed)
    print(f"re-pinned {total} reference(s) across {len(changed)} file(s) to {new_sha[:12]}:")
    for rel, n in changed:
        print(f"  {rel} ({n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
