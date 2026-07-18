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

This test asserts every internal ``shipmate-iac/shipmate/<path>@<sha>`` reference
pins a commit whose ``<path>`` content is identical to the current tree. A
difference means the pin is stale: bump it to a commit that contains the current
action (in practice, a follow-up commit pinning the just-merged release SHA).
"""

import pathlib
import re
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REF = re.compile(r"shipmate-iac/shipmate/([^@\s]+)@([0-9a-f]{40})")


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


def test_internal_action_pins_are_current():
    refs = _self_refs()
    assert refs, "no internal shipmate self-references found -- regex or repo layout changed?"

    stale, unverifiable = [], []
    for path, sha, src in refs:
        if not _commit_present(sha):
            unverifiable.append(f"{src}: {path}@{sha[:12]} (commit not in this clone)")
            continue
        # --quiet: exit 0 == identical, 1 == the path changed between the pin and HEAD.
        r = _git("diff", "--quiet", sha, "HEAD", "--", path)
        if r.returncode == 1:
            stale.append(f"{src} pins {path}@{sha[:12]} but {path} changed since that commit")
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
