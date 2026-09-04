"""The engine's internal SHA-pin model: find pins, derive what each one runs,
diff a pin against a baseline commit.

Shared by the CI guard (``scripts/tests/test_internal_pins.py``, which carries
the full rationale for the shape of this) and the ``dev/`` re-pin CLIs. Pure
library: no argparse, no printing, no process exits.
"""

import contextlib
import io
import os
import pathlib
import re
import subprocess
import tempfile
import tokenize
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parent.parent


class GitFailure(RuntimeError):
    """A git invocation this model depends on failed, so its answer is unknown --
    not empty. Without it, a failed ``git ls-tree`` and a commit with no pin-bearing
    files both leave ``source_paths`` reporting nothing, which every caller reads as
    "no problems" instead of "could not check."
    """

    def __init__(self, commit, stderr):
        self.commit = commit
        self.stderr = stderr
        super().__init__(f"git ls-tree failed for {commit!r}: {stderr}")


REF = re.compile(r"ship-iac/shipmate/([^@\s]+)@([0-9a-f]{40})")
SCRIPT_REF = re.compile(r"\$GITHUB_ACTION_PATH/\.\./\.\./scripts/([A-Za-z0-9_-]+)")
# The lookbehind matters: without it this matches the tail of any identifier ending in _load, so
# a `yaml.safe_load("x")` anywhere in a helper would feed the phantom dependency `scripts/x` into
# script_closure and redden scripts/tests/test_pin_derivation_premises.py with a wrong diagnosis.
LOAD_REF = re.compile(r"""(?<![A-Za-z0-9_])_load\(\s*["']([^"']+)["']\s*\)""")
# Any engine ref regardless of shape -- what the SHA-only REF and repin_consumer._CONSUMER_REF
# cannot see, and would leave behind silently. ``scan_survivors`` says why quotes are excluded
# from the ref group and captured separately.
ANY_ENGINE_REF = re.compile(r"ship-iac/shipmate/([^@\s'\"]+)@(?P<quote>['\"])?([^\s'\"#]+)")


def scan_survivors(path_text_pairs, new_sha):
    """Engine refs across ``(path, text)`` pairs not left pinned to ``new_sha``.

    Serves the all-or-nothing promise of ``repin_consumer`` and
    ``repin_internal --all``: a ref the substitution could not touch must be
    named, not swallowed into a reported success. NOT valid for repin_internal's
    default stale-only mode, which legitimately leaves non-target pins alone --
    the caller guards it.

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


def git_show(ref, path):
    """``path``'s content at ``ref``, or None if it does not exist there."""
    r = git("show", f"{ref}:{path}")
    return r.stdout if r.returncode == 0 else None


def read_text(path):
    """UTF-8 text of ``path`` (universal newlines -- callers regex against
    ``\\n`` only) plus its dominant line ending, detected from the raw bytes
    before that normalization collapses CRLF to LF.

    Pair with ``write_text(path, text, newline)`` to round-trip a file's
    convention: reading normalized and writing LF-only flips a CRLF-committed
    workflow to LF over a one-line pin bump.
    """
    raw = path.read_bytes()
    crlf = raw.count(b"\r\n")
    lf_only = raw.count(b"\n") - crlf
    newline = "\r\n" if crlf > lf_only else "\n"
    return raw.decode("utf-8").replace("\r\n", "\n"), newline


def write_text(path, text, newline="\n"):
    """Write ``text`` (``\\n``-delimited) to ``path`` as UTF-8 using ``newline``
    as the line ending.

    Never pathlib's default ``newline=None``: it translates every "\\n" to
    ``os.linesep``, so on Windows a one-line pin bump becomes a whole-file CRLF
    diff. Pass through what ``read_text`` reported; the ``"\\n"`` default is for
    fresh files with no convention to preserve.
    """
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))


