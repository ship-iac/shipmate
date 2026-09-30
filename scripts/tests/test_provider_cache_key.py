"""`scripts/provider-cache-key` digests a lock file's provider addresses and versions, and nothing
else, and prints an empty digest rather than failing when there is nothing to digest.

Mutations that red this module: `sorted(pairs)` replaced by `pairs`; the constraints line
captured into the digest input; `except OSError` narrowed to `except FileNotFoundError`;
`errors="replace"` dropped from the lock read.
"""

import os
import subprocess
import sys

import pytest
from _loader import SCRIPTS

#: `printf 'registry.opentofu.org/hashicorp/random 3.9.1' | sha256sum`
_RANDOM_ONLY = "85e935a14fbb14fb8dce4b31428254f78f4d20557f2ad3aab9987280af585ad7"
#: The same `printf | sha256sum` over the null then the random line, joined by `\n`, no trailing
#: newline: `registry.opentofu.org/hashicorp/null 3.2.4`.
_NULL_AND_RANDOM = "03533df891685f9574974a503fbf0959b5201ead4a29039c4ab838fec65afbaa"

#: `repo-example-stacks/stacks/app/.terraform.lock.hcl`, its hash list cut to two lines.
_RANDOM = """provider "registry.opentofu.org/hashicorp/random" {
  version     = "3.9.1"
  constraints = "~> 3.0"
  hashes = [
    "h1:38E2VQmQDhws/3AL3D/EzBGuCseepyZRIswAOx8CqoQ=",
    "zh:09aaf19b0d22726d2378e0e89fbbefc183494d7bd585759d6c4e69ba50951a2f",
  ]
}
"""

_NULL = """provider "registry.opentofu.org/hashicorp/null" {
  version     = "3.2.4"
  constraints = "3.2.4"
  hashes = [
    "h1:jsKjBiLb+v3OIC3xuDiY4sR0r1OHUMSWPYKult9MhT0=",
  ]
}
"""

_HEADER = (
    '# This file is maintained automatically by "tofu init".\n'
    "# Manual edits may be lost in future updates.\n\n"
)


def _run(tmp_path, lock=None, *, lock_is_dir=False):
    stack = tmp_path / "stack"
    stack.mkdir()
    if lock_is_dir:
        (stack / ".terraform.lock.hcl").mkdir()
    elif isinstance(lock, bytes):
        (stack / ".terraform.lock.hcl").write_bytes(lock)
    elif lock is not None:
        (stack / ".terraform.lock.hcl").write_text(lock, encoding="utf-8")
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    env = {**os.environ, "STACK": str(stack), "GITHUB_OUTPUT": str(out)}
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "provider-cache-key")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    return out.read_text(encoding="utf-8")


def test_the_sample_lock_digests_its_one_provider(tmp_path):
    assert _run(tmp_path, _HEADER + _RANDOM) == f"digest={_RANDOM_ONLY}\n"


@pytest.mark.parametrize(
    "lock", [_NULL + "\n" + _RANDOM, _RANDOM + "\n" + _NULL], ids=["a-b", "b-a"]
)
def test_provider_order_does_not_change_the_digest(tmp_path, lock):
    assert _run(tmp_path, _HEADER + lock) == f"digest={_NULL_AND_RANDOM}\n"


def test_constraints_and_hashes_do_not_change_the_digest(tmp_path):
    lock = _RANDOM.replace('"~> 3.0"', '">= 3.1, < 4.0"').replace(
        '    "h1:38E2VQmQDhws/3AL3D/EzBGuCseepyZRIswAOx8CqoQ=",\n',
        '    "h1:7+qv9kpOpBC9EUPCubnPxh603tu3l9EIMMBkpbt1H1Y=",\n'
        '    "h1:CEQeHfnUDB3uqAkKoEWfWgbj+kpoQHgcuPbAjPzbh+U=",\n',
    )
    assert lock != _RANDOM
    assert _run(tmp_path, lock) == f"digest={_RANDOM_ONLY}\n"


def test_a_changed_version_changes_the_digest(tmp_path):
    lock = _RANDOM.replace('version     = "3.9.1"', 'version     = "3.9.2"')
    assert lock != _RANDOM
    # `printf 'registry.opentofu.org/hashicorp/random 3.9.2' | sha256sum`
    want = "7020e6d492b317e777cddd3d8a73c2469395c297485e335d54be36f7024d2788"
    assert _run(tmp_path, lock) == f"digest={want}\n"


@pytest.mark.parametrize(
    ("lock", "lock_is_dir"),
    [(None, False), (_HEADER, False), (None, True)],
    ids=["no-file", "no-provider-block", "directory"],
)
def test_nothing_to_digest_is_an_empty_digest_and_exit_zero(tmp_path, lock, lock_is_dir):
    assert _run(tmp_path, lock, lock_is_dir=lock_is_dir) == "digest=\n"


def test_a_non_utf8_byte_still_digests_the_provider(tmp_path):
    """Mutation: drop `errors="replace"`, which raises on the 0xff byte and exits non-zero."""
    lock = _RANDOM.replace('"~> 3.0"', '"~> 3.0 \xff"').encode("latin-1")
    assert b"\xff" in lock
    assert _run(tmp_path, lock) == f"digest={_RANDOM_ONLY}\n"
