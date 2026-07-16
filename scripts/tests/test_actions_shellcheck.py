"""Lint the bash inside every composite action.

actionlint (in CI) covers `.github/workflows/`, but not `actions/*/action.yml`,
where shipmate's real shell logic lives. This test extracts each bash `run:`
block and runs shellcheck on it, and also enforces the injection-safety
invariant that no run block interpolates a `${{ ... }}` expression (author- and
attacker-controlled values must reach the script through `env:`).
"""

import pathlib
import shutil
import subprocess

import pytest
import yaml

_ACTIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "actions"
_ACTION_FILES = sorted(_ACTIONS_DIR.glob("*/action.yml"))
# env: vars are injected by the GitHub runner, so shellcheck can't see their
# assignment — SC2154 (referenced but not assigned) is expected, not a defect.
_IGNORE = "SC2154"


def _bash_runs(action_file):
    """(index, run-text) for every bash `run:` step in a composite action."""
    spec = yaml.safe_load(action_file.read_text(encoding="utf-8"))
    steps = (spec.get("runs") or {}).get("steps") or []
    return [(i, s["run"]) for i, s in enumerate(steps) if s.get("shell") == "bash" and "run" in s]


@pytest.mark.parametrize("action", _ACTION_FILES, ids=lambda p: p.parent.name)
def test_no_template_expr_in_run(action):
    """No run block may contain a ${{ }} expression — pass values via env:."""
    offenders = [i for i, run in _bash_runs(action) if "${{" in run]
    assert not offenders, (
        f"{action.parent.name}: run block(s) {offenders} interpolate ${{{{ }}}} "
        "directly — pass the value via env: and reference it as a shell var."
    )


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
@pytest.mark.parametrize("action", _ACTION_FILES, ids=lambda p: p.parent.name)
def test_run_blocks_shellcheck_clean(action, tmp_path):
    problems = []
    for i, run in _bash_runs(action):
        script = tmp_path / f"{action.parent.name}_{i}.sh"
        # Normalize CRLF (a Windows working tree may carry it) so we lint the
        # shell logic, not line endings — CI checks out LF.
        body = run.replace("\r\n", "\n").replace("\r", "\n")
        script.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
        r = subprocess.run(
            ["shellcheck", "-s", "bash", "-e", _IGNORE, str(script)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            problems.append(f"step {i}:\n{r.stdout}")
    assert not problems, f"{action.parent.name}:\n" + "\n".join(problems)
