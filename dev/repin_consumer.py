#!/usr/bin/env python3
"""Re-pin a consumer repo's shipmate references to one commit.

Run from an engine clone (it needs engine history to judge the target):

    python dev/repin_consumer.py --repo ../repo-example-stacks --sha <sha> --label v0.2.0

Two rules from docs/releasing.md are enforced here:

* Every engine ref moves together. The seven reusable workflows share inputs
  and secrets across a release, so a repository holding two engine versions
  against one contract is a load-time or run-time failure, not a partial
  upgrade. There is deliberately no stale-only mode.
* A target must be provably on main. A commit reachable only from a branch stops
  existing when GitHub garbage-collects a force-push, and the pin no longer
  resolves. A clone where no mainline ref resolves cannot prove it either way,
  and is refused too.

Exit: 0 wrote, 1 refused, 3 bad target or repo path.
"""

import argparse
import contextlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parent.parent


# Any engine ref regardless of shape -- what _CONSUMER_REF, which matches only a 40-hex pin,
# cannot see and would leave behind silently. ``scan_survivors`` says why quotes are excluded
# from the ref group and captured separately.
ANY_ENGINE_REF = re.compile(r"ship-iac/shipmate/([^@\s'\"]+)@(?P<quote>['\"])?([^\s'\"#]+)")


def scan_survivors(path_text_pairs, new_sha):
    """Engine refs across ``(path, text)`` pairs not left pinned to ``new_sha``.

    Serves the tool's all-or-nothing promise: a ref the substitution could not
    touch must be named, not swallowed into a reported success.

    ``ANY_ENGINE_REF`` excludes quotes from its ref group, so a trailing one on a
    legal quoted ``uses:`` scalar cannot make a correctly rewritten ref look like a
    survivor. A quote directly after the ``@`` is never canonical and no rewriter can
    touch it, so that quote is captured and reported even at the new SHA.
    """
    out = []
    for rel, text in path_text_pairs:
        for path, quote, ref in ANY_ENGINE_REF.findall(text):
            if ref != new_sha or quote:
                out.append(f"{rel}: {path}@{quote}{ref}")
    return out


def git(*args):
    # encoding="utf-8": Windows' cp1252 default cannot decode non-ASCII git output.
    # argv is a fixed literal list, no shell, no user-controlled executable name.
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(ROOT), *args],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def read_text(path):
    """UTF-8 text of ``path`` (universal newlines -- callers regex against
    ``\\n`` only) plus its dominant line ending, detected from the raw bytes
    before that normalization collapses CRLF to LF.

    Pass the newline back to ``atomic_write_text`` to round-trip a file's
    convention: reading normalized and writing LF-only flips a CRLF-committed
    workflow to LF over a one-line pin bump.
    """
    raw = path.read_bytes()
    crlf = raw.count(b"\r\n")
    lf_only = raw.count(b"\n") - crlf
    newline = "\r\n" if crlf > lf_only else "\n"
    return raw.decode("utf-8").replace("\r\n", "\n"), newline


def atomic_write_text(path, text, newline="\n"):
    """Write ``text`` (``\\n``-delimited) to ``path`` as UTF-8 using ``newline``
    as the line ending, atomically PER FILE: temp file in ``path``'s own
    directory, then ``os.replace`` (atomic on both Windows and POSIX), so
    nothing ever observes a half-written file.

    Not a cross-file transaction -- an interrupt between two calls leaves the
    already-replaced files changed. Recover with ``git checkout --``.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.replace("\n", newline).encode("utf-8"))
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp_name)
        raise


def resolve(commitish):
    """Full 40-hex SHA for ``commitish`` peeled to a commit, or None if it does
    not resolve here. Without the peel a tag or tree-ish resolves too."""
    r = git("rev-parse", "--verify", f"{commitish}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 else None


def unreachable_from_main(sha):
    """True when ``sha`` is not an ancestor of the mainline, False when it is, and
    None when no mainline ref resolved here (git failed on every base).

    None is a refusal, not a pass. This is the only check standing between the tool
    and rewriting every engine pin in a consumer repo, so a clone that cannot judge
    the target must not rewrite against it -- the target may be branch-only and
    garbage-collectable. The caller tells the two refusals apart.
    """
    for base in ("origin/main", "main"):
        r = git("merge-base", "--is-ancestor", sha, base)
        if r.returncode == 0:
            return False
        if r.returncode == 1:
            return True
    return None


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
        text, newline = read_text(f)
        out, n = _CONSUMER_REF.subn(sub, text)
        rel = f.relative_to(root).as_posix()
        planned.append(_PlannedFile(rel, out, newline, n, out != text))
    return planned


def _commit_consumer(root, planned):
    """Write every planned file whose substitution actually altered the text to
    disk, atomically per file (see atomic_write_text).

    Returns ``(changed, matched)``. ``changed`` is ``[(path, n)]`` for the files
    written; ``matched`` is the total count of engine refs found across all files,
    independent of whether the substitution was a no-op. The two differ exactly when
    every match was already at ``new_sha``/``label`` -- the caller needs that to tell
    "matched nothing" (wrong --repo) apart from "matched N, all already current" (a
    safe re-run).
    """
    changed = []
    matched = 0
    for p in planned:
        matched += p.matched
        if p.matched and p.changed:
            atomic_write_text(root / p.path, p.text, p.newline)
            changed.append((p.path, p.matched))
    return changed, matched


def _resolve_sha(sha):
    """Full 40-hex sha for ``sha``, or None if it does not resolve here."""
    resolved = resolve(sha)
    if resolved is None:
        print(f"{sha} does not resolve to a commit in this engine clone")
    return resolved


def main(argv=None):
    ap = argparse.ArgumentParser(description="Re-pin a consumer repo to one engine commit.")
    ap.add_argument("--repo", required=True, help="path to the consumer repo checkout")
    ap.add_argument("--sha", required=True, help="engine commit-ish to pin to")
    ap.add_argument("--label", help="trailing comment to write, e.g. v0.2.0")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.repo).resolve()
    if not (root / ".github" / "workflows").is_dir():
        print(f"{root} has no .github/workflows -- not a consumer repo checkout")
        return 3

    new_sha = _resolve_sha(args.sha)
    if new_sha is None:
        return 3

    verdict = unreachable_from_main(new_sha)
    if verdict is None:
        print(
            f"refusing to pin {new_sha[:12]}: no mainline ref resolved here -- neither "
            "origin/main nor main. Fetch origin, or run from a clone that has main."
        )
        return 1
    if verdict:
        print(
            f"refusing to pin {new_sha[:12]}: it is not an ancestor of main (fetch origin "
            "first) -- a pin to it"
        )
        print("can stop resolving once the branch it lives on is force-pushed or deleted.")
        return 1

    return _rewrite_and_report(root, new_sha, args.label)


def _rewrite_and_report(root, new_sha, label):
    """Plan every workflow file in memory, validate the all-or-nothing rule
    against that plan, and only then commit to disk."""
    planned = _plan_consumer(root, new_sha, label)

    survivors = scan_survivors([(p.path, p.text) for p in planned], new_sha)
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
