"""pending-checks: check-run POST bodies from downloaded cell summaries."""

import json

import pytest
from _loader import load_script

pc = load_script("pending-checks")

HEAD = "a" * 40


def _write_cell(tmp_path, env, slug, **cell):
    d = tmp_path / f"cell-summary.{env}.{slug}"
    d.mkdir(parents=True)
    (d / "cell.json").write_text(json.dumps(cell), encoding="utf-8")


def test_changed_cell_yields_queued_body(tmp_path):
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="f" * 64,
    )
    (body,) = pc.bodies(str(tmp_path), HEAD)
    assert body == {
        "name": "apply / stacks/app / dev-eu",
        "head_sha": HEAD,
        "status": "queued",
        "external_id": "f" * 64,
        "output": {
            "title": "apply pending",
            "summary": "Waiting to be applied. Merge after apply completes "
            + "for this stack x environment.",
        },
    }


def test_unchanged_cell_yields_completed_neutral_body(tmp_path):
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-dns",
        stack="dns",
        stack_path="stacks/dns",
        environment="dev-eu",
        changed=False,
        fingerprint="0" * 64,
    )
    (body,) = pc.bodies(str(tmp_path), HEAD)
    assert body["status"] == "completed"
    assert body["conclusion"] == "neutral"
    assert body["output"]["title"] == "no changes"
    assert body["external_id"] == "0" * 64


def test_missing_fingerprint_fails_loud(tmp_path):
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
        environment="dev-eu",
        changed=True,
    )
    with pytest.raises(SystemExit, match="fingerprint"):
        pc.bodies(str(tmp_path), HEAD)


def test_cells_sorted_and_multiple(tmp_path):
    _write_cell(
        tmp_path,
        "dev-us",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
        environment="dev-us",
        changed=True,
        fingerprint="1" * 64,
    )
    _write_cell(
        tmp_path,
        "dev-eu",
        "stacks-app",
        stack="app",
        stack_path="stacks/app",
        environment="dev-eu",
        changed=True,
        fingerprint="2" * 64,
    )
    names = [b["name"] for b in pc.bodies(str(tmp_path), HEAD)]
    assert names == ["apply / stacks/app / dev-eu", "apply / stacks/app / dev-us"]