def atomic_write_text(path, text, newline="\n"):
    """Write ``text`` to ``path`` like ``write_text``, atomically PER FILE:
    temp file in ``path``'s own directory, then ``os.replace`` (atomic on both
    Windows and POSIX), so nothing ever observes a half-written file.

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


def is_shallow():
    """True when this clone lacks history (``--depth``), so absent objects prove nothing."""
    return git("rev-parse", "--is-shallow-repository").stdout.strip() == "true"


def release_baseline():
    """Merge-base of HEAD with the mainline, or None if no mainline ref resolves."""
    for base in ("origin/main", "main"):
        r = git("merge-base", "HEAD", base)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


_YAML = (".yml", ".yaml")

#: Pin-bearing in shape only. An entry here is safe only while another selector holds that file's
#: refs in shape; ``source_paths`` says why manifest-load.yml is exempt and names its selector.
_NOT_A_PIN_SOURCE = (".github/workflows/manifest-load.yml",)


def _is_source(path):
    """True for the two shapes that can carry an internal pin: a top-level
    workflow, or a composite action's own action.yml."""
    if path in _NOT_A_PIN_SOURCE:
        return False
    if path.count("/") != 2:
        return False
    if path.startswith(".github/workflows/") and path.endswith(_YAML):
        return True
    return path.startswith("actions/") and path.rsplit("/", 1)[-1] in ("action.yml", "action.yaml")


def source_paths(commit=None, root=None):
    """Repo-relative posix paths of pin-bearing files. ``commit=None`` reads the
    working tree; a commit reads that commit's tree.

    ``root`` overrides ROOT for working-tree reads (fixture directories). It is
    meaningless with ``commit`` and combining them raises rather than silently
    reading the wrong repo.

    Both shapes match ``.yml`` and ``.yaml``: GitHub accepts either, and a
    workflow escaping the guard on its extension is the hole this prevents.

    ``.github/workflows/manifest-load.yml`` is excluded (``_NOT_A_PIN_SOURCE``): it
    references all 20 engine actions at the floating ``@main`` on purpose, because
    GitHub parses a remote action's manifest while setting the job up and that parse
    is the whole check. A SHA there would validate an old tree's manifests instead of
    this one's, and would drag all 20 actions' script closures into the staleness
    cascade. The exemption is safe only because
    ``scripts/tests/test_manifest_load_workflow_covers_every_action.py`` compares that
    workflow's whole step list, so a pin added to it cannot hide.
    """
    if commit is not None and root is not None:
        raise ValueError("source_paths: root is only meaningful for a working-tree read")
    base = root or ROOT
    if commit is None:
        found = [
            p.relative_to(base).as_posix()
            for p in (base / ".github" / "workflows").glob("*")
            if p.is_file()
        ] + [p.relative_to(base).as_posix() for p in (base / "actions").glob("*/*") if p.is_file()]
    else:
        r = git("ls-tree", "-r", "--name-only", commit)
        if r.returncode != 0:
            raise GitFailure(commit, r.stderr.strip())
        found = r.stdout.splitlines()
    return sorted(p for p in found if _is_source(p))


def refs_at(commit=None):
    """(pinned-path, sha, source-path) for every internal self-reference, sorted.

    ``commit=None`` reads the working tree -- what the guard checks, since an
    in-flight edit to a pin must be seen before it is committed.
    """
    refs = set()
    for src in source_paths(commit):
        text = (ROOT / src).read_text(encoding="utf-8") if commit is None else git_show(commit, src)
        if text is None:
            continue
        for path, sha in REF.findall(text):
            refs.add((path, sha, src))
    return sorted(refs)


def direct_script_refs(action_yaml_text):
    """Script names an action.yml invokes via ``$GITHUB_ACTION_PATH/../../scripts/<name>``."""
    return set(SCRIPT_REF.findall(action_yaml_text))


