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
"""

import pathlib
import re
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REF = re.compile(r"ship-iac/shipmate/([^@\s]+)@([0-9a-f]{40})")


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
    return subprocess.run(["git", "-C", str(_ROOT), *args], capture_output=True, text=True)


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
        r = _git("diff", "--quiet", sha, baseline, "--", path)
        if r.returncode == 1:
            stale.append(f"{src} pins {path}@{sha[:12]} but {path} changed on the mainline since")
        elif r.returncode != 0:
            stale.append(f"{src}: git diff failed for {path}@{sha[:12]}: {r.stderr.strip()}")

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
