"""Guard against stale engine-internal action pins.

The reusable workflow ``.github/workflows/apply-env-level.yml`` and the composite
actions reference sibling shipmate actions/workflows by full commit SHA. GitHub
does not allow a local ``./actions/...`` reference across the reusable-workflow
boundary -- inside a reusable workflow ``./`` resolves against the *consumer*
repo, which has no ``actions/`` dir -- so the SHA pin is the only mechanism.

The hazard: when a referenced action's code changes, these internal pins go
stale silently and the engine keeps running the OLD action. This bit the deploy
path once -- ``apply-env-level.yml`` pinned ``apply-cell`` at a pre-safeguard SHA,
so the ``--disable-safeguards=git-out-of-sync`` fix never reached post-merge
applies even after consumers re-pinned to the new engine SHA.

This test asserts every internal ``ship-iac/shipmate/<path>@<sha>`` reference
pins a commit whose ``<path>`` content matches the **mainline** (the merge-base
with ``main``), not HEAD. A difference means the pin is stale on the release
line: bump it to a commit that contains the current action (in practice, a
follow-up commit pinning the just-merged release SHA). Comparing against the
mainline -- rather than HEAD -- means a branch that edits a pinned action isn't
flagged for its own not-yet-merged change (the bump is impossible before the
change has a SHA); the guard still fires once that change reaches ``main``
without a pin bump. See ``_release_baseline``.

A second hazard, same shape, one directory level deeper: a composite action's
runtime dependency surface is bigger than its own directory. Composite action
steps run ``python3 "$GITHUB_ACTION_PATH/../../scripts/<name>"``, and
``$GITHUB_ACTION_PATH`` resolves into the checkout of that action's *pinned*
SHA -- so the pinned tree's ``scripts/`` is what actually executes, not the
mainline's. Those scripts also cross-load each other via the repo's
``_load("<name>")`` pattern (e.g. ``apply-comment`` loads ``summary-comment``,
which loads ``apply-gate``). A ``scripts/``-only change is invisible to the
plain per-directory diff above: the referencing ``actions/<name>`` directory
never changed, so the guard stayed green while the pinned SHA kept running the
old script. (This shipped once: an engine PR changed only ``scripts/apply-comment``
and its tests; the following pin-bump PR was still required, and until it
landed, the previous pin's ``apply-summary`` action kept executing the
pre-fix ``apply-comment``, with the guard reporting no staleness throughout.)

To close that gap, every ``actions/<name>`` pin also gets its *script*
dependency set derived -- action.yml's direct ``$GITHUB_ACTION_PATH`` script
references, transitively closed over ``_load`` -- and each of those
``scripts/<script>`` paths is diffed between the pin and the baseline exactly
like a top-level ref. See ``_dependent_script_paths``. Deliberately not a diff
of the whole ``scripts/`` directory: that would flag every pinned action on
any unrelated script edit (e.g. a ``comment-parse`` change reddening
``apply-summary``, which never runs it) -- a guard that cries wolf gets
ignored, which is worse than the gap it closes. The pinned reusable workflow
path (``.github/workflows/apply-env-level.yml``) is not treated as a
script-running action for this purpose: comparing that file itself already
covers it, and the action pins it contains are separate top-level ref-list
entries, each checked (including their own script dependencies) on their own
-- adding the workflow's pinned actions' scripts to *its* set as well would
double-count them under two different (referencing-file, sha) pairs.
"""

import pathlib
import re
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REF = re.compile(r"ship-iac/shipmate/([^@\s]+)@([0-9a-f]{40})")
_SCRIPT_REF = re.compile(r"\$GITHUB_ACTION_PATH/\.\./\.\./scripts/([A-Za-z0-9_-]+)")
_LOAD_REF = re.compile(r"""_load\(\s*["']([^"']+)["']\s*\)""")


def _self_refs():
    """(referenced-path, sha, source-file) for every internal self-reference."""
    sources = sorted((_ROOT / ".github" / "workflows").glob("*.yml")) + sorted(
        (_ROOT / "actions").glob("*/action.yml")
    )
    refs = set()
    for f in sources:
        for path, sha in _REF.findall(f.read_text(encoding="utf-8")):
            refs.add((path, sha, f.relative_to(_ROOT).as_posix()))
    return sorted(refs)


def _git(*args):
    # encoding="utf-8": scripts/ source files carry non-ASCII (e.g. emoji status
    # markers); Windows' default locale codec (cp1252) can't decode `git show`
    # output for them, so pin the encoding rather than relying on text=True's
    # locale default.
    return subprocess.run(
        ["git", "-C", str(_ROOT), *args], capture_output=True, text=True, encoding="utf-8"
    )