def strip_comments(script_text):
    """``script_text`` with comment text blanked, positions otherwise preserved.

    Falls back to the input unchanged when it does not tokenize: this reads
    arbitrary historical commits, and over-reporting diffs an extra path while
    raising means no pin verdict at all.
    """
    try:
        spans = [
            tok.start
            for tok in tokenize.generate_tokens(io.StringIO(script_text).readline)
            if tok.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return script_text
    lines = script_text.splitlines(keepends=True)
    for row, col in spans:
        lines[row - 1] = lines[row - 1][:col] + "\n"
    return "".join(lines)


def load_refs(script_text):
    """Script names a helper cross-loads via the repo's ``_load("<name>")`` pattern.

    Comments are stripped first: a commented-out or merely documented
    ``_load("waves")`` would put a phantom dependency into the closure, diffed
    on every pin check against an action that never runs it.
    """
    return set(LOAD_REF.findall(strip_comments(script_text)))


def script_closure(direct, source_lookup):
    """Transitive closure of ``direct`` through ``_load`` edges.

    ``source_lookup(name)`` returns the script source or None when it does not
    exist on this side; the name still lands in the result so the caller diffs
    it and reports the missing side. ``seen`` makes this cycle-safe.
    """
    seen = set()
    frontier = list(direct)
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        src = source_lookup(name)
        if src is not None:
            frontier.extend(load_refs(src) - seen)
    return seen


def composite_action_name(path):
    """The action name if ``path`` is exactly ``actions/<name>``, else None."""
    parts = path.split("/")
    return parts[1] if len(parts) == 2 and parts[0] == "actions" else None


def dependent_script_paths(path, sha, baseline):
    """``scripts/<name>`` paths a pinned ``actions/<name>`` actually executes,
    transitively, derived from both sides and unioned."""
    name = composite_action_name(path)
    if name is None:
        return set()

    dependent = set()
    for ref in (sha, baseline):
        action_yaml = git_show(ref, f"actions/{name}/action.yml")
        if action_yaml is None:
            continue
        direct = direct_script_refs(action_yaml)
        dependent |= script_closure(direct, lambda n, ref=ref: git_show(ref, f"scripts/{n}"))
    return {f"scripts/{n}" for n in dependent}


def diff_status(path, sha, baseline):
    """0 == identical, 1 == differs (incl. one side missing the path)."""
    return git("diff", "--quiet", sha, baseline, "--", path)


class PinIssue(NamedTuple):
    """One problem with one pin.

    Records, not prose: the re-pin tools select on ``kind``, never on a
    formatted message. Substring-matching one would let a reword silently empty
    the fixer's target list while CI stays red.
    """

    path: str  # The pinned path.
    sha: str  # The pinned commit, as a full SHA.
    src: str  # The file the pin lives in.
    kind: str  # One of "stale", "dep_stale", "missing", "error".
    dep: str = ""  # scripts/<name>, when the issue is about a dependency.
    error: str = ""  # git stderr, when kind == "error".


#: Kinds a re-pin tool may act on. "missing" and "error" leave staleness unknown, so rewriting
#: the pin would be a guess.
ACTIONABLE = ("stale", "dep_stale")


#: format_issue's baseline_desc for a caller that baselines a commit on itself (pin_status,
#: repin_consumer) rather than on the mainline. The mainline wording would claim the mainline
#: moved past the target, when the fact is that the target's own tree runs stale code.
SELF_BASELINE_DESC = "is out of date against this commit's own tree"


def format_issue(i, baseline_desc="changed on the mainline since"):
    """The reader-facing line for an issue. ``baseline_desc`` completes
    "but <path> ..." / "-- <dep> ..." and names what was compared against."""
    if i.kind == "stale":
        return f"{i.src} pins {i.path}@{i.sha[:12]} but {i.path} {baseline_desc}"
    if i.kind == "dep_stale":
        return f"{i.src} pins {i.path}@{i.sha[:12]}, which runs {i.dep} -- {i.dep} {baseline_desc}"
    if i.kind == "missing":
        return f"{i.src}: {i.path}@{i.sha[:12]} (commit not in this clone)"
    return f"{i.src}: git diff failed for {i.dep or i.path} (pin {i.path}@{i.sha[:12]}): {i.error}"


def _direct_issue(path, sha, baseline, src):
    r = diff_status(path, sha, baseline)
    if r.returncode == 1:
        return PinIssue(path, sha, src, "stale")
    if r.returncode != 0:
        return PinIssue(path, sha, src, "error", error=r.stderr.strip())
    return None


def _dependency_issues(path, sha, baseline, src):
    out = []
    for dep in sorted(dependent_script_paths(path, sha, baseline)):
        r = diff_status(dep, sha, baseline)
        if r.returncode == 1:
            out.append(PinIssue(path, sha, src, "dep_stale", dep=dep))
        elif r.returncode != 0:
            out.append(PinIssue(path, sha, src, "error", dep=dep, error=r.stderr.strip()))
    return out


def pin_issues(refs, baseline):
    """Every problem with ``refs`` against ``baseline``, as PinIssue records.

    One record per (path, sha, src) triple per problem: a pin referenced from
    two workflows is two separate lines a reader has to go fix.
    """
    out = []
    for path, sha, src in refs:
        if not commit_present(sha):
            out.append(PinIssue(path, sha, src, "missing"))
            continue
        direct = _direct_issue(path, sha, baseline, src)
        if direct:
            out.append(direct)
        out.extend(_dependency_issues(path, sha, baseline, src))
    return out
