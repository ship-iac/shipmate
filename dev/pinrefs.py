"""File and git helpers for ``dev/repin_consumer.py``.

Pure library: no argparse, no printing, no process exits.
"""

import contextlib
import os
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


# Any engine ref regardless of shape -- what repin_consumer._CONSUMER_REF, which matches only a
# 40-hex pin, cannot see and would leave behind silently. ``scan_survivors`` says why quotes are
# excluded from the ref group and captured separately.
ANY_ENGINE_REF = re.compile(r"ship-iac/shipmate/([^@\s'\"]+)@(?P<quote>['\"])?([^\s'\"#]+)")


def scan_survivors(path_text_pairs, new_sha):
    """Engine refs across ``(path, text)`` pairs not left pinned to ``new_sha``.

    Serves the all-or-nothing promise of ``repin_consumer``: a ref the
    substitution could not touch must be named, not swallowed into a reported
    success.

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
    # encoding="utf-8": scripts/ sources carry non-ASCII (emoji status markers);
    # Windows' cp1252 default cannot decode `git show` output for them.
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


def commit_present(sha):
    return git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
