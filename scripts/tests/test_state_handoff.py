"""`scripts/state-handoff`: the `state` output a cell writes and the bundle it hands off.

Threat model: an accidental edit that makes `save` read "not applied" or "no state" for a cell
whose apply ran (its state is then never saved and no one is told), or digests other bytes than
the bundle `save` saves. A cell lying about itself is out of scope: its own code already
controls its state.
"""

import hashlib
import os
import subprocess
import sys

import pytest
from _loader import ENGINE, github_outputs, load_script

pcx = load_script("plan-crypt")
sh = load_script("state-handoff")

SCRIPT = ENGINE / "scripts" / "state-handoff"
STATE = b'{"version":4,"serial":7}'
ENC_VALUE = "handoff-test-key"


def _run(tmp_path, outcome, state_path, *, slug="stacks-app", content=None):
    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    if content is not None:
        (tmp_path / "terraform.tfstate").write_bytes(content)
    env = {
        **os.environ,
        "APPLY_OUTCOME": outcome,
        "STATE_PATH": state_path,
        "SLUG": slug,
        "ENV": "dev-eu",
        "ENCRYPTION_KEY": ENC_VALUE,
        "GITHUB_RUN_ID": "77",
        "GITHUB_RUN_ATTEMPT": "3",
        "GITHUB_OUTPUT": str(out),
        "RUNNER_TEMP": str(temp),
    }
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return r, github_outputs(out), temp / "state.bundle"


@pytest.mark.parametrize("slug", ["stacks-app", ""])
def test_a_skipped_apply_prints_not_applied(tmp_path, slug):
    """A failed `Stack slug` step leaves `SLUG` empty and skips the apply. Mutation: refuse on
    an empty `SLUG` before the outcome test."""
    r, outputs, bundle = _run(tmp_path, "skipped", "terraform.tfstate", slug=slug, content=STATE)
    assert (r.returncode, r.stdout, outputs, bundle.exists()) == (
        0,
        "",
        {"state": "not-applied attempt=3"},
        False,
    )


@pytest.mark.parametrize("outcome", ["", "bogus"])
def test_an_unknown_apply_outcome_prints_no_line(tmp_path, outcome):
    """An outcome the step cannot read is not "not applied". Mutation: treat `""` as
    `skipped`."""
    r, outputs, _ = _run(tmp_path, outcome, "terraform.tfstate", content=STATE)
    assert (r.returncode, r.stdout, outputs) == (1, "", {})
    assert r.stderr == (
        f"::error::the apply step's outcome {outcome!r} is not one this step knows, so the "
        "cell's state is not handed off and its save job reads the cell from the job listing.\n"
    )


def test_a_remote_backend_prints_none(tmp_path):
    """Mutation: print `missing` for an empty `STATE_PATH`."""
    r, outputs, _ = _run(tmp_path, "success", "")
    assert (r.returncode, r.stdout, outputs) == (0, "", {"state": "none attempt=3"})


@pytest.mark.parametrize("content", [None, b""], ids=["absent", "empty"])
def test_an_absent_or_empty_local_state_prints_missing(tmp_path, content):
    """Mutation: print `none` for a 0-byte local file (`os.path.isfile` only)."""
    r, outputs, _ = _run(tmp_path, "success", "terraform.tfstate", content=content)
    assert (r.returncode, r.stdout, outputs) == (0, "", {"state": "missing attempt=3"})


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_an_applied_state_prints_the_bundles_digest(tmp_path, outcome, monkeypatch):
    """The digest covers the magic line, the path and the ciphertext, and the ciphertext is
    sealed under the cell's key. Mutations: digest the ciphertext without the path; put the path
    in the `state` output; seal under another key's prefix."""
    r, outputs, bundle = _run(tmp_path, outcome, "terraform.tfstate", content=STATE)
    data = bundle.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert outputs == {
        "bundle": "true",
        "name": "state.dev-eu.stacks-app.3",
        "state": f"{digest} attempt=3",
    }
    magic, path, ciphertext = data.split(b"\n", 2)
    assert (magic, path) == (b"shipmate-state-bundle-v1", b"terraform.tfstate")
    sealed = tmp_path / "sealed"
    sealed.write_bytes(ciphertext)
    monkeypatch.setenv("ENCRYPTION_KEY", ENC_VALUE)
    pcx.decrypt_state(str(sealed), "state/stacks-app/dev-eu/1-1")
    assert sealed.read_bytes() == STATE
    assert (tmp_path / "terraform.tfstate").read_bytes() == STATE


def test_the_artifact_name_keeps_two_environments_apart():
    """Environment names hold no `.`, so `dev` + `eu-app` and `dev-eu` + `app` differ.
    Mutation: join with `-` (`state-<env>-<slug>-<attempt>`)."""
    assert sh.artifact_name("dev", "eu-app", "1") != sh.artifact_name("dev-eu", "app", "1")
    assert sh.artifact_name("dev-eu", "stacks-app", "2") == "state.dev-eu.stacks-app.2"
