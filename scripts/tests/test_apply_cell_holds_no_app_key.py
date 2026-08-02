"""The apply cells run branch-defined `script "apply"`, so they must hold no App key."""

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CELL = ROOT / "actions/apply-cell/action.yml"
LEVEL = ROOT / ".github/workflows/apply-env-level.yml"


def test_apply_cell_declares_no_app_credentials():
    text = CELL.read_text(encoding="utf-8")
    assert "private-key" not in text
    assert "create-github-app-token" not in text


def test_only_the_completer_job_reads_the_app_key():
    # Parse, don't string-split: the secret stays DECLARED at the top of the
    # file (the complete job consumes it, and test_gate_name_consistency
    # requires reusable targets called with `secrets: inherit` to declare it),
    # so a textual "not in" assertion would be checking the wrong thing.
    doc = yaml.safe_load(LEVEL.read_text(encoding="utf-8"))
    for name, job in doc["jobs"].items():
        body = yaml.safe_dump(job)
        if name == "complete":
            assert "SHIPMATE_APP_PRIVATE_KEY" in body
        else:
            assert "SHIPMATE_APP_PRIVATE_KEY" not in body, name


def test_the_completer_is_the_only_job_naming_the_engine_environment():
    text = LEVEL.read_text(encoding="utf-8")
    assert text.count("environment: shipmate-engine") == 1
