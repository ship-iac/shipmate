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
from typing import NamedTuple

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
# wrapped in a quote (quoted `uses:` scalars are legal YAML), optionally
# carrying a trailing comment this tool owns (the release annotation).
#
# The quote group is captured so the sub can re-emit it before any comment --
# without this, a closing quote matches neither the SHA nor the comment
# pattern, so it gets pushed past the new SHA into the middle of the string:
# `"...@<old>"` becomes `"...@<new> # label"`, one YAML string Actions cannot
# resolve. `(?(quote)(?P=quote))` requires the *same* quote character to follow
# the SHA -- an opening quote only counts as this ref's closing quote, not any
# unrelated quote elsewhere on the line.
#
# [^\S\n]* not \s*: \s matches newlines, so a pin line followed by a standalone
# comment line would capture that comment as the trailing one, and a --label
# rewrite would delete it and join the two lines. No sample workflow carries
# that shape today, which is exactly why it has to be closed here rather than
# noticed later.
_CONSUMER_REF = re.compile(
    r"""(?P<quote>["'])?(?P<ref>ship-iac/shipmate/[^@\s]+)@[0-9a-f]{40}
        (?(quote)(?P=quote))
        (?P<comment>[^\S\n]*\#[^\n]*)?""",
    re.VERBOSE,
)

# Any engine ref regardless of shape -- SHA, tag, short SHA, quoted or not --
# used only to find refs _CONSUMER_REF's SHA-only rewrite cannot touch and
# would otherwise leave behind silently (see F3 in the module docstring).
_ANY_ENGINE_REF = re.compile(r"ship-iac/shipmate/([^@\s'\"]+)@([^\s'\"#]+)")


class _PlannedFile(NamedTuple):
    """One workflow file's computed post-rewrite state, before anything is
    written. Carries an entry for every workflow under ``root``, including
    ones with no engine ref at all -- the survivor validation needs the
    planned text of the whole set to judge the rewrite without reading disk
    again."""

    path: str  # repo-relative posix path
    text: str  # full new content ("\n"-delimited)
    newline: str  # newline style read_text reported for this file
    matched: int  # engine refs found, regardless of whether the sub was a no-op
    changed: bool  # whether the substitution actually altered the text


def _plan_consumer(root, new_sha, label):
    """Compute the post-rewrite content of every workflow under ``root``, in
    memory. Touches nothing on disk but the reads.

    With ``label``, the trailing comment becomes ``# <label>`` (docs/releasing.md
    annotates each consumer pin ``# vX.Y.Z``). Without, an existing comment is
    left untouched. Third-party pins are unaffected -- the pattern is anchored
    on the engine slug.
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
    """Write every planned file whose substitution actually altered the text
    to disk, atomically per file (see pinrefs.atomic_write_text).

    Returns ``(changed, matched)``: ``changed`` is ``[(path, n)]`` for files
    whose substitution actually altered the text; ``matched`` is the total
    count of engine refs found across all files, independent of whether the
    substitution was a no-op. The two differ exactly when every match was
    already at ``new_sha``/``label`` -- the caller needs that to tell "matched
    nothing" (wrong --repo) apart from "matched N, all already current"
    (a safe re-run).

    This is the only place this rewriter touches disk, and it does nothing
    but write -- no git calls, no regex -- so an interrupt here can only ever
    leave "some files replaced, some not", never a torn file. Not a
    cross-file transaction: recover with `git status` / `git checkout --`.
    """
    changed = []
    matched = 0
    for p in planned:
        matched += p.matched
        if p.matched and p.changed:
            pinrefs.atomic_write_text(root / p.path, p.text, p.newline)
            changed.append((p.path, p.matched))
    return changed, matched


def rewrite_consumer(root, new_sha, label):
    """Point every engine ref in ``root``'s workflows at ``new_sha``.

    Returns ``(changed, matched)`` -- see ``_commit_consumer``. Plans every
    workflow in memory first, then commits -- see ``_plan_consumer``/
    ``_commit_consumer``.
    """
    return _commit_consumer(root, _plan_consumer(root, new_sha, label))


def _scan_survivors(path_text_pairs, new_sha):
    """Engine refs across ``(path, text)`` pairs still not pinned to
    ``new_sha`` -- a tag, short SHA, or uppercase SHA that ``_CONSUMER_REF``
    cannot match (it only recognizes 40-hex lowercase) and so silently leaves
    behind. All-or-nothing is the whole point of this tool (see the module
    docstring); a straddling pin pair must be named, not swallowed into a
    reported success.

    Takes ``(path, text)`` rather than a root to read from disk, so the same
    scan serves both the pre-write validation (against planned text, before
    anything is committed) and ``_survivors`` below (against the working
    tree, for standalone use/testing).
    """
    out = []
    for rel, text in path_text_pairs:
        for path, ref in _ANY_ENGINE_REF.findall(text):
            if ref != new_sha:
                out.append(f"{rel}: {path}@{ref}")
    return out


def _survivors(root, new_sha):
    """``_scan_survivors`` read fresh off ``root``'s workflows on disk."""
    wf = root / ".github" / "workflows"
    files = sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml"))
    pairs = [(f.relative_to(root).as_posix(), f.read_text(encoding="utf-8")) for f in files]
    return _scan_survivors(pairs, new_sha)


def _resolve_sha(sha):
    """Full 40-hex sha for ``sha``, or None if it does not resolve here."""
    r = pinrefs.git("rev-parse", "--verify", f"{sha}^{{commit}}")
    if r.returncode != 0:
        print(f"{sha} does not resolve to a commit in this engine clone")
        return None
    return r.stdout.strip()


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
    # A git failure means we do not know the target's pins at all -- refuse,
    # same as an unsafe-to-pin verdict (exit 1), and distinct from both "no
    # self-references" (exit 3, a bad-target shape) and a known stale pin
    # --force is meant to override. --force must not bypass this: it overrides
    # "your pins are stale", never "your pins cannot be judged."
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

    # A bad-target check, not a staleness call: --force overrides "your pins
    # are stale", never "your pins cannot be judged at all". refs_at([]) means
    # this commit's tree has no shipmate self-references (an old commit
    # predating today's actions/ layout, or an orphan commit) -- pin_status
    # would then vacuously report zero issues and this tool would write a pin
    # no one can evaluate. A git failure (GitFailure) is a different animal --
    # not "no refs", but "could not check" -- so it is refused separately
    # below, never folded into this message.
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
    against that plan, and only then commit to disk.

    This makes the *decision* atomic: either every planned file is written,
    or -- when a survivor is found -- none is, and the refusal is reported
    before any write happens. It is not a cross-file transaction: nothing
    here stops an interrupt between two of ``_commit_consumer``'s individual
    writes from leaving some files rewritten and others not; recover with
    `git status` / `git checkout --`, as always.
    """
    planned = _plan_consumer(root, new_sha, label)

    survivors = _scan_survivors([(p.path, p.text) for p in planned], new_sha)
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
