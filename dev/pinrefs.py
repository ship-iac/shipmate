"""The engine's internal SHA-pin model: find pins, derive what each one runs,
diff a pin against a baseline commit.

Shared by the CI guard (``scripts/tests/test_internal_pins.py``, which carries
the full rationale for the shape of this) and the ``dev/`` re-pin CLIs. Pure
library: no argparse, no printing, no process exits.
"""

import pathlib
import re
import subprocess
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parent.parent

REF = re.compile(r"ship-iac/shipmate/([^@\s]+)@([0-9a-f]{40})")
SCRIPT_REF = re.compile(r"\$GITHUB_ACTION_PATH/\.\./\.\./scripts/([A-Za-z0-9_-]+)")
LOAD_REF = re.compile(r"""_load\(\s*["']([^"']+)["']\s*\)""")


def git(*args):
    # encoding="utf-8": scripts/ sources carry non-ASCII (emoji status markers);
    # Windows' cp1252 default cannot decode `git show` output for them.
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8"
    )


def git_show(ref, path):
    """``path``'s content at ``ref``, or None if it does not exist there."""
    r = git("show", f"{ref}:{path}")
    return r.stdout if r.returncode == 0 else None


def write_text(path, text):
    """Write ``text`` to ``path`` as UTF-8 with LF endings.

    pathlib's default ``newline=None`` translates every "\\n" to ``os.linesep``,
    so on Windows rewriting a workflow file would flip the whole file to CRLF --
    burying a one-line pin bump in a whole-file diff for any consumer repo
    without a .gitattributes eol rule.
    """
    path.write_text(text, encoding="utf-8", newline="\n")


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


def _is_source(path):
    """True for the two shapes that can carry an internal pin: a top-level
    workflow, or a composite action's own action.yml."""
    if path.count("/") != 2:
        return False
    if path.startswith(".github/workflows/") and path.endswith(_YAML):
        return True
    return path.startswith("actions/") and path.rsplit("/", 1)[-1] in ("action.yml", "action.yaml")


def source_paths(commit=None, root=None):
    """Repo-relative posix paths of pin-bearing files. ``commit=None`` reads the
    working tree; a commit reads that commit's tree.

    ``root`` overrides ROOT for working-tree reads, so the re-pin tools can run
    against a fixture directory without a second copy of this glob. It is
    meaningless with ``commit`` -- a tree read has no filesystem root -- and
    combining them raises rather than silently reading the wrong repo.

    Both shapes match ``.yml`` and ``.yaml``: GitHub accepts either, and a
    workflow that escaped the guard by spelling its extension differently is
    exactly the silent hole this model exists to prevent.
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
        found = r.stdout.splitlines() if r.returncode == 0 else []
    return sorted(p for p in found if _is_source(p))


def refs_at(commit=None):
    """(pinned-path, sha, source-path) for every internal self-reference, sorted.

    ``commit=None`` reads the working tree -- what the guard checks, since an
    in-flight edit to a pin must be seen before it is committed. A commit reads
    that commit's tree, which is how pin-status answers questions about history.
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


def load_refs(script_text):
    """Script names a helper cross-loads via the repo's ``_load("<name>")`` pattern."""
    return set(LOAD_REF.findall(script_text))


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

    Records, not prose. The re-pin tools need to know *which* (path, sha) to
    rewrite and must not rewrite a pin whose diff merely errored -- recovering
    that by substring-matching a formatted message would make a message reword
    silently empty the fixer's target list while CI stays red.
    """

    path: str  # the pinned path
    sha: str  # the pinned commit, full
    src: str  # the file the pin lives in
    kind: str  # "stale" | "dep_stale" | "missing" | "error"
    dep: str = ""  # scripts/<name>, when the issue is about a dependency
    error: str = ""  # git stderr, when kind == "error"


#: Kinds a re-pin tool may act on. "missing" and "error" mean we do not know
#: whether the pin is stale, so rewriting it would be a guess.
ACTIONABLE = ("stale", "dep_stale")


def format_issue(i):
    """The reader-facing line for an issue."""
    if i.kind == "stale":
        return f"{i.src} pins {i.path}@{i.sha[:12]} but {i.path} changed on the mainline since"
    if i.kind == "dep_stale":
        return (
            f"{i.src} pins {i.path}@{i.sha[:12]}, which runs {i.dep} -- "
            f"{i.dep} changed on the mainline since"
        )
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

    One record per (path, sha, src) triple per problem -- a pin referenced from
    two workflows yields two records, because each is a separate line a reader
    has to go fix.
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