def _commit_present(sha):
    return _git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _release_baseline():
    """The commit the pins must be current against: the merge-base with the
    mainline, NOT HEAD.

    A pin is a *self*-reference, so the commit that edits a pinned action can
    never also pin that action's SHA (a commit cannot pin its own unborn SHA) --
    the bump is a documented follow-up (docs/releasing.md). Comparing against
    HEAD therefore reds every branch that edits a pinned action from the first
    keystroke, which is in-flight work, not staleness. Comparing against the
    fork point (merge-base with main) instead means: the pin was current on the
    release line this branch derives from. That keeps the real guard -- once an
    action change merges to main without a pin bump, merge-base == main and the
    stale pin fails, exactly where the bump can be done -- while an in-flight
    branch that has only *its own* unmerged edits stays green. Returns None when
    no mainline ref is reachable (shallow clone / detached HEAD with truncated
    history); the caller then skips rather than comparing against HEAD, which
    would re-introduce the self-edit false-positive this reframe removes.
    """
    for base in ("origin/main", "main"):
        r = _git("merge-base", "HEAD", base)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def _direct_script_refs(action_yaml_text):
    """Script names an action.yml invokes via ``$GITHUB_ACTION_PATH/../../scripts/<name>``."""
    return set(_SCRIPT_REF.findall(action_yaml_text))


def _load_refs(script_text):
    """Script names a helper script cross-loads via the repo's ``_load("<name>")`` pattern."""
    return set(_LOAD_REF.findall(script_text))


def _script_closure(direct, source_lookup):
    """Transitive closure of ``direct`` script names through ``_load(...)`` edges.

    ``source_lookup(name)`` returns that script's source text, or ``None`` when
    it doesn't exist on this side (a dependency added or removed between the
    pin and the comparison side) -- the name still ends up in the result so the
    caller diffs it and reports the missing side, rather than silently
    dropping it. A ``seen`` set makes this cycle-safe: two scripts that load
    each other still terminate.
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
            frontier.extend(_load_refs(src) - seen)
    return seen


def _composite_action_name(path):
    """The action name if ``path`` is exactly ``actions/<name>``, else ``None``.

    Excludes the pinned reusable workflow path and any nested/action.yml-shaped
    ref -- only a bare ``actions/<name>`` directory pin runs scripts this way.
    """
    parts = path.split("/")
    return parts[1] if len(parts) == 2 and parts[0] == "actions" else None


def _git_show(ref, path):
    """``<path>``'s content at ``<ref>``, or ``None`` if it doesn't exist there."""
    r = _git("show", f"{ref}:{path}")
    return r.stdout if r.returncode == 0 else None


def _dependent_script_paths(path, sha, baseline):
    """``scripts/<name>`` paths a pinned ``actions/<name>`` directory actually
    executes, transitively, at either the pin or the baseline.

    Not a composite action (``_composite_action_name`` returns ``None``, e.g.
    the pinned reusable workflow path): returns the empty set -- see the
    module docstring on why that path isn't double-counted here.

    Derived from *both* sides and unioned, not just the pin: action.yml itself
    changing is already caught by the whole-directory diff the caller does on
    ``path``, so which side supplies a reference that's identical on both
    never changes the outcome -- but a script named on only one side (a
    dependency added or dropped between the pin and the baseline) must still
    reach the comparison set, which deriving from a single side would miss.
    """
    name = _composite_action_name(path)
    if name is None:
        return set()

    dependent = set()
    for ref in (sha, baseline):
        action_yaml = _git_show(ref, f"actions/{name}/action.yml")
        if action_yaml is None:
            continue
        direct = _direct_script_refs(action_yaml)
        dependent |= _script_closure(direct, lambda n, ref=ref: _git_show(ref, f"scripts/{n}"))
    return {f"scripts/{n}" for n in dependent}


def _diff_status(path, sha, baseline):
    """git diff --quiet result for ``<path>`` between ``sha`` and ``baseline``.

    0 == identical, 1 == differs (including one side missing the path
    entirely), anything else is an unexpected git failure.
    """
    return _git("diff", "--quiet", sha, baseline, "--", path)


def _check_direct(path, sha, baseline, src):
    """Stale/failure message for the pinned path itself, or ``None``."""
    r = _diff_status(path, sha, baseline)
    if r.returncode == 1:
        return f"{src} pins {path}@{sha[:12]} but {path} changed on the mainline since"
    if r.returncode != 0:
        return f"{src}: git diff failed for {path}@{sha[:12]}: {r.stderr.strip()}"
    return None


def _check_dependencies(path, sha, baseline, src):
    """Stale/failure messages for the pinned action's script dependencies."""
    msgs = []
    for dep in sorted(_dependent_script_paths(path, sha, baseline)):
        r = _diff_status(dep, sha, baseline)
        if r.returncode == 1:
            msgs.append(
                f"{src} pins {path}@{sha[:12]}, which runs {dep} -- "
                f"{dep} changed on the mainline since"
            )
        elif r.returncode != 0:
            msgs.append(
                f"{src}: git diff failed for {dep} (run by {path}@{sha[:12]}): {r.stderr.strip()}"
            )
    return msgs


def test_internal_action_pins_are_current():
    refs = _self_refs()
    assert refs, "no internal shipmate self-references found -- regex or repo layout changed?"

    baseline = _release_baseline()
    if baseline is None:
        # No mainline ref reachable (shallow clone / detached HEAD with truncated
        # history). Falling back to HEAD here would re-introduce the very
        # self-edit false-positive the mainline comparison exists to remove, so
        # skip rather than assert against the wrong baseline. CI checks out with
        # fetch-depth: 0, where merge-base always resolves.
        pytest.skip("no mainline ref reachable (need main/origin/main with full history)")

    stale, unverifiable = [], []
    for path, sha, src in refs:
        if not _commit_present(sha):
            unverifiable.append(f"{src}: {path}@{sha[:12]} (commit not in this clone)")
            continue
        # --quiet: exit 0 == identical, 1 == the path differs between the pin and
        # the release baseline (merge-base with main), i.e. the pin is stale on
        # the mainline. A branch's own unmerged edits don't count -- they aren't
        # on the baseline yet.
        direct_msg = _check_direct(path, sha, baseline, src)
        if direct_msg:
            stale.append(direct_msg)
        stale.extend(_check_dependencies(path, sha, baseline, src))

    if stale:
        pytest.fail(
            "stale internal action pin(s) -- bump each to a commit containing the "
            "current action (typically the release SHA):\n" + "\n".join(stale)
        )
    if unverifiable:
        # A shallow clone lacks the pinned commit objects; don't pass as green.
        pytest.skip(
            "internal pins could not be verified (need full history -- set "
            "fetch-depth: 0 on the CI checkout):\n" + "\n".join(unverifiable)
        )


# --- script-dependency derivation (pure, no git) ----------------------------


def test_direct_script_refs_extracts_names_from_action_yaml():
    text = (
        'run: python3 "$GITHUB_ACTION_PATH/../../scripts/plan-classify" --fingerprint-only\n'
        'run: python3 "$GITHUB_ACTION_PATH/../../scripts/plan-crypt" decrypt "$STACK"\n'
    )
    assert _direct_script_refs(text) == {"plan-classify", "plan-crypt"}


def test_direct_script_refs_empty_when_action_runs_no_scripts():
    assert _direct_script_refs("runs:\n  using: composite\n  steps: []\n") == set()


def test_load_refs_extracts_names_from_load_calls():
    text = 'sc = _load("summary-comment")\nag = _load("apply-gate")\n'
    assert _load_refs(text) == {"summary-comment", "apply-gate"}


def test_load_refs_empty_when_script_loads_nothing():
    assert _load_refs("import json\nimport sys\n") == set()


def test_script_closure_walks_transitively():
    sources = {"a": '_load("b")', "b": '_load("c")', "c": ""}
    assert _script_closure({"a"}, sources.get) == {"a", "b", "c"}


def test_script_closure_terminates_on_cycle():
    sources = {"a": '_load("b")', "b": '_load("a")'}
    assert _script_closure({"a"}, sources.get) == {"a", "b"}


def test_script_closure_handles_missing_script():
    # A dependency name with no source on this side (e.g. added/removed
    # between the pin and the baseline) must not raise -- it's still part of
    # the derived set so the caller can diff it (git reports the missing side).
    assert _script_closure({"ghost"}, lambda _n: None) == {"ghost"}


def test_composite_action_name_matches_bare_actions_dir():
    assert _composite_action_name("actions/apply-summary") == "apply-summary"


def test_composite_action_name_none_for_reusable_workflow():
    assert _composite_action_name(".github/workflows/apply-env-level.yml") is None


def test_composite_action_name_none_for_nested_path():
    assert _composite_action_name("actions/apply-cell/action.yml") is None


def test_apply_summary_dependent_scripts_include_apply_comment_chain():
    """Regression: actions/apply-summary/action.yml only names scripts/apply-comment
    directly, but apply-comment cross-loads summary-comment and apply-gate. All
    three must land in the derived set -- this is the exact shape of the gap
    that let a scripts/apply-comment-only change ship without a pin bump while
    the guard stayed green (see the module docstring)."""
    scripts_dir = _ROOT / "scripts"

    def source_lookup(name):
        f = scripts_dir / name
        return f.read_text(encoding="utf-8") if f.is_file() else None

    action_yaml = (_ROOT / "actions" / "apply-summary" / "action.yml").read_text(encoding="utf-8")
    closure = _script_closure(_direct_script_refs(action_yaml), source_lookup)

    assert {"apply-comment", "summary-comment", "apply-gate"} <= closure


def test_dependent_script_paths_empty_for_reusable_workflow():
    # Comparing the reusable workflow file itself remains correct on its own;
    # it must not also pull in unrelated scripts just because it happens to
    # contain action pins (those are separate entries in the ref list).
    head = _git("rev-parse", "HEAD").stdout.strip()
    assert _dependent_script_paths(".github/workflows/apply-env-level.yml", head, head) == set()


def test_dependent_script_paths_for_apply_summary_contains_apply_comment():
    head = _git("rev-parse", "HEAD").stdout.strip()
    dependent = _dependent_script_paths("actions/apply-summary", head, head)
    assert {"scripts/apply-comment", "scripts/summary-comment", "scripts/apply-gate"} <= dependent
